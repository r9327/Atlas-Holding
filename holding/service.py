"""Headless Atlas Holding host, independent from the graphical Control Center.

A trusted agent adapter is mandatory. In particular, the service never treats
lack of an agent as a successful (or even attempted) code-writing operation.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import threading
import time
from typing import Callable, Protocol

from .autonomy import Mission
from .engine import HoldingEngine, Tick
from .git_worktree import GitWorktrees
from .recovery import RecoveryCoordinator, RecoveryOutcome
from .resource_guard import ResourceGuard
from .daemon_lease import LeaseError
from .state_store import StoreError
from .worker_router import OperationWorkerRouter


class AgentUnavailable(RuntimeError):
    """No healthy, trusted coding adapter is installed."""


class TrustedWorker(Protocol):
    def ready(self) -> bool: ...
    def __call__(self, mission: Mission) -> str: ...


@dataclass(frozen=True)
class ServiceSettings:
    database: Path
    idle_seconds: float = 2.0

    def __post_init__(self):
        if not self.database.is_absolute():
            raise ValueError("The service database path must be absolute")
        if not 0.05 <= self.idle_seconds <= 3600:
            raise ValueError("The polling interval must be between 0.05 and 3600 seconds")


def default_database() -> Path:
    """Persist outside executable/install folders for normal Windows upgrades."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return (base / "AtlasHolding" / "holding.sqlite").resolve()


