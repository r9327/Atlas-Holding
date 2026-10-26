"""Full local QA control flow: scoped advice NEVER bypasses signed gate."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from holding.autonomy import Mission, StandingGrant
from holding.proofs import ProofVerifier
from holding.qa_inbox import QaInbox, receipt_name
from holding.qa_scoped import ScopedDoctorRunner
from holding.state_store import StateStore

SHA = "a" * 40
BASE = "b" * 40
REPO = "r9327/Dof-Atlas"
KEY = b"Fixture key never installed or exposed in the code worker"


class _Git:
    repository = REPO

    def __init__(self, folder):
        self.folder = folder

    def inspect(self, mission_id):
        return type("Worktree", (), {"base_sha": SHA, "path": self.folder})()

    def _git(self, *args, **kwargs):
        return BASE if args[0] == "rev-parse" else ""


class QaChainTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.db = StateStore(self.root / "state.sqlite")
        self.addCleanup(self.db.close)
        self.db.add_missions([Mission("step1", REPO), Mission("step2", REPO,
                                                               dependencies=("step1",))])
        self.db.grant_for_onboarding(StandingGrant(
            REPO, frozenset({"code", "local_test"}), int(time.time()) + 300))
        self.assertEqual(self.db.claim_next(online=False).mission_id, "step1")
        self.db.checkpoint("step1", SHA)
        self.runner = ScopedDoctorRunner(_Git(self.root), Path(sys.executable).resolve())
        self.inbox = QaInbox(self.db, ProofVerifier(self.evidence, KEY))

    def local_scoped_pass(self):
        advice = type("Advice", (), {"status": "READY_TO_RUN_SCOPED",
                                     "report_sha256": "f" * 64})()
        report = {"candidate_sha": SHA, "base_sha": BASE,
                  "status": "READY_TO_RUN_SCOPED", "test_modules": ["tests.test_changes"],
                  "certified": False, "phase_certified": False,
                  "execution": {"status": "SCOPED_TESTS_PASS", "exit_code": 0,
                                "executed_modules": ["tests.test_changes"]}}
        with patch("holding.qa_scoped.DoctorPlanner") as plan, \
             patch("holding.qa_scoped._run_limited_doctor",
                   return_value=(0, json.dumps(report).encode())):
            plan.return_value.plan.return_value = advice
            return self.runner.run_pending(self.db, "step1")

    def external_signed_receipt(self, *, key=KEY):
        artifact = self.evidence / "report.json"
        artifact.write_text('{"source":"trusted external fixture"}', encoding="utf-8")
        data = {
            "schema": 1, "mission_id": "step1", "sha": SHA,
            "runner": "github_certification", "verdict": "PASS",
            "artifact": artifact.name,
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }
        signed_data = json.dumps(data, sort_keys=True, ensure_ascii=False,
                                 separators=(",", ":")).encode()
        mac = hmac.new(key, signed_data, hashlib.sha256).hexdigest()
        filename = receipt_name("step1", SHA)
        (self.evidence / filename).write_text(
            json.dumps({"payload": data, "mac": mac}), encoding="utf-8")

    def test_scoped_success_only_then_real_signed_gate_unblocks_dependency(self):
        advice = self.local_scoped_pass()
        self.assertEqual(advice.status, "SCOPED_TESTS_PASS")
        self.assertEqual({m.id: m.state for m in self.db.missions()},
                         {"step1": "QA_PENDING", "step2": "READY"})
        self.assertEqual(self.inbox.poll().missing, 1)
        self.assertNotEqual(self.db.claim_next(online=False).action, "RUN")
        self.external_signed_receipt()
        self.assertEqual(self.inbox.poll().passed, 1)
        self.assertEqual(self.db.claim_next(online=False).mission_id, "step2")

    def test_forged_receipt_does_not_upgrade_advisory(self):
        self.local_scoped_pass()
        self.external_signed_receipt(key=b"attacker-key" * 4)
        self.assertEqual(self.inbox.poll().rejected, 1)
        self.assertEqual(self.db.missions()[0].state, "QA_PENDING")


if __name__ == "__main__":
    unittest.main()
