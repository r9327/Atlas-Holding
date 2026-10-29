"""Read-only machine preflight used by the future Windows setup/UI.

This entrypoint reports observations, not an installation or a verified model
choice. The provisioner can act on the result in a separate authorized phase.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .inventory import scan
from .provision_plan import make_plan
from .mission_preflight import inspect_mission
from .state_store import StoreError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-inventory")
    parser.add_argument("--install-target", required=True, type=Path)
    parser.add_argument("--plan", action="store_true",
                        help="Include the existing read-only provisioning plan")
    parser.add_argument("--state-db", type=Path,
                        help="Existing SQLite state database (mission preflight only)")
    parser.add_argument("--mission-id",
                        help="Inspect one tracked mission with --state-db and --plan")
    parser.add_argument("--online", action="store_true",
                        help="Trusted caller assumes network availability; requires --plan")
    args = parser.parse_args(argv)
    if (args.state_db is None) != (args.mission_id is None):
        parser.error("--state-db and --mission-id must be supplied together")
    if args.mission_id is not None and not args.plan:
        parser.error("Mission preflight requires --plan")
    if args.state_db is not None and not args.state_db.is_absolute():
        parser.error("--state-db must be an absolute path")
    if args.online and not args.plan:
        parser.error("--online requires --plan; it is not a network health check")
    if not args.install_target.is_absolute():
        parser.error("--install-target must be an absolute path")
    try:
        report = scan(args.install_target)
    except (ValueError, OSError) as exc:
        parser.error(f"Cannot inspect installation target: {exc}")
    payload = report.as_json()
    if args.plan:
        payload = {
            "inventory": payload,
            "provision_plan": asdict(make_plan(report, online=args.online)),
            "network_available_assumed": args.online,
            "network_verification": "NOT_RUN",
            "executes_actions": False,
        }
        if args.mission_id is not None:
            try:
                payload["mission_preflight"] = asdict(
                    inspect_mission(args.state_db, args.mission_id))
            except (StoreError, OSError, ValueError) as exc:
                parser.error(f"Cannot inspect mission: {exc}")
    print(json.dumps(payload, ensure_ascii=False,
                     sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