class HoldingService:
    """Own one daemon lease and honor persistent UI control operations.

    The adapter is instantiated by a trusted bootstrap, never from a roadmap,
    environment-provided Python expression, or arbitrary LLM shell command.
    """

    def __init__(self, settings: ServiceSettings, worker: TrustedWorker,
                 *, online: Callable[[], bool], recovery_git: GitWorktrees | None = None):
        if not callable(worker) or not callable(getattr(worker, "ready", None)):
            raise AgentUnavailable("A trusted worker with a health check is mandatory")
        self.settings = settings
        self.worker = worker
        self.engine = HoldingEngine(settings.database, worker, online=online)
        self._stop = threading.Event()
        self._started = False
        self._recovery_git = recovery_git
        self.resource_guard = ResourceGuard(settings.database.parent)
        self.last_recovery: tuple[RecoveryOutcome, ...] = ()
        self._recovery_seen: dict[str, int] = {}
        self._qa_repair = None
        self._git = recovery_git
        self._github_health = None
        self._dashboard_ipc = None

    @classmethod
    def from_local_agent(cls, settings: ServiceSettings, *, program,
                         git: GitWorktrees, health_directory: Path,
                         online: Callable[[], bool]) -> "HoldingService":
        """Trusted setup only: bind the fixed local binary to leased Git writes.

        The user-onboarded config must choose the binary and repository. A
        roadmap must never choose a process, arguments, or executable path.
        """
        from .executor import AuthorizedGit
        from .process_agent import LocalProcessAgent
        from .code_worker import LocalCodeWorker

        class _Unbound:
            def ready(self) -> bool:
                return False
            def __call__(self, mission: Mission) -> str:
                raise AgentUnavailable('Worker not bound')

        service = cls(settings, _Unbound(), online=online, recovery_git=git)
        authenticated_git = AuthorizedGit(service.engine.store, git, service.engine.lease)
        last_fence_check = [0.0]
        last_resource_check = [0.0]
        def should_stop_agent() -> bool:
            if service._stop.is_set() or service.engine.store.background_suspended():
                return True
            # Health probes run before acquiring the daemon lease. Once
            # started, revoke execution quickly if another daemon takes over.
            now = time.monotonic()
            if service._started and now - last_fence_check[0] >= 1.0:
                last_fence_check[0] = now
                try:
                    service.engine.lease.assert_owned()
                except LeaseError:
                    return True
            # Resource pressure may appear AFTER the initial dispatch check.
            # Poll at most once per second; never sample disk/RAM every 25 ms.
            if service._started and now - last_resource_check[0] >= 1.0:
                last_resource_check[0] = now
                if not service.resource_guard.check().safe:
                    return True
            return False

        adapter = LocalProcessAgent(program, should_stop=should_stop_agent)
        worker = LocalCodeWorker(authenticated_git, adapter, health_directory=health_directory)
        router = OperationWorkerRouter({'code': worker})
        service.worker = router
        service.engine.worker = router
        # The currently implemented local adapter edits code only. Other
        # roadmap operations await separately authenticated role adapters.
        service.engine.supported_operations = frozenset({'code'})
        from .qa_repair import QaRepairCoordinator
        service._qa_repair = QaRepairCoordinator(
            service.engine.store, git, service.engine.lease)
        return service

    def enable_git_push(
        self, *, expected_account_id: str, expected_policy_sha: str,
        control_factory=None, health_probe=None,
    ) -> None:
        """Trusted bootstrap hook; never callable from roadmap/model content."""
        if self._started or self._git is None:
            raise RuntimeError("Git push role must be configured before service start")
        from .sync_worker import GitPushMissionWorker

        if not isinstance(self.worker, OperationWorkerRouter):
            raise RuntimeError("Service worker router is unavailable")
        adapter = GitPushMissionWorker(
            self.engine.store, self._git, self.engine.lease,
            expected_account_id=expected_account_id,
            expected_policy_sha=expected_policy_sha,
            control_factory=control_factory)
        self.worker.add('push', adapter)
        self.engine.supported_operations = frozenset({'code', 'push'})
        if health_probe is not None:
            from .github_health import GitHubHealthMonitor
            self._github_health = GitHubHealthMonitor(
                self.engine.store, self.engine.lease, health_probe,
                expected_account_id=expected_account_id)
            self.engine.online = self._github_health.online

    def _available_operations(self) -> frozenset[str] | None:
        provider = getattr(self.worker, 'available_operations', None)
        if not callable(provider):
            return self.engine.supported_operations
        return provider()

    def _worker_ready(self, available: frozenset[str] | None) -> bool:
        if callable(getattr(self.worker, 'available_operations', None)):
            return bool(available)
        return self.worker.ready()

    def _recovery_generations(self) -> dict[str, int]:
        """A quarantined task is inspected once per interruption/operator request."""
        if self._recovery_git is None:
            return {}
        rows = self.engine.store.db.execute("""
            SELECT m.id, COALESCE(MAX(e.seq), 0) AS generation
            FROM missions m
            LEFT JOIN events e ON e.mission_id=m.id
              AND e.event IN ('RUNNING_TO_RECOVERY_REQUIRED',
                              'RECOVERY_SCAN_REQUESTED')
            WHERE m.state='RECOVERY_REQUIRED' AND m.repo=?
            GROUP BY m.id
        """, (self._recovery_git.repository,))
        return {row['id']: int(row['generation']) for row in rows}

    def _recover_new_interruptions(self) -> None:
        current = self._recovery_generations()
        self._recovery_seen = {
            mission_id: gen for mission_id, gen in self._recovery_seen.items()
            if mission_id in current
        }
        if any(self._recovery_seen.get(mid) != gen for mid, gen in current.items()):
            self.last_recovery = RecoveryCoordinator(
                self.engine.store, self._recovery_git, self.engine.lease
            ).recover_all()
            self._recovery_seen.update(current)
            # Missions recovered to READY or QA_PENDING must not remain cached.
            active = self._recovery_generations()
            self._recovery_seen = {
                mid: gen for mid, gen in self._recovery_seen.items() if mid in active
            }

    def start(self) -> tuple[str, ...]:
        if self._started:
            raise RuntimeError("Service has already started")
        # Persisted Pause/STOP must survive reboot without launching even
        # a health subprocess. The supervisor may boot suspended so its
        # Control Center can later issue Resume without requiring model access.
        suspended = self.engine.store.background_suspended()
        available = self._available_operations()
        if not suspended and (
                available is not None and not available
                or not self._worker_ready(available)):
            raise AgentUnavailable("Coding agent unavailable; refusing to claim tasks")
        quarantined = self.engine.start()
        self._started = True
        # Onboarding provides the exact canonical Git checkout; without it,
        # recovery remains quarantined rather than making assumptions.
        if self._recovery_git is not None and not suspended:
            self.last_recovery = RecoveryCoordinator(
                self.engine.store, self._recovery_git, self.engine.lease
            ).recover_all()
            self._recovery_seen = self._recovery_generations()
        elif suspended:
            # Keep interrupted jobs quarantined until the operator resumes.
            # The first healthy run tick will inspect them, not blindly replay.
            self.last_recovery = ()
            self._recovery_seen = {}
        return quarantined

    def enable_dashboard_ipc(self, *, secret: bytes, engine_id: str) -> int:
        """Trusted host only: expose sanitised state, never engine commands.

        The installation supervisor owns and provisions the secret. It MUST
        enforce native Windows ACLs before passing a key to this method.
        """
        if not self._started or self._dashboard_ipc is not None:
            raise RuntimeError("Dashboard may be enabled once after service start")
        from .dashboard_ipc import DashboardIpcServer
        server = DashboardIpcServer.for_database(
            self.settings.database, secret=secret, engine_id=engine_id,
        )
        try:
            server.start()
        except BaseException:
            server.close()
            raise
        self._dashboard_ipc = server
        return server.port

    def request_shutdown(self) -> None:
        """Cooperative shutdown: no DB operations inside a signal handler."""
        self._stop.set()

    def run(self, *, max_ticks: int | None = None) -> Tick:
        if not self._started:
            raise RuntimeError("Call start() before run()")
        if max_ticks is not None and max_ticks < 1:
            raise ValueError("max_ticks must be >= 1")
        iterations = 0
        while not self._stop.is_set():
            # Do not claim new work without a healthy agent. The daemon keeps its
            # lease and may resume once the adapter passes its real health check.
            # Persistent STOP is never delayed by resource pressure or a dead model.
            if self.engine._stop_requested():
                result = Tick("STOPPED", None, "Stop requested")
            elif self.engine.store._paused():
                # Do not even start health probes or models during Pause.
                result = Tick("WAIT", None, "Holding paused")
            else:
                if self._github_health is not None:
                    try:
                        self._github_health.refresh()
                    except (LeaseError, StoreError) as failure:
                        if (isinstance(failure, StoreError)
                                and str(failure) != 'DAEMON_LEASE_LOST'):
                            raise
                        return Tick(
                            'LEASE_LOST', None,
                            'Lease lost during GitHub health probe')
                resources = self.resource_guard.check()
                if not resources.safe:
                    result = Tick("RESOURCE_PRESSURE", None, resources.reason)
                else:
                    available = self._available_operations()
                    if available is not None:
                        self.engine.supported_operations = available
                    if ((available is None or available)
                            and self._worker_ready(available)):
                        if self._recovery_git is not None:
                            try:
                                self._recover_new_interruptions()
                            except LeaseError:
                                return Tick('LEASE_LOST', None, 'Lease lost during recovery')
                        # Signed QA FAIL may safely requeue the SAME mission on
                        # a clean exact-SHA branch. Independent missions continue.
                        if self._qa_repair is not None:
                            try:
                                self._qa_repair.requeue_one()
                            except (LeaseError, StoreError) as failure:
                                if isinstance(failure, StoreError) and str(failure) != 'DAEMON_LEASE_LOST':
                                    raise
                                return Tick('LEASE_LOST', None, 'Lease lost during QA repair')
                        result = self.engine.tick()
                    else:
                        result = Tick("AGENT_UNAVAILABLE", None,
                                      "No trusted role adapter is ready")
            iterations += 1
            if result.action in {"STOPPED", "LEASE_LOST"}:
                return result
            if max_ticks is not None and iterations >= max_ticks:
                return result
            # COMPLETE is an idle queue, NOT permission to exit forever. The
            # Control Center may import new roadmap missions while we are idle.
            if result.action != "QA_PENDING":
                self._stop.wait(self.settings.idle_seconds)
        # Shutdown persists a STOP command for a deterministic next boot.
        self.engine.stop()
        return Tick("STOPPED", None, "Shutdown requested")

    def close(self) -> None:
        if self._dashboard_ipc is not None:
            self._dashboard_ipc.close()
            self._dashboard_ipc = None
        self.engine.close()
        self._started = False

    def __enter__(self) -> "HoldingService":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@contextmanager
def termination_signals(service: HoldingService):
    """SIGINT/SIGTERM on main thread; restore previous handlers on exit."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    old_handlers = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda _signum, _frame: service.request_shutdown())
        yield
    finally:
        for sig, previous in old_handlers.items():
            signal.signal(sig, previous)
