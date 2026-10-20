"""Bounded automatic repair of independently verified QA failures.

Only a *signed* FAIL tied to the current SHA may requeue a blocked code
mission. A clean, exact-SHA worktree and active code+commit grants are required.
Never resets an agent failure, unverified report, or dirty worktree.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time

from .daemon_lease import DaemonLease
from .git_worktree import GitWorktrees, GitSafetyError
from .state_store import StateStore, StoreError

_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class RepairDecision:
    action: str
    mission_id: str | None
    reason: str


class QaRepairCoordinator:
    def __init__(self, store: StateStore, git: GitWorktrees, lease: DaemonLease,
                 *, max_rounds: int = 3):
        if not 1 <= max_rounds <= 5:
            raise ValueError("QA repair must be bounded to 1..5 rounds")
        self.store = store
        self.git = git
        self.lease = lease
        self.max_rounds = max_rounds
        store.db.execute("""CREATE TABLE IF NOT EXISTS qa_repair_attempts (
            mission_id TEXT NOT NULL REFERENCES missions(id),
            failed_sha TEXT NOT NULL,
            requeued_epoch INTEGER NOT NULL,
            PRIMARY KEY(mission_id, failed_sha)
        )""")

    def requeue_one(self) -> RepairDecision:
        self.lease.assert_owned()
        # UI pause and durable STOP also gate recovery: do not silently
        # requeue work while the operator has suspended Holding.
        stop = self.store.db.execute(
            "SELECT value FROM settings WHERE key='stop_requested'").fetchone()
        if self.store._paused() or (stop is not None and stop[0] == "1"):
            return RepairDecision("PAUSED", None, "Holding paused or stopped")
        # Only signed independent FAILs from the trusted verifier are eligible.
        # Bounded scan does not load the entire roadmap or proof history.
        exists = self.store.db.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' "
            "AND name='signed_proofs'").fetchone()[0]
        if not exists:
            return RepairDecision("IDLE", None, "No independent QA failures")
        row = self.store.db.execute("""
            SELECT m.id,m.candidate_sha,m.repo,m.operation
              FROM missions m
              JOIN signed_proofs p ON p.mission_id=m.id
             WHERE m.repo=? AND m.operation='code' AND m.state='BLOCKED'
               AND m.candidate_sha=p.candidate_sha AND p.verdict='FAIL'
               AND m.evidence_sha=''
               AND NOT EXISTS (
                   SELECT 1 FROM qa_repair_attempts r
                    WHERE r.mission_id=m.id AND r.failed_sha=m.candidate_sha)
             ORDER BY m.updated_at,m.id LIMIT 1
        """, (self.git.repository,)).fetchone()
        if row is None:
            return RepairDecision("IDLE", None, "No new verified failure requiring repair")
        mid, sha = row["id"], row["candidate_sha"]
        if not _SHA.fullmatch(sha):
            return RepairDecision("INVALID_SHA", mid, "Cannot repair corrupt candidate")
        now = int(time.time())
        grants = self.store._grants()
        if not all(any(g.allows(self.git.repository, permission, now)
                       for g in grants) for permission in ("code", "commit")):
            return RepairDecision("UNAUTHORIZED", mid, "Code and commit grants are both required")
        attempted = self.store.db.execute(
            "SELECT count(*) FROM qa_repair_attempts WHERE mission_id=?", (mid,)
        ).fetchone()[0]
        if attempted >= self.max_rounds:
            return RepairDecision("REPAIR_LIMIT", mid, "QA failure repair budget exhausted")
        try:
            self.git.inspect_canonical()
            worktree = self.git.inspect(mid)
            if worktree.base_sha != sha or not self.git.inspect_recovery(mid):
                return RepairDecision("INSPECT_REQUIRED", mid, "Worktree is dirty or wrong SHA")
        except GitSafetyError:
            return RepairDecision("INSPECT_REQUIRED", mid, "Worktree needs manual recovery")
        # Recheck all mutable conditions under a daemon-fenced SQL transaction.
        with self.store._transaction():
            self.store._assert_daemon_fence(self.lease.owner)
            stop = self.store.db.execute(
                "SELECT value FROM settings WHERE key='stop_requested'").fetchone()
            if self.store._paused() or (stop is not None and stop[0] == "1"):
                return RepairDecision("PAUSED", mid, "Operator paused during QA inspection")
            current = self.store.db.execute("""
                SELECT m.candidate_sha,p.verdict,p.candidate_sha AS proof_sha
                  FROM missions m JOIN signed_proofs p ON p.mission_id=m.id
                 WHERE m.id=? AND m.repo=? AND m.operation='code'
                   AND m.state='BLOCKED' AND m.evidence_sha=''
            """, (mid, self.git.repository)).fetchone()
            if (current is None or current["candidate_sha"] != sha
                    or current["proof_sha"] != sha or current["verdict"] != "FAIL"):
                return RepairDecision("STALE", mid, "QA candidate changed during inspection")
            active = self.store._grants()
            if not all(any(g.allows(self.git.repository, op, int(time.time()))
                           for g in active) for op in ("code", "commit")):
                return RepairDecision("UNAUTHORIZED", mid, "Grant revoked during recovery")
            counted = self.store.db.execute(
                "SELECT count(*) FROM qa_repair_attempts WHERE mission_id=?", (mid,)
            ).fetchone()[0]
            if counted >= self.max_rounds:
                return RepairDecision("REPAIR_LIMIT", mid, "QA repair limit reached")
            already = self.store.db.execute(
                "SELECT count(*) FROM qa_repair_attempts WHERE mission_id=? AND failed_sha=?",
                (mid, sha)).fetchone()[0]
            if already:
                return RepairDecision("STALE", mid, "This signed QA failure was already requeued")
            self.store.db.execute(
                "INSERT INTO qa_repair_attempts(mission_id,failed_sha,requeued_epoch) "
                "VALUES(?,?,?)", (mid, sha, int(time.time())))
            self.store._transition(mid, "BLOCKED", "READY",
                                   "Independent QA failure eligible for bounded code repair")
            self.store._event(mid, "QA_REPAIR_REQUEUED", sha)
        return RepairDecision("REQUEUED", mid, "Previous SHA failed signed QA; new code required")
