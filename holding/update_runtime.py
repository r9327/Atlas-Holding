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

from .update_stage import (
    _RELEASE, _RESERVED, ReleaseStagingError, StagedRelease,
    stage_release_archive,
)
from .windows_runtime_launch import (
    InstalledEngineSpec, RuntimeLaunchError, _read_config, _sha,
    _unique_json, _validate_paths,
)
from .windows_runtime_payload import RuntimePayloadError, verify_runtime_payload

_SHA = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class EngineReleasePins:
    release: str
    archive_sha256: str
    viewer_sha256: str
    engine_sha256: str


@dataclass(frozen=True, slots=True)
class StagedEngineRelease:
    staged: StagedRelease
    launch_spec: InstalledEngineSpec


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
    if (not isinstance(pins, EngineReleasePins)
            or not isinstance(pins.release, str)
            or _RELEASE.fullmatch(pins.release) is None
            or pins.release in (".", "..")
            or pins.release[-1] in " ."
            or pins.release.split(".", 1)[0].upper() in _RESERVED
            or any(not isinstance(value, str) or _SHA.fullmatch(value) is None
                   for value in (
                       pins.archive_sha256, pins.viewer_sha256,
                       pins.engine_sha256,
                   ))
            or not isinstance(current, InstalledEngineSpec)
            or current.install_root != install_root):
        raise ReleaseStagingError("authenticated engine release pins required")
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
