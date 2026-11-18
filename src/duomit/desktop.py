"""Reference Desktop material store and mirror cache; never executes extensions.

The SQLite cache returns immutable byte copies, not filesystem mounts. It is not
an OS sandbox or a distributed cache. Run/node associations are supplied by a
trusted caller; node membership is enforced by the execution-side client.
"""

from contextlib import contextmanager
import hashlib
import math
from pathlib import Path
import sqlite3
import time
import uuid

from .contracts import ExtensionVersion, MirrorLease, RequestContext, digest, identifier
from .errors import DuomitError, require


class SqliteDesktop:
    def __init__(self, database: str | Path, *, clock=time.time):
        self._clock = clock
        self._db = sqlite3.connect(str(database), isolation_level=None, timeout=5)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        with self._transaction():
            for statement in (
                """CREATE TABLE IF NOT EXISTS desktop_versions (
                    tenant TEXT NOT NULL, owner TEXT NOT NULL, extension_id TEXT NOT NULL,
                    version TEXT NOT NULL, kind TEXT NOT NULL, content_digest TEXT NOT NULL,
                    license_id TEXT NOT NULL, body BLOB NOT NULL, withdrawn INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(tenant,owner,extension_id,version))""",
                """CREATE TABLE IF NOT EXISTS desktop_mirrors (
                    mirror_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, owner TEXT NOT NULL,
                    extension_id TEXT NOT NULL, version TEXT NOT NULL, state TEXT NOT NULL,
                    retire_after REAL, cached_body BLOB,
                    UNIQUE(tenant,owner,extension_id,version),
                    FOREIGN KEY(tenant,owner,extension_id,version)
                    REFERENCES desktop_versions(tenant,owner,extension_id,version))""",
                """CREATE TABLE IF NOT EXISTS desktop_leases (
                    lease_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, owner TEXT NOT NULL,
                    idem TEXT NOT NULL, request_digest TEXT NOT NULL,
                    mirror_id TEXT NOT NULL REFERENCES desktop_mirrors(mirror_id),
                    run_id TEXT NOT NULL, node_id TEXT NOT NULL, expires_at REAL NOT NULL,
                    fence INTEGER NOT NULL, state TEXT NOT NULL, UNIQUE(tenant,owner,idem))""",
                """CREATE INDEX IF NOT EXISTS desktop_active_leases
                    ON desktop_leases(mirror_id,state,expires_at)""",
                """CREATE TABLE IF NOT EXISTS desktop_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                    tenant TEXT NOT NULL, owner TEXT NOT NULL, event_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL, trace_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    occurred_at REAL NOT NULL)""",
            ):
                self._db.execute(statement)

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

    def _event(self, context, event_type, resource_id):
        self._db.execute("""INSERT INTO desktop_events
            (event_id,tenant,owner,event_type,resource_id,trace_id,request_id,occurred_at)
            VALUES (?,?,?,?,?,?,?,?)""", (uuid.uuid4().hex, context.tenant_id, context.principal_id,
            event_type, resource_id, context.trace_id, context.request_id, self._clock()))

    def _version(self, context, extension_id, version, *, allow_withdrawn=False):
        row = self._db.execute("""SELECT * FROM desktop_versions
            WHERE tenant=? AND owner=? AND extension_id=? AND version=?""",
            (context.tenant_id, context.principal_id, extension_id, version)).fetchone()
        if row is None:
            raise DuomitError("NOT_FOUND", "Extension is not available")
        if row["withdrawn"] and not allow_withdrawn:
            raise DuomitError("EXTENSION_WITHDRAWN", "Extension version was withdrawn")
        return row

    def publish(self, context: RequestContext, extension: ExtensionVersion, body: bytes) -> None:
        require(isinstance(extension, ExtensionVersion), "Invalid extension descriptor")
        require(type(body) is bytes and 0 < len(body) <= 1048576, "Invalid extension material size")
        require(hashlib.sha256(body).hexdigest() == extension.content_digest, "Extension digest mismatch")
        with self._transaction():
            row = self._db.execute("""SELECT * FROM desktop_versions
                WHERE tenant=? AND owner=? AND extension_id=? AND version=?""",
                (context.tenant_id, context.principal_id, extension.extension_id, extension.version)).fetchone()
            if row is not None:
                if (row["content_digest"], row["kind"], row["license_id"], row["body"]) != (
                        extension.content_digest, extension.kind, extension.license_id, body):
                    raise DuomitError("CONFLICT", "Published extension version is immutable")
                return  # Idempotent publication never clears a withdrawal.
            self._db.execute("INSERT INTO desktop_versions VALUES (?,?,?,?,?,?,?,?,0)",
                             (context.tenant_id, context.principal_id, extension.extension_id,
                              extension.version, extension.kind, extension.content_digest,
                              extension.license_id, body))
            self._event(context, "extension.published", extension.extension_id)

    @staticmethod
    def _duration(value, *, allow_zero=False):
        require(type(value) in (int, float) and math.isfinite(value)
                and (0 <= value if allow_zero else 0 < value) and value <= 3600,
                "Invalid lease duration")

    @staticmethod
    def _fence(value):
        require(type(value) is int and value > 0, "Invalid lease fence")

    def _lease(self, context, lease_id):
        row = self._db.execute("""SELECT l.*,m.extension_id,m.version,v.content_digest
            FROM desktop_leases l JOIN desktop_mirrors m ON m.mirror_id=l.mirror_id
            JOIN desktop_versions v ON v.tenant=m.tenant AND v.owner=m.owner
                AND v.extension_id=m.extension_id AND v.version=m.version
            WHERE l.lease_id=? AND l.tenant=? AND l.owner=?""",
            (lease_id, context.tenant_id, context.principal_id)).fetchone()
        if row is None:
            raise DuomitError("NOT_FOUND", "Mirror lease is not available")
        return row

    def _view(self, row):
        state = row["state"]
        if state == "active" and row["expires_at"] <= self._clock():
            state = "expired"
        return MirrorLease(row["lease_id"], row["mirror_id"], row["extension_id"], row["version"],
                           row["content_digest"], row["run_id"], row["node_id"], row["expires_at"],
                           row["fence"], state)

    def acquire(self, context, extension_id, version, run_id, node_id, idempotency_key, *, valid_seconds=60):
        for value in (extension_id, version, run_id, node_id, idempotency_key):
            identifier(value)
        self._duration(valid_seconds)
        request_digest = digest({"extension": extension_id, "version": version, "run": run_id,
                                 "node": node_id, "duration": valid_seconds})
        with self._transaction():
            extension = self._version(context, extension_id, version)
            previous = self._db.execute("SELECT lease_id,request_digest FROM desktop_leases WHERE tenant=? AND owner=? AND idem=?",
                (context.tenant_id, context.principal_id, idempotency_key)).fetchone()
            if previous is not None:
                if previous["request_digest"] != request_digest:
                    raise DuomitError("CONFLICT", "Idempotency key has different content")
                return self._view(self._lease(context, previous["lease_id"]))
            mirror = self._db.execute("""SELECT * FROM desktop_mirrors
                WHERE tenant=? AND owner=? AND extension_id=? AND version=?""",
                (context.tenant_id, context.principal_id, extension_id, version)).fetchone()
            if mirror is None:
                mirror_id = uuid.uuid4().hex
                self._db.execute("INSERT INTO desktop_mirrors VALUES (?,?,?,?,?,'ready',NULL,?)",
                    (mirror_id, context.tenant_id, context.principal_id, extension_id, version, extension["body"]))
            else:
                mirror_id = mirror["mirror_id"]
                self._db.execute("UPDATE desktop_mirrors SET state='ready',retire_after=NULL,cached_body=? WHERE mirror_id=?",
                                 (extension["body"], mirror_id))
            lease_id = uuid.uuid4().hex
            self._db.execute("INSERT INTO desktop_leases VALUES (?,?,?,?,?,?,?,?,?,1,'active')",
                (lease_id, context.tenant_id, context.principal_id, idempotency_key, request_digest,
                 mirror_id, run_id, node_id, self._clock() + valid_seconds))
            self._event(context, "mirror.acquired", lease_id)
            return self._view(self._lease(context, lease_id))

    def _active(self, context, lease_id, expected_fence):
        self._fence(expected_fence)
        row = self._lease(context, lease_id)
        self._version(context, row["extension_id"], row["version"])
        if row["state"] != "active" or row["expires_at"] <= self._clock():
            raise DuomitError("LEASE_INACTIVE", "Mirror lease is inactive or expired")
        if row["fence"] != expected_fence:
            raise DuomitError("CONFLICT", "Mirror lease fence changed")
        return row

    def renew(self, context, lease_id, expected_fence, *, valid_seconds=60):
        self._duration(valid_seconds)
        with self._transaction():
            row = self._active(context, lease_id, expected_fence)
            expires_at = max(row["expires_at"], self._clock() + valid_seconds)
            self._db.execute("UPDATE desktop_leases SET expires_at=?,fence=fence+1 WHERE lease_id=?",
                             (expires_at, lease_id))
            self._event(context, "mirror.renewed", lease_id)
            return self._view(self._lease(context, lease_id))

    def release(self, context, lease_id, expected_fence):
        self._fence(expected_fence)
        with self._transaction():
            row = self._lease(context, lease_id)
            if row["state"] in ("released", "expired", "revoked"):
                return self._view(row)
            if row["fence"] != expected_fence:
                raise DuomitError("CONFLICT", "Mirror lease fence changed")
            self._db.execute("UPDATE desktop_leases SET state='released',fence=fence+1 WHERE lease_id=?", (lease_id,))
            self._event(context, "mirror.released", lease_id)
            return self._view(self._lease(context, lease_id))

    def read(self, context, lease_id, expected_fence, run_id, node_id):
        with self._transaction():
            row = self._active(context, lease_id, expected_fence)
            if (row["run_id"], row["node_id"]) != (run_id, node_id):
                raise DuomitError("NOT_FOUND", "Mirror lease is not available")
            mirror = self._db.execute("SELECT cached_body,state FROM desktop_mirrors WHERE mirror_id=?", (row["mirror_id"],)).fetchone()
            if mirror["state"] != "ready" or mirror["cached_body"] is None:
                raise DuomitError("MIRROR_UNAVAILABLE", "Mirror cache is unavailable")
            body = bytes(mirror["cached_body"])
            if hashlib.sha256(body).hexdigest() != row["content_digest"]:
                raise DuomitError("MATERIAL_CORRUPT", "Mirror material failed verification")
            return body

    def withdraw(self, context, extension_id, version):
        with self._transaction():
            row = self._version(context, extension_id, version, allow_withdrawn=True)
            if row["withdrawn"]:
                return
            self._db.execute("UPDATE desktop_versions SET withdrawn=1 WHERE tenant=? AND owner=? AND extension_id=? AND version=?",
                             (context.tenant_id, context.principal_id, extension_id, version))
            self._db.execute("""UPDATE desktop_leases SET state='revoked',fence=fence+1
                WHERE mirror_id IN (SELECT mirror_id FROM desktop_mirrors
                    WHERE tenant=? AND owner=? AND extension_id=? AND version=?) AND state='active'""",
                (context.tenant_id, context.principal_id, extension_id, version))
            self._event(context, "extension.withdrawn", extension_id)

    def collect(self, context, *, grace_seconds=30) -> int:
        """Two-pass cache retirement. Authoritative materials and lease tombstones remain."""
        self._duration(grace_seconds, allow_zero=True)
        with self._transaction():
            now = self._clock()
            expired = self._db.execute("""SELECT lease_id FROM desktop_leases
                WHERE tenant=? AND owner=? AND state='active' AND expires_at<=?""",
                (context.tenant_id, context.principal_id, now)).fetchall()
            for row in expired:
                self._db.execute("UPDATE desktop_leases SET state='expired',fence=fence+1 WHERE lease_id=?", (row["lease_id"],))
                self._event(context, "mirror.expired", row["lease_id"])
            mirrors = self._db.execute("""SELECT m.* FROM desktop_mirrors m WHERE tenant=? AND owner=?
                AND state!='collected' AND NOT EXISTS (SELECT 1 FROM desktop_leases l
                    WHERE l.mirror_id=m.mirror_id AND l.state='active' AND l.expires_at>?)""",
                (context.tenant_id, context.principal_id, now)).fetchall()
            collected = 0
            for mirror in mirrors:
                if mirror["state"] == "ready":
                    self._db.execute("UPDATE desktop_mirrors SET state='quarantined',retire_after=? WHERE mirror_id=?",
                                     (now + grace_seconds, mirror["mirror_id"]))
                elif mirror["retire_after"] <= now:
                    self._db.execute("UPDATE desktop_mirrors SET state='collected',cached_body=NULL WHERE mirror_id=?", (mirror["mirror_id"],))
                    self._event(context, "mirror.collected", mirror["mirror_id"])
                    collected += 1
            return collected

    def stats(self, context, extension_id, version):
        with self._transaction():
            self._version(context, extension_id, version, allow_withdrawn=True)
            mirror = self._db.execute("""SELECT * FROM desktop_mirrors
                WHERE tenant=? AND owner=? AND extension_id=? AND version=?""",
                (context.tenant_id, context.principal_id, extension_id, version)).fetchone()
            active = 0 if mirror is None else self._db.execute("""SELECT count(*) FROM desktop_leases
                WHERE mirror_id=? AND state='active' AND expires_at>?""", (mirror["mirror_id"], self._clock())).fetchone()[0]
            return {"active_leases": active, "cache_state": mirror["state"] if mirror else "absent"}

    def events(self, context, *, after_sequence=0, limit=100):
        require(type(after_sequence) is int and after_sequence >= 0, "Invalid event cursor")
        require(type(limit) is int and 0 < limit <= 1000, "Invalid event page size")
        rows = self._db.execute("""SELECT sequence,event_id,event_type,resource_id,trace_id,request_id,occurred_at
            FROM desktop_events WHERE tenant=? AND owner=? AND sequence>? ORDER BY sequence LIMIT ?""",
            (context.tenant_id, context.principal_id, after_sequence, limit)).fetchall()
        return [dict(row) for row in rows]
