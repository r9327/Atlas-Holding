import hashlib
import unittest
from dataclasses import replace

from holding.model_selection import (
    HardwareBudget, ModelBenchmark, select_model,
)


SUITE_SHA = "a" * 64
REPOSITORY_SHA = "b" * 40
ENVIRONMENT_SHA = "c" * 64
BUDGET = HardwareBudget(24 * 1024**3, 8 * 1024**3, 16_384)


def benchmark(name="model-a", *, quality=95, latency=1000, role="CODER"):
    return ModelBenchmark(
        name, hashlib.sha256((name + "-model").encode()).hexdigest(), role,
        "atlas-hidden-v1", SUITE_SHA, REPOSITORY_SHA, ENVIRONMENT_SHA,
        hashlib.sha256((name + "-evidence").encode()).hexdigest(),
        20, 20, quality, 100, 10, 10, 0, 0, 0, latency,
        12 * 1024**3, 7 * 1024**3, 32_768, False, True,
        "trusted_test_runner",
    )


def select(*items, profile="BALANCED_VERIFIED", role="CODER", budget=BUDGET):
    return select_model(
        tuple(items), role=role, profile=profile, budget=budget,
        expected_suite_id="atlas-hidden-v1",
        expected_suite_sha256=SUITE_SHA,
        expected_repository_sha=REPOSITORY_SHA,
        expected_environment_sha256=ENVIRONMENT_SHA,
    )


class ModelSelectionTests(unittest.TestCase):
    def test_balanced_prefers_faster_model_inside_quality_tolerance(self):
        result = select(
            benchmark("quality", quality=96, latency=3000),
            benchmark("fast", quality=95, latency=900),
        )
        self.assertEqual((result.status, result.model_id), ("SELECTED", "fast"))
        self.assertEqual(result.eligible_models, 2)

    def test_quality_first_keeps_the_best_measured_quality(self):
        result = select(
            benchmark("quality", quality=96, latency=3000),
            benchmark("fast", quality=95, latency=900),
            profile="QUALITY_FIRST",
        )
        self.assertEqual(result.model_id, "quality")

    def test_required_tools_security_and_regressions_are_blocking(self):
        changes = (
            {"required_passed": 19}, {"tool_passed": 9},
            {"critical_failures": 1}, {"security_failures": 1},
            {"regressions": 1},
        )
        for change in changes:
            with self.subTest(change=change):
                self.assertEqual(select(replace(benchmark(), **change)).status, "BLOCKED")

    def test_uninstalled_or_wrong_role_is_never_selected(self):
        self.assertEqual(select(replace(benchmark(), installed=False)).status, "BLOCKED")
        self.assertEqual(select(benchmark(role="AUDITOR")).status, "BLOCKED")

    def test_benchmark_context_must_match_exactly(self):
        fields = {
            "suite_id": "other-suite",
            "suite_sha256": "d" * 64,
            "repository_sha": "e" * 40,
            "environment_sha256": "f" * 64,
        }
        for field, value in fields.items():
            with self.subTest(field=field):
                self.assertEqual(
                    select(replace(benchmark(), **{field: value})).status,
                    "BLOCKED",
                )

    def test_ram_vram_and_context_are_real_gates(self):
        self.assertEqual(select(replace(benchmark(), peak_ram_bytes=BUDGET.ram_bytes + 1)).status, "BLOCKED")
        self.assertEqual(select(replace(benchmark(), peak_vram_bytes=BUDGET.vram_bytes + 1)).status, "BLOCKED")
        self.assertEqual(select(replace(benchmark(), context_tokens=8192)).status, "BLOCKED")

    def test_cpu_offload_does_not_excuse_impossible_measured_vram(self):
        item = replace(
            benchmark(), peak_vram_bytes=BUDGET.vram_bytes + 1,
            offload_verified=True,
        )
        self.assertEqual(select(item).status, "BLOCKED")

    def test_tiny_benchmark_corpus_cannot_select_a_model(self):
        item = replace(
            benchmark(), required_passed=1, required_total=1,
            quality_passed=1, quality_total=1, tool_passed=1, tool_total=1,
        )
        self.assertEqual(select(item).status, "BLOCKED")

    def test_self_report_and_malformed_boolean_count_fail_closed(self):
        self.assertEqual(select(replace(benchmark(), source="model_self_report")).status, "BLOCKED")
        self.assertEqual(select(replace(benchmark(), required_passed=True)).status, "BLOCKED")

    def test_same_evidence_cannot_be_relabelled_for_two_models(self):
        first = benchmark("first")
        second = replace(benchmark("second"), evidence_sha256=first.evidence_sha256)
        result = select(first, second)
        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("reused", result.reason)

    def test_duplicate_model_role_benchmark_is_rejected(self):
        first = benchmark()
        second = replace(first, evidence_sha256="d" * 64)
        self.assertEqual(select(first, second).status, "BLOCKED")

    def test_selection_is_deterministic_under_input_reordering(self):
        a = benchmark("a", quality=95, latency=1000)
        b = benchmark("b", quality=95, latency=1000)
        self.assertEqual(select(a, b).model_id, select(b, a).model_id)

    def test_invalid_profile_or_hardware_context_is_blocked(self):
        self.assertEqual(select(benchmark(), profile="FASTEST").status, "BLOCKED")
        bad = HardwareBudget(True, 8 * 1024**3, 16_384)
        self.assertEqual(select(benchmark(), budget=bad).status, "BLOCKED")


if __name__ == "__main__":
    unittest.main()
