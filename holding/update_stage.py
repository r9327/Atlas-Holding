"""Safely unpack a release archive into an isolated, immutable version slot.

This is a staging primitive, NOT an updater/installer. The caller must first
authenticate its release manifest and provide the SHA-256 pinned by that trust
decision. Neither the active release nor the system configuration is changed.
A separate, privileged Windows helper must handle switch/health/rollback.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Callable
import zipfile

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_RELEASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_INVALID = frozenset('<>:"\\|?*')
_RESERVED = frozenset(
    ("CON", "PRN", "AUX", "NUL")
    + tuple("COM" + str(i) for i in range(1, 10))
    + tuple("LPT" + str(i) for i in range(1, 10))
)
_MARKER = ".atlas-stage.json"
_STAGE_SCHEMA = "atlas-staged-release/v2"
_TREE_SCHEMA = b"atlas-slot-tree/v1\0"
_MAX_FILES = 10000
_MAX_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024


class ReleaseStagingError(ValueError):
    """An archive, destination or release policy is invalid."""


@dataclass(frozen=True, slots=True)
class StagedRelease:
    release: str
    directory: Path
    archive_sha256: str
    files: int
    unpacked_bytes: int


@dataclass(frozen=True, slots=True)
class _SlotInventory:
    files: int
    unpacked_bytes: int
    tree_sha256: str


def _parts(name: str, *, directory: bool) -> tuple[str, ...]:
    if not isinstance(name, str) or not 1 <= len(name) <= 240:
        raise ReleaseStagingError("invalid archive path")
    if directory:
        if not name.endswith("/"):
            raise ReleaseStagingError("invalid directory entry")
        name = name[:-1]
    elif name.endswith("/"):
        raise ReleaseStagingError("invalid file entry")
    parts = tuple(name.split("/"))
    if (
        not parts or parts[0].casefold() == _MARKER.casefold()
        or any(
            not part or part in (".", "..") or len(part) > 120
            or part[-1] in " ."
            or part.split(".", 1)[0].upper() in _RESERVED
            or any(ord(ch) < 32 or ch in _INVALID for ch in part)
            for part in parts
        )
    ):
        raise ReleaseStagingError("unsafe archive path")
    return parts


def _index_entries(archive: zipfile.ZipFile, *, max_files: int,
                   max_unpacked_bytes: int) -> tuple[list[tuple[zipfile.ZipInfo, tuple[str, ...]]], int]:
    entries: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
    nodes: dict[str, tuple[str, str]] = {}
    explicit: set[str] = set()
    files = 0
    total = 0
    for entry in archive.infolist():
        if len(entries) >= max_files + 4096:
            raise ReleaseStagingError("too many archive entries")
        # On Windows, ZipInfo normalizes backslashes to '/' while reading.
        # Check the original central-directory name or malicious separators
        # would be accepted without ever reaching our path validation.
        if entry.orig_filename != entry.filename:
            raise ReleaseStagingError("normalized ZIP entry path is forbidden")
        is_dir = entry.is_dir()
        parts = _parts(entry.filename, directory=is_dir)
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if mode not in (0, stat.S_IFREG if not is_dir else stat.S_IFDIR):
            raise ReleaseStagingError("archive contains a symlink or special file")
        if entry.flag_bits & 1:
            raise ReleaseStagingError("encrypted archive entry")
        if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ReleaseStagingError("unsupported archive compression")
        for depth in range(1, len(parts) + 1):
            full = "/".join(parts[:depth])
            key = full.casefold()
            kind = "directory" if depth < len(parts) or is_dir else "file"
            value = (full, kind)
            if key in nodes and nodes[key] != value:
                raise ReleaseStagingError("conflicting or case-colliding archive paths")
            nodes[key] = value
            if len(nodes) > max_files + 4096:
                raise ReleaseStagingError("too many archive paths")
        full_key = "/".join(parts).casefold()
        if full_key in explicit:
            raise ReleaseStagingError("duplicate archive path")
        explicit.add(full_key)
        if not is_dir:
            files += 1
            if files > max_files:
                raise ReleaseStagingError("too many archived files")
            if entry.file_size < 0 or entry.file_size > max_unpacked_bytes - total:
                raise ReleaseStagingError("unpacked release exceeds size budget")
            total += entry.file_size
        entries.append((entry, parts))
    if files == 0:
        raise ReleaseStagingError("empty release archive")
    return entries, total


def _slot_inventory(
    root: Path,
    *,
    max_files: int = _MAX_FILES,
    max_unpacked_bytes: int = _MAX_UNPACKED_BYTES,
) -> _SlotInventory:
    """Hash the exact regular-file/directory tree without following links."""
    pending = [root]
    entries: list[tuple[str, Path, bool]] = []
    nodes: dict[str, tuple[str, str]] = {}
    while pending:
        folder = pending.pop()
        try:
            children = list(folder.iterdir())
        except OSError as exc:
            raise ReleaseStagingError("staged tree cannot be inspected") from exc
        for child in children:
            if child.parent == root and child.name == _MARKER:
                continue
            try:
                metadata = child.lstat()
            except OSError as exc:
                raise ReleaseStagingError("staged tree cannot be inspected") from exc
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or attributes & reparse:
                raise ReleaseStagingError("staged tree contains a link or reparse point")
            is_directory = stat.S_ISDIR(metadata.st_mode)
            if not is_directory and not stat.S_ISREG(metadata.st_mode):
                raise ReleaseStagingError("staged tree contains a special file")
            if not is_directory and metadata.st_nlink != 1:
                raise ReleaseStagingError("staged tree contains a hard-linked file")
            relative = child.relative_to(root).as_posix()
            parts = _parts(relative + ("/" if is_directory else ""), directory=is_directory)
            canonical = "/".join(parts)
            key = canonical.casefold()
            kind = "directory" if is_directory else "file"
            value = (canonical, kind)
            if key in nodes and nodes[key] != value:
                raise ReleaseStagingError("staged tree contains conflicting paths")
            nodes[key] = value
            entries.append((canonical, child, is_directory))
            if len(entries) > max_files + 4096:
                raise ReleaseStagingError("staged tree has too many entries")
            if is_directory:
                pending.append(child)

    digest = sha256(_TREE_SCHEMA)
    files = 0
    total = 0
    try:
        ordered = sorted(entries, key=lambda item: item[0].encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ReleaseStagingError("staged tree contains an invalid path") from exc
    for relative, path, is_directory in ordered:
        encoded = relative.encode("utf-8")
        digest.update(b"D" if is_directory else b"F")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        if is_directory:
            continue
        files += 1
        if files > max_files:
            raise ReleaseStagingError("staged tree has too many files")
        file_digest = sha256()
        size = 0
        try:
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    size += len(block)
                    if size > max_unpacked_bytes - total:
                        raise ReleaseStagingError("staged tree exceeds size budget")
                    file_digest.update(block)
        except OSError as exc:
            raise ReleaseStagingError("staged tree cannot be read") from exc
        total += size
        digest.update(size.to_bytes(8, "big"))
        digest.update(file_digest.digest())
    if files == 0:
        raise ReleaseStagingError("staged tree contains no files")
    return _SlotInventory(files, total, digest.hexdigest())


def stage_release_archive(
    archive_file: Path,
    install_root: Path,
    *,
    release: str,
    expected_sha256: str,
    max_files: int = _MAX_FILES,
    max_unpacked_bytes: int = _MAX_UNPACKED_BYTES,
    _materialize: Callable[[Path], None] | None = None,
) -> StagedRelease:
    """Extract a SHA-pinned ZIP only into a NEW version directory.

    Refuses symlinks, traversal, Windows device names, duplicate/case-colliding
    paths, compressed bombs exceeding declared AND actual limits and existing
    release directories. Leaves the active installation unchanged. Caller must
    authenticate expected_sha256 independently of any downloaded manifest.
    """
    if not isinstance(archive_file, Path) or not isinstance(install_root, Path):
        raise ReleaseStagingError("paths must be Path instances")
    if (not isinstance(release, str) or not _RELEASE.fullmatch(release)
            or release in (".", "..") or release[-1] in " ."
            or release.split(".", 1)[0].upper() in _RESERVED):
        raise ReleaseStagingError("invalid release ID")
    if not isinstance(expected_sha256, str) or not _SHA.fullmatch(expected_sha256):
        raise ReleaseStagingError("invalid expected archive hash")
    if (type(max_files) is not int or not 1 <= max_files <= _MAX_FILES
            or type(max_unpacked_bytes) is not int
            or not 1 <= max_unpacked_bytes <= _MAX_UNPACKED_BYTES
            or (_materialize is not None and not callable(_materialize))):
        raise ReleaseStagingError("invalid extraction quota")
    if archive_file.is_symlink() or not archive_file.is_file():
        raise ReleaseStagingError("archive must be a regular, non-symlink file")
    if install_root.is_symlink() or (install_root.exists() and not install_root.is_dir()):
        raise ReleaseStagingError("unsafe installation root")
    version_root = install_root / "versions"
    if version_root.is_symlink() or (version_root.exists() and not version_root.is_dir()):
        raise ReleaseStagingError("unsafe version directory")
    target = version_root / release
    if target.exists() or target.is_symlink():
        raise ReleaseStagingError("release already exists")
    # Verify the entire archive before creating any install folders.
    digest = sha256()
    with archive_file.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected_sha256:
        raise ReleaseStagingError("archive SHA-256 mismatch")
    version_root.mkdir(parents=True, exist_ok=True)
    if version_root.is_symlink() or target.exists() or target.is_symlink():
        raise ReleaseStagingError("unsafe version directory")
    temporary = Path(tempfile.mkdtemp(prefix=".atlas-stage-", dir=version_root))
    published = False
    try:
        with zipfile.ZipFile(archive_file) as bundle:
            entries, declared_total = _index_entries(
                bundle, max_files=max_files, max_unpacked_bytes=max_unpacked_bytes,
            )
            actual_total = 0
            count = 0
            for entry, parts in entries:
                destination = temporary.joinpath(*parts)
                if entry.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with bundle.open(entry, "r") as source, destination.open("xb") as output:
                    while block := source.read(1024 * 1024):
                        written += len(block)
                        actual_total += len(block)
                        if written > entry.file_size or actual_total > max_unpacked_bytes:
                            raise ReleaseStagingError("archive expanded beyond declared quota")
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                if written != entry.file_size:
                    raise ReleaseStagingError("archive file size mismatch")
                count += 1
        if actual_total != declared_total:
            raise ReleaseStagingError("archive totals mismatch")
        if _materialize is not None:
            _materialize(temporary)
        inventory = _slot_inventory(
            temporary, max_files=max_files,
            max_unpacked_bytes=max_unpacked_bytes,
        )
        if (_materialize is None and (
                inventory.files != count
                or inventory.unpacked_bytes != actual_total)):
            raise ReleaseStagingError("staged tree differs from extracted archive")
        receipt = {
            "schema": _STAGE_SCHEMA,
            "release": release,
            "archive_sha256": expected_sha256,
            "files": inventory.files,
            "unpacked_bytes": inventory.unpacked_bytes,
            "tree_sha256": inventory.tree_sha256,
        }
        with (temporary / _MARKER).open("x", encoding="utf-8") as marker:
            json.dump(receipt, marker, sort_keys=True, separators=(",", ":"))
            marker.flush()
            os.fsync(marker.fileno())
        if target.exists() or target.is_symlink():
            raise ReleaseStagingError("release appeared while staging")
        os.rename(temporary, target)
        published = True
        return StagedRelease(
            release, target, expected_sha256,
            inventory.files, inventory.unpacked_bytes,
        )
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ReleaseStagingError("release archive could not be safely staged") from exc
    finally:
        if not published:
            shutil.rmtree(temporary, ignore_errors=True)
