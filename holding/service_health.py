"""Deterministic per-service connectivity state with bounded retry policy."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .github_full_evidence import GitHubEvidenceError


MAX_RETRY_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 300
SUCCESS_THRESHOLD = 2
FAILURE_THRESHOLD = 2
_SERVICES = frozenset({"github", "context7", "firecrawl"})


class ProbeOutcome(str, Enum):
    SUCCESS = "success"
    NETWORK_ERROR = "network_error"
    SERVER_ERROR = "server_error"
    AUTH_ERROR = "auth_error"
    QUOTA_ERROR = "quota_error"


class ServiceStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    PROBING = "PROBING"
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ServiceHealth:
    service: str
    status: ServiceStatus = ServiceStatus.UNKNOWN
    observed_at: int | None = None
    consecutive_successes: int = 0
    consecutive_failures: int = 0
    retry_attempts: int = 0
    retry_not_before: int | None = None
    reason: str | None = None


def initial_service_health(service: str) -> ServiceHealth:
    if service not in _SERVICES:
        raise GitHubEvidenceError("Unsupported external service")
    return ServiceHealth(service=service)


def _backoff(attempt: int) -> int:
    return min(2 ** (attempt - 1), MAX_BACKOFF_SECONDS)


def record_probe(
    previous: ServiceHealth,
    *,
    outcome: ProbeOutcome,
    observed_at: int,
    retry_after: int | None = None,
) -> ServiceHealth:
    """Apply one targeted service observation; never performs the probe itself."""
    if (not isinstance(previous, ServiceHealth)
            or previous.service not in _SERVICES
            or not isinstance(outcome, ProbeOutcome)
            or type(observed_at) is not int
            or observed_at < 0
            or (previous.observed_at is not None and observed_at <= previous.observed_at)
            or (retry_after is not None
                and (type(retry_after) is not int or retry_after <= observed_at))):
        raise GitHubEvidenceError("Invalid or stale service observation")
    if outcome is not ProbeOutcome.QUOTA_ERROR and retry_after is not None:
        raise GitHubEvidenceError("Retry-After is only valid for quota observations")

    if outcome is ProbeOutcome.SUCCESS:
        successes = previous.consecutive_successes + 1
        return ServiceHealth(
            service=previous.service,
            status=(ServiceStatus.ONLINE if successes >= SUCCESS_THRESHOLD
                    else ServiceStatus.PROBING),
            observed_at=observed_at,
            consecutive_successes=successes,
        )

    if outcome in {ProbeOutcome.AUTH_ERROR, ProbeOutcome.QUOTA_ERROR}:
        quota_attempts = min(previous.retry_attempts + 1, MAX_RETRY_ATTEMPTS)
        quota_exhausted = (
            outcome is ProbeOutcome.QUOTA_ERROR
            and quota_attempts >= MAX_RETRY_ATTEMPTS
        )
        reason = (
            "AUTHORIZATION_REQUIRED" if outcome is ProbeOutcome.AUTH_ERROR else
            "QUOTA_RETRY_EXHAUSTED" if quota_exhausted else
            "QUOTA_EXHAUSTED"
        )
        return ServiceHealth(
            service=previous.service,
            status=ServiceStatus.BLOCKED,
            observed_at=observed_at,
            consecutive_failures=previous.consecutive_failures + 1,
            retry_attempts=(previous.retry_attempts if outcome is ProbeOutcome.AUTH_ERROR
                            else quota_attempts),
            retry_not_before=None if quota_exhausted else retry_after,
            reason=reason,
        )

    failures = previous.consecutive_failures + 1
    attempts = min(previous.retry_attempts + 1, MAX_RETRY_ATTEMPTS)
    exhausted = attempts >= MAX_RETRY_ATTEMPTS
    return ServiceHealth(
        service=previous.service,
        status=(ServiceStatus.BLOCKED if exhausted else
                ServiceStatus.DEGRADED if failures < FAILURE_THRESHOLD else
                ServiceStatus.OFFLINE),
        observed_at=observed_at,
        consecutive_failures=failures,
        retry_attempts=attempts,
        retry_not_before=None if exhausted else observed_at + _backoff(attempts),
        reason="RETRY_EXHAUSTED" if exhausted else outcome.value.upper(),
    )


def retry_allowed(health: ServiceHealth, *, now_epoch: int) -> bool:
    if not isinstance(health, ServiceHealth) or type(now_epoch) is not int or now_epoch < 0:
        raise GitHubEvidenceError("Invalid retry decision")
    return (
        health.status is not ServiceStatus.ONLINE
        and health.reason not in {"AUTHORIZATION_REQUIRED", "QUOTA_RETRY_EXHAUSTED"}
        and health.retry_attempts < MAX_RETRY_ATTEMPTS
        and health.retry_not_before is not None
        and now_epoch >= health.retry_not_before
    )
