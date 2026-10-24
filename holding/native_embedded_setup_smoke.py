"""Native Windows proof for bundled (synthetic) runtime assets in Setup.exe.

This runner-only smoke builds an actual PyInstaller setup and launches its
--self-test, then checks pinned staging onto a path with spaces and accents.
The two enclosed EXE-named test files are synthetic, NOT working Holding
engine/viewer binaries. This is never a release or ACL certification.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile

from .windows_build import build_windows_installer
from .windows_runtime_payload import (
    RuntimePayloadError, install_pinned_runtime_pair, verify_runtime_payload,
)

_FILES = ("AtlasHoldingControlCenter.exe", "AtlasHoldingEngine.exe")


def run_native_embedded_setup_smoke(manifest: Path, manifest_sha256: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Real Windows runner required")
    if (not isinstance(manifest, Path) or not manifest.is_absolute()
            or manifest.is_symlink() or not manifest.is_file()):
        raise ValueError("Expected a trusted absolute manifest path")
    with tempfile.TemporaryDirectory(prefix="atlas-setup-embedded-smoke-") as scratch:
        root = Path(scratch).resolve()
        inputs = root / "synthetic-not-runnable"
        inputs.mkdir()
        # Distinct fixed payloads exceeding the production size floor.
        for i, name in enumerate(_FILES):
            (inputs / name).write_bytes(b"MZ" + bytes((i + 73,)) * (1_000_041 + i))
        pins = {
            name: hashlib.sha256((inputs / name).read_bytes()).hexdigest()
            for name in _FILES
        }
        observed = verify_runtime_payload(inputs, pins)
        if observed != pins:
            raise RuntimeError("Synthetic fixture mismatch")
        output = root / "built"
        artifact = build_windows_installer(
            manifest=manifest, expected_manifest_sha256=manifest_sha256,
            output_dir=output, runtime_directory=inputs, runtime_pins=pins,
        )
        executable = Path(artifact.artifact)
        if (not executable.is_file() or
                hashlib.sha256(executable.read_bytes()).hexdigest() != artifact.sha256):
            raise RuntimeError("Packaged setup was modified after build")
        self_test = subprocess.run(
            [str(executable), "--self-test"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=70, shell=False,
            check=False,
        )
        if self_test.returncode != 0:
            raise RuntimeError("Frozen setup could not verify its extracted runtime data")
        invalid = subprocess.run(
            [str(executable), "--unsupported-setup-command"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=70, shell=False,
            check=False,
        )
        if invalid.returncode != 2:
            raise RuntimeError("Frozen setup unexpectedly accepted unknown command")
        # A real chosen volume is tested, but no ACL or UI registration is
        # implied by merely staging fixture bytes.
        destination = root / "Disque choisi été" / "Atlas Holding"
        destination.mkdir(parents=True)
        try:
            install_pinned_runtime_pair(inputs, destination, pins, approved=False)
        except RuntimePayloadError:
            pass
        else:
            raise RuntimeError("Runtime install happened without authorization")
        if (destination / "runtime").exists():
            raise RuntimeError("Unauthorized setup wrote a runtime directory")
        staged = install_pinned_runtime_pair(inputs, destination, pins, approved=True)
        if staged.ready or staged.state != "STAGED_BINARIES_SECURITY_PENDING":
            raise RuntimeError("Uncertified runtime was incorrectly marked READY")
        verify_runtime_payload(staged.directory, pins)
        reused = install_pinned_runtime_pair(inputs, destination, pins, approved=True)
        if reused.state != "REUSED_BINARIES_SECURITY_PENDING":
            raise RuntimeError("Verified reinstallation was not idempotent")
        (staged.directory / _FILES[1]).write_bytes(b"tampered")
        try:
            install_pinned_runtime_pair(inputs, destination, pins, approved=True)
        except RuntimePayloadError:
            pass
        else:
            raise RuntimeError("Modified installed engine was silently overwritten")
        if (staged.directory / _FILES[1]).read_bytes() != b"tampered":
            raise RuntimeError("Damaged engine was overwritten without repair consent")
        print("NONRELEASE_SETUP_EMBEDDED_PAYLOAD_NATIVE_PASS")
        print("NONRELEASE_RUNTIME_DISK_CHOICE_AND_IDEMPOTENCE_PASS")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-embedded-runtime-smoke")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args(argv)
    run_native_embedded_setup_smoke(args.manifest, args.manifest_sha256)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
