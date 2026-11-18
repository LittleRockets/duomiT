"""API-only model adapters. Credentials and HTTP execution stay outside Builder.

Provider profiles are deployment configuration, never model-generated endpoints.
No retries, redirects, provider tools, streaming or server-side conversations.
"""

from dataclasses import dataclass
from importlib.resources import files
import json
import math
import os
import re
import socket
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import (HTTPRedirectHandler, ProxyHandler, Request, build_opener)

from .contracts import (ChatMessage, ChatRequest, ModelRequest, ModelResponse,
                        canonical_json, identifier)
from .errors import DuomitError, require


PROVIDERS = ("openai", "gemini", "deepseek", "grok")
ENDPOINTS = {
    "openai": "https://api.openai.com/v1/responses",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
    "deepseek": "https://api.deepseek.com/chat/completions",
    "grok": "https://api.x.ai/v1/responses",
}
KEY_ENVIRONMENTS = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY",
                    "deepseek": "DEEPSEEK_API_KEY", "grok": "XAI_API_KEY"}


@dataclass(frozen=True)
class ApiProfile:
    tenant_id: str
    principal_id: str
    provider: str
    model: str
    credential_env: str

    def __post_init__(self):
        identifier(self.tenant_id)
        identifier(self.principal_id)
        require(self.provider in PROVIDERS, "Unsupported API provider")
        identifier(self.model)
        require(isinstance(self.credential_env, str)
                and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", self.credential_env) is not None,
                "Invalid credential reference")

    @property
    def owner(self):
        return self.tenant_id, self.principal_id


class JsonTransport(Protocol):
    def post(self, url: str, headers: dict[str, str], payload: dict,
             timeout_seconds: float) -> dict: ...


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpsJsonTransport:
    """Bounded HTTPS POST, verified TLS, no proxy discovery, no redirects/retries.

    Timeout applies to blocking socket operations, not a hard wall-clock deadline
    covering DNS resolution. A future worker deadline must enforce that separately.
    """

    def post(self, url, headers, payload, timeout_seconds):
        require(url in {ENDPOINTS[p] for p in ("openai", "deepseek", "grok")}
                or re.fullmatch(r"https://generativelanguage\.googleapis\.com/v1beta/models/[A-Za-z0-9_.-]+:generateContent", url)
                is not None, "Unapproved API endpoint")
        require(type(timeout_seconds) in (float, int) and math.isfinite(timeout_seconds)
                and 0 < timeout_seconds <= 120, "Invalid API timeout")
        request = Request(url, data=canonical_json(payload).encode("utf-8"),
                          headers=headers | {"Content-Type": "application/json"}, method="POST")
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout_seconds) as response:
                raw = response.read(1048577)
                if len(raw) > 1048576:
                    raise DuomitError("MODEL_RESPONSE_INVALID", "API response exceeds size limit")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except HTTPError as error:
            status = error.code
            error.close()
            if status in (401, 403):
                raise DuomitError("MODEL_AUTH_FAILED", "API authentication was rejected") from None
            if status == 429:
                raise DuomitError("MODEL_RATE_LIMITED", "API rate limit reached", True) from None
            raise DuomitError("MODEL_HTTP_ERROR", "API request was rejected", status >= 500) from None
        except (TimeoutError, socket.timeout):
            raise DuomitError("MODEL_TIMEOUT", "API request timed out", True) from None
        except URLError as error:
            code = "MODEL_TIMEOUT" if isinstance(error.reason, TimeoutError) else "MODEL_UNAVAILABLE"
            raise DuomitError(code, "API connection failed", True) from None
        except (ValueError, UnicodeError, RecursionError):
            raise DuomitError("MODEL_RESPONSE_INVALID", "API returned invalid JSON") from None
        except OSError:
            raise DuomitError("MODEL_UNAVAILABLE", "API connection failed", True) from None


