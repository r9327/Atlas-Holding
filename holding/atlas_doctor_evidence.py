"""Bounded consumer of Dofus Atlas' repository-owned Doctor agent protocol.

This adapter does not run Doctor and cannot certify a release. A trusted
supervisor must supply the process exit code and expected invocation identity.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any


PROTOCOL = "atlas-doctor-agent/v1"
MAX_REPORT_BYTES = 1024 * 1024
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ACTIONS = frozenset({"plan", "check", "validate"})
_STATUSES = frozenset({"READY", "PASS", "REVIEW", "BLOCKED", "FAIL"})
_EXIT = {"READY": 0, "PASS": 0, "REVIEW": 1, "BLOCKED": 2, "FAIL": 2}


class DoctorEvidenceError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise DoctorEvidenceError("Doctor report contains duplicate JSON keys")
        result[key] = value
    return result


@dataclass(frozen=True)
class LocalPassport:
    candidate_sha: str
    action: str
    status: str
    report_sha256: str
    dirty: bool
    dirty_digest: str
    graph_required: bool
    graph_status: str
    tests_executed: tuple[str, ...]
    certified_full: bool = False


def _safe_paths(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 80:
        raise DoctorEvidenceError("Invalid Doctor path scope")
    result = []
    for path in value:
        if (not isinstance(path, str) or not path or len(path) > 240
                or path.startswith("-") or ":" in path or "\x00" in path
                or path.startswith(("/", "\\")) or "\\" in path
                or any(part in ("", ".", "..") for part in path.split("/"))):
            raise DoctorEvidenceError("Unsafe Doctor path scope")
        result.append(path)
    if len(set(result)) != len(result):
        raise DoctorEvidenceError("Duplicate Doctor path scope")
    return tuple(result)


def _strings(value: Any, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > limit:
        raise DoctorEvidenceError("Invalid bounded Doctor string list")
    if any(not isinstance(item, str) or not item or len(item) > 500 for item in value):
        raise DoctorEvidenceError("Invalid Doctor string evidence")
    return tuple(value)


def consume_doctor_report(
    raw: bytes,
    *,
    observed_exit_code: int,
    expected_sha: str,
    expected_action: str,
    expected_base_ref: str,
    expected_paths: tuple[str, ...],
    expected_dirty: bool,
    expected_dirty_digest: str,
    structural: bool,
) -> LocalPassport:
    """Validate one local Doctor envelope without upgrading it to FULL."""
    if (not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_REPORT_BYTES
            or type(observed_exit_code) is not int
            or not isinstance(expected_sha, str) or not _SHA.fullmatch(expected_sha)
            or expected_action not in _ACTIONS or type(expected_paths) is not tuple
            or not isinstance(expected_base_ref, str) or not expected_base_ref
            or len(expected_base_ref) > 200
            or type(expected_dirty) is not bool
            or not isinstance(expected_dirty_digest, str)
            or (expected_dirty and not _DIGEST.fullmatch(expected_dirty_digest))
            or (not expected_dirty and expected_dirty_digest != "")
            or type(structural) is not bool):
        raise DoctorEvidenceError("Invalid trusted Doctor invocation identity")
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
    except DoctorEvidenceError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise DoctorEvidenceError("Doctor report is not valid JSON") from exc
    if (not isinstance(payload, dict)
            or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != 1):
        raise DoctorEvidenceError("Unsupported Doctor report schema")
    if payload.get("protocol") != PROTOCOL or payload.get("action") != expected_action:
        raise DoctorEvidenceError("Wrong Doctor protocol or action")
    status = payload.get("status")
    if (status not in _STATUSES
            or type(payload.get("exit_code")) is not int
            or payload.get("exit_code") != _EXIT[status]):
        raise DoctorEvidenceError("Inconsistent Doctor status and declared exit code")
    if observed_exit_code != _EXIT[status]:
        raise DoctorEvidenceError("Doctor process exit code disagrees with report")
    if payload.get("exact_sha") != expected_sha:
        raise DoctorEvidenceError("Doctor report belongs to a different SHA")
    if payload.get("base_ref") != expected_base_ref:
        raise DoctorEvidenceError("Doctor report belongs to a different base ref")

    worktree = payload.get("worktree")
    if not isinstance(worktree, dict) or type(worktree.get("dirty")) is not bool:
        raise DoctorEvidenceError("Missing Doctor worktree identity")
    dirty = worktree["dirty"]
    dirty_digest = worktree.get("dirty_digest")
    if (not isinstance(dirty_digest, str)
            or (dirty and not _DIGEST.fullmatch(dirty_digest))
            or (not dirty and dirty_digest != "")):
        raise DoctorEvidenceError("Missing exact dirty worktree digest")
    if dirty != expected_dirty or dirty_digest != expected_dirty_digest:
        raise DoctorEvidenceError("Doctor report belongs to a different worktree state")
    if expected_action == "plan" and dirty:
        raise DoctorEvidenceError("Planning requires a clean starting worktree")

    contract = payload.get("edit_contract")
    if not isinstance(contract, dict):
        raise DoctorEvidenceError("Missing Doctor edit contract")
    requested = _safe_paths(contract.get("requested_paths"))
    if requested != _safe_paths(list(expected_paths)):
        raise DoctorEvidenceError("Doctor report path scope differs from invocation")
    planned = _safe_paths(contract.get("planned_paths"))
    if not set(requested).issubset(planned):
        raise DoctorEvidenceError("Doctor plan omits requested paths")
    if contract.get("expansion_requires_replan") is not True:
        raise DoctorEvidenceError("Doctor path expansion is not fail-closed")

    checks = payload.get("selected_checks")
    if not isinstance(checks, dict) or type(checks.get("graph_required")) is not bool:
        raise DoctorEvidenceError("Missing Doctor selected checks")
    graph_required = checks["graph_required"]
    graph_status = checks.get("graph_status")
    if not isinstance(graph_status, str):
        raise DoctorEvidenceError("Missing Graphify status")
    _strings(checks.get("tests"), limit=40)
    _strings(checks.get("rules"), limit=30)
    if structural and (not graph_required or graph_status != "PASS"):
        raise DoctorEvidenceError("Structural work lacks exact-SHA Graphify evidence")

    certification = payload.get("certification")
    if (not isinstance(certification, dict)
            or certification.get("full_certified") is not False
            or certification.get("release_certified") is not False
            or certification.get("exact_sha_required") is not True):
        raise DoctorEvidenceError("Local Doctor report made an invalid certification claim")
    certification_status = certification.get("status")
    expected_certification = (
        "SCOPED_VALIDATED"
        if expected_action == "validate" and status == "PASS"
        else "NOT_RUN"
    )
    if certification_status != expected_certification:
        raise DoctorEvidenceError("Inconsistent local certification status")

    tests_executed: tuple[str, ...] = ()
    if expected_action in {"check", "validate"}:
        execution = payload.get("execution")
        if not isinstance(execution, dict):
            raise DoctorEvidenceError("Missing Doctor execution evidence")
        reported_tests = execution.get("tests_executed")
        if reported_tests is False and status != "PASS":
            # Real Atlas development_preflight sends a BOOLEAN for this field.
            # False may prove NO tests ran, but True cannot identify which
            # modules ran and MUST NOT satisfy a local PASS.
            tests_executed = ()
        else:
            tests_executed = _strings(reported_tests, limit=100)
        if status == "PASS" and (
                execution.get("status") not in {"PASS", "FOCUSED_CHECKED"}
                or not tests_executed):
            raise DoctorEvidenceError("Doctor PASS lacks successful named test execution evidence")

    local_status = {
        ("plan", "READY"): "PLANNED_LOCAL",
        ("check", "PASS"): "CHECKED_LOCAL",
        ("validate", "PASS"): "VALIDATED_LOCAL",
    }.get((expected_action, status), status)
    return LocalPassport(
        expected_sha, expected_action, local_status, hashlib.sha256(raw).hexdigest(),
        dirty, dirty_digest, graph_required, graph_status, tests_executed,
    )
