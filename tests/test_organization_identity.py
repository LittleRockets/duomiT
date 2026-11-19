import unittest
from duomit.errors import DuomitError
from duomit.organization_identity import *
class OrganizationIdentityTests(unittest.TestCase):
 def test_only_active_bound_membership_device_and_session_are_usable(self):
  m=OrganizationMembership('m','o','https://issuer','sub','active',1); d=DeviceRegistration('d','o','m','x'*16,'active',1); s=AuthenticatedSession('s','o','m','d','active','2026-11-19T00:00:00Z',1); self.assertTrue(session_is_usable(m,d,s)); self.assertFalse(session_is_usable(m,d,AuthenticatedSession('s','o','m','d','revoked','2026-11-19T00:00:00Z',1)))
 def test_invalid_issuer_and_states_rejected(self):
  with self.assertRaises(DuomitError): OrganizationMembership('m','o','issuer','sub','active',1)
  with self.assertRaises(DuomitError): DeviceRegistration('d','o','m','x'*16,'unknown',1)
