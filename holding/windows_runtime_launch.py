"""Fail-closed Windows handoff from pinned installation to headless engine.

Setup may stage EXEs without having a safe install config yet. Only a trusted
interactive user host may launch the pinned engine after native ACL and config
checks; the engine (PR #11) owns the Qt viewer capability handoff. A spawned
process is NOT proof of a healthy engine or an autonomous roadmap.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Mapping

from .windows_runtime_payload import (
    RuntimePayloadError, verify_runtime_payload,
)

_HEX = re.compile(r"^[0-9a-f]{64}$")


class RuntimeLaunchError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstalledEngineSpec:
    install_root: Path
    runtime_directory: Path
    config_file: Path
    viewer_sha256: str
    engine_sha256: str
    config_sha256: str


@dataclass(frozen=True)
class EngineLaunch:
    process: subprocess.Popen
    status: str = "ENGINE_SPAWNED_HEALTH_PENDING"


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeLaunchError("Installed engine config has duplicate fields")
        result[key] = value
    return result


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_paths(spec: InstalledEngineSpec) -> None:
    if not isinstance(spec, InstalledEngineSpec):
        raise RuntimeLaunchError("Pinned Windows runtime identity required")
    paths = (spec.install_root, spec.runtime_directory, spec.config_file)
    if (any(not isinstance(path, Path) or not path.is_absolute()
                   or ".." in path.parts or path.is_symlink()
                   for path in paths)
            or any(type(value) is not str or not _HEX.fullmatch(value)
                   for value in (spec.viewer_sha256, spec.engine_sha256,
                                 spec.config_sha256))):
        raise RuntimeLaunchError("Pinned Windows runtime identity required")
    try:
        root = spec.install_root.resolve(strict=True)
        runtime = spec.runtime_directory.resolve(strict=True)
        config = spec.config_file.resolve(strict=True)
    except OSError as exc:
        raise RuntimeLaunchError("Installed runtime path unavailable") from exc
    if (root != spec.install_root or runtime != spec.runtime_directory
            or config != spec.config_file or not root.is_dir()
            or not runtime.is_dir() or not config.is_file()
            or runtime.parent != root / "runtime"
            or config.parent != root / "config"):
        raise RuntimeLaunchError("Installed runtime is outside selected root")


def _read_config(spec: InstalledEngineSpec) -> None:
    try:
        if spec.config_file.stat().st_size > 32 * 1024:
            raise RuntimeLaunchError("Oversized trusted engine config")
        if _sha(spec.config_file) != spec.config_sha256:
            raise RuntimeLaunchError("Installed engine config hash changed")
        raw = spec.config_file.read_bytes()
        config = json.loads(raw, object_pairs_hook=_unique_json)
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise RuntimeLaunchError("Installed engine config unreadable") from exc
    if not isinstance(config, dict) or type(config.get("schema")) is not int or config["schema"] != 3:
        raise RuntimeLaunchError("Installed engine requires config schema v3")
    dashboard = config.get("dashboard")
    if (not isinstance(dashboard, dict)
            or dashboard.get("executable") != str(
                spec.runtime_directory / "AtlasHoldingControlCenter.exe")
            or dashboard.get("executable_sha256") != spec.viewer_sha256
            or dashboard.get("script") is not None
            or dashboard.get("script_sha256") is not None
            or type(dashboard.get("readonly")) is not bool):
        raise RuntimeLaunchError("Installed viewer is not pinned to engine config")
    if _sha(spec.config_file) != spec.config_sha256:
        raise RuntimeLaunchError("Installed engine config changed during validation")


def verify_installed_engine(spec: InstalledEngineSpec) -> tuple[str, str]:
    """Inspect actual bytes and native rights without starting a program."""
    if sys.platform != "win32":
        raise RuntimeLaunchError("Native Windows runtime launch unavailable")
    _validate_paths(spec)
    expected = {
        "AtlasHoldingControlCenter.exe": spec.viewer_sha256,
        "AtlasHoldingEngine.exe": spec.engine_sha256,
    }
    try:
        verify_runtime_payload(spec.runtime_directory, expected)
        from .windows_runtime_acl import validate_runtime_acl
        for path in (
            spec.runtime_directory.parent, spec.runtime_directory,
            spec.runtime_directory / "AtlasHoldingControlCenter.exe",
            spec.runtime_directory / "AtlasHoldingEngine.exe",
            spec.config_file.parent, spec.config_file,
        ):
            validate_runtime_acl(path)
        _read_config(spec)
        verify_runtime_payload(spec.runtime_directory, expected)
    except (RuntimePayloadError, ValueError, OSError) as exc:
        raise RuntimeLaunchError("Native installed runtime not trusted") from exc
    return (str(spec.runtime_directory / "AtlasHoldingEngine.exe"),
            str(spec.config_file))


def _interactive_user_session() -> bool:
    """Reject Windows Session 0: Qt must attach to the logged-in user's UI."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.ProcessIdToSessionId.argtypes = (
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        )
        kernel.ProcessIdToSessionId.restype = wintypes.BOOL
        session = wintypes.DWORD()
        if not kernel.ProcessIdToSessionId(
                os.getpid(), ctypes.byref(session)):
            return False
        return session.value > 0
    except (OSError, AttributeError, ValueError):
        return False


def launch_installed_engine(
    spec: InstalledEngineSpec, *, approved: bool,
) -> EngineLaunch:
    """Start only a pinned installed engine; it launches GUI itself via IPC."""
    if type(approved) is not bool or not approved:
        raise RuntimeLaunchError("Interactive engine launch not authorized")
    if not _interactive_user_session():
        raise RuntimeLaunchError("Engine cannot launch outside interactive Windows session")
    executable, config = verify_installed_engine(spec)
    # Never use shell, inherited stdin, arbitrary environment tokens, or the
    # caller's cwd. Configured roadmap content cannot supply CLI parameters.
    safe = {
        key: value for key, value in os.environ.items()
        if key.upper() in {
            "SYSTEMROOT", "WINDIR", "LOCALAPPDATA", "APPDATA", "USERPROFILE",
            "TEMP", "TMP", "PROGRAMDATA", "PATH", "HOMEDRIVE", "HOMEPATH",
        }
    }
    try:
        child = subprocess.Popen(
            [executable, "--config", config],
            cwd=str(spec.install_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            env=safe,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise RuntimeLaunchError("Installed engine cannot start") from exc
    if child.poll() is not None:
        raise RuntimeLaunchError("Installed engine exited during startup")
    return EngineLaunch(child)
