"""Independent QA receipt consumer entrypoint (not an evidence signer).

The verifier MUST run under a supervisor OS identity distinct from the coding
agent, with the HMAC key and evidence directory inaccessible to that agent.
The Windows installer must enforce and test those ACLs before enabling it.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import stat
import threading

from .proofs import ProofVerifier
from .qa_inbox import QaInbox
from .state_store import StateStore

MAX_KEY_BYTES = 4096


class QaBootstrapError(RuntimeError):
    pass


def load_verifier_key(key_file: Path) -> bytes:
    """Fail closed on shared POSIX keys; Windows needs installer-enforced ACLs."""
    if not key_file.is_absolute() or key_file.is_symlink() or not key_file.is_file():
        raise QaBootstrapError("Verifier key must be a regular absolute, non-symlinked file")
    if os.name != "nt" and stat.S_IMODE(key_file.stat().st_mode) & 0o077:
        raise QaBootstrapError("Verifier key must not be readable by group or others")
    with key_file.open("rb") as stream:
        key = stream.read(MAX_KEY_BYTES + 1)
    if not 32 <= len(key) <= MAX_KEY_BYTES:
        raise QaBootstrapError("Verifier key must contain 32..4096 bytes")
    return key


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-qa-inbox")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not 0.1 <= args.poll_seconds <= 3600:
        parser.error("--poll-seconds must be between 0.1 and 3600")
    if (not args.db.is_absolute() or args.db.is_symlink() or
            not args.db.is_file()):
        parser.error("--db must be an existing regular absolute database")
    if (not args.evidence_dir.is_absolute() or args.evidence_dir.is_symlink() or
            not args.evidence_dir.is_dir()):
        parser.error("--evidence-dir must be an existing trusted absolute directory")
    try:
        key = load_verifier_key(args.key_file)
    except (QaBootstrapError, OSError) as exc:
        parser.error(f"Invalid trusted QA key: {exc}")
    verifier = ProofVerifier(args.evidence_dir, key)
    stop = threading.Event()

    def shutdown(_signum, _frame):
        stop.set()

    old_handlers = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, shutdown)
    try:
        with StateStore(args.db) as store:
            inbox = QaInbox(store, verifier)
            while not stop.is_set():
                summary = inbox.poll()
                if args.once or summary.passed or summary.failed:
                    print(json.dumps(asdict(summary), sort_keys=True, separators=(",", ":")),
                          flush=True)
                if args.once:
                    return 0
                stop.wait(args.poll_seconds)
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
