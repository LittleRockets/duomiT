"""SQLite reference adapter for versioned, pinned configuration profiles."""

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import uuid

from .contracts import canonical_json, identifier
from .errors import DuomitError, require
from .foundation import (CONFIG_SCOPES, ConfigurationProfile, ConfigurationResolution,
                         ConfigurationSchema, resolve_configuration)
from .schema_registry import SchemaRegistry


@dataclass(frozen=True)
class ConfigurationSnapshot:
    snapshot_id: str
    resolution: ConfigurationResolution

    def __post_init__(self):
        identifier(self.snapshot_id)
        require(isinstance(self.resolution, ConfigurationResolution), "Invalid configuration snapshot")


class SqliteConfigurationProfiles:
    """Persistent adapter; callers provide schema registration and trusted scope IDs."""

    def __init__(self, database: str | Path, registry: SchemaRegistry):
        require(isinstance(registry, SchemaRegistry), "Invalid schema registry")
        self._registry = registry
        self._db = sqlite3.connect(str(database), isolation_level=None, timeout=5)
        self._db.row_factory = sqlite3.Row
        with self._transaction():
            self._db.execute("""CREATE TABLE IF NOT EXISTS configuration_profiles (
                profile_id TEXT PRIMARY KEY, scope TEXT NOT NULL, scope_id TEXT NOT NULL,
                schema_id TEXT NOT NULL, schema_version TEXT NOT NULL, values_json TEXT NOT NULL,
                revision INTEGER NOT NULL, UNIQUE(scope, scope_id, schema_id))""")
            self._db.execute("""CREATE TABLE IF NOT EXISTS configuration_snapshots (
                snapshot_id TEXT PRIMARY KEY, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
                schema_id TEXT NOT NULL, schema_version TEXT NOT NULL, resolution_json TEXT NOT NULL,
                effective_digest TEXT NOT NULL)""")

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._db.close()

    def _validate_profile(self, profile: ConfigurationProfile) -> None:
        definition = self._registry.resolve(profile.schema_id, profile.schema_version)
        # Rebuild fields from the conservative registered document.
        from .foundation import ConfigurationField
        fields = tuple(ConfigurationField(name, field.get("default"),
                    field.get("x-overridable", True), field.get("x-sensitive", False))
                       for name, field in definition.document["properties"].items())
        schema = ConfigurationSchema(definition.schema_id, definition.version, fields)
        resolve_configuration(schema, "profile_validation", profile.profile_id, (profile,))

    def save(self, profile: ConfigurationProfile, expected_revision: int) -> ConfigurationProfile:
        require(isinstance(profile, ConfigurationProfile), "Invalid configuration profile")
        require(type(expected_revision) is int and expected_revision >= 0, "Invalid expected revision")
        self._validate_profile(profile)
        with self._transaction():
            row = self._db.execute("SELECT * FROM configuration_profiles WHERE scope=? AND scope_id=? AND schema_id=?",
                                   (profile.scope, profile.scope_id, profile.schema_id)).fetchone()
            if row is None:
                require(expected_revision == 0 and profile.profile_revision == 1,
                        "Configuration profile already changed")
                self._db.execute("INSERT INTO configuration_profiles VALUES (?,?,?,?,?,?,?)",
                                 (profile.profile_id, profile.scope, profile.scope_id, profile.schema_id,
                                  profile.schema_version, canonical_json(dict(profile.values)), 1))
                return profile
            require(row["revision"] == expected_revision,
                    "Configuration profile revision changed")
            next_revision = row["revision"] + 1
            saved = ConfigurationProfile(row["profile_id"], profile.scope, profile.scope_id,
                                         profile.schema_id, profile.schema_version, dict(profile.values),
                                         next_revision)
            self._db.execute("""UPDATE configuration_profiles SET schema_version=?, values_json=?, revision=?
                WHERE profile_id=?""", (saved.schema_version, canonical_json(dict(saved.values)),
                                          saved.profile_revision, saved.profile_id))
            return saved

    def profiles(self, schema: ConfigurationSchema, scope_ids: dict[str, str]) -> tuple[ConfigurationProfile, ...]:
        require(set(scope_ids) <= set(CONFIG_SCOPES), "Invalid configuration scope")
        result = []
        for scope in CONFIG_SCOPES:
            scope_id = scope_ids.get(scope)
            if scope_id is None:
                continue
            identifier(scope_id)
            row = self._db.execute("""SELECT * FROM configuration_profiles
                WHERE scope=? AND scope_id=? AND schema_id=?""", (scope, scope_id, schema.schema_id)).fetchone()
            if row is not None:
                result.append(ConfigurationProfile(row["profile_id"], row["scope"], row["scope_id"],
                              row["schema_id"], row["schema_version"], json.loads(row["values_json"]), row["revision"]))
        return tuple(result)

    def resolve(self, schema: ConfigurationSchema, target_type: str, target_id: str,
                scope_ids: dict[str, str]) -> ConfigurationResolution:
        return resolve_configuration(schema, target_type, target_id, self.profiles(schema, scope_ids))

    def pin(self, resolution: ConfigurationResolution) -> ConfigurationSnapshot:
        require(isinstance(resolution, ConfigurationResolution), "Invalid configuration resolution")
        snapshot = ConfigurationSnapshot(uuid.uuid4().hex, resolution)
        body = {"target_type": resolution.target_type, "target_id": resolution.target_id,
                "schema_id": resolution.schema_id, "schema_version": resolution.schema_version,
                "profile_references": [item.to_dict() for item in resolution.profile_references],
                "values": dict(resolution.values), "effective_digest": resolution.effective_digest}
        with self._transaction():
            self._db.execute("INSERT INTO configuration_snapshots VALUES (?,?,?,?,?,?,?)",
                             (snapshot.snapshot_id, resolution.target_type, resolution.target_id,
                              resolution.schema_id, resolution.schema_version, canonical_json(body),
                              resolution.effective_digest))
        return snapshot

    def snapshot(self, snapshot_id: str) -> ConfigurationSnapshot:
        identifier(snapshot_id)
        row = self._db.execute("SELECT * FROM configuration_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if row is None:
            raise DuomitError("NOT_FOUND", "Configuration snapshot is not available")
        value = json.loads(row["resolution_json"])
        from .foundation import PinnedReference
        resolution = ConfigurationResolution(value["target_type"], value["target_id"], value["schema_id"],
                    value["schema_version"], tuple(PinnedReference(**item) for item in value["profile_references"]),
                    value["values"], value["effective_digest"])
        return ConfigurationSnapshot(snapshot_id, resolution)
