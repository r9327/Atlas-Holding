"""Streaming, resumable artifact downloads for the *staging* area.

This never installs software or runs a downloaded artifact. The updater must
authenticate the manifest, choose a trusted staging directory, and validate
the application before any version switch or rollback.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)\Z")
_MAX_BYTES = 1 << 40


class ArtifactDownloadError(RuntimeError):
    pass


def _check_url(url: str, *, allow_test_loopback: bool) -> None:
    if not isinstance(url, str) or len(url) > 8192:
        raise ArtifactDownloadError("invalid artifact URL")
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
        raise ArtifactDownloadError("unsafe artifact URL")
    if parsed.scheme == "https":
        return
    if (allow_test_loopback and parsed.scheme == "http"
            and parsed.hostname in ("localhost", "127.0.0.1", "::1")):
        return
    raise ArtifactDownloadError("HTTPS is required")


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Reject unsafe redirected URLs before opening the next connection."""

    def __init__(self, *, allow_test_loopback: bool) -> None:
        super().__init__()
        self.allow_test_loopback = allow_test_loopback

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl, allow_test_loopback=self.allow_test_loopback)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(request: Request, *, timeout: float, allow_test_loopback: bool):
    # Keep the redirect policy scoped to one request, not process-global.
    return build_opener(
        _SafeRedirectHandler(allow_test_loopback=allow_test_loopback)
    ).open(request, timeout=timeout)


def _strong_etag(value: str | None) -> str | None:
    if (isinstance(value, str) and len(value) <= 200
            and value.startswith('"') and value.endswith('"')
            and not any(ch in value for ch in "\r\n")):
        return value
    return None


def download_staged_artifact(
    url: str,
    destination: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    timeout: float = 30.0,
    chunk_bytes: int = 1024 * 1024,
    allow_test_loopback: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download to destination.part; replace destination only after size/hash match.

    A partial download is resumed only when saved strong ETag and server's
    Content-Range agree. A full HTTP 200 response restarts from byte zero.
    The returned path is an inert staged blob, never an installer.
    """
    _check_url(url, allow_test_loopback=allow_test_loopback)
    if not isinstance(destination, Path):
        raise ArtifactDownloadError("destination must be a Path")
    if (not isinstance(expected_sha256, str) or not _SHA.fullmatch(expected_sha256)
            or type(expected_size) is not int or not 0 <= expected_size <= _MAX_BYTES
            or not 0 < timeout <= 300 or not 4096 <= chunk_bytes <= 8 * 1024 * 1024):
        raise ArtifactDownloadError("invalid download requirements")
    if on_progress is not None and not callable(on_progress):
        raise ArtifactDownloadError("invalid progress callback")
    if destination.exists() or destination.is_symlink():
        raise ArtifactDownloadError("staged destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    etag_file = destination.with_name(destination.name + ".part.etag")
    if partial.is_symlink() or etag_file.is_symlink():
        raise ArtifactDownloadError("unsafe staging files")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > expected_size:
        raise ArtifactDownloadError("partial file exceeds expected size")
    if partial.exists() and offset == expected_size:
        # Power loss can happen after fsync but before the final rename. A
        # locally complete file can be promoted only after full SHA-256 check.
        complete_digest = hashlib.sha256()
        with partial.open("rb") as completed:
            for block in iter(lambda: completed.read(chunk_bytes), b""):
                complete_digest.update(block)
        if complete_digest.hexdigest() == expected_sha256:
            if destination.exists() or destination.is_symlink():
                raise ArtifactDownloadError("staged destination appeared during recovery")
            os.replace(partial, destination)
            etag_file.unlink(missing_ok=True)
            if on_progress is not None:
                on_progress(expected_size, expected_size)
            return destination
        # Wrong content of the right size must never be resumed with Range.
        partial.unlink()
        etag_file.unlink(missing_ok=True)
        offset = 0
    saved_etag = None
    if offset and etag_file.exists():
        saved_etag = _strong_etag(etag_file.read_text(encoding="utf-8"))
    if offset and not saved_etag:
        offset = 0
    headers = {"Accept-Encoding": "identity", "User-Agent": "AtlasHoldingUpdater/1"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
        headers["If-Range"] = saved_etag
    request = Request(url, headers=headers)
    try:
        with _open_url(request, timeout=timeout, allow_test_loopback=allow_test_loopback) as response:
            _check_url(response.geturl(), allow_test_loopback=allow_test_loopback)
            status = response.status
            if status not in (200, 206):
                raise ArtifactDownloadError(f"unexpected HTTP {status}")
            if status == 206:
                if not offset:
                    raise ArtifactDownloadError("unexpected partial HTTP response")
                match = _RANGE.fullmatch(response.headers.get("Content-Range", ""))
                if (not match or tuple(map(int, match.groups()))
                        != (offset, expected_size - 1, expected_size)):
                    raise ArtifactDownloadError("invalid Content-Range")
                if _strong_etag(response.headers.get("ETag")) != saved_etag:
                    raise ArtifactDownloadError("resumed ETag changed")
            else:
                offset = 0  # server ignored Range, or ordinary fresh download
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    declared = int(length)
                except ValueError as exc:
                    raise ArtifactDownloadError("invalid Content-Length") from exc
                if declared != expected_size - offset:
                    raise ArtifactDownloadError("download size differs from manifest")
            current_etag = _strong_etag(response.headers.get("ETag"))
            if offset == 0:
                if current_etag:
                    etag_file.write_text(current_etag, encoding="utf-8")
                else:
                    etag_file.unlink(missing_ok=True)
            digest = hashlib.sha256()
            if offset:
                with partial.open("rb") as old:
                    for block in iter(lambda: old.read(chunk_bytes), b""):
                        digest.update(block)
            received = offset
            with partial.open("ab" if offset else "wb") as file:
                if on_progress is not None:
                    on_progress(received, expected_size)
                while True:
                    block = response.read(chunk_bytes)
                    if not block:
                        break
                    received += len(block)
                    if received > expected_size:
                        raise ArtifactDownloadError("response exceeds manifest size")
                    digest.update(block)
                    file.write(block)
                    if on_progress is not None:
                        on_progress(received, expected_size)
                file.flush()
                os.fsync(file.fileno())
            if received != expected_size:
                raise ArtifactDownloadError("incomplete download")
            if digest.hexdigest() != expected_sha256:
                partial.unlink(missing_ok=True)
                etag_file.unlink(missing_ok=True)
                raise ArtifactDownloadError("SHA-256 mismatch")
            if destination.exists() or destination.is_symlink():
                raise ArtifactDownloadError("staged destination appeared during download")
            os.replace(partial, destination)
            etag_file.unlink(missing_ok=True)
            return destination
    except (ArtifactDownloadError, OSError):
        raise
