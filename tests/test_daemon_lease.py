import tempfile
from pathlib import Path
import unittest

from holding.daemon_lease import DaemonLease, LeaseError
from holding.state_store import StateStore


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "holding.sqlite"
        with StateStore(self.path):
            pass

    def make(self, owner):
        return DaemonLease(self.path, ttl_seconds=12, heartbeat=False, owner=owner)

    def test_second_daemon_cannot_quarantine_live_writer(self):
        a, b = self.make("first"), self.make("second")
        try:
            self.assertEqual(a.acquire(), "NEW")
            with self.assertRaises(LeaseError):
                b.acquire()
            a.assert_owned()
        finally:
            a.release()
            b.release()

    def test_graceful_close_allows_relaunch(self):
        a = self.make("first")
        a.acquire()
        a.release()
        b = self.make("second")
        try:
            self.assertEqual(b.acquire(), "NEW")
        finally:
            b.release()

    def test_expired_owner_can_be_taken_over(self):
        a, b = self.make("first"), self.make("second")
        try:
            a.acquire(now_epoch=100)
            with self.assertRaises(LeaseError):
                b.acquire(now_epoch=101)
            self.assertEqual(b.acquire(now_epoch=112), "TAKEOVER")
            with self.assertRaises(LeaseError):
                a.assert_owned(now_epoch=112)
            b.assert_owned(now_epoch=112)
        finally:
            a.release()
            b.release()

    def test_renew_rejects_expired_ownership(self):
        a = self.make("first")
        try:
            a.acquire(now_epoch=100)
            with self.assertRaises(LeaseError):
                a.renew(now_epoch=112)
        finally:
            a.release()

    def test_heartbeat_extends_only_current_owner(self):
        a = self.make("first")
        try:
            a.acquire(now_epoch=100)
            a.renew(now_epoch=105)
            a.assert_owned(now_epoch=116)
            with self.assertRaises(LeaseError):
                a.assert_owned(now_epoch=117)
        finally:
            a.release()


if __name__ == "__main__":
    unittest.main()
