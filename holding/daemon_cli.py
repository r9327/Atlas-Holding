"""Operator-side, local-only CLI for the headless service.

This CLI does not load or execute model-provided Python. Agent bootstrapping is
restricted to the trusted launcher/agent integration in a subsequent PR.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .control import ControlPlane
from .roadmap_import import MAX_ROADMAP_BYTES
from .state_store import StoreError
from .service import default_database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding")
    parser.add_argument("--db", type=Path, default=default_database())
    parser.add_argument("action", choices=("status", "pause", "resume", "stop",
                                           "clear-stop", "import", "coverage"))
    parser.add_argument("--roadmap-file", type=Path)
    parser.add_argument("--source")
    args = parser.parse_args(argv)
    if not args.db.is_absolute():
        parser.error("--db must be an absolute path")
    if args.action == "import":
        path = args.roadmap_file
        if path is None:
            parser.error("import requires --roadmap-file")
        if path.is_symlink() or not path.is_file():
            parser.error("roadmap must be an existing regular, non-symlinked file")
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_ROADMAP_BYTES + 1)
            if len(data) > MAX_ROADMAP_BYTES:
                parser.error("roadmap exceeds the 2 MiB intake limit")
            raw = data.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            parser.error(f"cannot read UTF-8 roadmap: {type(exc).__name__}")
        source = args.source if args.source is not None else f"file:{path.resolve()}"
        try:
            with ControlPlane(args.db) as control:
                imported = asdict(control.import_roadmap(raw, source=source))
        except StoreError as exc:
            parser.error(f"roadmap rejected: {exc}")
        print(json.dumps(imported, sort_keys=True, separators=(",", ":")))
        return 0
    if args.roadmap_file is not None or args.source is not None:
        parser.error("--roadmap-file and --source are valid only with import")
    with ControlPlane(args.db) as control:
        if args.action == "pause":
            control.pause()
        elif args.action == "resume":
            control.resume()
        elif args.action == "stop":
            control.request_stop()
        elif args.action == "clear-stop":
            control.clear_stop_for_restart()
        snap = asdict(control.snapshot())
    print(json.dumps(snap, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
