"""SQLite reference event journal with organization-scoped at-least-once delivery."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from .contracts import canonical_json
from .errors import DuomitError, require
from .foundation import EventEnvelope, PinnedReference


class SqliteEventJournal:
    def __init__(self, database: str | Path):
        self._db = sqlite3.connect(str(database), isolation_level=None, timeout=5)
        self._db.row_factory = sqlite3.Row
        with self._transaction():
            self._db.execute("""CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                organization_id TEXT NOT NULL, body TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)""")

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield; self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK"); raise

    def close(self): self._db.close()

    @staticmethod
    def _body(event):
        return {"event_id": event.event_id, "event_type": event.event_type, "schema_version": event.schema_version,
                "organization_id": event.organization_id, "aggregate_id": event.aggregate_id,
                "aggregate_revision": event.aggregate_revision, "occurred_at": event.occurred_at,
                "payload_ref": event.payload_ref.to_dict(), "trace_id": event.trace_id,
                "causation_id": event.causation_id, "task_id": event.task_id, "run_id": event.run_id}

    def append(self, event: EventEnvelope):
        require(isinstance(event, EventEnvelope), "Invalid event envelope")
        body = canonical_json(self._body(event))
        with self._transaction():
            row = self._db.execute("SELECT body FROM events WHERE event_id=?", (event.event_id,)).fetchone()
            if row:
                require(row["body"] == body, "Event identifier has different content")
                return
            self._db.execute("INSERT INTO events(event_id,organization_id,body) VALUES (?,?,?)",
                             (event.event_id, event.organization_id, body))

    def pending(self, organization_id: str, limit: int = 100):
        require(isinstance(limit, int) and 0 < limit <= 1000, "Invalid event page size")
        rows = self._db.execute("SELECT sequence,body FROM events WHERE organization_id=? AND delivered=0 ORDER BY sequence LIMIT ?",
                                (organization_id, limit)).fetchall()
        result=[]
        for row in rows:
            value=json.loads(row["body"]); value["payload_ref"]=PinnedReference(**value["payload_ref"])
            result.append((row["sequence"], EventEnvelope(**value)))
        return tuple(result)

    def acknowledge(self, organization_id: str, event_id: str):
        with self._transaction():
            cursor=self._db.execute("UPDATE events SET delivered=1 WHERE event_id=? AND organization_id=?", (event_id, organization_id))
            if cursor.rowcount != 1: raise DuomitError("NOT_FOUND", "Event is not available")
