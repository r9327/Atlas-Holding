"""Crash-safe, deliberately sequential Holding dispatch loop.

Worker is a trusted adapter, not model-generated shell text. An iteration
never marks a job DONE; it moves to QA_PENDING with its actual commit SHA.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Callable, Protocol

from .autonomy import Mission
from .state_store import StateStore, StoreError


class Worker(Protocol):
    def __call__(self, mission: Mission) -> str: ...


@dataclass(frozen=True)
class Tick:
    action: str
    mission_id: str | None
    reason: str


class HoldingEngine:
    def __init__(self, db_path: str | Path, worker: Worker, *, online: Callable[[], bool]):
        self.store = StateStore(db_path)
        self.worker = worker
        self.online = online
        self._stop = threading.Event()
        self._started = False

    def start(self) -> tuple[str, ...]:
        """Call once before dispatch; quarantine uncheckpointed in-flight jobs."""
        if self._started:
            raise StoreError('Engine already started')
        self._started = True
        return self.store.quarantine_interrupted()

    def pause(self) -> None:
        self.store.set_paused(True)

    def resume(self) -> None:
        self.store.set_paused(False)

    def stop(self) -> None:
        """Graceful stop: finish the current synchronous operation/checkpoint."""
        self._stop.set()

    def tick(self) -> Tick:
        if not self._started:
            raise StoreError('Start engine to recover interrupted writers before dispatch')
        if self._stop.is_set():
            return Tick('STOPPED', None, 'Stop requested')
        choice = self.store.claim_next(online=bool(self.online()))
        if choice.action != 'RUN' or not choice.mission_id:
            return Tick(choice.action, choice.mission_id, choice.reason)
        mission = next(m for m in self.store.missions() if m.id == choice.mission_id)
        try:
            # Actual execution must be implemented by a trusted, separately
            # permission-checked adapter. Never execute a raw LLM command here.
            sha = self.worker(mission)
            self.store.checkpoint(mission.id, sha)
        except Exception as exc:
            # Avoid persisting secrets/exception contents that may contain tokens.
            self.store.fail_running(mission.id, type(exc).__name__)
            return Tick('RETRY_OR_BLOCKED', mission.id, type(exc).__name__)
        return Tick('QA_PENDING', mission.id, 'Awaiting authenticated tests and Doctor evidence')

    def run(self, *, idle_seconds: float = 2, max_ticks: int | None = None) -> Tick:
        if not 0.05 <= idle_seconds <= 3600:
            raise ValueError('Invalid polling interval')
        count = 0
        while not self._stop.is_set():
            result = self.tick()
            count += 1
            if result.action == 'COMPLETE' or (max_ticks is not None and count >= max_ticks):
                return result
            if result.action != 'QA_PENDING':
                self._stop.wait(idle_seconds)
        return Tick('STOPPED', None, 'Stop requested')

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> 'HoldingEngine':
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
