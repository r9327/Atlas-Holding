"""Offline/portable tests of pinned CPython embeddable companion staging."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from holding.embedded_python_stage import (
    EmbeddedPythonRelease, EmbeddedRuntimeError, stage_pinned_embedded_python,
)

MODULES = (
    "__init__.py", "mcp_entry.py", "mcp_stdio.py",
    "mcp_hub_session.py", "mcp_hub_probe.py", "mcp_windows_job.py",
    "mcp_local_inventory.py",
)
NATIVE = (
    "python.exe", "python3.dll", "python313.dll",
    "python313.zip", "python313._pth",
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class EmbeddedPythonStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.package_root = self.root / "checkout"
        self.package = self.package_root / "holding"
        self.package.mkdir(parents=True)
        self.dest = self.root / "installed"
        self.dest.mkdir()
        self.archive = self.root / "python-3.13-embed-amd64.zip"
        self.modules = {}
        for name in MODULES:
            raw = ("# module " + name + "\n").encode()
            (self.package / name).write_bytes(raw)
            self.modules[name] = sha(raw)
        self.members = {name: ("embedded " + name).encode() for name in NATIVE}
        self.make_archive()
        self.pin = EmbeddedPythonRelease(
            python_minor=13, zip_sha256=sha(self.archive.read_bytes()),
            module_sha256=dict(self.modules),
        )

    def make_archive(self, members=None, **kwargs):
        if members is None:
            members = self.members
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
            for name, data in members.items():
                if isinstance(data, tuple):
                    data, attr = data
                    item = zipfile.ZipInfo(name)
                    item.create_system = 3
                    item.external_attr = attr
                    out.writestr(item, data)
                else:
                    out.writestr(name, data)

    def stage(self, pin=None):
        return stage_pinned_embedded_python(
            archive=self.archive, approved_package_root=self.package_root,
            target_parent=self.dest, pin=pin or self.pin,
        )

    def test_real_staging_retains_exact_bytes_and_writes_nonexecuted_receipt(self):
        observed = self.stage()
        self.assertEqual(observed.status, "STAGED_NOT_EXECUTED")
        self.assertTrue(observed.directory.is_dir())
        self.assertEqual(observed.python_exe.read_bytes(), self.members["python.exe"])
        self.assertEqual(observed.interpreter_sha256, sha(self.members["python.exe"]))
        self.assertEqual(
            (observed.directory / "python313._pth").read_text(),
            "python313.zip\n.\n",
        )
        for name in MODULES:
            self.assertEqual(
                (observed.directory / "holding" / name).read_bytes(),
                (self.package / name).read_bytes(),
            )
        receipt = json.loads(
            (observed.directory / "atlas-python-embedded-receipt.json").read_text()
        )
        self.assertEqual(receipt["native_probe"], "NOT_EXECUTED")
        self.assertEqual(receipt["source_release_zip_sha256"], self.pin.zip_sha256)
        self.assertEqual(receipt["module_sha256"], self.modules)
        with self.assertRaises(FileExistsError):
            self.stage()
        self.assertEqual(len(list(self.dest.iterdir())), 1)

    def test_tampered_zip_refuses_install_without_creating_install_directory(self):
        self.members["python.exe"] = b"not publisher's python"
        self.make_archive()
        with self.assertRaisesRegex(EmbeddedRuntimeError, "release hash"):
            self.stage()
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_tampered_module_refuses_install_even_with_valid_zip(self):
        (self.package / "mcp_entry.py").write_bytes(b"changed source")
        with self.assertRaisesRegex(EmbeddedRuntimeError, "differs from release"):
            self.stage()
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_unknown_extra_module_and_removed_module_are_blocked(self):
        unexpected = self.package / "evil.py"
        unexpected.write_text("print('hostile')\n")
        with self.assertRaisesRegex(EmbeddedRuntimeError, "inventory differs"):
            self.stage()
        unexpected.unlink()
        (self.package / "mcp_stdio.py").unlink()
        with self.assertRaisesRegex(EmbeddedRuntimeError, "inventory differs"):
            self.stage()
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_unsafe_zip_members_rejected_and_staging_cleaned(self):
        attacks = [
            {"../escape.exe": b"pwn"},
            {"holding/evil.py": b"pwn"},
            {"holding\\evil.py": b"pwn"},
            {"C:windows.exe": b"pwn"},
            {".hidden.py": b"pwn"},
            {"fake.pyd": (b"evil", (stat.S_IFLNK | 0o777) << 16)},
        ]
        for attack in attacks:
            with self.subTest(attack=attack):
                data = dict(self.members)
                data.update(attack)
                self.make_archive(data)
                pin = EmbeddedPythonRelease(
                    13, sha(self.archive.read_bytes()), dict(self.modules))
                with self.assertRaises(EmbeddedRuntimeError):
                    self.stage(pin=pin)
                self.assertEqual(list(self.dest.iterdir()), [])
                self.assertFalse((self.root / "escape.exe").exists())

    def test_unpinned_native_python_import_cannot_stage(self):
        shadow = self.package / "mcp_entry.pyd"
        shadow.write_bytes(b"untrusted native module")
        with self.assertRaisesRegex(EmbeddedRuntimeError, "Unpinned executable"):
            self.stage()
        shadow.unlink()
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_symlinked_module_directory_cannot_be_approved(self):
        linked = self.package_root / "holding"
        alternate = self.root / "real-holding"
        try:
            linked.rename(alternate)
            linked.symlink_to(alternate, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink unavailable on this machine")
        with self.assertRaisesRegex(EmbeddedRuntimeError, "module directory"):
            self.stage()
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_missing_embeddable_runtime_files_cannot_stage(self):
        members = dict(self.members)
        del members["python3.dll"]
        self.make_archive(members)
        pin = EmbeddedPythonRelease(
            13, sha(self.archive.read_bytes()), dict(self.modules))
        with self.assertRaisesRegex(EmbeddedRuntimeError, "Incomplete Python"):
            self.stage(pin)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_malformed_release_pin_is_never_implicitly_upgraded(self):
        for m in (
            dict(self.modules, **{"evil.py": "0" * 40}),
            {"mcp_entry.py": "a" * 64},
            dict(self.modules, **{"../escape.py": "a" * 64}),
        ):
            with self.subTest(manifest=m):
                with self.assertRaises(EmbeddedRuntimeError):
                    EmbeddedPythonRelease(13, self.pin.zip_sha256, m)
        with self.assertRaises(EmbeddedRuntimeError):
            EmbeddedPythonRelease(True, self.pin.zip_sha256, self.modules)
        with self.assertRaises(EmbeddedRuntimeError):
            EmbeddedPythonRelease(13, "a" * 40, self.modules)


if __name__ == "__main__":
    unittest.main()
