"""Run one approved scoped QA check from a separately sandboxed process.

Advisory only. The installed supervisor must launch this CLI as a restricted
user without GitHub secrets, signing keys, or access to the canonical checkout
for writes. A Python subprocess is not an OS sandbox.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .git_worktree import GitWorktrees, GitSafetyError
from .qa_scoped import ScopedDoctorRunner, ScopedQaError
from .state_store import StateStore, StoreError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-qa-scoped")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--canonical-root", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--interpreter", required=True, type=Path)
    parser.add_argument("--mission", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args(argv)
    if any(not path.is_absolute() for path in (
            args.db, args.canonical_root, args.workspace, args.interpreter)):
        parser.error("All local filesystem paths must be absolute")
    if args.db.is_symlink() or not args.db.is_file():
        parser.error("Refusing a missing or linked mission database")
    try:
        git = GitWorktrees(args.canonical_root, args.workspace, args.repository)
        git.inspect_canonical()
        runner = ScopedDoctorRunner(
            git, args.interpreter, timeout_seconds=args.timeout_seconds)
        with StateStore(args.db) as store:
            result = runner.run_pending(store, args.mission)
    except (GitSafetyError, ScopedQaError, StoreError, OSError, ValueError) as exc:
        parser.error(f"Scoped QA rejected: {type(exc).__name__}: {exc}")
    print(json.dumps(asdict(result), sort_keys=True, separators=(",", ":")))
    # FULL_REQUIRED and failed scoped checks remain explicit non-zero outcomes.
    return 0 if result.status == "SCOPED_TESTS_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
