"""Deterministic scope checks for revocable, repository-bound grants.

This module never performs an operation and never treats authorization as
quality evidence.  A trusted store must supply the expected record digest.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any


MAX_GRANT_BYTES = 64 * 1024
MAX_GRANT_SECONDS = 366 * 24 * 60 * 60
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")

ALLOWED_OPERATIONS = frozenset({
    "repo.read", "roadmap.plan", "worktree.create", "repo.edit", "tests.run",
    "commit.create", "github.push", "github.pr_draft", "github.pr_ready",
    "github.ci_trigger", "github.merge",
})
FORBIDDEN_OPERATIONS = frozenset({
    "github.force_push", "repo.main_write", "permissions.change", "payment.make",
    "secrets.read", "release.publish", "backup.delete",
})
_BRANCH_OPERATIONS = frozenset({
    "worktree.create", "repo.edit", "tests.run", "commit.create", "github.push",
    "github.pr_draft", "github.pr_ready", "github.ci_trigger", "github.merge",
})
_SHA_OPERATIONS = frozenset({
    "github.push", "github.pr_draft", "github.pr_ready", "github.ci_trigger",
    "github.merge",
})


class StandingGrantError(ValueError):
    pass


@dataclass(frozen=True)
class AuthorizationDecision:
    status: str
    grant_id: str
    account_id: str
    repository: str
    operation: str
    branch: str | None
    candidate_sha: str | None
    grant_sha256: str
    executes_action: bool = False
    certifies_quality: bool = False


def _integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise StandingGrantError(f"Invalid {label}")
    return value


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise StandingGrantError(f"Invalid {label}")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise StandingGrantError("Standing grant contains duplicate JSON keys")
        result[key] = value
    return result


def authorize_operation(
    raw_grant: bytes,
    *,
    expected_record_sha256: str,
    expected_policy_sha: str,
    observed_account_id: str,
    repository: str,
    operation: str,
    now_epoch: int,
    branch: str | None = None,
    candidate_sha: str | None = None,
) -> AuthorizationDecision:
    """Authorize scope only; downstream safety and quality gates still apply."""
    if (not isinstance(raw_grant, bytes) or not 1 <= len(raw_grant) <= MAX_GRANT_BYTES
            or not isinstance(expected_record_sha256, str)
            or not _DIGEST.fullmatch(expected_record_sha256)
            or not isinstance(expected_policy_sha, str) or not _SHA.fullmatch(expected_policy_sha)
            or operation not in ALLOWED_OPERATIONS
            or operation in FORBIDDEN_OPERATIONS):
        raise StandingGrantError("Invalid trusted authorization request")
    now_epoch = _integer(now_epoch, "current time")
    digest = hashlib.sha256(raw_grant).hexdigest()
    if digest != expected_record_sha256:
        raise StandingGrantError("Standing grant integrity mismatch")
    try:
        payload = json.loads(raw_grant, object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as exc:
        raise StandingGrantError("Standing grant is not valid JSON") from exc
    required = {
        "schema_version", "grant_id", "account_id", "repository", "operations",
        "issued_at", "expires_at", "revoked_at", "policy_sha",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise StandingGrantError("Unexpected standing grant shape")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise StandingGrantError("Unsupported standing grant schema")
    grant_id = _identity(payload["grant_id"], "grant id")
    account_id = _identity(payload["account_id"], "account id")
    observed_account_id = _identity(observed_account_id, "observed account id")
    if account_id != observed_account_id:
        raise StandingGrantError("Standing grant belongs to another account")
    if (not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository)
            or payload["repository"] != repository):
        raise StandingGrantError("Standing grant belongs to another repository")
    if payload["policy_sha"] != expected_policy_sha:
        raise StandingGrantError("Standing grant uses a stale policy SHA")

    operations = payload["operations"]
    if (not isinstance(operations, list) or not operations
            or len(operations) > len(ALLOWED_OPERATIONS)
            or any(not isinstance(item, str) for item in operations)
            or len(set(operations)) != len(operations)
            or any(item not in ALLOWED_OPERATIONS or item in FORBIDDEN_OPERATIONS
                   for item in operations)):
        raise StandingGrantError("Standing grant contains invalid operations")
    if operation not in operations:
        raise StandingGrantError("Operation is outside the standing grant")

    issued_at = _integer(payload["issued_at"], "grant issue time")
    expires_at = _integer(payload["expires_at"], "grant expiry time")
    if not issued_at <= now_epoch < expires_at or expires_at - issued_at > MAX_GRANT_SECONDS:
        raise StandingGrantError("Standing grant is not currently valid")
    revoked_at = payload["revoked_at"]
    if revoked_at is not None:
        revoked_at = _integer(revoked_at, "grant revocation time")
        if revoked_at <= now_epoch:
            raise StandingGrantError("Standing grant has been revoked")

    if operation in _BRANCH_OPERATIONS:
        if (not isinstance(branch, str) or not _BRANCH.fullmatch(branch)
                or branch in {"main", "master"} or branch.startswith(("refs/heads/main", "refs/heads/master"))
                or ".." in branch or "//" in branch):
            raise StandingGrantError("Operation requires a safe dedicated branch")
    elif branch is not None:
        raise StandingGrantError("Unexpected branch for non-mutating operation")
    if operation in _SHA_OPERATIONS:
        if not isinstance(candidate_sha, str) or not _SHA.fullmatch(candidate_sha):
            raise StandingGrantError("GitHub operation requires an exact candidate SHA")
    elif candidate_sha is not None:
        raise StandingGrantError("Unexpected candidate SHA for local operation")

    return AuthorizationDecision(
        "AUTHORIZED_SCOPE_ONLY", grant_id, account_id, repository, operation,
        branch, candidate_sha, digest,
    )
