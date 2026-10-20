"""Durable, atomic local task queue for Atlas Holding.

This is orchestration infrastructure, not an executor: it cannot validate Doctor
artifacts, create Git worktrees, or authenticate a user. Grants are inserted only
by an external, authenticated onboarding component (not by model tool calls).
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import time
from typing import Iterable, Iterator

from .autonomy import Decision, KNOWN_OPERATIONS, Mission, StandingGrant, choose_next
from .schema import CORE_SCHEMA, apply_schema

SHA_RE = re.compile(r"^[a-fA-F0-9]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
SYNC_OPERATIONS = frozenset({
    "github.push", "github.pr_draft", "github.pr_ready",
    "github.ci_trigger", "github.merge",
})
SERVICE_STATUSES = frozenset({
    "UNKNOWN", "PROBING", "ONLINE", "DEGRADED", "OFFLINE", "BLOCKED",
})


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
        try:
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA busy_timeout=8000")
            if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StoreError("SQLite database is invalid or corrupt")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            apply_schema(self.db, CORE_SCHEMA)
            self.db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('paused', '0')")
        except StoreError:
            self.db.close()
            raise
        except sqlite3.DatabaseError as exc:
            self.db.close()
            raise StoreError("SQLite database is invalid or corrupt") from exc

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

    @staticmethod
    def _validate_onboarding_grant(grant: StandingGrant) -> None:
        if (not isinstance(grant, StandingGrant)
                or not isinstance(grant.repository, str)
                or not REPO_RE.fullmatch(grant.repository)
                or any(part in {".", ".."} for part in grant.repository.split("/"))
                or not isinstance(grant.operations, frozenset) or not grant.operations
                or any(not isinstance(op, str) or op not in KNOWN_OPERATIONS
                       for op in grant.operations)
                or type(grant.expires_epoch) is not int or grant.expires_epoch < 0
                or type(grant.enabled) is not bool):
            raise StoreError("Invalid grant scope")

    def _write_grant_rows(self, grant: StandingGrant) -> None:
        for operation in sorted(grant.operations):
            self.db.execute(
                """INSERT INTO grants(repo,operation,expires_epoch,enabled) VALUES(?,?,?,?)
                   ON CONFLICT(repo,operation) DO UPDATE SET
                   expires_epoch=excluded.expires_epoch, enabled=excluded.enabled""",
                (grant.repository, operation, grant.expires_epoch, int(grant.enabled)),
            )

    def grant_for_onboarding(self, grant: StandingGrant) -> None:
        """Add operations from trusted setup; never expose this as an LLM tool."""
        self._validate_onboarding_grant(grant)
        with self._transaction():
            self._write_grant_rows(grant)
            self._event(None, "GRANT_CHANGED", f"{grant.repository}: {','.join(sorted(grant.operations))}")

    def replace_grant_for_onboarding(self, grant: StandingGrant) -> None:
        """Atomically replace one repository's operations after trusted consent.

        This is the safe path for reducing a standing grant: omitted operations
        are removed in the same transaction before the new scope becomes visible.
        """
        self._validate_onboarding_grant(grant)
        with self._transaction():
            self.db.execute("DELETE FROM grants WHERE repo=?", (grant.repository,))
            self._write_grant_rows(grant)
            self._event(None, "GRANT_REPLACED",
                        f"{grant.repository}: {','.join(sorted(grant.operations))}")

    def revoke(self, repo: str) -> None:
        if (not isinstance(repo, str) or not REPO_RE.fullmatch(repo)
                or any(part in {".", ".."} for part in repo.split("/"))):
            raise StoreError("Invalid exact repository")
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

    def background_suspended(self) -> bool:
        """Durable Pause/STOP gate shared by daemon, scoped QA and proof intake.

        Read inside BEGIN IMMEDIATE when claiming or settling work, so a
        concurrent control-plane STOP cannot interleave with that mutation.
        """
        if self._paused():
            return True
        row = self.db.execute(
            "SELECT value FROM settings WHERE key='stop_requested'").fetchone()
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
                   lease_owner: str | None = None,
                   supported_operations: frozenset[str] | None = None) -> Decision:
        """Atomically choose and claim one mission; never dispatch an executor here."""
        now = int(time.time()) if now_epoch is None else now_epoch
        with self._transaction():
            self._assert_daemon_fence(lease_owner)
            if self._paused():
                return Decision("WAIT", None, "Holding paused")
            if self.background_suspended():
                return Decision("WAIT", None, "Holding stopped")
            choice = choose_next(self.missions(), self._grants(), now_epoch=now, online=online,
                                 supported_operations=supported_operations)
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
            prior = self.db.execute(
                "SELECT candidate_sha FROM missions WHERE id=? AND state='RUNNING'",
                (mission_id,)).fetchone()
            if prior is None:
                raise StoreError("Checkpoint requires a RUNNING mission")
            if prior["candidate_sha"] == candidate_sha.lower():
                # A failed SHA can never be "repaired" by resubmitting the
                # same commit. Keep the existing QA history and retry budget.
                raise StoreError("UNCHANGED_CANDIDATE_SHA")
            self._transition(mission_id, "RUNNING", "QA_PENDING")
            self.db.execute(
                "UPDATE missions SET candidate_sha=?,evidence_sha='', "
                "failures_without_progress=0 WHERE id=?",
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

    def save_service_health(
        self, *, service: str, status: str, observed_at: int | None,
        consecutive_successes: int, consecutive_failures: int,
        retry_attempts: int, retry_not_before: int | None, reason: str | None,
        updated_epoch: int, lease_owner: str,
    ) -> None:
        """Persist one newer service observation without accepting time reversal."""
        integers = (consecutive_successes, consecutive_failures, retry_attempts, updated_epoch)
        if (service not in {"github", "context7", "firecrawl"}
                or status not in SERVICE_STATUSES
                or any(type(value) is not int or value < 0 for value in integers)
                or retry_attempts > 5
                or (observed_at is not None
                    and (type(observed_at) is not int or observed_at < 0))
                or (retry_not_before is not None
                    and (type(retry_not_before) is not int or retry_not_before < 0))
                or (reason is not None and (not isinstance(reason, str) or len(reason) > 160))
                or (observed_at is None) != (status == "UNKNOWN")
                or observed_at is not None and updated_epoch < observed_at
                or retry_not_before is not None
                and (observed_at is None or retry_not_before <= observed_at)
                or consecutive_successes and consecutive_failures
                or status == "ONLINE" and consecutive_successes < 2
                or status == "BLOCKED" and not reason):
            raise StoreError("Invalid external service health")
        with self._transaction():
            self._assert_daemon_fence(lease_owner)
            prior = self.db.execute(
                "SELECT observed_at FROM external_service_health WHERE service=?",
                (service,),
            ).fetchone()
            if (prior is not None and (observed_at is None
                    or prior["observed_at"] is not None
                    and observed_at <= prior["observed_at"])):
                raise StoreError("STALE_SERVICE_OBSERVATION")
            self.db.execute(
                """INSERT INTO external_service_health(
                   service,status,observed_at,consecutive_successes,consecutive_failures,
                   retry_attempts,retry_not_before,reason,updated_epoch)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(service) DO UPDATE SET
                   status=excluded.status, observed_at=excluded.observed_at,
                   consecutive_successes=excluded.consecutive_successes,
                   consecutive_failures=excluded.consecutive_failures,
                   retry_attempts=excluded.retry_attempts,
                   retry_not_before=excluded.retry_not_before,
                   reason=excluded.reason, updated_epoch=excluded.updated_epoch""",
                (service, status, observed_at, consecutive_successes,
                 consecutive_failures, retry_attempts, retry_not_before,
                 reason, updated_epoch),
            )
            self._event(None, "SERVICE_HEALTH", f"{service}:{status}")

    def service_health(self, service: str) -> dict | None:
        if service not in {"github", "context7", "firecrawl"}:
            raise StoreError("Invalid external service")
        row = self.db.execute(
            "SELECT * FROM external_service_health WHERE service=?", (service,)
        ).fetchone()
        return None if row is None else dict(row)

    @staticmethod
    def _validate_sync_identity(
        operation: str, repo: str, branch: str, candidate_sha: str,
        idempotency_key: str,
    ) -> None:
        if (operation not in SYNC_OPERATIONS
                or not isinstance(repo, str) or not REPO_RE.fullmatch(repo)
                or any(part in {".", ".."} for part in repo.split("/"))
                or not isinstance(branch, str) or not BRANCH_RE.fullmatch(branch)
                or branch in {"main", "master"} or branch.startswith("refs/")
                or ".." in branch or "//" in branch
                or not isinstance(candidate_sha, str) or not SHA_RE.fullmatch(candidate_sha)
                or candidate_sha != candidate_sha.lower()
                or not isinstance(idempotency_key, str)
                or not DIGEST_RE.fullmatch(idempotency_key)):
            raise StoreError("Invalid GitHub synchronization identity")

    def begin_sync_attempt(
        self, *, operation: str, repo: str, branch: str, candidate_sha: str,
        attempt: int, idempotency_key: str, updated_epoch: int,
        lease_owner: str, recovery_context: bytes | None = None,
        recovery_context_sha256: str | None = None,
    ) -> None:
        """Atomically persist PENDING before any privileged network mutation."""
        self._validate_sync_identity(
            operation, repo, branch, candidate_sha, idempotency_key)
        if (type(attempt) is not int or not 1 <= attempt <= 3
                or type(updated_epoch) is not int or updated_epoch < 0):
            raise StoreError("Invalid GitHub synchronization attempt")
        if ((recovery_context is None) != (recovery_context_sha256 is None)
                or recovery_context is not None and (
                    not isinstance(recovery_context, bytes)
                    or not 1 <= len(recovery_context) <= 16 * 1024
                    or not isinstance(recovery_context_sha256, str)
                    or not DIGEST_RE.fullmatch(recovery_context_sha256)
                    or hashlib.sha256(recovery_context).hexdigest()
                    != recovery_context_sha256)):
            raise StoreError("Invalid synchronization recovery context")
        candidate_sha = candidate_sha.lower()
        with self._transaction():
            self._assert_daemon_fence(lease_owner)
            rows = self.db.execute(
                """SELECT attempt,status,retryable,idempotency_key,updated_epoch
                   FROM github_sync_attempts
                   WHERE operation=? AND repo=? AND branch=? AND candidate_sha=?
                   ORDER BY attempt""",
                (operation, repo, branch, candidate_sha),
            ).fetchall()
            foreign_key = self.db.execute(
                """SELECT 1 FROM github_sync_attempts WHERE idempotency_key=?
                   AND (operation<>? OR repo<>? OR branch<>? OR candidate_sha<>?)
                   LIMIT 1""",
                (idempotency_key, operation, repo, branch, candidate_sha),
            ).fetchone()
            if foreign_key is not None:
                raise StoreError("Synchronization idempotency key reused across identities")
            if any(row["idempotency_key"] != idempotency_key for row in rows):
                raise StoreError("Synchronization idempotency key changed")
            if [row["attempt"] for row in rows] != list(range(1, len(rows) + 1)):
                raise StoreError("Synchronization attempt sequence has gaps")
            if any(row["status"] != "FAILED" or not row["retryable"] for row in rows):
                raise StoreError("Synchronization operation is pending or terminal")
            expected = 1 if not rows else rows[-1]["attempt"] + 1
            if attempt != expected:
                raise StoreError("Unexpected synchronization attempt number")
            if rows and updated_epoch <= rows[-1]["updated_epoch"]:
                raise StoreError("STALE_SYNCHRONIZATION_ATTEMPT")
            self.db.execute(
                """INSERT INTO github_sync_attempts(
                   operation,repo,branch,candidate_sha,attempt,status,retryable,
                   idempotency_key,updated_epoch) VALUES(?,?,?,?,?,'PENDING',0,?,?)""",
                (operation, repo, branch, candidate_sha, attempt,
                 idempotency_key, updated_epoch),
            )
            if recovery_context is not None:
                self.db.execute(
                    """INSERT INTO github_sync_context(
                       operation,repo,branch,candidate_sha,attempt,pre_state,
                       pre_state_sha256,recorded_epoch) VALUES(?,?,?,?,?,?,?,?)""",
                    (operation, repo, branch, candidate_sha, attempt,
                     recovery_context, recovery_context_sha256, updated_epoch),
                )
            self._event(None, "SYNC_ATTEMPT_STARTED",
                        f"{operation}:{repo}:{candidate_sha}:{attempt}")

    def finish_sync_attempt(
        self, *, operation: str, repo: str, branch: str, candidate_sha: str,
        attempt: int, idempotency_key: str, status: str, retryable: bool,
        updated_epoch: int, lease_owner: str,
    ) -> None:
        """Settle exactly one PENDING attempt; terminal rows are immutable."""
        self._validate_sync_identity(
            operation, repo, branch, candidate_sha, idempotency_key)
        if (type(attempt) is not int or not 1 <= attempt <= 3
                or status not in {"SUCCEEDED", "FAILED", "BLOCKED"}
                or type(retryable) is not bool
                or (status != "FAILED" and retryable)
                or type(updated_epoch) is not int or updated_epoch < 0):
            raise StoreError("Invalid synchronization verdict")
        with self._transaction():
            self._assert_daemon_fence(lease_owner)
            updated = self.db.execute(
                """UPDATE github_sync_attempts SET status=?,retryable=?,updated_epoch=?
                   WHERE operation=? AND repo=? AND branch=? AND candidate_sha=?
                   AND attempt=? AND idempotency_key=? AND status='PENDING'
                   AND updated_epoch<=?""",
                (status, int(retryable), updated_epoch, operation, repo, branch,
                 candidate_sha.lower(), attempt, idempotency_key, updated_epoch),
            )
            if updated.rowcount != 1:
                raise StoreError("Synchronization attempt is missing or already settled")
            self._event(None, "SYNC_ATTEMPT_FINISHED",
                        f"{operation}:{repo}:{candidate_sha.lower()}:{attempt}:{status}")

    def sync_attempts(
        self, *, operation: str, repo: str, branch: str, candidate_sha: str,
    ) -> tuple[dict, ...]:
        self._validate_sync_identity(
            operation, repo, branch, candidate_sha, "0" * 64)
        return tuple(dict(row) for row in self.db.execute(
            """SELECT * FROM github_sync_attempts
               WHERE operation=? AND repo=? AND branch=? AND candidate_sha=?
               ORDER BY attempt""",
            (operation, repo, branch, candidate_sha.lower()),
        ))

    def sync_recovery_context(
        self, *, operation: str, repo: str, branch: str,
        candidate_sha: str, attempt: int,
    ) -> dict | None:
        self._validate_sync_identity(
            operation, repo, branch, candidate_sha, "0" * 64)
        if type(attempt) is not int or not 1 <= attempt <= 3:
            raise StoreError("Invalid synchronization attempt")
        row = self.db.execute(
            """SELECT pre_state,pre_state_sha256,recorded_epoch
               FROM github_sync_context WHERE operation=? AND repo=? AND branch=?
               AND candidate_sha=? AND attempt=?""",
            (operation, repo, branch, candidate_sha.lower(), attempt),
        ).fetchone()
        if row is None:
            return None
        try:
            raw = bytes(row["pre_state"])
            digest = row["pre_state_sha256"]
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError(
                "Malformed synchronization recovery context") from exc
        if (not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest)
                or hashlib.sha256(raw).hexdigest() != digest):
            raise StoreError("Synchronization recovery context integrity mismatch")
        return {
            "pre_state": raw,
            "pre_state_sha256": digest,
            "recorded_epoch": row["recorded_epoch"],
        }

    def integrity_check(self) -> bool:
        return self.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
