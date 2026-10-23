"""Real ZIP extraction tests without networking, installation or elevation."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
import zipfile

from holding.update_stage import ReleaseStagingError, stage_release_archive


class ReleaseStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "candidate.zip"
        self.install = self.root / "installed"

    def zip(self, entries):
        with zipfile.ZipFile(self.archive, "w", zipfile.ZIP_STORED) as bundle:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                for name, value in entries:
                    bundle.writestr(name, value)
        return sha256(self.archive.read_bytes()).hexdigest()

    def stage(self, expected=None, **kw):
        return stage_release_archive(
            self.archive, self.install, release="v1.2.3",
            expected_sha256=expected or sha256(self.archive.read_bytes()).hexdigest(),
            **kw,
        )

    def test_stages_versioned_release_without_touching_active_install(self):
        digest = self.zip([
            ("bin/", b""),
            ("bin/Holding.exe", b"test exe bytes"),
            ("data/config.json", b"{}"),
        ])
        self.install.mkdir()
        active = self.install / "active.json"
        active.write_text('{"release":"v1.0"}', encoding="utf-8")
        result = self.stage(expected=digest)
        self.assertEqual(result.directory, self.install / "versions" / "v1.2.3")
        self.assertEqual(result.files, 2)
        self.assertEqual(result.unpacked_bytes, 16)
        self.assertEqual((result.directory / "bin" / "Holding.exe").read_bytes(), b"test exe bytes")
        self.assertEqual(active.read_text(), '{"release":"v1.0"}')
        receipt = json.loads((result.directory / ".atlas-stage.json").read_text())
        self.assertEqual(receipt["archive_sha256"], digest)
        self.assertEqual(receipt["schema"], "atlas-staged-release/v1")
        self.assertFalse(list((self.install / "versions").glob(".atlas-stage-*")))

    def test_invalid_checksum_cannot_create_install_directory(self):
        self.zip([("Holding.exe", b"abc")])
        with self.assertRaisesRegex(ReleaseStagingError, "SHA-256"):
            self.stage(expected="0" * 64)
        self.assertFalse(self.install.exists())

    def test_traversal_absolute_devices_streams_and_backslashes_fail(self):
        for filename in (
            "../bad", "dir/../../bad", "/absolute", "C:/Windows/win.ini",
            "dir//file", "dir/./file", "bin/CON.exe", "bin/NUL",
            "bin/file:stream", "bin/evil\\file", "bin/trailing. ",
            ".atlas-stage.json",
        ):
            with self.subTest(filename=filename):
                # ZipInfo(str) normalizes backslashes on Windows.
                # Bypass that conversion so the malformed archive is really
                # what our extractor receives on both operating systems.
                name = zipfile.ZipInfo("placeholder")
                name.filename = filename
                self.zip([(name, b"x")])
                with self.assertRaises(ReleaseStagingError):
                    self.stage()
                self.assertFalse((self.install / "versions" / "v1.2.3").exists())

    def test_case_collisions_and_implicit_directory_file_conflicts(self):
        for entries in (
            [("bin/abc", b"1"), ("BIN/def", b"2")],
            [("readme", b"x"), ("readme/child", b"y")],
            [("a", b"x"), ("a", b"y")],
        ):
            with self.subTest(entries=entries):
                self.zip(entries)
                with self.assertRaises(ReleaseStagingError):
                    self.stage()

    def test_rejects_zip_symlink(self):
        info = zipfile.ZipInfo("bin/link.exe")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.zip([(info, b"../outside")])
        with self.assertRaisesRegex(ReleaseStagingError, "symlink"):
            self.stage()

    def test_bounded_unpacked_quota_and_file_count(self):
        self.zip([("a", b"abc"), ("b", b"def")])
        with self.assertRaisesRegex(ReleaseStagingError, "size budget"):
            self.stage(max_unpacked_bytes=5)
        with self.assertRaisesRegex(ReleaseStagingError, "too many"):
            self.stage(max_files=1)
        self.assertFalse((self.install / "versions" / "v1.2.3").exists())

    def test_corrupt_archive_is_rolled_back_without_partial_version(self):
        self.zip([("hello.txt", b"hello")])
        corrupted = self.archive.read_bytes().replace(b"hello", b"hxllo", 1)
        self.archive.write_bytes(corrupted)
        with self.assertRaisesRegex(ReleaseStagingError, "safely staged"):
            self.stage()
        self.assertFalse((self.install / "versions" / "v1.2.3").exists())
        self.assertEqual(list((self.install / "versions").glob(".atlas-stage-*")), [])

    def test_existing_release_never_overwritten(self):
        self.zip([("exe", b"first")])
        first = self.stage()
        with self.assertRaisesRegex(ReleaseStagingError, "already exists"):
            self.stage()
        self.assertEqual((first.directory / "exe").read_bytes(), b"first")

    def test_symlink_archive_and_version_root_rejected(self):
        self.zip([("exe", b"first")])
        link = self.root / "linked.zip"
        try:
            link.symlink_to(self.archive)
        except (OSError, NotImplementedError):
            self.skipTest("filesystem symlinks unavailable")
        with self.assertRaisesRegex(ReleaseStagingError, "non-symlink"):
            stage_release_archive(
                link, self.install, release="v1.2.3",
                expected_sha256=sha256(self.archive.read_bytes()).hexdigest(),
            )
        (self.root / "other").mkdir()
        self.install.mkdir()
        (self.install / "versions").symlink_to(self.root / "other", target_is_directory=True)
        with self.assertRaisesRegex(ReleaseStagingError, "unsafe"):
            self.stage()

    def test_bad_release_id_and_bool_quotas_rejected(self):
        self.zip([("exe", b"first")])
        for release in ("../escape", ".", "..", "bad/name", "bad ", "", "v" * 90, "CON", "LPT1.txt"):
            with self.subTest(release=release), self.assertRaises(ReleaseStagingError):
                stage_release_archive(
                    self.archive, self.install, release=release,
                    expected_sha256=sha256(self.archive.read_bytes()).hexdigest(),
                )
        with self.assertRaises(ReleaseStagingError):
            self.stage(max_files=True)

    def test_empty_archive_refused(self):
        self.zip([("bin/", b"")])
        with self.assertRaisesRegex(ReleaseStagingError, "empty"):
            self.stage()


if __name__ == "__main__":
    unittest.main()
