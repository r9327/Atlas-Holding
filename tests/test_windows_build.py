"""Windows setup build contract without uploading or running a release artifact."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.windows_build import build_windows_installer


class WindowsBuildTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.manifest = base / "assets.json"
        self.output = base / "dist"
        self.project = base / "project"
        (self.project / "holding").mkdir(parents=True)
        (self.project / "holding" / "windows_setup.py").write_text("pass")
        self.manifest.write_text(json.dumps({
            "version": 1,
            "assets": [{
                "component": "git", "version": "2.47",
                "url": "https://example.invalid/git.zip",
                "sha256": "a" * 64, "download_bytes": 300,
                "unpacked_bytes": 500,
            }],
        }), encoding="utf-8")
        self.digest = hashlib.sha256(self.manifest.read_bytes()).hexdigest()

    def call_build(self):
        return build_windows_installer(
            manifest=self.manifest, expected_manifest_sha256=self.digest,
            output_dir=self.output, project_root=self.project,
        )

    def test_non_windows_refused_before_outputs_created(self):
        with patch("holding.windows_build.sys.platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "Windows"):
                self.call_build()
        self.assertFalse(self.output.exists())

    def test_manifest_pin_mismatch_fails_before_pyinstaller(self):
        self.digest = "0" * 64
        with patch("holding.windows_build.sys.platform", "win32"):
            with patch("holding.windows_build.subprocess.run") as runner:
                with self.assertRaises(ValueError):
                    self.call_build()
        runner.assert_not_called()

    def test_verified_bundle_report_and_do_not_publish_release(self):
        calls = []
        def build(command, *, cwd, check, timeout, shell):
            self.assertTrue(check)
            self.assertFalse(shell)
            self.assertEqual(timeout, 900)
            self.assertIn("--onefile", command)
            self.assertIn("--windowed", command)
            self.assertIn("--noupx", command)
            self.assertIn("AtlasHoldingSetup", command)
            self.assertEqual(cwd, self.project)
            stage = Path(command[command.index("--distpath") + 1])
            stage.mkdir(parents=True)
            pin_path = Path(command[command.index("--add-data") + 1].split(":")[0])
            self.assertEqual(pin_path.name, "release-assets.json")
            self.assertEqual(hashlib.sha256(pin_path.read_bytes()).hexdigest(), self.digest)
            shim = Path(command[-1]).read_text(encoding="utf-8")
            self.assertIn(self.digest, shim)
            self.assertIn("holding.windows_setup", shim)
            (stage / "AtlasHoldingSetup.exe").write_bytes(b"MZ" + b"0" * 1500)
            calls.append(True)

        with patch("holding.windows_build.sys.platform", "win32"):
            with patch("holding.windows_build.subprocess.run", side_effect=build):
                result = self.call_build()
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.manifest_sha256, self.digest)
        self.assertGreater(result.bytes, 1024)
        self.assertEqual(result.sha256, hashlib.sha256(
            Path(result.artifact).read_bytes()).hexdigest())
        self.assertTrue(Path(result.artifact).is_file())

    def test_refuses_to_replace_an_existing_installer(self):
        self.output.mkdir()
        destination = self.output / "AtlasHoldingSetup.exe"
        destination.write_bytes(b"original")
        with patch("holding.windows_build.sys.platform", "win32"):
            with patch("holding.windows_build.subprocess.run") as run:
                with self.assertRaises(FileExistsError):
                    self.call_build()
        self.assertEqual(destination.read_bytes(), b"original")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
