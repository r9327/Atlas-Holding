"""Final closure needs verified evidence for every original roadmap task."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import time
import unittest

from holding.autonomy import StandingGrant
from holding.closure import RoadmapClosure
from holding.proofs import ProofVerifier
from holding.roadmap_import import RoadmapImporter
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40
KEY = b"Trusted test-only fixture key, never deployed in a live installation"


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proofdir = self.root / "proofs"
        self.proofdir.mkdir()
        self.store = StateStore(self.root / "holding.sqlite")
        self.addCleanup(self.store.close)
        RoadmapImporter(self.store).import_text(json.dumps({
            "repo": REPO, "missions": [
                {"id": "first", "operation": "code", "acceptance": ["Build A"]},
                {"id": "second", "operation": "code", "depends_on": ["first"],
                 "acceptance": ["Build B"]}]}), source="roadmap")
        self.store.grant_for_onboarding(StandingGrant(
            REPO, frozenset({"code"}), int(time.time()) + 300))
        self.verifier = ProofVerifier(self.proofdir, KEY)

    def sign(self, mission_id: str, sha: str, *, runner="github_certification"):
        artifact = self.proofdir / (mission_id + ".txt")
        artifact.write_text("Authenticated GH CI proof")
        payload = {"schema": 1, "mission_id": mission_id, "sha": sha,
                   "verdict": "PASS", "runner": runner,
                   "artifact": artifact.name,
                   "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
        message = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode()
        mac = hmac.new(KEY, message, hashlib.sha256).hexdigest()
        name = mission_id + "-receipt.json"
        (self.proofdir / name).write_text(
            json.dumps({"payload": payload, "mac": mac}))
        return self.verifier.apply(self.store, name, mission_id=mission_id)

    def checkpoint(self, mission_id, sha):
        self.assertEqual(self.store.claim_next(online=False).mission_id, mission_id)
        self.store.checkpoint(mission_id, sha)

    def test_one_done_one_ready_cannot_close(self):
        self.checkpoint("first", SHA)
        self.sign("first", SHA)
        current = RoadmapClosure(self.store).inspect()
        self.assertFalse(current.ready)
        self.assertEqual(current.reason, "ROADMAP_REQUIREMENTS_UNPROVEN")
        self.assertFalse(current.report_digest)

    def test_every_independent_signature_yields_deterministic_closure(self):
        self.checkpoint("first", SHA)
        self.sign("first", SHA)
        self.checkpoint("second", "b" * 40)
        self.sign("second", "b" * 40)
        report = RoadmapClosure(self.store).inspect()
        self.assertTrue(report.ready)
        self.assertEqual(report.reason, "ALL_REQUIREMENTS_SIGNED")
        self.assertEqual(report.requirements.proven, 2)
        self.assertEqual(report.requirements.missions_total, 2)
        self.assertEqual(len(report.report_digest), 64)
        self.assertEqual([entry["mission_id"] for entry in report.mission_proofs],
                         ["first", "second"])
        self.assertEqual(report.report_digest,
                         RoadmapClosure(self.store).inspect().report_digest)

    def test_signed_scoped_doctor_never_completes_closure(self):
        self.checkpoint("first", SHA)
        with self.assertRaises(ValueError):
            self.sign("first", SHA, runner="doctor_atlas")
        self.assertFalse(RoadmapClosure(self.store).inspect().ready)

    def test_corrupt_proof_link_denies_closure(self):
        self.checkpoint("first", SHA)
        self.sign("first", SHA)
        self.checkpoint("second", "b" * 40)
        self.sign("second", "b" * 40)
        self.store.db.execute(
            "UPDATE signed_proofs SET receipt_digest='' WHERE mission_id='second'")
        status = RoadmapClosure(self.store).inspect()
        self.assertFalse(status.ready)
        self.assertEqual(status.reason, "MISSING_FINAL_SIGNED_PROOF")


if __name__ == "__main__":
    unittest.main()
