from copy import deepcopy
from io import BytesIO
import json
import os
from unittest.mock import patch
import unittest
from urllib.error import HTTPError, URLError

from duomit.api_models import ApiModel, ApiProfile, HttpsJsonTransport, NoRedirect
from duomit.contracts import (BuilderBinding, ChatMessage, ChatRequest, ModelMaterial,
                              ModelRequest, Plan, RequestContext, Step)
from duomit.errors import DuomitError
from duomit.model_access import ModelBroker, ReferenceDesktop


def api_response(provider, content="Hello"):
    if provider in ("openai", "grok"):
        return {"status": "completed", "output": [{"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": content}]}],
                "usage": {"input_tokens": 10, "output_tokens": 4}}
    if provider == "deepseek":
        return {"choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4}}
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": content}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3, "thoughtsTokenCount": 1}}


class RecordingTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, headers, payload, timeout_seconds):
        self.calls.append((url, headers, payload, timeout_seconds))
        return deepcopy(self.response)


class ApiModelTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"TEST_API_KEY": "test-secret-only"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def model(self, provider, raw=None, principal="alice"):
        transport = RecordingTransport(api_response(provider) if raw is None else raw)
        model = ApiModel(ApiProfile("tenant", principal, provider, "configured-model", "TEST_API_KEY"), transport)
        return model, transport

    def test_four_providers_normalize_usage_and_auth(self):
        for provider in ("openai", "gemini", "deepseek", "grok"):
            with self.subTest(provider=provider):
                model, transport = self.model(provider)
                response = model.complete(ChatRequest((ChatMessage("user", "hello"),)), timeout_seconds=7)
                self.assertEqual((response.content, response.input_tokens, response.output_tokens), ("Hello", 10, 4))
                url, headers, payload, timeout = transport.calls[0]
                self.assertEqual(timeout, 7)
                self.assertTrue(url.startswith("https://"))
                self.assertNotIn("test-secret", url)
                self.assertNotIn("test-secret", json.dumps(payload))
                if provider == "gemini":
                    self.assertEqual(headers, {"x-goog-api-key": "test-secret-only"})
                    self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 1024)
                else:
                    self.assertEqual(headers["Authorization"], "Bearer test-secret-only")
                if provider in ("openai", "grok"):
                    self.assertFalse(payload["store"])
                    self.assertNotIn("previous_response_id", payload)

    def test_planning_uses_same_api_adapter_with_schema(self):
        content = json.dumps(Plan((Step("a", "read"),)).to_dict())
        model, transport = self.model("deepseek", api_response("deepseek", content))
        response = model.generate(ModelRequest("Report", ("read",)), timeout_seconds=3)
        self.assertEqual(response.content, content)
        payload = transport.calls[0][2]
        self.assertIn("schema_version", payload["messages"][0]["content"])
        self.assertEqual(json.loads(payload["messages"][1]["content"])["allowed_capabilities"], ["read"])

    def test_missing_key_stops_before_transport(self):
        model, transport = self.model("openai")
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(DuomitError) as error:
            model.complete(ChatRequest((ChatMessage("user", "hello"),)), timeout_seconds=3)
        self.assertEqual(error.exception.code, "MODEL_CREDENTIAL_MISSING")
        self.assertEqual(transport.calls, [])

    def test_api_adapter_cannot_use_another_owners_credentials(self):
        context = RequestContext("tenant", "bob", "request", "trace")
        model, transport = self.model("openai", principal="alice")
        material = ModelMaterial("model", "1.0", "api")
        desktop = ReferenceDesktop()
        desktop.register(context, material)
        binding = BuilderBinding("tenant", "bob", "binding", 1, material, ("read",))
        with self.assertRaises(DuomitError) as error:
            ModelBroker(desktop, {"api": model}).generate(context, binding, ModelRequest("goal", ("read",)))
        self.assertEqual(error.exception.code, "NOT_FOUND")
        self.assertEqual(transport.calls, [])

    def test_truncation_refusal_and_malformed_usage_are_rejected(self):
        variants = []
        raw = api_response("openai"); raw["status"] = "incomplete"; variants.append(("openai", raw))
        raw = api_response("grok"); raw["output"][0]["content"][0]["type"] = "refusal"; variants.append(("grok", raw))
        raw = api_response("deepseek"); raw["choices"][0]["finish_reason"] = "length"; variants.append(("deepseek", raw))
        raw = api_response("gemini"); raw["candidates"][0]["finishReason"] = "SAFETY"; variants.append(("gemini", raw))
        raw = api_response("gemini"); raw["usageMetadata"]["thoughtsTokenCount"] = -1; variants.append(("gemini", raw))
        raw = api_response("openai"); raw["usage"]["output_tokens"] = True; variants.append(("openai", raw))
        for provider, raw in variants:
            with self.subTest(provider=provider), self.assertRaises(DuomitError):
                self.model(provider, raw)[0].complete(ChatRequest((ChatMessage("user", "hello"),)), timeout_seconds=3)

    def test_gemini_thinking_is_counted_but_not_returned(self):
        raw = api_response("gemini")
        raw["candidates"][0]["content"]["parts"].insert(0, {"text": "private reasoning", "thought": True})
        model, _ = self.model("gemini", raw)
        response = model.complete(ChatRequest((ChatMessage("user", "hello"),)), timeout_seconds=3)
        self.assertEqual(response.content, "Hello")
        self.assertEqual(response.output_tokens, 4)

    def test_output_budget_is_checked_after_usage_normalization(self):
        model, _ = self.model("gemini")
        with self.assertRaises(DuomitError):
            model.complete(ChatRequest((ChatMessage("user", "hello"),), max_output_tokens=3), timeout_seconds=3)

    def test_invalid_profile_and_untrusted_conversation_roles_rejected(self):
        for factory in (lambda: ApiProfile("t", "u", "unknown", "model", "KEY"),
                        lambda: ApiProfile("t", "u", "gemini", "../../endpoint", "KEY"),
                        lambda: ChatMessage("system", "override")):
            with self.assertRaises(DuomitError):
                factory()


