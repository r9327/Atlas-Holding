"""Runtime dispatch settles push missions only from durable exact-SHA proof."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from holding.autonomy import Mission, StandingGrant
from holding.daemon_lease import DaemonLease
from holding.engine import HoldingEngine
from holding.git_push import GitPushState
from holding.migrations import SchemaMigrator
from holding.state_store import StateStore, StoreError
from holding.sync_worker import GitPushMissionWorker, VerifiedGitHubSync


REPO = "r9327/Dof-Atlas"
SHA = "a" * 40
POLICY = "b" * 40
ACCOUNT = "github:r9327"
NOW = 1_800_000_000


class FakeControl:
    def __init__(self, status="success", *, safe=True):
        self.remote = None
        self.status = status
        self.safe = safe
        self.pushes = 0

    def observe(self, repository, branch, candidate_sha):
        return GitPushState(
            repository, branch, candidate_sha, self.remote, self.safe)

    def push(self, intent, idempotency_key):
        self.pushes += 1
        if self.status == "ambiguous":
            raise RuntimeError("unknown")
        self.remote = intent.candidate_sha


class SyncWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "holding.sqlite"
        self.store = StateStore(self.path)
        self.addCleanup(self.store.close)
        self.lease = DaemonLease(self.path, heartbeat=False)
        self.lease.acquire()
        self.addCleanup(self.lease.release)
        SchemaMigrator(self.store, backup_dir=self.root / "backups").ensure(
            lease_owner=self.lease.owner)
        self.store.add_missions([
            Mission("code-1", REPO),
            Mission("push-1", REPO, operation="push",
                    dependencies=("code-1",), external_required=True),
        ])
        self.store.db.execute(
            "UPDATE missions SET state='DONE',candidate_sha=?,evidence_sha=? "
            "WHERE id='code-1'", (SHA, SHA))
        self.store.grant_for_onboarding(StandingGrant(
            REPO, frozenset({"push"}), NOW + 3600))
        raw = json.dumps({
            "schema_version": 1, "grant_id": "grant-runtime-1",
            "account_id": ACCOUNT, "repository": REPO,
            "operations": ["github.push"], "issued_at": NOW - 100,
            "expires_at": NOW + 3600, "revoked_at": None,
            "policy_sha": POLICY,
        }, sort_keys=True).encode()
        self.store.install_github_grant_for_onboarding(
            raw, expected_record_sha256=hashlib.sha256(raw).hexdigest(),
            observed_account_id=ACCOUNT, expected_policy_sha=POLICY,
            repository=REPO, now_epoch=NOW,
            lease_owner=self.lease.owner)
        self.store.save_service_health(
            service="github", status="ONLINE", observed_at=NOW,
            consecutive_successes=2, consecutive_failures=0,
            retry_attempts=0, retry_not_before=None, reason=None,
            updated_epoch=NOW, lease_owner=self.lease.owner)
        self.git = Mock(repository=REPO)
        self.git.inspect_canonical.return_value = None
        self.git.inspect.return_value = Mock(
            branch="holding/code-1", base_sha=SHA)

    def claim(self):
        decision = self.store.claim_next(
            online=True, now_epoch=NOW, lease_owner=self.lease.owner,
            supported_operations=frozenset({"push"}))
        self.assertEqual(decision.mission_id, "push-1")
        return next(m for m in self.store.missions() if m.id == "push-1")

    def worker(self, control):
        return GitPushMissionWorker(
            self.store, self.git, self.lease,
            expected_account_id=ACCOUNT, expected_policy_sha=POLICY,
            clock=lambda: NOW, control_factory=lambda _source: control)

    def engine(self, control):
        self.store.close()
        self.lease.release()
        engine = HoldingEngine(
            self.path, lambda _mission: "unused", online=lambda: True,
            supported_operations=frozenset({"push"}))
        self.addCleanup(engine.close)
        engine.start()
        engine.worker = GitPushMissionWorker(
            engine.store, self.git, engine.lease,
            expected_account_id=ACCOUNT, expected_policy_sha=POLICY,
            clock=lambda: NOW, control_factory=lambda _source: control)
        return engine

    def test_worker_pushes_certified_dependency_and_returns_verified_result(self):
        control = FakeControl()
        outcome = self.worker(control)(self.claim())
        self.assertIsInstance(outcome, VerifiedGitHubSync)
        self.store.complete_github_sync_mission(
            "push-1", sync_operation=outcome.sync_operation,
            branch=outcome.branch, candidate_sha=outcome.candidate_sha,
            idempotency_key=outcome.idempotency_key,
            lease_owner=self.lease.owner)
        mission = next(m for m in self.store.missions() if m.id == "push-1")
        self.assertEqual((mission.state, mission.evidence_sha), ("DONE", SHA))
        self.assertEqual(control.pushes, 1)

    def test_forged_result_without_settled_journal_cannot_mark_done(self):
        self.claim()
        with self.assertRaisesRegex(StoreError, "no settled"):
            self.store.complete_github_sync_mission(
                "push-1", sync_operation="github.push",
                branch="holding/code-1", candidate_sha=SHA,
                idempotency_key="c" * 64, lease_owner=self.lease.owner)

    def test_ambiguous_push_is_quarantined_by_engine_without_replay(self):
        control = FakeControl("ambiguous")
        engine = self.engine(control)
        result = engine.tick()
        self.assertEqual(result.action, "RECOVERY_REQUIRED")
        mission = next(m for m in engine.store.missions() if m.id == "push-1")
        self.assertEqual(mission.state, "RECOVERY_REQUIRED")
        self.assertEqual(control.pushes, 1)

    def test_engine_marks_verified_push_done_without_qa_pretense(self):
        engine = self.engine(FakeControl())
        result = engine.tick()
        self.assertEqual(result.action, "DONE")
        mission = next(m for m in engine.store.missions() if m.id == "push-1")
        self.assertEqual((mission.state, mission.candidate_sha,
                          mission.evidence_sha), ("DONE", SHA, SHA))

    def test_health_defer_consumes_no_mission_retry(self):
        self.store.save_service_health(
            service="github", status="OFFLINE", observed_at=NOW + 1,
            consecutive_successes=0, consecutive_failures=1,
            retry_attempts=1, retry_not_before=NOW + 10,
            reason="NETWORK_ERROR", updated_epoch=NOW + 1,
            lease_owner=self.lease.owner)
        engine = self.engine(FakeControl())
        result = engine.tick()
        self.assertEqual(result.action, "WAIT_FOR_NETWORK")
        mission = next(m for m in engine.store.missions() if m.id == "push-1")
        self.assertEqual((mission.state, mission.failures_without_progress),
                         ("READY", 0))

    def test_remote_divergence_blocks_once_without_push(self):
        control = FakeControl(safe=False)
        engine = self.engine(control)
        result = engine.tick()
        self.assertEqual(result.action, "BLOCKED")
        mission = next(m for m in engine.store.missions() if m.id == "push-1")
        self.assertEqual(mission.state, "BLOCKED")
        self.assertEqual(control.pushes, 0)


if __name__ == "__main__":
    unittest.main()
