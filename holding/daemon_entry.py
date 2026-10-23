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
import sys

from .dashboard_session import DashboardSessionError, ViewerProgram
from .git_worktree import GitWorktrees
from .github_broker_protocol import (
    BrokerFilePin, BrokerGitPushControl, BrokerProcessConfig,
    GitHubBrokerProcessControl,
)
from .github_full_evidence import GitHubEvidenceError
from .process_agent import AgentProgram
from .service import HoldingService, ServiceSettings, termination_signals
from .mcp_local_inventory import PinnedLocalPlugin
from .mcp_package_pin import McpPackagePin, McpPackagePinError
from .embedded_python_stage import EmbeddedPythonRelease, verify_staged_embedded_python


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
class McpInstallation:
    interpreter_pin: PinnedLocalPlugin
    response_timeout: float
    package_pin: McpPackagePin | None = None
    package_root: Path | None = None


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
    mcp: "McpInstallation | None" = None

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

    @staticmethod
    def _mcp_config(info: object, *, require_package_pin: bool = False,
                    installed_companion: bool = False,
                    install_root: Path | None = None) -> McpInstallation:
        # A PyInstaller frozen EXE is not the embeddable CPython interpreter
        # needed by "python -B -m holding.mcp_entry". Fail during setup
        # rather than silently advertising a usable local MCP runtime.
        if getattr(sys, "frozen", False) and not installed_companion:
            raise BootstrapError("MCP requires an explicitly bundled Python interpreter, not a frozen app EXE")
        required = {"interpreter", "interpreter_sha256", "response_timeout"}
        if require_package_pin:
            required.add("module_sha256")
        if installed_companion:
            required.update({"package_root", "source_release_zip_sha256", "python_minor"})
        if type(info) is not dict or set(info) != required:
            raise BootstrapError("Invalid pinned MCP installation")
        raw, digest, timeout = (
            info["interpreter"], info["interpreter_sha256"],
            info["response_timeout"])
        if (type(raw) is not str or not 1 <= len(raw) <= 4096
                or type(digest) is not str or not _DIGEST.fullmatch(digest)
                or type(timeout) not in (int, float)
                or not .25 <= timeout <= 10):
            raise BootstrapError("Invalid pinned MCP interpreter settings")
        binary = Path(raw)
        try:
            canonical = binary.resolve(strict=True)
            running = Path(sys.executable).resolve(strict=True)
        except OSError as exc:
            raise BootstrapError("Installed MCP interpreter unavailable") from exc
        if (not binary.is_absolute() or '..' in binary.parts or binary.is_symlink()
                or not canonical.is_file()
                or (not installed_companion and canonical != running)):
            raise BootstrapError("MCP interpreter must match installed runtime")
        try:
            observed = _hash_file(canonical)
        except OSError as exc:
            raise BootstrapError("Installed MCP interpreter unavailable") from exc
        if not hmac.compare_digest(observed, digest):
            raise BootstrapError("MCP interpreter changed since onboarding")
        package_root = Path(__file__).resolve().parent.parent
        if installed_companion:
            raw_root = info["package_root"]
            archive_hash = info["source_release_zip_sha256"]
            minor = info["python_minor"]
            if (type(raw_root) is not str or not 1 <= len(raw_root) <= 4096
                    or type(archive_hash) is not str or not _DIGEST.fullmatch(archive_hash)
                    or type(minor) is not int or not 13 <= minor <= 19
                    or install_root is None):
                raise BootstrapError("Invalid installed MCP companion identity")
            package_root = Path(raw_root)
            expected = (
                install_root / "mcp-python" /
                ("python-embedded-" + archive_hash[:16]))
            if (not package_root.is_absolute() or package_root != expected
                    or package_root.is_symlink()
                    or not package_root.is_dir()
                    or canonical.parent != package_root
                    or canonical.name.lower() != "python.exe"):
                raise BootstrapError("Installed MCP companion outside approved root")
        package_pin = None
        if require_package_pin:
            try:
                package_pin = McpPackagePin.from_manifest(info["module_sha256"])
                if not package_pin.verify(package_root):
                    raise McpPackagePinError("Installed package digest mismatch")
            except (McpPackagePinError, OSError) as exc:
                raise BootstrapError("MCP package code changed since onboarding") from exc
        if installed_companion:
            try:
                release = EmbeddedPythonRelease(
                    python_minor=minor, zip_sha256=archive_hash,
                    module_sha256=info["module_sha256"])
                staged = verify_staged_embedded_python(
                    directory=package_root, pin=release)
                if staged.interpreter_sha256 != digest:
                    raise BootstrapError("Installed MCP interpreter release pin changed")
            except (OSError, ValueError) as exc:
                raise BootstrapError("Pinned MCP companion receipt invalid") from exc
        return McpInstallation(
            PinnedLocalPlugin("atlas-holding-internal", "1.0", canonical, digest),
            float(timeout), package_pin,
            package_root if installed_companion else None)

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
                or info['schema'] not in {1, 2, 3, 4, 5, 6}
                or set(info) != (base if info['schema'] == 1 else
                    base | {'github'} if info['schema'] == 2 else
                    base | {'github', 'dashboard'} if info['schema'] == 3 else
                    base | {'github', 'dashboard', 'mcp'})):
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
            if info['schema'] in {2, 3, 4, 5, 6} and info['github'] is not None else None
        )
        # Schema v2 is GitHub-enabled only; schema v3 permits a local-only
        # viewer when no account is configured, without inventing authority.
        if info['schema'] == 2 and github is None:
            raise BootstrapError('GitHub schema requires configured broker')
        dashboard = (cls._dashboard_config(info['dashboard'])
                     if info['schema'] in {3, 4, 5, 6} else None)
        mcp = (cls._mcp_config(
                    info["mcp"],
                    require_package_pin=info["schema"] in {5, 6},
                    installed_companion=info["schema"] == 6,
                    install_root=locations["database"].parent.parent)
               if info["schema"] in {4, 5, 6} and info["mcp"] is not None else None)
        return cls(locations['database'],info['repository'],locations['canonical_root'],
                   locations['workspace'],locations['health_directory'],exe,checksum,
                   tuple(args),pins,float(info['timeout_seconds']),github,dashboard,mcp)

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
        if self.mcp is not None:
            service.enable_local_mcp(
                interpreter_pin=self.mcp.interpreter_pin,
                package_root=(self.mcp.package_root
                              or Path(__file__).resolve().parent.parent),
                response_timeout=self.mcp.response_timeout,
                package_pin=self.mcp.package_pin)
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


