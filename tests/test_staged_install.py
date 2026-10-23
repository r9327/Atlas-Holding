"""Pinned asset extraction: no arbitrary execution, replacement or path escapes."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from holding.staged_install import (InstallError, PinnedZip, install_versioned_zip,
                                    inspect_versioned_zip)


def archive_file(entries: list[tuple[str, bytes | None]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zipout:
        for name, contents in entries:
            zipout.writestr(name, contents if contents is not None else b"")
    return buffer.getvalue()


class StagedInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "installation"
        self.cache = Path(self.temp.name) / "cache"
        self.data = archive_file([
            ("bin/", None),
            ("bin/runner.exe", b"versioned-runtime"),
            ("readme.txt", b"Atlas test fixture"),
        ])
        self.calls = 0

    def spec(self, *, data=None, unpacked_bytes=None) -> PinnedZip:
        contents = self.data if data is None else data
        return PinnedZip(
            "runner", "1.2.3", "https://example.invalid/runtime.zip",
            hashlib.sha256(contents).hexdigest(), len(contents),
            len(b"versioned-runtime") + len(b"Atlas test fixture")
            if unpacked_bytes is None else unpacked_bytes,
        )

    def install(self, spec=None, *, authorized=True, cancelled=None):
        spec = spec or self.spec()
        source = self.data

        def pinned_downloader(url, destination, **kwargs):
            self.calls += 1
            self.assertEqual(url, spec.url)
            self.assertEqual(kwargs["sha256"], spec.sha256)
            self.assertEqual(kwargs["size"], spec.download_bytes)
            destination = Path(destination)
            destination.write_bytes(source)
            return destination

        with patch("holding.staged_install.download_pinned",
                   side_effect=pinned_downloader):
            return install_versioned_zip(
                spec, install_root=self.root, cache_root=self.cache,
                authorized=authorized, cancelled=cancelled,
            )

    def test_readonly_inspection_does_not_create_install_or_cache(self):
        inspection = inspect_versioned_zip(self.spec(), install_root=self.root)
        self.assertEqual(inspection.status, "NOT_INSTALLED")
        self.assertFalse(self.root.exists())
        self.assertFalse(self.cache.exists())

    def test_readonly_inspection_verifies_contents_and_refuses_corruption(self):
        installed = self.install()
        valid = inspect_versioned_zip(self.spec(), install_root=self.root)
        self.assertEqual(valid.status, "VERIFIED_PAYLOAD")
        self.assertEqual(valid.file_count, 2)
        self.assertEqual(self.calls, 1)

        (installed.directory / "bin/runner.exe").write_bytes(b"tampered")
        invalid = inspect_versioned_zip(self.spec(), install_root=self.root)
        self.assertEqual(invalid.status, "BLOCKED_INVALID")
        self.assertEqual(invalid.file_count, 0)
        self.assertEqual(self.calls, 1)

    def test_readonly_inspection_refuses_linked_root(self):
        outside = Path(self.temp.name) / "outside-inventory"
        outside.mkdir()
        try:
            self.root.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks unavailable on this runner")
        inspection = inspect_versioned_zip(self.spec(), install_root=self.root)
        self.assertEqual(inspection.status, "BLOCKED_INVALID")
        self.assertFalse(list(outside.iterdir()))

    def test_installs_then_reuses_verified_bytes(self):
        installed = self.install()
        self.assertEqual(installed.status, "INSTALLED")
        self.assertEqual(installed.file_count, 2)
        self.assertEqual((installed.directory / "bin/runner.exe").read_bytes(),
                         b"versioned-runtime")
        meta = json.loads(
            (installed.directory / ".atlas-install.json").read_text()
        )
        self.assertEqual(meta["sha256"], self.spec().sha256)
        again = self.install()
        self.assertEqual(again.status, "REUSED")
        self.assertEqual(again.directory, installed.directory)
        self.assertEqual(self.calls, 1)

    def test_rejects_corruption_without_overwrite(self):
        result = self.install()
        path = result.directory / "bin/runner.exe"
        path.write_bytes(b"local user bytes")
        with self.assertRaisesRegex(InstallError, "no longer matches pin"):
            self.install()
        self.assertEqual(path.read_bytes(), b"local user bytes")
        self.assertEqual(self.calls, 1)

    def test_existing_additional_file_cannot_be_hidden(self):
        result = self.install()
        (result.directory / "unexpected").write_bytes(b"other version")
        with self.assertRaisesRegex(InstallError, "unexpected files"):
            self.install()

    def test_install_requires_explicit_authorization(self):
        with self.assertRaisesRegex(InstallError, "authorized mandate"):
            self.install(authorized=False)
        self.assertFalse(self.root.exists())

    def test_install_requires_absolute_roots(self):
        with self.assertRaisesRegex(InstallError, "Absolute installation root"):
            install_versioned_zip(
                self.spec(), install_root=Path("relative"), cache_root=self.cache,
                authorized=True,
            )
        with self.assertRaisesRegex(InstallError, "Absolute cache root"):
            install_versioned_zip(
                self.spec(), install_root=self.root, cache_root=Path("relative"),
                authorized=True,
            )

    def test_blocks_traversal_case_collision_and_reserved_paths(self):
        for names in (
            ["../secrets.txt"],
            ["/absolute/file.txt"],
            ["C:/drive.txt"],
            ["a\\..\\evil"],
            ["CON.txt"],
            ["sub/A.txt", "sub/a.txt"],
            ["nested/ok", "nested/ok/child"],
            [".atlas-install.json"],
        ):
            with self.subTest(names=names):
                data = archive_file([(name, b"x") for name in names])
                self.data = data
                with self.assertRaises(InstallError):
                    self.install(self.spec(data=data, unpacked_bytes=len(names)))
                self.assertEqual(list(self.root.rglob(".atlas-stage-*")), [])

    def test_blocks_zip_symlink(self):
        info = zipfile.ZipInfo("evil")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as zipout:
            zipout.writestr(info, "outside")
        self.data = output.getvalue()
        with self.assertRaisesRegex(InstallError, "non-regular"):
            self.install(self.spec(data=self.data, unpacked_bytes=7))

    def test_refuses_inaccurate_unpacked_manifest_without_publish(self):
        with self.assertRaisesRegex(InstallError, "declared unpacked size"):
            self.install(self.spec(unpacked_bytes=2))
        self.assertEqual(list(self.root.rglob("*.atlas-install.json")), [])
        self.assertEqual(list(self.root.rglob(".atlas-stage-*")), [])

    def test_cancelled_extract_does_not_publish(self):
        with self.assertRaisesRegex(InstallError, "cancelled"):
            self.install(cancelled=lambda: True)
        self.assertEqual(list(self.root.rglob(".atlas-stage-*")), [])
        self.assertEqual(list(self.root.rglob(".atlas-install.json")), [])

    def test_version_collision_not_used_for_other_pin(self):
        first = self.install()
        newer = archive_file([("different.txt", b"new")])
        self.data = newer
        another = self.install(self.spec(data=newer, unpacked_bytes=3))
        self.assertNotEqual(first.directory, another.directory)
        self.assertEqual((first.directory / "readme.txt").read_bytes(),
                         b"Atlas test fixture")

    def test_linked_parent_refused(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        try:
            self.root.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks unavailable on this runner")
        with self.assertRaisesRegex(InstallError, "symlink or junction"):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
