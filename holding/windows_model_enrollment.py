"""Consent-bound local Ollama model enrollment for frozen Holding agent.

A release never invents a model. Choose an actual /api/tags identity and pin
its digest in a small ACL-protected JSON passed as an executable argument.
No model downloads, external URLs, remote credentials, or shell commands.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.request import Request, ProxyHandler, build_opener

from .windows_runtime_acl import validate_runtime_acl, secure_runtime_acl


class LocalModelEnrollmentError(ValueError):
    pass


_ALLOWED_MODEL = re.compile(
    r"^[A-Za-z0-9_.:-]+(?:/[A-Za-z0-9_.:-]+)*$",
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_LIMIT = 256 * 1024


@dataclass(frozen=True)
class PinnedModel:
    path: Path
    sha256: str
    model: str
    digest: str
    status: str


def discover_local_models() -> tuple[tuple[str, str], ...]:
    """Read exact local Ollama tags via loopback with an explicit size limit."""
    opener = build_opener(ProxyHandler({}))
    request = Request("http://127.0.0.1:11434/api/tags", method="GET")
    try:
        with opener.open(request, timeout=4) as response:
            if response.status != 200:
                raise LocalModelEnrollmentError("Local Ollama inventory unavailable")
            if int(response.headers.get("Content-Length") or "0") > _LIMIT:
                raise LocalModelEnrollmentError("Local model inventory too large")
            raw = response.read(_LIMIT + 1)
        if len(raw) > _LIMIT:
            raise LocalModelEnrollmentError("Local model inventory too large")
        payload = json.loads(raw)
    except (OSError, ValueError, UnicodeError) as exc:
        raise LocalModelEnrollmentError("Local Ollama models cannot be verified") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise LocalModelEnrollmentError("Invalid local Ollama inventory")
    models = {}
    for item in payload["models"]:
        if not isinstance(item, dict):
            raise LocalModelEnrollmentError("Invalid installed model entry")
        name, digest = item.get("name"), item.get("digest")
        if (type(name) is not str or not 1 <= len(name) <= 100
                or not _ALLOWED_MODEL.fullmatch(name)
                or any(part in (".", "..") for part in name.split("/"))
                or type(digest) is not str or not _DIGEST.fullmatch(digest)):
            raise LocalModelEnrollmentError("Invalid local model name or digest")
        if name in models and models[name] != digest:
            raise LocalModelEnrollmentError("Conflicting installed model identities")
        models[name] = digest
    return tuple(sorted(models.items()))


def _valid_file(path: Path) -> bool:
    return (path.is_absolute() and not path.is_symlink()
            and path.is_file() and path.resolve(strict=True) == path)


def enroll_local_model(
    install_root: Path, *, selected_model: str, approved: bool,
    num_ctx: int = 8192, timeout: int = 600,
) -> PinnedModel:
    if type(approved) is not bool or not approved:
        raise LocalModelEnrollmentError("Model selection requires explicit approval")
    if (not isinstance(install_root, Path) or not install_root.is_absolute()
            or not install_root.is_dir() or install_root.is_symlink()
            or install_root.resolve(strict=True) != install_root
            or type(selected_model) is not str or not selected_model
            or type(num_ctx) is not int or not 1024 <= num_ctx <= 131072
            or type(timeout) is not int or not 2 <= timeout <= 3600):
        raise LocalModelEnrollmentError("Invalid pinned model enrollment")
    models = dict(discover_local_models())
    if selected_model not in models:
        raise LocalModelEnrollmentError("Selected model not present in local Ollama")
    folder = install_root / "agents"
    if (not folder.is_dir() or folder.is_symlink()
            or folder.resolve(strict=True) != folder):
        raise LocalModelEnrollmentError("Trusted installed agent folder missing")
    validate_runtime_acl(install_root)
    validate_runtime_acl(folder)
    payload = (json.dumps({
        "schema": 1, "model": selected_model,
        "digest": models[selected_model], "num_ctx": num_ctx,
        "timeout": timeout,
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    digest = hashlib.sha256(payload).hexdigest()
    target = folder / "AtlasHoldingModel.json"
    if target.exists() or target.is_symlink():
        if not _valid_file(target):
            raise LocalModelEnrollmentError("Installed model pin is linked or missing")
        validate_runtime_acl(target)
        if target.read_bytes() != payload:
            raise LocalModelEnrollmentError("Existing model pin changed; explicit repair required")
        return PinnedModel(target, digest, selected_model, models[selected_model],
                           "REUSED_MODEL_PIN_HEALTH_PENDING")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w+b", prefix=".holding-model-", suffix=".tmp",
                dir=folder, delete=False) as output:
            temporary = Path(output.name)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        secure_runtime_acl(temporary)
        if temporary.read_bytes() != payload:
            raise LocalModelEnrollmentError("Model pin bytes changed")
        # Hardlink publication refuses an existing target, including a
        # concurrent writer, instead of silently replacing live model config.
        os.link(temporary, target)
        validate_runtime_acl(target)
        if target.read_bytes() != payload:
            raise LocalModelEnrollmentError("Published model pin changed")
        return PinnedModel(target, digest, selected_model, models[selected_model],
                           "MODEL_PINNED_HEALTH_PENDING")
    except OSError as exc:
        raise LocalModelEnrollmentError("Protected model pin publication failed") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
