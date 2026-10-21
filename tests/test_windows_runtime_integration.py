"""End-to-end headless setup → verified runtime staging contracts.

Tests the actual execute_setup wiring and filesystem writes; dependency
download/probe remains explicitly simulated and does not certify a Windows PC.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.provision_runtime import ProvisionExecution, ProvisionStep
from holding.windows_runtime_payload import verify_runtime_payload
from holding.windows_setup import SetupSummary, execute_setup


class InstalledPairIntegrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.source = self.root / "bundled"
        self.source.mkdir()
        self.install = self.root / "disk selected" / "Atlas Holding"
        self.install.mkdir(parents=True)
        self.cache = self.root / "downloads"
        self.config = self.root / "manifest.json"
        self.pin = "d" * 64
        self.names = ("AtlasHoldingEngine.exe", "AtlasHoldingControlCenter.exe")
        self.pins = {}
        for idx, name in enumerate(self.names):
            value = bytes([64 + idx]) * (1_000_025 + idx)
            (self.source / name).write_bytes(value)
            self.pins[name] = hashlib.sha256(value).hexdigest()
        self.report = SetupSummary(
            "PREFLIGHT", "BENCHMARK_REQUIRED", "Windows", "AMD64",
            "", 32 * 1024**3, 32 * 1024**3, 0, (), 0, (),
        )
        self.stage = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED", (), ())
        self.healthy = ProvisionExecution(
            "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING", (), ())

    def execute(self, *, approved=True, outcome=None, **kwargs):
        args = {
            "manifest": self.config, "manifest_sha256": self.pin,
            "install_root": self.install, "cache_root": self.cache,
            "online": False, "approved": approved,
            "runtime_root": self.source, "runtime_pins": self.pins,
            **kwargs,
        }
        with patch("holding.windows_setup.preflight_setup",
                   return_value=(self.report, ())), \
             patch("holding.windows_setup.scan", return_value=object()), \
             patch("holding.windows_setup.restore_pinned_inventory",
                   return_value=object()), \
             patch("holding.windows_setup.stage_required_dependencies",
                   return_value=self.stage), \
             patch("holding.windows_setup.assess_post_install",
                   return_value=self.healthy if outcome is None else outcome):
            return execute_setup(**args)

    def test_first_approved_setup_stages_real_files_and_exposes_pending_gate(self):
        summary, result = self.execute()
        self.assertIs(summary, self.report)
        self.assertEqual(result.state, "RUNTIME_STAGED_ENGINE_CONFIG_PENDING")
        self.assertEqual(len(result.steps), 1)
        self.assertEqual(result.steps[-1].result,
                         "STAGED_BINARIES_SECURITY_PENDING")
        installs = list((self.install / "runtime").iterdir())
        self.assertEqual(len(installs), 1)
        self.assertEqual(verify_runtime_payload(installs[0], self.pins), self.pins)
        self.assertFalse(any(s.result == "READY" for s in result.steps))

    def test_reusing_same_version_makes_no_duplicate_or_overwrite(self):
        _, previous = self.execute()
        self.assertEqual(previous.state, "RUNTIME_STAGED_ENGINE_CONFIG_PENDING")
        _, reused = self.execute()
        self.assertEqual(reused.steps[-1].result,
                         "REUSED_BINARIES_SECURITY_PENDING")
        self.assertEqual(len(list((self.install / "runtime").iterdir())), 1)

    def test_unapproved_setup_never_writes_any_runtime_file(self):
        _, result = self.execute(approved=False)
        self.assertIsNone(result)
        self.assertFalse((self.install / "runtime").exists())

    def test_postinstall_blocker_never_stages_runtime_files(self):
        blocked = ProvisionExecution("POST_INSTALL_RECHECK_REQUIRED", (),
                                     ("Native prerequisite unhealthy",))
        _, result = self.execute(outcome=blocked)
        self.assertEqual(result, blocked)
        self.assertFalse((self.install / "runtime").exists())

    def test_tampered_embedded_executable_refused_before_dependency_mutation(self):
        (self.source / self.names[0]).write_bytes(b"tampered")
        with patch("holding.windows_setup.preflight_setup") as prepare:
            with self.assertRaises(ValueError):
                execute_setup(
                    manifest=self.config, manifest_sha256=self.pin,
                    install_root=self.install, cache_root=self.cache,
                    online=False, approved=True,
                    runtime_root=self.source, runtime_pins=self.pins,
                )
            prepare.assert_not_called()
        self.assertFalse((self.install / "runtime").exists())

    def test_stage_failure_reports_partial_not_installed(self):
        with patch("holding.windows_setup.install_pinned_runtime_pair",
                   side_effect=OSError("read only drive")):
            _, result = self.execute()
        self.assertEqual(result.state, "PARTIAL_OR_BLOCKED")
        self.assertEqual(result.steps[-1].result, "RUNTIME_STAGE_FAILED")
        self.assertTrue(result.blockers)
        self.assertNotIn("read only drive", " ".join(result.blockers))
        self.assertFalse((self.install / "runtime").exists())


if __name__ == "__main__":
    unittest.main()
