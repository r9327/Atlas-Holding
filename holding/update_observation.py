"""Read-only, fail-closed local update observations for Control Center.

A staged receipt is local *metadata*, not a signed release, executable
integrity proof or certification. No network, file mutations, subprocesses or
engine authorization are performed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .update_switch import (
    ReleaseSwitchError, _pending_record, _verified_slot, read_active_release,
)


@dataclass(frozen=True, slots=True)
class LocalUpdateObservation:
    status: str
    release: str | None
    archive_sha256: str | None


def observe_local_update(install_root: Path | None) -> LocalUpdateObservation:
    """Inspect an explicitly selected local installation root without writes.

    Statuses are display-only. The signed release manifest, runtime file hashes,
    ACLs, process liveness and independent quality certification are NOT checked.
    """
    if install_root is None:
        return LocalUpdateObservation("NOT_CONFIGURED", None, None)
    if not isinstance(install_root, Path):
        raise ValueError("install_root must be a Path or None")
    try:
        # A pending switch must NEVER be presented as a healthy active update.
        pending = _pending_record(install_root) if (
            install_root.is_dir() and not install_root.is_symlink()
        ) else None
        if pending is not None:
            return LocalUpdateObservation(
                "RECOVERY_REQUIRED", None, None,
            )
        current = read_active_release(install_root)
        if current is None:
            return LocalUpdateObservation("NO_ACTIVE_POINTER", None, None)
        _verified_slot(install_root, current)
        return LocalUpdateObservation(
            "LOCAL_RECEIPT_ONLY", current.release, current.archive_sha256,
        )
    except (ReleaseSwitchError, OSError, ValueError):
        return LocalUpdateObservation("UNVERIFIED_LOCAL_STATE", None, None)
