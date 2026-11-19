"""Pinned NodeDescriptor catalog for Builder review-time selection.

Descriptors declare compatibility only.  Registering one neither runs code nor
installs an extension; execution remains a later Runtime/Desktop concern.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .contracts import canonical_json, digest, identifier
from .errors import DuomitError, require
from .foundation import OrganizationContext


NODE_DOMAINS = ("need", "workflow", "build", "run")
EXECUTION_MODES = ("human", "deterministic", "model_assisted", "brokered_external")


@dataclass(frozen=True)
class NodeDescriptor:
    node_id: str
    version: str
    domain: str
    role: str
    execution_modes: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    input_schema_ref: str
    output_schema_ref: str

    def __post_init__(self):
        for value in (self.node_id, self.version, self.role, self.input_schema_ref, self.output_schema_ref):
            identifier(value)
        require(self.domain in NODE_DOMAINS, "Invalid node domain")
        require(type(self.execution_modes) is tuple and self.execution_modes
                and len(set(self.execution_modes)) == len(self.execution_modes)
                and all(mode in EXECUTION_MODES for mode in self.execution_modes), "Invalid node execution modes")
        require(type(self.required_capabilities) is tuple and len(set(self.required_capabilities)) == len(self.required_capabilities),
                "Invalid node capabilities")
        for capability in self.required_capabilities:
            identifier(capability)

    @property
    def content_digest(self) -> str:
        return digest({"node_id": self.node_id, "version": self.version, "domain": self.domain,
                       "role": self.role, "execution_modes": self.execution_modes,
                       "required_capabilities": self.required_capabilities,
                       "input_schema_ref": self.input_schema_ref, "output_schema_ref": self.output_schema_ref})


class SqliteNodeCatalog:
    def __init__(self, database: str | Path):
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""CREATE TABLE IF NOT EXISTS node_descriptors (
          organization_id TEXT NOT NULL, node_id TEXT NOT NULL, version TEXT NOT NULL,
          body_json TEXT NOT NULL, content_digest TEXT NOT NULL,
          PRIMARY KEY(organization_id,node_id,version))""")

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

    def register(self, context: OrganizationContext, descriptor: NodeDescriptor) -> NodeDescriptor:
        require(isinstance(descriptor, NodeDescriptor), "Invalid node descriptor")
        body = canonical_json(descriptor.__dict__)
        with self._transaction():
            row = self._db.execute("SELECT body_json,content_digest FROM node_descriptors WHERE organization_id=? AND node_id=? AND version=?",
                                   (context.organization_id, descriptor.node_id, descriptor.version)).fetchone()
            if row is not None:
                if row["body_json"] != body or row["content_digest"] != descriptor.content_digest:
                    raise DuomitError("CONFLICT", "Node descriptor versions are immutable")
                return descriptor
            self._db.execute("INSERT INTO node_descriptors VALUES (?,?,?,?,?)",
                             (context.organization_id, descriptor.node_id, descriptor.version, body, descriptor.content_digest))
            return descriptor

    def get(self, context: OrganizationContext, node_id: str, version: str) -> NodeDescriptor:
        identifier(node_id)
        identifier(version)
        row = self._db.execute("SELECT body_json,content_digest FROM node_descriptors WHERE organization_id=? AND node_id=? AND version=?",
                               (context.organization_id, node_id, version)).fetchone()
        if row is None:
            raise DuomitError("NOT_FOUND", "Node descriptor is not available")
        import json
        body = json.loads(row["body_json"])
        descriptor = NodeDescriptor(**{key: tuple(value) if key in ("execution_modes", "required_capabilities") else value
                                       for key, value in body.items()})
        if descriptor.content_digest != row["content_digest"]:
            raise DuomitError("MATERIAL_CORRUPT", "Node descriptor digest mismatch")
        return descriptor
