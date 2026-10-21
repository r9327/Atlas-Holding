"""Fail-closed projection of durable engine state into the UI wire schema."""
from __future__ import annotations

import json
import re
from typing import Any

from .control import ControlPlane
from .control_center_state import MAX_LOG_PAGE, MAX_SNAPSHOT_BYTES


_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class DashboardProjectionError(ValueError):
    pass


def _table_exists(control: ControlPlane, name: str) -> bool:
    return bool(control.store.db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone())


def _phase(operation: str, state: str) -> str:
    if state == "RECOVERY_REQUIRED":
        return "EXTERNAL_WAIT"
    if state in {"QA_PENDING", "DONE", "DONE_UNVERIFIED"}:
        return "CERTIFICATION" if state.startswith("DONE") else "TESTS"
    if operation in {"push", "open_pr", "ready_for_review", "read_ci", "merge"}:
        return "CERTIFICATION"
    if operation == "local_test":
        return "TESTS"
    return "CODING"


def _completion_evidence(control: ControlPlane, mission: Any) -> dict | None:
    sha = mission["candidate_sha"]
    if not _SHA.fullmatch(sha or "") or mission["evidence_sha"] != sha:
        return None
    if _table_exists(control, "signed_proofs"):
        row = control.store.db.execute(
            "SELECT verdict,artifact,receipt_digest FROM signed_proofs "
            "WHERE mission_id=? AND candidate_sha=?", (mission["id"], sha),
        ).fetchone()
        if (row is not None and row["verdict"] == "PASS"
                and isinstance(row["receipt_digest"], str)
                and _DIGEST.fullmatch(row["receipt_digest"])):
            return {
                "kind": "CI", "outcome": "SUCCESS", "scope": "FULL",
                "sha": sha, "digest": row["receipt_digest"],
                "source": f"signed_proofs:{row['artifact']}",
            }
    sync_operation = {
        "push": "github.push",
        "ready_for_review": "github.pr_ready",
        "merge": "github.merge",
    }.get(mission["operation"])
    if sync_operation and _table_exists(control, "github_sync_attempts"):
        # A successful sync with the same SHA is NOT evidence for any other
        # mission. Bind the exact idempotency key to the durable transition
        # event generated in the same transaction as this mission's DONE.
        transition = control.store.db.execute(
            "SELECT detail FROM events WHERE mission_id=? "
            "AND event='RUNNING_TO_DONE' ORDER BY seq DESC LIMIT 1",
            (mission["id"],),
        ).fetchone()
        prefix = f"verified sync:{sync_operation}:"
        detail = transition["detail"] if transition is not None else None
        if isinstance(detail, str) and detail.startswith(prefix):
            key = detail[len(prefix):]
            if _DIGEST.fullmatch(key):
                row = control.store.db.execute(
                    "SELECT 1 FROM github_sync_attempts "
                    "WHERE operation=? AND repo=? AND candidate_sha=? "
                    "AND idempotency_key=? AND status='SUCCEEDED' LIMIT 1",
                    (sync_operation, mission["repo"], sha, key),
                ).fetchone()
                if row is not None:
                    # SYNC is an observed remote effect, never a FULL CI pass.
                    return {
                        "kind": "SYNC", "outcome": "SUCCESS", "scope": "SCOPED",
                        "sha": sha, "digest": key,
                        "source": "github_sync_attempts",
                    }
    return None


def _task(control: ControlPlane, row: Any, now_epoch: int) -> dict:
    persistent_state = row["state"]
    if persistent_state not in {
            "READY", "RUNNING", "QA_PENDING", "RECOVERY_REQUIRED",
            "BLOCKED", "DONE"}:
        raise DashboardProjectionError("Unsupported durable mission state")
    candidate = row["candidate_sha"] or None
    if candidate is not None and not _SHA.fullmatch(candidate):
        raise DashboardProjectionError("Durable mission has invalid candidate SHA")
    if (type(row["updated_at"]) is not int or row["updated_at"] < 0
            or row["updated_at"] > now_epoch + 5):
        raise DashboardProjectionError("Durable mission timestamp is invalid")
    evidence = _completion_evidence(control, row) if persistent_state == "DONE" else None
    state = persistent_state
    if persistent_state == "DONE" and evidence is None:
        state = "DONE_UNVERIFIED"
    progress = {
        "READY": "WAITING",
        "RUNNING": "PROGRESSING",
        "QA_PENDING": "WAITING",
        "RECOVERY_REQUIRED": "BLOCKED",
        "BLOCKED": "EXTERNAL_WAIT" if row["external_required"] else "BLOCKED",
        "DONE_UNVERIFIED": "BLOCKED",
        "DONE": "COMPLETED",
    }[state]
    return {
        "id": row["id"],
        "title": f"{row['id']} · {row['operation']}",
        "state": state,
        "phase": _phase(row["operation"], state),
        "progress": progress,
        "candidate_sha": candidate,
        "elapsed_seconds": max(0, now_epoch - row["updated_at"]),
        "attempts": row["failures_without_progress"],
        "completion_evidence": evidence,
    }


