"""Loopback-only, read-only P2 desktop-shell reference projection.

The shell is intentionally a local presentation adapter.  It receives a
trusted OrganizationContext from its caller and never authenticates users,
invokes a model, starts a run, or grants access to an artifact.
"""

from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .foundation import OrganizationContext
from .workspace import LocalWorkspace


class LocalTaskDashboard:
    """Read-only Project/Task dashboard backed by the P2 workspace adapter."""

    def __init__(self, database: str | Path, context: OrganizationContext):
        self._database = database
        self._context = context

    def render(self, project_id: str) -> str:
        workspace = LocalWorkspace(self._database)
        try:
            tasks = workspace.list_tasks(self._context, project_id)
        finally:
            workspace.close()
        return _page(project_id, tasks)


def _page(project_id: str, tasks: list[dict]) -> str:
    safe_project = escape(project_id)
    cards = "".join(_task_card(index, task) for index, task in enumerate(tasks))
    nodes = "".join(_task_node(index, task) for index, task in enumerate(tasks))
    empty = "<p class=\"empty\">此项目尚无任务。先通过本机工作区创建一个 Task。</p>" if not tasks else ""
    return f"""<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>duomiT · 本机任务概览</title><style>
:root {{ color-scheme: dark; --ink:#ecf3fb; --muted:#9fb1c3; --line:#263d54; --panel:#102235; --accent:#55dbc4; --amber:#e9a83d; }}
* {{ box-sizing:border-box }} body {{ margin:0; background:#07131f; color:var(--ink); font:15px/1.5 system-ui,"Microsoft YaHei",sans-serif }}
main {{ max-width:1160px; margin:auto; padding:38px 24px 64px }} header {{ display:flex; justify-content:space-between; gap:24px; border-bottom:1px solid var(--line); padding-bottom:24px }}
h1 {{ margin:0; font-size:30px }} h2 {{ margin:34px 0 14px; font-size:18px }} .tag {{ color:var(--accent); border:1px solid #28796f; border-radius:99px; padding:4px 10px; height:max-content }}
.muted,.empty {{ color:var(--muted) }} .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); gap:14px }} .card {{ padding:16px; background:var(--panel); border:1px solid var(--line); border-radius:12px }}
.card h3 {{ font-size:16px; margin:0 0 10px }} .state {{ color:var(--amber); font-size:12px; text-transform:uppercase }} .goal {{ color:var(--muted); margin:10px 0 0; white-space:pre-wrap }}
.graph {{ width:100%; min-height:180px; background:var(--panel); border:1px solid var(--line); border-radius:12px }} .node {{ fill:#173b55; stroke:#55dbc4; stroke-width:1 }} .node-title {{ fill:#ecf3fb; font-size:13px }} .node-state {{ fill:#e9a83d; font-size:11px }} footer {{ margin-top:32px; color:var(--muted); font-size:13px }}
</style></head><body><main><header><div><h1>duomiT 本机任务概览</h1><p class=\"muted\">Project · {safe_project}</p></div><span class=\"tag\">P2 · 只读投影</span></header>
<section><h2>任务节点图</h2><svg class=\"graph\" viewBox=\"0 0 1080 {max(180, ((len(tasks)+3)//4)*132)}\" role=\"img\" aria-label=\"任务节点图\">{nodes}</svg><p class=\"muted\">当前仅显示任务实体；尚未接受 Proposal，因此不会虚构工作流连线、进度或执行状态。</p></section>
<section><h2>任务</h2>{empty}<div class=\"grid\">{cards}</div></section><footer>此本机壳层不执行任务、不调用模型、不请求或授予资源权限。</footer></main></body></html>"""


def _task_card(index: int, task: dict) -> str:
    return (f"<article class=\"card\"><span class=\"state\">{escape(task['state'])}</span>"
            f"<h3>{escape(task['title'])}</h3><p class=\"goal\">{escape(task['goal'])}</p>"
            f"<p class=\"muted\">Task · {escape(task['id'])[:12]} · rev {task['revision']}</p></article>")


def _task_node(index: int, task: dict) -> str:
    x = 24 + (index % 4) * 264
    y = 24 + (index // 4) * 132
    title = escape(task["title"][:28])
    return (f"<g><rect class=\"node\" x=\"{x}\" y=\"{y}\" width=\"232\" height=\"88\" rx=\"10\"/>"
            f"<text class=\"node-title\" x=\"{x + 16}\" y=\"{y + 35}\">{title}</text>"
            f"<text class=\"node-state\" x=\"{x + 16}\" y=\"{y + 61}\">{escape(task['state'])}</text></g>")


def serve_dashboard(database: str | Path, context: OrganizationContext, project_id: str, *, port: int = 8765) -> ThreadingHTTPServer:
    """Create a loopback server. Caller owns ``serve_forever`` and shutdown."""
    dashboard = LocalTaskDashboard(database, context)

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - required stdlib handler name
            query = parse_qs(urlparse(self.path).query)
            requested_project = query.get("project", [project_id])[0]
            try:
                body = dashboard.render(requested_project).encode("utf-8")
            except Exception:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    return ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
