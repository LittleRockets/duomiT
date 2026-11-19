import tempfile
import unittest
from pathlib import Path

from duomit.errors import DuomitError
from duomit.foundation import OrganizationContext
from duomit.task_revisions import AcceptanceCriterion, SqliteTaskRevisionStore


class TaskRevisionTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.store = SqliteTaskRevisionStore(Path(self.temp.name) / "tasks.sqlite3")
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)
        self.other = OrganizationContext("org", "bob", "bob", "deviceb", "sessionb", "requestb", "traceb", 1)
        self.acceptance = (AcceptanceCriterion("criterionone", "A report is reviewed"),)

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def test_revisions_are_immutable_and_conflict_checked(self):
        first = self.store.create(self.context, "Report", "Prepare report", "Reviewed report", self.acceptance)
        second = self.store.revise(self.context, first.task_id, 1, "Report", "Prepare revised report", "Reviewed report", self.acceptance)
        self.assertEqual((first.number, second.number), (1, 2))
        self.assertEqual(self.store.get_current(self.context, first.task_id).task_revision_id, second.task_revision_id)
        with self.assertRaises(DuomitError):
            self.store.revise(self.context, first.task_id, 1, "Report", "Late", "Reviewed report", self.acceptance)
        with self.assertRaises(DuomitError):
            self.store.get_current(self.other, first.task_id)

    def test_deliverable_and_blocking_acceptance_are_required(self):
        with self.assertRaises(DuomitError):
            self.store.create(self.context, "Report", "Prepare", "", self.acceptance)
        with self.assertRaises(DuomitError):
            self.store.create(self.context, "Report", "Prepare", "Deliverable", (AcceptanceCriterion("criterionone", "Optional", False),))


if __name__ == "__main__":
    unittest.main()
