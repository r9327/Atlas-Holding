"""GitHub provenance tests: fail closed on stale, skipped and foreign FULL runs."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from holding.github_full_evidence import (
    _PROTECTED_SOURCES, _REQUIRED_STEPS, GitHubEvidenceError,
    FullEvidence, validate_full_evidence, observe_full_pr,
)

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40
NOW = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
RECENT = (NOW - timedelta(hours=1)).isoformat()


def fixtures():
    pull = {"number": 230, "state": "open", "draft": False,
            "merged": False, "head": {"sha": SHA, "ref": "fix/230",
            "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"full_name": REPO}}}
    run = {"id": 77, "head_sha": SHA, "head_branch": "fix/230",
           "event": "pull_request", "status": "completed",
           "conclusion": "success", "path": ".github/workflows/phase-certification.yml",
           "repository": {"full_name": REPO}, "created_at": RECENT,
           "updated_at": RECENT}
    jobs = [{"id": 88, "name": "Phase Certification / Full Validation",
             "run_id": 77, "status": "completed", "conclusion": "success",
             "steps": [{"name": name, "status": "completed", "conclusion": "success"}
                       for name in _REQUIRED_STEPS]}]
    checks = [{"name": "Public PR / Safe Validation", "head_sha": SHA,
               "status": "completed", "conclusion": "success",
               "app": {"slug": "github-actions"}, "completed_at": RECENT}]
    protected = {p: ("blob-1", "blob-1") for p in _PROTECTED_SOURCES}
    return {"repo": REPO, "pr_number": 230, "sha": SHA, "pull": pull,
            "run": run, "jobs": jobs, "checks": checks,
            "protected": protected, "now": NOW}


class FullEvidenceTests(unittest.TestCase):
    def test_live_exact_sha_full_success(self):
        result = validate_full_evidence(**fixtures())
        self.assertEqual((result.status, result.run_id, result.job_id),
                         ("FULL_VERIFIED_BY_GITHUB", 77, 88))

    def test_draft_or_closed_pr_rejected(self):
        for field, value in (("draft", True), ("state", "closed"), ("merged", True)):
            with self.subTest(field=field):
                obj = fixtures()
                obj["pull"][field] = value
                with self.assertRaises(GitHubEvidenceError):
                    validate_full_evidence(**obj)

    def test_wrong_head_and_wrong_base_rejected(self):
        for section, field, value in (("head", "sha", "b" * 40),
                                      ("base", "ref", "feature")):
            obj = fixtures()
            obj["pull"][section][field] = value
            with self.assertRaises(GitHubEvidenceError):
                validate_full_evidence(**obj)

    def test_other_repo_or_workflow_rejected(self):
        for path, value in (
                (("run", "repository", "full_name"), "someone/fork"),
                (("run", "path"), ".github/workflows/fake.yml"),
                (("run", "head_sha"), "b" * 40),
                (("run", "head_branch"), "different"),
                (("run", "event"), "push")):
            obj = fixtures()
            target = obj
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(GitHubEvidenceError):
                validate_full_evidence(**obj)

    def test_skipped_failed_or_missing_mandatory_step_rejected(self):
        for verdict in ("skipped", "failure", "cancelled", "neutral"):
            obj = fixtures()
            obj["jobs"][0]["steps"][-1]["conclusion"] = verdict
            with self.subTest(verdict=verdict), self.assertRaises(GitHubEvidenceError):
                validate_full_evidence(**obj)
        obj = fixtures()
        obj["jobs"][0]["steps"].pop()
        with self.assertRaises(GitHubEvidenceError):
            validate_full_evidence(**obj)

    def test_missing_or_wrong_safe_validation_rejected(self):
        for change in ("missing", "wrong_sha", "neutral", "unknown_app"):
            obj = fixtures()
            if change == "missing":
                obj["checks"] = []
            elif change == "wrong_sha":
                obj["checks"][0]["head_sha"] = "b" * 40
            elif change == "neutral":
                obj["checks"][0]["conclusion"] = "neutral"
            else:
                obj["checks"][0]["app"]["slug"] = "external"
            with self.subTest(change=change), self.assertRaises(GitHubEvidenceError):
                validate_full_evidence(**obj)

    def test_changed_root_of_trust_refuses_full_even_if_green(self):
        obj = fixtures()
        name = next(iter(obj["protected"]))
        obj["protected"][name] = ("candidate-blob", "main-blob")
        with self.assertRaises(GitHubEvidenceError):
            validate_full_evidence(**obj)

    def test_stale_and_future_proofs_refused(self):
        for when in (NOW - timedelta(days=8), NOW + timedelta(days=1)):
            obj = fixtures()
            obj["run"]["updated_at"] = when.isoformat()
            with self.subTest(when=when), self.assertRaises(GitHubEvidenceError):
                validate_full_evidence(**obj)

    def test_wrong_full_run_job_identity_refused(self):
        obj = fixtures()
        obj["jobs"][0]["run_id"] = 99
        with self.assertRaises(GitHubEvidenceError):
            validate_full_evidence(**obj)


class _MockGitHub:
    repo = REPO

    def __init__(self, fixture, *, incomplete=False):
        self.fixture = fixture
        self.incomplete = incomplete
        self.queried = []

    def get(self, route):
        self.queried.append(route)
        data = self.fixture
        if route == "pulls/230":
            return deepcopy(data["pull"])
        if route.startswith("contents/"):
            return {"sha": "blob-1"}
        if route.startswith("actions/workflows/"):
            return {"workflow_runs": [deepcopy(data["run"])],
                    "total_count": 101 if self.incomplete else 1}
        if route.startswith("commits/"):
            return {"check_runs": deepcopy(data["checks"]), "total_count": 1}
        if route.startswith("actions/runs/"):
            return {"jobs": deepcopy(data["jobs"]), "total_count": 1}
        raise AssertionError("Unexpected GitHub API route: " + route)


class ApiObservationTests(unittest.TestCase):
    def test_observe_live_records_every_required_gate(self):
        client = _MockGitHub(fixtures())
        result = observe_full_pr(client, 230, now=NOW)
        self.assertEqual(result.candidate_sha, SHA)
        self.assertEqual(sum(route.startswith("contents/") for route in client.queried),
                         2 * len(_PROTECTED_SOURCES))
        self.assertEqual(len([r for r in client.queried if r.startswith("actions/runs/")]), 1)

    def test_incomplete_page_is_not_accepted(self):
        client = _MockGitHub(fixtures(), incomplete=True)
        with self.assertRaisesRegex(GitHubEvidenceError, "paginated"):
            observe_full_pr(client, 230, now=NOW)

    def test_reject_wrong_candidate_before_network_calls(self):
        client = _MockGitHub(fixtures())
        client.fixture["pull"]["head"]["sha"] = "garbage"
        with self.assertRaises(GitHubEvidenceError):
            observe_full_pr(client, 230, now=NOW)
        self.assertEqual(client.queried, ["pulls/230"])


if __name__ == "__main__":
    unittest.main()
