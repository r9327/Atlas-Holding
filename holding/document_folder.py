"""Deterministic, bounded offline batch import for Documentalist sources.

Reads a flat directory sequentially and writes one immutable exchange file per
source. No recursion, network, execution of document text, deletion or overwrite.
Existing exports are reused only when their contents still match the source;
changes require separate review and produce a conflict rather than data loss.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from .document_csv import ingest_csv_records
from .document_exchange import export_document_batch
from .document_html import ingest_html_table
from .document_intake import (
    DocumentIntakeError, MAX_DOCUMENT_BYTES, _timestamp, ingest_json_records,
)
from .document_markdown import ingest_markdown_table

_FORMATS = {
    ".json": ingest_json_records,
    ".csv": ingest_csv_records,
    ".html": ingest_html_table,
    ".htm": ingest_html_table,
    ".md": ingest_markdown_table,
    ".markdown": ingest_markdown_table,
}
_MAX_SCANNED = 10000
_MAX_FILES = 2000
_MAX_TOTAL_BYTES = 256 * 1024 * 1024


class DocumentFolderError(ValueError):
    """Invalid inputs, contradictory previously exported data or write failure."""


@dataclass(frozen=True, slots=True)
class FolderImport:
    scanned: int
    eligible: int
    created: int
    reused: int
    input_bytes: int
    output_bytes: int


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DocumentFolderError("duplicate saved exchange key")
        result[key] = value
    return result


def _matches_saved(path: Path, fresh: bytes) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise DocumentFolderError("unsafe or oversized existing output")
    try:
        old = json.loads(path.read_bytes(), object_pairs_hook=_unique_pairs)
        new = json.loads(fresh)
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        raise DocumentFolderError("invalid saved exchange") from exc
    if not isinstance(old, dict) or set(old) != set(new):
        raise DocumentFolderError("saved exchange schema mismatch")
    try:
        _timestamp(old.get("collected_at"))
    except DocumentIntakeError as exc:
        raise DocumentFolderError("invalid saved provenance timestamp") from exc
    # Timestamp records the FIRST successful import. A later identical import
    # must not change or silently reset the provenance timestamp.
    new["collected_at"] = old["collected_at"]
    return old == new


def import_document_folder(
    input_dir: Path,
    output_dir: Path,
    *,
    id_field: str = "id",
    max_files: int = _MAX_FILES,
    max_total_bytes: int = _MAX_TOTAL_BYTES,
    collected_at: str | None = None,
) -> FolderImport:
    """Process up to max_files tabular documents, one at a time.

    This mode deliberately does not crawl nested directories or parse PDF/Word
    files. Unknown extensions are ignored, not mislabelled as processed.
    """
    if not isinstance(input_dir, Path) or not isinstance(output_dir, Path):
        raise DocumentFolderError("folder paths must be Path instances")
    if (type(max_files) is not int or not 1 <= max_files <= _MAX_FILES
            or not isinstance(id_field, str) or not 1 <= len(id_field) <= 100
            or type(max_total_bytes) is not int
            or not 1 <= max_total_bytes <= 2 * 1024 * 1024 * 1024):
        raise DocumentFolderError("invalid batch configuration")
    if input_dir.is_symlink() or not input_dir.is_dir():
        raise DocumentFolderError("input must be an existing non-symlink directory")
    if output_dir.is_symlink() or (output_dir.exists() and not output_dir.is_dir()):
        raise DocumentFolderError("unsafe output directory")
    if input_dir.resolve() == output_dir.resolve():
        raise DocumentFolderError("input and output directories must differ")
    now = collected_at or datetime.now(timezone.utc).isoformat()
    if not isinstance(now, str):
        raise DocumentFolderError("invalid collection time")

    selected: list[Path] = []
    names: set[str] = set()
    scanned = 0
    with os.scandir(input_dir) as iterator:
        for entry in iterator:
            scanned += 1
            if scanned > _MAX_SCANNED:
                raise DocumentFolderError("too many input directory entries")
            file_path = Path(entry.path)
            if file_path.is_symlink():
                raise DocumentFolderError("source symlinks are prohibited")
            if not entry.is_file(follow_symlinks=False):
                continue
            if file_path.suffix.lower() not in _FORMATS:
                continue
            name_key = file_path.name.casefold()
            if name_key in names:
                raise DocumentFolderError("case-colliding source filenames")
            names.add(name_key)
            selected.append(file_path)
            if len(selected) > max_files:
                raise DocumentFolderError("batch source count exceeds quota")
    selected.sort(key=lambda p: (p.name.casefold(), p.name))
    # Reject an excessive batch before creating ANY exported documents.
    projected_bytes = 0
    for candidate in selected:
        if candidate.is_symlink() or not candidate.is_file():
            raise DocumentFolderError("source changed during batch")
        size = candidate.stat().st_size
        if size > MAX_DOCUMENT_BYTES:
            raise DocumentFolderError("source exceeds 8 MiB")
        projected_bytes += size
        if projected_bytes > max_total_bytes:
            raise DocumentFolderError("batch exceeds total byte budget")
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.is_symlink():
        raise DocumentFolderError("unsafe output directory")

    created = reused = input_bytes = output_bytes = 0
    for source in selected:
        if source.is_symlink() or not source.is_file():
            raise DocumentFolderError("source changed during batch")
        if source.stat().st_size > MAX_DOCUMENT_BYTES:
            raise DocumentFolderError("source exceeds 8 MiB")
        size = source.stat().st_size
        if input_bytes + size > max_total_bytes:
            raise DocumentFolderError("batch exceeds total byte budget")
        raw = source.read_bytes()
        if len(raw) > MAX_DOCUMENT_BYTES or input_bytes + len(raw) > max_total_bytes:
            raise DocumentFolderError("source exceeds batch byte budget")
        ext = source.suffix.lower()
        importer = _FORMATS[ext]
        kw = {"table_index": 0} if ext in (".md", ".markdown", ".html", ".htm") else {}
        try:
            batch = importer(
                raw, source="local:" + source.name, collected_at=now,
                id_field=id_field, **kw,
            )
            wire = export_document_batch(batch)
        except DocumentIntakeError as exc:
            raise DocumentFolderError("invalid source: " + source.name) from exc
        target = output_dir / (source.name + ".batch.json")
        input_bytes += len(raw)
        output_bytes += len(wire)
        if target.exists() or target.is_symlink():
            if not _matches_saved(target, wire):
                raise DocumentFolderError("existing output conflicts: " + target.name)
            reused += 1
            continue
        try:
            with target.open("xb") as out:
                try:
                    out.write(wire)
                    out.flush()
                    os.fsync(out.fileno())
                except OSError:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise
        except FileExistsError as exc:
            raise DocumentFolderError("output created concurrently") from exc
        created += 1
    return FolderImport(scanned, len(selected), created, reused, input_bytes, output_bytes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import bounded tabular documents from one folder")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--id-field", default="id")
    parser.add_argument("--max-files", type=int, default=_MAX_FILES)
    parser.add_argument("--max-bytes", type=int, default=_MAX_TOTAL_BYTES)
    args = parser.parse_args(argv)
    try:
        result = import_document_folder(
            args.input_dir, args.out_dir, id_field=args.id_field,
            max_files=args.max_files, max_total_bytes=args.max_bytes,
        )
    except (DocumentFolderError, OSError, ValueError, UnicodeError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({
        "status": "EXPORTED",
        "scanned": result.scanned,
        "eligible": result.eligible,
        "created": result.created,
        "reused": result.reused,
        "source_bytes": result.input_bytes,
        "exchange_bytes": result.output_bytes,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
