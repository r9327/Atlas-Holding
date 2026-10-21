"""Opt-in Windows runner E2E: install pinned Ollama, run a real local API.

This intentionally mutates a disposable GitHub-hosted Windows runner. It is
never invoked by the end-user EXE, ordinary unittest collection, or normal
bootstrap validation. No model pull, GPU/quality claim, reboot, credentials,
global PATH edits by Atlas, or takeover of a prior Ollama daemon.
"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time

from .inventory import Component, _api_json, scan
from .ollama_service import _local_api_alive, start_local_ollama
from .version_evidence import matches_component_version
from .windows_package_manager import WingetPin, install_pinned_winget
from .winget_binary import discover_winget_binary
from .windows_ollama_environment import configure_user_models, undo_created_user_models


PIN = WingetPin("ollama", "Ollama.Ollama", "0.32.8")


def _runner_gate() -> None:
    if (platform.system() != "Windows"
            or os.environ.get("GITHUB_ACTIONS") != "true"
            or not os.environ.get("RUNNER_TEMP")
            or not Path(os.environ["RUNNER_TEMP"]).is_absolute()):
        raise RuntimeError("Disposable GitHub Windows runner required")


def verify_real_ollama() -> dict[str, str]:
    """Check actual WinGet installer + owned daemon; never touch outside API."""
    _runner_gate()
    with tempfile.TemporaryDirectory(
        prefix="atlas-ollama-e2e-", dir=os.environ["RUNNER_TEMP"],
    ) as temporary:
        root = Path(temporary)
        before = scan(root)
        if before.ollama.status != "NOT_FOUND" or _local_api_alive():
            raise RuntimeError("Pre-existing Ollama; refusing takeover")
        try:
            native = discover_winget_binary(PIN)
            if native.state == "EXACT_BINARY_VERIFIED":
                raise RuntimeError("Existing WinGet Ollama; refusing reinstall")
            selected_models = root / "models"
            config = configure_user_models(
                selected_models, authorized=True,
            )
            if config.status not in {
                "USER_MODELS_CREATED", "USER_MODELS_ALREADY_MATCH",
            }:
                raise RuntimeError(
                    "Ollama model volume cannot be configured: " +
                    config.status
                )
            installed = install_pinned_winget(
                PIN, authorized=True, online=True, max_seconds=420,
                model_root=selected_models,
            )
            if (installed.state != "INSTALLED_NOT_HEALTHCHECKED"
                    and config.status == "USER_MODELS_CREATED"):
                undo_created_user_models(selected_models)
            if installed.state != "INSTALLED_NOT_HEALTHCHECKED":
                raise RuntimeError(
                    "Pinned user Ollama installation not confirmed: " +
                    installed.state
                )
            discovered = discover_winget_binary(PIN)
            if discovered.state != "EXACT_BINARY_VERIFIED":
                raise RuntimeError("Installed Ollama binary absent or wrong version")
            # Inno Setup may auto-launch its own Ollama daemon as soon as
            # WinGet reports installation success. Never kill, reconfigure or
            # falsely claim its model disk. In either branch, its API must
            # still prove the pinned native version and a healthy tags route.
            installer_started_api = _local_api_alive()
            launch = None
            if not installer_started_api:
                candidate = replace(
                    before, ollama=Component(
                        "FOUND_BROKEN", path=discovered.executable,
                        version=discovered.version,
                        reason="Ollama installed but local API is not healthy",
                    ),
                )
                launch = start_local_ollama(
                    candidate, install_root=root, model_root=selected_models,
                    pins=(PIN,), authorized=True, startup_seconds=45,
                )
            try:
                # OllamaSetup may spawn a tray daemon after WinGet exits.
                # Its 11434 socket may become available moments *after* our
                # attempted server exits on an address-in-use race. Never
                # retry a second competing server; observe the API instead.
                if launch is not None and launch.state == "PROCESS_EXITED":
                    for _ in range(16):
                        if _local_api_alive():
                            installer_started_api = True
                            break
                        time.sleep(0.5)
                if (launch is not None
                        and launch.state not in {
                            "OWNED_API_HEALTHY",
                            "EXISTING_API_STORAGE_UNVERIFIED",
                        } and not (
                            launch.state == "PROCESS_EXITED"
                            and installer_started_api
                        )):
                    raise RuntimeError("Ollama start failed: " + launch.state)
                version = _api_json("/api/version").get("version", "")
                tags = _api_json("/api/tags").get("models")
                if (not matches_component_version("ollama", PIN.version, version)
                        or not isinstance(tags, list)):
                    raise RuntimeError("Ollama API does not match pinned version")
                owned = launch is not None and launch.owned
                return {
                    "state": (
                        "OLLAMA_WINDOWS_NATIVE_API_PASS"
                        if owned else
                        "OLLAMA_WINDOWS_AUTOSTART_API_PASS_STORAGE_UNVERIFIED"
                    ),
                    "winget_version": PIN.version,
                    "api_version": version,
                    "model_volume": (
                        "runner-temp-owned-process"
                        if owned else "NOT_ATTESTED"
                    ),
                    "model_downloads": "none",
                }
            finally:
                # Stop only the child created by this exact smoke test.
                if launch is not None and launch.owned and launch.process is not None:
                    child = launch.process
                    if child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait(timeout=5)

        finally:
            # The ephemeral GitHub VM is discarded after the job; never run
            # an uninstall that might remove a pre-existing unrelated app.
            pass


def main() -> int:
    print(json.dumps(verify_real_ollama(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
