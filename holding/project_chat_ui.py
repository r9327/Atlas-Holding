"""Small standalone operator chat for an explicitly selected local project.

Never attach this window to the display-only Control Center IPC. All SQLite
operations and local model requests run on a background executor, never in Qt
event handlers. The operator owns the repo/model selection outside chat text.
"""
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import (
    QApplication, QLabel, QLineEdit, QMainWindow, QPushButton,
    QTextEdit, QVBoxLayout, QHBoxLayout, QWidget,
)

from .project_chat import ChatView, ProjectChatCoordinator, ProjectChatError
from .state_store import StateStore


class ProjectChatWindow(QMainWindow):
    """One visible chat input and a durable local R&D conversation."""

    def __init__(self, *, database: Path, repository: str,
                 model_config: Path, circuit_config: Path | None = None):
        super().__init__()
        if not all(isinstance(p, Path) and p.is_absolute()
                   for p in (database, model_config)):
            raise ValueError("Explicit absolute local paths required")
        self.database = database
        self.repository = repository
        self.model_config = model_config
        self.circuit_config = circuit_config
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="atlas-project-chat")
        self._future: Future | None = None
        self._busy = False
        self._closing = False
        self._current: ChatView | None = None

        self.setObjectName("atlasProjectChat")
        self.setWindowTitle("Atlas Holding — Projet R&D")
        self.setMinimumSize(410, 320)
        self.resize(540, 460)
        root = QWidget()
        col = QVBoxLayout(root)
        self.scope = QLabel("Projet : " + repository)
        self.scope.setObjectName("chatRepository")
        self.scope.setWordWrap(True)
        col.addWidget(self.scope)
        self.history = QTextEdit()
        self.history.setObjectName("projectChatHistory")
        self.history.setReadOnly(True)
        col.addWidget(self.history)
        self.status = QLabel("Décris ton projet pour commencer.")
        self.status.setObjectName("projectChatStatus")
        self.status.setWordWrap(True)
        col.addWidget(self.status)
        row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setObjectName("projectChatEntry")
        self.entry.setMaxLength(1400)
        self.entry.setPlaceholderText("Décris ton projet…")
        self.send = QPushButton("Envoyer")
        self.send.setObjectName("projectChatSend")
        self.send.clicked.connect(self.submit)
        self.entry.returnPressed.connect(self.submit)
        row.addWidget(self.entry)
        row.addWidget(self.send)
        col.addLayout(row)
        self.disclaimer = QLabel(
            "Dossier R&D proposé, non vérifié. Aucune fabrication, "
            "expérience ou permission Git n'est déclenchée par ce chat.")
        self.disclaimer.setObjectName("projectChatLimits")
        self.disclaimer.setWordWrap(True)
        col.addWidget(self.disclaimer)
        self.setCentralWidget(root)
        self._poll = QTimer(self)
        self._poll.setInterval(140)
        self._poll.timeout.connect(self._poll_result)
        self._poll.start()
        self._schedule("latest", None)

    def _call(self, action: Literal["latest", "start", "answer", "resume"],
              value: str | None) -> ChatView | None:
        with StateStore(self.database) as store:
            coordinator = ProjectChatCoordinator(
                store, repository=self.repository, config_path=self.model_config,
                circuit_config_path=self.circuit_config)
            if action == "latest":
                view = coordinator.latest()
                return coordinator.resume(view.session_id) if view else None
            if action == "start":
                return coordinator.start(value or "")
            if action == "answer":
                if self._current is None:
                    raise ProjectChatError("No active project")
                return coordinator.answer(self._current.session_id, value or "")
            if action == "resume":
                if self._current is None:
                    raise ProjectChatError("No project to resume")
                return coordinator.resume(self._current.session_id)
            raise ProjectChatError("Unknown chat action")

    def _schedule(self, action: str, value: str | None) -> None:
        if self._closing or self._busy:
            return
        self._busy = True
        self.send.setEnabled(False)
        self.entry.setEnabled(False)
        self.status.setText("La Holding prépare la prochaine étape R&D…")
        self._future = self._executor.submit(self._call, action, value)

    @Slot()
    def submit(self) -> None:
        if (self._closing or self._busy
                or (self._current is not None and self._current.status == "READY")):
            return
        text = self.entry.text().strip()
        if not text:
            return
        self.entry.clear()
        self._schedule(
            "answer" if self._current and self._current.status == "ASKING"
            else "start", text)

    @Slot()
    def _poll_result(self) -> None:
        future = self._future
        if self._closing or future is None or not future.done():
            return
        self._future = None
        self._busy = False
        try:
            self._current = future.result()
        except Exception:
            self.status.setText(
                "Génération interrompue ou modèle indisponible. "
                "Ferme et rouvre le chat pour reprendre la session.")
        else:
            self._render()
            if self._current and self._current.status in ("ASSESSING", "GENERATING"):
                self._schedule("resume", None)
                return
        can_send = self._current is None or self._current.status == "ASKING"
        self.send.setEnabled(can_send)
        self.entry.setEnabled(can_send)

    def _render(self) -> None:
        view = self._current
        if view is None:
            self.history.setPlainText("")
            self.status.setText("Décris ton projet pour commencer.")
            self.entry.setPlaceholderText("Décris ton projet…")
            return
        lines = ["Vous : " + view.initial_goal]
        for index, question in enumerate(view.questions):
            lines.append("Holding : " + question)
            if index < len(view.answers):
                lines.append("Vous : " + view.answers[index])
        if view.status == "ASKING":
            self.status.setText(
                f"Précision {len(view.answers) + 1}/{len(view.questions)}. "
                "Après la dernière réponse, la R&D démarre automatiquement.")
            self.entry.setPlaceholderText("Ta réponse…")
        elif view.status == "READY":
            lines.append("Holding : Dossier R&D enregistré (proposition non vérifiée).")
            if view.concept:
                lines.append("Concept : " + view.concept)
            work = (
                f"{view.queued_tasks} tâches logicielles transmises, "
                f"{view.deferred_tasks} tâches hors code différées."
                if view.workplan_state == "STAGED"
                else "Plan de développement non transféré ; reprise requise."
            )
            if view.simulated_tasks:
                work += f" {view.simulated_tasks} simulations numériques calculées."
            lines.append("Holding : " + work)
            for result in view.simulation_results:
                lines.append("Calcul hypothétique : " + result)
            for result in view.circuit_results:
                lines.append("Circuit électrique simulé (hypothèses) : " + result)
            self.status.setText(
                "Étude R&D enregistrée. " + work +
                " Exécution selon les autorisations du moteur.")
            self.entry.setPlaceholderText("Autre projet : nouveau dépôt requis")
        else:
            self.status.setText("Étude R&D en cours, reprise possible.")
        self.history.setPlainText("\n\n".join(lines))
        scrollbar = self.history.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event) -> None:
        self._closing = True
        self._poll.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-project-chat")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--circuit-config", type=Path,
                        help="Chemin absolu du moteur ngspice épinglé (optionnel)")
    args = parser.parse_args(argv)
    if (not args.db.is_absolute() or not args.model_config.is_absolute()
            or (args.circuit_config is not None
                and not args.circuit_config.is_absolute())):
        parser.error("all supplied config and database paths must be absolute")
    from .ollama_protocol import _config
    _config(args.model_config)
    app = QApplication.instance() or QApplication([])
    window = ProjectChatWindow(
        database=args.db, repository=args.repo,
        model_config=args.model_config, circuit_config=args.circuit_config)
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
