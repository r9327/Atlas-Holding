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
from .daemon_lease import DaemonLease, LeaseError
from .process_agent import AgentInterrupted
from .state_store import StateStore, StoreError


class Worker(Protocol):
    def __call__(self, mission: Mission) -> object: ...


class WorkerDeferred(RuntimeError):
    """A safe precondition is temporarily absent; consume no retry budget."""


class WorkerInspectionRequired(RuntimeError):
    """A mutation may have happened and must never be replayed blindly."""


class WorkerBlocked(RuntimeError):
    """A deterministic safety gate refuses this mission."""


@dataclass(frozen=True)
class Tick:
    action: str
    mission_id: str | None
    reason: str


class HoldingEngine:
    def __init__(self, db_path: str | Path, worker: Worker, *, online: Callable[[], bool],
                 supported_operations: frozenset[str] | None = None):
        self.store = StateStore(db_path)
        self.worker = worker
        self.online = online
        self.supported_operations = supported_operations
        self._stop = threading.Event()
        self._started = False
        self.lease = DaemonLease(db_path)

    def start(self) -> tuple[str, ...]:
        """Call once before dispatch; quarantine uncheckpointed in-flight jobs."""
        if self._started:
            raise StoreError('Engine already started')
        # Never quarantine tasks belonging to another active daemon.
        self.lease.acquire()
        try:
            # Back up and migrate before interrupted tasks may resume.
            from .migrations import SchemaMigrator
            SchemaMigrator(self.store, backup_dir=self.store.path.parent / 'backups').ensure(
                lease_owner=self.lease.owner)
            interrupted = self.store.quarantine_interrupted(lease_owner=self.lease.owner)
        except BaseException:
            self.lease.release()
            raise
        self._started = True
        return interrupted

    def pause(self) -> None:
        self.store.set_paused(True)

    def resume(self) -> None:
        self.store.set_paused(False)

    def stop(self) -> None:
        """Graceful stop: finish the current synchronous operation/checkpoint."""
        self._stop.set()
        with self.store._transaction():
            self.store.db.execute("INSERT INTO settings(key,value) VALUES('stop_requested','1') "
                                  "ON CONFLICT(key) DO UPDATE SET value='1'")
            self.store._event(None, 'STOP_REQUESTED')

    def reset_stop(self) -> None:
        """Trusted control-plane only: explicitly clear a persistent stop."""
        with self.store._transaction():
            self.store.db.execute("INSERT INTO settings(key,value) VALUES('stop_requested','0') "
                                  "ON CONFLICT(key) DO UPDATE SET value='0'")
            self.store._event(None, 'STOP_RESET')
        self._stop.clear()

    def _stop_requested(self) -> bool:
        row = self.store.db.execute("SELECT value FROM settings WHERE key='stop_requested'").fetchone()
        return row is not None and row[0] == '1'

    def tick(self) -> Tick:
        if not self._started:
            raise StoreError('Start engine to recover interrupted writers before dispatch')
        if self._stop.is_set() or self._stop_requested():
            return Tick('STOPPED', None, 'Stop requested')
        self.lease.assert_owned()
        # A persisted Pause/STOP must not initiate any reachability probe.
        # The atomic claim repeats this check under SQLite's writer lock.
        if self.store.background_suspended():
            return Tick('WAIT', None, 'Holding paused or stopped')
        try:
            # A broken network detector must not take down offline coding.
            # Only an actual bool True can authorize external-required work.
            # Offline-only roadmaps must not poll an unrelated network API
            # on every idle tick. A final SQLite transaction still gates claims.
            requires_network = self.store.db.execute(
                "SELECT 1 FROM missions WHERE state='READY' "
                "AND external_required=1 LIMIT 1"
            ).fetchone() is not None
            observed_online = False
            if requires_network:
                try:
                    observed_online = self.online()
                except (OSError, TimeoutError, ValueError):
                    observed_online = False
            online = observed_online is True
            choice = self.store.claim_next(online=online, lease_owner=self.lease.owner,
                                           supported_operations=self.supported_operations)
        except StoreError as exc:
            if str(exc) == 'DAEMON_LEASE_LOST':
                self._stop.set()
                return Tick('LEASE_LOST', None, 'Lease lost during task claim')
            raise
        if choice.action != 'RUN' or not choice.mission_id:
            return Tick(choice.action, choice.mission_id, choice.reason)
        mission = next(m for m in self.store.missions() if m.id == choice.mission_id)
        try:
            # Actual execution must be implemented by a trusted, separately
            # permission-checked adapter. Never execute a raw LLM command here.
            outcome = self.worker(mission)
            self.lease.assert_owned()
            from .sync_worker import VerifiedGitHubSync
            if isinstance(outcome, VerifiedGitHubSync):
                self.store.complete_github_sync_mission(
                    mission.id, sync_operation=outcome.sync_operation,
                    branch=outcome.branch, candidate_sha=outcome.candidate_sha,
                    idempotency_key=outcome.idempotency_key,
                    lease_owner=self.lease.owner)
                return Tick('DONE', mission.id, 'Verified external effect')
            if not isinstance(outcome, str):
                raise TypeError('Trusted worker returned an unsupported outcome')
            self.store.checkpoint(
                mission.id, outcome, lease_owner=self.lease.owner)
        except WorkerDeferred as deferred:
            self.store.defer_running(
                mission.id, type(deferred).__name__, lease_owner=self.lease.owner)
            return Tick('WAIT_FOR_NETWORK', mission.id, str(deferred))
        except WorkerInspectionRequired as uncertain:
            self.store.interrupt_running(
                mission.id, lease_owner=self.lease.owner,
                reason=str(uncertain))
            return Tick('RECOVERY_REQUIRED', mission.id, str(uncertain))
        except WorkerBlocked as blocked:
            self.store.block_running(
                mission.id, str(blocked), lease_owner=self.lease.owner)
            return Tick('BLOCKED', mission.id, str(blocked))
        except AgentInterrupted:
            try:
                self.store.interrupt_running(
                    mission.id, lease_owner=self.lease.owner,
                    reason="Agent interrupted by supervisor; inspect Git journal")
            except StoreError as fence:
                if str(fence) == 'DAEMON_LEASE_LOST':
                    self._stop.set()
                    return Tick('LEASE_LOST', mission.id, 'Daemon lost its lease')
                raise
            return Tick('RECOVERY_REQUIRED', mission.id,
                        'Worker was preempted; no retry budget consumed')
        except (LeaseError, StoreError) as failure:
            if isinstance(failure, StoreError) and str(failure) == 'UNCHANGED_CANDIDATE_SHA':
                # Count lack of forward progress instead of crashing the daemon
                # or falsely reusing a previous signed receipt.
                try:
                    self.store.fail_running(
                        mission.id, 'UNCHANGED_CANDIDATE_SHA',
                        lease_owner=self.lease.owner)
                except StoreError as fence:
                    if str(fence) == 'DAEMON_LEASE_LOST':
                        self._stop.set()
                        return Tick('LEASE_LOST', mission.id, 'Lease lost during no-progress handling')
                    raise
                return Tick('RETRY_OR_BLOCKED', mission.id, 'UNCHANGED_CANDIDATE_SHA')
            if isinstance(failure, StoreError) and str(failure) != 'DAEMON_LEASE_LOST':
                raise
            self._stop.set()
            # Never release/quarantine a task from a stale supervisor.
            return Tick('LEASE_LOST', mission.id, 'Lease lost; recovery inspection required')
        except Exception as exc:
            # An old daemon must never reset a mission after replacement.
            # The fence is checked inside BEGIN IMMEDIATE, not just before it.
            failure_reason = type(exc).__name__
            structured_code = getattr(exc, 'code', None)
            if isinstance(structured_code, str) and structured_code:
                failure_reason += ':' + structured_code[:80]
            try:
                self.store.fail_running(mission.id, failure_reason,
                                        lease_owner=self.lease.owner)
            except StoreError as fence:
                if str(fence) == 'DAEMON_LEASE_LOST':
                    self._stop.set()
                    return Tick('LEASE_LOST', mission.id, 'Lease lost during task failure')
                raise
            return Tick('RETRY_OR_BLOCKED', mission.id, failure_reason)
        return Tick('QA_PENDING', mission.id, 'Awaiting authenticated tests and Doctor evidence')

    def run(self, *, idle_seconds: float = 2, max_ticks: int | None = None) -> Tick:
        if not 0.05 <= idle_seconds <= 3600:
            raise ValueError('Invalid polling interval')
        count = 0
        while not self._stop.is_set():
            result = self.tick()
            count += 1
            if result.action in {'COMPLETE','STOPPED','LEASE_LOST'} or (max_ticks is not None and count >= max_ticks):
                return result
            if result.action != 'QA_PENDING':
                self._stop.wait(idle_seconds)
        return Tick('STOPPED', None, 'Stop requested')

    def close(self) -> None:
        self.lease.release()
        self.store.close()

    def __enter__(self) -> 'HoldingEngine':
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
