"""Provider-neutral wire contracts. Unknown fields fail closed at input boundaries."""

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from .errors import DuomitError, require

SCHEMA_VERSION = "1.0"


def identifier(value: Any) -> str:
    require(isinstance(value, str) and 0 < len(value) <= 128
            and all(c.isascii() and (c.isalnum() or c in "_-.") for c in value),
            "Invalid identifier")
    return value


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise DuomitError("INVALID_CONTRACT", "Expected JSON-compatible data") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def strict_json(raw: str) -> Any:
    require(isinstance(raw, str) and len(raw.encode("utf-8")) <= 65536,
            "JSON response exceeds limit")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON field")
            result[key] = value
        return result

    def reject_constant(value):
        raise DuomitError("INVALID_CONTRACT", "Non-finite JSON number")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (ValueError, RecursionError) as exc:
        raise DuomitError("INVALID_CONTRACT", "Malformed JSON response") from exc


def fields(value: Any, required: set[str], optional: set[str] = frozenset()) -> dict:
    require(isinstance(value, dict), "Expected an object")
    require(required <= value.keys() and value.keys() <= required | optional,
            "Missing or unknown fields")
    return value


@dataclass(frozen=True)
class RequestContext:
    """Created by the trusted authentication adapter, never parsed from model output.

    The reference app has no HTTP authentication; callers must not treat this DTO
    as a credential or accept it directly from untrusted request bodies.
    """

    tenant_id: str
    principal_id: str
    request_id: str
    trace_id: str

    def __post_init__(self):
        for value in (self.tenant_id, self.principal_id, self.request_id, self.trace_id):
            identifier(value)


@dataclass(frozen=True)
class Step:
    step_id: str
    capability: str
    depends_on: tuple[str, ...] = ()
    requires_approval: bool = False

    def __post_init__(self):
        identifier(self.step_id)
        identifier(self.capability)
        require(type(self.depends_on) is tuple, "Dependencies must be an immutable tuple")
        for dependency in self.depends_on:
            identifier(dependency)
        require(len(set(self.depends_on)) == len(self.depends_on), "Duplicate dependency")
        require(type(self.requires_approval) is bool, "Approval flag must be boolean")

    def to_dict(self):
        return {"step_id": self.step_id, "capability": self.capability,
                "depends_on": list(self.depends_on),
                "requires_approval": self.requires_approval}


@dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        require(self.schema_version == SCHEMA_VERSION, "Unsupported schema version")
        require(type(self.steps) is tuple and 0 < len(self.steps) <= 64,
                "Plan must have 1 to 64 immutable steps")
        require(all(isinstance(step, Step) for step in self.steps), "Invalid step")
        indexed = {step.step_id: step for step in self.steps}
        require(len(indexed) == len(self.steps), "Duplicate step identifier")
        for step in self.steps:
            require(set(step.depends_on) <= indexed.keys(), "Unknown dependency")
        remaining = set(indexed)
        while remaining:
            ready = {key for key in remaining
                     if not (set(indexed[key].depends_on) & remaining)}
            require(bool(ready), "Cyclic execution dependency")
            remaining -= ready

    def to_dict(self):
        return {"schema_version": self.schema_version,
                "steps": [step.to_dict() for step in self.steps]}

    @property
    def content_digest(self):
        return digest(self.to_dict())

    @classmethod
    def from_dict(cls, value):
        fields(value, {"schema_version", "steps"})
        require(type(value["steps"]) is list, "Steps must be an array")
        steps = []
        for item in value["steps"]:
            fields(item, {"step_id", "capability", "depends_on", "requires_approval"})
            require(type(item["depends_on"]) is list, "Dependencies must be an array")
            steps.append(Step(item["step_id"], item["capability"],
                              tuple(item["depends_on"]), item["requires_approval"]))
        return cls(tuple(steps), value["schema_version"])


@dataclass(frozen=True)
class ModelRequest:
    goal: str
    allowed_capabilities: tuple[str, ...]
    max_output_tokens: int = 1024

    def __post_init__(self):
        require(isinstance(self.goal, str) and 0 < len(self.goal.strip()) <= 4096,
                "Goal must contain 1 to 4096 characters")
        require(type(self.allowed_capabilities) is tuple and bool(self.allowed_capabilities),
                "Capabilities must be a non-empty immutable tuple")
        for capability in self.allowed_capabilities:
            identifier(capability)
        require(len(set(self.allowed_capabilities)) == len(self.allowed_capabilities),
                "Duplicate capability")
        require(type(self.max_output_tokens) is int and 0 < self.max_output_tokens <= 8192,
                "Invalid output token budget")


@dataclass(frozen=True)
class ModelResponse:
    content: str
    model_id: str
    input_tokens: int
    output_tokens: int

    def __post_init__(self):
        identifier(self.model_id)
        require(isinstance(self.content, str) and len(self.content.encode("utf-8")) <= 65536,
                "Model content exceeds limit")
        for value in (self.input_tokens, self.output_tokens):
            require(type(value) is int and value >= 0, "Invalid token usage")


@dataclass(frozen=True)
class ModelMaterial:
    extension_id: str
    version: str
    adapter_id: str

    def __post_init__(self):
        for value in (self.extension_id, self.version, self.adapter_id):
            identifier(value)

    @property
    def content_digest(self):
        return digest({"extension_id": self.extension_id, "version": self.version,
                       "adapter_id": self.adapter_id})


@dataclass(frozen=True)
class BuilderBinding:
    tenant_id: str
    principal_id: str
    binding_id: str
    revision: int
    model: ModelMaterial
    allowed_capabilities: tuple[str, ...]
    timeout_seconds: float = 10.0
    max_output_tokens: int = 1024

    def __post_init__(self):
        for value in (self.tenant_id, self.principal_id, self.binding_id):
            identifier(value)
        require(type(self.revision) is int and self.revision > 0, "Invalid binding revision")
        require(type(self.timeout_seconds) in (int, float)
                and math.isfinite(self.timeout_seconds) and 0 < self.timeout_seconds <= 120,
                "Invalid timeout")
        require(isinstance(self.model, ModelMaterial), "Invalid model material")
        ModelRequest("binding-validation", self.allowed_capabilities, self.max_output_tokens)


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str

    def __post_init__(self):
        require(self.role in ("user", "assistant"), "Invalid conversation role")
        require(isinstance(self.content, str) and 0 < len(self.content.strip()) <= 16384,
                "Invalid conversation content")


@dataclass(frozen=True)
class ChatRequest:
    messages: tuple[ChatMessage, ...]
    instructions: str = "Answer the user. Do not claim to execute tools."
    max_output_tokens: int = 1024

    def __post_init__(self):
        require(type(self.messages) is tuple and 0 < len(self.messages) <= 32
                and all(isinstance(item, ChatMessage) for item in self.messages),
                "Invalid conversation messages")
        require(isinstance(self.instructions, str) and 0 < len(self.instructions) <= 32768,
                "Invalid model instructions")
        require(sum(len(item.content) for item in self.messages) <= 65536,
                "Conversation exceeds size limit")
        require(type(self.max_output_tokens) is int and 0 < self.max_output_tokens <= 8192,
                "Invalid output token budget")
