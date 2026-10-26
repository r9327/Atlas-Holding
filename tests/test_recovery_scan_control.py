"""Regression: one recovery scan per interruption, with explicit operator retry."""
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from holding.autonomy import Mission, StandingGrant
from holding.control import ControlPlane
from holding.git_worktree import GitWorktrees
from holding.recovery import RecoveryCoordinator
from holding.service import HoldingService, ServiceSettings
from holding.state_store import StateStore, StoreError

REPO = 'r9327/Dof-Atlas'


def git(directory, *args):
    return subprocess.run(['git', '-C', str(directory), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


class InertWorker:
    def ready(self):
        return True
    def __call__(self, mission):
        raise AssertionError('Quarantined mission must not execute')


class RecoveryScanTests(unittest.TestCase):
    def test_quarantine_not_reinspected_on_each_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = root / 'canonical'
            checkout.mkdir()
            git(checkout, 'init', '-b', 'main')
            git(checkout, 'config', 'user.name', 'Test')
            git(checkout, 'config', 'user.email', 'test@example.invalid')
            (checkout / 'README.md').write_text('baseline')
            git(checkout, 'add', 'README.md')
            git(checkout, 'commit', '-m', 'initial')
            git(checkout, 'remote', 'add', 'origin',
                'https://github.com/r9327/Dof-Atlas.git')
            state_path = root / 'state.sqlite'
            with StateStore(state_path) as db:
                db.add_missions([Mission('job', REPO)])
                db.grant_for_onboarding(StandingGrant(
                    REPO, frozenset({'code'}), int(time.time()) + 500))
            manager = GitWorktrees(checkout, root / 'worktrees', REPO)
            with HoldingService(ServiceSettings(state_path, idle_seconds=.05),
                                InertWorker(), online=lambda: False,
                                recovery_git=manager) as host:
                host.start()
                store = host.engine.store
                lease = host.engine.lease.owner
                self.assertEqual(store.claim_next(online=False, lease_owner=lease).mission_id,
                                 'job')
                store.quarantine_interrupted(lease_owner=lease)
                actual = RecoveryCoordinator.recover_all
                counts = []
                def tracked(coordinator):
                    counts.append(1)
                    return actual(coordinator)
                with patch.object(RecoveryCoordinator, 'recover_all', tracked):
                    host.run(max_ticks=1)
                    host.run(max_ticks=1)
                    self.assertEqual(len(counts), 1)
                    self.assertEqual(store.missions()[0].state, 'RECOVERY_REQUIRED')
                    with ControlPlane(state_path) as control:
                        control.request_recovery('job')
                    host.run(max_ticks=1)
                    self.assertEqual(len(counts), 2)
                    self.assertEqual(store.missions()[0].state, 'RECOVERY_REQUIRED')
                    self.assertEqual(store.missions()[0].failures_without_progress, 0)

    def test_operator_cannot_recover_nonquarantined_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = Path(tmp) / 'db.sqlite'
            with StateStore(dbpath) as db:
                db.add_missions([Mission('job', REPO)])
            with ControlPlane(dbpath) as control:
                with self.assertRaises(StoreError):
                    control.request_recovery('job')
                with self.assertRaises(StoreError):
                    control.request_recovery('missing')


if __name__ == '__main__':
    unittest.main()
