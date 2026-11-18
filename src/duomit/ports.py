"""Core ports contain no provider SDK imports, network calls or private strategies."""

from typing import Protocol

from .contracts import (AssistantBinding, BuilderBinding, ChatMessage, ChatRequest,
                        ModelRequest, ModelResponse, Plan, RequestContext)


class ModelPort(Protocol):
    def generate(self, request: ModelRequest, *, timeout_seconds: float) -> ModelResponse:
        """Adapter must enforce the timeout; credentials remain outside requests."""
        ...


class PlanningPort(Protocol):
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
