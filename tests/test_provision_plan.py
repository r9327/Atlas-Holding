"""Installation plan must reuse healthy dependencies and never invent model readiness."""
from __future__ import annotations

from dataclasses import replace
import unittest

from holding.inventory import Component, Inventory, OllamaModel
from holding.provision_plan import make_plan

GIB = 1024**3


def sample(**overrides):
    baseline = Inventory(
        os_name="Windows", os_architecture="AMD64", cpu_threads=16,
        ram_total_bytes=32 * GIB, installation_disk_free_bytes=100 * GIB,
        git=Component("FOUND_VALID", version="git 2.47"),
        python=Component("FOUND_VALID", version="3.13"),
        ollama=Component("FOUND_VALID", version="0.12"),
        opencode=Component("FOUND_VALID", version="1.0"),
        models=(OllamaModel("code:local", "sha256:test", 5 * GIB),),
        gpu="RTX 3060 Ti", vram_total_bytes=8 * GIB, vram_free_bytes=5 * GIB)
    return replace(baseline, **overrides)


class ProvisionPlanTests(unittest.TestCase):
    def test_all_installed_still_needs_quality_benchmark(self):
        plan = make_plan(sample(), online=True)
        self.assertEqual(plan.state, "BENCHMARK_REQUIRED")
        self.assertTrue(plan.model_validation_required)
        self.assertEqual([action.action for action in plan.actions],
                         ["REUSE", "REUSE", "REUSE", "REUSE", "BENCHMARK_INSTALLED"])

    def test_only_broken_component_is_repaired(self):
        plan = make_plan(sample(ollama=Component("FOUND_BROKEN")), online=True)
        self.assertEqual(plan.state, "REPAIR_REQUIRED")
        actions = {item.name: item.action for item in plan.actions}
        self.assertEqual(actions["ollama"], "REPAIR")
        self.assertEqual(actions["git"], "REUSE")
        self.assertEqual(actions["python_runtime"], "REUSE")

    def test_missing_component_reused_python_remains_untouched(self):
        plan = make_plan(sample(opencode=Component("NOT_FOUND")), online=True)
        self.assertEqual(plan.state, "PROVISIONING_REQUIRED")
        self.assertEqual({item.name: item.action for item in plan.actions}["python_runtime"],
                         "REUSE")

    def test_no_model_and_offline_is_explicitly_blocked(self):
        plan = make_plan(sample(models=()), online=False)
        self.assertEqual(plan.state, "BLOCKED")
        self.assertIn("No local model", " ".join(plan.blockers))
        self.assertEqual(plan.actions[-1].action, "BENCHMARK_OR_DOWNLOAD")

    def test_insufficient_app_storage_never_returns_ready(self):
        plan = make_plan(sample(installation_disk_free_bytes=1024), online=True)
        self.assertEqual(plan.state, "BLOCKED")

    def test_non_windows_platform_never_returns_compatible(self):
        for os_name, architecture in (("Linux", "x86_64"), ("Windows", "ARM64"),
                                       ("Windows", "x86")):
            with self.subTest(os_name=os_name, arch=architecture):
                plan = make_plan(sample(os_name=os_name, os_architecture=architecture),
                                 online=True)
                self.assertEqual(plan.state, "BLOCKED")

    def test_invalid_status_is_inspect_not_auto_install(self):
        plan = make_plan(sample(git=Component("WEIRD_STATUS")), online=True)
        actions = {item.name: item.action for item in plan.actions}
        self.assertEqual(actions["git"], "INSPECT")
        self.assertEqual(plan.state, "INSPECTION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
