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
                                  preflight_setup, execute_setup)
from test_provision_plan import sample


class WindowsSetupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.manifest = base / "assets.json"
        self.install_root = base / "holding"
        self.cache_root = base / "downloads"
        self.pin = PinnedZip(
            "opencode", "1.0", "https://example.invalid/archive.zip",
            "a" * 64, 8123, 12345,
        )
        self.snapshot = sample(opencode=Component("NOT_FOUND"))

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
