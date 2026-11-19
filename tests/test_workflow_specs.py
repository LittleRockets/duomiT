import tempfile
import unittest
from pathlib import Path

from duomit.errors import DuomitError
from duomit.foundation import OrganizationContext
from duomit.need_records import NeedCandidate, NeedOutcome, NeedUnknown, SqliteNeedStore
from duomit.task_revisions import AcceptanceCriterion, SqliteTaskRevisionStore
from duomit.workflow_specs import SqliteProcessSpecStore


class WorkflowSpecTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"; root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root); database = Path(self.temp.name) / "state.sqlite3"
        self.tasks = SqliteTaskRevisionStore(database); self.needs = SqliteNeedStore(database); self.processes = SqliteProcessSpecStore(database)
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)
        task = self.tasks.create(self.context, "Report", "Prepare", "Reviewed report", (AcceptanceCriterion("reviewed", "Reviewed"),))
        self.complete = self.needs.create(self.context, task, NeedCandidate((NeedOutcome("outcome", "Report", ("reviewed",)),)))
        self.incomplete = self.needs.create(self.context, task, NeedCandidate((NeedOutcome("other", "Report", ("reviewed",)),), (NeedUnknown("input", "Need source", True),)))

    def tearDown(self):
        self.processes.close(); self.needs.close(); self.tasks.close(); self.temp.cleanup()

    def test_process_spec_preserves_blocking_acceptance_as_quality_gate(self):
        process = self.processes.derive(self.context, self.complete)
        self.assertEqual(process.quality_gates[0].criterion_ids, ("reviewed",))
        self.assertEqual(self.processes.derive(self.context, self.complete).process_spec_id, process.process_spec_id)

    def test_blocking_unknown_prevents_process_derivation(self):
        with self.assertRaises(DuomitError):
            self.processes.derive(self.context, self.incomplete)


if __name__ == "__main__":
    unittest.main()
