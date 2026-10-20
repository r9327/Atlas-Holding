"""Real pinned Python child -> stub Ollama -> isolated Git commit -> QA_PENDING."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from holding.daemon_entry import Installation
from holding.roadmap_import import RoadmapImporter
from holding.state_store import StateStore
from holding.autonomy import StandingGrant
from holding import ollama_protocol

MODEL = 'holding-fixture:Q4'
DIGEST = 'a' * 64


class Handler(BaseHTTPRequestHandler):
    received = []
    def log_message(self, *_):
        pass
    def do_GET(self):
        if self.path != '/api/tags':
            self.send_error(404); return
        self.send_json({'models': [{'name': MODEL, 'digest': DIGEST}]})
    def do_POST(self):
        if self.path != '/api/chat':
            self.send_error(404); return
        req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).received.append(req)
        plan = {'files': [{'path': 'src/generated.py', 'content': 'answer = 42\n'}],
                'commit_message': 'feat: implement acceptance requirement'}
        self.send_json({'message': {'content': json.dumps(plan)}})
    def send_json(self, data):
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True).stdout.strip()


class OllamaServiceTests(unittest.TestCase):
    def test_pinned_installed_child_writes_only_isolated_worktree(self):
        try:
            server = ThreadingHTTPServer(('127.0.0.1', 11434), Handler)
        except OSError:
            self.skipTest('Ollama port 11434 already in use')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                canonical = root / 'canonical'
                canonical.mkdir()
                git(canonical, 'init', '-b', 'main')
                git(canonical, 'config', 'user.name', 'Test')
                git(canonical, 'config', 'user.email', 'test@example.invalid')
                (canonical / 'README.md').write_text('baseline')
                git(canonical, 'add', 'README.md')
                git(canonical, 'commit', '-m', 'start')
                git(canonical, 'remote', 'add', 'origin', 'https://github.com/r9327/Dof-Atlas.git')
                initial_sha = git(canonical, 'rev-parse', 'HEAD')
                script = root / 'ollama_protocol.py'
                shutil.copy2(ollama_protocol.__file__, script)
                config = root / 'model.json'
                config.write_text(json.dumps({'schema': 1, 'model': MODEL,
                    'digest': DIGEST, 'num_ctx': 4096, 'timeout': 6}))
                dbpath = root / 'state.sqlite'
                with StateStore(dbpath) as db:
                    RoadmapImporter(db).import_text(json.dumps({'repo': 'r9327/Dof-Atlas',
                       'missions': [{'id': 'job', 'operation': 'code',
                       'acceptance': ['Add answer = 42 in src/generated.py']}]}), source='test')
                    db.grant_for_onboarding(StandingGrant('r9327/Dof-Atlas',
                                    frozenset({'code', 'commit'}), int(time.time()) + 300))
                exe = Path(sys.executable).resolve()
                pinned = {'schema': 1, 'database': str(dbpath),
                    'repository': 'r9327/Dof-Atlas', 'canonical_root': str(canonical),
                    'workspace': str(root / 'worktrees'), 'health_directory': str(root),
                    'executable': str(exe),
                    'executable_sha256': hashlib.sha256(exe.read_bytes()).hexdigest(),
                    'arguments': [str(script), str(config)],
                    'argument_sha256': {
                        str(script): hashlib.sha256(script.read_bytes()).hexdigest(),
                        str(config): hashlib.sha256(config.read_bytes()).hexdigest()},
                    'timeout_seconds': 8}
                install = root / 'install.json'
                install.write_text(json.dumps(pinned))
                with Installation.load(install).build_service() as service:
                    self.assertEqual(service.start(), ())
                    result = service.run(max_ticks=1)
                    self.assertEqual(result.action, 'QA_PENDING', result)
                    mission = service.engine.store.missions()[0]
                    self.assertEqual(mission.state, 'QA_PENDING')
                    self.assertEqual(mission.evidence_sha, '')
                    self.assertEqual(mission.candidate_sha,
                                      git(root / 'worktrees' / 'job', 'rev-parse', 'HEAD'))
                self.assertEqual(git(canonical, 'rev-parse', 'HEAD'), initial_sha)
                self.assertEqual((root / 'worktrees' / 'job' / 'src' / 'generated.py').read_text(),
                                 'answer = 42\n')
                self.assertEqual(json.loads(Handler.received[-1]['messages'][1]['content'])
                                 ['requirements'], ['Add answer = 42 in src/generated.py'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
