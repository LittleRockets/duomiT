import tempfile,unittest
from pathlib import Path
from duomit.errors import DuomitError
from duomit.foundation import OrganizationContext
from duomit.workspace import LocalWorkspace
class WorkspaceTests(unittest.TestCase):
 def setUp(self):
  root=Path(__file__).resolve().parents[1]/'build'/'tmp';root.mkdir(parents=True,exist_ok=True);self.t=tempfile.TemporaryDirectory(dir=root);self.w=LocalWorkspace(Path(self.t.name)/'w.db');self.a=OrganizationContext('org','alice','alice','device','session','request','trace',1);self.b=OrganizationContext('other','bob','bob','device2','session2','request2','trace2',1)
 def tearDown(self):self.w.close();self.t.cleanup()
 def test_project_and_task_are_persistent_and_organization_scoped(self):
  p=self.w.create_project(self.a,'Launch');t=self.w.create_task(self.a,p,'Design','Create a design');self.assertEqual(self.w.list_tasks(self.a,p)[0]['id'],t)
  with self.assertRaises(DuomitError):self.w.list_tasks(self.b,p)