def _network(control: ControlPlane) -> str:
    if not _table_exists(control, "external_service_health"):
        return "UNKNOWN"
    statuses = {
        row["status"] for row in control.store.db.execute(
            "SELECT status FROM external_service_health")
    }
    if not statuses or statuses == {"UNKNOWN"}:
        return "UNKNOWN"
    if "BLOCKED" in statuses:
        return "ERROR"
    if "PROBING" in statuses:
        return "SYNCING"
    if "DEGRADED" in statuses:
        return "DEGRADED"
    if statuses == {"ONLINE"}:
        return "ONLINE_STABLE"
    if "ONLINE" in statuses:
        return "DEGRADED"
    return "OFFLINE"


def _category(event: str, mission_id: str | None) -> str:
    if mission_id is None:
        return "SYSTEM"
    if event.startswith(("QA", "SCOPED", "SIGNED")):
        return "QA"
    if event.startswith(("GITHUB", "SYNC", "CI")):
        return "CI"
    if any(word in event for word in ("COMMIT", "WORKTREE", "CANDIDATE")):
        return "CODE"
    return "TASK"


def project_dashboard_snapshot(
    control: ControlPlane, *, engine_id: str, now_epoch: int,
) -> bytes:
    """Return bounded canonical JSON from one consistent SQLite read view."""
    if (not isinstance(control, ControlPlane) or not isinstance(engine_id, str)
            or not engine_id or len(engine_id) > 80
            or type(now_epoch) is not int or not 0 <= now_epoch <= 4_102_444_800):
        raise DashboardProjectionError("Invalid trusted projection context")
    database = control.store.db
    database.execute("BEGIN")
    try:
        mission_rows = tuple(control.store.db.execute(
            "SELECT id,repo,operation,state,candidate_sha,evidence_sha,"
            "external_required,failures_without_progress,updated_at "
            "FROM missions ORDER BY priority DESC,id LIMIT 1001"))
        if len(mission_rows) > 1000:
            raise DashboardProjectionError("Dashboard mission bound exceeded")
        tasks = [_task(control, row, now_epoch) for row in mission_rows]
        events = list(control.store.db.execute(
            "SELECT seq,mission_id,event FROM events ORDER BY seq DESC LIMIT ?",
            (MAX_LOG_PAGE,)))
        events.reverse()
        sequence = events[-1]["seq"] if events else 0
        pending_external = control.store.db.execute(
            "SELECT COUNT(*) FROM missions WHERE external_required=1 "
            "AND state!='DONE'").fetchone()[0]
        network = _network(control)
    except BaseException:
        database.execute("ROLLBACK")
        raise
    else:
        database.execute("COMMIT")
    payload = {
        "schema_version": 1,
        "engine_id": engine_id,
        "sequence": sequence,
        "generated_at": now_epoch,
        "network": network,
        "pending_external": pending_external,
        "tasks": tasks,
        "agents": [],
        "plugins": [],
        "log_page": {
            "items": [{
                "sequence": row["seq"],
                "category": _category(row["event"], row["mission_id"]),
                "message": row["event"],
                "task_id": row["mission_id"],
                "plugin_name": None,
                "evidence_digest": None,
            } for row in events],
            "next_cursor": (f"before:{events[0]['seq']}"
                            if len(events) == MAX_LOG_PAGE else None),
        },
    }
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise DashboardProjectionError("Projected dashboard snapshot is oversized")
    return raw
