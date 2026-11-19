import tempfile,unittest
from pathlib import Path
from duomit.errors import DuomitError
from duomit.organization_identity import *
from duomit.organization_session_store import OrganizationSessionStore
class StoreTests(unittest.TestCase):
 def setUp(self):
  root=Path(__file__).resolve().parents[1]/'build'/'tmp';root.mkdir(parents=True,exist_ok=True);self.t=tempfile.TemporaryDirectory(dir=root);self.s=OrganizationSessionStore(Path(self.t.name)/'s.db');self.m=OrganizationMembership('m','o','https://issuer','sub','active',1);self.d=DeviceRegistration('d','o','m','x'*16,'active',1);self.x=AuthenticatedSession('s','o','m','d','active','2026-11-19T00:00:00Z',1)
 def tearDown(self):self.s.close();self.t.cleanup()
 def test_persists_and_authenticates_bound_session(self):self.s.put_membership(self.m);self.s.put_device(self.d);self.s.put_session(self.x);self.assertEqual(self.s.authenticate('s').device_id,'d')
 def test_suspension_invalidates_existing_session(self):
  self.s.put_membership(self.m);self.s.put_device(self.d);self.s.put_session(self.x)
  self.s.put_membership(OrganizationMembership('m','o','https://issuer','sub','suspended',2))
  with self.assertRaises(DuomitError):self.s.authenticate('s')
