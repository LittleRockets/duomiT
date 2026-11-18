from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from duomit.__main__ import main
from duomit.api_configuration import ApiSelection, configure_api_services
from duomit.contracts import (AssistantBinding, ChatMessage, ChatRequest, ModelMaterial,
                              Plan, RequestContext, Step)
from duomit.errors import DuomitError
from duomit.model_access import ModelBroker, ReferenceDesktop
from test_api_models import RecordingTransport, api_response


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.context = RequestContext("tenant", "alice", "request", "trace")
        self.environment = patch.dict(os.environ, {"PLAN_KEY": "plan-secret", "HELP_KEY": "assistant-secret"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_roles_use_separate_provider_model_and_credential(self):
        class MultiTransport(RecordingTransport):
            def post(inner, url, headers, payload, timeout_seconds):
                inner.calls.append((url, headers, payload, timeout_seconds))
                if "deepseek" in url:
                    return api_response("deepseek", json.dumps(Plan((Step("a", "data.read"),)).to_dict()))
                return api_response("gemini", "Advice only")

        transport = MultiTransport({})
        services = configure_api_services(self.context,
            planner=ApiSelection("deepseek", "planning-model", "PLAN_KEY"),
            assistant=ApiSelection("gemini", "assistant-model", "HELP_KEY"), transport=transport)
        self.assertEqual(transport.calls, [])
        self.assertEqual(services.planner.propose(self.context, "Private task").steps[0].capability, "data.read")
        reply = services.assistant.respond(self.context, (ChatMessage("user", "Review supplied text"),))
        self.assertEqual(reply.content, "Advice only")
        self.assertEqual(transport.calls[0][1]["Authorization"], "Bearer plan-secret")
        self.assertEqual(transport.calls[1][1]["x-goog-api-key"], "assistant-secret")
        self.assertNotIn("Private task", json.dumps(transport.calls[1][2]))
        self.assertNotIn("tools", transport.calls[1][2])

    def test_missing_roles_do_not_fall_back(self):
        only_planner = configure_api_services(self.context, planner=ApiSelection("grok", "model", "PLAN_KEY"))
        self.assertIsNone(only_planner.assistant)
        only_assistant = configure_api_services(self.context, assistant=ApiSelection("openai", "model", "HELP_KEY"))
        self.assertIsNone(only_assistant.planner)

    def test_assistant_denies_cross_user_and_cross_tenant_before_api_call(self):
        transport = RecordingTransport(api_response("openai"))
        services = configure_api_services(self.context, assistant=ApiSelection("openai", "model", "HELP_KEY"), transport=transport)
        for tenant, principal in (("tenant", "bob"), ("other", "alice")):
            with self.assertRaises(DuomitError) as error:
                services.assistant.respond(RequestContext(tenant, principal, "r", "t"), (ChatMessage("user", "hello"),))
            self.assertEqual(error.exception.code, "NOT_FOUND")
        self.assertEqual(transport.calls, [])

    def test_explicit_history_is_forwarded_without_hidden_memory(self):
        transport = RecordingTransport(api_response("deepseek"))
        services = configure_api_services(self.context, assistant=ApiSelection("deepseek", "model", "HELP_KEY"), transport=transport)
        messages = (ChatMessage("user", "first"), ChatMessage("assistant", "answer"), ChatMessage("user", "follow-up"))
        services.assistant.respond(self.context, messages)
        sent = transport.calls[0][2]["messages"]
        self.assertEqual([item["content"] for item in sent[1:]], ["first", "answer", "follow-up"])

    def test_unregistered_material_and_excess_budget_fail_before_call(self):
        transport = RecordingTransport(api_response("deepseek"))
        desktop = ReferenceDesktop()
        binding = AssistantBinding("tenant", "alice", "assistant", 1, ModelMaterial("model", "1.0", "adapter"), max_output_tokens=10)
        broker = ModelBroker(desktop, {})
        for request in (ChatRequest((ChatMessage("user", "hello"),), max_output_tokens=11),
                        ChatRequest((ChatMessage("user", "hello"),), max_output_tokens=10)):
            with self.assertRaises(DuomitError):
                broker.complete(self.context, binding, request)
        self.assertEqual(transport.calls, [])


class CliTests(unittest.TestCase):
    def test_help_does_not_read_credentials_or_call_api(self):
        with patch("sys.argv", ["duomit", "--help"]), redirect_stdout(StringIO()), self.assertRaises(SystemExit) as result:
            main()
        self.assertEqual(result.exception.code, 0)

    def test_assistant_api_cli(self):
        transport = RecordingTransport(api_response("gemini", "Assistant answer"))
        output = StringIO()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), \
                patch("duomit.api_models.HttpsJsonTransport.post", side_effect=transport.post), \
                patch("sys.argv", ["duomit", "--assistant-provider", "gemini", "--assistant-model-id", "model", "--ask", "hello"]), \
                redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue())["content"], "Assistant answer")
        self.assertEqual(len(transport.calls), 1)

    def test_planning_api_cli(self):
        plan = Plan((Step("a", "data.read"),))
        transport = RecordingTransport(api_response("openai", json.dumps(plan.to_dict())))
        output = StringIO()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
                patch("duomit.api_models.HttpsJsonTransport.post", side_effect=transport.post), \
                patch("sys.argv", ["duomit", "--provider", "openai", "--model-id", "model", "--goal", "report"]), \
                redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue()), plan.to_dict())
        self.assertEqual(len(transport.calls), 1)

    def test_cli_rejects_incomplete_config_and_implicit_assistant_fallback(self):
        cases = (["--provider", "openai"], ["--assistant-provider", "gemini"],
                 ["--provider", "deepseek", "--model-id", "model", "--ask", "hello"],
                 ["--ask", "hello"], ["--api-key-env", "KEY"])
        for arguments in cases:
            with self.subTest(arguments=arguments), patch("sys.argv", ["duomit"] + arguments), \
                    redirect_stderr(StringIO()), self.assertRaises(SystemExit) as result:
                main()
            self.assertEqual(result.exception.code, 2)

    def test_approval_resumes_saved_plan_without_another_api_call(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            path = str(Path(directory) / "run.sqlite3")
            with patch("sys.argv", ["duomit", "--database", path]), redirect_stdout(StringIO()):
                main()
            output = StringIO()
            with patch("duomit.api_models.HttpsJsonTransport.post") as post, \
                    patch("sys.argv", ["duomit", "--database", path, "--resume", "--approve",
                                       "--provider", "openai", "--model-id", "model"]), redirect_stdout(output):
                main()
            self.assertEqual(json.loads(output.getvalue())["state"], "succeeded")
            post.assert_not_called()

    def test_save_and_load_configuration_without_sending_api_keys_to_disk(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            path = str(Path(directory) / "settings.sqlite3")
            output = StringIO()
            with patch("sys.argv", ["duomit", "--config-database", path, "--save-config",
                                    "--assistant-provider", "gemini", "--assistant-model-id", "saved-model"]), \
                    patch("duomit.api_models.HttpsJsonTransport.post") as post, redirect_stdout(output):
                main()
            self.assertEqual(json.loads(output.getvalue()), {"saved_revisions": {"assistant": 1}})
            post.assert_not_called()
            transport = RecordingTransport(api_response("gemini", "Saved assistant answer"))
            output = StringIO()
            with patch.dict(os.environ, {"GEMINI_API_KEY": "fixture-key"}), \
                    patch("sys.argv", ["duomit", "--config-database", path, "--ask", "hello"]), \
                    patch("duomit.api_models.HttpsJsonTransport.post", side_effect=transport.post), redirect_stdout(output):
                main()
            self.assertEqual(json.loads(output.getvalue())["content"], "Saved assistant answer")
            self.assertIn("saved-model", transport.calls[0][0])


if __name__ == "__main__":
    unittest.main()
