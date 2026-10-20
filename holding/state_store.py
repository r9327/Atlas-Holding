"""Durable, atomic local task queue for Atlas Holding.

This is orchestration infrastructure, not an executor: it cannot validate Doctor
artifacts, create Git worktrees, or authenticate a user. Grants are inserted only
by an external, authenticated onboarding component (not by model tool calls).
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import json
import re
import sqlite3
import time
from typing import Iterable, Iterator

from .autonomy import Decision, KNOWN_OPERATIONS, Mission, StandingGrant, choose_next

SHA_RE = re.compile(r"^[a-fA-F0-9]{40}$")


class StoreError(RuntimeError):
    pass


class StateStore:
    """SQLite WAL queue. Every mutation is a short BEGIN IMMEDIATE transaction.

    Intended as the *single process* persistence layer for a daemon. Multiple
    store objects are safe to compete for a task; only one can acquire it.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), isolation_level=None, timeout=8)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=8000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                operation TEXT NOT NULL,
                dependencies TEXT NOT NULL,
                priority INTEGER NOT NULL,
                state TEXT NOT NULL,
                candidate_sha TEXT NOT NULL DEFAULT '',
                evidence_sha TEXT NOT NULL DEFAULT '',
                external_required INTEGER NOT NULL DEFAULT 0,
                product_decision_required INTEGER NOT NULL DEFAULT 0,
                failures_without_progress INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS grants (
                repo TEXT NOT NULL,
                operation TEXT NOT NULL,
                expires_epoch INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(repo, operation)
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                mission_id TEXT,
                event TEXT NOT NULL,
                detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO settings(key, value) VALUES ('paused', '0');
        """)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def _event(self, task: str | None, name: str, detail: str = "") -> None:
        self.db.execute(
            "INSERT INTO events(ts,mission_id,event,detail) VALUES(?,?,?,?)",
            (int(time.time()), task, name, detail[:1200]),
        )

    @staticmethod
    def _mission(row: sqlite3.Row) -> Mission:
        return Mission(
            id=row["id"], repo=row["repo"], operation=row["operation"],
            dependencies=tuple(json.loads(row["dependencies"])),
            priority=row["priority"], state=row["state"],
            candidate_sha=row["candidate_sha"], evidence_sha=row["evidence_sha"],
            external_required=bool(row["external_required"]),
            product_decision_required=bool(row["product_decision_required"]),
            failures_without_progress=row["failures_without_progress"],
        )

    def missions(self) -> tuple[Mission, ...]:
        return tuple(self._mission(r) for r in self.db.execute("SELECT * FROM missions ORDER BY id"))

    def _grants(self) -> tuple[StandingGrant, ...]:
        grouped: dict[tuple[str, int, bool], set[str]] = {}
        for row in self.db.execute("SELECT * FROM grants"):
            key = (row["repo"], row["expires_epoch"], bool(row["enabled"]))
            grouped.setdefault(key, set()).add(row["operation"])
        return tuple(StandingGrant(k[0], frozenset(ops), k[1], k[2]) for k, ops in grouped.items())

    def add_missions(self, missions: Iterable[Mission]) -> None:
        incoming = tuple(missions)
        if not incoming:
            return
        with self._transaction():
            old = self.missions()
            seen = {m.id for m in old}
            if any(not m.id or m.id in seen or m.state != "READY" or
                   not m.repo or m.operation not in KNOWN_OPERATIONS for m in incoming):
                raise StoreError("Only unique, known-operation, READY missions may be added")
            if len({m.id for m in incoming}) != len(incoming):
                raise StoreError("Duplicate mission IDs in import")
            checked = choose_next((*old, *incoming), (), now_epoch=int(time.time()), online=False)
            if checked.action == "BLOCKED" and checked.reason in {
                "Dependency cycle", "Unknown dependency", "Duplicate or blank task IDs",
                "Task missing exact repository", "Missing or stale completion evidence",
            }:
                raise StoreError(checked.reason)
            now = int(time.time())
            for m in incoming:
                self.db.execute(
                    """INSERT INTO missions(
                       id,repo,operation,dependencies,priority,state,candidate_sha,evidence_sha,
                       external_required,product_decision_required,failures_without_progress,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (m.id, m.repo, m.operation, json.dumps(m.dependencies), m.priority, m.state,
                     m.candidate_sha, m.evidence_sha, int(m.external_required),
                     int(m.product_decision_required), m.failures_without_progress, now),
                )
                self._event(m.id, "MISSION_IMPORTED", m.operation)

    def grant_for_onboarding(self, grant: StandingGrant) -> None:
        """ONLY trusted setup/UI can call; never expose this method as an LLM tool."""
        if not grant.repository or not grant.operations or any(op not in KNOWN_OPERATIONS for op in grant.operations):
            raise StoreError("Invalid grant scope")
        with self._transaction():
            for operation in sorted(grant.operations):
                self.db.execute(
                    """INSERT INTO grants(repo,operation,expires_epoch,enabled) VALUES(?,?,?,?)
                       ON CONFLICT(repo,operation) DO UPDATE SET
                       expires_epoch=excluded.expires_epoch, enabled=excluded.enabled""",
                    (grant.repository, operation, grant.expires_epoch, int(grant.enabled)),
                )
            self._event(None, "GRANT_CHANGED", f"{grant.repository}: {','.join(sorted(grant.operations))}")

    def revoke(self, repo: str) -> None:
        with self._transaction():
            self.db.execute("UPDATE grants SET enabled=0 WHERE repo=?", (repo,))
            self._event(None, "GRANT_REVOKED", repo)

    def set_paused(self, paused: bool) -> None:
        with self._transaction():
            self.db.execute("UPDATE settings SET value=? WHERE key='paused'", ("1" if paused else "0",))
            self._event(None, "PAUSED" if paused else "RESUMED")

    def _paused(self) -> bool:
        row = self.db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        return row is not None and row["value"] == "1"

    def _assert_daemon_fence(self, lease_owner: str | None) -> None:
        """Verify ownership within BEGIN IMMEDIATE before changing mission state.

        Legacy direct StateStore callers can omit a lease; the autonomous daemon
        MUST pass its unique owner token at every state transition.
        """
        if lease_owner is None:
            return
        row = self.db.execute(
            "SELECT owner,expires_epoch FROM daemon_lease WHERE singleton=1"
        ).fetchone()
        if row is None or row["owner"] != lease_owner or row["expires_epoch"] <= int(time.time()):
            raise StoreError("DAEMON_LEASE_LOST")

    def claim_next(self, *, online: bool, now_epoch: int | None = None,
                   lease_owner: str | None = None) -> Decision:
        """Atomically choose and claim one mission; never dispatch an executor here."""
        now = int(time.time()) if now_epoch is None else now_epoch
        with self._transaction():
            self._assert_daemon_fence(lease_owner)
            if self._paused():
                return Decision("WAIT", None, "Holding paused")
            choice = choose_next(self.missions(), self._grants(), now_epoch=now, online=online)
            if choice.action == "RUN" and choice.mission_id:
                updated = self.db.execute(
                    "UPDATE missions SET state='RUNNING', updated_at=? WHERE id=? AND state='READY'",
                    (now, choice.mission_id),
                )
                if updated.rowcount != 1:
                    raise StoreError("Mission was already claimed")
                self._event(choice.mission_id, "MISSION_CLAIMED", choice.reason)
            return choice

    def _transition(self, mission_id: str, source: str, destination: str, reason: str = "") -> None:
        updated = self.db.execute(
            "UPDATE missions SET state=?,updated_at=? WHERE id=? AND state=?",
            (destination, int(time.time()), mission_id, source),
        )
        if updated.rowcount != 1:
            raise StoreError(f"Invalid transition for {mission_id}: expected {source}")
        self._event(mission_id, f"{source}_TO_{destination}", reason)

    def checkpoint(self, mission_id: str, candidate_sha: str, *,
                   lease_owner: str | None = None) -> None:
        """Store a code checkpoint. A checkpoint is NOT Doctor/CI verification."""
        if not SHA_RE.fullmatch(candidate_sha):
            raise StoreError("Candidate SHA must be a real Git SHA-1 object ID")
        with self._transaction():
            self._assert_daemon_fence(lease_owner)
            self._transition(mission_id, "RUNNING", "QA_PENDING")
            self.db.execute(
                "UPDATE missions SET candidate_sha=?,evidence_sha='' WHERE id=?",
                (candidate_sha.lower(), mission_id),
            )

    def register_test_verdict(
        self, mission_id: str, *, candidate_sha: str, evidence_sha: str,
        verdict: str, runner: str, artifact_id: str,
    ) -> None:
        """Legacy string-only attestations are not sufficient to mark DONE.

        Only the independently privileged ProofVerifier.apply() can accept
        an actual signed Doctor/CI artifact bound to the candidate SHA.
        """
        raise StoreError("Unauthenticated text verdict refused; use ProofVerifier.apply")

    def fail_running(self, mission_id: str, reason: str, *, max_attempts: int = 3,
                     lease_owner: str | None = None) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        with self._transaction():
            self._assert_daemon_fence(lease_owner)
            row = self.db.execute(
                "SELECT failures_without_progress FROM missions WHERE id=? AND state='RUNNING'",
                (mission_id,),
            ).fetchone()
            if row is None:
                raise StoreError("Only running tasks can fail")
            attempts = row[0] + 1
            self._transition(mission_id, "RUNNING", "BLOCKED" if attempts >= max_attempts else "READY", reason)
            self.db.execute(
                "UPDATE missions SET failures_without_progress=? WHERE id=?", (attempts, mission_id)
            )

    def interrupt_running(self, mission_id: str, *, lease_owner: str, reason: str) -> None:
        """Quarantine an interrupted worker without consuming its retry budget.

        No automatic replay is safe until Git journal/worktree inspection.
        """
        with self._transaction():
            self._assert_daemon_fence(lease_owner)
            self._transition(mission_id, "RUNNING", "RECOVERY_REQUIRED", reason[:160])

    def quarantine_interrupted(self, *, lease_owner: str | None = None) -> tuple[str, ...]:
        """On daemon restart: NEVER automatically rerun an interrupted writer."""
        with self._transaction():
            self._assert_daemon_fence(lease_owner)
            ids = [r[0] for r in self.db.execute("SELECT id FROM missions WHERE state='RUNNING'")]
            for task in ids:
                self._transition(task, "RUNNING", "RECOVERY_REQUIRED", "Interrupted process; inspect worktree")
            return tuple(ids)

    def acknowledge_recovery(self, mission_id: str, *, safe_worktree_confirmed: bool,
                             lease_owner: str | None = None) -> None:
        """Must be called by a Git/worktree repair service, never by the LLM."""
        if not safe_worktree_confirmed:
            raise StoreError("Worktree inspection has not succeeded")
        with self._transaction():
            self._assert_daemon_fence(lease_owner)
            self._transition(mission_id, "RECOVERY_REQUIRED", "READY")

    def recover_verified_commit(self, mission_id: str, candidate_sha: str, *,
                                lease_owner: str) -> None:
        """Recovery coordinator only: checkpoint a Git-verified interrupted commit.

        Does NOT accept a Doctor PASS, and cannot transition to DONE.
        """
        if not SHA_RE.fullmatch(candidate_sha):
            raise StoreError("Invalid recovered Git SHA")
        with self._transaction():
            self._assert_daemon_fence(lease_owner)
            self._transition(mission_id, "RECOVERY_REQUIRED", "QA_PENDING",
                             "Verified Git commit recovered; QA still required")
            self.db.execute(
                "UPDATE missions SET candidate_sha=?,evidence_sha='' WHERE id=?",
                (candidate_sha.lower(), mission_id),
            )

    def events(self, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 10000:
            raise ValueError("limit must be 1..10000")
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM events ORDER BY seq DESC LIMIT ?", (limit,)
        )]

    def integrity_check(self) -> bool:
        return self.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
