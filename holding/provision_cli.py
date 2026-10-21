"""Headless bootstrap preflight for the future AtlasHoldingSetup.exe host.

The trusted Windows setup pins the release manifest, chooses the disk and
supplies consent. No manual model selection, remote script execution or
claim of a working install is made by this command.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .bootstrap_assets_cli import BootstrapManifestError, read_pinned_manifest
from .inventory import scan
from .provision_runtime import stage_required_dependencies
from .staged_install import InstallError
from .windows_setup import assess_post_install


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-provision")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--online-confirmed", action="store_true",
                        help="Trusted host observed network availability")
    parser.add_argument("--approve-install", action="store_true",
                        help="Initial setup authorization for pinned dependency staging")
    args = parser.parse_args(argv)
    try:
        if not args.install_root.is_absolute() or not args.cache_root.is_absolute():
            raise ValueError("Absolute install/cache roots required")
        pins = read_pinned_manifest(args.manifest,
                                    expected_sha256=args.manifest_sha256)
        inventory = scan(args.install_root)
        result = stage_required_dependencies(
            inventory, pins,
            online=args.online_confirmed, authorized=args.approve_install,
            install_root=args.install_root, cache_root=args.cache_root,
        )
        if args.approve_install and result.state == "PAYLOADS_STAGED_NOT_VALIDATED":
            # CLI and GUI must not diverge: a successful unpack/winget exit
            # is never proof that the native toolchain really works.
            result = assess_post_install(
                result, scan(args.install_root), pins,
                install_root=args.install_root,
            )
        print(json.dumps(asdict(result), sort_keys=True, ensure_ascii=True))
        return 0 if result.state in {
            "AUTH_REQUIRED", "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING",
        } else 2
    except (BootstrapManifestError, InstallError, ValueError, OSError) as exc:
        # No raw exception messages or URL tokens in user-facing/error logs.
        print(json.dumps({
            "state": "BLOCKED", "reason": type(exc).__name__,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
