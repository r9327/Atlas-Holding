"""Windows-only live MinGit verification: real official ZIP, no installer/UAC.

Called by the intentionally opt-in PR validation workflow, never at user
startup. The entire Git installation lives in a temporary directory. No
registry, global PATH, Windows service or machine installation is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .bootstrap_assets_cli import read_pinned_manifest
from .mingit_candidate import (
    MINGIT_SHA256, manifest_for_candidate, prepare_mingit_candidate,
)
from .managed_toolchain import resolve_managed_tool
from .probe_process import run_probe
from .staged_install import install_versioned_zip


def verify_live_mingit(archive: Path) -> dict[str, str]:
    pins = prepare_mingit_candidate(archive)
    manifest = manifest_for_candidate(pins)
    with tempfile.TemporaryDirectory(prefix="atlas-mingit-ci-") as dirname:
        root = Path(dirname)
        release = root / "manifest.json"
        release.write_bytes(manifest)
        reread = read_pinned_manifest(
            release, expected_sha256=hashlib.sha256(manifest).hexdigest(),
        )
        spec = reread[0]
        install_root = root / "installed"
        cache_root = root / "cache"
        cache_root.mkdir()
        cached = cache_root / (
            spec.component + "-" + spec.version + "-" + spec.sha256 + ".zip"
        )
        shutil.copyfile(archive, cached)
        outcome = install_versioned_zip(
            spec, install_root=install_root, cache_root=cache_root,
            authorized=True,
        )
        if outcome.status != "INSTALLED":
            raise RuntimeError("Expected an actual installed MinGit payload")
        tool = resolve_managed_tool(spec, install_root=install_root)
        if tool.state != "VERIFIED_EXECUTABLE":
            raise RuntimeError("Native MinGit version or payload invalid")
        environment = {
            key: value for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        }
        environment.update({
            "GIT_CONFIG_GLOBAL": "NUL",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": str(root),
        })
        repository = root / "git-worktree"
        repository.mkdir()
        commands = [
            [tool.executable, "init", "-q", str(repository)],
            [tool.executable, "-C", str(repository), "config", "user.name",
             "Atlas Holding CI"],
            [tool.executable, "-C", str(repository), "config", "user.email",
             "atlas-ci@invalid.example"],
        ]
        for cmd in commands:
            result = run_probe(cmd, environment=environment, timeout=15)
            if result.returncode:
                raise RuntimeError("Portable Git cannot initialize repository")
        (repository / "readme.txt").write_text(
            "Atlas Holding portable Git smoke\n", encoding="utf-8",
        )
        for command in (
            [tool.executable, "-C", str(repository), "add", "readme.txt"],
            [tool.executable, "-C", str(repository), "commit", "-qm",
             "Verify real MinGit offline"],
        ):
            result = run_probe(command, environment=environment, timeout=15)
            if result.returncode:
                raise RuntimeError("Portable Git cannot stage or commit")
        head = run_probe(
            [tool.executable, "-C", str(repository), "rev-parse", "HEAD"],
            environment=environment, timeout=10,
        )
        status = run_probe(
            [tool.executable, "-C", str(repository), "status", "--porcelain"],
            environment=environment, timeout=10,
        )
        if (head.returncode or len(head.stdout.strip()) != 40
                or status.returncode or status.stdout.strip()):
            raise RuntimeError("Portable Git repository self-test failed")
        return {
            "state": "PORTABLE_GIT_NATIVE_E2E_PASS",
            "archive_sha256": MINGIT_SHA256,
            "native_version": tool.expected_version,
            "package_version": spec.version,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-ci-mingit")
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(verify_live_mingit(args.archive), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
