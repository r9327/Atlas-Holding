"""Versioned, non-destructive installation of SHA-pinned ZIP runtime assets.

Only installs archive *data*, never runs downloaded code or installers. A separate,
trusted bootstrap must authorize each asset pin and establish secure directory ACLs.
A broken existing version is quarantined logically (refused), never overwritten.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Callable
import zipfile

from .downloads import download_pinned, _target_lock, _validate_url

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SHA = re.compile(r"^[a-fA-F0-9]{64}$")
_INVALID_WINDOWS_CHARS = frozenset('<>:"|?*')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
MAX_FILES = 4096
MAX_UNPACKED_BYTES = 4 * 1024**3
_MANIFEST = ".atlas-install.json"


class InstallError(ValueError):
    """Asset is untrusted, incompatible or cannot be safely published."""


@dataclass(frozen=True)
class PinnedZip:
    component: str
    version: str
    url: str
    sha256: str
    download_bytes: int
    unpacked_bytes: int
    executable: str | None = None
    expected_version: str | None = None

    def validate(self) -> None:
        if (not isinstance(self.component, str) or not isinstance(self.version, str)
                or not _NAME.fullmatch(self.component) or not _NAME.fullmatch(self.version)):
            raise InstallError("Invalid pinned component or version")
        if not isinstance(self.sha256, str) or not _SHA.fullmatch(self.sha256):
            raise InstallError("Invalid asset SHA-256")
        if (type(self.download_bytes) is not int or self.download_bytes <= 0
                or type(self.unpacked_bytes) is not int
                or not 0 < self.unpacked_bytes <= MAX_UNPACKED_BYTES):
            raise InstallError("Invalid pinned asset size")
        if not isinstance(self.url, str) or len(self.url) > 2048:
            raise InstallError("Invalid asset URL")
        _validate_url(self.url)
        if (self.executable is None) != (self.expected_version is None):
            raise InstallError("Executable and version pin must be specified together")
        if self.executable is not None:
            relative = _safe_relative(self.executable)
            if (not relative.lower().endswith(".exe")
                    or not isinstance(self.expected_version, str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,79}",
                                        self.expected_version)):
                raise InstallError("Invalid native runtime health pin")


@dataclass(frozen=True)
class InstallResult:
    status: str
    directory: Path
    file_count: int


def _no_linked_ancestors(path: Path) -> None:
    for entry in (path, *path.parents):
        if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
            raise InstallError("Installation path traverses a symlink or junction")


def _safe_relative(raw: str) -> str:
    if (not isinstance(raw, str) or not raw or raw.startswith("/") or "\\" in raw or
            raw.endswith(" ") or raw.endswith(".") or raw.startswith("//")):
        raise InstallError("Unsafe ZIP entry name")
    parts = raw.rstrip("/").split("/")
    if not parts or any(
        part in ("", ".", "..") or len(part) > 120
        or part.endswith((" ", "."))
        or any(ord(char) < 32 or ord(char) == 92 or char in _INVALID_WINDOWS_CHARS
               for char in part)
        or part.split(".")[0].upper() in _RESERVED
        for part in parts
    ):
        raise InstallError("Unsafe ZIP entry path")
    relative = PurePosixPath(*parts).as_posix()
    if relative.casefold() == _MANIFEST.casefold():
        raise InstallError("ZIP cannot supply installer metadata")
    return relative


def _safe_members(archive: zipfile.ZipFile, spec: PinnedZip) -> list[tuple[zipfile.ZipInfo, str]]:
    members = archive.infolist()
    if not members or len(members) > MAX_FILES:
        raise InstallError("ZIP has an invalid file count")
    seen: set[str] = set()
    file_names: set[str] = set()
    unpacked = 0
    selected: list[tuple[zipfile.ZipInfo, str]] = []
    for item in members:
        relative = _safe_relative(item.filename)
        folded = relative.casefold()
        if folded in seen:
            raise InstallError("Duplicate case-insensitive ZIP path")
        seen.add(folded)
        mode = item.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if item.flag_bits & 1 or kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise InstallError("Encrypted or non-regular ZIP member")
        if (item.is_dir() and kind == stat.S_IFREG) or (
                not item.is_dir() and kind == stat.S_IFDIR):
            raise InstallError("ZIP type and file mode disagree")
        if not item.is_dir():
            if type(item.file_size) is not int or item.file_size < 0:
                raise InstallError("Invalid ZIP file size")
            unpacked += item.file_size
            file_names.add(folded)
            if unpacked > spec.unpacked_bytes:
                raise InstallError("ZIP exceeds declared unpacked size")
        selected.append((item, relative))
    if unpacked != spec.unpacked_bytes:
        raise InstallError("ZIP does not match declared unpacked size")
    if any(
        "/".join(path.split("/")[:i]) in file_names
        for path in seen for i in range(1, len(path.split("/")))
    ):
        raise InstallError("ZIP file occupies parent directory")
    return selected


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_version(target: Path, spec: PinnedZip) -> int:
    """Verify every installed byte; a metadata sidecar alone is not a receipt."""
    _no_linked_ancestors(target)
    if not target.is_dir():
        raise InstallError("Existing installation is not a directory")
    meta = target / _MANIFEST
    if meta.is_symlink() or not meta.is_file() or meta.stat().st_size > 1024 * 1024:
        raise InstallError("Installed manifest missing or invalid")
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        if (set(data) != {"component", "version", "sha256", "files"}
                or data["component"] != spec.component or data["version"] != spec.version
                or data["sha256"] != spec.sha256.lower()
                or not isinstance(data["files"], list)
                or not 0 < len(data["files"]) <= MAX_FILES):
            raise InstallError("Installed version manifest doesn't match pin")
        seen: set[str] = set()
        total = 0
        for row in data["files"]:
            if not isinstance(row, dict) or set(row) != {"path", "sha256", "size"}:
                raise InstallError("Invalid installed file record")
            relative = _safe_relative(row["path"])
            if relative.casefold() in seen or not _SHA.fullmatch(row["sha256"]):
                raise InstallError("Duplicate or invalid installed file")
            seen.add(relative.casefold())
            path = target.joinpath(*relative.split("/"))
            _no_linked_ancestors(path)
            if (not path.is_file() or path.stat().st_nlink != 1
                    or type(row["size"]) is not int or path.stat().st_size != row["size"]
                    or _file_digest(path) != row["sha256"]):
                raise InstallError("Installed file content no longer matches pin")
            total += row["size"]
        if total != spec.unpacked_bytes:
            raise InstallError("Installed size no longer matches pin")
        discovered = set()
        for root, dirs, files in os.walk(target, followlinks=False):
            for folder in dirs:
                path = Path(root) / folder
                if path.is_symlink() or (
                        hasattr(path, "is_junction") and path.is_junction()):
                    raise InstallError("Installed directory is linked")
            for filename in files:
                relative = (Path(root) / filename).relative_to(target).as_posix()
                if relative != _MANIFEST:
                    discovered.add(relative.casefold())
                    if len(discovered) > MAX_FILES:
                        raise InstallError("Unexpected installed file count")
        if discovered != seen:
            raise InstallError("Installed version has unexpected files")
        return len(seen)
    except (KeyError, TypeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError("Installed manifest cannot be verified") from exc



@dataclass(frozen=True)
class InstallInspection:
    """Read-only assessment, never equivalent to an executable health check."""

    status: str
    directory: Path
    file_count: int = 0


def inspect_versioned_zip(spec: PinnedZip, *, install_root: Path) -> InstallInspection:
    """Check a pinned local payload without downloading, creating or repairing.

    The result may be stale by the time the install begins; install_versioned_zip
    re-verifies under its destination lock. A verified payload is not a working
    Ollama, Git or OpenCode binary until the native health probe passes.
    """
    spec.validate()
    if not isinstance(install_root, Path) or not install_root.is_absolute():
        raise InstallError("Absolute installation root required")
    target = (
        install_root / "components" / spec.component /
        (spec.version + "-" + spec.sha256[:16].lower())
    )
    try:
        _no_linked_ancestors(target)
        if not target.exists():
            return InstallInspection("NOT_INSTALLED", target)
        files = _inspect_version(target, spec)
    except (InstallError, OSError):
        return InstallInspection("BLOCKED_INVALID", target)
    return InstallInspection("VERIFIED_PAYLOAD", target, files)


def install_versioned_zip(
    spec: PinnedZip, *, install_root: Path, cache_root: Path,
    authorized: bool, cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> InstallResult:
    """Install a manifest-pinned component once without replacing user content.

    This provides a versioned unpacked payload, NOT a healthy runnable tool.
    Explicit authorization is required and runtime health checks come later.
    """
    spec.validate()
    if authorized is not True:
        raise InstallError("Installation requires an explicit authorized mandate")
    if not isinstance(install_root, Path) or not install_root.is_absolute():
        raise InstallError("Absolute installation root required")
    if not isinstance(cache_root, Path) or not cache_root.is_absolute():
        raise InstallError("Absolute cache root required")

    versions = install_root / "components" / spec.component
    target = versions / (spec.version + "-" + spec.sha256[:16].lower())
    _no_linked_ancestors(versions)
    _no_linked_ancestors(cache_root)
    versions.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    _no_linked_ancestors(versions)
    _no_linked_ancestors(cache_root)
    with _target_lock(target):
        if target.exists() or target.is_symlink():
            return InstallResult("REUSED", target, _inspect_version(target, spec))
        archive_path = cache_root / (
            spec.component + "-" + spec.version + "-" + spec.sha256.lower() + ".zip"
        )
        archive_path = download_pinned(
            spec.url, archive_path, sha256=spec.sha256,
            size=spec.download_bytes, manifest_id=spec.component + ":" + spec.version,
            cancelled=cancelled, progress=progress,
        )
        _no_linked_ancestors(archive_path)
        stage: Path | None = None
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = _safe_members(archive, spec)
                if shutil.disk_usage(versions).free < spec.unpacked_bytes + 128 * 1024**2:
                    raise InstallError("Insufficient disk space to unpack pinned component")
                stage = Path(tempfile.mkdtemp(prefix=".atlas-stage-", dir=versions))
                records = []
                for info, relative in members:
                    if cancelled is not None and cancelled():
                        raise InstallError("Installation cancelled; cached ZIP retained")
                    output = stage.joinpath(*relative.split("/"))
                    if info.is_dir():
                        output.mkdir(parents=True, exist_ok=True)
                        continue
                    output.parent.mkdir(parents=True, exist_ok=True)
                    sha = hashlib.sha256()
                    remaining = info.file_size
                    with archive.open(info) as source, output.open("xb") as dest:
                        while remaining:
                            if cancelled is not None and cancelled():
                                raise InstallError("Installation cancelled; cached ZIP retained")
                            chunk = source.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise InstallError("Truncated ZIP member")
                            remaining -= len(chunk)
                            dest.write(chunk)
                            sha.update(chunk)
                        if source.read(1):
                            raise InstallError("ZIP member exceeded declared size")
                        dest.flush()
                        os.fsync(dest.fileno())
                    records.append({
                        "path": relative, "sha256": sha.hexdigest(), "size": info.file_size,
                    })
                payload = {
                    "component": spec.component, "version": spec.version,
                    "sha256": spec.sha256.lower(), "files": records,
                }
                with (stage / _MANIFEST).open("x", encoding="utf-8") as stream:
                    json.dump(payload, stream, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            if target.exists() or target.is_symlink():
                raise InstallError("Installed target appeared while staging")
            os.rename(stage, target)
            stage = None
            return InstallResult("INSTALLED", target, len(records))
        except (OSError, zipfile.BadZipFile, EOFError, RuntimeError) as exc:
            raise InstallError("Pinned archive cannot be safely installed") from exc
        finally:
            if stage is not None:
                shutil.rmtree(stage)
