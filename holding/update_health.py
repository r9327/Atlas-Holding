"""Compose release switching with authenticated engine IPC health.

The transactional switch stays independent of Control Center transport. This
adapter is the narrow launcher-facing boundary that requires a real bounded,
HMAC-authenticated engine snapshot instead of accepting a spawn or port check.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .dashboard_ipc import DashboardIpcClient, DashboardIpcError
from .update_switch import (
    ReleaseSwitchError, ReleaseSwitchResult, activate_staged_release,
)


def activate_staged_release_after_authenticated_ipc(
    root: Path,
    *,
    release: str,
    approved_archive_sha256: str,
    client_factory: Callable[[Path], DashboardIpcClient],
) -> ReleaseSwitchResult:
    """Activate only after the selected engine answers authenticated IPC.

    ``client_factory`` is trusted launcher code. It is invoked only after the
    active pointer selects ``slot`` and must start that slot with a fresh,
    private dashboard capability. This adapter then performs the actual HMAC
    request and validates the bounded engine snapshot itself; a port-open,
    process-spawn or caller-supplied boolean can never satisfy this gate.

    Executable pinning, ACL validation and coordinated process replacement are
    separate launcher/installer gates. A failed factory or authenticated fetch
    is handled by the existing transaction and restores the previous pointer.
    """
    if not callable(client_factory):
        raise ReleaseSwitchError("authenticated IPC client factory required")

    def authenticated_health(slot: Path) -> bool:
        client = client_factory(slot)
        if not isinstance(client, DashboardIpcClient):
            raise ReleaseSwitchError("authenticated IPC client required")
        try:
            client.fetch()
        except DashboardIpcError as exc:
            raise ReleaseSwitchError(
                "new engine failed authenticated IPC health"
            ) from exc
        return True

    return activate_staged_release(
        root,
        release=release,
        approved_archive_sha256=approved_archive_sha256,
        health_probe=authenticated_health,
    )
