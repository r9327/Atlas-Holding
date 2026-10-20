"""Separate privileged issuer for FULL GitHub proof receipts consumed by Holding Core.

This signer independently fetches GitHub evidence in the same operation. It
never signs a local Doctor report, accepts a model-supplied PASS, or reads PR
artifacts as trusted input. The Windows installer must enforce key/data ACLs
and process identity separation BEFORE providing an isolation witness.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from typing import Callable

from .github_full_evidence import (
    FullEvidence, GitHubEvidenceClient, GitHubEvidenceError, observe_full_pr,
)

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class IssuedReceipt:
    mission_id: str
    sha: str
    receipt: str
    artifact: str
    run_id: int


def _json_bytes(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _put_immutable(path: Path, data: bytes) -> None:
    """Create-only, mode 0600, crash-restart compatible, never overwrite proof."""
    if path.is_symlink():
        raise GitHubEvidenceError("Symlinked signed evidence target refused")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        # A restart after writing only the artifact is safe and idempotent.
        if not path.is_file() or path.stat().st_size > 32768:
            raise GitHubEvidenceError("Conflicting or oversized existing evidence")
        with path.open("rb") as stream:
            existing = stream.read(32769)
        if not hmac.compare_digest(hashlib.sha256(existing).digest(),
                                   hashlib.sha256(data).digest()):
            raise GitHubEvidenceError("Existing evidence disagrees with new attestation")
        return
    with os.fdopen(fd, "wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


class GitHubProofIssuer:
    """Use ONLY in a trusted isolated service with validated Windows ACLs.

    isolation_check must be an independent OS identity/ACL verifier installed
    by the bootstrapper, never a value derived from roadmap/agent input.
    """

    def __init__(self, client: GitHubEvidenceClient, secret: bytes,
                 evidence_root: Path, *, isolation_check: Callable[[], bool]):
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise GitHubEvidenceError("A separate 256-bit signing key is mandatory")
        if not evidence_root.is_absolute() or evidence_root.is_symlink() or not evidence_root.is_dir():
            raise GitHubEvidenceError("Trusted evidence directory must exist and not be a symlink")
        if not callable(isolation_check):
            raise GitHubEvidenceError("Trusted OS isolation witness is mandatory")
        self.client = client
        self._secret = secret
        self.root = evidence_root
        self._isolation_check = isolation_check

    def issue(self, *, mission_id: str, pr_number: int,
              expected_sha: str) -> IssuedReceipt:
        if (not isinstance(mission_id, str) or not _ID.fullmatch(mission_id)
                or not isinstance(expected_sha, str)
                or not _SHA.fullmatch(expected_sha)):
            raise GitHubEvidenceError("Unsafe mission identity or candidate SHA")
        if not self._isolation_check():
            raise GitHubEvidenceError("Trusted OS separation and ACLs have not been verified")
        full = observe_full_pr(self.client, pr_number)
        if (full.repository != self.client.repo or full.pr_number != pr_number
                or full.status != "FULL_VERIFIED_BY_GITHUB"
                or full.candidate_sha != expected_sha):
            raise GitHubEvidenceError("Independent GitHub identity or SHA mismatch")
        # This matches Holding Core's receipt_name() without importing the
        # other Draft PR before their dependencies are merged.
        identity = hashlib.sha256(mission_id.encode("utf-8")).hexdigest()[:20]
        receipt_name = f"qa-{identity}-{expected_sha}.json"
        artifact_name = f"full-{pr_number}-{expected_sha[:20]}.json"
        if any(len(name) > 120 for name in (receipt_name, artifact_name)):
            raise GitHubEvidenceError("Unexpected evidence filename")
        artifact = _json_bytes({
            "schema": 1, "source": "github_api", "repo": full.repository,
            "pr": full.pr_number, "candidate_sha": full.candidate_sha,
            "run_id": full.run_id, "job_id": full.job_id,
            "verdict": "PASS_FULL_AUTHENTICATED",
        })
        payload = {
            "schema": 1, "mission_id": mission_id, "sha": expected_sha,
            "verdict": "PASS", "runner": "github_certification",
            "artifact": artifact_name,
            "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
        }
        signature = hmac.new(self._secret, _json_bytes(payload), hashlib.sha256).hexdigest()
        envelope = _json_bytes({"payload": payload, "mac": signature})
        if len(envelope) > 32768:
            raise GitHubEvidenceError("Signed receipt is oversized")
        # Publish artifact first. A crash before the receipt does not grant
        # permission. Re-running identical evidence is safe; changed evidence
        # for the same candidate SHA requires explicit human investigation.
        _put_immutable(self.root / artifact_name, artifact)
        _put_immutable(self.root / receipt_name, envelope)
        return IssuedReceipt(mission_id, expected_sha, receipt_name,
                             artifact_name, full.run_id)
