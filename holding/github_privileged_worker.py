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
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from holding.github_broker_protocol import MAX_MESSAGE_BYTES, SCHEMA_VERSION
    from holding.github_evidence_cli import read_token_file
    from holding.github_full_evidence import GitHubEvidenceError
    from holding.github_mutation_gate import GitHubMutationIntent
    from holding.github_privileged_transport import GitHubPrivilegedTransport
    from holding.sync_coordinator import MutationFailure
else:
    from .github_broker_protocol import MAX_MESSAGE_BYTES, SCHEMA_VERSION
    from .github_evidence_cli import read_token_file
    from .github_full_evidence import GitHubEvidenceError
    from .github_mutation_gate import GitHubMutationIntent
    from .github_privileged_transport import GitHubPrivilegedTransport
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


def handle_request(raw: bytes, transport: GitHubPrivilegedTransport) -> bytes:
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
                or action not in {"observe_pr", "observe_full", "mutate"}
                or not isinstance(payload, dict)):
            raise ValueError("invalid request")
        expected_id = hashlib.sha256(_canonical({
            "schema_version": SCHEMA_VERSION,
            "action": action,
            "payload": payload,
        })).hexdigest()
        if request_id != expected_id:
            raise ValueError("request integrity")
        if action in {"observe_pr", "observe_full"}:
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
        else:
            if set(payload) != {"intent", "idempotency_key"}:
                raise ValueError("invalid mutation")
            intent_data = payload["intent"]
            if not isinstance(intent_data, dict) or set(intent_data) != {
                    "operation", "repository", "branch", "candidate_sha",
                    "pr_number", "authorization_sha256", "evidence_run_id",
                    "executes_action", "certifies_quality"}:
                raise ValueError("invalid intent")
            intent = GitHubMutationIntent(**intent_data)
            transport.mutate(intent, payload["idempotency_key"])
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
    except GitHubEvidenceError:
        return _error(request_id, MutationFailure(
            "TRANSIENT_UNAVAILABLE", retryable=True, outcome_known=True))
    except Exception:
        return _error(request_id, MutationFailure(
            "REJECTED" if action is None else "TRANSIENT_UNAVAILABLE",
            retryable=action is not None,
            outcome_known=action != "mutate",
        ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-github-broker")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args(argv)
    raw = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
    try:
        transport = GitHubPrivilegedTransport(
            args.repo, read_token_file(args.token_file))
        response = handle_request(raw, transport)
    except Exception:
        # Startup failures contain no request authority and never echo details.
        return 2
    sys.stdout.buffer.write(response)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
