"""Windows user-scoped Ollama model-volume configuration.

Ollama Desktop auto-starts after user-scope installation and at sign-in. The
vendor requires the user's OLLAMA_MODELS environment variable for a durable
model location. This module never edits machine-level environment, overwrites
an existing different user value, or kills/restarts third-party processes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform

from .model_volume import inspect_model_volume
from .staged_install import _no_linked_ancestors


@dataclass(frozen=True)
class UserModelEnvironment:
    status: str
    directory: str = ""


def configure_user_models(model_root: Path, *, authorized: bool) -> UserModelEnvironment:
    """Set HKCU\\Environment\\OLLAMA_MODELS once, with explicit consent."""
    if not isinstance(model_root, Path) or not model_root.is_absolute():
        raise ValueError("Absolute model storage path required")
    if type(authorized) is not bool:
        raise ValueError("Explicit boolean consent required")
    if not authorized:
        return UserModelEnvironment("AUTH_REQUIRED")
    if platform.system() != "Windows":
        return UserModelEnvironment("WINDOWS_REQUIRED")
    inspection = inspect_model_volume(model_root)
    if inspection.status != "VOLUME_AVAILABLE":
        return UserModelEnvironment(
            "MODEL_VOLUME_" + inspection.status, str(model_root),
        )
    _no_linked_ancestors(model_root)
    import winreg
    access = winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, "Environment", 0, access,
        ) as key:
            try:
                existing, kind = winreg.QueryValueEx(key, "OLLAMA_MODELS")
            except FileNotFoundError:
                existing, kind = None, None
            if existing is not None:
                if existing == str(model_root) and kind == winreg.REG_SZ:
                    return UserModelEnvironment(
                        "USER_MODELS_ALREADY_MATCH", str(model_root),
                    )
                return UserModelEnvironment(
                    "USER_MODELS_CONFLICT", str(model_root),
                )
            # Second read avoids accidental overwrites in ordinary races,
            # although Windows Registry does not provide a CAS operation.
            try:
                winreg.QueryValueEx(key, "OLLAMA_MODELS")
            except FileNotFoundError:
                pass
            else:
                return UserModelEnvironment(
                    "USER_MODELS_CONFLICT", str(model_root),
                )
            winreg.SetValueEx(
                key, "OLLAMA_MODELS", 0, winreg.REG_SZ, str(model_root),
            )
            return UserModelEnvironment(
                "USER_MODELS_CREATED", str(model_root),
            )
    except OSError:
        return UserModelEnvironment(
            "USER_MODELS_REGISTRY_FAILED", str(model_root),
        )


def undo_created_user_models(model_root: Path) -> str:
    """Rollback only an exact freshly created matching user value."""
    if not isinstance(model_root, Path) or not model_root.is_absolute():
        raise ValueError("Absolute owned model path required")
    if platform.system() != "Windows":
        return "WINDOWS_REQUIRED"
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as key:
            try:
                existing, kind = winreg.QueryValueEx(key, "OLLAMA_MODELS")
            except FileNotFoundError:
                return "USER_MODELS_NOT_CONFIGURED"
            if existing != str(model_root) or kind != winreg.REG_SZ:
                return "USER_MODELS_CHANGED_NO_ROLLBACK"
            winreg.DeleteValue(key, "OLLAMA_MODELS")
        return "USER_MODELS_ROLLED_BACK"
    except OSError:
        return "USER_MODELS_ROLLBACK_FAILED"


def inspect_user_models(model_root: Path) -> UserModelEnvironment:
    """Read-only HKCU registry attestation, never an active-daemon assertion."""
    if not isinstance(model_root, Path) or not model_root.is_absolute():
        raise ValueError("Absolute requested model path required")
    if platform.system() != "Windows":
        return UserModelEnvironment("WINDOWS_REQUIRED", str(model_root))
    try:
        _no_linked_ancestors(model_root)
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0,
            winreg.KEY_QUERY_VALUE,
        ) as key:
            try:
                value, kind = winreg.QueryValueEx(key, "OLLAMA_MODELS")
            except FileNotFoundError:
                return UserModelEnvironment(
                    "USER_MODELS_NOT_CONFIGURED", str(model_root),
                )
        if value == str(model_root) and kind == winreg.REG_SZ:
            if inspect_model_volume(model_root).status != "VOLUME_AVAILABLE":
                return UserModelEnvironment(
                    "MODEL_VOLUME_UNAVAILABLE", str(model_root),
                )
            return UserModelEnvironment("USER_MODELS_MATCH", str(model_root))
        return UserModelEnvironment("USER_MODELS_CONFLICT", str(model_root))
    except FileNotFoundError:
        return UserModelEnvironment(
            "USER_MODELS_NOT_CONFIGURED", str(model_root),
        )
    except (OSError, ValueError):
        return UserModelEnvironment(
            "USER_MODELS_READ_FAILED", str(model_root),
        )
