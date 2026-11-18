"""Trusted API composition. Role bindings are explicit and never fall back."""

from dataclasses import dataclass

from .api_models import ApiModel, ApiProfile, JsonTransport, KEY_ENVIRONMENTS
from .assistant import AssistantService
from .contracts import (ApiSelection, AssistantBinding, BuilderBinding, ModelMaterial, RequestContext, digest)
from .errors import require
from .model_access import ModelBroker, ReferenceDesktop
from .planning import ReferencePlanner
from .ports import ModelConfigurationStorePort
from .registry import Capability, DomainRegistry


@dataclass(frozen=True)
class ApiServices:
    planner: ReferencePlanner | None
    assistant: AssistantService | None


def configure_api_services(context: RequestContext, *, planner: ApiSelection | None = None,
                           assistant: ApiSelection | None = None,
                           transport: JsonTransport | None = None,
                           planner_revision: int = 1, assistant_revision: int = 1,
                           configurations: ModelConfigurationStorePort | None = None) -> ApiServices:
    """Install profiles for one authenticated owner, with no I/O or key reads.

    An omitted role remains unavailable even if the other role is configured.
    """
    desktop = ReferenceDesktop()
    adapters = {}
    materials = {}
    for role, selection in (("planner", planner), ("assistant", assistant)):
        if selection is None:
            continue
        require(isinstance(selection, ApiSelection), "Invalid API selection")
        profile = ApiProfile(context.tenant_id, context.principal_id, selection.provider,
                             selection.model, selection.credential_env if selection.credential_env is not None else
                             KEY_ENVIRONMENTS.get(selection.provider, "UNKNOWN_API_KEY"))
        adapter_id = digest({"owner": profile.owner, "role": role, "provider": profile.provider,
                             "model": profile.model, "credential_env": profile.credential_env})
        revision = planner_revision if role == "planner" else assistant_revision
        require(type(revision) is int and revision > 0, "Invalid role revision")
        material = ModelMaterial("api-" + role, str(revision), adapter_id)
        desktop.register(context, material)
        materials[role] = material
        adapters[adapter_id] = ApiModel(profile, transport)
    broker = ModelBroker(desktop, adapters, configurations=configurations)
    registry = DomainRegistry()
    registry.register(Capability("data.read", "1.0"))
    registry.register(Capability("report.render", "1.0", True))
    planner_service = None
    assistant_service = None
    if planner is not None:
        binding = BuilderBinding(context.tenant_id, context.principal_id, "api-planner", planner_revision,
                                 materials["planner"], ("data.read", "report.render"))
        planner_service = ReferencePlanner(broker, registry, binding)
    if assistant is not None:
        binding = AssistantBinding(context.tenant_id, context.principal_id, "api-assistant", assistant_revision,
                                   materials["assistant"])
        assistant_service = AssistantService(broker, binding)
    return ApiServices(planner_service, assistant_service)


def configure_saved_api_services(context: RequestContext, configurations: ModelConfigurationStorePort,
                                 *, transport: JsonTransport | None = None) -> ApiServices:
    planner = configurations.load(context, "planner")
    assistant = configurations.load(context, "assistant")
    return configure_api_services(
        context, planner=planner.selection if planner else None,
        assistant=assistant.selection if assistant else None,
        planner_revision=planner.revision if planner else 1,
        assistant_revision=assistant.revision if assistant else 1,
        transport=transport, configurations=configurations)
