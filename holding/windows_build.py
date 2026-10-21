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
from typing import Mapping

from .windows_runtime_payload import verify_runtime_payload
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
    runtime_directory: Path | None = None,
    runtime_pins: Mapping[str, str] | None = None,
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
    if (runtime_directory is None) != (runtime_pins is None):
        raise ValueError("Runtime directory and SHA-256 pins must be supplied together")
    if runtime_directory is not None:
        verify_runtime_payload(runtime_directory, runtime_pins)
    root = project_root or Path(__file__).resolve().parent.parent
    if not root.is_absolute() or not (root / "holding" / "windows_setup.py").is_file():
        raise ValueError("Expected checked-out project root")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "AtlasHoldingSetup.exe"
    if path.exists():
        raise FileExistsError("Refusing to overwrite an existing setup executable")
    with tempfile.TemporaryDirectory(prefix="atlas-pyinstaller-") as temp:
        workspace = Path(temp).resolve()
        stage = workspace / "dist"
        # This digest comes from a trusted build input, not a self-reported
        # value inside the JSON assets manifest.
        pinned_manifest = workspace / "release-assets.json"
        shutil.copyfile(manifest, pinned_manifest)
        if _sha256(pinned_manifest) != expected_manifest_sha256.lower():
            raise ValueError("Release manifest changed while preparing Windows build")
        bundled_runtime = workspace / "runtime"
        if runtime_directory is not None:
            bundled_runtime.mkdir()
            # Source validation is repeated after the copy, before PyInstaller.
            for name in sorted(runtime_pins):
                original = runtime_directory / name
                copied = bundled_runtime / name
                with original.open("rb") as reader, copied.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, 1024 * 1024)
            verify_runtime_payload(bundled_runtime, runtime_pins)
            verify_runtime_payload(runtime_directory, runtime_pins)

        shim = workspace / "atlas_windows_entry.py"
        shim_lines = [
            "from pathlib import Path",
            "import hashlib, sys",
            "from holding.bootstrap_assets_cli import read_pinned_manifest",
            "from holding.windows_setup import main",
            "from holding.windows_runtime_payload import verify_runtime_payload",
            "from holding.ollama_resume import resume_ollama_at_logon",
            "base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)).resolve()",
            "manifest = base / 'release-assets.json'",
            f"expected = {expected_manifest_sha256.lower()!r}",
            f"runtime_pins = {dict(runtime_pins) if runtime_pins is not None else None!r}",
            "if runtime_pins is not None:",
            "    verify_runtime_payload(base / 'runtime', runtime_pins)",
            "if sys.argv[1:] == ['--self-test']:",
            "    read_pinned_manifest(manifest, expected_sha256=expected)",
            "    raise SystemExit(0)",
            "if len(sys.argv) == 3 and sys.argv[1] == '--resume-ollama':",
            "    stored_root = Path(sys.argv[2])",
            "    if not stored_root.is_absolute():",
            "        raise SystemExit(2)",
            "    try:",
            "        outcome = resume_ollama_at_logon(",
            "            manifest=manifest, manifest_sha256=expected,",
            "            install_root=stored_root)",
            "    except (OSError, ValueError):",
            "        raise SystemExit(2)",
            "    raise SystemExit(0 if outcome.state == 'OWNED_SERVICE_RESUMED' else 2)",
            "if len(sys.argv) > 1:",
            "    raise SystemExit(2)",
            "wizard_args = ['--manifest', str(manifest),",
            "               '--manifest-sha256', expected]",
            "if runtime_pins is not None:",
            "    wizard_args.extend([",
            "        '--runtime-root', str(base / 'runtime'),",
            "        '--runtime-viewer-sha256', runtime_pins['AtlasHoldingControlCenter.exe'],",
            "        '--runtime-engine-sha256', runtime_pins['AtlasHoldingEngine.exe'],",
            "    ])",
            "raise SystemExit(main(wizard_args))",
        ]
        shim.write_text("\n".join(shim_lines) + "\n", encoding="utf-8")
        # Do not invoke shells, arbitrary remote scripts or a runtime model.
        command = [
            sys.executable, "-m", "PyInstaller", "--onefile", "--windowed",
            "--noconfirm", "--clean", "--noupx",
            "--name", "AtlasHoldingSetup",
            "--paths", str(root),
            "--distpath", str(stage),
            "--workpath", str(workspace / "work"),
            "--specpath", str(workspace / "spec"),
            "--add-data", str(pinned_manifest) + ":.",
            str(shim),
        ]
        if runtime_directory is not None:
            # The script argument stays last, per PyInstaller CLI convention.
            for name in sorted(runtime_pins):
                command[-1:-1] = [
                    "--add-data",
                    str(bundled_runtime / name) + os.pathsep + "runtime",
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
    parser.add_argument("--runtime-directory", type=Path)
    parser.add_argument("--viewer-sha256")
    parser.add_argument("--engine-sha256")
    args = parser.parse_args(argv)
    choices = (args.runtime_directory, args.viewer_sha256, args.engine_sha256)
    if any(item is not None for item in choices) and not all(
            item is not None for item in choices):
        parser.error("Runtime directory and both pinned binary hashes are required")
    pins = None
    if args.runtime_directory is not None:
        pins = {
            "AtlasHoldingControlCenter.exe": args.viewer_sha256,
            "AtlasHoldingEngine.exe": args.engine_sha256,
        }
    result = build_windows_installer(
        manifest=args.manifest, expected_manifest_sha256=args.manifest_sha256,
        output_dir=args.output_dir, runtime_directory=args.runtime_directory,
        runtime_pins=pins,
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
