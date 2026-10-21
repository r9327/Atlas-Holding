"""Configuration schema-v3 enrollment contract, not a production Windows release."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.windows_engine_enrollment import (
    EngineEnrollmentError, EngineEnrollmentRequest,
    enroll_pinned_engine, prepare_protected_engine_roots,
    _require_native_windows,
)


class EngineEnrollmentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.runtime = self.root / "runtime" / "trusted-pair"
        self.runtime.mkdir(parents=True)
        self.viewer = self.runtime / "AtlasHoldingControlCenter.exe"
        self.engine = self.runtime / "AtlasHoldingEngine.exe"
        self.viewer.write_bytes(b"Q" * 1_000_061)
        self.engine.write_bytes(b"E" * 1_000_063)
        self.agent = self.root / "trusted-agent.exe"
        self.agent.write_bytes(b"verified program bytes")
        self.canonical = self.root / "atlas-clone"
        self.workspace = self.root / "workspaces"
        self.health = self.root / "health"
        for folder in (self.canonical, self.workspace, self.health):
            folder.mkdir()
        self.request = EngineEnrollmentRequest(
            install_root=self.root,
            runtime_directory=self.runtime,
            viewer_sha256=self.sha(self.viewer),
            engine_sha256=self.sha(self.engine),
            repository="r9327/Dof-Atlas",
            canonical_root=self.canonical,
            workspace=self.workspace,
            health_directory=self.health,
            agent_executable=self.agent,
            agent_sha256=self.sha(self.agent),
            agent_argument_pins={},
        )

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def fake_windows(self):
        return patch(
            "holding.windows_engine_enrollment._require_native_windows",
        )

    def mock_acl(self):
        return patch(
            "holding.windows_runtime_acl.secure_runtime_acl",
        )

    def create_roots(self):
        with self.fake_windows(), self.mock_acl(), \
             patch("holding.windows_runtime_acl.validate_runtime_acl"):
            return prepare_protected_engine_roots(self.root, approved=True)

    def enroll(self, *, request=None, approved=True):
        with self.fake_windows(), self.mock_acl(), \
             patch("holding.windows_engine_enrollment._native_security"):
            return enroll_pinned_engine(
                self.request if request is None else request,
                approved=approved,
            )

    def test_version_three_config_binds_exact_installed_viewer_and_agent(self):
        self.create_roots()
        result = self.enroll()
        self.assertFalse(result.engine_healthy)
        self.assertEqual(result.status, "CONFIGURED_ENGINE_HEALTH_PENDING")
        self.assertEqual(result.config_path, self.root / "config" / "engine.json")
        self.assertEqual(result.config_sha256, self.sha(result.config_path))
        payload = json.loads(result.config_path.read_bytes())
        self.assertEqual(payload["schema"], 3)
        self.assertIsNone(payload["github"])
        self.assertEqual(payload["database"], str(self.root / "data" / "engine.sqlite"))
        self.assertEqual(payload["repository"], "r9327/Dof-Atlas")
        self.assertEqual(payload["dashboard"], {
            "executable": str(self.viewer),
            "executable_sha256": self.request.viewer_sha256,
            "script": None, "script_sha256": None, "readonly": False,
        })
        self.assertEqual(payload["executable"], str(self.agent))
        self.assertEqual(payload["executable_sha256"], self.request.agent_sha256)
        self.assertEqual(payload["arguments"], [])
        self.assertEqual(payload["argument_sha256"], {})
        existing = self.enroll()
        self.assertEqual(existing.status, "REUSED_CONFIG_HEALTH_PENDING")
        self.assertEqual(existing.config_sha256, result.config_sha256)
        self.assertEqual(
            [p.name for p in (self.root / "config").iterdir()], ["engine.json"],
        )

    def test_enrolled_config_is_accepted_by_real_installed_engine_preflight(self):
        from holding.windows_runtime_launch import (
            InstalledEngineSpec, verify_installed_engine,
        )

        self.create_roots()
        enrolled = self.enroll()
        spec = InstalledEngineSpec(
            self.root, self.runtime, enrolled.config_path,
            self.request.viewer_sha256,
            self.request.engine_sha256,
            enrolled.config_sha256,
        )
        # Only OS rights are stubbed; config parsing and EXE SHA validation
        # run unchanged from the native production preflight contract.
        with patch("holding.windows_runtime_launch.sys.platform", "win32"), \
             patch("holding.windows_runtime_acl.validate_runtime_acl") as rights:
            self.assertEqual(
                verify_installed_engine(spec),
                (str(self.engine), str(enrolled.config_path)),
            )
            self.assertEqual(rights.call_count, 6)
        self.assertFalse(enrolled.engine_healthy)

    def test_without_setup_approval_neither_folder_nor_config_is_created(self):
        with self.assertRaisesRegex(EngineEnrollmentError, "approval"):
            prepare_protected_engine_roots(self.root, approved=False)
        self.assertFalse((self.root / "config").exists())
        self.assertFalse((self.root / "data").exists())
        with self.assertRaisesRegex(EngineEnrollmentError, "approval"):
            enroll_pinned_engine(self.request, approved=False)

    def test_native_acl_failure_refuses_configuration_without_elevating_claim(self):
        with self.fake_windows(), self.mock_acl(), \
             patch("holding.windows_runtime_acl.validate_runtime_acl",
                   side_effect=ValueError("unsafe")):
            with self.assertRaises(ValueError):
                prepare_protected_engine_roots(self.root, approved=True)
        self.assertFalse((self.root / "config").exists())
        self.assertFalse((self.root / "data").exists())

    def test_bad_agent_hash_and_foreign_runtime_never_write_config(self):
        self.create_roots()
        with self.assertRaisesRegex(EngineEnrollmentError, "agent executable changed"):
            self.enroll(request=replace(self.request, agent_sha256="0" * 64))
        with self.assertRaisesRegex(EngineEnrollmentError, "selected disk"):
            self.enroll(request=replace(
                self.request, runtime_directory=self.canonical,
            ))
        self.assertEqual(list((self.root / "config").iterdir()), [])

    def test_tampered_binary_or_missing_agent_argument_pin_fail_closed(self):
        self.create_roots()
        self.engine.write_bytes(b"tampered")
        with self.assertRaisesRegex(EngineEnrollmentError, "pair"):
            self.enroll()
        self.engine.write_bytes(b"E" * 1_000_063)
        with self.assertRaisesRegex(EngineEnrollmentError, "argument"):
            self.enroll(request=replace(
                self.request, agent_arguments=(self.agent,),
                agent_argument_pins={},
            ))
        self.assertEqual(list((self.root / "config").iterdir()), [])

    def test_existing_config_is_never_overwritten_implicitly(self):
        self.create_roots()
        result = self.enroll()
        old_bytes = result.config_path.read_bytes()
        result.config_path.write_bytes(b"changed configuration")
        with self.assertRaisesRegex(EngineEnrollmentError, "explicit repair"):
            self.enroll()
        self.assertEqual(result.config_path.read_bytes(), b"changed configuration")
        self.assertNotEqual(result.config_path.read_bytes(), old_bytes)

    def test_missing_protected_data_roots_is_not_automatically_repaired(self):
        with self.assertRaises(EngineEnrollmentError):
            self.enroll()
        self.assertFalse((self.root / "config").exists())

    def test_native_enrollment_guard_fails_closed_elsewhere(self):
        if os.name == "nt":
            self.skipTest("Native Windows boundary is covered by Windows CI")
        with self.assertRaisesRegex(EngineEnrollmentError, "Native Windows"):
            _require_native_windows()


if __name__ == "__main__":
    unittest.main()