def _self_test() -> int:
    """Exercise bundled engine + durable storage offline, without credentials."""
    import tempfile
    from .state_store import StateStore
    with tempfile.TemporaryDirectory(prefix="atlas-engine-self-test-") as root:
        with StateStore(Path(root) / "selftest.sqlite") as store:
            if store.missions() or store.background_suspended():
                return 2
    return 0


def _native_ipc_smoke(input_stream=None, *, output_stream=None) -> int:
    """Native frozen-engine proof; disposable DB, read-only key from stdin.

    The test host supplies exactly one private capability over a pipe. Nothing
    from this diagnostic entrypoint can start a mission, modify a repository,
    touch an installed config, or create a durable authorization.
    """
    import os
    import sys
    import tempfile
    import time
    from .dashboard_ipc import DashboardIpcServer
    from .state_store import StateStore

    if os.name != 'nt':
        return 2
    incoming = sys.stdin.buffer if input_stream is None else input_stream
    outgoing = sys.stdout if output_stream is None else output_stream
    if incoming is None or outgoing is None or incoming.isatty():
        return 2
    try:
        key = incoming.read(33)
    except (OSError, ValueError, AttributeError):
        return 2
    if type(key) is not bytes or len(key) != 32:
        return 2
    with tempfile.TemporaryDirectory(prefix='atlas-frozen-engine-ipc-') as tmp:
        database = Path(tmp) / 'actual-engine.sqlite'
        with StateStore(database):
            pass
        with DashboardIpcServer.for_database(
                database, engine_id='frozen-engine-smoke-v1',
                secret=key) as server:
            import json
            outgoing.write(json.dumps({
                'engine_id': 'frozen-engine-smoke-v1',
                'port': server.port,
                'schema': 1,
            }, sort_keys=True) + '\n')
            outgoing.flush()
            # Set only after the engine has emitted a complete authenticated
            # snapshot on the wire; a forged request or failed projection
            # cannot satisfy this native diagnostic proof.
            return 0 if server._server.valid_read_replied.wait(timeout=18) else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='atlas-holding-daemon')
    parser.add_argument('--config',type=Path)
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--native-ipc-smoke',action='store_true')
    args=parser.parse_args(argv)
    if args.self_test and args.native_ipc_smoke:
        return 2
    if args.native_ipc_smoke:
        if args.config is not None:
            return 2
        return _native_ipc_smoke()
    if args.self_test:
        if args.config is not None:
            return 2
        return _self_test()
    if args.config is None:
        parser.error('--config required outside self-test')
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
