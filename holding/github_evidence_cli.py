"""Read-only exact-SHA GitHub FULL evidence CLI for a privileged supervisor.

The CLI never issues a signature, grants permissions, or merges a PR. Signing
requires the separate OS-isolated issuer after the installer enforces ACLs.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat

from .github_full_evidence import (
    GitHubEvidenceClient, GitHubEvidenceError, observe_full_pr,
)

_MAX_TOKEN_BYTES = 4096


def read_token_file(path: Path) -> str:
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise GitHubEvidenceError("GitHub token must be a protected regular absolute file")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise GitHubEvidenceError("Token file must be unreadable by group and others")
    with path.open("rb") as stream:
        raw = stream.read(_MAX_TOKEN_BYTES + 1)
    if not 1 <= len(raw) <= _MAX_TOKEN_BYTES:
        raise GitHubEvidenceError("GitHub token is empty or oversized")
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeError as exc:
        raise GitHubEvidenceError("GitHub token has invalid encoding") from exc
    if not token or any(character.isspace() for character in token):
        raise GitHubEvidenceError("GitHub token contains invalid whitespace")
    return token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-github-certification")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        client = GitHubEvidenceClient(args.repo, read_token_file(args.token_file))
        record = observe_full_pr(client, args.pr)
        if record.candidate_sha != args.candidate_sha:
            raise GitHubEvidenceError("Local candidate no longer matches the GitHub PR")
    except (GitHubEvidenceError, OSError) as exc:
        # Do not leak an exception containing credential/authorization headers.
        parser.error(f"GitHub proof unavailable: {type(exc).__name__}")
    print(json.dumps(asdict(record), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
