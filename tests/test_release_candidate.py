"""Candidate pinned winget catalog is parseable, authentic by pinned bytes,
and deliberately not a production release without external QA/approval.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import unittest
from holding.bootstrap_assets_cli import read_pinned_manifest
from holding.windows_package_manager import WingetPin

CANDIDATE = (
    Path(__file__).resolve().parent.parent /
    "release-candidates" / "winget-windows-x64-v3.json"
)

class ReleaseCandidateTests(unittest.TestCase):
    def test_candidate_has_three_exact_cli_dependencies(self):
        content=CANDIDATE.read_bytes()
        digest=hashlib.sha256(content).hexdigest()
        pins=read_pinned_manifest(CANDIDATE,expected_sha256=digest)
        self.assertEqual(len(pins),3)
        self.assertTrue(all(isinstance(x,WingetPin) for x in pins))
        self.assertEqual(
            {pin.component:pin.package_id for pin in pins},
            {"git":"Git.Git","ollama":"Ollama.Ollama",
             "opencode":"SST.opencode"},
        )
        self.assertEqual(
            {pin.component:pin.version for pin in pins},
            {"git":"2.55.0","ollama":"0.32.8",
             "opencode":"1.18.29"},
        )
        self.assertNotIn("SST.OpenCodeDesktop", content.decode())
        self.assertNotIn("example.invalid", content.decode())

    def test_tampered_catalog_digest_must_fail_before_install(self):
        from holding.bootstrap_assets_cli import BootstrapManifestError
        with self.assertRaises(BootstrapManifestError):
            read_pinned_manifest(CANDIDATE,expected_sha256="0"*64)

if __name__=="__main__":
    unittest.main()
