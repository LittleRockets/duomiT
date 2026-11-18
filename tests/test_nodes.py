from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest

from duomit.contracts import ExtensionVersion, NodeDescriptor, RequestContext
from duomit.desktop import SqliteDesktop
from duomit.errors import DuomitError
from duomit.nodes import NodeHandle, NodeRegistry
from duomit.node_demo import run_demo


class NodeTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.now = [100.0]
        self.desktop = SqliteDesktop(Path(self.temp.name) / "desktop.sqlite3", clock=lambda: self.now[0])
        self.addCleanup(self.desktop.close)
        self.context = RequestContext("tenant", "alice", "request", "trace")
        self.body = b"Shared reference document"
        self.extension = ExtensionVersion("reference", "1.0", "document", hashlib.sha256(self.body).hexdigest(), "Apache-2.0")
        self.desktop.publish(self.context, self.extension, self.body)
        self.registry = NodeRegistry()
        self.a = NodeDescriptor("a", "test-domain", "1.0", (("reference", "1.0"),))
        self.b = NodeDescriptor("b", "test-domain", "1.0", (("reference", "1.0"),))
        self.registry.register(self.context, self.a)
        self.registry.register(self.context, self.b)

    def activate(self, run="run", *, edges=(("a", "b"), ("b", "a")), budget=100):
        session = self.registry.activate(self.context, run, (("a", "1.0"), ("b", "1.0")), edges,
                                         self.desktop, message_budget=budget)
        self.addCleanup(session.close)
        return session

    def test_readonly_material_shared_but_workspaces_isolated(self):
        session = self.activate()
        a, b = session.handle("a"), session.handle("b")
        self.assertEqual(a.material("reference", "1.0"), self.body)
        self.assertEqual(b.material("reference", "1.0"), self.body)
        a.put("draft", "private changes")
        self.assertIsNone(b.get("draft"))
        self.assertEqual(b.material("reference", "1.0"), self.body)
        second = self.activate("other-run")
        self.assertIsNone(second.handle("a").get("draft"))
        self.assertEqual(self.desktop.stats(self.context, "reference", "1.0")["active_leases"], 2)

    def test_bidirectional_messages_have_fixed_scope_and_sequence(self):
        session = self.activate()
        a, b = session.handle("a"), session.handle("b")
        first = a.send(b, "request")
        self.assertEqual(b.receive(), first)
        second = b.send(a, "response")
        self.assertEqual(a.receive(), second)
        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertEqual((first.graph_id, first.run_id, first.from_node, first.to_node), (session.graph_id, "run", "a", "b"))
        self.assertIsNone(a.receive())

    def test_cross_graph_and_missing_edge_are_denied(self):
        session = self.activate(edges=(("a", "b"),))
        other = self.activate("other-run")
        for source, target in ((session.handle("a"), other.handle("b")),
                               (session.handle("b"), session.handle("a"))):
            with self.assertRaises(DuomitError) as error:
                source.send(target, "forbidden")
            self.assertEqual(error.exception.code, "CAPABILITY_DENIED")

    def test_message_budget_stops_bidirectional_loops(self):
        session = self.activate(budget=2)
        a, b = session.handle("a"), session.handle("b")
        a.send(b, "one")
        b.send(a, "two")
        with self.assertRaises(DuomitError) as error:
            a.send(b, "three")
        self.assertEqual(error.exception.code, "BUDGET_EXCEEDED")

    def test_undeclared_extension_and_forged_handle_are_denied(self):
        session = self.activate()
        with self.assertRaises(DuomitError):
            session.handle("a").material("reference", "2.0")
        forged = NodeHandle(session, self.a)
        for action in (lambda: forged.material("reference", "1.0"),
                       lambda: forged.send(session.handle("b"), "forged"),
                       lambda: session.handle("a").send(forged, "forged")):
            with self.assertRaises(DuomitError):
                action()

    def test_drain_blocks_new_graphs_but_existing_activation_continues(self):
        session = self.activate()
        self.registry.drain(self.context, "a", "1.0")
        with self.assertRaises(DuomitError) as error:
            self.activate("new-run")
        self.assertEqual(error.exception.code, "NODE_DRAINING")
        self.assertEqual(session.handle("a").material("reference", "1.0"), self.body)
        with self.assertRaises(DuomitError):
            self.registry.register(self.context, replace(self.a, domain_id="changed"))

    def test_cross_owner_registry_access_is_denied(self):
        for tenant, owner in (("other", "alice"), ("tenant", "bob")):
            context = RequestContext(tenant, owner, "r", "t")
            with self.assertRaises(DuomitError):
                self.registry.activate(context, "run", (("a", "1.0"),), (), self.desktop)
            with self.assertRaises(DuomitError):
                self.registry.drain(context, "a", "1.0")

    def test_session_close_releases_leases_and_disables_handles(self):
        session = self.activate()
        a, b = session.handle("a"), session.handle("b")
        a.material("reference", "1.0")
        b.material("reference", "1.0")
        session.close()
        session.close()
        self.assertEqual(self.desktop.stats(self.context, "reference", "1.0")["active_leases"], 0)
        for action in (lambda: a.material("reference", "1.0"), lambda: a.put("key", "value"),
                       lambda: a.send(b, "message"), lambda: a.receive(), lambda: session.handle("a")):
            with self.assertRaises(DuomitError) as error:
                action()
            self.assertEqual(error.exception.code, "GRAPH_CLOSED")

    def test_expired_material_is_not_silently_reacquired(self):
        session = self.activate()
        a = session.handle("a")
        a.material("reference", "1.0")
        self.now[0] += 61
        with self.assertRaises(DuomitError):
            a.material("reference", "1.0")
        with self.assertRaises(DuomitError):
            a.renew_material("reference", "1.0")

    def test_explicit_renewal_updates_handle_fence(self):
        session = self.activate()
        a = session.handle("a")
        a.material("reference", "1.0")
        self.now[0] += 50
        a.renew_material("reference", "1.0")
        self.now[0] += 20
        self.assertEqual(a.material("reference", "1.0"), self.body)

    def test_invalid_graph_members_and_edges_rejected(self):
        for nodes, edges in (((("a", "1.0"), ("a", "1.0")), ()),
                             ((("a", "1.0"),), (("a", "unknown"),))):
            with self.assertRaises(DuomitError):
                self.registry.activate(self.context, "run", nodes, edges, self.desktop)

    def test_close_can_retry_release_failure_without_reopening_graph(self):
        session = self.activate()
        handle = session.handle("a")
        handle.material("reference", "1.0")
        desktop = self.desktop

        class FlakyRelease:
            calls = 0

            def release(inner, context, lease_id, expected_fence):
                inner.calls += 1
                if inner.calls == 1:
                    raise RuntimeError("untrusted backend detail")
                return desktop.release(context, lease_id, expected_fence)

        session._desktop = FlakyRelease()
        with self.assertRaises(DuomitError) as error:
            session.close()
        self.assertEqual(error.exception.code, "LEASE_RELEASE_FAILED")
        self.assertNotIn("backend", str(error.exception))
        with self.assertRaises(DuomitError):
            handle.get("draft")
        session.close()
        self.assertEqual(self.desktop.stats(self.context, "reference", "1.0")["active_leases"], 0)

    def test_offline_demo_repeats_without_leaking_active_leases(self):
        database = Path(self.temp.name) / "demo.sqlite3"
        first = run_demo(database)
        second = run_demo(database)
        for result in (first, second):
            self.assertTrue(result["shared_material_verified"])
            self.assertTrue(result["workspaces_isolated"])
            self.assertEqual(result["active_leases_before_close"], 2)
            self.assertEqual(result["active_leases_after_close"], 0)
            self.assertEqual(result["caches_collected"], 1)
        self.assertNotEqual(first["graph_id"], second["graph_id"])


if __name__ == "__main__":
    unittest.main()
