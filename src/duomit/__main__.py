"""Reference CLI; API calls are opt-in and identities are trusted local demo setup."""

import argparse
from contextlib import ExitStack
import json
import os
import sys

from .api_configuration import ApiSelection, configure_api_services, configure_saved_api_services
from .api_models import PROVIDERS
from .configuration_store import SqliteModelConfigurationStore
from .contracts import ChatMessage, RequestContext
from .errors import DuomitError
from .local_runtime import LocalRuntime
from .identity import LocalIdentityStore
from .model_access import BuilderBinding, ModelBroker, ModelMaterial, ReferenceDesktop
from .models import IndependentModel, SequentialModel
from .planning import ReferencePlanner
from .registry import Capability, DomainRegistry
from .foundation import OrganizationContext
from .desktop_app import launch_native_desktop
from .workspace import LocalWorkspace


def reference_components(model: str = "sequential", context: RequestContext | None = None):
    context = context or RequestContext("demo-tenant", "demo-user", "demo-request", "demo-trace")
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
    parser = argparse.ArgumentParser(description="duomit reference planner and API assistant")
    parser.add_argument("--model", choices=("sequential", "independent"), default="sequential")
    parser.add_argument("--goal", default="Prepare a data report")
    parser.add_argument("--database", help="Persist the simulated workflow in this SQLite file")
    parser.add_argument("--approve", action="store_true", help="Approve waiting demo steps as demo-user")
    parser.add_argument("--resume", action="store_true", help="Resume an existing demo run without replanning")
    parser.add_argument("--provider", choices=PROVIDERS, help="Opt into a planning API call")
    parser.add_argument("--model-id", help="Exact API planning model ID available to your account")
    parser.add_argument("--api-key-env", help="Environment variable holding the planning API key")
    parser.add_argument("--assistant-provider", choices=PROVIDERS)
    parser.add_argument("--assistant-model-id")
    parser.add_argument("--assistant-api-key-env")
    parser.add_argument("--ask", help="Send text only to the separately configured API assistant")
    parser.add_argument("--config-database", help="Store or load API role configuration; no key values")
    parser.add_argument("--save-config", action="store_true", help="Save explicit API selections without calling models")
    parser.add_argument("--identity-database", help="Validate a local access token and derive the user identity")
    parser.add_argument("--token-env", default="DUOMIT_ACCESS_TOKEN", help="Environment variable containing a local access token")
    parser.add_argument("--workspace-database", help="Local Project and Task reference workspace database")
    parser.add_argument("--workspace-project", help="Create a Project or address an existing Project ID")
    parser.add_argument("--workspace-create-project", action="store_true", help="Create --workspace-project as a Project name")
    parser.add_argument("--workspace-task", help="Create a Task title in the addressed Project")
    parser.add_argument("--workspace-goal", help="Task goal used with --workspace-task")
    parser.add_argument("--desktop-workspace-database", help="Open the native Desktop shell against a reference workspace")
    parser.add_argument("--desktop-project", help="Project ID rendered by the native Desktop shell")
    args = parser.parse_args()
    if args.desktop_workspace_database:
        if not args.desktop_project:
            parser.error("native desktop mode requires --desktop-project")
        _native_desktop(args)
        return
    if args.workspace_database:
        if not args.workspace_project or bool(args.workspace_task) != bool(args.workspace_goal) or (args.workspace_task and args.workspace_create_project):
            parser.error("workspace mode requires --workspace-project and both --workspace-task/--workspace-goal when creating a task")
        _workspace(args)
        return
    if bool(args.provider) != bool(args.model_id) or (args.api_key_env and not args.provider):
        parser.error("--provider and --model-id must be configured together")
    if bool(args.assistant_provider) != bool(args.assistant_model_id) or (
            args.assistant_api_key_env and not args.assistant_provider):
        parser.error("--assistant-provider and --assistant-model-id must be configured together")
    if args.resume and not args.database:
        parser.error("--resume requires --database")
    if args.ask is not None and ((not args.assistant_provider and not args.config_database) or args.database or args.approve or args.resume):
        parser.error("--ask needs a separate assistant configuration and cannot run a workflow")
    if args.save_config and (not args.config_database or not (args.provider or args.assistant_provider)
                             or args.ask is not None or args.database or args.resume or args.approve):
        parser.error("--save-config requires --config-database and API selection, without execution options")
    if args.config_database and (args.provider or args.assistant_provider) and not args.save_config:
        parser.error("Use --save-config to update persisted API selections")
    context, planner = reference_components(args.model)
    planning_selection = ApiSelection(args.provider, args.model_id, args.api_key_env) if args.provider else None
    assistant_selection = ApiSelection(args.assistant_provider, args.assistant_model_id,
                                       args.assistant_api_key_env) if args.assistant_provider else None
    with ExitStack() as cleanup:
        if args.identity_database:
            identities = LocalIdentityStore(args.identity_database)
            cleanup.callback(identities.close)
            context, planner = reference_components(args.model, identities.authenticate(os.environ.get(args.token_env)))
        if args.config_database:
            store = SqliteModelConfigurationStore(args.config_database)
            cleanup.callback(store.close)
            if args.save_config:
                # Validate provider profiles without reading credentials or making requests.
                configure_api_services(context, planner=planning_selection, assistant=assistant_selection)
                saved = {}
                for role, selection in (("planner", planning_selection), ("assistant", assistant_selection)):
                    if selection is not None:
                        current = store.load(context, role)
                        saved[role] = store.save(context, role, selection,
                                                 current.revision if current else 0).revision
                print(json.dumps({"saved_revisions": saved}))
                return
            services = configure_saved_api_services(context, store)
            if args.ask is None and not args.resume and not args.approve and services.planner is None:
                raise DuomitError("MODEL_UNAVAILABLE", "No planning model is configured")
        else:
            services = configure_api_services(context, planner=planning_selection, assistant=assistant_selection)
        _execute(args, context, planner, services)


