"""Strict runtime payload contract shared by a trusted Windows builder and setup.

This module only verifies the two compiled binaries; it cannot authorize
installation, invent a release verdict or provision Windows ACLs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Mapping


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
