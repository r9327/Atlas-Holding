"""Native disposable Windows installation / agent / engine configuration smoke.

Actually copies three frozen executable bytes, applies real Windows ACLs,
enrolls a pinned local Ollama model, parses engine schema v3, and checks the
approved canonical Git remote. No shell generation, real model, QA or push.
"""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading

from holding.windows_engine_enrollment import protect_approved_install_root
from holding.windows_runtime_payload import (
    install_pinned_runtime_pair, install_pinned_adapter, verify_runtime_payload,
)
from holding.windows_setup import configure_and_launch_engine
from holding.windows_model_enrollment import discover_local_models
from holding.windows_runtime_acl import validate_runtime_acl
from holding.daemon_entry import Installation, BootstrapError
from holding.git_worktree import GitWorktrees


_MODEL = "atlas-native-test:Q4"
_DIGEST = "a" * 64
_FILES = ("AtlasHoldingControlCenter.exe", "AtlasHoldingEngine.exe")


class _ModelInventory(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path != "/api/tags":
            self.send_error(404)
            return
        body = json.dumps({
            "models": [{"name": _MODEL, "digest": _DIGEST}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def _git(*args: str) -> None:
    subprocess.run(
        ["git", *args], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=25, check=True, shell=False,
    )


def run(frozen_directory: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Real native Windows security checks required")
    if (not frozen_directory.is_absolute()
            or frozen_directory.resolve(strict=True) != frozen_directory):
        raise ValueError("Pinned Windows runtime must be canonical")
    expected = {name: _sha(frozen_directory / name) for name in _FILES}
    agent_source = frozen_directory / "AtlasHoldingAgent.exe"
    agent_pin = _sha(agent_source)
    verify_runtime_payload(frozen_directory, expected)
    with tempfile.TemporaryDirectory(prefix="atlas-native-enroll-") as temp:
        base = Path(temp).resolve()
        installed = base / "Disque sélectionné" / "Atlas Holding"
        installed.mkdir(parents=True)
        canonical = base / "Dof-Atlas"
        canonical.mkdir()
        (canonical / "README.md").write_text("Test checkout for installation\n")
        _git("init", "-b", "main", str(canonical))
        _git("-C", str(canonical), "config", "user.name", "Atlas CI")
        _git("-C", str(canonical), "config", "user.email", "atlas-ci@example.invalid")
        _git("-C", str(canonical), "remote", "add", "origin",
             "https://github.com/r9327/Dof-Atlas.git")
        _git("-C", str(canonical), "add", "README.md")
        _git("-C", str(canonical), "commit", "-m", "fixture: isolated native installer checkout")

        protect_approved_install_root(installed, approved=True)
        validate_runtime_acl(installed)
        runtime = install_pinned_runtime_pair(
            frozen_directory, installed, expected, approved=True)
        if runtime.ready:
            raise RuntimeError("Staged binaries falsely claimed to be ready")
        if install_pinned_adapter(
                agent_source, installed, agent_pin, approved=True,
        ) != "STAGED_PINNED_ADAPTER_HEALTH_PENDING":
            raise RuntimeError("Actual frozen agent did not stage")
        validate_runtime_acl(installed / "agents" / "AtlasHoldingAgent.exe")
        if install_pinned_adapter(
                agent_source, installed, agent_pin, approved=True,
        ) != "REUSED_PINNED_ADAPTER_HEALTH_PENDING":
            raise RuntimeError("Second install replaced an existing agent")

        # A loopback fake models endpoint proves protocol wiring only. It is
        # explicitly NOT evidence of local Ollama inference quality.
        server = ThreadingHTTPServer(("127.0.0.1", 11434), _ModelInventory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            if dict(discover_local_models()).get(_MODEL) != _DIGEST:
                raise RuntimeError("Local model digest inventory mismatch")
            status, pid = configure_and_launch_engine(
                install_root=installed, atlas_root=canonical,
                runtime_pins=expected, adapter_sha256=agent_pin,
                model_name=_MODEL, approved=True, launch_approved=False,
            )
            if status != "CONFIGURED_ENGINE_HEALTH_PENDING" or pid is not None:
                raise RuntimeError("Installer spawned an unapproved engine")
            config = installed / "config" / "engine.json"
            validate_runtime_acl(config)
            model = installed / "agents" / "AtlasHoldingModel.json"
            validate_runtime_acl(model)
            installed_engine = Installation.load(config)
            if (installed_engine.executable != installed / "agents" / "AtlasHoldingAgent.exe"
                    or installed_engine.arguments != (str(model),)
                    or installed_engine.argument_sha256[str(model)] != _sha(model)
                    or installed_engine.dashboard.program.executable != (
                        runtime.directory / "AtlasHoldingControlCenter.exe")
                    or installed_engine.github is not None):
                raise RuntimeError("Installed engine did not accept real pinned v3 contract")
            git = GitWorktrees(canonical, installed / "workspaces",
                               "r9327/Dof-Atlas")
            git.inspect_canonical()
            status2, pid2 = configure_and_launch_engine(
                install_root=installed, atlas_root=canonical,
                runtime_pins=expected, adapter_sha256=agent_pin,
                model_name=_MODEL, approved=True, launch_approved=False,
            )
            if status2 != "REUSED_CONFIG_HEALTH_PENDING" or pid2 is not None:
                raise RuntimeError("Installer rewrote an existing pinned enrollment")
            model.write_bytes(b'{"model":"changed-by-unknown-process"}')
            try:
                Installation.load(config)
            except BootstrapError:
                pass
            else:
                raise RuntimeError("Engine accepted tampered pinned model JSON")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    print("NONRELEASE_NATIVE_ACL_INSTALLED_ENGINE_CONFIG_PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-directory", required=True, type=Path)
    args = parser.parse_args()
    run(args.frozen_directory)


if __name__ == "__main__":
    main()
