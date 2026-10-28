"""Deterministic validation of a planner-produced roadmap inventory."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


MAX_ROADMAP_BYTES = 2 * 1024 * 1024
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
_AUTONOMY = frozenset({
    "AUTO", "AUTO_WITH_STANDING_GRANT", "REQUIRES_DECISION", "BLOCKED", "FORBIDDEN",
})
_INITIAL_STATES = frozenset({"TRIAGED", "READY", "REQUIRES_DECISION", "BLOCKED"})


class RoadmapContractError(ValueError):
    pass


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    statement: str


@dataclass(frozen=True)
class Lot:
    lot_id: str
    title: str
    dependencies: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    acceptance: tuple[str, ...]
    autonomy: str
    state: str
    external_evidence_required: bool
    external_review_pending: bool


@dataclass(frozen=True)
class RoadmapPlan:
    repository: str
    snapshot_sha: str
    source_digest: str
    requirements: tuple[Requirement, ...]
    lots: tuple[Lot, ...]
    project_goal: str | None = None

    def initially_ready_lots(self) -> tuple[str, ...]:
        return tuple(lot.lot_id for lot in self.lots if lot.state == "READY")

    def importable_lot_ids(self) -> tuple[str, ...]:
        """Admit only eligible lots whose complete dependency chain is eligible.

        This is planner preflight, NOT an execution grant. Every deferred lot
        remains part of the complete, exact-SHA validated plan.
        """
        eligible = {
            lot.lot_id for lot in self.lots
            if lot.autonomy in {"AUTO", "AUTO_WITH_STANDING_GRANT"}
            and lot.state not in {"BLOCKED", "REQUIRES_DECISION"}
        }
        while True:
            closed = {
                lot.lot_id for lot in self.lots
                if lot.lot_id in eligible
                and all(dep in eligible for dep in lot.dependencies)
            }
            if closed == eligible:
                break
            eligible = closed
        return tuple(lot.lot_id for lot in self.lots if lot.lot_id in eligible)

    def deferred_lot_ids(self) -> tuple[str, ...]:
        """The complete plan's non-importable lots, including dependents."""
        importable = set(self.importable_lot_ids())
        return tuple(lot.lot_id for lot in self.lots if lot.lot_id not in importable)


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RoadmapContractError(f"Unexpected {label} shape")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise RoadmapContractError(f"Invalid {label}")
    return value


def _strings(value: Any, label: str, *, maximum: int) -> tuple[str, ...]:
    if (not isinstance(value, list) or not value or len(value) > maximum
            or any(not isinstance(item, str) or not item or len(item) > 1_000 for item in value)):
        raise RoadmapContractError(f"Invalid {label}")
    return tuple(value)


