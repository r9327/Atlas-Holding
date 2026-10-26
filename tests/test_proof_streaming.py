"""Proof verification must stream large files without an unbounded read."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.proofs import ProofError, ProofVerifier

SECRET = b"Independent test fixture key of more than 32 bytes."
SHA = "a" * 40


class ProofStreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.verifier = ProofVerifier(self.root, SECRET)
        self.artifact = self.root / "large-result.bin"
        self.receipt = self.root / "signed-receipt.json"

    def sign(self, *, mission="a", verdict="PASS"):
        digest = hashlib.sha256()
        with self.artifact.open("rb") as stream:
            for chunk in iter(lambda: stream.read(256 * 1024), b""):
                digest.update(chunk)
        payload = {"schema": 1, "mission_id": mission, "sha": SHA,
                   "verdict": verdict, "runner": "doctor_atlas",
                   "artifact": self.artifact.name, "artifact_sha256": digest.hexdigest()}
        message = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode()
        mac = hmac.new(SECRET, message, hashlib.sha256).hexdigest()
        self.receipt.write_text(json.dumps({"payload": payload, "mac": mac}), encoding="utf-8")

    def test_large_verified_artifact_is_never_read_all_at_once(self):
        with self.artifact.open("wb") as stream:
            for _ in range(8):
                stream.write(b"sample data" * (256 * 1024))
        self.sign()
        # Path.read_bytes has no place in evidence verification. Even a
        # correctly signed artifact must be streamed with fixed-size buffers.
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
            proof = self.verifier.verify(self.receipt.name, mission_id="a", sha=SHA)
        self.assertEqual(proof.verdict, "PASS")

    def test_oversized_receipt_is_refused_without_read_bytes(self):
        self.artifact.write_bytes(b"test")
        self.receipt.write_bytes(b"x" * 32769)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
            with self.assertRaises(ProofError):
                self.verifier.verify(self.receipt.name, mission_id="a", sha=SHA)

    def test_tampered_streamed_artifact_fails(self):
        self.artifact.write_bytes(b"original")
        self.sign()
        self.artifact.write_bytes(b"modified")
        with self.assertRaises(ProofError):
            self.verifier.verify(self.receipt.name, mission_id="a", sha=SHA)


if __name__ == "__main__":
    unittest.main()
