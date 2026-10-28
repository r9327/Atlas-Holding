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
        window.close()


if __name__ == "__main__":
    unittest.main()
