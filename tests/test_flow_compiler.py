import tempfile
import unittest
from pathlib import Path

from duomit.errors import DuomitError
from duomit.flow_compiler import compile_flow_draft
from duomit.flow_drafts import SqliteFlowDraftStore
from duomit.foundation import OrganizationContext


class FlowCompilerTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.store = SqliteFlowDraftStore(Path(self.temp.name) / "flow.sqlite3")
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_compiles_pinned_valid_intent_graph_without_execution(self):
        draft = self.store.create(self.context, "taskrevision", "Delivery flow")
        draft = self.store.add_node(self.context, draft.flow_draft_id, 1, "outcome_alignment", "1_0", "Outcome", {})
        draft = self.store.add_node(self.context, draft.flow_draft_id, 2, "process_plan", "1_0", "Process", {})
        draft = self.store.add_edge(self.context, draft.flow_draft_id, 3, draft.nodes[0]["id"], "out", draft.nodes[1]["id"], "in", "control")
        compiled = compile_flow_draft(self.context, self.store, draft.flow_draft_id, draft.semantic_digest)
        self.assertEqual([intent.domain for intent in compiled.intents], ["need", "workflow"])
        self.assertEqual(len(compiled.edge_map), 1)
        self.assertEqual(len(compiled.digest), 64)

    def test_rejects_invalid_or_unpinned_draft(self):
        draft = self.store.create(self.context, "taskrevision", "Incomplete")
        with self.assertRaises(DuomitError):
            compile_flow_draft(self.context, self.store, draft.flow_draft_id, draft.semantic_digest)
        draft = self.store.add_node(self.context, draft.flow_draft_id, 1, "outcome_alignment", "1_0", "Outcome", {})
        with self.assertRaises(DuomitError):
            compile_flow_draft(self.context, self.store, draft.flow_draft_id, "0" * 64)


if __name__ == "__main__":
    unittest.main()
