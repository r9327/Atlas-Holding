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
