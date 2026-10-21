"""Build AtlasHoldingSetup.exe from trusted, SHA-pinned release inputs.

This command is intentionally for a Windows release runner, not an unattended
installer inside an AI coding task. It does not publish a GitHub Release.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .bootstrap_assets_cli import read_pinned_manifest

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class WindowsBuildResult:
    artifact: str
    sha256: str
    manifest_sha256: str
    bytes: int


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as reader:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def build_windows_installer(
    *, manifest: Path, expected_manifest_sha256: str, output_dir: Path,
    project_root: Path | None = None,
) -> WindowsBuildResult:
    """Bundle the pinned setup wizard; native E2E and signing are separate gates."""
    if sys.platform != "win32":
        raise RuntimeError("Setup.exe must be built and exercised on Windows")
    if (not isinstance(manifest, Path) or not manifest.is_absolute()
            or not isinstance(output_dir, Path) or not output_dir.is_absolute()
            or not isinstance(expected_manifest_sha256, str)
            or not _HEX64.fullmatch(expected_manifest_sha256)):
        raise ValueError("Build requires absolute paths and release manifest SHA256")
    if manifest.is_symlink():
        raise ValueError("Refusing symlinked release manifest")
    read_pinned_manifest(manifest, expected_sha256=expected_manifest_sha256)
    root = project_root or Path(__file__).resolve().parent.parent
    if not root.is_absolute() or not (root / "holding" / "windows_setup.py").is_file():
        raise ValueError("Expected checked-out project root")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "AtlasHoldingSetup.exe"
    if path.exists():
        raise FileExistsError("Refusing to overwrite an existing setup executable")
    with tempfile.TemporaryDirectory(prefix="atlas-pyinstaller-") as temp:
        workspace = Path(temp)
        stage = workspace / "dist"
        # This digest comes from a trusted build input, not a self-reported
        # value inside the JSON assets manifest.
        pinned_manifest = workspace / "release-assets.json"
        shutil.copyfile(manifest, pinned_manifest)
        if _sha256(pinned_manifest) != expected_manifest_sha256.lower():
            raise ValueError("Release manifest changed while preparing Windows build")
        shim = workspace / "atlas_windows_entry.py"
        shim.write_text(
            "from pathlib import Path\\n"
            "import sys\\n"
            "from holding.windows_setup import main\\n"
            "base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))\\n"
            "raise SystemExit(main(["
            "'--manifest', str(base / 'release-assets.json'), "
            "'--manifest-sha256', " + repr(expected_manifest_sha256.lower()) +
            "]))\\n",
            encoding="utf-8",
        )
        # Do not invoke shells, arbitrary remote scripts or a runtime model.
        command = [
            sys.executable, "-m", "PyInstaller", "--onefile", "--windowed",
            "--noconfirm", "--clean", "--noupx",
            "--name", "AtlasHoldingSetup",
            "--paths", str(root),
            "--distpath", str(stage),
            "--workpath", str(workspace / "work"),
            "--specpath", str(workspace / "spec"),
            "--add-data", str(pinned_manifest) + os.pathsep + ".",
            str(shim),
        ]
        subprocess.run(command, cwd=root, check=True, timeout=900,
                       shell=False)
        built = stage / "AtlasHoldingSetup.exe"
        if not built.is_file() or built.stat().st_size < 1024:
            raise RuntimeError("PyInstaller did not produce a valid setup artifact")
        digest = _sha256(built)
        # The final destination is created only after a complete build.
        try:
            with built.open("rb") as reader, path.open("xb") as writer:
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            if _sha256(path) != digest:
                raise RuntimeError("Published executable differs from built bytes")
        except BaseException:
            if path.exists() and not path.is_symlink():
                path.unlink()
            raise
    return WindowsBuildResult(str(path), digest,
                              expected_manifest_sha256.lower(), path.stat().st_size)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-build-setup")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = build_windows_installer(
        manifest=args.manifest, expected_manifest_sha256=args.manifest_sha256,
        output_dir=args.output_dir,
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
