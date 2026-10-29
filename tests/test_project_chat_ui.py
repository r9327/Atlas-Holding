"""Offscreen minimal chat Qt tests; never call a real local model."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit

from holding.project_chat import ChatView
from holding.project_chat_ui import ProjectChatWindow

SID = "a" * 32
REPO = "r9327/Dof-Atlas"


class ProjectChatUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.database = Path(temp.name) / "store.sqlite"
        self.config = Path(temp.name) / "model.json"

    def flush(self, window, seconds=4.0):
        limit = time.monotonic() + seconds
        while time.monotonic() < limit:
            self.app.processEvents()
            if not window._busy:
                self.app.processEvents()
                return
            time.sleep(0.01)
        self.fail("Qt background task did not finish")

    def asking(self):
        return ChatView(SID, REPO, "Créer un outil innovant",
                        ("Pour qui ?", "Quel usage ?"), (), "ASKING",
                        "Pour qui ?", "chat-" + SID[:24], None, None)

    def test_minimal_window_has_one_input_and_displays_questions(self):
        with patch.object(ProjectChatWindow, "_call", return_value=self.asking()):
            window = ProjectChatWindow(
                database=self.database, repository=REPO, model_config=self.config)
            self.flush(window)
        self.assertEqual(len(window.findChildren(QLineEdit)), 1)
        self.assertEqual(len(window.findChildren(QTextEdit)), 1)
        self.assertTrue(window.history.isReadOnly())
        self.assertIn("Pour qui ?", window.history.toPlainText())
        self.assertIn("1/2", window.status.text())
        self.assertIn("non vérifié", window.disclaimer.text())
        window.close()

    def test_answer_uses_existing_session_and_does_not_block_qt(self):
        with patch.object(ProjectChatWindow, "_call", return_value=self.asking()):
            window = ProjectChatWindow(
                database=self.database, repository=REPO, model_config=self.config)
            self.flush(window)
        answered = ChatView(SID, REPO, "Créer un outil innovant",
                            ("Pour qui ?", "Quel usage ?"), ("Des artisans.",),
                            "ASKING", "Quel usage ?", "chat-" + SID[:24], None, None)
        with patch.object(window, "_call", return_value=answered) as call:
            window.entry.setText("Des artisans.")
            window.submit()
            self.assertFalse(window.send.isEnabled())
            self.flush(window)
            self.assertEqual(call.call_args.args, ("answer", "Des artisans."))
        self.assertIn("2/2", window.status.text())
        self.assertIn("Des artisans.", window.history.toPlainText())
        window.close()

    def test_staged_workplan_counts_are_only_queued_not_completed(self):
        done = ChatView(SID, REPO, "Créer un outil", (), (), "READY", None,
                        "chat-" + SID[:24], "1" * 64, "Concept proposé",
                        "STAGED", 2, 3)
        with patch.object(ProjectChatWindow, "_call", return_value=done):
            window = ProjectChatWindow(
                database=self.database, repository=REPO,
                model_config=self.config)
            self.flush(window)
        self.assertIn("2 tâches logicielles transmises",
                      window.history.toPlainText())
        self.assertIn("3 tâches différées",
                      window.history.toPlainText())
        self.assertNotIn("mission terminée", window.status.text().lower())
        window.close()

    def test_completed_local_simulations_shown_as_computed_not_proven(self):
        done = ChatView(SID, REPO, "Concevoir un modèle", (), (), "READY", None,
                        "chat-" + SID[:24], "1" * 64,
                        "Concept de simulation", "STAGED", 0, 1, 2,
                        ("linear_discrete: résultat 22 ; preuve de calcul abc…",))
        with patch.object(ProjectChatWindow, "_call", return_value=done):
            window = ProjectChatWindow(
                database=self.database, repository=REPO, model_config=self.config)
            self.flush(window)
        self.assertIn("2 simulations numériques calculées",
                      window.history.toPlainText())
        self.assertIn("linear_discrete: résultat 22",
                      window.history.toPlainText())
        self.assertIn("1 tâches différées",
                      window.history.toPlainText())
        self.assertIn("non vérifiée", window.history.toPlainText())
        window.close()

    def test_csv_button_targets_selected_circuit_and_shows_comparison(self):
        from unittest.mock import patch as mocked
        import json
        done = ChatView(SID, REPO, "Étudier un circuit RC", (), (), "READY",
                        None, "chat-" + SID[:24], "1" * 64,
                        "Circuit à comparer", "STAGED", 0, 0, 0, (),
                        ("RC ngspice: tension finale 4.99 V",),
                        (2,), ())
        compared = ChatView(SID, REPO, "Étudier un circuit RC", (), (), "READY",
                            None, "chat-" + SID[:24], "1" * 64,
                            "Circuit à comparer", "STAGED", 0, 0, 0, (),
                            ("RC ngspice: tension finale 4.99 V",),
                            (2,), ("Tâche 3: RMSE 0.05 V, mesures non certifiées",))
        with mocked.object(ProjectChatWindow, "_call", return_value=done):
            window = ProjectChatWindow(
                database=self.database, repository=REPO, model_config=self.config)
            self.flush(window)
        self.assertTrue(window.compare_button.isEnabled())
        self.assertEqual(window.circuit_selector.currentData(), 2)
        csv = self.database.parent / "mesures.csv"
        csv.write_text("time_seconds,voltage_volts\n0,0\n1,5\n",
                       encoding="utf-8")
        with mocked("holding.project_chat_ui.QFileDialog.getOpenFileName",
                    return_value=(str(csv), "CSV")), \
             mocked.object(window, "_call", return_value=compared) as action:
            window.import_measurements()
            self.assertFalse(window.compare_button.isEnabled())
            self.flush(window)
            self.assertEqual(action.call_args.args[0], "measure")
            args = json.loads(action.call_args.args[1])
            self.assertEqual(args["task_index"], 2)
            self.assertEqual(args["file_path"], str(csv))
        self.assertIn("RMSE 0.05 V", window.history.toPlainText())
        self.assertIn("mesures non certifiées", window.history.toPlainText())
        window.close()

    def test_csv_button_disabled_without_real_simulated_circuit(self):
        done = ChatView(SID, REPO, "Sans appareil", (), (), "READY",
                        None, "chat-" + SID[:24], "1" * 64,
                        "Concept")
        with patch.object(ProjectChatWindow, "_call", return_value=done):
            window = ProjectChatWindow(
                database=self.database, repository=REPO, model_config=self.config)
            self.flush(window)
        self.assertFalse(window.compare_button.isEnabled())
        self.assertEqual(window.circuit_selector.count(), 0)
        window.close()

    def test_unverified_result_does_not_claim_prototype_complete(self):
        done = ChatView(SID, REPO, "Créer un outil innovant", (), (),
                        "READY", None, "chat-" + SID[:24],
                        "1" * 64, "Prototype proposé.")
        with patch.object(ProjectChatWindow, "_call", return_value=done):
            window = ProjectChatWindow(
                database=self.database, repository=REPO, model_config=self.config)
            self.flush(window)
        self.assertIn("Prototype proposé.", window.history.toPlainText())
        self.assertIn("non vérifiée", window.history.toPlainText())
        self.assertNotIn("certifié", window.status.text().casefold())
        self.assertFalse(window.send.isEnabled())
        self.assertIn("Exécution selon les autorisations", window.status.text())
        window.close()


    def test_oversized_utf8_answer_stays_editable_without_calling_model(self):
        with patch.object(ProjectChatWindow, "_call", return_value=self.asking()):
            window = ProjectChatWindow(
                database=self.database, repository=REPO, model_config=self.config)
            self.addCleanup(window.close)
            self.flush(window)
        for text in ("x" * 601, "é" * 301):
            with self.subTest(text=text[:4]), patch.object(window, "_call") as call:
                window.entry.setText(text)
                window.submit()
                call.assert_not_called()
                self.assertEqual(window.entry.text(), text)
                self.assertTrue(window.send.isEnabled())
                self.assertIn("600 octets UTF-8", window.status.text())
        accepted = ChatView(SID, REPO, "Créer un outil innovant",
                            ("Pour qui ?", "Quel usage ?"), ("é" * 300,),
                            "ASKING", "Quel usage ?", "chat-" + SID[:24], None, None)
        with patch.object(window, "_call", return_value=accepted):
            window.entry.setText("é" * 300)
            window.submit()
            self.flush(window)
        self.assertEqual(window.entry.text(), "")
        self.assertIn("2/2", window.status.text())


    def test_model_failure_keeps_input_and_resume_reloads_without_resubmission(self):
        with patch.object(ProjectChatWindow, "_call", return_value=None):
            window = ProjectChatWindow(
                database=self.database, repository=REPO, model_config=self.config)
            self.addCleanup(window.close)
            self.flush(window)
        with patch.object(window, "_call", side_effect=TimeoutError("model")):
            window.entry.setText("Mon projet conservé")
            window.submit()
            self.flush(window)
        self.assertEqual(window.entry.text(), "Mon projet conservé")
        self.assertFalse(window.send.isEnabled())
        self.assertFalse(window.retry.isHidden())
        asking = self.asking()
        from dataclasses import replace
        with patch.object(window, "_call", return_value=replace(
                asking, initial_goal="Mon projet conservé")) as call:
            window.retry.click()
            self.flush(window)
            self.assertEqual(call.call_args.args, ("resume", None))
        self.assertEqual(window.entry.text(), "")
        self.assertTrue(window.send.isEnabled())


    def test_resume_after_database_failure_keeps_unaccepted_text(self):
        with patch.object(ProjectChatWindow, "_call", return_value=None):
            window = ProjectChatWindow(
                database=self.database, repository=REPO, model_config=self.config)
            self.addCleanup(window.close)
            self.flush(window)
        with patch.object(window, "_call", side_effect=OSError("database unavailable")):
            window.entry.setText("Objectif non enregistré")
            window.submit()
            self.flush(window)
        with patch.object(window, "_call", return_value=None):
            window.retry.click()
            self.flush(window)
        self.assertEqual(window.entry.text(), "Objectif non enregistré")
        self.assertTrue(window.send.isEnabled())



    def test_deferred_tasks_can_be_resumed_without_closing_window(self):
        from dataclasses import replace
        waiting = ChatView(
            SID, REPO, "Simuler un circuit", (), (), "READY", None,
            "chat-" + SID[:24], "1" * 64, "RC", "STAGED", 0, 2,
            task_details=("Tâche 1 : RC — simulation RC en échec",))
        with patch.object(ProjectChatWindow, "_call", return_value=waiting):
            window = ProjectChatWindow(
                database=self.database, repository=REPO, model_config=self.config)
            self.addCleanup(window.close)
            self.flush(window)
        self.assertFalse(window.retry.isHidden())
        self.assertIsNone(window._future)
        recovered = replace(waiting, queued_tasks=1, deferred_tasks=0,
                            task_details=("Tâche 1 : RC — calculé sur hypothèses",))
        with patch.object(window, "_call", return_value=recovered) as call:
            window.retry.click()
            self.flush(window)
            self.assertEqual(call.call_args.args, ("resume", None))
        self.assertTrue(window.retry.isHidden())
        self.assertIn("calculé sur hypothèses", window.history.toPlainText())
        self.assertIn("1 tâches logicielles transmises", window.history.toPlainText())


    def test_complete_research_brief_is_readable(self):
        done = ChatView(
            SID, REPO, "Étudier un prototype", (), (), "READY", None,
            "chat-" + SID[:24], "1" * 64, "Concept",
            research_brief={
                "hypotheses": ["Hypothèse visible"],
                "prototype_steps": ["Étape visible"],
                "unknowns": ["Inconnue visible"],
                "experiments": [{"name": "Essai visible", "method": "Méthode visible",
                                 "success_measure": "Critère visible"}],
            })
        with patch.object(ProjectChatWindow, "_call", return_value=done):
            window = ProjectChatWindow(
                database=self.database, repository=REPO, model_config=self.config)
            self.addCleanup(window.close)
            self.flush(window)
        for expected in ("Hypothèse visible", "Étape visible", "Inconnue visible",
                         "Essai visible", "Méthode visible", "Critère visible"):
            self.assertIn(expected, window.history.toPlainText())


if __name__ == "__main__":
    unittest.main()
