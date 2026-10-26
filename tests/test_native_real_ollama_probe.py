"""Portable fail-closed checks for opt-in real-model CI evidence."""
from __future__ import annotations

import copy
import json
import unittest

from tests.native_windows_real_ollama_smoke import (
    _build_verified_session, _check_live_receipt, _pinned_digest, MODEL,
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

    def sample_trials(self):
        entries = []
        for number in range(3):
            proof = copy.deepcopy(self.receipt)
            proof["candidate_commit_sha"] = str(number + 2) * 40
            proof["duration_ms"] = 1500 + 100 * number
            raw = (json.dumps(proof, sort_keys=True, separators=(",", ":"))
                   + "\n").encode("utf-8")
            entries.append((raw, proof))
        return entries

    def test_three_independent_valid_receipts_form_only_nonrelease_session(self):
        summary = _build_verified_session(
            self.sample_trials(), model=MODEL, digest=self.digest)
        self.assertEqual(summary["trial_count"], 3)
        self.assertEqual(len(set(summary["candidate_commit_sha"])), 3)
        self.assertEqual(len(set(summary["individual_receipt_sha256"])), 3)
        self.assertEqual(summary["doctor_status"], "NOT_EXECUTED")
        self.assertEqual(summary["github_push_status"], "NOT_PERMITTED")
        self.assertEqual(summary["model_quality_benchmark"], "NOT_CERTIFIED")
        self.assertIs(summary["release_ready"], False)

    def test_one_or_two_good_trials_cannot_make_session_pass(self):
        entries = self.sample_trials()
        for count in (0, 1, 2):
            with self.subTest(count=count), self.assertRaises(RuntimeError):
                _build_verified_session(
                    entries[:count], model=MODEL, digest=self.digest)

    def test_one_failing_or_changed_model_receipt_rejects_whole_session(self):
        for field, value in (
            ("source", "DETERMINISTIC_CI_STUB"),
            ("doctor_status", "PASS"),
            ("model_digest", "b" * 64),
            ("github_push_status", "PUSHED"),
        ):
            with self.subTest(field=field):
                entries = self.sample_trials()
                corrupted = dict(entries[-1][1])
                corrupted[field] = value
                entries[-1] = (
                    (json.dumps(corrupted, sort_keys=True) + "\n").encode(),
                    corrupted,
                )
                with self.assertRaises(RuntimeError):
                    _build_verified_session(
                        entries, model=MODEL, digest=self.digest)

    def test_duplicate_candidate_or_agent_swap_rejects_session(self):
        for field, value in (
            ("candidate_commit_sha", "2" * 40),
            ("agent_executable_sha256", "4" * 64),
        ):
            with self.subTest(field=field):
                entries = self.sample_trials()
                altered = dict(entries[-1][1])
                altered[field] = value
                entries[-1] = (
                    (json.dumps(altered, sort_keys=True) + "\n").encode(),
                    altered,
                )
                with self.assertRaises(RuntimeError):
                    _build_verified_session(
                        entries, model=MODEL, digest=self.digest)

    def test_receipt_bytes_mismatch_cannot_make_session_pass(self):
        entries = self.sample_trials()
        entries[-1] = (entries[-1][0] + b" ", entries[-1][1])
        with self.assertRaises(RuntimeError):
            _build_verified_session(entries, model=MODEL, digest=self.digest)

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
