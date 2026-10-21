"""Per-user Ollama resume registration, never a global Windows service.

Microsoft documents HKCU Run as an at-sign-in launcher and imposes a 260
character command-line bound. Enrollment is optional, explicitly consented,
and refuses to overwrite an unrelated or changed autostart registration.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys

from .staged_install import _no_linked_ancestors
from .ollama_storage import read_model_directory


REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REGISTRY_VALUE = "AtlasHoldingOllama"
AGENT_FILENAME = "AtlasHoldingAgent.exe"


@dataclass(frozen=True)
class AgentInstall:
    status: str
    executable: str = ""
    sha256: str = ""


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_frozen_agent(
    install_root: Path, *, authorized: bool,
) -> AgentInstall:
    """Copy only this running frozen application; never clobber installed bytes."""
    if not isinstance(install_root, Path) or not install_root.is_absolute():
        raise ValueError("Absolute install path required")
    if type(authorized) is not bool:
        raise ValueError("Explicit boolean consent required")
    if not authorized:
        return AgentInstall("AUTH_REQUIRED")
    if platform.system() != "Windows" or not getattr(sys, "frozen", False):
        return AgentInstall("FROZEN_WINDOWS_HOST_REQUIRED")
    source = Path(sys.executable)
    _no_linked_ancestors(source)
    _no_linked_ancestors(install_root)
    if not source.is_absolute() or not source.is_file():
        return AgentInstall("HOST_INVALID")
    if source.suffix.lower() != ".exe" or source.stat().st_size < 1024 * 1024:
        return AgentInstall("HOST_INVALID")
    # At present only our release-packaged bootstrap is eligible as an agent.
    if source.name.casefold() not in {"atlasholdingsetup.exe",
                                      AGENT_FILENAME.casefold()}:
        return AgentInstall("UNEXPECTED_HOST")
    destination = install_root / AGENT_FILENAME
    _no_linked_ancestors(destination)
    identity = _hash_file(source)
    if destination.exists() or destination.is_symlink():
        if not destination.is_file() or destination.stat().st_nlink != 1:
            return AgentInstall("HOST_CONFLICT")
        if _hash_file(destination) != identity:
            return AgentInstall("HOST_DIFFERENT_VERSION")
        return AgentInstall("HOST_REUSED", str(destination), identity)
    install_root.mkdir(parents=True, exist_ok=True)
    _no_linked_ancestors(install_root)
    try:
        with source.open("rb") as reader, destination.open("xb") as output:
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if _hash_file(destination) != identity:
            raise OSError("Copied agent differs from running installer")
    except BaseException:
        # Do not unlink if a different process replaced the path.
        if destination.is_file() and not destination.is_symlink():
            try:
                if destination.stat().st_nlink == 1:
                    destination.unlink()
            except OSError:
                pass
        raise
    return AgentInstall("HOST_STAGED", str(destination), identity)


def _command(install_root: Path, agent: Path) -> str:
    if (not isinstance(install_root, Path) or not install_root.is_absolute()
            or agent != install_root / AGENT_FILENAME
            or any("\n" in str(item) or "\r" in str(item)
                   for item in (install_root, agent))):
        raise ValueError("Invalid trusted startup command")
    command = subprocess.list2cmdline([
        str(agent), "--resume-ollama", str(install_root),
    ])
    if len(command) > 260:
        raise ValueError("Windows HKCU Run startup command exceeds 260 characters")
    return command


def enable_logon_resume(
    install_root: Path, *, agent_sha256: str, authorized: bool,
) -> str:
    """Register only our exact agent, under HKCU, after distinct user consent."""
    if type(authorized) is not bool or not authorized:
        return "AUTH_REQUIRED"
    if platform.system() != "Windows":
        return "WINDOWS_REQUIRED"
    if not isinstance(agent_sha256, str) or len(agent_sha256) != 64:
        raise ValueError("Pinned agent SHA256 required")
    agent = install_root / AGENT_FILENAME
    _no_linked_ancestors(agent)
    if (not agent.is_file() or agent.stat().st_nlink != 1
            or _hash_file(agent) != agent_sha256):
        return "AGENT_CHANGED_OR_MISSING"
    command = _command(install_root, agent)
    import winreg
    access = winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REGISTRY_PATH,
                                0, access) as key:
            try:
                existing, kind = winreg.QueryValueEx(key, REGISTRY_VALUE)
            except FileNotFoundError:
                existing, kind = None, None
            if existing is not None:
                if existing == command and kind == winreg.REG_SZ:
                    return "ALREADY_ENROLLED"
                return "FOREIGN_AUTOSTART_CONFLICT"
            winreg.SetValueEx(key, REGISTRY_VALUE, 0, winreg.REG_SZ, command)
            return "LOGON_RESUME_ENROLLED"
    except OSError:
        return "REGISTRY_REGISTRATION_FAILED"


def disable_logon_resume(install_root: Path) -> str:
    """Remove only the exact Atlas command; no other run values are touched."""
    if platform.system() != "Windows":
        return "WINDOWS_REQUIRED"
    command = _command(install_root, install_root / AGENT_FILENAME)
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REGISTRY_PATH, 0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as key:
            try:
                existing, kind = winreg.QueryValueEx(key, REGISTRY_VALUE)
            except FileNotFoundError:
                return "NOT_ENROLLED"
            if existing != command or kind != winreg.REG_SZ:
                return "FOREIGN_AUTOSTART_CONFLICT"
            winreg.DeleteValue(key, REGISTRY_VALUE)
        return "LOGON_RESUME_DISABLED"
    except FileNotFoundError:
        return "NOT_ENROLLED"
    except OSError:
        return "REGISTRY_REGISTRATION_FAILED"


def configure_logon_resume(
    install_root: Path, *, authorized: bool,
) -> str:
    """Enroll only after an owned Ollama service saved its storage preference."""
    if type(authorized) is not bool or not authorized:
        return "AUTH_REQUIRED"
    saved = read_model_directory(install_root)
    if saved.status != "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED":
        return "MODEL_VOLUME_" + saved.status
    agent = install_frozen_agent(install_root, authorized=True)
    if agent.status not in {"HOST_STAGED", "HOST_REUSED"}:
        return agent.status
    return enable_logon_resume(
        install_root, agent_sha256=agent.sha256, authorized=True,
    )
