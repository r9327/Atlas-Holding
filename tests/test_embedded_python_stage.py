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

    def test_installed_runtime_receipt_detects_module_native_and_policy_tamper(self):
        from holding.embedded_python_stage import verify_staged_embedded_python
        observed = self.stage()
        verified = verify_staged_embedded_python(
            directory=observed.directory, pin=self.pin)
        self.assertEqual(verified.python_exe, observed.python_exe)
        self.assertEqual(verified.status, "STAGED_NOT_EXECUTED")
        binary = observed.directory / "python3.dll"
        good = binary.read_bytes()
        binary.write_bytes(b"tampered DLL")
        with self.assertRaises(EmbeddedRuntimeError):
            verify_staged_embedded_python(directory=observed.directory, pin=self.pin)
        binary.write_bytes(good)
        code = observed.directory / "holding" / "mcp_entry.py"
        module_before = code.read_bytes()
        code.write_bytes(b"print('attacker')")
        with self.assertRaises(EmbeddedRuntimeError):
            verify_staged_embedded_python(directory=observed.directory, pin=self.pin)
        code.write_bytes(module_before)
        policy = observed.directory / "python313._pth"
        policy.write_text("python313.zip\\n.\\nimport site\\n", encoding="ascii")
        with self.assertRaises(EmbeddedRuntimeError):
            verify_staged_embedded_python(directory=observed.directory, pin=self.pin)

    def test_installed_runtime_receipt_rejects_forged_extra_files(self):
        from holding.embedded_python_stage import verify_staged_embedded_python
        observed = self.stage()
        (observed.directory / "evil.dll").write_bytes(b"untrusted")
        with self.assertRaises(EmbeddedRuntimeError):
            verify_staged_embedded_python(directory=observed.directory, pin=self.pin)
        (observed.directory / "evil.dll").unlink()
        receipt = observed.directory / "atlas-python-embedded-receipt.json"
        data = json.loads(receipt.read_text())
        data["source_release_zip_sha256"] = "0" * 64
        receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(EmbeddedRuntimeError, "release pins"):
            verify_staged_embedded_python(directory=observed.directory, pin=self.pin)

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


class WindowsSetupMcpCompanionTests(unittest.TestCase):
    """Real ZIP/file staging; only the native ACL interface is patched."""
    setUp = EmbeddedPythonStageTests.setUp
    make_archive = EmbeddedPythonStageTests.make_archive
    stage = EmbeddedPythonStageTests.stage

    def test_approved_companion_is_staged_under_chosen_install_drive(self):
        from unittest.mock import patch
        from holding.windows_setup import stage_embedded_mcp_companion
        with (
            patch("holding.windows_engine_enrollment._require_native_windows"),
            patch("holding.windows_setup.validate_runtime_acl"),
            patch("holding.windows_setup.secure_runtime_acl"),
        ):
            enrolled = stage_embedded_mcp_companion(
                install_root=self.dest, archive=self.archive,
                trusted_source_root=self.package_root,
                pin=self.pin, approved=True)
        self.assertTrue(enrolled.directory.is_relative_to(self.dest / "mcp-python"))
        self.assertEqual(enrolled.status, "STAGED_NOT_EXECUTED")
        self.assertEqual(enrolled.interpreter_sha256, sha(self.members["python.exe"]))

    def test_companion_requires_explicit_setup_approval(self):
        from unittest.mock import patch
        from holding.windows_setup import stage_embedded_mcp_companion
        with patch("holding.windows_engine_enrollment._require_native_windows") as native:
            with self.assertRaises(ValueError):
                stage_embedded_mcp_companion(
                    install_root=self.dest, archive=self.archive,
                    trusted_source_root=self.package_root, pin=self.pin,
                    approved=False)
            native.assert_not_called()
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_failed_native_acl_rolls_back_only_new_companion(self):
        from unittest.mock import patch
        from holding.windows_setup import stage_embedded_mcp_companion
        from holding.windows_runtime_acl import RuntimeAclError
        with (
            patch("holding.windows_engine_enrollment._require_native_windows"),
            patch("holding.windows_setup.validate_runtime_acl",
                  side_effect=[None, None, None, RuntimeAclError("Native ACL rejected")]),
            patch("holding.windows_setup.secure_runtime_acl"),
        ):
            with self.assertRaises(RuntimeAclError):
                stage_embedded_mcp_companion(
                    install_root=self.dest, archive=self.archive,
                    trusted_source_root=self.package_root,
                    pin=self.pin, approved=True)
        self.assertEqual(list(self.dest.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
