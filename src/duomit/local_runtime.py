"""SQLite reference lifecycle owner for engine experiments, not a production engine.

The caller supplies authenticated contexts and trusted capability policy. SQLite
transactions serialize claims. An interrupted operation is never retried without
explicit reconciliation. No arbitrary code, provider or remote tool is run here.
"""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .contracts import Plan, RequestContext, canonical_json, digest, identifier
from .errors import DuomitError, require
from .ports import StepExecutorPort
from .registry import Capability


class LocalRuntime:
    def __init__(self, database: str | Path, capabilities: tuple[Capability, ...], *, clock=time.time):
        self._policy = {item.name: item for item in capabilities}
        self._clock = clock
        self._db = sqlite3.connect(str(database), isolation_level=None, timeout=5)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        with self._transaction():
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            require(version in (0, 1, 2), "Unsupported database version")
            statements = (
                """CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, owner TEXT NOT NULL,
                    idem TEXT NOT NULL, plan TEXT NOT NULL, plan_digest TEXT NOT NULL,
                    state TEXT NOT NULL, revision INTEGER NOT NULL, cancel_requested INTEGER NOT NULL,
                    UNIQUE(tenant, owner, idem))""",
                """CREATE TABLE IF NOT EXISTS steps (
                    run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL,
                    state TEXT NOT NULL, operation_key TEXT NOT NULL, artifact_ref TEXT,
                    approval_expires REAL, PRIMARY KEY(run_id,id))""",
                """CREATE TABLE IF NOT EXISTS outbox (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                    tenant TEXT NOT NULL, owner TEXT NOT NULL, run_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL,
                    trace_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0)""",
            )
            for statement in statements:
                self._db.execute(statement)
            if version == 1:
                self._db.execute("ALTER TABLE outbox ADD COLUMN trace_id TEXT NOT NULL DEFAULT 'legacy'")
                self._db.execute("ALTER TABLE outbox ADD COLUMN request_id TEXT NOT NULL DEFAULT 'legacy'")
            self._db.execute("PRAGMA user_version=2")

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def close(self):
        self._db.close()

    def _owned(self, context, run_id):
        row = self._db.execute("SELECT * FROM runs WHERE id=? AND tenant=? AND owner=?",
                               (run_id, context.tenant_id, context.principal_id)).fetchone()
        if row is None:
            raise DuomitError("NOT_FOUND", "Run is not available")
        return row

    def _revision(self, run, expected):
        if type(expected) is not int or run["revision"] != expected:
            raise DuomitError("CONFLICT", "Run revision changed")

    def _event(self, context, run_id, event_type, payload):
        self._db.execute("UPDATE runs SET revision=revision+1 WHERE id=?", (run_id,))
        revision = self._owned(context, run_id)["revision"]
        self._db.execute("""INSERT INTO outbox
            (event_id,tenant,owner,run_id,revision,event_type,payload,trace_id,request_id)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (uuid.uuid4().hex, context.tenant_id, context.principal_id, run_id, revision,
             event_type, canonical_json(payload), context.trace_id, context.request_id))

    def create(self, context: RequestContext, plan: Plan, idempotency_key: str) -> str:
        identifier(idempotency_key)
        for step in plan.steps:
            capability = self._policy.get(step.capability)
            require(capability is not None, "Plan exceeds execution policy")
            require(not capability.requires_approval or step.requires_approval,
                    "Plan omits mandatory approval")
        with self._transaction():
            previous = self._db.execute("SELECT * FROM runs WHERE tenant=? AND owner=? AND idem=?",
                                        (context.tenant_id, context.principal_id, idempotency_key)).fetchone()
            if previous:
                if previous["plan_digest"] != plan.content_digest:
                    raise DuomitError("CONFLICT", "Idempotency key has different content")
                return previous["id"]
            run_id = uuid.uuid4().hex
            self._db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,0,0)",
                             (run_id, context.tenant_id, context.principal_id, idempotency_key,
                              canonical_json(plan.to_dict()), plan.content_digest, "ready"))
            for step in plan.steps:
                operation_key = digest({"run": run_id, "step": step.step_id,
                                        "plan": plan.content_digest})
                self._db.execute("INSERT INTO steps VALUES (?,?,?,?,NULL,NULL)",
                                 (run_id, step.step_id, "pending", operation_key))
            self._event(context, run_id, "run.created", {"plan_digest": plan.content_digest})
            return run_id

    def inspect(self, context: RequestContext, run_id: str) -> dict:
        with self._transaction():
            run = self._owned(context, run_id)
            steps = self._db.execute("""SELECT id,state,operation_key,artifact_ref
                FROM steps WHERE run_id=? ORDER BY rowid""", (run_id,)).fetchall()
            return {"run_id": run_id, "state": run["state"], "revision": run["revision"],
                    "plan_digest": run["plan_digest"],
                    "cancel_requested": bool(run["cancel_requested"]),
                    "steps": [dict(row) for row in steps]}

    def find_run(self, context: RequestContext, idempotency_key: str) -> str | None:
        identifier(idempotency_key)
        row = self._db.execute("SELECT id FROM runs WHERE tenant=? AND owner=? AND idem=?",
                               (context.tenant_id, context.principal_id, idempotency_key)).fetchone()
        return row["id"] if row else None

    def approve(self, context, run_id, step_id, operation_key, expected_revision, *, valid_seconds=60):
        require(type(valid_seconds) in (int, float) and 0 < valid_seconds <= 3600,
                "Invalid approval validity")
        with self._transaction():
            run = self._owned(context, run_id)
            self._revision(run, expected_revision)
            row = self._db.execute("SELECT * FROM steps WHERE run_id=? AND id=?",
                                   (run_id, step_id)).fetchone()
            require(row is not None and row["state"] == "waiting_approval"
                    and row["operation_key"] == operation_key and not run["cancel_requested"],
                    "Approval is not valid for this operation")
            self._db.execute("UPDATE steps SET state='pending',approval_expires=? WHERE run_id=? AND id=?",
                             (self._clock() + valid_seconds, run_id, step_id))
            self._db.execute("UPDATE runs SET state='ready' WHERE id=?", (run_id,))
            self._event(context, run_id, "approval.granted", {"step_id": step_id})

    def _claim(self, context, run_id):
        with self._transaction():
            run = self._owned(context, run_id)
            if run["state"] in ("succeeded", "failed", "cancelled"):
                return None
            rows = {row["id"]: row for row in self._db.execute(
                "SELECT * FROM steps WHERE run_id=?", (run_id,))}
            # An active or uncertain step blocks claims, including concurrent callers.
            if any(row["state"] in ("running", "reconciling") for row in rows.values()):
                return None
            if run["cancel_requested"]:
                self._db.execute("UPDATE steps SET state='cancelled' WHERE run_id=? AND state IN ('pending','waiting_approval')", (run_id,))
                self._db.execute("UPDATE runs SET state='cancelled' WHERE id=?", (run_id,))
                self._event(context, run_id, "run.cancelled", {})
                return None
            plan = Plan.from_dict(json.loads(run["plan"]))
            if all(row["state"] == "succeeded" for row in rows.values()):
                self._db.execute("UPDATE runs SET state='succeeded' WHERE id=?", (run_id,))
                self._event(context, run_id, "run.completed", {})
                return None
            for step in plan.steps:
                row = rows[step.step_id]
                if row["state"] != "pending" or any(rows[d]["state"] != "succeeded" for d in step.depends_on):
                    continue
                policy = self._policy.get(step.capability)
                require(policy is not None, "Capability is no longer authorized")
                needs_approval = step.requires_approval or policy.requires_approval
                if needs_approval and (row["approval_expires"] or 0) <= self._clock():
                    self._db.execute("UPDATE steps SET state='waiting_approval' WHERE run_id=? AND id=?",
                                     (run_id, step.step_id))
                    self._db.execute("UPDATE runs SET state='waiting_approval' WHERE id=?", (run_id,))
                    self._event(context, run_id, "approval.requested", {"step_id": step.step_id})
                    return None
                self._db.execute("UPDATE steps SET state='running',approval_expires=NULL WHERE run_id=? AND id=?",
                                 (run_id, step.step_id))
                self._db.execute("UPDATE runs SET state='running' WHERE id=?", (run_id,))
                self._event(context, run_id, "step.started", {"step_id": step.step_id})
                return step, row["operation_key"]
            return None

    def drive(self, context, run_id, executor: StepExecutorPort) -> dict:
        """Drive until blocked/terminal; at most one active step per reference run."""
        while claim := self._claim(context, run_id):
            step, operation_key = claim
            try:
                artifact = executor.execute(context, step.capability, operation_key)
                require(isinstance(artifact, str) and artifact.startswith("artifact:")
                        and len(artifact) <= 256, "Expected artifact reference")
            except Exception:
                self._finish(context, run_id, step.step_id, "reconciling", None)
                break
            self._finish(context, run_id, step.step_id, "succeeded", artifact)
        return self.inspect(context, run_id)

    def _finish(self, context, run_id, step_id, state, artifact):
        with self._transaction():
            self._owned(context, run_id)
            cursor = self._db.execute("""UPDATE steps SET state=?,artifact_ref=?
                WHERE run_id=? AND id=? AND state='running'""", (state, artifact, run_id, step_id))
            if cursor.rowcount != 1:
                raise DuomitError("CONFLICT", "Step is no longer owned by this attempt")
            self._db.execute("UPDATE runs SET state=? WHERE id=?",
                             ("reconciling" if state == "reconciling" else "ready", run_id))
            self._event(context, run_id, f"step.{state}", {"step_id": step_id})

    def recover(self, context, run_id, expected_revision):
        """Operator-only after confirming the old worker stopped; never auto-steal work."""
        with self._transaction():
            run = self._owned(context, run_id)
            self._revision(run, expected_revision)
            cursor = self._db.execute("UPDATE steps SET state='reconciling' WHERE run_id=? AND state='running'",
                                      (run_id,))
            if cursor.rowcount:
                self._db.execute("UPDATE runs SET state='reconciling' WHERE id=?", (run_id,))
                self._event(context, run_id, "run.reconciliation_required", {})

    def reconcile(self, context, run_id, step_id, operation_key, expected_revision, *, artifact_ref=None):
        """Trusted operator confirms success with a safe reference, or fails the run.

        There is deliberately no retry-unknown option in this reference experiment.
        """
        require(artifact_ref is None or (isinstance(artifact_ref, str)
                and artifact_ref.startswith("artifact:") and len(artifact_ref) <= 256),
                "Invalid artifact reference")
        with self._transaction():
            run = self._owned(context, run_id)
            self._revision(run, expected_revision)
            row = self._db.execute("SELECT * FROM steps WHERE run_id=? AND id=?",
                                   (run_id, step_id)).fetchone()
            require(row is not None and row["state"] == "reconciling"
                    and row["operation_key"] == operation_key, "Invalid reconciliation")
            self._db.execute("UPDATE steps SET state=?,artifact_ref=? WHERE run_id=? AND id=?",
                             ("succeeded" if artifact_ref else "failed", artifact_ref, run_id, step_id))
            self._db.execute("UPDATE runs SET state=? WHERE id=?",
                             ("ready" if artifact_ref else "failed", run_id))
            self._event(context, run_id, "step.reconciled", {"step_id": step_id,
                         "outcome": "succeeded" if artifact_ref else "failed"})

    def cancel(self, context, run_id, expected_revision):
        with self._transaction():
            run = self._owned(context, run_id)
            self._revision(run, expected_revision)
            if run["state"] in ("succeeded", "failed", "cancelled"):
                return
            self._db.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (run_id,))
            active = self._db.execute("SELECT 1 FROM steps WHERE run_id=? AND state IN ('running','reconciling')",
                                      (run_id,)).fetchone()
            if not active:
                self._db.execute("UPDATE steps SET state='cancelled' WHERE run_id=? AND state IN ('pending','waiting_approval')", (run_id,))
                self._db.execute("UPDATE runs SET state='cancelled' WHERE id=?", (run_id,))
            self._event(context, run_id, "run.cancel_requested", {})

    def pending_events(self, context):
        rows = self._db.execute("""SELECT event_id,run_id,revision,event_type,payload,trace_id,request_id FROM outbox
            WHERE tenant=? AND owner=? AND delivered=0 ORDER BY sequence""",
            (context.tenant_id, context.principal_id)).fetchall()
        return [dict(row) | {"schema_version": "1.0", "payload": json.loads(row["payload"])} for row in rows]

    def acknowledge(self, context, event_id):
        with self._transaction():
            self._db.execute("UPDATE outbox SET delivered=1 WHERE event_id=? AND tenant=? AND owner=?",
                             (event_id, context.tenant_id, context.principal_id))
