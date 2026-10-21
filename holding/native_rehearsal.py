"""Windows-only nonrelease integration rehearsal across exact #2 and #11 SHAs.

Builds two REAL frozen runtimes from the integrated service checkout and
embeds their exact bytes inside the REAL setup from the bootstrap checkout.
The run does not install, publish, enroll ACLs, or certify an autonomous agent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

_SHA = re.compile(r"^[0-9a-f]{40}$")


def _call(command: list[str], *, cwd: Path, timeout: int) -> None:
    subprocess.run(
        command, cwd=cwd, timeout=timeout, shell=False, check=True,
    )


def _digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def _checked_checkout(path: Path, expected: str) -> None:
    if (not path.is_absolute() or path.is_symlink() or
            path.resolve(strict=True) != path or
            not (path / "holding" / "__init__.py").is_file()):
        raise ValueError("Native rehearsal requires canonical checked-out code")
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True,
        text=True, timeout=20, shell=False, check=True,
    ).stdout.strip()
    if actual != expected:
        raise ValueError("Native integration input changed from approved SHA")
    changed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=path, capture_output=True, text=True, timeout=20,
        shell=False, check=True,
    ).stdout
    if changed.strip():
        raise ValueError("Trusted integration checkout is not clean")


def run_rehearsal(
    bootstrap: Path, integration: Path, *,
    bootstrap_sha: str, integration_sha: str,
) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Native frozen integration requires Windows")
    if any(not _SHA.fullmatch(pin) for pin in (bootstrap_sha, integration_sha)):
        raise ValueError("Exact source SHA pins required")
    _checked_checkout(bootstrap, bootstrap_sha)
    _checked_checkout(integration, integration_sha)

    with tempfile.TemporaryDirectory(prefix="atlas-triple-exe-proof-") as tmp:
        root = Path(tmp).resolve()
        binaries = root / "frozen-runtimes"
        binaries.mkdir()
        _call(
            [sys.executable, "-B", "-X", "utf8", "-m",
             "holding.windows_control_center_build", "--kind", "both",
             "--output-dir", str(binaries)],
            cwd=integration, timeout=1100,
        )
        viewer = binaries / "AtlasHoldingControlCenter.exe"
        engine = binaries / "AtlasHoldingEngine.exe"
        for binary in (viewer, engine):
            if not binary.is_file() or binary.stat().st_size < 1_000_000:
                raise RuntimeError("Real frozen runtime binary not produced")
            _call([str(binary), "--self-test"], cwd=root, timeout=100)
        asset_manifest = root / "synthetic-offline-assets.json"
        asset_manifest.write_text(json.dumps({
            "version": 1,
            "assets": [{
                "component": "git", "version": "2.47",
                "url": "https://example.invalid/git.zip",
                "sha256": "a" * 64,
                "download_bytes": 300, "unpacked_bytes": 500,
            }],
        }, sort_keys=True), encoding="utf-8")
        candidate = root / "single-setup"
        candidate.mkdir()
        viewer_sha = _digest(viewer)
        engine_sha = _digest(engine)
        _call(
            [sys.executable, "-B", "-X", "utf8", "-m",
             "holding.windows_build",
             "--manifest", str(asset_manifest),
             "--manifest-sha256", _digest(asset_manifest),
             "--runtime-directory", str(binaries),
             "--viewer-sha256", viewer_sha,
             "--engine-sha256", engine_sha,
             "--output-dir", str(candidate)],
            cwd=bootstrap, timeout=1100,
        )
        setup = candidate / "AtlasHoldingSetup.exe"
        if not setup.is_file() or setup.stat().st_size <= max(
                viewer.stat().st_size, engine.stat().st_size):
            raise RuntimeError("Real setup failed to embed both frozen applications")
        # The self-test must independently validate the extracted contents
        # against the two actual runtime hashes before returning success.
        _call([str(setup), "--self-test"], cwd=root, timeout=100)
        denied = subprocess.run(
            [str(setup), "--invalid-mode"],
            cwd=root, timeout=100, check=False, shell=False,
        )
        if denied.returncode != 2:
            raise RuntimeError("Frozen setup accepted a forbidden startup mode")
        # Unlike a simple file-copy demonstration, this invokes the actual
        # PR#2 production staging function on the same measured real binaries,
        # and boots both executables from an accented, space-bearing path.
        _call(
            [sys.executable, "-B", "-X", "utf8",
             str(Path(__file__).resolve().parent.parent
                 / "tests" / "windows_installed_pair_smoke.py"),
             "--bootstrap", str(bootstrap),
             "--runtime-source", str(binaries),
             "--destination", str(root / "Disque choisi été" / "Atlas Holding"),
             "--viewer-sha256", viewer_sha,
             "--engine-sha256", engine_sha],
            cwd=bootstrap, timeout=300,
        )
        _checked_checkout(bootstrap, bootstrap_sha)
        _checked_checkout(integration, integration_sha)
        print("NONRELEASE_REAL_GUI_ENGINE_INSIDE_REAL_SETUP_PASS")
        print("Setup SHA256: " + _digest(setup))
        print("Viewer SHA256: " + viewer_sha)
        print("Engine SHA256: " + engine_sha)
        print("Source SHAs: " + bootstrap_sha + " " + integration_sha)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-native-rehearsal")
    parser.add_argument("--bootstrap", required=True, type=Path)
    parser.add_argument("--integration", required=True, type=Path)
    parser.add_argument("--bootstrap-sha", required=True)
    parser.add_argument("--integration-sha", required=True)
    args = parser.parse_args(argv)
    run_rehearsal(
        args.bootstrap, args.integration,
        bootstrap_sha=args.bootstrap_sha, integration_sha=args.integration_sha,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
