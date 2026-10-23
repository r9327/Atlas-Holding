"""Exact SHA-256 manifest for the installed Holding Python module tree.

A verified interpreter does not attest imported Python modules. Installer v5
pins *all* .py files under the trusted holding package, then a supervised hub
verifies the complete set before launch and again after protocol discovery.

It is not a replacement for a locked install directory, ACLs, signature
verification, or a file-system isolation boundary against TOCTOU writes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}\.py$")
MAX_PY_FILES = 192
MAX_PY_FILE_BYTES = 2 * 1024 * 1024
MAX_PY_TOTAL_BYTES = 32 * 1024 * 1024


class McpPackagePinError(ValueError):
    pass


def _discover(package_root: Path) -> dict[str, Path]:
    if not isinstance(package_root, Path) or not package_root.is_absolute():
        raise McpPackagePinError("Expected absolute installed package")
    folder = package_root / "holding"
    if not folder.is_dir() or folder.is_symlink():
        raise McpPackagePinError("Expected regular installed holding directory")
    files = {}
    total = 0
    for candidate in folder.iterdir():
        # Native extensions are importable *before* .py on Python import.
        # Never allow an unpinned .pyd/.so to shadow a signed .py module.
        if candidate.suffix.lower() in {".pyd", ".so", ".dll", ".pyc"}:
            raise McpPackagePinError("Unpinned executable package artifact")
        if candidate.suffix != ".py":
            continue
        if (not _SAFE.fullmatch(candidate.name)
                or candidate.is_symlink()):
            raise McpPackagePinError("Unsafe installed Python module")
        try:
            mode = candidate.lstat()
        except OSError as exc:
            raise McpPackagePinError("Unreadable installed Python module") from exc
        if not stat.S_ISREG(mode.st_mode) or not 1 <= mode.st_size <= MAX_PY_FILE_BYTES:
            raise McpPackagePinError("Invalid Python module length or type")
        total += mode.st_size
        if total > MAX_PY_TOTAL_BYTES or len(files) >= MAX_PY_FILES:
            raise McpPackagePinError("Installed package exceeds budget")
        files[candidate.name] = candidate
    required = {"__init__.py", "mcp_entry.py", "mcp_stdio.py",
                "mcp_hub_session.py", "mcp_hub_probe.py",
                "mcp_windows_job.py", "mcp_local_inventory.py"}
    if not required.issubset(files):
        raise McpPackagePinError("Installed Holding hub dependencies missing")
    return files


def _hash_file(path: Path) -> str:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        raise McpPackagePinError("Installed Python module changed")
    options = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, options)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size) != (
                    before.st_dev, before.st_ino, before.st_size):
                raise McpPackagePinError("Installed Python module changed")
            digest = hashlib.sha256()
            while True:
                block = stream.read(64 * 1024)
                if not block:
                    break
                digest.update(block)
            after = os.fstat(stream.fileno())
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                    opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
                raise McpPackagePinError("Installed Python module changed")
    except OSError as exc:
        raise McpPackagePinError("Installed Python module unreadable") from exc
    latest = path.lstat()
    if (latest.st_dev, latest.st_ino, latest.st_size, latest.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise McpPackagePinError("Installed Python module changed")
    return digest.hexdigest()


@dataclass(frozen=True)
class McpPackagePin:
    """Immutable normalized set of exact module digests from installation."""

    entries: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if (type(self.entries) is not tuple
                or not 7 <= len(self.entries) <= MAX_PY_FILES):
            raise McpPackagePinError("Invalid module manifest length")
        if any(type(item) is not tuple or len(item) != 2
               or type(item[0]) is not str or not _SAFE.fullmatch(item[0])
               or type(item[1]) is not str or not _DIGEST.fullmatch(item[1])
               for item in self.entries):
            raise McpPackagePinError("Invalid pinned module identity")
        names = [key for key, _ in self.entries]
        if names != sorted(set(names)):
            raise McpPackagePinError("Module manifest requires unique sorted names")

    @classmethod
    def from_manifest(cls, entries: dict) -> "McpPackagePin":
        if type(entries) is not dict:
            raise McpPackagePinError("Expected installer module digest map")
        return cls(tuple(sorted(entries.items())))

    def verify(self, package_root: Path) -> bool:
        files = _discover(package_root)
        expected = dict(self.entries)
        if set(files) != set(expected):
            return False
        for name, path in files.items():
            if not hmac.compare_digest(_hash_file(path), expected[name]):
                return False
        return True


def build_installation_module_pins(package_root: Path) -> McpPackagePin:
    """Installer-only helper; NOT exposed through any MCP tool."""
    files = _discover(package_root)
    return McpPackagePin.from_manifest(
        {name: _hash_file(path) for name, path in files.items()})
