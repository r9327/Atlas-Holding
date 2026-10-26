"""Scoped Control Center + local updater observations, with Qt offscreen."""
from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from holding.control_center_entry import main
from holding.control_center_ui import ControlCenterUiError, ControlCenterWindow
from holding.update_observation import LocalUpdateObservation

READ = b"r" * 32
WRITE = b"w" * 32


class UpdateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_update_tab_is_present_and_explicitly_uncertified(self):
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        try:
            status = window.findChild(QLabel, "localUpdateStatus")
            note = window.findChild(QLabel, "localUpdateDisclaimer")
            self.assertIsNotNone(status)
            self.assertIsNotNone(note)
            self.assertIn("non signé", note.text())
            window.render_update_observation(
                LocalUpdateObservation(
                    "LOCAL_RECEIPT_ONLY", "v2", "a" * 64,
                )
            )
            self.assertIn("v2", status.text())
            self.assertIn("non certifié", status.text())
            window.render_update_observation(
                LocalUpdateObservation("RECOVERY_REQUIRED", None, None)
            )
            self.assertIn("reprise nécessaire", status.text())
            self.assertNotIn("v2", status.text())
        finally:
            window.close()

    def test_unknown_or_ambiguous_update_state_cannot_be_rendered(self):
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        try:
            with self.assertRaises(ControlCenterUiError):
                window.render_update_observation({"status": "LOCAL_RECEIPT_ONLY"})
            with self.assertRaises(ControlCenterUiError):
                window.render_update_observation(
                    LocalUpdateObservation("UNVERIFIED_LOCAL_STATE", "v2", None)
                )
            with self.assertRaises(ControlCenterUiError):
                window.render_update_observation(
                    LocalUpdateObservation("CERTIFIED", "v2", "a" * 64)
                )
        finally:
            window.close()

    def test_entry_forwards_only_optional_display_root_after_enrollment(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("holding.control_center_entry._run_gui", return_value=0) as gui:
                result = main(
                    ["--port", "1234", "--engine-id", "engine-1",
                     "--update-root", tmp],
                    input_stream=io.BytesIO(READ + WRITE),
                )
            self.assertEqual(result, 0)
            self.assertEqual(gui.call_args.kwargs["update_root"], Path(tmp))
            self.assertEqual(gui.call_args.args[0].engine_id, "engine-1")

    def test_self_test_and_ipc_probe_reject_extra_update_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("holding.control_center_entry._self_test") as smoke:
                self.assertEqual(
                    main(["--self-test", "--update-root", tmp]), 2,
                )
                smoke.assert_not_called()
            with self.assertRaises(SystemExit):
                main([
                    "--probe", "--port", "1234", "--engine-id", "engine-1",
                    "--update-root", tmp,
                ], input_stream=io.BytesIO(READ))


if __name__ == "__main__":
    unittest.main()
