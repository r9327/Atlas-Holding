"""Trusted, bounded consumer of independently signed exact-SHA QA receipts.

Runs in a separate verifier process/account from the code-writing worker.
This process only ACCEPTS signed receipts; it never runs tests, issues PASS
verdicts, holds Git credentials, or signs its own evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from .proofs import ProofError, ProofVerifier
from .state_store import StateStore, StoreError

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_SHA = re.compile(r"^[a-f0-9]{40}$")


def receipt_name(mission_id: str, candidate_sha: str) -> str:
    """Stable, traversal-safe routing; receipt contents remain HMAC-authoritative."""
    if not isinstance(mission_id, str) or not _ID.fullmatch(mission_id):
        raise ValueError("Invalid QA mission identifier")
    if not isinstance(candidate_sha, str) or not _SHA.fullmatch(candidate_sha):
        raise ValueError("QA receipt requires an exact lowercase Git SHA")
    identity = hashlib.sha256(mission_id.encode("utf-8")).hexdigest()[:20]
    return f"qa-{identity}-{candidate_sha}.json"


@dataclass(frozen=True)
class InboxResult:
    scanned: int
    passed: int
    failed: int
    rejected: int
    missing: int


class QaInbox:
    """Poll only pending mission IDs, not arbitrary filenames in an untrusted tree.

    A rejected receipt never grants success, changes the mission state, or
    exhausts the coding worker's retry budget. Scans are paginated to keep
    long unattended roadmaps bounded.
    """

    def __init__(self, store: StateStore, verifier: ProofVerifier, *, batch_size: int = 128):
        if not 1 <= batch_size <= 512:
            raise ValueError("QA batch_size must be 1..512")
        self.store = store
        self.verifier = verifier
        self.batch_size = batch_size
        self._after_id = ""

    def poll(self) -> InboxResult:
        if self.store.background_suspended():
            return InboxResult(0, 0, 0, 0, 0)
        rows = self.store.db.execute(
            "SELECT id,candidate_sha FROM missions "
            "WHERE state='QA_PENDING' AND id>? ORDER BY id LIMIT ?",
            (self._after_id, self.batch_size),
        ).fetchall()
        if not rows:
            self._after_id = ""
            rows = self.store.db.execute(
                "SELECT id,candidate_sha FROM missions "
                "WHERE state='QA_PENDING' ORDER BY id LIMIT ?",
                (self.batch_size,),
            ).fetchall()
        if not rows:
            return InboxResult(0, 0, 0, 0, 0)
        self._after_id = rows[-1]["id"]
        passed = failed = rejected = missing = 0
        for row in rows:
            try:
                filename = receipt_name(row["id"], row["candidate_sha"])
            except ValueError:
                rejected += 1
                continue
            path = self.verifier.root / filename
            if not path.is_file() and not path.is_symlink():
                missing += 1
                continue
            try:
                # Verifier transactionally checks QA_PENDING + exact candidate,
                # cryptographic signature, and artifact checksum before DONE.
                proof = self.verifier.apply(self.store, filename, mission_id=row["id"])
            except (ProofError, StoreError, OSError):
                rejected += 1
                continue
            if proof.verdict == "PASS":
                passed += 1
            else:
                failed += 1
        return InboxResult(len(rows), passed, failed, rejected, missing)
