"""Offline reference app; all identity and adapter installation is trusted setup."""

import argparse
import json

from .contracts import RequestContext
from .model_access import BuilderBinding, ModelBroker, ModelMaterial, ReferenceDesktop
from .models import IndependentModel, SequentialModel
from .planning import ReferencePlanner
from .registry import Capability, DomainRegistry


def reference_components(model: str = "sequential"):
    context = RequestContext("demo-tenant", "demo-user", "demo-request", "demo-trace")
    registry = DomainRegistry()
    registry.register(Capability("data.read", "1.0"))
    registry.register(Capability("report.render", "1.0", True))
    desktop = ReferenceDesktop()
    material = ModelMaterial("offline-model", "1.0", model)
    desktop.register(context, material)
    binding = BuilderBinding(context.tenant_id, context.principal_id, "demo-binding", 1,
                             material, ("data.read", "report.render"))
    broker = ModelBroker(desktop, {"sequential": SequentialModel(),
                                   "independent": IndependentModel()})
    return context, ReferencePlanner(broker, registry, binding)


def main():
    parser = argparse.ArgumentParser(description="duomit offline reference planner")
    parser.add_argument("--model", choices=("sequential", "independent"), default="sequential")
    parser.add_argument("--goal", default="Prepare a data report")
    args = parser.parse_args()
    context, planner = reference_components(args.model)
    print(json.dumps(planner.propose(context, args.goal).to_dict(), indent=2))


if __name__ == "__main__":
    main()
