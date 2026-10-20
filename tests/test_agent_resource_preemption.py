"""Resource pressure DURING agent generation must interrupt the subprocess."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from holding.autonomy import StandingGrant
from holding.git_worktree import GitWorktrees
from holding.process_agent import AgentProgram
from holding.resource_guard import ResourceReport, ResourceGuard
from holding.roadmap_import import RoadmapImporter
from holding.service import HoldingService, ServiceSettings
from holding.state_store import StateStore

REPO = 'r9327/Dof-Atlas'


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class ResourcePreemptionTests(unittest.TestCase):
    def test_memory_pressure_kills_busy_local_model_without_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            canonical = root / 'canonical'
            canonical.mkdir()
            git(canonical, 'init', '-b', 'main')
            git(canonical, 'config', 'user.email', 'fixture@invalid.test')
            git(canonical, 'config', 'user.name', 'Test')
            (canonical / 'README.md').write_text('baseline')
            git(canonical, 'add', 'README.md')
            git(canonical, 'commit', '-m', 'initial')
            git(canonical, 'remote', 'add', 'origin',
                'https://github.com/r9327/Dof-Atlas.git')
            original_sha = git(canonical, 'rev-parse', 'HEAD')

            script = root / 'slow_adapter.py'
            script.write_text(
                'import json,sys,time\n'
                'req=json.load(sys.stdin)\n'
                "if req['action']=='health':\n"
                " print(json.dumps({'protocol':1,'action':'health','status':'ok','agent_ready':True}))\n"
                'else:\n'
                ' time.sleep(10)\n'
                " print(json.dumps({'protocol':1,'action':'plan_patch','status':'ok','plan':{"
                "'files':[{'path':'bad.py','content':'x=1'}],"
                "'commit_message':'feat: never run'}}))\n", encoding='utf8')
            dbpath = root / 'state.sqlite'
            with StateStore(dbpath) as store:
                RoadmapImporter(store).import_text(json.dumps({
                    'repo': REPO, 'missions': [
                        {'id': 'task', 'operation': 'code', 'acceptance': ['make safe code']}
                    ]}), source='resource-regression')
                store.grant_for_onboarding(StandingGrant(
                    REPO, frozenset({'code', 'commit'}), int(time.time()) + 300))
            program = AgentProgram(Path(sys.executable).resolve(),
                                   (str(script),), timeout_seconds=20)
            git_manager = GitWorktrees(canonical, root / 'worktrees', REPO)
            with HoldingService.from_local_agent(
                    ServiceSettings(dbpath, idle_seconds=.05),
                    program=program, git=git_manager,
                    health_directory=root, online=lambda: False) as service:
                service.start()
                start = time.monotonic()
                def simulated_pressure(_self):
                    if time.monotonic() - start < .6:
                        return ResourceReport(True, 'OK', 10**10, 10**10)
                    return ResourceReport(False, 'RAM_PRESSURE', 10**10, 0)
                with patch.object(ResourceGuard, 'check', autospec=True,
                                  side_effect=simulated_pressure):
                    result = service.run(max_ticks=1)
                self.assertEqual(result.action, 'RECOVERY_REQUIRED')
                self.assertLess(time.monotonic() - start, 5)
                self.assertEqual(service.engine.store.missions()[0].state, 'RECOVERY_REQUIRED')
                self.assertEqual(
                    service.engine.store.missions()[0].failures_without_progress, 0)
                self.assertFalse((root / 'worktrees' / 'task' / 'bad.py').exists())
            self.assertEqual(git(canonical, 'rev-parse', 'HEAD'), original_sha)


if __name__ == '__main__':
    unittest.main()
