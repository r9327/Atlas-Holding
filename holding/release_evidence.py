"""Fail-closed validation for a future Windows release publication gate."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping


MAX_MANIFEST_BYTES = 512 * 1024
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")


class ReleaseEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseCandidate:
    source_sha: str
    installer_sha256: str
    upstream_shas: tuple[tuple[str, str], ...]
    passed_gates: tuple[str, ...]
    signed: bool
    publishable: bool = True


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ReleaseEvidenceError(f"Invalid {label} shape")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ReleaseEvidenceError(f"Invalid {label} digest")
    return value


def validate_release_manifest(
    raw: bytes,
    *,
    expected_source_sha: str,
    expected_upstream_shas: Mapping[str, str],
    observed_artifacts: Mapping[str, str],
    required_gates: tuple[str, ...],
) -> ReleaseCandidate:
    """Return a publishable candidate only when every trusted input agrees."""
    if (not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_MANIFEST_BYTES
            or not isinstance(expected_source_sha, str)
            or not _SHA.fullmatch(expected_source_sha)
            or type(required_gates) is not tuple or not required_gates):
        raise ReleaseEvidenceError("Invalid trusted release expectation")
    expected_upstream = dict(expected_upstream_shas)
    observed = dict(observed_artifacts)
    if (not expected_upstream or any(
            not isinstance(name, str) or not name or not isinstance(sha, str)
            or not _SHA.fullmatch(sha) for name, sha in expected_upstream.items())
            or any(not isinstance(name, str) or not _ARTIFACT.fullmatch(name)
                   or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
                   for name, digest in observed.items())
            or len(set(required_gates)) != len(required_gates)):
        raise ReleaseEvidenceError("Invalid trusted release evidence")
    try:
        payload = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ReleaseEvidenceError("Release manifest is not valid JSON") from exc
    data = _exact(payload, {
        "schema_version", "source_sha", "platform", "upstream_shas",
        "artifacts", "gates", "signing",
    }, "release manifest")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ReleaseEvidenceError("Unsupported release manifest schema")
    if data["source_sha"] != expected_source_sha or data["platform"] != "windows-real":
        raise ReleaseEvidenceError("Release manifest is not exact-SHA Windows evidence")
    if data["upstream_shas"] != expected_upstream:
        raise ReleaseEvidenceError("Release manifest has stale upstream dependencies")

    artifacts = data["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ReleaseEvidenceError("Release artifacts are missing")
    declared: dict[str, str] = {}
    for value in artifacts:
        item = _exact(value, {"name", "sha256", "size_bytes"}, "artifact")
        name = item["name"]
        if (not isinstance(name, str) or not _ARTIFACT.fullmatch(name)
                or name in declared or type(item["size_bytes"]) is not int
                or not 0 < item["size_bytes"] <= 20 * 1024**3):
            raise ReleaseEvidenceError("Invalid release artifact")
        declared[name] = _digest(item["sha256"], "artifact")
    if declared != observed or "AtlasHoldingSetup.exe" not in declared:
        raise ReleaseEvidenceError("Declared artifacts differ from observed files")

    gates = data["gates"]
    if not isinstance(gates, list):
        raise ReleaseEvidenceError("Release gates are missing")
    passed = []
    for value in gates:
        item = _exact(value, {
            "name", "status", "source_sha", "platform", "evidence_sha256",
        }, "release gate")
        name = item["name"]
        if (not isinstance(name, str) or not name or len(name) > 100
                or name in passed or item["status"] != "PASS"
                or item["source_sha"] != expected_source_sha
                or item["platform"] != "windows-real"):
            raise ReleaseEvidenceError("Release gate is not exact-SHA Windows PASS")
        _digest(item["evidence_sha256"], "gate evidence")
        passed.append(name)
    if set(passed) != set(required_gates):
        raise ReleaseEvidenceError("Required release gates are incomplete")

    signing = _exact(data["signing"], {
        "signed", "signature_evidence_sha256", "smartscreen_warning",
    }, "signing")
    if type(signing["signed"]) is not bool or type(signing["smartscreen_warning"]) is not bool:
        raise ReleaseEvidenceError("Invalid signing declaration")
    if signing["signed"]:
        _digest(signing["signature_evidence_sha256"], "signature evidence")
    elif signing["signature_evidence_sha256"] is not None or not signing["smartscreen_warning"]:
        raise ReleaseEvidenceError("Unsigned installer lacks explicit SmartScreen warning")

    return ReleaseCandidate(
        expected_source_sha, declared["AtlasHoldingSetup.exe"],
        tuple(sorted(expected_upstream.items())), tuple(sorted(passed)),
        signing["signed"],
    )
