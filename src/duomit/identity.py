"""Local bearer-token identity adapter; not an HTTP/OIDC authentication service.

Issuance and revocation are trusted administrative APIs. Protect the SQLite file
with operating-system access controls. Public entry points expose authenticate
only, and derive tenant/principal from the stored identity rather than user input.
"""

import hashlib
import math
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

from .contracts import RequestContext, identifier
from .errors import DuomitError, require


class LocalIdentityStore:
    def __init__(self, database: str | Path, *, clock=time.time):
        self._clock = clock
        self._db = sqlite3.connect(str(database), isolation_level=None, timeout=5)
        self._db.execute("""CREATE TABLE IF NOT EXISTS local_identities (
            token_hash TEXT PRIMARY KEY, tenant TEXT NOT NULL, principal TEXT NOT NULL,
            expires_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)""")

    def close(self):
        self._db.close()

    def issue(self, tenant_id: str, principal_id: str, *, valid_seconds: float = 3600) -> str:
        """Trusted admin provisioner only. Return a token once, never persist its value."""
        identifier(tenant_id)
        identifier(principal_id)
        require(type(valid_seconds) in (int, float) and math.isfinite(valid_seconds)
                and 0 < valid_seconds <= 86400, "Invalid identity lifetime")
        token = secrets.token_urlsafe(32)
        self._db.execute("INSERT INTO local_identities VALUES (?,?,?,?,0)",
                         (self._hash(token), tenant_id, principal_id, self._clock() + valid_seconds))
        return token

    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    @staticmethod
    def _valid_shape(token):
        return (isinstance(token, str) and 32 <= len(token) <= 128
                and all(char in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in token))

    def authenticate(self, token: str, *, request_id: str | None = None,
                     trace_id: str | None = None) -> RequestContext:
        if not self._valid_shape(token):
            raise DuomitError("UNAUTHENTICATED", "Access token is invalid or expired")
        row = self._db.execute("SELECT tenant,principal,expires_at,revoked FROM local_identities WHERE token_hash=?",
                               (self._hash(token),)).fetchone()
        if row is None or row[3] or row[2] <= self._clock():
            raise DuomitError("UNAUTHENTICATED", "Access token is invalid or expired")
        return RequestContext(row[0], row[1], request_id or uuid.uuid4().hex, trace_id or uuid.uuid4().hex)

    def revoke(self, token: str) -> None:
        """Trusted admin operation, idempotent; unknown token status is not disclosed."""
        if self._valid_shape(token):
            self._db.execute("UPDATE local_identities SET revoked=1 WHERE token_hash=?", (self._hash(token),))
