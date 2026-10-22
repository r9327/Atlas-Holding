"""Persist authenticated GitHub connectivity without exposing credentials."""
from __future__ import annotations

import re
import time
from typing import Callable, Protocol

from .daemon_lease import DaemonLease
from .service_health import (
    ProbeOutcome, ServiceHealth, ServiceStatus, initial_service_health,
    record_probe,
)
from .state_store import StateStore


MAX_HEALTH_AGE_SECONDS = 60
_ACCOUNT = re.compile(
    r"^github:[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


class AccountProbe(Protocol):
    def observe_account(self) -> str: ...


def _from_row(row: dict | None) -> ServiceHealth:
    if row is None:
        return initial_service_health("github")
    return ServiceHealth(
        service="github", status=ServiceStatus(row["status"]),
        observed_at=row["observed_at"],
        consecutive_successes=row["consecutive_successes"],
        consecutive_failures=row["consecutive_failures"],
        retry_attempts=row["retry_attempts"],
        retry_not_before=row["retry_not_before"], reason=row["reason"])


class GitHubHealthMonitor:
    def __init__(
        self, store: StateStore, lease: DaemonLease, probe: AccountProbe, *,
        expected_account_id: str, clock: Callable[[], float] = time.time,
    ):
        if not _ACCOUNT.fullmatch(expected_account_id) or not callable(clock):
            raise ValueError("Invalid GitHub health monitor configuration")
        self.store = store
        self.lease = lease
        self.probe = probe
        self.expected_account_id = expected_account_id
        self.clock = clock

    def online(self) -> bool:
        row = self.store.service_health("github")
        now = int(self.clock())
        return bool(
            row is not None and row["status"] == "ONLINE"
            and type(row["observed_at"]) is int
            and row["observed_at"] <= now + 5
            and now - row["observed_at"] <= MAX_HEALTH_AGE_SECONDS)

    def refresh(self) -> ServiceHealth:
        self.lease.assert_owned()
        previous = _from_row(self.store.service_health("github"))
        observed_at = int(self.clock())
        if previous.observed_at is not None and observed_at <= previous.observed_at:
            return previous
        if (previous.retry_not_before is not None
                and observed_at < previous.retry_not_before):
            return previous
        if (previous.status is ServiceStatus.BLOCKED
                and previous.reason in {
                    "AUTHORIZATION_REQUIRED", "QUOTA_RETRY_EXHAUSTED",
                    "RETRY_EXHAUSTED"}):
            return previous
        try:
            account_id = self.probe.observe_account()
            outcome = (ProbeOutcome.SUCCESS
                       if account_id == self.expected_account_id
                       else ProbeOutcome.AUTH_ERROR)
        except Exception as exc:
            outcome = (
                ProbeOutcome.AUTH_ERROR
                if getattr(exc, "code", None) == "REJECTED"
                else ProbeOutcome.NETWORK_ERROR)
        current = record_probe(
            previous, outcome=outcome, observed_at=observed_at)
        self.store.save_service_health(
            service="github", status=current.status.value,
            observed_at=current.observed_at,
            consecutive_successes=current.consecutive_successes,
            consecutive_failures=current.consecutive_failures,
            retry_attempts=current.retry_attempts,
            retry_not_before=current.retry_not_before,
            reason=current.reason, updated_epoch=observed_at,
            lease_owner=self.lease.owner)
        return current
