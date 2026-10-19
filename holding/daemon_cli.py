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
from .service import default_database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding")
    parser.add_argument("--db", type=Path, default=default_database())
    parser.add_argument("action", choices=("status", "pause", "resume", "stop", "clear-stop"))
    args = parser.parse_args(argv)
    if not args.db.is_absolute():
        parser.error("--db must be an absolute path")
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
