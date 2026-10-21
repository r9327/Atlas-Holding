"""Trusted installed-service entrypoint. No dynamic Python imports from roadmaps.

Install/setup must protect the JSON configuration and execute under the least
privileged OS account. A file hash detects binary changes; it is not a replacement
for Windows ACLs / AppLocker / Authenticode enforcement.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re

from .dashboard_session import DashboardSessionError, ViewerProgram
from .git_worktree import GitWorktrees
from .github_broker_protocol import (
    BrokerFilePin, BrokerGitPushControl, BrokerProcessConfig,
    GitHubBrokerProcessControl,
)
from .github_full_evidence import GitHubEvidenceError
from .process_agent import AgentProgram
from .service import HoldingService, ServiceSettings, termination_signals


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ACCOUNT = re.compile(
    r"^github:[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")

def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubInstallation:
    account_id: str
    policy_sha: str
    broker: BrokerProcessConfig


@dataclass(frozen=True)
class DashboardInstallation:
    program: ViewerProgram
    readonly: bool


@dataclass(frozen=True)
class Installation:
    database: Path
    repository: str
    canonical_root: Path
    workspace: Path
    health_directory: Path
    executable: Path
    executable_sha256: str
    arguments: tuple[str, ...]
    argument_sha256: dict[str, str]
    timeout_seconds: float
    github: GitHubInstallation | None = None
    dashboard: "DashboardInstallation | None" = None

    @staticmethod
    def _github_config(info: object) -> GitHubInstallation:
        expected = {
            'account_id', 'policy_sha', 'broker_command',
            'broker_file_sha256', 'broker_spool_root',
            'broker_timeout_seconds',
        }
        if not isinstance(info, dict) or set(info) != expected:
            raise BootstrapError('Invalid GitHub installation config')
        if (not isinstance(info['account_id'], str)
                or not _ACCOUNT.fullmatch(info['account_id'])
                or not isinstance(info['policy_sha'], str)
                or not _SHA.fullmatch(info['policy_sha'])):
            raise BootstrapError('Invalid pinned GitHub identity or policy')
        raw_command = info['broker_command']
        if (not isinstance(raw_command, list) or not 1 <= len(raw_command) <= 32
                or any(not isinstance(item, str) or not item
                       or len(item) > 4096 or '\x00' in item
                       for item in raw_command)):
            raise BootstrapError('Invalid privileged broker command')
        raw_pins = info['broker_file_sha256']
        if (not isinstance(raw_pins, dict) or not 1 <= len(raw_pins) <= 128):
            raise BootstrapError('Invalid privileged broker file pins')
        pins = []
        canonical_paths = {}
        seen_canonical = set()
        for raw_path, digest in raw_pins.items():
            if (not isinstance(raw_path, str) or len(raw_path) > 4096
                    or not isinstance(digest, str)
                    or not _DIGEST.fullmatch(digest)):
                raise BootstrapError('Invalid privileged broker file pin')
            path = Path(raw_path)
            if (not path.is_absolute() or '..' in path.parts or path.is_symlink()):
                raise BootstrapError('Pinned broker path is unsafe')
            try:
                canonical = path.resolve(strict=True)
            except OSError:
                raise BootstrapError('Pinned broker file is unavailable') from None
            if canonical in seen_canonical:
                raise BootstrapError('Duplicate canonical broker file pin')
            if not canonical.is_file() or canonical.is_symlink():
                raise BootstrapError('Pinned broker path is not a regular file')
            try:
                observed = _hash_file(canonical)
            except OSError:
                raise BootstrapError('Pinned broker file is unavailable') from None
            if not hmac.compare_digest(observed, digest):
                raise BootstrapError('Pinned broker file changed since onboarding')
            seen_canonical.add(canonical)
            canonical_paths[raw_path] = canonical
            pins.append(BrokerFilePin(canonical, digest))
        raw_spool = info['broker_spool_root']
        if not isinstance(raw_spool, str) or len(raw_spool) > 4096:
            raise BootstrapError('Invalid privileged broker spool path')
        spool = Path(raw_spool)
        if (not spool.is_absolute() or not spool.is_dir() or spool.is_symlink()
                or '..' in spool.parts):
            raise BootstrapError('Invalid privileged broker spool directory')
        try:
            spool = spool.resolve(strict=True)
        except OSError:
            raise BootstrapError('Invalid privileged broker spool directory') from None
        timeout = info['broker_timeout_seconds']
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise BootstrapError('Invalid privileged broker timeout')
        command = tuple(
            str(canonical_paths[item]) if item in canonical_paths else item
            for item in raw_command)
        broker = BrokerProcessConfig(command, tuple(pins), spool, timeout)
        try:
            # Constructor validation proves command/code coverage before the
            # service is built; per-call validation detects later tampering.
            GitHubBrokerProcessControl(broker)
        except GitHubEvidenceError as exc:
            raise BootstrapError('Unsafe privileged broker configuration') from exc
        return GitHubInstallation(
            info['account_id'], info['policy_sha'], broker)

    @staticmethod
    def _dashboard_config(info: object) -> DashboardInstallation:
        expected = {
            'executable', 'executable_sha256',
            'script', 'script_sha256', 'readonly',
        }
        if not isinstance(info, dict) or set(info) != expected:
            raise BootstrapError('Invalid installed dashboard contract')
        if type(info['readonly']) is not bool:
            raise BootstrapError('Invalid dashboard read permission')
        executable = info['executable']
        script = info['script']
        if (not isinstance(executable, str) or len(executable) > 4096
                or not executable or '\x00' in executable
                or script is not None and (
                    not isinstance(script, str) or not script
                    or len(script) > 4096 or '\x00' in script)):
            raise BootstrapError('Invalid dashboard executable path')
        if (not isinstance(info['executable_sha256'], str)
                or not _DIGEST.fullmatch(info['executable_sha256'])
                or script is None and info['script_sha256'] is not None
                or script is not None and (
                    not isinstance(info['script_sha256'], str)
                    or not _DIGEST.fullmatch(info['script_sha256']))):
            raise BootstrapError('Invalid dashboard executable pin')
        viewer = ViewerProgram(
            Path(executable), info['executable_sha256'],
            Path(script) if script is not None else None,
            info['script_sha256'],
        )
        try:
            viewer.verify()
        except DashboardSessionError as exc:
            raise BootstrapError('Installed dashboard binary is not verified') from exc
        return DashboardInstallation(viewer, info['readonly'])

    @classmethod
    def load(cls, filename: Path) -> 'Installation':
        if not filename.is_absolute() or not filename.is_file() or filename.is_symlink():
            raise BootstrapError('Trusted config must be a regular absolute file')
        if filename.stat().st_size > 32 * 1024:
            raise BootstrapError('Oversized installation config')
        try:
            info = json.loads(filename.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError, OSError) as exc:
            raise BootstrapError('Invalid installer config') from exc
        base = {'schema','database','repository','canonical_root','workspace',
                'health_directory','executable','executable_sha256','arguments',
                'argument_sha256','timeout_seconds'}
        if (not isinstance(info, dict) or type(info.get('schema')) is not int
                or info['schema'] not in {1, 2, 3}
                or set(info) != (base if info['schema'] == 1 else
                    base | {'github'} if info['schema'] == 2 else
                    base | {'github', 'dashboard'})):
            raise BootstrapError('Unknown installer schema or keys')
        locations = {}
        for field in ('database','canonical_root','workspace','health_directory','executable'):
            raw = info[field]
            if not isinstance(raw,str) or len(raw) > 4096:
                raise BootstrapError('Invalid path in installer config')
            value = Path(raw)
            if not value.is_absolute() or value.is_symlink() or '..' in value.parts:
                raise BootstrapError('Expected absolute, non-symlinked trusted paths')
            locations[field] = value
        if (not isinstance(info['repository'], str)
                or not _REPOSITORY.fullmatch(info['repository'])
                or any(part in {'.', '..'} for part in info['repository'].split('/'))):
            raise BootstrapError('Invalid repository')
        checksum = info['executable_sha256']
        if not isinstance(checksum,str) or len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum):
            raise BootstrapError('Expected a SHA-256 binary pin')
        args = info['arguments']
        if not isinstance(args,list) or any(not isinstance(a,str) or len(a)>4096 or '\x00' in a for a in args):
            raise BootstrapError('Invalid trusted executable arguments')
        pins = info['argument_sha256']
        if not isinstance(pins, dict) or set(pins) != set(args) or len(args) > 8:
            raise BootstrapError('Every trusted agent argument requires an immutable file pin')
        for argument in args:
            file = Path(argument)
            if not file.is_absolute() or not file.is_file() or file.is_symlink():
                raise BootstrapError('Unpinned argument or executable CLI code refused')
            digest = pins.get(argument)
            if (not isinstance(digest, str) or len(digest) != 64 or
                    any(c not in '0123456789abcdef' for c in digest) or
                    _hash_file(file) != digest):
                raise BootstrapError('Trusted agent argument changed since onboarding')
        if type(info['timeout_seconds']) not in (int,float):
            raise BootstrapError('Invalid timeout')
        exe = locations['executable']
        if not exe.is_file() or exe.is_symlink():
            raise BootstrapError('Agent executable is missing')
        digest = _hash_file(exe)
        if digest != checksum:
            raise BootstrapError('Agent executable changed since onboarding')
        github = (
            cls._github_config(info['github'])
            if info['schema'] in {2, 3} and info['github'] is not None else None
        )
        # Schema v2 is GitHub-enabled only; schema v3 permits a local-only
        # viewer when no account is configured, without inventing authority.
        if info['schema'] == 2 and github is None:
            raise BootstrapError('GitHub schema requires configured broker')
        dashboard = (cls._dashboard_config(info['dashboard'])
                     if info['schema'] == 3 else None)
        return cls(locations['database'],info['repository'],locations['canonical_root'],
                   locations['workspace'],locations['health_directory'],exe,checksum,
                   tuple(args),pins,float(info['timeout_seconds']),github,dashboard)

    def build_service(self) -> HoldingService:
        git = GitWorktrees(self.canonical_root,self.workspace,self.repository)
        git.inspect_canonical()
        program = AgentProgram(self.executable, self.arguments,
            timeout_seconds=self.timeout_seconds,
            pinned_executable_sha256=self.executable_sha256,
            pinned_arguments=tuple((arg, self.argument_sha256[arg]) for arg in self.arguments))
        service = HoldingService.from_local_agent(
            ServiceSettings(self.database), program=program, git=git,
            health_directory=self.health_directory, online=lambda: False)
        if self.github is not None:
            control = GitHubBrokerProcessControl(self.github.broker)
            service.enable_git_push(
                expected_account_id=self.github.account_id,
                expected_policy_sha=self.github.policy_sha,
                control_factory=lambda source_id: BrokerGitPushControl(
                    control, source_id),
                health_probe=control)
        return service


def _is_interactive_windows_session() -> bool:
    """Reject Windows service Session 0 and unknown process session contexts."""
    if os.name != 'nt':
        return True
    try:
        import ctypes
        session = ctypes.c_ulong()
        query = ctypes.windll.kernel32.ProcessIdToSessionId
        query.argtypes = (ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))
        query.restype = ctypes.c_int
        return bool(query(os.getpid(), ctypes.byref(session))) and session.value != 0
    except (OSError, ValueError, AttributeError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='atlas-holding-daemon')
    parser.add_argument('--config',required=True,type=Path)
    args=parser.parse_args(argv)
    config=Installation.load(args.config)
    with config.build_service() as service:
        with termination_signals(service):
            service.start()
            if config.dashboard is not None:
                # Windows Session 0 cannot host an interactive Qt desktop.
                # Installer must invoke this under the selected signed-in user.
                if os.name == 'nt' and not _is_interactive_windows_session():
                    raise BootstrapError('Dashboard needs a trusted interactive Windows session')
                service.launch_dashboard(
                    config.dashboard.program, readonly=config.dashboard.readonly,
                )
            result=service.run()
            return 0 if result.action == 'STOPPED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
