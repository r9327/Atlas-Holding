"""Integration checks for asynchronous QA evidence intake."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import time
import unittest

from holding.autonomy import Mission, StandingGrant
from holding.proofs import ProofVerifier
from holding.qa_inbox import QaInbox, receipt_name
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40
OTHER_SHA = "b" * 40
KEY = b"Trusted QA test-only signing secret of more than 32 bytes"


class QaInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = StateStore(self.root / "state.sqlite")
        self.addCleanup(self.db.close)
        self.evidence = self.root / "qa"
        self.evidence.mkdir()
        self.verifier = ProofVerifier(self.evidence, KEY)
        self.inbox = QaInbox(self.db, self.verifier)
        self.db.grant_for_onboarding(StandingGrant(
            REPO, frozenset({"code"}), int(time.time()) + 3600))

    def add_pending(self, name, *, depends=()):
        self.db.add_missions([Mission(name, REPO, dependencies=depends)])
        claimed = self.db.claim_next(online=False)
        self.assertEqual(claimed.mission_id, name)
        self.db.checkpoint(name, SHA)

    def receipt(self, mission_id, *, candidate=SHA, verdict="PASS",
                key=KEY, runner=None):
        artifact = self.evidence / ("artifact-" + mission_id + ".txt")
        artifact.write_text("trusted independent test report", encoding="utf-8")
        payload = {
            "schema": 1, "mission_id": mission_id, "sha": candidate,
            "verdict": verdict,
            "runner": runner or (
                "github_certification" if verdict == "PASS" else "doctor_atlas"),
            "artifact": artifact.name,
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()
        mac = hmac.new(key, encoded, hashlib.sha256).hexdigest()
        path = self.evidence / receipt_name(mission_id, SHA)
        path.write_text(json.dumps({"payload": payload, "mac": mac}), encoding="utf-8")
        return path

    def test_signed_pass_finishes_pending_and_releases_dependent(self):
        self.add_pending("a")
        self.db.add_missions([Mission("b", REPO, dependencies=("a",))])
        self.assertEqual(self.inbox.poll().missing, 1)
        self.receipt("a")
        result = self.inbox.poll()
        self.assertEqual((result.passed, result.failed, result.rejected), (1, 0, 0))
        self.assertEqual({m.id: m.state for m in self.db.missions()},
                         {"a": "DONE", "b": "READY"})
        self.assertEqual(self.db.claim_next(online=False).mission_id, "b")

    def test_invalid_mac_leaves_qa_pending(self):
        self.add_pending("a")
        self.receipt("a", key=b"x" * 32)
        result = self.inbox.poll()
        self.assertEqual(result.rejected, 1)
        self.assertEqual(self.db.missions()[0].state, "QA_PENDING")
        self.assertEqual(self.db.missions()[0].failures_without_progress, 0)

    def test_signed_scoped_doctor_pass_does_not_complete_mission(self):
        self.add_pending("a")
        self.receipt("a", verdict="PASS", runner="doctor_atlas")
        result = self.inbox.poll()
        self.assertEqual(result.passed, 0)
        self.assertEqual(result.rejected, 1)
        self.assertEqual(self.db.missions()[0].state, "QA_PENDING")
        self.assertEqual(self.db.missions()[0].evidence_sha, "")

    def test_fail_blocks_and_never_unlocks(self):
        self.add_pending("a")
        self.db.add_missions([Mission("b", REPO, dependencies=("a",))])
        self.receipt("a", verdict="FAIL")
        result = self.inbox.poll()
        self.assertEqual(result.failed, 1)
        self.assertEqual({m.id: m.state for m in self.db.missions()},
                         {"a": "BLOCKED", "b": "READY"})
        self.assertNotEqual(self.db.claim_next(online=False).action, "RUN")

    def test_stale_candidate_sha_receipt_is_rejected(self):
        self.add_pending("a")
        self.receipt("a", candidate=OTHER_SHA)
        self.assertEqual(self.inbox.poll().rejected, 1)
        self.assertEqual(self.db.missions()[0].state, "QA_PENDING")

    def test_symlinked_receipt_refused(self):
        self.add_pending("a")
        destination = self.evidence / "outside.json"
        destination.write_text("{}", encoding="utf-8")
        try:
            (self.evidence / receipt_name("a", SHA)).symlink_to(destination)
        except OSError:
            self.skipTest("Symlinks are unavailable")
        self.assertEqual(self.inbox.poll().rejected, 1)
        self.assertEqual(self.db.missions()[0].state, "QA_PENDING")

    def test_many_pending_missions_are_polled_in_bounded_pages(self):
        self.db.add_missions([Mission(f"mission-{i:03d}", REPO) for i in range(300)])
        with self.db._transaction():
            self.db.db.execute(
                "UPDATE missions SET state='QA_PENDING',candidate_sha=?", (SHA,))
        self.receipt("mission-299")
        result1 = self.inbox.poll()
        result2 = self.inbox.poll()
        result3 = self.inbox.poll()
        self.assertEqual([result1.scanned, result2.scanned, result3.scanned],
                         [128, 128, 44])
        self.assertEqual([result1.passed, result2.passed, result3.passed],
                         [0, 0, 1])
        self.assertEqual(self.db.db.execute(
            "SELECT state FROM missions WHERE id='mission-299'").fetchone()[0], "DONE")

    def test_signed_receipt_is_deferred_while_paused_or_stopped(self):
        self.add_pending("a")
        self.receipt("a")
        self.db.set_paused(True)
        self.assertEqual(self.inbox.poll().scanned, 0)
        self.assertEqual(self.db.missions()[0].state, "QA_PENDING")
        self.db.set_paused(False)
        self.db.db.execute(
            "INSERT INTO settings(key,value) VALUES('stop_requested','1')")
        self.assertEqual(self.inbox.poll().scanned, 0)
        self.assertEqual(self.db.missions()[0].state, "QA_PENDING")
        self.db.db.execute(
            "UPDATE settings SET value='0' WHERE key='stop_requested'")
        self.assertEqual(self.inbox.poll().passed, 1)
        self.assertEqual(self.db.missions()[0].state, "DONE")

    def test_receipt_names_are_exact_sha_and_path_safe(self):
        self.assertEqual(receipt_name("test.1", SHA), receipt_name("test.1", SHA))
        self.assertNotEqual(receipt_name("test.1", SHA), receipt_name("test.2", SHA))
        for name in ("../x", "", "a/b"):
            with self.assertRaises(ValueError):
                receipt_name(name, SHA)
        with self.assertRaises(ValueError):
            receipt_name("a", "bad-sha")


if __name__ == "__main__":
    unittest.main()
