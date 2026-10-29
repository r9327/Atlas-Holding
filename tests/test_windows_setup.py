"""First-run setup wizard's headless contract; no display required on CI."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.inventory import Component
from holding.provision_runtime import ProvisionExecution, ProvisionStep
from holding.staged_install import InstallInspection, PinnedZip
from holding.windows_setup import (assess_post_install, detect_online,
                                  preflight_setup, execute_setup,
                                  _publish_nonrelease_install_receipt,
                                  SetupSummary)
from test_provision_plan import sample


class WindowsSetupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.manifest = base / "assets.json"
        self.install_root = base / "holding"
        self.cache_root = base / "downloads"
        self.pin = PinnedZip(
            "opencode", "1.0", "https://example.invalid/archive.zip",
            "a" * 64, 8123, 12345,
        )
        self.snapshot = sample(opencode=Component("NOT_FOUND"))

    def test_fresh_runner_receipt_records_real_runtime_but_never_certifies_release(self):
        import hashlib
        import json
        self.install_root.mkdir()
        pins = {
            "AtlasHoldingControlCenter.exe": hashlib.sha256(b"viewer").hexdigest(),
            "AtlasHoldingEngine.exe": hashlib.sha256(b"engine").hexdigest(),
        }
        version = pins["AtlasHoldingControlCenter.exe"][:16] + "-" + pins[
            "AtlasHoldingEngine.exe"][:16]
        runtime = self.install_root / "runtime" / version
        runtime.mkdir(parents=True)
        (runtime / "AtlasHoldingControlCenter.exe").write_bytes(b"viewer")
        (runtime / "AtlasHoldingEngine.exe").write_bytes(b"engine")
        receipt = self.install_root.parent / "fresh-install.json"
        summary = SetupSummary(
            "PREFLIGHT", "CONSENT_REQUIRED", "Windows", "AMD64", "", None,
            0, 0, (), 0, (),
        )
        execution = ProvisionExecution(
            "RUNTIME_STAGED_ENGINE_CONFIG_PENDING", (), ())
        with patch("holding.windows_setup.verify_runtime_payload") as verify:
            digest = _publish_nonrelease_install_receipt(
                receipt, install_root=self.install_root, summary=summary,
                execution=execution, runtime_pins=pins)
        document = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(digest, hashlib.sha256(receipt.read_bytes()).hexdigest())
        self.assertEqual(document["execution_state"],
                         "RUNTIME_STAGED_ENGINE_CONFIG_PENDING")
        self.assertEqual(set(document["runtime_artifacts"]), set(pins))
        for claim in (
                "windows_clean_install_certified",
                "interactive_wizard_certified", "reboot_certified",
                "offline_after_provisioning_certified",
                "e2e_roadmap_certified", "release_ready"):
            self.assertIs(document[claim], False)
        verify.assert_called_once_with(runtime, pins)
        with self.assertRaisesRegex(ValueError, "New canonical"):
            _publish_nonrelease_install_receipt(
                receipt, install_root=self.install_root, summary=summary,
                execution=execution, runtime_pins=pins)

    def call_preflight(self, *, snapshot=None, observed="NOT_INSTALLED",
                       pins=None, online=True):
        with patch("holding.windows_setup.read_pinned_manifest",
                   return_value=(self.pin,) if pins is None else pins):
            with patch("holding.windows_setup.scan",
                       return_value=snapshot or self.snapshot):
                with patch("holding.windows_setup.inspect_versioned_zip",
                           return_value=InstallInspection(observed, self.install_root)):
                    return preflight_setup(
                        manifest=self.manifest, manifest_sha256="b" * 64,
                        install_root=self.install_root, cache_root=self.cache_root,
                        online=online,
                    )

    def test_setup_cli_refuses_partial_mcp_companion_before_showing_gui(self):
        from holding.windows_setup import main
        args = [
            "--manifest", str(self.manifest),
            "--manifest-sha256", "a" * 64,
        ]
        with patch("tkinter.Tk") as tk:
            for extra in (
                ["--mcp-archive", str(self.manifest)],
                ["--mcp-archive-sha256", "a" * 64],
                ["--mcp-python-minor", "13"],
            ):
                with self.subTest(extra=extra):
                    with self.assertRaises(SystemExit) as error:
                        main(args + extra)
                    self.assertEqual(error.exception.code, 2)
            tk.assert_not_called()

    def test_setup_stages_agent_only_after_healthy_toolchain_and_consent(self):
        import hashlib
        from unittest.mock import Mock
        from holding.windows_setup import SetupSummary
        runtime = (self.install_root.parent / "approved-runtime").resolve()
        runtime.mkdir()
        agent_source = self.install_root.parent / "AtlasHoldingAgent.exe"
        agent_source.write_bytes(b"trusted packaged agent")
        agent_pin = hashlib.sha256(agent_source.read_bytes()).hexdigest()
        pins = {
            "AtlasHoldingControlCenter.exe": "a" * 64,
            "AtlasHoldingEngine.exe": "b" * 64,
        }
        summary = SetupSummary(
            "PREFLIGHT", "CONSENT_REQUIRED", "Windows", "AMD64", "", None,
            0, 0, (), 0, (),
        )
        staged = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED", (), ())
        healthy = ProvisionExecution(
            "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING", (), ())
        calls = []
        with patch("holding.windows_setup.verify_runtime_payload"), patch(
                "holding.windows_setup.preflight_setup",
                return_value=(summary, ()),
        ), patch("holding.windows_setup.scan", return_value=self.snapshot), patch(
                "holding.windows_setup.stage_required_dependencies",
                return_value=staged,
        ), patch("holding.windows_setup.assess_post_install",
                 return_value=healthy), patch(
                "holding.windows_setup.protect_approved_install_root",
                side_effect=lambda *a, **kw: calls.append("protect"),
        ), patch(
                "holding.windows_setup.install_pinned_runtime_pair",
                side_effect=lambda *a, **kw: (
                    calls.append("runtime"),
                    Mock(state="STAGED_BINARIES_SECURITY_PENDING"),
                )[1],
        ), patch(
                "holding.windows_setup.install_pinned_adapter",
                side_effect=lambda *a, **kw: (
                    calls.append("adapter"),
                    "STAGED_PINNED_ADAPTER_HEALTH_PENDING",
                )[1],
        ):
            report, result = execute_setup(
                manifest=self.manifest, manifest_sha256="b" * 64,
                install_root=self.install_root, cache_root=self.cache_root,
                online=True, approved=True,
                runtime_root=runtime, runtime_pins=pins,
                adapter_source=agent_source, adapter_sha256=agent_pin,
            )
        self.assertEqual(report, summary)
        self.assertEqual(calls, ["protect", "runtime", "adapter"])
        self.assertEqual(result.state, "RUNTIME_STAGED_ENGINE_CONFIG_PENDING")
        self.assertIn(
            ("holding-adapter", "STAGE_VERIFIED_AGENT",
             "STAGED_PINNED_ADAPTER_HEALTH_PENDING"),
            [(x.name, x.action, x.result) for x in result.steps],
        )

    def test_setup_refuses_unpinned_agent_before_any_mutating_preflight(self):
        from holding.windows_setup import execute_setup
        agent_source = self.install_root.parent / "AtlasHoldingAgent.exe"
        agent_source.write_bytes(b"trust must not be fabricated")
        with patch("holding.windows_setup.preflight_setup") as plan:
            with self.assertRaisesRegex(ValueError, "release pin"):
                execute_setup(
                    manifest=self.manifest, manifest_sha256="b" * 64,
                    install_root=self.install_root,
                    cache_root=self.cache_root, online=True, approved=True,
                    runtime_root=self.install_root.parent,
                    runtime_pins={
                        "AtlasHoldingControlCenter.exe": "a" * 64,
                        "AtlasHoldingEngine.exe": "b" * 64,
                    },
                    adapter_source=agent_source, adapter_sha256="0" * 64,
                )
            plan.assert_not_called()

    def test_checkout_guard_requires_exact_clean_canonical_git_origin(self):
        from holding.git_worktree import GitSafetyError
        from holding.windows_engine_enrollment import EngineEnrollmentError
        from holding.windows_setup import verify_canonical_atlas_checkout

        atlas = self.install_root.parent / "atlas"
        atlas.mkdir()
        workspace = self.install_root / "workspaces"
        with patch("holding.git_worktree.GitWorktrees") as controller:
            verify_canonical_atlas_checkout(atlas, workspace)
            controller.assert_called_once_with(
                atlas, workspace, "r9327/Dof-Atlas",
            )
            controller.return_value.inspect_canonical.assert_called_once_with()
            controller.return_value.inspect_canonical.side_effect = GitSafetyError(
                "Remote points to an unrelated repository",
            )
            with self.assertRaisesRegex(EngineEnrollmentError, "clean"):
                verify_canonical_atlas_checkout(atlas, workspace)

    def test_engine_activation_does_not_run_without_consent_or_release_pin(self):
        from holding.windows_setup import configure_and_launch_engine
        from holding.windows_engine_enrollment import EngineEnrollmentError
        import hashlib
        self.install_root.mkdir()
        atlas = self.install_root.parent / "atlas"
        atlas.mkdir()
        with patch("holding.windows_setup.protect_approved_install_root") as protect:
            with self.assertRaisesRegex(EngineEnrollmentError, "consent"):
                configure_and_launch_engine(
                    install_root=self.install_root, atlas_root=atlas,
                    runtime_pins={}, adapter_sha256="a" * 64,
                    approved=False, launch_approved=False,
                )
            with self.assertRaisesRegex(EngineEnrollmentError, "installation inputs"):
                configure_and_launch_engine(
                    install_root=self.install_root, atlas_root=atlas,
                    runtime_pins={}, adapter_sha256="a" * 64,
                    approved=True, launch_approved=False,
                )
            protect.assert_not_called()

    def test_engine_activation_enforces_bundled_adapter_hash_before_acl_mutation(self):
        import hashlib
        from holding.windows_setup import configure_and_launch_engine
        from holding.windows_engine_enrollment import EngineEnrollmentError
        self.install_root.mkdir()
        atlas = self.install_root.parent / "atlas"
        atlas.mkdir()
        agents = self.install_root / "agents"
        agents.mkdir()
        agent = agents / "AtlasHoldingAgent.exe"
        agent.write_bytes(b"installed test adapter")
        pins = {
            "AtlasHoldingControlCenter.exe": "a" * 64,
            "AtlasHoldingEngine.exe": "b" * 64,
        }
        with patch("holding.windows_engine_enrollment._require_native_windows"), patch(
                "holding.windows_setup.verify_runtime_payload"
        ), patch("holding.windows_setup.protect_approved_install_root") as protect:
            with self.assertRaisesRegex(EngineEnrollmentError, "release pin"):
                configure_and_launch_engine(
                    install_root=self.install_root, atlas_root=atlas,
                    runtime_pins=pins, adapter_sha256="f" * 64,
                    approved=True, launch_approved=False,
                )
            protect.assert_not_called()

    def test_engine_activation_does_not_enroll_without_installed_model(self):
        import hashlib
        from holding.windows_setup import configure_and_launch_engine
        from holding.windows_engine_enrollment import EngineEnrollmentError
        self.install_root.mkdir()
        atlas = self.install_root.parent / "atlas"
        atlas.mkdir()
        agent_dir = self.install_root / "agents"
        agent_dir.mkdir()
        agent = agent_dir / "AtlasHoldingAgent.exe"
        agent.write_bytes(b"adapter code")
        pin = hashlib.sha256(agent.read_bytes()).hexdigest()
        runtime = self.install_root / "runtime" / ("a" * 16 + "-" + "b" * 16)
        runtime.mkdir(parents=True)
        with patch("holding.windows_engine_enrollment._require_native_windows"), patch(
                "holding.windows_setup.verify_runtime_payload"), patch(
                "holding.windows_setup.protect_approved_install_root"), patch(
                "holding.windows_setup.validate_runtime_acl"), patch(
                "holding.windows_setup.prepare_protected_engine_roots"), patch(
                "holding.windows_setup.enroll_pinned_engine") as enroll:
            with self.assertRaisesRegex(EngineEnrollmentError, "model"):
                configure_and_launch_engine(
                    install_root=self.install_root, atlas_root=atlas,
                    runtime_pins={
                        "AtlasHoldingControlCenter.exe": "a" * 64,
                        "AtlasHoldingEngine.exe": "b" * 64,
                    },
                    adapter_sha256=pin, approved=True, launch_approved=False,
                )
            enroll.assert_not_called()

    def test_engine_activation_prepares_pinned_config_then_launches_with_consent(self):
        import hashlib
        from unittest.mock import Mock
        from holding.windows_setup import configure_and_launch_engine
        from holding.windows_engine_enrollment import EngineEnrollment
        from holding.windows_model_enrollment import PinnedModel
        self.install_root.mkdir()
        atlas = self.install_root.parent / "atlas"
        atlas.mkdir()
        agent_dir = self.install_root / "agents"
        agent_dir.mkdir()
        agent = agent_dir / "AtlasHoldingAgent.exe"
        agent.write_bytes(b"pinned trusted fixture")
        pin = hashlib.sha256(agent.read_bytes()).hexdigest()
        runtime = self.install_root / "runtime" / ("a" * 16 + "-" + "b" * 16)
        runtime.mkdir(parents=True)
        pins = {
            "AtlasHoldingControlCenter.exe": "a" * 64,
            "AtlasHoldingEngine.exe": "b" * 64,
        }
        receipt = EngineEnrollment(
            self.install_root / "config" / "engine.json", "c" * 64,
            "CONFIGURED_ENGINE_HEALTH_PENDING",
        )
        with patch("holding.windows_engine_enrollment._require_native_windows"), patch(
                "holding.windows_setup.verify_runtime_payload"
        ) as binaries, patch(
                "holding.windows_setup.protect_approved_install_root"
        ) as protect, patch(
                "holding.windows_setup.validate_runtime_acl"
        ) as rights, patch(
                "holding.windows_setup.secure_runtime_acl"
        ) as secure, patch(
                "holding.windows_setup.prepare_protected_engine_roots"
        ) as folders, patch(
                "holding.windows_setup.enroll_pinned_engine", return_value=receipt
        ) as enroll, patch(
                "holding.windows_setup.discover_local_models",
                return_value=(("atlas-test:Q4", "e" * 64),),
        ), patch(
                "holding.windows_setup.verify_canonical_atlas_checkout"
        ), patch(
                "holding.windows_setup.enroll_local_model",
                return_value=PinnedModel(
                    self.install_root / "agents" / "AtlasHoldingModel.json",
                    "d" * 64, "atlas-test:Q4", "e" * 64,
                    "MODEL_PINNED_HEALTH_PENDING",
                ),
        ) as model, patch(
                "holding.windows_setup.launch_enrolled_engine"
        ) as launch:
            status, pid = configure_and_launch_engine(
                install_root=self.install_root, atlas_root=atlas,
                runtime_pins=pins, adapter_sha256=pin,
                approved=True, launch_approved=False,
                model_name="atlas-test:Q4",
            )
            self.assertEqual(status, receipt.status)
            self.assertIsNone(pid)
            launch.assert_not_called()
            self.assertEqual(binaries.call_args.args, (runtime, pins))
            protect.assert_called_with(self.install_root, approved=True)
            folders.assert_called_with(self.install_root, approved=True)
            request = enroll.call_args.args[0]
            self.assertEqual(request.agent_executable, agent)
            self.assertEqual(request.canonical_root, atlas)
            model_path = self.install_root / "agents" / "AtlasHoldingModel.json"
            self.assertEqual(request.agent_arguments, (model_path,))
            self.assertEqual(request.agent_argument_pins, {str(model_path): "d" * 64})
            model.assert_called_with(
                self.install_root, selected_model="atlas-test:Q4", approved=True)
            launch.return_value = Mock(status="ENGINE_SPAWNED_HEALTH_PENDING",
                                       process=Mock(pid=7380))
            status, pid = configure_and_launch_engine(
                install_root=self.install_root, atlas_root=atlas,
                runtime_pins=pins, adapter_sha256=pin,
                approved=True, launch_approved=True,
                model_name="atlas-test:Q4",
            )
            self.assertEqual((status, pid), ("ENGINE_SPAWNED_HEALTH_PENDING", 7380))
            launch.assert_called_once_with(request, receipt, approved=True)

    def test_installed_project_chat_uses_existing_pins_without_viewer_ipc(self):
        import json
        import subprocess
        from unittest.mock import Mock
        from holding.windows_setup import launch_installed_project_chat
        from holding.windows_engine_enrollment import EngineEnrollmentError

        self.install_root.mkdir()
        pins = {
            "AtlasHoldingControlCenter.exe": "a" * 64,
            "AtlasHoldingEngine.exe": "b" * 64,
        }
        version = "a" * 16 + "-" + "b" * 16
        runtime = self.install_root / "runtime" / version
        runtime.mkdir(parents=True)
        viewer = runtime / "AtlasHoldingControlCenter.exe"
        viewer.write_bytes(b"frozen viewer fixture")
        (runtime / "AtlasHoldingEngine.exe").write_bytes(b"engine fixture")
        agents = self.install_root / "agents"
        agents.mkdir()
        model = agents / "AtlasHoldingModel.json"
        model.write_text(json.dumps({
            "schema": 1, "model": "test:Q4", "digest": "c" * 64,
            "num_ctx": 4096, "timeout": 30,
        }), encoding="utf-8")
        data = self.install_root / "data"
        data.mkdir()
        database = data / "engine.sqlite"
        database.write_bytes(b"sqlite fixture for path checks")
        with patch("holding.windows_engine_enrollment._require_native_windows"), \
             patch("holding.windows_setup.verify_runtime_payload") as verify, \
             patch("holding.windows_setup.validate_runtime_acl") as acl, \
             patch("subprocess.Popen", return_value=Mock(pid=8419)) as spawn:
            self.assertEqual(launch_installed_project_chat(
                install_root=self.install_root, runtime_pins=pins,
                approved=True), 8419)
            verify.assert_called_once_with(runtime, pins)
            self.assertEqual(acl.call_count, 3)
            argv = spawn.call_args.args[0]
            self.assertEqual(argv, [
                str(viewer), "--project-chat", "--chat-db", str(database),
                "--chat-repo", "r9327/Dof-Atlas",
                "--chat-model-config", str(model),
            ])
            options = spawn.call_args.kwargs
            self.assertEqual(options["cwd"], runtime)
            self.assertIs(options["stdin"], subprocess.DEVNULL)
            self.assertIs(options["stdout"], subprocess.DEVNULL)
            self.assertIs(options["stderr"], subprocess.DEVNULL)
            self.assertIs(options["shell"], False)
            self.assertIs(options["close_fds"], True)
            spawn.reset_mock()
            with self.assertRaisesRegex(EngineEnrollmentError, "operator"):
                launch_installed_project_chat(
                    install_root=self.install_root, runtime_pins=pins,
                    approved=False)
            spawn.assert_not_called()
            database.unlink()
            with self.assertRaisesRegex(EngineEnrollmentError, "unavailable"):
                launch_installed_project_chat(
                    install_root=self.install_root, runtime_pins=pins,
                    approved=True)
            spawn.assert_not_called()

    def test_first_install_preflight_is_read_only_and_reports_bytes(self):
        summary, pins = self.call_preflight()
        self.assertEqual(summary.phase, "PREFLIGHT")
        self.assertEqual(summary.status, "CONSENT_REQUIRED")
        self.assertEqual(summary.download_upper_bound_bytes, 8123)
        self.assertEqual(summary.installed_models, 1)
        self.assertEqual(len(pins), 1)
        self.assertIn(("opencode", "INSTALL", "NOT_INSTALLED"),
                      summary.dependency_actions)
        self.assertFalse(self.install_root.exists())
        self.assertFalse(self.cache_root.exists())

    def test_existing_pinned_payload_reports_zero_download(self):
        summary, _ = self.call_preflight(observed="VERIFIED_PAYLOAD")
        self.assertEqual(summary.status, "CONSENT_REQUIRED")
        self.assertEqual(summary.download_upper_bound_bytes, 0)

    def test_new_ollama_user_models_env_precedes_winget_staging(self):
        from holding.windows_package_manager import WingetPin
        from holding.windows_setup import SetupSummary
        from holding.windows_ollama_environment import UserModelEnvironment
        chosen = self.install_root / "models"
        pin = WingetPin("ollama", "Ollama.Ollama", "0.32.8")
        snapshot = sample(ollama=Component("NOT_FOUND"))
        report = SetupSummary(
            "PREFLIGHT", "CONSENT_REQUIRED", "Windows", "AMD64",
            "", 32 * 1024**3, 100 * 1024**3, 0, (), 0, (),
        )
        stage = ProvisionExecution(
            "PARTIAL_OR_BLOCKED",
            (ProvisionStep("ollama", "INSTALL",
                           "INSTALLED_NOT_HEALTHCHECKED"),),
            ("Unrelated other package failed",),
        )
        with patch("holding.windows_setup.preflight_setup",
                   return_value=(report, (pin,))):
            with patch("holding.windows_setup.scan",
                       return_value=snapshot):
                with patch("holding.windows_setup.restore_pinned_inventory",
                           return_value=snapshot):
                    with patch("holding.windows_setup.configure_user_models",
                               return_value=UserModelEnvironment(
                                   "USER_MODELS_CREATED", str(chosen),
                               )) as configure:
                        with patch("holding.windows_setup.stage_required_dependencies",
                                   return_value=stage) as install:
                            with patch("holding.windows_setup.undo_created_user_models") as undo:
                                _, actual = execute_setup(
                                    manifest=self.manifest,
                                    manifest_sha256="b" * 64,
                                    install_root=self.install_root,
                                    cache_root=self.cache_root,
                                    online=True, approved=True, model_root=chosen,
                                )
        configure.assert_called_once_with(chosen, authorized=True)
        self.assertEqual(install.call_args.kwargs["model_root"], chosen)
        self.assertEqual(actual.steps[-1].result, "USER_MODELS_CREATED")
        undo.assert_not_called()

    def test_conflicting_user_ollama_variable_blocks_mutating_install(self):
        from holding.windows_package_manager import WingetPin
        from holding.windows_setup import SetupSummary
        from holding.windows_ollama_environment import UserModelEnvironment
        chosen = self.install_root / "models"
        pin = WingetPin("ollama", "Ollama.Ollama", "0.32.8")
        snapshot = sample(ollama=Component("NOT_FOUND"))
        report = SetupSummary(
            "PREFLIGHT", "CONSENT_REQUIRED", "Windows", "AMD64",
            "", 32 * 1024**3, 100 * 1024**3, 0, (), 0, (),
        )
        with patch("holding.windows_setup.preflight_setup",
                   return_value=(report, (pin,))):
            with patch("holding.windows_setup.scan",
                       return_value=snapshot):
                with patch("holding.windows_setup.restore_pinned_inventory",
                           return_value=snapshot):
                    with patch("holding.windows_setup.configure_user_models",
                               return_value=UserModelEnvironment(
                                   "USER_MODELS_CONFLICT", str(chosen),
                               )):
                        with patch("holding.windows_setup.stage_required_dependencies") as install:
                            _, actual = execute_setup(
                                manifest=self.manifest, manifest_sha256="b" * 64,
                                install_root=self.install_root,
                                cache_root=self.cache_root,
                                online=True, approved=True, model_root=chosen,
                            )
        self.assertEqual(actual.state, "PARTIAL_OR_BLOCKED")
        self.assertEqual(actual.steps[-1].result, "USER_MODELS_CONFLICT")
        install.assert_not_called()

    def test_failed_staging_rolls_back_only_fresh_models_variable(self):
        from holding.windows_package_manager import WingetPin
        from holding.windows_setup import SetupSummary
        from holding.windows_ollama_environment import UserModelEnvironment
        chosen = self.install_root / "models"
        pin = WingetPin("ollama", "Ollama.Ollama", "0.32.8")
        snapshot = sample(ollama=Component("NOT_FOUND"))
        report = SetupSummary(
            "PREFLIGHT", "CONSENT_REQUIRED", "Windows", "AMD64",
            "", 32 * 1024**3, 100 * 1024**3, 0, (), 0, (),
        )
        with patch("holding.windows_setup.preflight_setup",
                   return_value=(report, (pin,))):
            with patch("holding.windows_setup.scan",
                       return_value=snapshot):
                with patch("holding.windows_setup.restore_pinned_inventory",
                           return_value=snapshot):
                    with patch("holding.windows_setup.configure_user_models",
                               return_value=UserModelEnvironment(
                                   "USER_MODELS_CREATED", str(chosen),
                               )):
                        with patch("holding.windows_setup.stage_required_dependencies",
                                   return_value=ProvisionExecution(
                                       "PARTIAL_OR_BLOCKED", (),
                                       ("No package installed",),
                                   )):
                            with patch("holding.windows_setup.undo_created_user_models",
                                       return_value="USER_MODELS_ROLLED_BACK") as undo:
                                _, actual = execute_setup(
                                    manifest=self.manifest,
                                    manifest_sha256="b" * 64,
                                    install_root=self.install_root,
                                    cache_root=self.cache_root,
                                    online=True, approved=True, model_root=chosen,
                                )
        undo.assert_called_once_with(chosen)
        self.assertEqual(actual.steps[-1].result, "USER_MODELS_ROLLED_BACK")

    def test_second_launch_restores_pinned_cli_before_planning_downloads(self):
        from holding.windows_package_manager import WingetPin
        pin = WingetPin("opencode", "SST.opencode", "1.18.29")
        restored = sample(opencode=Component(
            "FOUND_VALID", path=str(self.install_root / "opencode.exe"),
            version="opencode 1.18.29",
        ))
        with patch("holding.windows_setup.read_pinned_manifest",
                   return_value=(pin,)):
            with patch("holding.windows_setup.scan",
                       return_value=self.snapshot):
                with patch("holding.windows_setup.restore_pinned_inventory",
                           return_value=restored) as recover:
                    with patch("holding.windows_setup.inspect_versioned_zip") as inspect:
                        report, found_pins = preflight_setup(
                            manifest=self.manifest,
                            manifest_sha256="b" * 64,
                            install_root=self.install_root,
                            cache_root=self.cache_root,
                            online=True,
                        )
        self.assertEqual(report.status, "BENCHMARK_REQUIRED")
        self.assertEqual(report.unestimated_package_downloads, 0)
        self.assertEqual(report.download_upper_bound_bytes, 0)
        recover.assert_called_once_with(
            self.snapshot, (pin,), install_root=self.install_root,
        )
        inspect.assert_not_called()
        self.assertEqual(found_pins, (pin,))

    def test_winget_download_size_is_explicitly_unknown(self):
        from holding.windows_package_manager import WingetPin
        pin = WingetPin("opencode", "SST.opencode", "1.18.29")
        summary, pins = self.call_preflight(pins=(pin,))
        self.assertEqual(summary.status, "CONSENT_REQUIRED")
        self.assertEqual(summary.unestimated_package_downloads, 1)
        self.assertEqual(summary.download_upper_bound_bytes, 0)
        self.assertEqual(summary.dependency_actions[-3][0], "opencode")
        self.assertEqual(summary.dependency_actions[-3][2], "WINGET_VERSION_PINNED")

    def test_model_drive_checked_before_install_and_bytes_reported(self):
        from holding.model_volume import ModelVolumeInspection
        destination = self.install_root / "separate-model-volume"
        with patch("holding.windows_setup.inspect_model_volume",
                   return_value=ModelVolumeInspection(
                       "VOLUME_AVAILABLE", 15 * 1024**3, str(self.install_root),
                   )) as inspect:
            with patch("holding.windows_setup.read_pinned_manifest",
                       return_value=(self.pin,)):
                with patch("holding.windows_setup.scan",
                           return_value=self.snapshot):
                    with patch("holding.windows_setup.inspect_versioned_zip",
                               return_value=InstallInspection(
                                   "NOT_INSTALLED", self.install_root,
                               )):
                        summary, _ = preflight_setup(
                            manifest=self.manifest, manifest_sha256="b" * 64,
                            install_root=self.install_root,
                            cache_root=self.cache_root, online=True,
                            model_root=destination,
                        )
        self.assertEqual(summary.model_volume_status, "VOLUME_AVAILABLE")
        self.assertEqual(summary.model_volume_free_bytes, 15 * 1024**3)
        inspect.assert_called_once_with(destination)
        self.assertFalse(destination.exists())

    def test_unavailable_model_drive_blocks_before_staging(self):
        from holding.model_volume import ModelVolumeInspection
        with patch("holding.windows_setup.inspect_model_volume",
                   return_value=ModelVolumeInspection("VOLUME_UNAVAILABLE")):
            with patch("holding.windows_setup.read_pinned_manifest",
                       return_value=(self.pin,)):
                with patch("holding.windows_setup.scan",
                           return_value=self.snapshot):
                    with patch("holding.windows_setup.inspect_versioned_zip",
                               return_value=InstallInspection(
                                   "NOT_INSTALLED", self.install_root,
                               )):
                        with patch("holding.windows_setup.stage_required_dependencies") as staging:
                            summary, executed = execute_setup(
                                manifest=self.manifest, manifest_sha256="b" * 64,
                                install_root=self.install_root,
                                cache_root=self.cache_root,
                                online=True, approved=True,
                                model_root=self.install_root / "models",
                            )
        self.assertEqual(summary.status, "BLOCKED")
        self.assertIsNone(executed)
        self.assertIn("VOLUME_UNAVAILABLE", " ".join(summary.blockers))
        staging.assert_not_called()

    def test_corrupt_pinned_payload_blocks_setup(self):
        summary, _ = self.call_preflight(observed="BLOCKED_INVALID")
        self.assertEqual(summary.status, "BLOCKED")
        self.assertTrue(any("Corrupt pinned" in text for text in summary.blockers))

    def test_no_pin_blocks_before_download(self):
        summary, _ = self.call_preflight(pins=())
        self.assertEqual(summary.status, "BLOCKED")
        self.assertTrue(any("Missing release pin" in text for text in summary.blockers))

    def test_healthy_machine_still_needs_model_benchmark(self):
        summary, _ = self.call_preflight(snapshot=sample())
        self.assertEqual(summary.status, "BENCHMARK_REQUIRED")
        self.assertEqual(summary.download_upper_bound_bytes, 0)

    def test_offline_without_model_blocks_before_mutation(self):
        summary, _ = self.call_preflight(
            snapshot=sample(models=()), online=False,
        )
        self.assertEqual(summary.status, "BLOCKED")
        self.assertTrue(summary.blockers)

    def test_stopped_ollama_preflight_requires_consent_offline(self):
        snapshot = sample(
            models=(),
            ollama=Component(
                "FOUND_BROKEN", path="C:/Tools/ollama.exe",
                version="0.12",
                reason="Ollama installed but local API is not healthy",
            ),
        )
        summary, pins = self.call_preflight(
            snapshot=snapshot, online=False, pins=(),
        )
        self.assertEqual(summary.status, "CONSENT_REQUIRED")
        self.assertEqual(summary.download_upper_bound_bytes, 0)
        self.assertFalse(summary.blockers)
        self.assertTrue(any(action == "START_SERVICE"
                            for _, action, _ in summary.dependency_actions))

    def test_execute_setup_starts_ollama_before_postcheck(self):
        from holding.provision_runtime import ProvisionExecution
        before = sample(
            ollama=Component(
                "FOUND_BROKEN", path="C:/Tools/ollama.exe",
                version="0.12",
                reason="Ollama installed but local API is not healthy",
            ),
        )
        after = sample()
        summary = self.call_preflight(snapshot=before, pins=())[0]
        stage = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED",
            (ProvisionStep("ollama", "START_SERVICE", "SERVICE_START_PENDING"),),
            (),
        )
        models = self.install_root / "models"
        with patch("holding.windows_setup.preflight_setup",
                   return_value=(summary, ())):
            with patch("holding.windows_setup.scan", return_value=before):
                with patch("holding.windows_setup.stage_required_dependencies",
                           return_value=stage):
                    with patch("holding.windows_setup.ensure_selected_model_volume",
                               return_value=(stage, after)) as run:
                        result_summary, checked = execute_setup(
                            manifest=self.manifest, manifest_sha256="b" * 64,
                            install_root=self.install_root,
                            cache_root=self.cache_root,
                            online=False, approved=True, model_root=models,
                        )
        self.assertEqual(result_summary, summary)
        self.assertEqual(checked.state,
                         "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING")
        self.assertEqual(run.call_args.kwargs["model_root"], models)

    def test_no_approval_cannot_call_staging(self):
        with patch("holding.windows_setup.preflight_setup",
                   return_value=self.call_preflight()):
            with patch("holding.windows_setup.stage_required_dependencies") as stage:
                summary, executed = execute_setup(
                    manifest=self.manifest, manifest_sha256="b" * 64,
                    install_root=self.install_root, cache_root=self.cache_root,
                    online=True, approved=False,
                )
        self.assertIsNone(executed)
        self.assertEqual(summary.status, "CONSENT_REQUIRED")
        stage.assert_not_called()

    def test_approval_rechecks_inventory_and_never_claims_ready(self):
        expected = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED",
            (ProvisionStep("opencode", "INSTALL", "STAGED_INSTALLED"),), (),
        )
        summary = self.call_preflight()[0]
        callback = lambda x, y: None
        with patch("holding.windows_setup.preflight_setup",
                   return_value=(summary, (self.pin,))):
            with patch("holding.windows_setup.scan", return_value=self.snapshot) as scan:
                with patch("holding.windows_setup.stage_required_dependencies",
                           return_value=expected) as stage:
                    actual, executed = execute_setup(
                        manifest=self.manifest, manifest_sha256="b" * 64,
                        install_root=self.install_root, cache_root=self.cache_root,
                        online=True, approved=True, cancelled=lambda: False,
                        progress=callback,
                    )
        self.assertEqual(actual, summary)
        self.assertEqual(executed.state, "POST_INSTALL_RECHECK_REQUIRED")
        self.assertEqual(scan.call_count, 2)
        self.assertEqual(scan.call_args.args, (self.install_root,))
        self.assertTrue(stage.call_args.kwargs["authorized"])
        self.assertIs(stage.call_args.kwargs["progress"], callback)

    def test_post_install_requires_real_healthy_dependency_not_installer_exit(self):
        result = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED",
            (ProvisionStep("opencode", "INSTALL", "INSTALLED_NOT_HEALTHCHECKED"),),
            (),
        )
        invalid = assess_post_install(result, self.snapshot, (self.pin,))
        self.assertEqual(invalid.state, "POST_INSTALL_RECHECK_REQUIRED")
        self.assertIn("opencode", invalid.blockers[0])
        self.assertEqual(invalid.steps[-2].result, "NOT_FOUND")

    def test_post_install_enforces_exact_pinned_version(self):
        result = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED",
            (ProvisionStep("opencode", "INSTALL", "STAGED_NATIVE_PROBED"),),
            (),
        )
        wrong = sample(opencode=Component("FOUND_VALID", version="opencode 10.0"))
        outcome = assess_post_install(result, wrong, (self.pin,))
        self.assertEqual(outcome.state, "POST_INSTALL_RECHECK_REQUIRED")
        self.assertEqual(outcome.steps[-2].result, "VERSION_MISMATCH")
        valid = sample(opencode=Component("FOUND_VALID", version="opencode 1.0"))
        outcome = assess_post_install(result, valid, (self.pin,))
        self.assertEqual(outcome.state, "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING")
        self.assertFalse(outcome.blockers)
        self.assertEqual(outcome.steps[-2].result, "FOUND_VALID")

    def test_mingit_release_tag_differs_from_native_git_version(self):
        from holding.managed_toolchain import ManagedTool
        pin = PinnedZip(
            "git", "2.56.0.2",
            "https://github.com/git-for-windows/git/releases/download/"
            "v2.56.0.windows.2/MinGit-2.56.0.2-64-bit.zip",
            "a" * 64, 128, 500, "cmd/git.exe", "2.56.0",
        )
        staged = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED",
            (ProvisionStep("git", "INSTALL", "STAGED_NATIVE_PROBED"),), (),
        )
        missing = sample(git=Component("NOT_FOUND"))
        with patch("holding.windows_setup.resolve_managed_tool",
                   return_value=ManagedTool(
                       "git", "VERIFIED_EXECUTABLE",
                       str(self.install_root / "cmd" / "git.exe"), "2.56.0",
                   )):
            confirmed = assess_post_install(
                staged, missing, (pin,), install_root=self.install_root,
            )
        self.assertEqual(
            confirmed.state, "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING",
        )
        self.assertFalse(confirmed.blockers)
        # The asset release tag is not the actual binary version.
        inconsistent = sample(git=Component(
            "FOUND_VALID", version="git version 2.56.0.2",
        ))
        mismatch = assess_post_install(staged, inconsistent, (pin,))
        self.assertEqual(mismatch.state, "POST_INSTALL_RECHECK_REQUIRED")
        self.assertTrue(mismatch.blockers)

    def test_git_for_windows_release_suffix_matches_exact_pin_only(self):
        from holding.windows_package_manager import WingetPin
        pin = WingetPin("git", "Git.Git", "2.55.0")
        steps = (ProvisionStep("git", "INSTALL", "INSTALLED_NOT_HEALTHCHECKED"),)
        result = ProvisionExecution("PAYLOADS_STAGED_NOT_VALIDATED", steps, ())
        expected = sample(git=Component(
            "FOUND_VALID", version="git version 2.55.0.windows.1",
        ))
        valid = assess_post_install(result, expected, (pin,))
        self.assertEqual(valid.state, "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING")
        for label in ("git version 2.55.01.windows.1",
                      "git version 2.55.0-alpha", "git version 2.55.0.1"):
            with self.subTest(version=label):
                wrong = sample(git=Component("FOUND_VALID", version=label))
                invalid = assess_post_install(result, wrong, (pin,))
                self.assertEqual(invalid.state, "POST_INSTALL_RECHECK_REQUIRED")

    def test_verified_managed_zip_executable_works_without_windows_path(self):
        from holding.managed_toolchain import ManagedTool
        managed_pin = PinnedZip(
            "opencode", "1.0", self.pin.url, self.pin.sha256,
            self.pin.download_bytes, self.pin.unpacked_bytes,
            "bin/opencode.exe", "1.0",
        )
        result = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED",
            (ProvisionStep("opencode", "INSTALL", "STAGED_NATIVE_PROBED"),), (),
        )
        # System PATH remains unchanged: the user has not registered an app
        # executable globally but Atlas can still bind to its checked copy.
        self.assertEqual(self.snapshot.opencode.status, "NOT_FOUND")
        with patch("holding.windows_setup.resolve_managed_tool",
                   return_value=ManagedTool(
                       "opencode", "VERIFIED_EXECUTABLE",
                       str(self.install_root / "components" / "opencode.exe"), "1.0",
                   )):
            outcome = assess_post_install(
                result, self.snapshot, (managed_pin,),
                install_root=self.install_root,
            )
        self.assertEqual(outcome.state,
                         "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING")
        self.assertTrue(any(step.action == "MANAGED_BINDING" and
                            step.result == "VERIFIED_EXECUTABLE"
                            for step in outcome.steps))

    def test_unverified_managed_zip_remains_blocked_if_path_missing(self):
        from holding.managed_toolchain import ManagedTool
        managed_pin = PinnedZip(
            "opencode", "1.0", self.pin.url, self.pin.sha256,
            self.pin.download_bytes, self.pin.unpacked_bytes,
            "opencode.exe", "1.0",
        )
        result = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED",
            (ProvisionStep("opencode", "INSTALL", "STAGED_NATIVE_PROBED"),), (),
        )
        with patch("holding.windows_setup.resolve_managed_tool",
                   return_value=ManagedTool(
                       "opencode", "BINARY_VERSION_MISMATCH",
                   )):
            outcome = assess_post_install(
                result, self.snapshot, (managed_pin,),
                install_root=self.install_root,
            )
        self.assertEqual(outcome.state, "POST_INSTALL_RECHECK_REQUIRED")
        self.assertIn("opencode", " ".join(outcome.blockers))

    def test_ollama_binary_without_running_api_is_not_healthy(self):
        from holding.managed_toolchain import ManagedTool
        model_pin = PinnedZip(
            "ollama", "0.12", self.pin.url, self.pin.sha256,
            self.pin.download_bytes, self.pin.unpacked_bytes,
            "ollama.exe", "0.12",
        )
        result = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED",
            (ProvisionStep("ollama", "INSTALL", "STAGED_NATIVE_PROBED"),), (),
        )
        snapshot = sample(ollama=Component("NOT_FOUND"))
        with patch("holding.windows_setup.resolve_managed_tool") as resolver:
            outcome = assess_post_install(
                result, snapshot, (model_pin,),
                install_root=self.install_root,
            )
        self.assertEqual(outcome.state, "POST_INSTALL_RECHECK_REQUIRED")
        resolver.assert_not_called()

    def test_native_winget_binary_after_install_does_not_need_relogon(self):
        from holding.windows_package_manager import WingetPin
        from holding.winget_binary import WingetBinary
        pin = WingetPin("opencode", "SST.opencode", "1.18.29")
        staged = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED",
            (ProvisionStep("opencode", "INSTALL",
                           "INSTALLED_NOT_HEALTHCHECKED"),),
            (),
        )
        missing = sample(opencode=Component("NOT_FOUND"))
        with patch("holding.windows_setup.discover_winget_binary",
                   return_value=WingetBinary(
                       "EXACT_BINARY_VERIFIED",
                       str(self.install_root / "opencode.exe"),
                       "opencode 1.18.29",
                   )) as locate:
            result = assess_post_install(
                staged, missing, (pin,), install_root=self.install_root,
            )
        self.assertEqual(result.state,
                         "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING")
        self.assertTrue(any(row.action == "WINGET_BINDING" and
                            row.result == "EXACT_BINARY_VERIFIED"
                            for row in result.steps))
        locate.assert_called_once_with(pin)

    def test_winget_version_mismatch_does_not_satisfy_health(self):
        from holding.windows_package_manager import WingetPin
        from holding.winget_binary import WingetBinary
        pin = WingetPin("opencode", "SST.opencode", "1.18.29")
        staged = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED",
            (ProvisionStep("opencode", "INSTALL",
                           "INSTALLED_NOT_HEALTHCHECKED"),),
            (),
        )
        with patch("holding.windows_setup.discover_winget_binary",
                   return_value=WingetBinary("NO_PINNED_BINARY_FOUND")):
            result = assess_post_install(
                staged, sample(opencode=Component("NOT_FOUND")),
                (pin,), install_root=self.install_root,
            )
        self.assertEqual(result.state, "POST_INSTALL_RECHECK_REQUIRED")
        self.assertTrue(result.blockers)

    def test_healthy_reused_tool_need_not_match_new_manifest_release_version(self):
        result = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED",
            (ProvisionStep("opencode", "REUSE", "REUSED_HEALTHY"),), (),
        )
        valid = sample(opencode=Component("FOUND_VALID", version="opencode 0.9"))
        outcome = assess_post_install(result, valid, (self.pin,))
        self.assertEqual(outcome.state, "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING")

    def test_network_probe_fails_closed_without_tokens(self):
        with patch("holding.windows_setup.build_opener", side_effect=OSError):
            self.assertFalse(detect_online())
        with self.assertRaises(ValueError):
            detect_online(timeout=0)


if __name__ == "__main__":
    unittest.main()
