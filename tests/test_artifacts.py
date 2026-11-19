import tempfile,unittest
from pathlib import Path
from duomit.artifacts import LocalArtifactStore
from duomit.errors import DuomitError
from duomit.foundation import OrganizationContext
class ArtifactTests(unittest.TestCase):
 def setUp(self):
  r=Path(__file__).resolve().parents[1]/'build'/'tmp';r.mkdir(parents=True,exist_ok=True);self.t=tempfile.TemporaryDirectory(dir=r);self.s=LocalArtifactStore(Path(self.t.name)/'a.db');self.a=OrganizationContext('o','a','a','d','s','r','t',1);self.b=OrganizationContext('o','b','b','d2','s2','r2','t2',1)
 def tearDown(self):self.s.close();self.t.cleanup()
 def test_owner_only_immutable_artifact(self):
  x=self.s.create(self.a,'notes',b'hello');self.assertEqual(self.s.read(self.a,x['artifact_id']),b'hello')
  with self.assertRaises(DuomitError):self.s.read(self.b,x['artifact_id'])
