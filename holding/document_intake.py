"""Deterministic, offline-safe JSON record intake for the Documentalist.

This is a bounded parser/diff engine, not a crawler or a source of truth.
External content is DATA only; importing it never executes code or writes files.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 20_000


class DocumentIntakeError(ValueError):
    """An input document is invalid, conflicting or too large."""


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _source(value: str) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 2048 or
        any(ord(ch) < 32 for ch in value) or
        not (value.startswith("https://") or value.startswith("local:"))):
        raise DocumentIntakeError("invalid source reference")
    return value


def _timestamp(value: str) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise DocumentIntakeError("invalid collection timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DocumentIntakeError("invalid collection timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise DocumentIntakeError("timestamp must have a UTC offset")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _identifier(value: object) -> str:
    if isinstance(value, bool):
        raise DocumentIntakeError("boolean record IDs are not valid")
    if type(value) is int:
        if value < 0:
            raise DocumentIntakeError("negative record ID")
        return str(value)
    if not isinstance(value, str) or not 1 <= len(value) <= 160:
        raise DocumentIntakeError("invalid record ID")
    if value != value.strip() or any(ord(ch) < 32 for ch in value):
        raise DocumentIntakeError("invalid record ID")
    return value


@dataclass(frozen=True, slots=True)
class Record:
    record_id: str
    canonical_json: str
    sha256: str


@dataclass(frozen=True, slots=True)
class DocumentBatch:
    source: str
    collected_at: str
    source_sha256: str
    records: tuple[Record, ...]


@dataclass(frozen=True, slots=True)
class RecordChange:
    record_id: str
    status: str  # added / changed / unchanged / missing
    old_sha256: str | None
    new_sha256: str | None


def ingest_json_records(raw: bytes, *, source: str, collected_at: str,
                        id_field: str = "id") -> DocumentBatch:
    """Import top-level array or {"records": [...]} into immutable rows.

    A missing row is only reported as 'missing' by diff_records, NEVER deleted.
    """
    source = _source(source)
    collected_at = _timestamp(collected_at)
    if not isinstance(raw, bytes) or len(raw) > MAX_DOCUMENT_BYTES:
        raise DocumentIntakeError("invalid or oversized JSON bytes")
    if not isinstance(id_field, str) or not id_field or len(id_field) > 100:
        raise DocumentIntakeError("invalid ID field")
    try:
        doc = json.loads(raw.decode("utf-8"), parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DocumentIntakeError("malformed document") from exc
    if isinstance(doc, dict) and set(doc) == {"records"}:
        doc = doc["records"]
    if not isinstance(doc, list) or len(doc) > MAX_RECORDS:
        raise DocumentIntakeError("expected bounded record list")
    seen: set[str] = set()
    records: list[Record] = []
    for item in doc:
        if not isinstance(item, dict) or id_field not in item:
            raise DocumentIntakeError("record without ID")
        record_id = _identifier(item[id_field])
        if record_id in seen:
            raise DocumentIntakeError(f"duplicate ID: {record_id}")
        seen.add(record_id)
        try:
            canonical = _canonical(item)
        except (TypeError, ValueError, RecursionError) as exc:
            raise DocumentIntakeError(f"unsupported record: {record_id}") from exc
        records.append(Record(record_id, canonical,
                              sha256(canonical.encode("utf-8")).hexdigest()))
    records.sort(key=lambda row: row.record_id)
    return DocumentBatch(source, collected_at, sha256(raw).hexdigest(), tuple(records))


def diff_records(before: DocumentBatch, after: DocumentBatch) -> tuple[RecordChange, ...]:
    """Plan non-destructive changes; missing rows require separate review."""
    old = {row.record_id: row.sha256 for row in before.records}
    new = {row.record_id: row.sha256 for row in after.records}
    changes = []
    for key in sorted(old.keys() | new.keys()):
        old_sha, new_sha = old.get(key), new.get(key)
        status = ("added" if old_sha is None else "missing" if new_sha is None else
                  "unchanged" if old_sha == new_sha else "changed")
        changes.append(RecordChange(key, status, old_sha, new_sha))
    return tuple(changes)


def find_cross_source_conflicts(*batches: DocumentBatch) -> tuple[str, ...]:
    """Return IDs represented by conflicting content across sources."""
    seen: dict[str, str] = {}
    conflicts: set[str] = set()
    for batch in batches:
        for record in batch.records:
            old = seen.setdefault(record.record_id, record.sha256)
            if old != record.sha256:
                conflicts.add(record.record_id)
    return tuple(sorted(conflicts))
