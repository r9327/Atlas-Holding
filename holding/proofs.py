"""Verifier of externally signed Doctor/CI evidence for the exact code SHA.

The verifying secret and evidence directory MUST belong to a separately
privileged supervisor, inaccessible to the code-writing agent or its subprocesses.
This module verifies receipts only. It never generates a signature or a PASS.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re

SHA = re.compile(r"^[a-f0-9]{40}$")
NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}$")


class ProofError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedProof:
    mission_id: str
    sha: str
    verdict: str
    artifact: str
    proof_digest: str


class ProofVerifier:
    """Validates signed result + local artifact, not free-form runner strings."""

    def __init__(self, evidence_root: str | Path, secret: bytes):
        candidate = Path(evidence_root)
        if candidate.is_symlink():
            raise ProofError("Evidence root cannot be a symlink")
        self.root = candidate.resolve(strict=True)
        if not self.root.is_dir():
            raise ProofError("Evidence root must be an existing directory")
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ProofError("A protected 256-bit verifier secret is required")
        self.__key = secret

    def verify(self, receipt_name: str, *, mission_id: str, sha: str) -> VerifiedProof:
        if not NAME.fullmatch(receipt_name) or not SHA.fullmatch(sha):
            raise ProofError("Invalid receipt identifier or SHA")
        receipt_file = self.root / receipt_name
        if not receipt_file.is_file() or receipt_file.is_symlink():
            raise ProofError("No regular evidence receipt")
        # Read at most one byte past the limit. A corrupt multi-GB receipt
        # must never be loaded into the daemon's memory before rejection.
        with receipt_file.open("rb") as stream:
            raw = stream.read(32769)
        if not 0 < len(raw) <= 32768:
            raise ProofError("Receipt size out of bounds")
        try:
            envelope = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ProofError("Unreadable evidence receipt") from exc
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "mac"}:
            raise ProofError("Unsupported evidence envelope")
        payload = envelope["payload"]
        if not isinstance(payload, dict) or set(payload) != {
            "schema", "mission_id", "sha", "verdict", "runner", "artifact", "artifact_sha256"
        }:
            raise ProofError("Invalid evidence fields")
        if (type(payload["schema"]) is not int or payload["schema"] != 1
                or payload["mission_id"] != mission_id
                or payload["sha"] != sha
                or payload["verdict"] not in ("PASS", "FAIL")
                or payload["runner"] not in ("doctor_atlas", "github_certification")):
            raise ProofError("Evidence does not match candidate mission and SHA")
        if not isinstance(envelope["mac"], str):
            raise ProofError("Invalid authentication tag")
        message = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        expected_mac = hmac.new(self.__key, message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_mac, envelope["mac"]):
            raise ProofError("Unauthenticated Doctor/CI proof")
        name = payload["artifact"]
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ProofError("Unsafe artifact filename")
        artifact = self.root / name
        if not artifact.is_file() or artifact.is_symlink():
            raise ProofError("Missing or unsafe evidence artifact")
        if not isinstance(payload["artifact_sha256"], str):
            raise ProofError("Invalid evidence checksum")
        # Doctor artifacts may be large. Hash in bounded chunks so QA does
        # not cause unclassified Holding RAM spikes during regular coding.
        digest = hashlib.sha256()
        with artifact.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
        if not hmac.compare_digest(actual, payload["artifact_sha256"]):
            raise ProofError("Changed evidence artifact")
        return VerifiedProof(mission_id, sha, payload["verdict"], name,
                             hashlib.sha256(raw).hexdigest())

    def apply(self, store, receipt_name: str, *, mission_id: str) -> VerifiedProof:
        """Atomically settle QA_PENDING using verified signed evidence.

        Only the privileged supervisor may invoke this. Do not expose the
        verifier secret or direct SQLite access to an LLM or code worker.
        """
        from .state_store import StoreError
        with store._transaction():
            row = store.db.execute(
                "SELECT candidate_sha FROM missions WHERE id=? AND state='QA_PENDING'",
                (mission_id,),
            ).fetchone()
            if row is None or not SHA.fullmatch(row["candidate_sha"]):
                raise StoreError("No pending candidate to validate")
            proof = self.verify(receipt_name, mission_id=mission_id, sha=row["candidate_sha"])
            store.db.execute("""CREATE TABLE IF NOT EXISTS signed_proofs (
                mission_id TEXT PRIMARY KEY REFERENCES missions(id),
                candidate_sha TEXT NOT NULL,
                verdict TEXT NOT NULL,
                artifact TEXT NOT NULL,
                receipt_digest TEXT NOT NULL
            )""")
            store.db.execute(
                "INSERT INTO signed_proofs(mission_id,candidate_sha,verdict,artifact,receipt_digest) "
                "VALUES(?,?,?,?,?)",
                (mission_id, proof.sha, proof.verdict, proof.artifact, proof.proof_digest),
            )
            store._transition(mission_id, "QA_PENDING", "DONE" if proof.verdict == "PASS" else "BLOCKED",
                              "signed proof:" + proof.proof_digest)
            if proof.verdict == "PASS":
                store.db.execute("UPDATE missions SET evidence_sha=? WHERE id=?", (proof.sha, mission_id))
            return proof
