import tempfile
import unittest
from pathlib import Path

from duomit.desktop_app import DuomitDesktopApp, NativeTaskViewModel, TaskNode
from duomit.foundation import OrganizationContext
from duomit.workspace import LocalWorkspace


class NativeDesktopViewTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"; root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root); self.path = Path(self.temp.name) / "workspace.sqlite3"
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)

    def tearDown(self): self.temp.cleanup()

    def test_native_view_model_projects_local_tasks_without_http(self):
        workspace = LocalWorkspace(self.path)
        try:
            project = workspace.create_project(self.context, "Project")
            task = workspace.create_task(self.context, project, "Native task", "Show in desktop application")
        finally:
            workspace.close()
        nodes = NativeTaskViewModel(self.path, self.context, project).load_tasks()
        self.assertEqual(nodes[0].task_id, task)
        self.assertEqual((nodes[0].x, nodes[0].y), (44, 54))

    def test_reselecting_same_task_does_not_reenter_tree_selection(self):
        class Tree:
            def __init__(self): self.selected = (); self.set_count = 0
            def selection(self): return self.selected
            def selection_set(self, task_id): self.selected = (task_id,); self.set_count += 1
            def focus(self, _task_id): pass

        class Detail:
            def set(self, _value): pass

        app = object.__new__(DuomitDesktopApp)
        app._tasks = (TaskNode("taskone", "Task", "draft", "Goal", 1, 0, 0),)
        app._selected_task_id = None
        app._tree = Tree()
        app._detail = Detail()
        app._show_task("taskone")
        app._show_task("taskone")
        self.assertEqual(app._tree.set_count, 1)


if __name__ == "__main__":
    unittest.main()