def _execute(args, context, planner, services):
    if args.ask is not None:
        if services.assistant is None:
            raise DuomitError("MODEL_UNAVAILABLE", "No assistant model is configured")
        response = services.assistant.respond(context, (ChatMessage("user", args.ask),))
        print(json.dumps({"content": response.content, "model_id": response.model_id,
                          "usage": {"input_tokens": response.input_tokens,
                                    "output_tokens": response.output_tokens}}, indent=2))
        return
    if services.planner is not None:
        planner = services.planner
    if not args.database:
        plan = planner.propose(context, args.goal)
        print(json.dumps(plan.to_dict(), indent=2))
        return
    runtime = LocalRuntime(args.database, (Capability("data.read", "1.0"),
                                          Capability("report.render", "1.0", True)))

    class SimulatedExecutor:
        def execute(self, context, capability, operation_key):
            return "artifact:simulated-" + operation_key

    try:
        run_id = runtime.find_run(context, "demo-run") if args.resume or args.approve else None
        if args.resume and run_id is None:
            raise DuomitError("NOT_FOUND", "No existing demo run to resume")
        if run_id is None:
            plan = planner.propose(context, args.goal)
            run_id = runtime.create(context, plan, "demo-run", snapshot=planner.snapshot)
        snapshot = runtime.drive(context, run_id, SimulatedExecutor())
        if args.approve:
            while snapshot["state"] == "waiting_approval":
                step = next(item for item in snapshot["steps"] if item["state"] == "waiting_approval")
                runtime.approve(context, run_id, step["id"], step["operation_key"], snapshot["revision"])
                snapshot = runtime.drive(context, run_id, SimulatedExecutor())
        print(json.dumps(snapshot, indent=2))
    finally:
        runtime.close()


def _workspace(args):
    context = OrganizationContext("local-org", "local-member", "local-member", "local-device",
                                  "local-session", "local-request", "local-trace", 1)
    workspace = LocalWorkspace(args.workspace_database)
    try:
        project_id = args.workspace_project
        if args.workspace_create_project:
            project_id = workspace.create_project(context, project_id)
            print(json.dumps({"project_id": project_id, "created": True}, indent=2))
            return
        if not args.workspace_task:
            print(json.dumps({"project_id": project_id, "tasks": workspace.list_tasks(context, project_id)}, indent=2))
            return
        task_id = workspace.create_task(context, project_id, args.workspace_task, args.workspace_goal)
        print(json.dumps({"project_id": project_id, "task_id": task_id, "created": True}, indent=2))
    finally:
        workspace.close()


def _native_desktop(args):
    """Explicit local test entry point; production identity is wired in a later adapter."""
    context = OrganizationContext("local-org", "local-member", "local-member", "local-device",
                                  "local-session", "local-request", "local-trace", 1)
    launch_native_desktop(args.desktop_workspace_database, context, args.desktop_project)


if __name__ == "__main__":
    try:
        main()
    except DuomitError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
