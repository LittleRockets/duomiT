"""Workflow-domain process specifications derived from validated Need records."""

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


@dataclass(frozen=True)
class ProcessStage:
    stage_id: str
    title: str
    purpose: str
    domain_coverage: tuple[str, ...]
    parallel_limit: int = 1

    def __post_init__(self):
        identifier(self.stage_id)
        require(isinstance(self.title, str) and 1 <= len(self.title.strip()) <= 128, "Invalid process stage")
        require(isinstance(self.purpose, str) and 1 <= len(self.purpose.strip()) <= 1024, "Invalid process stage purpose")
        require(type(self.domain_coverage) is tuple and self.domain_coverage
                and all(domain in ("need", "workflow", "build", "run") for domain in self.domain_coverage),
                "Invalid process domain coverage")
        require(type(self.parallel_limit) is int and 1 <= self.parallel_limit <= 16, "Invalid process parallel limit")


@dataclass(frozen=True)
class QualityGate:
    gate_id: str
    criterion_ids: tuple[str, ...]
    blocking: bool = True

    def __post_init__(self):
        identifier(self.gate_id)
        require(type(self.criterion_ids) is tuple and self.criterion_ids, "Invalid quality gate criteria")
        for criterion_id in self.criterion_ids:
            identifier(criterion_id)
        require(type(self.blocking) is bool, "Invalid quality gate blocking flag")


@dataclass(frozen=True)
class ProcessSpecification:
    process_spec_id: str
    need_id: str
    stages: tuple[ProcessStage, ...]
    quality_gates: tuple[QualityGate, ...]
    digest: str


class SqliteProcessSpecStore:
    def __init__(self, database: str | Path):
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""CREATE TABLE IF NOT EXISTS process_specs (
          id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, owner_membership_id TEXT NOT NULL,
          need_id TEXT NOT NULL, body_json TEXT NOT NULL, content_digest TEXT NOT NULL,
          UNIQUE(organization_id,owner_membership_id,need_id,content_digest))""")

    def close(self):
        self._db.close()

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield; self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK"); raise

    def derive(self, context: OrganizationContext, need: NeedRecord) -> ProcessSpecification:
        require(isinstance(need, NeedRecord), "Invalid need record")
        if need.completeness == "insufficient":
            raise DuomitError("TASK_CLARIFICATION_REQUIRED", "Blocking need questions must be resolved")
        criterion_ids = tuple(sorted({criterion_id for outcome in need.outcomes for criterion_id in outcome.acceptance_criterion_ids}))
        stage = ProcessStage(digest({"need": need.need_id, "kind": "outcome_delivery"})[:32],
                             "Outcome delivery", "Advance validated outcomes through review", ("workflow",), 1)
        gate = QualityGate(digest({"need": need.need_id, "kind": "blocking_quality_gate", "criteria": criterion_ids})[:32],
                           criterion_ids, True)
        draft = {"need_id": need.need_id, "stages": [stage.__dict__], "quality_gates": [gate.__dict__]}
        content_digest = digest(draft)
        with self._transaction():
            existing = self._db.execute("SELECT id,body_json,content_digest FROM process_specs WHERE organization_id=? AND owner_membership_id=? AND need_id=? AND content_digest=?",
                                        (context.organization_id, context.membership_id, need.need_id, content_digest)).fetchone()
            if existing is not None:
                return self._from_body(existing["id"], json.loads(existing["body_json"]), existing["content_digest"])
            process_id = uuid.uuid4().hex
            self._db.execute("INSERT INTO process_specs VALUES (?,?,?,?,?,?)",
                             (process_id, context.organization_id, context.membership_id, need.need_id,
                              canonical_json(draft), content_digest))
            return self._from_body(process_id, draft, content_digest)

    @staticmethod
    def _from_body(process_id: str, body: dict, content_digest: str) -> ProcessSpecification:
        return ProcessSpecification(process_id, body["need_id"],
                                    tuple(ProcessStage(**{**item, "domain_coverage": tuple(item["domain_coverage"])}) for item in body["stages"]),
                                    tuple(QualityGate(**{**item, "criterion_ids": tuple(item["criterion_ids"])}) for item in body["quality_gates"]),
                                    content_digest)
