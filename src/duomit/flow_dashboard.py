"""Read-only FlowDraft graph projection for the loopback reference shell."""

from __future__ import annotations

from html import escape

from .flow_drafts import SqliteFlowDraftStore
from .foundation import OrganizationContext


DOMAIN_COLORS = {"need": "#647dff", "workflow": "#55dbc4", "build": "#e9a83d", "run": "#d66df0"}


class FlowDraftDashboard:
    def __init__(self, store: SqliteFlowDraftStore, context: OrganizationContext):
        self._store = store
        self._context = context

    def render(self, draft_id: str) -> str:
        snapshot = self._store.get_snapshot(self._context, draft_id)
        nodes = {node["id"]: node for node in snapshot.nodes}
        edges = "".join(_edge(nodes[edge["from_node_id"]], nodes[edge["to_node_id"]], edge) for edge in snapshot.edges)
        cards = "".join(_card(node, self._store) for node in snapshot.nodes)
        node_svg = "".join(_node(node, self._store) for node in snapshot.nodes)
        return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>duomiT · 流程草稿</title><style>
:root{{color-scheme:dark;--ink:#ecf3fb;--muted:#9fb1c3;--line:#263d54;--panel:#102235}}*{{box-sizing:border-box}}body{{margin:0;background:#07131f;color:var(--ink);font:15px/1.5 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1160px;margin:auto;padding:36px 24px}}h1{{margin:0;font-size:30px}}h2{{margin:32px 0 12px;font-size:18px}}.muted{{color:var(--muted)}}.tag{{color:#55dbc4;border:1px solid #28796f;border-radius:99px;padding:4px 10px}}header{{display:flex;justify-content:space-between;gap:20px;border-bottom:1px solid var(--line);padding-bottom:22px}}.graph{{width:100%;min-height:260px;background:var(--panel);border:1px solid var(--line);border-radius:12px}}.edge{{stroke:#8396a8;stroke-width:2;fill:none}}.node{{fill:#173b55;stroke-width:2}}.title{{fill:#ecf3fb;font-size:13px}}.domain{{font-size:11px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}.card{{padding:14px;background:var(--panel);border:1px solid var(--line);border-radius:10px}}.card h3{{margin:6px 0;font-size:16px}}footer{{color:var(--muted);margin-top:28px;font-size:13px}}</style></head><body><main>
<header><div><h1>流程草稿</h1><p class=\"muted\">{escape(snapshot.name)} · 语义修订 {snapshot.semantic_revision} · 布局修订 {snapshot.layout_revision}</p></div><span class=\"tag\">P3 · 意图视图</span></header>
<section><h2>受控流程图</h2><svg class=\"graph\" viewBox=\"0 0 1080 640\" role=\"img\" aria-label=\"流程草稿图\">{edges}{node_svg}</svg><p class=\"muted\">边表示用户草稿中的过程意图；它们尚未编译为 WorkUnit，也不表示数据读取权限或运行状态。</p></section>
<section><h2>节点目录</h2><div class=\"grid\">{cards}</div></section><footer>此页面只读，不调用模型、不生成 Proposal、不启动执行。</footer></main></body></html>"""


def _domain(node: dict, store: SqliteFlowDraftStore) -> str:
    return store.get_template(node["template_id"], node["template_version"]).domain


def _node(node: dict, store: SqliteFlowDraftStore) -> str:
    domain = _domain(node, store)
    color = DOMAIN_COLORS[domain]
    x, y = node["x"] + 130, node["y"] + 90
    return (f"<g><rect class=\"node\" stroke=\"{color}\" x=\"{x}\" y=\"{y}\" width=\"210\" height=\"82\" rx=\"10\"/>"
            f"<text class=\"title\" x=\"{x + 14}\" y=\"{y + 32}\">{escape(node['display_name'][:24])}</text>"
            f"<text class=\"domain\" fill=\"{color}\" x=\"{x + 14}\" y=\"{y + 58}\">{domain}</text></g>")


def _edge(source: dict, target: dict, edge: dict) -> str:
    x1, y1 = source["x"] + 340, source["y"] + 131
    x2, y2 = target["x"] + 130, target["y"] + 131
    return f"<path class=\"edge\" d=\"M{x1} {y1} L{x2} {y2}\"><title>{escape(edge['edge_kind'])}</title></path>"


def _card(node: dict, store: SqliteFlowDraftStore) -> str:
    domain = _domain(node, store)
    return (f"<article class=\"card\"><span style=\"color:{DOMAIN_COLORS[domain]}\">{domain}</span>"
            f"<h3>{escape(node['display_name'])}</h3><p class=\"muted\">{escape(node['template_id'])} · {escape(node['template_version'])}</p></article>")
