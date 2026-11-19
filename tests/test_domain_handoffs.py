import tempfile
import unittest
from pathlib import Path

from duomit.build_specs import SqliteBuildSpecStore
from duomit.domain_handoffs import validate_four_domain_handoff
from duomit.errors import DuomitError
from duomit.foundation import OrganizationContext
from duomit.need_records import NeedCandidate, NeedOutcome, SqliteNeedStore
from duomit.run_specs import SqliteRunSpecStore
from duomit.task_revisions import AcceptanceCriterion, SqliteTaskRevisionStore
from duomit.workflow_specs import SqliteProcessSpecStore


class DomainHandoffTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"; root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root); database = Path(self.temp.name) / "state.sqlite3"
        self.tasks = SqliteTaskRevisionStore(database); self.needs = SqliteNeedStore(database); self.processes = SqliteProcessSpecStore(database)
        self.builds = SqliteBuildSpecStore(database); self.runs = SqliteRunSpecStore(database)
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)

    def tearDown(self):
        self.runs.close(); self.builds.close(); self.processes.close(); self.needs.close(); self.tasks.close(); self.temp.cleanup()

    def chain(self, title="Report", criterion="reviewed"):
        task = self.tasks.create(self.context, title, "Prepare", "Reviewed output", (AcceptanceCriterion(criterion, "Reviewed"),))
        need = self.needs.create(self.context, task, NeedCandidate((NeedOutcome("outcome" + criterion, "Output", (criterion,)),)))
        process = self.processes.derive(self.context, need); build = self.builds.derive(self.context, task, need, process)
        return task, need, process, build, self.runs.derive(self.context, build, process)

    def test_validates_all_four_fixed_domain_handoffs(self):
        plan = validate_four_domain_handoff(*self.chain())
        self.assertEqual(len(plan.digest), 64)

    def test_rejects_cross_task_domain_mix(self):
        task, need, process, build, run = self.chain()
        other = self.chain("Other", "otherreviewed")
        with self.assertRaises(DuomitError):
            validate_four_domain_handoff(task, need, process, build, other[4])


if __name__ == "__main__":
    unittest.main()
