"""Trustworthy on-demand inventory of pinned local MCP executable files.

A matching file SHA-256 only proves the bytes on disk. This module never
launches an executable, probes a remote service, grants permissions, or claims
that a plugin's runtime protocol is healthy.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat


_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
MAX_BIN_BYTES = 512 * 1024 * 1024
MAX_PLUGINS = 32


@dataclass(frozen=True)
class PinnedLocalPlugin:
    name: str
    declared_version: str
    executable: Path
    expected_sha256: str

    def __post_init__(self) -> None:
        if (type(self.name) is not str or not _NAME.fullmatch(self.name)
                or type(self.declared_version) is not str
                or not _VERSION.fullmatch(self.declared_version)
                or not isinstance(self.executable, Path)
                or not self.executable.is_absolute()
                or type(self.expected_sha256) is not str
                or not _DIGEST.fullmatch(self.expected_sha256)):
            raise ValueError("Invalid trusted local MCP plugin pin")


@dataclass(frozen=True)
class LocalPluginObservation:
    name: str
    declared_version: str
    binary_state: str
    observed_sha256: str | None = None


def inspect_pinned_plugin(pin: PinnedLocalPlugin) -> LocalPluginObservation:
    """Hash a fixed host-provisioned path, without executing its contents."""
    if type(pin) is not PinnedLocalPlugin:
        raise ValueError("Only trusted immutable plugin pins are accepted")
    base = (pin.name, pin.declared_version)
    try:
        before = pin.executable.lstat()
    except FileNotFoundError:
        return LocalPluginObservation(*base, "MISSING")
    except OSError:
        return LocalPluginObservation(*base, "UNREADABLE")
    if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_BIN_BYTES:
        return LocalPluginObservation(*base, "NOT_REGULAR_FILE")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(pin.executable, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            identity = lambda st: (st.st_dev, st.st_ino)
            if (not stat.S_ISREG(opened.st_mode)
                    or identity(opened) != identity(before)
                    or opened.st_size != before.st_size):
                return LocalPluginObservation(*base, "CHANGED_DURING_READ")
            h = hashlib.sha256()
            while True:
                part = handle.read(64 * 1024)
                if not part:
                    break
                h.update(part)
            after = os.fstat(handle.fileno())
            if (identity(after) != identity(opened)
                    or after.st_size != opened.st_size
                    or after.st_mtime_ns != opened.st_mtime_ns):
                return LocalPluginObservation(*base, "CHANGED_DURING_READ")
            observed = h.hexdigest()
    except FileNotFoundError:
        return LocalPluginObservation(*base, "MISSING")
    except OSError:
        return LocalPluginObservation(*base, "UNREADABLE")
    try:
        latest = pin.executable.lstat()
    except OSError:
        return LocalPluginObservation(*base, "CHANGED_DURING_READ")
    if (identity(latest) != identity(opened)
            or latest.st_size != after.st_size
            or latest.st_mtime_ns != after.st_mtime_ns):
        return LocalPluginObservation(*base, "CHANGED_DURING_READ")
    if observed != pin.expected_sha256:
        return LocalPluginObservation(*base, "HASH_MISMATCH")
    return LocalPluginObservation(*base, "BINARY_VERIFIED", observed)


def inspect_pinned_plugins(pins: tuple[PinnedLocalPlugin, ...]) -> tuple[LocalPluginObservation, ...]:
    """Inventory only explicitly trusted pins; no directories or auto-scan."""
    if (type(pins) is not tuple or len(pins) > MAX_PLUGINS
            or any(type(item) is not PinnedLocalPlugin for item in pins)
            or len({item.name for item in pins}) != len(pins)):
        raise ValueError("Invalid bounded local plugin inventory")
    return tuple(inspect_pinned_plugin(pin)
                 for pin in sorted(pins, key=lambda pin: pin.name))
