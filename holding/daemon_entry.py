"""Trusted installed-service entrypoint. No dynamic Python imports from roadmaps.

Install/setup must protect the JSON configuration and execute under the least
privileged OS account. A file hash detects binary changes; it is not a replacement
for Windows ACLs / AppLocker / Authenticode enforcement.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .git_worktree import GitWorktrees
from .process_agent import AgentProgram
from .service import HoldingService, ServiceSettings, termination_signals


class BootstrapError(RuntimeError):
    pass


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
    timeout_seconds: float

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
        expected = {'schema','database','repository','canonical_root','workspace',
                    'health_directory','executable','executable_sha256','arguments','timeout_seconds'}
        if not isinstance(info, dict) or set(info) != expected or type(info['schema']) is not int or info['schema'] != 1:
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
        if not isinstance(info['repository'],str) or '/' not in info['repository']:
            raise BootstrapError('Invalid repository')
        checksum = info['executable_sha256']
        if not isinstance(checksum,str) or len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum):
            raise BootstrapError('Expected a SHA-256 binary pin')
        args = info['arguments']
        if not isinstance(args,list) or any(not isinstance(a,str) or len(a)>4096 or '\x00' in a for a in args):
            raise BootstrapError('Invalid trusted executable arguments')
        if type(info['timeout_seconds']) not in (int,float):
            raise BootstrapError('Invalid timeout')
        exe = locations['executable']
        if not exe.is_file() or exe.is_symlink():
            raise BootstrapError('Agent executable is missing')
        digest = hashlib.sha256(exe.read_bytes()).hexdigest()
        if digest != checksum:
            raise BootstrapError('Agent executable changed since onboarding')
        return cls(locations['database'],info['repository'],locations['canonical_root'],
                   locations['workspace'],locations['health_directory'],exe,checksum,
                   tuple(args),float(info['timeout_seconds']))

    def build_service(self) -> HoldingService:
        git = GitWorktrees(self.canonical_root,self.workspace,self.repository)
        git.inspect_canonical()
        program = AgentProgram(self.executable,self.arguments,timeout_seconds=self.timeout_seconds)
        return HoldingService.from_local_agent(ServiceSettings(self.database), program=program,
            git=git,health_directory=self.health_directory,online=lambda:False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='atlas-holding-daemon')
    parser.add_argument('--config',required=True,type=Path)
    args=parser.parse_args(argv)
    config=Installation.load(args.config)
    with config.build_service() as service:
        with termination_signals(service):
            service.start()
            result=service.run()
            return 0 if result.action == 'STOPPED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
