import tempfile
import unittest
from pathlib import Path

from duomit.errors import DuomitError
from duomit.flow_compiler import compile_flow_draft
from duomit.flow_drafts import SqliteFlowDraftStore
from duomit.foundation import OrganizationContext
from duomit.node_catalog import NodeDescriptor, SqliteNodeCatalog
from duomit.proposals import SqliteProposalStore


class ProposalTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        database = Path(self.temp.name) / "state.sqlite3"
        self.flow = SqliteFlowDraftStore(database)
        self.catalog = SqliteNodeCatalog(database)
        self.store = SqliteProposalStore(database, self.catalog)
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)
        self.other = OrganizationContext("org", "bob", "bob", "deviceb", "sessionb", "requestb", "traceb", 1)
        self.catalog.register(self.context, NodeDescriptor("need_node", "1_0", "need", "normalize", ("deterministic",), (), "need_input", "need_output"))
        self.catalog.register(self.context, NodeDescriptor("workflow_node", "1_0", "workflow", "coordinate", ("deterministic",), (), "workflow_input", "workflow_output"))

    def tearDown(self):
        self.store.close(); self.catalog.close(); self.flow.close(); self.temp.cleanup()

    def compilation(self):
        draft = self.flow.create(self.context, "taskrevision", "Flow")
        draft = self.flow.add_node(self.context, draft.flow_draft_id, 1, "outcome_alignment", "1_0", "Outcome", {})
        draft = self.flow.add_node(self.context, draft.flow_draft_id, 2, "process_plan", "1_0", "Process", {})
        draft = self.flow.add_edge(self.context, draft.flow_draft_id, 3, draft.nodes[0]["id"], "out", draft.nodes[1]["id"], "in", "control")
        return compile_flow_draft(self.context, self.flow, draft.flow_draft_id, draft.semantic_digest)

    def test_proposal_pins_compilation_and_descriptor_versions(self):
        compilation = self.compilation()
        proposal = self.store.propose(self.context, "taskone", compilation,
                                      {"need": ("need_node", "1_0"), "workflow": ("workflow_node", "1_0")}, "requestone")
        self.assertEqual(proposal.state, "ready_for_review")
        self.assertEqual(len(proposal.domain_nodes), 2)
        self.assertEqual(self.store.propose(self.context, "taskone", compilation,
                         {"need": ("need_node", "1_0"), "workflow": ("workflow_node", "1_0")}, "requestone").proposal_id, proposal.proposal_id)
        with self.assertRaises(DuomitError):
            self.store.get(self.other, proposal.proposal_id)

    def test_rejects_missing_or_wrong_domain_descriptor(self):
        compilation = self.compilation()
        with self.assertRaises(DuomitError):
            self.store.propose(self.context, "taskone", compilation, {"need": ("need_node", "1_0")}, "missing")
        with self.assertRaises(DuomitError):
            self.store.propose(self.context, "taskone", compilation,
                               {"need": ("workflow_node", "1_0"), "workflow": ("workflow_node", "1_0")}, "wrong")


if __name__ == "__main__":
    unittest.main()
