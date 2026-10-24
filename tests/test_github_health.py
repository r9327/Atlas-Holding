from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from holding.daemon_lease import DaemonLease
from holding.github_health import GitHubHealthMonitor
from holding.migrations import SchemaMigrator
from holding.state_store import StateStore


class Probe:
    def __init__(self, account="github:r9327", failure=False):
        self.account = account
        self.failure = failure
        self.calls = 0

    def observe_account(self):
        self.calls += 1
        if self.failure:
            raise RuntimeError("offline")
        return self.account


class RejectedProbe:
    def observe_account(self):
        error = RuntimeError("REJECTED")
        error.code = "REJECTED"
        raise error


class GitHubHealthMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root / "holding.sqlite")
        self.addCleanup(self.store.close)
        self.lease = DaemonLease(self.store.path, heartbeat=False)
        self.lease.acquire()
        self.addCleanup(self.lease.release)
        SchemaMigrator(self.store, backup_dir=self.root / "backups").ensure(
            lease_owner=self.lease.owner)
        self.now = 100

    def monitor(self, probe):
        return GitHubHealthMonitor(
            self.store, self.lease, probe,
            expected_account_id="github:r9327", clock=lambda: self.now)

    def test_two_authenticated_observations_are_required(self):
        probe = Probe()
        monitor = self.monitor(probe)
        self.assertEqual(monitor.refresh().status.value, "PROBING")
        self.assertFalse(monitor.online())
        self.now += 1
        self.assertEqual(monitor.refresh().status.value, "ONLINE")
        self.assertTrue(monitor.online())
        self.assertEqual(probe.calls, 2)

    def test_wrong_account_blocks_without_retry(self):
        probe = Probe("github:other")
        monitor = self.monitor(probe)
        state = monitor.refresh()
        self.assertEqual((state.status.value, state.reason),
                         ("BLOCKED", "AUTHORIZATION_REQUIRED"))
        self.now += 100
        self.assertEqual(monitor.refresh(), state)
        self.assertEqual(probe.calls, 1)

    def test_explicit_credential_rejection_blocks_without_retry(self):
        monitor = self.monitor(RejectedProbe())
        state = monitor.refresh()
        self.assertEqual((state.status.value, state.reason),
                         ("BLOCKED", "AUTHORIZATION_REQUIRED"))

    def test_network_failure_uses_bounded_backoff(self):
        probe = Probe(failure=True)
        monitor = self.monitor(probe)
        first = monitor.refresh()
        self.assertEqual(first.status.value, "DEGRADED")
        self.assertEqual(first.retry_not_before, 101)
        self.assertEqual(monitor.refresh(), first)
        self.assertEqual(probe.calls, 1)
        self.now = 101
        second = monitor.refresh()
        self.assertEqual(second.status.value, "OFFLINE")
        self.assertEqual(probe.calls, 2)

    def test_health_expires_without_fabricating_online(self):
        monitor = self.monitor(Probe())
        monitor.refresh(); self.now += 1; monitor.refresh()
        self.now += 61
        self.assertFalse(monitor.online())


if __name__ == "__main__":
    unittest.main()
