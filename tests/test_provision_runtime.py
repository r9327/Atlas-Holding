"""The provisioning executor must preserve healthy programs and fail closed."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.inventory import Component
from holding.provision_runtime import stage_required_dependencies
from holding.staged_install import (InstallError, InstallInspection, InstallResult,
                                    PinnedZip)
from test_provision_plan import sample


class ProvisionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "installed"
        self.cache = Path(self.temp.name) / "cache"
        self.ollama = PinnedZip("ollama", "1.2", "https://example.invalid/ollama.zip",
                                "a" * 64, 123, 456)
        self.opencode = PinnedZip("opencode", "2.1", "https://example.invalid/opencode.zip",
                                  "b" * 64, 321, 789)
        self.all_healthy = sample()
        self.needs_opencode = sample(opencode=Component("NOT_FOUND"))

    def run_plan(self, snapshot=None, pins=(), *, approved=False, online=True):
        return stage_required_dependencies(
            snapshot if snapshot is not None else self.needs_opencode,
            pins, online=online, authorized=approved, install_root=self.root,
            cache_root=self.cache,
        )

    def test_all_healthy_only_benchmarks_no_reinstall(self):
        with patch("holding.provision_runtime.install_versioned_zip") as install:
            result = self.run_plan(self.all_healthy, approved=True)
        self.assertEqual(result.state, "PAYLOADS_STAGED_NOT_VALIDATED")
        self.assertTrue(result.steps)
        self.assertTrue(all(row.result == "REUSED_HEALTHY" for row in result.steps))
        install.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_missing_pin_blocks_everything_before_mutation(self):
        with patch("holding.provision_runtime.install_versioned_zip") as install:
            result = self.run_plan(pins=(), approved=True)
        self.assertEqual(result.state, "BLOCKED")
        self.assertIn("opencode", result.blockers[0])
        install.assert_not_called()

    def test_explicit_consent_required_for_missing_dependency(self):
        with patch("holding.provision_runtime.install_versioned_zip") as install:
            result = self.run_plan(pins=(self.opencode,))
        self.assertEqual(result.state, "AUTH_REQUIRED")
        self.assertEqual(result.steps[-1].result, "AWAITING_APPROVAL")
        install.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_missing_dependency_stages_only_one_pinned_payload(self):
        with patch(
            "holding.provision_runtime.inspect_versioned_zip",
            return_value=InstallInspection("NOT_INSTALLED", self.root),
        ) as inspect:
            with patch(
                "holding.provision_runtime.install_versioned_zip",
                return_value=InstallResult("INSTALLED", self.root / "new", 2),
            ) as install:
                result = self.run_plan(pins=(self.opencode, self.ollama),
                                       approved=True)
        self.assertEqual(result.state, "PAYLOADS_STAGED_NOT_VALIDATED")
        self.assertEqual(result.steps[-1].result, "STAGED_INSTALLED")
        self.assertEqual(install.call_count, 1)
        self.assertEqual(install.call_args.args[0].component, "opencode")
        self.assertIs(install.call_args.kwargs["authorized"], True)
        self.assertEqual(inspect.call_count, 1)

    def test_installed_matching_pin_is_not_downloaded(self):
        with patch(
            "holding.provision_runtime.inspect_versioned_zip",
            return_value=InstallInspection("VERIFIED_PAYLOAD", self.root, 3),
        ):
            with patch("holding.provision_runtime.install_versioned_zip") as install:
                result = self.run_plan(pins=(self.opencode,), approved=True)
        self.assertEqual(result.state, "PAYLOADS_STAGED_NOT_VALIDATED")
        self.assertEqual(result.steps[-1].result, "STAGED_VERIFIED")
        install.assert_not_called()

    def test_tampered_version_blocks_no_replacement(self):
        with patch(
            "holding.provision_runtime.inspect_versioned_zip",
            return_value=InstallInspection("BLOCKED_INVALID", self.root),
        ):
            with patch("holding.provision_runtime.install_versioned_zip") as install:
                result = self.run_plan(pins=(self.opencode,), approved=True)
        self.assertEqual(result.state, "PARTIAL_OR_BLOCKED")
        self.assertEqual(result.steps[-1].result, "BLOCKED_INVALID")
        install.assert_not_called()

    def test_partial_failure_is_never_reported_as_success(self):
        snapshot = sample(ollama=Component("FOUND_BROKEN"),
                          opencode=Component("NOT_FOUND"))
        with patch(
            "holding.provision_runtime.inspect_versioned_zip",
            return_value=InstallInspection("NOT_INSTALLED", self.root),
        ):
            with patch(
                "holding.provision_runtime.install_versioned_zip",
                side_effect=[
                    InstallResult("INSTALLED", self.root / "first", 2),
                    InstallError("failure"),
                ],
            ) as install:
                result = self.run_plan(snapshot, (self.ollama, self.opencode),
                                       approved=True)
        self.assertEqual(result.state, "PARTIAL_OR_BLOCKED")
        self.assertEqual(install.call_count, 2)
        self.assertEqual(result.steps[-2].result, "STAGED_INSTALLED")
        self.assertEqual(result.steps[-1].result, "BLOCKED")

    def test_unsupported_windows_or_disk_blocks_before_install(self):
        for snapshot in (
            sample(os_architecture="ARM64"),
            sample(installation_disk_free_bytes=1),
            sample(models=()),
        ):
            with self.subTest(snapshot=snapshot):
                with patch("holding.provision_runtime.install_versioned_zip") as install:
                    result = self.run_plan(snapshot, (self.opencode,),
                                           approved=True, online=False)
                self.assertEqual(result.state, "BLOCKED")
                install.assert_not_called()

    def test_duplicate_and_invalid_pins_are_refused(self):
        result = self.run_plan(pins=(self.opencode, self.opencode), approved=True)
        self.assertEqual(result.state, "BLOCKED")
        self.assertEqual(result.blockers, ("Duplicate component pin",))
        with self.assertRaises(ValueError):
            self.run_plan(pins=(object(),), approved=True)


if __name__ == "__main__":
    unittest.main()
