import unittest
from dataclasses import replace
from holding.learning import Evaluation, evaluate_candidate

SHA = "a" * 64


def trial(id, passed=9):
    return Evaluation(id, "atlas-hidden-v1", SHA, passed, 10, 0, 0, 0, "trusted_test_runner")


class LearningTests(unittest.TestCase):
    def test_measurable_improvement(self):
        self.assertEqual(evaluate_candidate(trial("old", 8), trial("new", 9)).action, "PROMOTE")

    def test_no_promotion_on_tie(self):
        self.assertEqual(evaluate_candidate(trial("old", 9), trial("new", 9)).action, "KEEP")

    def test_no_promotion_on_regression(self):
        x = replace(trial("new", 10), regressions=1)
        self.assertEqual(evaluate_candidate(trial("old", 8), x).action, "REJECT")

    def test_no_promotion_on_security_failure(self):
        x = replace(trial("new", 10), security_failures=1)
        self.assertEqual(evaluate_candidate(trial("old", 8), x).action, "REJECT")

    def test_no_promotion_on_critical_failure(self):
        x = replace(trial("new", 10), critical_failures=1)
        self.assertEqual(evaluate_candidate(trial("old", 8), x).action, "REJECT")

    def test_untrusted_self_report_rejected(self):
        x = replace(trial("new", 10), source="llm_self_report")
        self.assertEqual(evaluate_candidate(trial("old", 8), x).action, "REJECT")

    def test_different_test_suites_rejected(self):
        x = replace(trial("new", 10), suite_sha256="b" * 64)
        self.assertEqual(evaluate_candidate(trial("old", 8), x).action, "REJECT")

    def test_too_few_trials_rejected(self):
        self.assertEqual(evaluate_candidate(trial("old", 8), trial("new", 10), min_trials=20).action, "REJECT")

    def test_invalid_counts_rejected(self):
        x = replace(trial("new", 10), passed=11)
        self.assertEqual(evaluate_candidate(trial("old", 8), x).action, "REJECT")

    def test_same_strategy_is_kept(self):
        self.assertEqual(evaluate_candidate(trial("old", 8), trial("old", 10)).action, "KEEP")


if __name__ == "__main__":
    unittest.main()
