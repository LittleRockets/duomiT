"""Native FlowDraft editor for controlled process intent, not executable workflow."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from .flow_drafts import FlowDraftSnapshot, SqliteFlowDraftStore
from .flow_compiler import FlowCompilation, compile_flow_draft
from .foundation import OrganizationContext
from .desktop_design import DesktopTokens, configure_desktop_style, status_palette


TEMPLATES = {
    "Need": ("outcome_alignment", "1_0"),
    "Workflow": ("process_plan", "1_0"),
    "Build": ("requirements_refinement", "1_0"),
    "Run": ("operation_monitoring", "1_0"),
}
COLORS = {"need": "#0F766E", "workflow": "#1D4ED8", "build": "#B45309", "run": "#7E22CE"}


class NativeFlowViewModel:
    def __init__(self, database: str | Path, context: OrganizationContext):
        self._database = database; self._context = context

    def create_draft(self, task_id: str, name: str) -> FlowDraftSnapshot:
        store = SqliteFlowDraftStore(self._database)
        try: return store.create(self._context, task_id, name)
        finally: store.close()

    def snapshot(self, draft_id: str) -> FlowDraftSnapshot:
        store = SqliteFlowDraftStore(self._database)
        try: return store.get_snapshot(self._context, draft_id)
        finally: store.close()

    def list_for_task(self, task_id: str) -> tuple[FlowDraftSnapshot, ...]:
        store = SqliteFlowDraftStore(self._database)
        try: return store.list_for_owner(self._context, task_id)
        finally: store.close()

    def add_node(self, draft_id: str, label: str) -> FlowDraftSnapshot:
        template_id, version = TEMPLATES[label]
        store = SqliteFlowDraftStore(self._database)
        try:
            snapshot = store.get_snapshot(self._context, draft_id)
            x, y = 44 + (len(snapshot.nodes) % 3) * 244, 54 + (len(snapshot.nodes) // 3) * 150
            return store.add_node(self._context, draft_id, snapshot.semantic_revision, template_id, version,
                                  label, {}, x=x, y=y)
        finally: store.close()

    def move_node(self, draft_id: str, node_id: str, x: int, y: int) -> FlowDraftSnapshot:
        store = SqliteFlowDraftStore(self._database)
        try:
            snapshot = store.get_snapshot(self._context, draft_id)
            return store.move_node(self._context, draft_id, snapshot.layout_revision, node_id, x, y)
        finally: store.close()

    def auto_layout(self, draft_id: str) -> FlowDraftSnapshot:
        """Place a directed draft in stable columns without requiring drag input."""
        store = SqliteFlowDraftStore(self._database)
        try:
            snapshot = store.get_snapshot(self._context, draft_id)
            nodes = list(snapshot.nodes)
            by_id = {node["id"]: node for node in nodes}
            incoming = {node_id: 0 for node_id in by_id}
            outgoing = {node_id: [] for node_id in by_id}
            for edge in snapshot.edges:
                outgoing[edge["from_node_id"]].append(edge["to_node_id"])
                incoming[edge["to_node_id"]] += 1
            pending = [node["id"] for node in nodes if incoming[node["id"]] == 0]
            layer = {node_id: 0 for node_id in pending}
            ordered: list[str] = []
            while pending:
                current = pending.pop(0); ordered.append(current)
                for target in outgoing[current]:
                    layer[target] = max(layer.get(target, 0), layer[current] + 1)
                    incoming[target] -= 1
                    if incoming[target] == 0:
                        pending.append(target)
            # Validation rejects cycles before compilation, but preserve every node
            # in a deterministic row even while a draft is incomplete.
            ordered.extend(node_id for node_id in by_id if node_id not in ordered)
            rows: dict[int, int] = {}
            current = snapshot
            for node_id in ordered:
                column = layer.get(node_id, 0); row = rows.get(column, 0); rows[column] = row + 1
                x, y = 44 + column * 260, 54 + row * 140
                node = by_id[node_id]
                if (node["x"], node["y"]) != (x, y):
                    current = store.move_node(self._context, draft_id, current.layout_revision, node_id, x, y)
            return current
        finally: store.close()

    def connect(self, draft_id: str, from_node_id: str, to_node_id: str) -> FlowDraftSnapshot:
        store = SqliteFlowDraftStore(self._database)
        try:
            snapshot = store.get_snapshot(self._context, draft_id)
            return store.add_edge(self._context, draft_id, snapshot.semantic_revision,
                                  from_node_id, "out", to_node_id, "in", "control")
        finally: store.close()

    def validate(self, draft_id: str) -> dict:
        store = SqliteFlowDraftStore(self._database)
        try: return store.validate(self._context, draft_id)
        finally: store.close()

    def compile_review(self, draft_id: str) -> FlowCompilation:
        """Produce a pinned, in-memory ProcessIntent review; no Proposal or Run is created."""
        store = SqliteFlowDraftStore(self._database)
        try:
            snapshot = store.get_snapshot(self._context, draft_id)
            return compile_flow_draft(self._context, store, draft_id, snapshot.semantic_digest)
        finally: store.close()

    def node_domain(self, node: dict) -> str:
        store = SqliteFlowDraftStore(self._database)
        try: return store.get_template(node["template_id"], node["template_version"]).domain
        finally: store.close()


class NativeFlowDraftEditor:
    def __init__(self, parent: tk.Misc, model: NativeFlowViewModel, draft_id: str):
        self._model = model; self._draft_id = draft_id; self._snapshot = model.snapshot(draft_id)
        self._drag: tuple[str, int, int] | None = None; self._connect_from: str | None = None
        self._window = tk.Toplevel(parent); self._window.title("duomiT · 流程草稿")
        self._window.geometry("1040x720"); self._window.minsize(820, 580)
        configure_desktop_style(self._window)
        self._build(); self._render()

    def _build(self):
        root = self._window; root.columnconfigure(0, weight=1); root.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(root, style="Surface.TFrame", padding=(20, 12)); toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Label(toolbar, text="流程草稿", style="PageTitle.TLabel").pack(side="left", padx=(0, 20))
        for label in TEMPLATES:
            ttk.Button(toolbar, text=f"添加 {label}", style="Secondary.TButton", command=lambda item=label: self._add(item)).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="连接节点", style="Secondary.TButton", command=self._open_connection_dialog).pack(side="left", padx=(12, 6))
        ttk.Button(toolbar, text="画布连接", style="Quiet.TButton", command=self._start_connect).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="自动布局", style="Secondary.TButton", command=self._auto_layout).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="验证", style="Secondary.TButton", command=self._validate).pack(side="left")
        ttk.Button(toolbar, text="审阅 ProcessIntent", style="Primary.TButton", command=self._review_intent).pack(side="right")
        self._status = tk.StringVar(value="添加受控节点后，可拖拽布局并连接兼容端口。")
        ttk.Label(toolbar, textvariable=self._status, style="Muted.TLabel").pack(side="right", padx=(0, 14))
        self._canvas = tk.Canvas(root, bg=DesktopTokens.canvas, highlightthickness=1, highlightbackground=DesktopTokens.line)
        self._canvas.grid(row=1, column=0, sticky="nsew", padx=20, pady=(16, 20))

    def _render(self):
        canvas = self._canvas; canvas.delete("all")
        for x in range(0, max(canvas.winfo_width(), 880), 24):
            canvas.create_line(x, 0, x, max(canvas.winfo_height(), 520), fill="#EEF1EF")
        for y in range(0, max(canvas.winfo_height(), 520), 24):
            canvas.create_line(0, y, max(canvas.winfo_width(), 880), y, fill="#EEF1EF")
        by_id = {node["id"]: node for node in self._snapshot.nodes}
        for edge in self._snapshot.edges:
            source, target = by_id[edge["from_node_id"]], by_id[edge["to_node_id"]]
            canvas.create_line(source["x"] + 210, source["y"] + 46, target["x"], target["y"] + 46,
                               fill=DesktopTokens.muted, width=1, arrow="last")
        for node in self._snapshot.nodes:
            domain = self._domain(node); color = COLORS[domain]; tag = f"node:{node['id']}"
            x, y = node["x"], node["y"]
            canvas.create_rectangle(x, y, x + 210, y + 92, fill=DesktopTokens.surface, outline=DesktopTokens.line, width=1, tags=(tag,))
            canvas.create_rectangle(x, y, x + 4, y + 92, fill=color, outline=color, tags=(tag,))
            canvas.create_text(x + 16, y + 25, text=node["display_name"], anchor="w", fill=DesktopTokens.ink, font=(DesktopTokens.font, 10, "bold"), tags=(tag,))
            canvas.create_text(x + 16, y + 54, text=domain.upper(), anchor="w", fill=color, font=(DesktopTokens.font, 8, "bold"), tags=(tag,))
            canvas.create_text(x + 16, y + 74, text="受控流程意图", anchor="w", fill=DesktopTokens.muted, font=(DesktopTokens.font, 8), tags=(tag,))
            canvas.tag_bind(tag, "<ButtonPress-1>", lambda event, node_id=node["id"]: self._press(node_id, event))
            canvas.tag_bind(tag, "<B1-Motion>", self._drag_motion)
            canvas.tag_bind(tag, "<ButtonRelease-1>", self._release)

    def _domain(self, node: dict) -> str:
        return self._model.node_domain(node)

    def _add(self, label: str):
        try:
            self._snapshot = self._model.add_node(self._draft_id, label); self._status.set(f"已添加 {label} 节点。"); self._render()
        except Exception as error: messagebox.showerror("Cannot add node", str(error), parent=self._window)

    def _auto_layout(self):
        try:
            self._snapshot = self._model.auto_layout(self._draft_id)
            self._status.set("已按流程依赖自动布局。")
            self._render()
        except Exception as error: messagebox.showerror("Cannot lay out nodes", str(error), parent=self._window)

    def _start_connect(self):
        self._connect_from = None; self._status.set("连接模式：先选择来源节点，再选择目标节点。")

    def _open_connection_dialog(self):
        if len(self._snapshot.nodes) < 2:
            messagebox.showinfo("至少需要两个节点", "先添加两个受控节点，再建立流程连接。", parent=self._window)
            return
        dialog = tk.Toplevel(self._window)
        dialog.title("连接流程节点")
        dialog.transient(self._window); dialog.grab_set(); dialog.resizable(False, False)
        body = ttk.Frame(dialog, style="Surface.TFrame", padding=20); body.pack(fill="both", expand=True)
        ttk.Label(body, text="建立控制连接", style="PageTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text="来源节点必须有输出端口，目标节点必须有输入端口。", style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 16))
        items = tuple(f"{node['display_name']} · {self._domain(node)} · {node['id'][:8]}" for node in self._snapshot.nodes)
        lookup = {label: node["id"] for label, node in zip(items, self._snapshot.nodes)}
        ttk.Label(body, text="来源节点").grid(row=2, column=0, sticky="w", pady=(0, 5))
        source = ttk.Combobox(body, values=items, state="readonly", width=44)
        source.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 14)); source.current(0)
        ttk.Label(body, text="目标节点").grid(row=4, column=0, sticky="w", pady=(0, 5))
        target = ttk.Combobox(body, values=items, state="readonly", width=44)
        target.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 18)); target.current(1)

        def submit():
            if source.get() == target.get():
                messagebox.showerror("无法连接节点", "来源节点和目标节点必须不同。", parent=dialog)
                return
            try:
                self._snapshot = self._model.connect(self._draft_id, lookup[source.get()], lookup[target.get()])
                dialog.destroy(); self._status.set("已添加控制连接。"); self._render()
            except Exception as error:
                messagebox.showerror("无法连接节点", f"{error}\n请选择一个具有兼容端口的来源和目标。", parent=dialog)

        ttk.Button(body, text="取消", style="Secondary.TButton", command=dialog.destroy).grid(row=6, column=0, sticky="w")
        ttk.Button(body, text="建立连接", style="Primary.TButton", command=submit).grid(row=6, column=1, sticky="e")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

    def _press(self, node_id: str, event):
        if self._connect_from is not None:
            if self._connect_from == node_id: return
            try:
                self._snapshot = self._model.connect(self._draft_id, self._connect_from, node_id)
                self._status.set("已添加控制连接。"); self._connect_from = None; self._render()
            except Exception as error:
                self._connect_from = None; messagebox.showerror("Cannot connect nodes", str(error), parent=self._window)
            return
        if self._status.get().startswith("连接模式"):
            self._connect_from = node_id; self._status.set("请选择目标节点。"); return
        node = next(item for item in self._snapshot.nodes if item["id"] == node_id)
        self._drag = (node_id, event.x - node["x"], event.y - node["y"])

    def _drag_motion(self, event):
        if self._drag is None: return
        node_id, offset_x, offset_y = self._drag; node = next(item for item in self._snapshot.nodes if item["id"] == node_id)
        dx, dy = event.x - offset_x - node["x"], event.y - offset_y - node["y"]
        self._canvas.move(f"node:{node_id}", dx, dy)

    def _release(self, event):
        if self._drag is None: return
        node_id, offset_x, offset_y = self._drag; self._drag = None
        try:
            self._snapshot = self._model.move_node(self._draft_id, node_id, event.x - offset_x, event.y - offset_y)
            self._status.set("布局已保存。"); self._render()
        except Exception as error: messagebox.showerror("Cannot move node", str(error), parent=self._window); self._render()

    def _validate(self):
        report = self._model.validate(self._draft_id)
        self._status.set(f"草稿验证：{report['status']}。")
        if report["status"] != "valid":
            messagebox.showwarning("FlowDraft validation", "Add or repair required controlled connections before compiling.", parent=self._window)

    def _review_intent(self):
        try:
            compilation = self._model.compile_review(self._draft_id)
        except Exception as error:
            messagebox.showerror("Cannot compile review", str(error), parent=self._window)
            return
        dialog = tk.Toplevel(self._window)
        dialog.title("ProcessIntent review · non-executable")
        dialog.transient(self._window); dialog.geometry("760x470")
        body = ttk.Frame(dialog, padding=18); body.pack(fill="both", expand=True)
        ttk.Label(body, text="Pinned ProcessIntent preview", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(body, text=f"Semantic revision {compilation.semantic_revision} · {compilation.semantic_digest[:16]}…\nThis preview creates no Proposal, WorkUnit, permission, model call, or Run.").pack(anchor="w", pady=(5, 12))
        table = ttk.Treeview(body, columns=("domain", "template", "intent"), show="headings", height=10)
        for name, title, width in (("domain", "Domain", 110), ("template", "Template", 180), ("intent", "Pinned intent", 370)):
            table.heading(name, text=title); table.column(name, width=width, minwidth=90)
        for intent in compilation.intents:
            table.insert("", "end", values=(intent.domain, f"{intent.template_id}@{intent.template_version}", intent.intent_id))
        table.pack(fill="both", expand=True)
        ttk.Label(body, text=f"Compilation digest: {compilation.digest}", wraplength=700).pack(anchor="w", pady=(12, 0))
