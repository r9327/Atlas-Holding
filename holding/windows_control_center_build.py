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


def build_windows_control_center(
    *, output_dir: Path, project_root: Path | None = None,
) -> ViewerBuildEvidence:
    """Package PySide6 Qt and strict enrollment CLI; never publish a release."""
    if sys.platform != "win32":
        raise RuntimeError("Viewer EXE is built and smoke tested on native Windows")
    root = project_root if project_root is not None else Path(__file__).resolve().parent.parent
    if (not isinstance(root, Path) or not root.is_absolute()
            or root != root.resolve()
            or not isinstance(output_dir, Path) or not output_dir.is_absolute()
            or output_dir.is_symlink()
            or not (root / "holding" / "control_center_entry.py").is_file()
            or not (root / "holding" / "control_center_live.py").is_file()):
        raise ValueError("Verified Windows viewer sources and absolute output required")
    destination = output_dir / "AtlasHoldingControlCenter.exe"
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Refusing to overwrite an existing viewer binary")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="atlas-viewer-build-") as temp:
        workspace = Path(temp)
        entry = workspace / "viewer_entry.py"
        entry.write_text(
            "from holding.control_center_entry import main\n"
            "raise SystemExit(main())\n", encoding="utf-8",
        )
        dist = workspace / "dist"
        command = [
            sys.executable, "-m", "PyInstaller",
            "--clean", "--noconfirm", "--onefile", "--windowed", "--noupx",
            "--name", "AtlasHoldingControlCenter",
            "--paths", str(root),
            "--distpath", str(dist),
            "--workpath", str(workspace / "build"),
            "--specpath", str(workspace),
            str(entry),
        ]
        subprocess.run(
            command, cwd=root, shell=False, check=True, timeout=900,
        )
        candidate = dist / "AtlasHoldingControlCenter.exe"
        if (not candidate.is_file() or candidate.is_symlink()
                or candidate.stat().st_size < 1_000_000):
            raise RuntimeError("Windows viewer binary is missing or incomplete")
        # Build and output can be on different Windows volumes. Copy to a
        # random temporary file on the chosen destination volume, then publish
        # atomically without replacing an existing artifact.
        staged = output_dir / (".viewer-" + secrets.token_hex(16) + ".tmp")
        try:
            with candidate.open("rb") as inp, staged.open("xb") as out:
                shutil.copyfileobj(inp, out, length=1024 * 1024)
            if destination.exists() or destination.is_symlink():
                raise FileExistsError("Viewer output appeared during packaging")
            os.rename(staged, destination)
        finally:
            staged.unlink(missing_ok=True)
    return ViewerBuildEvidence(
        str(destination), _digest(destination), destination.stat().st_size,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-viewer-build")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    evidence = build_windows_control_center(output_dir=args.output_dir)
    print(json.dumps(asdict(evidence), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
