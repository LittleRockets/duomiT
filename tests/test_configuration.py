from dataclasses import replace
from pathlib import Path
import os
import tempfile
from unittest.mock import patch
import unittest

from duomit.api_configuration import configure_saved_api_services
from duomit.configuration_store import SqliteModelConfigurationStore
from duomit.contracts import ApiSelection, ChatMessage, Plan, RequestContext, RunSnapshot, Step, digest
from duomit.errors import DuomitError
from duomit.local_runtime import LocalRuntime
from duomit.registry import Capability
from test_api_models import RecordingTransport, api_response
from test_runtime import Recorder


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.sqlite3"
        self.store = self.open_store()
        self.alice = RequestContext("tenant", "alice", "request", "trace")

    def open_store(self):
        store = SqliteModelConfigurationStore(self.path)
        self.addCleanup(store.close)
        return store

    def test_reopen_and_historical_versions(self):
        first = self.store.save(self.alice, "planner", ApiSelection("openai", "model-one", "KEY_REF"), 0)
        self.store.save(self.alice, "planner", ApiSelection("deepseek", "model-two"), 1)
        restored = self.open_store()
        self.assertEqual(restored.load(self.alice, "planner").revision, 2)
        self.assertEqual(restored.load(self.alice, "planner", 1), first)
        self.assertIsNone(restored.load(self.alice, "assistant"))

    def test_stale_compare_and_swap_and_role_isolation(self):
        self.store.save(self.alice, "planner", ApiSelection("openai", "model"), 0)
        other_connection = self.open_store()
        with self.assertRaises(DuomitError) as error:
            other_connection.save(self.alice, "planner", ApiSelection("grok", "other"), 0)
        self.assertEqual(error.exception.code, "CONFLICT")
        self.assertEqual(self.store.load(self.alice, "planner").selection.provider, "openai")
        self.store.save(self.alice, "assistant", ApiSelection("gemini", "model"), 0)
        self.assertEqual(self.store.load(self.alice, "assistant").revision, 1)

    def test_cross_user_and_tenant_cannot_read_or_disable(self):
        self.store.save(self.alice, "planner", ApiSelection("openai", "model"), 0)
        for tenant, owner in (("other", "alice"), ("tenant", "bob")):
            context = RequestContext(tenant, owner, "r", "t")
            self.assertIsNone(self.store.load(context, "planner"))
            with self.assertRaises(DuomitError):
                self.store.disable(context, "planner", 1)
        self.assertEqual(self.store.load(self.alice, "planner").revision, 1)

    def test_disable_and_new_revision_stop_cached_api_binding(self):
        self.store.save(self.alice, "assistant", ApiSelection("openai", "model", "FIXTURE_API_KEY"), 0)
        transport = RecordingTransport(api_response("openai"))
        services = configure_saved_api_services(self.alice, self.store, transport=transport)
        disabled_revision = self.store.disable(self.alice, "assistant", 1)
        with self.assertRaises(DuomitError) as error:
            services.assistant.respond(self.alice, (ChatMessage("user", "hello"),))
        self.assertEqual(error.exception.code, "MODEL_DISABLED")
        with self.assertRaises(DuomitError):
            self.store.save(self.alice, "assistant", ApiSelection("openai", "model-two", "FIXTURE_API_KEY"), 1)
        self.store.save(self.alice, "assistant", ApiSelection("openai", "model-two", "FIXTURE_API_KEY"), disabled_revision)
        with self.assertRaises(DuomitError) as error:
            services.assistant.respond(self.alice, (ChatMessage("user", "hello"),))
        self.assertEqual(error.exception.code, "CONFIGURATION_CHANGED")
        self.assertEqual(transport.calls, [])

    def test_configuration_never_persists_api_key_values(self):
        with patch.dict(os.environ, {"FIXTURE_API_KEY": "do-not-persist-this-secret"}):
            self.store.save(self.alice, "assistant", ApiSelection("deepseek", "model", "FIXTURE_API_KEY"), 0)
            services = configure_saved_api_services(self.alice, self.store,
                transport=RecordingTransport(api_response("deepseek")))
            self.assertEqual(services.assistant.respond(self.alice, (ChatMessage("user", "hello"),)).content, "Hello")
        self.assertNotIn(b"do-not-persist-this-secret", self.path.read_bytes())


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "runs.sqlite3"
        self.context = RequestContext("tenant", "alice", "r", "t")
        self.policy = (Capability("read", "1.0"),)
        self.runtime = self.open_runtime(self.policy)
        self.plan = Plan((Step("a", "read"),))
        self.snapshot = RunSnapshot("binding", 2, digest({"material": "version-two"}), (("read", "1.0"),))

    def open_runtime(self, policy):
        runtime = LocalRuntime(self.path, policy)
        self.addCleanup(runtime.close)
        return runtime

    def test_snapshot_is_persistent_and_part_of_idempotency(self):
        run = self.runtime.create(self.context, self.plan, "task", snapshot=self.snapshot)
        restored = self.open_runtime(self.policy)
        self.assertEqual(restored.inspect(self.context, run)["snapshot"], self.snapshot.to_dict())
        self.assertEqual(run, restored.create(self.context, self.plan, "task", snapshot=self.snapshot))
        with self.assertRaises(DuomitError):
            restored.create(self.context, self.plan, "task", snapshot=replace(self.snapshot, binding_revision=3))

    def test_version_change_blocks_execution_before_side_effect(self):
        run = self.runtime.create(self.context, self.plan, "task", snapshot=self.snapshot)
        changed = self.open_runtime((Capability("read", "2.0"),))
        recorder = Recorder()
        with self.assertRaises(DuomitError) as error:
            changed.drive(self.context, run, recorder)
        self.assertEqual(error.exception.code, "POLICY_CHANGED")
        self.assertEqual(recorder.calls, [])
        self.assertEqual(changed.inspect(self.context, run)["state"], "ready")

    def test_snapshot_missing_or_wrong_versions_rejected(self):
        for snapshot in (replace(self.snapshot, capability_versions=(("other", "1.0"),)),
                         replace(self.snapshot, capability_versions=(("read", "2.0"),))):
            with self.assertRaises(DuomitError):
                self.runtime.create(self.context, self.plan, "task", snapshot=snapshot)


if __name__ == "__main__":
    unittest.main()
