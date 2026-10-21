"""MinGit candidate builds only from exact bytes of the official ZIP."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from holding.bootstrap_assets_cli import read_pinned_manifest
from holding.mingit_candidate import (
    MINGIT_EXECUTABLE, MINGIT_URL, MINGIT_PACKAGE_VERSION,
    MINGIT_NATIVE_GIT_VERSION, manifest_for_candidate,
    prepare_mingit_candidate,
)
from holding.staged_install import InstallError, PinnedZip
from holding.windows_package_manager import WingetPin


class MinGitCandidateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.zip = self.folder / "official-mingit.zip"
        with zipfile.ZipFile(self.zip, "w") as target:
            target.writestr(MINGIT_EXECUTABLE, b"mock-git-no-execution")
            target.writestr("etc/gitconfig", b"[core]\n")
        self.digest = hashlib.sha256(self.zip.read_bytes()).hexdigest()

    def prepare(self):
        with patch("holding.mingit_candidate.MINGIT_SHA256", self.digest):
            return prepare_mingit_candidate(self.zip)

    def test_exact_zip_sizes_and_sha_pin_are_derived_not_guessed(self):
        pins = self.prepare()
        self.assertEqual(len(pins), 3)
        self.assertTrue(isinstance(pins[0], PinnedZip))
        self.assertEqual(pins[0].download_bytes, self.zip.stat().st_size)
        self.assertEqual(
            pins[0].unpacked_bytes,
            len(b"mock-git-no-execution") + len(b"[core]\n"),
        )
        self.assertEqual(pins[0].sha256, self.digest)
        self.assertEqual(pins[0].url, MINGIT_URL)
        self.assertEqual(pins[0].version, MINGIT_PACKAGE_VERSION)
        self.assertEqual(pins[0].expected_version, MINGIT_NATIVE_GIT_VERSION)
        self.assertEqual(pins[0].executable, "cmd/git.exe")
        self.assertEqual([p.component for p in pins],
                         ["git", "ollama", "opencode"])
        self.assertTrue(all(isinstance(p, WingetPin) for p in pins[1:]))

    def test_manifest_is_parseable_with_external_exact_sha(self):
        blob = manifest_for_candidate(self.prepare())
        dest = self.folder / "candidate.json"
        dest.write_bytes(blob)
        trusted_sha = hashlib.sha256(blob).hexdigest()
        parsed = read_pinned_manifest(dest, expected_sha256=trusted_sha)
        self.assertEqual(parsed[0].sha256, self.digest)
        self.assertEqual(parsed[0].executable, "cmd/git.exe")
        self.assertEqual([p.component for p in parsed],
                         ["git", "ollama", "opencode"])

    def test_incorrect_publisher_digest_is_rejected_before_zip_inspection(self):
        with self.assertRaisesRegex(ValueError, "official"):
            prepare_mingit_candidate(self.zip)

    def test_missing_git_executable_is_rejected(self):
        with zipfile.ZipFile(self.zip, "w") as target:
            target.writestr("other/tool.exe", b"not-git")
        digest = hashlib.sha256(self.zip.read_bytes()).hexdigest()
        with patch("holding.mingit_candidate.MINGIT_SHA256", digest):
            with self.assertRaisesRegex(ValueError, "cmd/git.exe"):
                prepare_mingit_candidate(self.zip)

    def test_relative_paths_and_linked_archives_refused(self):
        with self.assertRaises(ValueError):
            prepare_mingit_candidate(Path("relative.zip"))
        alias = self.folder / "alias.zip"
        try:
            alias.symlink_to(self.zip)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink unavailable on runner")
        with self.assertRaises(ValueError):
            prepare_mingit_candidate(alias)

    def test_traversal_entry_fails_after_hash_check(self):
        with zipfile.ZipFile(self.zip, "w") as target:
            target.writestr(MINGIT_EXECUTABLE, b"git")
            target.writestr("../escape.exe", b"escape")
        digest = hashlib.sha256(self.zip.read_bytes()).hexdigest()
        with patch("holding.mingit_candidate.MINGIT_SHA256", digest):
            with self.assertRaises(InstallError):
                prepare_mingit_candidate(self.zip)

    def test_pinned_versions_reject_inconsistent_manifest(self):
        pins = self.prepare()
        with self.assertRaises(ValueError):
            manifest_for_candidate(pins[:-1])


if __name__ == "__main__":
    unittest.main()
