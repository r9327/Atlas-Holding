import tempfile
from pathlib import Path
import time
import unittest

from holding.autonomy import Mission, StandingGrant
from holding.daemon_lease import LeaseError
from holding.engine import HoldingEngine
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40


class EngineLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.db"
        with StateStore(self.path) as db:
            db.add_missions([Mission("one", REPO)])
            db.grant_for_onboarding(StandingGrant(REPO, frozenset({"code"}), int(time.time()) + 300))

    def engine(self, worker=None):
        return HoldingEngine(self.path, worker or (lambda mission: SHA), online=lambda: False)

    def test_second_engine_cannot_quarantine_live_writer(self):
        with self.engine() as first:
            first.start()
            first.store.claim_next(online=False)
            with self.engine() as second:
                with self.assertRaises(LeaseError):
                    second.start()
            self.assertEqual(first.store.missions()[0].state, "RUNNING")

    def test_stale_lease_quarantines_only_on_takeover(self):
        first, second = self.engine(), self.engine()
        try:
            first.start()
            first.store.claim_next(online=False)
            with first.store._transaction():
                first.store.db.execute("UPDATE daemon_lease SET expires_epoch=0 WHERE singleton=1")
            self.assertEqual(second.start(), ("one",))
            self.assertEqual(second.store.missions()[0].state, "RECOVERY_REQUIRED")
            with self.assertRaises(LeaseError):
                first.lease.assert_owned()
        finally:
            first.close()
            second.close()

    def test_stop_persists_through_restart(self):
        with self.engine() as first:
            first.start()
            first.stop()
        with self.engine() as second:
            second.start()
            self.assertEqual(second.tick().action, "STOPPED")
            second.reset_stop()
            self.assertEqual(second.tick().action, "QA_PENDING")

    def test_lost_lease_cannot_checkpoint(self):
        engine = None
        def worker(_mission):
            with engine.store._transaction():
                engine.store.db.execute("UPDATE daemon_lease SET expires_epoch=0 WHERE singleton=1")
            return SHA
        engine = self.engine(worker)
        with engine:
            engine.start()
            self.assertEqual(engine.tick().action, "LEASE_LOST")
            self.assertEqual(engine.store.missions()[0].state, "RUNNING")


if __name__ == "__main__":
    unittest.main()
