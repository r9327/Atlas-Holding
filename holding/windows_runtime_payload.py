"""Strict runtime payload contract shared by a trusted Windows builder and setup.

This module only verifies the two compiled binaries; it cannot authorize
installation, invent a release verdict or provision Windows ACLs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Callable, Mapping


_RUNTIME_FILES = frozenset({
    "AtlasHoldingControlCenter.exe",
    "AtlasHoldingEngine.exe",
})
_SHA = re.compile(r"^[0-9a-f]{64}$")


class RuntimePayloadError(ValueError):
    pass


def verify_runtime_payload(
    directory: Path, expected: Mapping[str, str],
) -> dict[str, str]:
    """Hash real bytes under the exact runner-approved runtime directory."""
    if (not isinstance(directory, Path) or not directory.is_absolute()
            or directory.is_symlink() or not directory.is_dir()
            or not isinstance(expected, Mapping)
            or set(expected) != _RUNTIME_FILES
            or any(type(sha) is not str or not _SHA.fullmatch(sha)
                   for sha in expected.values())):
        raise RuntimePayloadError("Trusted runtime payload pins required")
    try:
        if directory.resolve(strict=True) != directory:
            raise RuntimePayloadError("Runtime directory uses an unsafe alias")
        observations = {}
        for name in sorted(_RUNTIME_FILES):
            file = directory / name
            if (file.is_symlink() or file.resolve(strict=True) != file
                    or not file.is_file()):
                raise RuntimePayloadError("Missing or linked runtime binary")
            size = file.stat().st_size
            if not 1_000_000 <= size <= 1024**3:
                raise RuntimePayloadError("Runtime binary size is implausible")
            hasher = hashlib.sha256()
            with file.open("rb") as reader:
                for block in iter(lambda: reader.read(1024 * 1024), b""):
                    hasher.update(block)
            digest = hasher.hexdigest()
            if digest != expected[name] or file.stat().st_size != size:
                raise RuntimePayloadError("Runtime payload changed since trusted build")
            observations[name] = digest
        return observations
    except OSError as exc:
        raise RuntimePayloadError("Runtime payload files unavailable") from exc


@dataclass(frozen=True)
class InstalledRuntimePair:
    state: str
    directory: Path
    # Staging is NEVER readiness: account ACLs, engine config and real
    # roadmap execution must be certified separately.
    ready: bool = False


def install_pinned_runtime_pair(
    source: Path, install_root: Path, expected: Mapping[str, str], *,
    approved: bool,
    cancelled: Callable[[], bool] | None = None,
) -> InstalledRuntimePair:
    """Stage two checked binaries on the selected volume without overwrite."""
    if approved is not True:
        raise RuntimePayloadError("Runtime installation requires explicit setup approval")
    if cancelled is not None and not callable(cancelled):
        raise RuntimePayloadError("Invalid runtime cancellation control")

    def ensure_active() -> None:
        if cancelled is not None and cancelled():
            raise RuntimePayloadError("Runtime copy cancelled")

    ensure_active()
    if (not isinstance(install_root, Path) or not install_root.is_absolute()
            or install_root.is_symlink() or not install_root.is_dir()
            or install_root.resolve(strict=True) != install_root):
        raise RuntimePayloadError("Verified existing installation root required")
    verify_runtime_payload(source, expected)
    ensure_active()
    version = expected["AtlasHoldingControlCenter.exe"][:16] + "-" + (
        expected["AtlasHoldingEngine.exe"][:16])
    parent = install_root / "runtime"
    if parent.is_symlink():
        raise RuntimePayloadError("Runtime parent cannot be a link")
    parent.mkdir(exist_ok=True)
    if parent.resolve(strict=True) != parent:
        raise RuntimePayloadError("Runtime parent is not a canonical directory")
    if os.name == "nt":
        from .windows_runtime_acl import secure_runtime_acl
        # Apply a protected native ACL before any executable bytes are copied.
        secure_runtime_acl(parent)
    final = parent / version
    if final.exists() or final.is_symlink():
        ensure_active()
        try:
            verify_runtime_payload(final, expected)
            if os.name == "nt":
                from .windows_runtime_acl import validate_runtime_acl
                validate_runtime_acl(parent)
                validate_runtime_acl(final)
                for name in sorted(_RUNTIME_FILES):
                    validate_runtime_acl(final / name)
        except (RuntimePayloadError, OSError, ValueError) as exc:
            raise RuntimePayloadError("Existing runtime is damaged; explicit repair required") from exc
        return InstalledRuntimePair("REUSED_BINARIES_SECURITY_PENDING", final)
    with tempfile.TemporaryDirectory(dir=parent, prefix=".runtime-staging-") as tmp:
        stage = Path(tmp).resolve()
        if os.name == "nt":
            from .windows_runtime_acl import secure_runtime_acl
            secure_runtime_acl(stage)
        for name in sorted(_RUNTIME_FILES):
            ensure_active()
            src = source / name
            if src.is_symlink() or src.resolve(strict=True) != src:
                raise RuntimePayloadError("Pinned executable replaced during staging")
            with src.open("rb") as reader, (stage / name).open("xb") as writer:
                while True:
                    ensure_active()
                    block = reader.read(1024 * 1024)
                    if not block:
                        break
                    writer.write(block)
            if os.name == "nt":
                secure_runtime_acl(stage / name)
            ensure_active()
        verify_runtime_payload(stage, expected)
        ensure_active()
        verify_runtime_payload(source, expected)
        ensure_active()
        if final.exists() or final.is_symlink():
            raise RuntimePayloadError("Runtime destination appeared during staging")
        os.rename(stage, final)
    verify_runtime_payload(final, expected)
    if os.name == "nt":
        from .windows_runtime_acl import validate_runtime_acl
        validate_runtime_acl(parent)
        validate_runtime_acl(final)
        for name in sorted(_RUNTIME_FILES):
            validate_runtime_acl(final / name)
    return InstalledRuntimePair("STAGED_BINARIES_SECURITY_PENDING", final)



def verify_pinned_adapter(source: Path, expected_sha256: str) -> str:
    """Verify the packaged protocol-v1 adapter, never OpenCode by inference."""
    if (not isinstance(source, Path) or not source.is_absolute()
            or source.name != "AtlasHoldingAgent.exe" or source.is_symlink()
            or not isinstance(expected_sha256, str)
            or not _SHA.fullmatch(expected_sha256)):
        raise RuntimePayloadError("Trusted bundled adapter path and pin required")
    try:
        if (source.resolve(strict=True) != source or not source.is_file()
                or not 1 <= source.stat().st_size <= 1024**3):
            raise RuntimePayloadError("Adapter is missing, linked or implausible")
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected_sha256:
            raise RuntimePayloadError("Bundled adapter differs from trusted release pin")
    except OSError as exc:
        raise RuntimePayloadError("Bundled adapter unavailable") from exc
    return expected_sha256


def install_pinned_adapter(
    source: Path, install_root: Path, expected_sha256: str, *,
    approved: bool,
) -> str:
    """Place the exact bundled adapter beside, not inside, an editable clone.

    This installs only pinned bytes; it does not validate the agent's protocol
    or give it a GitHub token. The engine cannot be claimed healthy here.
    """
    if approved is not True:
        raise RuntimePayloadError("Adapter staging requires explicit approval")
    verify_pinned_adapter(source, expected_sha256)
    if (not isinstance(install_root, Path) or not install_root.is_absolute()
            or install_root.is_symlink() or not install_root.is_dir()
            or install_root.resolve(strict=True) != install_root):
        raise RuntimePayloadError("Canonical installation root required")
    if os.name == "nt":
        from .windows_runtime_acl import validate_runtime_acl
        validate_runtime_acl(install_root)
    agents = install_root / "agents"
    if agents.is_symlink():
        raise RuntimePayloadError("Linked adapter directory refused")
    if agents.exists():
        if not agents.is_dir() or agents.resolve(strict=True) != agents:
            raise RuntimePayloadError("Unsafe installed adapter directory")
        if os.name == "nt":
            validate_runtime_acl(agents)
    else:
        agents.mkdir()
        if os.name == "nt":
            from .windows_runtime_acl import secure_runtime_acl
            secure_runtime_acl(agents)
    installed = agents / "AtlasHoldingAgent.exe"
    if installed.exists() or installed.is_symlink():
        verify_pinned_adapter(installed, expected_sha256)
        if os.name == "nt":
            validate_runtime_acl(installed)
        return "REUSED_PINNED_ADAPTER_HEALTH_PENDING"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w+b", prefix=".adapter-", suffix=".tmp",
                dir=agents, delete=False) as target:
            temporary = Path(target.name)
            with source.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    target.write(block)
            target.flush()
            os.fsync(target.fileno())
        # The temporary file is renamed only after its full contents and
        # Windows DACL were verified. No replacement of an existing adapter.
        digest = hashlib.sha256()
        with temporary.open("rb") as verify_stream:
            for chunk in iter(lambda: verify_stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise RuntimePayloadError("Copied adapter differs from release pin")
        verify_pinned_adapter(source, expected_sha256)
        if os.name == "nt":
            secure_runtime_acl(temporary)
        # Exclusive name creation, including when another installer races.
        os.link(temporary, installed)
        if os.name == "nt":
            validate_runtime_acl(installed)
        verify_pinned_adapter(installed, expected_sha256)
        return "STAGED_PINNED_ADAPTER_HEALTH_PENDING"
    except OSError as exc:
        raise RuntimePayloadError("Trusted adapter staging failed") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
