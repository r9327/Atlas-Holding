"""Small bounded offline MCP document cache with explicit untrusted provenance.

Only a trusted ingester may call put(). The MCP router offers read-only lookup
on a cache object provisioned by the host; never accepts a database path, SQL,
source content, or a remote fetch command from model-supplied arguments.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qsl, urlsplit


MAX_CONTENT_BYTES = 32 * 1024
MAX_RECORDS = 64
MAX_AGE_SECONDS = 30 * 86400
_MAX_EPOCH = 4_102_444_800
_VERSION = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:+-]{0,79}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class McpCacheError(ValueError):
    """Unsafe, invalid or corrupted cached material."""


def _validate_identity(source_uri: str, source_version: str) -> None:
    if (type(source_uri) is not str or len(source_uri) > 2048
            or type(source_version) is not str
            or not _VERSION.fullmatch(source_version)):
        raise McpCacheError("Invalid versioned cache identity")
    try:
        parts = urlsplit(source_uri)
        port = parts.port
    except ValueError as exc:
        raise McpCacheError("Invalid source URL") from exc
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.fragment or port == 0
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in source_uri)):
        raise McpCacheError("Cache source must be a public HTTPS URL without credentials")
    secret_names = {"token", "access_token", "api_key", "apikey",
                    "password", "secret", "authorization", "auth", "credential",
                    "client_secret", "session", "sessionid"}
    if any(name.casefold() in secret_names
           for name, _value in parse_qsl(parts.query, keep_blank_values=True)):
        raise McpCacheError("Cache source URL must not contain authentication secrets")


def _epoch(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_EPOCH


@dataclass(frozen=True)
class CachedDocument:
    status: str
    source_uri: str
    source_version: str
    captured_epoch: int | None = None
    expires_epoch: int | None = None
    content_sha256: str | None = None
    text: str | None = None
    provenance: str = "UNTRUSTED_EXTERNAL"


class OfflineDocumentCache:
    """Finite SQLite cache; no network, shell, or implicit plugin execution."""

    def __init__(self, database: Path) -> None:
        if not isinstance(database, Path) or not database.is_absolute():
            raise McpCacheError("Trusted absolute cache location required")
        if database.is_symlink():
            raise McpCacheError("Cache path must not be a symlink")
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(database), timeout=5, isolation_level=None)
        try:
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS mcp_cached_docs (
                    source_uri TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    captured_epoch INTEGER NOT NULL,
                    expires_epoch INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    content BLOB NOT NULL,
                    PRIMARY KEY (source_uri,source_version)
                )
            """)
        except BaseException:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "OfflineDocumentCache":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def put(self, *, source_uri: str, source_version: str, captured_epoch: int,
            expires_epoch: int, now_epoch: int, content: bytes,
            content_sha256: str) -> None:
        """Trusted ingestion only. No MCP tool is permitted to call this."""
        _validate_identity(source_uri, source_version)
        if (not all(_epoch(x) for x in (captured_epoch, expires_epoch, now_epoch))
                or captured_epoch > now_epoch + 5
                or expires_epoch <= captured_epoch
                or expires_epoch - captured_epoch > MAX_AGE_SECONDS
                or type(content) is not bytes or not 1 <= len(content) <= MAX_CONTENT_BYTES
                or type(content_sha256) is not str
                or not _DIGEST.fullmatch(content_sha256)
                or hashlib.sha256(content).hexdigest() != content_sha256):
            raise McpCacheError("Cache content or freshness proof is invalid")
        try:
            content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise McpCacheError("Cache only accepts bounded UTF-8 text") from exc
        with self.db:
            # sqlite isolation_level=None otherwise autocommits each write.
            # BEGIN makes insertion and bounded eviction one atomic operation.
            self.db.execute("BEGIN IMMEDIATE")
            old = self.db.execute(
                "SELECT captured_epoch FROM mcp_cached_docs "
                "WHERE source_uri=? AND source_version=?",
                (source_uri, source_version),
            ).fetchone()
            if old is not None and old[0] >= captured_epoch:
                raise McpCacheError("A cache snapshot cannot replace newer evidence")
            self.db.execute(
                "INSERT INTO mcp_cached_docs VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(source_uri,source_version) DO UPDATE SET "
                "captured_epoch=excluded.captured_epoch,"
                "expires_epoch=excluded.expires_epoch,"
                "content_sha256=excluded.content_sha256,"
                "content=excluded.content",
                (source_uri, source_version, captured_epoch, expires_epoch,
                 content_sha256, content),
            )
            # Bound both rows and disk growth without executing user SQL.
            self.db.execute(
                "DELETE FROM mcp_cached_docs WHERE rowid NOT IN "
                "(SELECT rowid FROM mcp_cached_docs ORDER BY captured_epoch DESC,"
                "source_uri,source_version LIMIT ?)",
                (MAX_RECORDS,),
            )

    def lookup(self, *, source_uri: str, source_version: str,
               now_epoch: int) -> CachedDocument:
        _validate_identity(source_uri, source_version)
        if not _epoch(now_epoch):
            raise McpCacheError("Invalid offline clock")
        row = self.db.execute(
            "SELECT captured_epoch,expires_epoch,content_sha256,content "
            "FROM mcp_cached_docs WHERE source_uri=? AND source_version=?",
            (source_uri, source_version),
        ).fetchone()
        if row is None:
            return CachedDocument("MISSING", source_uri, source_version)
        captured, expires, digest, content = row
        if (not _epoch(captured) or not _epoch(expires)
                or not captured < expires <= captured + MAX_AGE_SECONDS
                or type(digest) is not str or not _DIGEST.fullmatch(digest)
                or type(content) is not bytes
                or not 1 <= len(content) <= MAX_CONTENT_BYTES
                or hashlib.sha256(content).hexdigest() != digest):
            raise McpCacheError("Persisted cache proof is corrupt")
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise McpCacheError("Persisted cache text is corrupt") from exc
        valid = captured <= now_epoch <= expires
        return CachedDocument(
            "CACHED_ONLY" if valid else "STALE", source_uri, source_version,
            captured, expires, digest, text if valid else None,
        )
