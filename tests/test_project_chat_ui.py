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
        self.assertIn("3 tâches hors code différées",
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
        self.assertIn("1 tâches hors code différées",
                      window.history.toPlainText())
        self.assertIn("non vérifiée", window.history.toPlainText())
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



if __name__ == "__main__":
    unittest.main()
