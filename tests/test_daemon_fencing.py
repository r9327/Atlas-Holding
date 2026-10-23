"""Ensure expired daemon cannot overwrite the replacement daemon's state."""
import tempfile
import time
import unittest
from pathlib import Path

from holding.autonomy import Mission, StandingGrant
from holding.daemon_lease import DaemonLease
from holding.engine import HoldingEngine
from holding.state_store import StateStore, StoreError

REPO = 'r9327/Dof-Atlas'


class FenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'state.sqlite'
        with StateStore(self.path) as db:
            db.add_missions([Mission('x', REPO)])
            db.grant_for_onboarding(StandingGrant(REPO, frozenset({'code'}), int(time.time())+120))

    def test_transaction_fence_rejects_stale_writes(self):
        with StateStore(self.path) as db:
            first = DaemonLease(self.path, owner='first', heartbeat=False)
            replacement = DaemonLease(self.path, owner='replacement', heartbeat=False)
            try:
                first.acquire()
                self.assertEqual(db.claim_next(online=False,lease_owner='first').mission_id,'x')
                db.db.execute('UPDATE daemon_lease SET expires_epoch=0 WHERE singleton=1')
                replacement.acquire()
                for action in (
                    lambda: db.checkpoint('x','a'*40,lease_owner='first'),
                    lambda: db.fail_running('x','crash',lease_owner='first'),
                    lambda: db.quarantine_interrupted(lease_owner='first'),
                ):
                    with self.assertRaises(StoreError) as error:
                        action()
                    self.assertEqual(str(error.exception),'DAEMON_LEASE_LOST')
                self.assertEqual(db.missions()[0].state,'RUNNING')
            finally:
                first.release()
                replacement.release()

    def test_failed_worker_cannot_reset_quarantine_after_takeover(self):
        first = None
        replacement = None
        def failed_worker(job):
            with first.store._transaction():
                first.store.db.execute('UPDATE daemon_lease SET expires_epoch=0 WHERE singleton=1')
            replacement.start()
            raise OSError('do not leak environment secrets')
        first = HoldingEngine(self.path, failed_worker, online=lambda:False)
        replacement = HoldingEngine(self.path, lambda _: 'a'*40, online=lambda:False)
        with first, replacement:
            first.start()
            self.assertEqual(first.tick().action,'LEASE_LOST')
            self.assertEqual(replacement.store.missions()[0].state,'RECOVERY_REQUIRED')


if __name__ == '__main__':
    unittest.main()
