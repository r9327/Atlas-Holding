"""Durable, conditional, read-only release update checks.

A fresh cache can suppress a redundant request for a short bounded time, but
never claims that a newer version doesn't exist until the server answers.
This helper does not download or install application payloads.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time

from .update_manifest_client import (
    ManifestCheck, ManifestFetchError, check_manifest,
)

_SCHEMA = "holding-update-probe-cache/v1"
_MAX_STATE_BYTES = 8192
_MAX_AGE_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class UpdateProbe:
    status: str  # UPDATED / UNCHANGED_VALIDATED / CACHE_NOT_REVALIDATED
    release: str
    channel: str
    etag: str | None
    manifest: object | None
    network_bytes: int


def _load(cache: Path, manifest_url: str) -> dict | None:
    if cache.is_symlink():
        raise ManifestFetchError("unsafe cached probe state")
    if not cache.exists():
        return None
    try:
        if cache.stat().st_size > _MAX_STATE_BYTES:
            raise ManifestFetchError("cached probe state too large")
        obj = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, ManifestFetchError):
            raise
        raise ManifestFetchError("invalid cached probe state") from exc
    if (not isinstance(obj, dict) or
            set(obj) != {"schema", "manifest_url_sha256", "release", "channel",
                         "last_verified_epoch", "etag"} or
            obj["schema"] != _SCHEMA or
            obj["manifest_url_sha256"] != sha256(manifest_url.encode("utf-8")).hexdigest() or
            not isinstance(obj["release"], str) or not 1 <= len(obj["release"]) <= 80 or
            obj["channel"] not in ("stable", "test") or
            type(obj["last_verified_epoch"]) not in (int, float) or
            not 0 <= obj["last_verified_epoch"] < 1e15 or
            (obj["etag"] is not None and (not isinstance(obj["etag"], str) or
             len(obj["etag"]) > 200))):
        # Switching channel/endpoint or malformed state must trigger a new
        # full check rather than pretending an unchanged release.
        return None
    return obj


def _save(cache: Path, manifest_url: str, response: ManifestCheck, now: float) -> None:
    manifest = response.manifest
    if manifest is None:
        raise ManifestFetchError("cannot save a response without validated manifest")
    data = {
        "schema": _SCHEMA,
        "manifest_url_sha256": sha256(manifest_url.encode("utf-8")).hexdigest(),
        "release": manifest.release,
        "channel": manifest.channel,
        "last_verified_epoch": now,
        "etag": response.etag,
    }
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(payload) > _MAX_STATE_BYTES or cache.is_symlink():
        raise ManifestFetchError("unsafe probe cache write")
    cache.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".probe-", dir=cache.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, cache)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def probe_release(
    manifest_url: str,
    *,
    cache_file: Path,
    max_cache_age_seconds: int = 30,
    now_epoch: float | None = None,
    allow_test_loopback: bool = False,
) -> UpdateProbe:
    """One request at most, or no request for short fresh cache.

    A 304 is useful only when a previous *validated* manifest is recorded.
    Fail closed on offline/network failures: don't claim UNCHANGED.
    """
    if not isinstance(cache_file, Path):
        raise ManifestFetchError("cache_file must be a Path")
    if (type(max_cache_age_seconds) is not int or
            not 0 <= max_cache_age_seconds <= _MAX_AGE_SECONDS):
        raise ManifestFetchError("invalid cache lifetime")
    now = time.time() if now_epoch is None else now_epoch
    if not isinstance(now, (int, float)) or not 0 <= now < 1e15:
        raise ManifestFetchError("invalid clock")
    old = _load(cache_file, manifest_url)
    if old is not None:
        age = now - old["last_verified_epoch"]
        if 0 <= age < max_cache_age_seconds:
            return UpdateProbe("CACHE_NOT_REVALIDATED", old["release"],
                               old["channel"], old["etag"], None, 0)
    response = check_manifest(manifest_url, previous_etag=old["etag"] if old else None,
                              allow_test_loopback=allow_test_loopback)
    if response.status == "UNCHANGED":
        if old is None or not old["etag"]:
            raise ManifestFetchError("unexpected 304 without trusted local state")
        # 304 proves the matching content is unchanged, but carries no new
        # manifest bytes; persist the verified time without altering release.
        restored = dict(old)
        restored["last_verified_epoch"] = now
        new_payload = json.dumps(restored, separators=(",", ":"), sort_keys=True).encode()
        if cache_file.is_symlink():
            raise ManifestFetchError("unsafe probe cache")
        fd, temp_name = tempfile.mkstemp(prefix=".probe-", dir=cache_file.parent)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(new_payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_name, cache_file)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return UpdateProbe("UNCHANGED_VALIDATED", old["release"], old["channel"],
                           old["etag"], None, 0)
    if response.manifest is None:
        raise ManifestFetchError("unexpected manifest response without metadata")
    _save(cache_file, manifest_url, response, now)
    return UpdateProbe("UPDATED", response.manifest.release, response.manifest.channel,
                       response.etag, response.manifest, response.bytes_received)
