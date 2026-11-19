"""P2 reference Artifact grants and ContextPackage boundary.

This module deliberately implements no model, node, filesystem mount, or
external export.  It demonstrates the critical rule that an artifact id is not
permission: a recipient must hold an active, exact-version grant for every
read or ContextPackage use.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import uuid

from .contracts import canonical_json, digest, identifier
from .errors import DuomitError, require
from .foundation import OrganizationContext


RESOURCE_ACTIONS = ("inspect_metadata", "read_content", "use_as_context")
RESOURCE_PURPOSES = ("task_context", "task_review")
SHAREABILITIES = ("owner_only", "grantable")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _valid_expiry(value: str | None) -> str | None:
    if value is None:
        return None
    require(isinstance(value, str), "Invalid grant expiry")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DuomitError("INVALID_CONTRACT", "Invalid grant expiry") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed),
            "Grant expiry must be UTC")
    return parsed.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ArtifactVersionReference:
    artifact_id: str
    version: int
    content_digest: str

    def __post_init__(self) -> None:
        identifier(self.artifact_id)
        require(type(self.version) is int and self.version > 0, "Invalid artifact version")
        require(isinstance(self.content_digest, str) and len(self.content_digest) == 64,
                "Invalid artifact digest")


@dataclass(frozen=True)
class ResourceGrant:
    grant_id: str
    artifact: ArtifactVersionReference
    grantee_membership_id: str
    actions: tuple[str, ...]
    purpose: str
    expires_at: str | None
    state: str
    revision: int

    def __post_init__(self) -> None:
        identifier(self.grant_id)
        require(isinstance(self.artifact, ArtifactVersionReference), "Invalid grant artifact")
        identifier(self.grantee_membership_id)
        require(type(self.actions) is tuple and 0 < len(self.actions) <= len(RESOURCE_ACTIONS)
                and len(set(self.actions)) == len(self.actions)
                and all(action in RESOURCE_ACTIONS for action in self.actions), "Invalid grant actions")
        require(self.purpose in RESOURCE_PURPOSES, "Invalid grant purpose")
        _valid_expiry(self.expires_at)
        require(self.state in ("active", "revoked", "expired"), "Invalid grant state")
        require(type(self.revision) is int and self.revision > 0, "Invalid grant revision")


@dataclass(frozen=True)
class ContextPackage:
    context_package_id: str
    task_id: str
    purpose: str
    item_grant_ids: tuple[str, ...]
    digest: str
    revision: int

    def __post_init__(self) -> None:
        identifier(self.context_package_id)
        identifier(self.task_id)
        require(self.purpose in RESOURCE_PURPOSES, "Invalid context purpose")
        require(type(self.item_grant_ids) is tuple and 0 < len(self.item_grant_ids) <= 64
                and len(set(self.item_grant_ids)) == len(self.item_grant_ids), "Invalid context items")
        for grant_id in self.item_grant_ids:
            identifier(grant_id)
        require(isinstance(self.digest, str) and len(self.digest) == 64, "Invalid context digest")
        require(type(self.revision) is int and self.revision > 0, "Invalid context revision")


class SqliteResourceAccess:
    """SQLite reference adapter for exact-version, membership-bound access."""

    def __init__(self, database: str | Path, *, now=_utc_now):
        self._now = now
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS resource_artifacts (
              id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, owner_membership_id TEXT NOT NULL,
              name TEXT NOT NULL, shareability TEXT NOT NULL, revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS resource_versions (
              artifact_id TEXT NOT NULL REFERENCES resource_artifacts(id), version INTEGER NOT NULL,
              content_digest TEXT NOT NULL, body BLOB NOT NULL, PRIMARY KEY(artifact_id, version));
            CREATE TABLE IF NOT EXISTS resource_grants (
              id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
              artifact_version INTEGER NOT NULL, grantee_membership_id TEXT NOT NULL, actions_json TEXT NOT NULL,
              purpose TEXT NOT NULL, expires_at TEXT, state TEXT NOT NULL, revision INTEGER NOT NULL,
              UNIQUE(organization_id, id));
            CREATE TABLE IF NOT EXISTS context_packages (
              id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, creator_membership_id TEXT NOT NULL,
              task_id TEXT NOT NULL, purpose TEXT NOT NULL, digest TEXT NOT NULL, revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS context_package_items (
              package_id TEXT NOT NULL REFERENCES context_packages(id), ordinal INTEGER NOT NULL,
              grant_id TEXT NOT NULL REFERENCES resource_grants(id), PRIMARY KEY(package_id, ordinal),
              UNIQUE(package_id, grant_id));
        """)

    def close(self) -> None:
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
    def _not_found() -> None:
        raise DuomitError("NOT_FOUND", "Resource is not available")

    def _artifact(self, context: OrganizationContext, artifact_id: str) -> sqlite3.Row:
        identifier(artifact_id)
        row = self._db.execute("SELECT * FROM resource_artifacts WHERE id=? AND organization_id=?",
                               (artifact_id, context.organization_id)).fetchone()
        if row is None:
            self._not_found()
        return row

    def _grant(self, context: OrganizationContext, grant_id: str) -> sqlite3.Row:
        identifier(grant_id)
        row = self._db.execute("SELECT * FROM resource_grants WHERE id=? AND organization_id=?",
                               (grant_id, context.organization_id)).fetchone()
        if row is None:
            self._not_found()
        return row

    def create_artifact(self, context: OrganizationContext, name: str, body: bytes,
                        *, shareability: str = "owner_only") -> ArtifactVersionReference:
        require(isinstance(name, str) and 1 <= len(name.strip()) <= 256, "Invalid artifact name")
        require(type(body) is bytes and 0 < len(body) <= 10 * 1024 * 1024, "Invalid artifact body")
        require(shareability in SHAREABILITIES, "Invalid artifact shareability")
        artifact_id = uuid.uuid4().hex
        content_digest = hashlib.sha256(body).hexdigest()
        with self._transaction():
            self._db.execute("INSERT INTO resource_artifacts VALUES (?,?,?,?,?,1)",
                             (artifact_id, context.organization_id, context.membership_id, name.strip(), shareability))
            self._db.execute("INSERT INTO resource_versions VALUES (?,?,?,?)",
                             (artifact_id, 1, content_digest, body))
        return ArtifactVersionReference(artifact_id, 1, content_digest)

    def issue_grant(self, context: OrganizationContext, artifact: ArtifactVersionReference,
                    grantee_membership_id: str, actions: tuple[str, ...], purpose: str,
                    *, expires_at: str | None = None) -> ResourceGrant:
        require(isinstance(artifact, ArtifactVersionReference), "Invalid artifact reference")
        identifier(grantee_membership_id)
        require(type(actions) is tuple and actions, "Invalid grant actions")
        require(purpose in RESOURCE_PURPOSES, "Invalid grant purpose")
        expiry = _valid_expiry(expires_at)
        with self._transaction():
            stored = self._artifact(context, artifact.artifact_id)
            if stored["owner_membership_id"] != context.membership_id or stored["shareability"] != "grantable":
                self._not_found()
            version = self._db.execute("SELECT content_digest FROM resource_versions WHERE artifact_id=? AND version=?",
                                       (artifact.artifact_id, artifact.version)).fetchone()
            if version is None or version["content_digest"] != artifact.content_digest:
                self._not_found()
            grant = ResourceGrant(uuid.uuid4().hex, artifact, grantee_membership_id, actions, purpose, expiry,
                                  "active", 1)
            self._db.execute("INSERT INTO resource_grants VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (grant.grant_id, context.organization_id, artifact.artifact_id, artifact.version,
                              grantee_membership_id, canonical_json(list(actions)), purpose, expiry, "active", 1))
            return grant

    def revoke_grant(self, context: OrganizationContext, grant_id: str, expected_revision: int) -> ResourceGrant:
        require(type(expected_revision) is int and expected_revision > 0, "Invalid grant revision")
        with self._transaction():
            row = self._grant(context, grant_id)
            artifact = self._artifact(context, row["artifact_id"])
            if artifact["owner_membership_id"] != context.membership_id:
                self._not_found()
            if row["revision"] != expected_revision:
                raise DuomitError("REVISION_CONFLICT", "Grant revision has changed")
            if row["state"] == "active":
                self._db.execute("UPDATE resource_grants SET state='revoked',revision=revision+1 WHERE id=?", (grant_id,))
            return self.get_grant(context, grant_id)

    def get_grant(self, context: OrganizationContext, grant_id: str) -> ResourceGrant:
        row = self._grant(context, grant_id)
        version = self._db.execute("SELECT content_digest FROM resource_versions WHERE artifact_id=? AND version=?",
                                   (row["artifact_id"], row["artifact_version"])).fetchone()
        if version is None:
            self._not_found()
        state = row["state"]
        if state == "active" and row["expires_at"] is not None and row["expires_at"] <= self._now():
            state = "expired"
        return ResourceGrant(row["id"], ArtifactVersionReference(row["artifact_id"], row["artifact_version"],
                             version["content_digest"]), row["grantee_membership_id"],
                             tuple(__import__("json").loads(row["actions_json"])), row["purpose"], row["expires_at"],
                             state, row["revision"])

    def _authorize(self, context: OrganizationContext, grant_id: str, action: str, purpose: str) -> ResourceGrant:
        grant = self.get_grant(context, grant_id)
        if (grant.state != "active" or grant.grantee_membership_id != context.membership_id
                or action not in grant.actions or grant.purpose != purpose):
            self._not_found()
        return grant

    def read_granted_artifact(self, context: OrganizationContext, grant_id: str) -> bytes:
        with self._transaction():
            grant = self._authorize(context, grant_id, "read_content", self.get_grant(context, grant_id).purpose)
            row = self._db.execute("SELECT body FROM resource_versions WHERE artifact_id=? AND version=?",
                                   (grant.artifact.artifact_id, grant.artifact.version)).fetchone()
            if row is None:
                self._not_found()
            return bytes(row["body"])

    def create_context_package(self, context: OrganizationContext, task_id: str, purpose: str,
                               grant_ids: tuple[str, ...]) -> ContextPackage:
        identifier(task_id)
        require(purpose in RESOURCE_PURPOSES, "Invalid context purpose")
        require(type(grant_ids) is tuple and 0 < len(grant_ids) <= 64 and len(set(grant_ids)) == len(grant_ids),
                "Invalid context items")
        with self._transaction():
            grants = tuple(self._authorize(context, grant_id, "use_as_context", purpose) for grant_id in grant_ids)
            payload = {"task_id": task_id, "purpose": purpose, "grants": [
                {"grant_id": grant.grant_id, "artifact": grant.artifact.artifact_id,
                 "version": grant.artifact.version, "digest": grant.artifact.content_digest,
                 "revision": grant.revision} for grant in grants]}
            package = ContextPackage(uuid.uuid4().hex, task_id, purpose, grant_ids, digest(payload), 1)
            self._db.execute("INSERT INTO context_packages VALUES (?,?,?,?,?,?,?)",
                             (package.context_package_id, context.organization_id, context.membership_id,
                              task_id, purpose, package.digest, 1))
            self._db.executemany("INSERT INTO context_package_items VALUES (?,?,?)",
                                 [(package.context_package_id, index, grant_id) for index, grant_id in enumerate(grant_ids)])
            return package

    def resolve_context_package(self, context: OrganizationContext, context_package_id: str) -> tuple[ArtifactVersionReference, ...]:
        identifier(context_package_id)
        with self._transaction():
            package = self._db.execute("SELECT * FROM context_packages WHERE id=? AND organization_id=? AND creator_membership_id=?",
                                       (context_package_id, context.organization_id, context.membership_id)).fetchone()
            if package is None:
                self._not_found()
            rows = self._db.execute("SELECT grant_id FROM context_package_items WHERE package_id=? ORDER BY ordinal",
                                    (context_package_id,)).fetchall()
            return tuple(self._authorize(context, row["grant_id"], "use_as_context", package["purpose"]).artifact
                         for row in rows)
