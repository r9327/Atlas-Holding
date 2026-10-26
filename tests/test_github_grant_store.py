"""Durable GitHub grant material remains scoped, revocable, and token-free."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from holding.daemon_lease import DaemonLease
from holding.migrations import SchemaMigrator
from holding.state_store import StateStore, StoreError


REPO = "r9327/Dof-Atlas"
NOW = 1_800_000_000
POLICY = "a" * 40
ACCOUNT = "github:r9327"
SHA = "b" * 40
BRANCH = "holding/lot-1"


def grant(*, operations=None, policy=POLICY, account=ACCOUNT,
          revoked_at=None, expires=NOW + 3600):
    value = {
        "schema_version": 1, "grant_id": "grant-runtime-1",
        "account_id": account, "repository": REPO,
        "operations": operations or ["github.push", "github.pr_ready"],
        "issued_at": NOW - 100, "expires_at": expires,
        "revoked_at": revoked_at, "policy_sha": policy,
    }
    raw = json.dumps(value, sort_keys=True).encode()
    return raw, hashlib.sha256(raw).hexdigest()


class GitHubGrantStoreTests(unittest.TestCase):
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

    def install(self, raw, digest):
        self.store.install_github_grant_for_onboarding(
            raw, expected_record_sha256=digest,
            observed_account_id=ACCOUNT, expected_policy_sha=POLICY,
            repository=REPO, now_epoch=NOW,
            lease_owner=self.lease.owner)

    def test_installed_grant_produces_fresh_exact_sha_receipt(self):
        raw, digest = grant()
        self.install(raw, digest)
        receipt, receipt_digest = self.store.github_authorization_receipt(
            repository=REPO, operation="github.push", branch=BRANCH,
            candidate_sha=SHA, expected_account_id=ACCOUNT,
            expected_policy_sha=POLICY, now_epoch=NOW + 1)
        value = json.loads(receipt)
        self.assertEqual(value["candidate_sha"], SHA)
        self.assertEqual(value["operation"], "github.push")
        self.assertEqual(receipt_digest, hashlib.sha256(receipt).hexdigest())
        self.assertNotIn(b"token", receipt.lower())

    def test_scope_account_policy_and_integrity_are_revalidated(self):
        raw, digest = grant(operations=["github.push"])
        self.install(raw, digest)
        cases = (
            {"operation": "github.merge"},
            {"expected_account_id": "github:other"},
            {"expected_policy_sha": "c" * 40},
        )
        base = dict(
            repository=REPO, operation="github.push", branch=BRANCH,
            candidate_sha=SHA, expected_account_id=ACCOUNT,
            expected_policy_sha=POLICY, now_epoch=NOW + 1)
        for changed in cases:
            with self.subTest(changed=changed), self.assertRaises(StoreError):
                self.store.github_authorization_receipt(**(base | changed))
        self.store.db.execute(
            "UPDATE github_authorization_grants SET raw_grant=? WHERE repo=?",
            (b"{}", REPO))
        with self.assertRaises(StoreError):
            self.store.github_authorization_receipt(**base)

    def test_revocation_disables_simple_and_cryptographic_grants(self):
        raw, digest = grant()
        self.install(raw, digest)
        self.store.revoke(REPO)
        with self.assertRaisesRegex(StoreError, "No active"):
            self.store.github_authorization_receipt(
                repository=REPO, operation="github.push", branch=BRANCH,
                candidate_sha=SHA, expected_account_id=ACCOUNT,
                expected_policy_sha=POLICY, now_epoch=NOW + 1)

    def test_invalid_or_stale_grant_is_never_persisted(self):
        for raw, digest in (
            grant(account="github:other"), grant(policy="c" * 40),
            grant(revoked_at=NOW - 1), grant(expires=NOW),
        ):
            with self.subTest(raw=raw), self.assertRaises(StoreError):
                self.install(raw, digest)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM github_authorization_grants").fetchone()[0], 0)

    def test_stale_daemon_cannot_install_grant(self):
        raw, digest = grant()
        self.lease.release()
        with self.assertRaisesRegex(StoreError, "DAEMON_LEASE_LOST"):
            self.install(raw, digest)


if __name__ == "__main__":
    unittest.main()
