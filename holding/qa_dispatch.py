"""Unattended advisory QA dispatcher; no local result can certify code.

Runs in a separate, restricted process. Scans persisted QA_PENDING candidates,
deduplicates exact-SHA reports and bounds retries with a durable backoff.
Neither the runner nor this dispatcher holds a signing key.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time

from .qa_scoped import ScopedDoctorRunner, ScopedQaError
from .state_store import StateStore, StoreError

_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class QaDispatch:
    action: str
    mission_id: str | None
    reason: str


class ScopedQaDispatcher:
    def __init__(self, store: StateStore, runner: ScopedDoctorRunner, *,
                 max_attempts: int = 3):
        if not 1 <= max_attempts <= 6:
            raise ValueError("QA retry limit must be 1..6")
        self.store = store
        self.runner = runner
        self.max_attempts = max_attempts
        store.db.execute("""CREATE TABLE IF NOT EXISTS scoped_qa_advisories (
            mission_id TEXT PRIMARY KEY REFERENCES missions(id),
            candidate_sha TEXT NOT NULL, status TEXT NOT NULL,
            base_sha TEXT NOT NULL, report_sha256 TEXT NOT NULL,
            checked_epoch INTEGER NOT NULL
        )""")
        store.db.execute("""CREATE TABLE IF NOT EXISTS scoped_qa_retries (
            mission_id TEXT PRIMARY KEY REFERENCES missions(id),
            candidate_sha TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            next_epoch INTEGER NOT NULL
        )""")

    def tick(self, *, now_epoch: int | None = None) -> QaDispatch:
        now = int(time.time()) if now_epoch is None else now_epoch
        repo = self.runner.git.repository
        if not any(grant.allows(repo, "local_test", now)
                   for grant in self.store._grants()):
            return QaDispatch("WAIT_FOR_GRANT", None, "No local_test grant")
        # A single bounded SELECT; no full mission graph or evidence directory
        # scan is loaded into memory for hundreds of queued candidates.
        row = self.store.db.execute("""
            SELECT m.id, m.candidate_sha
              FROM missions AS m
         LEFT JOIN scoped_qa_advisories AS a ON a.mission_id=m.id
                                                AND a.candidate_sha=m.candidate_sha
         LEFT JOIN scoped_qa_retries AS r ON r.mission_id=m.id
                                            AND r.candidate_sha=m.candidate_sha
             WHERE m.repo=? AND m.state='QA_PENDING'
               AND length(m.candidate_sha)=40
               AND a.mission_id IS NULL
               AND (r.mission_id IS NULL OR r.next_epoch<=?)
             ORDER BY m.updated_at ASC,m.id ASC LIMIT 1
        """, (repo, now)).fetchone()
        if row is None:
            return QaDispatch("IDLE", None, "No untested exact-SHA candidate")
        mid, candidate_sha = row["id"], row["candidate_sha"]
        if not _SHA.fullmatch(candidate_sha):
            return QaDispatch("INVALID_SHA", mid, "Candidate SHA failed validation")
        try:
            outcome = self.runner.run_pending(self.store, mid)
        except (ScopedQaError, StoreError, OSError, ValueError) as exc:
            # A crash or transient Doctor failure must not relaunch forever.
            # Record only the exception TYPE to prevent leaking secrets/logs.
            with self.store._transaction():
                latest = self.store.db.execute(
                    "SELECT candidate_sha FROM missions WHERE id=? AND "
                    "repo=? AND state='QA_PENDING'", (mid, repo)).fetchone()
                if latest is None or latest["candidate_sha"] != candidate_sha:
                    return QaDispatch("STALE", mid, "Candidate changed during QA")
                previous = self.store.db.execute(
                    "SELECT attempts FROM scoped_qa_retries WHERE mission_id=? "
                    "AND candidate_sha=?", (mid, candidate_sha)).fetchone()
                attempts = (previous["attempts"] if previous else 0) + 1
                next_epoch = now + min(3600, 30 * (2 ** (attempts - 1)))
                self.store.db.execute("""INSERT INTO scoped_qa_retries
                    (mission_id,candidate_sha,attempts,next_epoch) VALUES(?,?,?,?)
                    ON CONFLICT(mission_id) DO UPDATE SET
                    candidate_sha=excluded.candidate_sha,
                    attempts=excluded.attempts,next_epoch=excluded.next_epoch""",
                    (mid, candidate_sha, attempts, next_epoch))
                if attempts >= self.max_attempts:
                    self.store.db.execute("""INSERT INTO scoped_qa_advisories
                        (mission_id,candidate_sha,status,base_sha,report_sha256,checked_epoch)
                        VALUES(?,?,'SCOPED_RUNNER_ERROR','','',?)
                        ON CONFLICT(mission_id) DO UPDATE SET
                        candidate_sha=excluded.candidate_sha,status=excluded.status,
                        base_sha=excluded.base_sha,report_sha256=excluded.report_sha256,
                        checked_epoch=excluded.checked_epoch""",
                        (mid, candidate_sha, now))
                self.store._event(mid, "SCOPED_QA_RUNNER_ERROR",
                                  f"attempt:{attempts} type:{type(exc).__name__}")
            return QaDispatch(
                "RETRY_LATER" if attempts < self.max_attempts else "RUNNER_BLOCKED",
                mid, f"{type(exc).__name__} after {attempts} attempt(s)")
        with self.store._transaction():
            self.store.db.execute("DELETE FROM scoped_qa_retries WHERE mission_id=?",
                                  (mid,))
        return QaDispatch(outcome.status, mid, "Scoped advisory recorded; signed QA still required")
