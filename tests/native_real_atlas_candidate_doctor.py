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
from holding.atlas_context_index import INDEX_PATH, render_atlas_context_index
from tests.native_real_atlas_doctor_probe import _git as inspect_git

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
SCOPE = ("README.md",)
LEVELS = ("plan", "check", "validate")
MODIFIED_PATHS = tuple(sorted((*SCOPE, INDEX_PATH)))


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


def _sync_disposable_context_index(destination: Path) -> None:
    """Mirror Atlas's committed AI Context without repo hooks or model edits."""
    target = destination / INDEX_PATH
    if (not target.is_file() or target.is_symlink()
            or target.parent.is_symlink() or target.stat().st_size > 512 * 1024):
        raise RuntimeError("Canonical tracked Atlas AI Context index required")
    try:
        tracked = _git(destination, "ls-files", "--error-unmatch", "--",
                       INDEX_PATH)
    except RuntimeError as exc:
        raise RuntimeError("Canonical tracked Atlas AI Context index required") from exc
    if tracked != INDEX_PATH:
        raise RuntimeError("Canonical tracked Atlas AI Context index required")
    staged_tree = _git(destination, "write-tree")
    if not _SHA.fullmatch(staged_tree):
        raise RuntimeError("No exact staged Git tree for AI Context")
    # Git-tree metadata, not content from the candidate/model.
    rendered = render_atlas_context_index(
        _git(destination, "ls-tree", staged_tree))
    with target.open("w", encoding="utf-8", newline="\n") as out:
        out.write(rendered)
        out.flush()
        os.fsync(out.fileno())
    _git(destination, "add", "--", INDEX_PATH)
    if target.read_text(encoding="utf-8") != rendered:
        raise RuntimeError("AI Context changed during staged synchronization")


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
    _sync_disposable_context_index(destination)
    if sorted(_git(destination, "diff", "--cached", "--name-only").splitlines()) != list(MODIFIED_PATHS):
        raise RuntimeError("Disposable candidate has missing/extra staged files")
    # Overrides prevent global repo identity or a local hook from taking over.
    _git(destination, "-c", "core.hooksPath=NUL",
         "-c", "user.name=Atlas Doctor Fixture",
         "-c", "user.email=atlas-doctor@invalid.example",
         "commit", "--quiet", "-m", "test: disposable real Atlas Doctor candidate",
         timeout=60)
    candidate = _git(destination, "rev-parse", "HEAD")
    if (not _SHA.fullmatch(candidate) or candidate == base_sha
            or _git(destination, "rev-parse", "HEAD^") != base_sha
            or tuple(sorted(_git(destination, "diff", "--name-only", base_sha, "HEAD").splitlines())) != MODIFIED_PATHS
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
        "modified_paths": list(MODIFIED_PATHS),
        "stages": stages,
        "independent_qa_pass": False,
        "doctor_full_certified": False,
        "github_push_permitted": False,
        "release_ready": False,
        "model_generated_candidate": False,
    }



def _integrity_failure_details(checks: object) -> tuple[str, ...]:
    """Export bounded failure identities only; never expose raw Doctor output.

    The Doctor envelope is authenticated separately. This adapter is NOT a
    second certification authority, and diagnostic text cannot grant a PASS.
    """
    if not isinstance(checks, list) or len(checks) > 60:
        return ()
    findings: list[str] = []
    for check in checks:
        if not isinstance(check, dict) or check.get("key") != "atlas_integrity":
            continue
        report = check.get("report")
        groups = report.get("groups") if isinstance(report, dict) else None
        if not isinstance(groups, dict) or len(groups) > 100:
            continue
        for name, item in groups.items():
            if len(findings) >= 16:
                break
            if (not isinstance(name, str)
                    or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", name)
                    or not isinstance(item, dict)):
                continue
            state = item.get("status")
            if state not in {"BLOCKED", "FAIL", "REVIEW", "MEASURED_ONLY_FAILED"}:
                continue
            commands = item.get("commands")
            if not isinstance(commands, list) or len(commands) > 20:
                continue
            codes = [
                str(cmd["exit_code"])
                for cmd in commands
                if isinstance(cmd, dict)
                and type(cmd.get("exit_code")) is int
                and 0 <= cmd["exit_code"] <= 255
            ]
            finding = f"guardian={name}:status={state}"
            if codes:
                finding += ":exit=" + ",".join(codes[:4])
            findings.append(finding)
            # Extract real unittest case identities from existing bounded
            # command tails, never echo arbitrary stdout/stderr or file paths.
            seen: set[str] = set()
            for cmd in commands[:4]:
                if not isinstance(cmd, dict):
                    continue
                tail = cmd.get("output_tail")
                if not isinstance(tail, str) or len(tail) > 20_000:
                    continue
                for case in re.findall(
                    r"(?:FAIL|ERROR): [A-Za-z0-9_]+ "
                    r"\((tests\.test_[A-Za-z0-9_.]{1,160})\)",
                    tail,
                ):
                    if case not in seen and len(seen) < 3 and len(findings) < 16:
                        seen.add(case)
                        findings.append(f"failed_test={name}:{case}")
    return tuple(findings)


