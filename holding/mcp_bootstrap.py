"""Strict host-provisioned MCP startup manifest (never model supplied).

This is configuration for an already trusted supervisor, NOT authentication.
On POSIX a manifest must be owned by the current user and not writable by
group/others. On Windows its enclosing supervisor must separately enforce
private ACLs: Python's portable stat permissions do not prove Windows ACLs.
No network, executable launch or arbitrary imports occur during provisioning.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat

from .mcp_local_inventory import MAX_PLUGINS, PinnedLocalPlugin
from .mcp_windows_acl import WindowsAclError, require_private_windows_path

MAX_MANIFEST_BYTES = 64 * 1024


class McpBootstrapError(ValueError):
    pass


@dataclass(frozen=True)
class McpBootstrapConfig:
    cache_database: Path | None
    pins: tuple[PinnedLocalPlugin, ...]


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise McpBootstrapError("Duplicate manifest key")
        result[key] = value
    return result


def _nonfinite(_number: str):
    raise McpBootstrapError("Nonfinite bootstrap manifest number")


def _private_manifest_bytes(path: Path) -> bytes:
    if not isinstance(path, Path) or not path.is_absolute():
        raise McpBootstrapError("Trusted absolute manifest path required")
    try:
        prior = path.lstat()
        if (not stat.S_ISREG(prior.st_mode)
                or not 1 <= prior.st_size <= MAX_MANIFEST_BYTES):
            raise McpBootstrapError("Manifest is not a bounded regular file")
        if os.name == "nt":
            require_private_windows_path(path)
        if os.name == "posix":
            parent = path.parent.stat()
            if (prior.st_uid != os.getuid() or
                    stat.S_IMODE(prior.st_mode) & 0o022 or
                    not stat.S_ISDIR(parent.st_mode) or
                    parent.st_uid != os.getuid() or
                    stat.S_IMODE(parent.st_mode) & 0o022):
                raise McpBootstrapError("Manifest owner or permissions unsafe")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino, opened.st_size,
                        opened.st_mtime_ns) !=
                       (prior.st_dev, prior.st_ino, prior.st_size,
                        prior.st_mtime_ns)):
                raise McpBootstrapError("Manifest changed during open")
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
            after = os.fstat(stream.fileno())
        latest = path.lstat()
        if os.name == "nt":
            # A writable directory could replace an inspected manifest.
            require_private_windows_path(path)
        if (len(raw) != prior.st_size
                or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    != (prior.st_dev, prior.st_ino, prior.st_size, prior.st_mtime_ns)
                or (latest.st_dev, latest.st_ino, latest.st_size, latest.st_mtime_ns)
                    != (prior.st_dev, prior.st_ino, prior.st_size, prior.st_mtime_ns)
                or not stat.S_ISREG(latest.st_mode)):
            raise McpBootstrapError("Manifest changed during read")
        return raw
    except (OSError, WindowsAclError) as exc:
        raise McpBootstrapError("Trusted manifest unavailable") from exc


def load_bootstrap_manifest(path: Path) -> McpBootstrapConfig:
    raw = _private_manifest_bytes(path)
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_nonfinite)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise McpBootstrapError("Invalid trusted manifest JSON") from exc
    if (type(data) is not dict
            or set(data) != {"schema_version", "cache_database", "pins"}
            or type(data["schema_version"]) is not int
            or data["schema_version"] != 1):
        raise McpBootstrapError("Unsupported trusted manifest schema")
    cache = data["cache_database"]
    if cache is not None:
        if (type(cache) is not str or not 1 <= len(cache) <= 4096
                or not Path(cache).is_absolute()):
            raise McpBootstrapError("Invalid provisioned cache location")
        cache = Path(cache)
    pins = data["pins"]
    if type(pins) is not list or len(pins) > MAX_PLUGINS:
        raise McpBootstrapError("Invalid bounded pinned plugin list")
    validated = []
    for item in pins:
        if type(item) is not dict or set(item) != {
                "name", "declared_version", "executable", "expected_sha256"}:
            raise McpBootstrapError("Invalid plugin pin shape")
        executable = item["executable"]
        if (type(executable) is not str or not 1 <= len(executable) <= 4096):
            raise McpBootstrapError("Invalid plugin executable location")
        try:
            validated.append(PinnedLocalPlugin(
                name=item["name"], declared_version=item["declared_version"],
                executable=Path(executable), expected_sha256=item["expected_sha256"],
            ))
        except (TypeError, ValueError) as exc:
            raise McpBootstrapError("Invalid trusted plugin pin") from exc
    if len({pin.name for pin in validated}) != len(validated):
        raise McpBootstrapError("Duplicate pinned plugin identity")
    return McpBootstrapConfig(cache, tuple(validated))
