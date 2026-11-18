"""Simple public reference planner. Proprietary planning strategies live elsewhere."""

from dataclasses import replace

from .contracts import BuilderBinding, ModelRequest, Plan, RequestContext, RunSnapshot, strict_json
from .errors import require
from .ports import ModelAccessPort
from .registry import DomainRegistry


class ReferencePlanner:
    def __init__(self, broker: ModelAccessPort, registry: DomainRegistry,
                 binding: BuilderBinding):
        self._broker = broker
        self._registry = registry
        self._binding = binding

    @property
    def snapshot(self) -> RunSnapshot:
        return RunSnapshot(self._binding.binding_id, self._binding.revision,
                           self._binding.model.content_digest,
                           tuple((name, self._registry.resolve(name).version)
                                 for name in self._binding.allowed_capabilities))

    def propose(self, context: RequestContext, goal: str) -> Plan:
        request = ModelRequest(goal, self._binding.allowed_capabilities,
                               self._binding.max_output_tokens)
        response = self._broker.generate(context, self._binding, request)
        proposed = Plan.from_dict(strict_json(response.content))
        validated = []
        for step in proposed.steps:
            require(step.capability in self._binding.allowed_capabilities,
                    "Plan requests an unauthorized capability")
            capability = self._registry.resolve(step.capability)
            validated.append(replace(step, requires_approval=(
                step.requires_approval or capability.requires_approval)))
        return Plan(tuple(validated))
