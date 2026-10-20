"""Prepare GitHub mutation intents without performing any network mutation.

Authorization is necessary but never sufficient: Ready/CI/merge additionally
require current exact-head GitHub state.  The eventual privileged client must
consume these immutable intents and recheck the head immediately before use.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from .github_full_evidence import FullEvidence, GitHubEvidenceError
from .git_sync_gate import GitPushState, PushDisposition, validate_push_state


MAX_RECEIPT_BYTES = 64 * 1024
MAX_DECISION_AGE_SECONDS = 300
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_OPERATIONS = frozenset({
    "github.push", "github.pr_draft", "github.pr_ready",
    "github.ci_trigger", "github.merge",
})


@dataclass(frozen=True)
class GitHubMutationIntent:
    operation: str
    repository: str
    branch: str
    candidate_sha: str
    pr_number: int | None
    authorization_sha256: str
    evidence_run_id: int | None
    push_disposition: PushDisposition | None = None
    remote_main_head: str | None = None
    remote_branch_head: str | None = None
    executes_action: bool = False
    certifies_quality: bool = False


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise GitHubEvidenceError(f"Invalid authorization {label}")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise GitHubEvidenceError("Authorization receipt contains duplicate JSON keys")
        result[key] = value
    return result


def _receipt(
    raw: bytes, *, expected_receipt_sha256: str, expected_repository: str,
    expected_operation: str, expected_branch: str, expected_sha: str,
    expected_account_id: str, expected_policy_sha: str, now_epoch: int,
) -> str:
    if (not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_RECEIPT_BYTES
            or not isinstance(expected_receipt_sha256, str)
            or not _DIGEST.fullmatch(expected_receipt_sha256)
            or not isinstance(expected_repository, str) or not _REPO.fullmatch(expected_repository)
            or expected_operation not in _OPERATIONS
            or not isinstance(expected_branch, str) or not _BRANCH.fullmatch(expected_branch)
            or expected_branch in {"main", "master"} or expected_branch.startswith("refs/")
            or ".." in expected_branch or "//" in expected_branch
            or not isinstance(expected_sha, str) or not _SHA.fullmatch(expected_sha)
            or not isinstance(expected_policy_sha, str) or not _SHA.fullmatch(expected_policy_sha)
            or type(now_epoch) is not int or now_epoch < 0):
        raise GitHubEvidenceError("Invalid trusted GitHub mutation expectation")
    expected_account_id = _identity(expected_account_id, "expected account id")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_receipt_sha256:
        raise GitHubEvidenceError("Authorization receipt integrity mismatch")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except GitHubEvidenceError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise GitHubEvidenceError("Authorization receipt is not valid JSON") from exc
    keys = {
        "schema_version", "status", "grant_id", "account_id", "repository",
        "operation", "branch", "candidate_sha", "grant_sha256", "policy_sha",
        "decision_epoch", "expires_at",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise GitHubEvidenceError("Unexpected authorization receipt shape")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise GitHubEvidenceError("Unsupported authorization receipt schema")
    _identity(value["grant_id"], "grant id")
    account_id = _identity(value["account_id"], "account id")
    if (value["status"] != "AUTHORIZED_SCOPE_ONLY"
            or account_id != expected_account_id
            or value["repository"] != expected_repository
            or value["operation"] != expected_operation
            or value["branch"] != expected_branch
            or value["candidate_sha"] != expected_sha):
        raise GitHubEvidenceError("Authorization receipt scope mismatch")
    if (not isinstance(value["grant_sha256"], str)
            or not _DIGEST.fullmatch(value["grant_sha256"])
            or value["policy_sha"] != expected_policy_sha):
        raise GitHubEvidenceError("Authorization provenance is missing")
    decision_epoch, expires_at = value["decision_epoch"], value["expires_at"]
    if (type(decision_epoch) is not int or type(expires_at) is not int
            or decision_epoch > now_epoch + 5
            or now_epoch - decision_epoch > MAX_DECISION_AGE_SECONDS
            or expires_at <= now_epoch):
        raise GitHubEvidenceError("Authorization receipt is stale or expired")
    return digest


def prepare_github_mutation(
    raw_authorization: bytes,
    *,
    expected_receipt_sha256: str,
    repository: str,
    operation: str,
    branch: str,
    candidate_sha: str,
    now_epoch: int,
    expected_account_id: str,
    expected_policy_sha: str,
    pr_state: dict[str, Any] | None = None,
    full_evidence: FullEvidence | None = None,
    push_state: GitPushState | None = None,
    legacy_privileged_push_observer: bool = False,
) -> GitHubMutationIntent:
    """Validate prerequisites and return a non-executing immutable intent."""
    authorization_digest = _receipt(
        raw_authorization, expected_receipt_sha256=expected_receipt_sha256,
        expected_repository=repository, expected_operation=operation,
        expected_branch=branch, expected_sha=candidate_sha,
        expected_account_id=expected_account_id,
        expected_policy_sha=expected_policy_sha, now_epoch=now_epoch,
    )
    pr_number = None
    run_id = None
    push_disposition = None
    remote_main_head = None
    remote_branch_head = None
    if operation == "github.push":
        if pr_state is not None or full_evidence is not None:
            raise GitHubEvidenceError("Unexpected GitHub evidence for this operation")
        if type(legacy_privileged_push_observer) is not bool:
            raise GitHubEvidenceError("Invalid legacy push observer declaration")
        if push_state is None:
            if not legacy_privileged_push_observer:
                raise GitHubEvidenceError("Fresh Git topology is required for push")
        else:
            if legacy_privileged_push_observer:
                raise GitHubEvidenceError("Conflicting Git topology authorities")
            push_disposition = validate_push_state(
                push_state, repository=repository, branch=branch,
                candidate_sha=candidate_sha, now_epoch=now_epoch,
            )
            remote_main_head = push_state.remote_main_head
            remote_branch_head = push_state.remote_branch_head
    elif operation in {"github.pr_draft", "github.pr_ready", "github.ci_trigger"}:
        if not isinstance(pr_state, dict) or set(pr_state) != {
                "number", "draft", "state", "head_sha", "head_branch", "complete"}:
            raise GitHubEvidenceError("Current PR state is required")
        if (type(pr_state["number"]) is not int or pr_state["number"] < 1
                or pr_state["state"] != "open" or pr_state["head_sha"] != candidate_sha
                or pr_state["head_branch"] != branch or pr_state["complete"] is not True):
            raise GitHubEvidenceError("PR is incomplete or no longer at the authorized head")
        expected_draft = operation == "github.pr_ready"
        if pr_state["draft"] is not expected_draft:
            raise GitHubEvidenceError("PR draft state does not match requested operation")
        pr_number = pr_state["number"]
    elif operation == "github.merge":
        if (not isinstance(full_evidence, FullEvidence)
                or full_evidence.repository != repository
                or full_evidence.candidate_sha != candidate_sha
                or full_evidence.status != "FULL_VERIFIED_BY_GITHUB"
                or type(full_evidence.pr_number) is not int
                or full_evidence.pr_number < 1
                or type(full_evidence.run_id) is not int
                or full_evidence.run_id < 1
                or not isinstance(pr_state, dict)
                or set(pr_state) != {
                    "number", "draft", "state", "head_sha", "head_branch", "complete"
                }
                or pr_state["number"] != full_evidence.pr_number
                or pr_state["draft"] is not False
                or pr_state["state"] != "open"
                or pr_state["head_sha"] != candidate_sha
                or pr_state["head_branch"] != branch
                or pr_state["complete"] is not True):
            raise GitHubEvidenceError("Merge lacks exact-head FULL GitHub evidence")
        pr_number = full_evidence.pr_number
        run_id = full_evidence.run_id
    elif (pr_state is not None or full_evidence is not None or push_state is not None
          or legacy_privileged_push_observer is not False):
        raise GitHubEvidenceError("Unexpected GitHub evidence for this operation")
    return GitHubMutationIntent(
        operation, repository, branch, candidate_sha, pr_number,
        authorization_digest, run_id, push_disposition,
        remote_main_head, remote_branch_head,
    )
