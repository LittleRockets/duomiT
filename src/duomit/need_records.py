"""Validated Need records derived from immutable TaskRevision inputs.

An LLM may supply a ``NeedCandidate`` through a later API adapter, but it has
no authority here: this module validates and persists only normalized facts.
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
from .task_revisions import TaskRevision


@dataclass(frozen=True)
class NeedOutcome:
    outcome_id: str
    statement: str
    acceptance_criterion_ids: tuple[str, ...]

    def __post_init__(self):
        identifier(self.outcome_id)
        require(isinstance(self.statement, str) and 1 <= len(self.statement.strip()) <= 2048, "Invalid need outcome")
        require(type(self.acceptance_criterion_ids) is tuple and self.acceptance_criterion_ids,
                "Need outcome must bind acceptance")
        for criterion_id in self.acceptance_criterion_ids:
            identifier(criterion_id)


@dataclass(frozen=True)
class NeedUnknown:
    question_id: str
    question: str
    blocking: bool

    def __post_init__(self):
        identifier(self.question_id)
        require(isinstance(self.question, str) and 1 <= len(self.question.strip()) <= 1024, "Invalid need question")
        require(type(self.blocking) is bool, "Invalid need question blocking flag")


@dataclass(frozen=True)
class NeedCandidate:
    outcomes: tuple[NeedOutcome, ...]
    unknowns: tuple[NeedUnknown, ...] = ()
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class NeedRecord:
    need_id: str
    task_revision_id: str
    outcomes: tuple[NeedOutcome, ...]
    unknowns: tuple[NeedUnknown, ...]
    constraints: tuple[str, ...]
    completeness: str
    digest: str


class SqliteNeedStore:
    def __init__(self, database: str | Path):
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""CREATE TABLE IF NOT EXISTS need_records (
          id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, owner_membership_id TEXT NOT NULL,
          task_revision_id TEXT NOT NULL, body_json TEXT NOT NULL, content_digest TEXT NOT NULL,
          UNIQUE(organization_id,owner_membership_id,task_revision_id,content_digest))""")

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
    def _normalize(task: TaskRevision, candidate: NeedCandidate) -> tuple[tuple[NeedOutcome, ...], tuple[NeedUnknown, ...], str]:
        require(isinstance(task, TaskRevision) and isinstance(candidate, NeedCandidate), "Invalid need input")
        require(type(candidate.outcomes) is tuple and 1 <= len(candidate.outcomes) <= 32
                and all(isinstance(item, NeedOutcome) for item in candidate.outcomes), "Need requires outcomes")
        require(type(candidate.unknowns) is tuple and len(candidate.unknowns) <= 32
                and all(isinstance(item, NeedUnknown) for item in candidate.unknowns), "Invalid need unknowns")
        criterion_ids = {item.criterion_id for item in task.acceptance if item.blocking}
        bound_ids = {criterion_id for outcome in candidate.outcomes for criterion_id in outcome.acceptance_criterion_ids}
        if not criterion_ids <= bound_ids:
            raise DuomitError("DECOMPOSITION_INVALID", "Need outcomes do not cover blocking acceptance")
        unknown_ids = [item.question_id for item in candidate.unknowns]
        require(len(unknown_ids) == len(set(unknown_ids)), "Duplicate need unknown")
        completeness = "insufficient" if any(item.blocking for item in candidate.unknowns) else "sufficient"
        return candidate.outcomes, candidate.unknowns, completeness

    def create(self, context: OrganizationContext, task: TaskRevision, candidate: NeedCandidate) -> NeedRecord:
        outcomes, unknowns, completeness = self._normalize(task, candidate)
        payload = {"task_revision_id": task.task_revision_id,
                   "outcomes": [item.__dict__ for item in outcomes], "unknowns": [item.__dict__ for item in unknowns],
                   "constraints": task.constraints, "completeness": completeness}
        content_digest = digest(payload)
        with self._transaction():
            prior = self._db.execute("SELECT id,body_json,content_digest FROM need_records WHERE organization_id=? AND owner_membership_id=? AND task_revision_id=? AND content_digest=?",
                                     (context.organization_id, context.membership_id, task.task_revision_id, content_digest)).fetchone()
            if prior is not None:
                return self._from_body(prior["id"], json.loads(prior["body_json"]), prior["content_digest"])
            need_id = uuid.uuid4().hex
            self._db.execute("INSERT INTO need_records VALUES (?,?,?,?,?,?)",
                             (need_id, context.organization_id, context.membership_id, task.task_revision_id,
                              canonical_json(payload), content_digest))
            return self._from_body(need_id, payload, content_digest)

    def list_for_task_revision(self, context: OrganizationContext, task_revision_id: str) -> tuple[NeedRecord, ...]:
        identifier(task_revision_id)
        rows = self._db.execute(
            "SELECT id,body_json,content_digest FROM need_records WHERE organization_id=? AND owner_membership_id=? AND task_revision_id=? ORDER BY rowid",
            (context.organization_id, context.membership_id, task_revision_id)).fetchall()
        return tuple(self._from_body(row["id"], json.loads(row["body_json"]), row["content_digest"]) for row in rows)

    @staticmethod
    def _from_body(need_id: str, body: dict, content_digest: str) -> NeedRecord:
        return NeedRecord(need_id, body["task_revision_id"],
                          tuple(NeedOutcome(**{**item, "acceptance_criterion_ids": tuple(item["acceptance_criterion_ids"])}) for item in body["outcomes"]),
                          tuple(NeedUnknown(**item) for item in body["unknowns"]), tuple(body["constraints"]),
                          body["completeness"], content_digest)
