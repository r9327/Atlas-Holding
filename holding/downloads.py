"""Safe, restartable HTTP downloads for pinned setup artifacts.

This is NOT an Ollama model-blob downloader: model pulls must be validated
through Ollama's supported API/CLI and tested separately on Windows.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from http.client import HTTPException
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class DownloadError(Exception):
    """An unrecoverable or currently exhausted bootstrap download."""


class IntegrityError(DownloadError):
    """Downloaded bytes did not match the pinned manifest."""


class DownloadBusyError(DownloadError):
    """Another process currently owns this destination download."""


class InsufficientSpaceError(DownloadError):
    """The destination volume cannot safely hold the remaining bytes."""


class DownloadCancelled(DownloadError):
    """The caller cancelled cleanly; resumable bytes remain on disk."""


class TransferJournalError(DownloadError):
    """Persistent transfer metadata is invalid or belongs to another pin."""


_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$")
_TRANSIENT = {408, 425, 429, 500, 502, 503, 504}
_DEFAULT_FREE_RESERVE = 128 * 1024 * 1024


def _is_link_like(path: Path) -> bool:
    return path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    )


def _reject_linked_ancestors(path: Path) -> None:
    """Refuse existing symlink/junction parents before creating download data."""
    for ancestor in (path, *path.parents):
        if ancestor.exists() and _is_link_like(ancestor):
            raise DownloadError("Refusing download path below symlink or junction")


def _validate_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None:
        raise ValueError("URLs with embedded credentials are forbidden")
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname:
        try:
            if ipaddress.ip_address(parts.hostname).is_loopback:
                return  # integration tests, local services only
        except ValueError:
            if parts.hostname == "localhost":
                return
    raise ValueError("Download URL must use HTTPS (or HTTP loopback)")


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        if urlsplit(req.full_url).scheme == "https" and urlsplit(newurl).scheme != "https":
            raise DownloadError("HTTPS redirect to insecure transport forbidden")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fd:
        for data in iter(lambda: fd.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


@contextmanager
def _target_lock(target: Path):
    """Take a non-blocking advisory lock shared by Windows and POSIX peers."""
    lock_path = target.with_name(target.name + ".lock")
    if _is_link_like(lock_path):
        raise DownloadError("Refusing symlinked download lock")
    handle = lock_path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise DownloadBusyError(
                "Another process is already downloading this destination"
            ) from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _require_disk_space(parent: Path, remaining: int, reserve: int) -> None:
    if reserve < 0:
        raise ValueError("Free-space reserve must not be negative")
    free = shutil.disk_usage(parent).free
    if free < remaining + reserve:
        raise InsufficientSpaceError(
            f"Destination volume has {free} free bytes; "
            f"download requires {remaining} plus {reserve} bytes reserve"
        )


def _canonical_journal_url(url: str) -> str:
    parts = urlsplit(url)
    # Query strings commonly contain expiring signatures. They are needed for
    # the request but must never be copied into persistent diagnostic state.
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class _TransferJournal:
    """Atomic sidecar describing one pinned transfer across restarts."""

    def __init__(self, target: Path, *, manifest_id: str, url: str,
                 sha256: str, size: int):
        if (not isinstance(manifest_id, str) or not manifest_id
                or len(manifest_id) > 200 or not manifest_id.isprintable()):
            raise ValueError("Manifest ID must be 1-200 printable characters")
        self.path = target.with_name(target.name + ".transfer.json")
        self.identity = {
            "manifest_id": manifest_id,
            "url": _canonical_journal_url(url),
            "sha256": sha256,
            "size": size,
        }
        self.attempts = 0
        if self.path.exists():
            if _is_link_like(self.path) or not self.path.is_file():
                raise TransferJournalError("Transfer journal is not a regular file")
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise TransferJournalError("Transfer journal is unreadable") from exc
            if not isinstance(payload, dict):
                raise TransferJournalError("Transfer journal must contain an object")
            actual = {key: payload.get(key) for key in self.identity}
            if actual != self.identity:
                raise TransferJournalError(
                    "Transfer journal does not match the pinned manifest"
                )
            attempts = payload.get("attempts", 0)
            if type(attempts) is not int or attempts < 0:
                raise TransferJournalError("Transfer journal attempt count is invalid")
            self.attempts = attempts

    def record(self, state: str, partial_bytes: int, *, error: str = "",
               new_attempt: bool = False) -> None:
        if new_attempt:
            self.attempts += 1
        payload = {
            **self.identity,
            "state": state,
            "partial_bytes": partial_bytes,
            "attempts": self.attempts,
            "error": error[:200],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent,
                prefix=self.path.name + ".", suffix=".tmp", delete=False,
            ) as handle:
                temporary_name = handle.name
                json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
            temporary_name = None
        except OSError as exc:
            raise TransferJournalError("Could not persist transfer journal") from exc
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink()
                except OSError:
                    pass


def _error_label(error: Exception | None) -> str:
    if error is None:
        return "UnknownTransferError"
    if isinstance(error, HTTPError):
        return f"HTTP_{error.code}"
    return type(error).__name__


def _publish_without_overwrite(partial: Path, target: Path, *,
                               expected: str, size: int) -> Path:
    """Atomically publish verified bytes without replacing a concurrent file."""
    try:
        if os.link in os.supports_follow_symlinks:
            os.link(partial, target, follow_symlinks=False)
        else:
            os.link(partial, target)
    except FileExistsError:
        if (_is_link_like(target) or not target.is_file()
                or target.stat().st_size != size or _sha256(target) != expected):
            raise IntegrityError(
                "Destination appeared during download and does not match manifest"
            )
    else:
        # The target is now an atomic hard link to the already verified bytes.
        # Removing the resumable name leaves one ordinary destination file.
        partial.unlink()
        return target

    # A concurrent writer published the same trusted bytes first.
    if partial.exists():
        partial.unlink()
    return target


def download_pinned(
    url: str,
    destination: str | Path,
    *,
    sha256: str,
    size: int,
    max_attempts: int = 12,
    timeout: float = 45.0,
    retry_delay: float = 3.0,
    free_space_reserve: int = _DEFAULT_FREE_RESERVE,
    manifest_id: str | None = None,
    cancelled=None,
    progress=None,
) -> Path:
    """Download fixed-content asset with resume, checksum and atomic publish.

    Keeps <destination>.part on transient failures and across restarts.
    A server returning HTTP 200 to a Range request is handled by truncating
    and restarting the partial file, never concatenating unrelated bytes.
    max_attempts limits one call; caller can retry during a later session.
    The expected SHA-256 and byte count must come from a trusted manifest.
    """
    _validate_url(url)
    if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
        raise ValueError("Expected SHA-256 must be 64 hex characters")
    if (size < 0 or max_attempts < 1 or timeout <= 0 or retry_delay < 0
            or free_space_reserve < 0):
        raise ValueError("Invalid download limits")
    expected = sha256.lower()
    target = Path(destination)
    if _is_link_like(target):
        raise DownloadError("Refusing symlinked destination")
    if target.exists() and not target.is_file():
        raise DownloadError("Destination is not a regular file")
    _reject_linked_ancestors(target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_linked_ancestors(target.parent)
    with _target_lock(target):
        partial = target.with_name(target.name + ".part")
        if _is_link_like(partial) or _is_link_like(target):
            raise DownloadError("Refusing symlinked download path")
        journal = _TransferJournal(
            target,
            manifest_id=manifest_id if manifest_id is not None else expected,
            url=url,
            sha256=expected,
            size=size,
        )
        if target.exists():
            if target.stat().st_size == size and _sha256(target) == expected:
                journal.record("COMPLETE", size)
                return target
            journal.record("FAILED", 0, error="IntegrityError")
            raise IntegrityError("Existing destination does not match pinned checksum")
        if partial.exists() and not partial.is_file():
            raise DownloadError("Partial destination is not a regular file")
        if partial.exists() and partial.stat().st_nlink != 1:
            raise DownloadError("Partial destination must not have hard links")

        opener = build_opener(_SafeRedirect())
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            if cancelled is not None and cancelled():
                journal.record(
                    "CANCELLED", partial.stat().st_size if partial.exists() else 0,
                    error="DownloadCancelled",
                )
                raise DownloadCancelled("Download cancelled before the next request")
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > size:
                journal.record("FAILED", offset, error="IntegrityError")
                raise IntegrityError("Partial file exceeds pinned size; manual repair required")
            if offset == size:
                if _sha256(partial) != expected:
                    journal.record("FAILED", offset, error="IntegrityError")
                    raise IntegrityError("Complete partial has invalid checksum; repair required")
                result = _publish_without_overwrite(
                    partial, target, expected=expected, size=size,
                )
                journal.record("COMPLETE", size)
                return result
            try:
                _require_disk_space(target.parent, size - offset, free_space_reserve)
            except InsufficientSpaceError:
                journal.record("BLOCKED", offset, error="InsufficientSpaceError")
                raise
            journal.record("DOWNLOADING", offset, new_attempt=True)

            headers = {"User-Agent": "Atlas-Holding-Bootstrap/0.1", "Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            try:
                with opener.open(Request(url, headers=headers), timeout=timeout) as response:
                    status = response.status
                    if status not in (200, 206):
                        raise DownloadError(f"Unsupported HTTP response {status}")
                    if status == 206:
                        match = _RANGE.fullmatch(response.headers.get("Content-Range", ""))
                        if not match or int(match.group(1)) != offset:
                            raise DownloadError("Invalid Content-Range; unsafe to append")
                        end = int(match.group(2))
                        total = match.group(3)
                        if end < offset or end >= size or (total != "*" and int(total) != size):
                            raise DownloadError("Server range doesn't match pinned asset")
                        if response.headers.get("Content-Encoding", "identity") != "identity":
                            raise DownloadError("Encoded range cannot be verified")
                    elif offset:
                        # A server that ignores Range forces a complete rewrite;
                        # the smaller resume preflight is no longer sufficient.
                        _require_disk_space(target.parent, size, free_space_reserve)
                    file_mode = "ab" if status == 206 and offset else "wb"
                    count = offset if file_mode == "ab" else 0
                    with partial.open(file_mode) as fd:
                        while count < size:
                            if cancelled is not None and cancelled():
                                journal.record(
                                    "CANCELLED", count, error="DownloadCancelled"
                                )
                                raise DownloadCancelled(
                                    f"Download cancelled at {count}/{size} bytes"
                                )
                            chunk = response.read(min(1024 * 1024, size - count))
                            if not chunk:
                                break
                            fd.write(chunk)
                            count += len(chunk)
                            if progress is not None:
                                progress(count, size)
                        fd.flush()
                        os.fsync(fd.fileno())
                    if count == size:
                        if _sha256(partial) != expected:
                            raise IntegrityError("SHA-256 mismatch; partial kept for inspection")
                        result = _publish_without_overwrite(
                            partial, target, expected=expected, size=size,
                        )
                        journal.record("COMPLETE", size)
                        return result
                    last_error = DownloadError(f"Short transfer: {count}/{size} bytes; partial retained")
            except DownloadCancelled:
                raise
            except InsufficientSpaceError:
                journal.record(
                    "BLOCKED", partial.stat().st_size if partial.exists() else 0,
                    error="InsufficientSpaceError",
                )
                raise
            except DownloadError as exc:
                journal.record(
                    "FAILED", partial.stat().st_size if partial.exists() else 0,
                    error=_error_label(exc),
                )
                raise
            except HTTPError as exc:
                if exc.code not in _TRANSIENT:
                    failure = DownloadError(f"Permanent HTTP {exc.code}")
                    journal.record(
                        "FAILED", partial.stat().st_size if partial.exists() else 0,
                        error=f"HTTP_{exc.code}",
                    )
                    raise failure from exc
                last_error = exc
            except (URLError, TimeoutError, ConnectionError, OSError, HTTPException) as exc:
                last_error = exc
            journal.record(
                "INTERRUPTED", partial.stat().st_size if partial.exists() else 0,
                error=_error_label(last_error),
            )
            if attempt + 1 < max_attempts:
                time.sleep(min(retry_delay * (2 ** min(attempt, 5)), 90.0))
        raise DownloadError(f"Download interrupted after {max_attempts} attempts; partial preserved: {last_error}")
