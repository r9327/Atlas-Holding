"""Documentalist CLI: collect JSON/CSV and export an Atlas-readable batch.

Only an explicitly named output file is created. No Atlas catalogue, Git
worktree, installer or model is modified or run by this command.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from .document_csv import ingest_csv_records
from .document_exchange import export_document_batch
from .document_html import ingest_html_table
from .document_intake import DocumentIntakeError, MAX_DOCUMENT_BYTES, ingest_json_records
from .document_source import DocumentSourceError, fetch_document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect a bounded JSON/CSV source into a versioned batch")
    selected = parser.add_mutually_exclusive_group(required=True)
    selected.add_argument("--file", type=Path, help="local document (read-only)")
    selected.add_argument("--url", help="HTTPS JSON/CSV URL")
    parser.add_argument("--format", choices=("json", "csv", "html"), required=True)
    parser.add_argument("--table-index", type=int, default=0,
                        help="zero-based HTML table index")
    parser.add_argument("--out", type=Path, required=True, help="new batch JSON; will not overwrite")
    parser.add_argument("--allow-host", action="append", default=[],
                        help="explicit remote hostname permitted for collection")
    parser.add_argument("--id-field", default="id")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    try:
        if args.format != "html" and args.table_index != 0:
            raise DocumentIntakeError("--table-index applies only to HTML")
        if args.file:
            if args.allow_host:
                raise DocumentIntakeError("--allow-host applies only to --url")
            if args.file.is_symlink() or not args.file.is_file():
                raise DocumentIntakeError("source must be an existing regular file")
            if args.file.stat().st_size > MAX_DOCUMENT_BYTES:
                raise DocumentIntakeError("source exceeds 8 MiB")
            raw = args.file.read_bytes()
            stamp = datetime.now(timezone.utc).isoformat()
            ingest = (ingest_json_records if args.format == "json" else
                      ingest_csv_records if args.format == "csv" else ingest_html_table)
            kwargs = {"table_index": args.table_index} if args.format == "html" else {}
            batch = ingest(raw, source="local:" + args.file.name,
                           collected_at=stamp, id_field=args.id_field, **kwargs)
            read_bytes = len(raw)
        else:
            if not args.allow_host:
                raise DocumentSourceError("--allow-host is required for URL collection")
            fetched = fetch_document(args.url, allowed_hosts=tuple(args.allow_host),
                                     format=args.format, id_field=args.id_field,
                                     table_index=args.table_index, timeout=args.timeout)
            batch, read_bytes = fetched.batch, fetched.bytes_received
        result = export_document_batch(batch)
        if args.out.is_symlink():
            raise DocumentIntakeError("output path cannot be a symlink")
        # Exclusive creation, never overwrites existing data.
        with args.out.open("xb") as file:
            try:
                file.write(result)
                file.flush()
                os.fsync(file.fileno())
            except OSError:
                file.close()
                args.out.unlink(missing_ok=True)
                raise
    except (DocumentIntakeError, DocumentSourceError, OSError,
            UnicodeError, ValueError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    print(json.dumps({
        "status": "EXPORTED",
        "output": str(args.out),
        "source": batch.source,
        "source_sha256": batch.source_sha256,
        "records": len(batch.records),
        "source_bytes": read_bytes,
        "exchange_bytes": len(result),
    }, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
