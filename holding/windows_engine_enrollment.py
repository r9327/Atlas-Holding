"""Approved Windows first-run enrollment of a pinned Holding engine config.

The setup may not launch autonomous agents without an exact agent executable,
canonical Atlas clone, installed GUI+engine pair and protected native files.
This does not connect GitHub, download LLMs or attest engine readiness.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping

from .windows_runtime_payload import verify_runtime_payload, RuntimePayloadError

_HEX = re.compile(r"^[0-9a-f]{64}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class EngineEnrollmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class EngineEnrollmentRequest:
    install_root: Path
    runtime_directory: Path
    viewer_sha256: str
    engine_sha256: str
    repository: str
    canonical_root: Path
    workspace: Path
    health_directory: Path
    agent_executable: Path
    agent_sha256: str
    agent_arguments: tuple[Path, ...] = ()
    agent_argument_pins: Mapping[str, str] | None = None
    agent_timeout_seconds: int = 600


@dataclass(frozen=True)
class EngineEnrollment:
    config_path: Path
    config_sha256: str
    status: str
    engine_healthy: bool = False


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(path: Path, *, directory: bool) -> None:
    if (not isinstance(path, Path) or not path.is_absolute()
            or ".." in path.parts or path.is_symlink()):
        raise EngineEnrollmentError("Trusted canonical enrollment path required")
    try:
        resolved = path.resolve(strict=True)
        good = path.is_dir() if directory else path.is_file()
    except (OSError, ValueError) as exc:
        raise EngineEnrollmentError("Trusted enrollment file or directory unavailable") from exc
    if resolved != path or not good:
        raise EngineEnrollmentError("Untrusted enrollment path or link")


def _validate(request: EngineEnrollmentRequest) -> None:
    if not isinstance(request, EngineEnrollmentRequest):
        raise EngineEnrollmentError("Trusted installer enrollment request required")
    directories = (
        request.install_root, request.runtime_directory,
        request.canonical_root, request.workspace, request.health_directory,
    )
    for directory in directories:
        _canonical(directory, directory=True)
    if request.runtime_directory.parent != request.install_root / "runtime":
        raise EngineEnrollmentError("Runtime must be installed on selected disk")
    if (not isinstance(request.repository, str) or not _REPO.fullmatch(request.repository)
            or any(part in (".", "..") for part in request.repository.split("/"))
            or type(request.agent_timeout_seconds) is not int
            or not 1 <= request.agent_timeout_seconds <= 3600):
        raise EngineEnrollmentError("Invalid pinned repository or agent timeout")
    if (not isinstance(request.agent_arguments, tuple)
            or len(request.agent_arguments) > 8
            or request.agent_argument_pins is None
            or not isinstance(request.agent_argument_pins, Mapping)
            or set(request.agent_argument_pins) != set(map(str, request.agent_arguments))):
        raise EngineEnrollmentError("Every trusted agent argument must be pinned")
    _canonical(request.agent_executable, directory=False)
    if (type(request.agent_sha256) is not str or not _HEX.fullmatch(request.agent_sha256)
            or _sha(request.agent_executable) != request.agent_sha256):
        raise EngineEnrollmentError("Trusted agent executable changed")
    for path in request.agent_arguments:
        _canonical(path, directory=False)
        digest = request.agent_argument_pins[str(path)]
        if type(digest) is not str or not _HEX.fullmatch(digest) or _sha(path) != digest:
            raise EngineEnrollmentError("Trusted agent argument changed")
    if any(type(digest) is not str or not _HEX.fullmatch(digest)
           for digest in (request.viewer_sha256, request.engine_sha256)):
        raise EngineEnrollmentError("Invalid installed runtime pins")
    try:
        verify_runtime_payload(request.runtime_directory, {
            "AtlasHoldingControlCenter.exe": request.viewer_sha256,
            "AtlasHoldingEngine.exe": request.engine_sha256,
        })
    except RuntimePayloadError as exc:
        raise EngineEnrollmentError("Installed runtime pair does not match pins") from exc


def _native_security(paths: tuple[Path, ...]) -> None:
    if os.name != "nt":
        raise EngineEnrollmentError("Native Windows ACL enrollment is required")
    from .windows_runtime_acl import (
        validate_runtime_acl, secure_runtime_acl,
    )
    # Never relax permissions on existing user-owned data; the caller must
    # provision protected config/data roots separately before enrollment.
    for path in paths:
        validate_runtime_acl(path)


def _document(request: EngineEnrollmentRequest) -> bytes:
    # Exact key set matches Installation.load(schema=3) in PR #11.
    config = {
        "schema": 3,
        "database": str(request.install_root / "data" / "engine.sqlite"),
        "repository": request.repository,
        "canonical_root": str(request.canonical_root),
        "workspace": str(request.workspace),
        "health_directory": str(request.health_directory),
        "executable": str(request.agent_executable),
        "executable_sha256": request.agent_sha256,
        "arguments": [str(path) for path in request.agent_arguments],
        "argument_sha256": dict(request.agent_argument_pins),
        "timeout_seconds": request.agent_timeout_seconds,
        "github": None,  # GitHub credentials cannot be inferred.
        "dashboard": {
            "executable": str(request.runtime_directory / "AtlasHoldingControlCenter.exe"),
            "executable_sha256": request.viewer_sha256,
            "script": None,
            "script_sha256": None,
            "readonly": False,
        },
    }
    return (json.dumps(config, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n").encode("utf-8")


def enroll_pinned_engine(
    request: EngineEnrollmentRequest, *, approved: bool,
) -> EngineEnrollment:
    """Write protected config once and report configuration only, not READY."""
    if type(approved) is not bool or not approved:
        raise EngineEnrollmentError("Installer approval required for engine enrollment")
    _validate(request)
    root = request.install_root
    data_root = root / "data"
    config_root = root / "config"
    # Directories must already be created and security-enrolled by setup.
    # Do not create a user-writable config root just to make enrollment pass.
    _canonical(data_root, directory=True)
    _canonical(config_root, directory=True)
    _native_security((
        request.runtime_directory.parent,
        request.runtime_directory,
        request.runtime_directory / "AtlasHoldingControlCenter.exe",
        request.runtime_directory / "AtlasHoldingEngine.exe",
        data_root, config_root,
    ))
    payload = _document(request)
    if len(payload) > 32 * 1024:
        raise EngineEnrollmentError("Trusted engine config exceeds size limit")
    target = config_root / "engine.json"
    if target.exists() or target.is_symlink():
        _canonical(target, directory=False)
        _native_security((target,))
        if target.read_bytes() != payload:
            raise EngineEnrollmentError("Existing config differs; explicit repair required")
        return EngineEnrollment(target, hashlib.sha256(payload).hexdigest(),
                                "REUSED_CONFIG_HEALTH_PENDING")
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w+b", dir=config_root, prefix=".engine-config-",
                suffix=".tmp", delete=False) as writer:
            temp = Path(writer.name)
            from .windows_runtime_acl import secure_runtime_acl
            secure_runtime_acl(temp)
            writer.write(payload)
            writer.flush()
            os.fsync(writer.fileno())
        if temp.read_bytes() != payload:
            raise EngineEnrollmentError("Staged engine config bytes were modified")
        _native_security((temp,))
        if target.exists() or target.is_symlink():
            raise EngineEnrollmentError("Engine config appeared during enrollment")
        # os.rename raises on Windows if the target was created concurrently.
        os.rename(temp, target)
        temp = None
        _native_security((target,))
        if target.read_bytes() != payload:
            raise EngineEnrollmentError("Installed engine config changed")
        return EngineEnrollment(target, hashlib.sha256(payload).hexdigest(),
                                "CONFIGURED_ENGINE_HEALTH_PENDING")
    except OSError as exc:
        raise EngineEnrollmentError("Engine config enrollment failed") from exc
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
