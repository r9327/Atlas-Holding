"""Bounded local agent subprocess protocol. Never accepts a model-authored command.

Protocol v1: send one JSON request on stdin; receive one JSON response on stdout.
The OS-account/ACL sandbox is a deployment prerequisite: a subprocess is NOT a
security boundary against malicious native executables.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from typing import Callable


class AgentProcessError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentProgram:
    executable: Path
    arguments: tuple[str, ...] = ()
    timeout_seconds: float = 120.0
    output_limit: int = 512 * 1024

    def __post_init__(self):
        if (not self.executable.is_absolute() or not self.executable.is_file() or
                self.executable.is_symlink()):
            raise ValueError('Agent executable must be a regular absolute path')
        if not 1 <= self.timeout_seconds <= 7200:
            raise ValueError('Agent timeout must be between 1 and 7200 seconds')
        if not 512 <= self.output_limit <= 4 * 1024 * 1024:
            raise ValueError('Agent output limit is invalid')
        if any(not isinstance(a, str) or '\x00' in a or len(a) > 4096 for a in self.arguments):
            raise ValueError('Invalid trusted agent arguments')


class LocalProcessAgent:
    """Run the configured executable; reject command parameters from mission data."""

    def __init__(self, config: AgentProgram, *, should_stop: Callable[[], bool] = lambda: False):
        self.config = config
        self.should_stop = should_stop

    @staticmethod
    def _stop_tree(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if os.name == 'nt':
            # Best effort only; Windows Job Objects are required for hard isolation.
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=6, check=False)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def request(self, action: str, payload: dict, *, cwd: Path) -> dict:
        if action not in ('health', 'plan_patch'):
            raise AgentProcessError('Unsupported agent action')
        if not cwd.is_absolute() or not cwd.is_dir():
            raise AgentProcessError('Agent worktree must be an existing absolute directory')
        request = json.dumps({'protocol': 1, 'action': action, 'payload': payload},
                             separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        if len(request) > 64 * 1024:
            raise AgentProcessError('Agent input too large')
        # A minimum environment. Deliberately no GitHub token, API key, profile
        # secret, or credential helper inherited from the daemon.
        environment = {k: v for k, v in os.environ.items() if k.upper() in {
            'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR', 'HOME',
            'USERPROFILE', 'LOCALAPPDATA', 'LANG', 'PYTHONUTF8', 'PYTHONIOENCODING',
        }}
        environment['GIT_TERMINAL_PROMPT'] = '0'
        flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) if os.name == 'nt' else 0
        with tempfile.TemporaryFile() as stdout:
            proc = subprocess.Popen([str(self.config.executable), *self.config.arguments],
                                    stdin=subprocess.PIPE, stdout=stdout,
                                    stderr=subprocess.DEVNULL, cwd=cwd,
                                    env=environment, shell=False,
                                    start_new_session=(os.name != 'nt'), creationflags=flags)
            try:
                assert proc.stdin is not None
                proc.stdin.write(request)
                proc.stdin.close()
                started = time.monotonic()
                while proc.poll() is None:
                    if self.should_stop():
                        raise AgentProcessError('Agent interrupted by controller')
                    if time.monotonic() - started > self.config.timeout_seconds:
                        raise AgentProcessError('Agent execution timed out')
                    if os.fstat(stdout.fileno()).st_size > self.config.output_limit:
                        raise AgentProcessError('Agent output exceeded limit')
                    time.sleep(0.025)
                if proc.returncode:
                    raise AgentProcessError(f'Agent exited with code {proc.returncode}')
                if os.fstat(stdout.fileno()).st_size > self.config.output_limit:
                    raise AgentProcessError('Agent output exceeded limit')
                stdout.seek(0)
                raw = stdout.read(self.config.output_limit + 1)
                try:
                    response = json.loads(raw)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise AgentProcessError('Invalid agent JSON response') from exc
                if (not isinstance(response, dict) or response.get('protocol') != 1 or
                        response.get('action') != action or response.get('status') != 'ok'):
                    raise AgentProcessError('Agent protocol or status mismatch')
                return response
            except BaseException:
                self._stop_tree(proc)
                raise
