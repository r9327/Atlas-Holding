"""Installed setup stages bundled binaries only after real consent/health."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from holding.provision_runtime import ProvisionExecution
from holding.windows_setup import execute_setup


class SetupRuntimeBridgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.root = root
        self.install = root / "selected-disk"
        self.cache = root / "cache"
        self.runtime = root / "bundled"
        self.install.mkdir()
        self.runtime.mkdir()
        self.pins = {}
        for i, name in enumerate((
                "AtlasHoldingControlCenter.exe", "AtlasHoldingEngine.exe")):
            file = self.runtime / name
            file.write_bytes(bytes([80+i]) * (1_000_011+i))
            self.pins[name] = hashlib.sha256(file.read_bytes()).hexdigest()

    def execute(self, *, approved=True, healthy=True):
        from holding.windows_setup import SetupSummary
        report = SetupSummary(
            "PREFLIGHT", "CONSENT_REQUIRED", "Windows", "x64", "unknown",
            None, 2_000_000_000, 0, (), 0, (),
        )
        work = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED" if healthy
            else "PARTIAL_OR_BLOCKED", (), (),
        )
        snapshot = SimpleNamespace(
            ollama=SimpleNamespace(status="FOUND_VALID"))
        with patch("holding.windows_setup.preflight_setup",
                   return_value=(report, ())), \
             patch("holding.windows_setup.scan", return_value=snapshot), \
             patch("holding.windows_setup.restore_pinned_inventory",
                   return_value=snapshot), \
             patch("holding.windows_setup.stage_required_dependencies",
                   return_value=work), \
             patch("holding.windows_setup.assess_post_install",
                   return_value=ProvisionExecution(
                       "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING", (), ())) as assess:
            result = execute_setup(
                manifest=self.root / "none.json",
                manifest_sha256="a" * 64,
                install_root=self.install,
                cache_root=self.cache,
                online=False, approved=approved,
                runtime_root=self.runtime, runtime_pins=self.pins,
            )
            if not healthy or not approved:
                assess.assert_not_called()
            return result

    def test_only_post_health_and_consent_stage_real_native_pair(self):
        _, result = self.execute()
        self.assertEqual(result.state, "RUNTIME_STAGED_ENGINE_CONFIG_PENDING")
        self.assertFalse(result.blockers)
        step = result.steps[-1]
        self.assertEqual(step.name, "holding-runtime")
        self.assertEqual(step.result, "STAGED_BINARIES_SECURITY_PENDING")
        _, repeated = self.execute()
        self.assertEqual(repeated.steps[-1].result,
                         "REUSED_BINARIES_SECURITY_PENDING")

    def test_neither_no_approval_nor_failed_dependencies_install_binaries(self):
        report, none = self.execute(approved=False)
        self.assertIsNone(none)
        self.assertFalse((self.install / "runtime").exists())
        _, blocked = self.execute(healthy=False)
        self.assertEqual(blocked.state, "PARTIAL_OR_BLOCKED")
        self.assertFalse((self.install / "runtime").exists())


if __name__ == "__main__":
    unittest.main()
