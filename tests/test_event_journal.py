import tempfile, unittest
from pathlib import Path
from duomit.errors import DuomitError
from duomit.event_journal import SqliteEventJournal
from duomit.foundation import EventEnvelope, PinnedReference
class EventJournalTests(unittest.TestCase):
 def setUp(self):
  root=Path(__file__).resolve().parents[1]/'build'/'tmp'; root.mkdir(parents=True,exist_ok=True); self.temp=tempfile.TemporaryDirectory(dir=root); self.journal=SqliteEventJournal(Path(self.temp.name)/'events.sqlite3')
 def tearDown(self): self.journal.close(); self.temp.cleanup()
 def event(self, event_id='event'):
  return EventEnvelope(event_id,'task.created','1.0','org','task',1,'2026-11-18T00:00:00Z',PinnedReference('payload','p','1','a'*64),'trace')
 def test_append_replay_delivery_and_organization_scope(self):
  self.journal.append(self.event()); self.journal.append(self.event()); pending=self.journal.pending('org'); self.assertEqual(len(pending),1); self.journal.acknowledge('org','event'); self.assertEqual(self.journal.pending('org'),())
 def test_conflicting_replay_and_cross_org_ack_fail(self):
  self.journal.append(self.event())
  with self.assertRaises(DuomitError): self.journal.append(self.event('event')) if False else self.journal.append(EventEnvelope('event','run.created','1.0','org','task',1,'2026-11-18T00:00:00Z',PinnedReference('payload','p','1','a'*64),'trace'))
  with self.assertRaises(DuomitError): self.journal.acknowledge('other','event')
