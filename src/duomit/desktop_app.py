"""Native tkinter Desktop shell for the local-first duomiT reference app.

This is a native process, not an HTTP service.  It reads a trusted local
workspace projection and does not run tasks, invoke models, or issue grants.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from .foundation import OrganizationContext
from .workspace import LocalWorkspace


@dataclass(frozen=True)
class TaskNode:
    task_id: str
    title: str
    state: str
    goal: str
    revision: int
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
        return tuple(TaskNode(task["id"], task["title"], task["state"], task["goal"], task["revision"],
                              44 + (index % 3) * 244, 54 + (index // 3) * 150)
                     for index, task in enumerate(tasks))


class DuomitDesktopApp:
    """Read-only native Project/Task shell. All mutations remain explicit ports."""

    def __init__(self, view_model: NativeTaskViewModel):
        self._model = view_model
        self._tasks: tuple[TaskNode, ...] = ()
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
        ttk.Label(header, text="Project / Task · native read-only reference shell", style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Button(header, text="Refresh", command=self.refresh).pack(anchor="e", side="right")

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
        self._tree.delete(*self._tree.get_children())
        for task in self._tasks:
            self._tree.insert("", "end", iid=task.task_id, text=task.title, values=(task.state,))
        self._draw_graph()
        if not self._tasks:
            self._detail.set("This Project has no tasks yet. Create one through the explicit workspace command.")

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
            canvas.create_text(x + 14, y + 74, text=f"revision {task.revision}", fill="#9fb1c3", anchor="w", font=("Segoe UI", 9), tags=(task.task_id,))
            canvas.tag_bind(task.task_id, "<Button-1>", lambda event, task_id=task.task_id: self._show_task(task_id))

    def _select_task(self, _event):
        selected = self._tree.selection()
        if selected: self._show_task(selected[0])

    def _show_task(self, task_id: str):
        task = next((item for item in self._tasks if item.task_id == task_id), None)
        if task is None: return
        self._tree.selection_set(task_id); self._tree.focus(task_id)
        self._detail.set(f"{task.title} · {task.state} · revision {task.revision}\n{task.goal}\nNo Proposal, WorkUnit, Run, or external operation exists for this draft.")

    def run(self):
        self._root.mainloop()


def launch_native_desktop(database: str | Path, context: OrganizationContext, project_id: str) -> None:
    DuomitDesktopApp(NativeTaskViewModel(database, context, project_id)).run()
