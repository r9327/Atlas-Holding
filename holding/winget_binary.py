"""Read-only discovery of exact user-scoped WinGet executables.

WinGet's successful installer status is not enough: a fresh GUI process may
not see a newly installed CLI on PATH until the next user logon. We inspect
a small fixed set of standard per-user install folders, never walk the disk,
and probe an actual binary with a bounded process tree and version pin.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import subprocess

from .probe_process import ProbeProcessError, run_probe
from .staged_install import _no_linked_ancestors
from .version_evidence import matches_component_version
from .windows_package_manager import WingetPin


@dataclass(frozen=True)
class WingetBinary:
    state: str
    executable: str = ""
    version: str = ""


def _candidates(pin: WingetPin, local: Path) -> tuple[Path, ...]:
    programs = local / "Programs"
    if pin.component == "git":
        return (programs / "Git" / "cmd" / "git.exe",
                programs / "Git" / "bin" / "git.exe")
    if pin.component == "ollama":
        return (programs / "Ollama" / "ollama.exe",)
    # Portable ZIP package can be installed in a versioned per-user WinGet
    # package directory. A WinGet link is typically a symlink; only execute
    # the real payload within this trusted, bounded WinGet root.
    packages = local / "Microsoft" / "WinGet" / "Packages"
    try:
        _no_linked_ancestors(packages)
        if not packages.is_dir():
            return ()
        dirs = list(packages.iterdir())
        if len(dirs) > 256:
            return ()
        allowed = [
            p / "opencode.exe" for p in dirs
            if p.is_dir() and not p.is_symlink()
            and p.name.startswith("SST.opencode_")
        ]
        if len(allowed) > 16:
            return ()
        return tuple(allowed)
    except (OSError, ValueError):
        return ()


def discover_winget_binary(pin: WingetPin, *, timeout: float = 4.0) -> WingetBinary:
    if not isinstance(pin, WingetPin):
        raise ValueError("Trusted winget release pin is required")
    pin.validate()
    if not 1 <= timeout <= 10:
        raise ValueError("Native probe timeout out of range")
    if platform.system() != "Windows":
        return WingetBinary("WINDOWS_REQUIRED")
    raw = os.environ.get("LOCALAPPDATA", "")
    if not raw or not Path(raw).is_absolute():
        return WingetBinary("LOCALAPPDATA_UNAVAILABLE")
    local = Path(raw)
    try:
        _no_linked_ancestors(local)
    except (OSError, ValueError):
        return WingetBinary("LOCALAPPDATA_UNSAFE")
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
                           "USERPROFILE", "LOCALAPPDATA", "APPDATA", "LANG"}
    }
    environment.update(GIT_TERMINAL_PROMPT="0", NO_PROXY="127.0.0.1,localhost")
    for candidate in _candidates(pin, local):
        try:
            _no_linked_ancestors(candidate)
            if not candidate.is_file() or candidate.stat().st_nlink != 1:
                continue
            result = run_probe(
                [str(candidate), "--version"], environment=environment,
                timeout=timeout, max_output_bytes=8192,
            )
        except (OSError, ValueError, ProbeProcessError,
                subprocess.TimeoutExpired):
            continue
        version = (result.stdout + "\n" + result.stderr).strip()[:8192]
        if (result.returncode == 0 and
                matches_component_version(pin.component, pin.version, version)):
            return WingetBinary("EXACT_BINARY_VERIFIED", str(candidate), version)
    return WingetBinary("NO_PINNED_BINARY_FOUND")
