"""Ensure a successful POSIX agent leaves no idle helper processes."""
import os
import sys
import time
import tempfile
from pathlib import Path
import unittest

from holding.process_agent import AgentProgram, LocalProcessAgent


class AgentDescendantTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group behavior')
    def test_successful_agent_cleans_up_child_process_group(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sentinel = root / 'helper_survived'
            helper = root / 'helper.py'
            helper.write_text(
                'import sys,time,pathlib\n'
                'time.sleep(1.2)\n'
                'pathlib.Path(sys.argv[1]).write_text("alive")\n',
                encoding='utf8')
            parent = root / 'agent.py'
            parent.write_text(
                'import json,sys,subprocess\n'
                f'subprocess.Popen([sys.executable,{str(helper)!r},{str(sentinel)!r}], '
                'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n'
                "print(json.dumps({'protocol':1,'action':'health','status':'ok'}))\n",
                encoding='utf8')
            agent = LocalProcessAgent(AgentProgram(
                Path(sys.executable).resolve(), (str(parent),), timeout_seconds=3))
            reply = agent.request('health', {}, cwd=root)
            self.assertEqual(reply['status'], 'ok')
            time.sleep(1.6)
            self.assertFalse(sentinel.exists(), 'Agent descendant survived cleanup')


if __name__ == '__main__':
    unittest.main()
