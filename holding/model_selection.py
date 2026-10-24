"""Deterministic local-model routing from independent benchmark evidence."""
from __future__ import annotations

from dataclasses import dataclass
import re


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ROLES = frozenset({"CEO", "ARCHITECT", "CODER", "AUDITOR"})
_PROFILES = frozenset({"BALANCED_VERIFIED", "QUALITY_FIRST"})


@dataclass(frozen=True)
class HardwareBudget:
    ram_bytes: int
    vram_bytes: int
    minimum_context_tokens: int


@dataclass(frozen=True)
class ModelBenchmark:
    model_id: str
    model_digest: str
    role: str
    suite_id: str
    suite_sha256: str
    repository_sha: str
    environment_sha256: str
    evidence_sha256: str
    required_passed: int
    required_total: int
    quality_passed: int
    quality_total: int
    tool_passed: int
    tool_total: int
    critical_failures: int
    security_failures: int
    regressions: int
    median_latency_ms: int
    peak_ram_bytes: int
    peak_vram_bytes: int
    context_tokens: int
    offload_verified: bool
    installed: bool
    source: str


@dataclass(frozen=True)
class ModelSelection:
    status: str
    model_id: str = ""
    model_digest: str = ""
    reason: str = ""
    eligible_models: int = 0


def _positive_integer(value) -> bool:
    return type(value) is int and value > 0


def _valid(result: ModelBenchmark) -> bool:
    if type(result) is not ModelBenchmark:
        return False
    counts = (
        result.required_passed, result.required_total,
        result.quality_passed, result.quality_total,
        result.tool_passed, result.tool_total,
        result.critical_failures, result.security_failures, result.regressions,
    )
    measures = (
        result.median_latency_ms, result.peak_ram_bytes,
        result.context_tokens,
    )
    return bool(
        type(result.model_id) is str and _ID.fullmatch(result.model_id)
        and type(result.model_digest) is str and _DIGEST.fullmatch(result.model_digest)
        and result.role in _ROLES
        and type(result.suite_id) is str and _ID.fullmatch(result.suite_id)
        and type(result.suite_sha256) is str and _DIGEST.fullmatch(result.suite_sha256)
        and type(result.repository_sha) is str and _SHA.fullmatch(result.repository_sha)
        and type(result.environment_sha256) is str and _DIGEST.fullmatch(result.environment_sha256)
        and type(result.evidence_sha256) is str and _DIGEST.fullmatch(result.evidence_sha256)
        and all(type(value) is int and value >= 0 for value in counts)
        and 10 <= result.required_total <= 10_000
        and 20 <= result.quality_total <= 10_000
        and 5 <= result.tool_total <= 10_000
        and result.required_passed <= result.required_total
        and result.quality_passed <= result.quality_total
        and result.tool_passed <= result.tool_total
        and all(_positive_integer(value) for value in measures)
        # A verified CPU-only run legitimately consumes zero GPU VRAM.
        and type(result.peak_vram_bytes) is int
        and result.peak_vram_bytes >= 0
        and type(result.offload_verified) is bool
        and type(result.installed) is bool
        and result.source == "trusted_test_runner"
    )


def select_model(
    results: tuple[ModelBenchmark, ...], *, role: str,
    profile: str = "BALANCED_VERIFIED", budget: HardwareBudget,
    expected_suite_id: str, expected_suite_sha256: str,
    expected_repository_sha: str, expected_environment_sha256: str,
) -> ModelSelection:
    """Select one already-installed model; never download or start a model."""
    if (type(results) is not tuple or not results or len(results) > 200
            or role not in _ROLES or profile not in _PROFILES
            or type(budget) is not HardwareBudget
            or not _positive_integer(budget.ram_bytes)
            or type(budget.vram_bytes) is not int or budget.vram_bytes < 0
            or not _positive_integer(budget.minimum_context_tokens)
            or type(expected_suite_id) is not str
            or not _ID.fullmatch(expected_suite_id)
            or type(expected_suite_sha256) is not str
            or not _DIGEST.fullmatch(expected_suite_sha256)
            or type(expected_repository_sha) is not str
            or not _SHA.fullmatch(expected_repository_sha)
            or type(expected_environment_sha256) is not str
            or not _DIGEST.fullmatch(expected_environment_sha256)):
        return ModelSelection("BLOCKED", reason="Invalid trusted selection context")
    if any(not _valid(result) for result in results):
        return ModelSelection("BLOCKED", reason="Malformed or untrusted benchmark evidence")
    identities = [(item.model_id, item.model_digest, item.role) for item in results]
    if len(set(identities)) != len(identities):
        return ModelSelection("BLOCKED", reason="Duplicate model benchmark identity")
    evidence = [item.evidence_sha256 for item in results]
    if len(set(evidence)) != len(evidence):
        return ModelSelection("BLOCKED", reason="Benchmark evidence reused across models")

    eligible = []
    for item in results:
        comparable = (
            item.role == role
            and item.suite_id == expected_suite_id
            and item.suite_sha256 == expected_suite_sha256
            and item.repository_sha == expected_repository_sha
            and item.environment_sha256 == expected_environment_sha256
        )
        gates = (
            item.installed
            and item.required_passed == item.required_total
            and item.tool_passed == item.tool_total
            and item.critical_failures == 0
            and item.security_failures == 0
            and item.regressions == 0
            and item.peak_ram_bytes <= budget.ram_bytes
            and item.context_tokens >= budget.minimum_context_tokens
            # Offload never excuses measured VRAM above physical capacity. A
            # verified offload run should itself report a compliant peak.
            and item.peak_vram_bytes <= budget.vram_bytes
        )
        if comparable and gates:
            eligible.append(item)
    if not eligible:
        return ModelSelection(
            "BLOCKED", reason="No installed model passed comparable safety and resource gates",
        )

    def quality(item: ModelBenchmark) -> float:
        return item.quality_passed / item.quality_total

    best_quality = max(quality(item) for item in eligible)
    if profile == "QUALITY_FIRST":
        pool = [item for item in eligible if quality(item) == best_quality]
    else:
        # Within two percentage points of the best independently measured
        # quality, prefer lower latency and resource use.
        pool = [item for item in eligible if quality(item) >= best_quality - 0.02]
    chosen = min(pool, key=lambda item: (
        item.median_latency_ms,
        item.peak_vram_bytes + item.peak_ram_bytes,
        item.model_id,
        item.model_digest,
    ))
    return ModelSelection(
        "SELECTED", chosen.model_id, chosen.model_digest,
        f"{profile}: independent comparable benchmark and resource gates passed",
        len(eligible),
    )
