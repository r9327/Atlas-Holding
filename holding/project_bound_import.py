"""Fail-closed bridge from a selected Holding project to real RoadmapImporter.

A planner inventory and an executable mission manifest are distinct inputs:
only explicit, autonomous, dependency-free lots may be imported as READY.
The existing runtime importer owns the SQLite transaction, permissions and
later execution. This adapter imports nothing if validation fails.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol

from .project_plan_binding import (
    ProjectPlanBindingError, bind_selected_project_plan,
)
from .project_registry import ProjectRegistry, _registry_lock
from .roadmap_contract import RoadmapContractError, validate_roadmap_plan

_ALLOWED_OPERATIONS = frozenset({"plan", "code", "local_test", "read_ci"})
_MAX_MISSIONS = 2000


class BoundImportError(ValueError):
    """The selected project does not authorize this exact mission import."""


class RoadmapWriter(Protocol):
    def import_text(self, raw: str, *, source: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class BoundImportReport:
    project: str
    repository: str
    profile_digest: str
    snapshot_sha: str
    imported_lot_ids: tuple[str, ...]
    deferred_lot_ids: tuple[str, ...]
    roadmap_version: int | None
    status: str


def import_eligible_project_missions(
    registry: ProjectRegistry,
    *,
    inventory: bytes,
    mission_manifest: bytes,
    expected_profile_digest: str,
    expected_snapshot_sha: str,
    expected_source_digest: str,
    runtime_repository: str,
    writer: RoadmapWriter,
    source: str,
) -> BoundImportReport:
    """Import ONLY planned, explicitly safe initial missions into real SQLite.

    Caller must authenticate runtime_repository, source SHA and the writer
    independently (e.g. installed service config + checked-out Git HEAD).
    No grants are issued here. Any later privileged operation remains subject
    to the existing Holding authority and Doctor/CI checks.
    """
    if not isinstance(registry, ProjectRegistry):
        raise BoundImportError("expected trusted registry")
    if not isinstance(runtime_repository, str) or not runtime_repository:
        raise BoundImportError("runtime repository is required")
    if not isinstance(source, str) or not source.strip() or len(source) > 500:
        raise BoundImportError("trusted source description required")
    if not callable(getattr(writer, "import_text", None)):
        raise BoundImportError("trusted RoadmapImporter required")
    if (type(mission_manifest) is not bytes or not 1 <= len(mission_manifest) <= 2 * 1024 * 1024):
        raise BoundImportError("bounded mission manifest required")

    # A concurrent profile selection or update cannot slip between the
    # exact digest check and the atomic mission import.
    with _registry_lock(registry.directory):
        try:
            binding = bind_selected_project_plan(
                registry, inventory,
                expected_profile_digest=expected_profile_digest,
                expected_snapshot_sha=expected_snapshot_sha,
                expected_source_digest=expected_source_digest,
            )
            plan = validate_roadmap_plan(
                inventory,
                expected_repository=binding.repository,
                expected_snapshot_sha=expected_snapshot_sha,
                expected_source_digest=expected_source_digest,
            )
        except (ProjectPlanBindingError, RoadmapContractError) as exc:
            raise BoundImportError("selected project or plan changed") from exc
        if binding.repository != runtime_repository:
            raise BoundImportError("selected project differs from installed engine repository")

        # The real RoadmapImporter initially persists all imported missions
        # as READY. Never import anything with unresolved dependencies,
        # external review, privileged autonomy, or user decision pending.
        eligible = {
            lot.lot_id: lot for lot in plan.lots
            if lot.state == "READY"
            and lot.autonomy == "AUTO"
            and not lot.dependencies
            and not lot.external_evidence_required
            and not lot.external_review_pending
        }
        try:
            doc = json.loads(
                mission_manifest.decode("utf-8"),
                object_pairs_hook=_unique,
                parse_constant=lambda constant: (_ for _ in ()).throw(ValueError(constant)),
            )
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise BoundImportError("mission manifest is not valid JSON") from exc
        if (not isinstance(doc, dict) or set(doc) != {"repo", "missions"}
                or doc["repo"] != binding.repository
                or not isinstance(doc["missions"], list)
                or len(doc["missions"]) > _MAX_MISSIONS):
            raise BoundImportError("mission manifest has wrong repository or shape")
        entries = {}
        for item in doc["missions"]:
            if not isinstance(item, dict) or set(item) - {
                    "id", "operation", "acceptance", "depends_on", "priority",
                    "external_required", "product_decision_required"}:
                raise BoundImportError("unexpected mission fields")
            ident = item.get("id")
            if not isinstance(ident, str) or ident not in eligible or ident in entries:
                raise BoundImportError("mission not eligible, missing or duplicated")
            lot = eligible[ident]
            if (item.get("operation") not in _ALLOWED_OPERATIONS
                    or item.get("acceptance") != list(lot.acceptance)
                    or item.get("depends_on", []) != []
                    or item.get("external_required", False) is not False
                    or item.get("product_decision_required", False) is not False):
                raise BoundImportError("mission differs from selected planned lot")
            priority = item.get("priority", 0)
            if type(priority) is not int or not -1000 <= priority <= 1000:
                raise BoundImportError("invalid mission priority")
            entries[ident] = item
        if set(entries) != set(eligible):
            raise BoundImportError("all safe initial lots must be represented")
        deferred = tuple(sorted(set(binding.lot_ids) - set(eligible)))
        if not eligible:
            return BoundImportReport(
                binding.profile_name, binding.repository, binding.profile_digest,
                binding.snapshot_sha, (), deferred, None, "NO_ELIGIBLE_MISSIONS",
            )
        # Re-emit the already validated exact values; never pass raw
        # JSON with duplicate fields to the execution-facing importer.
        canonical = json.dumps(
            {"repo": binding.repository,
             "missions": [entries[ident] for ident in sorted(entries)]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        result = writer.import_text(
            canonical, source=f"project:{binding.profile_name}:sha:{binding.snapshot_sha}:{source}",
        )
        return BoundImportReport(
            binding.profile_name, binding.repository, binding.profile_digest,
            binding.snapshot_sha, tuple(sorted(entries)), deferred,
            result.version, "IMPORTED_READY_ONLY",
        )


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise BoundImportError("duplicate mission manifest JSON key")
        result[key] = value
    return result
