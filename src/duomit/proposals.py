"""P3 review-only Builder Proposal reference adapter.

The adapter receives already-pinned ProcessIntent records and explicit public
descriptor bindings.  Choosing/ranking alternatives remains outside this
reference implementation; no LLM or private strategy is exposed here.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import uuid

from .contracts import canonical_json, digest, identifier
from .errors import DuomitError, require
from .flow_compiler import FlowCompilation
from .foundation import OrganizationContext
from .node_catalog import NodeDescriptor, SqliteNodeCatalog


@dataclass(frozen=True)
class PinnedNodeDescriptor:
    node_id: str
    version: str
    content_digest: str


@dataclass(frozen=True)
class DomainNodeProposal:
    proposal_node_id: str
    source_intent_id: str
    domain: str
    descriptor: PinnedNodeDescriptor
    role: str


@dataclass(frozen=True)
class DecompositionProposal:
    proposal_id: str
    task_id: str
    flow_draft_id: str
    flow_semantic_digest: str
    compilation_digest: str
    state: str
    revision: int
    domain_nodes: tuple[DomainNodeProposal, ...]
    digest: str

@dataclass(frozen=True)
class ProposalReviewDecision:
    decision_id: str
    proposal_id: str
    decision: str
    rationale: str
    revision: int


class SqliteProposalStore:
    """Stores immutable, review-only proposals under the requesting member."""

    def __init__(self, database: str | Path, catalog: SqliteNodeCatalog):
        require(isinstance(catalog, SqliteNodeCatalog), "Invalid node catalog")
        self._catalog = catalog
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""CREATE TABLE IF NOT EXISTS decomposition_proposals (
          id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, owner_membership_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL, request_digest TEXT NOT NULL, body_json TEXT NOT NULL,
          UNIQUE(organization_id,owner_membership_id,idempotency_key))""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS proposal_review_decisions (
          id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, organization_id TEXT NOT NULL,
          owner_membership_id TEXT NOT NULL, decision TEXT NOT NULL, rationale TEXT NOT NULL,
          revision INTEGER NOT NULL, UNIQUE(proposal_id,revision))""")

    def close(self):
        self._db.close()

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    @staticmethod
    def _from_body(body: dict) -> DecompositionProposal:
        nodes = tuple(DomainNodeProposal(item["proposal_node_id"], item["source_intent_id"], item["domain"],
                                         PinnedNodeDescriptor(**item["descriptor"]), item["role"])
                      for item in body["domain_nodes"])
        return DecompositionProposal(body["proposal_id"], body["task_id"], body["flow_draft_id"],
                                     body["flow_semantic_digest"], body["compilation_digest"], body["state"],
                                     body["revision"], nodes, body["digest"])

    def propose(self, context: OrganizationContext, task_id: str, compilation: FlowCompilation,
                descriptors_by_domain: dict[str, tuple[str, str]], idempotency_key: str) -> DecompositionProposal:
        identifier(task_id)
        identifier(idempotency_key)
        require(isinstance(compilation, FlowCompilation), "Invalid flow compilation")
        require(type(descriptors_by_domain) is dict and descriptors_by_domain, "Invalid descriptor bindings")
        request_digest = digest({"task_id": task_id, "compilation": compilation.digest,
                                 "descriptors": descriptors_by_domain})
        with self._transaction():
            prior = self._db.execute("SELECT request_digest,body_json FROM decomposition_proposals WHERE organization_id=? AND owner_membership_id=? AND idempotency_key=?",
                                     (context.organization_id, context.membership_id, idempotency_key)).fetchone()
            if prior is not None:
                if prior["request_digest"] != request_digest:
                    raise DuomitError("CONFLICT", "Idempotency key has different content")
                return self._from_body(json.loads(prior["body_json"]))
            domain_nodes = []
            for intent in compilation.intents:
                binding = descriptors_by_domain.get(intent.domain)
                if type(binding) is not tuple or len(binding) != 2:
                    raise DuomitError("DECOMPOSITION_INVALID", "Missing pinned descriptor for intent domain")
                descriptor = self._catalog.get(context, binding[0], binding[1])
                if descriptor.domain != intent.domain:
                    raise DuomitError("DECOMPOSITION_INVALID", "Descriptor domain is incompatible with intent")
                domain_nodes.append(DomainNodeProposal(uuid.uuid4().hex, intent.intent_id, intent.domain,
                                    PinnedNodeDescriptor(descriptor.node_id, descriptor.version, descriptor.content_digest),
                                    descriptor.role))
            body = {"proposal_id": uuid.uuid4().hex, "task_id": task_id, "flow_draft_id": compilation.flow_draft_id,
                    "flow_semantic_digest": compilation.semantic_digest, "compilation_digest": compilation.digest,
                    "state": "ready_for_review", "revision": 1,
                    "domain_nodes": [{"proposal_node_id": item.proposal_node_id, "source_intent_id": item.source_intent_id,
                                      "domain": item.domain, "descriptor": item.descriptor.__dict__, "role": item.role}
                                     for item in domain_nodes]}
            body["digest"] = digest(body)
            self._db.execute("INSERT INTO decomposition_proposals VALUES (?,?,?,?,?,?)",
                             (body["proposal_id"], context.organization_id, context.membership_id, idempotency_key,
                              request_digest, canonical_json(body)))
            return self._from_body(body)

    def get(self, context: OrganizationContext, proposal_id: str) -> DecompositionProposal:
        identifier(proposal_id)
        row = self._db.execute("SELECT body_json FROM decomposition_proposals WHERE id=? AND organization_id=? AND owner_membership_id=?",
                               (proposal_id, context.organization_id, context.membership_id)).fetchone()
        if row is None:
            raise DuomitError("NOT_FOUND", "Proposal is not available")
        proposal = self._from_body(json.loads(row["body_json"]))
        body = json.loads(row["body_json"])
        expected = body.copy(); expected.pop("digest")
        if proposal.digest != digest(expected):
            raise DuomitError("MATERIAL_CORRUPT", "Proposal digest mismatch")
        return proposal

    def decide(self, context: OrganizationContext, proposal_id: str, expected_revision: int,
               decision: str, rationale: str) -> ProposalReviewDecision:
        identifier(proposal_id)
        require(type(expected_revision) is int and expected_revision > 0, "Invalid proposal revision")
        require(decision in ("accepted", "rejected"), "Invalid proposal decision")
        require(isinstance(rationale, str) and 1 <= len(rationale.strip()) <= 2048, "Review rationale is required")
        self.get(context, proposal_id)
        with self._transaction():
            prior = self._db.execute("SELECT revision FROM proposal_review_decisions WHERE proposal_id=? ORDER BY revision DESC LIMIT 1", (proposal_id,)).fetchone()
            current = 1 if prior is None else prior["revision"] + 1
            if expected_revision != current:
                raise DuomitError("PROPOSAL_REVIEW_CONFLICT", "Proposal review has changed")
            decision_id = uuid.uuid4().hex
            self._db.execute("INSERT INTO proposal_review_decisions VALUES (?,?,?,?,?,?,?)",
                             (decision_id, proposal_id, context.organization_id, context.membership_id,
                              decision, rationale.strip(), current))
            return ProposalReviewDecision(decision_id, proposal_id, decision, rationale.strip(), current)
