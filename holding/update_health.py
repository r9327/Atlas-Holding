"""Compose release switching with authenticated engine IPC health.

The transactional switch stays independent of Control Center transport. This
adapter is the narrow launcher-facing boundary that requires a real bounded,
HMAC-authenticated engine snapshot instead of accepting a spawn or port check.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .dashboard_ipc import DashboardIpcClient, DashboardIpcError
from .update_switch import (
    ReleaseSwitchError, ReleaseSwitchResult, activate_staged_release,
)


@dataclass(frozen=True, slots=True)
class UpdateEngineSession:
    """Candidate engine capability and its mandatory trusted cleanup."""

    client: DashboardIpcClient
    close: Callable[[], None]


@dataclass(frozen=True, slots=True)
class AuthenticatedReleaseActivation:
    switch: ReleaseSwitchResult
    session: UpdateEngineSession


class CandidateCleanupError(ReleaseSwitchError):
    """Candidate stop was ambiguous; another engine must not be started."""


def activate_staged_release_after_authenticated_ipc(
    root: Path,
    *,
    release: str,
    approved_archive_sha256: str,
    session_factory: Callable[[Path], UpdateEngineSession],
) -> AuthenticatedReleaseActivation:
    """Activate only after the selected engine answers authenticated IPC.

    ``session_factory`` is trusted launcher code. It is invoked only after the
    active pointer selects ``slot`` and must start that slot with a fresh,
    private dashboard capability plus a cleanup callback. This adapter then
    performs the actual HMAC request and validates the bounded engine snapshot
    itself; a port-open, process-spawn or caller-supplied boolean can never
    satisfy this gate. Failed candidates are closed before pointer rollback.

    Executable pinning, ACL validation and coordinated process replacement are
    separate launcher/installer gates. A failed factory or authenticated fetch
    is handled by the existing transaction and restores the previous pointer.
    """
    if not callable(session_factory):
        raise ReleaseSwitchError("authenticated engine session factory required")

    candidate: UpdateEngineSession | None = None
    cleanup_attempted = False
    cleanup_failure: CandidateCleanupError | None = None

    def close_candidate() -> None:
        nonlocal cleanup_attempted, cleanup_failure
        if candidate is None or cleanup_attempted:
            return
        # Cleanup is at-most-once: retrying an ambiguous process-control
        # operation could target a recycled PID or mask the first failure.
        cleanup_attempted = True
        try:
            candidate.close()
        except Exception as exc:
            cleanup_failure = CandidateCleanupError(
                "candidate engine cleanup failed; inspect running processes"
            )
            raise cleanup_failure from exc

    def authenticated_health(slot: Path) -> bool:
        nonlocal candidate
        candidate = session_factory(slot)
        if (not isinstance(candidate, UpdateEngineSession)
                or not isinstance(candidate.client, DashboardIpcClient)
                or not callable(candidate.close)):
            candidate = None
            raise ReleaseSwitchError("authenticated engine session required")
        try:
            candidate.client.fetch()
        except DashboardIpcError as exc:
            try:
                close_candidate()
            except ReleaseSwitchError as cleanup:
                raise cleanup from exc
            raise ReleaseSwitchError("new engine failed authenticated IPC health") from exc
        return True

    try:
        switched = activate_staged_release(
            root,
            release=release,
            approved_archive_sha256=approved_archive_sha256,
            health_probe=authenticated_health,
        )
    except BaseException as exc:
        try:
            close_candidate()
        except ReleaseSwitchError:
            pass
        if cleanup_failure is not None:
            raise cleanup_failure from exc
        raise
    if candidate is None:
        raise ReleaseSwitchError("candidate engine session unavailable")
    return AuthenticatedReleaseActivation(switched, candidate)
