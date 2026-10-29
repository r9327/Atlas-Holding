"""Exact-main Windows assembly and installed-runtime smoke, never a release.

Run only under a Windows runner with a trusted checkout of this PR and an exact
certified-main runtime checkout.  The manifest is built from publisher bytes.
The smoke exercises native ACL application and
installed engine enrollment, but does not infer clean-machine, production
model, autonomous-roadmap or release readiness.
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


def _checkout(root: Path, expected: str, *, ignore_upstream: bool = False) -> None:
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
    status_args = ["git", "-C", str(root), "status", "--porcelain",
                   "--untracked-files=normal"]
    if ignore_upstream:
        status_args.extend(["--", ".", ":!upstream"])
    dirty = subprocess.check_output(
        status_args, text=True, timeout=15,
    ).strip()
    if dirty:
        raise RuntimeError("Cross-PR integration source tree is dirty")


def assemble(
    *, setup_root: Path, runtime_root: Path, output_dir: Path,
    manifest: Path, candidate_sha: str, setup_sha: str, runtime_sha: str,
) -> dict:
    if os.name != "nt":
        raise RuntimeError("Actual three-binary assembly requires native Windows")
    candidate_root = Path(__file__).resolve().parent.parent
    _checkout(setup_root, setup_sha,
              ignore_upstream=(setup_root == candidate_root))
    _checkout(runtime_root, runtime_sha)
    actual_candidate = subprocess.check_output(
        ["git", "-C", str(candidate_root), "rev-parse", "HEAD"],
        text=True, timeout=15,
    ).strip()
    if (actual_candidate != candidate_sha or len(candidate_sha) != 40
            or any(ch not in "0123456789abcdef" for ch in candidate_sha)):
        raise RuntimeError("Release candidate source SHA changed")
    if (not manifest.is_absolute() or manifest.is_symlink()
            or not manifest.is_file()
            or manifest.resolve(strict=True) != manifest
            or not 1 <= manifest.stat().st_size <= 1024 * 1024):
        raise ValueError("Exact official runtime manifest required")
    if (not output_dir.is_absolute() or output_dir.exists()
            or output_dir.is_symlink()
            or output_dir.parent.resolve(strict=True) != output_dir.parent):
        raise ValueError("New absolute nonrelease output directory required")
    with tempfile.TemporaryDirectory(prefix="atlas-cross-pr-build-") as workspace:
        temp = Path(workspace).resolve()
        viewer_dir = temp / "frozen"
        setup_dir = temp / "installer"
        # Release-specific fixes to the GUI AND durable engine must be in
        # the shipped bytes. Freeze BOTH from the exact current PR candidate;
        # the independent pinned-main tree still supplies the agent and
        # acceptance harness, and remains an integration cross-check.
        for kind in ("agent", "acceptance"):
            _run([
                sys.executable, "-B", "-m",
                "holding.windows_control_center_build",
                "--kind", kind, "--output-dir", str(viewer_dir),
            ], cwd=runtime_root)
        _run([
            sys.executable, "-B", "-m",
            "holding.windows_control_center_build",
            "--kind", "both", "--output-dir", str(viewer_dir),
        ], cwd=setup_root)
        engine = viewer_dir / "AtlasHoldingEngine.exe"
        viewer = viewer_dir / "AtlasHoldingControlCenter.exe"
        agent = viewer_dir / "AtlasHoldingAgent.exe"
        for executable in (engine, viewer, agent):
            if not executable.is_file() or executable.stat().st_size < 1_000_000:
                raise RuntimeError("Real frozen runtime missing")
        _run([str(engine), "--self-test"], cwd=runtime_root, timeout=90)
        _run([str(viewer), "--self-test"], cwd=runtime_root, timeout=90)
        # The packaged Control Center must also contain the R&D chat UI.
        # Smoke the isolated mode from the TRUE frozen EXE; no model, SQLite
        # authority, IPC capabilities or Python interpreter are provisioned.
        _run([str(viewer), "--project-chat-self-test"],
             cwd=runtime_root, timeout=90)
        _run([
            sys.executable, "-B",
            str(Path(__file__).with_name("native_windows_runtime_smoke.py")),
            "--viewer", str(viewer), "--engine", str(engine),
        ], cwd=runtime_root, timeout=140)
        _run([
            sys.executable, "-B",
            str(runtime_root / "tests" / "native_windows_agent_smoke.py"),
            "--agent", str(agent),
        ], cwd=runtime_root, timeout=140)
        _run([
            sys.executable, "-B",
            str(runtime_root / "tests" /
                "native_windows_installer_enrollment_smoke.py"),
            "--frozen-directory", str(viewer_dir),
        ], cwd=runtime_root, timeout=240)
        multi_lot_receipt = temp / "native-three-lot-roadmap.json"
        _run([
            sys.executable, "-B",
            str(Path(__file__).with_name(
                "native_windows_multi_lot_roadmap_smoke.py")),
            "--agent", str(agent),
            "--receipt-json", str(multi_lot_receipt),
        ], cwd=candidate_root, timeout=300)
        if not multi_lot_receipt.is_file():
            raise RuntimeError("Native multi-lot roadmap receipt is missing")
        pins = {
            "AtlasHoldingControlCenter.exe": _sha(viewer),
            "AtlasHoldingEngine.exe": _sha(engine),
        }
        _run([
            sys.executable, "-B", "-m", "holding.windows_build",
            "--manifest", str(manifest),
            "--manifest-sha256", _sha(manifest),
            "--output-dir", str(setup_dir),
            "--runtime-directory", str(viewer_dir),
            "--viewer-sha256", pins["AtlasHoldingControlCenter.exe"],
            "--engine-sha256", pins["AtlasHoldingEngine.exe"],
            "--adapter-executable", str(agent),
            "--adapter-sha256", _sha(agent),
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
            with multi_lot_receipt.open("rb") as src, (
                    output_dir / multi_lot_receipt.name).open("xb") as dst:
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
                    "release_candidate": candidate_sha,
                    "release_candidate_setup": setup_sha,
                    "release_candidate_viewer": setup_sha,
                    "release_candidate_engine": setup_sha,
                    "certified_main_agent": runtime_sha,
                },
                "manifest_kind": (
                    "OFFICIAL_PINNED_PORTABLE_AND_WINGET_CANDIDATE"
                ),
                "manifest_sha256": _sha(manifest),
                "artifacts": [
                    {"name": name, "sha256": info.sha256,
                     "size_bytes": info.size_bytes}
                    for name, info in sorted(observed.items())
                ],
                "proof": (
                    "THREE_FROZEN_EXES_IPC_ACL_ENROLLMENT_"
                    "AND_EMBEDDED_SETUP_SELFTEST"
                ),
                "native_acl_enrollment_smoke": "PASS",
                "installed_agent_health_smoke": "PASS",
                "local_three_lot_signed_qa_smoke": "PASS",
                "local_three_lot_receipt_sha256": _sha(
                    output_dir / multi_lot_receipt.name),
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
    _checkout(setup_root, setup_sha,
              ignore_upstream=(setup_root == candidate_root))
    _checkout(runtime_root, runtime_sha)
    print("NONRELEASE_THREE_FROZEN_BINARIES_CROSS_PR_PASS")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--setup-sha", required=True)
    parser.add_argument("--runtime-sha", required=True)
    args = parser.parse_args()
    assemble(
        setup_root=args.setup_root.resolve(),
        runtime_root=args.runtime_root.resolve(),
        output_dir=args.output_dir.resolve(strict=False),
        manifest=args.manifest.resolve(strict=True),
        candidate_sha=args.candidate_sha,
        setup_sha=args.setup_sha, runtime_sha=args.runtime_sha,
    )


if __name__ == "__main__":
    main()
