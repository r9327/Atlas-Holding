"""Real pinned Dof-Atlas acceptance of Holding's no-hook AI Context commit.

Opt-in nonrelease Windows CI. Never pushes, edits source checkout, or
provides Doctor FULL certification.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys

from holding.git_worktree import GitWorktrees

_SHA = re.compile(r"[0-9a-f]{40}\Z")
BASE_REPO = "r9327/Dof-Atlas"


def git(root: Path, *args: str, timeout: int = 25) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True, capture_output=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=timeout, check=True,
        env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1",
             "GIT_TERMINAL_PROMPT": "0"},
    )
    return result.stdout.strip()


def run(source: Path, sha: str) -> None:
    runner_temp = Path(os.environ.get("RUNNER_TEMP", ""))
    if (os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != "r9327/Atlas-Holding"
            or os.environ.get("GITHUB_EVENT_NAME") != "pull_request"
            or not _SHA.fullmatch(sha)
            or not runner_temp.is_absolute() or not runner_temp.is_dir()
            or runner_temp.is_symlink()):
        raise RuntimeError("Exact SHA on disposable Windows PR runner required")
    source = source.resolve(strict=True)
    if (not source.is_dir() or source.is_symlink()
            or git(source, "rev-parse", "HEAD") != sha
            or git(source, "status", "--porcelain", "--untracked-files=all")):
        raise RuntimeError("Untrusted or dirty original Dof-Atlas checkout")
    if not (source / "tools/ai_context.py").is_file():
        raise RuntimeError("Canonical Atlas AI Context implementation is absent")
    # GitHub checkout pins detached HEAD; add a LOCAL branch ref only if
    # absent. Never checkout/change the source's HEAD or contact GitHub.
    local = subprocess.run(
        ["git", "-C", str(source), "show-ref", "--verify", "refs/heads/main"],
        capture_output=True, text=True, check=False, timeout=15,
    )
    if local.returncode:
        git(source, "branch", "main", sha)
    elif git(source, "rev-parse", "refs/heads/main") != sha:
        raise RuntimeError("Local main does not match exact pinned source SHA")
    tmp = runner_temp.resolve(strict=True)
    workspace = tmp / ("holding-index-worktrees-" + os.environ["GITHUB_RUN_ID"])
    worktree = GitWorktrees(source, workspace, BASE_REPO)
    before = git(source, "rev-parse", "HEAD")
    tree = worktree.create("real-atlas-index")
    document = tree.path / "README.md"
    with document.open("ab") as output:
        output.write(b"\n<!-- NONRELEASE trusted Git index acceptance only -->\n")
        output.flush()
        os.fsync(output.fileno())
    candidate = worktree.commit("real-atlas-index", ("README.md",),
                                "test: trusted generated Atlas index proof")
    if not _SHA.fullmatch(candidate) or candidate == sha:
        raise RuntimeError("No actual disposable Atlas commit created")
    changed = set(git(tree.path, "show", "--pretty=format:",
                      "--name-only", "HEAD").splitlines())
    if changed != {"README.md", ".ai/context_index.json"}:
        raise RuntimeError("Trusted generated index was not in exact commit scope")
    check = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-m", "tools.ai_context", "check"],
        cwd=tree.path, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", check=False, timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
             "GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"},
    )
    if check.returncode != 0 or "AI context index: PASS" not in check.stdout:
        raise RuntimeError("Real Dof-Atlas AI Context refused Holding-generated index")
    if (git(tree.path, "rev-parse", "HEAD") != candidate
            or git(tree.path, "status", "--porcelain", "--untracked-files=all")):
        raise RuntimeError("Canonical AI Context check modified disposable candidate")
    if (git(source, "rev-parse", "HEAD") != before
            or git(source, "status", "--porcelain", "--untracked-files=all")):
        raise RuntimeError("Original Dof-Atlas checkout was changed")
    index = tree.path / ".ai/context_index.json"
    if index.is_symlink() or index.stat().st_size > 512 * 1024:
        raise RuntimeError("Unsafe generated index source")
    print("NONRELEASE_REAL_ATLAS_AI_CONTEXT_CANONICAL_CHECK_PASS", flush=True)
    print("Dof-Atlas base SHA: " + sha, flush=True)
    print("Disposable candidate SHA: " + candidate, flush=True)
    print("Generated index SHA256: " + hashlib.sha256(index.read_bytes()).hexdigest(),
          flush=True)
    print("Doctor FULL/CI/release/push: NOT_CERTIFIED/NOT_PERMITTED", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas-root", type=Path, required=True)
    parser.add_argument("--expected-atlas-sha", required=True)
    args = parser.parse_args()
    run(args.atlas_root, args.expected_atlas_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
