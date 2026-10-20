"""Acceptance coverage must be computed from real signed exact-SHA proof."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import time
import unittest

from holding.autonomy import StandingGrant
from holding.control import ControlPlane
from holding.coverage import RequirementCoverage
from holding.proofs import ProofVerifier
from holding.roadmap_import import RoadmapImporter
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40
KEY = b"Independent test signing key; never a deployed production credential"


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "state.sqlite"
        self.evidence = self.root / "qa"
        self.evidence.mkdir()
        self.store = StateStore(self.db)
        self.addCleanup(self.store.close)
        RoadmapImporter(self.store).import_text(json.dumps({
            "repo": REPO,
            "missions": [
                {"id": "step-a", "operation": "code",
                 "acceptance": ["Generate module", "No regression"]},
                {"id": "step-b", "operation": "code",
                 "depends_on": ["step-a"],
                 "acceptance": ["Demonstrate result"]}]}), source="fixture")
        self.store.grant_for_onboarding(StandingGrant(
            REPO, frozenset({"code"}), int(time.time()) + 600))

    def sign(self, *, runner="github_certification", verdict="PASS"):
        artifact = self.evidence / "proof.txt"
        artifact.write_text("upstream independent run", encoding="utf-8")
        payload = {"schema": 1, "mission_id": "step-a", "sha": SHA,
                   "verdict": verdict, "runner": runner,
                   "artifact": artifact.name,
                   "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
        message = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()
        mac = hmac.new(KEY, message, hashlib.sha256).hexdigest()
        (self.evidence / "receipt.json").write_text(
            json.dumps({"payload": payload, "mac": mac}), encoding="utf-8")
        return ProofVerifier(self.evidence, KEY).apply(
            self.store, "receipt.json", mission_id="step-a")

    def test_idle_roadmap_is_not_a_coverage_pass(self):
        audit = RequirementCoverage(self.store)
        result = audit.summary()
        self.assertEqual(result.requirements_total, 3)
        self.assertEqual(result.incomplete, 3)
        self.assertEqual(result.proven, 0)
        self.assertFalse(result.ready_to_close)

    def test_local_scoped_pass_cannot_prove_acceptance(self):
        self.store.claim_next(online=False)
        self.store.checkpoint("step-a", SHA)
        audit = RequirementCoverage(self.store)
        self.assertEqual(audit.summary().awaiting_proof, 2)
        with self.assertRaises(ValueError):
            self.sign(runner="doctor_atlas")
        self.assertEqual(audit.summary().proven, 0)

    def test_signed_full_marks_only_its_requirements_proven(self):
        self.store.claim_next(online=False)
        self.store.checkpoint("step-a", SHA)
        self.sign()
        result = RequirementCoverage(self.store).summary()
        self.assertEqual((result.proven, result.incomplete), (2, 1))
        self.assertFalse(result.ready_to_close)
        rows = RequirementCoverage(self.store).page(limit=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r.status for r in rows], ["PROVEN", "PROVEN"])
        self.assertEqual([r.criterion_number for r in rows], [0, 1])

    def test_pagination_can_resume_in_middle_of_mission(self):
        audit = RequirementCoverage(self.store)
        first = audit.page(limit=1)[0]
        self.assertEqual((first.mission_id, first.criterion_number),
                         ("step-a", 0))
        middle = audit.page(after_mission_id=first.mission_id,
                            after_criterion=first.criterion_number, limit=1)[0]
        self.assertEqual((middle.mission_id, middle.criterion_number),
                         ("step-a", 1))
        last = audit.page(after_mission_id=middle.mission_id,
                          after_criterion=middle.criterion_number, limit=10)[0]
        self.assertEqual((last.mission_id, last.criterion_number), ("step-b", 0))

    def test_control_snapshot_remains_quick_and_coverage_is_on_demand(self):
        with ControlPlane(self.db) as control:
            before = control.snapshot()
            self.assertEqual(before.mission_counts["READY"], 2)
            report = control.requirement_coverage()
            self.assertFalse(report.ready_to_close)
            self.assertEqual(len(control.requirement_page(limit=3)), 3)

    def test_missing_or_corrupt_mapping_is_never_proven(self):
        self.store.db.execute(
            "UPDATE mission_specs SET spec_json='{}' WHERE id='step-a'")
        with self.assertRaisesRegex(RuntimeError, "mapping"):
            RequirementCoverage(self.store).summary()


if __name__ == "__main__":
    unittest.main()
