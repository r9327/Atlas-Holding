"""User Ollama model location must be consented, collision-safe and reversible."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from holding.model_volume import ModelVolumeInspection
from holding.windows_ollama_environment import (
    configure_user_models, undo_created_user_models,
)


class RegistryKey:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeWinreg:
    HKEY_CURRENT_USER = 1
    KEY_QUERY_VALUE = 2
    KEY_SET_VALUE = 4
    REG_SZ = 1
    REG_EXPAND_SZ = 2

    def __init__(self):
        self.storage = {}
        self.writes = 0
        self.deleted = 0

    def CreateKeyEx(self, hive, name, reserved, access):
        assert hive == 1 and name == "Environment"
        return RegistryKey()

    def OpenKey(self, hive, name, reserved, access):
        assert hive == 1 and name == "Environment"
        return RegistryKey()

    def QueryValueEx(self, key, name):
        if name not in self.storage:
            raise FileNotFoundError()
        return self.storage[name]

    def SetValueEx(self, key, name, reserved, kind, text):
        self.writes += 1
        self.storage[name] = (text, kind)

    def DeleteValue(self, key, name):
        self.deleted += 1
        del self.storage[name]


class UserOllamaEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "models"
        self.registry = FakeWinreg()

    def configured(self, authorized=True):
        with patch("holding.windows_ollama_environment.platform.system",
                   return_value="Windows"):
            with patch.dict(sys.modules, {"winreg": self.registry}):
                with patch("holding.windows_ollama_environment.inspect_model_volume",
                           return_value=ModelVolumeInspection(
                               "VOLUME_AVAILABLE", 20 * 1024**3,
                           )):
                    return configure_user_models(
                        self.root, authorized=authorized,
                    )

    def undone(self):
        with patch("holding.windows_ollama_environment.platform.system",
                   return_value="Windows"):
            with patch.dict(sys.modules, {"winreg": self.registry}):
                return undo_created_user_models(self.root)

    def test_no_consent_never_reads_or_writes_registry(self):
        result = self.configured(authorized=False)
        self.assertEqual(result.status, "AUTH_REQUIRED")
        self.assertEqual(self.registry.writes, 0)
        self.assertEqual(self.registry.deleted, 0)

    def test_creates_user_path_and_is_idempotent(self):
        first = self.configured()
        self.assertEqual(first.status, "USER_MODELS_CREATED")
        self.assertEqual(
            self.registry.storage["OLLAMA_MODELS"],
            (str(self.root), self.registry.REG_SZ),
        )
        second = self.configured()
        self.assertEqual(second.status, "USER_MODELS_ALREADY_MATCH")
        self.assertEqual(self.registry.writes, 1)

    def test_conflicting_existing_user_path_is_not_overwritten(self):
        self.registry.storage["OLLAMA_MODELS"] = (
            "D:/OtherAppsModels", self.registry.REG_SZ,
        )
        result = self.configured()
        self.assertEqual(result.status, "USER_MODELS_CONFLICT")
        self.assertEqual(self.registry.writes, 0)
        self.assertEqual(
            self.registry.storage["OLLAMA_MODELS"][0],
            "D:/OtherAppsModels",
        )

    def test_removes_only_unchanged_user_value(self):
        self.configured()
        self.assertEqual(self.undone(), "USER_MODELS_ROLLED_BACK")
        self.assertNotIn("OLLAMA_MODELS", self.registry.storage)
        self.assertEqual(self.registry.deleted, 1)

    def test_cannot_delete_external_modified_value(self):
        self.configured()
        self.registry.storage["OLLAMA_MODELS"] = (
            "E:/ChangedOutsideHolding", self.registry.REG_SZ,
        )
        self.assertEqual(
            self.undone(), "USER_MODELS_CHANGED_NO_ROLLBACK",
        )
        self.assertEqual(self.registry.deleted, 0)

    def test_drive_checks_fail_before_touching_registry(self):
        with patch("holding.windows_ollama_environment.platform.system",
                   return_value="Windows"):
            with patch("holding.windows_ollama_environment.inspect_model_volume",
                       return_value=ModelVolumeInspection(
                           "VOLUME_UNAVAILABLE",
                       )):
                outcome = configure_user_models(
                    self.root, authorized=True,
                )
        self.assertEqual(outcome.status,
                         "MODEL_VOLUME_VOLUME_UNAVAILABLE")
        self.assertEqual(self.registry.writes, 0)

    def test_relative_and_linux_paths_are_rejected(self):
        with self.assertRaises(ValueError):
            configure_user_models(Path("relative"), authorized=True)
        with patch("holding.windows_ollama_environment.platform.system",
                   return_value="Linux"):
            result = configure_user_models(self.root, authorized=True)
        self.assertEqual(result.status, "WINDOWS_REQUIRED")


if __name__ == "__main__":
    unittest.main()
