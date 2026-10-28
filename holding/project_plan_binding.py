"""Fail-closed project/roadmap binding for the Holding planning boundary.

This adapter verifies the selected persistent profile and an exact-SHA
planner-produced roadmap. It never dispatches a mission, executes profile
commands, grants Git permissions or creates a worktree. The engine must still
perform its independent authority and resource checks before scheduling.
"""
from __future__ import annotations

from dataclasses import dataclass

from .project_registry import ProjectRegistry, ProjectRegistryError
from .roadmap_contract import (
    RoadmapContractError, RoadmapPlan, validate_roadmap_plan,
)


class ProjectPlanBindingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProjectPlanBinding:
    profile_name: str
    profile_digest: str
    repository: str
    branch: str
    checkout_dir: str
    snapshot_sha: str
    source_digest: str
    lot_ids: tuple[str, ...]
    initially_ready_lots: tuple[str, ...]
    status: str = "PREFLIGHT_ONLY"
    importable_lots: tuple[str, ...] = ()
    deferred_lots: tuple[str, ...] = ()


def bind_selected_project_plan(
    registry: ProjectRegistry,
    raw: bytes,
    *,
    expected_profile_digest: str,
    expected_snapshot_sha: str,
    expected_source_digest: str,
) -> ProjectPlanBinding:
    """Verify current selection has NOT changed since the user selected it.

    The repository is obtained only from the trusted profile, never from
    untrusted plan contents. Even if a roadmap is otherwise valid, a plan for
    another repository must be refused rather than silently switching tasks.
    """
    if not isinstance(registry, ProjectRegistry):
        raise ProjectPlanBindingError("trusted project registry required")
    if (not isinstance(expected_profile_digest, str) or
            len(expected_profile_digest) != 64 or
            any(c not in "0123456789abcdef" for c in expected_profile_digest)):
        raise ProjectPlanBindingError("invalid selected project digest")
    try:
        entry = registry.selected()
    except (ProjectRegistryError, OSError) as exc:
        raise ProjectPlanBindingError("selected profile cannot be verified") from exc
    if entry is None:
        raise ProjectPlanBindingError("no project explicitly selected")
    if entry.digest != expected_profile_digest:
        raise ProjectPlanBindingError("selected project changed after approval")
    try:
        plan: RoadmapPlan = validate_roadmap_plan(
            raw,
            expected_repository=entry.profile.repository,
            expected_snapshot_sha=expected_snapshot_sha,
            expected_source_digest=expected_source_digest,
        )
    except RoadmapContractError as exc:
        raise ProjectPlanBindingError("roadmap rejected for selected project") from exc
    return ProjectPlanBinding(
        profile_name=entry.profile.name,
        profile_digest=entry.digest,
        repository=entry.profile.repository,
        branch=entry.profile.branch,
        checkout_dir=entry.profile.checkout_dir,
        snapshot_sha=plan.snapshot_sha,
        source_digest=plan.source_digest,
        lot_ids=tuple(lot.lot_id for lot in plan.lots),
        initially_ready_lots=plan.initially_ready_lots(),
        importable_lots=plan.importable_lot_ids(),
        deferred_lots=plan.deferred_lot_ids(),
    )
