from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest

from duomit.contracts import ExtensionVersion, RequestContext
from duomit.desktop import SqliteDesktop
from duomit.errors import DuomitError


class DesktopTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "desktop.sqlite3"
        self.now = [100.0]
        self.desktop = self.open_desktop()
        self.context = RequestContext("tenant", "alice", "request", "trace")
        self.body = b'{"kind":"document","content":"reference material"}'
        self.extension = ExtensionVersion("material", "1.0", "document",
                                          hashlib.sha256(self.body).hexdigest(), "Apache-2.0")
        self.desktop.publish(self.context, self.extension, self.body)

    def open_desktop(self):
        desktop = SqliteDesktop(self.path, clock=lambda: self.now[0])
        self.addCleanup(desktop.close)
        return desktop

    def acquire(self, key="request", *, run="run", node="node", valid_seconds=60):
        return self.desktop.acquire(self.context, "material", "1.0", run, node, key,
                                    valid_seconds=valid_seconds)

    def read(self, lease):
        return self.desktop.read(self.context, lease.lease_id, lease.fence, lease.run_id, lease.node_id)

    def test_publish_is_immutable_and_digest_checked(self):
        self.desktop.publish(self.context, self.extension, self.body)
        for extension, body in ((self.extension, b"changed"),
                                (replace(self.extension, license_id="MIT"), self.body)):
            with self.assertRaises(DuomitError):
                self.desktop.publish(self.context, extension, body)
        self.assertEqual(len(self.desktop.events(self.context)), 1)

    def test_material_read_and_reopen(self):
        lease = self.acquire()
        self.assertEqual(type(self.read(lease)), bytes)
        self.assertEqual(self.read(lease), self.body)
        reopened = self.open_desktop()
        self.assertEqual(reopened.read(self.context, lease.lease_id, lease.fence, "run", "node"), self.body)
        self.assertEqual(reopened.stats(self.context, "material", "1.0")["active_leases"], 1)

    def test_idempotent_acquire_does_not_extend_or_duplicate_lease(self):
        lease = self.acquire()
        self.now[0] += 10
        self.assertEqual(self.acquire(), lease)
        self.assertEqual(self.desktop.stats(self.context, "material", "1.0")["active_leases"], 1)
        with self.assertRaises(DuomitError) as error:
            self.acquire(run="another-run")
        self.assertEqual(error.exception.code, "CONFLICT")

    def test_release_is_idempotent_and_replay_does_not_resurrect(self):
        lease = self.acquire()
        released = self.desktop.release(self.context, lease.lease_id, lease.fence)
        self.assertEqual(self.desktop.release(self.context, lease.lease_id, lease.fence), released)
        self.assertEqual(self.acquire().state, "released")
        self.assertEqual(self.desktop.stats(self.context, "material", "1.0")["active_leases"], 0)
        with self.assertRaises(DuomitError):
            self.read(lease)
        self.assertEqual(sum(event["event_type"] == "mirror.released" for event in self.desktop.events(self.context)), 1)

    def test_renewal_fences_stale_read_release_and_renew(self):
        lease = self.acquire()
        self.now[0] += 5
        renewed = self.desktop.renew(self.context, lease.lease_id, lease.fence)
        self.assertEqual(renewed.fence, lease.fence + 1)
        self.assertGreater(renewed.expires_at, lease.expires_at)
        for action in (lambda: self.read(lease),
                       lambda: self.desktop.release(self.context, lease.lease_id, lease.fence),
                       lambda: self.desktop.renew(self.context, lease.lease_id, lease.fence)):
            with self.assertRaises(DuomitError) as error:
                action()
            self.assertEqual(error.exception.code, "CONFLICT")
        self.assertEqual(self.read(renewed), self.body)

    def test_expiry_cannot_be_renewed_and_replay_does_not_create_new_lease(self):
        lease = self.acquire(valid_seconds=1)
        self.now[0] += 1
        for action in (lambda: self.read(lease),
                       lambda: self.desktop.renew(self.context, lease.lease_id, lease.fence)):
            with self.assertRaises(DuomitError):
                action()
        self.assertEqual(self.acquire(valid_seconds=1).state, "expired")
        self.assertEqual(self.desktop.stats(self.context, "material", "1.0")["active_leases"], 0)

    def test_grace_period_and_authority_survive_cache_collection(self):
        lease = self.acquire()
        self.desktop.release(self.context, lease.lease_id, lease.fence)
        self.assertEqual(self.desktop.collect(self.context, grace_seconds=10), 0)
        self.assertEqual(self.desktop.stats(self.context, "material", "1.0")["cache_state"], "quarantined")
        self.now[0] += 9
        self.assertEqual(self.desktop.collect(self.context, grace_seconds=10), 0)
        self.now[0] += 1
        self.assertEqual(self.desktop.collect(self.context, grace_seconds=10), 1)
        self.assertEqual(self.desktop.stats(self.context, "material", "1.0")["cache_state"], "collected")
        self.assertEqual(self.read(self.acquire("new-request")), self.body)
        self.assertEqual(self.acquire().state, "released")

    def test_active_lease_prevents_collection_and_new_lease_cancels_retirement(self):
        lease = self.acquire()
        self.assertEqual(self.desktop.collect(self.context, grace_seconds=0), 0)
        self.desktop.release(self.context, lease.lease_id, lease.fence)
        self.desktop.collect(self.context, grace_seconds=0)
        new = self.acquire("new-request")
        self.assertEqual(self.desktop.collect(self.context, grace_seconds=0), 0)
        self.assertEqual(self.read(new), self.body)

    def test_crashed_holder_expires_and_cache_is_reclaimed(self):
        self.acquire(valid_seconds=1)
        self.now[0] += 2
        restored = self.open_desktop()
        self.assertEqual(restored.collect(self.context, grace_seconds=0), 0)
        self.assertEqual(restored.collect(self.context, grace_seconds=0), 1)
        self.assertEqual(restored.stats(self.context, "material", "1.0")["active_leases"], 0)

    def test_cross_user_and_tenant_cannot_observe_or_mutate_materials(self):
        lease = self.acquire()
        for tenant, owner in (("other", "alice"), ("tenant", "bob")):
            context = RequestContext(tenant, owner, "r", "t")
            for action in (lambda: self.desktop.acquire(context, "material", "1.0", "run", "node", "key"),
                           lambda: self.desktop.read(context, lease.lease_id, lease.fence, "run", "node"),
                           lambda: self.desktop.release(context, lease.lease_id, lease.fence),
                           lambda: self.desktop.renew(context, lease.lease_id, lease.fence),
                           lambda: self.desktop.withdraw(context, "material", "1.0"),
                           lambda: self.desktop.stats(context, "material", "1.0")):
                with self.assertRaises(DuomitError) as error:
                    action()
                self.assertEqual(error.exception.code, "NOT_FOUND")
            self.assertEqual(self.desktop.collect(context, grace_seconds=0), 0)
            self.assertEqual(self.desktop.events(context), [])

    def test_run_and_node_binding_are_required_for_read(self):
        lease = self.acquire()
        for run, node in (("other", "node"), ("run", "other")):
            with self.assertRaises(DuomitError):
                self.desktop.read(self.context, lease.lease_id, lease.fence, run, node)

    def test_withdrawal_blocks_new_and_existing_lease_use(self):
        lease = self.acquire()
        self.desktop.withdraw(self.context, "material", "1.0")
        self.desktop.publish(self.context, self.extension, self.body)
        for action in (lambda: self.acquire("new-request"), lambda: self.read(lease),
                       lambda: self.desktop.renew(self.context, lease.lease_id, lease.fence)):
            with self.assertRaises(DuomitError) as error:
                action()
            self.assertEqual(error.exception.code, "EXTENSION_WITHDRAWN")
        self.assertEqual(self.desktop.release(self.context, lease.lease_id, lease.fence).state, "revoked")

    def test_cache_tampering_is_detected(self):
        lease = self.acquire()
        self.desktop._db.execute("UPDATE desktop_mirrors SET cached_body=? WHERE mirror_id=?", (b"tampered", lease.mirror_id))
        with self.assertRaises(DuomitError) as error:
            self.read(lease)
        self.assertEqual(error.exception.code, "MATERIAL_CORRUPT")

    def test_invalid_duration_is_rejected(self):
        for value in (0, -1, True, float("nan"), float("inf"), 3601):
            with self.assertRaises(DuomitError):
                self.acquire(valid_seconds=value)

    def test_parallel_duplicate_acquire_has_one_lease(self):
        barrier = Barrier(4)

        def acquire_once(_):
            desktop = SqliteDesktop(self.path, clock=lambda: self.now[0])
            try:
                barrier.wait(timeout=5)
                return desktop.acquire(self.context, "material", "1.0", "run", "node", "same-request").lease_id
            finally:
                desktop.close()

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(acquire_once, range(4)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(self.desktop.stats(self.context, "material", "1.0")["active_leases"], 1)
        self.assertEqual(sum(event["event_type"] == "mirror.acquired" for event in self.desktop.events(self.context)), 1)

    def test_parallel_collection_and_acquire_cannot_delete_live_material(self):
        lease = self.acquire()
        self.desktop.release(self.context, lease.lease_id, lease.fence)
        self.desktop.collect(self.context, grace_seconds=0)
        barrier = Barrier(2)

        def action(collect):
            desktop = SqliteDesktop(self.path, clock=lambda: self.now[0])
            try:
                barrier.wait(timeout=5)
                if collect:
                    return desktop.collect(self.context, grace_seconds=0)
                return desktop.acquire(self.context, "material", "1.0", "run", "node", "new-request")
            finally:
                desktop.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(action, (True, False)))
        self.assertEqual(self.read(results[1]), self.body)

    def test_event_failure_rolls_back_lease_and_mirror_creation(self):
        self.desktop._db.execute("""CREATE TRIGGER fail_desktop_events BEFORE INSERT ON desktop_events
            BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.acquire()
        self.assertEqual(self.desktop.stats(self.context, "material", "1.0"),
                         {"active_leases": 0, "cache_state": "absent"})


if __name__ == "__main__":
    unittest.main()
