"""Immutable TaskRevision reference model for planning inputs.

The original local workspace remains a P2 entry-point fixture.  This module is
the P3 planning input boundary: every change produces a new revision and every
revision carries a deliverable plus explicit blocking acceptance.
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


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    statement: str
    blocking: bool = True

    def __post_init__(self):
        identifier(self.criterion_id)
        require(isinstance(self.statement, str) and 1 <= len(self.statement.strip()) <= 2048,
                "Invalid acceptance statement")
        require(type(self.blocking) is bool, "Invalid acceptance blocking flag")


@dataclass(frozen=True)
class TaskRevision:
    task_id: str
    task_revision_id: str
    number: int
    title: str
    goal: str
    deliverable: str
    acceptance: tuple[AcceptanceCriterion, ...]
    constraints: tuple[str, ...]
    digest: str


class SqliteTaskRevisionStore:
    def __init__(self, database: str | Path):
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript("""
          CREATE TABLE IF NOT EXISTS planning_tasks (
            id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, owner_membership_id TEXT NOT NULL,
            current_number INTEGER NOT NULL, revision INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS task_revisions (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES planning_tasks(id), number INTEGER NOT NULL,
            body_json TEXT NOT NULL, content_digest TEXT NOT NULL, UNIQUE(task_id,number));
          CREATE TABLE IF NOT EXISTS workspace_task_revisions (
            workspace_task_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES planning_tasks(id),
            organization_id TEXT NOT NULL, owner_membership_id TEXT NOT NULL);
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

    @staticmethod
    def _validate(title: str, goal: str, deliverable: str,
                  acceptance: tuple[AcceptanceCriterion, ...], constraints: tuple[str, ...]):
        require(isinstance(title, str) and 1 <= len(title.strip()) <= 200, "Invalid task title")
        require(isinstance(goal, str) and 1 <= len(goal.strip()) <= 8192, "Invalid task goal")
        require(isinstance(deliverable, str) and 1 <= len(deliverable.strip()) <= 4096, "DELIVERABLE_UNDEFINED")
        require(type(acceptance) is tuple and 1 <= len(acceptance) <= 64
                and all(isinstance(item, AcceptanceCriterion) for item in acceptance)
                and len({item.criterion_id for item in acceptance}) == len(acceptance)
                and any(item.blocking for item in acceptance), "ACCEPTANCE_CRITERION_REQUIRED")
        require(type(constraints) is tuple and len(constraints) <= 64
                and all(isinstance(item, str) and 1 <= len(item.strip()) <= 2048 for item in constraints),
                "Invalid task constraints")

    def _task(self, context: OrganizationContext, task_id: str) -> sqlite3.Row:
        identifier(task_id)
        row = self._db.execute("SELECT * FROM planning_tasks WHERE id=? AND organization_id=? AND owner_membership_id=?",
                               (task_id, context.organization_id, context.membership_id)).fetchone()
        if row is None:
            raise DuomitError("NOT_FOUND", "Task is not available")
        return row

    @staticmethod
    def _body(task_id: str, revision_id: str, number: int, title: str, goal: str, deliverable: str,
              acceptance: tuple[AcceptanceCriterion, ...], constraints: tuple[str, ...]) -> dict:
        return {"task_id": task_id, "task_revision_id": revision_id, "number": number, "title": title.strip(),
                "goal": goal.strip(), "deliverable": deliverable.strip(),
                "acceptance": [{"criterion_id": item.criterion_id, "statement": item.statement.strip(), "blocking": item.blocking}
                               for item in acceptance], "constraints": [item.strip() for item in constraints]}

    @staticmethod
    def _revision(body: dict, content_digest: str) -> TaskRevision:
        acceptance = tuple(AcceptanceCriterion(**item) for item in body["acceptance"])
        return TaskRevision(body["task_id"], body["task_revision_id"], body["number"], body["title"], body["goal"],
                            body["deliverable"], acceptance, tuple(body["constraints"]), content_digest)

    def create(self, context: OrganizationContext, title: str, goal: str, deliverable: str,
               acceptance: tuple[AcceptanceCriterion, ...], constraints: tuple[str, ...] = ()) -> TaskRevision:
        self._validate(title, goal, deliverable, acceptance, constraints)
        task_id, revision_id = uuid.uuid4().hex, uuid.uuid4().hex
        body = self._body(task_id, revision_id, 1, title, goal, deliverable, acceptance, constraints)
        content_digest = digest(body)
        with self._transaction():
            self._db.execute("INSERT INTO planning_tasks VALUES (?,?,?,?,1)",
                             (task_id, context.organization_id, context.membership_id, 1))
            self._db.execute("INSERT INTO task_revisions VALUES (?,?,?,?,?)",
                             (revision_id, task_id, 1, canonical_json(body), content_digest))
        return self._revision(body, content_digest)

    def create_workspace_task(self, context: OrganizationContext, project_id: str, title: str, goal: str,
                              deliverable: str, acceptance: tuple[AcceptanceCriterion, ...],
                              constraints: tuple[str, ...] = ()) -> str:
        """Atomically create the entry Task and its first immutable planning revision."""
        identifier(project_id)
        self._validate(title, goal, deliverable, acceptance, constraints)
        workspace_task_id, task_id, revision_id = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
        body = self._body(task_id, revision_id, 1, title, goal, deliverable, acceptance, constraints)
        content_digest = digest(body)
        with self._transaction():
            project = self._db.execute("SELECT 1 FROM projects WHERE id=? AND organization_id=?",
                                       (project_id, context.organization_id)).fetchone()
            if project is None:
                raise DuomitError("NOT_FOUND", "Project is not available")
            self._db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,1)",
                             (workspace_task_id, project_id, context.organization_id, context.membership_id,
                              title.strip(), goal.strip(), "draft"))
            self._db.execute("INSERT INTO planning_tasks VALUES (?,?,?,?,1)",
                             (task_id, context.organization_id, context.membership_id, 1))
            self._db.execute("INSERT INTO task_revisions VALUES (?,?,?,?,?)",
                             (revision_id, task_id, 1, canonical_json(body), content_digest))
            self._db.execute("INSERT INTO workspace_task_revisions VALUES (?,?,?,?)",
                             (workspace_task_id, task_id, context.organization_id, context.membership_id))
        return workspace_task_id

    def revise(self, context: OrganizationContext, task_id: str, expected_task_revision: int, title: str, goal: str,
               deliverable: str, acceptance: tuple[AcceptanceCriterion, ...], constraints: tuple[str, ...] = ()) -> TaskRevision:
        require(type(expected_task_revision) is int and expected_task_revision > 0, "Invalid task revision")
        self._validate(title, goal, deliverable, acceptance, constraints)
        with self._transaction():
            task = self._task(context, task_id)
            if task["revision"] != expected_task_revision:
                raise DuomitError("TASK_REVISION_CONFLICT", "Task revision has changed")
            number = task["current_number"] + 1
            revision_id = uuid.uuid4().hex
            body = self._body(task_id, revision_id, number, title, goal, deliverable, acceptance, constraints)
            content_digest = digest(body)
            self._db.execute("INSERT INTO task_revisions VALUES (?,?,?,?,?)",
                             (revision_id, task_id, number, canonical_json(body), content_digest))
            self._db.execute("UPDATE planning_tasks SET current_number=?,revision=revision+1 WHERE id=?", (number, task_id))
            return self._revision(body, content_digest)

    def revise_workspace_task(self, context: OrganizationContext, workspace_task_id: str,
                              expected_workspace_revision: int, title: str, goal: str, deliverable: str,
                              acceptance: tuple[AcceptanceCriterion, ...],
                              constraints: tuple[str, ...] = ()) -> TaskRevision:
        """Atomically revise the Desktop projection and its linked planning Task."""
        identifier(workspace_task_id)
        require(type(expected_workspace_revision) is int and expected_workspace_revision > 0, "Invalid task revision")
        self._validate(title, goal, deliverable, acceptance, constraints)
        with self._transaction():
            workspace = self._db.execute(
                "SELECT revision FROM tasks WHERE id=? AND organization_id=? AND owner=?",
                (workspace_task_id, context.organization_id, context.membership_id)).fetchone()
            if workspace is None:
                raise DuomitError("NOT_FOUND", "Task is not available")
            if workspace["revision"] != expected_workspace_revision:
                raise DuomitError("TASK_REVISION_CONFLICT", "Task revision has changed")
            link = self._db.execute(
                "SELECT task_id FROM workspace_task_revisions WHERE workspace_task_id=? AND organization_id=? AND owner_membership_id=?",
                (workspace_task_id, context.organization_id, context.membership_id)).fetchone()
            if link is None:
                raise DuomitError("NOT_FOUND", "Task planning input is not available")
            task = self._task(context, link["task_id"])
            number, revision_id = task["current_number"] + 1, uuid.uuid4().hex
            body = self._body(link["task_id"], revision_id, number, title, goal, deliverable, acceptance, constraints)
            content_digest = digest(body)
            self._db.execute("UPDATE tasks SET title=?,goal=?,revision=revision+1 WHERE id=?",
                             (title.strip(), goal.strip(), workspace_task_id))
            self._db.execute("INSERT INTO task_revisions VALUES (?,?,?,?,?)",
                             (revision_id, link["task_id"], number, canonical_json(body), content_digest))
            self._db.execute("UPDATE planning_tasks SET current_number=?,revision=revision+1 WHERE id=?",
                             (number, link["task_id"]))
            return self._revision(body, content_digest)

    def get_current(self, context: OrganizationContext, task_id: str) -> TaskRevision:
        with self._transaction():
            task = self._task(context, task_id)
            row = self._db.execute("SELECT body_json,content_digest FROM task_revisions WHERE task_id=? AND number=?",
                                   (task_id, task["current_number"])).fetchone()
            body = json.loads(row["body_json"])
            if digest(body) != row["content_digest"]:
                raise DuomitError("MATERIAL_CORRUPT", "Task revision digest mismatch")
            return self._revision(body, row["content_digest"])

    def link_workspace_task(self, context: OrganizationContext, workspace_task_id: str, task_id: str) -> None:
        """Bind one local entry Task to its independent immutable planning Task."""
        identifier(workspace_task_id)
        self._task(context, task_id)
        with self._transaction():
            self._db.execute("INSERT INTO workspace_task_revisions VALUES (?,?,?,?)",
                             (workspace_task_id, task_id, context.organization_id, context.membership_id))

    def get_current_for_workspace_task(self, context: OrganizationContext, workspace_task_id: str) -> TaskRevision | None:
        identifier(workspace_task_id)
        row = self._db.execute(
            "SELECT task_id FROM workspace_task_revisions WHERE workspace_task_id=? AND organization_id=? AND owner_membership_id=?",
            (workspace_task_id, context.organization_id, context.membership_id)).fetchone()
        return None if row is None else self.get_current(context, row["task_id"])
