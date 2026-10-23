"""Holdings selected-project -> real mission-import safety gate.

Planner DAGs are not executable missions. This adapter accepts a separate,
explicit operational plan only if every lot, dependency and acceptance agrees
with a validated exact-SHA planner plan for the selected project. The trusted
ControlPlane importer remains responsible for durable atomic state changes;
this module never supplies process authority, merge rights or shell commands.
"""
from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from .project_plan_binding import bind_selected_project_plan, ProjectPlanBindingError
from .project_registry import ProjectRegistry, _registry_lock
from .roadmap_contract import MAX_ROADMAP_BYTES, validate_roadmap_plan


class ProjectMissionGateError(ValueError):
    pass


_ALLOWED_OPERATIONS = frozenset({
    "plan", "code", "local_test", "commit", "push", "open_pr", "read_ci",
})


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record = {}
    for key, value in pairs:
        if key in record:
            raise ProjectMissionGateError("duplicate mission JSON field")
        record[key] = value
    return record


def import_verified_project_missions(
    registry: ProjectRegistry,
    planner_bytes: bytes,
    operations_text: str,
    *,
    expected_profile_digest: str,
    expected_snapshot_sha: str,
    expected_source_digest: str,
    runtime_repository: str,
    source: str,
    trusted_importer: Callable[..., object],
) -> object:
    """Import through the genuine engine importer, only after strict binding.

    The registry lock prevents changing project selection during the import.
    The *actual* control plane must independently authorize execution later.
    """
    if not isinstance(runtime_repository, str) or not runtime_repository or "/" not in runtime_repository:
        raise ProjectMissionGateError("trusted runtime repository required")
    if not callable(trusted_importer):
        raise ProjectMissionGateError("trusted mission importer is required")
    if not isinstance(source, str) or not source.strip() or len(source) > 500:
        raise ProjectMissionGateError("mission source provenance required")
    if not isinstance(operations_text, str):
        raise ProjectMissionGateError("invalid operational roadmap")
    try:
        encoded = operations_text.encode("utf-8")
    except UnicodeError as exc:
        raise ProjectMissionGateError("invalid operational encoding") from exc
    if not 1 <= len(encoded) <= MAX_ROADMAP_BYTES:
        raise ProjectMissionGateError("operational roadmap exceeds size budget")
    try:
        document = json.loads(operations_text, object_pairs_hook=_unique,
                              parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except ProjectMissionGateError:
        raise
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise ProjectMissionGateError("invalid operational roadmap JSON") from exc
    if (not isinstance(document, dict) or set(document) != {"repo", "missions"}
            or not isinstance(document["missions"], list)):
        raise ProjectMissionGateError("invalid operational roadmap structure")
    if len(document["missions"]) > 2000:
        raise ProjectMissionGateError("operational task limit exceeded")

    with _registry_lock(registry.directory):
        binding = bind_selected_project_plan(
            registry, planner_bytes,
            expected_profile_digest=expected_profile_digest,
            expected_snapshot_sha=expected_snapshot_sha,
            expected_source_digest=expected_source_digest,
        )
        if document["repo"] != binding.repository:
            raise ProjectMissionGateError("operational roadmap targets another project")
        # A selected UI project does not authorize changing the repo of the
        # installed engine, whose canonical root and Git grants are pinned.
        if binding.repository != runtime_repository:
            raise ProjectMissionGateError("selected project differs from installed engine repository")
        plan = validate_roadmap_plan(
            planner_bytes,
            expected_repository=binding.repository,
            expected_snapshot_sha=binding.snapshot_sha,
            expected_source_digest=binding.source_digest,
        )
        lots = {lot.lot_id: lot for lot in plan.lots}
        if any(lot.state in {"BLOCKED", "REQUIRES_DECISION"} for lot in plan.lots):
            # The live importer writes all missions as READY. Never smuggle
            # a planner-blocked lot into that executable state.
            raise ProjectMissionGateError("blocked planner lots require explicit replan")
        mission_ids = set()
        for mission in document["missions"]:
            if not isinstance(mission, dict):
                raise ProjectMissionGateError("mission is not an object")
            if not {"id", "operation", "acceptance"} <= set(mission) or set(mission) - {
                "id", "operation", "acceptance", "depends_on", "priority",
                "external_required", "product_decision_required",
            }:
                raise ProjectMissionGateError("unexpected mission shape")
            identifier = mission["id"]
            if not isinstance(identifier, str) or identifier not in lots or identifier in mission_ids:
                raise ProjectMissionGateError("mission IDs differ from approved planner lots")
            mission_ids.add(identifier)
            lot = lots[identifier]
            if lot.autonomy not in {"AUTO", "AUTO_WITH_STANDING_GRANT"}:
                raise ProjectMissionGateError("restricted lot requires separate human decision")
            if (not isinstance(mission["operation"], str)
                    or mission["operation"] not in _ALLOWED_OPERATIONS):
                raise ProjectMissionGateError("unsupported or privileged operation")
            if (mission.get("depends_on", []) != list(lot.dependencies)
                    or mission["acceptance"] != list(lot.acceptance)
                    or type(mission.get("external_required", False)) is not bool
                    or mission.get("external_required", False) != lot.external_evidence_required
                    or mission.get("product_decision_required", False) is not False):
                raise ProjectMissionGateError("mission differs from approved lot")
            priority = mission.get("priority", 0)
            if type(priority) is not int or not -1000 <= priority <= 1000:
                raise ProjectMissionGateError("invalid mission priority")
        if mission_ids != set(lots):
            raise ProjectMissionGateError("operational roadmap omits approved lots")
        # Never trust LLM-shaped strings to grant capabilities. The real
        # ControlPlane importer owns transactional persistence and follow-on
        # authorization is enforced by the engine/lease/standing grants.
        return trusted_importer(operations_text, source=source)
