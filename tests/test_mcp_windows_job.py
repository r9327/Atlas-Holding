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
