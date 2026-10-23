"""Materialize a staged Windows engine slot from authenticated release pins.

The release ZIP never chooses installation-specific absolute paths. A verified
installed config is copied and only its dashboard binary binding is advanced;
the resulting config is included in the staged-slot v2 content digest before
the slot is published.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable

from .dashboard_ipc import DashboardIpcError
from .update_health import (
    AuthenticatedReleaseActivation, CandidateCleanupError,
    UpdateEngineSession, activate_staged_release_after_authenticated_ipc,
)
from .update_stage import (
    _RELEASE, _RESERVED, ReleaseStagingError, StagedRelease,
    stage_release_archive,
)
from .windows_runtime_launch import (
    InstalledEngineSpec, RuntimeLaunchError, _read_config, _sha,
    _unique_json, _validate_paths, launch_installed_engine_health_session,
)
from .windows_runtime_payload import RuntimePayloadError, verify_runtime_payload

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_MANIFEST_BYTES = 4096


@dataclass(frozen=True, slots=True)
class EngineReleasePins:
    release: str
    archive_sha256: str
    archive_bytes: int
    viewer_sha256: str
    engine_sha256: str


@dataclass(frozen=True, slots=True)
class StagedEngineRelease:
    staged: StagedRelease
    launch_spec: InstalledEngineSpec


@dataclass(frozen=True, slots=True)
class EngineReplacementResult:
    status: str  # ACTIVATED / ROLLED_BACK_RESTARTED
    session: UpdateEngineSession
    activation: AuthenticatedReleaseActivation | None
    failure: str | None = None


class EngineReplacementError(RuntimeError):
    """Process ownership is ambiguous or the previous engine could not restart."""


def _unique_manifest(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseStagingError("duplicate engine release manifest key")
        result[key] = value
    return result


def read_pinned_engine_release_manifest(
    manifest: Path, *, expected_sha256: str,
) -> EngineReleasePins:
    """Read an exact manifest whose digest came from the trusted launcher."""
    if (not isinstance(manifest, Path) or not manifest.is_absolute()
            or manifest.is_symlink() or not manifest.is_file()
            or not isinstance(expected_sha256, str)
            or _SHA.fullmatch(expected_sha256) is None):
        raise ReleaseStagingError("pinned engine release manifest required")
    try:
        with manifest.open("rb") as source:
            raw = source.read(_MAX_MANIFEST_BYTES + 1)
    except OSError as exc:
        raise ReleaseStagingError("engine release manifest unreadable") from exc
    if (not raw or len(raw) > _MAX_MANIFEST_BYTES
            or hashlib.sha256(raw).hexdigest() != expected_sha256):
        raise ReleaseStagingError("engine release manifest does not match trusted pin")
    try:
        document = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_manifest,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, ReleaseStagingError):
            raise
        raise ReleaseStagingError("engine release manifest JSON rejected") from exc
    if (not isinstance(document, dict)
            or set(document) != {"schema", "release", "archive", "runtime"}
            or document["schema"] != "atlas-engine-release/v1"
            or not isinstance(document["archive"], dict)
            or set(document["archive"]) != {"sha256", "bytes"}
            or not isinstance(document["runtime"], dict)
            or set(document["runtime"])
                != {"viewer_sha256", "engine_sha256"}):
        raise ReleaseStagingError("invalid engine release manifest schema")
    archive = document["archive"]
    runtime = document["runtime"]
    pins = EngineReleasePins(
        release=document["release"],
        archive_sha256=archive["sha256"],
        archive_bytes=archive["bytes"],
        viewer_sha256=runtime["viewer_sha256"],
        engine_sha256=runtime["engine_sha256"],
    )
    if (not isinstance(pins.release, str)
            or _RELEASE.fullmatch(pins.release) is None
            or pins.release in (".", "..")
            or pins.release[-1] in " ."
            or pins.release.split(".", 1)[0].upper() in _RESERVED
            or type(pins.archive_bytes) is not int
            or not 1 <= pins.archive_bytes <= 2 * 1024 * 1024 * 1024
            or any(not isinstance(value, str) or _SHA.fullmatch(value) is None
                   for value in (
                       pins.archive_sha256, pins.viewer_sha256,
                       pins.engine_sha256,
                   ))):
        raise ReleaseStagingError("invalid engine release manifest pins")
    return pins


def _config_payload(current: InstalledEngineSpec, viewer: Path,
                    viewer_sha256: str) -> bytes:
    try:
        _validate_paths(current)
        _read_config(current)
        raw = current.config_file.read_bytes()
        if hashlib.sha256(raw).hexdigest() != current.config_sha256:
            raise RuntimeLaunchError("Installed engine config hash changed")
        document = json.loads(raw, object_pairs_hook=_unique_json)
        dashboard = document["dashboard"]
        dashboard["executable"] = str(viewer)
        dashboard["executable_sha256"] = viewer_sha256
        dashboard["script"] = None
        dashboard["script_sha256"] = None
        payload = json.dumps(
            document, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > 32 * 1024:
            raise RuntimeLaunchError("Materialized engine config is oversized")
        return payload
    except (OSError, KeyError, TypeError, ValueError, RuntimeLaunchError) as exc:
        raise ReleaseStagingError("installed engine config cannot seed update") from exc


def stage_windows_engine_release(
    archive_file: Path,
    install_root: Path,
    *,
    pins: EngineReleasePins,
    current: InstalledEngineSpec,
) -> StagedEngineRelease:
    """Stage pinned binaries plus a locally derived, slot-bound config."""
    if (not isinstance(archive_file, Path)
            or not isinstance(install_root, Path)
            or not isinstance(pins, EngineReleasePins)
            or not isinstance(pins.release, str)
            or _RELEASE.fullmatch(pins.release) is None
            or pins.release in (".", "..")
            or pins.release[-1] in " ."
            or pins.release.split(".", 1)[0].upper() in _RESERVED
            or type(pins.archive_bytes) is not int
            or not 1 <= pins.archive_bytes <= 2 * 1024 * 1024 * 1024
            or any(not isinstance(value, str) or _SHA.fullmatch(value) is None
                   for value in (
                       pins.archive_sha256, pins.viewer_sha256,
                       pins.engine_sha256,
                   ))
            or not isinstance(current, InstalledEngineSpec)
            or current.install_root != install_root):
        raise ReleaseStagingError("authenticated engine release pins required")
    try:
        if archive_file.stat().st_size != pins.archive_bytes:
            raise ReleaseStagingError("engine release archive size mismatch")
    except OSError as exc:
        raise ReleaseStagingError("engine release archive unavailable") from exc
    pair = pins.viewer_sha256[:16] + "-" + pins.engine_sha256[:16]
    final = install_root / "versions" / pins.release
    runtime = final / "runtime" / pair
    config = final / "config" / "engine.json"
    payload = _config_payload(current, runtime / "AtlasHoldingControlCenter.exe",
                              pins.viewer_sha256)

    def materialize(temporary: Path) -> None:
        staged_runtime = temporary / "runtime" / pair
        staged_config = temporary / "config" / "engine.json"
        if staged_config.exists() or staged_config.is_symlink():
            raise ReleaseStagingError("release archive must not provide engine config")
        try:
            verify_runtime_payload(staged_runtime, {
                "AtlasHoldingControlCenter.exe": pins.viewer_sha256,
                "AtlasHoldingEngine.exe": pins.engine_sha256,
            })
            staged_config.parent.mkdir(parents=True, exist_ok=False)
            with staged_config.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            verify_runtime_payload(staged_runtime, {
                "AtlasHoldingControlCenter.exe": pins.viewer_sha256,
                "AtlasHoldingEngine.exe": pins.engine_sha256,
            })
            if _sha(current.config_file) != current.config_sha256:
                raise ReleaseStagingError("installed engine config changed during staging")
        except (OSError, RuntimePayloadError) as exc:
            raise ReleaseStagingError("pinned engine runtime layout is invalid") from exc

    staged = stage_release_archive(
        archive_file, install_root, release=pins.release,
        expected_sha256=pins.archive_sha256, _materialize=materialize,
    )
    spec = InstalledEngineSpec(
        install_root=install_root,
        runtime_directory=runtime,
        config_file=config,
        viewer_sha256=pins.viewer_sha256,
        engine_sha256=pins.engine_sha256,
        config_sha256=hashlib.sha256(payload).hexdigest(),
        release_directory=final,
    )
    return StagedEngineRelease(staged, spec)


def replace_windows_engine_release(
    install_root: Path,
    *,
    target: StagedEngineRelease,
    current: InstalledEngineSpec,
    stop_current: Callable[[], None],
    approved: bool,
) -> EngineReplacementResult:
    """Stop, switch, authenticate, and restart the previous engine on failure."""
    from .update_switch import read_active_release

    if (approved is not True
            or not isinstance(target, StagedEngineRelease)
            or not isinstance(current, InstalledEngineSpec)
            or current.install_root != install_root
            or target.launch_spec.install_root != install_root
            or target.launch_spec.release_directory != target.staged.directory
            or target.launch_spec.config_file.parent
                != target.staged.directory / "config"
            or target.launch_spec.runtime_directory.parent
                != target.staged.directory / "runtime"
            or not callable(stop_current)):
        raise EngineReplacementError("approved staged engine replacement required")
    previous = read_active_release(install_root)
    if previous is None:
        if current.release_directory is not None:
            raise EngineReplacementError("running engine does not match active pointer")
    elif current.release_directory != install_root / "versions" / previous.release:
        raise EngineReplacementError("running engine does not match active pointer")
    if previous is not None and previous.release == target.staged.release:
        raise EngineReplacementError("target release is already active")
    try:
        stop_current()
    except Exception as exc:
        raise EngineReplacementError(
            "current engine stop failed; replacement not attempted"
        ) from exc
    def start_target(slot: Path) -> UpdateEngineSession:
        if slot != target.staged.directory:
            raise EngineReplacementError("switch selected an unexpected engine slot")
        return launch_installed_engine_health_session(
            target.launch_spec, approved=True,
        )

    try:
        activation = activate_staged_release_after_authenticated_ipc(
            install_root,
            release=target.staged.release,
            approved_archive_sha256=target.staged.archive_sha256,
            session_factory=start_target,
        )
        return EngineReplacementResult(
            "ACTIVATED", activation.session, activation,
        )
    except CandidateCleanupError as exc:
        raise EngineReplacementError(
            "candidate cleanup failed; previous engine was not restarted"
        ) from exc
    except Exception as candidate_failure:
        if read_active_release(install_root) != previous:
            raise EngineReplacementError(
                "rollback pointer is not restored; previous engine was not restarted"
            ) from candidate_failure
        restored = None
        try:
            restored = launch_installed_engine_health_session(
                current, approved=True,
            )
            if not isinstance(restored, UpdateEngineSession):
                raise EngineReplacementError("previous engine session unavailable")
            restored.client.fetch()
        except Exception as restart_failure:
            if isinstance(restored, UpdateEngineSession):
                try:
                    restored.close()
                except Exception:
                    pass
            raise EngineReplacementError(
                "candidate failed and previous engine restart was not authenticated"
            ) from restart_failure
        return EngineReplacementResult(
            "ROLLED_BACK_RESTARTED", restored, None,
            failure=type(candidate_failure).__name__,
        )
