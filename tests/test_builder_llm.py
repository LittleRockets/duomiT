import tempfile
import unittest
from pathlib import Path

from duomit.builder_llm import BrokeredNeedNormalizer, decode_need_candidate
from duomit.errors import DuomitError
from duomit.foundation import OrganizationContext
from duomit.need_records import SqliteNeedStore
from duomit.task_revisions import AcceptanceCriterion, SqliteTaskRevisionStore


class FakeNeedApi:
    def __init__(self, payload): self.payload = payload; self.calls = 0
    def generate_need_candidate(self, context, task): self.calls += 1; return self.payload


class BuilderLlmTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"; root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root); database = Path(self.temp.name) / "state.sqlite3"
        self.tasks = SqliteTaskRevisionStore(database); self.needs = SqliteNeedStore(database)
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)
        self.task = self.tasks.create(self.context, "Report", "Prepare", "Reviewed report", (AcceptanceCriterion("reviewed", "Reviewed"),))

    def tearDown(self): self.needs.close(); self.tasks.close(); self.temp.cleanup()

    def test_api_candidate_is_decoded_then_subject_to_need_validation(self):
        api = FakeNeedApi({"outcomes": [{"outcome_id": "outcome", "statement": "Reviewed report", "acceptance_criterion_ids": ["reviewed"]}]})
        record = BrokeredNeedNormalizer(api, self.needs).normalize(self.context, self.task)
        self.assertEqual((api.calls, record.completeness), (1, "sufficient"))

    def test_candidate_cannot_supply_authority_or_unknown_fields(self):
        with self.assertRaises(DuomitError):
            decode_need_candidate({"outcomes": [], "tool": "shell"})
        api = FakeNeedApi({"outcomes": [{"outcome_id": "outcome", "statement": "Report", "acceptance_criterion_ids": ["wrong"]}]})
        with self.assertRaises(DuomitError):
            BrokeredNeedNormalizer(api, self.needs).normalize(self.context, self.task)


if __name__ == "__main__":
    unittest.main()
