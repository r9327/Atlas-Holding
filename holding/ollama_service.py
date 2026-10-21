"""Explicit local Ollama startup with user-selected model volume.

This module never kills an unrelated Ollama process, changes machine/user
environment settings, opens an externally accessible listener or migrates
existing model data. Successful startup is not a model benchmark or a reboot
persistence guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
from typing import Callable

from .inventory import Inventory, _api_json, _existing_disk
from .managed_toolchain import resolve_managed_tool
from .staged_install import PinnedZip, _no_linked_ancestors

_OWNED_PROCESSES: list[subprocess.Popen] = []


@dataclass(frozen=True)
class OllamaLaunch:
    state: str
    model_directory: str
    process: subprocess.Popen | None = None

    @property
    def owned(self) -> bool:
        return self.process is not None and self.state == "OWNED_API_HEALTHY"


def _local_api_alive() -> bool:
    try:
        version = _api_json("/api/version").get("version")
        return isinstance(version, str) and bool(version.strip())
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _minimal_ollama_environment(model_root: Path) -> dict[str, str]:
    allowed = {
        "PATH", "SYSTEMROOT", "WINDIR", "USERPROFILE", "LOCALAPPDATA",
        "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA",
        "TEMP", "TMP", "LANG", "LC_ALL", "HOMEDRIVE", "HOMEPATH",
    }
    environment = {
        k: v for k, v in os.environ.items()
        if k.upper() in allowed
    }
    environment.update({
        "OLLAMA_HOST": "127.0.0.1:11434",
        "OLLAMA_MODELS": str(model_root),
        "NO_PROXY": "127.0.0.1,localhost",
    })
    return environment


def start_local_ollama(
    snapshot: Inventory, *, install_root: Path, model_root: Path,
    pins: tuple[PinnedZip, ...] = (), authorized: bool,
    cancelled: Callable[[], bool] | None = None,
    startup_seconds: float = 15.0,
) -> OllamaLaunch:
    """Start only a binary already known healthy or fully pinned by Atlas.

    If an unrelated server already occupies the loopback endpoint, its model
    directory cannot be attested. No alternate host/port or forced takeover.
    """
    if (not isinstance(snapshot, Inventory)
            or not isinstance(install_root, Path)
            or not install_root.is_absolute()
            or not isinstance(model_root, Path) or not model_root.is_absolute()
            or type(authorized) is not bool
            or not 1 <= startup_seconds <= 60
            or not isinstance(pins, tuple)):
        raise ValueError("Invalid trusted Ollama launch request")
    target = model_root
    _no_linked_ancestors(target)
    _no_linked_ancestors(install_root)
    if target.exists() and not target.is_dir():
        return OllamaLaunch("MODEL_DIRECTORY_INVALID", str(target))
    if platform.system() != "Windows":
        return OllamaLaunch("WINDOWS_REQUIRED", str(target))
    if _local_api_alive():
        # The running process may use the default volume or a user-set path.
        # The local API does not attest OLLAMA_MODELS, so never claim otherwise.
        return OllamaLaunch("EXISTING_API_STORAGE_UNVERIFIED", str(target))
    if snapshot.ollama.status not in {"FOUND_VALID", "FOUND_BROKEN", "NOT_FOUND"}:
        return OllamaLaunch("BINARY_STATE_UNKNOWN", str(target))
    executable: str = ""
    if snapshot.ollama.status in {"FOUND_VALID", "FOUND_BROKEN"}:
        # Broken API but a validated versioned binary may be recoverable.
        executable = snapshot.ollama.path
    if not executable:
        for pin in pins:
            if not isinstance(pin, PinnedZip) or pin.component != "ollama":
                continue
            managed = resolve_managed_tool(pin, install_root=install_root)
            if managed.state == "VERIFIED_EXECUTABLE":
                executable = managed.executable
            break
    if not executable:
        return OllamaLaunch("OLLAMA_EXECUTABLE_NOT_FOUND", str(target))
    binary = Path(executable)
    if not binary.is_absolute():
        return OllamaLaunch("OLLAMA_BINARY_NOT_ABSOLUTE", str(target))
    _no_linked_ancestors(binary)
    if not binary.is_file():
        return OllamaLaunch("OLLAMA_BINARY_NOT_FOUND", str(target))
    if not authorized:
        return OllamaLaunch("AUTH_REQUIRED", str(target))
    if cancelled is not None and cancelled():
        return OllamaLaunch("CANCELLED", str(target))
    # No forced migration of a directory that could be used by an existing
    # running daemon. Disk capacity for each actual model is checked later.
    existing = _existing_disk(target)
    if shutil.disk_usage(existing).free < 1024**3:
        return OllamaLaunch("MODEL_DISK_LOW", str(target))
    target.mkdir(parents=True, exist_ok=True)
    _no_linked_ancestors(target)
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": _minimal_ollama_environment(target),
        "shell": False,
        "cwd": str(binary.parent),
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) |
            getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen([str(binary), "serve"], **kwargs)
    except OSError:
        return OllamaLaunch("START_FAILED", str(target))
    deadline = time.monotonic() + startup_seconds
    succeeded = False
    try:
        while time.monotonic() < deadline:
            if cancelled is not None and cancelled():
                return OllamaLaunch("CANCELLED", str(target))
            if process.poll() is not None:
                return OllamaLaunch("PROCESS_EXITED", str(target))
            if _local_api_alive() and process.poll() is None:
                succeeded = True
                _OWNED_PROCESSES.append(process)
                return OllamaLaunch("OWNED_API_HEALTHY", str(target), process)
            time.sleep(0.2)
        return OllamaLaunch("START_TIMEOUT", str(target))
    finally:
        # Only the process created by this invocation may be terminated.
        # A healthy service intentionally remains alive after setup returns.
        if process.poll() is not None:
            process.wait(timeout=2)
        elif not succeeded:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
