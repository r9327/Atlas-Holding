"""Both official portable archives must pass before any manifest is usable."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from holding.bootstrap_assets_cli import read_pinned_manifest
from holding.mingit_candidate import MINGIT_EXECUTABLE
from holding.portable_runtime import (
    OPENCODE_EXECUTABLE, OPENCODE_SHA256, OPENCODE_URL, OPENCODE_VERSION,
    prepare_portable_manifest, _verified_official_zip,
)
from holding.staged_install import InstallError, PinnedZip
from holding.windows_package_manager import WingetPin


class PortableRuntimeTests(unittest.TestCase):
    def setUp(self):
        context = tempfile.TemporaryDirectory()
        self.addCleanup(context.cleanup)
        self.folder = Path(context.name)
        self.git = self.folder / "MinGit-official.zip"
        self.code = self.folder / "OpenCode-official.zip"
        with zipfile.ZipFile(self.git, "w") as z:
            z.writestr(MINGIT_EXECUTABLE, b"real-git-fixture")
        with zipfile.ZipFile(self.code, "w") as z:
            z.writestr(OPENCODE_EXECUTABLE, b"real-code-fixture")
        self.sha_git = hashlib.sha256(self.git.read_bytes()).hexdigest()
        self.sha_code = hashlib.sha256(self.code.read_bytes()).hexdigest()

    def prepare(self):
        with patch("holding.portable_runtime.MINGIT_SHA256", self.sha_git):
            with patch("holding.portable_runtime.OPENCODE_SHA256",
                       self.sha_code):
                return prepare_portable_manifest(self.git, self.code)

    def test_official_zip_shape_and_real_measurements(self):
        blob = self.prepare()
        out = self.folder / "manifest.json"
        out.write_bytes(blob)
        pins = read_pinned_manifest(
            out, expected_sha256=hashlib.sha256(blob).hexdigest(),
        )
        self.assertEqual([p.component for p in pins],
                         ["git", "ollama", "opencode"])
        self.assertIsInstance(pins[0], PinnedZip)
        self.assertIsInstance(pins[1], WingetPin)
        self.assertIsInstance(pins[2], PinnedZip)
        self.assertEqual(pins[2].url, OPENCODE_URL)
        self.assertEqual(pins[2].version, OPENCODE_VERSION)
        self.assertEqual(pins[2].sha256, self.sha_code)
        self.assertEqual(pins[2].download_bytes, self.code.stat().st_size)
        self.assertEqual(pins[2].unpacked_bytes, len(b"real-code-fixture"))
        self.assertEqual(pins[2].executable, OPENCODE_EXECUTABLE)

    def test_publisher_sha_mismatch_blocks_whole_manifest(self):
        with patch("holding.portable_runtime.MINGIT_SHA256", self.sha_git):
            with self.assertRaisesRegex(ValueError, "SHA256"):
                prepare_portable_manifest(self.git, self.code)

    def test_missing_opencode_executable_rejected(self):
        with zipfile.ZipFile(self.code, "w") as z:
            z.writestr("not-opencode.exe", b"wrong")
        digest = hashlib.sha256(self.code.read_bytes()).hexdigest()
        with patch("holding.portable_runtime.OPENCODE_SHA256", digest):
            with patch("holding.portable_runtime.MINGIT_SHA256", self.sha_git):
                with self.assertRaisesRegex(ValueError, "missing native"):
                    prepare_portable_manifest(self.git, self.code)

    def test_windows_zip_traversal_rejected(self):
        with zipfile.ZipFile(self.code, "w") as z:
            z.writestr("opencode.exe", b"good")
            z.writestr("../outside.exe", b"escape")
        digest = hashlib.sha256(self.code.read_bytes()).hexdigest()
        with patch("holding.portable_runtime.OPENCODE_SHA256", digest):
            with patch("holding.portable_runtime.MINGIT_SHA256", self.sha_git):
                with self.assertRaises(InstallError):
                    prepare_portable_manifest(self.git, self.code)

    def test_rejects_linked_or_relative_archives(self):
        with self.assertRaises(ValueError):
            _verified_official_zip(
                Path("relative"), component="opencode",
                version=OPENCODE_VERSION, expected_version=OPENCODE_VERSION,
                url=OPENCODE_URL, sha256=OPENCODE_SHA256,
                executable=OPENCODE_EXECUTABLE,
            )
        linked = self.folder / "linked.zip"
        try:
            linked.symlink_to(self.code)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unsupported")
        with self.assertRaises(ValueError):
            _verified_official_zip(
                linked, component="opencode",
                version=OPENCODE_VERSION, expected_version=OPENCODE_VERSION,
                url=OPENCODE_URL, sha256=OPENCODE_SHA256,
                executable=OPENCODE_EXECUTABLE,
            )

    def test_hash_failure_leaves_no_manifest_output(self):
        with patch("holding.portable_runtime.MINGIT_SHA256", self.sha_git):
            with self.assertRaises(ValueError):
                prepare_portable_manifest(self.git, self.code)
        self.assertFalse((self.folder / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
