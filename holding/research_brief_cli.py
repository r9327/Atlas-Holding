"""Read and export existing research drafts without invoking a model."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from .research_brief import ResearchBriefError, ResearchBriefService
from .state_store import StateStore, StoreError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-research")
    parser.add_argument("action", choices=("history", "export"))
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--mission-id", required=True)
    parser.add_argument("--spec-sha256")
    parser.add_argument("--model-digest")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--before-record", type=int)
    parser.add_argument("--output-file", type=Path)
    args = parser.parse_args(argv)
    if not args.db.is_absolute() or not args.db.is_file():
        parser.error("--db must be an existing absolute database path")
    if args.action == "history":
        if args.spec_sha256 or args.model_digest or args.output_file is not None:
            parser.error("history only accepts pagination options")
    elif (not args.spec_sha256 or not args.model_digest
          or args.limit is not None or args.before_record is not None):
        parser.error("export requires source and model digests, without pagination")
    if args.output_file is not None and not args.output_file.is_absolute():
        parser.error("--output-file must be absolute and new")
    try:
        with StateStore(args.db) as store:
            service = ResearchBriefService(store)
            if args.action == "history":
                rows = service.history(args.mission_id, repository=args.repo,
                    limit=args.limit if args.limit is not None else 20,
                    before_record=args.before_record)
                output = json.dumps([{
                    "record_id": record_id,
                    "mission_id": view.receipt.mission_id,
                    "spec_sha256": view.receipt.spec_sha256,
                    "model_digest": view.receipt.model_digest,
                    "proposal_sha256": view.receipt.proposal_sha256,
                    "current": view.current,
                    "source_context_available": view.source_context is not None,
                    "status": view.receipt.status,
                } for record_id, view in rows], ensure_ascii=False, indent=2) + "\n"
            else:
                output = service.export(args.mission_id, repository=args.repo,
                    spec_sha256=args.spec_sha256, model_digest=args.model_digest)
        if args.output_file is not None:
            # Exclusive creation also rejects an existing symlink and prevents
            # accidentally replacing the database or another operator file.
            with args.output_file.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(output)
        else:
            print(output, end="")
    except (ResearchBriefError, StoreError, OSError, sqlite3.Error) as exc:
        parser.error(f"research draft unavailable: {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
