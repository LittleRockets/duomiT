"""Local-first Project and Task reference workspace; no execution or model access."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3, uuid
from .contracts import identifier
from .errors import DuomitError, require
from .foundation import OrganizationContext

class LocalWorkspace:
 def __init__(self,database:str|Path):
  self.db=sqlite3.connect(str(database),isolation_level=None);self.db.row_factory=sqlite3.Row
  self.db.executescript('CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,organization_id TEXT NOT NULL,owner TEXT NOT NULL,name TEXT NOT NULL,revision INTEGER NOT NULL);CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),organization_id TEXT NOT NULL,owner TEXT NOT NULL,title TEXT NOT NULL,goal TEXT NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL);')
 @contextmanager
 def tx(self):
  self.db.execute('BEGIN IMMEDIATE')
  try:yield;self.db.execute('COMMIT')
  except BaseException:self.db.execute('ROLLBACK');raise
 def close(self):self.db.close()
 def create_project(self,context:OrganizationContext,name:str):
  require(isinstance(name,str) and 1<=len(name.strip())<=128,'Invalid project name');pid=uuid.uuid4().hex
  with self.tx():self.db.execute('INSERT INTO projects VALUES (?,?,?,?,1)',(pid,context.organization_id,context.membership_id,name.strip()))
  return pid
 def create_task(self,context:OrganizationContext,project_id:str,title:str,goal:str):
  identifier(project_id);require(isinstance(title,str) and 1<=len(title.strip())<=200,'Invalid task title');require(isinstance(goal,str) and 1<=len(goal.strip())<=8192,'Invalid task goal')
  with self.tx():
   p=self.db.execute('SELECT * FROM projects WHERE id=? AND organization_id=?',(project_id,context.organization_id)).fetchone()
   if not p:raise DuomitError('NOT_FOUND','Project is not available')
   tid=uuid.uuid4().hex;self.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,1)',(tid,project_id,context.organization_id,context.membership_id,title.strip(),goal.strip(),'draft'))
  return tid
 def revise_task(self,context:OrganizationContext,task_id:str,expected_revision:int,title:str,goal:str):
  identifier(task_id);require(type(expected_revision) is int and expected_revision>0,'Invalid task revision');require(isinstance(title,str) and 1<=len(title.strip())<=200,'Invalid task title');require(isinstance(goal,str) and 1<=len(goal.strip())<=8192,'Invalid task goal')
  with self.tx():
   task=self.db.execute('SELECT * FROM tasks WHERE id=? AND organization_id=? AND owner=?',(task_id,context.organization_id,context.membership_id)).fetchone()
   if not task:raise DuomitError('NOT_FOUND','Task is not available')
   if task['revision']!=expected_revision:raise DuomitError('TASK_REVISION_CONFLICT','Task revision has changed')
   self.db.execute('UPDATE tasks SET title=?,goal=?,revision=revision+1 WHERE id=?',(title.strip(),goal.strip(),task_id))
 def list_tasks(self,context:OrganizationContext,project_id:str):
  identifier(project_id);p=self.db.execute('SELECT 1 FROM projects WHERE id=? AND organization_id=?',(project_id,context.organization_id)).fetchone()
  if not p:raise DuomitError('NOT_FOUND','Project is not available')
  return [dict(x) for x in self.db.execute('SELECT id,title,goal,state,revision FROM tasks WHERE project_id=? AND organization_id=? ORDER BY rowid',(project_id,context.organization_id))]
