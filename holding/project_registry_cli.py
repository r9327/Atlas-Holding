"""Trusted operator CLI for persisted, non-executing Holding project profiles.

Usage: python -m holding.project_registry_cli --root PATH list|register|select|clear
Selecting a profile never launches a model, process, test or Git write.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .project_profile import InvalidProjectProfile
from .project_registry import ProjectRegistry, ProjectRegistryError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Holding multi-project config (no execution)")
    parser.add_argument("--root", type=Path, required=True, help="local profile registry folder")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    register = actions.add_parser("register")
    register.add_argument("file", type=Path)
    register.add_argument("--expected-digest", help="mandatory when replacing an existing profile")
    select = actions.add_parser("select")
    select.add_argument("name")
    select.add_argument("--expected-digest", required=True)
    actions.add_parser("clear")
    args = parser.parse_args(argv)
    try:
        registry = ProjectRegistry(args.root)
        if args.action == "register":
            if args.file.is_symlink() or not args.file.is_file() or args.file.stat().st_size > 65536:
                raise ProjectRegistryError("profile input must be a bounded regular file")
            result = registry.register(args.file.read_text(encoding="utf-8"),
                                       expected_digest=args.expected_digest)
            out = {"status": "REGISTERED", "name": result.profile.name,
                   "digest": result.digest, "selected": result.selected}
        elif args.action == "select":
            result = registry.select(args.name, expected_digest=args.expected_digest)
            out = {"status": "SELECTED", "name": result.profile.name,
                   "repository": result.profile.repository, "digest": result.digest}
        elif args.action == "clear":
            registry.clear_selection()
            out = {"status": "SELECTION_CLEARED"}
        else:
            entries = registry.list_entries()
            out = {"status": "READ_ONLY", "profiles": [
                {"name": item.profile.name, "repository": item.profile.repository,
                 "checkout_dir": item.profile.checkout_dir,
                 "digest": item.digest, "selected": item.selected}
                for item in entries
            ]}
    except (ProjectRegistryError, InvalidProjectProfile, OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
