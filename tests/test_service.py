"""Headless service lifecycle; no model, internet, or payment requirements."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from holding.autonomy import Mission, StandingGrant
from holding.daemon_cli import main
from holding.service import AgentUnavailable, HoldingService, ServiceSettings
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40


class FakeWorker:
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.calls = []

    def ready(self):
        return self.healthy

    def __call__(self, mission):
        self.calls.append(mission.id)
        return SHA


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = Path(self.folder.name) / "holding.sqlite"
        with StateStore(self.db) as state:
            state.grant_for_onboarding(StandingGrant(REPO, frozenset({"code"}), int(time.time()) + 1000))

    def make(self, worker):
        return HoldingService(ServiceSettings(self.db, idle_seconds=0.05), worker, online=lambda: False)

    def invoke(self, action):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--db", str(self.db), action]), 0)
        return json.loads(output.getvalue())

    def test_missing_agent_never_claims(self):
        with self.make(FakeWorker(False)) as service:
            with self.assertRaises(AgentUnavailable):
                service.start()
        with StateStore(self.db) as state:
            self.assertEqual(state.missions(), ())

    def test_idle_daemon_stays_alive_for_new_roadmap(self):
        worker = FakeWorker()
        with self.make(worker) as service:
            service.start()
            self.assertEqual(service.run(max_ticks=2).action, "COMPLETE")
            with StateStore(self.db) as state:
                state.add_missions([Mission("a", REPO)])
            self.assertEqual(service.run(max_ticks=1).action, "QA_PENDING")
            self.assertEqual(worker.calls, ["a"])

    def test_cli_pause_resume_and_stop(self):
        with StateStore(self.db) as state:
            state.add_missions([Mission("a", REPO)])
        worker = FakeWorker()
        with self.make(worker) as service:
            service.start()
            self.assertTrue(self.invoke("pause")["paused"])
            self.assertEqual(service.run(max_ticks=1).action, "WAIT")
            self.assertFalse(self.invoke("resume")["paused"])
            self.assertEqual(service.run(max_ticks=1).action, "QA_PENDING")
            self.assertEqual(worker.calls, ["a"])
            self.assertTrue(self.invoke("stop")["stop_requested"])
            self.assertEqual(service.run(max_ticks=1).action, "STOPPED")

    def test_paused_service_does_not_probe_local_model(self):
        with StateStore(self.db) as state:
            state.add_missions([Mission("a", REPO)])
        worker = FakeWorker()
        with self.make(worker) as service:
            service.start()
            with StateStore(self.db) as state:
                state.set_paused(True)
            with patch.object(worker, "ready",
                              side_effect=AssertionError("Do not probe while paused")) as probe:
                self.assertEqual(service.run(max_ticks=1).action, "WAIT")
                probe.assert_not_called()
            self.assertEqual(worker.calls, [])

    def test_sqlite_claim_respects_durable_stop(self):
        with StateStore(self.db) as state:
            state.add_missions([Mission("a", REPO)])
            state.db.execute(
                "INSERT INTO settings(key,value) VALUES('stop_requested','1')")
            self.assertEqual(state.claim_next(online=False).action, "WAIT")
            self.assertEqual(state.missions()[0].state, "READY")
            state.db.execute(
                "UPDATE settings SET value='0' WHERE key='stop_requested'")
            self.assertEqual(state.claim_next(online=False).mission_id, "a")

    def test_boot_paused_skips_agent_probes_until_resume(self):
        with StateStore(self.db) as state:
            state.add_missions([Mission("a", REPO)])
            state.set_paused(True)
        worker = FakeWorker()
        with self.make(worker) as service:
            with patch.object(worker, "ready",
                              side_effect=AssertionError("Startup probed a paused model")) as probe:
                self.assertEqual(service.start(), ())
                self.assertEqual(service.run(max_ticks=1).action, "WAIT")
                probe.assert_not_called()
            with StateStore(self.db) as state:
                state.set_paused(False)
            self.assertEqual(service.run(max_ticks=1).action, "QA_PENDING")
            self.assertEqual(worker.calls, ["a"])

    def test_boot_stopped_does_not_require_running_model(self):
        with StateStore(self.db) as state:
            state.add_missions([Mission("a", REPO)])
            state.db.execute(
                "INSERT INTO settings(key,value) VALUES('stop_requested','1')")
        worker = FakeWorker(False)
        with self.make(worker) as service:
            self.assertEqual(service.start(), ())
            self.assertEqual(service.run(max_ticks=1).action, "STOPPED")
            self.assertEqual(worker.calls, [])
        with StateStore(self.db) as state:
            self.assertEqual(state.missions()[0].state, "READY")

    def test_health_loss_prevents_new_dispatch(self):
        with StateStore(self.db) as state:
            state.add_missions([Mission("a", REPO)])
        worker = FakeWorker()
        with self.make(worker) as service:
            service.start()
            worker.healthy = False
            self.assertEqual(service.run(max_ticks=1).action, "AGENT_UNAVAILABLE")
            self.assertEqual(worker.calls, [])
            worker.healthy = True
            self.assertEqual(service.run(max_ticks=1).action, "QA_PENDING")

    def test_cooperative_shutdown_persists(self):
        with self.make(FakeWorker()) as service:
            service.start()
            service.request_shutdown()
            self.assertEqual(service.run().action, "STOPPED")
        self.assertTrue(self.invoke("status")["stop_requested"])
        self.assertFalse(self.invoke("clear-stop")["stop_requested"])

    def test_lost_sqlite_fence_during_qa_repair_stops_without_retry(self):
        from holding.state_store import StoreError
        from unittest.mock import Mock
        with self.make(FakeWorker()) as service:
            service.start()
            service._qa_repair = Mock()
            service._qa_repair.requeue_one.side_effect = StoreError("DAEMON_LEASE_LOST")
            outcome = service.run(max_ticks=1)
            self.assertEqual(outcome.action, "LEASE_LOST")
            service._qa_repair.requeue_one.assert_called_once()

    def test_lost_lease_during_github_probe_stops_without_dispatch(self):
        from holding.daemon_lease import LeaseError
        from unittest.mock import Mock
        worker = FakeWorker()
        with self.make(worker) as service:
            service.start()
            service._github_health = Mock()
            service._github_health.refresh.side_effect = LeaseError("lost")
            outcome = service.run(max_ticks=1)
            self.assertEqual(outcome.action, "LEASE_LOST")
            self.assertEqual(worker.calls, [])

    def test_broken_gui_teardown_cannot_strand_engine_lease(self):
        from unittest.mock import Mock
        first = self.make(FakeWorker())
        first.start()
        viewer = Mock()
        viewer.close.side_effect = OSError("viewer teardown failed")
        listener = Mock()
        first._dashboard_session = viewer
        first._dashboard_ipc = listener
        with self.assertRaisesRegex(OSError, "viewer teardown"):
            first.close()
        listener.close.assert_called_once()
        self.assertIsNone(first._dashboard_session)
        self.assertIsNone(first._dashboard_ipc)
        self.assertFalse(first._started)
        # Another daemon must be able to acquire the now-released lease.
        with self.make(FakeWorker()) as successor:
            successor.start()

    def test_broken_dashboard_listener_cannot_strand_engine_lease(self):
        from unittest.mock import Mock
        first = self.make(FakeWorker())
        first.start()
        listener = Mock()
        listener.close.side_effect = OSError("listener teardown failed")
        first._dashboard_ipc = listener
        with self.assertRaisesRegex(OSError, "listener teardown"):
            first.close()
        listener.close.assert_called_once()
        self.assertFalse(first._started)
        with self.make(FakeWorker()) as successor:
            successor.start()

    def test_no_second_headless_writer(self):
        with self.make(FakeWorker()) as primary:
            primary.start()
            with self.make(FakeWorker()) as secondary:
                with self.assertRaises(Exception):
                    secondary.start()

    def test_invalid_config_or_untrusted_worker(self):
        with self.assertRaises(ValueError):
            ServiceSettings(Path("relative.sqlite"))
        with self.assertRaises(AgentUnavailable):
            self.make(lambda _: SHA)


if __name__ == "__main__":
    unittest.main()
