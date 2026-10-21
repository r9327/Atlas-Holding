"""Non-blocking Qt view of authenticated engine snapshots.

The GUI never receives a SQLite path, daemon lease or execution handle. A
single background transport read runs at a time; closing the window cancels
future polls without interrupting the engine or waiting on a hung network.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from PySide6.QtCore import QTimer

from .control_center_ui import ControlCenterWindow
from .dashboard_ipc import DashboardIpcClient, DashboardIpcError


class LiveControlCenterWindow(ControlCenterWindow):
    def __init__(self, client: DashboardIpcClient, *, refresh_ms: int = 1500):
        if not isinstance(client, DashboardIpcClient):
            raise ValueError("Authenticated dashboard client required")
        if type(refresh_ms) is not int or not 250 <= refresh_ms <= 10_000:
            raise ValueError("Dashboard refresh must be 250-10000 milliseconds")
        super().__init__(trusted_engine_id=client.engine_id)
        self._client = client
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="atlas-dashboard-ipc")
        self._pending: Future | None = None
        self._closing = False
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(refresh_ms)
        self._poll_timer.timeout.connect(self.poll_live_state)
        self._poll_timer.start()
        self.poll_live_state()

    def poll_live_state(self) -> None:
        """Runs on the Qt thread, never blocks on sockets or SQLite."""
        if self._closing:
            return
        if self._pending is None:
            self._pending = self._executor.submit(self._client.fetch)
            return
        if not self._pending.done():
            return
        result = self._pending
        self._pending = None
        try:
            state = result.result()
        except (DashboardIpcError, OSError, ValueError):
            # Old rows may remain for diagnosis, but are never presented as live.
            self.status.setText(
                "Déconnecté — dernier état conservé, non actualisé")
        except Exception:
            self.status.setText("État indisponible — dernière vue non actualisée")
        else:
            if self.displayed_sequence is not None and (
                    state.sequence == self.displayed_sequence):
                # Same durable state; connection was authenticated again.
                self.status.setText(
                    f"{state.network} — séquence {state.sequence} — "
                    f"{state.pending_external} vérification(s) externe(s) en attente")
            else:
                self.render_state(state)

    def closeEvent(self, event) -> None:
        self._closing = True
        self._poll_timer.stop()
        # The transport call has a 3s socket timeout. No callbacks touch Qt
        # after close, and the worker never holds or controls daemon resources.
        self._executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)
