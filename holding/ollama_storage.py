"""Persist a user-approved Ollama model volume, not a service-ready claim.

This config never modifies Windows machine/user environment variables and
cannot prove a separately started Ollama daemon uses this directory. Each
future launch must re-check the model root and service ownership.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

from .staged_install import _no_linked_ancestors


_NAME = "ollama-model-storage.json"
_SOURCES = frozenset({
    "consented_owned_ollama_service",
    "consented_user_ollama_environment",
})


@dataclass(frozen=True)
class StoredModelDirectory:
    status: str
    directory: Path | None = None
    source: str = ""


def read_model_directory(install_root: Path) -> StoredModelDirectory:
    if not isinstance(install_root, Path) or not install_root.is_absolute():
        raise ValueError("Absolute installation directory required")
    config = install_root / "settings" / _NAME
    try:
        _no_linked_ancestors(config)
        if not config.exists():
            return StoredModelDirectory("NOT_CONFIGURED")
        if not config.is_file() or config.stat().st_size > 4096:
            return StoredModelDirectory("BLOCKED_INVALID")
        raw = json.loads(config.read_text(encoding="utf-8"))
        if (not isinstance(raw, dict)
                or set(raw) != {"schema", "directory", "source"}
                or type(raw["schema"]) is not int or raw["schema"] != 1
                or raw["source"] not in _SOURCES
                or not isinstance(raw["directory"], str)
                or not 0 < len(raw["directory"]) <= 1024):
            return StoredModelDirectory("BLOCKED_INVALID")
        path = Path(raw["directory"])
        if not path.is_absolute():
            return StoredModelDirectory("BLOCKED_INVALID")
        _no_linked_ancestors(path)
        if not path.is_dir():
            return StoredModelDirectory("MISSING_MODEL_VOLUME", path)
        return StoredModelDirectory(
            "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED", path, raw["source"],
        )
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return StoredModelDirectory("BLOCKED_INVALID")


def store_model_directory(
    install_root: Path, model_root: Path, *, authorized: bool,
    source: str = "consented_owned_ollama_service",
) -> StoredModelDirectory:
    if (not isinstance(install_root, Path) or not install_root.is_absolute()
            or not isinstance(model_root, Path) or not model_root.is_absolute()
            or type(authorized) is not bool):
        raise ValueError("Invalid model directory preference request")
    if source not in _SOURCES:
        raise ValueError("Invalid provenance for stored Ollama volume")
    if not authorized:
        return StoredModelDirectory("AUTH_REQUIRED")
    _no_linked_ancestors(install_root)
    _no_linked_ancestors(model_root)
    if not model_root.is_dir():
        return StoredModelDirectory("MODEL_VOLUME_NOT_MOUNTED", model_root)
    state_dir = install_root / "settings"
    _no_linked_ancestors(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    _no_linked_ancestors(state_dir)
    target = state_dir / _NAME
    _no_linked_ancestors(target)
    if target.exists():
        existing = read_model_directory(install_root)
        if existing.status != "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED":
            return StoredModelDirectory("BLOCKED_INVALID")
        if existing.directory != model_root:
            return StoredModelDirectory("EXISTING_MODEL_VOLUME_CONFLICT",
                                        existing.directory)
        # Already authorized and saved; do not replace evidence or downgrade
        # the original provenance when a second installer runs.
        return existing
    payload = {
        "schema": 1, "directory": str(model_root),
        "source": source,
    }
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", prefix=".atlas-ollama-",
            suffix=".tmp", dir=state_dir, delete=False,
        ) as output:
            temp_name = output.name
            json.dump(payload, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        _no_linked_ancestors(target)
        os.replace(temp_name, target)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)
    return StoredModelDirectory("STORED_PREFERENCE_NOT_RUNTIME_ATTESTED",
                                model_root, source)
