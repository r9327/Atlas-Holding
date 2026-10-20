"""Signed evidence integration against the real StateStore API."""
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import time
import unittest

from holding.autonomy import Mission, StandingGrant
from holding.proofs import ProofVerifier, ProofError
from holding.state_store import StateStore, StoreError

SHA = "a" * 40
KEY = b"Only for tests: a 256-bit simulated independent key!"
REPO = "r9327/Dof-Atlas"


class ProofStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.store = StateStore(root / "state.sqlite")
        self.addCleanup(self.store.close)
        self.proof_dir = root / "trusted"
        self.proof_dir.mkdir()
        self.verifier = ProofVerifier(self.proof_dir, KEY)
        self.store.grant_for_onboarding(StandingGrant(REPO, frozenset({"code"}), int(time.time()) + 3600))
        self.store.add_missions([Mission("first", REPO), Mission("second", REPO, dependencies=("first",))])
        self.assertEqual(self.store.claim_next(online=False).mission_id, "first")
        self.store.checkpoint("first", SHA)

    def signed_receipt(self, *, signature_key=KEY, candidate=SHA, verdict="PASS"):
        artifact = self.proof_dir / "result.txt"
        artifact.write_text("Independent test result", encoding="utf-8")
        data = {
            "schema": 1, "mission_id": "first", "sha": candidate,
            "runner": "doctor_atlas", "verdict": verdict, "artifact": artifact.name,
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()
        }
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        envelope = {"payload": data, "mac": hmac.new(signature_key, encoded, hashlib.sha256).hexdigest()}
        (self.proof_dir / "receipt.json").write_text(json.dumps(envelope), encoding="utf-8")
        return "receipt.json"

    def test_pass_unlocks_dependent_job_and_is_auditable(self):
        result = self.verifier.apply(self.store, self.signed_receipt(), mission_id="first")
        self.assertEqual(result.verdict, "PASS")
        jobs = {m.id: m for m in self.store.missions()}
        self.assertEqual(jobs["first"].state, "DONE")
        self.assertEqual(jobs["first"].evidence_sha, SHA)
        row = self.store.db.execute("SELECT receipt_digest FROM signed_proofs WHERE mission_id='first'").fetchone()
        self.assertEqual(row[0], result.proof_digest)
        self.assertEqual(self.store.claim_next(online=False).mission_id, "second")

    def test_bad_signature_remains_qa_pending(self):
        with self.assertRaises(ProofError):
            self.verifier.apply(self.store, self.signed_receipt(signature_key=b"Z" * 32), mission_id="first")
        self.assertEqual(self.store.missions()[0].state, "QA_PENDING")
        self.assertEqual(self.store.claim_next(online=False).action, "WAIT")

    def test_fail_blocks_without_fake_done(self):
        self.verifier.apply(self.store, self.signed_receipt(verdict="FAIL"), mission_id="first")
        self.assertEqual(self.store.missions()[0].state, "BLOCKED")
        self.assertEqual(self.store.claim_next(online=False).action, "BLOCKED")

    def test_legacy_string_pass_is_forbidden(self):
        with self.assertRaises(StoreError):
            self.store.register_test_verdict("first", candidate_sha=SHA, evidence_sha=SHA,
                verdict="PASS", runner="doctor_adapter", artifact_id="fake")
        self.assertEqual(self.store.missions()[0].state, "QA_PENDING")

    def test_repaired_new_sha_preserves_both_signed_verdicts(self):
        self.verifier.apply(
            self.store, self.signed_receipt(verdict="FAIL"), mission_id="first")
        with self.store._transaction():
            self.store._transition("first", "BLOCKED", "READY", "test-only new code")
            self.store._transition("first", "READY", "RUNNING", "test-only claimed")
            self.store._transition("first", "RUNNING", "QA_PENDING")
            self.store.db.execute(
                "UPDATE missions SET candidate_sha=? WHERE id='first'", ("b" * 40,))
        self.verifier.apply(
            self.store, self.signed_receipt(candidate="b" * 40, verdict="PASS"),
            mission_id="first")
        history = self.store.db.execute(
            "SELECT candidate_sha,verdict FROM signed_proof_history "
            "WHERE mission_id='first' ORDER BY seq").fetchall()
        self.assertEqual([tuple(row) for row in history],
                         [(SHA, "FAIL"), ("b" * 40, "PASS")])
        self.assertEqual(self.store.missions()[0].state, "DONE")
        self.assertEqual(self.store.missions()[0].evidence_sha, "b" * 40)

    def test_receipt_is_not_replayable(self):
        name = self.signed_receipt()
        self.verifier.apply(self.store, name, mission_id="first")
        with self.assertRaises(StoreError):
            self.verifier.apply(self.store, name, mission_id="first")


if __name__ == "__main__":
    unittest.main()
