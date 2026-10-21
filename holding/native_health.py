"""Pinned executable version check for Windows staging, not product QA.

An authenticated manifest may optionally provide executable and version pins.
The probe is contained and time/output bounded. Passing it does not establish
Ollama API health, model quality, OpenCode tool-calling or full installer PASS.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess

from .probe_process import ProbeProcessError, run_probe
from .version_evidence import matches_component_version
from .staged_install import (PinnedZip, inspect_versioned_zip,
                             _no_linked_ancestors)


@dataclass(frozen=True)
class NativeHealth:
    component: str
    state: str


def check_staged_binary(
    spec: PinnedZip, *, install_root: Path, timeout: float = 4.0,
) -> NativeHealth:
    spec.validate()
    if not isinstance(install_root, Path) or not install_root.is_absolute():
        raise ValueError("Absolute install root required")
    if timeout <= 0 or timeout > 15:
        raise ValueError("Invalid native health timeout")
    if not spec.executable or not spec.expected_version:
        return NativeHealth(spec.component, "NO_NATIVE_PROBE_CONFIGURED")
    if spec.component not in {"git", "ollama", "opencode"}:
        return NativeHealth(spec.component, "NATIVE_PROBE_NOT_ALLOWED")
    observed = inspect_versioned_zip(spec, install_root=install_root)
    if observed.status != "VERIFIED_PAYLOAD":
        return NativeHealth(spec.component, observed.status)
    exe = observed.directory.joinpath(*spec.executable.split("/"))
    try:
        _no_linked_ancestors(exe)
        if not exe.is_file() or exe.stat().st_nlink != 1:
            return NativeHealth(spec.component, "BINARY_UNSAFE")
    except OSError:
        return NativeHealth(spec.component, "BINARY_UNSAFE")
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
                           "TMPDIR", "LANG", "LC_ALL"}
    }
    environment.update({
        "GIT_TERMINAL_PROMPT": "0",
        "PYTHONNOUSERSITE": "1",
        "NO_PROXY": "127.0.0.1,localhost",
    })
    try:
        result = run_probe(
            [str(exe), "--version"], environment=environment,
            timeout=timeout, max_output_bytes=8192,
        )
    except (ProbeProcessError, OSError, subprocess.TimeoutExpired):
        return NativeHealth(spec.component, "BINARY_PROBE_FAILED")
    if (result.returncode != 0
            or not matches_component_version(
                spec.component, spec.expected_version,
                result.stdout + "\n" + result.stderr,
            )):
        return NativeHealth(spec.component, "BINARY_VERSION_MISMATCH")
    after = inspect_versioned_zip(spec, install_root=install_root)
    if after.status != "VERIFIED_PAYLOAD":
        return NativeHealth(spec.component, "BINARY_MODIFIED_DURING_PROBE")
    return NativeHealth(spec.component, "BINARY_VERSION_PROBED")
