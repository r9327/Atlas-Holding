"""Independent non-executing acceptance rules for live model and CI stub.

The Python AST must match exactly. Never eval/import/execute generated source.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

# unittest discover -s tests adds this folder to sys.path, but direct execution
# must also import the exact checked-out native mission helper.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_windows_code_mission_smoke import (
    _check_answer, _mission_receipt, _publish_receipt,
)
from holding.ollama_observation import OllamaAllocation


class NativeCodeStaticAcceptanceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="atlas-static-acceptance-")
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name).resolve() / "feature.py"

    def source(self, code: str) -> None:
        self.path.write_text(code, encoding="utf-8")

    def test_exclusively_one_answer_function_with_comments_is_accepted(self):
        for code in (
            "def answer():\n    return 42\n",
            "# harmless comment\n\n\ndef answer():\n    return 42  # okay\n",
            "def answer():\n\n    return (42)\n",
        ):
            with self.subTest(code=code):
                self.source(code)
                _check_answer(self.path)

    def test_hidden_import_time_execution_never_passes_static_qa(self):
        payloads = (
            "@__import__('os').system('echo unsafe')\ndef answer():\n    return 42\n",
            "def answer(*, dangerous=__import__('os').system('echo unsafe')):\n    return 42\n",
            "def answer(x=__import__('os').system('echo unsafe')):\n    return 42\n",
            "def answer() -> __import__('os').system('echo unsafe'):\n    return 42\n",
            "def answer(x: __import__('os').system('echo unsafe') = 1):\n    return 42\n",
            "from os import system\ndef answer():\n    return 42\n",
            "def answer():\n    return 42\n__import__('os').system('echo unsafe')\n",
            "async def answer():\n    return 42\n",
            "def answer(*args):\n    return 42\n",
            "def answer(**kwargs):\n    return 42\n",
            "def answer(*, x=1):\n    return 42\n",
            "def answer(x, /):\n    return 42\n",
            "def answer():\n    pass\n    return 42\n",
            "def answer():\n    return 42.0\n",
            "def answer():\n    return True\n",
            "def answer():\n    return 42\n\ndef answer():\n    return 42\n",
        )
        for code in payloads:
            with self.subTest(code=code):
                self.source(code)
                with self.assertRaisesRegex(RuntimeError, "static acceptance"):
                    _check_answer(self.path)

    def test_malformed_oversized_and_nonregular_source_is_rejected(self):
        for code in ("def answer(:\n", "def answer():\n    return 42\n" + "x" * 8193):
            with self.subTest(code=code[:50]):
                self.source(code)
                with self.assertRaises(RuntimeError):
                    _check_answer(self.path)
        self.path.write_bytes(b"\xff\xfe\xfd")
        with self.assertRaises(RuntimeError):
            _check_answer(self.path)
        self.path.unlink()
        with self.assertRaises(RuntimeError):
            _check_answer(self.path)


class NativeMissionReceiptTests(unittest.TestCase):
    def receipt(self, **overrides):
        details = dict(
            live_model=False, model_name="atlas-ci-fixture:Q4",
            model_digest="a" * 64, agent_sha="b" * 64,
            base_sha="c" * 40, candidate_sha="d" * 40,
            adapter_peak=None, duration_ms=154,
        )
        details.update(overrides)
        return _mission_receipt(**details)

    def test_stub_receipt_never_asserts_live_model_or_doctor_verdict(self):
        proof = self.receipt()
        self.assertEqual(proof["source"], "DETERMINISTIC_CI_STUB")
        self.assertEqual(proof["doctor_status"], "NOT_EXECUTED")
        self.assertEqual(proof["github_push_status"], "NOT_PERMITTED")
        self.assertIs(proof["release_ready"], False)
        self.assertIsNone(proof["ollama_ram_peak_bytes"])
        self.assertIsNone(proof["ollama_vram_peak_bytes"])
        self.assertIsNone(proof["adapter_job_peak_bytes"])

    def test_real_model_receipt_does_not_invent_ram_or_release_certificate(self):
        proof = self.receipt(live_model=True, adapter_peak=42_000_000)
        self.assertEqual(proof["source"], "LIVE_PINNED_OLLAMA")
        self.assertEqual(proof["adapter_job_peak_bytes"], 42_000_000)
        self.assertIsNone(proof["ollama_ram_peak_bytes"])
        self.assertFalse(proof["release_ready"])

    def test_live_ollama_allocation_is_advisory_not_actual_server_peak(self):
        observed = OllamaAllocation(
            observations=2, loaded_model_bytes_high=4_000_000_000,
            loaded_model_vram_bytes_high=3_000_000_000, failed_polls=1)
        proof = self.receipt(live_model=True, allocation=observed)
        self.assertEqual(proof["ollama_allocation_observations"], 2)
        self.assertEqual(proof["ollama_allocation_failed_polls"], 1)
        self.assertEqual(proof["ollama_loaded_model_bytes_observed_high"],
                         4_000_000_000)
        self.assertEqual(proof["ollama_loaded_model_vram_bytes_observed_high"],
                         3_000_000_000)
        self.assertIsNone(proof["ollama_ram_peak_bytes"])
        self.assertIsNone(proof["ollama_vram_peak_bytes"])
        self.assertFalse(proof["release_ready"])
        with self.assertRaises(ValueError):
            self.receipt(allocation=observed)
        with self.assertRaises(ValueError):
            self.receipt(live_model=True, allocation=OllamaAllocation(
                observations=1, loaded_model_bytes_high=10,
                loaded_model_vram_bytes_high=20))
        with self.assertRaises(ValueError):
            self.receipt(live_model=True, allocation=OllamaAllocation(
                observations=0, loaded_model_bytes_high=1))

    def test_receipt_rejects_fake_sha_or_invalid_peak_and_duration(self):
        for changes in (
            {"agent_sha": "bad"},
            {"model_digest": "broken"},
            {"base_sha": "d" * 40},
            {"adapter_peak": 257 * 1024**2},
            {"adapter_peak": -1},
            {"adapter_peak": True},
            {"duration_ms": 0},
            {"duration_ms": 9_999_999},
            {"live_model": "probably"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.receipt(**changes)

    def test_receipt_is_atomic_and_cannot_overwrite_existing_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root).resolve() / "real-test-receipt.json"
            proof = self.receipt(live_model=True)
            checksum = _publish_receipt(path, proof)
            import hashlib
            import json
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(), checksum)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), proof)
            with self.assertRaisesRegex(ValueError, "replace"):
                _publish_receipt(path, self.receipt())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), proof)
            with self.assertRaises(ValueError):
                _publish_receipt(Path("relative.json"), proof)
            with self.assertRaises(ValueError):
                _publish_receipt(path.parent / "receipt.txt", proof)


if __name__ == "__main__":
    unittest.main()
