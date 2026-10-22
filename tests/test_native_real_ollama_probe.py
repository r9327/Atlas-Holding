"""Portable fail-closed checks for opt-in real-model CI evidence."""
from __future__ import annotations

import copy
import unittest

from tests.native_windows_real_ollama_smoke import (
    _check_live_receipt, _pinned_digest, MODEL,
)


class NativeRealOllamaProbeTests(unittest.TestCase):
    def setUp(self):
        self.digest = "a" * 64
        self.receipt = {
            "schema": 1,
            "kind": "NONRELEASE_DISPOSABLE_LOCAL_MISSION",
            "source": "LIVE_PINNED_OLLAMA",
            "target": "ISOLATED_TEST_GIT_REPOSITORY_NOT_REAL_ATLAS",
            "model": MODEL,
            "model_digest": self.digest,
            "mission_state": "QA_PENDING_AWAITING_PRIVILEGED_DOCTOR",
            "doctor_status": "NOT_EXECUTED",
            "github_push_status": "NOT_PERMITTED",
            "release_ready": False,
            "static_ast_exact_match": True,
            "changed_paths": ["src/feature.py"],
            "ollama_ram_peak_bytes": None,
            "ollama_vram_peak_bytes": None,
            "base_commit_sha": "1" * 40,
            "candidate_commit_sha": "2" * 40,
            "agent_executable_sha256": "3" * 64,
            "duration_ms": 1500,
        }

    def verify(self, receipt):
        _check_live_receipt(receipt, model=MODEL, digest=self.digest)

    def test_actual_nonrelease_receipt_shape_is_accepted(self):
        self.verify(self.receipt)

    def test_stub_and_wrong_model_digest_are_rejected(self):
        for field, value in (("source", "DETERMINISTIC_CI_STUB"),
                             ("model", "different:1.5b"),
                             ("model_digest", "b" * 64)):
            with self.subTest(field=field):
                receipt = copy.deepcopy(self.receipt)
                receipt[field] = value
                with self.assertRaises(RuntimeError):
                    self.verify(receipt)

    def test_forged_qa_or_release_pass_is_never_accepted(self):
        for field, value in (
            ("mission_state", "DONE"),
            ("doctor_status", "PASS"),
            ("github_push_status", "PUSHED"),
            ("release_ready", True),
            ("certified", True),
            ("static_ast_exact_match", False),
        ):
            with self.subTest(field=field):
                receipt = copy.deepcopy(self.receipt)
                receipt[field] = value
                with self.assertRaises(RuntimeError):
                    self.verify(receipt)

    def test_missing_candidate_identity_is_rejected(self):
        for field, value in (
            ("base_commit_sha", "bad"),
            ("candidate_commit_sha", "1" * 40),
            ("agent_executable_sha256", "wrong"),
            ("duration_ms", True),
            ("changed_paths", ["src/feature.py", "tools/doctor.py"]),
        ):
            with self.subTest(field=field):
                receipt = copy.deepcopy(self.receipt)
                receipt[field] = value
                with self.assertRaises(RuntimeError):
                    self.verify(receipt)

    def test_exact_model_digest_is_required(self):
        self.assertEqual(_pinned_digest(MODEL, {"models": [
            {"name": MODEL, "digest": self.digest},
            {"name": "other", "digest": "b" * 64},
        ]}), self.digest)
        for models in ([], [{"name": MODEL, "digest": "invalid"}],
                       [{"name": MODEL, "digest": self.digest}] * 2):
            with self.subTest(models=models):
                with self.assertRaises(RuntimeError):
                    _pinned_digest(MODEL, {"models": models})


if __name__ == "__main__":
    unittest.main()
