import tempfile
import unittest
from pathlib import Path

from duomit.errors import DuomitError
from duomit.foundation import OrganizationContext
from duomit.need_records import NeedCandidate, NeedOutcome, NeedUnknown, SqliteNeedStore
from duomit.task_revisions import AcceptanceCriterion, SqliteTaskRevisionStore


class NeedRecordTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.tasks = SqliteTaskRevisionStore(Path(self.temp.name) / "state.sqlite3")
        self.needs = SqliteNeedStore(Path(self.temp.name) / "state.sqlite3")
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)
        self.task = self.tasks.create(self.context, "Report", "Prepare report", "Reviewed report",
                                      (AcceptanceCriterion("reviewed", "Reviewer accepts report"),), ("Use local data",))

    def tearDown(self):
        self.needs.close(); self.tasks.close(); self.temp.cleanup()

    def test_need_requires_acceptance_coverage_and_tracks_blocking_unknowns(self):
        with self.assertRaises(DuomitError):
            self.needs.create(self.context, self.task, NeedCandidate((NeedOutcome("outcome", "Report", ("other",)),)))
        candidate = NeedCandidate((NeedOutcome("outcome", "Reviewed report", ("reviewed",)),),
                                  (NeedUnknown("source", "Which source is approved?", True),))
        record = self.needs.create(self.context, self.task, candidate)
        self.assertEqual(record.completeness, "insufficient")
        self.assertEqual(self.needs.create(self.context, self.task, candidate).need_id, record.need_id)

    def test_complete_need_is_sufficient(self):
        record = self.needs.create(self.context, self.task,
                                   NeedCandidate((NeedOutcome("outcome", "Reviewed report", ("reviewed",)),)))
        self.assertEqual(record.completeness, "sufficient")


if __name__ == "__main__":
    unittest.main()
