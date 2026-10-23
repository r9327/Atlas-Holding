"""Configuration schema-v3 enrollment contract, not a production Windows release."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from holding.windows_engine_enrollment import (
    EngineEnrollmentError, EngineEnrollmentRequest,
    enroll_pinned_engine, prepare_protected_engine_roots,
    prepare_installed_engine_launch, launch_enrolled_engine,
    protect_approved_install_root, _require_native_windows,
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
        (self.canonical / ".git").mkdir()
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

    def _stage_mcp_companion_for_test(self):
        from holding.embedded_python_stage import (
            EmbeddedPythonRelease, stage_pinned_embedded_python)
        source = self.root / "release-source"
        package = source / "holding"
        package.mkdir(parents=True)
        required = (
            "__init__.py", "mcp_entry.py", "mcp_stdio.py",
            "mcp_hub_session.py", "mcp_hub_probe.py",
            "mcp_windows_job.py", "mcp_local_inventory.py",
        )
        hashes = {}
        for name in required:
            data = ("# signed " + name + "\n").encode()
            (package / name).write_bytes(data)
            hashes[name] = hashlib.sha256(data).hexdigest()
        archive = self.root / "publisher-python.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for name in (
                "python.exe", "python3.dll", "python313.dll",
                "python313.zip", "python313._pth",
            ):
                output.writestr(name, ("publisher " + name).encode())
        release = EmbeddedPythonRelease(
            python_minor=13, zip_sha256=self.sha(archive),
            module_sha256=hashes)
        companion = self.root / "mcp-python"
        companion.mkdir()
        stage = stage_pinned_embedded_python(
            archive=archive, approved_package_root=source,
            target_parent=companion, pin=release)
        return stage, release

    def test_schema_six_pins_real_installed_companion_and_native_handoff(self):
        from holding.daemon_entry import Installation
        from holding.windows_runtime_launch import (
            InstalledEngineSpec, verify_installed_engine)
        self.create_roots()
        stage, release = self._stage_mcp_companion_for_test()
        enrolled = self.enroll(request=replace(
            self.request, mcp_runtime=stage, mcp_release=release))
        payload = json.loads(enrolled.config_path.read_bytes())
        self.assertEqual(payload["schema"], 6)
        self.assertEqual(payload["mcp"]["package_root"], str(stage.directory))
        self.assertEqual(payload["mcp"]["interpreter"], str(stage.python_exe))
        self.assertEqual(payload["mcp"]["source_release_zip_sha256"], release.zip_sha256)
        loaded = Installation.load(enrolled.config_path)
        self.assertEqual(loaded.mcp.package_root, stage.directory)
        self.assertEqual(loaded.mcp.interpreter_pin.expected_sha256,
                         stage.interpreter_sha256)
        spec = InstalledEngineSpec(
            self.root, self.runtime, enrolled.config_path,
            self.request.viewer_sha256, self.request.engine_sha256,
            enrolled.config_sha256)
        with (
            patch("holding.windows_runtime_launch.sys.platform", "win32"),
            patch("holding.windows_runtime_acl.validate_runtime_acl"),
        ):
            self.assertEqual(verify_installed_engine(spec),
                             (str(self.engine), str(enrolled.config_path)))
        stage.python_exe.write_bytes(b"malicious replacement")
        with self.assertRaises(ValueError):
            Installation.load(enrolled.config_path)
        with (
            patch("holding.windows_runtime_launch.sys.platform", "win32"),
            patch("holding.windows_runtime_acl.validate_runtime_acl"),
        ):
            with self.assertRaises(ValueError):
                verify_installed_engine(spec)

    def test_incomplete_schema_six_mcp_companion_never_enrolls(self):
        self.create_roots()
        stage, release = self._stage_mcp_companion_for_test()
        wrong = replace(stage, source_sha256=())
        with self.assertRaises(EngineEnrollmentError):
            self.enroll(request=replace(
                self.request, mcp_runtime=wrong, mcp_release=release))
        self.assertFalse((self.root / "config" / "engine.json").exists())
        with self.assertRaises(EngineEnrollmentError):
            self.enroll(request=replace(
                self.request, mcp_runtime=stage, mcp_release=None))
        self.assertFalse((self.root / "config" / "engine.json").exists())

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
            self.assertEqual(rights.call_count, 7)
        self.assertFalse(enrolled.engine_healthy)

    def test_enrollment_receipt_binds_to_actual_native_launch_preflight(self):
        from holding.windows_runtime_launch import verify_installed_engine
        self.create_roots()
        enrolled = self.enroll()
        with self.fake_windows(), patch(
                "holding.windows_engine_enrollment._native_security"
        ), patch(
                "holding.windows_runtime_launch.sys.platform", "win32"
        ), patch("holding.windows_runtime_acl.validate_runtime_acl"):
            launch_spec = prepare_installed_engine_launch(self.request, enrolled)
            self.assertEqual(
                verify_installed_engine(launch_spec),
                (str(self.engine), str(enrolled.config_path)),
            )
        # Enrollment and verified preflight still do not start a worker.
        self.assertFalse(enrolled.engine_healthy)

    def test_enrolled_engine_launch_requires_separate_consent_and_exact_config(self):
        from holding.windows_runtime_launch import EngineLaunch
        from unittest.mock import Mock
        self.create_roots()
        receipt = self.enroll()
        with self.fake_windows(), patch(
                "holding.windows_engine_enrollment._native_security"
        ), patch(
                "holding.windows_runtime_launch.sys.platform", "win32"
        ), patch("holding.windows_runtime_acl.validate_runtime_acl"), patch(
                "holding.windows_runtime_launch._interactive_user_session",
                return_value=True,
        ), patch("holding.windows_runtime_launch.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(EngineEnrollmentError, "launch approval"):
                launch_enrolled_engine(self.request, receipt, approved=False)
            spawn.assert_not_called()
            with self.assertRaisesRegex(EngineEnrollmentError, "receipt"):
                launch_enrolled_engine(
                    self.request,
                    replace(receipt, config_sha256="0" * 64),
                    approved=True,
                )
            spawn.assert_not_called()
            child = Mock()
            child.poll.return_value = None
            spawn.return_value = child
            launched = launch_enrolled_engine(
                self.request, receipt, approved=True)
            self.assertIsInstance(launched, EngineLaunch)
            self.assertEqual(launched.status, "ENGINE_SPAWNED_HEALTH_PENDING")
            self.assertIs(launched.process, child)
            spawn.assert_called_once()
            self.assertEqual(
                spawn.call_args.args[0],
                [str(self.engine), "--config", str(receipt.config_path)],
            )
            self.assertFalse(spawn.call_args.kwargs["shell"])

    def test_replaced_receipt_and_changed_agent_block_native_handoff(self):
        self.create_roots()
        receipt = self.enroll()
        with self.fake_windows(), patch(
                "holding.windows_engine_enrollment._native_security"
        ), patch(
                "holding.windows_runtime_launch.sys.platform", "win32"
        ), patch("holding.windows_runtime_acl.validate_runtime_acl"):
            with self.assertRaisesRegex(EngineEnrollmentError, "receipt"):
                prepare_installed_engine_launch(
                    self.request, replace(receipt, config_sha256="0" * 64))
            with self.assertRaisesRegex(EngineEnrollmentError, "receipt"):
                prepare_installed_engine_launch(
                    self.request,
                    replace(receipt, config_path=self.root / "data" / "fake.json"))
            self.agent.write_bytes(b"new unauthorized bytes")
            with self.assertRaisesRegex(EngineEnrollmentError, "agent executable changed"):
                prepare_installed_engine_launch(self.request, receipt)

    def test_without_setup_approval_neither_folder_nor_config_is_created(self):
        with self.assertRaisesRegex(EngineEnrollmentError, "approval"):
            prepare_protected_engine_roots(self.root, approved=False)
        self.assertFalse((self.root / "config").exists())
        self.assertFalse((self.root / "data").exists())
        with self.assertRaisesRegex(EngineEnrollmentError, "approval"):
            enroll_pinned_engine(self.request, approved=False)

    def test_root_protection_requires_separate_approval_and_refuses_drive_root(self):
        with self.assertRaisesRegex(EngineEnrollmentError, "consent"):
            protect_approved_install_root(self.root, approved=False)
        with self.fake_windows(), patch(
                "holding.windows_runtime_acl.validate_runtime_acl"
        ) as check, patch("holding.windows_runtime_acl.secure_runtime_acl") as secure:
            protect_approved_install_root(self.root, approved=True)
            check.assert_called_once_with(self.root)
            secure.assert_not_called()
            with self.assertRaisesRegex(EngineEnrollmentError, "broad Windows"):
                protect_approved_install_root(Path(self.root.anchor), approved=True)

    def test_unprotected_install_root_blocks_enrollment_before_writes(self):
        with self.fake_windows(), patch(
                "holding.windows_runtime_acl.secure_runtime_acl") as secure, patch(
                "holding.windows_runtime_acl.validate_runtime_acl",
                side_effect=ValueError("root is not protected")) as acl:
            with self.assertRaisesRegex(ValueError, "not protected"):
                prepare_protected_engine_roots(self.root, approved=True)
        acl.assert_called_once_with(self.root)
        secure.assert_not_called()
        self.assertFalse((self.root / "config").exists())
        self.assertFalse((self.root / "data").exists())

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

    def test_non_checkout_canonical_root_is_rejected_before_config_write(self):
        self.create_roots()
        (self.canonical / ".git").rmdir()
        with self.assertRaisesRegex(EngineEnrollmentError, "Git checkout"):
            self.enroll()
        self.assertFalse((self.root / "config" / "engine.json").exists())

    def test_mutable_clone_or_workspace_cannot_supply_trusted_agent(self):
        self.create_roots()
        for directory in (self.canonical, self.workspace):
            agent = directory / "untrusted-writer.exe"
            agent.write_bytes(b"mutable executable")
            request = replace(
                self.request, agent_executable=agent, agent_sha256=self.sha(agent))
            with self.subTest(directory=directory), self.assertRaisesRegex(
                    EngineEnrollmentError, "mutable checkout"):
                self.enroll(request=request)
            pinned_argument = directory / "entry.py"
            pinned_argument.write_bytes(b"mutable code")
            request = replace(
                self.request, agent_arguments=(pinned_argument,),
                agent_argument_pins={str(pinned_argument): self.sha(pinned_argument)})
            with self.subTest(directory=directory), self.assertRaisesRegex(
                    EngineEnrollmentError, "mutable checkout"):
                self.enroll(request=request)
        self.assertFalse((self.root / "config" / "engine.json").exists())

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

    @unittest.skipUnless(os.name == "nt", "Requires native Windows ACLs")
    def test_real_windows_acl_enrollment_and_cross_module_launch_preflight(self):
        from holding.windows_runtime_acl import (
            secure_runtime_acl, validate_runtime_acl,
        )
        from holding.windows_runtime_launch import (
            InstalledEngineSpec, verify_installed_engine,
        )

        for path in (
            self.root, self.runtime.parent, self.runtime,
            self.viewer, self.engine,
        ):
            secure_runtime_acl(path)
            validate_runtime_acl(path)
        data, config = prepare_protected_engine_roots(self.root, approved=True)
        validate_runtime_acl(data)
        validate_runtime_acl(config)
        enrolled = enroll_pinned_engine(self.request, approved=True)
        self.assertFalse(enrolled.engine_healthy)
        self.assertEqual(enrolled.status, "CONFIGURED_ENGINE_HEALTH_PENDING")
        validate_runtime_acl(enrolled.config_path)
        spec = InstalledEngineSpec(
            self.root, self.runtime, enrolled.config_path,
            self.request.viewer_sha256, self.request.engine_sha256,
            enrolled.config_sha256,
        )
        self.assertEqual(
            verify_installed_engine(spec),
            (str(self.engine), str(enrolled.config_path)),
        )

    def test_native_enrollment_guard_fails_closed_elsewhere(self):
        if os.name == "nt":
            self.skipTest("Native Windows boundary is covered by Windows CI")
        with self.assertRaisesRegex(EngineEnrollmentError, "Native Windows"):
            _require_native_windows()


if __name__ == "__main__":
    unittest.main()
