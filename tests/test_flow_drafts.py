import tempfile
import unittest
from pathlib import Path

from duomit.errors import DuomitError
from duomit.flow_drafts import SqliteFlowDraftStore
from duomit.foundation import OrganizationContext


class FlowDraftTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.store = SqliteFlowDraftStore(Path(self.temp.name) / "flow.sqlite3")
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)
        self.other = OrganizationContext("org", "bob", "bob", "deviceb", "sessionb", "requestb", "traceb", 1)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_semantic_and_layout_revisions_are_independent(self):
        draft = self.store.create(self.context, "taskrevision", "Delivery flow")
        draft = self.store.add_node(self.context, draft.flow_draft_id, 1, "outcome_alignment", "1_0", "Outcome", {})
        semantic_digest = draft.semantic_digest
        moved = self.store.move_node(self.context, draft.flow_draft_id, 1, draft.nodes[0]["id"], 200, 80)
        self.assertEqual(moved.semantic_revision, 2)
        self.assertEqual(moved.semantic_digest, semantic_digest)
        self.assertEqual(moved.layout_revision, 2)

    def test_edges_are_directional_and_acyclic(self):
        draft = self.store.create(self.context, "taskrevision", "Delivery flow")
        draft = self.store.add_node(self.context, draft.flow_draft_id, 1, "outcome_alignment", "1_0", "Outcome", {})
        draft = self.store.add_node(self.context, draft.flow_draft_id, 2, "process_plan", "1_0", "Process", {})
        source, target = draft.nodes
        draft = self.store.add_edge(self.context, draft.flow_draft_id, 3, source["id"], "out", target["id"], "in", "control")
        self.assertEqual(self.store.validate(self.context, draft.flow_draft_id)["status"], "valid")
        with self.assertRaises(DuomitError):
            self.store.add_edge(self.context, draft.flow_draft_id, 4, target["id"], "out", source["id"], "out", "control")

    def test_unknown_template_and_other_member_access_are_rejected(self):
        draft = self.store.create(self.context, "taskrevision", "Delivery flow")
        with self.assertRaises(DuomitError):
            self.store.add_node(self.context, draft.flow_draft_id, 1, "shell_script", "1_0", "Bad", {})
        with self.assertRaises(DuomitError):
            self.store.add_node(self.context, draft.flow_draft_id, 1, "outcome_alignment", "1_0", "Bad", {"command": "x"})
        with self.assertRaises(DuomitError):
            self.store.validate(self.other, draft.flow_draft_id)

    def test_owner_can_list_only_its_own_drafts(self):
        first = self.store.create(self.context, "taskone", "First")
        self.store.create(self.context, "tasktwo", "Second")
        self.assertEqual(tuple(item.flow_draft_id for item in self.store.list_for_owner(self.context, "taskone")),
                         (first.flow_draft_id,))


if __name__ == "__main__":
    unittest.main()
