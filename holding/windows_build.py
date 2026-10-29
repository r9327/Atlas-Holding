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

from .windows_runtime_payload import verify_runtime_payload, verify_pinned_adapter
from .bootstrap_assets_cli import read_pinned_manifest
from .embedded_python_stage import EmbeddedPythonRelease, stage_pinned_embedded_python

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
    adapter_source: Path | None = None,
    adapter_sha256: str | None = None,
    embedded_python_archive: Path | None = None,
    embedded_python_pin: EmbeddedPythonRelease | None = None,
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
    if (adapter_source is None) != (adapter_sha256 is None):
        raise ValueError("Complete packaged adapter and pinned hash required")
    if adapter_source is not None and runtime_directory is None:
        raise ValueError("Agent bundle needs both engine and Control Center")
    if (embedded_python_archive is None) != (embedded_python_pin is None):
        raise ValueError("Bundled embedded Python ZIP requires complete trusted release pins")
    if embedded_python_archive is not None and runtime_directory is None:
        raise ValueError("Embedded MCP Python requires an engine/Control Center bundle")
    if adapter_source is not None:
        verify_pinned_adapter(adapter_source, adapter_sha256)
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

        bundled_adapter = workspace / "agents" / "AtlasHoldingAgent.exe"
        if adapter_source is not None:
            bundled_adapter.parent.mkdir()
            with adapter_source.open("rb") as reader, bundled_adapter.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
            verify_pinned_adapter(adapter_source, adapter_sha256)
            verify_pinned_adapter(bundled_adapter, adapter_sha256)

        embedded_zip = None
        embedded_sources = None
        embedded_pins_file = None
        embedded_pins_hash = None
        if embedded_python_pin is not None:
            # Verify ZIP/native members plus exact code from an independently
            # pinned release before the packaging tool receives any bytes.
            validated = stage_pinned_embedded_python(
                archive=embedded_python_archive, approved_package_root=root,
                target_parent=workspace, pin=embedded_python_pin)
            embedded_sources = validated.directory / "holding"
            embedded_zip = workspace / "mcp-embedded-python.zip"
            with embedded_python_archive.open("rb") as reader, embedded_zip.open("xb") as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
            if _sha256(embedded_zip) != embedded_python_pin.zip_sha256:
                raise ValueError("Bundled embedded CPython differs from trusted release")
            embedded_pins_file = workspace / "mcp-source-pins.json"
            embedded_pins_file.write_text(
                json.dumps(dict(sorted(embedded_python_pin.module_sha256.items())),
                           sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8")
            embedded_pins_hash = _sha256(embedded_pins_file)

        shim = workspace / "atlas_windows_entry.py"
        shim_lines = [
            "from pathlib import Path",
            "import hashlib, sys",
            "from holding.bootstrap_assets_cli import read_pinned_manifest",
            "from holding.windows_setup import main",
            "from holding.windows_runtime_payload import verify_runtime_payload, verify_pinned_adapter",
            "from holding.ollama_resume import resume_ollama_at_logon",
            "base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)).resolve()",
            "manifest = base / 'release-assets.json'",
            f"expected = {expected_manifest_sha256.lower()!r}",
            f"runtime_pins = {dict(runtime_pins) if runtime_pins is not None else None!r}",
            f"adapter_pin = {adapter_sha256!r}",
            f"mcp_zip_pin = {embedded_python_pin.zip_sha256 if embedded_python_pin else None!r}",
            f"mcp_minor = {embedded_python_pin.python_minor if embedded_python_pin else None!r}",
            f"mcp_source_pin = {embedded_pins_hash!r}",
            "if adapter_pin is not None:",
            "    verify_pinned_adapter(base / 'agents' / 'AtlasHoldingAgent.exe', adapter_pin)",
            "if runtime_pins is not None:",
            "    verify_runtime_payload(base / 'runtime', runtime_pins)",
            "if mcp_zip_pin is not None:",
            "    zip_file = base / 'mcp-embedded-python.zip'",
            "    mcp_pins_file = base / 'mcp-source-pins.json'",
            "    if hashlib.sha256(zip_file.read_bytes()).hexdigest() != mcp_zip_pin:",
            "        raise SystemExit(2)",
            "    if hashlib.sha256(mcp_pins_file.read_bytes()).hexdigest() != mcp_source_pin:",
            "        raise SystemExit(2)",
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
            "forwarded = []",
            "if len(sys.argv) > 1:",
            "    if (len(sys.argv) != 10 or sys.argv[1] != '--noninteractive-acceptance'",
            "            or sys.argv[2] != '--install-root' or sys.argv[4] != '--cache-root'",
            "            or sys.argv[6] != '--models-root' or sys.argv[8] != '--receipt-json'):",
            "        raise SystemExit(2)",
            "    forwarded = sys.argv[1:]",
            "wizard_args = ['--manifest', str(manifest),",
            "               '--manifest-sha256', expected]",
            "if runtime_pins is not None:",
            "    wizard_args.extend([",
            "        '--runtime-root', str(base / 'runtime'),",
            "        '--runtime-viewer-sha256', runtime_pins['AtlasHoldingControlCenter.exe'],",
            "        '--runtime-engine-sha256', runtime_pins['AtlasHoldingEngine.exe'],",
            "    ])",
            "if adapter_pin is not None:",
            "    wizard_args.extend([",
            "        '--adapter-root', str(base / 'agents'),",
            "        '--adapter-sha256', adapter_pin,",
            "    ])",
            "if mcp_zip_pin is not None:",
            "    wizard_args.extend([",
            "        '--mcp-archive', str(base / 'mcp-embedded-python.zip'),",
            "        '--mcp-archive-sha256', mcp_zip_pin,",
            "        '--mcp-source-root', str(base / 'mcp-source'),",
            "        '--mcp-source-pins', str(base / 'mcp-source-pins.json'),",
            "        '--mcp-source-pins-sha256', mcp_source_pin,",
            "        '--mcp-python-minor', str(mcp_minor),",
            "    ])",
            "wizard_args.extend(forwarded)",
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
        if adapter_source is not None:
            command[-1:-1] = [
                "--add-data",
                str(bundled_adapter) + os.pathsep + "agents",
            ]
        if embedded_zip is not None:
            for name, source, dest in (
                ("embedded", embedded_zip, "."),
                ("pins", embedded_pins_file, "."),
                ("sources", embedded_sources, "mcp-source/holding"),
            ):
                command[-1:-1] = [
                    "--add-data", str(source) + os.pathsep + dest,
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
    parser.add_argument("--adapter-executable", type=Path)
    parser.add_argument("--adapter-sha256")
    parser.add_argument("--mcp-archive", type=Path)
    parser.add_argument("--mcp-archive-sha256")
    parser.add_argument("--mcp-module-pins", type=Path)
    parser.add_argument("--mcp-module-pins-sha256")
    parser.add_argument("--mcp-python-minor", type=int)
    args = parser.parse_args(argv)
    if (args.adapter_executable is None) != (args.adapter_sha256 is None):
        parser.error("Full adapter filename and SHA-256 required")
    if args.adapter_executable is not None and args.runtime_directory is None:
        parser.error("Adapter needs bundled Control Center and engine")
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
    mcp_fields = (
        args.mcp_archive, args.mcp_archive_sha256,
        args.mcp_module_pins, args.mcp_module_pins_sha256,
        args.mcp_python_minor,
    )
    mcp_release = None
    if any(item is not None for item in mcp_fields):
        if not all(item is not None for item in mcp_fields):
            parser.error("Complete embedded MCP archive, module pins and SHA-256 required")
        if (args.runtime_directory is None
                or not args.mcp_archive.is_absolute()
                or not args.mcp_module_pins.is_absolute()
                or not isinstance(args.mcp_archive_sha256, str)
                or not isinstance(args.mcp_module_pins_sha256, str)
                or not _HEX64.fullmatch(args.mcp_archive_sha256)
                or not _HEX64.fullmatch(args.mcp_module_pins_sha256)
                or args.mcp_archive.is_symlink()
                or args.mcp_module_pins.is_symlink()):
            parser.error("Untrusted embedded MCP release inputs")
        module_bytes = args.mcp_module_pins.read_bytes()
        if (not 1 <= len(module_bytes) <= 32768
                or _sha256(args.mcp_module_pins) != args.mcp_module_pins_sha256):
            parser.error("Embedded MCP module inventory is not release-pinned")
        def unique(pairs):
            record = {}
            for key, value in pairs:
                if key in record:
                    raise ValueError("Duplicated release module identity")
                record[key] = value
            return record
        try:
            module_hashes = json.loads(module_bytes, object_pairs_hook=unique)
            mcp_release = EmbeddedPythonRelease(
                python_minor=args.mcp_python_minor,
                zip_sha256=args.mcp_archive_sha256,
                module_sha256=module_hashes,
            )
        except (ValueError, UnicodeError):
            parser.error("Invalid pinned embedded Python package inventory")
    result = build_windows_installer(
        manifest=args.manifest, expected_manifest_sha256=args.manifest_sha256,
        output_dir=args.output_dir, runtime_directory=args.runtime_directory,
        runtime_pins=pins,
        adapter_source=args.adapter_executable,
        adapter_sha256=args.adapter_sha256,
        embedded_python_archive=args.mcp_archive,
        embedded_python_pin=mcp_release,
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
