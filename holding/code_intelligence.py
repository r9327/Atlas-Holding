"""Fail-closed policy for optional, advisory-only code intelligence."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable


MAX_REPORT_BYTES = 4 * 1024 * 1024
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
SERENA_READ_TOOLS = frozenset({
    "find_symbol", "find_referencing_symbols", "get_symbols_overview",
    "find_file", "search_for_pattern", "find_declaration",
    "find_implementing_symbols", "get_diagnostics_for_file",
})
SERENA_CORE_TOOLS = frozenset({
    "find_symbol", "find_referencing_symbols", "get_symbols_overview",
})


class CodeIntelligenceError(ValueError):
    pass


@dataclass(frozen=True)
class SerenaAvailability:
    status: str
    version: str | None
    exposed_tools: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class SemgrepInvocation:
    argv: tuple[str, ...]
    rules_sha256: str
    timeout_seconds: int
    advisory_only: bool = True


@dataclass(frozen=True)
class SemgrepFinding:
    rule_id: str
    path: str
    start_line: int
    message: str


@dataclass(frozen=True)
class SemgrepAdvisory:
    status: str
    findings: tuple[SemgrepFinding, ...]
    report_sha256: str
    can_certify: bool = False
    can_promote_mission: bool = False


def _local_path(value: Any) -> bool:
    return (isinstance(value, str) and bool(_SAFE_PATH.fullmatch(value))
            and all(part not in {".", ".."} for part in value.split("/")))


def discover_serena(
    *, installed: bool, version: str | None,
    advertised_tools: Iterable[str], permission: str,
) -> SerenaAvailability:
    """Expose only known symbolic read tools from an observed Serena server."""
    if type(installed) is not bool or permission not in {"READ", "NONE"}:
        raise CodeIntelligenceError("Invalid Serena discovery evidence")
    tools = tuple(advertised_tools)
    if any(not isinstance(tool, str) or not tool or len(tool) > 100 for tool in tools):
        raise CodeIntelligenceError("Invalid Serena tool advertisement")
    if len(set(tools)) != len(tools) or len(tools) > 200:
        raise CodeIntelligenceError("Invalid Serena tool advertisement")
    if not installed:
        if version is not None or tools:
            raise CodeIntelligenceError("Unavailable Serena has contradictory evidence")
        return SerenaAvailability("UNAVAILABLE", None, (), "not-installed")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise CodeIntelligenceError("Installed Serena lacks a valid version")
    exposed = tuple(sorted(SERENA_READ_TOOLS.intersection(tools)))
    if permission != "READ":
        return SerenaAvailability("BLOCKED", version, (), "read-permission-denied")
    if not SERENA_CORE_TOOLS.issubset(exposed):
        return SerenaAvailability("BLOCKED", version, exposed, "core-tools-missing")
    return SerenaAvailability("READY", version, exposed, "read-only-allowlist")


def prepare_semgrep_scan(
    *, executable: str, rules_path: str, rules_sha256: str,
    target_path: str, timeout_seconds: int,
) -> SemgrepInvocation:
    """Build an offline CE scan request; a supervisor still owns execution."""
    if (executable not in {"semgrep", "semgrep.exe"}
            or not _local_path(rules_path)
            or not rules_path.endswith((".yml", ".yaml"))
            or not _local_path(target_path)
            or not isinstance(rules_sha256, str) or not _DIGEST.fullmatch(rules_sha256)
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600):
        raise CodeIntelligenceError("Invalid bounded Semgrep scan request")
    return SemgrepInvocation((
        executable, "scan", "--config", rules_path, "--json", "--metrics=off",
        "--disable-version-check", target_path,
    ), rules_sha256, timeout_seconds)


def consume_semgrep_report(
    raw: bytes, *, observed_exit_code: int,
    expected_rules_sha256: str, observed_rules_sha256: str,
) -> SemgrepAdvisory:
    """Parse bounded Semgrep JSON as advice, never as CI or certification."""
    if (not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_REPORT_BYTES
            or type(observed_exit_code) is not int
            or not isinstance(expected_rules_sha256, str)
            or not _DIGEST.fullmatch(expected_rules_sha256)
            or observed_rules_sha256 != expected_rules_sha256):
        raise CodeIntelligenceError("Invalid Semgrep process evidence")
    try:
        payload = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise CodeIntelligenceError("Semgrep report is not valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"results", "errors"}:
        raise CodeIntelligenceError("Unexpected Semgrep report shape")
    if observed_exit_code not in {0, 1}:
        raise CodeIntelligenceError("Semgrep execution failed")
    if not isinstance(payload["errors"], list) or payload["errors"]:
        raise CodeIntelligenceError("Semgrep report contains scan errors")
    if not isinstance(payload["results"], list) or len(payload["results"]) > 1_000:
        raise CodeIntelligenceError("Invalid bounded Semgrep findings")
    findings = []
    for value in payload["results"]:
        if not isinstance(value, dict) or set(value) != {"check_id", "path", "start", "extra"}:
            raise CodeIntelligenceError("Unexpected Semgrep finding shape")
        start, extra = value["start"], value["extra"]
        if (not isinstance(start, dict) or set(start) != {"line"}
                or type(start["line"]) is not int or not 1 <= start["line"] <= 10_000_000
                or not isinstance(extra, dict) or set(extra) != {"message"}
                or not isinstance(value["check_id"], str) or not value["check_id"]
                or len(value["check_id"]) > 200
                or not _local_path(value["path"])
                or not isinstance(extra["message"], str) or not extra["message"]
                or len(extra["message"]) > 2_000):
            raise CodeIntelligenceError("Invalid Semgrep finding")
        findings.append(SemgrepFinding(
            value["check_id"], value["path"], start["line"], extra["message"],
        ))
    return SemgrepAdvisory(
        "ADVISORY_FINDINGS" if findings else "ADVISORY_CLEAR",
        tuple(findings), hashlib.sha256(raw).hexdigest(),
    )
