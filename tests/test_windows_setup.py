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

    def test_winget_download_size_is_explicitly_unknown(self):
        from holding.windows_package_manager import WingetPin
        pin = WingetPin("opencode", "SST.opencode", "1.18.29")
        summary, pins = self.call_preflight(pins=(pin,))
        self.assertEqual(summary.status, "CONSENT_REQUIRED")
        self.assertEqual(summary.unestimated_package_downloads, 1)
        self.assertEqual(summary.download_upper_bound_bytes, 0)
        self.assertEqual(summary.dependency_actions[-3][0], "opencode")
        self.assertEqual(summary.dependency_actions[-3][2], "WINGET_VERSION_PINNED")

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
