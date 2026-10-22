"""Safe, opt-in synchronization of the Atlas-Holding *source checkout*.

This is deliberately separate from mission GitHub publishing (Dof-Atlas) and
from updating a running installed EXE. No staging, commits, merges or force pushes.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Literal
from urllib.parse import urlsplit


REPOSITORY = "r9327/Atlas-Holding"
_SHA = re.compile(r"^[0-9a-f]{40}$")


class SyncBlocked(RuntimeError):
    """The checkout was not changed because a safety precondition failed."""


@dataclass(frozen=True)
class Status:
    repository: str
    branch: str
    head: str
    dirty: bool
    remote_head: str | None = None
    ahead: int | None = None
    behind: int | None = None
    remote_checked: bool = False


@dataclass(frozen=True)
class SyncResult:
    before: Status
    action: Literal["UP_TO_DATE", "PULLED", "PUSHED"]
    after: Status


def _origin_identity(raw: str) -> str | None:
    """Accept only the expected GitHub repository in HTTPS or SSH form."""
    raw = raw.strip()
    if raw.startswith("git@github.com:"):
        path = raw[len("git@github.com:"):]
    else:
        try:
            url = urlsplit(raw)
            port = url.port
        except ValueError:
            return None
        if url.scheme not in ("https", "ssh") or url.hostname != "github.com":
            return None
        if url.username not in (None, "git") or url.password or url.query or url.fragment:
            return None
        if port not in ((None, 443) if url.scheme == "https" else (None, 22)):
            return None
        path = url.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path if path == REPOSITORY else None


class HoldingGitSync:
    def __init__(self, root: Path, *, expected_origin: str | None = None):
        # expected_origin is for local-bare-repository integration tests only.
        self.root = Path(root).resolve()
        self.expected_origin = expected_origin

    def _git(self, *args: str, timeout: int = 60) -> str:
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GCM_INTERACTIVE"] = "Never"
        try:
            process = subprocess.run(
                ["git", "-C", str(self.root), *args],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                shell=False, timeout=timeout, check=False,
                env=environment,  # existing auth only, no terminal prompts or token logging
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SyncBlocked(f"Git unavailable or timed out during {args[0]}") from exc
        if process.returncode != 0:
            # Deliberately omit raw stderr: remote URLs may embed credentials.
            raise SyncBlocked(f"Git {args[0]} failed (exit {process.returncode})")
        return process.stdout.strip()

    def _validate_checkout(self) -> tuple[str, str]:
        top = Path(self._git("rev-parse", "--show-toplevel")).resolve()
        if top != self.root:
            raise SyncBlocked("Expected the root of a Git checkout, not a subdirectory")
        if self._git("rev-parse", "--is-bare-repository") != "false":
            raise SyncBlocked("Bare repositories cannot be synchronized")
        origin = self._git("remote", "get-url", "origin")
        if self.expected_origin is None:
            if _origin_identity(origin) != REPOSITORY:
                raise SyncBlocked("Origin does not identify r9327/Atlas-Holding")
        elif origin != self.expected_origin:
            raise SyncBlocked("Origin differs from the expected test repository")
        branch = self._git("symbolic-ref", "--quiet", "--short", "HEAD")
        if not branch or branch.startswith("-") or not self._valid_branch(branch):
            raise SyncBlocked("Detached HEAD or unsafe branch name")
        head = self._git("rev-parse", "--verify", "HEAD^{commit}")
        if not _SHA.fullmatch(head):
            raise SyncBlocked("Invalid local commit SHA")
        return branch, head

    def _valid_branch(self, branch: str) -> bool:
        if branch.startswith("refs/") or ".." in branch or "//" in branch:
            return False
        try:
            return bool(branch) and self._git(
                "check-ref-format", "--branch", branch) == branch
        except SyncBlocked:
            return False

    def status(self) -> Status:
        """Local-only status, usable even while the network is down."""
        branch, head = self._validate_checkout()
        dirty = bool(self._git("status", "--porcelain=v1", "--untracked-files=normal"))
        return Status(REPOSITORY, branch, head, dirty)

    def _remote_tip(self, branch: str) -> str | None:
        """Read one exact remote branch tip without touching worktree or refs."""
        lines = self._git("ls-remote", "--heads", "origin", f"refs/heads/{branch}", timeout=90)
        if not lines:
            return None
        parts = lines.splitlines()
        if len(parts) != 1:
            raise SyncBlocked("Remote branch is ambiguous")
        pieces = parts[0].split("\t")
        if len(pieces) != 2 or pieces[1] != f"refs/heads/{branch}" or not _SHA.fullmatch(pieces[0]):
            raise SyncBlocked("Unexpected remote ref")
        return pieces[0]

    def _observe_remote(self, branch: str, *, local_head: str) -> str | None:
        """Fast path: 1 GitHub ref probe, ZERO download when already current.

        Only fetch a changed tip for a genuine Git ancestry comparison.
        Re-check its exact SHA after fetch; never trust a racing remote.
        """
        tip = self._remote_tip(branch)
        if tip is None or tip == local_head:
            return tip
        self._git("fetch", "--no-tags", "origin", f"refs/heads/{branch}", timeout=180)
        fetched = self._git("rev-parse", "--verify", "FETCH_HEAD^{commit}")
        if fetched != tip:
            raise SyncBlocked("Remote changed during fetch; retry after inspection")
        return fetched

    def _status_remote(self, current: Status, remote_head: str | None) -> Status:
        if remote_head is None:
            return Status(current.repository, current.branch, current.head, current.dirty,
                          remote_head=None, ahead=None, behind=None, remote_checked=True)
        if remote_head == current.head:
            return Status(current.repository, current.branch, current.head, current.dirty,
                          remote_head=remote_head, ahead=0, behind=0, remote_checked=True)
        counts = self._git("rev-list", "--left-right", "--count", "HEAD...FETCH_HEAD")
        try:
            ahead, behind = (int(n) for n in counts.split())
        except ValueError as exc:
            raise SyncBlocked("Unexpected Git ancestry counts") from exc
        return Status(current.repository, current.branch, current.head, current.dirty,
                      remote_head=remote_head, ahead=ahead, behind=behind,
                      remote_checked=True)

    def run(self, mode: Literal["pull", "push", "sync"]) -> SyncResult:
        if mode not in ("pull", "push", "sync"):
            raise ValueError("Unknown synchronization mode")
        local = self.status()
        if local.dirty:
            raise SyncBlocked("Working tree has changes; commit or set them aside first")
        remote = self._observe_remote(local.branch, local_head=local.head)
        before = self._status_remote(local, remote)
        if before.ahead and before.behind:
            raise SyncBlocked("Local and remote histories diverged; resolve manually (no reset/force)")
        if self.status() != local:
            raise SyncBlocked("Checkout changed during remote inspection")

        if mode == "pull" or (mode == "sync" and before.behind):
            if remote is None:
                raise SyncBlocked("No remote branch to pull")
            if before.ahead:
                raise SyncBlocked("Local branch is ahead; nothing to pull")
            if before.behind:
                self._git("merge", "--ff-only", remote, timeout=120)
                action = "PULLED"
            else:
                action = "UP_TO_DATE"
        elif mode == "push" or (mode == "sync" and (before.ahead or remote is None)):
            if local.branch in ("main", "master"):
                raise SyncBlocked("Direct push to main/master is forbidden; use a feature branch and PR")
            if before.behind:
                raise SyncBlocked("Remote is ahead; synchronize safely before pushing")
            if remote != local.head:
                self._git("push", "--porcelain", "origin",
                          f"{local.head}:refs/heads/{local.branch}", timeout=300)
                action = "PUSHED"
            else:
                action = "UP_TO_DATE"
        else:
            action = "UP_TO_DATE"

        # A second remote observation is mandatory after every attempted mutation.
        after_local = self.status()
        # Post-condition requires only the current remote SHA. A second Git
        # fetch would waste bandwidth and cannot strengthen exact-tip equality.
        remote_after = self._remote_tip(after_local.branch)
        after = Status(after_local.repository, after_local.branch, after_local.head,
                       after_local.dirty, remote_head=remote_after,
                       ahead=0 if remote_after == after_local.head else None,
                       behind=0 if remote_after == after_local.head else None,
                       remote_checked=True)
        if after.dirty or after.remote_head != after.head or after.ahead != 0 or after.behind != 0:
            raise SyncBlocked("Post-sync remote observation is inconsistent; inspect before retrying")
        return SyncResult(before, action, after)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synchroniser sans écrasement le Git source Atlas-Holding")
    parser.add_argument("operation", choices=("status", "pull", "push", "sync"))
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Racine du checkout Holding")
    parser.add_argument("--json", action="store_true", help="Sortie exploitable par le Control Center")
    args = parser.parse_args(argv)
    client = HoldingGitSync(args.repo)
    try:
        if args.operation == "status":
            result: dict = {"ok": True, "operation": "status",
                            "status": asdict(client.status())}
        else:
            sync = client.run(args.operation)
            result = {"ok": True, "operation": args.operation,
                      "action": sync.action, "before": asdict(sync.before),
                      "after": asdict(sync.after)}
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except SyncBlocked as exc:
        if args.json:
            print(json.dumps({"ok": False, "operation": args.operation,
                              "reason": str(exc)}, sort_keys=True))
        else:
            print(f"SYNCHRONISATION BLOQUÉE : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
