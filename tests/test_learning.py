import unittest
from dataclasses import replace
import hashlib
from holding.learning import Evaluation, evaluate_candidate

SHA = "a" * 64
REPOSITORY_SHA = "b" * 40
ENVIRONMENT_SHA = "c" * 64


def trial(id, passed=9):
    evidence = hashlib.sha256(id.encode("utf-8")).hexdigest()
    return Evaluation(id, "atlas-hidden-v1", SHA, REPOSITORY_SHA,
                      ENVIRONMENT_SHA, evidence, passed, 10, 0, 0, 0,
                      "trusted_test_runner")


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

    def test_different_repository_or_environment_is_not_comparable(self):
        for field, value in (("repository_sha", "d" * 40),
                             ("environment_sha256", "e" * 64)):
            with self.subTest(field=field):
                candidate = replace(trial("new", 10), **{field: value})
                decision = evaluate_candidate(trial("old", 8), candidate)
                self.assertEqual(decision.action, "REJECT")
                self.assertIn("benchmark context", decision.reason)

    def test_same_report_cannot_be_relabelled_as_a_new_strategy(self):
        current = trial("old", 8)
        candidate = replace(trial("new", 10),
                            evidence_sha256=current.evidence_sha256)
        decision = evaluate_candidate(current, candidate)
        self.assertEqual(decision.action, "REJECT")
        self.assertIn("reused", decision.reason)

    def test_too_few_trials_rejected(self):
        self.assertEqual(evaluate_candidate(trial("old", 8), trial("new", 10), min_trials=20).action, "REJECT")

    def test_invalid_counts_rejected(self):
        x = replace(trial("new", 10), passed=11)
        self.assertEqual(evaluate_candidate(trial("old", 8), x).action, "REJECT")

    def test_partial_or_different_trial_count_cannot_win(self):
        candidate = replace(trial("new", 10), passed=19, total=20)
        decision = evaluate_candidate(trial("old", 8), candidate)
        self.assertEqual(decision.action, "REJECT")
        self.assertIn("complete comparison corpus", decision.reason)

    def test_non_integer_or_malformed_evidence_is_rejected_not_crashed(self):
        malformed = (
            replace(trial("new"), passed=True),
            replace(trial("new"), total=10.5),
            replace(trial("new"), suite_sha256=None),
            replace(trial("new"), repository_sha="not-a-sha"),
            replace(trial("new"), environment_sha256=None),
            replace(trial("new"), evidence_sha256="short"),
            replace(trial("new"), regressions=0.5),
        )
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                self.assertEqual(
                    evaluate_candidate(trial("old", 8), candidate).action,
                    "REJECT",
                )

    def test_invalid_minimum_trial_shape_is_rejected(self):
        for minimum in (True, 0, -1, 10.5):
            with self.subTest(minimum=minimum):
                self.assertEqual(
                    evaluate_candidate(trial("old", 8), trial("new", 9),
                                       min_trials=minimum).action,
                    "REJECT",
                )

    def test_same_strategy_is_kept(self):
        self.assertEqual(evaluate_candidate(trial("old", 8), trial("old", 10)).action, "KEEP")


if __name__ == "__main__":
    unittest.main()
