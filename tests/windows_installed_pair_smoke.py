"""Native staging and launch proof for the REAL frozen runtime pair.

Runs only in a GitHub Actions trust-controlled staging rehearsal. It imports
the exact SHA-pinned PR #2 implementation, not a duplicate installer. This
does not test Windows DACL, production first run, or autonomous AI.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", type=Path, required=True)
    parser.add_argument("--runtime-source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--viewer-sha256", required=True)
    parser.add_argument("--engine-sha256", required=True)
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        raise RuntimeError("Actual Windows executable launch required")
    if (not args.bootstrap.is_absolute()
            or args.bootstrap.resolve(strict=True) != args.bootstrap
            or not (args.bootstrap / "holding" / "windows_runtime_payload.py").is_file()
            or not args.runtime_source.is_absolute()
            or not args.destination.is_absolute()):
        raise ValueError("Expected canonical trusted Windows runtime locations")
    # No other Holding module is loaded in this standalone test entrypoint.
    # Inserting the already pinned and verified PR#2 checkout directly lets
    # us exercise its production staging function rather than a copy.
    sys.path.insert(0, str(args.bootstrap))
    from holding.windows_runtime_payload import (
        RuntimePayloadError, install_pinned_runtime_pair,
        verify_runtime_payload,
    )

    expected = {
        "AtlasHoldingControlCenter.exe": args.viewer_sha256,
        "AtlasHoldingEngine.exe": args.engine_sha256,
    }
    verify_runtime_payload(args.runtime_source, expected)
    args.destination.mkdir(parents=True, exist_ok=False)
    try:
        install_pinned_runtime_pair(
            args.runtime_source, args.destination, expected, approved=False,
        )
    except RuntimePayloadError:
        pass
    else:
        raise RuntimeError("Unauthorized runtime placement succeeded")
    if (args.destination / "runtime").exists():
        raise RuntimeError("Unapproved runtime created a destination")

    installed = install_pinned_runtime_pair(
        args.runtime_source, args.destination, expected, approved=True,
    )
    if installed.ready or installed.state != "STAGED_BINARIES_SECURITY_PENDING":
        raise RuntimeError("Setup falsely claimed a certified installation")
    verify_runtime_payload(installed.directory, expected)
    for name in sorted(expected):
        binary = installed.directory / name
        completed = subprocess.run(
            [str(binary), "--self-test"], cwd=installed.directory,
            timeout=100, shell=False, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if completed.returncode != 0:
            raise RuntimeError("Real frozen runtime failed after installation to selected volume")
        with binary.open("rb") as stream:
            measured = hashlib.file_digest(stream, "sha256").hexdigest()
        if measured != expected[name]:
            raise RuntimeError("Installed binary changed after native execution")
    again = install_pinned_runtime_pair(
        args.runtime_source, args.destination, expected, approved=True,
    )
    if (again.state != "REUSED_BINARIES_SECURITY_PENDING"
            or again.directory != installed.directory):
        raise RuntimeError("Repeat installation rewrote the existing binary pair")

    # Fail-closed repair: corrupted installed binaries are retained for a
    # separately authorized repair; never silently rewritten from setup.
    damaged = installed.directory / "AtlasHoldingEngine.exe"
    damaged.write_bytes(b"tampered")
    try:
        install_pinned_runtime_pair(
            args.runtime_source, args.destination, expected, approved=True,
        )
    except RuntimePayloadError:
        pass
    else:
        raise RuntimeError("Corrupt existing engine was silently accepted")
    if damaged.read_bytes() != b"tampered":
        raise RuntimeError("Installed engine was overwritten without explicit repair")
    print("NONRELEASE_REAL_RUNTIME_SELECTED_DISK_LAUNCH_PASS")
    print("NONRELEASE_RUNTIME_REPEAT_AND_TAMPER_REFUSAL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
