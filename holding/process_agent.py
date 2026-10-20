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


HEALTH_TIMEOUT_SECONDS = 15.0


class AgentProcessError(RuntimeError):
    pass


class AgentInterrupted(AgentProcessError):
    """Stop or resource preemption, not a coding failure."""
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
            'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR',
            'LANG', 'PYTHONUTF8', 'PYTHONIOENCODING',
        }}
        # Environment redaction reduces accidental leakage but does not
        # sandbox a same-user process: Windows ACL isolation is still required.
        environment['GIT_TERMINAL_PROMPT'] = '0'
        environment['PYTHONNOUSERSITE'] = '1'
        environment['NO_PROXY'] = '127.0.0.1,localhost'
        flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) if os.name == 'nt' else 0
        # A large write to Popen(stdin=PIPE) can block forever if the child
        # never reads stdin: the timeout below has not started yet. Instead,
        # prepare bounded input in a regular temporary file BEFORE launching.
        # Passing its handle also works on Windows (no pipe backpressure).
        with tempfile.TemporaryFile() as input_file, tempfile.TemporaryFile() as stdout:
            input_file.write(request)
            input_file.seek(0)
            proc = None
            job = None
            try:
                if os.name == 'nt':
                    from .windows_job import WindowsJob
                    job = WindowsJob()
                started = time.monotonic()
                # Inventory/health checks must never use a coding-generation
                # timeout (potentially hours). Health is an inexpensive probe.
                deadline = min(self.config.timeout_seconds, HEALTH_TIMEOUT_SECONDS) if action == 'health' else self.config.timeout_seconds
                proc = subprocess.Popen([str(self.config.executable), *self.config.arguments],
                                        stdin=input_file, stdout=stdout,
                                        stderr=subprocess.DEVNULL, cwd=cwd,
                                        env=environment, shell=False,
                                        start_new_session=(os.name != 'nt'), creationflags=flags)
                # The child may start before Windows Job attachment. This
                # mechanism manages descendants but is not an OS sandbox.
                if job is not None:
                    job.attach(proc)
                while proc.poll() is None:
                    if self.should_stop():
                        raise AgentInterrupted('Agent interrupted by controller')
                    if time.monotonic() - started > deadline:
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
                if job is not None:
                    try:
                        job.terminate()
                    except Exception:
                        if proc is not None:
                            self._stop_tree(proc)
                elif proc is not None:
                    self._stop_tree(proc)
                raise
            finally:
                # KILL_ON_JOB_CLOSE terminates any descendants even if the
                # original agent process exited after spawning a helper.
                if job is not None:
                    job.close()
                elif os.name != 'nt' and proc is not None:
                    # A parent can exit successfully after spawning helpers.
                    # Kill its original POSIX process group on every exit.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
