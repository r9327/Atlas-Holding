"""Opt-in supervised smoke probe for Holding's own local MCP stdio hub.

Never runs an arbitrary pinned plugin. Only reexecutes this process's own
Python interpreter with the fixed local holding.mcp_entry module, sends one
read-only server/discover request, and exits. A positive result proves only
the local hub protocol responded, not that external plugins are usable.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread

from .mcp_local_inventory import PinnedLocalPlugin, inspect_pinned_plugin
from .mcp_stdio import PROTOCOL_VERSION
from .mcp_windows_job import WindowsJobError, launch_verified_local_hub


MAX_PROBE_BYTES = 64 * 1024


@dataclass(frozen=True)
class HubProbeResult:
    status: str
    protocol_version: str | None = None
    server_name: str | None = None
    external_plugins_tested: bool = False


def _discover_packet() -> bytes:
    request = {
        "jsonrpc": "2.0", "id": "holding-probe-1",
        "method": "server/discover",
        "params": {"_meta": {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {
                "name": "holding-local-probe", "version": "1",
            },
        }},
    }
    return (json.dumps(request, separators=(",", ":"), sort_keys=True)
            + "\n").encode("ascii")


def _valid_response(raw: bytes, expected_server: str) -> bool:
    if (type(raw) is not bytes or len(raw) > MAX_PROBE_BYTES
            or not raw.endswith(b"\n") or raw.count(b"\n") != 1):
        return False
    try:
        def reject_duplicate(pairs):
            data = {}
            for key, value in pairs:
                if key in data:
                    raise ValueError("Duplicate protocol key")
                data[key] = value
            return data
        response = json.loads(raw.decode("utf-8"),
                              object_pairs_hook=reject_duplicate)
        if (type(response) is not dict or
                set(response) != {"jsonrpc", "id", "result"} or
                response["jsonrpc"] != "2.0" or
                response["id"] != "holding-probe-1" or
                type(response["result"]) is not dict):
            return False
        result = response["result"]
        info = result.get("_meta", {}).get("io.modelcontextprotocol/serverInfo")
        return bool(
            result.get("resultType") == "complete" and
            result.get("supportedVersions") == [PROTOCOL_VERSION] and
            type(info) is dict and info.get("name") == expected_server and
            type(info.get("version")) is str and
            result.get("capabilities") == {"tools": {}}
        )
    except (UnicodeError, ValueError, TypeError, AttributeError,
            RecursionError, KeyError):
        return False


def _spawn_hub(*, interpreter_pin: PinnedLocalPlugin,
               repository_root: Path, env: dict, creationflags: int):
    if os.name == "nt":
        managed = launch_verified_local_hub(
            interpreter_pin=interpreter_pin, package_root=repository_root,
            job_memory_mb=256, active_limit=4)
        return managed.process, managed
    process = subprocess.Popen(
        [str(interpreter_pin.executable), "-E", "-S", "-B", "-m", "holding.mcp_entry"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd=str(repository_root),
        env=env, shell=False, creationflags=creationflags,
        start_new_session=True,
    )
    return process, None


def probe_local_hub(*, interpreter_pin: PinnedLocalPlugin,
                    repository_root: Path, timeout_seconds: float = 3.0,
                    expected_server: str = "atlas-holding-local") -> HubProbeResult:
    """Run a fixed bounded health smoke; no arbitrary plugin argv or command.

    The installation supervisor must protect the Python executable and the
    repository installation against concurrent tampering (OS ACLs/job objects).
    This function is diagnostic, not a process sandbox.
    """
    if (type(interpreter_pin) is not PinnedLocalPlugin
            or not isinstance(repository_root, Path)
            or not repository_root.is_absolute()
            or not (repository_root / "holding" / "mcp_entry.py").is_file()
            or type(timeout_seconds) not in (float, int)
            or isinstance(timeout_seconds, bool)
            or not 0.25 <= timeout_seconds <= 10
            or expected_server != "atlas-holding-local"):
        raise ValueError("Invalid trusted local MCP hub probe configuration")
    try:
        if interpreter_pin.executable.resolve(strict=True) != Path(sys.executable).resolve(strict=True):
            raise ValueError("Hub probe may execute only the current Python runtime")
        if repository_root.resolve(strict=True) != Path(__file__).resolve().parent.parent:
            raise ValueError("Hub probe may inspect only its own installed package")
    except OSError as exc:
        raise ValueError("Trusted hub installation unavailable") from exc
    current = inspect_pinned_plugin(interpreter_pin)
    if current.binary_state != "BINARY_VERIFIED":
        return HubProbeResult("BINARY_UNVERIFIED")
    env = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    # No API keys, tokens, model variables or home profile secrets inherited.
    for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    creationflags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                     if os.name == "nt" else 0)
    try:
        process, managed = _spawn_hub(
            interpreter_pin=interpreter_pin, repository_root=repository_root,
            env=env, creationflags=creationflags)
    except (OSError, WindowsJobError):
        return HubProbeResult("LAUNCH_FAILED")
    responses: Queue[bytes | None] = Queue(maxsize=1)
    def read_bounded():
        try:
            # Never materialize a malicious/unbounded subprocess stdout.
            responses.put(process.stdout.read(MAX_PROBE_BYTES + 1))
        except OSError:
            responses.put(None)

    reader = Thread(target=read_bounded, daemon=True)
    reader.start()
    try:
        try:
            process.stdin.write(_discover_packet())
            process.stdin.close()
        except OSError:
            pass
        try:
            stdout = responses.get(timeout=timeout_seconds)
        except Empty:
            return HubProbeResult("TIMEOUT")
        if stdout is None or len(stdout) > MAX_PROBE_BYTES:
            return HubProbeResult("OUTPUT_LIMIT")
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            return HubProbeResult("TIMEOUT")
    finally:
        if managed is not None:
            managed.close()  # Includes descendants on Windows timeout.
        else:
            if process.poll() is None:
                process.kill()
            process.wait()
        reader.join(timeout=1)
    # Re-hash after response; this cannot replace the supervisor's private
    # installation/ACL guarantee against hash-to-exec races.
    if inspect_pinned_plugin(interpreter_pin).binary_state != "BINARY_VERIFIED":
        return HubProbeResult("BINARY_CHANGED")
    if process.returncode != 0 or not _valid_response(stdout, expected_server):
        return HubProbeResult("BAD_PROTOCOL")
    return HubProbeResult("PROTOCOL_RESPONSIVE", PROTOCOL_VERSION,
                          expected_server)
