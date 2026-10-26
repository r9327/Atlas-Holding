"""Exact-SHA idempotency decisions for deferred external operations."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Iterable

from .github_full_evidence import GitHubEvidenceError


MAX_ATTEMPTS = 3
MAX_JOURNAL_RECORDS = 10_000
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_OPERATIONS = frozenset({
    "github.push", "github.pr_draft", "github.pr_ready",
    "github.ci_trigger", "github.merge",
})


class AttemptStatus(str, Enum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class AttemptAction(str, Enum):
    START = "START"
    WAIT = "WAIT"
    ALREADY_DONE = "ALREADY_DONE"
    RETRY = "RETRY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class OperationRecord:
    operation: str
    repository: str
    branch: str
    candidate_sha: str
    attempt: int
    status: AttemptStatus
    retryable: bool


@dataclass(frozen=True)
class AttemptDecision:
    action: AttemptAction
    idempotency_key: str
    next_attempt: int | None
    executes_action: bool = False


def _key(operation: str, repository: str, branch: str, candidate_sha: str) -> str:
    raw = json.dumps(
        [operation, repository, branch, candidate_sha],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _validate_identity(
    operation: str, repository: str, branch: str, candidate_sha: str,
) -> None:
    if (operation not in _OPERATIONS
            or not isinstance(repository, str) or not _REPO.fullmatch(repository)
            or not isinstance(branch, str) or not _BRANCH.fullmatch(branch)
            or branch in {"main", "master"} or branch.startswith("refs/")
            or ".." in branch or "//" in branch
            or not isinstance(candidate_sha, str) or not _SHA.fullmatch(candidate_sha)):
        raise GitHubEvidenceError("Invalid synchronization identity")


def decide_attempt(
    records: Iterable[OperationRecord],
    *,
    operation: str,
    repository: str,
    branch: str,
    candidate_sha: str,
) -> AttemptDecision:
    """Decide whether one exact operation may start; never performs it."""
    _validate_identity(operation, repository, branch, candidate_sha)
    if isinstance(records, (str, bytes, dict)):
        raise GitHubEvidenceError("Invalid synchronization journal")
    try:
        rows = []
        for index, row in enumerate(records, start=1):
            if index > MAX_JOURNAL_RECORDS:
                raise GitHubEvidenceError("Synchronization journal exceeds limit")
            rows.append(row)
    except TypeError as exc:
        raise GitHubEvidenceError("Invalid synchronization journal") from exc

    matching: list[OperationRecord] = []
    seen_attempts: set[int] = set()
    for row in rows:
        if (not isinstance(row, OperationRecord)
                or row.operation not in _OPERATIONS
                or not isinstance(row.status, AttemptStatus)
                or type(row.attempt) is not int or row.attempt < 1
                or type(row.retryable) is not bool):
            raise GitHubEvidenceError("Malformed synchronization record")
        _validate_identity(
            row.operation, row.repository, row.branch, row.candidate_sha)
        if (row.operation, row.repository, row.branch, row.candidate_sha) != (
                operation, repository, branch, candidate_sha):
            continue
        if row.attempt in seen_attempts:
            raise GitHubEvidenceError("Conflicting synchronization attempt")
        seen_attempts.add(row.attempt)
        matching.append(row)

    identity_key = _key(operation, repository, branch, candidate_sha)
    if not matching:
        return AttemptDecision(AttemptAction.START, identity_key, 1)
    matching.sort(key=lambda row: row.attempt)
    if [row.attempt for row in matching] != list(range(1, len(matching) + 1)):
        raise GitHubEvidenceError("Synchronization attempt sequence has gaps")
    if any(
            row.status is not AttemptStatus.FAILED or not row.retryable
            for row in matching[:-1]):
        raise GitHubEvidenceError("Synchronization record follows a terminal attempt")
    latest = matching[-1]
    if latest.status is AttemptStatus.SUCCEEDED:
        return AttemptDecision(AttemptAction.ALREADY_DONE, identity_key, None)
    if latest.status is AttemptStatus.PENDING:
        return AttemptDecision(AttemptAction.WAIT, identity_key, None)
    if (latest.status is AttemptStatus.BLOCKED or not latest.retryable
            or latest.attempt >= MAX_ATTEMPTS):
        return AttemptDecision(AttemptAction.BLOCKED, identity_key, None)
    return AttemptDecision(AttemptAction.RETRY, identity_key, latest.attempt + 1)
