"""SQLite reference store for trusted organization membership, devices and sessions."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from .errors import DuomitError, require
from .organization_identity import AuthenticatedSession, DeviceRegistration, OrganizationMembership, session_is_usable

class OrganizationSessionStore:
 def __init__(self, database: str|Path, *, clock=None):
  self.clock=clock or (lambda: datetime.now(timezone.utc))
  self.db=sqlite3.connect(str(database),isolation_level=None); self.db.row_factory=sqlite3.Row
  self.db.executescript('CREATE TABLE IF NOT EXISTS memberships(id TEXT PRIMARY KEY,org TEXT,issuer TEXT,subject TEXT,state TEXT,revision INTEGER);CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,org TEXT,membership_id TEXT,key TEXT,state TEXT,revision INTEGER);CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,org TEXT,membership_id TEXT,device_id TEXT,state TEXT,expires_at TEXT,policy_revision INTEGER);')
 @contextmanager
 def tx(self):
  self.db.execute('BEGIN IMMEDIATE')
  try: yield; self.db.execute('COMMIT')
  except BaseException: self.db.execute('ROLLBACK'); raise
 def close(self): self.db.close()
 def put_membership(self, value: OrganizationMembership):
  require(isinstance(value,OrganizationMembership),'Invalid membership')
  with self.tx(): self.db.execute('INSERT OR REPLACE INTO memberships VALUES (?,?,?,?,?,?)',(value.membership_id,value.organization_id,value.issuer,value.subject,value.state,value.revision))
 def put_device(self,value:DeviceRegistration):
  require(isinstance(value,DeviceRegistration),'Invalid device')
  with self.tx(): self.db.execute('INSERT OR REPLACE INTO devices VALUES (?,?,?,?,?,?)',(value.device_id,value.organization_id,value.membership_id,value.public_key,value.state,value.revision))
 def put_session(self,value:AuthenticatedSession):
  require(isinstance(value,AuthenticatedSession),'Invalid session')
  with self.tx():
   m=self._membership(value.membership_id); d=self._device(value.device_id)
   require(session_is_usable(m,d,value) and datetime.fromisoformat(value.expires_at.replace('Z','+00:00')) > self.clock(),'Session binding is not usable')
   self.db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?)',(value.session_id,value.organization_id,value.membership_id,value.device_id,value.state,value.expires_at,value.policy_revision))
 def _membership(self,id):
  r=self.db.execute('SELECT * FROM memberships WHERE id=?',(id,)).fetchone()
  if not r: raise DuomitError('NOT_FOUND','Membership is not available')
  return OrganizationMembership(r['id'],r['org'],r['issuer'],r['subject'],r['state'],r['revision'])
 def _device(self,id):
  r=self.db.execute('SELECT * FROM devices WHERE id=?',(id,)).fetchone()
  if not r: raise DuomitError('NOT_FOUND','Device is not available')
  return DeviceRegistration(r['id'],r['org'],r['membership_id'],r['key'],r['state'],r['revision'])
 def authenticate(self,session_id):
  r=self.db.execute('SELECT * FROM sessions WHERE id=?',(session_id,)).fetchone()
  if not r: raise DuomitError('UNAUTHENTICATED','Session is unavailable')
  s=AuthenticatedSession(r['id'],r['org'],r['membership_id'],r['device_id'],r['state'],r['expires_at'],r['policy_revision'])
  expires=datetime.fromisoformat(s.expires_at.replace('Z','+00:00'))
  if expires <= self.clock() or not session_is_usable(self._membership(s.membership_id),self._device(s.device_id),s): raise DuomitError('UNAUTHENTICATED','Session is unavailable')
  return s
