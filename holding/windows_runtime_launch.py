"""Fail-closed Windows handoff from pinned installation to headless engine.

Setup may stage EXEs without having a safe install config yet. Only a trusted
interactive user host may launch the pinned engine after native ACL and config
checks; the engine (PR #11) owns the Qt viewer capability handoff. A spawned
process is NOT proof of a healthy engine or an autonomous roadmap.
"""
from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from typing import Mapping

from .embedded_python_stage import EmbeddedPythonRelease, verify_staged_embedded_python
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
    if (not isinstance(config, dict)
            or type(config.get("schema")) is not int
            or config["schema"] not in (3, 6)):
        raise RuntimeLaunchError("Installed engine requires config schema v3 or v6")
    dashboard = config.get("dashboard")
    if (not isinstance(dashboard, dict)
            or dashboard.get("executable") != str(
                spec.runtime_directory / "AtlasHoldingControlCenter.exe")
            or dashboard.get("executable_sha256") != spec.viewer_sha256
            or dashboard.get("script") is not None
            or dashboard.get("script_sha256") is not None
            or type(dashboard.get("readonly")) is not bool):
        raise RuntimeLaunchError("Installed viewer is not pinned to engine config")
    if config["schema"] == 6:
        mcp = config.get("mcp")
        if (type(mcp) is not dict or set(mcp) != {
                "interpreter", "interpreter_sha256", "package_root",
                "module_sha256", "source_release_zip_sha256",
                "python_minor", "response_timeout"}):
            raise RuntimeLaunchError("Invalid installed MCP companion configuration")
        try:
            release = EmbeddedPythonRelease(
                python_minor=mcp["python_minor"],
                zip_sha256=mcp["source_release_zip_sha256"],
                module_sha256=mcp["module_sha256"])
            root = (spec.install_root / "mcp-python" /
                    ("python-embedded-" + release.zip_sha256[:16]))
            if (mcp["package_root"] != str(root)
                    or mcp["interpreter"] != str(root / "python.exe")
                    or type(mcp["response_timeout"]) not in (int, float)
                    or not .25 <= mcp["response_timeout"] <= 10):
                raise RuntimeLaunchError("Installed MCP outside approved root")
            verified = verify_staged_embedded_python(
                directory=root, pin=release)
            if verified.interpreter_sha256 != mcp["interpreter_sha256"]:
                raise RuntimeLaunchError("Installed CPython binary hash changed")
            from .windows_runtime_acl import validate_runtime_acl
            for path in (
                root.parent, root, root / "python.exe", root / "holding",
                *(root / "holding" / name for name in release.module_sha256),
            ):
                validate_runtime_acl(path)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise RuntimeLaunchError("Pinned MCP companion is not trusted") from exc
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
            spec.install_root,
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
    """Prove a logged-in user's *visible* Windows window station, not Session 0."""
    if sys.platform != "win32":
        return False
    from .windows_user_session import (
        InteractiveSessionError, require_interactive_windows_session,
    )
    try:
        require_interactive_windows_session()
        return True
    except (InteractiveSessionError, OSError, AttributeError, ValueError):
        return False


def _runtime_environment() -> dict[str, str]:
    return {
        key: value for key, value in os.environ.items()
        if key.upper() in {
            "SYSTEMROOT", "WINDIR", "LOCALAPPDATA", "APPDATA", "USERPROFILE",
            "TEMP", "TMP", "PROGRAMDATA", "PATH", "HOMEDRIVE", "HOMEPATH",
        }
    }


def _stop_engine_process(process: subprocess.Popen) -> None:
    """Close pipes and stop one engine process with bounded escalation."""
    failure = None
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        failure = exc
    finally:
        for stream in (process.stdin, process.stdout):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
    if failure is not None:
        raise RuntimeLaunchError("Installed engine process could not be stopped") from failure


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
    try:
        child = subprocess.Popen(
            [executable, "--config", config],
            cwd=str(spec.install_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            env=_runtime_environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise RuntimeLaunchError("Installed engine cannot start") from exc
    if child.poll() is not None:
        raise RuntimeLaunchError("Installed engine exited during startup")
    return EngineLaunch(child)


def launch_installed_engine_health_session(
    spec: InstalledEngineSpec, *, approved: bool,
    handshake_timeout: float = 10.0,
):
    """Start a pinned engine and return its authenticated health session.

    The fresh capability crosses only the child's anonymous stdin pipe. The
    child returns a bounded endpoint identity on stdout; actual acceptance is
    still performed by ``update_health`` through a HMAC snapshot fetch.
    """
    from .dashboard_ipc import DashboardIpcClient
    from .update_health import UpdateEngineSession

    if type(approved) is not bool or not approved:
        raise RuntimeLaunchError("Interactive engine launch not authorized")
    if (type(handshake_timeout) not in (int, float)
            or not .25 <= handshake_timeout <= 30):
        raise RuntimeLaunchError("Invalid update health handshake timeout")
    if not _interactive_user_session():
        raise RuntimeLaunchError("Engine cannot launch outside interactive Windows session")
    executable, config = verify_installed_engine(spec)
    capability = secrets.token_bytes(32)
    child = None
    try:
        child = subprocess.Popen(
            [executable, "--config", config, "--update-health-handshake"],
            cwd=str(spec.install_root), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            shell=False, close_fds=True, env=_runtime_environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if child.stdin is None or child.stdout is None:
            raise RuntimeLaunchError("Engine update health pipes unavailable")
        child.stdin.write(capability)
        child.stdin.flush()
        child.stdin.close()
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(child.stdout.readline, 4097)
            try:
                raw = future.result(timeout=float(handshake_timeout))
            except FutureTimeout as exc:
                _stop_engine_process(child)
                child = None
                raise RuntimeLaunchError("Engine update health handshake timed out") from exc
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        if len(raw) > 4096:
            raise RuntimeLaunchError("Oversized engine update health handshake")
        try:
            response = json.loads(raw, object_pairs_hook=_unique_json)
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise RuntimeLaunchError("Invalid engine update health handshake") from exc
        if (type(response) is not dict
                or set(response) != {"schema", "port", "engine_id"}
                or type(response["schema"]) is not int
                or response["schema"] != 1
                or type(response["port"]) is not int
                or not 1 <= response["port"] <= 65535
                or type(response["engine_id"]) is not str
                or not 1 <= len(response["engine_id"]) <= 80
                or child.poll() is not None):
            raise RuntimeLaunchError("Invalid engine update health handshake")
        child.stdout.close()
        client = DashboardIpcClient(
            port=response["port"], secret=capability,
            engine_id=response["engine_id"],
        )
        stopped = False
        def close_candidate() -> None:
            nonlocal stopped
            if stopped:
                return
            stopped = True
            _stop_engine_process(child)
        return UpdateEngineSession(client, close_candidate)
    except BaseException:
        if child is not None:
            _stop_engine_process(child)
        raise
