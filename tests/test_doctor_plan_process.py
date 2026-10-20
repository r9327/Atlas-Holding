"""A real, isolated Doctor preflight child obeys resource and secret boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from holding.doctor_plan import DoctorPlanError, _run_bounded_planning


class BoundedPlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_real_planning_child_cannot_read_github_token(self):
        before = os.environ.get("GITHUB_TOKEN")
        os.environ["GITHUB_TOKEN"] = "secret-not-for-doctor"
        try:
            code, raw = _run_bounded_planning(
                [sys.executable, "-c",
                 "import os,json;print(json.dumps({'token':os.getenv('GITHUB_TOKEN')}))"],
                cwd=self.root, timeout_seconds=5)
        finally:
            if before is None:
                os.environ.pop("GITHUB_TOKEN", None)
            else:
                os.environ["GITHUB_TOKEN"] = before
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(raw)["token"])

    def test_oversized_stdout_is_killed_early(self):
        started = time.monotonic()
        with self.assertRaisesRegex(DoctorPlanError, "exceeds configured limit"):
            _run_bounded_planning(
                [sys.executable, "-c",
                 "import sys;sys.stdout.write('x'*2000000);sys.stdout.flush()"],
                cwd=self.root, timeout_seconds=5)
        self.assertLess(time.monotonic() - started, 4)

    def test_hung_child_timeout_is_enforced(self):
        started = time.monotonic()
        with self.assertRaisesRegex(DoctorPlanError, "exceeded deadline"):
            _run_bounded_planning(
                [sys.executable, "-c", "import time;time.sleep(20)"],
                cwd=self.root, timeout_seconds=1)
        self.assertLess(time.monotonic() - started, 4)


if __name__ == "__main__":
    unittest.main()
