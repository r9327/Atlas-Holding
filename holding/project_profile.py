"""Pure-data v1 project profiles; never grants permission to execute commands.

A profile describes a project.  Authorization, process spawning and Git writes
belong to the existing orchestrator and its privileged execution boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping

SCHEMA = "project-profile/v1"
_ID = re.compile(r"[a-z][a-z0-9-]{1,47}\Z", re.ASCII)
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z", re.ASCII)
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z", re.ASCII)
_ALLOWED_STAGES = frozenset({"test", "lint", "build"})
_ALLOWED_ADAPTERS = frozenset({"doctor", "graphify", "ai-context"})
_MAX_COMMAND_PARTS = 32
_MAX_PART_LENGTH = 4096


class InvalidProjectProfile(ValueError):
    """A profile is malformed or requires unsupported capabilities."""


def _safe_ref(value: object) -> str:
    if not isinstance(value, str) or not _REF.fullmatch(value):
        raise InvalidProjectProfile("branch must be a valid Git work branch")
    if value.lower() in {"main", "master"} or any(
        fragment in value for fragment in ("..", "//", "@{", "\\")
    ) or value.endswith(("/", ".", ".lock")):
        raise InvalidProjectProfile("unsafe branch name")
    return value


def _safe_directory(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise InvalidProjectProfile("checkout_dir must be a single safe directory name")
    return value


def _safe_commands(raw: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(raw, dict) or not raw:
        raise InvalidProjectProfile("commands must have at least one stage")
    if set(raw) - _ALLOWED_STAGES:
        raise InvalidProjectProfile("unsupported stage")
    normalized = []
    for stage, argv in sorted(raw.items()):
        if (not isinstance(argv, list) or not argv or
            len(argv) > _MAX_COMMAND_PARTS or
            any(not isinstance(a, str) or not a or len(a) > _MAX_PART_LENGTH or
                any(ch in a for ch in "\x00\r\n") for a in argv)):
            raise InvalidProjectProfile(f"invalid argv for {stage}")
        # Parser accepts data, not shell strings; executor must still authorize it.
        normalized.append((stage, tuple(argv)))
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class ProjectProfile:
    name: str
    repository: str
    branch: str
    checkout_dir: str
    commands: tuple[tuple[str, tuple[str, ...]], ...]
    adapters: tuple[str, ...]

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> "ProjectProfile":
        if not isinstance(payload, dict):
            raise InvalidProjectProfile("profile must be a JSON object")
        expected = {"schema", "name", "repository", "branch", "checkout_dir", "commands", "adapters"}
        if set(payload) != expected or payload.get("schema") != SCHEMA:
            raise InvalidProjectProfile("unexpected fields or schema")
        name = payload["name"]
        repository = payload["repository"]
        if not isinstance(name, str) or not _ID.fullmatch(name):
            raise InvalidProjectProfile("invalid name")
        if not isinstance(repository, str) or not _REPO.fullmatch(repository) or ".." in repository:
            raise InvalidProjectProfile("invalid repository")
        adapters = payload["adapters"]
        if (not isinstance(adapters, list) or
            len(adapters) != len(set(a for a in adapters if isinstance(a, str))) or
            any(not isinstance(a, str) or a not in _ALLOWED_ADAPTERS for a in adapters)):
            raise InvalidProjectProfile("unknown or repeated adapter")
        return cls(
            name=name,
            repository=repository,
            branch=_safe_ref(payload["branch"]),
            checkout_dir=_safe_directory(payload["checkout_dir"]),
            commands=_safe_commands(payload["commands"]),
            adapters=tuple(sorted(adapters)),
        )

    def canonical_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA, "name": self.name, "repository": self.repository,
            "branch": self.branch, "checkout_dir": self.checkout_dir,
            "commands": {name: list(argv) for name, argv in self.commands},
            "adapters": list(self.adapters),
        }

    @property
    def digest(self) -> str:
        data = json.dumps(self.canonical_dict(), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
        return sha256(data).hexdigest()


def load_profile(text: str) -> ProjectProfile:
    if not isinstance(text, str) or len(text) > 65536:
        raise InvalidProjectProfile("profile text is not bounded")
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise InvalidProjectProfile("invalid JSON") from exc
    return ProjectProfile.parse(payload)
