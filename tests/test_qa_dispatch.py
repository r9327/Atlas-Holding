"""Unattended QA must be idempotent, bounded and safe across crashes."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from holding.autonomy import Mission, StandingGrant
from holding.qa_dispatch import ScopedQaDispatcher
from holding.qa_scoped import ScopedDoctorRunner, ScopedQaError, ScopedQaResult
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40
NEXT_SHA = "c" * 40
BASE = "b" * 40


class _Git:
    repository = REPO


class ScopedQaDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = StateStore(Path(self.temp.name) / "state.sqlite")
        self.addCleanup(self.db.close)
        self.db.add_missions([Mission("first", REPO)])
        self.db.grant_for_onboarding(StandingGrant(
            REPO, frozenset({"code", "local_test"}), int(time.time()) + 500))
        self.assertEqual(self.db.claim_next(online=False).mission_id, "first")
        self.db.checkpoint("first", SHA)
        self.runner = ScopedDoctorRunner(_Git(), Path(sys.executable).resolve())
        self.dispatch = ScopedQaDispatcher(self.db, self.runner)
        self.now = int(time.time())

    def advice(self, sha=SHA):
        return ScopedQaResult(sha, BASE, "SCOPED_TESTS_PASS",
                              ("tests.test_code",), "d" * 64)

    def test_once_per_exact_sha_and_no_done(self):
        with patch.object(self.runner, "run", return_value=self.advice()) as run:
            first = self.dispatch.tick(now_epoch=self.now)
            second = self.dispatch.tick(now_epoch=self.now)
        self.assertEqual(first.action, "SCOPED_TESTS_PASS")
        self.assertEqual(second.action, "IDLE")
        run.assert_called_once_with("first", SHA)
        self.assertEqual(self.db.missions()[0].state, "QA_PENDING")
        self.assertEqual(self.db.missions()[0].evidence_sha, "")

    def test_second_dispatcher_cannot_claim_in_flight_same_sha(self):
        second = ScopedQaDispatcher(self.db, self.runner)
        def checking_run(*args):
            self.assertEqual(second.tick(now_epoch=self.now).action, "IDLE")
            return self.advice()
        with patch.object(self.runner, "run", side_effect=checking_run):
            self.assertEqual(self.dispatch.tick(now_epoch=self.now).action,
                             "SCOPED_TESTS_PASS")

    def test_exceptions_backoff_and_block_after_three_attempts(self):
        with patch.object(self.runner, "run",
                          side_effect=ScopedQaError("broken tool")) as run:
            self.assertEqual(self.dispatch.tick(now_epoch=self.now).action,
                             "RETRY_LATER")
            self.assertEqual(self.dispatch.tick(now_epoch=self.now).action, "IDLE")
            self.assertEqual(self.dispatch.tick(now_epoch=self.now + 30).action,
                             "RETRY_LATER")
            self.assertEqual(self.dispatch.tick(now_epoch=self.now + 90).action,
                             "RUNNER_BLOCKED")
            self.assertEqual(self.dispatch.tick(now_epoch=self.now + 300).action,
                             "IDLE")
        self.assertEqual(run.call_count, 3)
        status = self.db.db.execute(
            "SELECT status FROM scoped_qa_advisories WHERE mission_id='first'"
        ).fetchone()[0]
        self.assertEqual(status, "SCOPED_RUNNER_ERROR")
        self.assertEqual(self.db.missions()[0].state, "QA_PENDING")

    def test_new_candidate_sha_resets_qa_dedupe(self):
        with patch.object(self.runner, "run", return_value=self.advice()):
            self.assertEqual(self.dispatch.tick(now_epoch=self.now).action,
                             "SCOPED_TESTS_PASS")
        self.db.db.execute(
            "UPDATE missions SET candidate_sha=? WHERE id='first'", (NEXT_SHA,))
        with patch.object(self.runner, "run", return_value=self.advice(NEXT_SHA)):
            self.assertEqual(self.dispatch.tick(now_epoch=self.now + 1).action,
                             "SCOPED_TESTS_PASS")
        advisory = self.db.db.execute(
            "SELECT candidate_sha FROM scoped_qa_advisories WHERE mission_id='first'"
        ).fetchone()[0]
        self.assertEqual(advisory, NEXT_SHA)

    def test_invalid_sha_does_not_starve_other_valid_pending_work(self):
        self.db.add_missions([Mission("second", REPO)])
        self.db.db.execute("UPDATE missions SET state='QA_PENDING',"
                           "candidate_sha=? WHERE id='second'", ("z" * 40,))
        with patch.object(self.runner, "run", return_value=self.advice()) as run:
            self.assertEqual(self.dispatch.tick(now_epoch=self.now).action,
                             "SCOPED_TESTS_PASS")
            self.assertEqual(self.dispatch.tick(now_epoch=self.now).action,
                             "IDLE")
        run.assert_called_once_with("first", SHA)

    def test_git_inspection_error_is_bounded_retry(self):
        from holding.git_worktree import GitSafetyError
        with patch.object(self.runner, "run",
                          side_effect=GitSafetyError("Git unavailable")):
            self.assertEqual(self.dispatch.tick(now_epoch=self.now).action,
                             "RETRY_LATER")
        row = self.db.db.execute(
            "SELECT attempts,next_epoch FROM scoped_qa_retries "
            "WHERE mission_id='first'").fetchone()
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["next_epoch"], self.now + 30)

    def test_no_scoped_grant_never_launches_test_process(self):
        self.db.revoke(REPO)
        with patch.object(self.runner, "run") as run:
            self.assertEqual(self.dispatch.tick(now_epoch=self.now).action,
                             "WAIT_FOR_GRANT")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
