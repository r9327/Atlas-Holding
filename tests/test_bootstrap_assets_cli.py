"""The trusted installer pin comes from the release, not untrusted JSON."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.bootstrap_assets_cli import (
    BootstrapManifestError, main, read_pinned_manifest,
)
from holding.staged_install import InstallError, InstallResult


class BootstrapAssetsCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / "assets.json"
        self.install = self.root / "installed"
        self.cache = self.root / "cache"
        self.assets = [{
            "component": "ollama-portable", "version": "1.1",
            "url": "https://example.invalid/ollama.zip",
            "sha256": "a" * 64, "download_bytes": 100,
            "unpacked_bytes": 500,
        }]

    def pin(self, *, raw=None):
        if raw is None:
            raw = json.dumps({"version": 1, "assets": self.assets}).encode()
        self.manifest.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def args(self, digest):
        return [
            "--manifest", str(self.manifest),
            "--manifest-sha256", digest,
            "--install-root", str(self.install),
            "--cache-root", str(self.cache),
        ]

    def test_preview_is_read_only_and_explicit_about_approval(self):
        digest = self.pin()
        captured = io.StringIO()
        with patch("holding.bootstrap_assets_cli.install_versioned_zip") as install:
            with redirect_stdout(captured):
                result = main(self.args(digest))
        self.assertEqual(result, 0)
        install.assert_not_called()
        payload = json.loads(captured.getvalue())
        self.assertEqual(payload["state"], "PREVIEW_ONLY")
        self.assertEqual(payload["assets"][0]["status"], "AWAITING_APPROVAL")
        self.assertFalse(self.install.exists())
        self.assertFalse(self.cache.exists())

    def test_explicit_approval_installs_only_pinned_asset(self):
        digest = self.pin()
        with patch(
            "holding.bootstrap_assets_cli.install_versioned_zip",
            return_value=InstallResult("INSTALLED", self.install / "v1", 2),
        ) as install:
            captured = io.StringIO()
            with redirect_stdout(captured):
                code = main(self.args(digest) + ["--approve-install"])
        self.assertEqual(code, 0)
        self.assertEqual(install.call_count, 1)
        args, kwargs = install.call_args
        self.assertEqual(args[0].component, "ollama-portable")
        self.assertEqual(kwargs["authorized"], True)
        self.assertEqual(json.loads(captured.getvalue())["state"],
                         "PAYLOADS_STAGED_NOT_VALIDATED")

    def test_failed_install_reports_blocker_not_false_completion(self):
        digest = self.pin()
        with patch(
            "holding.bootstrap_assets_cli.install_versioned_zip",
            side_effect=InstallError("secret?token=never-print"),
        ):
            captured = io.StringIO()
            with redirect_stdout(captured):
                code = main(self.args(digest) + ["--approve-install"])
        self.assertEqual(code, 2)
        self.assertNotIn("never-print", captured.getvalue())
        payload = json.loads(captured.getvalue())
        self.assertEqual(payload["state"], "PARTIAL_OR_BLOCKED")
        self.assertEqual(payload["assets"][0]["status"], "BLOCKED")

    def test_rejects_incorrect_external_hash_and_duplicate_keys(self):
        good = self.pin()
        with self.assertRaisesRegex(BootstrapManifestError, "release pin"):
            read_pinned_manifest(self.manifest, expected_sha256="0" * 64)
        raw = b'{"version":1,"version":1,"assets":[]}'
        with self.assertRaises(BootstrapManifestError):
            read_pinned_manifest(self.manifest, expected_sha256=self.pin(raw=raw))
        self.assertNotEqual(good, hashlib.sha256(raw).hexdigest())

    def test_rejects_untrusted_network_and_duplicate_version(self):
        bad_urls = (
            "http://other.example/assets.zip",
            "https://user:secret@host.example/a.zip",
            "file:///C:/Downloads/a.zip",
        )
        for url in bad_urls:
            with self.subTest(url=url):
                self.assets[0]["url"] = url
                digest = self.pin()
                with self.assertRaises(BootstrapManifestError):
                    read_pinned_manifest(self.manifest, expected_sha256=digest)
        self.assets[0]["url"] = "https://example.invalid/a.zip"
        self.assets.append({**self.assets[0], "component": "OLLAMA-PORTABLE"})
        with self.assertRaisesRegex(BootstrapManifestError, "Duplicate component"):
            read_pinned_manifest(self.manifest, expected_sha256=self.pin())

    def test_bad_sizes_and_unknown_keys_fail_closed(self):
        self.assets[0]["unpacked_bytes"] = -1
        with self.assertRaises(BootstrapManifestError):
            read_pinned_manifest(self.manifest, expected_sha256=self.pin())
        self.assets[0]["unpacked_bytes"] = 500
        self.assets[0]["random_extra"] = "unexpected"
        with self.assertRaises(BootstrapManifestError):
            read_pinned_manifest(self.manifest, expected_sha256=self.pin())

    def test_invalid_manifest_never_creates_install_dir(self):
        captured = io.StringIO()
        digest = self.pin(raw=b"not-json")
        with redirect_stdout(captured):
            code = main(self.args(digest) + ["--approve-install"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(captured.getvalue())["state"], "BLOCKED")
        self.assertFalse(self.install.exists())

    def test_rejects_relative_paths(self):
        digest = self.pin()
        argv = self.args(digest)
        argv[argv.index("--install-root") + 1] = "relative"
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(argv)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["state"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
