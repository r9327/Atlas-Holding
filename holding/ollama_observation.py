"""Bounded read-only local Ollama model allocation observation.

This is *not* process RSS, peak RAM, peak VRAM, NVIDIA-SMI telemetry, or
proof of a genuine model inference. /api/ps reports loaded model allocation
at sampled instants only. A missing observation must remain UNKNOWN.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import threading
from urllib.request import Request, ProxyHandler, build_opener

_MODEL = re.compile(r"^[A-Za-z0-9_.:-]+(?:/[A-Za-z0-9_.:-]+)*$")
_MAX_RESPONSE = 128 * 1024
_MAX_ALLOCATED = 1024 ** 5  # Reject implausible >1 PiB API values.


@dataclass(frozen=True)
class OllamaAllocation:
    observations: int = 0
    loaded_model_bytes_high: int | None = None
    loaded_model_vram_bytes_high: int | None = None
    failed_polls: int = 0


def _read_active_model(name: str) -> tuple[int, int] | None:
    """Query loopback only, no proxy, bounded bytes and deadline.

    /api/ps is advisory telemetry. No credentials or model prompt are sent.
    """
    opener = build_opener(ProxyHandler({}))
    request = Request("http://127.0.0.1:11434/api/ps", method="GET")
    with opener.open(request, timeout=1) as response:
        if response.status != 200:
            raise ValueError("Ollama process inventory is not healthy")
        if int(response.headers.get("Content-Length") or "0") > _MAX_RESPONSE:
            raise ValueError("Ollama process inventory too large")
        raw = response.read(_MAX_RESPONSE + 1)
    if len(raw) > _MAX_RESPONSE:
        raise ValueError("Ollama process inventory too large")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("Invalid Ollama process inventory")
    for item in payload["models"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid Ollama process entry")
        if item.get("name") != name:
            continue
        size, vram = item.get("size"), item.get("size_vram")
        if (type(size) is not int or type(vram) is not int
                or not 0 <= vram <= size <= _MAX_ALLOCATED):
            raise ValueError("Untrusted model allocation reported")
        return size, vram
    return None


class OllamaAllocationObserver:
    """Sample one pinned model while the trusted supervisor runs a mission.

    Failures are advisory, not successful zeros. The observer only measures
    the requested model. It never starts, pulls, or stops local Ollama.
    """

    def __init__(self, name: str, *, interval_seconds: float = 0.5) -> None:
        if (not isinstance(name, str) or not 1 <= len(name) <= 100
                or not _MODEL.fullmatch(name)
                or any(part in (".", "..") for part in name.split("/"))
                or type(interval_seconds) not in (float, int)
                or not 0.2 <= interval_seconds <= 5):
            raise ValueError("Invalid local Ollama observation")
        self.name = name
        self.interval_seconds = interval_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._observations = 0
        self._failures = 0
        self._loaded_max: int | None = None
        self._vram_max: int | None = None

    def _poll(self) -> None:
        try:
            sizes = _read_active_model(self.name)
        except (OSError, ValueError, UnicodeError, TimeoutError, OverflowError):
            with self._lock:
                self._failures += 1
            return
        if sizes is None:
            return
        with self._lock:
            self._observations += 1
            loaded, vram = sizes
            self._loaded_max = max(self._loaded_max or 0, loaded)
            self._vram_max = max(self._vram_max or 0, vram)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._poll()
            if self._stop.wait(self.interval_seconds):
                break

    def start(self) -> None:
        if self._thread is not None:
            raise ValueError("Ollama observation has already started")
        thread = threading.Thread(target=self._run, name="OllamaAllocation", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self) -> OllamaAllocation:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise RuntimeError("Ollama allocation observer did not terminate")
        with self._lock:
            return OllamaAllocation(
                observations=self._observations,
                loaded_model_bytes_high=self._loaded_max,
                loaded_model_vram_bytes_high=self._vram_max,
                failed_polls=self._failures,
            )
