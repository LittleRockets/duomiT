import tempfile
import unittest
from pathlib import Path

from duomit.configuration_profiles import SqliteConfigurationProfiles
from duomit.errors import DuomitError
from duomit.foundation import ConfigurationField, ConfigurationProfile, ConfigurationSchema
from duomit.schema_registry import SchemaDefinition, SchemaRegistry


class ConfigurationProfileTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.registry = SchemaRegistry()
        self.registry.register(SchemaDefinition("runtime", "1.0.0", {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 1},
            "mode": {"type": "string", "default": "safe", "x-overridable": False},
        }, "required": []}))
        self.schema = ConfigurationSchema("runtime", "1.0.0", (
            ConfigurationField("limit", 1), ConfigurationField("mode", "safe", overridable=False),
        ))
        self.store = SqliteConfigurationProfiles(Path(self.temp.name) / "profiles.sqlite3", self.registry)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def profile(self, profile_id, scope, scope_id, values, revision=1):
        return ConfigurationProfile(profile_id, scope, scope_id, "runtime", "1.0.0", values, revision)

    def test_profiles_resolve_in_scope_order_and_pin_immutable_snapshot(self):
        self.store.save(self.profile("org-profile", "organization_default", "org", {"limit": 2}), 0)
        self.store.save(self.profile("task-profile", "task", "task", {"limit": 3}), 0)
        resolution = self.store.resolve(self.schema, "run", "run", {"organization_default": "org", "task": "task"})
        self.assertEqual(resolution.values["limit"], 3)
        snapshot = self.store.pin(resolution)
        updated = self.store.save(self.profile("task-profile", "task", "task", {"limit": 4}), 1)
        self.assertEqual(updated.profile_revision, 2)
        self.assertEqual(self.store.snapshot(snapshot.snapshot_id).resolution.values["limit"], 3)

    def test_save_uses_compare_and_swap_and_reopens(self):
        saved = self.store.save(self.profile("sandbox-profile", "sandbox", "sandbox", {"limit": 2}), 0)
        self.assertEqual(saved.profile_revision, 1)
        with self.assertRaises(DuomitError):
            self.store.save(self.profile("sandbox-profile", "sandbox", "sandbox", {"limit": 3}), 0)
        self.store.close()
        self.store = SqliteConfigurationProfiles(Path(self.temp.name) / "profiles.sqlite3", self.registry)
        profiles = self.store.profiles(self.schema, {"sandbox": "sandbox"})
        self.assertEqual(profiles[0].values["limit"], 2)

    def test_schema_validation_rejects_unknown_and_locked_values(self):
        with self.assertRaises(DuomitError):
            self.store.save(self.profile("p", "sandbox", "sandbox", {"unknown": 2}), 0)
        with self.assertRaises(DuomitError):
            self.store.save(self.profile("p", "sandbox", "sandbox", {"mode": "unsafe"}), 0)
