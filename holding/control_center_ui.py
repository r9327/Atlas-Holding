"""Passive PySide6 dashboard for validated Control Center snapshots.

This process is deliberately display-only.  It receives ``DashboardState``
instances that have already crossed the authenticated snapshot boundary and
does not expose engine mutation controls until that IPC exists.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QMainWindow,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .control_center_state import DashboardState


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

        tabs = QTabWidget()
        tabs.setObjectName("dashboardTabs")
        tabs.addTab(self.tasks, "Roadmap")
        tabs.addTab(self.agents, "Agents")
        tabs.addTab(self.plugins, "Plugins")
        tabs.addTab(self.logs, "Journal")

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self.status)
        layout.addWidget(tabs)
        self.setCentralWidget(root)

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

        _replace_rows(self.tasks, task_rows)
        _replace_rows(self.agents, agent_rows)
        _replace_rows(self.plugins, plugin_rows)
        _replace_rows(self.logs, log_rows)
        self.status.setText(
            f"{state.network} — séquence {state.sequence} — "
            f"{state.pending_external} vérification(s) externe(s) en attente"
        )
        self._sequence = state.sequence
        return True
