"""Bounded Markdown pipe-table adapter for Documentalist records.

Only simple pipe tables are supported. Instructions, links and embedded HTML
remain inert strings; no document content is ever executed.
"""
from __future__ import annotations

from hashlib import sha256
import re

from .document_intake import (
    DocumentBatch, DocumentIntakeError, MAX_DOCUMENT_BYTES, MAX_RECORDS,
    Record, _canonical, _identifier, _source, _timestamp,
)

_SEPARATOR = re.compile(r":?-{3,}:?\Z")


def _cells(line: str) -> list[str] | None:
    """Split unescaped pipes, preserving literal pipes inside values."""
    line = line.strip()
    if "|" not in line:
        return None
    result: list[str] = []
    buf: list[str] = []
    escaped = False
    for ch in line:
        if escaped:
            if ch not in ("|", "\\"):
                buf.append("\\")
            buf.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            result.append("".join(buf).strip())
            buf.clear()
        else:
            buf.append(ch)
    if escaped:
        buf.append("\\")
    result.append("".join(buf).strip())
    if line.startswith("|"):
        result.pop(0)
    # A trailing escaped pipe belongs to the last cell, not the table border.
    if line.endswith("|") and result[-1] == "":
        result.pop()
    return result


def ingest_markdown_table(
    raw: bytes, *, source: str, collected_at: str,
    id_field: str = "id", table_index: int = 0,
) -> DocumentBatch:
    """Parse one explicitly selected pipe table, with an ID column."""
    source = _source(source)
    stamp = _timestamp(collected_at)
    if not isinstance(raw, bytes) or len(raw) > MAX_DOCUMENT_BYTES:
        raise DocumentIntakeError("invalid or oversized Markdown bytes")
    if type(table_index) is not int or not 0 <= table_index < 32:
        raise DocumentIntakeError("invalid Markdown table index")
    if not isinstance(id_field, str) or not 1 <= len(id_field) <= 100:
        raise DocumentIntakeError("invalid Markdown ID field")
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeError as exc:
        raise DocumentIntakeError("malformed Markdown document") from exc

    tables: list[tuple[list[str], list[list[str]]]] = []
    index = 0
    fence: str | None = None
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith((chr(96) * 3, "~" * 3)):
            marker = line[:3]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            index += 1
            continue
        if fence is not None or index + 1 >= len(lines):
            index += 1
            continue
        headers = _cells(line)
        separator = _cells(lines[index + 1])
        if (headers and separator and len(headers) == len(separator)
                and all(_SEPARATOR.fullmatch(cell) for cell in separator)):
            if len(tables) >= 32:
                raise DocumentIntakeError("too many Markdown tables")
            if (len(headers) > 120 or len(set(headers)) != len(headers)
                    or any(not h or len(h) > 100 for h in headers)):
                raise DocumentIntakeError("invalid Markdown headers")
            rows: list[list[str]] = []
            index += 2
            while index < len(lines) and lines[index].strip():
                cells = _cells(lines[index])
                if cells is None:
                    break
                if len(cells) != len(headers):
                    raise DocumentIntakeError("inconsistent Markdown table columns")
                if any(len(value) > 8192 for value in cells):
                    raise DocumentIntakeError("Markdown cell exceeds size budget")
                if len(rows) >= MAX_RECORDS:
                    raise DocumentIntakeError("too many Markdown rows")
                rows.append(cells)
                index += 1
            tables.append((headers, rows))
            continue
        index += 1

    if table_index >= len(tables):
        raise DocumentIntakeError("Markdown table does not exist")
    headers, rows = tables[table_index]
    if id_field not in headers:
        raise DocumentIntakeError("missing Markdown ID column")
    seen: set[str] = set()
    records: list[Record] = []
    for cells in rows:
        payload = dict(zip(headers, cells))
        record_id = _identifier(payload[id_field])
        if record_id in seen:
            raise DocumentIntakeError("duplicate Markdown record ID")
        seen.add(record_id)
        canonical = _canonical(payload)
        records.append(Record(
            record_id, canonical, sha256(canonical.encode("utf-8")).hexdigest(),
        ))
    records.sort(key=lambda row: row.record_id)
    return DocumentBatch(source, stamp, sha256(raw).hexdigest(), tuple(records))
