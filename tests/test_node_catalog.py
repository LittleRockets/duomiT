import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from duomit.errors import DuomitError
from duomit.foundation import OrganizationContext
from duomit.node_catalog import NodeDescriptor, SqliteNodeCatalog


class NodeCatalogTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.catalog = SqliteNodeCatalog(Path(self.temp.name) / "nodes.sqlite3")
        self.context = OrganizationContext("org", "alice", "alice", "device", "session", "request", "trace", 1)
        self.other = OrganizationContext("other", "eve", "eve", "devicee", "sessione", "requeste", "tracee", 1)
        self.descriptor = NodeDescriptor("process_coordinator", "1_0", "workflow", "coordinate",
                                         ("deterministic",), ("task_read",), "process_input", "process_output")

    def tearDown(self):
        self.catalog.close()
        self.temp.cleanup()

    def test_descriptor_is_immutable_and_organization_scoped(self):
        self.assertEqual(self.catalog.register(self.context, self.descriptor), self.descriptor)
        self.assertEqual(self.catalog.get(self.context, "process_coordinator", "1_0"), self.descriptor)
        with self.assertRaises(DuomitError):
            self.catalog.register(self.context, replace(self.descriptor, role="different"))
        with self.assertRaises(DuomitError):
            self.catalog.get(self.other, "process_coordinator", "1_0")


if __name__ == "__main__":
    unittest.main()
