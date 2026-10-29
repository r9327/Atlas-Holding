"""Read-only, mission-scoped feasibility snapshot (Autonomie V2 F02).

The planner may display this report. It MUST NOT use it as an execution grant,
worker attestation, online probe, Doctor proof, or model benchmark.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
import time

from .autonomy import FORBIDDEN_OPERATIONS, KNOWN_OPERATIONS, StandingGrant
from .state_store import StateStore, StoreError

_MISSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


@dataclass(frozen=True)
class MissionPreflight:
    mission_id: str
    repository: str
    operation: str
    mission_state: str
    status: str
    blockers: tuple[str, ...]
    waiting_for: tuple[str, ...]
    permission: str
    network_verification: str = "NOT_RUN"
    worker_validation: str = "NOT_RUN"
    model_benchmark: str = "NOT_RUN"
    executes_actions: bool = False


def inspect_mission(db_path: Path, mission_id: str, *, now_epoch: int | None = None) -> MissionPreflight:
    """Inspect existing local SQLite without creating a file, table or grant.

    Only the daemon's independent authorization and execution gates can move a
    mission to RUNNING. A PRECHECK_PASSED result does not mean it is runnable.
    """
    if not isinstance(db_path, Path) or not db_path.is_absolute():
        raise StoreError("Absolute state database path required")
    if not isinstance(mission_id, str) or not _MISSION_ID.fullmatch(mission_id):
        raise StoreError("Invalid mission ID")
    if not db_path.is_file():
        raise StoreError("State database does not exist")
    if now_epoch is None:
        now_epoch = int(time.time())
    if type(now_epoch) is not int or now_epoch < 0:
        raise StoreError("Invalid preflight timestamp")

    try:
        # SQLite mode=ro (not StateStore.__init__) prevents schema migration,
        # WAL writes and accidental creation of a missing database.
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            row = db.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
            if row is None:
                raise StoreError("Unknown mission")
            mission = StateStore._mission(row)
            blockers: list[str] = []
            waiting: list[str] = []
            for dep in mission.dependencies:
                dependency = db.execute(
                    "SELECT state,candidate_sha,evidence_sha FROM missions WHERE id=?",
                    (dep,),
                ).fetchone()
                if dependency is None:
                    blockers.append("UNKNOWN_DEPENDENCY:" + dep)
                elif (dependency["state"] != "DONE"
                      or not dependency["candidate_sha"]
                      or dependency["candidate_sha"] != dependency["evidence_sha"]):
                    waiting.append(dep)

            grants = [
                StandingGrant(r["repo"], frozenset({r["operation"]}),
                              r["expires_epoch"], bool(r["enabled"]))
                for r in db.execute(
                    "SELECT repo,operation,expires_epoch,enabled FROM grants "
                    "WHERE repo=? AND operation=?", (mission.repo, mission.operation))
            ]
            granted = any(g.allows(mission.repo, mission.operation, now_epoch)
                          for g in grants)
            permission = "SCOPED_GRANT_PRESENT" if granted else "MISSING_OR_EXPIRED"
            if mission.operation in FORBIDDEN_OPERATIONS or mission.operation not in KNOWN_OPERATIONS:
                blockers.append("UNSUPPORTED_OPERATION")
            if not granted:
                blockers.append("NO_ACTIVE_SCOPED_GRANT")
            if mission.product_decision_required:
                blockers.append("PRODUCT_DECISION_REQUIRED")
            if mission.failures_without_progress >= 3:
                blockers.append("RETRY_BUDGET_EXHAUSTED")
            flags = dict(db.execute(
                "SELECT key,value FROM settings WHERE key IN ('paused','stop_requested')").fetchall())
            if flags.get("paused") == "1" or flags.get("stop_requested") == "1":
                blockers.append("OPERATOR_SUSPENDED")
            if mission.state == "READY" and db.execute(
                "SELECT 1 FROM missions WHERE state='RUNNING' AND id!=? LIMIT 1",
                (mission_id,)).fetchone():
                blockers.append("OTHER_MISSION_RUNNING")
            if mission.external_required:
                blockers.append("EXTERNAL_HEALTH_NOT_VERIFIED")

            if mission.state != "READY":
                status = "NOT_READY"
            elif blockers:
                status = "BLOCKED"
            elif waiting:
                status = "WAITING_FOR_DEPENDENCIES"
            else:
                status = "PRECHECK_PASSED"
            return MissionPreflight(
                mission_id, mission.repo, mission.operation, mission.state,
                status, tuple(blockers), tuple(waiting), permission,
            )
    except sqlite3.DatabaseError as exc:
        raise StoreError("Cannot inspect existing state database") from exc
