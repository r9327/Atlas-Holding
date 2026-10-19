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
        def should_stop_agent() -> bool:
            if service._stop.is_set() or service.engine._stop_requested():
                return True
            # During initial health checking no lease is held yet. Once started,
            # an obsolete daemon must terminate its worker without waiting for
            # the model timeout or allowing any more filesystem writes.
            now = time.monotonic()
            if service._started and now - last_fence_check[0] >= 1.0:
                last_fence_check[0] = now
                try:
                    service.engine.lease.assert_owned()
                except LeaseError:
                    return True
            return False

        adapter = LocalProcessAgent(program, should_stop=should_stop_agent)
        worker = LocalCodeWorker(authenticated_git, adapter, health_directory=health_directory)
        service.worker = worker
        service.engine.worker = worker
        return service

    def start(self) -> tuple[str, ...]:
        if self._started:
            raise RuntimeError("Service has already started")
        if not self.worker.ready():
            raise AgentUnavailable("Coding agent unavailable; refusing to claim tasks")
        quarantined = self.engine.start()
        self._started = True
        # Onboarding provides the exact canonical Git checkout; without it,
        # recovery remains quarantined rather than making assumptions.
        if self._recovery_git is not None:
            self.last_recovery = RecoveryCoordinator(
                self.engine.store, self._recovery_git, self.engine.lease
            ).recover_all()
        return quarantined

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
            else:
                resources = self.resource_guard.check()
                if not resources.safe:
                    result = Tick("RESOURCE_PRESSURE", None, resources.reason)
                elif self.worker.ready():
                    result = self.engine.tick()
                else:
                    result = Tick("AGENT_UNAVAILABLE", None, "Coding agent unavailable")
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
