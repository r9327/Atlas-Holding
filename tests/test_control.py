import tempfile
from pathlib import Path
import time
import unittest

from holding.autonomy import Mission, StandingGrant
from holding.control import ControlPlane
from holding.engine import HoldingEngine
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite"
        with StateStore(self.path) as store:
            store.add_missions([Mission("a", REPO)])
            store.grant_for_onboarding(StandingGrant(
                REPO, frozenset({"code"}), int(time.time()) + 3600))
            store._event(None, "TEST_SETUP_COMPLETE")

    def test_external_pause_and_resume(self):
        with HoldingEngine(self.path, lambda m: SHA, online=lambda: False) as engine:
            engine.start()
            with ControlPlane(self.path) as control:
                control.pause()
                self.assertTrue(control.snapshot().paused)
                self.assertEqual(engine.tick().action, "WAIT")
                control.resume()
                self.assertEqual(engine.tick().action, "QA_PENDING")

    def test_external_stop_prevents_dispatch(self):
        with HoldingEngine(self.path, lambda m: SHA, online=lambda: False) as engine:
            engine.start()
            with ControlPlane(self.path) as control:
                control.request_stop()
                self.assertTrue(control.snapshot().stop_requested)
                self.assertEqual(engine.tick().action, "STOPPED")
                control.clear_stop_for_restart()
                self.assertEqual(engine.tick().action, "QA_PENDING")

    def test_snapshot_reads_real_state_and_events(self):
        with ControlPlane(self.path) as control:
            snap = control.snapshot()
            self.assertEqual(snap.mission_counts, {"READY": 1})
            self.assertFalse(snap.lease_valid)
            self.assertGreater(snap.last_event_seq, 0)

    def test_live_lease_is_reported_as_lease_only(self):
        with HoldingEngine(self.path, lambda m: SHA, online=lambda: False) as engine:
            engine.start()
            with ControlPlane(self.path) as control:
                self.assertTrue(control.snapshot().lease_valid)


if __name__ == "__main__":
    unittest.main()
