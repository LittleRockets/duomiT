"""Core ports contain no provider SDK imports, network calls or private strategies."""

from typing import Protocol

from .contracts import (ApiSelection, AssistantBinding, BuilderBinding, ChatMessage, ChatRequest,
                        ExtensionVersion, MirrorLease, ModelConfiguration, ModelRequest,
                        ModelResponse, Plan, RequestContext, RunSnapshot)


class ModelPort(Protocol):
    def generate(self, request: ModelRequest, *, timeout_seconds: float) -> ModelResponse:
        """Adapter must enforce the timeout; credentials remain outside requests."""
        ...


class PlanningPort(Protocol):
    @property
    def snapshot(self) -> RunSnapshot: ...
    def propose(self, context: RequestContext, goal: str) -> Plan: ...


class StepExecutorPort(Protocol):
    def execute(self, context: RequestContext, capability: str,
                operation_key: str) -> str:
        """Return a safe artifact reference. Never infer permission from a plan."""
        ...


class ModelAccessPort(Protocol):
    def generate(self, context: RequestContext, binding: BuilderBinding,
                 request: ModelRequest) -> ModelResponse: ...


class ChatPort(Protocol):
    def complete(self, request: ChatRequest, *, timeout_seconds: float) -> ModelResponse: ...


class AssistantAccessPort(Protocol):
    def complete(self, context: RequestContext, binding: AssistantBinding,
                 request: ChatRequest) -> ModelResponse: ...


class AssistantPort(Protocol):
    def respond(self, context: RequestContext, messages: tuple[ChatMessage, ...]) -> ModelResponse: ...


class ModelConfigurationStorePort(Protocol):
    def save(self, context: RequestContext, role: str, selection: ApiSelection,
             expected_revision: int) -> ModelConfiguration: ...
    def load(self, context: RequestContext, role: str,
             revision: int | None = None) -> ModelConfiguration | None: ...
    def disable(self, context: RequestContext, role: str, expected_revision: int) -> int: ...


class WorkflowRuntimePort(Protocol):
    def create(self, context: RequestContext, plan: Plan, idempotency_key: str,
               *, snapshot: RunSnapshot | None = None) -> str: ...
    def inspect(self, context: RequestContext, run_id: str) -> dict: ...
    def drive(self, context: RequestContext, run_id: str, executor: StepExecutorPort) -> dict: ...


class IdentityPort(Protocol):
    def authenticate(self, token: str, *, request_id: str | None = None,
                     trace_id: str | None = None) -> RequestContext: ...


class DesktopPort(Protocol):
    def acquire(self, context: RequestContext, extension_id: str, version: str, run_id: str,
                node_id: str, idempotency_key: str, *, valid_seconds: float = 60) -> MirrorLease: ...
    def renew(self, context: RequestContext, lease_id: str, expected_fence: int,
              *, valid_seconds: float = 60) -> MirrorLease: ...
    def release(self, context: RequestContext, lease_id: str, expected_fence: int) -> MirrorLease: ...
    def read(self, context: RequestContext, lease_id: str, expected_fence: int,
             run_id: str, node_id: str) -> bytes: ...
