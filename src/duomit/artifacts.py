"""Immutable local Artifact versions; sharing requires a later explicit grant."""
from contextlib import contextmanager
import hashlib, sqlite3, uuid
from pathlib import Path
from .errors import DuomitError, require
from .foundation import OrganizationContext

class LocalArtifactStore:
 def __init__(self,database:str|Path):
  self.db=sqlite3.connect(str(database),isolation_level=None);self.db.row_factory=sqlite3.Row
  self.db.execute('CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,org TEXT,owner TEXT,name TEXT,version INTEGER,digest TEXT,body BLOB,UNIQUE(org,owner,name,version))')
 @contextmanager
 def tx(self):
  self.db.execute('BEGIN IMMEDIATE')
  try:yield;self.db.execute('COMMIT')
  except BaseException:self.db.execute('ROLLBACK');raise
 def close(self):self.db.close()
 def create(self,c:OrganizationContext,name:str,body:bytes):
  require(isinstance(name,str) and 1<=len(name.strip())<=256,'Invalid artifact name');require(type(body) is bytes and 0<len(body)<=10485760,'Invalid artifact body')
  aid=uuid.uuid4().hex;digest=hashlib.sha256(body).hexdigest()
  with self.tx():self.db.execute('INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)',(aid,c.organization_id,c.membership_id,name.strip(),1,digest,body))
  return {'artifact_id':aid,'version':1,'digest':digest}
 def read(self,c:OrganizationContext,artifact_id:str):
  row=self.db.execute('SELECT * FROM artifacts WHERE id=? AND org=? AND owner=?',(artifact_id,c.organization_id,c.membership_id)).fetchone()
  if not row:raise DuomitError('NOT_FOUND','Artifact is not available')
  body=bytes(row['body']);require(hashlib.sha256(body).hexdigest()==row['digest'],'Artifact digest mismatch');return body
