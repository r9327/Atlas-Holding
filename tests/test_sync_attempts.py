import unittest

from holding.github_full_evidence import GitHubEvidenceError
from holding.sync_attempts import (
    AttemptAction,
    AttemptStatus,
    OperationRecord,
    decide_attempt,
)


REPO = "r9327/Dof-Atlas"
BRANCH = "holding/lot-1"
SHA = "a" * 40


def record(attempt, status, *, retryable=True, sha=SHA):
    return OperationRecord(
        "github.ci_trigger", REPO, BRANCH, sha, attempt, status, retryable,
    )


def decide(records, **overrides):
    values = dict(
        operation="github.ci_trigger",
        repository=REPO,
        branch=BRANCH,
        candidate_sha=SHA,
    )
    values.update(overrides)
    return decide_attempt(records, **values)


class SyncAttemptTests(unittest.TestCase):
    def test_new_exact_operation_starts_once_with_stable_key(self):
        first = decide([])
        second = decide([])
        self.assertEqual(first.action, AttemptAction.START)
        self.assertEqual(first.next_attempt, 1)
        self.assertEqual(first.idempotency_key, second.idempotency_key)
        self.assertFalse(first.executes_action)

    def test_pending_operation_waits_instead_of_relaunching(self):
        decision = decide([record(1, AttemptStatus.PENDING)])
        self.assertEqual(decision.action, AttemptAction.WAIT)
        self.assertIsNone(decision.next_attempt)

    def test_successful_operation_is_not_replayed(self):
        decision = decide([record(1, AttemptStatus.SUCCEEDED)])
        self.assertEqual(decision.action, AttemptAction.ALREADY_DONE)

    def test_only_retryable_failure_advances_attempt(self):
        decision = decide([record(1, AttemptStatus.FAILED)])
        self.assertEqual((decision.action, decision.next_attempt), (AttemptAction.RETRY, 2))
        blocked = decide([record(1, AttemptStatus.FAILED, retryable=False)])
        self.assertEqual(blocked.action, AttemptAction.BLOCKED)

    def test_retry_count_is_bounded(self):
        rows = [record(number, AttemptStatus.FAILED) for number in (1, 2, 3)]
        self.assertEqual(decide(rows).action, AttemptAction.BLOCKED)

    def test_old_sha_success_is_never_reused_for_new_head(self):
        old = record(1, AttemptStatus.SUCCEEDED, sha="b" * 40)
        decision = decide([old])
        self.assertEqual((decision.action, decision.next_attempt), (AttemptAction.START, 1))

    def test_operation_and_branch_change_the_idempotency_key(self):
        original = decide([]).idempotency_key
        operation = decide([], operation="github.pr_ready").idempotency_key
        branch = decide([], branch="holding/lot-2").idempotency_key
        self.assertEqual(len({original, operation, branch}), 3)

    def test_duplicate_or_gapped_attempts_fail_closed(self):
        with self.assertRaisesRegex(GitHubEvidenceError, "Conflicting"):
            decide([
                record(1, AttemptStatus.FAILED),
                record(1, AttemptStatus.FAILED),
            ])
        with self.assertRaisesRegex(GitHubEvidenceError, "gaps"):
            decide([record(2, AttemptStatus.FAILED)])

    def test_no_attempt_may_follow_pending_success_or_block(self):
        for terminal in (
                AttemptStatus.PENDING,
                AttemptStatus.SUCCEEDED,
                AttemptStatus.BLOCKED):
            with self.subTest(terminal=terminal), self.assertRaisesRegex(
                    GitHubEvidenceError, "terminal"):
                decide([
                    record(1, terminal),
                    record(2, AttemptStatus.FAILED),
                ])
        with self.assertRaisesRegex(GitHubEvidenceError, "terminal"):
            decide([
                record(1, AttemptStatus.FAILED, retryable=False),
                record(2, AttemptStatus.FAILED),
            ])

    def test_journal_size_is_bounded_before_decision(self):
        with self.assertRaisesRegex(GitHubEvidenceError, "exceeds limit"):
            decide((record(1, AttemptStatus.PENDING) for _ in range(10_001)))

    def test_malformed_unrelated_record_is_not_ignored(self):
        malformed = OperationRecord(
            "github.ci_trigger", REPO, BRANCH, "short", 1,
            AttemptStatus.SUCCEEDED, False,
        )
        with self.assertRaisesRegex(GitHubEvidenceError, "identity"):
            decide([malformed])


if __name__ == "__main__":
    unittest.main()
