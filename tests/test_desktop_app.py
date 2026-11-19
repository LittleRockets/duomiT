import tempfile
import unittest
from pathlib import Path

from duomit.desktop_app import DuomitDesktopApp, NativeTaskViewModel, TaskNode
from duomit.desktop_flow_editor import NativeFlowViewModel
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

    def test_native_view_model_creates_project_and_task_through_workspace_boundary(self):
        model = NativeTaskViewModel(self.path, self.context, "placeholder")
        project = model.create_project("New project")
        task = model.create_task("New task", "Create from native desktop", "A reviewed native task", ("Task is reviewed",))
        self.assertEqual(model.project_id, project)
        node = model.load_tasks()[0]
        self.assertEqual(node.task_id, task)
        self.assertEqual(node.planning_revision.deliverable, "A reviewed native task")

    def test_native_view_model_revises_task_as_a_new_planning_revision(self):
        model = NativeTaskViewModel(self.path, self.context, "placeholder")
        model.create_project("New project")
        task = model.create_task("New task", "First goal", "First deliverable", ("First review",))
        before = model.load_tasks()[0]
        revised = model.revise_task(task, before.revision, "New task", "Revised goal", "Revised deliverable", ("Second review",), ("Local only",))
        after = model.load_tasks()[0]
        self.assertEqual((revised.number, after.planning_revision.number, after.goal), (2, 2, "Revised goal"))

    def test_reselecting_same_task_does_not_reenter_tree_selection(self):
        class Tree:
            def __init__(self): self.selected = (); self.set_count = 0
            def selection(self): return self.selected
            def selection_set(self, task_id): self.selected = (task_id,); self.set_count += 1
            def focus(self, _task_id): pass

        class Detail:
            def set(self, _value): pass

        app = object.__new__(DuomitDesktopApp)
        app._tasks = (TaskNode("taskone", "Task", "draft", "Goal", 1, None, 0, 0),)
        app._selected_task_id = None
        app._tree = Tree()
        app._detail = Detail()
        app._show_task("taskone")
        app._show_task("taskone")
        self.assertEqual(app._tree.set_count, 1)

    def test_native_flow_view_model_edits_controlled_draft_without_execution(self):
        workspace = LocalWorkspace(self.path)
        try:
            project = workspace.create_project(self.context, "Project")
            task = workspace.create_task(self.context, project, "Task", "Goal")
        finally:
            workspace.close()
        flow = NativeFlowViewModel(self.path, self.context)
        draft = flow.create_draft(task, "Task flow")
        draft = flow.add_node(draft.flow_draft_id, "Need")
        draft = flow.add_node(draft.flow_draft_id, "Workflow")
        draft = flow.connect(draft.flow_draft_id, draft.nodes[0]["id"], draft.nodes[1]["id"])
        self.assertEqual(flow.validate(draft.flow_draft_id)["status"], "valid")
        compilation = flow.compile_review(draft.flow_draft_id)
        self.assertEqual(tuple(item.domain for item in compilation.intents), ("need", "workflow"))


if __name__ == "__main__":
    unittest.main()
