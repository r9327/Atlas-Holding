"""Trusted session bridge between the installed daemon and Qt viewer.

Only the installer may pin and invoke this program. The viewer executable and
its optional script are verified before spawn; credentials travel over a private
anonymous pipe and never appear in argv, env, SQLite, disk, or logs.

This bridge is NOT an OS security boundary: Windows per-user launch, signed
bundle integrity, protected installation paths and ACLs remain release gates.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .service import HoldingService


class DashboardSessionError(RuntimeError):
    pass


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


@dataclass(frozen=True)
class ViewerProgram:
    executable: Path
    executable_sha256: str
    # Script is for the developer/test harness only; the Windows release uses
    # a frozen viewer binary. Neither path may contain executable flags.
    script: Path | None = None
    script_sha256: str | None = None

    def _verify_one(self, path: Path, digest: str) -> None:
        if (not isinstance(path, Path) or not path.is_absolute()
                or ".." in path.parts or path.is_symlink()
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise DashboardSessionError("Invalid trusted viewer file pin")
        try:
            resolved = path.resolve(strict=True)
            if resolved != path or not path.is_file():
                raise DashboardSessionError("Viewer path uses an unsafe link")
            observed = _digest(path)
        except (OSError, ValueError) as exc:
            raise DashboardSessionError("Pinned viewer file unavailable") from exc
        if not hmac.compare_digest(observed, digest):
            raise DashboardSessionError("Pinned viewer file changed since enrollment")

    def verify(self) -> tuple[str, ...]:
        self._verify_one(self.executable, self.executable_sha256)
        if self.script is None:
            if self.script_sha256 is not None:
                raise DashboardSessionError("Unexpected viewer script pin")
            return (str(self.executable),)
        if self.script_sha256 is None or self.script == self.executable:
            raise DashboardSessionError("Missing separate viewer script pin")
        self._verify_one(self.script, self.script_sha256)
        return (str(self.executable), str(self.script))


def _viewer_env() -> dict[str, str]:
    # Keep only OS/user session requirements. In particular no PYTHON*,
    # QT_PLUGIN_PATH, tokens, shell hooks or inherited application secrets.
    allowed = {
        "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA",
        "APPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
        "PROGRAMDATA", "COMSPEC", "PATH", "LANG", "LC_ALL",
        "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
        "XDG_SESSION_TYPE", "QT_QPA_PLATFORM",
    }
    return {key: value for key, value in os.environ.items()
            if key.upper() in allowed}


class DashboardSession:
    """Own a single GUI child; never stop the daemon on viewer close."""

    def __init__(self, process: subprocess.Popen[bytes]):
        self._process = process

    @property
    def pid(self) -> int:
        return self._process.pid

    def is_running(self) -> bool:
        return self._process.poll() is None

    def close(self) -> None:
        if self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)


def start_dashboard_session(
    service: "HoldingService", program: ViewerProgram, *,
    readonly: bool = False,
) -> DashboardSession:
    """Spawn a verified user-session viewer and deliver one-time capabilities.

    The installer must call this only from its trusted, interactive user host.
    Child exit or failed pipe transmission cannot upgrade the service or claim
    success. No automatic relaunch with stale privileges is attempted.
    """
    from .service import HoldingService
    if (not isinstance(service, HoldingService) or not service._started
            or service._dashboard_ipc is not None or not isinstance(program, ViewerProgram)
            or type(readonly) is not bool):
        raise DashboardSessionError("Invalid trusted dashboard launch context")
    command = program.verify()  # last-minute integrity check
    read_key = secrets.token_bytes(32)
    write_key = None if readonly else secrets.token_bytes(32)
    engine_id = "session-" + secrets.token_hex(16)
    port = service.enable_dashboard_ipc(
        secret=read_key, control_secret=write_key, engine_id=engine_id,
    )
    child = None
    try:
        argv = [
            *command, "--port", str(port), "--engine-id", engine_id,
        ]
        if readonly:
            argv.append("--readonly")
        child = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, cwd=str(program.executable.parent),
            env=_viewer_env(), shell=False, close_fds=True,
        )
        if child.stdin is None:
            raise DashboardSessionError("Viewer credential pipe unavailable")
        try:
            child.stdin.write(read_key + (write_key or b""))
            child.stdin.flush()
        finally:
            child.stdin.close()
        if child.poll() is not None and child.returncode != 0:
            raise DashboardSessionError("Viewer exited before enrollment")
        session = DashboardSession(child)
        return session
    except BaseException:
        if child is not None:
            DashboardSession(child).close()
        service.disable_dashboard_ipc()
        raise
