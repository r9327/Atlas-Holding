"""QA repair unit contracts with a pre-verified proof-store fixture.

Proof cryptography is covered separately in test_proof_store.py. This fixture
starts at the database boundary after the independent verifier has run.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from holding.autonomy import Mission, StandingGrant
from holding.daemon_lease import DaemonLease
from holding.qa_repair import QaRepairCoordinator
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40


class FakeGit:
    repository = REPO

    def __init__(self):
        self.head = SHA
        self.clean = True

    def inspect_canonical(self):
        return None

    def inspect(self, _mission):
        return type("Tree", (), {"base_sha": self.head})()

    def inspect_recovery(self, _mission):
        return self.clean


class RepairEligibilityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = StateStore(Path(folder.name) / "state.sqlite")
        self.addCleanup(self.store.close)
        self.store.add_missions([Mission("repair", REPO)])
        self.store.grant_for_onboarding(StandingGrant(
            REPO, frozenset(("code", "commit")), int(time.time()) + 300))
        self.lease = DaemonLease(self.store.path, heartbeat=False)
        self.lease.acquire()
        self.addCleanup(self.lease.release)
        self.store.claim_next(online=False, lease_owner=self.lease.owner)
        self.store.checkpoint("repair", SHA, lease_owner=self.lease.owner)
        self.git = FakeGit()
        self.repair = QaRepairCoordinator(self.store, self.git, self.lease)

    def verified_failure_fixture(self):
        with self.store._transaction():
            self.store.db.execute("""CREATE TABLE signed_proofs (
                mission_id TEXT PRIMARY KEY,
                candidate_sha TEXT NOT NULL, verdict TEXT NOT NULL,
                artifact TEXT NOT NULL, receipt_digest TEXT NOT NULL)""")
            self.store.db.execute(
                "INSERT INTO signed_proofs VALUES(?,?,?,?,?)",
                ("repair", SHA, "FAIL", "fixture.json", "b" * 64))
            self.store._transition("repair", "QA_PENDING", "BLOCKED",
                                   "signed fixture already independently checked")

    def test_only_independent_failure_can_requeue(self):
        self.assertEqual(self.repair.requeue_one().action, "IDLE")
        self.verified_failure_fixture()
        self.assertEqual(self.repair.requeue_one().action, "REQUEUED")
        self.assertEqual(self.repair.requeue_one().action, "IDLE")
        self.assertEqual(self.store.missions()[0].state, "READY")
        self.assertEqual(self.store.db.execute(
            "SELECT count(*) FROM qa_repair_attempts").fetchone()[0], 1)

    def test_paused_or_stopped_never_requeues(self):
        self.verified_failure_fixture()
        self.store.set_paused(True)
        self.assertEqual(self.repair.requeue_one().action, "PAUSED")
        self.store.set_paused(False)
        self.store.db.execute(
            "INSERT INTO settings(key,value) VALUES('stop_requested','1')")
        self.assertEqual(self.repair.requeue_one().action, "PAUSED")
        self.store.db.execute(
            "UPDATE settings SET value='0' WHERE key='stop_requested'")
        self.assertEqual(self.repair.requeue_one().action, "REQUEUED")

    def test_dirty_or_wrong_sha_stays_blocked(self):
        self.verified_failure_fixture()
        self.git.clean = False
        self.assertEqual(self.repair.requeue_one().action, "INSPECT_REQUIRED")
        self.git.clean = True
        self.git.head = "c" * 40
        self.assertEqual(self.repair.requeue_one().action, "INSPECT_REQUIRED")
        self.assertEqual(self.store.missions()[0].state, "BLOCKED")

    def test_revoked_grant_stays_blocked(self):
        self.verified_failure_fixture()
        self.store.revoke(REPO)
        self.assertEqual(self.repair.requeue_one().action, "UNAUTHORIZED")
        self.assertEqual(self.store.missions()[0].state, "BLOCKED")


if __name__ == "__main__":
    unittest.main()
