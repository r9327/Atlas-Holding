"""Trusted bootstrap entry point for SHA-pinned versioned runtime payloads.

The distributable setup must supply the *expected manifest digest* independently
of the manifest. CLI defaults to read-only preview; execution requires approval.
This command does not install Ollama models or certify runnable dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .downloads import DownloadError, _validate_url
from .staged_install import (InstallError, PinnedZip, install_versioned_zip,
                             inspect_versioned_zip)
from .windows_package_manager import WingetPin, install_pinned_winget


MAX_MANIFEST_BYTES = 128 * 1024
_SHA = re.compile(r"^[0-9a-fA-F]{64}$")


class BootstrapManifestError(ValueError):
    pass


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise BootstrapManifestError("Duplicate manifest JSON key")
        obj[key] = value
    return obj


def read_pinned_manifest(path: Path, *, expected_sha256: str) -> tuple[PinnedZip | WingetPin, ...]:
    """Parse exactly one externally pinned manifest without any side effects."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise BootstrapManifestError("Absolute manifest path required")
    if not isinstance(expected_sha256, str) or not _SHA.fullmatch(expected_sha256):
        raise BootstrapManifestError("External SHA-256 pin required")
    if path.is_symlink() or not path.is_file():
        raise BootstrapManifestError("Manifest must be a regular file")
    with path.open("rb") as source:
        raw = source.read(MAX_MANIFEST_BYTES + 1)
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise BootstrapManifestError("Manifest size is invalid")
    if hashlib.sha256(raw).hexdigest() != expected_sha256.lower():
        raise BootstrapManifestError("Manifest does not match release pin")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, ValueError) as exc:
        raise BootstrapManifestError("Manifest JSON rejected") from exc
    if (not isinstance(data, dict) or set(data) != {"version", "assets"}
            or type(data["version"]) is not int or data["version"] not in (1, 2, 3)
            or not isinstance(data["assets"], list)
            or not 1 <= len(data["assets"]) <= 32):
        raise BootstrapManifestError("Invalid manifest schema")
    base_fields = {
        "component", "version", "url", "sha256", "download_bytes",
        "unpacked_bytes",
    }
    names = set()
    assets: list[PinnedZip | WingetPin] = []
    for row in data["assets"]:
        if not isinstance(row, dict):
            raise BootstrapManifestError("Invalid asset record")
        try:
            if data["version"] == 3 and row.get("kind") == "winget":
                if set(row) != {"kind", "component", "package_id", "version"}:
                    raise BootstrapManifestError("Invalid winget asset fields")
                item = WingetPin(row["component"], row["package_id"],
                                 row["version"])
                item.validate()
                identity = (item.component.casefold(), item.version.casefold())
            else:
                required = (base_fields if data["version"] == 1
                            else base_fields | {"executable", "expected_version"})
                if data["version"] == 3:
                    required = required | {"kind"}
                    if row.get("kind") != "zip":
                        raise BootstrapManifestError("Invalid asset kind")
                if set(row) != required:
                    raise BootstrapManifestError("Invalid ZIP asset fields")
                record = {k: v for k, v in row.items() if k != "kind"}
                item = PinnedZip(**record)
                item.validate()
                _validate_url(item.url)
                identity = (item.component.casefold(), item.version.casefold())
        except (TypeError, ValueError) as exc:
            raise BootstrapManifestError("Asset pin or URL is invalid") from exc
        if identity in names or any(name == identity[0] for name, _ in names):
            raise BootstrapManifestError("Duplicate component version")
        names.add(identity)
        assets.append(item)
    return tuple(assets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-bootstrap")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--online-confirmed", action="store_true")
    parser.add_argument("--approve-install", action="store_true",
                        help="Authorize all pinned assets in this one manifest")
    args = parser.parse_args(argv)
    try:
        assets = read_pinned_manifest(
            args.manifest, expected_sha256=args.manifest_sha256,
        )
        if not args.install_root.is_absolute() or not args.cache_root.is_absolute():
            raise BootstrapManifestError("Absolute install and cache roots required")
        results = []
        if not args.approve_install:
            for spec in assets:
                if isinstance(spec, WingetPin):
                    results.append({
                        "component": spec.component, "version": spec.version,
                        "status": "AWAITING_APPROVAL",
                        "installation_state": "WINGET_VERSION_PINNED_NOT_PROBED",
                        "download_bytes": None, "unpacked_bytes": None,
                    })
                    continue
                inspection = inspect_versioned_zip(
                    spec, install_root=args.install_root,
                )
                results.append({
                    "component": spec.component, "version": spec.version,
                    "status": ("BLOCKED" if inspection.status == "BLOCKED_INVALID"
                               else "AWAITING_APPROVAL"),
                    "installation_state": inspection.status,
                    "file_count": inspection.file_count,
                    "download_bytes": (
                        0 if inspection.status == "VERIFIED_PAYLOAD"
                        else spec.download_bytes
                    ),
                    "unpacked_bytes": spec.unpacked_bytes,
                })
            blocked = any(item["status"] == "BLOCKED" for item in results)
            print(json.dumps({
                "state": "PREVIEW_BLOCKED" if blocked else "PREVIEW_ONLY",
                "assets": results,
            }, sort_keys=True))
            return 2 if blocked else 0
        for spec in assets:
            try:
                if isinstance(spec, WingetPin):
                    winget = install_pinned_winget(
                        spec, authorized=True, online=args.online_confirmed,
                    )
                    if winget.state != "INSTALLED_NOT_HEALTHCHECKED":
                        results.append({
                            "component": spec.component, "version": spec.version,
                            "status": winget.state,
                        })
                        print(json.dumps({
                            "state": "PARTIAL_OR_BLOCKED", "assets": results,
                        }, sort_keys=True))
                        return 2
                    results.append({
                        "component": spec.component, "version": spec.version,
                        "status": winget.state,
                    })
                    continue
                installed = install_versioned_zip(
                    spec, install_root=args.install_root,
                    cache_root=args.cache_root, authorized=True,
                )
            except (DownloadError, InstallError, OSError) as exc:
                results.append({
                    "component": spec.component, "version": spec.version,
                    "status": "BLOCKED", "reason": type(exc).__name__,
                })
                print(json.dumps({"state": "PARTIAL_OR_BLOCKED", "assets": results},
                                 sort_keys=True))
                return 2
            results.append({
                "component": spec.component, "version": spec.version,
                "status": installed.status, "file_count": installed.file_count,
                "directory": str(installed.directory),
            })
        print(json.dumps({"state": "PAYLOADS_STAGED_NOT_VALIDATED", "assets": results},
                         sort_keys=True))
        return 0
    except (BootstrapManifestError, OSError) as exc:
        print(json.dumps({"state": "BLOCKED", "reason": type(exc).__name__},
                         sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
