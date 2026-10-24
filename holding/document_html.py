"""Deterministic HTML table adapter; no JavaScript, DOM execution or browser.

Intended for simple, server-rendered HTML tables. Site-specific guide pages,
nested tables and JS-rendered content require a separate source adapter.
"""
from __future__ import annotations

from hashlib import sha256
from html.parser import HTMLParser
import re

from .document_intake import (
    DocumentBatch, DocumentIntakeError, MAX_DOCUMENT_BYTES, MAX_RECORDS,
    Record, _canonical, _identifier, _source, _timestamp,
)

_WHITESPACE = re.compile(r"\s+")


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.skip = 0
        self.tables: list[list[list[tuple[str, bool]]]] = []
        self.current_table: list[list[tuple[str, bool]]] | None = None
        self.row: list[tuple[str, bool]] | None = None
        self.cell: list[str] | None = None
        self.is_header = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "table":
            if self.depth == 0:
                self.current_table = []
            self.depth += 1
            return
        if self.depth != 1:
            return
        if tag == "tr":
            self.row = []
        elif tag in ("th", "td") and self.row is not None and self.cell is None:
            attributes = dict(attrs)
            if attributes.get("rowspan", "1") != "1" or attributes.get("colspan", "1") != "1":
                raise DocumentIntakeError("spanning HTML cells are unsupported")
            self.cell = []
            self.is_header = tag == "th"
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, data):
        if self.skip == 0 and self.depth == 1 and self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag in ("th", "td") and self.depth == 1 and self.cell is not None:
            value = _WHITESPACE.sub(" ", "".join(self.cell)).strip()
            if len(value) > 8192:
                raise DocumentIntakeError("HTML cell exceeds size budget")
            assert self.row is not None
            self.row.append((value, self.is_header))
            self.cell = None
        elif tag == "tr" and self.depth == 1 and self.row is not None:
            if self.row:
                assert self.current_table is not None
                if len(self.current_table) > MAX_RECORDS:
                    raise DocumentIntakeError("too many HTML rows")
                self.current_table.append(self.row)
            self.row = None
        elif tag == "table" and self.depth:
            self.depth -= 1
            if self.depth == 0:
                if self.current_table:
                    if len(self.tables) >= 32:
                        raise DocumentIntakeError("too many HTML tables")
                    self.tables.append(self.current_table)
                self.current_table = None
                self.row = None
                self.cell = None


def ingest_html_table(raw: bytes, *, source: str, collected_at: str,
                      id_field: str = "id", table_index: int = 0) -> DocumentBatch:
    source = _source(source)
    stamp = _timestamp(collected_at)
    if not isinstance(raw, bytes) or len(raw) > MAX_DOCUMENT_BYTES:
        raise DocumentIntakeError("invalid or oversized HTML bytes")
    if type(table_index) is not int or not 0 <= table_index < 32:
        raise DocumentIntakeError("invalid HTML table index")
    if not isinstance(id_field, str) or not 1 <= len(id_field) <= 100:
        raise DocumentIntakeError("invalid ID field")
    try:
        parser = _Tables()
        parser.feed(raw.decode("utf-8-sig"))
        parser.close()
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DocumentIntakeError("invalid HTML input") from exc
    if parser.depth != 0 or parser.cell is not None or parser.row is not None:
        raise DocumentIntakeError("incomplete HTML table")
    if table_index >= len(parser.tables):
        raise DocumentIntakeError("HTML table does not exist")
    table = parser.tables[table_index]
    headers = [value for value, _ in table[0]]
    if (not all(is_head for _, is_head in table[0]) or not headers or
            id_field not in headers or len(set(headers)) != len(headers) or
            len(headers) > 120 or any(not h or len(h) > 100 for h in headers)):
        raise DocumentIntakeError("unsupported HTML table header")
    if len(table) - 1 > MAX_RECORDS:
        raise DocumentIntakeError("too many HTML records")
    seen = set()
    records = []
    for line in table[1:]:
        if len(line) != len(headers):
            raise DocumentIntakeError("inconsistent HTML table columns")
        if any(is_head for _, is_head in line):
            raise DocumentIntakeError("unexpected second HTML table header")
        row = {header: value for header, (value, _) in zip(headers, line)}
        rec_id = _identifier(row[id_field])
        if rec_id in seen:
            raise DocumentIntakeError(f"duplicate HTML ID: {rec_id}")
        seen.add(rec_id)
        canonical = _canonical(row)
        records.append(Record(rec_id, canonical, sha256(canonical.encode("utf-8")).hexdigest()))
    records.sort(key=lambda row: row.record_id)
    return DocumentBatch(source, stamp, sha256(raw).hexdigest(), tuple(records))
