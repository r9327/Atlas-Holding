"""Acceptance executable is diagnostic only; no model or repository touched."""
from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

from holding.windows_acceptance_entry import main


class AcceptanceEntrypointTests(unittest.TestCase):
    def test_standalone_self_test_never_calls_agent_or_git(self):
        output = io.StringIO()
        with patch("holding.windows_acceptance_entry.run_mission",
                   side_effect=AssertionError("Cannot execute mission")) as worker, \
             contextlib.redirect_stdout(output):
            self.assertEqual(main(["--self-test"]), 0)
            worker.assert_not_called()
        self.assertIn("NONRELEASE_ACCEPTANCE_RUNTIME_SELF_TEST_PASS",
                      output.getvalue())

    def test_self_test_cannot_be_mixed_with_execution_arguments(self):
        with patch("holding.windows_acceptance_entry.run_mission",
                   side_effect=AssertionError("Cannot execute mission")):
            self.assertEqual(main([
                "--self-test", "--agent", "arbitrary.exe",
            ]), 2)

    def test_real_mission_dispatch_remains_explicit(self):
        args = [
            "--agent", "C:/approved/AtlasHoldingAgent.exe",
            "--model-json", "C:/approved/model.json",
            "--receipt-json", "C:/approved/proof.json",
        ]
        with patch("holding.windows_acceptance_entry.run_mission",
                   return_value=0) as worker:
            self.assertEqual(main(args), 0)
            worker.assert_called_once_with(args)


if __name__ == "__main__":
    unittest.main()
