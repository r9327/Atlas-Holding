import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from holding.bounded_process import BoundedProcessError, run_bounded


class BoundedProcessTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()

    def run_process(self, code, *, timeout=3, stdout_limit=1024):
        return run_bounded(
            (sys.executable, "-B", "-c", code), cwd=self.root,
            environment={"PYTHONUTF8": "1"}, timeout_seconds=timeout,
            stdout_limit=stdout_limit, stderr_limit=1024,
        )

    def test_real_process_has_only_explicit_environment(self):
        os.environ["ATLAS_TEST_SECRET"] = "must-not-leak"
        self.addCleanup(os.environ.pop, "ATLAS_TEST_SECRET", None)
        result = self.run_process(
            "import os;print(os.getenv('ATLAS_TEST_SECRET','absent'))"
        )
        self.assertEqual((result.returncode, result.stdout.strip()), (0, b"absent"))

    def test_timeout_terminates_process_tree(self):
        started = time.monotonic()
        with self.assertRaisesRegex(BoundedProcessError, "timed out"):
            self.run_process("import time;time.sleep(20)", timeout=.15)
        self.assertLess(time.monotonic() - started, 3)

    def test_stdout_and_stderr_are_bounded(self):
        with self.assertRaisesRegex(BoundedProcessError, "stdout"):
            self.run_process("import sys;sys.stdout.write('x'*10000);sys.stdout.flush()")
        with self.assertRaisesRegex(BoundedProcessError, "stderr"):
            self.run_process("import sys;sys.stderr.write('x'*10000);sys.stderr.flush()")

    def test_small_binary_output_is_returned_exactly(self):
        result = self.run_process("import sys;sys.stdout.buffer.write(b'abc\\x00def')")
        self.assertEqual(result.stdout, b"abc\x00def")


if __name__ == "__main__":
    unittest.main()
