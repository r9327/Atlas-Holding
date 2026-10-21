"""Integrated setup entry point must never invent native runtime health."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.inventory import Component
from holding.provision_cli import main
from holding.staged_install import InstallInspection, InstallResult, PinnedZip
from test_provision_plan import sample


class ProvisionCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.install = root / "Atlas Holding"
        self.cache = root / "Cached Assets"
        self.manifest = root / "manifest.json"
        self.pin = PinnedZip(
            "opencode", "1.0", "https://example.invalid/pin.zip",
            "a" * 64, 100, 200,
        )
        self.arguments = [
            "--manifest", str(self.manifest),
            "--manifest-sha256", "b" * 64,
            "--install-root", str(self.install),
            "--cache-root", str(self.cache),
            "--online-confirmed",
        ]

    def run_cli(self, *, snapshot=None, approve=False):
        output = io.StringIO()
        args = self.arguments + (["--approve-install"] if approve else [])
        with patch("holding.provision_cli.read_pinned_manifest",
                   return_value=(self.pin,)) as manifest:
            with patch("holding.provision_cli.scan",
                       return_value=snapshot or sample(
                           opencode=Component("NOT_FOUND"))) as inventory:
                with redirect_stdout(output):
                    code = main(args)
        return code, json.loads(output.getvalue()), manifest, inventory

    def test_no_consent_is_preview_only_and_does_not_create_dirs(self):
        with patch("holding.provision_runtime.install_versioned_zip") as install:
            code, report, manifest, inventory = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(report["state"], "AUTH_REQUIRED")
        self.assertEqual(report["steps"][-1]["result"], "AWAITING_APPROVAL")
        self.assertEqual(inventory.call_args.args, (self.install,))
        self.assertEqual(manifest.call_args.kwargs["expected_sha256"], "b" * 64)
        self.assertFalse(self.install.exists())
        self.assertFalse(self.cache.exists())
        install.assert_not_called()

    def test_consent_stages_necessary_component_but_not_claims_ready(self):
        with patch("holding.provision_runtime.inspect_versioned_zip",
                   return_value=InstallInspection("NOT_INSTALLED", self.install)):
            with patch("holding.provision_runtime.install_versioned_zip",
                       return_value=InstallResult("INSTALLED", self.install, 3)
                       ) as install:
                code, report, _, _ = self.run_cli(approve=True)
        self.assertEqual(code, 2)
        self.assertEqual(report["state"], "POST_INSTALL_RECHECK_REQUIRED")
        self.assertTrue(any(row["result"] == "STAGED_INSTALLED"
                            for row in report["steps"]))
        self.assertTrue(any("opencode" in item for item in report["blockers"]))
        self.assertIs(install.call_args.kwargs["authorized"], True)
        self.assertEqual(install.call_args.args[0].component, "opencode")

    def test_postinstall_healthy_toolchain_still_waits_for_benchmarks(self):
        healthy = sample()
        output = io.StringIO()
        with patch("holding.provision_cli.read_pinned_manifest",
                   return_value=(self.pin,)):
            with patch("holding.provision_cli.scan",
                       side_effect=[healthy, healthy]) as scan:
                with patch("holding.provision_runtime.install_versioned_zip") as install:
                    with redirect_stdout(output):
                        code = main(self.arguments + ["--approve-install"])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["state"],
                         "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING")
        self.assertEqual(scan.call_count, 2)
        install.assert_not_called()

    def test_cli_post_install_recheck_uses_actual_second_inventory(self):
        first = sample(opencode=Component("NOT_FOUND"))
        healthy = sample(opencode=Component("FOUND_VALID", version="opencode 1.0"))
        output = io.StringIO()
        with patch("holding.provision_cli.read_pinned_manifest",
                   return_value=(self.pin,)):
            with patch("holding.provision_cli.scan",
                       side_effect=[first, healthy]) as scan:
                with patch("holding.provision_runtime.inspect_versioned_zip",
                           return_value=InstallInspection("NOT_INSTALLED",
                                                          self.install)):
                    with patch("holding.provision_runtime.install_versioned_zip",
                               return_value=InstallResult(
                                   "INSTALLED", self.install, 3)):
                        with redirect_stdout(output):
                            code = main(self.arguments + ["--approve-install"])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["state"],
                         "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING")
        self.assertEqual(scan.call_count, 2)

    def test_no_local_model_offline_blocks_without_staging(self):
        args = [value for value in self.arguments if value != "--online-confirmed"]
        output = io.StringIO()
        with patch("holding.provision_cli.read_pinned_manifest",
                   return_value=(self.pin,)):
            with patch("holding.provision_cli.scan",
                       return_value=sample(models=())):
                with patch("holding.provision_runtime.install_versioned_zip") as install:
                    with redirect_stdout(output):
                        code = main(args + ["--approve-install"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["state"], "BLOCKED")
        install.assert_not_called()

    def test_invalid_manifest_does_not_probe_system_or_log_secrets(self):
        output = io.StringIO()
        from holding.bootstrap_assets_cli import BootstrapManifestError
        with patch("holding.provision_cli.read_pinned_manifest",
                   side_effect=BootstrapManifestError("private-url-token")):
            with patch("holding.provision_cli.scan") as inventory:
                with redirect_stdout(output):
                    code = main(self.arguments + ["--approve-install"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["state"], "BLOCKED")
        self.assertNotIn("private-url-token", output.getvalue())
        inventory.assert_not_called()

    def test_relative_destination_rejected_before_manifest_read(self):
        values = self.arguments.copy()
        values[values.index("--cache-root") + 1] = "relative"
        output = io.StringIO()
        with patch("holding.provision_cli.read_pinned_manifest") as manifest:
            with redirect_stdout(output):
                code = main(values)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["state"], "BLOCKED")
        manifest.assert_not_called()


if __name__ == "__main__":
    unittest.main()
