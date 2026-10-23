"""Non-blocking Qt view of authenticated engine snapshots.

The GUI never receives a SQLite path, daemon lease or execution handle. A
single background transport read runs at a time; closing the window cancels
future polls without interrupting the engine or waiting on a hung network.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from time import monotonic
from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton, QWidget

from .control_center_ui import ControlCenterWindow
from .control_center_state import MAX_SNAPSHOT_AGE_SECONDS
from .dashboard_ipc import DashboardControlClient, DashboardIpcClient, DashboardIpcError


class LiveControlCenterWindow(ControlCenterWindow):
    def __init__(self, client: DashboardIpcClient, *,
                 control: DashboardControlClient | None = None,
                 refresh_ms: int = 1500):
        if not isinstance(client, DashboardIpcClient):
            raise ValueError("Authenticated dashboard client required")
        if type(refresh_ms) is not int or not 250 <= refresh_ms <= 10_000:
            raise ValueError("Dashboard refresh must be 250-10000 milliseconds")
        if control is not None and not isinstance(control, DashboardControlClient):
            raise ValueError("A separately authenticated write capability is required")
        if control is not None and control.port != client.port:
            raise ValueError("Dashboard read/write endpoints must be the same service")
        super().__init__(trusted_engine_id=client.engine_id)
        self._client = client
        self._closing = False
        self._control = control
        self._control_pending: Future | None = None
        self._control_name: str | None = None
        # No second command until a later authenticated SQLite projection
        # confirms the first. An ambiguous acknowledgement is never retried.
        self._awaiting_confirmation: tuple[str, int] | None = None
        self._connected = False
        self._paused: bool | None = None
        self._stopped: bool | None = None
        self._control_report: str | None = None
        self.control_panel = QWidget(self)
        self.control_panel.setObjectName("controlPanel")
        row = QHBoxLayout(self.control_panel)
        self.pause_button = QPushButton("Pause", self)
        self.pause_button.setObjectName("pauseHolding")
        self.resume_button = QPushButton("Reprendre", self)
        self.resume_button.setObjectName("resumeHolding")
        self.stop_button = QPushButton("Arrêter", self)
        self.stop_button.setObjectName("stopHolding")
        for button in (self.pause_button, self.resume_button, self.stop_button):
            row.addWidget(button)
        self.pause_button.clicked.connect(self.request_pause)
        self.resume_button.clicked.connect(self.request_resume)
        self.stop_button.clicked.connect(self.request_stop)
        self.centralWidget().layout().insertWidget(1, self.control_panel)
        self._update_control_buttons()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="atlas-dashboard-ipc")
        self._pending: Future | None = None
        self._closing = False
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(refresh_ms)
        self._poll_timer.timeout.connect(self.poll_live_state)
        self._poll_timer.start()
        self.poll_live_state()

    def _has_fresh_state(self) -> bool:
        """Do not authorize a command using an expired authenticated view."""
        received = self._last_received_at
        if received is None or not self._connected:
            return False
        age = monotonic() - received
        return 0 <= age < MAX_SNAPSHOT_AGE_SECONDS

    def _update_control_buttons(self) -> None:
        enabled = (not self._closing and self._has_fresh_state()
                   and self._control is not None
                   and self._control_pending is None
                   and self._awaiting_confirmation is None)
        self.pause_button.setEnabled(
            enabled and self._paused is False and self._stopped is False)
        self.resume_button.setEnabled(
            enabled and self._paused is True and self._stopped is False)
        self.stop_button.setEnabled(enabled and self._stopped is False)

    def _request_command(self, name: str) -> None:
        if (self._closing or not self._has_fresh_state() or self._control is None
                or self._control_pending is not None
                or self._awaiting_confirmation is not None):
            return
        permitted = {
            "pause": self._paused is False and self._stopped is False,
            "resume": self._paused is True and self._stopped is False,
            "stop": self._stopped is False,
        }
        if permitted.get(name) is not True:
            return
        # Snapshot reads may have started before the mutation. Only a later
        # durable sequence can confirm the command was actually applied.
        self._awaiting_confirmation = (
            name, self.displayed_sequence if self.displayed_sequence is not None else -1
        )
        self._control_name = name
        self._control_pending = self._executor.submit(self._control.send, name)
        self._update_control_buttons()

    @Slot()
    def request_pause(self) -> None:
        self._request_command("pause")

    @Slot()
    def request_resume(self) -> None:
        self._request_command("resume")

    @Slot()
    def request_stop(self) -> None:
        if (self._control is not None and self._has_fresh_state()
                and self._control_pending is None
                and self._awaiting_confirmation is None
                and QMessageBox.question(
                    self, "Arrêter la Holding",
                    "Confirmer la demande d’arrêt durable du moteur ?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                ) == QMessageBox.StandardButton.Yes):
            self._request_command("stop")

    @Slot()
    def poll_live_state(self) -> None:
        """Runs on the Qt thread, never blocks on sockets or SQLite."""
        if self._closing:
            return
        # Check every timer tick, including while network work is still
        # pending; a stale snapshot must disable all write buttons.
        self._update_control_buttons()
        if self._control_pending is not None and self._control_pending.done():
            completed = self._control_pending
            name = self._control_name
            self._control_pending = None
            self._control_name = None
            try:
                completed.result()
            except Exception:
                self._control_report = (
                    f"Commande {name} non confirmée — inspecter l’état durable")
            else:
                self._control_report = (
                    f"Commande {name} enregistrée — vérification en cours")
            self._update_control_buttons()
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
            self._connected = False
            self.mark_local_hub_unverified()
            self.status.setText(
                "Déconnecté — dernier état conservé, non actualisé")
        except Exception:
            self._connected = False
            self.mark_local_hub_unverified()
            self.status.setText("État indisponible — dernière vue non actualisée")
        else:
            self._connected = True
            self._paused = state.paused
            self._stopped = state.stop_requested
            if self._awaiting_confirmation is not None:
                action, previous_sequence = self._awaiting_confirmation
                confirmed = (
                    state.sequence > previous_sequence and
                    (state.stop_requested is True if action == "stop" else
                     state.paused is (action == "pause") and
                     state.stop_requested is False)
                )
                if confirmed:
                    self._awaiting_confirmation = None
                    self._control_report = None
            if self.displayed_sequence is not None and (
                    state.sequence == self.displayed_sequence):
                # A new verified request/response pair is a heartbeat even
                # without SQLite writes. Do not re-render rows or reset
                # replay protections; simply renew connection freshness.
                self.acknowledge_authenticated_state(state)
                self.status.setText(
                    f"{state.network} — séquence {state.sequence} — "
                    f"{state.pending_external} vérification(s) externe(s) en attente")
            else:
                self.render_state(state)
            if state.stop_requested is True:
                self.status.setText("ARRÊT DEMANDÉ — " + self.status.text())
            elif state.paused is True:
                self.status.setText("EN PAUSE — " + self.status.text())
            if self._control_report is not None:
                self.status.setText(self._control_report)
                # Keep the warning visible until a real newer state confirms
                # the command or the operator closes the session.
        self._update_control_buttons()

    def closeEvent(self, event) -> None:
        self._closing = True
        self._poll_timer.stop()
        # The transport call has a 3s socket timeout. No callbacks touch Qt
        # after close, and the worker never holds or controls daemon resources.
        self._executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)
