"""Reference model material distribution and per-user access broker.

This is a trusted, in-process fixture, not the S2 Desktop mirror lease service.
Only deployment code installs model adapters. Material never includes credentials.
"""

from .contracts import (AssistantBinding, BuilderBinding, ChatRequest, ModelMaterial,
                        ModelRequest, ModelResponse, RequestContext)
from .errors import DuomitError, require
from .ports import ChatPort, ModelConfigurationStorePort, ModelPort


class ReferenceDesktop:
    """Immutable registration and tenant-scoped material access; never runs a model."""

    def __init__(self):
        self._materials: dict[tuple[str, str, str, str], ModelMaterial] = {}

    def register(self, context: RequestContext, material: ModelMaterial) -> None:
        key = (context.tenant_id, context.principal_id, material.extension_id, material.version)
        previous = self._materials.get(key)
        if previous is not None and previous != material:
            raise DuomitError("CONFLICT", "Published material version is immutable")
        self._materials[key] = material

    def acquire(self, context: RequestContext, material: ModelMaterial) -> ModelMaterial:
        key = (context.tenant_id, context.principal_id, material.extension_id, material.version)
        registered = self._materials.get(key)
        if registered is None or registered != material:
            raise DuomitError("NOT_FOUND", "Model material is not available")
        return registered


class ModelBroker:
    """Runs adapters after checking owner and Desktop material; no provider selection in core."""

    def __init__(self, desktop: ReferenceDesktop, adapters: dict[str, ModelPort | ChatPort],
                 *, configurations: ModelConfigurationStorePort | None = None):
        self._desktop = desktop
        self._adapters = dict(adapters)
        self._configurations = configurations

    def generate(self, context: RequestContext, binding: BuilderBinding,
                 request: ModelRequest) -> ModelResponse:
        require(type(binding) is BuilderBinding, "Expected planner binding")
        require(set(request.allowed_capabilities) <= set(binding.allowed_capabilities),
                "Request exceeds binding capabilities")
        adapter = self._resolve(context, binding, request.max_output_tokens)
        return self._invoke(adapter.generate, request, binding)

    def complete(self, context: RequestContext, binding: AssistantBinding,
                 request: ChatRequest) -> ModelResponse:
        require(type(binding) is AssistantBinding, "Expected assistant binding")
        adapter = self._resolve(context, binding, request.max_output_tokens)
        if not callable(getattr(adapter, "complete", None)):
            raise DuomitError("MODEL_UNAVAILABLE", "Assistant API is not available")
        return self._invoke(adapter.complete, request, binding)

    def _resolve(self, context, binding, max_output_tokens):
        if (context.tenant_id, context.principal_id) != (binding.tenant_id, binding.principal_id):
            raise DuomitError("NOT_FOUND", "Builder binding is not available")
        if self._configurations is not None:
            role = "planner" if type(binding) is BuilderBinding else "assistant"
            current = self._configurations.load(context, role)
            if current is None or current.revision != binding.revision:
                raise DuomitError("CONFIGURATION_CHANGED", "Model binding is no longer current")
        require(max_output_tokens <= binding.max_output_tokens,
                "Request exceeds binding token budget")
        material = self._desktop.acquire(context, binding.model)
        adapter = self._adapters.get(material.adapter_id)
        if adapter is None:
            raise DuomitError("MODEL_UNAVAILABLE", "Model adapter is not installed")
        if getattr(adapter, "owner", (context.tenant_id, context.principal_id)) != (
                context.tenant_id, context.principal_id):
            raise DuomitError("NOT_FOUND", "Model adapter is not available")
        return adapter

    def _invoke(self, method, request, binding):
        try:
            response = method(request, timeout_seconds=binding.timeout_seconds)
        except TimeoutError:
            raise DuomitError("MODEL_TIMEOUT", "Model request timed out", True) from None
        except DuomitError:
            raise
        except Exception:
            # Never expose provider exception messages, credentials or prompt contents.
            raise DuomitError("MODEL_FAILED", "Model request failed") from None
        require(isinstance(response, ModelResponse), "Invalid model response")
        require(response.output_tokens <= request.max_output_tokens,
                "Model exceeded output token budget")
        return response
