"""Frozen Windows agent installation and HKCU login registration tests."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from holding import windows_autostart as target


class FakeKey:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class RegistryStub:
    def __init__(self):
        self.values = {}
        self.HKEY_CURRENT_USER = 42
        self.KEY_QUERY_VALUE = 1
        self.KEY_SET_VALUE = 2
        self.REG_SZ = 1
        self.writes = 0
        self.deletes = 0

    def CreateKeyEx(self, hive, path, _, access):
        assert hive == self.HKEY_CURRENT_USER
        assert path == target.REGISTRY_PATH
        return FakeKey(self.values)

    def OpenKey(self, hive, path, _, access):
        return FakeKey(self.values)

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError()
        return self.values[name]

    def SetValueEx(self, key, name, _, kind, command):
        self.writes += 1
        self.values[name] = (command, kind)

    def DeleteValue(self, key, name):
        self.deletes += 1
        del self.values[name]


class AutostartTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.install = self.base / "Atlas Setup Folder"
        self.source = self.base / "AtlasHoldingSetup.exe"
        self.source.write_bytes(b"MZ" + b"x" * (1024 * 1024))
        self.registry = RegistryStub()

    def install_agent(self, *, authorized=True):
        with patch("holding.windows_autostart.platform.system",
                   return_value="Windows"):
            with patch.object(target.sys, "frozen", True, create=True):
                with patch.object(target.sys, "executable", str(self.source)):
                    return target.install_frozen_agent(
                        self.install, authorized=authorized,
                    )

    def registered(self, *, authorized=True, digest=None):
        with patch("holding.windows_autostart.platform.system",
                   return_value="Windows"):
            with patch.dict(sys.modules, {"winreg": self.registry}):
                return target.enable_logon_resume(
                    self.install, agent_sha256=digest or
                    hashlib.sha256(self.source.read_bytes()).hexdigest(),
                    authorized=authorized,
                )

    def test_no_consent_creates_neither_files_nor_run_key(self):
        result = self.install_agent(authorized=False)
        self.assertEqual(result.status, "AUTH_REQUIRED")
        self.assertFalse(self.install.exists())
        with patch("holding.windows_autostart.platform.system",
                   return_value="Windows"):
            self.assertEqual(
                target.enable_logon_resume(
                    self.install, agent_sha256="a" * 64, authorized=False,
                ), "AUTH_REQUIRED",
            )
        self.assertEqual(self.registry.writes, 0)

    def test_stages_only_frozen_windows_exe_and_reuses_identical_bytes(self):
        first = self.install_agent()
        self.assertEqual(first.status, "HOST_STAGED")
        self.assertEqual(
            hashlib.sha256(Path(first.executable).read_bytes()).hexdigest(),
            first.sha256,
        )
        second = self.install_agent()
        self.assertEqual(second.status, "HOST_REUSED")
        self.assertEqual(first.executable, second.executable)

    def test_different_host_bytes_cannot_be_overwritten(self):
        first = self.install_agent()
        original = Path(first.executable).read_bytes()
        self.source.write_bytes(b"MZ" + b"y" * (1024 * 1024))
        result = self.install_agent()
        self.assertEqual(result.status, "HOST_DIFFERENT_VERSION")
        self.assertEqual(Path(first.executable).read_bytes(), original)

    def test_non_frozen_or_non_windows_is_blocked(self):
        with patch("holding.windows_autostart.platform.system",
                   return_value="Windows"):
            with patch.object(target.sys, "frozen", False, create=True):
                result = target.install_frozen_agent(
                    self.install, authorized=True,
                )
        self.assertEqual(result.status, "FROZEN_WINDOWS_HOST_REQUIRED")

    def test_registry_command_is_exact_scoped_and_idempotent(self):
        first = self.install_agent()
        self.assertEqual(
            self.registered(digest=first.sha256),
            "LOGON_RESUME_ENROLLED",
        )
        self.assertEqual(
            self.registered(digest=first.sha256),
            "ALREADY_ENROLLED",
        )
        self.assertEqual(self.registry.writes, 1)
        command, kind = self.registry.values[target.REGISTRY_VALUE]
        self.assertEqual(kind, self.registry.REG_SZ)
        self.assertEqual(
            command, subprocess.list2cmdline([
                first.executable, "--resume-ollama", str(self.install),
            ]),
        )
        self.assertLessEqual(len(command), 260)

    def test_foreign_registry_value_never_overwritten_or_deleted(self):
        first = self.install_agent()
        self.registry.values[target.REGISTRY_VALUE] = (
            "C:/OtherSoftware/start.exe", self.registry.REG_SZ,
        )
        self.assertEqual(self.registered(digest=first.sha256),
                         "FOREIGN_AUTOSTART_CONFLICT")
        with patch("holding.windows_autostart.platform.system",
                   return_value="Windows"):
            with patch.dict(sys.modules, {"winreg": self.registry}):
                result = target.disable_logon_resume(self.install)
        self.assertEqual(result, "FOREIGN_AUTOSTART_CONFLICT")
        self.assertEqual(self.registry.writes, 0)
        self.assertEqual(self.registry.deletes, 0)

    def test_disable_only_removes_the_exact_atlas_registration(self):
        first = self.install_agent()
        self.assertEqual(self.registered(digest=first.sha256),
                         "LOGON_RESUME_ENROLLED")
        with patch("holding.windows_autostart.platform.system",
                   return_value="Windows"):
            with patch.dict(sys.modules, {"winreg": self.registry}):
                self.assertEqual(
                    target.disable_logon_resume(self.install),
                    "LOGON_RESUME_DISABLED",
                )
        self.assertEqual(self.registry.deletes, 1)
        self.assertNotIn(target.REGISTRY_VALUE, self.registry.values)

    def test_hash_change_prevents_autostart_registration(self):
        first = self.install_agent()
        Path(first.executable).write_bytes(b"MZ" + b"z" * 1024 * 1024)
        self.assertEqual(
            self.registered(digest=first.sha256),
            "AGENT_CHANGED_OR_MISSING",
        )
        self.assertEqual(self.registry.writes, 0)

    def test_orchestrator_requires_persisted_model_volume_before_copy(self):
        from holding.ollama_storage import StoredModelDirectory
        with patch("holding.windows_autostart.read_model_directory",
                   return_value=StoredModelDirectory("BLOCKED_INVALID")):
            with patch("holding.windows_autostart.install_frozen_agent") as copy:
                state = target.configure_logon_resume(
                    self.install, authorized=True,
                )
        self.assertEqual(state, "MODEL_VOLUME_BLOCKED_INVALID")
        copy.assert_not_called()

    def test_orchestrator_registers_only_after_verified_frozen_host(self):
        from holding.ollama_storage import StoredModelDirectory
        with patch("holding.windows_autostart.read_model_directory",
                   return_value=StoredModelDirectory(
                       "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED",
                       self.base,
                   )):
            with patch("holding.windows_autostart.platform.system",
                       return_value="Windows"):
                with patch.object(target.sys, "frozen", True, create=True):
                    with patch.object(target.sys, "executable",
                                      str(self.source)):
                        with patch.dict(sys.modules, {"winreg": self.registry}):
                            state = target.configure_logon_resume(
                                self.install, authorized=True,
                            )
        self.assertEqual(state, "LOGON_RESUME_ENROLLED")
        self.assertEqual(self.registry.writes, 1)

    def test_oversized_run_command_is_refused(self):
        long_root = self.base / ("very-long-folder-name" * 15)
        with self.assertRaises(ValueError):
            target._command(long_root, long_root / target.AGENT_FILENAME)


if __name__ == "__main__":
    unittest.main()
