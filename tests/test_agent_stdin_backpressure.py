"""A child that ignores stdin must never bypass the agent watchdog."""
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from holding.process_agent import AgentProcessError, AgentProgram, LocalProcessAgent


class AgentStdinBackpressureTests(unittest.TestCase):
    def test_unread_large_input_still_times_out(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = root / "silent.py"
            child.write_text("import time\n"
                             "time.sleep(8)\n", encoding="utf8")
            agent = LocalProcessAgent(
                AgentProgram(Path(sys.executable).resolve(), (str(child),),
                             timeout_seconds=1, output_limit=2048))
            processes = []
            real_popen = subprocess.Popen
            def tracked(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                processes.append(process)
                return process
            start = time.monotonic()
            with patch("holding.process_agent.subprocess.Popen", side_effect=tracked):
                with self.assertRaisesRegex(AgentProcessError, 'timed out'):
                    agent.request("plan_patch", {"source": "x" * 60_000}, cwd=root)
            self.assertLess(time.monotonic() - start, 4)
            self.assertTrue(processes)
            self.assertTrue(all(process.poll() is not None for process in processes))

    def test_child_stdin_is_seekable_file_not_bounded_pipe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = root / "reader.py"
            child.write_text(
                "import json,sys\n"
                "req=json.load(sys.stdin)\n"
                "print(json.dumps({'protocol':1,'action':req['action'],"
                "'status':'ok','bytes':len(req['payload']['source'])}))\n",
                encoding="utf8")
            agent = LocalProcessAgent(
                AgentProgram(Path(sys.executable).resolve(), (str(child),),
                             timeout_seconds=3))
            real_popen = subprocess.Popen
            observed = []
            def tracked(*args, **kwargs):
                handle = kwargs.get("stdin")
                observed.append(handle is not subprocess.PIPE and
                                hasattr(handle, "seek") and
                                handle.tell() == 0)
                return real_popen(*args, **kwargs)
            with patch("holding.process_agent.subprocess.Popen", side_effect=tracked):
                reply = agent.request("plan_patch", {"source": "x" * 60_000}, cwd=root)
            self.assertTrue(observed and all(observed))
            self.assertEqual(reply["bytes"], 60_000)


if __name__ == "__main__":
    unittest.main()
