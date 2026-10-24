"""Exact-SHA non-force push coordination and constrained Git argv."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from holding.daemon_lease import DaemonLease
from holding.git_push import (
    GitPushState, GitRemotePushControl, coordinate_git_push,
    recover_pending_git_push,
)
from holding.github_mutation_gate import GitHubMutationIntent
from holding.migrations import SchemaMigrator
from holding.standing_grant import authorize_operation, serialize_github_authorization
from holding.state_store import StateStore, StoreError


REPO = "r9327/Dof-Atlas"
BRANCH = "holding/lot-1"
SHA = "a" * 40
OLD = "b" * 40
NOW = 1_800_000_000
POLICY = "c" * 40


class FakePushControl:
    def __init__(self, remote=OLD, *, safe=True, ambiguous=False):
        self.remote = remote
        self.safe = safe
        self.ambiguous = ambiguous
        self.pushes = 0

    def observe(self, repository, branch, candidate_sha):
        return GitPushState(repository, branch, candidate_sha,
                            self.remote, self.safe)

    def push(self, intent, idempotency_key):
        self.pushes += 1
        if self.ambiguous:
            raise RuntimeError("network outcome unknown")
        self.remote = intent.candidate_sha


class GitPushTests(unittest.TestCase):
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
            updated_epoch=NOW, lease_owner=self.lease.owner)

    def authorization(self):
        grant = {
            "schema_version": 1, "grant_id": "grant-1",
            "account_id": "github:r9327", "repository": REPO,
            "operations": ["github.push"], "issued_at": NOW - 100,
            "expires_at": NOW + 3600, "revoked_at": None,
            "policy_sha": POLICY,
        }
        raw = json.dumps(grant, sort_keys=True).encode()
        decision = authorize_operation(
            raw, expected_record_sha256=hashlib.sha256(raw).hexdigest(),
            expected_policy_sha=POLICY, observed_account_id="github:r9327",
            repository=REPO, operation="github.push", now_epoch=NOW,
            branch=BRANCH, candidate_sha=SHA)
        receipt = serialize_github_authorization(decision)
        return receipt, hashlib.sha256(receipt).hexdigest()

    def coordinate(self, control, now=NOW):
        receipt, digest = self.authorization()
        return coordinate_git_push(
            self.store, control, receipt, expected_receipt_sha256=digest,
            expected_account_id="github:r9327", expected_policy_sha=POLICY,
            repository=REPO, branch=BRANCH, candidate_sha=SHA,
            now_epoch=now, lease_owner=self.lease.owner)

    def test_fast_forward_push_is_observed_and_journaled(self):
        control = FakePushControl()
        result = self.coordinate(control)
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertTrue(result.mutation_performed)
        self.assertEqual(control.pushes, 1)
        self.assertEqual(self.coordinate(control).status, "ALREADY_DONE")

    def test_divergence_blocks_before_journal_or_push(self):
        control = FakePushControl(safe=False)
        result = self.coordinate(control)
        self.assertEqual(result.status, "REMOTE_DIVERGED")
        self.assertEqual(control.pushes, 0)
        self.assertEqual(self.store.sync_attempts(
            operation="github.push", repo=REPO, branch=BRANCH,
            candidate_sha=SHA), ())

    def test_already_remote_is_recorded_without_mutation(self):
        control = FakePushControl(remote=SHA)
        result = self.coordinate(control)
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertFalse(result.mutation_performed)
        self.assertEqual(control.pushes, 0)

    def test_ambiguous_push_is_never_replayed_by_recovery(self):
        control = FakePushControl(ambiguous=True)
        self.assertEqual(self.coordinate(control).status, "INSPECT_REQUIRED")
        control.ambiguous = False
        recovered = recover_pending_git_push(
            self.store, control, repository=REPO, branch=BRANCH,
            candidate_sha=SHA, now_epoch=NOW + 31,
            lease_owner=self.lease.owner)
        self.assertEqual(recovered.status, "RETRY")
        self.assertEqual(control.pushes, 1)

    def test_visible_ambiguous_effect_is_closed_without_replay(self):
        control = FakePushControl(ambiguous=True)
        self.coordinate(control)
        control.remote = SHA
        recovered = recover_pending_git_push(
            self.store, control, repository=REPO, branch=BRANCH,
            candidate_sha=SHA, now_epoch=NOW + 31,
            lease_owner=self.lease.owner)
        self.assertEqual(recovered.status, "SUCCEEDED")
        self.assertEqual(control.pushes, 1)

    def test_tampered_recovery_context_is_never_accepted(self):
        control = FakePushControl(ambiguous=True)
        self.coordinate(control)
        self.store.db.execute(
            "UPDATE github_sync_context SET pre_state=?",
            (b'{"repository":"x","repository":"y"}',))
        with self.assertRaisesRegex(StoreError, "integrity"):
            recover_pending_git_push(
                self.store, control, repository=REPO, branch=BRANCH,
                candidate_sha=SHA, now_epoch=NOW + 31,
                lease_owner=self.lease.owner)
        self.assertEqual(control.pushes, 1)

    def test_concrete_adapter_uses_non_force_immutable_refspec(self):
        git = Mock()
        git.repository = REPO
        tree = Mock(path=self.root, branch=BRANCH, base_sha=SHA)
        git.inspect.return_value = tree
        git._git.return_value = ""
        control = GitRemotePushControl(git, "lot-1")
        intent = GitHubMutationIntent(
            "github.push", REPO, BRANCH, SHA, None, "d" * 64, None)
        control.push(intent, "e" * 64)
        push = [call for call in git._git.call_args_list
                if call.args and call.args[0] == "push"]
        self.assertEqual(len(push), 1)
        self.assertEqual(push[0].args[:4], (
            "push", "--porcelain", "origin",
            f"{SHA}:refs/heads/{BRANCH}"))
        self.assertNotIn("--force", push[0].args)


if __name__ == "__main__":
    unittest.main()
