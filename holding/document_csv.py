"""Bounded CSV adapter using the Documentalist's shared immutable record schema.

CSV values are strings on purpose: type coercion belongs to a project's
schema and must not silently turn canonical identifiers into different IDs.
"""
from __future__ import annotations

import csv
from hashlib import sha256
from io import StringIO

from .document_intake import (
    DocumentBatch, DocumentIntakeError, MAX_DOCUMENT_BYTES, MAX_RECORDS, Record,
    _canonical, _identifier, _source, _timestamp,
)


def ingest_csv_records(raw: bytes, *, source: str, collected_at: str,
                       id_field: str = "id") -> DocumentBatch:
    source = _source(source)
    stamp = _timestamp(collected_at)
    if not isinstance(raw, bytes) or len(raw) > MAX_DOCUMENT_BYTES:
        raise DocumentIntakeError("invalid or oversized CSV bytes")
    if not isinstance(id_field, str) or not 1 <= len(id_field) <= 100:
        raise DocumentIntakeError("invalid ID field")
    try:
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(StringIO(text, newline=""), strict=True)
        fields = reader.fieldnames
        if (not fields or id_field not in fields or len(fields) != len(set(fields))
                or any(not isinstance(col, str) or not col.strip() or
                       len(col) > 100 for col in fields)):
            raise DocumentIntakeError("missing, duplicate or invalid CSV column")
        seen: set[str] = set()
        records: list[Record] = []
        for row in reader:
            if len(records) >= MAX_RECORDS:
                raise DocumentIntakeError("too many CSV records")
            if None in row or any(value is None for value in row.values()):
                raise DocumentIntakeError("mismatched CSV column count")
            rec_id = _identifier(row[id_field])
            if rec_id in seen:
                raise DocumentIntakeError(f"duplicate CSV ID: {rec_id}")
            seen.add(rec_id)
            canonical = _canonical(row)
            records.append(Record(rec_id, canonical, sha256(canonical.encode()).hexdigest()))
    except (UnicodeError, csv.Error, ValueError, OverflowError) as exc:
        if isinstance(exc, DocumentIntakeError):
            raise
        raise DocumentIntakeError("malformed CSV document") from exc
    records.sort(key=lambda r: r.record_id)
    return DocumentBatch(source, stamp, sha256(raw).hexdigest(), tuple(records))
