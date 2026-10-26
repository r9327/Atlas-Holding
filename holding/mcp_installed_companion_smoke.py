"""Native Windows end-to-end MCP protocol using an official embeddable CPython.

The runner downloads a publisher-digest-pinned ZIP. This test does NOT create
a signed release or claim that the Setup wizard has passed human QA.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from .embedded_python_stage import (
    EmbeddedPythonRelease, stage_pinned_embedded_python,
    verify_staged_embedded_python,
)
from .mcp_hub_session import LocalMcpHubSession
from .mcp_local_inventory import PinnedLocalPlugin
from .mcp_package_pin import McpPackagePin


OFFICIAL_31316_X64_SHA256 = (
    "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297"
)


def run_native_pinned_hub(*, archive: Path) -> dict[str, object]:
    if os.name != "nt":
        raise RuntimeError("Native Windows Job Object MCP protocol required")
    root = Path(__file__).resolve().parent.parent
    if (not isinstance(archive, Path) or not archive.is_absolute()
            or not archive.is_file() or archive.is_symlink()):
        raise ValueError("Pinned official CPython archive required")
    source = root / "holding"
    if not source.is_dir() or source.is_symlink():
        raise ValueError("Trusted exact-checkout Holding module folder required")
    files = sorted(source.glob("*.py"))
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }
    release = EmbeddedPythonRelease(
        python_minor=13, zip_sha256=OFFICIAL_31316_X64_SHA256,
        module_sha256=hashes)
    with tempfile.TemporaryDirectory(prefix="atlas-full-mcp-native-") as temporary:
        install = Path(temporary).resolve() / "mcp-python"
        install.mkdir()
        staged = stage_pinned_embedded_python(
            archive=archive, approved_package_root=root,
            target_parent=install, pin=release)
        verified = verify_staged_embedded_python(
            directory=staged.directory, pin=release)
        if verified.interpreter_sha256 != staged.interpreter_sha256:
            raise RuntimeError("Native CPython bytes changed after staging")
        interpreter = PinnedLocalPlugin(
            "atlas-installed-companion", "3.13",
            staged.python_exe, staged.interpreter_sha256)
        pin = McpPackagePin.from_manifest(hashes)
        with LocalMcpHubSession(
                interpreter_pin=interpreter,
                package_root=staged.directory,
                package_pin=pin, response_timeout=8) as session:
            observed = session.observe()
            if observed.state != "UNVERIFIED":
                raise RuntimeError("Unprobed MCP hub was falsely responsive")
            first = session.discover()
            if (first.state != "RESPONSIVE"
                    or first.protocol_version is None
                    or first.pid != observed.pid
                    or first.external_plugins_tested):
                raise RuntimeError("Real installed MCP hub did not discover protocol")
            native = session._managed.observe()
            if (native.process_state != "RUNNING"
                    or native.pid != observed.pid
                    or native.job_memory_limit_bytes != 256 * 1024 * 1024
                    or native.active_process_limit != 4):
                raise RuntimeError("Real installed CPython is not bounded by Job Object")
            again = session.discover()
            if again.state != "RESPONSIVE" or again.pid != first.pid:
                raise RuntimeError("Installed MCP hub did not reuse its process")
            (staged.directory / "holding" / "mcp_stdio.py").write_bytes(
                b"# intentionally modified package import\n")
            changed = session.discover()
            if (changed.state != "BINARY_CHANGED"
                    or changed.protocol_version is not None
                    or session._process is not None):
                raise RuntimeError("Modified embedded MCP source was not terminated")
        return {
            "state": "NATIVE_INSTALLED_MCP_HUB_PROTOCOL_PASS",
            "official_archive_sha256": OFFICIAL_31316_X64_SHA256,
            "interpreter_sha256": staged.interpreter_sha256,
            "protocol_verified": True,
            "job_object_verified": True,
            "module_tamper_refused": True,
            "external_plugins_verified": False,
            "signed_setup_release_ready": False,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-native-installed-mcp")
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run_native_pinned_hub(archive=args.archive),
                     sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
