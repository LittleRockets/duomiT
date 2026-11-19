import tempfile
import unittest
from pathlib import Path

from duomit.desktop_shell import LocalTaskDashboard
from duomit.foundation import OrganizationContext
from duomit.workspace import LocalWorkspace


class DesktopShellTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.path = Path(self.temp.name) / "workspace.sqlite3"
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)

    def tearDown(self):
        self.temp.cleanup()

    def test_dashboard_is_read_only_and_escapes_task_content(self):
        workspace = LocalWorkspace(self.path)
        try:
            project = workspace.create_project(self.context, "Project")
            workspace.create_task(self.context, project, "<unsafe>", "A <script> must be text")
        finally:
            workspace.close()
        page = LocalTaskDashboard(self.path, self.context).render(project)
        self.assertIn("任务节点图", page)
        self.assertIn("&lt;unsafe&gt;", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("不执行任务、不调用模型", page)
        reopened = LocalWorkspace(self.path)
        try:
            self.assertEqual(len(reopened.list_tasks(self.context, project)), 1)
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
