"""Real subprocess resource gates for scoped Doctor checks (no Git clone needed)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from holding.qa_scoped import _run_limited_doctor, ScopedQaError


class ScopedRunnerProcessTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def test_small_real_python_output_and_secret_stripping(self):
        before = os.environ.get("GITHUB_TOKEN")
        os.environ["GITHUB_TOKEN"] = "not-for-test-child"
        try:
            code, raw = _run_limited_doctor(
                [sys.executable, "-c",
                 "import os,json; print(json.dumps({'token': os.getenv('GITHUB_TOKEN')}))"],
                cwd=self.root, timeout_seconds=4)
        finally:
            if before is None:
                os.environ.pop("GITHUB_TOKEN", None)
            else:
                os.environ["GITHUB_TOKEN"] = before
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(raw)["token"])

    def test_large_stdout_is_stopped_and_never_buffered(self):
        start = time.monotonic()
        with self.assertRaisesRegex(ScopedQaError, "exceeded 2 MiB"):
            _run_limited_doctor(
                [sys.executable, "-c",
                 "import sys; sys.stdout.write('x'*3000000);sys.stdout.flush()"],
                cwd=self.root, timeout_seconds=5)
        self.assertLess(time.monotonic() - start, 4)

    def test_stalled_child_hits_timeout(self):
        start = time.monotonic()
        with self.assertRaisesRegex(ScopedQaError, "timed out"):
            _run_limited_doctor(
                [sys.executable, "-c", "import time;time.sleep(10)"],
                cwd=self.root, timeout_seconds=0.2)
        self.assertLess(time.monotonic() - start, 3)


if __name__ == "__main__":
    unittest.main()
