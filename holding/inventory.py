"""Non-destructive local provisioning inventory for Atlas Holding.

Detection is read-only: no installation, downloads, registration, services
started, or model selection from guesses. Every probe has a fixed deadline.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Callable
from urllib import request


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Even a locally installed or compromised daemon must not be able to
        # redirect an inventory probe to an outside network endpoint.
        return None


@dataclass(frozen=True)
class Component:
    status: str
    path: str = ""
    version: str = ""
    reason: str = ""


@dataclass(frozen=True)
class OllamaModel:
    name: str
    digest: str
    size_bytes: int
    family: str = ""
    quantization: str = ""


@dataclass(frozen=True)
class Inventory:
    os_name: str
    os_architecture: str
    cpu_threads: int
    ram_total_bytes: int | None
    installation_disk_free_bytes: int
    git: Component
    python: Component
    ollama: Component
    opencode: Component
    models: tuple[OllamaModel, ...]
    gpu: str
    vram_total_bytes: int | None
    vram_free_bytes: int | None

    def as_json(self) -> dict:
        return asdict(self)


def _command(executable: str, args: tuple[str, ...], *, timeout: float = 2.0) -> tuple[int, str]:
    try:
        completed = subprocess.run([executable, *args], capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""
    return completed.returncode, (completed.stdout or completed.stderr)[:8192].strip()


def _program(name: str, arguments: tuple[str, ...] = ("--version",)) -> Component:
    found = shutil.which(name)
    if not found:
        return Component("NOT_FOUND", reason="No executable found in PATH")
    code, version = _command(found, arguments)
    if code != 0 or not version:
        return Component("FOUND_BROKEN", path=str(Path(found).resolve()),
                         reason="Executable failed its version health check")
    return Component("FOUND_VALID", path=str(Path(found).resolve()), version=version.splitlines()[0][:200])


def _api_json(route: str) -> dict:
    """Fixed loopback API only: never follow a user-supplied Ollama URL."""
    url = "http://127.0.0.1:11434" + route
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
    with opener.open(request.Request(url, headers={"Accept": "application/json"}),
                     timeout=1.5) as response:
        if response.status != 200:
            raise ValueError("Ollama returned an unsuccessful HTTP response")
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Oversized Ollama API response")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Ollama API returned a non-object")
    return payload


def _ollama(binary: Component) -> tuple[Component, tuple[OllamaModel, ...]]:
    if binary.status == "NOT_FOUND":
        return binary, ()
    if binary.status != "FOUND_VALID":
        return binary, ()
    try:
        info = _api_json("/api/version")
        tags = _api_json("/api/tags")
        version = info["version"]
        rows = tags["models"]
        if not isinstance(version, str) or not version.strip() or not isinstance(rows, list):
            raise ValueError("Unexpected Ollama schema")
        parsed = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            name, digest, size = item.get("name"), item.get("digest"), item.get("size")
            if not isinstance(name, str) or not name or not isinstance(digest, str):
                continue
            if type(size) is not int or size < 0:
                continue
            details = item.get("details")
            details = details if isinstance(details, dict) else {}
            family = details.get("family", "")
            quant = details.get("quantization_level", "")
            parsed.append(OllamaModel(name, digest, size,
                                      family if isinstance(family, str) else "",
                                      quant if isinstance(quant, str) else ""))
        return Component("FOUND_VALID", path=binary.path, version=version,
                         reason="Loopback API healthy"), tuple(parsed)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return Component("FOUND_BROKEN", path=binary.path, version=binary.version,
                         reason="Ollama installed but local API is not healthy"), ()


def _ram_total() -> int | None:
    if os.name == "nt":
        try:
            import ctypes
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                            ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                            ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                            ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                            ("avail_extended_virtual", ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total_phys)
        except (AttributeError, OSError, ValueError):
            pass
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * size) if pages > 0 and size > 0 else None
    except (AttributeError, OSError, ValueError):
        return None


def _gpu() -> tuple[str, int | None, int | None]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return "", None, None
    code, output = _command(executable, ("--query-gpu=name,memory.total,memory.free",
                                         "--format=csv,noheader,nounits"), timeout=3)
    if code or not output:
        return "", None, None
    # The unit-free Nvidia query returns values in MiB. Never invent VRAM.
    row = output.splitlines()[0].split(",")
    if len(row) != 3:
        return "", None, None
    try:
        total, free = int(row[1].strip()), int(row[2].strip())
    except ValueError:
        return "", None, None
    if total < 1 or free < 0 or free > total:
        return "", None, None
    return row[0].strip()[:120], total * 1024 * 1024, free * 1024 * 1024


def _existing_disk(path: Path) -> Path:
    """Check the chosen install volume without creating user directories."""
    candidate = path.expanduser().resolve()
    while not candidate.exists():
        if candidate == candidate.parent:
            raise ValueError("No existing parent for installation target")
        candidate = candidate.parent
    if not candidate.is_dir():
        raise ValueError("Installation target ancestor is not a directory")
    return candidate


def scan(install_target: Path) -> Inventory:
    if not isinstance(install_target, Path) or not install_target.is_absolute():
        raise ValueError("An absolute install target is required")
    disk = shutil.disk_usage(_existing_disk(install_target))
    python_exe = Path(sys.executable)
    python_result = Component("FOUND_VALID", path=str(python_exe.resolve()),
                              version=platform.python_version())
    git = _program("git")
    ollama, models = _ollama(_program("ollama"))
    gpu, vram_total, vram_free = _gpu()
    return Inventory(platform.system(), platform.machine(), os.cpu_count() or 1,
                     _ram_total(), int(disk.free), git, python_result, ollama,
                     _program("opencode"), models, gpu, vram_total, vram_free)
