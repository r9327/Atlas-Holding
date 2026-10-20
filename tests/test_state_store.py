import tempfile
import unittest
from pathlib import Path
from holding.autonomy import Mission, StandingGrant
from holding.state_store import StateStore, StoreError

REPO = 'r9327/Dof-Atlas'
SHA = 'a' * 40

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'holding.sqlite'
        self.store = StateStore(self.path)
        self.addCleanup(self.store.close)
        # Track the current handle too: restart tests replace self.store.
        self.addCleanup(lambda: self.store.close())
        self.store.grant_for_onboarding(StandingGrant(REPO, frozenset({'code'}), 1000000))

    def claim(self):
        return self.store.claim_next(online=False, now_epoch=12345)

    def test_real_sqlite_and_atomic_claim(self):
        self.store.add_missions([Mission('a', REPO)])
        self.assertEqual(self.claim().mission_id, 'a')
        self.assertEqual(self.claim().action, 'WAIT')
        self.assertEqual(self.store.missions()[0].state, 'RUNNING')
        self.assertTrue(self.store.integrity_check())

    def test_restart_quarantines_unfinished_writer(self):
        self.store.add_missions([Mission('a', REPO)])
        self.claim()
        self.assertEqual(self.store.quarantine_interrupted(), ('a',))
        self.assertEqual(self.claim().action, 'WAIT')
        with self.assertRaises(StoreError):
            self.store.acknowledge_recovery('a', safe_worktree_confirmed=False)
        self.store.acknowledge_recovery('a', safe_worktree_confirmed=True)
        self.assertEqual(self.claim().mission_id, 'a')

    def test_durable_pause_and_reopen(self):
        self.store.add_missions([Mission('a', REPO)])
        self.store.set_paused(True)
        self.assertEqual(self.claim().action, 'WAIT')
        self.store.close()
        self.store = StateStore(self.path)
        self.assertEqual(self.claim().action, 'WAIT')
        self.store.set_paused(False)
        self.assertEqual(self.claim().mission_id, 'a')

    def test_persisted_grant_revoke(self):
        self.store.add_missions([Mission('a', REPO)])
        self.store.revoke(REPO)
        self.assertEqual(self.claim().action, 'REQUIRES_DECISION')
        self.store.close()
        self.store = StateStore(self.path)
        self.assertEqual(self.claim().action, 'REQUIRES_DECISION')

    def test_text_verdict_cannot_mark_done(self):
        self.store.add_missions([Mission('a', REPO)])
        self.claim()
        self.store.checkpoint('a', SHA)
        with self.assertRaises(StoreError):
            self.store.register_test_verdict('a', candidate_sha=SHA,
                evidence_sha=SHA, verdict='PASS', runner='doctor_adapter',
                artifact_id='forged')
        self.assertEqual(self.store.missions()[0].state, 'QA_PENDING')

    def test_untrusted_runner_cannot_publish_pass(self):
        self.store.add_missions([Mission('a', REPO)])
        self.claim()
        self.store.checkpoint('a', SHA)
        with self.assertRaises(StoreError):
            self.store.register_test_verdict('a', candidate_sha=SHA, evidence_sha=SHA,
                                            verdict='PASS', runner='model', artifact_id='fake')

    def test_untrusted_verdict_does_not_unblock_dependencies(self):
        self.store.add_missions([Mission('a', REPO), Mission('b', REPO, dependencies=('a',))])
        self.assertEqual(self.claim().mission_id, 'a')
        self.store.checkpoint('a', SHA)
        with self.assertRaises(StoreError):
            self.store.register_test_verdict('a', candidate_sha=SHA,
                evidence_sha=SHA, verdict='PASS', runner='doctor_adapter', artifact_id='ok')
        self.assertEqual(self.claim().action, 'WAIT')

    def test_no_duplicate_import_and_rollback(self):
        with self.assertRaises(StoreError):
            self.store.add_missions([Mission('x', REPO), Mission('x', REPO)])
        self.assertEqual(self.store.missions(), ())
        self.assertEqual(self.store.events()[0]['event'], 'GRANT_CHANGED')

    def test_missing_dependency_import_refused(self):
        with self.assertRaises(StoreError):
            self.store.add_missions([Mission('x', REPO, dependencies=('absent',))])
        self.assertEqual(self.store.missions(), ())

    def test_cycle_import_refused(self):
        with self.assertRaises(StoreError):
            self.store.add_missions([Mission('x', REPO, dependencies=('y',)),
                                     Mission('y', REPO, dependencies=('x',))])

    def test_illegal_transition_refused(self):
        self.store.add_missions([Mission('a', REPO)])
        with self.assertRaises(StoreError):
            self.store.checkpoint('a', SHA)

    def test_fail_and_retry_then_blocked(self):
        self.store.add_missions([Mission('a', REPO)])
        for i in range(3):
            self.claim()
            self.store.fail_running('a', 'test fail', max_attempts=3)
        self.assertEqual(self.store.missions()[0].state, 'BLOCKED')
        self.assertEqual(self.claim().action, 'BLOCKED')

    def test_real_checkpoint_resets_consecutive_no_progress_failures(self):
        self.store.add_missions([Mission('a', REPO)])
        self.assertEqual(self.claim().mission_id, 'a')
        self.store.fail_running('a', 'transient failure')
        self.assertEqual(self.store.missions()[0].failures_without_progress, 1)
        self.assertEqual(self.claim().mission_id, 'a')
        self.store.checkpoint('a', SHA)
        self.assertEqual(self.store.missions()[0].failures_without_progress, 0)
        self.assertEqual(self.store.missions()[0].state, 'QA_PENDING')

    def test_repair_requires_new_candidate_sha_without_clearing_failure_budget(self):
        self.store.add_missions([Mission('a', REPO)])
        self.assertEqual(self.claim().mission_id, 'a')
        self.store.checkpoint('a', SHA)
        with self.store._transaction():
            self.store._transition('a', 'QA_PENDING', 'BLOCKED', 'test-only signed fail')
            self.store._transition('a', 'BLOCKED', 'READY', 'test-only repair')
        self.assertEqual(self.claim().mission_id, 'a')
        with self.assertRaisesRegex(StoreError, 'UNCHANGED_CANDIDATE_SHA'):
            self.store.checkpoint('a', SHA)
        self.assertEqual(self.store.missions()[0].state, 'RUNNING')
        self.assertEqual(self.store.missions()[0].candidate_sha, SHA)
        self.store.fail_running('a', 'unchanged candidate')
        self.assertEqual(self.store.missions()[0].failures_without_progress, 1)

    def test_independent_task_continues_when_other_blocked(self):
        self.store.add_missions([Mission('z', REPO, priority=10), Mission('a', REPO)])
        for i in range(3):
            self.claim()
            self.store.fail_running('z', 'not progressing')
        self.assertEqual(self.claim().mission_id, 'a')

    def test_two_connections_cannot_claim_same_task(self):
        self.store.add_missions([Mission('a', REPO)])
        with StateStore(self.path) as second:
            self.assertEqual(self.claim().mission_id, 'a')
            self.assertEqual(second.claim_next(online=False, now_epoch=12345).action, 'WAIT')

    def test_credential_scope_cannot_cross_repo(self):
        self.store.add_missions([Mission('a', 'r9327/other')])
        self.assertEqual(self.claim().action, 'REQUIRES_DECISION')

    def test_event_log_and_recovery(self):
        self.store.add_missions([Mission('a', REPO)])
        self.claim()
        self.store.quarantine_interrupted()
        names = [e['event'] for e in self.store.events()]
        self.assertIn('MISSION_CLAIMED', names)
        self.assertIn('RUNNING_TO_RECOVERY_REQUIRED', names)

if __name__ == '__main__':
    unittest.main()
