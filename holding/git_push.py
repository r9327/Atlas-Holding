"""Exact-SHA, non-force Git push with durable ambiguity recovery."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Protocol

from .git_worktree import GitSafetyError, GitWorktrees
from .github_full_evidence import GitHubEvidenceError
from .github_mutation_gate import GitHubMutationIntent, prepare_github_mutation
from .state_store import StateStore, StoreError
from .sync_attempts import AttemptAction, AttemptStatus, OperationRecord, decide_attempt
from .sync_coordinator import CoordinationResult, MutationFailure


_SHA = re.compile(r"^[0-9a-f]{40}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
MAX_HEALTH_AGE_SECONDS = 60
MIN_RECOVERY_AGE_SECONDS = 30


@dataclass(frozen=True)
class GitPushState:
    repository: str
    branch: str
    candidate_sha: str
    remote_sha: str | None
    fast_forward_safe: bool


class PrivilegedGitPushControl(Protocol):
    def observe(self, repository: str, branch: str,
                candidate_sha: str) -> GitPushState: ...

    def push(self, intent: GitHubMutationIntent,
             idempotency_key: str) -> None: ...


class GitRemotePushControl:
    """Constrained Git adapter: explicit ref, immutable SHA, never force-pushes."""

    def __init__(self, git: GitWorktrees, mission_id: str):
        self.git = git
        self.mission_id = mission_id

    def _local(self, repository: str, branch: str, candidate_sha: str):
        if (repository != self.git.repository or not _BRANCH.fullmatch(branch)
                or branch in {"main", "master"} or branch.startswith("refs/")
                or ".." in branch or "//" in branch
                or not _SHA.fullmatch(candidate_sha)):
            raise GitSafetyError("Invalid push identity")
        self.git.inspect_canonical()
        tree = self.git.inspect(self.mission_id)
        if tree.branch != branch or tree.base_sha != candidate_sha:
            raise GitSafetyError("Worktree no longer matches push intent")
        if self.git._git(
                "status", "--porcelain", "--untracked-files=normal",
                cwd=tree.path):
            raise GitSafetyError("Refusing to push a dirty worktree")
        return tree

    def observe(self, repository: str, branch: str,
                candidate_sha: str) -> GitPushState:
        tree = self._local(repository, branch, candidate_sha)
        output = self.git._git(
            "ls-remote", "--heads", "origin", f"refs/heads/{branch}",
            cwd=tree.path, timeout=60,
        )
        lines = [line for line in output.splitlines() if line]
        if not lines:
            return GitPushState(repository, branch, candidate_sha, None, True)
        expected_ref = f"refs/heads/{branch}"
        if len(lines) != 1:
            raise GitSafetyError("Remote branch observation is ambiguous")
        fields = lines[0].split("\t")
        if (len(fields) != 2 or fields[1] != expected_ref
                or not _SHA.fullmatch(fields[0])):
            raise GitSafetyError("Remote branch observation is malformed")
        remote_sha = fields[0]
        if remote_sha == candidate_sha:
            return GitPushState(
                repository, branch, candidate_sha, remote_sha, True)
        self.git._git(
            "fetch", "--no-tags", "--no-write-fetch-head", "origin",
            expected_ref, cwd=tree.path, timeout=120,
        )
        self.git._git(
            "cat-file", "-e", f"{remote_sha}^{{commit}}", cwd=tree.path)
        try:
            self.git._git(
                "merge-base", "--is-ancestor", remote_sha, candidate_sha,
                cwd=tree.path,
            )
            safe = True
        except GitSafetyError:
            safe = False
        return GitPushState(
            repository, branch, candidate_sha, remote_sha, safe)

    def push(self, intent: GitHubMutationIntent,
             idempotency_key: str) -> None:
        if (not isinstance(intent, GitHubMutationIntent)
                or intent.operation != "github.push"
                or not isinstance(idempotency_key, str)
                or not re.fullmatch(r"[0-9a-f]{64}", idempotency_key)):
            raise GitSafetyError("Invalid immutable push request")
        tree = self._local(
            intent.repository, intent.branch, intent.candidate_sha)
        self.git._git(
            "push", "--porcelain", "origin",
            f"{intent.candidate_sha}:refs/heads/{intent.branch}",
            cwd=tree.path, timeout=300,
        )


def _records(rows: tuple[dict, ...]) -> tuple[OperationRecord, ...]:
    try:
        return tuple(OperationRecord(
            operation=row["operation"], repository=row["repo"],
            branch=row["branch"], candidate_sha=row["candidate_sha"],
            attempt=row["attempt"], status=AttemptStatus(row["status"]),
            retryable=bool(row["retryable"]),
        ) for row in rows)
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError("Malformed durable push journal") from exc


def _healthy(store: StateStore, now_epoch: int) -> bool:
    health = store.service_health("github")
    return bool(
        health is not None and health["status"] == "ONLINE"
        and type(health["observed_at"]) is int
        and health["observed_at"] <= now_epoch + 5
        and now_epoch - health["observed_at"] <= MAX_HEALTH_AGE_SECONDS
    )


def _validate_state(state: GitPushState, repository: str, branch: str,
                    candidate_sha: str) -> None:
    if (not isinstance(state, GitPushState)
            or state.repository != repository or state.branch != branch
            or state.candidate_sha != candidate_sha
            or (state.remote_sha is not None
                and not _SHA.fullmatch(state.remote_sha))
            or type(state.fast_forward_safe) is not bool):
        raise GitHubEvidenceError("Privileged Git observer returned invalid state")


def _state_bytes(state: GitPushState) -> bytes:
    return json.dumps({
        "repository": state.repository, "branch": state.branch,
        "candidate_sha": state.candidate_sha, "remote_sha": state.remote_sha,
        "fast_forward_safe": state.fast_forward_safe,
    }, sort_keys=True, separators=(",", ":")).encode("ascii")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise StoreError("Duplicate push recovery context key")
        result[key] = value
    return result


def _state_from_context(raw: bytes) -> GitPushState:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except StoreError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise StoreError("Invalid push recovery context") from exc
    if not isinstance(value, dict) or set(value) != {
            "repository", "branch", "candidate_sha", "remote_sha",
            "fast_forward_safe"}:
        raise StoreError("Invalid push recovery context")
    try:
        state = GitPushState(**value)
        _validate_state(state, state.repository, state.branch, state.candidate_sha)
    except (GitHubEvidenceError, TypeError) as exc:
        raise StoreError("Invalid push recovery context") from exc
    return state


def coordinate_git_push(
    store: StateStore, control: PrivilegedGitPushControl,
    raw_authorization: bytes, *, expected_receipt_sha256: str,
    expected_account_id: str, expected_policy_sha: str, repository: str,
    branch: str, candidate_sha: str, now_epoch: int, lease_owner: str,
) -> CoordinationResult:
    """Push only after a fresh remote observation proves fast-forward safety."""
    rows = store.sync_attempts(
        operation="github.push", repo=repository, branch=branch,
        candidate_sha=candidate_sha)
    decision = decide_attempt(
        _records(rows), operation="github.push", repository=repository,
        branch=branch, candidate_sha=candidate_sha)
    if decision.action in {
            AttemptAction.WAIT, AttemptAction.ALREADY_DONE, AttemptAction.BLOCKED}:
        return CoordinationResult(
            decision.action.value, decision.idempotency_key, None)
    if (type(now_epoch) is not int or now_epoch < 0
            or not isinstance(lease_owner, str) or not lease_owner):
        raise GitHubEvidenceError("Invalid trusted push request")
    if not _healthy(store, now_epoch):
        return CoordinationResult(
            "GITHUB_HEALTH_BLOCKED", decision.idempotency_key, None)
    intent = prepare_github_mutation(
        raw_authorization, expected_receipt_sha256=expected_receipt_sha256,
        repository=repository, operation="github.push", branch=branch,
        candidate_sha=candidate_sha, now_epoch=now_epoch,
        expected_account_id=expected_account_id,
        expected_policy_sha=expected_policy_sha)
    try:
        before = control.observe(repository, branch, candidate_sha)
    except Exception:
        raise GitHubEvidenceError("Git remote observation unavailable") from None
    _validate_state(before, repository, branch, candidate_sha)
    if not before.fast_forward_safe:
        return CoordinationResult(
            "REMOTE_DIVERGED", decision.idempotency_key, None)
    attempt = decision.next_attempt
    if type(attempt) is not int:
        raise StoreError("Push decision lacks an attempt number")
    context = _state_bytes(before)
    store.begin_sync_attempt(
        operation="github.push", repo=repository, branch=branch,
        candidate_sha=candidate_sha, attempt=attempt,
        idempotency_key=decision.idempotency_key, updated_epoch=now_epoch,
        lease_owner=lease_owner, recovery_context=context,
        recovery_context_sha256=hashlib.sha256(context).hexdigest())
    if before.remote_sha != candidate_sha:
        try:
            control.push(intent, decision.idempotency_key)
        except MutationFailure as exc:
            if not exc.outcome_known:
                return CoordinationResult(
                    "INSPECT_REQUIRED", decision.idempotency_key, attempt)
            retryable = exc.retryable and attempt < 3
            store.finish_sync_attempt(
                operation="github.push", repo=repository, branch=branch,
                candidate_sha=candidate_sha, attempt=attempt,
                idempotency_key=decision.idempotency_key,
                status="FAILED" if retryable else "BLOCKED",
                retryable=retryable, updated_epoch=now_epoch,
                lease_owner=lease_owner)
            return CoordinationResult(
                "RETRY" if retryable else "BLOCKED",
                decision.idempotency_key, attempt)
        except Exception:
            return CoordinationResult(
                "INSPECT_REQUIRED", decision.idempotency_key, attempt)
    try:
        after = control.observe(repository, branch, candidate_sha)
    except Exception:
        return CoordinationResult(
            "INSPECT_REQUIRED", decision.idempotency_key, attempt,
            mutation_performed=before.remote_sha != candidate_sha)
    _validate_state(after, repository, branch, candidate_sha)
    if after.remote_sha != candidate_sha:
        return CoordinationResult(
            "INSPECT_REQUIRED", decision.idempotency_key, attempt,
            mutation_performed=before.remote_sha != candidate_sha)
    store.finish_sync_attempt(
        operation="github.push", repo=repository, branch=branch,
        candidate_sha=candidate_sha, attempt=attempt,
        idempotency_key=decision.idempotency_key, status="SUCCEEDED",
        retryable=False, updated_epoch=now_epoch, lease_owner=lease_owner)
    return CoordinationResult(
        "SUCCEEDED", decision.idempotency_key, attempt,
        mutation_performed=before.remote_sha != candidate_sha)


def recover_pending_git_push(
    store: StateStore, control: PrivilegedGitPushControl, *, repository: str,
    branch: str, candidate_sha: str, now_epoch: int, lease_owner: str,
) -> CoordinationResult:
    """Reobserve an ambiguous push; never replay it during recovery."""
    rows = store.sync_attempts(
        operation="github.push", repo=repository, branch=branch,
        candidate_sha=candidate_sha)
    decision = decide_attempt(
        _records(rows), operation="github.push", repository=repository,
        branch=branch, candidate_sha=candidate_sha)
    if decision.action is not AttemptAction.WAIT or not rows:
        return CoordinationResult(
            "NO_PENDING_INSPECTION", decision.idempotency_key, None)
    attempt = rows[-1]["attempt"]
    context = store.sync_recovery_context(
        operation="github.push", repo=repository, branch=branch,
        candidate_sha=candidate_sha, attempt=attempt)
    if context is None:
        return CoordinationResult(
            "LEGACY_CONTEXT_BLOCKED", decision.idempotency_key, attempt)
    if (type(now_epoch) is not int or type(context["recorded_epoch"]) is not int
            or context["recorded_epoch"] > now_epoch + 5):
        raise StoreError("Invalid push recovery timestamp")
    if now_epoch - context["recorded_epoch"] < MIN_RECOVERY_AGE_SECONDS:
        return CoordinationResult("WAIT", decision.idempotency_key, attempt)
    if not _healthy(store, now_epoch):
        return CoordinationResult(
            "GITHUB_HEALTH_BLOCKED", decision.idempotency_key, attempt)
    before = _state_from_context(context["pre_state"])
    _validate_state(before, repository, branch, candidate_sha)
    try:
        current = control.observe(repository, branch, candidate_sha)
        _validate_state(current, repository, branch, candidate_sha)
    except Exception:
        return CoordinationResult(
            "INSPECT_REQUIRED", decision.idempotency_key, attempt)
    if current.remote_sha == candidate_sha:
        store.finish_sync_attempt(
            operation="github.push", repo=repository, branch=branch,
            candidate_sha=candidate_sha, attempt=attempt,
            idempotency_key=decision.idempotency_key, status="SUCCEEDED",
            retryable=False, updated_epoch=now_epoch, lease_owner=lease_owner)
        return CoordinationResult("SUCCEEDED", decision.idempotency_key, attempt)
    if current == before:
        retryable = attempt < 3
        store.finish_sync_attempt(
            operation="github.push", repo=repository, branch=branch,
            candidate_sha=candidate_sha, attempt=attempt,
            idempotency_key=decision.idempotency_key,
            status="FAILED" if retryable else "BLOCKED",
            retryable=retryable, updated_epoch=now_epoch,
            lease_owner=lease_owner)
        return CoordinationResult(
            "RETRY" if retryable else "BLOCKED",
            decision.idempotency_key, attempt)
    return CoordinationResult(
        "INSPECT_REQUIRED", decision.idempotency_key, attempt)
