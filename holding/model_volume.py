"""Read-only model-volume preflight separate from application disk sizing.

The capacity of the chosen drive is measured before any downloads/installations.
A model benchmark/catalogue will later determine exact model requirements.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from .inventory import _existing_disk
from .staged_install import _no_linked_ancestors


MIN_MODELS_FREE_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class ModelVolumeInspection:
    status: str
    free_bytes: int | None = None
    backing_directory: str = ""


def inspect_model_volume(model_root: Path) -> ModelVolumeInspection:
    """Never create a target directory or switch silently to another drive."""
    if not isinstance(model_root, Path) or not model_root.is_absolute():
        return ModelVolumeInspection("ABSOLUTE_PATH_REQUIRED")
    try:
        _no_linked_ancestors(model_root)
        if model_root.exists() and not model_root.is_dir():
            return ModelVolumeInspection("NOT_A_DIRECTORY")
        ancestor = _existing_disk(model_root)
        _no_linked_ancestors(ancestor)
        free = int(shutil.disk_usage(ancestor).free)
        if free < MIN_MODELS_FREE_BYTES:
            return ModelVolumeInspection("INSUFFICIENT_SPACE", free,
                                         str(ancestor))
        return ModelVolumeInspection("VOLUME_AVAILABLE", free, str(ancestor))
    except (OSError, ValueError):
        return ModelVolumeInspection("VOLUME_UNAVAILABLE")
