"""P3 FlowDraft reference contracts and persistence.

FlowDraft records user process intent only.  It cannot emit executable code,
invoke a model, create a Proposal, or mutate a WorkflowRun.
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
from .foundation import OrganizationContext


DOMAINS = ("need", "workflow", "build", "run")
EDGE_KINDS = ("control", "data", "review")


@dataclass(frozen=True)
class FlowPortTemplate:
    port_id: str
    direction: str
    edge_kinds: tuple[str, ...]
    required: bool = False

    def __post_init__(self):
        identifier(self.port_id)
        require(self.direction in ("input", "output"), "Invalid flow port direction")
        require(type(self.edge_kinds) is tuple and self.edge_kinds
                and all(kind in EDGE_KINDS for kind in self.edge_kinds), "Invalid flow port kinds")
        require(type(self.required) is bool, "Invalid flow port requirement")


@dataclass(frozen=True)
class FlowNodeTemplate:
    template_id: str
    version: str
    domain: str
    input_ports: tuple[FlowPortTemplate, ...]
    output_ports: tuple[FlowPortTemplate, ...]
    max_instances: int = 1

    def __post_init__(self):
        identifier(self.template_id)
        identifier(self.version)
        require(self.domain in DOMAINS, "Invalid flow template domain")
        require(type(self.input_ports) is tuple and type(self.output_ports) is tuple,
                "Invalid flow template ports")
        require(all(isinstance(port, FlowPortTemplate) and port.direction == "input" for port in self.input_ports),
                "Invalid flow input port")
        require(all(isinstance(port, FlowPortTemplate) and port.direction == "output" for port in self.output_ports),
                "Invalid flow output port")
        all_ids = [port.port_id for port in self.input_ports + self.output_ports]
        require(len(all_ids) == len(set(all_ids)), "Duplicate flow port")
        require(type(self.max_instances) is int and 1 <= self.max_instances <= 64,
                "Invalid flow template limit")

    @property
    def template_digest(self) -> str:
        return digest({"id": self.template_id, "version": self.version, "domain": self.domain,
                       "inputs": [port.__dict__ for port in self.input_ports],
                       "outputs": [port.__dict__ for port in self.output_ports], "limit": self.max_instances})


def builtin_flow_templates() -> tuple[FlowNodeTemplate, ...]:
    control_in = FlowPortTemplate("in", "input", ("control",))
    control_out = FlowPortTemplate("out", "output", ("control",))
    return (
        FlowNodeTemplate("outcome_alignment", "1_0", "need", (), (control_out,)),
        FlowNodeTemplate("process_plan", "1_0", "workflow", (control_in,), (control_out,)),
        FlowNodeTemplate("requirements_refinement", "1_0", "build", (control_in,), (control_out,)),
        FlowNodeTemplate("human_checkpoint", "1_0", "workflow", (control_in,), (control_out,), 16),
        FlowNodeTemplate("operation_monitoring", "1_0", "run", (control_in,), (), 16),
    )


class FlowTemplateCatalog:
    def __init__(self, templates: tuple[FlowNodeTemplate, ...] | None = None):
        entries = templates or builtin_flow_templates()
        self._templates = {(entry.template_id, entry.version): entry for entry in entries}
        require(len(self._templates) == len(entries), "Duplicate flow template")

    def get(self, template_id: str, version: str) -> FlowNodeTemplate:
        identifier(template_id)
        identifier(version)
        try:
            return self._templates[(template_id, version)]
        except KeyError as exc:
            raise DuomitError("FLOW_TEMPLATE_UNAVAILABLE", "Flow template is unavailable") from exc


@dataclass(frozen=True)
class FlowDraftSnapshot:
    flow_draft_id: str
    owner_ref: str
    name: str
    semantic_revision: int
    layout_revision: int
    semantic_digest: str
    layout_digest: str
    nodes: tuple[dict, ...]
    edges: tuple[dict, ...]


class SqliteFlowDraftStore:
    """Optimistic-concurrency FlowDraft reference adapter."""

    def __init__(self, database: str | Path, catalog: FlowTemplateCatalog | None = None):
        self._catalog = catalog or FlowTemplateCatalog()
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript("""
          CREATE TABLE IF NOT EXISTS flow_drafts (
            id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, owner_membership_id TEXT NOT NULL,
            owner_ref TEXT NOT NULL, name TEXT NOT NULL, semantic_revision INTEGER NOT NULL,
            layout_revision INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS flow_nodes (
            id TEXT PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES flow_drafts(id), template_id TEXT NOT NULL,
            template_version TEXT NOT NULL, template_digest TEXT NOT NULL, display_name TEXT NOT NULL,
            parameters_json TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, revision INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS flow_edges (
            id TEXT PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES flow_drafts(id), from_node_id TEXT NOT NULL,
            from_port_id TEXT NOT NULL, to_node_id TEXT NOT NULL, to_port_id TEXT NOT NULL,
            edge_kind TEXT NOT NULL, required INTEGER NOT NULL, revision INTEGER NOT NULL);
        """)

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

    def _draft(self, context: OrganizationContext, draft_id: str) -> sqlite3.Row:
        identifier(draft_id)
        row = self._db.execute("SELECT * FROM flow_drafts WHERE id=? AND organization_id=? AND owner_membership_id=?",
                               (draft_id, context.organization_id, context.membership_id)).fetchone()
        if row is None:
            raise DuomitError("NOT_FOUND", "Flow draft is not available")
        return row

    def create(self, context: OrganizationContext, owner_ref: str, name: str) -> FlowDraftSnapshot:
        identifier(owner_ref)
        require(isinstance(name, str) and 1 <= len(name.strip()) <= 128, "Invalid flow draft name")
        draft_id = uuid.uuid4().hex
        with self._transaction():
            self._db.execute("INSERT INTO flow_drafts VALUES (?,?,?,?,?,?,?)",
                             (draft_id, context.organization_id, context.membership_id, owner_ref, name.strip(), 1, 1))
            return self._snapshot(context, draft_id)

    def _nodes(self, draft_id: str) -> list[dict]:
        return [dict(row) for row in self._db.execute("SELECT * FROM flow_nodes WHERE draft_id=? ORDER BY rowid", (draft_id,))]

    def _edges(self, draft_id: str) -> list[dict]:
        return [dict(row) for row in self._db.execute("SELECT * FROM flow_edges WHERE draft_id=? ORDER BY rowid", (draft_id,))]

    def _snapshot(self, context: OrganizationContext, draft_id: str) -> FlowDraftSnapshot:
        draft = self._draft(context, draft_id)
        nodes = self._nodes(draft_id)
        edges = self._edges(draft_id)
        semantic_nodes = [{key: node[key] for key in ("id", "template_id", "template_version", "template_digest", "display_name", "parameters_json", "revision")} for node in nodes]
        semantic_edges = [{key: edge[key] for key in ("id", "from_node_id", "from_port_id", "to_node_id", "to_port_id", "edge_kind", "required", "revision")} for edge in edges]
        return FlowDraftSnapshot(draft_id, draft["owner_ref"], draft["name"], draft["semantic_revision"],
                                 draft["layout_revision"], digest({"nodes": semantic_nodes, "edges": semantic_edges}),
                                 digest({"nodes": [{"id": node["id"], "x": node["x"], "y": node["y"]} for node in nodes]}),
                                 tuple(nodes), tuple(edges))

    @staticmethod
    def _layout(x: int, y: int):
        require(type(x) is int and -100000 <= x <= 100000 and type(y) is int and -100000 <= y <= 100000,
                "Invalid flow node layout")

    def add_node(self, context: OrganizationContext, draft_id: str, expected_semantic_revision: int,
                 template_id: str, template_version: str, display_name: str, parameters: dict, *, x: int = 0, y: int = 0) -> FlowDraftSnapshot:
        require(type(expected_semantic_revision) is int and expected_semantic_revision > 0, "Invalid flow revision")
        require(isinstance(display_name, str) and 1 <= len(display_name.strip()) <= 128, "Invalid flow node name")
        require(type(parameters) is dict, "Invalid flow node parameters")
        canonical_json(parameters)
        # P3a templates intentionally expose no parameter schema.  Rejecting
        # unknown keys is safer than accepting inert-looking script/URL fields
        # that a future compiler might accidentally interpret.
        require(not parameters, "Flow template parameters are not available")
        self._layout(x, y)
        template = self._catalog.get(template_id, template_version)
        with self._transaction():
            draft = self._draft(context, draft_id)
            if draft["semantic_revision"] != expected_semantic_revision:
                raise DuomitError("FLOW_REVISION_CONFLICT", "Flow draft semantic revision has changed")
            count = self._db.execute("SELECT count(*) FROM flow_nodes WHERE draft_id=? AND template_id=? AND template_version=?",
                                     (draft_id, template_id, template_version)).fetchone()[0]
            if count >= template.max_instances:
                raise DuomitError("FLOW_NODE_INSTANCE_LIMIT", "Flow template instance limit reached")
            self._db.execute("INSERT INTO flow_nodes VALUES (?,?,?,?,?,?,?,?,?,1)",
                             (uuid.uuid4().hex, draft_id, template_id, template_version, template.template_digest,
                              display_name.strip(), canonical_json(parameters), x, y))
            self._db.execute("UPDATE flow_drafts SET semantic_revision=semantic_revision+1 WHERE id=?", (draft_id,))
            return self._snapshot(context, draft_id)

    def move_node(self, context: OrganizationContext, draft_id: str, expected_layout_revision: int,
                  node_id: str, x: int, y: int) -> FlowDraftSnapshot:
        identifier(node_id)
        self._layout(x, y)
        with self._transaction():
            draft = self._draft(context, draft_id)
            if draft["layout_revision"] != expected_layout_revision:
                raise DuomitError("FLOW_LAYOUT_REVISION_CONFLICT", "Flow draft layout revision has changed")
            updated = self._db.execute("UPDATE flow_nodes SET x=?,y=? WHERE id=? AND draft_id=?", (x, y, node_id, draft_id))
            if updated.rowcount != 1:
                raise DuomitError("NOT_FOUND", "Flow node is not available")
            self._db.execute("UPDATE flow_drafts SET layout_revision=layout_revision+1 WHERE id=?", (draft_id,))
            return self._snapshot(context, draft_id)

    def add_edge(self, context: OrganizationContext, draft_id: str, expected_semantic_revision: int,
                 from_node_id: str, from_port_id: str, to_node_id: str, to_port_id: str, edge_kind: str,
                 *, required: bool = True) -> FlowDraftSnapshot:
        for value in (from_node_id, from_port_id, to_node_id, to_port_id):
            identifier(value)
        require(edge_kind in EDGE_KINDS and type(required) is bool, "Invalid flow edge")
        with self._transaction():
            draft = self._draft(context, draft_id)
            if draft["semantic_revision"] != expected_semantic_revision:
                raise DuomitError("FLOW_REVISION_CONFLICT", "Flow draft semantic revision has changed")
            source = self._db.execute("SELECT * FROM flow_nodes WHERE id=? AND draft_id=?", (from_node_id, draft_id)).fetchone()
            target = self._db.execute("SELECT * FROM flow_nodes WHERE id=? AND draft_id=?", (to_node_id, draft_id)).fetchone()
            if source is None or target is None:
                raise DuomitError("FLOW_PORT_NOT_FOUND", "Flow edge endpoint is not available")
            source_template = self._catalog.get(source["template_id"], source["template_version"])
            target_template = self._catalog.get(target["template_id"], target["template_version"])
            output = next((port for port in source_template.output_ports if port.port_id == from_port_id), None)
            input_port = next((port for port in target_template.input_ports if port.port_id == to_port_id), None)
            if output is None or input_port is None or edge_kind not in output.edge_kinds or edge_kind not in input_port.edge_kinds:
                raise DuomitError("FLOW_EDGE_DIRECTION_INVALID", "Flow edge ports are incompatible")
            if source["id"] == target["id"] or self._would_cycle(draft_id, source["id"], target["id"]):
                raise DuomitError("FLOW_CYCLE_UNBOUNDED", "Flow draft control graph cannot contain a cycle")
            if input_port.required and self._db.execute("SELECT 1 FROM flow_edges WHERE draft_id=? AND to_node_id=? AND to_port_id=? AND required=1", (draft_id, target["id"], to_port_id)).fetchone():
                raise DuomitError("FLOW_REQUIRED_INPUT_UNBOUND", "Required input already has a binding")
            self._db.execute("INSERT INTO flow_edges VALUES (?,?,?,?,?,?,?,?,1)",
                             (uuid.uuid4().hex, draft_id, source["id"], from_port_id, target["id"], to_port_id, edge_kind, int(required)))
            self._db.execute("UPDATE flow_drafts SET semantic_revision=semantic_revision+1 WHERE id=?", (draft_id,))
            return self._snapshot(context, draft_id)

    def _would_cycle(self, draft_id: str, source_id: str, target_id: str) -> bool:
        adjacency: dict[str, list[str]] = {}
        for edge in self._edges(draft_id):
            adjacency.setdefault(edge["from_node_id"], []).append(edge["to_node_id"])
        pending = [target_id]
        visited = set()
        while pending:
            current = pending.pop()
            if current == source_id:
                return True
            if current not in visited:
                visited.add(current)
                pending.extend(adjacency.get(current, ()))
        return False

    def validate(self, context: OrganizationContext, draft_id: str) -> dict:
        with self._transaction():
            snapshot = self._snapshot(context, draft_id)
            bound = {(edge["to_node_id"], edge["to_port_id"]) for edge in snapshot.edges if edge["required"]}
            missing = []
            for node in snapshot.nodes:
                template = self._catalog.get(node["template_id"], node["template_version"])
                for port in template.input_ports:
                    if port.required and (node["id"], port.port_id) not in bound:
                        missing.append({"node_id": node["id"], "port_id": port.port_id})
            return {"flow_draft_id": draft_id, "semantic_revision": snapshot.semantic_revision,
                    "status": "valid" if not missing else "invalid", "required_inputs_unbound": missing,
                    "semantic_digest": snapshot.semantic_digest}
