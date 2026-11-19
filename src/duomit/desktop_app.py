"""Native tkinter Desktop shell for the local-first duomiT reference app.

This is a native process, not an HTTP service.  It reads a trusted local
workspace projection and does not run tasks, invoke models, or issue grants.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from .foundation import OrganizationContext
from .desktop_flow_editor import NativeFlowDraftEditor, NativeFlowViewModel
from .desktop_design import DesktopTokens, configure_desktop_style, status_palette
from .task_revisions import AcceptanceCriterion, SqliteTaskRevisionStore, TaskRevision
from .need_records import NeedCandidate, NeedOutcome, NeedUnknown, SqliteNeedStore
from .workflow_specs import SqliteProcessSpecStore
from .workspace import LocalWorkspace


@dataclass(frozen=True)
class TaskNode:
    task_id: str
    title: str
    state: str
    goal: str
    revision: int
    planning_revision: TaskRevision | None
    x: int
    y: int


class NativeTaskViewModel:
    """Native-shell read model; persistence and authorization stay in Workspace."""

    def __init__(self, database: str | Path, context: OrganizationContext, project_id: str):
        self._database = database
        self._context = context
        self._project_id = project_id

    def load_tasks(self) -> tuple[TaskNode, ...]:
        workspace = LocalWorkspace(self._database)
        try:
            tasks = workspace.list_tasks(self._context, self._project_id)
        finally:
            workspace.close()
        planning = SqliteTaskRevisionStore(self._database)
        try:
            return tuple(TaskNode(task["id"], task["title"], task["state"], task["goal"], task["revision"],
                                  planning.get_current_for_workspace_task(self._context, task["id"]),
                                  44 + (index % 3) * 244, 54 + (index // 3) * 150)
                         for index, task in enumerate(tasks))
        finally:
            planning.close()

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def database(self) -> str | Path:
        return self._database

    @property
    def context(self) -> OrganizationContext:
        return self._context

    def create_project(self, name: str) -> str:
        workspace = LocalWorkspace(self._database)
        try:
            self._project_id = workspace.create_project(self._context, name)
        finally:
            workspace.close()
        return self._project_id

    def create_task(self, title: str, goal: str, deliverable: str, acceptance: tuple[str, ...],
                    constraints: tuple[str, ...] = ()) -> str:
        criteria = tuple(AcceptanceCriterion(f"criterion{index + 1}", statement)
                         for index, statement in enumerate(acceptance) if statement.strip())
        normalized_constraints = tuple(item.strip() for item in constraints if item.strip())
        planning = SqliteTaskRevisionStore(self._database)
        try:
            return planning.create_workspace_task(self._context, self._project_id, title, goal, deliverable,
                                                  criteria, normalized_constraints)
        finally:
            planning.close()

    def revise_task(self, workspace_task_id: str, expected_workspace_revision: int, title: str, goal: str,
                    deliverable: str, acceptance: tuple[str, ...], constraints: tuple[str, ...] = ()) -> TaskRevision:
        criteria = tuple(AcceptanceCriterion(f"criterion{index + 1}", statement)
                         for index, statement in enumerate(acceptance) if statement.strip())
        normalized_constraints = tuple(item.strip() for item in constraints if item.strip())
        planning = SqliteTaskRevisionStore(self._database)
        try:
            return planning.revise_workspace_task(self._context, workspace_task_id, expected_workspace_revision,
                                                  title, goal, deliverable, criteria, normalized_constraints)
        finally:
            planning.close()

    def create_manual_need(self, workspace_task_id: str, outcome: str, questions: tuple[str, ...] = ()):
        planning = SqliteTaskRevisionStore(self._database)
        needs = SqliteNeedStore(self._database)
        try:
            task = planning.get_current_for_workspace_task(self._context, workspace_task_id)
            if task is None:
                raise ValueError("This Task has no versioned planning input.")
            criteria = tuple(item.criterion_id for item in task.acceptance if item.blocking)
            candidate = NeedCandidate((NeedOutcome("outcome1", outcome, criteria),),
                                      tuple(NeedUnknown(f"question{index + 1}", item, True)
                                            for index, item in enumerate(questions) if item.strip()))
            return needs.create(self._context, task, candidate)
        finally:
            needs.close(); planning.close()

    def derive_process(self, workspace_task_id: str):
        planning = SqliteTaskRevisionStore(self._database)
        needs = SqliteNeedStore(self._database)
        processes = SqliteProcessSpecStore(self._database)
        try:
            task = planning.get_current_for_workspace_task(self._context, workspace_task_id)
            if task is None: raise ValueError("This Task has no versioned planning input.")
            records = needs.list_for_task_revision(self._context, task.task_revision_id)
            if not records: raise ValueError("先定义 Need，再生成 Workflow 过程规格。")
            return processes.derive(self._context, records[-1])
        finally:
            processes.close(); needs.close(); planning.close()


class DuomitDesktopApp:
    """Read-only native Project/Task shell. All mutations remain explicit ports."""

    def __init__(self, view_model: NativeTaskViewModel):
        self._model = view_model
        self._tasks: tuple[TaskNode, ...] = ()
        self._selected_task_id: str | None = None
        self._feedback_after_id: str | None = None
        self._root = tk.Tk()
        self._root.title("duomiT · 本机工作台")
        self._root.geometry("1280x800")
        self._root.minsize(1024, 680)
        self._configure_style()
        self._build()
        self.refresh()

    def _configure_style(self):
        configure_desktop_style(self._root)

    def _build(self):
        root = self._root
        root.columnconfigure(1, weight=1); root.rowconfigure(0, weight=1)
        rail = ttk.Frame(root, style="Rail.TFrame", padding=(20, 24))
        rail.grid(row=0, column=0, sticky="nsw")
        ttk.Label(rail, text="duomiT", style="RailBrand.TLabel").pack(anchor="w")
        ttk.Label(rail, text="LOCAL ORGANIZATION WORKSPACE", style="RailMuted.TLabel").pack(anchor="w", pady=(2, 34))
        ttk.Label(rail, text="工作台", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        self._nav_task = ttk.Button(rail, text="任务", style="Primary.TButton", command=self.refresh)
        self._nav_task.pack(fill="x", pady=(0, 8))
        ttk.Button(rail, text="流程草稿", style="Secondary.TButton", command=self._open_flow_list).pack(fill="x", pady=(0, 8))
        ttk.Separator(rail).pack(fill="x", pady=20)
        ttk.Label(rail, text="本机边界", style="Section.TLabel").pack(anchor="w")
        ttk.Label(rail, text="规划对象可审阅\n不执行任务\n不调用模型", style="RailMuted.TLabel", justify="left").pack(anchor="w", pady=(8, 0))
        ttk.Button(rail, text="新建项目", style="Quiet.TButton", command=self._open_project_dialog).pack(anchor="w", pady=(28, 0))

        body = ttk.Frame(root, style="App.TFrame")
        body.grid(row=0, column=1, sticky="nsew")
        body.columnconfigure(0, weight=1); body.rowconfigure(1, weight=1)
        header = ttk.Frame(body, style="Surface.TFrame", padding=(28, 16))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="任务工作台", style="PageTitle.TLabel").pack(anchor="w")
        self._subtitle = tk.StringVar()
        ttk.Label(header, textvariable=self._subtitle, style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        self._feedback = tk.StringVar(value="")
        self._feedback_label = ttk.Label(header, textvariable=self._feedback, style="Muted.TLabel", wraplength=520)
        self._feedback_label.pack(anchor="w", pady=(4, 0))
        ttk.Button(header, text="新建版本化任务", style="Primary.TButton", command=self._open_task_dialog).pack(anchor="e", side="right")
        ttk.Button(header, text="刷新", style="Secondary.TButton", command=self.refresh).pack(anchor="e", side="right", padx=(0, 8))

        work = ttk.Frame(body, style="App.TFrame", padding=(24, 20, 24, 24))
        work.grid(row=1, column=0, sticky="nsew")
        work.columnconfigure(1, weight=1); work.rowconfigure(0, weight=1)
        left = ttk.Frame(work, style="Surface.TFrame", padding=16)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 16))
        ttk.Label(left, text="任务", style="Section.TLabel").pack(anchor="w")
        ttk.Label(left, text="选择一个任务以检查其规划状态。", style="Muted.TLabel", wraplength=270).pack(anchor="w", pady=(4, 12))
        self._tree = ttk.Treeview(left, columns=("state",), show="tree headings", height=18)
        self._tree.heading("#0", text="Task"); self._tree.heading("state", text="State")
        self._tree.column("#0", width=230, minwidth=160); self._tree.column("state", width=76, minwidth=65)
        self._tree.pack(fill="both", expand=True); self._tree.bind("<<TreeviewSelect>>", self._select_task)
        ttk.Label(left, text="此工作台不会执行任务或调用模型。", style="Muted.TLabel", wraplength=270).pack(anchor="w", pady=(14, 0))

        right = ttk.Frame(work, style="Surface.TFrame", padding=16)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1); right.columnconfigure(0, weight=1)
        toolbar = ttk.Frame(right, style="Surface.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(toolbar, text="任务图", style="Section.TLabel").pack(side="left")
        ttk.Button(toolbar, text="编辑任务", style="Secondary.TButton", command=self._open_task_edit_dialog).pack(side="right")
        ttk.Button(toolbar, text="生成 Workflow", style="Secondary.TButton", command=self._derive_workflow).pack(side="right", padx=(0, 8))
        ttk.Button(toolbar, text="定义 Need", style="Secondary.TButton", command=self._open_need_dialog).pack(side="right", padx=(0, 8))
        ttk.Button(toolbar, text="查看流程", style="Secondary.TButton", command=self._open_flow_list).pack(side="right", padx=(0, 8))
        ttk.Button(toolbar, text="新建流程", style="Quiet.TButton", command=self._open_flow_editor).pack(side="right", padx=(0, 8))
        self._canvas = tk.Canvas(right, bg=DesktopTokens.canvas, highlightthickness=1, highlightbackground=DesktopTokens.line)
        self._canvas.grid(row=1, column=0, sticky="nsew")
        self._detail = tk.StringVar(value="选择任务以检查其版本化规划与流程草稿。")
        detail = ttk.Frame(right, style="Subtle.TFrame", padding=12)
        detail.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(detail, text="检查器", style="Section.TLabel").pack(anchor="w")
        ttk.Label(detail, textvariable=self._detail, style="Muted.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(5, 0))

    def _announce(self, message: str):
        self._feedback.set(message)
        if self._feedback_after_id is not None:
            self._root.after_cancel(self._feedback_after_id)
        self._feedback_after_id = self._root.after(5000, lambda: self._feedback.set(""))

    def refresh(self):
        previous = self._selected_task_id
        self._tasks = self._model.load_tasks()
        self._selected_task_id = None
        self._subtitle.set(f"Project {self._model.project_id[:12]} · native local workspace")
        self._tree.delete(*self._tree.get_children())
        for task in self._tasks:
            self._tree.insert("", "end", iid=task.task_id, text=task.title, values=(task.state,))
        self._draw_graph()
        if previous and any(item.task_id == previous for item in self._tasks):
            self._show_task(previous)
        elif not self._tasks:
            self._detail.set("This Project has no tasks yet. Create a versioned task through the local planning boundary.")

    def _draw_graph(self):
        canvas = self._canvas; canvas.delete("all")
        if not self._tasks:
            canvas.create_text(350, 180, text="尚无任务", fill=DesktopTokens.muted, font=(DesktopTokens.font, 15, "bold"), tags=("create-task",))
            canvas.create_text(350, 210, text="新建一个版本化任务，定义目标、交付物与验收标准。", fill=DesktopTokens.muted, font=(DesktopTokens.font, 10), tags=("create-task",))
            canvas.create_rectangle(276, 235, 424, 269, fill=DesktopTokens.primary, outline=DesktopTokens.primary, tags=("create-task",))
            canvas.create_text(350, 252, text="新建版本化任务", fill="#FFFFFF", font=(DesktopTokens.font, 9, "bold"), tags=("create-task",))
            canvas.tag_bind("create-task", "<Button-1>", lambda _event: self._open_task_dialog())
            return
        width = max(canvas.winfo_width(), 700)
        for x in range(0, width, 24):
            canvas.create_line(x, 0, x, max(canvas.winfo_height(), 440), fill="#EEF1EF")
        for y in range(0, max(canvas.winfo_height(), 440), 24):
            canvas.create_line(0, y, width, y, fill="#EEF1EF")
        for task in self._tasks:
            x, y = task.x, task.y
            color, background = status_palette("valid" if task.planning_revision else "attention")
            canvas.create_rectangle(x, y, x + 210, y + 92, fill=DesktopTokens.surface, outline=DesktopTokens.line, width=1, tags=(task.task_id,))
            canvas.create_rectangle(x, y, x + 4, y + 92, fill=color, outline=color, tags=(task.task_id,))
            canvas.create_text(x + 16, y + 24, text=task.title[:26], fill=DesktopTokens.ink, anchor="w", font=(DesktopTokens.font, 10, "bold"), tags=(task.task_id,))
            canvas.create_rectangle(x + 16, y + 42, x + 97, y + 62, fill=background, outline=background, tags=(task.task_id,))
            canvas.create_text(x + 23, y + 52, text="已规划" if task.planning_revision else "待规划", fill=color, anchor="w", font=(DesktopTokens.font, 8, "bold"), tags=(task.task_id,))
            revision = f"plan v{task.planning_revision.number}" if task.planning_revision else "planning required"
            canvas.create_text(x + 16, y + 76, text=revision, fill=DesktopTokens.muted, anchor="w", font=(DesktopTokens.font, 8), tags=(task.task_id,))
            canvas.tag_bind(task.task_id, "<Button-1>", lambda event, task_id=task.task_id: self._show_task(task_id))

    def _select_task(self, _event):
        selected = self._tree.selection()
        if selected: self._show_task(selected[0])

    def _show_task(self, task_id: str):
        task = next((item for item in self._tasks if item.task_id == task_id), None)
        if task is None: return
        if self._selected_task_id != task_id:
            self._selected_task_id = task_id
            if task_id not in self._tree.selection():
                self._tree.selection_set(task_id)
            self._tree.focus(task_id)
        if task.planning_revision is None:
            planning = "Planning input has not been created for this legacy local task."
        else:
            revision = task.planning_revision
            accepted = "; ".join(item.statement for item in revision.acceptance)
            constraints = "; ".join(revision.constraints) or "None"
            planning = f"Deliverable: {revision.deliverable}\nBlocking acceptance: {accepted}\nConstraints: {constraints}"
        self._detail.set(f"{task.title} · {task.state} · workspace revision {task.revision}\n{task.goal}\n{planning}\nNo Proposal, WorkUnit, Run, or external operation exists for this draft.")

    def _open_project_dialog(self):
        dialog = tk.Toplevel(self._root)
        dialog.title("新建项目")
        dialog.transient(self._root); dialog.grab_set(); dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=20); body.pack(fill="both", expand=True)
        ttk.Label(body, text="项目名称").grid(row=0, column=0, sticky="w")
        name = ttk.Entry(body, width=42); name.grid(row=1, column=0, pady=(6, 16)); name.focus_set()

        def submit():
            try:
                self._model.create_project(name.get())
                dialog.destroy(); self.refresh(); self._announce("项目已创建。现在可新建版本化任务。")
            except Exception as error:
                messagebox.showerror("Cannot create Project", str(error), parent=dialog)

        ttk.Button(body, text="创建项目", style="Primary.TButton", command=submit).grid(row=2, column=0, sticky="e")
        dialog.bind("<Return>", lambda _event: submit())

    def _open_task_dialog(self):
        dialog = tk.Toplevel(self._root)
        dialog.title("新建版本化任务")
        dialog.transient(self._root); dialog.grab_set(); dialog.geometry("580x560")
        body = ttk.Frame(dialog, padding=20); body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1); body.rowconfigure(3, weight=1); body.rowconfigure(7, weight=1)
        ttk.Label(body, text="任务名称 *").grid(row=0, column=0, sticky="w")
        title = ttk.Entry(body); title.grid(row=1, column=0, sticky="ew", pady=(6, 14)); title.focus_set()
        ttk.Label(body, text="目标 *").grid(row=2, column=0, sticky="w")
        goal = tk.Text(body, height=5, bg=DesktopTokens.surface, fg=DesktopTokens.ink, insertbackground=DesktopTokens.ink, highlightthickness=1, highlightbackground=DesktopTokens.line, relief="flat", padx=8, pady=8)
        goal.grid(row=3, column=0, sticky="nsew", pady=(6, 12))
        ttk.Label(body, text="交付物 *").grid(row=4, column=0, sticky="w")
        deliverable = ttk.Entry(body); deliverable.grid(row=5, column=0, sticky="ew", pady=(6, 12))
        ttk.Label(body, text="阻塞验收标准 *（每行一项）").grid(row=6, column=0, sticky="w")
        acceptance = tk.Text(body, height=5, bg=DesktopTokens.surface, fg=DesktopTokens.ink, insertbackground=DesktopTokens.ink, highlightthickness=1, highlightbackground=DesktopTokens.line, relief="flat", padx=8, pady=8)
        acceptance.grid(row=7, column=0, sticky="nsew", pady=(6, 12))
        ttk.Label(body, text="约束（可选，每行一项）").grid(row=8, column=0, sticky="w")
        constraints = ttk.Entry(body); constraints.grid(row=9, column=0, sticky="ew", pady=(6, 16))

        def submit():
            try:
                task_id = self._model.create_task(title.get(), goal.get("1.0", "end-1c"), deliverable.get(),
                                                   tuple(acceptance.get("1.0", "end-1c").splitlines()),
                                                   tuple(constraints.get().splitlines()))
                dialog.destroy(); self.refresh(); self._show_task(task_id)
                self._announce("任务已创建，并已固定为规划版本 1。")
            except Exception as error:
                messagebox.showerror("Cannot create Task", str(error), parent=dialog)

        ttk.Button(body, text="创建版本化任务", style="Primary.TButton", command=submit).grid(row=10, column=0, sticky="e")

    def _open_flow_editor(self):
        selected = self._tree.selection()
        if not selected:
            messagebox.showinfo("Select a Task", "Select a local Task before creating its FlowDraft.", parent=self._root)
            return
        task = next((item for item in self._tasks if item.task_id == selected[0]), None)
        if task is None: return
        try:
            flow = NativeFlowViewModel(self._model.database, self._model.context)
            draft = flow.create_draft(task.task_id, f"{task.title} flow")
            NativeFlowDraftEditor(self._root, flow, draft.flow_draft_id)
        except Exception as error:
            messagebox.showerror("Cannot create FlowDraft", str(error), parent=self._root)

    def _open_flow_list(self):
        selected = self._tree.selection()
        if not selected:
            messagebox.showinfo("Select a Task", "Select a local Task before viewing its FlowDrafts.", parent=self._root)
            return
        flow = NativeFlowViewModel(self._model.database, self._model.context)
        try:
            drafts = flow.list_for_task(selected[0])
        except Exception as error:
            messagebox.showerror("Cannot list FlowDrafts", str(error), parent=self._root)
            return
        dialog = tk.Toplevel(self._root)
        dialog.title("Task FlowDrafts")
        dialog.transient(self._root); dialog.geometry("640x390")
        body = ttk.Frame(dialog, padding=18); body.pack(fill="both", expand=True)
        ttk.Label(body, text="Controlled process-intent drafts", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        table = ttk.Treeview(body, columns=("name", "semantic", "layout", "nodes"), show="headings", height=9)
        for key, label, width in (("name", "Name", 220), ("semantic", "Semantic rev.", 110), ("layout", "Layout rev.", 100), ("nodes", "Nodes", 70)):
            table.heading(key, text=label); table.column(key, width=width, minwidth=65)
        for draft in drafts:
            table.insert("", "end", iid=draft.flow_draft_id,
                         values=(draft.name, draft.semantic_revision, draft.layout_revision, len(draft.nodes)))
        table.pack(fill="both", expand=True, pady=(10, 12))
        if not drafts:
            ttk.Label(body, text="No FlowDraft exists for this Task yet. Create one explicitly with New Flow.").pack(anchor="w")

        def open_selected():
            chosen = table.selection()
            if chosen:
                NativeFlowDraftEditor(self._root, flow, chosen[0])

        ttk.Button(body, text="Open selected FlowDraft", command=open_selected).pack(anchor="e")

    def _open_task_edit_dialog(self):
        selected = self._tree.selection()
        if not selected:
            messagebox.showinfo("Select a Task", "Select a versioned local Task before editing it.", parent=self._root)
            return
        task = next((item for item in self._tasks if item.task_id == selected[0]), None)
        if task is None or task.planning_revision is None:
            messagebox.showinfo("Planning required", "Only Tasks with a versioned planning input can be revised.", parent=self._root)
            return
        revision = task.planning_revision
        dialog = tk.Toplevel(self._root)
        dialog.title("修订任务规划")
        dialog.transient(self._root); dialog.grab_set(); dialog.geometry("580x560")
        body = ttk.Frame(dialog, padding=20); body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1); body.rowconfigure(3, weight=1); body.rowconfigure(7, weight=1)
        ttk.Label(body, text=f"将创建规划版本 {revision.number + 1}；版本 {revision.number} 保持不可变。", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 10))
        ttk.Label(body, text="任务名称 *").grid(row=1, column=0, sticky="w")
        title = ttk.Entry(body); title.grid(row=2, column=0, sticky="ew", pady=(6, 12)); title.insert(0, task.title)
        ttk.Label(body, text="目标 *").grid(row=3, column=0, sticky="w")
        goal = tk.Text(body, height=5, bg=DesktopTokens.surface, fg=DesktopTokens.ink, insertbackground=DesktopTokens.ink, highlightthickness=1, highlightbackground=DesktopTokens.line, relief="flat", padx=8, pady=8)
        goal.grid(row=4, column=0, sticky="nsew", pady=(6, 12)); goal.insert("1.0", task.goal)
        ttk.Label(body, text="交付物 *").grid(row=5, column=0, sticky="w")
        deliverable = ttk.Entry(body); deliverable.grid(row=6, column=0, sticky="ew", pady=(6, 12)); deliverable.insert(0, revision.deliverable)
        ttk.Label(body, text="阻塞验收标准 *（每行一项）").grid(row=7, column=0, sticky="w")
        acceptance = tk.Text(body, height=5, bg=DesktopTokens.surface, fg=DesktopTokens.ink, insertbackground=DesktopTokens.ink, highlightthickness=1, highlightbackground=DesktopTokens.line, relief="flat", padx=8, pady=8)
        acceptance.grid(row=8, column=0, sticky="nsew", pady=(6, 12)); acceptance.insert("1.0", "\n".join(item.statement for item in revision.acceptance))
        ttk.Label(body, text="约束（可选，每行一项）").grid(row=9, column=0, sticky="w")
        constraints = ttk.Entry(body); constraints.grid(row=10, column=0, sticky="ew", pady=(6, 16)); constraints.insert(0, "\n".join(revision.constraints))

        def submit():
            try:
                self._model.revise_task(task.task_id, task.revision, title.get(), goal.get("1.0", "end-1c"),
                                        deliverable.get(), tuple(acceptance.get("1.0", "end-1c").splitlines()),
                                        tuple(constraints.get().splitlines()))
                dialog.destroy(); self.refresh(); self._show_task(task.task_id)
                self._announce(f"已创建规划版本 {revision.number + 1}；此前版本保持可追溯。")
            except Exception as error:
                messagebox.showerror("Cannot revise Task", str(error), parent=dialog)

        ttk.Button(body, text="创建新版本", style="Primary.TButton", command=submit).grid(row=11, column=0, sticky="e")

    def _open_need_dialog(self):
        selected = self._tree.selection()
        task = next((item for item in self._tasks if selected and item.task_id == selected[0]), None)
        if task is None or task.planning_revision is None:
            messagebox.showinfo("需要版本化任务", "请先选择一个已完成规划输入的任务。", parent=self._root)
            return
        dialog = tk.Toplevel(self._root)
        dialog.title("定义 Need")
        dialog.transient(self._root); dialog.grab_set(); dialog.geometry("580x470")
        body = ttk.Frame(dialog, style="Surface.TFrame", padding=20); body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1); body.rowconfigure(3, weight=1)
        acceptance = "\n".join(f"• {item.statement}" for item in task.planning_revision.acceptance if item.blocking)
        ttk.Label(body, text="定义可审阅的 Need", style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text=f"该记录绑定 TaskRevision {task.planning_revision.number}，并覆盖以下阻塞验收：\n{acceptance}", style="Muted.TLabel", justify="left", wraplength=520).grid(row=1, column=0, sticky="w", pady=(6, 16))
        ttk.Label(body, text="预期结果 *").grid(row=2, column=0, sticky="w")
        outcome = tk.Text(body, height=5, bg=DesktopTokens.surface, fg=DesktopTokens.ink, insertbackground=DesktopTokens.ink, highlightthickness=1, highlightbackground=DesktopTokens.line, relief="flat", padx=8, pady=8)
        outcome.grid(row=3, column=0, sticky="nsew", pady=(6, 14))
        ttk.Label(body, text="待澄清问题（可选，每行一项；填写后会阻止下游规划）").grid(row=4, column=0, sticky="w")
        questions = tk.Text(body, height=3, bg=DesktopTokens.surface, fg=DesktopTokens.ink, insertbackground=DesktopTokens.ink, highlightthickness=1, highlightbackground=DesktopTokens.line, relief="flat", padx=8, pady=8)
        questions.grid(row=5, column=0, sticky="ew", pady=(6, 18))

        def submit():
            try:
                record = self._model.create_manual_need(task.task_id, outcome.get("1.0", "end-1c"), tuple(questions.get("1.0", "end-1c").splitlines()))
                dialog.destroy(); self._announce(f"Need 已保存：{record.completeness}。")
            except Exception as error:
                messagebox.showerror("无法保存 Need", str(error), parent=dialog)

        ttk.Button(body, text="取消", style="Secondary.TButton", command=dialog.destroy).grid(row=6, column=0, sticky="w")
        ttk.Button(body, text="保存 Need", style="Primary.TButton", command=submit).grid(row=6, column=0, sticky="e")

    def _derive_workflow(self):
        selected = self._tree.selection()
        task = next((item for item in self._tasks if selected and item.task_id == selected[0]), None)
        if task is None:
            messagebox.showinfo("选择任务", "请选择任务后再生成 Workflow 过程规格。", parent=self._root); return
        try:
            process = self._model.derive_process(task.task_id)
        except Exception as error:
            messagebox.showwarning("暂不能生成 Workflow", f"{error}\n请完成 Need 的待澄清项后重试。", parent=self._root); return
        messagebox.showinfo("Workflow 已生成", f"已生成可审阅的 ProcessSpecification。\n阶段：{len(process.stages)}\n质量门：{len(process.quality_gates)}\n不会创建运行或外部操作。", parent=self._root)
        self._announce("Workflow 过程规格已生成，可继续审阅 Build/Run 输入。")

    def run(self):
        self._root.mainloop()


def launch_native_desktop(database: str | Path, context: OrganizationContext, project_id: str) -> None:
    DuomitDesktopApp(NativeTaskViewModel(database, context, project_id)).run()
