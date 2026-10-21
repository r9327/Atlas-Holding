"""Windows live OpenCode portable smoke, pinned to official publisher bytes.

No providers or API keys, no login, no model request. A version/help probe is
a native executable smoke, *not* OpenCode agent tool-calling certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .bootstrap_assets_cli import read_pinned_manifest
from .managed_toolchain import resolve_managed_tool
from .portable_runtime import (
    OPENCODE_SHA256, OPENCODE_VERSION, prepare_portable_manifest,
)
from .probe_process import run_probe
from .staged_install import install_versioned_zip


def verify_live_opencode(git_archive: Path, code_archive: Path) -> dict[str, str]:
    data = prepare_portable_manifest(git_archive, code_archive)
    with tempfile.TemporaryDirectory(prefix="atlas-code-windows-") as temporary:
        root = Path(temporary)
        manifest = root / "candidate-manifest.json"
        manifest.write_bytes(data)
        pins = read_pinned_manifest(
            manifest, expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        target = next(p for p in pins if p.component == "opencode")
        cache = root / "cache"
        cache.mkdir()
        copied = cache / (
            target.component + "-" + target.version + "-" +
            target.sha256 + ".zip"
        )
        shutil.copyfile(code_archive, copied)
        installed = install_versioned_zip(
            target, install_root=root / "installed", cache_root=cache,
            authorized=True,
        )
        if installed.status != "INSTALLED":
            raise RuntimeError("OpenCode staging did not install a new version")
        program = resolve_managed_tool(
            target, install_root=root / "installed",
        )
        if program.state != "VERIFIED_EXECUTABLE":
            raise RuntimeError("OpenCode executable failed native version probe")
        environment = {
            k: v for k, v in os.environ.items()
            if k.upper() in {
                "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE",
                "LOCALAPPDATA", "APPDATA",
            }
        }
        environment.update({
            "NO_PROXY": "127.0.0.1,localhost",
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
            "GIT_TERMINAL_PROMPT": "0",
        })
        help_text = run_probe(
            [program.executable, "--help"],
            environment=environment, timeout=25, max_output_bytes=65536,
        )
        if (help_text.returncode != 0
                or "opencode" not in (
                    help_text.stdout + help_text.stderr
                ).lower()):
            raise RuntimeError("OpenCode CLI cannot display native usage")
        return {
            "state": "PORTABLE_OPENCODE_NATIVE_SMOKE_PASS",
            "archive_sha256": OPENCODE_SHA256,
            "native_version": OPENCODE_VERSION,
            "scope": "native_cli_no_provider_or_model_test",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-ci-opencode")
    parser.add_argument("--git-archive", type=Path, required=True)
    parser.add_argument("--opencode-archive", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(verify_live_opencode(
        args.git_archive, args.opencode_archive,
    ), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
