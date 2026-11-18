from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from duomit.contracts import Plan, RequestContext, Step
from duomit.errors import DuomitError
from duomit.local_runtime import LocalRuntime
from duomit.registry import Capability


class Recorder:
    def __init__(self):
        self.calls = []

    def execute(self, context, capability, operation_key):
        self.calls.append(operation_key)
        return "artifact:" + operation_key


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.policy = (Capability("read", "1.0"), Capability("write", "1.0", True))
        self.context = RequestContext("tenant", "alice", "r", "t")
        self.runtime = self.open_runtime()

    def open_runtime(self, **kwargs):
        runtime = LocalRuntime(self.path, self.policy, **kwargs)
        self.addCleanup(runtime.close)
        return runtime

    def plan(self):
        return Plan((Step("a", "read"), Step("b", "write", ("a",), True)))

    def test_persistent_approval_and_terminal_replay(self):
        run = self.runtime.create(self.context, self.plan(), "request")
        recorder = Recorder()
        waiting = self.runtime.drive(self.context, run, recorder)
        self.assertEqual(waiting["state"], "waiting_approval")
        second = self.open_runtime()
        self.assertEqual(second.inspect(self.context, run), waiting)
        second.approve(self.context, run, "b", waiting["steps"][1]["operation_key"], waiting["revision"])
        finished = second.drive(self.context, run, recorder)
        self.assertEqual(finished["state"], "succeeded")
        self.assertEqual(second.drive(self.context, run, recorder), finished)
        self.assertEqual(len(recorder.calls), 2)

    def test_approval_bound_to_revision_operation_and_expiry(self):
        now = [100.0]
        runtime = self.open_runtime(clock=lambda: now[0])
        run = runtime.create(self.context, self.plan(), "expiry")
        waiting = runtime.drive(self.context, run, Recorder())
        key = waiting["steps"][1]["operation_key"]
        with self.assertRaises(DuomitError):
            runtime.approve(self.context, run, "b", "wrong", waiting["revision"])
        with self.assertRaises(DuomitError):
            runtime.approve(self.context, run, "b", key, waiting["revision"] - 1)
        runtime.approve(self.context, run, "b", key, waiting["revision"], valid_seconds=1)
        now[0] = 102
        self.assertEqual(runtime.drive(self.context, run, Recorder())["state"], "waiting_approval")

    def test_idempotent_create_and_conflicting_payload(self):
        run = self.runtime.create(self.context, self.plan(), "same")
        self.assertEqual(run, self.runtime.create(self.context, self.plan(), "same"))
        with self.assertRaises(DuomitError) as error:
            self.runtime.create(self.context, Plan((Step("a", "read"),)), "same")
        self.assertEqual(error.exception.code, "CONFLICT")

    def test_cross_tenant_and_user_access_denied(self):
        run = self.runtime.create(self.context, self.plan(), "private")
        for tenant, user in (("other", "alice"), ("tenant", "bob")):
            context = RequestContext(tenant, user, "r", "t")
            for action in (lambda: self.runtime.inspect(context, run),
                           lambda: self.runtime.drive(context, run, Recorder()),
                           lambda: self.runtime.cancel(context, run, 1),
                           lambda: self.runtime.recover(context, run, 1),
                           lambda: self.runtime.approve(context, run, "b", "key", 1)):
                with self.subTest(tenant=tenant, user=user), self.assertRaises(DuomitError) as error:
                    action()
                self.assertEqual(error.exception.code, "NOT_FOUND")
            self.assertEqual(self.runtime.pending_events(context), [])

    def test_execution_policy_is_not_granted_by_plan(self):
        for plan in (Plan((Step("a", "admin"),)), Plan((Step("a", "write"),))):
            with self.assertRaises(DuomitError):
                self.runtime.create(self.context, plan, "invalid")

    def test_crash_after_external_success_is_not_retried(self):
        calls = []

        class Crash:
            def execute(inner, context, capability, operation_key):
                calls.append(operation_key)  # External success before missing receipt.
                raise SystemExit("simulated worker termination")

        run = self.runtime.create(self.context, Plan((Step("a", "read"),)), "crash")
        with self.assertRaises(SystemExit):
            self.runtime.drive(self.context, run, Crash())
        restored = self.open_runtime()
        running = restored.inspect(self.context, run)
        recorder = Recorder()
        self.assertEqual(restored.drive(self.context, run, recorder)["state"], "running")
        restored.recover(self.context, run, running["revision"])
        uncertain = restored.drive(self.context, run, recorder)
        self.assertEqual(uncertain["state"], "reconciling")
        self.assertEqual(recorder.calls, [])
        restored.reconcile(self.context, run, "a", calls[0], uncertain["revision"],
                           artifact_ref="artifact:verified-receipt")
        self.assertEqual(restored.drive(self.context, run, recorder)["state"], "succeeded")
        self.assertEqual(len(calls), 1)
        self.assertEqual(recorder.calls, [])

    def test_unknown_failure_requires_explicit_resolution(self):
        class Failed:
            def execute(inner, context, capability, operation_key):
                raise TimeoutError("provider-secret")

        run = self.runtime.create(self.context, Plan((Step("a", "read"),)), "failure")
        uncertain = self.runtime.drive(self.context, run, Failed())
        self.assertEqual(uncertain["state"], "reconciling")
        self.runtime.reconcile(self.context, run, "a", uncertain["steps"][0]["operation_key"],
                               uncertain["revision"])
        self.assertEqual(self.runtime.inspect(self.context, run)["state"], "failed")
        self.assertNotIn("provider-secret", str(self.runtime.pending_events(self.context)))

    def test_second_worker_cannot_claim_running_step(self):
        second = self.open_runtime()
        other_recorder = Recorder()
        runtime = self.runtime
        run = runtime.create(self.context, Plan((Step("a", "read"),)), "workers")

        class Probe:
            def execute(inner, context, capability, operation_key):
                self.assertEqual(second.drive(context, run, other_recorder)["state"], "running")
                return "artifact:receipt"

        self.assertEqual(runtime.drive(self.context, run, Probe())["state"], "succeeded")
        self.assertEqual(other_recorder.calls, [])

    def test_cancel_does_not_start_more_steps(self):
        run = self.runtime.create(self.context, self.plan(), "cancel")
        waiting = self.runtime.drive(self.context, run, Recorder())
        self.runtime.cancel(self.context, run, waiting["revision"])
        recorder = Recorder()
        self.assertEqual(self.runtime.drive(self.context, run, recorder)["state"], "cancelled")
        self.assertEqual(recorder.calls, [])

    def test_transactional_outbox_replay_and_owner_scoped_ack(self):
        run = self.runtime.create(self.context, Plan((Step("a", "read"),)), "events")
        self.runtime.drive(self.context, run, Recorder())
        events = self.runtime.pending_events(self.context)
        self.assertEqual([event["revision"] for event in events], [1, 2, 3, 4])
        other = RequestContext("tenant", "bob", "r", "t")
        self.runtime.acknowledge(other, events[0]["event_id"])
        self.assertEqual(self.runtime.pending_events(self.context), events)
        self.runtime.acknowledge(self.context, events[0]["event_id"])
        self.runtime.acknowledge(self.context, events[0]["event_id"])
        self.assertEqual(self.runtime.pending_events(self.context), events[1:])

    def test_cancellation_during_execution_waits_for_receipt(self):
        second = self.open_runtime()
        runtime = self.runtime
        run = runtime.create(self.context, self.plan(), "inflight-cancel")

        class Cancelling:
            def execute(inner, context, capability, operation_key):
                state = second.inspect(context, run)
                second.cancel(context, run, state["revision"])
                self.assertEqual(second.inspect(context, run)["state"], "running")
                return "artifact:already-completed"

        result = runtime.drive(self.context, run, Cancelling())
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["steps"][0]["state"], "succeeded")
        self.assertEqual(result["steps"][1]["state"], "cancelled")

    def test_unknown_write_remains_reconciling_after_cancellation(self):
        class Unknown:
            def execute(inner, context, capability, operation_key):
                raise TimeoutError()

        run = self.runtime.create(self.context, self.plan(), "unknown-cancel")
        state = self.runtime.drive(self.context, run, Unknown())
        self.runtime.cancel(self.context, run, state["revision"])
        self.assertEqual(self.runtime.drive(self.context, run, Recorder())["state"], "reconciling")

    def test_outbox_failure_rolls_back_state_change(self):
        run = self.runtime.create(self.context, self.plan(), "atomic")
        before = self.runtime.inspect(self.context, run)
        self.runtime._db.execute("""CREATE TRIGGER reject_events BEFORE INSERT ON outbox
            BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.drive(self.context, run, Recorder())
        self.assertEqual(self.runtime.inspect(self.context, run), before)
        self.assertEqual(len(self.runtime.pending_events(self.context)), 1)

    def test_v1_database_migrates_without_losing_events(self):
        path = Path(self.temp.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("""CREATE TABLE outbox (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                tenant TEXT NOT NULL, owner TEXT NOT NULL, run_id TEXT NOT NULL,
                revision INTEGER NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0)""")
            connection.execute("""INSERT INTO outbox
                (event_id,tenant,owner,run_id,revision,event_type,payload)
                VALUES ('event','tenant','alice','run',1,'run.created','{}')""")
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        runtime = LocalRuntime(path, self.policy)
        self.addCleanup(runtime.close)
        events = runtime.pending_events(self.context)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["trace_id"], "legacy")
        self.assertEqual(runtime._db.execute("PRAGMA user_version").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
