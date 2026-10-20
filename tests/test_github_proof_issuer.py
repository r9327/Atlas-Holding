"""Independent signed-proof producer compatible with Holding Core verifier."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.github_full_evidence import FullEvidence, GitHubEvidenceError
from holding.github_proof_issuer import GitHubProofIssuer, _put_immutable

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40
KEY = b"Only used in isolated test runner -- never deployed as a real key"


class _Client:
    repo = REPO


class ProofIssueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = _Client()
        self.full = FullEvidence(REPO, 230, SHA, 77, 88)

    def issuer(self, *, trusted=True):
        return GitHubProofIssuer(
            self.client, KEY, self.root, isolation_check=lambda: trusted)

    def issue(self, signer):
        with patch("holding.github_proof_issuer.observe_full_pr",
                   return_value=self.full) as observer:
            result = signer.issue(mission_id="atlas-step", pr_number=230,
                                  expected_sha=SHA)
        observer.assert_called_once_with(self.client, 230)
        return result

    def test_produces_schema_verified_by_holding_core(self):
        result = self.issue(self.issuer())
        receipt = json.loads((self.root / result.receipt).read_text())
        artifact = (self.root / result.artifact).read_bytes()
        payload = receipt["payload"]
        self.assertEqual(set(payload), {
            "schema", "mission_id", "sha", "verdict", "runner",
            "artifact", "artifact_sha256"})
        self.assertEqual((payload["mission_id"], payload["sha"]),
                         ("atlas-step", SHA))
        self.assertEqual(payload["runner"], "github_certification")
        self.assertEqual(payload["artifact_sha256"], hashlib.sha256(artifact).hexdigest())
        message = json.dumps(payload, sort_keys=True,
                             separators=(",", ":"), ensure_ascii=False).encode()
        self.assertEqual(receipt["mac"],
                         hmac.new(KEY, message, hashlib.sha256).hexdigest())
        data = json.loads(artifact)
        self.assertEqual((data["run_id"], data["job_id"]), (77, 88))

    def test_same_shas_reuse_immutable_evidence(self):
        signer = self.issuer()
        previous = self.issue(signer)
        again = self.issue(signer)
        self.assertEqual(previous, again)
        self.assertEqual(len(list(self.root.glob("*.json"))), 2)

    def test_cannot_sign_without_os_separation(self):
        signer = self.issuer(trusted=False)
        with patch("holding.github_proof_issuer.observe_full_pr") as observer:
            with self.assertRaisesRegex(GitHubEvidenceError, "isolation"):
                signer.issue(mission_id="atlas-step", pr_number=230,
                             expected_sha=SHA)
            observer.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_moved_pr_sha_never_writes_receipt(self):
        self.full = FullEvidence(REPO, 230, "b" * 40, 77, 88)
        with self.assertRaises(GitHubEvidenceError):
            self.issue(self.issuer())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_modified_existing_artifact_cannot_be_signed(self):
        signer = self.issuer()
        result = self.issue(signer)
        (self.root / result.artifact).write_bytes(b"tampered artifact")
        with self.assertRaisesRegex(GitHubEvidenceError, "disagrees"):
            self.issue(signer)

    def test_crash_between_artifact_and_receipt_never_grants_pass(self):
        import holding.github_proof_issuer as module
        writer = module._put_immutable
        calls = []

        def broken(path, data):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("simulated power loss")
            writer(path, data)

        signer = self.issuer()
        with patch("holding.github_proof_issuer.observe_full_pr",
                   return_value=self.full), \
             patch("holding.github_proof_issuer._put_immutable",
                   side_effect=broken):
            with self.assertRaises(OSError):
                signer.issue(mission_id="atlas-step", pr_number=230,
                             expected_sha=SHA)
        self.assertEqual(len(list(self.root.glob("full-*.json"))), 1)
        self.assertEqual(len(list(self.root.glob("qa-*.json"))), 0)
        self.issue(signer)
        self.assertEqual(len(list(self.root.glob("qa-*.json"))), 1)

    def test_symlinked_artifact_refused(self):
        with tempfile.TemporaryDirectory() as outside:
            link = self.root / "evil.json"
            try:
                link.symlink_to(Path(outside) / "target.json")
            except OSError:
                self.skipTest("Symlink privilege unavailable")
            with self.assertRaises(GitHubEvidenceError):
                _put_immutable(link, b"data")

    def test_key_and_identity_require_strict_shapes(self):
        with self.assertRaises(GitHubEvidenceError):
            GitHubProofIssuer(self.client, b"short", self.root,
                              isolation_check=lambda: True)
        for name in ("", "../evil", "bad/id"):
            with self.subTest(name=name), self.assertRaises(GitHubEvidenceError):
                self.issuer().issue(mission_id=name, pr_number=230,
                                    expected_sha=SHA)


if __name__ == "__main__":
    unittest.main()
