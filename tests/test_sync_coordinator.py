import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from holding.daemon_lease import DaemonLease
from holding.github_full_evidence import FullEvidence, GitHubEvidenceError
from holding.migrations import SchemaMigrator
from holding.standing_grant import authorize_operation, serialize_github_authorization
from holding.state_store import StateStore
from holding.sync_coordinator import (
    LiveGitHubState,
    MutationFailure,
    coordinate_github_mutation,
)


REPO = "r9327/Dof-Atlas"
BRANCH = "holding/lot-1"
SHA = "a" * 40
POLICY_SHA = "b" * 40
NOW = 1_800_000_000


class FakeControl:
    def __init__(self, operation, *, failure=None, unverifiable=False):
        self.operation = operation
        self.failure = failure
        self.unverifiable = unverifiable
        self.calls = 0
        self.live = LiveGitHubState(
            REPO, 42, BRANCH, SHA, "open",
            draft=operation == "github.pr_ready", complete=True,
            ci_run_id=10, ci_head_sha=SHA,
        )

    def observe_pr(self, repository, pr_number):
        return self.live

    def observe_full(self, repository, pr_number):
        return FullEvidence(REPO, 42, SHA, 77, 88)

    def mutate(self, intent, idempotency_key):
        self.calls += 1
        if self.failure:
            raise self.failure
        if self.unverifiable:
            return
        if self.operation == "github.pr_ready":
            self.live = LiveGitHubState(REPO, 42, BRANCH, SHA, "open", False, True)
        elif self.operation == "github.pr_draft":
            self.live = LiveGitHubState(REPO, 42, BRANCH, SHA, "open", True, True)
        elif self.operation == "github.ci_trigger":
            self.live = LiveGitHubState(
                REPO, 42, BRANCH, SHA, "open", False, True,
                ci_run_id=11, ci_head_sha=SHA,
            )
        elif self.operation == "github.merge":
            self.live = LiveGitHubState(
                REPO, 42, BRANCH, SHA, "closed", False, True, merged=True)


class SyncCoordinatorTests(unittest.TestCase):
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
        self.store.save_service_health(
            service="github", status="ONLINE", observed_at=NOW,
            consecutive_successes=2, consecutive_failures=0,
            retry_attempts=0, retry_not_before=None, reason=None,
            updated_epoch=NOW, lease_owner=self.lease.owner,
        )

    def authorization(self, operation):
        grant = {
            "schema_version": 1, "grant_id": "grant-1",
            "account_id": "github:r9327", "repository": REPO,
            "operations": [operation], "issued_at": NOW - 100,
            "expires_at": NOW + 3600, "revoked_at": None,
            "policy_sha": POLICY_SHA,
        }
        raw_grant = json.dumps(grant, sort_keys=True).encode()
        decision = authorize_operation(
            raw_grant,
            expected_record_sha256=hashlib.sha256(raw_grant).hexdigest(),
            expected_policy_sha=POLICY_SHA,
            observed_account_id="github:r9327", repository=REPO,
            operation=operation, now_epoch=NOW,
            branch=BRANCH, candidate_sha=SHA,
        )
        receipt = serialize_github_authorization(decision)
        return receipt, hashlib.sha256(receipt).hexdigest()

    def coordinate(self, operation, control, *, now_epoch=NOW):
        receipt, digest = self.authorization(operation)
        return coordinate_github_mutation(
            self.store, control, receipt,
            expected_receipt_sha256=digest,
            expected_account_id="github:r9327",
            expected_policy_sha=POLICY_SHA,
            repository=REPO, operation=operation, branch=BRANCH,
            candidate_sha=SHA, pr_number=42, now_epoch=now_epoch,
            lease_owner=self.lease.owner,
        )

    def test_ready_flows_from_grant_through_journal_and_revalidation(self):
        control = FakeControl("github.pr_ready")
        result = self.coordinate("github.pr_ready", control)
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertTrue(result.mutation_performed)
        self.assertFalse(result.certifies_quality)
        rows = self.store.sync_attempts(
            operation="github.pr_ready", repo=REPO, branch=BRANCH,
            candidate_sha=SHA,
        )
        self.assertEqual(rows[0]["status"], "SUCCEEDED")
        self.assertEqual(self.coordinate("github.pr_ready", control).status, "ALREADY_DONE")
        self.assertEqual(control.calls, 1)

    def test_stale_health_blocks_before_journal_or_mutation(self):
        self.store.save_service_health(
            service="github", status="OFFLINE", observed_at=NOW + 1,
            consecutive_successes=0, consecutive_failures=2,
            retry_attempts=2, retry_not_before=NOW + 3,
            reason="NETWORK_ERROR", updated_epoch=NOW + 1,
            lease_owner=self.lease.owner,
        )
        control = FakeControl("github.pr_ready")
        result = self.coordinate("github.pr_ready", control)
        self.assertEqual(result.status, "GITHUB_HEALTH_BLOCKED")
        self.assertEqual(control.calls, 0)

    def test_known_retryable_failure_is_persisted_for_next_attempt(self):
        control = FakeControl(
            "github.pr_ready",
            failure=MutationFailure(
                "TRANSIENT_UNAVAILABLE", retryable=True, outcome_known=True),
        )
        result = self.coordinate("github.pr_ready", control)
        self.assertEqual(result.status, "RETRY")
        control.failure = None
        self.assertEqual(
            self.coordinate("github.pr_ready", control, now_epoch=NOW + 1).status,
            "SUCCEEDED",
        )
        rows = self.store.sync_attempts(
            operation="github.pr_ready", repo=REPO, branch=BRANCH,
            candidate_sha=SHA,
        )
        self.assertEqual([row["status"] for row in rows], ["FAILED", "SUCCEEDED"])

    def test_unknown_outcome_stays_pending_and_is_never_replayed(self):
        control = FakeControl(
            "github.pr_ready",
            failure=MutationFailure(
                "TRANSIENT_UNAVAILABLE", retryable=True, outcome_known=False),
        )
        self.assertEqual(
            self.coordinate("github.pr_ready", control).status, "INSPECT_REQUIRED")
        self.assertEqual(self.coordinate("github.pr_ready", control).status, "WAIT")
        self.assertEqual(control.calls, 1)

    def test_unsanitized_client_exception_is_redacted_and_left_pending(self):
        control = FakeControl(
            "github.pr_ready", failure=RuntimeError("secret-token-value"))
        with self.assertRaises(GitHubEvidenceError) as raised:
            self.coordinate("github.pr_ready", control)
        self.assertNotIn("secret-token-value", str(raised.exception))
        self.assertEqual(self.coordinate("github.pr_ready", control).status, "WAIT")
        self.assertEqual(control.calls, 1)

    def test_unverified_postcondition_stays_pending(self):
        control = FakeControl("github.ci_trigger", unverifiable=True)
        result = self.coordinate("github.ci_trigger", control)
        self.assertEqual(result.status, "INSPECT_REQUIRED")
        self.assertTrue(result.mutation_performed)
        self.assertEqual(self.coordinate("github.ci_trigger", control).status, "WAIT")

    def test_merge_reobserves_full_evidence_and_exact_postcondition(self):
        control = FakeControl("github.merge")
        result = self.coordinate("github.merge", control)
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(control.calls, 1)

    def test_push_waits_for_separate_git_divergence_proof(self):
        control = FakeControl("github.push")
        result = self.coordinate("github.push", control)
        self.assertEqual(result.status, "REQUIRES_GIT_DIVERGENCE_PROOF")
        self.assertEqual(control.calls, 0)


if __name__ == "__main__":
    unittest.main()
