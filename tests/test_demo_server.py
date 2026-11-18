from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import tempfile
from threading import Thread
from unittest.mock import patch
import unittest

from duomit.demo_server import DemoApplication, create_server
from duomit.errors import DuomitError


class DemoApplicationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.application = DemoApplication(self.temp.name)

    def plan(self):
        return self.application.dispatch("plan", {"goal": "Generate a report", "provider": "offline", "model": ""})["run"]

    def test_plan_drive_approve_and_restore(self):
        run = self.plan()
        waiting = self.application.dispatch("drive", {"run_id": run["run_id"], "fault": False})["run"]
        self.assertEqual(waiting["state"], "waiting_approval")
        step = next(step for step in waiting["steps"] if step["state"] == "waiting_approval")
        result = self.application.dispatch("approve", {"run_id": run["run_id"], "revision": waiting["revision"],
                    "step_id": step["id"], "operation_key": step["operation_key"]})
        self.assertEqual(result["run"]["state"], "succeeded")
        self.assertEqual(result["run"]["definition"]["steps"][0]["capability"], "data.read")
        restored = DemoApplication(self.temp.name).dispatch("state", {"run_id": run["run_id"]})
        self.assertEqual(restored, result)

    def test_fault_is_reconciled_without_repeating_the_unknown_action(self):
        run = self.plan()
        uncertain = self.application.dispatch("drive", {"run_id": run["run_id"], "fault": True})["run"]
        self.assertEqual(uncertain["state"], "reconciling")
        step = uncertain["steps"][0]
        result = self.application.dispatch("reconcile", {"run_id": run["run_id"], "revision": uncertain["revision"],
            "step_id": step["id"], "operation_key": step["operation_key"], "success": True})
        self.assertEqual(result["run"]["state"], "waiting_approval")

    def test_node_demo_and_cancel(self):
        node_result = self.application.dispatch("nodes", {})
        self.assertTrue(node_result["workspaces_isolated"])
        self.assertEqual(node_result["active_leases_after_close"], 0)
        run = self.plan()
        result = self.application.dispatch("cancel", {"run_id": run["run_id"], "revision": run["revision"]})
        self.assertEqual(result["run"]["state"], "cancelled")

    def test_missing_api_key_does_not_fall_back_to_offline(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(DuomitError) as error:
            self.application.dispatch("plan", {"goal": "Report", "provider": "openai", "model": "model"})
        self.assertEqual(error.exception.code, "MODEL_CREDENTIAL_MISSING")


class LocalHttpTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.server = create_server(self.temp.name, port=0)
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            self.server.shutdown()
            thread.join(timeout=3)
            self.server.server_close()

        self.addCleanup(cleanup)
        self.port = self.server.server_port
        self.origin = f"http://127.0.0.1:{self.port}"

    def request(self, method, path, body=None, headers=None):
        with closing(HTTPConnection("127.0.0.1", self.port, timeout=5)) as connection:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()

    def test_page_loads_and_authorized_api_works(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        token = re.search(rb"const token='([A-Za-z0-9_-]+)'", body)[1].decode("ascii")
        status, _, body = self.request("POST", "/api/plan", json.dumps({"goal": "Report", "provider": "offline", "model": ""}),
            {"Content-Type": "application/json", "X-Duomit-Demo": token, "Origin": self.origin})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["run"]["state"], "ready")
        self.assertEqual(self.request("GET", "/icon.svg")[0], 200)

    def test_host_origin_and_missing_token_are_rejected(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": "attacker.invalid"})[0], 403)
        self.assertEqual(self.request("POST", "/api/nodes", "{}", {"Content-Type": "application/json"})[0], 403)
        _, _, page = self.request("GET", "/")
        token = re.search(rb"const token='([A-Za-z0-9_-]+)'", page)[1].decode("ascii")
        self.assertEqual(self.request("POST", "/api/nodes", "{}", {"Content-Type": "application/json",
            "X-Duomit-Demo": token, "Origin": "https://attacker.invalid"})[0], 403)

    def test_source_files_and_internal_docs_are_not_served(self):
        for path in ("/docs/03-phased-delivery.md", "/../README.md", "/.env", "/src/duomit/contracts.py"):
            self.assertEqual(self.request("GET", path)[0], 404)


if __name__ == "__main__":
    unittest.main()
