"""Persistent control-plane API for the future Windows Control Center.

Reports actual SQLite states, not imaginary agent/plugin availability.
The UI must have an authenticated local supervisor boundary in deployment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from .state_store import StateStore


@dataclass(frozen=True)
class ControlSnapshot:
    paused: bool
    stop_requested: bool
    lease_valid: bool
    mission_counts: dict[str, int]
    last_event_seq: int
    scoped_qa_counts: dict[str, int] = field(default_factory=dict)
    repair_counts: dict[str, int] = field(default_factory=dict)


class ControlPlane:
    def __init__(self, path: str | Path):
        self.store = StateStore(path)

    def close(self) -> None:
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _setting(self, name: str) -> bool:
        row = self.store.db.execute("SELECT value FROM settings WHERE key=?", (name,)).fetchone()
        return row is not None and row["value"] == "1"

    def snapshot(self) -> ControlSnapshot:
        counts: dict[str, int] = {}
        for row in self.store.db.execute("SELECT state, COUNT(*) AS count FROM missions GROUP BY state"):
            counts[row["state"]] = row["count"]
        lease_exists = self.store.db.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='daemon_lease'"
        ).fetchone()[0]
        lease = (self.store.db.execute("SELECT expires_epoch FROM daemon_lease WHERE singleton=1").fetchone()
                 if lease_exists else None)
        last = self.store.db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
        return ControlSnapshot(self._setting("paused"), self._setting("stop_requested"),
                               lease is not None and lease["expires_epoch"] > int(time.time()),
                               counts, last, self.scoped_qa_counts(),
                               self.qa_repair_counts())

    def qa_repair_counts(self) -> dict[str, int]:
        """Show actual durable QA retries, never projected completion."""
        present = self.store.db.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' "
            "AND name='qa_repair_attempts'").fetchone()[0]
        if not present:
            return {}
        row = self.store.db.execute(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT mission_id) AS tasks "
            "FROM qa_repair_attempts").fetchone()
        blocked = self.store.db.execute("""
            SELECT COUNT(*) FROM missions m
            WHERE m.state='BLOCKED' AND EXISTS (
                SELECT 1 FROM qa_repair_attempts r WHERE r.mission_id=m.id)
        """).fetchone()[0]
        return {"attempts": int(row["total"]), "missions": int(row["tasks"]),
                "blocked": int(blocked)}

    def scoped_qa_counts(self) -> dict[str, int]:
        """Advisory-only status for the UI, never a certification badge."""
        exists = self.store.db.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' "
            "AND name='scoped_qa_advisories'").fetchone()[0]
        if not exists:
            return {}
        return {row["status"]: row["count"] for row in self.store.db.execute(
            "SELECT q.status, COUNT(*) AS count FROM scoped_qa_advisories q "
            "JOIN missions m ON m.id=q.mission_id "
            "WHERE m.state='QA_PENDING' AND q.candidate_sha=m.candidate_sha "
            "GROUP BY q.status ORDER BY q.status")}

    def scoped_qa_missions(self, *, limit: int = 100) -> list[dict]:
        """Bounded local QA list, exact-SHA matched, no report contents."""
        if not 1 <= limit <= 500:
            raise ValueError("QA list limit must be between 1 and 500")
        if not self.scoped_qa_counts():
            return []
        return [dict(row) for row in self.store.db.execute(
            "SELECT m.id AS mission_id,q.candidate_sha,q.status,q.checked_epoch "
            "FROM scoped_qa_advisories q JOIN missions m ON m.id=q.mission_id "
            "WHERE m.state='QA_PENDING' AND q.candidate_sha=m.candidate_sha "
            "ORDER BY q.checked_epoch DESC, m.id LIMIT ?", (limit,))]

    def import_roadmap(self, raw: str, *, source: str):
        """Accept a versioned DAG without granting any new permissions.

        Called by a trusted local operator/UI host. The importer verifies
        dependencies and commits the version plus tasks atomically. A roadmap
        never authorizes its own execution.
        """
        from .roadmap_import import RoadmapImporter
        return RoadmapImporter(self.store).import_text(raw, source=source)

    def pause(self) -> None:
        self.store.set_paused(True)

    def resume(self) -> None:
        self.store.set_paused(False)

    def request_recovery(self, mission_id: str) -> None:
        """Request one new auditable inspection without bypassing quarantine."""
        from .state_store import StoreError
        with self.store._transaction():
            row = self.store.db.execute(
                "SELECT state FROM missions WHERE id=?", (mission_id,)
            ).fetchone()
            if row is None or row['state'] != 'RECOVERY_REQUIRED':
                raise StoreError("Only quarantined missions can be re-inspected")
            self.store._event(mission_id, 'RECOVERY_SCAN_REQUESTED')

    def request_stop(self) -> None:
        with self.store._transaction():
            self.store.db.execute("INSERT INTO settings(key,value) VALUES('stop_requested','1') "
                                  "ON CONFLICT(key) DO UPDATE SET value='1'")
            self.store._event(None, "STOP_REQUESTED")

    def clear_stop_for_restart(self) -> None:
        """Trusted operator action only: never resume a STOP implicitly."""
        with self.store._transaction():
            self.store.db.execute("INSERT INTO settings(key,value) VALUES('stop_requested','0') "
                                  "ON CONFLICT(key) DO UPDATE SET value='0'")
            self.store._event(None, "STOP_RESET")
