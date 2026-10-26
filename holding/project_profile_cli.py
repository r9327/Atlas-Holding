"""Read-only CLI to validate a Holding project-profile/v1 manifest.

This does not load models, execute project commands, edit Git or authorize work.
Usage: python -m holding.project_profile_cli PATH [--json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .project_profile import InvalidProjectProfile, load_profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a project profile (no execution)")
    parser.add_argument("file", type=Path, help="Path to a project-profile/v1 JSON file")
    parser.add_argument("--json", action="store_true", help="Machine-readable result")
    args = parser.parse_args(argv)
    try:
        if args.file.stat().st_size > 65536:
            raise InvalidProjectProfile("profile file exceeds 64 KiB")
        profile = load_profile(args.file.read_text(encoding="utf-8"))
    except (InvalidProjectProfile, OSError, UnicodeError) as exc:
        result = {"status": "INVALID", "reason": str(exc)}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    result = {
        "status": "VALID",
        "schema": "project-profile/v1",
        "name": profile.name,
        "repository": profile.repository,
        "branch": profile.branch,
        "sha256": profile.digest,
        "stages": [stage for stage, _ in profile.commands],
        "adapters": list(profile.adapters),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"VALID {profile.name} {profile.repository} SHA256 {profile.digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
