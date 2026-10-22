"""Durable, content-addressed offline cache for Documentalist exchange batches.

The cache is never trusted as source authority: both the metadata and every
record SHA-256 are checked on read. Missing/stale cache never fakes fresh data.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .document_exchange import export_document_batch
from .document_intake import (
    DocumentBatch, DocumentIntakeError, Record, MAX_DOCUMENT_BYTES,
    _canonical, _identifier, _source, _timestamp,
)

_SCHEMA = "documentalist-cache/v1"
_MAX_WIRE = MAX_DOCUMENT_BYTES
_MAX_METADATA = 4096


class DocumentCacheError(ValueError):
    pass


@dataclass(frozen=True)
class CachedBatch:
    batch: DocumentBatch
    verified_wire_sha256: str


def _key(source: str, fmt: str, id_field: str, table_index: int) -> str:
    if fmt not in {"json", "csv", "html"} or not isinstance(id_field, str):
        raise DocumentCacheError("invalid cache source format")
    if type(table_index) is not int or not 0 <= table_index <= 31:
        raise DocumentCacheError("invalid HTML table selector")
    return sha256(json.dumps([source, fmt, id_field, table_index],
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _dirs(root: Path) -> tuple[Path, Path]:
    if not isinstance(root, Path) or root.is_symlink():
        raise DocumentCacheError("unsafe cache root")
    entries, objects = root / "entries", root / "objects"
    for directory in (root, entries, objects):
        if directory.is_symlink():
            raise DocumentCacheError("unsafe cache directory")
        directory.mkdir(parents=True, exist_ok=True)
    return entries, objects


def _atomic_bytes(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise DocumentCacheError("unsafe cache target")
    fd, temporary = tempfile.mkstemp(prefix=".documentalist-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def store_batch(root: Path, batch: DocumentBatch, *, format: str,
                id_field: str = "id", table_index: int = 0,
                max_entries: int = 2048) -> str:
    if not isinstance(batch, DocumentBatch):
        raise DocumentCacheError("expected DocumentBatch")
    if not 1 <= max_entries <= 20_000:
        raise DocumentCacheError("invalid cache entry limit")
    _source(batch.source)
    _timestamp(batch.collected_at)
    key = _key(batch.source, format, id_field, table_index)
    wire = export_document_batch(batch)
    if len(wire) > _MAX_WIRE:
        raise DocumentCacheError("batch exceeds cache size budget")
    digest = sha256(wire).hexdigest()
    entries, objects = _dirs(root)
    pointer = entries / (key + ".json")
    if not pointer.exists() and len(list(entries.glob("*.json"))) >= max_entries:
        raise DocumentCacheError("cache entry count exceeded")
    blob = objects / (digest + ".json")
    if blob.is_symlink():
        raise DocumentCacheError("unsafe cached blob")
    if not blob.exists():
        _atomic_bytes(blob, wire)
    elif blob.stat().st_size > _MAX_WIRE or sha256(blob.read_bytes()).hexdigest() != digest:
        raise DocumentCacheError("existing cache blob was corrupted")
    metadata = {
        "schema": _SCHEMA, "source": batch.source, "format": format,
        "id_field": id_field, "table_index": table_index,
        "wire_sha256": digest,
    }
    _atomic_bytes(pointer, json.dumps(metadata, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8"))
    return digest


def load_cached_batch(root: Path, *, source: str, format: str,
                      id_field: str = "id", table_index: int = 0
                      ) -> CachedBatch | None:
    _source(source)
    key = _key(source, format, id_field, table_index)
    entries, objects = _dirs(root)
    pointer = entries / (key + ".json")
    if not pointer.exists():
        return None
    if pointer.is_symlink() or pointer.stat().st_size > _MAX_METADATA:
        raise DocumentCacheError("unsafe cache pointer")
    try:
        metadata = json.loads(pointer.read_text(encoding="utf-8"))
        expected_fields = {"schema", "source", "format", "id_field", "table_index", "wire_sha256"}
        if (not isinstance(metadata, dict) or set(metadata) != expected_fields
            or metadata["schema"] != _SCHEMA or metadata["source"] != source
            or metadata["format"] != format or metadata["id_field"] != id_field
            or metadata["table_index"] != table_index):
            raise DocumentCacheError("cache pointer mismatch")
        wire_sha = metadata["wire_sha256"]
        if not isinstance(wire_sha, str) or len(wire_sha) != 64 or any(
            ch not in "0123456789abcdef" for ch in wire_sha
        ):
            raise DocumentCacheError("invalid cache blob hash")
        blob = objects / (wire_sha + ".json")
        if blob.is_symlink() or blob.stat().st_size > _MAX_WIRE:
            raise DocumentCacheError("unsafe cache blob")
        wire = blob.read_bytes()
        if sha256(wire).hexdigest() != wire_sha:
            raise DocumentCacheError("cached blob integrity failure")
        document = json.loads(wire.decode("utf-8"))
        if (not isinstance(document, dict) or set(document) != {
            "schema", "source", "collected_at", "source_sha256", "records"
        } or document["schema"] != "documentalist-batch/v1"
            or document["source"] != source
            or not isinstance(document["records"], list)
            or len(document["records"]) > 20_000):
            raise DocumentCacheError("invalid cached document exchange")
        timestamp = _timestamp(document["collected_at"])
        raw_sha = document["source_sha256"]
        if not isinstance(raw_sha, str) or len(raw_sha) != 64 or any(
            ch not in "0123456789abcdef" for ch in raw_sha
        ):
            raise DocumentCacheError("invalid original source hash")
        seen = set()
        rows = []
        for row in document["records"]:
            if not isinstance(row, dict) or set(row) != {"id", "sha256", "payload"}:
                raise DocumentCacheError("invalid cached record")
            rec_id = _identifier(row["id"])
            payload = row["payload"]
            if (not isinstance(payload, dict) or "id" not in payload
                    or _identifier(payload["id"]) != rec_id):
                raise DocumentCacheError("cached record identity mismatch")
            if rec_id in seen:
                raise DocumentCacheError("duplicate cached ID")
            seen.add(rec_id)
            canonical = _canonical(row["payload"])
            record_sha = sha256(canonical.encode("utf-8")).hexdigest()
            if record_sha != row["sha256"]:
                raise DocumentCacheError("cached record checksum mismatch")
            rows.append(Record(rec_id, canonical, record_sha))
        rows.sort(key=lambda row: row.record_id)
        return CachedBatch(DocumentBatch(source, timestamp, raw_sha, tuple(rows)), wire_sha)
    except DocumentCacheError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RecursionError, DocumentIntakeError) as exc:
        raise DocumentCacheError("cached exchange cannot be verified") from exc
