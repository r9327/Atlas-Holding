"""Windows-only real CPython embeddable executable smoke (not a release gate).

A CI build downloads the official Python.org 3.13.16 embeddable ZIP with the
publisher's SHA256 published on the Python release page. It stages a synthetic
Holding package, launches the actual python.exe under isolated flags, and
verifies module imports and site isolation. It does NOT test a real MCP server
or a final released installer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .embedded_python_stage import (
    EmbeddedPythonRelease, stage_pinned_embedded_python,
    verify_staged_embedded_python,
)


OFFICIAL_PYTHON_31316_ZIP_SHA256 = (
    "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297"
)
_REQUIRED = (
    "__init__.py", "mcp_entry.py", "mcp_stdio.py",
    "mcp_hub_session.py", "mcp_hub_probe.py", "mcp_windows_job.py",
    "mcp_local_inventory.py",
)


def smoke_official_embedded_python(*, archive: Path) -> dict[str, object]:
    if sys.platform != "win32":
        raise RuntimeError("Native Windows CPython companion smoke required")
    if (not isinstance(archive, Path) or not archive.is_absolute()
            or archive.is_symlink() or not archive.is_file()):
        raise ValueError("Pinned official CPython ZIP required")
    with tempfile.TemporaryDirectory(prefix="atlas-cpython-native-") as temp:
        base = Path(temp).resolve()
        checkout = base / "checkout"
        package = checkout / "holding"
        package.mkdir(parents=True)
        files = {}
        for name in _REQUIRED:
            value = (
                "from __future__ import annotations\n"
                + ("" if name != "mcp_entry.py"
                   else "PROBE_ID='atlas-holding-python-embedded'\n")
            ).encode("utf-8")
            (package / name).write_bytes(value)
            files[name] = hashlib.sha256(value).hexdigest()
        dest = base / "installation"
        dest.mkdir()
        release = EmbeddedPythonRelease(
            python_minor=13, zip_sha256=OFFICIAL_PYTHON_31316_ZIP_SHA256,
            module_sha256=files,
        )
        staged = stage_pinned_embedded_python(
            archive=archive, approved_package_root=checkout,
            target_parent=dest, pin=release)
        proven = verify_staged_embedded_python(
            directory=staged.directory, pin=release)
        if proven.interpreter_sha256 != staged.interpreter_sha256:
            raise RuntimeError("Staged native runtime changed before execution")
        # Importing an actual module from the strict ". / python313.zip"
        # path demonstrates that Python was genuinely started from the
        # separately staged interpreter, not sys.executable of the runner.
        script = (
            "import json,sys,site\n"
            "from holding.mcp_entry import PROBE_ID\n"
            "print(json.dumps({'id':PROBE_ID,'exe':sys.executable,"
            "'site':bool(sys.flags.no_site),'prefix':sys.prefix,"
            "'isolated':bool(sys.flags.isolated)}))\n"
        )
        # "site" is intentionally not imported: -S prohibits its automatic
        # import, but importing it explicitly would not prove isolation.
        script = script.replace("import json,sys,site", "import json,sys")
        env = {
            "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
            "PATH": os.environ.get("SystemRoot", r"C:\Windows") + r"\System32",
            "PYTHONPATH": str(base / "injection"),
            "PYTHONHOME": str(base / "injection"),
        }
        completed = subprocess.run(
            [str(staged.python_exe), "-E", "-S", "-B", "-c", script],
            cwd=staged.directory, shell=False, env=env,
            capture_output=True, timeout=15, check=True, text=True,
        )
        record = json.loads(completed.stdout)
        if (record.get("id") != "atlas-holding-python-embedded"
                or not record.get("site") or not record.get("isolated")
                or Path(record.get("exe", "")).resolve() != staged.python_exe.resolve()
                or staged.status != "STAGED_NOT_EXECUTED"):
            raise RuntimeError("Native embedded Python protocol preflight failed")
        return {
            "state": "NATIVE_PYTHON_EXECUTABLE_VERIFIED",
            "zip_sha256": release.zip_sha256,
            "python_exe_sha256": staged.interpreter_sha256,
            "mcp_protocol_verified": False,
            "installer_release_ready": False,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-embedded-cpython-smoke")
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(smoke_official_embedded_python(archive=args.archive),
                     sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
