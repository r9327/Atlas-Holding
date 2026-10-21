"""Stage a three-binary Windows candidate without declaring it a release.

Only a trusted runner calls this after collecting actual frozen binaries. An
untrusted manifest cannot authorize copying a missing or modified executable.
Stage is side-by-side, never destructive to the previous installation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Mapping

from .release_evidence import (
    ArtifactObservation, ReleaseEvidenceError, _REQUIRED_RUNTIME_FILES,
    _REQUIRED_UPSTREAM, _unique_object, observe_native_release_files,
)

_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class NativeCandidateStage:
    directory: Path
    index_sha256: str
    source_sha: str
    files: tuple[tuple[str, str, int], ...]
    release_ready: bool = False


def stage_native_candidate(
    source: Path, destination: Path, *,
    source_sha: str, upstream_shas: Mapping[str, str],
) -> NativeCandidateStage:
    """Make an immutable-intent staging folder with rechecked real file hashes.

    This is NOT a Windows installer, signature, ACL proof or an E2E test.
    Destination must be new; any missing/tampered file fails closed.
    """
    if (not isinstance(source, Path) or not source.is_absolute()
            or not isinstance(destination, Path) or not destination.is_absolute()
            or not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha)
            or not isinstance(upstream_shas, Mapping)):
        raise ReleaseEvidenceError("Invalid trusted native staging request")
    upstream = dict(upstream_shas)
    if (not _REQUIRED_UPSTREAM.issubset(upstream)
            or any(type(k) is not str or type(v) is not str
                   or not _SHA.fullmatch(v) for k, v in upstream.items())):
        raise ReleaseEvidenceError("Required upstream SHA pins unavailable")
    parent = destination.parent
    try:
        if (source.is_symlink() or source.resolve(strict=True) != source
                or parent.is_symlink() or parent.resolve(strict=True) != parent
                or source == destination or destination.exists()
                or destination.is_symlink() or source == parent):
            raise ReleaseEvidenceError("Unsafe or already published candidate path")
        before = observe_native_release_files(source)
    except OSError as exc:
        raise ReleaseEvidenceError("Trusted staging path unavailable") from exc

    # O_EXCL lock prevents two cooperative builds from racing on the same
    # destination. Never replace files, a previous candidate or a release.
    lock = parent / ("." + destination.name + ".staging-lock")
    fd = None
    acquired = False
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        acquired = True
        os.close(fd)
        fd = None
        with tempfile.TemporaryDirectory(prefix=".native-stage-", dir=parent) as work:
            folder = Path(work)
            for filename in sorted(_REQUIRED_RUNTIME_FILES):
                src = source / filename
                dst = folder / filename
                if src.is_symlink() or src.resolve(strict=True) != src:
                    raise ReleaseEvidenceError("Source artifact changed or linked")
                with src.open("rb") as reader, dst.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, 1024 * 1024)
            after = observe_native_release_files(folder)
            if after != before or observe_native_release_files(source) != before:
                raise ReleaseEvidenceError("Native candidate changed while staging")
            entries = [
                {"name": name, "sha256": observed.sha256,
                 "size_bytes": observed.size_bytes}
                for name, observed in sorted(after.items())
            ]
            index = {
                "schema_version": 1,
                "source_sha": source_sha,
                "upstream_shas": dict(sorted(upstream.items())),
                "artifacts": entries,
                "release_ready": False,
            }
            payload = (json.dumps(
                index, sort_keys=True, ensure_ascii=True,
                separators=(",", ":"), allow_nan=False,
            ) + "\n").encode("utf-8")
            if len(payload) > 64 * 1024:
                raise ReleaseEvidenceError("Candidate index exceeds size limit")
            with (folder / "candidate-index.json").open("xb") as writer:
                writer.write(payload)
            if destination.exists() or destination.is_symlink():
                raise ReleaseEvidenceError("Native candidate destination appeared")
            os.rename(folder, destination)
            return NativeCandidateStage(
                destination, hashlib.sha256(payload).hexdigest(), source_sha,
                tuple((name, value.sha256, value.size_bytes)
                      for name, value in sorted(after.items())),
            )
    except FileExistsError as exc:
        raise ReleaseEvidenceError("Native candidate already staging") from exc
    finally:
        if fd is not None:
            os.close(fd)
        # A failed run must not leave an invisible lock behind. Only the
        # trusted single-runner staging flow can manipulate this directory.
        if acquired:
            lock.unlink(missing_ok=True)


def verify_native_candidate(
    directory: Path, *, expected_source_sha: str,
    expected_upstream_shas: Mapping[str, str],
) -> NativeCandidateStage:
    """Revalidate staged binaries against a *trusted* build identity.

    The index is an untrusted statement, not a release certificate; both the
    caller's SHA expectations and real on-disk artifact bytes must agree.
    """
    if (not isinstance(directory, Path) or not directory.is_absolute()
            or not isinstance(expected_source_sha, str)
            or not _SHA.fullmatch(expected_source_sha)
            or not isinstance(expected_upstream_shas, Mapping)):
        raise ReleaseEvidenceError("Invalid trusted candidate verification")
    expected = dict(expected_upstream_shas)
    if (not _REQUIRED_UPSTREAM.issubset(expected)
            or any(type(key) is not str or type(sha) is not str
                   or not _SHA.fullmatch(sha)
                   for key, sha in expected.items())):
        raise ReleaseEvidenceError("Incomplete native candidate upstream identity")
    index_file = directory / "candidate-index.json"
    try:
        if (directory.is_symlink() or directory.resolve(strict=True) != directory
                or index_file.is_symlink()
                or index_file.resolve(strict=True) != index_file
                or index_file.stat().st_size > 64 * 1024):
            raise ReleaseEvidenceError("Unsafe candidate index path")
        raw = index_file.read_bytes()
        data = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, OSError, UnicodeError, RecursionError) as exc:
        raise ReleaseEvidenceError("Native candidate index unavailable") from exc
    if (type(data) is not dict or set(data) != {
            "schema_version", "source_sha", "upstream_shas", "artifacts",
            "release_ready",
        } or type(data["schema_version"]) is not int
            or data["schema_version"] != 1
            or data["source_sha"] != expected_source_sha
            or data["upstream_shas"] != expected
            or data["release_ready"] is not False):
        raise ReleaseEvidenceError("Native candidate is stale or falsely certified")
    artifacts = data["artifacts"]
    if type(artifacts) is not list or len(artifacts) != len(_REQUIRED_RUNTIME_FILES):
        raise ReleaseEvidenceError("Native candidate runtime list is incomplete")
    declared = {}
    for artifact in artifacts:
        if type(artifact) is not dict or set(artifact) != {
                "name", "sha256", "size_bytes",
        }:
            raise ReleaseEvidenceError("Invalid candidate artifact record")
        name, sha, size = (
            artifact["name"], artifact["sha256"], artifact["size_bytes"],
        )
        if (type(name) is not str or name not in _REQUIRED_RUNTIME_FILES
                or name in declared or type(sha) is not str
                or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)
                or type(size) is not int or not 0 < size <= 20 * 1024**3):
            raise ReleaseEvidenceError("Invalid observed runtime identity")
        declared[name] = ArtifactObservation(sha, size)
    if declared != observe_native_release_files(directory):
        raise ReleaseEvidenceError("Native candidate binaries changed after staging")
    return NativeCandidateStage(
        directory, hashlib.sha256(raw).hexdigest(), expected_source_sha,
        tuple((name, value.sha256, value.size_bytes)
              for name, value in sorted(declared.items())),
    )