class ApiModel:
    def __init__(self, profile: ApiProfile, transport: JsonTransport | None = None):
        self._profile = profile
        self._transport = transport if transport is not None else HttpsJsonTransport()

    @property
    def owner(self):
        return self._profile.owner

    def generate(self, request: ModelRequest, *, timeout_seconds: float) -> ModelResponse:
        schema = files("duomit").joinpath("plan.schema.json").read_text(encoding="utf-8")
        instructions = ("Return only a JSON plan matching this schema. Use only the supplied "
                        "capabilities. Do not execute tools. Schema: " + schema)
        content = canonical_json({"goal": request.goal,
                                  "allowed_capabilities": request.allowed_capabilities})
        return self.complete(ChatRequest((ChatMessage("user", content),), instructions,
                                         request.max_output_tokens), timeout_seconds=timeout_seconds)

    def complete(self, request: ChatRequest, *, timeout_seconds: float) -> ModelResponse:
        profile = self._profile
        key = os.environ.get(profile.credential_env)
        if not key:
            raise DuomitError("MODEL_CREDENTIAL_MISSING", "API credential is not configured")
        require(len(key) <= 4096 and all(33 <= ord(char) <= 126 for char in key),
                "Invalid API credential")
        messages = [{"role": item.role, "content": item.content} for item in request.messages]
        headers = {"Authorization": "Bearer " + key}
        url = ENDPOINTS[profile.provider].format(model=profile.model)
        if profile.provider in ("openai", "grok"):
            payload = {"model": profile.model, "instructions": request.instructions,
                       "input": messages, "max_output_tokens": request.max_output_tokens,
                       "store": False, "stream": False}
        elif profile.provider == "deepseek":
            payload = {"model": profile.model,
                       "messages": [{"role": "system", "content": request.instructions}] + messages,
                       "max_tokens": request.max_output_tokens, "stream": False}
        else:
            headers = {"x-goog-api-key": key}
            payload = {"systemInstruction": {"parts": [{"text": request.instructions}]},
                       "contents": [{"role": "model" if item.role == "assistant" else "user",
                                     "parts": [{"text": item.content}]} for item in request.messages],
                       "generationConfig": {"maxOutputTokens": request.max_output_tokens}}
        try:
            raw = self._transport.post(url, headers, payload, timeout_seconds)
            response = self._parse(raw)
        except DuomitError:
            raise
        except Exception:
            raise DuomitError("MODEL_RESPONSE_INVALID", "API response could not be processed") from None
        require(response.output_tokens <= request.max_output_tokens, "Model exceeded output token budget")
        return response

    def _parse(self, value):
        provider = self._profile.provider
        if provider in ("openai", "grok"):
            if value.get("status") != "completed" or value.get("error"):
                raise DuomitError("MODEL_INCOMPLETE", "API did not complete the response")
            text = []
            for item in value["output"]:
                if item["type"] == "reasoning":
                    continue
                require(item["type"] == "message" and item.get("role") == "assistant",
                        "Unexpected API action")
                for part in item["content"]:
                    require(part["type"] == "output_text", "API did not return plain text")
                    text.append(part["text"])
            usage = value["usage"]
            input_tokens, output_tokens = usage["input_tokens"], usage["output_tokens"]
            content = "".join(text)
        elif provider == "deepseek":
            require(len(value["choices"]) == 1, "Unexpected response choices")
            choice = value["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise DuomitError("MODEL_INCOMPLETE", "API did not complete the response")
            message = choice["message"]
            require(not message.get("tool_calls") and not message.get("refusal"), "Unexpected API action")
            content = message["content"]
            input_tokens = value["usage"]["prompt_tokens"]
            output_tokens = value["usage"]["completion_tokens"]
        else:
            require(len(value["candidates"]) == 1, "Unexpected response candidates")
            candidate = value["candidates"][0]
            if candidate.get("finishReason") != "STOP":
                raise DuomitError("MODEL_INCOMPLETE", "API did not complete the response")
            text = []
            for part in candidate["content"]["parts"]:
                require("text" in part and "functionCall" not in part, "Unexpected API action")
                if not part.get("thought", False):
                    text.append(part["text"])
            content = "".join(text)
            usage = value["usageMetadata"]
            input_tokens = usage["promptTokenCount"]
            visible, thoughts = usage["candidatesTokenCount"], usage.get("thoughtsTokenCount", 0)
            require(type(visible) is int and visible >= 0 and type(thoughts) is int and thoughts >= 0,
                    "Invalid API usage")
            output_tokens = visible + thoughts
        require(isinstance(content, str) and bool(content.strip()), "API returned empty text")
        return ModelResponse(content, self._profile.model, input_tokens, output_tokens)
