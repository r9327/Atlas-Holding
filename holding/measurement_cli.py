"""Operator-selected, local-only import of measurement CSV for an RC simulation."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .research_measurement import MeasurementComparisonError, MeasurementComparisonService
from .state_store import StateStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-measurement-compare")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--task-index", type=int, required=True)
    parser.add_argument("--csv-file", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.db.is_absolute() or not args.csv_file.is_absolute():
        parser.error("--db and --csv-file require absolute local paths")
    try:
        with StateStore(args.db) as store:
            receipt = MeasurementComparisonService(
                store, repository=args.repo).import_file(
                    args.session_id, args.task_index, args.csv_file)
    except (MeasurementComparisonError, OSError) as exc:
        parser.error(f"measurements rejected: {type(exc).__name__}: {exc}")
    print(json.dumps(asdict(receipt), ensure_ascii=False,
                     sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
