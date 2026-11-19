import tempfile
import unittest
from pathlib import Path

from duomit.build_specs import SqliteBuildSpecStore
from duomit.errors import DuomitError
from duomit.foundation import OrganizationContext
from duomit.need_records import NeedCandidate, NeedOutcome, NeedUnknown, SqliteNeedStore
from duomit.task_revisions import AcceptanceCriterion, SqliteTaskRevisionStore
from duomit.workflow_specs import SqliteProcessSpecStore


class BuildSpecTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"; root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root); database = Path(self.temp.name) / "state.sqlite3"
        self.tasks = SqliteTaskRevisionStore(database); self.needs = SqliteNeedStore(database)
        self.processes = SqliteProcessSpecStore(database); self.builds = SqliteBuildSpecStore(database)
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)
        self.task = self.tasks.create(self.context, "Report", "Prepare", "Reviewed report", (AcceptanceCriterion("reviewed", "Reviewed"),))
        self.need = self.needs.create(self.context, self.task, NeedCandidate((NeedOutcome("outcome", "Report", ("reviewed",)),)))
        self.process = self.processes.derive(self.context, self.need)

    def tearDown(self):
        self.builds.close(); self.processes.close(); self.needs.close(); self.tasks.close(); self.temp.cleanup()

    def test_build_spec_has_deliverable_and_evidence_path_for_each_acceptance(self):
        build = self.builds.derive(self.context, self.task, self.need, self.process)
        self.assertEqual(build.deliverables[0].description, "Reviewed report")
        self.assertEqual([item.criterion_id for item in build.verification_plan], ["reviewed"])
        self.assertEqual(self.builds.derive(self.context, self.task, self.need, self.process).build_spec_id, build.build_spec_id)

    def test_incomplete_need_cannot_cross_into_build(self):
        incomplete = self.needs.create(self.context, self.task, NeedCandidate((NeedOutcome("other", "Report", ("reviewed",)),), (NeedUnknown("input", "Need source", True),)))
        with self.assertRaises(DuomitError):
            self.builds.derive(self.context, self.task, incomplete, self.process)


if __name__ == "__main__":
    unittest.main()
