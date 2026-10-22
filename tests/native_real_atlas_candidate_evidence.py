"""Independent, nonprivileged verifier for the disposable real Atlas Doctor run.

A structurally valid receipt remains advisory. It cannot sign a QA PASS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_STATES = {
    "plan": frozenset({"PLANNED_LOCAL", "REVIEW"}),
    "check": frozenset({"CHECKED_LOCAL", "REVIEW", "BLOCKED", "FAIL"}),
    "validate": frozenset({"VALIDATED_LOCAL", "REVIEW", "BLOCKED", "FAIL"}),
}
_EXACT_KEYS = {
    "schema", "kind", "source_repository", "source_sha", "candidate_sha",
    "modified_paths", "stages", "independent_qa_pass",
    "doctor_full_certified", "github_push_permitted", "release_ready",
    "model_generated_candidate",
}


class EvidenceError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise EvidenceError("Duplicate JSON keys in model/Doctor evidence")
        value[key] = item
    return value


def verify_disposable_candidate(raw: bytes, *, source_sha: str) -> str:
    if (not isinstance(raw, bytes) or not 1 <= len(raw) <= 4096
            or not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha)):
        raise EvidenceError("Invalid bounded source or evidence bytes")
    try:
        receipt = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as exc:
        raise EvidenceError("Malformed Doctor evidence JSON") from exc
    if (not isinstance(receipt, dict) or set(receipt) != _EXACT_KEYS
            or type(receipt["schema"]) is not int or receipt["schema"] != 1
            or receipt["kind"] != "NONRELEASE_REAL_ATLAS_DISPOSABLE_CANDIDATE"
            or receipt["source_repository"] != "r9327/Dof-Atlas"
            or receipt["source_sha"] != source_sha
            or not isinstance(receipt["candidate_sha"], str)
            or not _SHA.fullmatch(receipt["candidate_sha"])
            or receipt["candidate_sha"] == source_sha
            or receipt["modified_paths"] != ["README.md"]
            or receipt["independent_qa_pass"] is not False
            or receipt["doctor_full_certified"] is not False
            or receipt["github_push_permitted"] is not False
            or receipt["release_ready"] is not False
            or receipt["model_generated_candidate"] is not False):
        raise EvidenceError("Invalid exact-SHA candidate or unauthorized QA claim")
    stages = receipt["stages"]
    if not isinstance(stages, list) or len(stages) != 3:
        raise EvidenceError("Incomplete local Doctor stages")
    for (expected_name, allowed), item in zip(_STATES.items(), stages):
        if (not isinstance(item, dict)
                or set(item) != {"stage", "state", "doctor_report_sha256",
                                 "test_modules_reported", "local_advisory_only"}
                or item["stage"] != expected_name or item["state"] not in allowed
                or item["local_advisory_only"] is not True
                or not isinstance(item["doctor_report_sha256"], str)
                or not _DIGEST.fullmatch(item["doctor_report_sha256"])
                or type(item["test_modules_reported"]) is not int
                or not 0 <= item["test_modules_reported"] <= 100
                or expected_name == "plan" and item["test_modules_reported"] != 0
                or expected_name == "check" and item["state"] == "CHECKED_LOCAL"
                    and item["test_modules_reported"] == 0):
            raise EvidenceError("Stale, forged or incomplete Doctor step")
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--expected-source-sha", required=True)
    args = parser.parse_args()
    source = args.receipt
    if not source.is_absolute() or source.is_symlink() or not source.is_file():
        raise EvidenceError("Exact regular candidate evidence file required")
    if source.stat().st_size > 4096:
        raise EvidenceError("Oversized Doctor receipt")
    digest = verify_disposable_candidate(
        source.read_bytes(), source_sha=args.expected_source_sha)
    print("NONRELEASE_INDEPENDENT_DISPOSABLE_DOCTOR_RECEIPT_PASS", flush=True)
    print("Doctor receipt SHA256: " + digest, flush=True)
    print("Actual Doctor scope: local only; independent QA: NOT_EXECUTED",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
