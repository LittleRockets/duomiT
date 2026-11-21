"""Build-domain delivery specifications with evidence paths, never execution."""

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
from .need_records import NeedRecord
from .task_revisions import TaskRevision
from .workflow_specs import ProcessSpecification


@dataclass(frozen=True)
class DeliverableSpec:
    deliverable_id: str
    description: str
    owner_scope: str
    acceptance_criterion_ids: tuple[str, ...]

    def __post_init__(self):
        identifier(self.deliverable_id)
        require(isinstance(self.description, str) and 1 <= len(self.description.strip()) <= 4096,
                "Invalid deliverable description")
        require(self.owner_scope in ("task", "project", "owner_only"), "Invalid deliverable owner scope")
        require(type(self.acceptance_criterion_ids) is tuple and self.acceptance_criterion_ids,
                "Deliverable requires acceptance")
        for criterion_id in self.acceptance_criterion_ids:
            identifier(criterion_id)


@dataclass(frozen=True)
class VerificationSpec:
    verification_id: str
    criterion_id: str
    method: str
    blocking: bool

    def __post_init__(self):
        identifier(self.verification_id); identifier(self.criterion_id)
        require(self.method in ("static", "test", "review", "integration", "security", "performance"),
                "Invalid verification method")
        require(type(self.blocking) is bool, "Invalid verification blocking flag")


@dataclass(frozen=True)
class BuildSpecification:
    build_spec_id: str
    task_revision_id: str
    need_id: str
    process_spec_id: str
    deliverables: tuple[DeliverableSpec, ...]
    verification_plan: tuple[VerificationSpec, ...]
    digest: str


class SqliteBuildSpecStore:
    def __init__(self, database: str | Path):
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""CREATE TABLE IF NOT EXISTS build_specs (
          id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, owner_membership_id TEXT NOT NULL,
          task_revision_id TEXT NOT NULL, body_json TEXT NOT NULL, content_digest TEXT NOT NULL,
          UNIQUE(organization_id,owner_membership_id,task_revision_id,content_digest))""")

    def close(self): self._db.close()

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield; self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK"); raise

    def derive(self, context: OrganizationContext, task: TaskRevision, need: NeedRecord,
               process: ProcessSpecification) -> BuildSpecification:
        require(isinstance(task, TaskRevision) and isinstance(need, NeedRecord) and isinstance(process, ProcessSpecification),
                "Invalid build specification inputs")
        if need.completeness == "insufficient" or process.need_id != need.need_id or need.task_revision_id != task.task_revision_id:
            raise DuomitError("DOMAIN_HANDOFF_INVALID", "Build inputs are not a valid Need/Workflow handoff")
        acceptance_ids = tuple(item.criterion_id for item in task.acceptance if item.blocking)
        deliverable = DeliverableSpec(digest({"task": task.task_revision_id, "kind": "deliverable"})[:32],
                                      task.deliverable, "task", acceptance_ids)
        verifications = tuple(VerificationSpec(digest({"task": task.task_revision_id, "criterion": criterion_id})[:32],
                                                criterion_id, "review", True) for criterion_id in acceptance_ids)
        body = {"task_revision_id": task.task_revision_id, "need_id": need.need_id, "process_spec_id": process.process_spec_id,
                "deliverables": [item.__dict__ for item in (deliverable,)], "verification_plan": [item.__dict__ for item in verifications]}
        content_digest = digest(body)
        with self._transaction():
            existing = self._db.execute("SELECT id,body_json,content_digest FROM build_specs WHERE organization_id=? AND owner_membership_id=? AND task_revision_id=? AND content_digest=?",
                                        (context.organization_id, context.membership_id, task.task_revision_id, content_digest)).fetchone()
            if existing is not None:
                return self._from_body(existing["id"], json.loads(existing["body_json"]), existing["content_digest"])
            build_id = uuid.uuid4().hex
            self._db.execute("INSERT INTO build_specs VALUES (?,?,?,?,?,?)",
                             (build_id, context.organization_id, context.membership_id, task.task_revision_id,
                              canonical_json(body), content_digest))
            return self._from_body(build_id, body, content_digest)

    def latest_for_task_revision(self, context: OrganizationContext, task_revision_id: str) -> BuildSpecification | None:
        identifier(task_revision_id)
        row = self._db.execute("SELECT id,body_json,content_digest FROM build_specs WHERE organization_id=? AND owner_membership_id=? AND task_revision_id=? ORDER BY rowid DESC LIMIT 1",
                               (context.organization_id, context.membership_id, task_revision_id)).fetchone()
        return None if row is None else self._from_body(row["id"], json.loads(row["body_json"]), row["content_digest"])

    @staticmethod
    def _from_body(build_id: str, body: dict, content_digest: str) -> BuildSpecification:
        return BuildSpecification(build_id, body["task_revision_id"], body["need_id"], body["process_spec_id"],
                                  tuple(DeliverableSpec(**{**item, "acceptance_criterion_ids": tuple(item["acceptance_criterion_ids"])}) for item in body["deliverables"]),
                                  tuple(VerificationSpec(**item) for item in body["verification_plan"]), content_digest)
