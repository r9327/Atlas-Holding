"""Durable multi-project profile registry for Holding (trusted operator data only).

Registry actions select configuration; they DO NOT launch agents, execute
commands, edit Git or grant any new capability. An orchestrator must authorize
the repository/branch and adapter independently before running a mission.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

from .project_profile import (
    InvalidProjectProfile, ProjectProfile, load_profile,
)

_SCHEMA = "holding-project-registry/v1"
_MAX_BYTES = 4 * 1024 * 1024
_MAX_PROFILES = 128


class ProjectRegistryError(ValueError):
    """Invalid registry state or unauthorized mutation request."""


@dataclass(frozen=True, slots=True)
class ProfileEntry:
    profile: ProjectProfile
    digest: str
    selected: bool


def _root(folder: Path) -> Path:
    if not isinstance(folder, Path) or folder.is_symlink():
        raise ProjectRegistryError("registry root is unsafe")
    folder.mkdir(parents=True, exist_ok=True)
    if not folder.is_dir():
        raise ProjectRegistryError("registry root must be a directory")
    return folder


def _read(folder: Path) -> dict:
    location = folder / "project-registry.json"
    if location.is_symlink():
        raise ProjectRegistryError("registry state is a symlink")
    if not location.exists():
        return {"schema": _SCHEMA, "selected": None, "profiles": {}}
    if location.stat().st_size > _MAX_BYTES:
        raise ProjectRegistryError("registry exceeds size limit")
    try:
        data = json.loads(location.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or set(data) != {"schema", "selected", "profiles"}
                or data["schema"] != _SCHEMA or
                not isinstance(data["profiles"], dict) or
                len(data["profiles"]) > _MAX_PROFILES):
            raise ProjectRegistryError("invalid registry structure")
        profiles: dict = data["profiles"]
        names = set()
        worktrees = set()
        for name, entry in profiles.items():
            if not isinstance(entry, dict) or set(entry) != {"digest", "data"}:
                raise ProjectRegistryError("invalid registry profile entry")
            profile = ProjectProfile.parse(entry["data"])
            if name != profile.name or profile.digest != entry["digest"]:
                raise ProjectRegistryError("profile identity or digest mismatch")
            if profile.checkout_dir.casefold() in worktrees:
                raise ProjectRegistryError("checkout directory reused by multiple projects")
            worktrees.add(profile.checkout_dir.casefold())
            names.add(name)
        if data["selected"] is not None and data["selected"] not in names:
            raise ProjectRegistryError("selected project is missing")
        return data
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError,
            InvalidProjectProfile) as exc:
        if isinstance(exc, ProjectRegistryError):
            raise
        raise ProjectRegistryError("registry content cannot be verified") from exc


def _write(folder: Path, content: dict) -> None:
    location = folder / "project-registry.json"
    if location.is_symlink():
        raise ProjectRegistryError("registry state is a symlink")
    serialized = json.dumps(content, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")).encode("utf-8")
    if len(serialized) > _MAX_BYTES:
        raise ProjectRegistryError("registry exceeds size limit")
    fd, temporary = tempfile.mkstemp(prefix=".registry-", dir=folder)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, location)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ProjectRegistry:
    def __init__(self, directory: Path):
        self.directory = _root(directory)

    def list_entries(self) -> tuple[ProfileEntry, ...]:
        data = _read(self.directory)
        return tuple(
            ProfileEntry(ProjectProfile.parse(entry["data"]), entry["digest"],
                         name == data["selected"])
            for name, entry in sorted(data["profiles"].items())
        )

    def selected(self) -> ProfileEntry | None:
        for entry in self.list_entries():
            if entry.selected:
                return entry
        return None

    def register(self, raw_profile: str, *, expected_digest: str | None = None) -> ProfileEntry:
        profile = load_profile(raw_profile)
        data = _read(self.directory)
        entries = data["profiles"]
        old = entries.get(profile.name)
        if old is not None and (expected_digest is None or old["digest"] != expected_digest):
            raise ProjectRegistryError("profile update requires its current digest")
        if old is None and expected_digest is not None:
            raise ProjectRegistryError("cannot replace a missing profile")
        for key, entry in entries.items():
            if key != profile.name and entry["data"]["checkout_dir"].casefold() == profile.checkout_dir.casefold():
                raise ProjectRegistryError("checkout directory already assigned to another project")
        if old is None and len(entries) >= _MAX_PROFILES:
            raise ProjectRegistryError("maximum profile count reached")
        entries[profile.name] = {"digest": profile.digest, "data": profile.canonical_dict()}
        _write(self.directory, data)
        return ProfileEntry(profile, profile.digest, data["selected"] == profile.name)

    def select(self, name: str, *, expected_digest: str) -> ProfileEntry:
        data = _read(self.directory)
        row = data["profiles"].get(name)
        if row is None or row["digest"] != expected_digest:
            raise ProjectRegistryError("profile changed or does not exist")
        data["selected"] = name
        _write(self.directory, data)
        return ProfileEntry(ProjectProfile.parse(row["data"]), row["digest"], True)

    def clear_selection(self) -> None:
        data = _read(self.directory)
        if data["selected"] is not None:
            data["selected"] = None
            _write(self.directory, data)
