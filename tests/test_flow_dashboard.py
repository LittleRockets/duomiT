import tempfile
import unittest
from pathlib import Path

from duomit.flow_dashboard import FlowDraftDashboard
from duomit.flow_drafts import SqliteFlowDraftStore
from duomit.foundation import OrganizationContext


class FlowDashboardTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.store = SqliteFlowDraftStore(Path(self.temp.name) / "flow.sqlite3")
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def test_read_only_projection_uses_domain_labels_and_escaped_text(self):
        draft = self.store.create(self.context, "taskrevision", "Flow")
        draft = self.store.add_node(self.context, draft.flow_draft_id, 1, "outcome_alignment", "1_0", "<Need>", {}, x=20, y=30)
        draft = self.store.add_node(self.context, draft.flow_draft_id, 2, "process_plan", "1_0", "Process", {}, x=420, y=30)
        draft = self.store.add_edge(self.context, draft.flow_draft_id, 3, draft.nodes[0]["id"], "out", draft.nodes[1]["id"], "in", "control")
        page = FlowDraftDashboard(self.store, self.context).render(draft.flow_draft_id)
        self.assertIn("&lt;Need&gt;", page)
        self.assertIn("workflow", page)
        self.assertIn("尚未编译为 WorkUnit", page)
        self.assertIn("不调用模型、不生成 Proposal、不启动执行", page)


if __name__ == "__main__":
    unittest.main()
