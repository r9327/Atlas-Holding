"""Real Git integration tests for exactly-once operation recording and recovery."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

from holding.autonomy import Mission, StandingGrant
from holding.daemon_lease import DaemonLease
from holding.executor import AuthorizedGit
from holding.git_worktree import GitWorktrees, GitSafetyError
from holding.state_store import StateStore, StoreError

REPO = 'r9327/Dof-Atlas'


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], text=True,
                          capture_output=True, check=True).stdout.strip()


class GitJournalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        checkout = self.root / 'canonical'
        checkout.mkdir()
        git(checkout, 'init', '-b', 'main')
        git(checkout, 'config', 'user.name', 'Test')
        git(checkout, 'config', 'user.email', 'test@example.invalid')
        (checkout / 'README.md').write_text('initial')
        git(checkout, 'add', 'README.md')
        git(checkout, 'commit', '-m', 'init')
        git(checkout, 'remote', 'add', 'origin', 'https://github.com/r9327/Dof-Atlas.git')
        self.dbpath = self.root / 'tasks.sqlite'
        self.store = StateStore(self.dbpath)
        self.addCleanup(self.store.close)
        self.store.add_missions([Mission('job', REPO)])
        self.store.grant_for_onboarding(StandingGrant(
            REPO, frozenset({'code', 'commit'}), int(time.time()) + 1000))
        self.lease = DaemonLease(self.dbpath, heartbeat=False)
        self.lease.acquire()
        self.addCleanup(self.lease.release)
        self.git = GitWorktrees(checkout, self.root / 'jobs', REPO)
        self.actor = AuthorizedGit(self.store, self.git, self.lease)
        self.assertEqual(
            self.store.claim_next(online=False, lease_owner=self.lease.owner).mission_id,
            'job')

    def test_worktree_creation_is_idempotent(self):
        tree = self.actor.create('job')
        self.assertEqual(self.actor.create('job').path, tree.path)
        self.assertIn('holding/job', git(self.git.root, 'branch', '--list', 'holding/job'))
        self.assertEqual(self.actor.journal.get('job', 'worktree-create-v1').status, 'APPLIED')

    def test_main_advance_does_not_duplicate_existing_worktree(self):
        tree = self.actor.create('job')
        original = tree.base_sha
        (self.git.root / 'new-main.txt').write_text('external change')
        git(self.git.root, 'add', 'new-main.txt')
        git(self.git.root, 'commit', '-m', 'unrelated main update')
        self.assertNotEqual(git(self.git.root, 'rev-parse', 'HEAD'), original)
        resumed = self.actor.create('job')
        self.assertEqual(resumed.base_sha, original)
        self.assertEqual(resumed.path, tree.path)

    def test_commit_is_exactly_once_and_does_not_mark_done(self):
        tree = self.actor.create('job')
        (tree.path / 'feature.py').write_text('answer=42\n')
        sha = self.actor.commit('job', ('feature.py',), 'feat: tested', operation_key='patch-001')
        count = git(tree.path, 'rev-list', '--count', 'HEAD')
        self.assertEqual(sha, self.actor.commit('job', ('feature.py',), 'feat: tested',
                                                 operation_key='patch-001'))
        self.assertEqual(git(tree.path, 'rev-list', '--count', 'HEAD'), count)
        self.assertEqual(self.store.missions()[0].state, 'RUNNING')

    def test_crash_after_commit_before_recording_is_recovered(self):
        tree = self.actor.create('job')
        (tree.path / 'feature.py').write_text('tested=True\n')
        original = self.actor.journal.inspect_recovery
        with patch.object(self.actor.journal, 'inspect_recovery',
                          side_effect=RuntimeError('simulated power loss')):
            with self.assertRaises(RuntimeError):
                self.actor.commit('job', ('feature.py',), 'feat: tested',
                                  operation_key='crash-001')
        original_sha = git(tree.path, 'rev-parse', 'HEAD')
        self.assertEqual(self.actor.journal.get('job', 'crash-001').status, 'PREPARED')
        with self.assertRaises(GitSafetyError):
            self.actor.commit('job', ('feature.py',), 'feat: tested',
                              operation_key='crash-001')
        self.store.quarantine_interrupted(lease_owner=self.lease.owner)
        self.assertEqual(original('job', 'crash-001'), 'APPLIED')
        self.assertEqual(self.actor.journal.get('job', 'crash-001').result_sha, original_sha)
        self.assertEqual(git(tree.path, 'rev-parse', 'HEAD'), original_sha)
        self.assertEqual(self.store.missions()[0].state, 'RECOVERY_REQUIRED')

    def test_same_operation_id_cannot_change_intent(self):
        tree = self.actor.create('job')
        (tree.path / 'a.py').write_text('a=1')
        self.actor.commit('job', ('a.py',), 'feat: first', operation_key='stable-key')
        (tree.path / 'b.py').write_text('b=1')
        with self.assertRaises(GitSafetyError):
            self.actor.commit('job', ('b.py',), 'feat: other', operation_key='stable-key')

    def test_fake_trailer_with_wrong_paths_is_not_proof(self):
        tree = self.actor.create('job')
        initial = git(tree.path, 'rev-parse', 'HEAD')
        self.actor.journal.prepare('job', 'forge-001', 'COMMIT',
            intent={'paths': ['safe.py'], 'message': 'feat: safe'}, base_sha=initial)
        (tree.path / 'bad.py').write_text('bad=1')
        git(tree.path, 'add', 'bad.py')
        git(tree.path, 'commit', '-m', 'feat: safe\n\nHolding-Operation: forge-001')
        self.assertEqual(self.actor.journal.inspect_recovery('job', 'forge-001'),
                         'INSPECT_REQUIRED')
        self.assertEqual(self.actor.journal.get('job', 'forge-001').status,
                         'INSPECT_REQUIRED')

    def test_crash_after_worktree_create_does_not_duplicate(self):
        with patch.object(self.actor.journal, 'applied',
                          side_effect=RuntimeError('power loss')):
            with self.assertRaises(RuntimeError):
                self.actor.create('job')
        with self.assertRaises(GitSafetyError):
            self.actor.create('job')
        self.assertEqual(self.actor.journal.inspect_recovery('job', 'worktree-create-v1'),
                         'APPLIED')

    def test_missing_worktree_requires_inspection(self):
        self.actor.journal.prepare('job', 'missing-tree', 'CREATE',
            intent={'base': 'main'},
            base_sha=git(self.git.root, 'rev-parse', 'HEAD'))
        self.assertEqual(self.actor.journal.inspect_recovery('job', 'missing-tree'),
                         'INSPECT_REQUIRED')

    def test_recovery_survives_sqlite_reopen_and_process_replacement(self):
        tree = self.actor.create('job')
        (tree.path / 'feature.py').write_text('persisted=True\n')
        with patch.object(self.actor.journal, 'inspect_recovery',
                          side_effect=RuntimeError('simulated crash')):
            with self.assertRaises(RuntimeError):
                self.actor.commit('job', ('feature.py',), 'feat: recovery',
                                  operation_key='restart-1')
        saved_sha = git(tree.path, 'rev-parse', 'HEAD')
        self.lease.release()
        self.store.close()
        with StateStore(self.dbpath) as db:
            with DaemonLease(self.dbpath, heartbeat=False) as replacement:
                self.assertEqual(db.quarantine_interrupted(lease_owner=replacement.owner), ('job',))
                from holding.git_journal import GitJournal
                journal = GitJournal(db, self.git, lease_owner=replacement.owner)
                self.assertEqual(journal.pending('job')[0].status, 'PREPARED')
                self.assertEqual(journal.inspect_recovery('job', 'restart-1'), 'APPLIED')
                self.assertEqual(journal.pending('job'), ())
                self.assertEqual(journal.get('job', 'restart-1').result_sha, saved_sha)
                self.assertEqual(db.missions()[0].state, 'RECOVERY_REQUIRED')
                self.assertEqual(git(tree.path, 'rev-parse', 'HEAD'), saved_sha)

    def test_git_recovery_does_not_hold_sqlite_writer_lock(self):
        self.actor.create('job')
        journal = self.actor.journal
        def inspect_while_operator_changes_settings(old):
            # A distinct connection is allowed to commit an operator command
            # while potentially slow Git inspection is still in progress.
            with StateStore(self.dbpath) as control:
                control.set_paused(True)
                self.assertTrue(control.background_suspended())
            return 'APPLIED'
        with patch.object(journal, '_verify',
                          side_effect=inspect_while_operator_changes_settings):
            self.assertEqual(journal.inspect_recovery('job', 'worktree-create-v1'),
                             'APPLIED')
        self.assertTrue(self.store.background_suspended())

    def test_journal_mutated_while_inspecting_refuses_stale_result(self):
        self.actor.create('job')
        journal = self.actor.journal
        def concurrent_update(old):
            with StateStore(self.dbpath) as control:
                with control._transaction():
                    control.db.execute(
                        "UPDATE git_operations SET state='INSPECT_REQUIRED' "
                        "WHERE mission_id=? AND operation_key=?",
                        (old.mission_id, old.key))
            return 'APPLIED'
        with patch.object(journal, '_verify', side_effect=concurrent_update):
            with self.assertRaisesRegex(StoreError, 'changed during recovery'):
                journal.inspect_recovery('job', 'worktree-create-v1')

    def test_lost_lease_refuses_new_git_operation(self):
        self.lease.release()
        with self.assertRaises(Exception):
            self.actor.create('job')


if __name__ == '__main__':
    unittest.main()
