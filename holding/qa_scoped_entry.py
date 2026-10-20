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
import signal
import threading

from .qa_dispatch import ScopedQaDispatcher

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
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mission", help="Run scoped QA for one pending mission")
    group.add_argument("--watch", action="store_true",
                       help="Process pending QA autonomously (one writer per SHA)")
    parser.add_argument("--once", action="store_true",
                        help="With --watch, process one polling iteration and exit")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args(argv)
    if any(not path.is_absolute() for path in (
            args.db, args.canonical_root, args.workspace, args.interpreter)):
        parser.error("All local filesystem paths must be absolute")
    if args.db.is_symlink() or not args.db.is_file():
        parser.error("Refusing a missing or linked mission database")
    if not 0.25 <= args.poll_seconds <= 3600:
        parser.error("Poll interval must be 0.25..3600 seconds")
    try:
        git = GitWorktrees(args.canonical_root, args.workspace, args.repository)
        git.inspect_canonical()
        runner = ScopedDoctorRunner(
            git, args.interpreter, timeout_seconds=args.timeout_seconds)
        with StateStore(args.db) as store:
            if not args.watch:
                result = runner.run_pending(store, args.mission)
                print(json.dumps(asdict(result), sort_keys=True, separators=(",", ":")))
                return 0 if result.status == "SCOPED_TESTS_PASS" else 1
            dispatcher = ScopedQaDispatcher(store, runner)
            stop = threading.Event()
            previous = {}
            if threading.current_thread() is threading.main_thread():
                for signum in (signal.SIGINT, signal.SIGTERM):
                    previous[signum] = signal.getsignal(signum)
                    signal.signal(signum, lambda _sig, _frame: stop.set())
            try:
                while not stop.is_set():
                    result = dispatcher.tick()
                    if args.once or result.action not in ("IDLE", "WAIT_FOR_GRANT"):
                        print(json.dumps(asdict(result), sort_keys=True,
                                         separators=(",", ":")), flush=True)
                    if args.once:
                        return 0 if result.action not in (
                            "RUNNER_BLOCKED", "SCOPED_TESTS_FAIL",
                            "SCOPED_PLAN_BLOCKED", "FULL_REQUIRED") else 1
                    stop.wait(args.poll_seconds)
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
            return 0
    except (GitSafetyError, ScopedQaError, StoreError, OSError, ValueError) as exc:
        parser.error(f"Scoped QA rejected: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