def _review_diagnostics(report: dict, *, stage: str) -> tuple[str, ...]:
    """Extract only bounded non-authorizing reasons from a verified envelope."""
    if not isinstance(report, dict) or stage not in {"check", "validate"}:
        raise RuntimeError("Unrecognized Doctor diagnostic envelope")
    if (report.get("action") != stage
            or report.get("status") not in {"REVIEW", "FAIL", "BLOCKED"}):
        raise RuntimeError("Doctor refusal diagnostic status changed")
    execution = report.get("execution")
    if not isinstance(execution, dict):
        raise RuntimeError("Doctor review lacks execution evidence")
    state = execution.get("status")
    if not isinstance(state, str) or not 1 <= len(state) <= 64:
        raise RuntimeError("Doctor review has invalid execution state")
    checks = report.get("selected_checks")
    if not isinstance(checks, dict) or not isinstance(checks.get("tests"), list):
        raise RuntimeError("Doctor review lacks named test selection")
    selected = checks["tests"]
    if len(selected) > 40 or any(not isinstance(m, str) or not m or len(m) > 500 for m in selected):
        raise RuntimeError("Doctor review has invalid selected modules")
    messages = report.get("next_actions")
    if not isinstance(messages, list) or len(messages) > 10:
        raise RuntimeError("Doctor review has unbounded next actions")
    reason = []
    for item in messages[:4]:
        if not isinstance(item, str) or not 1 <= len(item) <= 1500:
            raise RuntimeError("Doctor review explanation is not bounded")
        reason.append(" ".join(item.split())[:180])
    detail = (_integrity_failure_details(execution.get("checks"))
              if stage == "validate" else ())
    return (
        "stage=" + stage,
        "execution=" + state,
        "selected_test_modules=" + str(len(selected)),
        *("reason=" + msg for msg in reason),
        *detail,
    )


def _inspect_real_doctor_review(runner: AtlasDoctorRunner, *,
                                stage: str, candidate: str, base: str,
                                expected_digest: str,
                                expected_status: str = "REVIEW") -> tuple[str, ...]:
    """Explain the first verified refusal; do not execute expensive Doctor twice.

    AtlasDoctorRunner already authenticated the observed exit code, worktree,
    exact source SHA, action, scope and digest before retaining these bytes.
    Only bounded, allowlisted diagnostics may leave this function.
    """
    if (stage not in {"check", "validate"}
            or expected_status not in {"REVIEW", "FAIL", "BLOCKED"}
            or not _SHA.fullmatch(candidate)
            or not _SHA.fullmatch(base)
            or not _DIGEST.fullmatch(expected_digest)):
        raise RuntimeError("Invalid bounded Doctor refusal request")
    if (_git(runner.root, "rev-parse", "HEAD") != candidate
            or runner._worktree_identity().dirty):
        raise RuntimeError("Doctor refusal checkout identity changed")
    raw = runner.take_verified_diagnostic(
        action=stage, candidate_sha=candidate,
        report_sha256=expected_digest,
    )
    report = json.loads(raw)
    if (not isinstance(report, dict)
            or report.get("action") != stage
            or report.get("status") != expected_status
            or report.get("exact_sha") != candidate
            or report.get("base_ref") != base
            or not isinstance(report.get("worktree"), dict)
            or report["worktree"].get("dirty") is not False
            or report["worktree"].get("dirty_digest") != ""):
        raise RuntimeError("Verified Doctor refusal changed between stages")
    return _review_diagnostics(report, stage=stage)

