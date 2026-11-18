"""Offline reference app; all identity and adapter installation is trusted setup."""

import argparse
import json
import sys

from .contracts import RequestContext
from .errors import DuomitError
from .local_runtime import LocalRuntime
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
    parser.add_argument("--database", help="Persist the simulated workflow in this SQLite file")
    parser.add_argument("--approve", action="store_true", help="Approve waiting demo steps as demo-user")
    args = parser.parse_args()
    context, planner = reference_components(args.model)
    plan = planner.propose(context, args.goal)
    if not args.database:
        print(json.dumps(plan.to_dict(), indent=2))
        return
    runtime = LocalRuntime(args.database, (Capability("data.read", "1.0"),
                                          Capability("report.render", "1.0", True)))

    class SimulatedExecutor:
        def execute(self, context, capability, operation_key):
            return "artifact:simulated-" + operation_key

    try:
        run_id = runtime.create(context, plan, "demo-run")
        snapshot = runtime.drive(context, run_id, SimulatedExecutor())
        if args.approve:
            while snapshot["state"] == "waiting_approval":
                step = next(item for item in snapshot["steps"] if item["state"] == "waiting_approval")
                runtime.approve(context, run_id, step["id"], step["operation_key"], snapshot["revision"])
                snapshot = runtime.drive(context, run_id, SimulatedExecutor())
        print(json.dumps(snapshot, indent=2))
    finally:
        runtime.close()


if __name__ == "__main__":
    try:
        main()
    except DuomitError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
