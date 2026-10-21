"""Build a nonrelease Windows Control Center executable from the checked-out SHA.

This is deliberately distinct from AtlasHoldingSetup.exe. No GitHub Release,
installer enrollment, Windows ACL, signature, or user-machine E2E is asserted.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile


@dataclass(frozen=True)
class ViewerBuildEvidence:
    artifact: str
    sha256: str
    bytes: int
    release_ready: bool = False


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as inp:
        for chunk in iter(lambda: inp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _freeze_entry(
    *, output_dir: Path, root: Path, executable_name: str,
    entry_module: str, ui: bool, pass_args: bool = False,
) -> ViewerBuildEvidence:
    """One constrained Windows packager shared by GUI and headless engine."""
    if sys.platform != "win32":
        raise RuntimeError("Holding EXE must be built and exercised on native Windows")
    expected_files = (
        ("ollama_protocol.py",) if pass_args else (
            ("control_center_entry.py", "control_center_live.py") if ui else
            ("daemon_entry.py", "service.py", "state_store.py")
        )
    )
    if (not isinstance(root, Path) or not root.is_absolute()
            or root != root.resolve()
            or not isinstance(output_dir, Path) or not output_dir.is_absolute()
            or output_dir.is_symlink()
            or any(not (root / "holding" / f).is_file()
                   for f in expected_files)):
        raise ValueError("Verified Windows sources and absolute output required")
    destination = output_dir / (executable_name + ".exe")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Refusing to overwrite an existing runtime executable")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="atlas-runtime-build-") as temp:
        workspace = Path(temp)
        entry = workspace / "frozen_entry.py"
        entry.write_text(
            ("import sys\n" if pass_args else "") +
            f"from {entry_module} import main\n" +
            ("raise SystemExit(main(sys.argv[1:]))\n" if pass_args
             else "raise SystemExit(main())\n"),
            encoding="utf-8",
        )
        dist = workspace / "dist"
        command = [
            sys.executable, "-m", "PyInstaller",
            "--clean", "--noconfirm", "--onefile",
            "--windowed" if ui else "--console", "--noupx",
            "--name", executable_name,
            "--paths", str(root),
            "--distpath", str(dist),
            "--workpath", str(workspace / "build"),
            "--specpath", str(workspace),
            str(entry),
        ]
        subprocess.run(
            command, cwd=root, shell=False, check=True, timeout=900,
        )
        candidate = dist / (executable_name + ".exe")
        if (not candidate.is_file() or candidate.is_symlink()
                or candidate.stat().st_size < 1_000_000):
            raise RuntimeError("Frozen Windows runtime is missing or incomplete")
        # The destination may be on another disk; stage the complete binary
        # on that disk and refuse to overwrite a previously produced artifact.
        staged = output_dir / (".runtime-" + secrets.token_hex(16) + ".tmp")
        try:
            with candidate.open("rb") as inp, staged.open("xb") as out:
                shutil.copyfileobj(inp, out, length=1024 * 1024)
            if destination.exists() or destination.is_symlink():
                raise FileExistsError("Runtime output appeared during packaging")
            os.rename(staged, destination)
        finally:
            staged.unlink(missing_ok=True)
    return ViewerBuildEvidence(
        str(destination), _digest(destination), destination.stat().st_size,
    )


def build_windows_control_center(
    *, output_dir: Path, project_root: Path | None = None,
) -> ViewerBuildEvidence:
    """Package PySide6 Qt and strict enrollment CLI; never publish a release."""
    root = project_root if project_root is not None else Path(__file__).resolve().parent.parent
    return _freeze_entry(
        output_dir=output_dir, root=root,
        executable_name="AtlasHoldingControlCenter",
        entry_module="holding.control_center_entry", ui=True,
    )


def build_windows_engine(
    *, output_dir: Path, project_root: Path | None = None,
) -> ViewerBuildEvidence:
    """Package headless durable engine, independent of Qt/window life cycle."""
    root = project_root if project_root is not None else Path(__file__).resolve().parent.parent
    return _freeze_entry(
        output_dir=output_dir, root=root,
        executable_name="AtlasHoldingEngine",
        entry_module="holding.daemon_entry", ui=False,
    )

def build_windows_agent(
    *, output_dir: Path, project_root: Path | None = None,
) -> ViewerBuildEvidence:
    """Freeze the existing Ollama protocol-v1 adapter, without enrolling a model.

    The installed engine must pass a separately pinned absolute model config
    path as argv[1]. Building this EXE is not evidence of a healthy LLM.
    """
    root = project_root if project_root is not None else Path(__file__).resolve().parent.parent
    return _freeze_entry(
        output_dir=output_dir, root=root, executable_name="AtlasHoldingAgent",
        entry_module="holding.ollama_protocol", ui=False, pass_args=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-runtime-build")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--kind", choices=("viewer", "engine", "both", "agent", "all"), default="viewer")
    args = parser.parse_args(argv)
    observed = {}
    if args.kind in {"viewer", "both", "all"}:
        observed["control_center"] = asdict(
            build_windows_control_center(output_dir=args.output_dir))
    if args.kind in {"engine", "both", "all"}:
        observed["engine"] = asdict(
            build_windows_engine(output_dir=args.output_dir))
    if args.kind in {"agent", "all"}:
        observed["agent"] = asdict(
            build_windows_agent(output_dir=args.output_dir))
    print(json.dumps(observed, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
