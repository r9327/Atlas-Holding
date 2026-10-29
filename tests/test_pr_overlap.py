"""F10 exact PR/local filename overlap is an advisory, bounded heuristic."""
from __future__ import annotations

import unittest

from holding.github_pr_inventory import OpenPrHint, OpenPrInventory
from holding.github_pr_changed_paths import PrChangedPaths
from holding.pr_overlap import choose_relevant_pr, compare_pr_paths

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40


def inventory(*branches):
    hints = tuple(
        OpenPrHint(i + 1, SHA, "b" * 40, branch, False, True)
        for i, branch in enumerate(branches))
    return OpenPrInventory(REPO, "OPEN_PRS_OBSERVED", len(hints), hints)


class PrOverlapTests(unittest.TestCase):
    def test_branch_relevance_selects_one_without_certifying_anything(self):
        report = inventory("feature/unrelated", "fix/calendar-sync",
                           "feature/calendar-ui")
        hint = choose_relevant_pr(
            report, acceptance=("Calendar synchronization should work",),
            candidate_paths=("app/calendar.py",))
        self.assertEqual(hint.number, 2)
        self.assertFalse(report.can_skip_implementation)

    def test_irrelevant_branch_does_not_trigger_network(self):
        hint = choose_relevant_pr(
            inventory("feature/unrelated"),
            acceptance=("Calendar synchronization",),
            candidate_paths=("app/calendar.py",))
        self.assertIsNone(hint)

    def test_exact_path_intersection_is_not_functional_proof(self):
        hint = inventory("fix/calendar-sync").hints[0]
        changed = PrChangedPaths(REPO, hint.number, hint.head_sha,
                                 "PR_PATHS_PARTIAL", 2,
                                 ("app/calendar.py", "docs/readme.md"))
        comparison = compare_pr_paths(
            hint, changed, ("app/calendar.py", "app/feature.py"))
        self.assertEqual(comparison.status, "EXACT_PATH_OVERLAP")
        self.assertEqual(comparison.matching_local_paths, ("app/calendar.py",))
        self.assertFalse(comparison.can_certify)
        self.assertFalse(comparison.can_skip_implementation)

    def test_partial_no_match_never_claims_no_duplicate(self):
        hint = inventory("fix/calendar").hints[0]
        changed = PrChangedPaths(REPO, hint.number, hint.head_sha,
                                 "PR_PATHS_PARTIAL", 1, ("docs/readme.md",))
        result = compare_pr_paths(hint, changed, ("app/calendar.py",))
        self.assertEqual(result.status, "NO_MATCH_IN_PARTIAL_PR_SCAN")
        self.assertFalse(result.can_skip_implementation)

    def test_sha_or_number_mismatch_is_rejected(self):
        hint = inventory("fix/calendar").hints[0]
        for evidence in (
            PrChangedPaths(REPO, 999, SHA, "PR_PATHS_BOUNDED", 1,
                           ("app/calendar.py",)),
            PrChangedPaths(REPO, 1, "c"*40, "PR_PATHS_BOUNDED", 1,
                           ("app/calendar.py",)),
        ):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                compare_pr_paths(hint, evidence, ("app/calendar.py",))


if __name__ == "__main__":
    unittest.main()
