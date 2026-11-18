from contextlib import redirect_stdout
from io import StringIO
import os
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from duomit.__main__ import main
from duomit.api_configuration import configure_saved_api_services
from duomit.configuration_store import SqliteModelConfigurationStore
from duomit.contracts import ApiSelection, ChatMessage, Plan, Step
from duomit.errors import DuomitError
from duomit.identity import LocalIdentityStore
from duomit.local_runtime import LocalRuntime
from duomit.registry import Capability
from test_api_models import RecordingTransport, api_response


class IdentityTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "identities.sqlite3"
        self.now = [100.0]
        self.identities = self.open_store()

    def open_store(self):
        store = LocalIdentityStore(self.path, clock=lambda: self.now[0])
        self.addCleanup(store.close)
        return store

    def test_authentication_derives_identity_and_persists_only_token_hash(self):
        token = self.identities.issue("tenant", "alice")
        context = self.open_store().authenticate(token, request_id="request", trace_id="trace")
        self.assertEqual((context.tenant_id, context.principal_id), ("tenant", "alice"))
        self.assertEqual((context.request_id, context.trace_id), ("request", "trace"))
        self.assertNotIn(token.encode("ascii"), self.path.read_bytes())

    def test_expired_revoked_unknown_and_malformed_tokens_fail_identically(self):
        expired = self.identities.issue("tenant", "alice", valid_seconds=1)
        revoked = self.identities.issue("tenant", "alice")
        self.identities.revoke(revoked)
        self.identities.revoke(revoked)
        self.now[0] += 2
        for token in (expired, revoked, "x" * 43, "short", None, "a" * 129, "中" * 43):
            with self.subTest(token_type=type(token)), self.assertRaises(DuomitError) as error:
                self.identities.authenticate(token)
            self.assertEqual(error.exception.code, "UNAUTHENTICATED")
            self.assertEqual(error.exception.message, "Access token is invalid or expired")

    def test_invalid_lifetime_rejected(self):
        for value in (0, -1, True, float("inf"), float("nan"), 86401):
            with self.assertRaises(DuomitError):
                self.identities.issue("tenant", "alice", valid_seconds=value)

    def test_token_cannot_read_another_users_config_or_run(self):
        alice = self.identities.authenticate(self.identities.issue("tenant", "alice"))
        bob = self.identities.authenticate(self.identities.issue("tenant", "bob"))
        configs = SqliteModelConfigurationStore(Path(self.temp.name) / "config.sqlite3")
        self.addCleanup(configs.close)
        configs.save(alice, "assistant", ApiSelection("deepseek", "model", "FIXTURE_KEY"), 0)
        self.assertIsNone(configs.load(bob, "assistant"))
        transport = RecordingTransport(api_response("deepseek"))
        services = configure_saved_api_services(alice, configs, transport=transport)
        with self.assertRaises(DuomitError):
            services.assistant.respond(bob, (ChatMessage("user", "hello"),))
        self.assertEqual(transport.calls, [])
        runtime = LocalRuntime(Path(self.temp.name) / "runs.sqlite3", (Capability("read", "1.0"),))
        self.addCleanup(runtime.close)
        run = runtime.create(alice, Plan((Step("a", "read"),)), "task")
        with self.assertRaises(DuomitError):
            runtime.inspect(bob, run)

    def test_cli_rejects_bad_token_before_api_call(self):
        with patch.dict(os.environ, {"DUOMIT_ACCESS_TOKEN": "invalid-token"}), \
                patch("sys.argv", ["duomit", "--identity-database", str(self.path), "--provider", "openai", "--model-id", "model"]), \
                patch("duomit.api_models.HttpsJsonTransport.post") as post, \
                self.assertRaises(DuomitError) as error:
            main()
        self.assertEqual(error.exception.code, "UNAUTHENTICATED")
        post.assert_not_called()

    def test_cli_configuration_uses_authenticated_owner(self):
        # Use the real wall clock for a token intended for the CLI process.
        live = LocalIdentityStore(self.path)
        self.addCleanup(live.close)
        token = live.issue("real-tenant", "real-user")
        config_path = Path(self.temp.name) / "settings.sqlite3"
        with patch.dict(os.environ, {"DUOMIT_ACCESS_TOKEN": token}), \
                patch("sys.argv", ["duomit", "--identity-database", str(self.path),
                                   "--config-database", str(config_path), "--save-config",
                                   "--assistant-provider", "gemini", "--assistant-model-id", "model"]), \
                redirect_stdout(StringIO()):
            main()
        config = SqliteModelConfigurationStore(config_path)
        self.addCleanup(config.close)
        authenticated = live.authenticate(token)
        self.assertEqual(config.load(authenticated, "assistant").selection.provider, "gemini")

    def test_authenticated_offline_plan_is_bound_to_the_authenticated_owner(self):
        live = LocalIdentityStore(self.path)
        self.addCleanup(live.close)
        token = live.issue("real-tenant", "real-user")
        output = StringIO()
        with patch.dict(os.environ, {"DUOMIT_ACCESS_TOKEN": token}), \
                patch("sys.argv", ["duomit", "--identity-database", str(self.path)]), redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue())["schema_version"], "1.0")


if __name__ == "__main__":
    unittest.main()
