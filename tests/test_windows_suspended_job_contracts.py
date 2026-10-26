"""Native Windows regression for process launchers using CREATE_SUSPENDED.

The Job Object must decide assignment from the kernel handle, never from a
pre-attach Popen.poll() result. Failed assignment must never resume a child.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from holding.bounded_process import (
    CREATE_SUSPENDED as BOUNDED_SUSPENDED,
    _WindowsJob as BoundedWindowsJob,
)
from holding.probe_process import (
    CREATE_SUSPENDED as PROBE_SUSPENDED,
    _WindowsJob as ProbeWindowsJob,
)


class WindowsSuspendedJobContracts(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Requires actual Win32 Job Objects")
    def test_bounded_and_probe_jobs_assign_suspended_child_despite_stale_poll(self):
        for label, job_type, flag in (
            ("bounded_process", BoundedWindowsJob, BOUNDED_SUSPENDED),
            ("probe_process", ProbeWindowsJob, PROBE_SUSPENDED),
        ):
            with self.subTest(module=label):
                job = job_type()
                proc = None
                try:
                    proc = subprocess.Popen(
                        [sys.executable, "-c", "print(42)"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        creationflags=flag,
                    )
                    with patch.object(proc, "poll", return_value=0):
                        job.attach_and_resume(proc)
                    stdout, _ = proc.communicate(timeout=8)
                    self.assertEqual(proc.returncode, 0)
                    self.assertEqual(stdout.strip(), b"42")
                finally:
                    job.close()
                    if proc is not None and proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
