"""Prepare a reproducible, SHA-pinned MinGit release candidate from real bytes.

The immutable Git for Windows release publishes a MinGit ZIP without an Inno
Setup/UAC installer. Never invent compressed/uncompressed sizes: derive them
only after the complete publisher SHA-256 matches the downloaded ZIP.
This prepares a *candidate manifest*, never authorizes execution or release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

from .staged_install import MAX_FILES, MAX_UNPACKED_BYTES, PinnedZip, _safe_members
from .windows_package_manager import WingetPin


MINGIT_URL = (
    "https://github.com/git-for-windows/git/releases/download/"
    "v2.56.0.windows.2/MinGit-2.56.0.2-64-bit.zip"
)
MINGIT_SHA256 = "da35e72aa21c005a5a0d298cfbae110bc1609a815730ea0dde84b01a1b3cd3be"
MINGIT_PACKAGE_VERSION = "2.56.0.2"
MINGIT_NATIVE_GIT_VERSION = "2.56.0"
MINGIT_EXECUTABLE = "cmd/git.exe"


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def prepare_mingit_candidate(archive: Path) -> tuple[PinnedZip, ...]:
    """Offline archive inspection, then generate exact hybrid ZIP/WinGet pins."""
    if not isinstance(archive, Path) or not archive.is_absolute():
        raise ValueError("An absolute previously downloaded ZIP is required")
    if (archive.is_symlink() or not archive.is_file()
            or archive.stat().st_nlink != 1):
        raise ValueError("Refusing missing or linked Git ZIP")
    compressed = archive.stat().st_size
    if not 0 < compressed <= 512 * 1024**2:
        raise ValueError("MinGit archive has an invalid byte count")
    if _digest(archive) != MINGIT_SHA256:
        raise ValueError("MinGit ZIP differs from official Git for Windows SHA256")
    try:
        with zipfile.ZipFile(archive, "r") as payload:
            info = payload.infolist()
            if not 0 < len(info) <= MAX_FILES:
                raise ValueError("Invalid MinGit archive member count")
            unpacked = sum(x.file_size for x in info if not x.is_dir())
            if not 0 < unpacked <= MAX_UNPACKED_BYTES:
                raise ValueError("Invalid MinGit uncompressed size")
            git = PinnedZip(
                component="git",
                version=MINGIT_PACKAGE_VERSION,
                url=MINGIT_URL,
                sha256=MINGIT_SHA256,
                download_bytes=compressed,
                unpacked_bytes=unpacked,
                executable=MINGIT_EXECUTABLE,
                expected_version=MINGIT_NATIVE_GIT_VERSION,
            )
            # _safe_members checks archive names, link modes, Windows
            # collisions, reserved paths and exact total unpacked bytes.
            members = _safe_members(payload, git)
            names = {relative.casefold() for info, relative in members
                     if not info.is_dir()}
            if MINGIT_EXECUTABLE.casefold() not in names:
                raise ValueError("Publisher ZIP lacks expected cmd/git.exe")
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ValueError("Invalid publisher ZIP") from exc
    return (
        git,
        WingetPin("ollama", "Ollama.Ollama", "0.32.8"),
        WingetPin("opencode", "SST.opencode", "1.18.29"),
    )


def manifest_for_candidate(pins: tuple[PinnedZip | WingetPin, ...]) -> bytes:
    """Canonical byte representation suitable for an independently pinned hash."""
    assets = []
    for pin in pins:
        pin.validate()
        if isinstance(pin, PinnedZip):
            assets.append({
                "kind": "zip", "component": pin.component,
                "version": pin.version, "url": pin.url,
                "sha256": pin.sha256, "download_bytes": pin.download_bytes,
                "unpacked_bytes": pin.unpacked_bytes,
                "executable": pin.executable,
                "expected_version": pin.expected_version,
            })
        else:
            assets.append({
                "kind": "winget", "component": pin.component,
                "package_id": pin.package_id, "version": pin.version,
            })
    if {x["component"] for x in assets} != {"git", "ollama", "opencode"}:
        raise ValueError("Incomplete release candidate")
    return (json.dumps({"version": 3, "assets": assets},
                       sort_keys=True, indent=2) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-prepare-mingit")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.output.is_absolute() or args.output.exists():
        raise ValueError("Use an absolute unused candidate-manifest output")
    encoded = manifest_for_candidate(prepare_mingit_candidate(args.archive))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as target:
        target.write(encoded)
    print(json.dumps({
        "candidate": str(args.output),
        "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
        "state": "CANDIDATE_NOT_RELEASE_APPROVED",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
