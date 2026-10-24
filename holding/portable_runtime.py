"""Prepare a hybrid UAC-free ZIP manifest for Git and OpenCode.

Both portable binaries come from official publisher release assets. Their
compressed/uncompressed sizes are measured from verified bytes, never guessed.
Ollama remains a separately consented, user-scoped Windows installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from .mingit_candidate import (
    MINGIT_SHA256, MINGIT_URL, MINGIT_NATIVE_GIT_VERSION,
    MINGIT_PACKAGE_VERSION, MINGIT_EXECUTABLE, _digest,
    manifest_for_candidate,
)
from .staged_install import (
    MAX_FILES, MAX_UNPACKED_BYTES, PinnedZip, _safe_members,
)
from .windows_package_manager import WingetPin


OPENCODE_URL = (
    "https://github.com/anomalyco/opencode/releases/download/"
    "v1.18.29/opencode-windows-x64.zip"
)
OPENCODE_SHA256 = "b32618aa3d1415f6e4f473aec248edef25759203fb707d7d968359d86d4a35ee"
OPENCODE_VERSION = "1.18.29"
OPENCODE_EXECUTABLE = "opencode.exe"


def _verified_official_zip(
    archive: Path, *, component: str, version: str, expected_version: str,
    url: str, sha256: str, executable: str,
) -> PinnedZip:
    """Derive authenticated archive sizes and require a real native EXE."""
    if not isinstance(archive, Path) or not archive.is_absolute():
        raise ValueError("Absolute publisher ZIP path required")
    if (archive.is_symlink() or not archive.is_file()
            or archive.stat().st_nlink != 1):
        raise ValueError("Publisher ZIP must be a regular unlinked file")
    compressed = archive.stat().st_size
    if not 0 < compressed <= 512 * 1024**2:
        raise ValueError("Publisher ZIP outside supported size limit")
    if _digest(archive) != sha256:
        raise ValueError("Publisher ZIP SHA256 does not match official release")
    try:
        with zipfile.ZipFile(archive) as payload:
            rows = payload.infolist()
            if not 0 < len(rows) <= MAX_FILES:
                raise ValueError("Invalid archive member count")
            unpacked = sum(x.file_size for x in rows if not x.is_dir())
            if not 0 < unpacked <= MAX_UNPACKED_BYTES:
                raise ValueError("Invalid unpacked asset size")
            pinned = PinnedZip(
                component=component, version=version, url=url, sha256=sha256,
                download_bytes=compressed, unpacked_bytes=unpacked,
                executable=executable, expected_version=expected_version,
            )
            entries = _safe_members(payload, pinned)
            available = {
                name.casefold() for info, name in entries if not info.is_dir()
            }
            if executable.casefold() not in available:
                raise ValueError("Publisher ZIP missing native executable")
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ValueError("Malformed publisher ZIP") from exc
    return pinned


def prepare_portable_manifest(git_zip: Path, opencode_zip: Path) -> bytes:
    git = _verified_official_zip(
        git_zip, component="git", version=MINGIT_PACKAGE_VERSION,
        expected_version=MINGIT_NATIVE_GIT_VERSION, url=MINGIT_URL,
        sha256=MINGIT_SHA256, executable=MINGIT_EXECUTABLE,
    )
    opencode = _verified_official_zip(
        opencode_zip, component="opencode", version=OPENCODE_VERSION,
        expected_version=OPENCODE_VERSION, url=OPENCODE_URL,
        sha256=OPENCODE_SHA256, executable=OPENCODE_EXECUTABLE,
    )
    return manifest_for_candidate((
        git, WingetPin("ollama", "Ollama.Ollama", "0.32.8"), opencode,
    ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-portable-candidate")
    parser.add_argument("--git-archive", type=Path, required=True)
    parser.add_argument("--opencode-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.output.is_absolute() or args.output.exists():
        raise ValueError("Candidate output must be an unused absolute path")
    encoded = prepare_portable_manifest(args.git_archive, args.opencode_archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as output:
        output.write(encoded)
    print(json.dumps({
        "state": "CANDIDATE_NOT_RELEASE_APPROVED",
        "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
        "candidate": str(args.output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
