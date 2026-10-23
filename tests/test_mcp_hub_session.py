"""Native end-to-end lifecycle contracts for the internal supervised MCP hub."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from holding.mcp_hub_session import (
    LocalMcpHubSession, McpSessionError,
)
from holding.mcp_local_inventory import PinnedLocalPlugin

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable).resolve()


def pinned(*, digest=None):
    h = hashlib.sha256()
    with PYTHON.open("rb") as handle:
        for part in iter(lambda: handle.read(64 * 1024), b""):
            h.update(part)
    return PinnedLocalPlugin(
        "hosting-python", "3.13", PYTHON, digest or h.hexdigest())


class LocalMcpHubSessionTests(unittest.TestCase):
    def test_two_real_discoveries_reuse_one_verified_process(self):
        session = LocalMcpHubSession(
            interpreter_pin=pinned(), package_root=ROOT, response_timeout=8)
        self.assertEqual(session.observe().state, "NOT_STARTED")
        with session:
            initial = session.observe()
            self.assertEqual(initial.state, "UNVERIFIED")
            self.assertIsNotNone(initial.pid)
            first = session.discover()
            self.assertEqual(first.state, "RESPONSIVE")
            self.assertFalse(first.external_plugins_tested)
            self.assertIsNotNone(first.protocol_version)
            self.assertEqual(first.pid, initial.pid)
            self.assertEqual(session.observe().state, "RESPONSIVE")
            second = session.discover()
            self.assertEqual(second.state, "RESPONSIVE")
            self.assertEqual(second.pid, first.pid)
            self.assertIsNone(session._process.poll())
        self.assertEqual(session.observe().state, "CLOSED")
        self.assertIsNone(session._process)
        session.close()

    def test_wrong_hash_cannot_start_process(self):
        session = LocalMcpHubSession(
            interpreter_pin=pinned(digest="0" * 64), package_root=ROOT)
        with patch("holding.mcp_hub_session._spawn_hub") as spawn:
            with self.assertRaisesRegex(McpSessionError, "unverified"):
                session.start()
            spawn.assert_not_called()
        self.assertEqual(session.observe().state, "NOT_STARTED")
        session.close()

    def test_forged_package_directory_cannot_start_hub(self):
        with self.assertRaisesRegex(ValueError, "Untrusted"):
            LocalMcpHubSession(
                interpreter_pin=pinned(), package_root=ROOT.parent)
        with self.assertRaises(ValueError):
            LocalMcpHubSession(
                interpreter_pin=pinned(), package_root=Path("."))
        with self.assertRaises(ValueError):
            LocalMcpHubSession(
                interpreter_pin=pinned(), package_root=ROOT,
                response_timeout=99)
        with self.assertRaises(ValueError):
            LocalMcpHubSession(
                interpreter_pin=pinned(), package_root=ROOT,
                response_timeout=True)

    def test_closed_session_cannot_be_restarted(self):
        session = LocalMcpHubSession(interpreter_pin=pinned(), package_root=ROOT)
        session.close()
        with self.assertRaises(McpSessionError):
            session.start()
        with self.assertRaises(McpSessionError):
            session.discover()

    def test_second_start_refuses_parallel_hub(self):
        session = LocalMcpHubSession(interpreter_pin=pinned(), package_root=ROOT)
        with session:
            with self.assertRaises(McpSessionError):
                session.start()
            self.assertEqual(session.discover().state, "RESPONSIVE")

    def test_untrusted_protocol_response_never_claims_health(self):
        def fake_spawn(**_kwargs):
            process = subprocess.Popen(
                [sys.executable, "-B", "-c",
                 "import sys;sys.stdin.readline();print('not json',flush=True)"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=os.name == "posix",
            )
            return process, None
        with patch("holding.mcp_hub_session._spawn_hub",
                   side_effect=fake_spawn):
            with LocalMcpHubSession(
                    interpreter_pin=pinned(), package_root=ROOT,
                    response_timeout=4) as session:
                result = session.discover()
                self.assertEqual(result.state, "BAD_PROTOCOL")
                self.assertIsNone(result.protocol_version)
                self.assertEqual(session.observe().state, "NOT_STARTED")

    def test_unresponsive_server_killed_and_not_left_running(self):
        spawned = []
        def fake_spawn(**_kwargs):
            process = subprocess.Popen(
                [sys.executable, "-B", "-c",
                 "import sys,time;sys.stdin.readline();time.sleep(15)"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=os.name == "posix",
            )
            spawned.append(process)
            return process, None
        with patch("holding.mcp_hub_session._spawn_hub",
                   side_effect=fake_spawn):
            with LocalMcpHubSession(
                    interpreter_pin=pinned(), package_root=ROOT,
                    response_timeout=.25) as session:
                self.assertEqual(session.discover().state, "TIMEOUT")
                self.assertEqual(session.observe().state, "NOT_STARTED")
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].poll())

    def test_unbounded_child_output_fails_closed(self):
        def fake_spawn(**_kwargs):
            process = subprocess.Popen(
                [sys.executable, "-B", "-c",
                 "import sys;sys.stdin.readline();"
                 "sys.stdout.write('x'*90000+'\\n');sys.stdout.flush()"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=os.name == "posix",
            )
            return process, None
        with patch("holding.mcp_hub_session._spawn_hub",
                   side_effect=fake_spawn):
            with LocalMcpHubSession(
                    interpreter_pin=pinned(), package_root=ROOT,
                    response_timeout=3) as session:
                self.assertEqual(session.discover().state, "OUTPUT_LIMIT")


if __name__ == "__main__":
    unittest.main()
