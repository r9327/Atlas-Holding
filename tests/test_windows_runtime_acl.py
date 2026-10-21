"""Native runtime ACL policy: tested on an actual Windows runner."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from holding.windows_runtime_acl import (
    RuntimeAclError, secure_runtime_acl, validate_runtime_acl,
)


class NativeRuntimeAclTests(unittest.TestCase):
    def test_non_windows_cannot_fake_a_windows_acl_gate(self):
        if os.name == "nt":
            self.skipTest("Native Windows tests run below")
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root).resolve()
            with self.assertRaises(RuntimeAclError):
                secure_runtime_acl(directory)
            with self.assertRaises(RuntimeAclError):
                validate_runtime_acl(directory)

    @unittest.skipUnless(os.name == "nt", "Real Windows security descriptor required")
    def test_hardened_directory_and_file_have_protected_owner_only_dacl(self):
        with tempfile.TemporaryDirectory(prefix="atlas-acl-") as temp:
            root = Path(temp).resolve()
            directory = root / "runtime"
            directory.mkdir()
            binary = directory / "AtlasHoldingEngine.exe"
            binary.write_bytes(b"MZ" + b"example")
            secure_runtime_acl(directory)
            secure_runtime_acl(binary)
            validate_runtime_acl(directory)
            validate_runtime_acl(binary)
            self.assertEqual(binary.read_bytes(), b"MZexample")

    @unittest.skipUnless(os.name == "nt", "Real Windows security descriptor required")
    def test_unprotected_inherited_acl_is_not_a_runtime_security_proof(self):
        with tempfile.TemporaryDirectory(prefix="atlas-inherit-") as temp:
            root = Path(temp).resolve()
            candidate = root / "engine.exe"
            candidate.write_bytes(b"MZ" + b"runtime")
            # TemporaryDirectory paths generally inherit their host ACL.
            # If the runner itself has a protected DACL, explicitly construct
            # a protected directory then create a child with inherited ACEs.
            secure_runtime_acl(root)
            inherited = root / "unprotected"
            inherited.mkdir()
            try:
                validate_runtime_acl(inherited)
            except RuntimeAclError:
                pass
            else:
                self.fail("Unprotected inherited runtime ACL was accepted")
            secure_runtime_acl(inherited)
            validate_runtime_acl(inherited)

    @unittest.skipUnless(os.name == "nt", "Real Windows security descriptor required")
    def test_everyone_ace_is_rejected_after_external_permission_change(self):
        # Actively loosen the ACL through the real Win32 API, as could happen
        # through another installer or a mistaken repair utility.
        import ctypes
        from ctypes import wintypes
        import holding.windows_runtime_acl as native

        with tempfile.TemporaryDirectory(prefix="atlas-acl-tamper-") as temp:
            root = Path(temp).resolve()
            file = root / "AtlasHoldingEngine.exe"
            file.write_bytes(b"MZ" + b"native-security-test")
            secure_runtime_acl(file)
            validate_runtime_acl(file)
            descriptor = ctypes.c_void_p()
            size = wintypes.DWORD()
            if not native._advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    "D:P(A;;FA;;;WD)", 1,
                    ctypes.byref(descriptor), ctypes.byref(size)):
                self.fail("Test could not construct a real Everyone ACE")
            try:
                if not native._advapi.SetFileSecurityW(
                        str(file), native._DACL | native._PROTECTED_DACL,
                        descriptor):
                    self.fail("Test could not apply an intentionally weak ACL")
            finally:
                native._kernel.LocalFree(descriptor)
            with self.assertRaisesRegex(RuntimeAclError, "unsafe permissions"):
                validate_runtime_acl(file)

    @unittest.skipUnless(os.name == "nt", "Real Windows security descriptor required")
    def test_runtime_payload_enforces_acl_on_first_stage_and_reuse(self):
        import hashlib
        from holding.windows_runtime_payload import install_pinned_runtime_pair

        with tempfile.TemporaryDirectory(prefix="atlas-payload-acl-") as temp:
            root = Path(temp).resolve()
            source = root / "bundle"
            install = root / "selected disk"
            source.mkdir()
            install.mkdir()
            pins = {}
            for index, name in enumerate(
                ("AtlasHoldingEngine.exe", "AtlasHoldingControlCenter.exe")
            ):
                file = source / name
                file.write_bytes(bytes([65 + index]) * 1_000_111)
                pins[name] = hashlib.sha256(file.read_bytes()).hexdigest()
            installed = install_pinned_runtime_pair(
                source, install, pins, approved=True)
            self.assertFalse(installed.ready)
            validate_runtime_acl(install / "runtime")
            validate_runtime_acl(installed.directory)
            for name in pins:
                validate_runtime_acl(installed.directory / name)
            second = install_pinned_runtime_pair(
                source, install, pins, approved=True)
            self.assertEqual(second.directory, installed.directory)


if __name__ == "__main__":
    unittest.main()