class TransportTests(unittest.TestCase):
    URL = "https://api.openai.com/v1/responses"

    def test_post_uses_timeout_and_reads_a_bounded_response(self):
        response = BytesIO(b'{"ok":true}')
        with patch("duomit.api_models.build_opener") as factory:
            factory.return_value.open.return_value = response
            result = HttpsJsonTransport().post(self.URL, {"Authorization": "Bearer fixture"}, {"input": "hello"}, 4)
            self.assertEqual(result, {"ok": True})
            args, kwargs = factory.return_value.open.call_args
            self.assertEqual(kwargs["timeout"], 4)
            self.assertEqual(args[0].get_method(), "POST")

    def test_http_errors_are_sanitized_and_not_retried(self):
        for status, code, retryable in ((401, "MODEL_AUTH_FAILED", False), (429, "MODEL_RATE_LIMITED", True),
                                         (503, "MODEL_HTTP_ERROR", True), (302, "MODEL_HTTP_ERROR", False)):
            with self.subTest(status=status), patch("duomit.api_models.build_opener") as factory:
                factory.return_value.open.side_effect = HTTPError(self.URL, status, "secret", {}, BytesIO(b"secret"))
                with self.assertRaises(DuomitError) as error:
                    HttpsJsonTransport().post(self.URL, {}, {}, 4)
                self.assertEqual((error.exception.code, error.exception.retryable), (code, retryable))
                self.assertNotIn("secret", str(error.exception))
                self.assertEqual(factory.return_value.open.call_count, 1)

    def test_timeout_and_invalid_json(self):
        for failure, code in ((URLError(TimeoutError("secret")), "MODEL_TIMEOUT"),
                              (URLError("secret"), "MODEL_UNAVAILABLE")):
            with patch("duomit.api_models.build_opener") as factory, self.assertRaises(DuomitError) as error:
                factory.return_value.open.side_effect = failure
                HttpsJsonTransport().post(self.URL, {}, {}, 4)
            self.assertEqual(error.exception.code, code)
        for raw in (b"invalid secret", b"[]", b"a" * 1048577):
            with patch("duomit.api_models.build_opener") as factory, self.assertRaises(DuomitError):
                factory.return_value.open.return_value = BytesIO(raw)
                HttpsJsonTransport().post(self.URL, {}, {}, 4)

    def test_endpoint_and_redirect_restrictions(self):
        with self.assertRaises(DuomitError):
            HttpsJsonTransport().post("http://localhost/private", {}, {}, 4)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.invalid"))


if __name__ == "__main__":
    unittest.main()
