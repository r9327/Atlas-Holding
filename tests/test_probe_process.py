"""Probe outputs are bounded in memory, even for hostile local binaries."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest

from holding.probe_process import ProbeProcessError, run_probe


class ProbeProcessTests(unittest.TestCase):
    def probe(self, script: str, *, timeout=3, maximum=1024):
        return run_probe(
            [sys.executable, "-B", "-c", script],
            environment=dict(os.environ), timeout=timeout,
            max_output_bytes=maximum,
        )

    def test_normal_probe_returns_bounded_text(self):
        done = self.probe(
            "import sys;print('healthy');print('status',file=sys.stderr)",
        )
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), "healthy")
        self.assertEqual(done.stderr.strip(), "status")

    def test_flooding_stdout_is_refused_before_unbounded_capture(self):
        with self.assertRaisesRegex(ProbeProcessError, "bounded|limit"):
            self.probe(
                "import sys;sys.stdout.write('A'*2000000);sys.stdout.flush()",
            )

    def test_flooding_stderr_is_refused(self):
        with self.assertRaisesRegex(ProbeProcessError, "bounded|limit"):
            self.probe(
                "import sys;sys.stderr.write('B'*2000000);sys.stderr.flush()",
            )

    def test_hung_probe_times_out(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.probe("import time;time.sleep(5)", timeout=0.15)

    def test_descendant_cannot_hold_output_pipe_after_parent_exits(self):
        done = self.probe(
            "import subprocess,sys;"
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(8)'],"
            "stdout=sys.stdout,stderr=sys.stderr);"
            "print('parent finished',flush=True)",
            timeout=3,
        )
        self.assertEqual(done.returncode, 0)
        self.assertIn("parent finished", done.stdout)

    def test_invalid_budget_refused_before_launch(self):
        with self.assertRaises(ValueError):
            self.probe("print('should not run')", maximum=0)


if __name__ == "__main__":
    unittest.main()
