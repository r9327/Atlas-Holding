import json
from pathlib import Path
import sys
import tempfile
import textwrap
import time
import unittest

from holding.process_agent import AgentProgram, AgentProcessError, LocalProcessAgent

class ProcessAgentTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.workdir = Path(self.folder.name)

    def agent(self, body, *, timeout=2, limit=2048, stop=lambda:False):
        path = self.workdir / 'trusted_agent.py'
        path.write_text(textwrap.dedent(body), encoding='utf8')
        return LocalProcessAgent(AgentProgram(Path(sys.executable).resolve(),
               (str(path),), timeout, limit),should_stop=stop)

    def test_valid_json_reply(self):
        worker = self.agent('''
            import sys, json
            request=json.load(sys.stdin)
            print(json.dumps({'protocol':1,'action':request['action'],'status':'ok','plan':[]}))
        ''')
        self.assertEqual(worker.request('health',{},cwd=self.workdir)['status'],'ok')

    def test_no_environment_secret_leak(self):
        import os
        worker = self.agent('''
            import os, sys, json
            req=json.load(sys.stdin)
            print(json.dumps({'protocol':1,'action':req['action'],'status':'ok',
                              'secret':os.getenv('GITHUB_TOKEN')}))
        ''')
        before = os.environ.get('GITHUB_TOKEN')
        os.environ['GITHUB_TOKEN']='do-not-leak'
        try:
            self.assertIsNone(worker.request('health',{},cwd=self.workdir)['secret'])
        finally:
            if before is None: os.environ.pop('GITHUB_TOKEN',None)
            else: os.environ['GITHUB_TOKEN']=before

    def test_deadline_enforced(self):
        worker=self.agent('''
            import time
            time.sleep(10)
        ''',timeout=1)
        t=time.monotonic()
        with self.assertRaisesRegex(AgentProcessError,'timed out'):
            worker.request('plan_patch',{'mission':'a'},cwd=self.workdir)
        self.assertLess(time.monotonic()-t,4)

    def test_controller_stop_enforced(self):
        worker=self.agent('''
            import time
            time.sleep(10)
        ''',stop=lambda:True)
        with self.assertRaisesRegex(AgentProcessError,'interrupted'):
            worker.request('plan_patch',{},cwd=self.workdir)

    def test_output_limit_enforced(self):
        worker=self.agent('''
            import sys
            print('x'*10000, flush=True)
        ''',limit=2048)
        with self.assertRaisesRegex(AgentProcessError,'output exceeded'):
            worker.request('health',{},cwd=self.workdir)

    def test_non_json_refused(self):
        worker=self.agent("print('not json')")
        with self.assertRaisesRegex(AgentProcessError,'Invalid agent JSON'):
            worker.request('health',{},cwd=self.workdir)

    def test_unknown_action_refused(self):
        worker=self.agent("print('unused')")
        with self.assertRaisesRegex(AgentProcessError,'Unsupported'):
            worker.request('shell_command',{'command':'rm -rf /'},cwd=self.workdir)

    def test_false_health_refused(self):
        worker=self.agent("print('''{\"protocol\":1,\"action\":\"health\",\"status\":\"ok\"}''')")
        with self.assertRaisesRegex(AgentProcessError,'protocol'):
            worker.request('plan_patch',{},cwd=self.workdir)

    def test_untrusted_binary_path_rejected(self):
        with self.assertRaises(ValueError):
            AgentProgram(Path('relative/program.exe'))

if __name__=='__main__': unittest.main()
