"""Fail-closed Git worktree controller. No arbitrary shell/LLM commands."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import tempfile

_REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')
_SHA = re.compile(r'^[0-9a-f]{40}$')
_PROTECTED = frozenset({'main', 'master'})


class GitSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    base_sha: str


class GitWorktrees:
    def __init__(self, canonical_root: str | Path, workspace: str | Path, repository: str):
        if not _REPO.fullmatch(repository) or any(part in ('.', '..') for part in repository.split('/')):
            raise GitSafetyError('Expected explicit owner/repo')
        self.root = Path(canonical_root).resolve(strict=True)
        self.workspace = Path(workspace).resolve()
        self.repository = repository
        if self.root == self.workspace or self.root in self.workspace.parents or self.workspace in self.root.parents:
            raise GitSafetyError('Workspace must be isolated from the canonical checkout')

    def _git(self, *args: str, cwd: Path | None = None, timeout: int = 30) -> str:
        """Fixed Git argv, bounded output and a terminated process tree on failure.

        A subprocess.run(timeout=...) kills only the direct Git process, while
        helpers can survive. This routine owns and closes every descendant.
        """
        import time

        if not args or not 1 <= timeout <= 300:
            raise GitSafetyError('Git operation must have bounded arguments and timeout')
        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith('GIT_')}
        environment.update(GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1',
                           GIT_PAGER='cat', GIT_OPTIONAL_LOCKS='0')
        environment.pop('GIT_CONFIG_COUNT', None)
        output_cap, error_cap = 4 * 1024 * 1024, 256 * 1024
        process = job = None
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                if os.name == 'nt':
                    from .windows_job import WindowsJob
                    job = WindowsJob()
                started = time.monotonic()
                process = subprocess.Popen(
                    ['git', '-C', str(cwd or self.root), *args],
                    stdout=output, stderr=errors, stdin=subprocess.DEVNULL,
                    env=environment, shell=False,
                    start_new_session=(os.name != 'nt'),
                    creationflags=(getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
                                   if os.name == 'nt' else 0))
                if job is not None:
                    job.attach(process)
                while process.poll() is None:
                    if time.monotonic() - started >= timeout:
                        raise GitSafetyError('Git timed out')
                    if (os.fstat(output.fileno()).st_size > output_cap or
                            os.fstat(errors.fileno()).st_size > error_cap):
                        raise GitSafetyError('Git output exceeded configured limit')
                    time.sleep(0.05)
                if (os.fstat(output.fileno()).st_size > output_cap or
                        os.fstat(errors.fileno()).st_size > error_cap):
                    raise GitSafetyError('Git output exceeded configured limit')
                output.seek(0)
                stdout = output.read(output_cap + 1)
                errors.seek(0)
                error = errors.read(error_cap).decode(
                    'utf-8', errors='replace')[-300:]
                if process.returncode:
                    raise GitSafetyError(f'Git {args[0]} failed: {error}')
                return stdout.decode('utf-8', errors='surrogateescape').strip()
            except (OSError, subprocess.SubprocessError) as exc:
                raise GitSafetyError('Git unavailable or failed to start') from exc
            finally:
                if job is not None:
                    # Kill descendants even if the parent exited successfully.
                    try:
                        job.terminate()
                    except Exception:
                        if process is not None:
                            from .process_agent import LocalProcessAgent
                            LocalProcessAgent._stop_tree(process)
                    finally:
                        job.close()
                elif process is not None:
                    if os.name != 'nt':
                        import signal
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    if process.poll() is None:
                        from .process_agent import LocalProcessAgent
                        LocalProcessAgent._stop_tree(process)

    def inspect_canonical(self) -> None:
        root = Path(self._git('rev-parse', '--show-toplevel')).resolve(strict=True)
        if root != self.root:
            raise GitSafetyError('Canonical root mismatch')
        remote = self._git('remote', 'get-url', 'origin')
        expected = self.repository.lower()
        matches = {f'https://github.com/{expected}', f'https://github.com/{expected}.git',
                   f'git@github.com:{expected}.git', f'ssh://git@github.com/{expected}.git'}
        if remote.lower() not in matches:
            raise GitSafetyError('Origin is not the explicitly allowed repository')
        if self._git('status', '--porcelain', '--untracked-files=normal'):
            raise GitSafetyError('Refusing a dirty canonical checkout')

    def _branch(self, mission_id: str) -> str:
        if not _ID.fullmatch(mission_id):
            raise GitSafetyError('Unsafe mission name')
        return f'holding/{mission_id}'

    def create(self, mission_id: str, *, base: str = 'main') -> Worktree:
        if base not in _PROTECTED:
            raise GitSafetyError('Base must be an explicitly allowed protected branch')
        self.inspect_canonical()
        branch = self._branch(mission_id)
        dest = self.workspace / mission_id
        if dest.exists() or dest.is_symlink() or self._git('branch', '--list', branch):
            raise GitSafetyError('Existing worktree or branch: recovery required')
        sha = self._git('rev-parse', '--verify', f'refs/heads/{base}^{{commit}}').lower()
        if not _SHA.fullmatch(sha):
            raise GitSafetyError('Invalid starting commit')
        self.workspace.mkdir(parents=True, exist_ok=True)
        if self.workspace.is_symlink() or dest.parent.resolve() != self.workspace.resolve():
            raise GitSafetyError('Unsafe workspace path')
        self._git('worktree','add','-b',branch,str(dest),sha)
        self.inspect(mission_id)
        return Worktree(dest, branch, sha)

    def inspect(self, mission_id: str) -> Worktree:
        branch = self._branch(mission_id)
        dest = self.workspace / mission_id
        if dest.is_symlink() or not dest.is_dir() or dest.resolve() != dest:
            raise GitSafetyError('Worktree missing or unsafe')
        if Path(self._git('rev-parse','--show-toplevel',cwd=dest)).resolve() != dest:
            raise GitSafetyError('Unexpected Git worktree')
        if self._git('branch','--show-current',cwd=dest) != branch:
            raise GitSafetyError('Wrong branch')
        common = Path(self._git('rev-parse','--path-format=absolute','--git-common-dir',cwd=dest)).resolve()
        expected_common = Path(self._git('rev-parse','--path-format=absolute','--git-common-dir')).resolve()
        if common != expected_common:
            raise GitSafetyError('Foreign worktree is not linked to canonical repo')
        ref = self._git('rev-parse','--verify','HEAD',cwd=dest).lower()
        if not _SHA.fullmatch(ref):
            raise GitSafetyError('Invalid worktree HEAD')
        return Worktree(dest, branch, ref)

    def inspect_recovery(self, mission_id: str) -> bool:
        """Only a clean, unchanged worktree is eligible for manual retry.

        Returns false when there is any local work or uncertainty. Does not
        discard changes, delete files, or claim a task has succeeded.
        """
        try:
            tree = self.inspect(mission_id)
            return not bool(self._git('status','--porcelain','--untracked-files=normal',cwd=tree.path))
        except GitSafetyError:
            return False

    def commit(self, mission_id: str, paths: tuple[str, ...], message: str) -> str:
        tree = self.inspect(mission_id)
        if not paths or len(message.strip()) < 5 or len(set(paths)) != len(paths):
            raise GitSafetyError('Explicit unique files and meaningful commit message required')
        if self._git('diff', '--cached', '--name-only', '-z', cwd=tree.path):
            raise GitSafetyError('Refusing to commit with pre-staged changes')
        for p in paths:
            norm = Path(p)
            parts = norm.parts
            if (not p or norm.is_absolute() or not parts or
                p != norm.as_posix() or p in ('.', '..') or
                any(x in ('', '.', '..', '.git') for x in parts) or
                parts[0] in {'.github', '.env'} or
                any(x.lower() in {'secrets', 'tokens', 'credentials'} for x in parts) or
                any(ord(c) < 32 or c in '*?[]:' for c in p)):
                raise GitSafetyError('Sensitive, ambiguous or unsafe path')
            location = tree.path / norm
            if location.is_dir() or location.is_symlink() or not location.resolve().is_relative_to(tree.path):
                raise GitSafetyError('Only regular files or tracked deletions are permitted')
            if not location.is_file() and self._git('ls-files', '--error-unmatch', '--', p,
                                                   cwd=tree.path, timeout=10) != p:
                raise GitSafetyError('Untracked deleted path is not allowed')
        self._git('add', '--', *paths, cwd=tree.path)
        staged = self._git('diff', '--cached', '--name-only', '-z', cwd=tree.path)
        actual = {item for item in staged.split('\0') if item}
        if not actual or actual - set(paths):
            raise GitSafetyError('Unexpected staged file (possible pathspec injection)')
        self._git('diff', '--cached', '--check', cwd=tree.path)
        # --only restricts the commit to the exact chosen paths even if the
        # index were modified after the safety check by another process.
        # Never run a repository/user-provided pre-commit hook or a global GPG
        # signing command from a generated-code checkout. An ephemeral empty
        # hooks directory is selected through trusted Git CLI config.
        with tempfile.TemporaryDirectory(prefix='atlas-holding-empty-hooks-') as hooks:
            self._git('-c', f'core.hooksPath={hooks}', '-c', 'commit.gpgsign=false',
                      'commit', '--only', '-m', message, '--', *paths,
                      cwd=tree.path, timeout=120)
        sha = self._git('rev-parse', 'HEAD', cwd=tree.path).lower()
        if not _SHA.fullmatch(sha):
            raise GitSafetyError('Invalid commit')
        committed = {item for item in self._git('diff-tree', '--no-commit-id', '--name-only',
                                                '-r', '-z', 'HEAD', cwd=tree.path).split('\0') if item}
        if not committed or committed - set(paths):
            raise GitSafetyError('Committed path set was unexpected: quarantine worktree')
        return sha
