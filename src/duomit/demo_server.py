"""Loopback-only interactive test harness. Not a production web/API service."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit
import uuid
import webbrowser

from .__main__ import reference_components
from .api_configuration import ApiSelection, configure_api_services
from .api_models import PROVIDERS
from .contracts import ChatMessage, RequestContext, fields, identifier, strict_json
from .errors import DuomitError, require
from .local_runtime import LocalRuntime
from .node_demo import run_demo
from .registry import Capability


class SimulatedExecutor:
    def __init__(self, fault=False):
        self._fault = fault

    def execute(self, context, capability, operation_key):
        if self._fault:
            raise TimeoutError("simulated missing receipt")
        return "artifact:simulated-" + operation_key


class DemoApplication:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def context():
        return RequestContext("demo-tenant", "demo-user", uuid.uuid4().hex, uuid.uuid4().hex)

    def dispatch(self, action: str, data: dict):
        context = self.context()
        if action == "nodes":
            fields(data, set())
            return run_demo(self.directory / "desktop.sqlite3")
        if action == "assistant":
            fields(data, {"provider", "model", "text"})
            require(data["provider"] in PROVIDERS, "Select an API provider")
            services = configure_api_services(context, assistant=ApiSelection(data["provider"], data["model"]))
            response = services.assistant.respond(context, (ChatMessage("user", data["text"]),))
            return {"content": response.content, "model_id": response.model_id,
                    "input_tokens": response.input_tokens, "output_tokens": response.output_tokens}
        runtime = LocalRuntime(self.directory / "workflows.sqlite3",
                               (Capability("data.read", "1.0"), Capability("report.render", "1.0", True)))
        try:
            if action == "plan":
                fields(data, {"goal", "provider", "model"})
                if data["provider"] == "offline":
                    _, planner = reference_components("sequential", context)
                else:
                    require(data["provider"] in PROVIDERS, "Select an API provider")
                    planner = configure_api_services(context, planner=ApiSelection(data["provider"], data["model"])).planner
                plan = planner.propose(context, data["goal"])
                run_id = runtime.create(context, plan, uuid.uuid4().hex, snapshot=planner.snapshot)
                return {"plan": plan.to_dict(), "run": runtime.inspect(context, run_id),
                        "events": self._events(runtime, context, run_id)}
            if action == "state":
                fields(data, {"run_id"})
                return self._state(runtime, context, data["run_id"])
            if action == "drive":
                fields(data, {"run_id", "fault"})
                require(type(data["fault"]) is bool, "Invalid fault flag")
                runtime.drive(context, data["run_id"], SimulatedExecutor(data["fault"]))
            elif action == "approve":
                fields(data, {"run_id", "step_id", "operation_key", "revision"})
                runtime.approve(context, data["run_id"], data["step_id"], data["operation_key"], data["revision"])
                runtime.drive(context, data["run_id"], SimulatedExecutor())
            elif action == "cancel":
                fields(data, {"run_id", "revision"})
                runtime.cancel(context, data["run_id"], data["revision"])
            elif action == "reconcile":
                fields(data, {"run_id", "step_id", "operation_key", "revision", "success"})
                require(type(data["success"]) is bool, "Invalid reconciliation outcome")
                runtime.reconcile(context, data["run_id"], data["step_id"], data["operation_key"], data["revision"],
                                  artifact_ref="artifact:simulated-verified" if data["success"] else None)
                if data["success"]:
                    runtime.drive(context, data["run_id"], SimulatedExecutor())
            else:
                raise DuomitError("NOT_FOUND", "Test action is not available")
            return self._state(runtime, context, data["run_id"])
        finally:
            runtime.close()

    @staticmethod
    def _events(runtime, context, run_id):
        return [event for event in runtime.pending_events(context) if event["run_id"] == run_id]

    def _state(self, runtime, context, run_id):
        identifier(run_id)
        return {"run": runtime.inspect(context, run_id), "events": self._events(runtime, context, run_id)}


def create_server(directory: str | Path, *, port=8765):
    application = DemoApplication(directory)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            pass  # Do not write prompts, headers or request bodies to HTTP logs.

        def _host_valid(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def _reply(self, status, content, mime="application/json; charset=utf-8"):
            body = json.dumps(content, ensure_ascii=False).encode("utf-8") if isinstance(content, dict) else content
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", f"default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'nonce-{token}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._host_valid():
                self._reply(403, {"message": "Invalid local host"})
                return
            path = urlsplit(self.path).path
            if path == "/":
                body = files("duomit").joinpath("web", "demo.html").read_text(encoding="utf-8")
                self._reply(200, body.replace("__SESSION_TOKEN__", token).encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/icon.svg":
                self._reply(200, files("duomit").joinpath("web", "icon.svg").read_bytes(), "image/svg+xml")
            elif path == "/health":
                self._reply(200, {"status": "ready", "mode": "local-test"})
            else:
                self._reply(404, {"message": "Not found"})

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if (not self._host_valid() or self.headers.get("Origin", origin) != origin
                    or not secrets.compare_digest(self.headers.get("X-Duomit-Demo", "").encode("utf-8"), token.encode("ascii"))):
                # Drain bounded rejected bodies so Windows can deliver the HTTP error
                # instead of resetting a socket with unread request data.
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if 0 < size <= 32768:
                        self.rfile.read(size)
                except (ValueError, OSError):
                    pass
                self._reply(403, {"message": "Open the local test page to continue"})
                return
            try:
                require(self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json",
                        "JSON request required")
                size = int(self.headers.get("Content-Length", "0"))
                require(0 < size <= 32768, "Request exceeds size limit")
                data = strict_json(self.rfile.read(size).decode("utf-8"))
                require(isinstance(data, dict), "Expected request object")
                path = urlsplit(self.path).path
                require(path.startswith("/api/"), "Invalid test route")
                self._reply(200, application.dispatch(path[5:], data))
            except DuomitError as error:
                self._reply(400, {"code": error.code, "message": error.message})
            except (ValueError, TypeError, KeyError, UnicodeError):
                self._reply(400, {"code": "INVALID_REQUEST", "message": "Invalid test request"})
            except Exception:
                self._reply(500, {"code": "TEST_FAILED", "message": "Test could not complete"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description="duomiT local interactive test environment")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-directory", default="build/demo")
    parser.add_argument("--open", action="store_true", help="Open the local test page in your browser")
    args = parser.parse_args()
    server = create_server(args.data_directory, port=args.port)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"duomiT test environment: {url}", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
