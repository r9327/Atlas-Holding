"""Repeat the initial Ollama service launch after Windows user logon.

Resume requires the authentic bundled release pins and the saved, previously
consented model directory. It never installs packages, downloads models,
migrates folders or takes over somebody else's running Ollama daemon.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .bootstrap_assets_cli import read_pinned_manifest
from .inventory import scan
from .ollama_service import start_local_ollama
from .ollama_storage import read_model_directory
from .staged_install import PinnedZip
from .post_ollama_inventory import scan_ollama_with_pins
from .windows_ollama_environment import inspect_user_models


@dataclass(frozen=True)
class ResumeResult:
    state: str
    model_directory: str = ""


def resume_ollama_at_logon(
    *, manifest: Path, manifest_sha256: str, install_root: Path,
) -> ResumeResult:
    """Use saved consent only, with no new writes on failure paths."""
    if not isinstance(install_root, Path) or not install_root.is_absolute():
        raise ValueError("Absolute installation root required")
    # Manifest digest comes from the frozen EXE, not the user-editable
    # install root. An unsupported or unsigned pin cannot be substituted.
    pins = read_pinned_manifest(manifest, expected_sha256=manifest_sha256)
    record = read_model_directory(install_root)
    if record.status != "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED":
        return ResumeResult(record.status)
    assert record.directory is not None
    if record.source == "consented_user_ollama_environment":
        registered = inspect_user_models(record.directory)
        if registered.status != "USER_MODELS_MATCH":
            # A formerly consented user setting may have been removed,
            # changed externally or become unavailable on this login.
            # Never start our own daemon with now-stale storage consent.
            return ResumeResult(
                "USER_MODEL_ENVIRONMENT_" + registered.status,
                str(record.directory),
            )
    inventory = scan(install_root)
    if inventory.os_name != "Windows":
        return ResumeResult("WINDOWS_REQUIRED", str(record.directory))
    if inventory.ollama.status == "FOUND_VALID":
        # Cannot attest the existing daemon's storage volume from /api.
        return ResumeResult("EXISTING_SERVER_STORAGE_NOT_ATTESTED",
                            str(record.directory))
    valid_pins = pins
    outcome = start_local_ollama(
        inventory, install_root=install_root, model_root=record.directory,
        pins=valid_pins, authorized=True,
    )
    if outcome.state != "OWNED_API_HEALTHY":
        return ResumeResult("RESUME_" + outcome.state, str(record.directory))
    # Re-probe; a successful spawn alone is not a healthy installed service.
    if scan_ollama_with_pins(install_root, valid_pins).ollama.status != "FOUND_VALID":
        return ResumeResult("POST_RESUME_API_UNHEALTHY", str(record.directory))
    return ResumeResult("OWNED_SERVICE_RESUMED", str(record.directory))
