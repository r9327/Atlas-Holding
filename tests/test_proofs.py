import hashlib
import hmac
import json
import tempfile
from pathlib import Path
import unittest

from holding.proofs import ProofVerifier, ProofError

SHA = "a" * 40
SECRET = b"This is a fixture key only, never a production secret."


class ProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.verifier = ProofVerifier(self.root, SECRET)
        artifact = self.root / "result.json"
        artifact.write_text('{"passed":true}', encoding="utf-8")
        self.payload = {
            "schema": 1, "mission_id": "m", "sha": SHA,
            "verdict": "PASS", "runner": "doctor_atlas",
            "artifact": artifact.name,
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()
        }

    def receipt(self, *, key=SECRET):
        data = json.dumps(self.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        receipt = {"payload": self.payload, "mac": hmac.new(key, data, hashlib.sha256).hexdigest()}
        (self.root / "receipt.json").write_text(json.dumps(receipt))
        return "receipt.json"

    def test_valid_receipt(self):
        proof = self.verifier.verify(self.receipt(), mission_id="m", sha=SHA)
        self.assertEqual((proof.sha, proof.verdict), (SHA, "PASS"))

    def test_forgery_fails(self):
        with self.assertRaises(ProofError):
            self.verifier.verify(self.receipt(key=b"Z" * 32), mission_id="m", sha=SHA)

    def test_stale_sha_fails(self):
        with self.assertRaises(ProofError):
            self.verifier.verify(self.receipt(), mission_id="m", sha="b" * 40)

    def test_tampered_artifact_fails(self):
        self.receipt()
        (self.root / "result.json").write_text("modified")
        with self.assertRaises(ProofError):
            self.verifier.verify("receipt.json", mission_id="m", sha=SHA)

    def test_untrusted_runner_fails(self):
        self.payload["runner"] = "model"
        with self.assertRaises(ProofError):
            self.verifier.verify(self.receipt(), mission_id="m", sha=SHA)

    def test_traversal_fails(self):
        with self.assertRaises(ProofError):
            self.verifier.verify("../receipt.json", mission_id="m", sha=SHA)

    def test_symlinked_evidence_root_refused(self):
        alias = self.root.parent / "evidence-link"
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation is not permitted on this system")
        with self.assertRaises(ProofError):
            ProofVerifier(alias, SECRET)

    def test_short_secret_fails(self):
        with self.assertRaises(ProofError):
            ProofVerifier(self.root, b"short")

    def test_symlinked_artifact_fails(self):
        self.receipt()
        artifact = self.root / "result.json"
        elsewhere = self.root / "elsewhere"
        elsewhere.write_bytes(artifact.read_bytes())
        artifact.unlink()
        artifact.symlink_to(elsewhere)
        with self.assertRaises(ProofError):
            self.verifier.verify("receipt.json", mission_id="m", sha=SHA)


if __name__ == "__main__":
    unittest.main()
