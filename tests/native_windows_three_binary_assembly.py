"""Cross-PR Windows assembly smoke: real frozen binary bytes, never a release.

Run only under a Windows runner with a trusted checkout of this PR and two
additional exact commit-SHA checkouts: #2 (installer) and #11 (engine/GUI).
The manifest below is an explicitly fake *build-only* fixture. No installation
health, Windows ACL, production model or release-readiness is inferred.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from holding.release_evidence import observe_native_release_files

_PINS = {
    "AtlasHoldingSetup.exe",
    "AtlasHoldingEngine.exe",
    "AtlasHoldingControlCenter.exe",
}


def _run(argv: list[str], *, cwd: Path, timeout: int = 900) -> None:
    env = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"}
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    subprocess.run(
        argv, cwd=cwd, env=env, shell=False, check=True,
        timeout=timeout,
    )


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _checkout(root: Path, expected: str) -> None:
    if (not root.is_absolute() or not root.is_dir()
            or len(expected) != 40
            or any(ch not in "0123456789abcdef" for ch in expected)
            or root.is_symlink()
            or root.resolve(strict=True) != root):
        raise ValueError("Trusted immutable integration checkout required")
    actual = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True,
        timeout=15,
    ).strip()
    if actual != expected:
        raise RuntimeError("Cross-PR integration source SHA changed")
    dirty = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain",
         "--untracked-files=normal"], text=True, timeout=15,
    ).strip()
    if dirty:
        raise RuntimeError("Cross-PR integration source tree is dirty")


def assemble(
    *, setup_root: Path, runtime_root: Path, output_dir: Path,
    setup_sha: str, runtime_sha: str,
) -> dict:
    if os.name != "nt":
        raise RuntimeError("Actual three-binary assembly requires native Windows")
    _checkout(setup_root, setup_sha)
    _checkout(runtime_root, runtime_sha)
    if (not output_dir.is_absolute() or output_dir.exists()
            or output_dir.is_symlink()
            or output_dir.parent.resolve(strict=True) != output_dir.parent):
        raise ValueError("New absolute nonrelease output directory required")
    with tempfile.TemporaryDirectory(prefix="atlas-cross-pr-build-") as workspace:
        temp = Path(workspace).resolve()
        viewer_dir = temp / "frozen"
        setup_dir = temp / "installer"
        fixture = temp / "NONRELEASE_BUILD_ONLY_ASSETS.json"
        fixture.write_text(json.dumps({
            "version": 1,
            "assets": [{
                "component": "git",
                "version": "2.47",
                "url": "https://example.invalid/fixture-not-downloadable.zip",
                "sha256": "a" * 64,
                "download_bytes": 300,
                "unpacked_bytes": 500,
            }],
        }, sort_keys=True), encoding="utf-8")
        _run([
            sys.executable, "-B", "-m",
            "holding.windows_control_center_build",
            "--kind", "both", "--output-dir", str(viewer_dir),
        ], cwd=runtime_root)
        engine = viewer_dir / "AtlasHoldingEngine.exe"
        viewer = viewer_dir / "AtlasHoldingControlCenter.exe"
        for executable in (engine, viewer):
            if not executable.is_file() or executable.stat().st_size < 1_000_000:
                raise RuntimeError("Real frozen runtime missing")
        _run([str(engine), "--self-test"], cwd=runtime_root, timeout=90)
        _run([str(viewer), "--self-test"], cwd=runtime_root, timeout=90)
        _run([
            sys.executable, "-B",
            str(runtime_root / "tests" / "native_windows_runtime_smoke.py"),
            "--viewer", str(viewer), "--engine", str(engine),
        ], cwd=runtime_root, timeout=140)
        pins = {
            "AtlasHoldingControlCenter.exe": _sha(viewer),
            "AtlasHoldingEngine.exe": _sha(engine),
        }
        _run([
            sys.executable, "-B", "-m", "holding.windows_build",
            "--manifest", str(fixture),
            "--manifest-sha256", _sha(fixture),
            "--output-dir", str(setup_dir),
            "--runtime-directory", str(viewer_dir),
            "--viewer-sha256", pins["AtlasHoldingControlCenter.exe"],
            "--engine-sha256", pins["AtlasHoldingEngine.exe"],
        ], cwd=setup_root)
        installer = setup_dir / "AtlasHoldingSetup.exe"
        if not installer.is_file() or installer.stat().st_size < 1_000_000:
            raise RuntimeError("Bundled native Windows installer missing")
        _run([str(installer), "--self-test"], cwd=setup_root, timeout=110)
        # All three observed files are copied together. The final directory
        # is never replaced; a failed attempt never leaves a fake candidate.
        output_dir.mkdir()
        try:
            for item in (engine, viewer, installer):
                with item.open("rb") as src, (output_dir / item.name).open("xb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
            observed = observe_native_release_files(output_dir)
            originals = {
                item.name: (_sha(item), item.stat().st_size)
                for item in (engine, viewer, installer)
            }
            if {name: (v.sha256, v.size_bytes)
                for name, v in observed.items()} != originals:
                raise RuntimeError("Copied Windows binary changed during assembly")
            report = {
                "schema_version": 1,
                "source_shas": {
                    "bootstrap_pr2": setup_sha,
                    "integration_pr11": runtime_sha,
                },
                "manifest_kind": "EXPLICITLY_FAKE_NONRELEASE_BUILD_FIXTURE",
                "artifacts": [
                    {"name": name, "sha256": info.sha256,
                     "size_bytes": info.size_bytes}
                    for name, info in sorted(observed.items())
                ],
                "proof": "TWO_FROZEN_EXES_IPC_AND_EMBEDDED_SETUP_SELFTEST",
                "release_ready": False,
                "windows_clean_install_certified": False,
                "acl_isolation_certified": False,
                "multi_agent_roadmap_certified": False,
            }
            (output_dir / "cross-pr-nonrelease-evidence.json").write_text(
                json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        except BaseException:
            shutil.rmtree(output_dir)
            raise
    _checkout(setup_root, setup_sha)
    _checkout(runtime_root, runtime_sha)
    print("NONRELEASE_THREE_FROZEN_BINARIES_CROSS_PR_PASS")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--setup-sha", required=True)
    parser.add_argument("--runtime-sha", required=True)
    args = parser.parse_args()
    assemble(
        setup_root=args.setup_root.resolve(),
        runtime_root=args.runtime_root.resolve(),
        output_dir=args.output_dir.resolve(strict=False),
        setup_sha=args.setup_sha, runtime_sha=args.runtime_sha,
    )


if __name__ == "__main__":
    main()
