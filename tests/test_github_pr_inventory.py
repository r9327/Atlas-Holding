"""F10 PR inventory: never trust a listing as functional proof or authority."""
from __future__ import annotations

from dataclasses import asdict
import json
import unittest

from holding.github_pr_inventory import (
    PrInventoryError, parse_open_prs, validate_open_pr_inventory,
)

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40


def pr(number=3, *, origin=REPO):
    return {
        "number": number, "state": "open", "draft": False,
        "head": {"ref": f"feature/issue-{number}", "sha": SHA,
                 "repo": {"full_name": origin}},
        "base": {"sha": "b" * 40, "repo": {"full_name": REPO}},
    }


class OpenPrInventoryTests(unittest.TestCase):
    def test_mixed_same_repo_and_fork_is_observed_not_certified(self):
        report = parse_open_prs(REPO, [pr(3), pr(4, origin="other/fork")])
        self.assertEqual(report.status, "OPEN_PRS_OBSERVED")
        self.assertEqual(report.observed_count, 2)
        self.assertEqual([p.same_repository for p in report.hints], [True, False])
        self.assertFalse(report.can_certify)
        self.assertFalse(report.can_skip_implementation)
        copy = validate_open_pr_inventory(REPO, json.loads(json.dumps(asdict(report))))
        self.assertEqual(copy, report)

    def test_bounded_first_page_does_not_pretend_pagination_is_complete(self):
        report = parse_open_prs(REPO, [pr(i + 1) for i in range(100)])
        self.assertEqual(report.status, "PAGINATION_NOT_VERIFIED")
        self.assertEqual(len(report.hints), 20)
        self.assertEqual(report.observed_count, 100)
        self.assertEqual(validate_open_pr_inventory(REPO, json.loads(json.dumps(asdict(report)))), report)

    def test_no_open_prs_means_only_no_pr_on_observed_api_page(self):
        report = parse_open_prs(REPO, [])
        self.assertEqual(report.status, "OPEN_PRS_OBSERVED")
        self.assertEqual(report.hints, ())
        self.assertFalse(report.can_skip_implementation)

    def test_malformed_and_cross_repo_metadata_fail_closed(self):
        failures = [
            [pr(1), pr(1)], [dict(pr(2), state="closed")],
            [dict(pr(2), draft=1)],
            [dict(pr(2), number=True)],
            [dict(pr(2), head={"ref": "../evil", "sha": SHA,
                                "repo": {"full_name": REPO}})],
            [dict(pr(2), base={"sha": "b" * 40,
                               "repo": {"full_name": "other/repo"}})],
            [dict(pr(2), head={"ref": "feature/good", "sha": "invalid",
                                "repo": {"full_name": REPO}})],
        ]
        for rows in failures:
            with self.subTest(rows=rows), self.assertRaises(PrInventoryError):
                parse_open_prs(REPO, rows)
        with self.assertRaises(PrInventoryError):
            parse_open_prs(REPO, [pr(i + 1) for i in range(101)])

    def test_broker_result_cannot_forge_authority_or_completeness(self):
        valid = json.loads(json.dumps(asdict(parse_open_prs(REPO, [pr(3)]))))
        variants = (
            {"can_skip_implementation": True},
            {"can_certify": True},
            {"repository": "other/repo"},
            {"observed_count": 100},
            {"status": "PAGINATION_NOT_VERIFIED"},
            {"status": ["OPEN_PRS_OBSERVED"]},
            {"hints": []},
        )
        for change in variants:
            with self.subTest(change=change), self.assertRaises(PrInventoryError):
                validate_open_pr_inventory(REPO, {**valid, **change})
        with self.assertRaises(PrInventoryError):
            validate_open_pr_inventory(REPO, {**valid, "hints": [
                {**valid["hints"][0], "head_sha": "x" * 40},
            ]})


if __name__ == "__main__":
    unittest.main()
