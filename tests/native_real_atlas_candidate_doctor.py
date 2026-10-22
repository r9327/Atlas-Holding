"""Real Dof-Atlas Doctor on a truly disposable local candidate SHA.

NONRELEASE: no LLM execution, never touches real Dof-Atlas GitHub, no merge.
This is a policy/QA-integration probe, not proof that an AI corrected Atlas.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from holding.atlas_doctor_evidence import LocalPassport
from holding.atlas_doctor_runner import AtlasDoctorRunner
from tests.native_real_atlas_doctor_probe import _git as inspect_git

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
SCOPE = ("README.md",)
LEVELS = ("plan", "check", "validate")


def _runner_root() -> Path:
    if (os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != "r9327/Atlas-Holding"
            or os.environ.get("GITHUB_EVENT_NAME") != "pull_request"):
        raise RuntimeError("Only disposable Windows PR runners can try real Atlas QA")
    root = Path(os.environ.get("RUNNER_TEMP", ""))
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if (not root.is_absolute() or not root.is_dir() or root.is_symlink()
            or not run_id.isdigit()):
        raise RuntimeError("Canonical GitHub runner directory required")
    return root.resolve(strict=True)


def _git(root: Path, *argv: str, timeout: int = 30) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *argv], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, timeout=timeout,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0",
             "GIT_LFS_SKIP_SMUDGE": "1"},
    )
    if proc.returncode:
        # Source is public and this helper never handles credentials. Include
        # a bounded Git-native diagnostic, not unbounded output or generated code.
        detail = " ".join(proc.stderr.split())[-380:]
        operation = argv[0] if argv else "unknown"
        raise RuntimeError(
            f"Disposable Git {operation} failed (exit {proc.returncode}): {detail}"
        )
    return proc.stdout.strip()


def _same_canonical_checkout(expected: Path, observed: str) -> bool:
    # Native Git prints Windows separators/case differently from pathlib.
    # Only resolved filesystem identity is admissible, never string equality.
    if not isinstance(observed, str) or not observed.strip():
        return False
    try:
        target = Path(observed)
        return target.is_absolute() and target.resolve(strict=True) == expected.resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        return False


def _is_source_clean(root: Path, sha: str) -> None:
    if (not _SHA.fullmatch(sha)
            or _git(root, "rev-parse", "HEAD") != sha
            or _git(root, "status", "--porcelain", "--untracked-files=all")):
        raise RuntimeError("Pinned original Atlas checkout was modified")


def _prepare_candidate(source: Path, destination: Path, base_sha: str) -> str:
    """Clone independent Git objects, append one README test marker, commit."""
    if (not source.is_absolute() or not source.is_dir()
            or source.is_symlink() or not destination.is_absolute()
            or destination.exists() or not _SHA.fullmatch(base_sha)
            or source.resolve(strict=True) == destination):
        raise RuntimeError("Unsafe disposable Git candidate request")
    _is_source_clean(source, base_sha)
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout",
         str(source), str(destination)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=180, check=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0",
             "GIT_LFS_SKIP_SMUDGE": "1"},
    )
    _git(destination, "-c", "core.longpaths=true", "checkout", "--quiet", "--detach", base_sha, timeout=120)
    if (not (destination / "tools" / "atlas_doctor.py").is_file()
            or not (destination / "AI_ENTRYPOINT.md").is_file()
            or not (destination / ".ai" / "scopes" / "project_docs.yaml").is_file()):
        raise RuntimeError("Disposable checkout omitted canonical Doctor")
    if _git(destination, "rev-parse", "HEAD") != base_sha:
        raise RuntimeError("Disposable clone does not start at pinned base SHA")
    readme = destination / SCOPE[0]
    if (not readme.is_file() or readme.is_symlink()
            or readme.stat().st_size > 1024 * 1024):
        raise RuntimeError("Unsafe source README for isolated Doctor exercise")
    with readme.open("ab") as out:
        out.write(b"\n<!-- NONRELEASE Doctor candidate: isolated CI documentation fixture -->\n")
        out.flush()
        os.fsync(out.fileno())
    _git(destination, "add", "--", SCOPE[0])
    # Overrides prevent global repo identity or a local hook from taking over.
    _git(destination, "-c", "core.hooksPath=NUL",
         "-c", "user.name=Atlas Doctor Fixture",
         "-c", "user.email=atlas-doctor@invalid.example",
         "commit", "--quiet", "-m", "test: disposable real Atlas Doctor candidate",
         timeout=60)
    candidate = _git(destination, "rev-parse", "HEAD")
    if (not _SHA.fullmatch(candidate) or candidate == base_sha
            or _git(destination, "rev-parse", "HEAD^") != base_sha
            or _git(destination, "diff", "--name-only", base_sha, "HEAD") != SCOPE[0]
            or _git(destination, "status", "--porcelain", "--untracked-files=all")):
        raise RuntimeError("Candidate is not an isolated one-file clean Git commit")
    _is_source_clean(source, base_sha)
    return candidate


def _verify_advisory(passport: LocalPassport, *, stage: str, sha: str) -> dict:
    """No local Doctor status is a signed QA or merge authorization."""
    if (not isinstance(passport, LocalPassport) or stage not in LEVELS
            or not _SHA.fullmatch(sha) or passport.candidate_sha != sha
            or passport.action != stage
            or not _DIGEST.fullmatch(passport.report_sha256)
            or passport.certified_full is not False or passport.dirty is not False
            or passport.dirty_digest != ""):
        raise RuntimeError("Stale, dirty or uncertified Doctor candidate evidence")
    allowed = {
        "plan": {"PLANNED_LOCAL", "REVIEW"},
        "check": {"CHECKED_LOCAL", "REVIEW", "BLOCKED", "FAIL"},
        "validate": {"VALIDATED_LOCAL", "REVIEW", "BLOCKED", "FAIL"},
    }
    if passport.status not in allowed[stage]:
        raise RuntimeError("Doctor stage returned a forged or unknown status")
    if stage == "plan" and passport.tests_executed:
        raise RuntimeError("A Doctor plan must not execute any tests")
    if stage == "check" and passport.status == "CHECKED_LOCAL" and not passport.tests_executed:
        raise RuntimeError("Doctor claimed CHECKED without selected tests")
    if len(passport.tests_executed) > 100:
        raise RuntimeError("Unbounded Doctor test selection")
    return {
        "stage": stage,
        "state": passport.status,
        "doctor_report_sha256": passport.report_sha256,
        "test_modules_reported": len(passport.tests_executed),
        "local_advisory_only": True,
    }


def _candidate_summary(*, source_sha: str, candidate_sha: str,
                       stages: list[dict]) -> dict:
    if (not _SHA.fullmatch(source_sha) or not _SHA.fullmatch(candidate_sha)
            or source_sha == candidate_sha or not isinstance(stages, list)
            or len(stages) != len(LEVELS)
            or [s.get("stage") for s in stages if isinstance(s, dict)] != list(LEVELS)
            or any(s.get("local_advisory_only") is not True
                   or not _DIGEST.fullmatch(s.get("doctor_report_sha256", ""))
                   for s in stages)):
        raise RuntimeError("Incomplete real Doctor candidate session")
    if (stages[0]["state"] != "PLANNED_LOCAL"
            or any(step["state"] in ("BLOCKED", "FAIL") for step in stages)):
        raise RuntimeError("Doctor review/blocker cannot produce a complete candidate QA session")
    return {
        "schema": 1,
        "kind": "NONRELEASE_REAL_ATLAS_DISPOSABLE_CANDIDATE",
        "source_repository": "r9327/Dof-Atlas",
        "source_sha": source_sha,
        "candidate_sha": candidate_sha,
        "modified_paths": list(SCOPE),
        "stages": stages,
        "independent_qa_pass": False,
        "doctor_full_certified": False,
        "github_push_permitted": False,
        "release_ready": False,
        "model_generated_candidate": False,
    }


def run(source: Path, sha: str) -> None:
    temp = _runner_root()
    source = source.resolve(strict=True)
    if (not _SHA.fullmatch(sha) or not source.is_dir() or source.is_symlink()
            or not (source / "tools" / "atlas_doctor.py").is_file()
            or not _same_canonical_checkout(source, inspect_git(source, "rev-parse", "--show-toplevel"))):
        raise RuntimeError("A true SHA-pinned Atlas checkout is required")
    _is_source_clean(source, sha)
    destination = temp / ("atlas-doctor-candidate-" + os.environ["GITHUB_RUN_ID"])
    candidate = _prepare_candidate(source, destination, sha)
    executable = shutil.which("git")
    if not executable:
        raise RuntimeError("Git CLI unavailable")
    runner = AtlasDoctorRunner(
        destination, Path(sys.executable).resolve(), Path(executable).resolve(),
        timeout_seconds=180, max_tests=4,
    )
    collected = []
    for stage in LEVELS:
        advice = runner.run(
            stage, SCOPE, candidate_sha=candidate,
            base_ref=sha, structural=False,
            run_tests=(stage == "check"),
        )
        observation = _verify_advisory(advice, stage=stage, sha=candidate)
        if (stage == "plan" and advice.status != "PLANNED_LOCAL"):
            raise RuntimeError("Doctor plan requested REVIEW: no further QA stages authorized")
        if advice.status in ("BLOCKED", "FAIL"):
            raise RuntimeError(f"Doctor {stage} blocked or failed: no QA continuation")
        collected.append(observation)
        print("NONRELEASE_REAL_ATLAS_CANDIDATE_STAGE="
              + stage + ":" + advice.status, flush=True)
        if _git(destination, "rev-parse", "HEAD") != candidate:
            raise RuntimeError("Doctor modified local candidate HEAD")
        if _git(destination, "status", "--porcelain", "--untracked-files=all"):
            raise RuntimeError("Doctor left the candidate worktree dirty")
        _is_source_clean(source, sha)
    result = _candidate_summary(
        source_sha=sha, candidate_sha=candidate, stages=collected)
    payload = (json.dumps(result, sort_keys=True, separators=(",", ":"))
               + "\n").encode()
    if len(payload) > 4096:
        raise RuntimeError("Doctor test candidate summary is oversized")
    target = temp / ("atlas-candidate-doctor-" + os.environ["GITHUB_RUN_ID"] + ".json")
    with target.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    print("NONRELEASE_REAL_ATLAS_CANDIDATE_THREE_ADVISORIES_RECORDED", flush=True)
    print("Candidate SHA: " + candidate, flush=True)
    print("Read-only source SHA: " + sha, flush=True)
    print("Nonrelease session SHA256: " + hashlib.sha256(payload).hexdigest(),
          flush=True)
    print("Independent QA/Doctor FULL/GitHub push: NOT_CERTIFIED/NOT_PERMITTED",
          flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas-root", type=Path, required=True)
    parser.add_argument("--expected-atlas-sha", required=True)
    args = parser.parse_args()
    run(args.atlas_root, args.expected_atlas_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
