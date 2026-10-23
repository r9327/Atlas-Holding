"""Offline staging of a release-pinned Windows CPython *embeddable* runtime.

Do not confuse the PyInstaller engine executable with Python's embeddable
interpreter. The installer supplies an independently trusted release ZIP
digest AND independently trusted Holding source digests. This module never
downloads anything, changes PATH, invokes arbitrary code, or claims that the
resulting Windows executable has passed a native smoke test.
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
import zipfile
from typing import Mapping

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PYTHON_NAME = re.compile(r"^python3(1[0-9])$", re.IGNORECASE)
_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,119}\.py$")
_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
_MAX_UNPACKED_BYTES = 300 * 1024 * 1024
_MAX_MEMBER_BYTES = 200 * 1024 * 1024
_MAX_FILE_COUNT = 128
_MAX_MODULES = 192
_MAX_MODULE_BYTES = 2 * 1024 * 1024
_RECEIPT = "atlas-python-embedded-receipt.json"


class EmbeddedRuntimeError(ValueError):
    pass


@dataclass(frozen=True)
class EmbeddedPythonRelease:
    python_minor: int
    zip_sha256: str
    module_sha256: Mapping[str, str]

    def __post_init__(self) -> None:
        if (type(self.python_minor) is not int or not 13 <= self.python_minor <= 19
                or type(self.zip_sha256) is not str
                or not _DIGEST.fullmatch(self.zip_sha256)
                or type(self.module_sha256) is not dict
                or not 7 <= len(self.module_sha256) <= _MAX_MODULES
                or any(type(name) is not str or not _MODULE_NAME.fullmatch(name)
                       or type(digest) is not str or not _DIGEST.fullmatch(digest)
                       for name, digest in self.module_sha256.items())
                or not {"__init__.py", "mcp_entry.py", "mcp_stdio.py",
                        "mcp_hub_session.py", "mcp_hub_probe.py",
                        "mcp_windows_job.py", "mcp_local_inventory.py"}.issubset(
                            self.module_sha256)):
            raise EmbeddedRuntimeError("Incomplete trusted embeddable Python pins")


@dataclass(frozen=True)
class EmbeddedRuntimeStage:
    directory: Path
    python_exe: Path
    interpreter_sha256: str
    source_sha256: tuple[tuple[str, str], ...]
    status: str = "STAGED_NOT_EXECUTED"


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_parent(path: Path) -> None:
    if (not isinstance(path, Path) or not path.is_absolute()
            or path.is_symlink() or not path.is_dir()
            or path.resolve(strict=True) != path):
        raise EmbeddedRuntimeError("Trusted canonical installer target required")


def _approved_entries(archive: zipfile.ZipFile, minor: int) -> list[zipfile.ZipInfo]:
    expected = {"python.exe", "python3.dll", f"python3{minor}.dll",
                f"python3{minor}.zip", f"python3{minor}._pth"}
    infos = archive.infolist()
    if not 1 <= len(infos) <= _MAX_FILE_COUNT:
        raise EmbeddedRuntimeError("Unexpected embeddable Python archive length")
    seen = set()
    unpacked = 0
    selected = []
    for info in infos:
        name = info.filename
        # The official embeddable release consists of flat files.
        if (info.is_dir() or name in {".", ".."} or "/" in name
                or "\\" in name or ":" in name or name.startswith(".")
                or not name.isascii() or len(name) > 127
                or (info.flag_bits & 0x01)
                or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK):
            raise EmbeddedRuntimeError("Unsafe embeddable Python archive entry")
        folded = name.casefold()
        if folded in seen:
            raise EmbeddedRuntimeError("Duplicate Windows archive filename")
        seen.add(folded)
        if not 1 <= info.file_size <= _MAX_MEMBER_BYTES:
            raise EmbeddedRuntimeError("Invalid embedded Python member length")
        unpacked += info.file_size
        if unpacked > _MAX_UNPACKED_BYTES:
            raise EmbeddedRuntimeError("Embedded Python exceeds maximum disk budget")
        selected.append(info)
    if not {x.casefold() for x in expected}.issubset(seen):
        raise EmbeddedRuntimeError("Incomplete Python embeddable release payload")
    return selected


def stage_pinned_embedded_python(
    *, archive: Path, approved_package_root: Path, target_parent: Path,
    pin: EmbeddedPythonRelease,
) -> EmbeddedRuntimeStage:
    """Stage one exact-release native runtime and exact-release Holding code.

    Source hashes are build/release inputs, not hashes sampled from arbitrary
    untrusted local bytes. Files are only read, never executed.
    """
    if (type(pin) is not EmbeddedPythonRelease
            or not isinstance(archive, Path) or not archive.is_absolute()
            or archive.is_symlink() or not archive.is_file()
            or not isinstance(approved_package_root, Path)
            or not approved_package_root.is_absolute()
            or approved_package_root.is_symlink()
            or approved_package_root.resolve(strict=True) != approved_package_root):
        raise EmbeddedRuntimeError("Trusted build inputs required")
    _canonical_parent(target_parent)
    if (not 1 <= archive.stat().st_size <= _MAX_ARCHIVE_BYTES
            or _sha_file(archive) != pin.zip_sha256):
        raise EmbeddedRuntimeError("CPython archive failed exact release hash")
    source_dir = approved_package_root / "holding"
    if (source_dir.is_symlink() or not source_dir.is_dir()
            or source_dir.resolve(strict=True) != source_dir):
        raise EmbeddedRuntimeError("Trusted Holding module directory required")
    files = {}
    for file in source_dir.iterdir():
        if file.suffix.lower() in {".pyd", ".so", ".dll", ".pyc"}:
            raise EmbeddedRuntimeError("Unpinned executable Holding import")
        if file.suffix == ".py":
            if file.name in files:
                raise EmbeddedRuntimeError("Repeated Holding module")
            files[file.name] = file
    if set(files) != set(pin.module_sha256):
        raise EmbeddedRuntimeError("Holding module inventory differs from release")
    for name, source in files.items():
        if (source.is_symlink() or not source.is_file()
                or not 1 <= source.stat().st_size <= _MAX_MODULE_BYTES
                or _sha_file(source) != pin.module_sha256[name]):
            raise EmbeddedRuntimeError("Holding source differs from release evidence")
    folder = target_parent / ("python-embedded-" + pin.zip_sha256[:16])
    if folder.exists() or folder.is_symlink():
        raise FileExistsError("Pinned Python runtime already installed")
    stage = Path(tempfile.mkdtemp(prefix=".atlas-cpython-", dir=target_parent))
    try:
        with zipfile.ZipFile(archive) as package:
            infos = _approved_entries(package, pin.python_minor)
            for info in infos:
                destination = stage / info.filename
                with package.open(info, "r") as reader, destination.open("xb") as writer:
                    copied = 0
                    for chunk in iter(lambda: reader.read(64 * 1024), b""):
                        copied += len(chunk)
                        if copied > info.file_size:
                            raise EmbeddedRuntimeError("Corrupted CPython member length")
                        writer.write(chunk)
                    if copied != info.file_size:
                        raise EmbeddedRuntimeError("Truncated CPython member")
        # Rebuild the _pth file from a constant safe policy. This forbids
        # import-site and user site packages even if the source ZIP differs.
        pth = stage / f"python3{pin.python_minor}._pth"
        pth.write_bytes(f"python3{pin.python_minor}.zip\n.\n".encode("ascii"))
        native_hashes = {
            file.name: _sha_file(file)
            for file in stage.iterdir() if file.is_file()
        }
        code = stage / "holding"
        code.mkdir()
        for name in sorted(files):
            with files[name].open("rb") as source, (code / name).open("xb") as output:
                shutil.copyfileobj(source, output, length=64 * 1024)
            if _sha_file(code / name) != pin.module_sha256[name]:
                raise EmbeddedRuntimeError("Staged Python code differs from release")
        exe = stage / "python.exe"
        observed = _sha_file(exe)
        receipt = {
            "schema": 1, "source_release_zip_sha256": pin.zip_sha256,
            "python_minor": pin.python_minor,
            "python_exe_sha256": observed,
            "native_file_sha256": dict(sorted(native_hashes.items())),
            "module_sha256": dict(sorted(pin.module_sha256.items())),
            "native_probe": "NOT_EXECUTED",
        }
        (stage / _RECEIPT).write_text(
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        # Full post-copy verification before publication of an immutable-by-
        # contract directory, without claiming a Windows ACL was provisioned.
        if (_sha_file(stage / "python.exe") != observed
                or any(_sha_file(code / name) != digest
                       for name, digest in pin.module_sha256.items())):
            raise EmbeddedRuntimeError("Staged runtime changed before publication")
        if folder.exists() or folder.is_symlink():
            raise FileExistsError("Pinned Python runtime appeared during staging")
        os.rename(stage, folder)
        return EmbeddedRuntimeStage(
            directory=folder, python_exe=folder / "python.exe",
            interpreter_sha256=observed,
            source_sha256=tuple(sorted(pin.module_sha256.items())),
        )
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _unique_json(pairs: list[tuple[str, object]]) -> dict[str, object]:
    data = {}
    for key, value in pairs:
        if key in data:
            raise EmbeddedRuntimeError("Duplicate embedded runtime receipt key")
        data[key] = value
    return data


def verify_staged_embedded_python(
    *, directory: Path, pin: EmbeddedPythonRelease,
) -> EmbeddedRuntimeStage:
    """Check local native + source bytes against the protected staged receipt.

    The receipt is a *local byte-integrity observation*, not a publisher
    signature. Installation authorization still requires trusted upstream
    SHA-256 and locked ACLs from the actual Windows setup.
    """
    if (not isinstance(directory, Path) or not directory.is_absolute()
            or directory.is_symlink() or not directory.is_dir()
            or directory.resolve(strict=True) != directory
            or type(pin) is not EmbeddedPythonRelease):
        raise EmbeddedRuntimeError("Trusted installed runtime directory required")
    try:
        raw = (directory / _RECEIPT).read_bytes()
        if not 1 <= len(raw) <= 32 * 1024:
            raise EmbeddedRuntimeError("Invalid embedded runtime receipt size")
        receipt = json.loads(raw, object_pairs_hook=_unique_json)
        if (type(receipt) is not dict
                or set(receipt) != {
                    "schema", "source_release_zip_sha256", "python_minor",
                    "python_exe_sha256", "module_sha256",
                    "native_file_sha256", "native_probe",
                } or type(receipt["schema"]) is not int
                or receipt["schema"] != 1
                or receipt["source_release_zip_sha256"] != pin.zip_sha256
                or receipt["python_minor"] != pin.python_minor
                or receipt["native_probe"] != "NOT_EXECUTED"
                or receipt["module_sha256"] != pin.module_sha256):
            raise EmbeddedRuntimeError("Staged runtime receipt does not match release pins")
        expected = receipt["native_file_sha256"]
        if (type(expected) is not dict or not 5 <= len(expected) <= _MAX_FILE_COUNT
                or any(type(name) is not str or name.startswith(".")
                       or "/" in name or "\\" in name
                       or type(sha) is not str or not _DIGEST.fullmatch(sha)
                       for name, sha in expected.items())
                or receipt["python_exe_sha256"] != expected.get("python.exe")):
            raise EmbeddedRuntimeError("Invalid staged native file receipt")
        visible = {p.name for p in directory.iterdir()}
        if visible != set(expected) | {_RECEIPT, "holding"}:
            raise EmbeddedRuntimeError("Unexpected runtime file appeared")
        for name, expected_hash in expected.items():
            path = directory / name
            if path.is_symlink() or not path.is_file() or _sha_file(path) != expected_hash:
                raise EmbeddedRuntimeError("Installed Python native file modified")
        pth = directory / f"python3{pin.python_minor}._pth"
        if pth.read_bytes() != f"python3{pin.python_minor}.zip\n.\n".encode("ascii"):
            raise EmbeddedRuntimeError("Embedded Python import policy changed")
        code = directory / "holding"
        if code.is_symlink() or not code.is_dir() or {
                p.name for p in code.iterdir()} != set(pin.module_sha256):
            raise EmbeddedRuntimeError("Installed Python source inventory changed")
        for name, expected_hash in pin.module_sha256.items():
            path = code / name
            if path.is_symlink() or not path.is_file() or _sha_file(path) != expected_hash:
                raise EmbeddedRuntimeError("Installed Python source modified")
        return EmbeddedRuntimeStage(
            directory=directory, python_exe=directory / "python.exe",
            interpreter_sha256=receipt["python_exe_sha256"],
            source_sha256=tuple(sorted(pin.module_sha256.items())),
        )
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, EmbeddedRuntimeError):
            raise
        raise EmbeddedRuntimeError("Installed Python receipt or files unavailable") from exc
