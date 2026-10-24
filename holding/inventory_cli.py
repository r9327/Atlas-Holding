"""Read-only machine preflight used by the future Windows setup/UI.

This entrypoint reports observations, not an installation or a verified model
choice. The provisioner can act on the result in a separate authorized phase.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .inventory import scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-inventory")
    parser.add_argument("--install-target", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.install_target.is_absolute():
        parser.error("--install-target must be an absolute path")
    try:
        report = scan(args.install_target)
    except (ValueError, OSError) as exc:
        parser.error(f"Cannot inspect installation target: {exc}")
    print(json.dumps(report.as_json(), ensure_ascii=False,
                     sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