def validate_roadmap_plan(
    raw: bytes, *, expected_repository: str, expected_snapshot_sha: str,
    expected_source_digest: str,
) -> RoadmapPlan:
    """Validate exhaustive coverage and a cycle-free initial lot graph."""
    if (not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_ROADMAP_BYTES
            or not isinstance(expected_repository, str) or "/" not in expected_repository
            or not isinstance(expected_snapshot_sha, str) or not _SHA.fullmatch(expected_snapshot_sha)
            or not isinstance(expected_source_digest, str) or not _DIGEST.fullmatch(expected_source_digest)):
        raise RoadmapContractError("Invalid trusted roadmap identity")
    try:
        payload = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise RoadmapContractError("Roadmap plan is not valid JSON") from exc
    mandatory = {
        "schema_version", "repository", "snapshot_sha", "source_uri",
        "source_digest", "requirements", "lots",
    }
    if (not isinstance(payload, dict) or not mandatory <= set(payload)
            or set(payload) - mandatory - {"project_goal"}):
        raise RoadmapContractError("Unexpected roadmap plan shape")
    data = payload
    goal = data.get("project_goal")
    if "project_goal" in data and (not isinstance(goal, str)
                                   or not goal.strip()
                                   or len(goal.encode("utf-8")) > 4096):
        raise RoadmapContractError("Invalid bounded project goal")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise RoadmapContractError("Unsupported roadmap plan schema")
    if (data["repository"] != expected_repository
            or data["snapshot_sha"] != expected_snapshot_sha
            or data["source_digest"] != expected_source_digest):
        raise RoadmapContractError("Roadmap plan identity differs from trusted source")
    if not isinstance(data["source_uri"], str) or not data["source_uri"] or len(data["source_uri"]) > 500:
        raise RoadmapContractError("Invalid roadmap source URI")

    if not isinstance(data["requirements"], list) or not 1 <= len(data["requirements"]) <= 5_000:
        raise RoadmapContractError("Invalid bounded requirements")
    requirements = []
    requirement_ids = set()
    for value in data["requirements"]:
        item = _exact(value, {"id", "statement"}, "requirement")
        requirement_id = _identifier(item["id"], "requirement id")
        if requirement_id in requirement_ids:
            raise RoadmapContractError("Duplicate requirement id")
        requirement_ids.add(requirement_id)
        if not isinstance(item["statement"], str) or not item["statement"] or len(item["statement"]) > 2_000:
            raise RoadmapContractError("Invalid requirement statement")
        requirements.append(Requirement(requirement_id, item["statement"]))

    if not isinstance(data["lots"], list) or not 1 <= len(data["lots"]) <= 2_000:
        raise RoadmapContractError("Invalid bounded lot list")
    lots = []
    lot_ids = set()
    for value in data["lots"]:
        item = _exact(value, {
            "id", "title", "dependencies", "requirement_ids", "acceptance",
            "autonomy", "state", "external_evidence_required",
            "external_review_pending",
        }, "lot")
        lot_id = _identifier(item["id"], "lot id")
        if lot_id in lot_ids:
            raise RoadmapContractError("Duplicate lot id")
        lot_ids.add(lot_id)
        if (not isinstance(item["dependencies"], list)
                or len(item["dependencies"]) > 200
                or any(not isinstance(dep, str) or not _ID.fullmatch(dep)
                       for dep in item["dependencies"])):
            raise RoadmapContractError("Invalid lot dependencies")
        dependencies = tuple(item["dependencies"])
        covered = _strings(item["requirement_ids"], "lot requirements", maximum=500)
        acceptance = _strings(item["acceptance"], "lot acceptance", maximum=200)
        if len(set(dependencies)) != len(dependencies) or len(set(covered)) != len(covered):
            raise RoadmapContractError("Duplicate lot relationship")
        if item["autonomy"] not in _AUTONOMY or item["state"] not in _INITIAL_STATES:
            raise RoadmapContractError("Invalid initial lot policy or state")
        if type(item["external_evidence_required"]) is not bool or type(item["external_review_pending"]) is not bool:
            raise RoadmapContractError("Invalid external evidence state")
        if item["external_evidence_required"] != item["external_review_pending"]:
            raise RoadmapContractError("Required external review must start pending")
        if item["state"] == "READY" and (dependencies
                or item["autonomy"] not in {"AUTO", "AUTO_WITH_STANDING_GRANT"}):
            raise RoadmapContractError("Initially READY lot is not executable")
        if item["autonomy"] in {"REQUIRES_DECISION", "BLOCKED", "FORBIDDEN"} \
                and item["state"] == "READY":
            raise RoadmapContractError("Restricted lot cannot start READY")
        title = item["title"]
        if not isinstance(title, str) or not title or len(title) > 300:
            raise RoadmapContractError("Invalid lot title")
        lots.append(Lot(
            lot_id, title, dependencies, covered, acceptance, item["autonomy"],
            item["state"], item["external_evidence_required"],
            item["external_review_pending"],
        ))

    coverage = set()
    graph = {}
    for lot in lots:
        if any(req not in requirement_ids for req in lot.requirement_ids):
            raise RoadmapContractError("Lot references an unknown requirement")
        if lot.lot_id in lot.dependencies or any(dep not in lot_ids for dep in lot.dependencies):
            raise RoadmapContractError("Lot has an invalid dependency")
        coverage.update(lot.requirement_ids)
        graph[lot.lot_id] = lot.dependencies
    if coverage != requirement_ids:
        raise RoadmapContractError("Roadmap requirements are not exhaustively covered")

    remaining = {node: set(dependencies) for node, dependencies in graph.items()}
    resolved = set()
    while remaining:
        ready = sorted(node for node, dependencies in remaining.items()
                       if dependencies.issubset(resolved))
        if not ready:
            raise RoadmapContractError("Roadmap lot graph contains a cycle")
        for node in ready:
            resolved.add(node)
            del remaining[node]

    return RoadmapPlan(
        expected_repository, expected_snapshot_sha, expected_source_digest,
        tuple(requirements), tuple(lots), goal,
    )
