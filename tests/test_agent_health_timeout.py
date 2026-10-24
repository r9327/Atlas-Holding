"""Probe timeouts and ambient profile redaction independent of coding."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from holding.process_agent import AgentProcessError, AgentProgram, LocalProcessAgent


class HealthTimeoutTests(unittest.TestCase):
    def test_health_timeout_is_not_generation_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "unresponsive_health.py"
            script.write_text("import time\ntime.sleep(8)\n", encoding="utf8")
            agent = LocalProcessAgent(AgentProgram(
                Path(sys.executable).resolve(), (str(script),),
                timeout_seconds=1200))
            begin = time.monotonic()
            with patch("holding.process_agent.HEALTH_TIMEOUT_SECONDS", 0.3):
                with self.assertRaisesRegex(AgentProcessError, "timed out"):
                    agent.request("health", {}, cwd=root)
            self.assertLess(time.monotonic() - begin, 3.0)

    def test_model_subprocess_does_not_inherit_secrets_or_profile_hints(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "env_report.py"
            script.write_text(
                "import json,os,sys\n"
                "req=json.load(sys.stdin)\n"
                "keys=['GITHUB_TOKEN','OPENAI_API_KEY','HOME','USERPROFILE','LOCALAPPDATA']\n"
                "print(json.dumps({'protocol':1,'action':req['action'],"
                "'status':'ok','received':{k:os.environ.get(k) for k in keys}}))\n",
                encoding="utf8")
            agent = LocalProcessAgent(AgentProgram(
                Path(sys.executable).resolve(), (str(script),),
                timeout_seconds=3))
            with patch.dict(os.environ, {
                "GITHUB_TOKEN": "sensitive", "OPENAI_API_KEY": "sensitive",
                "HOME": "/sensitive/home", "USERPROFILE": "C:\\sensitive",
                "LOCALAPPDATA": "C:\\sensitive"
            }):
                result = agent.request("health", {}, cwd=root)
            self.assertTrue(all(v is None for v in result["received"].values()))


if __name__ == "__main__":
    unittest.main()
