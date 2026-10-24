"""One-shot privileged GitHub broker entry point.

Deployment must run this worker under the credential-owning OS identity.  The
writer sends only bounded authorization-derived intents over stdin; the token
is read locally by this process from an ACL-protected file.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable


_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from holding.github_broker_protocol import MAX_MESSAGE_BYTES, SCHEMA_VERSION
    from holding.github_evidence_cli import read_token_file
    from holding.github_full_evidence import GitHubEvidenceError
    from holding.git_push import GitRemotePushControl
    from holding.git_worktree import GitWorktrees
    from holding.github_mutation_gate import GitHubMutationIntent
    from holding.github_privileged_transport import (
        GitHubCredentialRejected, GitHubPrivilegedTransport,
    )
    from holding.sync_coordinator import MutationFailure
else:
    from .github_broker_protocol import MAX_MESSAGE_BYTES, SCHEMA_VERSION
    from .github_evidence_cli import read_token_file
    from .github_full_evidence import GitHubEvidenceError
    from .git_push import GitRemotePushControl
    from .git_worktree import GitWorktrees
    from .github_mutation_gate import GitHubMutationIntent
    from .github_privileged_transport import (
        GitHubCredentialRejected, GitHubPrivilegedTransport,
    )
    from .sync_coordinator import MutationFailure


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def _error(request_id: str, failure: MutationFailure) -> bytes:
    return _canonical({
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "status": "ERROR",
        "code": failure.code,
        "retryable": failure.retryable,
        "outcome_known": failure.outcome_known,
    })


def _intent(value: object) -> GitHubMutationIntent:
    if not isinstance(value, dict) or set(value) != {
            "operation", "repository", "branch", "candidate_sha",
            "pr_number", "authorization_sha256", "evidence_run_id",
            "executes_action", "certifies_quality"}:
        raise ValueError("invalid intent")
    return GitHubMutationIntent(**value)


def handle_request(
    raw: bytes, transport: GitHubPrivilegedTransport, *,
    git_control_factory: Callable[[str], object] | None = None,
) -> bytes:
    """Validate one request, execute it once, and emit a secret-free response."""
    request_id = "0" * 64
    action = None
    try:
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_MESSAGE_BYTES:
            raise ValueError("invalid size")
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(value, dict) or set(value) != {
                "schema_version", "request_id", "action", "payload"}:
            raise ValueError("invalid shape")
        request_id = value["request_id"]
        action = value["action"]
        payload = value["payload"]
        if (value["schema_version"] != SCHEMA_VERSION
                or not isinstance(request_id, str) or len(request_id) != 64
                or any(character not in "0123456789abcdef" for character in request_id)
                or action not in {
                    "observe_account", "observe_pr", "observe_full",
                    "observe_git", "mutate", "push_git"}
                or not isinstance(payload, dict)):
            raise ValueError("invalid request")
        expected_id = hashlib.sha256(_canonical({
            "schema_version": SCHEMA_VERSION,
            "action": action,
            "payload": payload,
        })).hexdigest()
        if request_id != expected_id:
            raise ValueError("request integrity")
        if action == "observe_account":
            if payload:
                raise ValueError("invalid account observation")
            response = {
                "schema_version": SCHEMA_VERSION, "request_id": request_id,
                "status": "OK",
                "result": {"account_id": transport.observe_account()},
            }
        elif action in {"observe_pr", "observe_full"}:
            if set(payload) != {"repository", "pr_number"}:
                raise ValueError("invalid observation")
            result = (
                transport.observe_pr(payload["repository"], payload["pr_number"])
                if action == "observe_pr"
                else transport.observe_full(payload["repository"], payload["pr_number"])
            )
            response = {
                "schema_version": SCHEMA_VERSION, "request_id": request_id,
                "status": "OK", "result": asdict(result),
            }
        elif action == "observe_git":
            if set(payload) != {
                    "source_id", "repository", "branch", "candidate_sha"}:
                raise ValueError("invalid Git observation")
            if git_control_factory is None:
                raise ValueError("Git broker unavailable")
            if (not isinstance(payload["source_id"], str)
                    or not _SOURCE_ID.fullmatch(payload["source_id"])):
                raise ValueError("invalid Git source")
            result = git_control_factory(payload["source_id"]).observe(
                payload["repository"], payload["branch"],
                payload["candidate_sha"])
            response = {
                "schema_version": SCHEMA_VERSION, "request_id": request_id,
                "status": "OK", "result": asdict(result),
            }
        elif action == "mutate":
            if set(payload) != {"intent", "idempotency_key"}:
                raise ValueError("invalid mutation")
            intent = _intent(payload["intent"])
            transport.mutate(intent, payload["idempotency_key"])
            response = {
                "schema_version": SCHEMA_VERSION, "request_id": request_id,
                "status": "OK", "result": {},
            }
        else:
            if set(payload) != {"source_id", "intent", "idempotency_key"}:
                raise ValueError("invalid Git mutation")
            if git_control_factory is None:
                raise MutationFailure(
                    "REJECTED", retryable=False, outcome_known=True)
            if (not isinstance(payload["source_id"], str)
                    or not _SOURCE_ID.fullmatch(payload["source_id"])):
                raise ValueError("invalid Git source")
            intent = _intent(payload["intent"])
            if intent.operation != "github.push":
                raise ValueError("invalid Git push intent")
            git_control_factory(payload["source_id"]).push(
                intent, payload["idempotency_key"])
            response = {
                "schema_version": SCHEMA_VERSION, "request_id": request_id,
                "status": "OK", "result": {},
            }
        encoded = _canonical(response)
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise ValueError("oversized response")
        return encoded
    except MutationFailure as exc:
        return _error(request_id, exc)
    except GitHubCredentialRejected:
        return _error(request_id, MutationFailure(
            "REJECTED", retryable=False, outcome_known=True))
    except GitHubEvidenceError:
        return _error(request_id, MutationFailure(
            "TRANSIENT_UNAVAILABLE", retryable=True, outcome_known=True))
    except Exception:
        mutating = action in {"mutate", "push_git"}
        return _error(request_id, MutationFailure(
            "REJECTED" if action is None else "TRANSIENT_UNAVAILABLE",
            retryable=action is not None,
            outcome_known=not mutating,
        ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-github-broker")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--canonical-root", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--git-executable", type=Path)
    args = parser.parse_args(argv)
    raw = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
    try:
        transport = GitHubPrivilegedTransport(
            args.repo, read_token_file(args.token_file))
        git_values = (args.canonical_root, args.workspace, args.git_executable)
        if any(value is not None for value in git_values):
            if not all(value is not None for value in git_values):
                return 2
            git = GitWorktrees(
                args.canonical_root, args.workspace, args.repo,
                git_executable=args.git_executable)
            git.inspect_canonical()
            git_factory = lambda source_id: GitRemotePushControl(git, source_id)
        else:
            git_factory = None
        response = handle_request(
            raw, transport, git_control_factory=git_factory)
    except Exception:
        # Startup failures contain no request authority and never echo details.
        return 2
    sys.stdout.buffer.write(response)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
