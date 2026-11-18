"""Offline model adapters for conformance tests; no model weights or network SDK."""

from .contracts import ModelRequest, ModelResponse, Plan, Step, canonical_json
from .errors import require


class SequentialModel:
    def generate(self, request: ModelRequest, *, timeout_seconds: float) -> ModelResponse:
        require(timeout_seconds > 0, "Invalid timeout")
        steps = tuple(Step(f"step-{i}", name, (f"step-{i-1}",) if i else ())
                      for i, name in enumerate(request.allowed_capabilities))
        return ModelResponse(canonical_json(Plan(steps).to_dict()), "offline-sequential", 0, 0)


class IndependentModel:
    def generate(self, request: ModelRequest, *, timeout_seconds: float) -> ModelResponse:
        require(timeout_seconds > 0, "Invalid timeout")
        steps = tuple(Step(f"step-{i}", name)
                      for i, name in enumerate(request.allowed_capabilities))
        return ModelResponse(canonical_json(Plan(steps).to_dict()), "offline-independent", 0, 0)
