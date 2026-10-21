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
    final = parent / version
    if final.exists() or final.is_symlink():
        ensure_active()
        try:
            verify_runtime_payload(final, expected)
        except RuntimePayloadError as exc:
            raise RuntimePayloadError("Existing runtime is damaged; explicit repair required") from exc
        return InstalledRuntimePair("REUSED_BINARIES_SECURITY_PENDING", final)
    with tempfile.TemporaryDirectory(dir=parent, prefix=".runtime-staging-") as tmp:
        stage = Path(tmp).resolve()
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
            ensure_active()
        verify_runtime_payload(stage, expected)
        ensure_active()
        verify_runtime_payload(source, expected)
        ensure_active()
        if final.exists() or final.is_symlink():
            raise RuntimePayloadError("Runtime destination appeared during staging")
        os.rename(stage, final)
    verify_runtime_payload(final, expected)
    return InstalledRuntimePair("STAGED_BINARIES_SECURITY_PENDING", final)
