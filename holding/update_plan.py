"""Offline release-delta planner; performs NO download or installation.

Caller must independently authenticate the manifest, verify local base hashes and
validate downloaded artifacts before any privileged updater can apply this plan.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

_SCHEMA = "atlas-update-manifest/v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_RELEASE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}\Z", re.ASCII)
_MAX_ARTIFACTS = 1024
_MAX_BYTES = 1 << 40  # sanity limit, not a download quota


class InvalidUpdateManifest(ValueError):
    pass


def _digest(value: object) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise InvalidUpdateManifest("invalid SHA-256 digest")
    return value


def _size(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_BYTES:
        raise InvalidUpdateManifest("invalid size")
    return value


def _name(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 220:
        raise InvalidUpdateManifest("invalid artifact name")
    if (value.startswith("/") or "\\" in value or
        any(part in ("", ".", "..") for part in value.split("/")) or
        any(ord(ch) < 32 or ch == ":" for ch in value)):
        raise InvalidUpdateManifest("unsafe artifact path")
    return value


@dataclass(frozen=True)
class Patch:
    from_sha256: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Artifact:
    path: str
    sha256: str
    size: int
    patches: tuple[Patch, ...]


@dataclass(frozen=True)
class UpdateManifest:
    release: str
    channel: str
    artifacts: tuple[Artifact, ...]

    @classmethod
    def parse(cls, payload: object) -> "UpdateManifest":
        if not isinstance(payload, dict) or set(payload) != {"schema", "release", "channel", "artifacts"}:
            raise InvalidUpdateManifest("unknown or missing top-level fields")
        if payload["schema"] != _SCHEMA:
            raise InvalidUpdateManifest("unsupported schema")
        release, channel, raw = payload["release"], payload["channel"], payload["artifacts"]
        if not isinstance(release, str) or not _RELEASE.fullmatch(release):
            raise InvalidUpdateManifest("invalid release identifier")
        if channel not in ("stable", "test"):
            raise InvalidUpdateManifest("invalid channel")
        if not isinstance(raw, list) or not 1 <= len(raw) <= _MAX_ARTIFACTS:
            raise InvalidUpdateManifest("invalid artifact count")
        artifacts = []
        names = set()
        for data in raw:
            if not isinstance(data, dict) or set(data) != {"path", "sha256", "size", "patches"}:
                raise InvalidUpdateManifest("invalid artifact")
            path = _name(data["path"])
            if path in names:
                raise InvalidUpdateManifest("duplicate artifact")
            names.add(path)
            patches_data = data["patches"]
            if not isinstance(patches_data, list) or len(patches_data) > 16:
                raise InvalidUpdateManifest("invalid patch count")
            patches = []
            sources = set()
            for patch in patches_data:
                if not isinstance(patch, dict) or set(patch) != {"from_sha256", "sha256", "size"}:
                    raise InvalidUpdateManifest("invalid patch")
                base = _digest(patch["from_sha256"])
                if base in sources:
                    raise InvalidUpdateManifest("duplicate patch base")
                sources.add(base)
                patches.append(Patch(base, _digest(patch["sha256"]), _size(patch["size"])))
            artifacts.append(Artifact(path, _digest(data["sha256"]), _size(data["size"]), tuple(patches)))
        return cls(release, channel, tuple(artifacts))


@dataclass(frozen=True)
class UpdateAction:
    path: str
    mode: str  # keep / full / patch
    download_sha256: str | None
    download_bytes: int


def plan_updates(manifest: UpdateManifest, installed_sha256: Mapping[str, str],
                 *, min_savings_bytes: int = 4096) -> tuple[UpdateAction, ...]:
    """Select smallest compatible payload; this plan is NOT an install permit."""
    if min_savings_bytes < 0:
        raise ValueError("min_savings_bytes must be nonnegative")
    actions = []
    for artifact in manifest.artifacts:
        current = installed_sha256.get(artifact.path)
        if current == artifact.sha256:
            actions.append(UpdateAction(artifact.path, "keep", None, 0))
            continue
        compatible = (p for p in artifact.patches if p.from_sha256 == current
                      and p.size + min_savings_bytes < artifact.size)
        best = min(compatible, key=lambda p: p.size, default=None)
        if best is None:
            actions.append(UpdateAction(artifact.path, "full", artifact.sha256, artifact.size))
        else:
            actions.append(UpdateAction(artifact.path, "patch", best.sha256, best.size))
    return tuple(actions)
