"""Pinned Ollama model acquisition through its loopback API only.

The model+digest must come from an independently reviewed catalogue and must
already be selected by the quality benchmark layer. A successful pull means
only that model bytes match the pin, never that coding/tool QA has passed.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Callable
from urllib import request
from urllib.error import HTTPError, URLError

from .inventory import _NoRedirect, _api_json


_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,179}$")
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_MAX_LINE = 16 * 1024
_MAX_EVENTS = 100_000


class ModelPullError(ValueError):
    pass


@dataclass(frozen=True)
class PinnedModel:
    name: str
    digest: str

    def validate(self) -> None:
        if (not isinstance(self.name, str) or not _MODEL_NAME.fullmatch(self.name)
                or any(segment in ("", ".", "..") for segment in self.name.split("/"))
                or ".." in self.name):
            raise ModelPullError("Invalid trusted model name")
        if not isinstance(self.digest, str) or not _DIGEST.fullmatch(self.digest):
            raise ModelPullError("Model digest must be pinned to SHA256")


@dataclass(frozen=True)
class ModelPullResult:
    state: str
    name: str
    verified_digest: str | None
    events: int


def _lookup_digest(name: str) -> str | None:
    try:
        payload = _api_json("/api/tags")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ModelPullError("Ollama local inventory unavailable") from exc
    models = payload.get("models")
    if not isinstance(models, list):
        raise ModelPullError("Ollama returned an invalid model inventory")
    for row in models[:10000]:
        if isinstance(row, dict) and row.get("name") == name:
            digest = row.get("digest")
            return digest if isinstance(digest, str) else None
    return None


def pull_pinned_model(
    pin: PinnedModel, *, authorized: bool, online: bool,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    max_seconds: float = 7200.0,
) -> ModelPullResult:
    """Use Ollama's built-in resumable streaming pull without fake success."""
    if not isinstance(pin, PinnedModel):
        raise ModelPullError("Pinned model required")
    pin.validate()
    if type(authorized) is not bool or type(online) is not bool:
        raise ModelPullError("Invalid transfer authorization")
    if not 0 < max_seconds <= 86400:
        raise ModelPullError("Invalid model pull deadline")
    existing = _lookup_digest(pin.name)
    if existing == pin.digest:
        return ModelPullResult("ALREADY_VERIFIED", pin.name, existing, 0)
    if existing is not None:
        # Avoid silently replacing a valid but different user-owned model tag.
        return ModelPullResult("DIFFERENT_LOCAL_DIGEST", pin.name, None, 0)
    if not authorized:
        return ModelPullResult("AUTH_REQUIRED", pin.name, None, 0)
    if not online:
        return ModelPullResult("NETWORK_REQUIRED", pin.name, None, 0)
    if cancelled is not None and cancelled():
        return ModelPullResult("CANCELLED", pin.name, None, 0)

    data = json.dumps({
        "model": pin.name, "stream": True, "insecure": False,
    }, sort_keys=True).encode("ascii")
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
    source = request.Request(
        "http://127.0.0.1:11434/api/pull", data=data, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
    )
    started = time.monotonic()
    count = 0
    success = False
    try:
        with opener.open(source, timeout=15) as response:
            if response.status != 200:
                raise ModelPullError("Ollama declined model pull")
            while count < _MAX_EVENTS:
                if cancelled is not None and cancelled():
                    return ModelPullResult("CANCELLED", pin.name, None, count)
                if time.monotonic() - started > max_seconds:
                    return ModelPullResult("TIMEOUT_RETRYABLE", pin.name, None, count)
                line = response.readline(_MAX_LINE + 1)
                if len(line) > _MAX_LINE:
                    raise ModelPullError("Oversized Ollama pull event")
                if not line:
                    break
                count += 1
                try:
                    event = json.loads(line)
                except (UnicodeError, ValueError) as exc:
                    raise ModelPullError("Invalid Ollama pull event") from exc
                if not isinstance(event, dict):
                    raise ModelPullError("Non-object Ollama pull event")
                if "error" in event:
                    raise ModelPullError("Ollama failed model download")
                status = event.get("status")
                if not isinstance(status, str) or len(status) > 150:
                    raise ModelPullError("Invalid Ollama model pull status")
                total, completed = event.get("total"), event.get("completed")
                if type(total) is int and type(completed) is int:
                    if 0 <= completed <= total <= 500 * 1024**3:
                        if progress is not None:
                            progress(completed, total)
                if status == "success":
                    success = True
                    break
    except (HTTPError, URLError, OSError, TimeoutError):
        return ModelPullResult("INTERRUPTED_RETRYABLE", pin.name, None, count)
    if not success:
        return ModelPullResult("INTERRUPTED_RETRYABLE", pin.name, None, count)
    observed = _lookup_digest(pin.name)
    if observed != pin.digest:
        return ModelPullResult("DIGEST_MISMATCH", pin.name, None, count)
    return ModelPullResult("MODEL_VERIFIED_NOT_BENCHMARKED", pin.name, observed, count)
