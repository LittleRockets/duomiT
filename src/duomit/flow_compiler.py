"""Deterministic FlowDraft-to-ProcessIntent compiler for P3 review.

This is deliberately narrower than the Builder: it turns validated user
intent into pinned, inspectable records only.  It has no model client,
Proposal persistence, WorkUnit, broker, or runtime dependency.
"""

from dataclasses import dataclass

from .contracts import digest
from .errors import DuomitError, require
from .flow_drafts import SqliteFlowDraftStore
from .foundation import OrganizationContext


@dataclass(frozen=True)
class ProcessIntent:
    intent_id: str
    domain: str
    source_node_id: str
    template_id: str
    template_version: str
    template_digest: str
    display_name: str


@dataclass(frozen=True)
class FlowCompilation:
    flow_draft_id: str
    semantic_revision: int
    semantic_digest: str
    intents: tuple[ProcessIntent, ...]
    edge_map: tuple[dict, ...]
    digest: str


def compile_flow_draft(context: OrganizationContext, store: SqliteFlowDraftStore, draft_id: str,
                       expected_semantic_digest: str) -> FlowCompilation:
    """Compile a validated draft without creating any executable object."""
    require(isinstance(store, SqliteFlowDraftStore), "Invalid flow draft store")
    require(isinstance(expected_semantic_digest, str) and len(expected_semantic_digest) == 64,
            "Invalid flow semantic digest")
    snapshot = store.validated_snapshot(context, draft_id, expected_semantic_digest)
    intents = tuple(ProcessIntent(
        intent_id=digest({"draft": draft_id, "node": node["id"], "semantic": snapshot.semantic_digest})[:32],
        domain=store._catalog.get(node["template_id"], node["template_version"]).domain,
        source_node_id=node["id"], template_id=node["template_id"], template_version=node["template_version"],
        template_digest=node["template_digest"], display_name=node["display_name"]
    ) for node in snapshot.nodes)
    edge_map = tuple({"source_edge_id": edge["id"], "from_intent_id": next(intent.intent_id for intent in intents if intent.source_node_id == edge["from_node_id"]),
                      "to_intent_id": next(intent.intent_id for intent in intents if intent.source_node_id == edge["to_node_id"]),
                      "edge_kind": edge["edge_kind"], "required": bool(edge["required"])} for edge in snapshot.edges)
    compilation_digest = digest({"flow_draft_id": draft_id, "semantic_digest": snapshot.semantic_digest,
                                 "intents": [intent.__dict__ for intent in intents], "edges": edge_map})
    return FlowCompilation(draft_id, snapshot.semantic_revision, snapshot.semantic_digest, intents, edge_map,
                           compilation_digest)
