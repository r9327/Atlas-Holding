"""Atomic staging for SHA-pinned Holding engine and Control Center binaries.

Unlike the trusted Windows installer, this module does not set ACLs, register
the daemon, write config, create shortcuts or run either executable. It only
prepares a verifiable, immutable-by-contract pair for later privileged install.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from .staged_install import _no_linked_ancestors

_EXPECTED = ("AtlasHoldingControlCenter.exe", "AtlasHoldingEngine.exe")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_RECEIPT = ".atlas-runtime-receipt.json"
_MAX_BINARY = 1024 * 1024**2


class NativeRuntimeError(ValueError):
    """Untrusted, incomplete or concurrently changed runtime pair."""


@dataclass(frozen=True)
class NativePin:
    sha256: str
    size_bytes: int

    def validate(self) -> None:
        if (not isinstance(self.sha256, str) or not _DIGEST.fullmatch(self.sha256)
                or type(self.size_bytes) is not int
                or not 0 < self.size_bytes <= _MAX_BINARY):
            raise NativeRuntimeError("Invalid pinned runtime executable")


@dataclass(frozen=True)
class NativeStage:
    status: str
    location: Path


def _measure(path: Path, pin: NativePin) -> None:
    if path.is_symlink() or not path.is_file():
        raise NativeRuntimeError("Native executable is absent or linked")
    digest = hashlib.sha256()
    length = 0
    with path.open("rb") as stream:
        initial = os.fstat(stream.fileno())
        if not stat.S_ISREG(initial.st_mode) or initial.st_size != pin.size_bytes:
            raise NativeRuntimeError("Pinned native binary size mismatch")
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            length += len(chunk)
            if length > _MAX_BINARY:
                raise NativeRuntimeError("Pinned binary is too large")
            digest.update(chunk)
        final = os.fstat(stream.fileno())
    if (length != pin.size_bytes or final.st_size != length
            or digest.hexdigest() != pin.sha256):
        raise NativeRuntimeError("Pinned native binary SHA-256 mismatch")


def _receipt(pins: dict[str, NativePin]) -> bytes:
    return json.dumps({
        "schema_version": 1, "status": "STAGED_UNREGISTERED",
        "binaries": {name: {"sha256": pins[name].sha256,
                            "size_bytes": pins[name].size_bytes}
                     for name in _EXPECTED},
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_staged(folder: Path, pins: dict[str, NativePin], receipt: bytes) -> None:
    if folder.is_symlink() or not folder.is_dir():
        raise NativeRuntimeError("Staged runtime directory is invalid")
    if {p.name for p in folder.iterdir()} != {*_EXPECTED, _RECEIPT}:
        raise NativeRuntimeError("Staged runtime contents have changed")
    record = folder / _RECEIPT
    if record.is_symlink() or record.read_bytes() != receipt:
        raise NativeRuntimeError("Runtime receipt is missing or changed")
    for name in _EXPECTED:
        _measure(folder / name, pins[name])


def stage_native_runtime_pair(
    *, source: Path, destination: Path, pins: dict[str, NativePin],
    approved: bool,
) -> NativeStage:
    """Prepare both verified files or leave no published partial pair.

    Caller must obtain pins from an independently trusted release manifest,
    not a roadmap, AI message or the ZIP itself.
    """
    if (not isinstance(source, Path) or not source.is_absolute()
            or not isinstance(destination, Path) or not destination.is_absolute()
            or type(approved) is not bool
            or not isinstance(pins, dict) or set(pins) != set(_EXPECTED)):
        raise NativeRuntimeError("Trusted absolute paths and exact runtime pair required")
    if any(not isinstance(p, NativePin) for p in pins.values()):
        raise NativeRuntimeError("Invalid native pin object")
    for pin in pins.values():
        pin.validate()
    _no_linked_ancestors(source)
    _no_linked_ancestors(destination)
    if source == destination or source in destination.parents or destination in source.parents:
        raise NativeRuntimeError("Native source and target must be separate trees")

    # Observe BOTH publisher files before any destination mutation, even if
    # the destination already contains a same-version candidate.
    for name in _EXPECTED:
        _measure(source / name, pins[name])
    receipt = _receipt(pins)
    version = hashlib.sha256(receipt).hexdigest()
    target = destination / "runtime-candidates" / version
    _no_linked_ancestors(target)
    if target.exists():
        _validate_staged(target, pins, receipt)
        return NativeStage("VERIFIED_REUSED_UNREGISTERED", target)
    if not approved:
        return NativeStage("APPROVAL_REQUIRED", target)

    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    _no_linked_ancestors(parent)
    with tempfile.TemporaryDirectory(prefix=".native-staging-", dir=parent) as tmp:
        stage = Path(tmp)
        for name in _EXPECTED:
            # Preserve source files, never run them. Verify copied bytes again.
            shutil.copyfile(source / name, stage / name, follow_symlinks=False)
            _measure(stage / name, pins[name])
        with (stage / _RECEIPT).open("xb") as file:
            file.write(receipt)
            file.flush()
            os.fsync(file.fileno())
        _validate_staged(stage, pins, receipt)
        if target.exists():
            _validate_staged(target, pins, receipt)
            return NativeStage("VERIFIED_REUSED_UNREGISTERED", target)
        os.rename(stage, target)
    return NativeStage("STAGED_UNREGISTERED", target)
