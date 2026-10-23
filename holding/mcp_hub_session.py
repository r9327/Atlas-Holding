"""Bounded persistent lifecycle for the *internal* Atlas Holding MCP hub.

A supervised local process is reused for validated MCP discovery. This is
not a generic external-plugin launcher; model text cannot select an executable,
process arguments, working directory, permissions or MCP method.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import Empty, Queue
import signal
import subprocess
import sys
from threading import Lock, Thread
from time import monotonic

from .mcp_hub_probe import MAX_PROBE_BYTES, _valid_response, _spawn_hub
from .mcp_local_inventory import PinnedLocalPlugin, inspect_pinned_plugin
from .mcp_package_pin import McpPackagePin, McpPackagePinError
from .mcp_stdio import PROTOCOL_VERSION


class McpSessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class McpSessionObservation:
    state: str
    pid: int | None
    last_verified_age_seconds: float | None
    protocol_version: str | None
    external_plugins_tested: bool = False


class LocalMcpHubSession:
    """A single bounded internal MCP process owned by the trusted supervisor."""

    def __init__(self, *, interpreter_pin: PinnedLocalPlugin,
                 package_root: Path, response_timeout: float = 3.0,
                 package_pin: McpPackagePin | None = None) -> None:
        if (type(interpreter_pin) is not PinnedLocalPlugin
                or not isinstance(package_root, Path) or not package_root.is_absolute()
                or package_root.resolve() != Path(__file__).resolve().parent.parent
                or interpreter_pin.executable.resolve() != Path(sys.executable).resolve()
                or type(response_timeout) not in (int, float)
                or isinstance(response_timeout, bool)
                or not 0.25 <= response_timeout <= 10
                or (package_pin is not None and type(package_pin) is not McpPackagePin)):

            raise ValueError("Untrusted internal MCP session configuration")
        self._pin = interpreter_pin
        self._root = package_root
        self._timeout = float(response_timeout)
        self._package_pin = package_pin
        self._lock = Lock()
        self._process: subprocess.Popen | None = None
        self._managed = None
        self._last_verified: float | None = None
        self._closed = False

    def start(self) -> None:
        with self._lock:
            if self._closed or self._process is not None:
                raise McpSessionError("MCP session cannot be started twice")
            if inspect_pinned_plugin(self._pin).binary_state != "BINARY_VERIFIED":
                raise McpSessionError("Pinned internal hub interpreter is unverified")
            if self._package_pin is not None:
                try:
                    if not self._package_pin.verify(self._root):
                        raise McpSessionError("Installed MCP module digest changed")
                except McpPackagePinError as exc:
                    raise McpSessionError("Installed MCP module digest unverifiable") from exc
            env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
            for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP"):
                if key in os.environ:
                    env[key] = os.environ[key]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            try:
                process, managed = _spawn_hub(
                    interpreter_pin=self._pin, repository_root=self._root,
                    env=env, creationflags=flags)
            except OSError as exc:
                raise McpSessionError("Internal MCP hub launch failed") from exc
            self._process, self._managed = process, managed

    def discover(self) -> McpSessionObservation:
        """Exactly one read-only method, no arbitrary MCP tool execution."""
        with self._lock:
            process = self._process
            if self._closed or process is None:
                raise McpSessionError("MCP session is not running")
            if process.poll() is not None:
                self._last_verified = None
                return McpSessionObservation("EXITED", process.pid, None, None)
            packet = {
                "jsonrpc": "2.0", "id": "holding-probe-1",
                "method": "server/discover",
                "params": {"_meta": {
                    "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                    "io.modelcontextprotocol/clientCapabilities": {},
                }},
            }
            encoded = (json.dumps(packet, separators=(",", ":")) + "\n").encode()
            responses: Queue[bytes | None] = Queue(maxsize=1)
            def read_line() -> None:
                try:
                    responses.put(process.stdout.readline(MAX_PROBE_BYTES + 1))
                except (OSError, ValueError):
                    responses.put(None)
            worker = Thread(target=read_line, daemon=True)
            worker.start()
            try:
                process.stdin.write(encoded)
                process.stdin.flush()
                try:
                    raw = responses.get(timeout=self._timeout)
                except Empty:
                    self._terminate_unlocked()
                    return McpSessionObservation("TIMEOUT", process.pid, None, None)
                if raw is None or len(raw) > MAX_PROBE_BYTES:
                    self._terminate_unlocked()
                    return McpSessionObservation("OUTPUT_LIMIT", process.pid, None, None)
                if not _valid_response(raw, "atlas-holding-local"):
                    self._terminate_unlocked()
                    return McpSessionObservation("BAD_PROTOCOL", process.pid, None, None)
                if inspect_pinned_plugin(self._pin).binary_state != "BINARY_VERIFIED":
                    self._terminate_unlocked()
                    return McpSessionObservation("BINARY_CHANGED", process.pid, None, None)
                if self._package_pin is not None:
                    try:
                        module_ok = self._package_pin.verify(self._root)
                    except McpPackagePinError:
                        module_ok = False
                    if not module_ok:
                        self._terminate_unlocked()
                        return McpSessionObservation("BINARY_CHANGED", process.pid, None, None)
                self._last_verified = monotonic()
                return McpSessionObservation(
                    "RESPONSIVE", process.pid, 0.0, PROTOCOL_VERSION)
            except (OSError, ValueError):
                self._terminate_unlocked()
                return McpSessionObservation("IO_ERROR", process.pid, None, None)
            finally:
                worker.join(timeout=0.05)

    def observe(self) -> McpSessionObservation:
        """No network or process request. Expired proof is not live health."""
        with self._lock:
            process = self._process
            if self._closed:
                return McpSessionObservation("CLOSED", None, None, None)
            if process is None:
                return McpSessionObservation("NOT_STARTED", None, None, None)
            if process.poll() is not None:
                return McpSessionObservation("EXITED", process.pid, None, None)
            if self._last_verified is None:
                return McpSessionObservation("UNVERIFIED", process.pid, None, None)
            age = max(0.0, monotonic() - self._last_verified)
            if age > 30:
                return McpSessionObservation("STALE", process.pid, age, None)
            return McpSessionObservation(
                "RESPONSIVE", process.pid, age, PROTOCOL_VERSION)

    def _terminate_unlocked(self) -> None:
        process, managed = self._process, self._managed
        self._process = None
        self._managed = None
        self._last_verified = None
        if process is None:
            return
        if managed is not None:
            managed.close()
        else:
            if process.poll() is None:
                # POSIX start_new_session=True makes process.pid its group ID.
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # A racing, already exited process.
                else:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
            process.wait(timeout=5)
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    pipe.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._terminate_unlocked()

    def __enter__(self) -> "LocalMcpHubSession":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
