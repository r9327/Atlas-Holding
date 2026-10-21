"""Offline integration tests: real loopback HTTP server, no model downloads."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from holding import ollama_protocol as op

DIGEST = 'a' * 64
MODEL = 'atlas-test:Q4'
CONFIG = {'schema': 1, 'model': MODEL, 'digest': DIGEST, 'num_ctx': 4096, 'timeout': 5}
PLAN = {'files': [{'path': 'src/generated.py', 'content': 'answer = 42\n'}],
        'commit_message': 'feat: implement requested change'}


class Stub(BaseHTTPRequestHandler):
    recorded = []
    digest = DIGEST

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path != '/api/tags':
            self.send_error(404)
            return
        self._reply({'models': [{'name': MODEL, 'digest': type(self).digest}]})

    def do_POST(self):
        if self.path != '/api/chat':
            self.send_error(404)
            return
        req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).recorded.append(req)
        self._reply({'message': {'role': 'assistant', 'content': json.dumps(PLAN)}})

    def _reply(self, data):
        raw = json.dumps(data).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class OllamaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'existing.py').write_text('def get(): return 1\n')
        self.config_path = self.root / 'config.json'
        self.config_path.write_text(json.dumps(CONFIG))
        Stub.recorded.clear()
        Stub.digest = DIGEST
        self.url_patch = patch.object(op, 'BASE', f'http://127.0.0.1:{self.server.server_port}')
        self.url_patch.start()
        self.addCleanup(self.url_patch.stop)

    def payload(self):
        return {'mission_id': 'code-1', 'repository': 'r9327/Dof-Atlas',
                'acceptance': ['Add the answer field'], 'base_sha': 'a' * 40}

    def test_native_adapter_self_test_is_offline_and_requires_real_config_for_work(self):
        with patch.object(op, '_installed_model', side_effect=AssertionError('called')), \
             patch.object(op, '_http', side_effect=AssertionError('called')):
            self.assertEqual(op.main(['--self-test']), 0)
            self.assertEqual(op.main(['--self-test', str(self.config_path)]), 2)
        with patch('sys.stdin') as fake:
            fake.buffer.read.return_value = (
                b'{"protocol":1,"action":"health","payload":{}}')
            self.assertEqual(op.main([str(self.config_path)]), 0)

    def test_real_loopback_health_check(self):
        result = op.dispatch(CONFIG, {'protocol': 1, 'action': 'health', 'payload': {}}, self.root)
        self.assertEqual(result['agent_ready'], True)

    def test_real_loopback_chat_returns_valid_patch(self):
        result = op.dispatch(CONFIG, {'protocol': 1, 'action': 'plan_patch',
                                      'payload': self.payload()}, self.root)
        self.assertEqual(result['plan'], PLAN)
        request = Stub.recorded[-1]
        self.assertEqual(request['model'], MODEL)
        self.assertFalse(request['stream'])
        self.assertEqual(request['options']['num_ctx'], 4096)
        context = json.loads(request['messages'][1]['content'])
        self.assertEqual(context['requirements'], ['Add the answer field'])
        self.assertIn('src/existing.py', [x['path'] for x in context['source_files']])

    def test_context_prioritizes_relevant_source_outside_first_40_files(self):
        filler = self.root / 'aaa'
        filler.mkdir()
        for index in range(55):
            (filler / f'boring_{index:03d}.py').write_text('unrelated = 1')
        relevant = self.root / 'zzz' / 'quest'
        relevant.mkdir(parents=True)
        (relevant / 'quest_progress.py').write_text('def quest_progress(): return True')
        payload = self.payload()
        payload['acceptance'] = ['Repair quest progress recording']
        op.dispatch(CONFIG, {'protocol': 1, 'action': 'plan_patch',
                             'payload': payload}, self.root)
        context = json.loads(Stub.recorded[-1]['messages'][1]['content'])
        self.assertIn('zzz/quest/quest_progress.py',
                      [file['path'] for file in context['source_files']])
        self.assertLessEqual(len(context['source_files']), 40)
        self.assertEqual(context['source_files'][0]['path'],
                         'zzz/quest/quest_progress.py')

    def test_signed_fail_sha_reaches_local_repair_prompt(self):
        payload = self.payload()
        payload['previous_failed_candidate_sha'] = 'b' * 40
        payload['repair_objective'] = 'This freeform field must not be copied'
        op.dispatch(CONFIG, {'protocol': 1, 'action': 'plan_patch',
                             'payload': payload}, self.root)
        prompt = json.loads(Stub.recorded[-1]['messages'][1]['content'])
        self.assertEqual(prompt['previous_failed_candidate_sha'], 'b' * 40)
        self.assertIn('root cause', prompt['repair_guidance'])
        self.assertNotIn(payload['repair_objective'], Stub.recorded[-1]['messages'][1]['content'])
        Stub.recorded.clear()
        payload['previous_failed_candidate_sha'] = 'not-a-sha'
        with self.assertRaises(op.AdapterError):
            op.dispatch(CONFIG, {'protocol': 1, 'action': 'plan_patch',
                                 'payload': payload}, self.root)
        self.assertEqual(Stub.recorded, [])

    def test_pinned_model_digest_change_fail_closed(self):
        Stub.digest = 'b' * 64
        with self.assertRaisesRegex(op.AdapterError, 'digest changed'):
            op.dispatch(CONFIG, {'protocol': 1, 'action': 'health', 'payload': {}}, self.root)
        self.assertEqual(Stub.recorded, [])

    def test_no_external_proxy_used(self):
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:1',
                                     'http_proxy': 'http://127.0.0.1:1'}):
            self.assertTrue(op.dispatch(CONFIG, {'protocol': 1, 'action': 'health',
                                                 'payload': {}}, self.root)['agent_ready'])

    def test_source_context_does_not_read_secrets_or_symlinks(self):
        (self.root / '.env').write_text('PASSWORD=secret')
        (self.root / 'api_token.json').write_text('SECRET')
        (self.root / '.github').mkdir()
        (self.root / '.github' / 'workflows.yml').write_text('PASS: disabled')
        outside = self.root / 'outside.json'
        outside.write_text('PRIVATE')
        try:
            (self.root / 'src' / 'linked.json').symlink_to(outside)
        except OSError:
            pass
        names = [entry['path'] for entry in op._snapshot(self.root)]
        self.assertEqual(names, ['config.json', 'outside.json', 'src/existing.py'])
        self.assertNotIn('PRIVATE', str(op._snapshot(self.root / 'src')))

    def test_config_rejects_unpinned_or_external_url_fields(self):
        self.config_path.write_text(json.dumps(dict(CONFIG, host='http://evil.example')))
        with self.assertRaises(op.AdapterError):
            op._config(self.config_path)

    def test_config_accepts_only_bounded_options(self):
        for change in ({'digest': 'wrong'}, {'num_ctx': 99999999},
                       {'timeout': -1}, {'model': '../../secrets'}, {'schema': False}):
            self.config_path.write_text(json.dumps(dict(CONFIG, **change)))
            with self.subTest(change=change), self.assertRaises(op.AdapterError):
                op._config(self.config_path)

    def test_empty_mission_requirements_do_not_request_generation(self):
        payload = self.payload()
        payload['acceptance'] = []
        with self.assertRaises(op.AdapterError):
            op.dispatch(CONFIG, {'protocol': 1, 'action': 'plan_patch',
                                  'payload': payload}, self.root)
        self.assertEqual(Stub.recorded, [])

    def test_unknown_protocol_action_denied(self):
        with self.assertRaises(op.AdapterError):
            op.dispatch(CONFIG, {'protocol': 1, 'action': 'run_command', 'payload': {}}, self.root)

    def test_cli_failure_does_not_claim_pass(self):
        self.config_path.write_text(json.dumps(dict(CONFIG, digest='b' * 64)))
        with patch('sys.stdin') as fake:
            fake.buffer.read.return_value = b'{"protocol":1,"action":"health","payload":{}}'
            with patch('sys.stdout') as output:
                self.assertEqual(op.main([str(self.config_path)]), 1)
        text = ''.join(x.args[0] for x in output.write.call_args_list if x.args)
        self.assertNotIn('PASS', text)


if __name__ == '__main__':
    unittest.main()
