"""Trusted internal MCP hub supervisor with a durable engine-readable journal.

Only the Holding daemon's trusted bootstrap can construct this supervisor;
roadmaps and MCP client arguments cannot select the executable, SQL database,
capabilities or control lifecycle. This module never starts external plugins.
"""
from __future__ import annotations

from pathlib import Path
import os
from threading import RLock

from .mcp_hub_journal import HubObservationJournal, DurableHubObservation
from .mcp_hub_session import LocalMcpHubSession, McpSessionObservation
from .mcp_local_inventory import PinnedLocalPlugin
from .mcp_package_pin import McpPackagePin
from .mcp_windows_acl import require_private_windows_path


class McpSupervisorError(RuntimeError):
    pass


class InternalMcpSupervisor:
    """Resource- and STOP-gated internal hub with actual session observations."""

    def __init__(self, *, database: Path, interpreter_pin: PinnedLocalPlugin,
                 package_root: Path, response_timeout: float = 3.0,
                 package_pin: McpPackagePin | None = None) -> None:
        if (not isinstance(database, Path) or not database.is_absolute()
                or not database.is_file() or database.is_symlink()
                or type(interpreter_pin) is not PinnedLocalPlugin
                or not isinstance(package_root, Path) or not package_root.is_absolute()):
            raise ValueError("Only trusted provisioned hub database and interpreter")
        if os.name == "nt":
            require_private_windows_path(database)
            require_private_windows_path(interpreter_pin.executable)
        self._session = LocalMcpHubSession(
            interpreter_pin=interpreter_pin, package_root=package_root,
            response_timeout=response_timeout, package_pin=package_pin)
        self._journal = HubObservationJournal(database)
        self._lock = RLock()
        self._closed = False
        self._enabled = False

    def _record(self, observation: McpSessionObservation, *, now_epoch: int) -> DurableHubObservation:
        self._journal.record(
            observation, source_sha256=self._session._pin.expected_sha256,
            now_epoch=now_epoch,
        )
        result = self._journal.latest(now_epoch=now_epoch)
        if result is None:
            raise McpSupervisorError("Hub observation was not recorded")
        return result

    def step(self, *, background_allowed: bool, resources_safe: bool,
             now_epoch: int) -> DurableHubObservation:
        """One daemon-controlled iteration, no model input or network access."""
        if (type(background_allowed) is not bool or type(resources_safe) is not bool
                or type(now_epoch) is not int or not 0 <= now_epoch <= 4_102_444_800):
            raise ValueError("Invalid trusted MCP supervisor schedule")
        with self._lock:
            if self._closed:
                raise McpSupervisorError("Closed MCP supervisor cannot restart")
            if not background_allowed or not resources_safe:
                self._enabled = False
                self._session.close()
                return self._record(self._session.observe(), now_epoch=now_epoch)
            if not self._enabled:
                # An earlier Pause/STOP closes the previous session permanently.
                # A newly allowed tick constructs a new verified session.
                if self._session.observe().state == "CLOSED":
                    previous = self._session
                    self._session = LocalMcpHubSession(
                        interpreter_pin=previous._pin,
                        package_root=previous._root,
                        response_timeout=previous._timeout,
                        package_pin=previous._package_pin,
                    )
                self._enabled = True
            observed = self._session.observe()
            if observed.state in {"NOT_STARTED", "EXITED"}:
                if observed.state == "EXITED":
                    self._session.close()
                    old = self._session
                    self._session = LocalMcpHubSession(
                        interpreter_pin=old._pin,
                        package_root=old._root,
                        response_timeout=old._timeout,
                        package_pin=old._package_pin,
                    )
                try:
                    self._session.start()
                except Exception as exc:
                    # Failure to launch cannot appear to the Control Center
                    # as an agent or healthy external plugin.
                    self._enabled = False
                    self._session.close()
                    self._record(self._session.observe(), now_epoch=now_epoch)
                    raise McpSupervisorError("Internal MCP hub launch failed") from exc
            try:
                proof = self._session.discover()
            except Exception as exc:
                self._enabled = False
                self._session.close()
                self._record(self._session.observe(), now_epoch=now_epoch)
                raise McpSupervisorError("Internal MCP hub protocol verification failed") from exc
            # Never upgrade nonresponsive outcomes into success.
            return self._record(proof, now_epoch=now_epoch)

    def close(self, *, now_epoch: int | None = None) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._enabled = False
            try:
                self._session.close()
                if now_epoch is not None:
                    self._record(self._session.observe(), now_epoch=now_epoch)
            finally:
                self._journal.close()

    def __enter__(self) -> "InternalMcpSupervisor":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
