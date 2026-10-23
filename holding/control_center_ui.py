"""Passive PySide6 dashboard for validated Control Center snapshots.

This process is deliberately display-only.  It receives ``DashboardState``
instances that have crossed the authenticated snapshot boundary.
The optional control layer lives in control_center_live, not this viewer.
"""
from __future__ import annotations

from time import monotonic

from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .control_center_state import DashboardState, MAX_SNAPSHOT_AGE_SECONDS


class ControlCenterUiError(ValueError):
    """A validated state cannot safely replace the displayed state."""


def _table(headers: tuple[str, ...], object_name: str) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setObjectName(object_name)
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return table


def _replace_rows(table: QTableWidget, rows: tuple[tuple[str, ...], ...]) -> None:
    """Update only changed cells; avoid a full Qt item rebuild per snapshot.

    Repeated 2-second snapshots with 1,000 tasks used to throw away every
    row/item, causing allocations and disrupting selection and scroll position.
    The snapshot is bounded upstream; this function owns only the visible data.
    """
    table.setSortingEnabled(False)
    if table.rowCount() != len(rows):
        table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            item = table.item(row_index, column_index)
            if item is None:
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row_index, column_index, item)
            elif item.text() != value:
                item.setText(value)
                item.setToolTip(value)


class ControlCenterWindow(QMainWindow):
    """Render fresh immutable engine state without inferring progress."""

    def __init__(self, *, trusted_engine_id: str) -> None:
        super().__init__()
        if not trusted_engine_id:
            raise ControlCenterUiError("A trusted engine identity is required")
        self._trusted_engine_id = trusted_engine_id
        self._sequence: int | None = None
        self._last_received_at: float | None = None
        self._last_status_text = ""
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_connection_status)

        self.setObjectName("atlasControlCenter")
        self.setWindowTitle("Atlas Holding — Control Center")
        self.setMinimumSize(760, 480)

        self.status = QLabel("En attente d’un état authentifié")
        self.status.setObjectName("connectionStatus")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.tasks = _table(
            ("Tâche", "État", "Étape", "Progrès prouvé", "SHA", "Preuve"),
            "tasksTable",
        )
        self.agents = _table(
            ("Agent", "Rôle", "État", "Objectif", "Tâche", "Profil"),
            "agentsTable",
        )
        self.plugins = _table(
            ("Plugin", "Version", "État", "Mode", "Permission", "Quota"),
            "pluginsTable",
        )
        self.logs = _table(
            ("Séquence", "Catégorie", "Message", "Tâche", "Plugin", "Preuve"),
            "logsTable",
        )
        self.proofs = _table(
            ("Tâche certifiée", "Contrôle", "SHA candidat", "Empreinte", "Source"),
            "completionProofsTable",
        )
        self.protection_status = QLabel("Aucune preuve de certification reçue")
        self.protection_status.setObjectName("protectionStatus")
        self.protection_status.setWordWrap(True)

        tabs = QTabWidget()
        tabs.setObjectName("dashboardTabs")
        tabs.addTab(self.tasks, "Roadmap")
        tabs.addTab(self.agents, "Agents")
        tabs.addTab(self.plugins, "Plugins")
        proof_page = QWidget()
        proof_layout = QVBoxLayout(proof_page)
        proof_layout.addWidget(self.protection_status)
        proof_layout.addWidget(self.proofs)
        tabs.addTab(proof_page, "Protection et preuves")

        # The journal is bounded to one page upstream; filter locally without
        # touching the service, changing Qt models or requesting extra logs.
        self.log_category = QComboBox()
        self.log_category.setObjectName("logCategoryFilter")
        self.log_category.addItems(
            ["Toutes", "TASK", "CODE", "QA", "CI", "PLUGIN", "SYSTEM"])
        self.log_search = QLineEdit()
        self.log_search.setObjectName("logSearchFilter")
        self.log_search.setPlaceholderText("Rechercher dans le journal…")
        # Bound Qt slots avoid Python closure cycles that can outlive widgets
        # and crash during QApplication teardown.
        self.log_category.currentTextChanged.connect(self._filter_logs)
        self.log_search.textChanged.connect(self._filter_logs)
        filters = QHBoxLayout()
        filters.addWidget(self.log_category)
        filters.addWidget(self.log_search)
        journal = QWidget()
        journal_layout = QVBoxLayout(journal)
        journal_layout.addLayout(filters)
        journal_layout.addWidget(self.logs)
        tabs.addTab(journal, "Journal")

        root = QWidget()
        layout = QVBoxLayout(root)
        self.project_status = QLabel("Projet actif : non connecté")
        self.project_status.setObjectName("projectGitStatus")
        self.project_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.project_status)
        layout.addWidget(self.status)
        layout.addWidget(tabs)
        self.setCentralWidget(root)

    @Slot(str)
    def _filter_logs(self, _unused: str = "") -> None:
        category = self.log_category.currentText()
        query = self.log_search.text().strip().casefold()
        for row in range(self.logs.rowCount()):
            cells = tuple(
                self.logs.item(row, column).text()
                for column in range(self.logs.columnCount())
                if self.logs.item(row, column) is not None
            )
            visible = (
                (category == "Toutes" or (len(cells) > 1 and cells[1] == category))
                and (not query or any(query in cell.casefold() for cell in cells))
            )
            self.logs.setRowHidden(row, not visible)

    def render_project_status(self, *, project: str, repository: str,
                              git_status: object, eligible_missions: int) -> None:
        """Read-only, operator-supplied status; never authorizes engine actions."""
        from .control_center_project import validate_project_status
        view = validate_project_status(
            project=project, repository=repository,
            git_status=git_status, eligible_missions=eligible_missions,
        )
        self.project_status.setText(view)

    @Slot()
    def _refresh_connection_status(self) -> None:
        """Never keep displaying a silent engine as a live connection."""
        if self._last_received_at is None:
            return
        if monotonic() - self._last_received_at >= MAX_SNAPSHOT_AGE_SECONDS:
            self.status.setText(
                f"ÉTAT PÉRIMÉ — aucune nouvelle donnée depuis "
                f"{MAX_SNAPSHOT_AGE_SECONDS} s ; connexion moteur non confirmée"
            )
        # Do not overwrite explicit disconnected, paused or stopped status
        # in the live subclass on ordinary ticks.

    def closeEvent(self, event) -> None:
        # Closing the viewer must not touch the engine or leave UI timers alive.
        self._status_timer.stop()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Reopening the same window cannot resurrect an expired engine snapshot.
        self._refresh_connection_status()
        if self._last_received_at is not None:
            self._status_timer.start()

    def acknowledge_authenticated_state(self, state: DashboardState) -> bool:
        """Renew freshness only after a new MAC-verified IPC response."""
        if not isinstance(state, DashboardState):
            raise ControlCenterUiError("DashboardState required")
        if state.engine_id != self._trusted_engine_id:
            raise ControlCenterUiError("Dashboard state belongs to another engine")
        if self._sequence is None or state.sequence != self._sequence:
            return False
        self._last_received_at = monotonic()
        self.status.setText(self._last_status_text)
        self._status_timer.start()
        return True

    @property
    def displayed_sequence(self) -> int | None:
        return self._sequence

    def render_state(self, state: DashboardState) -> bool:
        """Atomically replace rows for a newer state; ignore replayed state."""
        if not isinstance(state, DashboardState):
            raise ControlCenterUiError("DashboardState required")
        if state.engine_id != self._trusted_engine_id:
            raise ControlCenterUiError("Dashboard state belongs to another engine")
        if self._sequence is not None and state.sequence <= self._sequence:
            return False

        task_rows = tuple((
            task.title,
            task.state,
            task.phase,
            task.progress,
            task.candidate_sha or "—",
            ("Aucune" if task.completion_evidence is None else
             f"{task.completion_evidence.kind} {task.completion_evidence.scope}"),
        ) for task in state.tasks)
        agent_rows = tuple((
            agent.agent_id,
            agent.role,
            agent.status,
            agent.objective,
            agent.task_id or "—",
            agent.model_profile,
        ) for agent in state.agents)
        plugin_rows = tuple((
            plugin.name,
            plugin.version,
            plugin.state,
            plugin.mode,
            plugin.permission,
            str(plugin.quota_remaining),
        ) for plugin in state.plugins)
        log_rows = tuple((
            str(log.sequence),
            log.category,
            log.message,
            log.task_id or "—",
            log.plugin_name or "—",
            log.evidence_digest or "—",
        ) for log in state.logs)

        proof_rows = tuple((
            task.title,
            f"{task.completion_evidence.kind} {task.completion_evidence.scope}",
            task.candidate_sha,
            task.completion_evidence.digest,
            task.completion_evidence.source,
        ) for task in state.tasks if task.state == "DONE"
          and task.completion_evidence is not None)
        blocked = sum(task.state in {"BLOCKED", "RECOVERY_REQUIRED", "DONE_UNVERIFIED"}
                      for task in state.tasks)
        pending = sum(task.state == "QA_PENDING" for task in state.tasks)
        ci_full = sum(
            task.completion_evidence is not None
            and task.completion_evidence.kind == "CI"
            and task.completion_evidence.scope == "FULL"
            for task in state.tasks
        )
        sync_done = sum(
            task.completion_evidence is not None
            and task.completion_evidence.kind == "SYNC"
            for task in state.tasks
        )
        self.protection_status.setText(
            f"Tâches avec preuve CI FULL : {ci_full} / {len(state.tasks)}"
            f" · Synchronisations prouvées : {sync_done}"
            f" · Bloquées/non vérifiées : {blocked} · En attente QA : {pending}"
        )
        _replace_rows(self.proofs, proof_rows)
        _replace_rows(self.tasks, task_rows)
        _replace_rows(self.agents, agent_rows)
        _replace_rows(self.plugins, plugin_rows)
        _replace_rows(self.logs, log_rows)
        self._filter_logs()
        self._last_status_text = (
            f"Dernier état reçu : {state.network} — séquence {state.sequence} — "
            f"{state.pending_external} vérification(s) externe(s) en attente"
        )
        self._last_received_at = monotonic()
        self.status.setText(self._last_status_text)
        self._status_timer.start()
        self._sequence = state.sequence
        return True
