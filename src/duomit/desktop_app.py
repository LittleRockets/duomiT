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
from .task_revisions import AcceptanceCriterion, SqliteTaskRevisionStore, TaskRevision
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


class DuomitDesktopApp:
    """Read-only native Project/Task shell. All mutations remain explicit ports."""

    def __init__(self, view_model: NativeTaskViewModel):
        self._model = view_model
        self._tasks: tuple[TaskNode, ...] = ()
        self._selected_task_id: str | None = None
        self._root = tk.Tk()
        self._root.title("duomiT Desktop · Local-first Builder")
        self._root.geometry("1180x760")
        self._root.minsize(920, 620)
        self._configure_style()
        self._build()
        self.refresh()

    def _configure_style(self):
        self._root.configure(bg="#07131f")
        style = ttk.Style(self._root)
        style.theme_use("clam")
        style.configure("App.TFrame", background="#07131f")
        style.configure("Panel.TFrame", background="#102235")
        style.configure("Title.TLabel", background="#07131f", foreground="#edf4fc", font=("Segoe UI", 22, "bold"))
        style.configure("Muted.TLabel", background="#07131f", foreground="#9fb1c3", font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background="#102235", foreground="#edf4fc", font=("Segoe UI", 11, "bold"))
        style.configure("Treeview", background="#102235", foreground="#edf4fc", fieldbackground="#102235", rowheight=31)
        style.configure("Treeview.Heading", background="#18334b", foreground="#cfe0ee", relief="flat")
        style.map("Treeview", background=[("selected", "#1d566d")])

    def _build(self):
        root = self._root
        root.columnconfigure(1, weight=1); root.rowconfigure(1, weight=1)
        header = ttk.Frame(root, style="App.TFrame", padding=(26, 22, 26, 15))
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(header, text="duomiT Desktop", style="Title.TLabel").pack(anchor="w")
        self._subtitle = tk.StringVar()
        ttk.Label(header, textvariable=self._subtitle, style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Button(header, text="Refresh", command=self.refresh).pack(anchor="e", side="right")
        ttk.Button(header, text="New Flow", command=self._open_flow_editor).pack(anchor="e", side="right", padx=(0, 8))
        ttk.Button(header, text="New Task", command=self._open_task_dialog).pack(anchor="e", side="right", padx=(0, 8))
        ttk.Button(header, text="Edit Task", command=self._open_task_edit_dialog).pack(anchor="e", side="right", padx=(0, 8))
        ttk.Button(header, text="New Project", command=self._open_project_dialog).pack(anchor="e", side="right", padx=(0, 8))

        left = ttk.Frame(root, style="Panel.TFrame", padding=16)
        left.grid(row=1, column=0, sticky="nsew", padx=(26, 12), pady=(0, 26))
        ttk.Label(left, text="Tasks", style="Panel.TLabel").pack(anchor="w", pady=(0, 10))
        self._tree = ttk.Treeview(left, columns=("state",), show="tree headings", height=18)
        self._tree.heading("#0", text="Task"); self._tree.heading("state", text="State")
        self._tree.column("#0", width=230, minwidth=160); self._tree.column("state", width=76, minwidth=65)
        self._tree.pack(fill="both", expand=True); self._tree.bind("<<TreeviewSelect>>", self._select_task)
        ttk.Label(left, text="This shell does not execute tasks or call models.", style="Muted.TLabel", wraplength=285).pack(anchor="w", pady=(14, 0))

        right = ttk.Frame(root, style="Panel.TFrame", padding=16)
        right.grid(row=1, column=1, sticky="nsew", padx=(0, 26), pady=(0, 26))
        right.rowconfigure(1, weight=1); right.columnconfigure(0, weight=1)
        ttk.Label(right, text="Task graph", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 10))
        self._canvas = tk.Canvas(right, bg="#0b1b2a", highlightthickness=1, highlightbackground="#2a465d")
        self._canvas.grid(row=1, column=0, sticky="nsew")
        self._detail = tk.StringVar(value="Select a task to inspect its local draft projection.")
        ttk.Label(right, textvariable=self._detail, style="Muted.TLabel", wraplength=700).grid(row=2, column=0, sticky="ew", pady=(12, 0))

    def refresh(self):
        self._tasks = self._model.load_tasks()
        self._selected_task_id = None
        self._subtitle.set(f"Project {self._model.project_id[:12]} · native local workspace")
        self._tree.delete(*self._tree.get_children())
        for task in self._tasks:
            self._tree.insert("", "end", iid=task.task_id, text=task.title, values=(task.state,))
        self._draw_graph()
        if not self._tasks:
            self._detail.set("This Project has no tasks yet. Create a versioned task through the local planning boundary.")

    def _draw_graph(self):
        canvas = self._canvas; canvas.delete("all")
        if not self._tasks:
            canvas.create_text(320, 170, text="No Task nodes", fill="#9fb1c3", font=("Segoe UI", 14))
            return
        for task in self._tasks:
            x, y = task.x, task.y
            canvas.create_rectangle(x, y, x + 210, y + 92, fill="#173b55", outline="#55dbc4", width=2, tags=(task.task_id,))
            canvas.create_text(x + 14, y + 24, text=task.title[:26], fill="#edf4fc", anchor="w", font=("Segoe UI", 11, "bold"), tags=(task.task_id,))
            canvas.create_text(x + 14, y + 53, text=task.state.upper(), fill="#e9a83d", anchor="w", font=("Segoe UI", 9), tags=(task.task_id,))
            revision = f"plan v{task.planning_revision.number}" if task.planning_revision else "planning required"
            canvas.create_text(x + 14, y + 74, text=revision, fill="#9fb1c3", anchor="w", font=("Segoe UI", 9), tags=(task.task_id,))
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
        dialog.title("Create Project")
        dialog.transient(self._root); dialog.grab_set(); dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=20); body.pack(fill="both", expand=True)
        ttk.Label(body, text="Project name").grid(row=0, column=0, sticky="w")
        name = ttk.Entry(body, width=42); name.grid(row=1, column=0, pady=(6, 16)); name.focus_set()

        def submit():
            try:
                self._model.create_project(name.get())
                dialog.destroy(); self.refresh()
            except Exception as error:
                messagebox.showerror("Cannot create Project", str(error), parent=dialog)

        ttk.Button(body, text="Create", command=submit).grid(row=2, column=0, sticky="e")
        dialog.bind("<Return>", lambda _event: submit())

    def _open_task_dialog(self):
        dialog = tk.Toplevel(self._root)
        dialog.title("Create Task")
        dialog.transient(self._root); dialog.grab_set(); dialog.geometry("580x560")
        body = ttk.Frame(dialog, padding=20); body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1); body.rowconfigure(3, weight=1); body.rowconfigure(7, weight=1)
        ttk.Label(body, text="Task title").grid(row=0, column=0, sticky="w")
        title = ttk.Entry(body); title.grid(row=1, column=0, sticky="ew", pady=(6, 14)); title.focus_set()
        ttk.Label(body, text="Goal").grid(row=2, column=0, sticky="w")
        goal = tk.Text(body, height=5, bg="#102235", fg="#edf4fc", insertbackground="#edf4fc", relief="flat", padx=8, pady=8)
        goal.grid(row=3, column=0, sticky="nsew", pady=(6, 12))
        ttk.Label(body, text="Deliverable").grid(row=4, column=0, sticky="w")
        deliverable = ttk.Entry(body); deliverable.grid(row=5, column=0, sticky="ew", pady=(6, 12))
        ttk.Label(body, text="Blocking acceptance criteria (one per line)").grid(row=6, column=0, sticky="w")
        acceptance = tk.Text(body, height=5, bg="#102235", fg="#edf4fc", insertbackground="#edf4fc", relief="flat", padx=8, pady=8)
        acceptance.grid(row=7, column=0, sticky="nsew", pady=(6, 12))
        ttk.Label(body, text="Constraints (optional, one per line)").grid(row=8, column=0, sticky="w")
        constraints = ttk.Entry(body); constraints.grid(row=9, column=0, sticky="ew", pady=(6, 16))

        def submit():
            try:
                self._model.create_task(title.get(), goal.get("1.0", "end-1c"), deliverable.get(),
                                        tuple(acceptance.get("1.0", "end-1c").splitlines()),
                                        tuple(constraints.get().splitlines()))
                dialog.destroy(); self.refresh()
            except Exception as error:
                messagebox.showerror("Cannot create Task", str(error), parent=dialog)

        ttk.Button(body, text="Create versioned task", command=submit).grid(row=10, column=0, sticky="e")

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
        dialog.title("Revise Task Planning")
        dialog.transient(self._root); dialog.grab_set(); dialog.geometry("580x560")
        body = ttk.Frame(dialog, padding=20); body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1); body.rowconfigure(3, weight=1); body.rowconfigure(7, weight=1)
        ttk.Label(body, text=f"Creates planning revision {revision.number + 1}; revision {revision.number} remains immutable.").grid(row=0, column=0, sticky="w", pady=(0, 10))
        ttk.Label(body, text="Task title").grid(row=1, column=0, sticky="w")
        title = ttk.Entry(body); title.grid(row=2, column=0, sticky="ew", pady=(6, 12)); title.insert(0, task.title)
        ttk.Label(body, text="Goal").grid(row=3, column=0, sticky="w")
        goal = tk.Text(body, height=5, bg="#102235", fg="#edf4fc", insertbackground="#edf4fc", relief="flat", padx=8, pady=8)
        goal.grid(row=4, column=0, sticky="nsew", pady=(6, 12)); goal.insert("1.0", task.goal)
        ttk.Label(body, text="Deliverable").grid(row=5, column=0, sticky="w")
        deliverable = ttk.Entry(body); deliverable.grid(row=6, column=0, sticky="ew", pady=(6, 12)); deliverable.insert(0, revision.deliverable)
        ttk.Label(body, text="Blocking acceptance criteria (one per line)").grid(row=7, column=0, sticky="w")
        acceptance = tk.Text(body, height=5, bg="#102235", fg="#edf4fc", insertbackground="#edf4fc", relief="flat", padx=8, pady=8)
        acceptance.grid(row=8, column=0, sticky="nsew", pady=(6, 12)); acceptance.insert("1.0", "\n".join(item.statement for item in revision.acceptance))
        ttk.Label(body, text="Constraints (optional, one per line)").grid(row=9, column=0, sticky="w")
        constraints = ttk.Entry(body); constraints.grid(row=10, column=0, sticky="ew", pady=(6, 16)); constraints.insert(0, "\n".join(revision.constraints))

        def submit():
            try:
                self._model.revise_task(task.task_id, task.revision, title.get(), goal.get("1.0", "end-1c"),
                                        deliverable.get(), tuple(acceptance.get("1.0", "end-1c").splitlines()),
                                        tuple(constraints.get().splitlines()))
                dialog.destroy(); self.refresh()
            except Exception as error:
                messagebox.showerror("Cannot revise Task", str(error), parent=dialog)

        ttk.Button(body, text="Create revision", command=submit).grid(row=11, column=0, sticky="e")

    def run(self):
        self._root.mainloop()


def launch_native_desktop(database: str | Path, context: OrganizationContext, project_id: str) -> None:
    DuomitDesktopApp(NativeTaskViewModel(database, context, project_id)).run()
