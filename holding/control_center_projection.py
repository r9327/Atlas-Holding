"""Fail-closed projection of durable engine state into the UI wire schema."""
from __future__ import annotations

import json
import re
from typing import Any

from .control import ControlPlane
from .control_center_state import MAX_LOG_PAGE, MAX_SNAPSHOT_BYTES


_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SERVICE_NAMES = frozenset({"github", "context7", "firecrawl"})
SERVICE_MAX_AGE_SECONDS = 300


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


def _network(services: list[dict]) -> str:
    # A stored ONLINE verdict outside its freshness window is not ONLINE now.
    statuses = {item["status"] for item in services}
    if not statuses or statuses == {"UNKNOWN"}:
        return "UNKNOWN"
    if "BLOCKED" in statuses:
        return "ERROR"
    if "STALE" in statuses:
        return "DEGRADED"
    if "PROBING" in statuses:
        return "SYNCING"
    if "DEGRADED" in statuses:
        return "DEGRADED"
    if statuses == {"ONLINE"}:
        return "ONLINE_STABLE"
    if "ONLINE" in statuses:
        return "DEGRADED"
    return "OFFLINE"


def _service_health(control: ControlPlane, now_epoch: int) -> list[dict]:
    """Read only durable service observations, never infer plugin installation.

    The status is historical evidence, not a live network probe. Expired or
    future timestamps cannot be displayed as a healthy ONLINE observation.
    No reason/details, account identifiers or credentials cross the UI.
    """
    if not _table_exists(control, "external_service_health"):
        return []
    result = []
    for row in control.store.db.execute(
            "SELECT service,status,observed_at FROM external_service_health "
            "ORDER BY service LIMIT 4"):
        name, status, observed = row["service"], row["status"], row["observed_at"]
        if name not in _SERVICE_NAMES or status not in {
                "UNKNOWN", "PROBING", "ONLINE", "DEGRADED", "OFFLINE", "BLOCKED"}:
            raise DashboardProjectionError("Invalid durable service health")
        age = None
        if observed is not None:
            if type(observed) is not int or not 0 <= observed <= 4_102_444_800:
                raise DashboardProjectionError("Invalid service observation timestamp")
            if observed <= now_epoch:
                age = min(now_epoch - observed, 31_536_000)
        # UNKNOWN is not a claim that a service is installed or operational.
        if status == "UNKNOWN":
            age = None
        elif age is None or age > SERVICE_MAX_AGE_SECONDS:
            status = "STALE"
        result.append({"name": name, "status": status, "age_seconds": age})
    if len(result) > len(_SERVICE_NAMES):
        raise DashboardProjectionError("Unexpected service health cardinality")
    return result


def _model_history(control: ControlPlane, mission_ids: set[str],
                   now_epoch: int) -> list[dict]:
    """Recorded model assignments, never live agent/session activity.

    Read only model usage that references a displayed mission and an enrolled
    measured model; model names are display strings, not commands or trust.
    """
    if (not mission_ids or not _table_exists(control, "mission_model_usage")
            or not _table_exists(control, "model_profiles")):
        return []
    rows = control.store.db.execute(
        "SELECT u.mission_id,u.role,u.model_digest_sha256,u.observed_at,"
        "p.model_name,p.benchmark_digest "
        "FROM mission_model_usage u JOIN model_profiles p "
        "ON p.digest_sha256=u.model_digest_sha256 "
        "ORDER BY u.seq DESC LIMIT 1000"
    )
    entries = []
    for row in rows:
        mission_id = row["mission_id"]
        if mission_id not in mission_ids:
            continue
        role = row["role"]
        if type(role) is not str or role.lower() not in {
                "ceo", "architect", "coder", "auditor", "doctor"}:
            continue  # Not a certified agent role.
        used_at = row["observed_at"]
        if type(used_at) is not int or not 0 <= used_at <= now_epoch + 5:
            raise DashboardProjectionError("Invalid persisted model use timestamp")
        digest, benchmark = row["model_digest_sha256"], row["benchmark_digest"]
        if (type(digest) is not str or not _DIGEST.fullmatch(digest)
                or type(benchmark) is not str or not _DIGEST.fullmatch(benchmark)):
            raise DashboardProjectionError("Unverified model identity in usage")
        name = row["model_name"]
        if type(name) is not str or not 1 <= len(name) <= 120:
            raise DashboardProjectionError("Invalid registered model name")
        entries.append({
            "role": role.upper(), "task_id": mission_id,
            "model_name": name, "model_digest": digest,
            "benchmark_digest": benchmark, "observed_at": used_at,
        })
        if len(entries) >= 20:
            break
    # The bounded newest database assignments are sorted by observed time
    # for clients, without sorting an arbitrarily large SQLite history.
    entries.sort(key=lambda item: item["observed_at"], reverse=True)
    return entries


