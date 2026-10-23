"""Constrained Windows package-manager adapter for official runtime dependencies.

winget verifies its own package manifests and installer hashes, while Atlas
pins package IDs and versions from its trusted release catalogue. This is not
a substitute for a native post-install health check or elevation policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

from .probe_process import ProbeProcessError, run_probe


_ALLOWED = {
    "git": "Git.Git",
    "ollama": "Ollama.Ollama",
    "opencode": "SST.opencode",
}
_VERSION = re.compile(r"^[0-9]+(?:[.][0-9]+){1,3}(?:-[A-Za-z0-9.]+)?$")


class WindowsPackageError(ValueError):
    pass


@dataclass(frozen=True)
class WingetPin:
    component: str
    package_id: str
    version: str

    def validate(self) -> None:
        if (type(self.component) is not str
                or self.component not in _ALLOWED
                or self.package_id != _ALLOWED[self.component]
                or not isinstance(self.version, str)
                or not _VERSION.fullmatch(self.version)):
            raise WindowsPackageError("Untrusted package identity or version")


@dataclass(frozen=True)
class WingetResult:
    component: str
    state: str


def install_pinned_winget(
    pin: WingetPin, *, authorized: bool, online: bool,
    max_seconds: float = 900.0,
    model_root: Path | None = None,
) -> WingetResult:
    """Pin exact package/version, no shell, no admin fallback, no implicit retry."""
    if not isinstance(pin, WingetPin):
        raise WindowsPackageError("Trusted Windows package pin required")
    pin.validate()
    if model_root is not None and (
            pin.component != "ollama"
            or not isinstance(model_root, Path)
            or not model_root.is_absolute()):
        raise WindowsPackageError("Model volume can only target Ollama")
    if type(authorized) is not bool or type(online) is not bool:
        raise WindowsPackageError("Invalid package authorization")
    if not 30 <= max_seconds <= 1800:
        raise WindowsPackageError("Invalid winget runtime budget")
    if not authorized:
        return WingetResult(pin.component, "AUTH_REQUIRED")
    if not online:
        return WingetResult(pin.component, "NETWORK_REQUIRED")
    if platform.system() != "Windows":
        return WingetResult(pin.component, "WINDOWS_REQUIRED")
    executable = shutil.which("winget")
    if not executable or not os.path.isabs(executable):
        return WingetResult(pin.component, "WINGET_UNAVAILABLE")
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() in {
            "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
            "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES",
            "PROGRAMFILES(X86)", "PROGRAMDATA", "HOMEDRIVE", "HOMEPATH",
            "LANG", "LC_ALL",
        }
    }
    environment.update({
        "GIT_TERMINAL_PROMPT": "0",
        "PYTHONNOUSERSITE": "1",
    })
    if model_root is not None:
        from .model_volume import inspect_model_volume
        if inspect_model_volume(model_root).status != "VOLUME_AVAILABLE":
            return WingetResult(pin.component, "MODEL_VOLUME_UNAVAILABLE")
        # The Windows user environment becomes effective at next sign-in.
        # The present native installer and daemon inherit this explicit
        # process-local value immediately, without global PATH edits.
        environment["OLLAMA_MODELS"] = str(model_root)
    try:
        version = run_probe(
            [executable, "--version"], environment=environment,
            timeout=8, max_output_bytes=8192,
        )
        if version.returncode or not version.stdout.strip():
            return WingetResult(pin.component, "WINGET_UNHEALTHY")
        # A source query with exact version, scope and architecture runs
        # before any native installer mutation. Do not parse localized text.
        available = run_probe(
            [
                executable, "show", "--id", pin.package_id, "--exact",
                "--version", pin.version, "--source", "winget",
                "--scope", "user", "--architecture", "x64",
                "--disable-interactivity", "--accept-source-agreements",
            ],
            environment=environment, timeout=min(max_seconds, 45),
            max_output_bytes=16384,
        )
        if available.returncode != 0 or not available.stdout.strip():
            return WingetResult(pin.component, "PINNED_USER_PACKAGE_UNAVAILABLE")
        result = run_probe(
            [
                executable, "install", "--exact", "--id", pin.package_id,
                "--version", pin.version, "--source", "winget",
                "--scope", "user", "--silent", "--disable-interactivity",
                "--accept-package-agreements", "--accept-source-agreements",
            ],
            environment=environment, timeout=max_seconds,
            max_output_bytes=32768,
        )
    except (OSError, ProbeProcessError, subprocess.TimeoutExpired):
        return WingetResult(pin.component, "INSTALL_INTERRUPTED")
    if result.returncode != 0:
        return WingetResult(pin.component, "INSTALL_FAILED")
    # A successful return code from winget isn't evidence that Git/Ollama/
    # OpenCode and their service/APIs really operate or that a model passed QA.
    return WingetResult(pin.component, "INSTALLED_NOT_HEALTHCHECKED")
