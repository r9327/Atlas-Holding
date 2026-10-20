"""Read-only GitHub FULL certification evidence, checked against the live API.

This module is not executed by any coding agent. It never dispatches a workflow,
modifies PRs, changes branch protection, or trusts model-authored receipts.
A successful decision is a prerequisite for a separately privileged signer;
the authenticated observation itself does NOT mark a Holding mission DONE.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any
from urllib import request

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[a-f0-9]{40}$")
_WORKFLOW = ".github/workflows/phase-certification.yml"
_FULL_JOB = "Phase Certification / Full Validation"
_REQUIRED_STEPS = (
    "Verify exact certification checkout",
    "Materialize certification LFS fixtures",
    "Materialize local Dofus catalog",
    "Run full Atlas phase certification gate",
    "Enforce phase certification verdict",
)
_PROTECTED_SOURCES = (
    ".github/workflows/phase-certification.yml",
    "tools/ci_required_certification.py",
    "tools/doctor_certification.py",
)
_MAX_RESPONSE = 2 * 1024 * 1024


class GitHubEvidenceError(ValueError):
    """Stable machine-readable failure plus a human-facing explanation."""

    def __init__(self, message: str, *, code: str = "GITHUB_EVIDENCE_INVALID"):
        super().__init__(message)
        self.code = code


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubEvidenceClient:
    """Allowlisted, bounded GitHub HTTPS GETs using a separately held token."""

    def __init__(self, repo: str, token: str):
        if not isinstance(repo, str) or not _REPO.fullmatch(repo):
            raise GitHubEvidenceError("Expected exact owner/repository")
        if not isinstance(token, str) or not token or any(c in token for c in "\r\n"):
            raise GitHubEvidenceError("Trusted GitHub API credential is required")
        self.repo = repo
        self._token = token
        self._opener = request.build_opener(
            request.ProxyHandler({}), _NoRedirect())

    def get(self, route: str) -> dict[str, Any]:
        if (not isinstance(route, str) or not route
                or route.startswith("/") or "://" in route
                or any(part in ("", ".", "..") for part in route.split("/"))
                or "\\" in route):
            raise GitHubEvidenceError("Unsafe GitHub API route")
        url = "https://api.github.com/repos/" + self.repo + "/" + route
        req = request.Request(url, headers={
            "Authorization": "Bearer " + self._token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "atlas-holding-independent-verifier",
        }, method="GET")
        try:
            with self._opener.open(req, timeout=15) as reply:
                if reply.status != 200:
                    raise GitHubEvidenceError("GitHub API returned non-200 status")
                raw = reply.read(_MAX_RESPONSE + 1)
        except (OSError, ValueError) as exc:
            raise GitHubEvidenceError("GitHub evidence unavailable") from exc
        if len(raw) > _MAX_RESPONSE:
            raise GitHubEvidenceError("GitHub evidence exceeds 2 MiB")
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise GitHubEvidenceError("GitHub response is not valid JSON") from exc
        if not isinstance(data, dict):
            raise GitHubEvidenceError("GitHub response must be a JSON object")
        return data


def _check_datetime(value: Any, now: datetime) -> None:
    if not isinstance(value, str):
        raise GitHubEvidenceError("Missing dated GitHub evidence")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GitHubEvidenceError("Malformed evidence timestamp") from exc
    if parsed.utcoffset() is None:
        raise GitHubEvidenceError("Unzoned evidence timestamp")
    parsed = parsed.astimezone(timezone.utc)
    if parsed > now + timedelta(minutes=5) or now - parsed > timedelta(days=7):
        raise GitHubEvidenceError("Future or stale evidence timestamp")


@dataclass(frozen=True)
class FullEvidence:
    repository: str
    pr_number: int
    candidate_sha: str
    run_id: int
    job_id: int
    status: str = "FULL_VERIFIED_BY_GITHUB"


def validate_full_evidence(
    *, repo: str, pr_number: int, sha: str,
    pull: dict[str, Any], run: dict[str, Any], jobs: list[dict[str, Any]],
    checks: list[dict[str, Any]], protected: dict[str, tuple[str, str]],
    now: datetime,
) -> FullEvidence:
    """Reject spoofed, stale, partial, skipped or cross-SHA evidence."""
    if (not _REPO.fullmatch(repo) or type(pr_number) is not int or pr_number < 1
            or not _SHA.fullmatch(sha) or now.tzinfo is None):
        raise GitHubEvidenceError("Invalid exact certification identity")
    if (pull.get("number") != pr_number or pull.get("state") != "open"
            or pull.get("draft") is not False or pull.get("merged") is True):
        raise GitHubEvidenceError("Expected a complete open non-draft PR")
    head, base = pull.get("head"), pull.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise GitHubEvidenceError("Missing PR head/base")
    if (head.get("sha") != sha or not isinstance(head.get("repo"), dict)
            or head["repo"].get("full_name") != repo
            or base.get("ref") != "main" or not isinstance(base.get("repo"), dict)
            or base["repo"].get("full_name") != repo
            or not isinstance(head.get("ref"), str)):
        raise GitHubEvidenceError("Wrong repository, PR base or head SHA")
    for name in _PROTECTED_SOURCES:
        pair = protected.get(name)
        if not isinstance(pair, tuple) or len(pair) != 2 or not pair[0] or pair[0] != pair[1]:
            raise GitHubEvidenceError("Certification trust boundary differs from main")
    if (run.get("head_sha") != sha or run.get("head_branch") != head["ref"]
            or run.get("event") not in ("pull_request", "workflow_dispatch")
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or not isinstance(run.get("repository"), dict)
            or run["repository"].get("full_name") != repo):
        raise GitHubEvidenceError("Incorrect or incomplete FULL workflow")
    path = run.get("path")
    if (not isinstance(path, str)
            or not (path == _WORKFLOW or path.startswith(_WORKFLOW + "@")
                    or "/" + _WORKFLOW + "@" in path)):
        raise GitHubEvidenceError("Not the protected Phase Certification workflow")
    _check_datetime(run.get("created_at"), now)
    _check_datetime(run.get("updated_at"), now)
    created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
    completed = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
    if completed < created:
        raise GitHubEvidenceError("Workflow completion predates its creation")
    run_id = run.get("id")
    if type(run_id) is not int or run_id < 1:
        raise GitHubEvidenceError("Invalid workflow run ID")
    full = [j for j in jobs if isinstance(j, dict) and j.get("name") == _FULL_JOB]
    if len(full) != 1:
        raise GitHubEvidenceError("Missing or ambiguous Windows FULL job")
    job = full[0]
    job_id = job.get("id")
    if (type(job_id) is not int or job_id < 1 or job.get("run_id") != run_id
            or job.get("status") != "completed" or job.get("conclusion") != "success"):
        raise GitHubEvidenceError("Windows FULL job incomplete")
    steps = job.get("steps")
    if not isinstance(steps, list):
        raise GitHubEvidenceError("Missing certified FULL steps")
    for required in _REQUIRED_STEPS:
        matching = [s for s in steps if isinstance(s, dict) and s.get("name") == required]
        if (len(matching) != 1 or matching[0].get("status") != "completed"
                or matching[0].get("conclusion") != "success"):
            raise GitHubEvidenceError("Required FULL step missing, failed or skipped")
    # Safe Validation is a separate mandatory check, not equivalent to FULL.
    safe = [c for c in checks if isinstance(c, dict) and
            c.get("name") == "Public PR / Safe Validation" and
            c.get("head_sha") == sha and
            c.get("status") == "completed" and
            c.get("conclusion") == "success" and
            isinstance(c.get("app"), dict) and
            c["app"].get("slug") == "github-actions"]
    if len(safe) != 1:
        raise GitHubEvidenceError("No unique successful Public PR / Safe Validation")
    _check_datetime(safe[0].get("completed_at"), now)
    return FullEvidence(repo, pr_number, sha, run_id, job_id)


def observe_full_pr(client: GitHubEvidenceClient, pr_number: int, *,
                    now: datetime | None = None) -> FullEvidence:
    """Fetch current upstream records: no PR-authored artifact is an authority."""
    if type(pr_number) is not int or pr_number < 1:
        raise GitHubEvidenceError("Invalid PR number")
    now = now or datetime.now(timezone.utc)
    pull = client.get(f"pulls/{pr_number}")
    head = pull.get("head")
    sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise GitHubEvidenceError("No immutable PR head SHA")
    protected = {}
    for source in _PROTECTED_SOURCES:
        # Candidate cannot overwrite the verification code/policy it is graded by.
        candidate = client.get("contents/" + source + "?ref=" + sha)
        baseline = client.get("contents/" + source + "?ref=main")
        protected[source] = (candidate.get("sha"), baseline.get("sha"))
    runs = client.get("actions/workflows/phase-certification.yml/runs"
                      "?head_sha=" + sha + "&per_page=100")
    rows = runs.get("workflow_runs")
    if (type(runs.get("total_count")) is not int or runs["total_count"] > 100
            or not isinstance(rows, list)):
        raise GitHubEvidenceError("Incomplete paginated FULL run inventory")
    checks = client.get(f"commits/{sha}/check-runs?per_page=100")
    if (type(checks.get("total_count")) is not int or checks["total_count"] > 100
            or not isinstance(checks.get("check_runs"), list)):
        raise GitHubEvidenceError("Incomplete Safe Validation check inventory")
    for run in rows:
        if (not isinstance(run, dict) or run.get("head_sha") != sha or
                run.get("status") != "completed" or run.get("conclusion") != "success"):
            continue
        run_id = run.get("id")
        if type(run_id) is not int or run_id < 1:
            continue
        jobs = client.get(f"actions/runs/{run_id}/jobs?per_page=100")
        if (type(jobs.get("total_count")) is not int or jobs["total_count"] > 100
                or not isinstance(jobs.get("jobs"), list)):
            raise GitHubEvidenceError("Incomplete FULL job inventory")
        try:
            return validate_full_evidence(
                repo=client.repo, pr_number=pr_number, sha=sha,
                pull=pull, run=run, jobs=jobs["jobs"],
                checks=checks["check_runs"], protected=protected, now=now)
        except GitHubEvidenceError:
            continue
    raise GitHubEvidenceError("No fresh exact-HEAD FULL and Safe Validation evidence")
