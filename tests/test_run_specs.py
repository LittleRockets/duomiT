import tempfile
import unittest
from pathlib import Path

from duomit.build_specs import SqliteBuildSpecStore
from duomit.errors import DuomitError
from duomit.foundation import OrganizationContext
from duomit.need_records import NeedCandidate, NeedOutcome, SqliteNeedStore
from duomit.run_specs import SqliteRunSpecStore
from duomit.task_revisions import AcceptanceCriterion, SqliteTaskRevisionStore
from duomit.workflow_specs import SqliteProcessSpecStore


class RunSpecTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"; root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root); database = Path(self.temp.name) / "state.sqlite3"
        self.tasks = SqliteTaskRevisionStore(database); self.needs = SqliteNeedStore(database)
        self.processes = SqliteProcessSpecStore(database); self.builds = SqliteBuildSpecStore(database); self.runs = SqliteRunSpecStore(database)
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)
        task = self.tasks.create(self.context, "Report", "Prepare", "Reviewed report", (AcceptanceCriterion("reviewed", "Reviewed"),))
        need = self.needs.create(self.context, task, NeedCandidate((NeedOutcome("outcome", "Report", ("reviewed",)),)))
        self.process = self.processes.derive(self.context, need); self.build = self.builds.derive(self.context, task, need, self.process)

    def tearDown(self):
        self.runs.close(); self.builds.close(); self.processes.close(); self.needs.close(); self.tasks.close(); self.temp.cleanup()

    def test_run_spec_is_readiness_and_review_only(self):
        run = self.runs.derive(self.context, self.build, self.process)
        self.assertEqual(run.targets[0].environment_class, "local")
        self.assertEqual(run.observations[0].response, "require_review")
        self.assertEqual(self.runs.derive(self.context, self.build, self.process).run_spec_id, run.run_spec_id)

    def test_mismatched_process_is_rejected(self):
        other_task = self.tasks.create(self.context, "Other", "Prepare", "Other report", (AcceptanceCriterion("otherreviewed", "Reviewed"),))
        other_need = self.needs.create(self.context, other_task, NeedCandidate((NeedOutcome("otheroutcome", "Other", ("otherreviewed",)),)))
        other_process = self.processes.derive(self.context, other_need)
        with self.assertRaises(DuomitError):
            self.runs.derive(self.context, self.build, other_process)


if __name__ == "__main__":
    unittest.main()