def _probe_main_import(runner: AtlasDoctorRunner, *, candidate: str) -> str:
    """A bounded, non-authorizing startup probe when Doctor STARTUP fails.

    Only an exception class and optional Python module identifier are emitted.
    Real Doctor evidence is never replaced by this narrow diagnostic.
    """
    if not _SHA.fullmatch(candidate):
        raise RuntimeError("Untrusted startup probe candidate SHA")
    if (_git(runner.root, "rev-parse", "HEAD") != candidate
            or runner._worktree_identity().dirty):
        raise RuntimeError("Startup probe checkout identity changed")
    script = (
        'import json,os,sys\n'
        'os.environ.setdefault("QT_QPA_PLATFORM","offscreen")\n'
        'try:\n'
        ' import main\n'
        'except Exception as exc:\n'
        ' print("ATLAS_IMPORT_PROBE="+json.dumps({"kind":"FAIL",'
        '"exception":type(exc).__name__,'
        '"module":getattr(exc,"name",None)}))\n'
        ' sys.exit(9)\n'
        'print("ATLAS_IMPORT_PROBE="+json.dumps({"kind":"PASS"}))\n'
    )
    try:
        result = runner._execute(
            (str(runner.interpreter), "-B", "-c", script),
            timeout=25, stdout_limit=4096,
        )
        lines = result.stdout.decode("utf-8", errors="strict").splitlines()
        records = [line.partition("ATLAS_IMPORT_PROBE=")[2]
                   for line in lines if line.startswith("ATLAS_IMPORT_PROBE=")]
        if len(records) != 1 or len(records[0]) > 320:
            verdict = "UNAVAILABLE"
        else:
            record = json.loads(records[0])
            if not isinstance(record, dict):
                verdict = "UNAVAILABLE"
            elif result.returncode == 0 and record == {"kind": "PASS"}:
                verdict = "IMPORT_OK_STARTUP_GUARD_STILL_FAILED"
            elif result.returncode == 9 and record.get("kind") == "FAIL":
                exception = record.get("exception")
                if exception not in {
                    "ModuleNotFoundError", "ImportError", "OSError",
                    "AttributeError", "RuntimeError", "TypeError", "ValueError",
                }:
                    exception = "OTHER_EXCEPTION"
                missing = record.get("module")
                if (type(missing) is str and len(missing) <= 100
                        and re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", missing)):
                    verdict = "IMPORT_FAILED:" + exception + ":module=" + missing
                else:
                    verdict = "IMPORT_FAILED:" + exception
            else:
                verdict = "UNAVAILABLE"
    except (OSError, RuntimeError, ValueError, UnicodeError,
            json.JSONDecodeError):
        verdict = "UNAVAILABLE"
    if (_git(runner.root, "rev-parse", "HEAD") != candidate
            or _git(runner.root, "status", "--porcelain", "--untracked-files=all")
            or runner._worktree_identity().dirty):
        raise RuntimeError("Startup probe changed the disposable candidate")
    return verdict


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
        timeout_seconds=180, max_tests=8,
    )
    collected = []
    for stage in LEVELS:
        advice = runner.run(
            stage, SCOPE, candidate_sha=candidate,
            base_ref=sha, structural=False,
            run_tests=(stage == "check"),
            capture_verified_refusal=(stage in {"check", "validate"}),
        )
        observation = _verify_advisory(advice, stage=stage, sha=candidate)
        if (stage == "plan" and advice.status != "PLANNED_LOCAL"):
            raise RuntimeError("Doctor plan requested REVIEW: no further QA stages authorized")
        collected.append(observation)
        print("NONRELEASE_REAL_ATLAS_CANDIDATE_STAGE="
              + stage + ":" + advice.status, flush=True)
        if stage in ("check", "validate") and advice.status in {"REVIEW", "FAIL", "BLOCKED"}:
            details = _inspect_real_doctor_review(
                runner, stage=stage, candidate=candidate, base=sha,
                expected_digest=advice.report_sha256,
                expected_status=advice.status)
            for item in details:
                print("NONRELEASE_DOCTOR_REFUSAL_DETAIL=" + item, flush=True)
            if (stage == "validate" and
                    any(item.startswith("guardian=STARTUP:") for item in details)):
                probe = _probe_main_import(runner, candidate=candidate)
                print("NONRELEASE_ATLAS_MAIN_IMPORT_PROBE=" + probe, flush=True)
        if advice.status in ("BLOCKED", "FAIL"):
            raise RuntimeError(f"Doctor {stage} blocked or failed: no QA continuation")
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
