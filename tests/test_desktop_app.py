import tempfile
import unittest
from pathlib import Path

from duomit.desktop_app import NativeTaskViewModel
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


if __name__ == "__main__":
    unittest.main()
