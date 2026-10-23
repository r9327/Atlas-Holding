"""Bridge authorization, durable state, and privileged GitHub mutations."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Protocol

from .github_full_evidence import FullEvidence, GitHubEvidenceError
from .github_mutation_gate import GitHubMutationIntent, prepare_github_mutation
from .state_store import StateStore, StoreError
from .sync_attempts import (
    AttemptAction,
    AttemptStatus,
    OperationRecord,
    decide_attempt,
)


MAX_HEALTH_AGE_SECONDS = 60
MIN_RECOVERY_AGE_SECONDS = 30


@dataclass(frozen=True)
class LiveGitHubState:
    repository: str
    pr_number: int
    head_branch: str
    head_sha: str
    state: str
    draft: bool
    complete: bool
    merged: bool = False
    ci_run_id: int | None = None
    ci_head_sha: str | None = None


@dataclass(frozen=True)
class CoordinationResult:
    status: str
    idempotency_key: str
    attempt: int | None
    mutation_performed: bool = False
    certifies_quality: bool = False


class MutationFailure(RuntimeError):
    """A sanitized transport result with explicit outcome certainty."""

    def __init__(self, code: str, *, retryable: bool, outcome_known: bool):
        if code not in {"TRANSIENT_UNAVAILABLE", "RATE_LIMITED", "REJECTED"}:
            raise ValueError("Unsupported mutation failure code")
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.outcome_known = outcome_known


class PrivilegedGitHubControl(Protocol):
    def observe_pr(self, repository: str, pr_number: int) -> LiveGitHubState: ...

    def observe_full(self, repository: str, pr_number: int) -> FullEvidence: ...

    def mutate(self, intent: GitHubMutationIntent, idempotency_key: str) -> None: ...


def _records(rows: tuple[dict, ...]) -> tuple[OperationRecord, ...]:
    try:
        return tuple(OperationRecord(
            operation=row["operation"], repository=row["repo"],
            branch=row["branch"], candidate_sha=row["candidate_sha"],
            attempt=row["attempt"], status=AttemptStatus(row["status"]),
            retryable=bool(row["retryable"]),
        ) for row in rows)
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError("Malformed durable synchronization journal") from exc


def _pr_state(observed: LiveGitHubState) -> dict:
    if not isinstance(observed, LiveGitHubState):
        raise GitHubEvidenceError("Privileged observer returned invalid PR state")
    return {
        "number": observed.pr_number,
        "draft": observed.draft,
        "state": observed.state,
        "head_sha": observed.head_sha,
        "head_branch": observed.head_branch,
        "complete": observed.complete,
    }


def _state_bytes(observed: LiveGitHubState) -> bytes:
    payload = {
        "repository": observed.repository,
        "pr_number": observed.pr_number,
        "head_branch": observed.head_branch,
        "head_sha": observed.head_sha,
        "state": observed.state,
        "draft": observed.draft,
        "complete": observed.complete,
        "merged": observed.merged,
        "ci_run_id": observed.ci_run_id,
        "ci_head_sha": observed.ci_head_sha,
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise StoreError("Duplicate synchronization recovery context key")
        result[key] = value
    return result


def _state_from_bytes(raw: bytes) -> LiveGitHubState:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except StoreError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise StoreError("Invalid synchronization recovery context") from exc
    keys = {
        "repository", "pr_number", "head_branch", "head_sha", "state",
        "draft", "complete", "merged", "ci_run_id", "ci_head_sha",
    }
    if (not isinstance(value, dict) or set(value) != keys
            or not isinstance(value["repository"], str)
            or type(value["pr_number"]) is not int or value["pr_number"] < 1
            or not isinstance(value["head_branch"], str)
            or not isinstance(value["head_sha"], str)
            or value["state"] not in {"open", "closed"}
            or type(value["draft"]) is not bool
            or type(value["complete"]) is not bool
            or type(value["merged"]) is not bool
            or (value["ci_run_id"] is not None
                and (type(value["ci_run_id"]) is not int or value["ci_run_id"] < 1))
            or (value["ci_head_sha"] is not None
                and not isinstance(value["ci_head_sha"], str))):
        raise StoreError("Invalid synchronization recovery context")
    return LiveGitHubState(**value)


def _same_identity(
    state: LiveGitHubState, *, repository: str, pr_number: int,
    branch: str, candidate_sha: str,
) -> bool:
    return (
        state.repository == repository
        and state.pr_number == pr_number
        and state.head_branch == branch
        and state.head_sha == candidate_sha
    )


def _postcondition(
    operation: str, before: LiveGitHubState, after: LiveGitHubState,
    *, repository: str, pr_number: int, branch: str, candidate_sha: str,
) -> bool:
    if not _same_identity(
            after, repository=repository, pr_number=pr_number,
            branch=branch, candidate_sha=candidate_sha):
        return False
    if operation == "github.pr_draft":
        return (after.state == "open" and after.draft is True
                and after.complete is True and not after.merged)
    if operation == "github.pr_ready":
        return (after.state == "open" and after.draft is False
                and after.complete is True and not after.merged)
    if operation == "github.ci_trigger":
        return (
            after.state == "open"
            and after.complete is True
            and type(after.ci_run_id) is int
            and after.ci_run_id > 0
            and after.ci_run_id != before.ci_run_id
            and after.ci_head_sha == candidate_sha
        )
    if operation == "github.merge":
        return after.state == "closed" and after.merged is True
    return False


def _github_healthy(store: StateStore, now_epoch: int) -> bool:
    health = store.service_health("github")
    return (
        health is not None
        and health["status"] == "ONLINE"
        and type(health["observed_at"]) is int
        and health["observed_at"] <= now_epoch + 5
        and now_epoch - health["observed_at"] <= MAX_HEALTH_AGE_SECONDS
    )


def coordinate_github_mutation(
    store: StateStore,
    control: PrivilegedGitHubControl,
    raw_authorization: bytes,
    *,
    expected_receipt_sha256: str,
    expected_account_id: str,
    expected_policy_sha: str,
    repository: str,
    operation: str,
    branch: str,
    candidate_sha: str,
    pr_number: int,
    now_epoch: int,
    lease_owner: str,
) -> CoordinationResult:
    """Run one journaled mutation or return a non-mutating durable decision."""
    if (type(pr_number) is not int or pr_number < 1
            or type(now_epoch) is not int or now_epoch < 0
            or not isinstance(lease_owner, str) or not lease_owner):
        raise GitHubEvidenceError("Invalid trusted synchronization request")
    rows = store.sync_attempts(
        operation=operation, repo=repository, branch=branch,
        candidate_sha=candidate_sha,
    )
    decision = decide_attempt(
        _records(rows), operation=operation, repository=repository,
        branch=branch, candidate_sha=candidate_sha,
    )
    if decision.action in {
            AttemptAction.WAIT, AttemptAction.ALREADY_DONE, AttemptAction.BLOCKED}:
        return CoordinationResult(
            decision.action.value, decision.idempotency_key, None)
    if operation == "github.push":
        return CoordinationResult(
            "REQUIRES_GIT_DIVERGENCE_PROOF", decision.idempotency_key, None)

    if not _github_healthy(store, now_epoch):
        return CoordinationResult("GITHUB_HEALTH_BLOCKED", decision.idempotency_key, None)

    try:
        before = control.observe_pr(repository, pr_number)
    except Exception:
        raise GitHubEvidenceError("GitHub PR observation unavailable") from None
    if not _same_identity(
            before, repository=repository, pr_number=pr_number,
            branch=branch, candidate_sha=candidate_sha):
        raise GitHubEvidenceError("GitHub PR moved before mutation preparation")
    try:
        full = control.observe_full(repository, pr_number) if operation == "github.merge" else None
    except Exception:
        raise GitHubEvidenceError("GitHub FULL observation unavailable") from None
    intent = prepare_github_mutation(
        raw_authorization,
        expected_receipt_sha256=expected_receipt_sha256,
        repository=repository,
        operation=operation,
        branch=branch,
        candidate_sha=candidate_sha,
        now_epoch=now_epoch,
        expected_account_id=expected_account_id,
        expected_policy_sha=expected_policy_sha,
        pr_state=_pr_state(before),
        full_evidence=full,
    )
    if intent.pr_number != pr_number:
        raise GitHubEvidenceError("Authorization resolved a different PR")
    attempt = decision.next_attempt
    if type(attempt) is not int:
        raise StoreError("Synchronization decision lacks an attempt number")
    store.begin_sync_attempt(
        operation=operation, repo=repository, branch=branch,
        candidate_sha=candidate_sha, attempt=attempt,
        idempotency_key=decision.idempotency_key,
        updated_epoch=now_epoch, lease_owner=lease_owner,
        recovery_context=(context := _state_bytes(before)),
        recovery_context_sha256=hashlib.sha256(context).hexdigest(),
    )
    try:
        control.mutate(intent, decision.idempotency_key)
    except MutationFailure as exc:
        if not exc.outcome_known:
            return CoordinationResult(
                "INSPECT_REQUIRED", decision.idempotency_key, attempt)
        retryable = exc.retryable and attempt < 3
        store.finish_sync_attempt(
            operation=operation, repo=repository, branch=branch,
            candidate_sha=candidate_sha, attempt=attempt,
            idempotency_key=decision.idempotency_key,
            status="FAILED" if retryable else "BLOCKED",
            retryable=retryable, updated_epoch=now_epoch,
            lease_owner=lease_owner,
        )
        return CoordinationResult(
            "RETRY" if retryable else "BLOCKED",
            decision.idempotency_key, attempt,
        )
    except Exception:
        raise GitHubEvidenceError("GitHub mutation outcome requires inspection") from None

    try:
        after = control.observe_pr(repository, pr_number)
    except Exception:
        return CoordinationResult(
            "INSPECT_REQUIRED", decision.idempotency_key, attempt,
            mutation_performed=True,
        )
    if not _postcondition(
            operation, before, after, repository=repository,
            pr_number=pr_number, branch=branch, candidate_sha=candidate_sha):
        return CoordinationResult(
            "INSPECT_REQUIRED", decision.idempotency_key, attempt,
            mutation_performed=True,
        )
    store.finish_sync_attempt(
        operation=operation, repo=repository, branch=branch,
        candidate_sha=candidate_sha, attempt=attempt,
        idempotency_key=decision.idempotency_key,
        status="SUCCEEDED", retryable=False, updated_epoch=now_epoch,
        lease_owner=lease_owner,
    )
    return CoordinationResult(
        "SUCCEEDED", decision.idempotency_key, attempt,
        mutation_performed=True,
    )


def recover_pending_github_mutation(
    store: StateStore,
    control: PrivilegedGitHubControl,
    *,
    repository: str,
    operation: str,
    branch: str,
    candidate_sha: str,
    pr_number: int,
    now_epoch: int,
    lease_owner: str,
) -> CoordinationResult:
    """Reobserve one ambiguous PENDING operation without replaying it."""
    if (type(pr_number) is not int or pr_number < 1
            or type(now_epoch) is not int or now_epoch < 0
            or not isinstance(lease_owner, str) or not lease_owner):
        raise GitHubEvidenceError("Invalid trusted recovery request")
    rows = store.sync_attempts(
        operation=operation, repo=repository, branch=branch,
        candidate_sha=candidate_sha,
    )
    decision = decide_attempt(
        _records(rows), operation=operation, repository=repository,
        branch=branch, candidate_sha=candidate_sha,
    )
    if decision.action is not AttemptAction.WAIT or not rows:
        return CoordinationResult(
            "NO_PENDING_INSPECTION", decision.idempotency_key, None)
    latest = rows[-1]
    attempt = latest["attempt"]
    context = store.sync_recovery_context(
        operation=operation, repo=repository, branch=branch,
        candidate_sha=candidate_sha, attempt=attempt,
    )
    if context is None:
        return CoordinationResult(
            "LEGACY_CONTEXT_BLOCKED", decision.idempotency_key, attempt)
    if type(context["recorded_epoch"]) is not int:
        raise StoreError("Invalid synchronization recovery timestamp")
    if context["recorded_epoch"] > now_epoch + 5:
        raise StoreError("Synchronization recovery timestamp is in the future")
    if now_epoch - context["recorded_epoch"] < MIN_RECOVERY_AGE_SECONDS:
        return CoordinationResult("WAIT", decision.idempotency_key, attempt)
    if not _github_healthy(store, now_epoch):
        return CoordinationResult(
            "GITHUB_HEALTH_BLOCKED", decision.idempotency_key, attempt)
    before = _state_from_bytes(context["pre_state"])
    if not _same_identity(
            before, repository=repository, pr_number=pr_number,
            branch=branch, candidate_sha=candidate_sha):
        raise StoreError("Synchronization recovery context identity mismatch")
    try:
        current = control.observe_pr(repository, pr_number)
    except Exception:
        return CoordinationResult(
            "INSPECT_REQUIRED", decision.idempotency_key, attempt)
    if not isinstance(current, LiveGitHubState):
        return CoordinationResult(
            "INSPECT_REQUIRED", decision.idempotency_key, attempt)
    if _postcondition(
            operation, before, current, repository=repository,
            pr_number=pr_number, branch=branch, candidate_sha=candidate_sha):
        store.finish_sync_attempt(
            operation=operation, repo=repository, branch=branch,
            candidate_sha=candidate_sha, attempt=attempt,
            idempotency_key=decision.idempotency_key,
            status="SUCCEEDED", retryable=False, updated_epoch=now_epoch,
            lease_owner=lease_owner,
        )
        return CoordinationResult(
            "SUCCEEDED", decision.idempotency_key, attempt)
    if operation in {"github.pr_draft", "github.pr_ready"} and current == before:
        retryable = attempt < 3
        store.finish_sync_attempt(
            operation=operation, repo=repository, branch=branch,
            candidate_sha=candidate_sha, attempt=attempt,
            idempotency_key=decision.idempotency_key,
            status="FAILED" if retryable else "BLOCKED",
            retryable=retryable, updated_epoch=now_epoch,
            lease_owner=lease_owner,
        )
        return CoordinationResult(
            "RETRY" if retryable else "BLOCKED",
            decision.idempotency_key, attempt,
        )
    return CoordinationResult(
        "INSPECT_REQUIRED", decision.idempotency_key, attempt)
