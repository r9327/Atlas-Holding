"""Fail-closed transition from installed bytes to headless Windows process."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from holding.windows_runtime_launch import (
    EngineLaunch, InstalledEngineSpec, RuntimeLaunchError,
    launch_installed_engine, launch_installed_engine_health_session,
    verify_installed_engine,
)


class WindowsInstalledEngineLaunchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.pair = self.root / "runtime" / "pinned-version"
        self.pair.mkdir(parents=True)
        self.config = self.root / "config" / "engine.json"
        self.config.parent.mkdir()
        self.viewer = self.pair / "AtlasHoldingControlCenter.exe"
        self.engine = self.pair / "AtlasHoldingEngine.exe"
        self.viewer.write_bytes(b"V" * 1_000_013)
        self.engine.write_bytes(b"E" * 1_000_029)
        self.viewer_pin = hashlib.sha256(self.viewer.read_bytes()).hexdigest()
        self.engine_pin = hashlib.sha256(self.engine.read_bytes()).hexdigest()
        self.doc = {
            "schema": 3,
            "dashboard": {
                "executable": str(self.viewer),
                "executable_sha256": self.viewer_pin,
                "script": None, "script_sha256": None, "readonly": False,
            },
        }
        self.write_doc()

    def write_doc(self):
        self.config.write_text(json.dumps(self.doc), encoding="utf-8")
        self.spec = InstalledEngineSpec(
            self.root, self.pair, self.config,
            self.viewer_pin, self.engine_pin,
            hashlib.sha256(self.config.read_bytes()).hexdigest(),
        )

    def native(self):
        return patch("holding.windows_runtime_launch.sys.platform", "win32")

    def acl(self):
        return patch("holding.windows_runtime_acl.validate_runtime_acl")

    def test_native_preflight_and_process_launch_never_claims_ready(self):
        with self.native(), self.acl() as acl, \
             patch("holding.windows_runtime_launch._interactive_user_session",
                   return_value=True), \
             patch("holding.windows_runtime_launch.subprocess.Popen") as spawn:
            process = Mock()
            process.poll.return_value = None
            spawn.return_value = process
            self.assertEqual(
                verify_installed_engine(self.spec),
                (str(self.engine), str(self.config)),
            )
            self.assertEqual(acl.call_count, 7)
            launched = launch_installed_engine(self.spec, approved=True)
        self.assertIsInstance(launched, EngineLaunch)
        self.assertEqual(launched.status, "ENGINE_SPAWNED_HEALTH_PENDING")
        self.assertIs(launched.process, process)
        self.assertEqual(spawn.call_count, 1)
        args, kw = spawn.call_args
        self.assertEqual(args[0], [str(self.engine), "--config", str(self.config)])
        self.assertFalse(kw["shell"])
        self.assertTrue(kw["close_fds"])
        self.assertEqual(kw["cwd"], str(self.root))
        self.assertEqual(kw["stdin"], -3)  # subprocess.DEVNULL
        self.assertNotIn("PYTHONPATH", kw["env"])
        self.assertNotIn("GITHUB_TOKEN", kw["env"])

    def test_unapproved_or_unsupported_platform_never_launches(self):
        with patch("holding.windows_runtime_launch.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RuntimeLaunchError, "not authorized"):
                launch_installed_engine(self.spec, approved=False)
            with patch("holding.windows_runtime_launch.sys.platform", "linux"), \
                 patch("holding.windows_runtime_launch._interactive_user_session",
                       return_value=True):
                with self.assertRaisesRegex(RuntimeLaunchError, "Native Windows"):
                    launch_installed_engine(self.spec, approved=True)
            spawn.assert_not_called()

    def test_session_zero_denied_before_config_or_process_access(self):
        with self.native(), \
             patch("holding.windows_runtime_launch._interactive_user_session",
                   return_value=False), \
             patch("holding.windows_runtime_launch.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RuntimeLaunchError, "interactive Windows session"):
                launch_installed_engine(self.spec, approved=True)
            spawn.assert_not_called()

    def test_native_acl_denial_and_modified_engine_block_execution(self):
        with self.native(), self.acl() as acl, \
             patch("holding.windows_runtime_launch._interactive_user_session",
                   return_value=True), \
             patch("holding.windows_runtime_launch.subprocess.Popen") as spawn:
            acl.side_effect = ValueError("untrusted DACL")
            with self.assertRaisesRegex(RuntimeLaunchError, "not trusted"):
                launch_installed_engine(self.spec, approved=True)
            acl.side_effect = None
            self.engine.write_bytes(b"changed executable")
            with self.assertRaisesRegex(RuntimeLaunchError, "not trusted"):
                launch_installed_engine(self.spec, approved=True)
            spawn.assert_not_called()

    def test_config_hash_and_viewer_binding_are_mandatory(self):
        with self.native(), self.acl(), \
             patch("holding.windows_runtime_launch._interactive_user_session",
                   return_value=True), \
             patch("holding.windows_runtime_launch.subprocess.Popen") as spawn:
            self.config.write_text('{"schema":1}')
            with self.assertRaisesRegex(RuntimeLaunchError, "hash"):
                launch_installed_engine(self.spec, approved=True)
            self.doc["dashboard"]["executable"] = str(self.root / "foreign.exe")
            self.write_doc()
            with self.assertRaisesRegex(RuntimeLaunchError, "not pinned"):
                launch_installed_engine(self.spec, approved=True)
            self.doc["dashboard"]["executable"] = str(self.viewer)
            self.doc["schema"] = 2
            self.write_doc()
            with self.assertRaisesRegex(RuntimeLaunchError, "schema v3"):
                launch_installed_engine(self.spec, approved=True)
            spawn.assert_not_called()

    def test_double_json_keys_are_refused_even_if_hash_matches(self):
        self.config.write_text(
            '{"schema":3,"schema":3,"dashboard":{}}', encoding="utf-8",
        )
        replaced = replace(
            self.spec,
            config_sha256=hashlib.sha256(self.config.read_bytes()).hexdigest(),
        )
        with self.native(), self.acl(), \
             patch("holding.windows_runtime_launch._interactive_user_session",
                   return_value=True), \
             patch("holding.windows_runtime_launch.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RuntimeLaunchError, "duplicate"):
                launch_installed_engine(replaced, approved=True)
            spawn.assert_not_called()

    def test_traversal_and_external_config_directory_refused(self):
        elsewhere = self.root / "other"
        elsewhere.mkdir()
        external = elsewhere / "engine.json"
        external.write_bytes(self.config.read_bytes())
        moved = replace(self.spec, config_file=external)
        with self.native(), self.acl(), \
             patch("holding.windows_runtime_launch._interactive_user_session",
                   return_value=True), \
             patch("holding.windows_runtime_launch.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RuntimeLaunchError, "outside selected root"):
                launch_installed_engine(moved, approved=True)
            spawn.assert_not_called()

    def test_child_crash_is_not_accepted_as_ready(self):
        with self.native(), self.acl(), \
             patch("holding.windows_runtime_launch._interactive_user_session",
                   return_value=True), \
             patch("holding.windows_runtime_launch.subprocess.Popen") as spawn:
            dead = Mock()
            dead.poll.return_value = 2
            spawn.return_value = dead
            with self.assertRaisesRegex(RuntimeLaunchError, "exited"):
                launch_installed_engine(self.spec, approved=True)

    def test_private_health_launch_returns_live_authenticated_session(self):
        from holding.dashboard_ipc import DashboardIpcServer
        from holding.state_store import StateStore

        database = self.root / "health.sqlite"
        with StateStore(database):
            pass
        key = b"h" * 32
        engine_id = "installed-update-engine"
        with DashboardIpcServer.for_database(
                database, engine_id=engine_id, secret=key) as server, \
             self.native(), self.acl(), \
             patch("holding.windows_runtime_launch._interactive_user_session",
                   return_value=True), \
             patch("holding.windows_runtime_launch.secrets.token_bytes",
                   return_value=key), \
             patch("holding.windows_runtime_launch.subprocess.Popen") as spawn:
            process = Mock()
            process.stdin = io.BytesIO()
            process.stdout = io.BytesIO((json.dumps({
                "schema": 1, "port": server.port, "engine_id": engine_id,
            }) + "\n").encode())
            process.poll.return_value = None
            process.pid = 4321
            spawn.return_value = process
            session = launch_installed_engine_health_session(
                self.spec, approved=True,
            )
            state = session.client.fetch()
            self.assertEqual(state.engine_id, engine_id)
            session.close()
            session.close()
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)
        args, kwargs = spawn.call_args
        self.assertEqual(args[0], [
            str(self.engine), "--config", str(self.config),
            "--update-health-handshake",
        ])
        self.assertFalse(kwargs["shell"])
        self.assertNotIn("GITHUB_TOKEN", kwargs["env"])

    def test_invalid_health_handshake_stops_candidate(self):
        with self.native(), self.acl(), \
             patch("holding.windows_runtime_launch._interactive_user_session",
                   return_value=True), \
             patch("holding.windows_runtime_launch.subprocess.Popen") as spawn:
            process = Mock()
            process.stdin = io.BytesIO()
            process.stdout = io.BytesIO(b'{"schema":1,"port":0}\n')
            process.poll.return_value = None
            spawn.return_value = process
            with self.assertRaisesRegex(RuntimeLaunchError, "Invalid"):
                launch_installed_engine_health_session(
                    self.spec, approved=True,
                )
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)

    def test_health_launch_requires_approval_and_bounded_timeout(self):
        with patch("holding.windows_runtime_launch.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RuntimeLaunchError, "not authorized"):
                launch_installed_engine_health_session(
                    self.spec, approved=False,
                )
            with self.assertRaisesRegex(RuntimeLaunchError, "timeout"):
                launch_installed_engine_health_session(
                    self.spec, approved=True, handshake_timeout=True,
                )
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
