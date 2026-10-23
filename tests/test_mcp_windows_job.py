"""Native Windows Job Object end-to-end tests for the internal MCP hub."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from holding.mcp_local_inventory import PinnedLocalPlugin
from holding.mcp_stdio import PROTOCOL_VERSION
from holding.mcp_windows_job import (
    JOB_MEMORY_LIMIT, KILL_ON_JOB_CLOSE, ACTIVE_PROCESS_LIMIT,
    WindowsJobError, launch_verified_local_hub,
    _ExtendedLimitInformation,
)

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable).resolve()


def runtime_pin(expected_digest=None):
    digest = hashlib.sha256()
    with PYTHON.open("rb") as stream:
        for part in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(part)
    return PinnedLocalPlugin(
        "atlas-hub-python", "3.13", PYTHON,
        expected_digest or digest.hexdigest(),
    )


def request():
    return (json.dumps({
        "jsonrpc": "2.0", "id": "verify-job",
        "method": "server/discover",
        "params": {"_meta": {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
        }},
    }, separators=(",", ":")) + "\n").encode()


@unittest.skipUnless(os.name == "nt", "Native Windows Job Objects")
class WindowsJobMcpTests(unittest.TestCase):
    def test_real_job_limit_and_mcp_discovery_with_clean_shutdown(self):
        import ctypes.wintypes as wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.IsProcessInJob.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel.IsProcessInJob.restype = wintypes.BOOL
        kernel.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel.QueryInformationJobObject.restype = wintypes.BOOL
        with launch_verified_local_hub(
                interpreter_pin=runtime_pin(), package_root=ROOT,
                job_memory_mb=128, active_limit=2) as managed:
            observed = managed.observe()
            self.assertEqual(observed.process_state, "RUNNING")
            self.assertEqual(observed.pid, managed.process.pid)
            self.assertEqual(observed.job_memory_limit_bytes, 128 * 1024 * 1024)
            self.assertEqual(observed.active_process_limit, 2)
            self.assertEqual(observed.security_scope, "RESOURCE_LIMITS_NOT_SANDBOX")
            process = kernel.OpenProcess(0x1000, False, managed.process.pid)
            self.assertTrue(process)
            try:
                in_job = wintypes.BOOL(0)
                self.assertTrue(kernel.IsProcessInJob(
                    process, managed.job_handle, ctypes.byref(in_job)))
                self.assertTrue(in_job.value)
                limits = _ExtendedLimitInformation()
                returned = wintypes.DWORD(0)
                self.assertTrue(kernel.QueryInformationJobObject(
                    managed.job_handle, 9, ctypes.byref(limits),
                    ctypes.sizeof(limits), ctypes.byref(returned)))
                self.assertEqual(
                    limits.BasicLimitInformation.LimitFlags &
                    (KILL_ON_JOB_CLOSE | JOB_MEMORY_LIMIT | ACTIVE_PROCESS_LIMIT),
                    KILL_ON_JOB_CLOSE | JOB_MEMORY_LIMIT | ACTIVE_PROCESS_LIMIT,
                )
                self.assertEqual(limits.JobMemoryLimit, 128 * 1024 * 1024)
                self.assertEqual(
                    limits.BasicLimitInformation.ActiveProcessLimit, 2)
            finally:
                kernel.CloseHandle(process)
            stdout, stderr = managed.process.communicate(input=request(), timeout=8)
            self.assertEqual(managed.process.returncode, 0)
            self.assertIsNone(stderr)
            doc = json.loads(stdout)
            self.assertEqual(doc["id"], "verify-job")
            self.assertEqual(doc["result"]["supportedVersions"], [PROTOCOL_VERSION])
            self.assertEqual(
                doc["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
                "atlas-holding-local",
            )
        self.assertTrue(managed._closed)
        closed = managed.observe()
        self.assertEqual(closed.process_state, "CLOSED")
        self.assertIsNone(closed.job_memory_limit_bytes)
        self.assertIsNone(closed.active_process_limit)

    def test_invalid_digests_fail_without_spawning_any_process(self):
        with patch("holding.mcp_windows_job.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(WindowsJobError, "not verified"):
                launch_verified_local_hub(
                    interpreter_pin=runtime_pin("a" * 64),
                    package_root=ROOT,
                )
            spawn.assert_not_called()

    def test_bogus_root_and_limits_fail_closed_before_process_spawn(self):
        with patch("holding.mcp_windows_job.subprocess.Popen") as spawn:
            for kwargs in (
                {"package_root": Path(".")},
                {"package_root": ROOT.parent},
                {"job_memory_mb": 20},
                {"job_memory_mb": 2048},
                {"active_limit": 0},
                {"active_limit": 10},
                {"active_limit": True},
            ):
                params = dict(
                    interpreter_pin=runtime_pin(), package_root=ROOT)
                params.update(kwargs)
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(ValueError):
                        launch_verified_local_hub(**params)
            spawn.assert_not_called()

    def test_failed_assignment_destroys_suspended_child_before_execution(self):
        import subprocess
        from holding.mcp_windows_job import WindowsJobError
        original_spawn = subprocess.Popen
        started = []

        def observe_spawn(*args, **kwargs):
            process = original_spawn(*args, **kwargs)
            started.append(process)
            return process

        with patch("holding.mcp_windows_job._assign",
                   side_effect=WindowsJobError("fake assignment rejected")):
            with patch("holding.mcp_windows_job.subprocess.Popen",
                       side_effect=observe_spawn):
                with self.assertRaisesRegex(WindowsJobError, "assignment rejected"):
                    launch_verified_local_hub(
                        interpreter_pin=runtime_pin(), package_root=ROOT)
        self.assertEqual(len(started), 1)
        self.assertIsNotNone(started[0].poll())
        self.assertTrue(started[0].stdin.closed)
        self.assertTrue(started[0].stdout.closed)

    def test_failed_thread_resume_kills_child_and_releases_resources(self):
        import subprocess
        original_spawn = subprocess.Popen
        launched = []

        def observe_spawn(*args, **kwargs):
            process = original_spawn(*args, **kwargs)
            launched.append(process)
            return process

        with patch("holding.mcp_windows_job._resume_suspended_child",
                   side_effect=WindowsJobError("fake thread resume denied")):
            with patch("holding.mcp_windows_job.subprocess.Popen",
                       side_effect=observe_spawn):
                with self.assertRaisesRegex(WindowsJobError, "resume denied"):
                    launch_verified_local_hub(
                        interpreter_pin=runtime_pin(), package_root=ROOT)
        self.assertEqual(len(launched), 1)
        self.assertIsNotNone(launched[0].poll())
        self.assertTrue(launched[0].stdin.closed)
        self.assertTrue(launched[0].stdout.closed)

    def test_job_close_terminates_idle_stdio_child(self):
        managed = launch_verified_local_hub(
            interpreter_pin=runtime_pin(), package_root=ROOT)
        child = managed.process
        self.assertIsNone(child.poll())
        managed.close()
        self.assertIsNotNone(child.poll())
        managed.close()  # Repeatable without second native handle closure.


class CrossPlatformWindowsGateTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Non-Windows refusal only")
    def test_windows_job_launcher_never_falls_back_to_unmanaged_linux_child(self):
        with self.assertRaisesRegex(WindowsJobError, "only available on Windows"):
            launch_verified_local_hub(
                interpreter_pin=runtime_pin(), package_root=ROOT)


if __name__ == "__main__":
    unittest.main()
