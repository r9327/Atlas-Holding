"""Opt-in read-only REAL Dof-Atlas Doctor probe (not a certification).

The candidate is the separately checked-out, SHA-pinned public Dof-Atlas
repository on a disposable GitHub Windows runner. The Holding checkout and the
canonical Atlas repository are never altered, and there is no push or merge.
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

_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
# These paths are defined by the pinned Atlas source's scope manifests.
# Never accept arbitrary model-supplied paths for a privileged probe.
SCOPE_PATHS = {
    "unowned": "app/constants.py",
    "owned_home": "app/pages/home_page.py",
}


def _approved_scope(key: str) -> tuple[str, ...]:
    if not isinstance(key, str) or key not in SCOPE_PATHS:
        raise RuntimeError("Unapproved real Atlas Doctor scope")
    return (SCOPE_PATHS[key],)


def _verify_local_plan(passport: LocalPassport, expected_sha: str) -> str:
    """Never turn a local Doctor plan into a test PASS or release certificate."""
    if (not isinstance(passport, LocalPassport)
            or not _SHA40.fullmatch(expected_sha)
            or passport.candidate_sha != expected_sha
            or passport.action != "plan"
            or passport.status not in {"PLANNED_LOCAL", "REVIEW"}
            or passport.dirty is not False
            or passport.dirty_digest != ""
            or passport.tests_executed != ()
            or passport.certified_full is not False
            or not re.fullmatch(r"[0-9a-f]{64}", passport.report_sha256)):
        raise RuntimeError("Doctor plan is not a clean exact-SHA local advisory")
    return ("NONRELEASE_REAL_ATLAS_DOCTOR_PLAN_READY"
            if passport.status == "PLANNED_LOCAL"
            else "NONRELEASE_REAL_ATLAS_DOCTOR_REVIEW_NOT_READY")


def _verify_pinned_scope_decision(scope: str, status: str) -> None:
    """The PINNED Atlas source declares constants.py UNOWNED.

    If it suddenly becomes READY, treat it as unexpected policy drift rather
    than silently giving this demonstration a green editing authorization.
    A declared owner is necessary but not sufficient for a READY decision.
    """
    _approved_scope(scope)
    if status not in {"REVIEW", "PLANNED_LOCAL"}:
        raise RuntimeError("Invalid real Atlas Doctor planning status")
    if scope == "unowned" and status != "REVIEW":
        raise RuntimeError("Unowned Atlas path was unexpectedly authorized")


def _evidence_receipt(scope: str, expected_sha: str,
                      passport: LocalPassport) -> dict:
    """Machine-readable, strictly NONRELEASE independent Doctor plan evidence."""
    _verify_pinned_scope_decision(scope, passport.status)
    decision = _verify_local_plan(passport, expected_sha)
    return {
        "schema": 1,
        "kind": "NONRELEASE_ATLAS_DOCTOR_REAL_PLAN",
        "source_repository": "r9327/Dof-Atlas",
        "source_sha": expected_sha,
        "scope": scope,
        "path": _approved_scope(scope)[0],
        "status": passport.status,
        "decision_marker": decision,
        "doctor_report_sha256": passport.report_sha256,
        "doctor_tests_executed": False,
        "doctor_full_certified": False,
        "github_push_permitted": False,
        "release_ready": False,
    }


def _git(root: Path, *args: str) -> str:
    command = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, capture_output=True, text=True, timeout=25,
        encoding="utf-8", errors="replace",
    )
    return command.stdout.strip()


def run(atlas_root: Path, exact_sha: str, *, scope: str = "unowned") -> None:
    if (os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != "r9327/Atlas-Holding"
            or os.environ.get("GITHUB_EVENT_NAME") != "pull_request"):
        raise RuntimeError("Requires a disposable Windows PR CI runner")
    if not _SHA40.fullmatch(exact_sha):
        raise RuntimeError("Required Dof-Atlas source SHA must be exact")
    paths = _approved_scope(scope)
    root = atlas_root.resolve(strict=True)
    if (not atlas_root.is_absolute() or atlas_root.is_symlink()
            or root != atlas_root
            or not (root / "AI_ENTRYPOINT.md").is_file()
            or not (root / "tools" / "atlas_doctor.py").is_file()
            or not (root / paths[0]).is_file()):
        raise RuntimeError("Atlas checkout is not a canonical project root")
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise RuntimeError("Git checkout root differs from expected Atlas path")
    if _git(root, "rev-parse", "HEAD") != exact_sha:
        raise RuntimeError("Checkout is not the pinned Dof-Atlas source SHA")
    if _git(root, "remote", "get-url", "origin").casefold().removesuffix(".git") != (
            "https://github.com/r9327/dof-atlas"):
        raise RuntimeError("Checkout does not belong to the canonical Atlas repository")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("Dirty Atlas checkout is not admissible for Doctor plan")
    git = shutil.which("git")
    if not git:
        raise RuntimeError("Trusted Git for Windows is required")
    runner = AtlasDoctorRunner(
        root, Path(sys.executable).resolve(), Path(git).resolve(),
        timeout_seconds=180, max_tests=4,
    )
    result = runner.run("plan", paths, candidate_sha=exact_sha,
                        base_ref="HEAD", structural=False, run_tests=False)
    decision = _verify_local_plan(result, exact_sha)
    _verify_pinned_scope_decision(scope, result.status)
    if result.status == "REVIEW":
        # A separate bounded, still read-only diagnostic invocation. The
        # first result is authoritative for the local passport; the second
        # must match SHA, action, scope and REVIEW before its notes are shown.
        import json
        from holding.atlas_doctor_evidence import consume_doctor_report
        command = (
            str(Path(sys.executable).resolve()), "-B", "-m",
            "tools.atlas_doctor", "--json", "agent", "plan", *paths,
            "--base-ref", "HEAD", "--max-tests", "4",
            "--timeout-seconds", "180",
        )
        diagnostic = runner._execute(command, timeout=195)
        checked = consume_doctor_report(
            diagnostic.stdout, observed_exit_code=diagnostic.returncode,
            expected_sha=exact_sha, expected_action="plan",
            expected_base_ref="HEAD", expected_paths=paths,
            expected_dirty=False, expected_dirty_digest="", structural=False,
        )
        if checked.status != "REVIEW":
            raise RuntimeError("Doctor changed decision during diagnostics")
        parsed = json.loads(diagnostic.stdout)
        messages = parsed.get("next_actions")
        if not isinstance(messages, list) or len(messages) > 10:
            raise RuntimeError("Doctor REVIEW lacks bounded next actions")
        for index, message in enumerate(messages[:5], 1):
            if not isinstance(message, str) or len(message) > 1500:
                raise RuntimeError("Invalid Doctor REVIEW explanation")
            # The project is public, but never stream raw Doctor JSON or code.
            line = " ".join(message.split())[:320]
            print(f"NONRELEASE_DOCTOR_REVIEW_ACTION_{index}: {line}", flush=True)
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("Doctor plan unexpectedly modified the Atlas checkout")
    if _git(root, "rev-parse", "HEAD") != exact_sha:
        raise RuntimeError("Doctor plan changed the candidate SHA")
    print("REAL_ATLAS_DOCTOR_SCOPE=" + scope + ":" + paths[0], flush=True)
    print(decision, flush=True)
    print("Real Dof-Atlas source SHA: " + exact_sha, flush=True)
    print("Read-only Doctor report SHA256: " + result.report_sha256, flush=True)
    print("Doctor tests: NOT_EXECUTED; local full certification: NOT_CERTIFIED",
          flush=True)
    print("Atlas push/PR mutation: NOT_PERMITTED", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas-root", type=Path, required=True)
    parser.add_argument("--expected-atlas-sha", required=True)
    parser.add_argument("--scope", choices=tuple(SCOPE_PATHS), default="unowned")
    args = parser.parse_args()
    run(args.atlas_root, args.expected_atlas_sha, scope=args.scope)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
