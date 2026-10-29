"""Trusted one-shot viewer bootstrap (never accepts credentials in argv)."""
from __future__ import annotations

from contextlib import redirect_stderr
import io
import unittest
from unittest.mock import patch

from holding.control_center_entry import (
    ViewerEnrollmentError, main, read_capabilities,
)


READ = b"r" * 32
WRITE = b"w" * 32


class ViewerEntryTests(unittest.TestCase):
    def test_requires_exact_binary_capabilities_and_distinct_keys(self):
        keys = read_capabilities(io.BytesIO(READ + WRITE), readonly=False)
        self.assertEqual(keys, (READ, WRITE))
        self.assertEqual(read_capabilities(io.BytesIO(READ), readonly=True),
                         (READ, None))
        for payload, readonly in (
                (READ, False), (READ + WRITE + b"x", False),
                (READ + b"x", True), (READ + READ, False),
                (READ[:-1], True), (b"", False)):
            with self.subTest(payload_len=len(payload), readonly=readonly):
                with self.assertRaises(ViewerEnrollmentError):
                    read_capabilities(io.BytesIO(payload), readonly=readonly)

    def test_interactive_terminal_cannot_supply_secrets(self):
        class FakeTerminal(io.BytesIO):
            def isatty(self):
                return True
        with self.assertRaises(ViewerEnrollmentError):
            read_capabilities(
                FakeTerminal(READ + WRITE), readonly=False)

    def test_main_binds_real_client_objects_without_leaking_credentials(self):
        with patch("holding.control_center_entry._run_gui", return_value=0) as ui:
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(["--port", "1234", "--engine-id", "engine-1"],
                            input_stream=io.BytesIO(READ + WRITE))
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            reader, writer = ui.call_args.args
            self.assertEqual(reader.engine_id, "engine-1")
            self.assertEqual(reader.port, 1234)
            self.assertEqual(writer.port, 1234)
            self.assertEqual(reader._secret, READ)
            self.assertEqual(writer._secret, WRITE)

    def test_read_only_viewer_has_no_command_client(self):
        with patch("holding.control_center_entry._run_gui", return_value=0) as ui:
            self.assertEqual(
                main(["--port", "1234", "--engine-id", "engine-1",
                      "--readonly"], input_stream=io.BytesIO(READ)), 0)
            self.assertIsNone(ui.call_args.args[1])

    def test_failure_leaks_neither_key_nor_configuration(self):
        diagnostics = io.StringIO()
        with patch("holding.control_center_entry._run_gui") as ui:
            with redirect_stderr(diagnostics):
                code = main(["--port", "1234", "--engine-id", "engine-1"],
                            input_stream=io.BytesIO(b"credential-too-short"))
            self.assertEqual(code, 2)
            self.assertNotIn("credential-too-short", diagnostics.getvalue())
            ui.assert_not_called()

    def test_packaged_self_test_requires_no_secret_or_engine(self):
        class NoCredentialStream:
            def isatty(self):
                raise AssertionError("A self-test must not request credentials")

            def read(self, count=-1):
                raise AssertionError("A self-test must not read stdin")

        with patch("holding.control_center_entry._self_test",
                   return_value=0) as smoke:
            self.assertEqual(main(["--self-test"],
                                  input_stream=NoCredentialStream()), 0)
            smoke.assert_called_once_with()
        for args in (
            ["--self-test", "--port", "9000"],
            ["--self-test", "--engine-id", "spoofed"],
            ["--self-test", "--readonly"],
        ):
            with self.subTest(args=args), \
                 patch("holding.control_center_entry._self_test") as smoke:
                self.assertEqual(main(args, input_stream=NoCredentialStream()), 2)
                smoke.assert_not_called()

    def test_packaged_self_test_creates_real_qt_window_offscreen(self):
        from holding.control_center_entry import _self_test
        self.assertEqual(_self_test(), 0)

    def test_probe_requires_a_real_authenticated_snapshot_not_a_port(self):
        from pathlib import Path
        import tempfile
        from holding.dashboard_ipc import DashboardIpcServer
        from holding.state_store import StateStore

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite"
            with StateStore(db):
                pass
            server = DashboardIpcServer.for_database(
                db, engine_id="probe-host", secret=READ)
            with server:
                args = ["--probe", "--readonly", "--port", str(server.port),
                        "--engine-id", "probe-host"]
                with patch("holding.control_center_entry._run_gui") as ui:
                    self.assertEqual(main(args, input_stream=io.BytesIO(READ)), 0)
                    ui.assert_not_called()
                self.assertEqual(main(args, input_stream=io.BytesIO(WRITE)), 2)
                self.assertEqual(main(
                    ["--probe", "--port", str(server.port),
                     "--engine-id", "wrong-engine"],
                    input_stream=io.BytesIO(READ + WRITE)), 2)

    def test_isolated_project_chat_frozen_mode_has_no_ipc_credentials(self):
        from pathlib import Path

        class NoCredentials:
            def isatty(self):
                raise AssertionError("R&D mode must not read dashboard credentials")

            def read(self, count=-1):
                raise AssertionError("R&D mode must not consume the private pipe")

        args = [
            "--project-chat", "--chat-db", "/tmp/chat.sqlite",
            "--chat-repo", "r9327/Atlas-Holding",
            "--chat-model-config", "/tmp/model.json",
            "--chat-circuit-config", "/tmp/circuit.json",
        ]
        # Path.is_absolute is portable: these are absolute POSIX paths even
        # when running the test itself on Windows.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            args[2] = str(root / "chat.sqlite")
            args[6] = str(root / "model.json")
            args[8] = str(root / "circuit.json")
            with patch("holding.control_center_entry._run_project_chat",
                       return_value=0) as chat, \
                 patch("holding.control_center_entry._run_gui") as viewer:
                self.assertEqual(main(args, input_stream=NoCredentials()), 0)
                chat.assert_called_once_with([
                    "--db", args[2], "--repo", "r9327/Atlas-Holding",
                    "--model-config", args[6],
                    "--circuit-config", args[8],
                ])
                viewer.assert_not_called()
            for extra in (
                ["--port", "80", "--engine-id", "fake"],
                ["--readonly"], ["--probe"], ["--self-test"],
                ["--update-root", str(root)],
            ):
                with self.subTest(extra=extra), \
                     patch("holding.control_center_entry._run_project_chat") as chat:
                    self.assertEqual(main(args + extra,
                                          input_stream=NoCredentials()), 2)
                    chat.assert_not_called()
            # A partial or relative selection must fail closed before opening Qt.
            for invalid in (
                ["--project-chat"],
                ["--project-chat", "--chat-db", "relative.sqlite",
                 "--chat-repo", "r9327/Atlas-Holding",
                 "--chat-model-config", str(root / "model.json")],
                ["--chat-db", str(root / "chat.sqlite")],
            ):
                with self.subTest(invalid=invalid), \
                     patch("holding.control_center_entry._run_project_chat") as chat:
                    self.assertEqual(main(invalid, input_stream=NoCredentials()), 2)
                    chat.assert_not_called()

    def test_bundled_project_chat_smoke_tests_real_qt(self):
        from holding.control_center_entry import _project_chat_self_test
        self.assertEqual(_project_chat_self_test(), 0)
        with patch("holding.control_center_entry._project_chat_self_test",
                   return_value=0) as smoke, \
             patch("holding.control_center_entry.read_capabilities") as credentials:
            self.assertEqual(main(["--project-chat-self-test"]), 0)
            smoke.assert_called_once_with()
            credentials.assert_not_called()
        for extra in (["--readonly"], ["--self-test"], ["--project-chat"]):
            with self.subTest(extra=extra), \
                 patch("holding.control_center_entry._project_chat_self_test") as smoke:
                self.assertEqual(main(["--project-chat-self-test", *extra]), 2)
                smoke.assert_not_called()

    def test_invalid_port_does_not_start_ui(self):
        with patch("holding.control_center_entry._run_gui") as ui:
            with redirect_stderr(io.StringIO()):
                self.assertEqual(
                    main(["--port", "0", "--engine-id", "engine-1"],
                         input_stream=io.BytesIO(READ + WRITE)), 2)
            ui.assert_not_called()


if __name__ == "__main__":
    unittest.main()