def _local_hub_observation(control: ControlPlane,
                           now_epoch: int) -> dict | None:
    """Read an existing local supervisor row; no live inference or plugin claim.

    This table is written by the separate trusted MCP supervisor only when
    explicitly configured to use the same engine database. Its contents gain
    transport authenticity only in the enclosing engine-signed IPC snapshot.
    """
    if not _table_exists(control, "mcp_hub_observations"):
        return None
    row = control.store.db.execute(
        "SELECT seq,state,recorded_epoch,pid,source_sha256,protocol_version "
        "FROM mcp_hub_observations ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    state, timestamp, pid = (
        row["state"], row["recorded_epoch"], row["pid"])
    digest, protocol = row["source_sha256"], row["protocol_version"]
    states = {
        "NOT_STARTED", "UNVERIFIED", "RESPONSIVE", "STALE",
        "EXITED", "TIMEOUT", "OUTPUT_LIMIT", "BAD_PROTOCOL",
        "BINARY_CHANGED", "IO_ERROR", "CLOSED",
    }
    if (type(row["seq"]) is not int or row["seq"] < 1
            or type(state) is not str or state not in states
            or type(timestamp) is not int or not 0 <= timestamp <= now_epoch
            or type(digest) is not str or not _DIGEST.fullmatch(digest)
            or (pid is not None and (type(pid) is not int or not 1 <= pid <= 2**32 - 1))
            or (state == "RESPONSIVE") != (protocol == "2026-07-28")
            or (state == "RESPONSIVE" and pid is None)):
        raise DashboardProjectionError("Unverified local MCP hub evidence")
    age = min(now_epoch - timestamp, 31_536_000)
    if age > 30 and state == "RESPONSIVE":
        state, protocol = "STALE", None
    return {
        "state": state, "pid": pid, "observed_epoch": timestamp,
        "age_seconds": age, "source_sha256": digest,
        "protocol_version": protocol,
    }


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
        model_history = _model_history(
            control, {item["id"] for item in tasks}, now_epoch)
        events = list(control.store.db.execute(
            "SELECT seq,mission_id,event FROM events ORDER BY seq DESC LIMIT ?",
            (MAX_LOG_PAGE,)))
        events.reverse()
        sequence = events[-1]["seq"] if events else 0
        pending_external = control.store.db.execute(
            "SELECT COUNT(*) FROM missions WHERE external_required=1 "
            "AND state!='DONE'").fetchone()[0]
        services = _service_health(control, now_epoch)
        hub = _local_hub_observation(control, now_epoch)
        network = _network(services)
        paused = control._setting("paused")
        stop_requested = control._setting("stop_requested")
    except BaseException:
        database.execute("ROLLBACK")
        raise
    else:
        database.execute("COMMIT")
    payload = {
        "schema_version": 5,
        "engine_id": engine_id,
        "paused": paused,
        "stop_requested": stop_requested,
        "sequence": sequence,
        "generated_at": now_epoch,
        "network": network,
        "pending_external": pending_external,
        "tasks": tasks,
        "agents": [],
        "plugins": [],
        "services": services,
        "model_history": model_history,
        "hub": hub,
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
