"""Fail-closed, read-only projection consumed by the future Qt UI.

The UI must never infer completion or mutate engine state.  This module accepts
one bounded snapshot from an authenticated engine channel and turns it into
immutable display data only after validating its evidence links.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_AGE_SECONDS = 30
MAX_LOG_PAGE = 200
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")


class ControlCenterStateError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ControlCenterStateError("Snapshot contains duplicate JSON keys")
        result[key] = value
    return result


@dataclass(frozen=True)
class EvidenceView:
    kind: str
    scope: str
    sha: str
    digest: str
    source: str


@dataclass(frozen=True)
class TaskView:
    task_id: str
    title: str
    state: str
    phase: str
    progress: str
    candidate_sha: str | None
    elapsed_seconds: int
    attempts: int
    completion_evidence: EvidenceView | None


@dataclass(frozen=True)
class MissionPreflightView:
    mission_id: str
    status: str
    blockers: tuple[str, ...]
    waiting_for: tuple[str, ...]
    permission: str


@dataclass(frozen=True)
class AgentView:
    agent_id: str
    role: str
    status: str
    objective: str
    task_id: str | None
    model_profile: str
    last_event_id: str | None


@dataclass(frozen=True)
class PluginView:
    name: str
    version: str
    state: str
    mode: str
    permission: str
    quota_remaining: int
    cache_generated_at: int | None


@dataclass(frozen=True)
class ModelUsageView:
    role: str
    task_id: str
    model_name: str
    model_digest: str
    benchmark_digest: str
    observed_at: int


@dataclass(frozen=True)
class LocalHubView:
    state: str
    pid: int | None
    observed_epoch: int
    age_seconds: int
    source_sha256: str
    protocol_version: str | None


@dataclass(frozen=True)
class ServiceView:
    name: str
    status: str
    age_seconds: int | None


@dataclass(frozen=True)
class LogView:
    sequence: int
    category: str
    message: str
    task_id: str | None
    plugin_name: str | None
    evidence_digest: str | None


@dataclass(frozen=True)
class DashboardState:
    engine_id: str
    sequence: int
    generated_at: int
    network: str
    pending_external: int
    tasks: tuple[TaskView, ...]
    agents: tuple[AgentView, ...]
    plugins: tuple[PluginView, ...]
    logs: tuple[LogView, ...]
    next_log_cursor: str | None
    paused: bool | None = None
    stop_requested: bool | None = None
    services: tuple[ServiceView, ...] = ()
    model_history: tuple[ModelUsageView, ...] = ()
    hub: LocalHubView | None = None
    mission_preflight: MissionPreflightView | None = None


def _object(value: Any, required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise ControlCenterStateError("Unexpected snapshot object shape")
    return value


def _text(value: Any, label: str, *, maximum: int = 300) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ControlCenterStateError(f"Invalid {label}")
    return value


def _identifier(value: Any, label: str) -> str:
    value = _text(value, label, maximum=80)
    if not _IDENTIFIER.fullmatch(value):
        raise ControlCenterStateError(f"Invalid {label}")
    return value


def _integer(value: Any, label: str, *, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ControlCenterStateError(f"Invalid {label}")
    return value


def _nullable_identifier(value: Any, label: str) -> str | None:
    return None if value is None else _identifier(value, label)


def _evidence(value: Any, candidate_sha: str) -> EvidenceView:
    item = _object(value, {"kind", "outcome", "scope", "sha", "digest", "source"})
    kind, scope, outcome = item["kind"], item["scope"], item["outcome"]
    if kind == "CI":
        if scope != "FULL" or outcome not in ("PASS", "SUCCESS"):
            raise ControlCenterStateError("DONE needs final CI FULL evidence")
    elif kind == "SYNC":
        # Successful network mutation is not proof of product quality.
        if (scope != "SCOPED" or outcome != "SUCCESS"
                or item["source"] != "github_sync_attempts"):
            raise ControlCenterStateError("Invalid scoped synchronization evidence")
    else:
        raise ControlCenterStateError("Unsupported completion evidence kind")
    if item["sha"] != candidate_sha:
        raise ControlCenterStateError("Completion evidence belongs to another SHA")
    digest = item["digest"]
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ControlCenterStateError("Invalid completion evidence digest")
    return EvidenceView(
        item["kind"], item["scope"], candidate_sha, digest,
        _text(item["source"], "evidence source", maximum=500),
    )


def _task(value: Any) -> TaskView:
    item = _object(value, {
        "id", "title", "state", "phase", "progress", "candidate_sha",
        "elapsed_seconds", "attempts", "completion_evidence",
    })
    task_id = _identifier(item["id"], "task id")
    state = item["state"]
    progress = item["progress"]
    if state not in {
            "QUEUED", "READY", "RUNNING", "QA_PENDING",
            "RECOVERY_REQUIRED", "BLOCKED", "DONE_UNVERIFIED", "DONE"}:
        raise ControlCenterStateError("Invalid task state")
    if item["phase"] not in {"CODING", "TESTS", "CERTIFICATION", "EXTERNAL_WAIT"}:
        raise ControlCenterStateError("Invalid task phase")
    allowed_progress = {
        "QUEUED": {"WAITING"},
        "READY": {"WAITING"},
        "RUNNING": {"PROGRESSING", "REASSESSING"},
        "QA_PENDING": {"WAITING"},
        "RECOVERY_REQUIRED": {"BLOCKED"},
        "BLOCKED": {"BLOCKED", "EXTERNAL_WAIT"},
        "DONE_UNVERIFIED": {"BLOCKED"},
        "DONE": {"COMPLETED"},
    }
    if progress not in allowed_progress[state]:
        raise ControlCenterStateError("Task progress contradicts task state")
    sha = item["candidate_sha"]
    if sha is not None and (not isinstance(sha, str) or not _SHA.fullmatch(sha)):
        raise ControlCenterStateError("Invalid task candidate SHA")
    if state in {"QA_PENDING", "DONE_UNVERIFIED", "DONE"} \
            and sha is None:
        raise ControlCenterStateError("Task state requires a candidate SHA")
    evidence = None
    if item["completion_evidence"] is not None:
        if sha is None:
            raise ControlCenterStateError("Completion evidence lacks a candidate SHA")
        evidence = _evidence(item["completion_evidence"], sha)
    if evidence is not None and evidence.kind == "SYNC":
        # The trusted projection exposes synchronization ONLY for these
        # operations, never as a replacement for a code/QA certificate.
        title = item["title"]
        if (not isinstance(title, str) or not title.endswith((
                " · push", " · ready_for_review", " · merge"))):
            raise ControlCenterStateError("SYNC evidence belongs to another task role")
    if state == "DONE" and evidence is None:
        raise ControlCenterStateError("DONE task lacks exact-SHA evidence")
    if state != "DONE" and evidence is not None:
        raise ControlCenterStateError("Non-DONE task cannot claim completion evidence")
    return TaskView(
        task_id, _text(item["title"], "task title"), state, item["phase"],
        progress, sha,
        _integer(item["elapsed_seconds"], "elapsed seconds", maximum=31_536_000),
        _integer(item["attempts"], "attempt count", maximum=10_000), evidence,
    )


def _mission_preflight(value: Any, tasks: tuple[TaskView, ...]) -> MissionPreflightView:
    """Validate advisory F02 status from the authenticated engine projection."""
    item = _object(value, {
        "mission_id", "repository", "operation", "mission_state", "status",
        "blockers", "waiting_for", "permission", "network_verification",
        "worker_validation", "model_benchmark", "executes_actions",
    })
    mission_id = _identifier(item["mission_id"], "preflight mission")
    task = next((t for t in tasks if t.task_id == mission_id), None)
    if task is None or task.state != "READY" or item["mission_state"] != "READY":
        raise ControlCenterStateError("Preflight must reference a READY visible task")
    repo = item["repository"]
    if (not isinstance(repo, str) or len(repo) > 180
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)):
        raise ControlCenterStateError("Invalid preflight repository")
    if item["operation"] not in {
            "plan", "code", "local_test", "commit", "push", "open_pr",
            "ready_for_review", "read_ci", "merge"}:
        raise ControlCenterStateError("Unsupported preflight operation")
    if task.title != f"{mission_id} · {item['operation']}":
        raise ControlCenterStateError("Preflight task/operation mismatch")
    for field in ("network_verification", "worker_validation", "model_benchmark"):
        if item[field] != "NOT_RUN":
            raise ControlCenterStateError("Unverified preflight cannot claim a test")
    if item["executes_actions"] is not False:
        raise ControlCenterStateError("Preflight is never an execution grant")
    status, permission = item["status"], item["permission"]
    if status not in {"BLOCKED", "WAITING_FOR_DEPENDENCIES", "PRECHECK_PASSED"}:
        raise ControlCenterStateError("Invalid preflight status")
    if permission not in {"SCOPED_GRANT_PRESENT", "MISSING_OR_EXPIRED"}:
        raise ControlCenterStateError("Invalid preflight permission")
    reasons = item["blockers"]
    waiting = item["waiting_for"]
    if (not isinstance(reasons, list) or len(reasons) > 40
            or not isinstance(waiting, list) or len(waiting) > 200
            or any(not isinstance(x, str) or len(x) > 200 or not re.fullmatch(
                r"[A-Z_]+(?::[A-Za-z0-9_.-]{1,80})?", x) for x in reasons)
            or any(not isinstance(x, str) or not _IDENTIFIER.fullmatch(x)
                   for x in waiting)
            or len(set(reasons)) != len(reasons)
            or len(set(waiting)) != len(waiting)):
        raise ControlCenterStateError("Invalid preflight blockers or dependencies")
    if ((permission == "MISSING_OR_EXPIRED") != ("NO_ACTIVE_SCOPED_GRANT" in reasons)
            or (status == "PRECHECK_PASSED" and (reasons or waiting))
            or (status == "WAITING_FOR_DEPENDENCIES" and (reasons or not waiting))
            or (status == "BLOCKED" and not reasons)):
        raise ControlCenterStateError("Preflight status contradicts its reasons")
    return MissionPreflightView(mission_id, status, tuple(reasons),
                                tuple(waiting), permission)


def _agent(value: Any, task_ids: set[str]) -> AgentView:
    item = _object(value, {
        "id", "role", "status", "objective", "task_id", "model_profile",
        "last_event_id",
    })
    status = item["status"]
    if item["role"] not in {"CEO", "ARCHITECT", "CODER", "AUDITOR", "DOCTOR"}:
        raise ControlCenterStateError("Invalid agent role")
    if status not in {"IDLE", "RUNNING", "BLOCKED"}:
        raise ControlCenterStateError("Invalid agent status")
    task_id = _nullable_identifier(item["task_id"], "agent task id")
    if task_id is not None and task_id not in task_ids:
        raise ControlCenterStateError("Agent references an unknown task")
    if status == "RUNNING" and task_id is None:
        raise ControlCenterStateError("Running agent lacks a task")
    return AgentView(
        _identifier(item["id"], "agent id"), item["role"], status,
        _text(item["objective"], "agent objective"), task_id,
        _text(item["model_profile"], "model profile", maximum=120),
        _nullable_identifier(item["last_event_id"], "last event id"),
    )


def _plugin(value: Any) -> PluginView:
    item = _object(value, {
        "name", "version", "state", "mode", "permission",
        "quota_remaining", "cache_generated_at",
    })
    if item["state"] not in {"AVAILABLE", "ACTIVE", "CACHED", "UNAVAILABLE"}:
        raise ControlCenterStateError("Invalid plugin state")
    if item["mode"] not in {"LOCAL", "ONLINE", "OFFLINE_CACHE"}:
        raise ControlCenterStateError("Invalid plugin mode")
    if item["permission"] not in {"READ", "CACHE_WRITE"}:
        raise ControlCenterStateError("Invalid plugin permission")
    cache_at = item["cache_generated_at"]
    if cache_at is not None:
        cache_at = _integer(cache_at, "plugin cache time", maximum=4_102_444_800)
    return PluginView(
        _identifier(item["name"], "plugin name"),
        _text(item["version"], "plugin version", maximum=80), item["state"],
        item["mode"], item["permission"],
        _integer(item["quota_remaining"], "plugin quota", maximum=1_000_000_000),
        cache_at,
    )


def _model_use(value: Any, task_ids: set[str],
               generated_at: int) -> ModelUsageView:
    item = _object(value, {
        "role", "task_id", "model_name", "model_digest",
        "benchmark_digest", "observed_at",
    })
    role = item["role"]
    if type(role) is not str or role not in {"CEO", "ARCHITECT", "CODER", "AUDITOR", "DOCTOR"}:
        raise ControlCenterStateError("Invalid historical model role")
    task_id = _identifier(item["task_id"], "model usage task")
    if task_id not in task_ids:
        raise ControlCenterStateError("Model assignment references unknown task")
    digest, benchmark = item["model_digest"], item["benchmark_digest"]
    if (type(digest) is not str or not _DIGEST.fullmatch(digest)
            or type(benchmark) is not str or not _DIGEST.fullmatch(benchmark)):
        raise ControlCenterStateError("Model history lacks verified digests")
    observed = _integer(
        item["observed_at"], "model usage time", maximum=4_102_444_800)
    if observed > generated_at + 5:
        raise ControlCenterStateError("Model usage is from the future")
    return ModelUsageView(
        role, task_id, _text(item["model_name"], "registered model name",
                             maximum=120), digest, benchmark, observed,
    )


def _local_hub(value: Any, generated_at: int) -> LocalHubView:
    item = _object(value, {
        "state", "pid", "observed_epoch", "age_seconds",
        "source_sha256", "protocol_version",
    })
    state = item["state"]
    if type(state) is not str or state not in {
            "NOT_STARTED", "UNVERIFIED", "RESPONSIVE", "STALE",
            "EXITED", "TIMEOUT", "OUTPUT_LIMIT", "BAD_PROTOCOL",
            "BINARY_CHANGED", "IO_ERROR", "CLOSED"}:
        raise ControlCenterStateError("Invalid MCP hub observation status")
    pid = item["pid"]
    if pid is not None:
        pid = _integer(pid, "MCP hub pid", maximum=2**32 - 1)
        if pid == 0:
            raise ControlCenterStateError("Invalid MCP hub process identity")
    observed = _integer(
        item["observed_epoch"], "MCP hub observation epoch",
        maximum=4_102_444_800)
    age = _integer(item["age_seconds"], "MCP hub age", maximum=31_536_000)
    if observed > generated_at or age != min(generated_at - observed, 31_536_000):
        raise ControlCenterStateError("MCP hub freshness contradicts snapshot time")
    digest = item["source_sha256"]
    if type(digest) is not str or not _DIGEST.fullmatch(digest):
        raise ControlCenterStateError("MCP hub source lacks exact binary digest")
    version = item["protocol_version"]
    if state == "RESPONSIVE":
        if version != "2026-07-28" or age > 30 or pid is None:
            raise ControlCenterStateError("MCP hub live proof is not current")
    elif version is not None:
        raise ControlCenterStateError("MCP hub inactive state cannot claim protocol")
    return LocalHubView(state, pid, observed, age, digest, version)


def _service(value: Any) -> ServiceView:
    item = _object(value, {"name", "status", "age_seconds"})
    name = item["name"]
    status = item["status"]
    if type(name) is not str or name not in {"github", "context7", "firecrawl"}:
        raise ControlCenterStateError("Invalid service identity")
    if status not in {"UNKNOWN", "PROBING", "ONLINE", "DEGRADED",
                      "OFFLINE", "BLOCKED", "STALE"}:
        raise ControlCenterStateError("Invalid service health status")
    age = item["age_seconds"]
    if age is not None:
        age = _integer(age, "service observation age", maximum=31_536_000)
    if status == "UNKNOWN" and age is not None:
        raise ControlCenterStateError("Unknown service cannot claim an observation")
    if status not in {"UNKNOWN", "STALE"} and (age is None or age > 300):
        raise ControlCenterStateError("Fresh service state requires recent observation")
    if status == "STALE" and age is not None and age <= 300:
        raise ControlCenterStateError("Stale service contradicts observation age")
    return ServiceView(name, status, age)


def _log(value: Any, task_ids: set[str], plugin_names: set[str]) -> LogView:
    item = _object(value, {
        "sequence", "category", "message", "task_id", "plugin_name",
        "evidence_digest",
    })
    if item["category"] not in {"TASK", "PLUGIN", "CODE", "QA", "CI", "SYSTEM"}:
        raise ControlCenterStateError("Invalid log category")
    task_id = _nullable_identifier(item["task_id"], "log task id")
    plugin_name = _nullable_identifier(item["plugin_name"], "log plugin name")
    if task_id is not None and task_id not in task_ids:
        raise ControlCenterStateError("Log references an unknown task")
    if plugin_name is not None and plugin_name not in plugin_names:
        raise ControlCenterStateError("Log references an unknown plugin")
    digest = item["evidence_digest"]
    if digest is not None and (not isinstance(digest, str) or not _DIGEST.fullmatch(digest)):
        raise ControlCenterStateError("Invalid log evidence digest")
    return LogView(
        _integer(item["sequence"], "log sequence", maximum=2**63 - 1),
        item["category"], _text(item["message"], "log message", maximum=2_000),
        task_id, plugin_name, digest,
    )


def consume_dashboard_snapshot(
    raw: bytes, *, trusted_engine_id: str, expected_sequence: int,
    now_epoch: int,
) -> DashboardState:
    """Validate an authenticated engine snapshot for read-only display."""
    trusted_engine_id = _identifier(trusted_engine_id, "trusted engine id")
    expected_sequence = _integer(
        expected_sequence, "expected sequence", maximum=2**63 - 1,
    )
    now_epoch = _integer(now_epoch, "current time", maximum=4_102_444_800)
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_SNAPSHOT_BYTES:
        raise ControlCenterStateError("Invalid bounded snapshot payload")
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
    except ControlCenterStateError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise ControlCenterStateError("Snapshot is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ControlCenterStateError("Snapshot object required")
    version = payload.get("schema_version")
    keys = {
        "schema_version", "engine_id", "sequence", "generated_at", "network",
        "pending_external", "tasks", "agents", "plugins", "log_page",
    }
    if type(version) is int and version in (2, 3, 4, 5, 6):
        keys |= {"paused", "stop_requested"}
    if type(version) is int and version in (3, 4, 5, 6):
        keys.add("services")
    if type(version) is int and version in (4, 5, 6):
        keys.add("model_history")
    if type(version) is int and version in (5, 6):
        keys.add("hub")
    if type(version) is int and version == 6:
        keys.add("mission_preflight")
    data = _object(payload, keys)
    if (type(version) is not int or version not in (1, 2, 3, 4, 5, 6)
            or data["engine_id"] != trusted_engine_id):
        raise ControlCenterStateError("Snapshot identity mismatch")
    if version in (2, 3, 4, 5, 6) and (type(data["paused"]) is not bool
                          or type(data["stop_requested"]) is not bool):
        raise ControlCenterStateError("Invalid durable operator control state")
    sequence = _integer(data["sequence"], "snapshot sequence", maximum=2**63 - 1)
    if sequence != expected_sequence:
        raise ControlCenterStateError("Stale or unexpected snapshot sequence")
    if data["network"] not in {
            "UNKNOWN", "OFFLINE", "ONLINE_STABLE", "SYNCING",
            "CI_PENDING", "DEGRADED", "ERROR"}:
        raise ControlCenterStateError("Invalid network state")
    generated_at = _integer(
        data["generated_at"], "generation time", maximum=4_102_444_800,
    )
    if (generated_at > now_epoch + 5
            or now_epoch - generated_at > MAX_SNAPSHOT_AGE_SECONDS):
        raise ControlCenterStateError("Snapshot timestamp is stale or from the future")

    if not isinstance(data["tasks"], list) or len(data["tasks"]) > 1_000:
        raise ControlCenterStateError("Invalid bounded task list")
    tasks = tuple(_task(item) for item in data["tasks"])
    task_ids = {item.task_id for item in tasks}
    if len(task_ids) != len(tasks):
        raise ControlCenterStateError("Duplicate task id")

    if not isinstance(data["agents"], list) or len(data["agents"]) > 20:
        raise ControlCenterStateError("Invalid bounded agent list")
    agents = tuple(_agent(item, task_ids) for item in data["agents"])
    if len({item.agent_id for item in agents}) != len(agents):
        raise ControlCenterStateError("Duplicate agent id")

    if not isinstance(data["plugins"], list) or len(data["plugins"]) > 200:
        raise ControlCenterStateError("Invalid bounded plugin list")
    plugins = tuple(_plugin(item) for item in data["plugins"])
    plugin_names = {item.name for item in plugins}
    if len(plugin_names) != len(plugins):
        raise ControlCenterStateError("Duplicate plugin name")

    services: tuple[ServiceView, ...] = ()
    if version in (3, 4, 5, 6):
        raw_services = data["services"]
        if not isinstance(raw_services, list) or len(raw_services) > 3:
            raise ControlCenterStateError("Invalid bounded service list")
        services = tuple(_service(item) for item in raw_services)
        if len({item.name for item in services}) != len(services):
            raise ControlCenterStateError("Duplicate service identities")

    model_history: tuple[ModelUsageView, ...] = ()
    if version in (4, 5, 6):
        raw_history = data["model_history"]
        if not isinstance(raw_history, list) or len(raw_history) > 20:
            raise ControlCenterStateError("Invalid bounded model usage history")
        model_history = tuple(
            _model_use(item, task_ids, generated_at) for item in raw_history
        )
        observed_times = [item.observed_at for item in model_history]
        if observed_times != sorted(observed_times, reverse=True):
            raise ControlCenterStateError("Model history order mismatch")

    hub = None
    if version in (5, 6) and data["hub"] is not None:
        hub = _local_hub(data["hub"], generated_at)

    mission_preflight = None
    if version == 6 and data["mission_preflight"] is not None:
        mission_preflight = _mission_preflight(data["mission_preflight"], tasks)

    page = _object(data["log_page"], {"items", "next_cursor"})
    if not isinstance(page["items"], list) or len(page["items"]) > MAX_LOG_PAGE:
        raise ControlCenterStateError("Invalid bounded log page")
    logs = tuple(_log(item, task_ids, plugin_names) for item in page["items"])
    log_sequences = [item.sequence for item in logs]
    if log_sequences != sorted(set(log_sequences)) or any(
            item > sequence for item in log_sequences):
        raise ControlCenterStateError("Logs are duplicated, unordered or from the future")
    cursor = page["next_cursor"]
    if cursor is not None:
        cursor = _text(cursor, "log cursor", maximum=200)

    return DashboardState(
        trusted_engine_id, sequence,
        generated_at,
        data["network"],
        _integer(data["pending_external"], "pending external count", maximum=100_000),
        tasks, agents, plugins, logs, cursor,
        data["paused"] if version in (2, 3, 4, 5, 6) else None,
        data["stop_requested"] if version in (2, 3, 4, 5, 6) else None,
        services, model_history, hub, mission_preflight,
    )
