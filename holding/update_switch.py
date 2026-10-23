"""Crash-recoverable release pointer switch, with health-probe rollback.

This manipulates only metadata within the install root. It never replaces a
running EXE, stops services, runs downloaded code or elevates privileges.
Only the future trusted Windows launcher may consume active-release.json.
A separate authenticated manifest/permission gate must approve its SHA.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Callable

from .update_stage import _MARKER, _RELEASE, _RESERVED, _SHA

_ACTIVE = "active-release.json"
_PENDING = ".atlas-switch.pending.json"
_STATE_SCHEMA = "atlas-active-release/v1"
_PENDING_SCHEMA = "atlas-release-switch/v1"
_MAX_METADATA = 4096


class ReleaseSwitchError(RuntimeError):
    """Refused state transition, failed health probe or unsafe recovery."""


@dataclass(frozen=True, slots=True)
class ReleasePointer:
    release: str
    archive_sha256: str


@dataclass(frozen=True, slots=True)
class ReleaseSwitchResult:
    status: str  # ACTIVATED / ALREADY_ACTIVE
    release: str
    previous_release: str | None


def _read_json(path: Path) -> object | None:
    if path.is_symlink():
        raise ReleaseSwitchError("unsafe symlinked release metadata")
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > _MAX_METADATA:
        raise ReleaseSwitchError("unsafe release metadata")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseSwitchError("duplicate metadata key")
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        if isinstance(exc, ReleaseSwitchError):
            raise
        raise ReleaseSwitchError("unreadable release metadata") from exc


def _valid_release(release: object) -> bool:
    return (
        isinstance(release, str)
        and _RELEASE.fullmatch(release) is not None
        and release not in (".", "..")
        and release[-1] not in " ."
        and release.split(".", 1)[0].upper() not in _RESERVED
    )


def _pointer(raw: object) -> ReleasePointer:
    if (
        not isinstance(raw, dict) or
        set(raw) != {"schema", "release", "archive_sha256"} or
        raw["schema"] != _STATE_SCHEMA or
        not _valid_release(raw["release"]) or
        not isinstance(raw["archive_sha256"], str) or
        _SHA.fullmatch(raw["archive_sha256"]) is None
    ):
        raise ReleaseSwitchError("invalid active release pointer")
    return ReleasePointer(raw["release"], raw["archive_sha256"])


def _payload(pointer: ReleasePointer) -> dict[str, str]:
    return {
        "schema": _STATE_SCHEMA,
        "release": pointer.release,
        "archive_sha256": pointer.archive_sha256,
    }


def _check_root(root: Path) -> None:
    if not isinstance(root, Path) or root.is_symlink() or not root.is_dir():
        raise ReleaseSwitchError("unsafe or missing installation root")
    versions = root / "versions"
    if versions.is_symlink() or not versions.is_dir():
        raise ReleaseSwitchError("unsafe or missing versions directory")


def read_active_release(root: Path) -> ReleasePointer | None:
    """Read a bounded pointer. A missing pointer means no activated version."""
    _check_root(root)
    state = _read_json(root / _ACTIVE)
    return None if state is None else _pointer(state)


def _write_active(root: Path, pointer: ReleasePointer | None) -> None:
    destination = root / _ACTIVE
    if destination.is_symlink():
        raise ReleaseSwitchError("active pointer symlink")
    if pointer is None:
        destination.unlink(missing_ok=True)
        return
    raw = json.dumps(_payload(pointer), sort_keys=True, separators=(",", ":")).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".atlas-active-", dir=root)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        if destination.is_symlink():
            raise ReleaseSwitchError("active pointer changed into symlink")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _verified_slot(root: Path, target: ReleasePointer) -> Path:
    folder = root / "versions" / target.release
    if folder.is_symlink() or not folder.is_dir():
        raise ReleaseSwitchError("staged version is missing or unsafe")
    saved = _read_json(folder / _MARKER)
    if (
        not isinstance(saved, dict)
        or set(saved) != {"schema", "release", "archive_sha256", "files", "unpacked_bytes"}
        or saved["schema"] != "atlas-staged-release/v1"
        or saved["release"] != target.release
        or saved["archive_sha256"] != target.archive_sha256
        or type(saved["files"]) is not int or saved["files"] < 1
        or type(saved["unpacked_bytes"]) is not int or saved["unpacked_bytes"] < 0
    ):
        raise ReleaseSwitchError("staged receipt does not match authorized archive")
    return folder


def _pending_record(root: Path) -> tuple[ReleasePointer | None, ReleasePointer] | None:
    saved = _read_json(root / _PENDING)
    if saved is None:
        return None
    if not isinstance(saved, dict) or set(saved) != {"schema", "previous", "target"}:
        raise ReleaseSwitchError("invalid pending release transaction")
    if saved["schema"] != _PENDING_SCHEMA:
        raise ReleaseSwitchError("unsupported release transaction")
    previous = None if saved["previous"] is None else _pointer(saved["previous"])
    return previous, _pointer(saved["target"])


def _new_pending(root: Path, previous: ReleasePointer | None, target: ReleasePointer) -> None:
    data = {
        "schema": _PENDING_SCHEMA,
        "previous": None if previous is None else _payload(previous),
        "target": _payload(target),
    }
    path = root / _PENDING
    if path.is_symlink():
        raise ReleaseSwitchError("pending transaction symlink")
    # Exclusive creation rejects concurrent attempts. A power interruption
    # halfway through writing leaves a fail-closed journal for manual review.
    with path.open("xb") as file:
        file.write(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        file.flush()
        os.fsync(file.fileno())


def recover_interrupted_switch(root: Path) -> bool:
    """Revert any pending switch without executing or deleting version files.

    Intended for the trusted launcher at startup BEFORE launching the engine.
    Unknown/modified pointers are refused rather than blindly overwritten.
    """
    _check_root(root)
    pending = _pending_record(root)
    if pending is None:
        return False
    previous, target = pending
    observed = read_active_release(root)
    if observed not in (previous, target):
        raise ReleaseSwitchError("pending transaction conflicts with active pointer")
    if observed == target:
        _write_active(root, previous)
    (root / _PENDING).unlink()
    return True


def activate_staged_release(
    root: Path,
    *,
    release: str,
    approved_archive_sha256: str,
    health_probe: Callable[[Path], bool],
) -> ReleaseSwitchResult:
    """Atomically select a staged slot, reverting if a trusted probe fails.

    The external caller must provide a separately AUTHENTICATED SHA, enforce
    single-writer privileges and ensure the running app is stopped as needed.
    The probe itself is supplied by trusted launcher code, never from the ZIP.
    """
    _check_root(root)
    if (
        not _valid_release(release)
        or not isinstance(approved_archive_sha256, str)
        or _SHA.fullmatch(approved_archive_sha256) is None
        or not callable(health_probe)
    ):
        raise ReleaseSwitchError("invalid activation request")
    if _pending_record(root) is not None:
        raise ReleaseSwitchError("interrupted switch must be recovered first")
    target = ReleasePointer(release, approved_archive_sha256)
    slot = _verified_slot(root, target)
    previous = read_active_release(root)
    if previous == target:
        return ReleaseSwitchResult("ALREADY_ACTIVE", release, release)
    _new_pending(root, previous, target)
    try:
        _write_active(root, target)
        if health_probe(slot) is not True:
            raise ReleaseSwitchError("new version failed health probe")
    except Exception as exc:
        observed = read_active_release(root)
        if observed not in (previous, target):
            # Never erase an unexpectedly changed pointer. Preserve journal.
            raise ReleaseSwitchError("active release changed during failed switch") from exc
        if observed == target:
            _write_active(root, previous)
        (root / _PENDING).unlink()
        raise ReleaseSwitchError("activation failed; previous pointer restored") from exc
    (root / _PENDING).unlink()
    return ReleaseSwitchResult(
        "ACTIVATED", release, previous.release if previous else None,
    )
