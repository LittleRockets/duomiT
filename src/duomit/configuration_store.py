"""SQLite adapter for versioned user-owned API configuration, never API key values."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .contracts import ApiSelection, ModelConfiguration, RequestContext
from .errors import DuomitError, require


class SqliteModelConfigurationStore:
    def __init__(self, database: str | Path):
        self._db = sqlite3.connect(str(database), isolation_level=None, timeout=5)
        self._db.row_factory = sqlite3.Row
        with self._transaction():
            self._db.execute("""CREATE TABLE IF NOT EXISTS model_config_heads (
                tenant TEXT NOT NULL, owner TEXT NOT NULL, role TEXT NOT NULL,
                revision INTEGER NOT NULL, enabled INTEGER NOT NULL,
                PRIMARY KEY(tenant,owner,role))""")
            self._db.execute("""CREATE TABLE IF NOT EXISTS model_config_versions (
                tenant TEXT NOT NULL, owner TEXT NOT NULL, role TEXT NOT NULL,
                revision INTEGER NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                credential_env TEXT, PRIMARY KEY(tenant,owner,role,revision))""")

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

    def _head(self, context, role):
        require(role in ("planner", "assistant"), "Invalid model role")
        return self._db.execute("SELECT * FROM model_config_heads WHERE tenant=? AND owner=? AND role=?",
                                (context.tenant_id, context.principal_id, role)).fetchone()

    def save(self, context: RequestContext, role: str, selection: ApiSelection,
             expected_revision: int) -> ModelConfiguration:
        require(isinstance(selection, ApiSelection), "Invalid API selection")
        require(type(expected_revision) is int and expected_revision >= 0, "Invalid expected revision")
        with self._transaction():
            head = self._head(context, role)
            current = head["revision"] if head else 0
            if current != expected_revision:
                raise DuomitError("CONFLICT", "Model configuration revision changed")
            revision = current + 1
            self._db.execute("INSERT INTO model_config_versions VALUES (?,?,?,?,?,?,?)",
                             (context.tenant_id, context.principal_id, role, revision,
                              selection.provider, selection.model, selection.credential_env))
            self._db.execute("""INSERT INTO model_config_heads VALUES (?,?,?,?,1)
                ON CONFLICT(tenant,owner,role) DO UPDATE SET revision=excluded.revision,enabled=1""",
                (context.tenant_id, context.principal_id, role, revision))
            return ModelConfiguration(role, revision, selection)

    def load(self, context: RequestContext, role: str,
             revision: int | None = None) -> ModelConfiguration | None:
        require(revision is None or (type(revision) is int and revision > 0), "Invalid revision")
        with self._transaction():
            head = self._head(context, role)
            if head is None:
                return None
            if not head["enabled"]:
                raise DuomitError("MODEL_DISABLED", "Model role is disabled")
            row = self._db.execute("""SELECT * FROM model_config_versions
                WHERE tenant=? AND owner=? AND role=? AND revision=?""",
                (context.tenant_id, context.principal_id, role,
                 revision if revision is not None else head["revision"])).fetchone()
            if row is None:
                raise DuomitError("NOT_FOUND", "Model configuration is not available")
            return ModelConfiguration(role, row["revision"], ApiSelection(
                row["provider"], row["model"], row["credential_env"]))

    def disable(self, context: RequestContext, role: str, expected_revision: int) -> int:
        with self._transaction():
            head = self._head(context, role)
            if head is None:
                raise DuomitError("NOT_FOUND", "Model configuration is not available")
            if type(expected_revision) is not int or head["revision"] != expected_revision:
                raise DuomitError("CONFLICT", "Model configuration revision changed")
            self._db.execute("UPDATE model_config_heads SET enabled=0,revision=revision+1 WHERE tenant=? AND owner=? AND role=?",
                             (context.tenant_id, context.principal_id, role))
            return head["revision"] + 1
