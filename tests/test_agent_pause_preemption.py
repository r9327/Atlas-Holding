"""Live process smoke test for cooperative Pause while the LLM is generating."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from holding.autonomy import StandingGrant
from holding.git_worktree import GitWorktrees
from holding.process_agent import AgentProgram
from holding.roadmap_import import RoadmapImporter
from holding.service import HoldingService, ServiceSettings
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True,
                   capture_output=True, text=True)


class PausePreemptionTests(unittest.TestCase):
    def test_pause_preempts_real_running_model_without_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            canonical = root / "repo"
            canonical.mkdir()
            git(canonical, "init", "-b", "main")
            git(canonical, "config", "user.name", "Test")
            git(canonical, "config", "user.email", "test@example.invalid")
            (canonical / "README.md").write_text("base")
            git(canonical, "add", "README.md")
            git(canonical, "commit", "-m", "initial")
            git(canonical, "remote", "add", "origin",
                "https://github.com/r9327/Dof-Atlas.git")
            script = root / "slow_agent.py"
            script.write_text(
                "import json,sys,time\n"
                "from pathlib import Path\n"
                "req=json.load(sys.stdin)\n"
                "if req['action']=='health':\n"
                " print(json.dumps({'protocol':1,'action':'health','status':'ok',"
                "'agent_ready':True}))\n"
                "else:\n"
                " Path(__file__).with_suffix('.started').write_text('running')\n"
                " time.sleep(15)\n"
                " print(json.dumps({'protocol':1,'action':'plan_patch','status':'ok',"
                "'plan':{'files':[{'path':'unexpected.py','content':'x=1'}],"
                "'commit_message':'feat: delayed patch'}}))\n",
                encoding="utf-8")
            marker = script.with_suffix(".started")
            database = root / "state.sqlite"
            with StateStore(database) as store:
                RoadmapImporter(store).import_text(json.dumps({
                    "repo": REPO, "missions": [{
                        "id": "job", "operation": "code",
                        "acceptance": ["create code"]}]}), source="test")
                store.grant_for_onboarding(StandingGrant(
                    REPO, frozenset(("code", "commit")),
                    int(time.time()) + 3600))
            program = AgentProgram(
                Path(sys.executable).resolve(), (str(script),), timeout_seconds=20)
            worktrees = GitWorktrees(canonical, root / "jobs", REPO)
            with HoldingService.from_local_agent(
                ServiceSettings(database, idle_seconds=.05), program=program,
                git=worktrees, health_directory=root, online=lambda: False
            ) as service:
                service.start()

                def pause_when_generating():
                    deadline = time.monotonic() + 6
                    while not marker.is_file() and time.monotonic() < deadline:
                        time.sleep(.02)
                    if marker.is_file():
                        with StateStore(database) as control:
                            control.set_paused(True)

                thread = threading.Thread(target=pause_when_generating, daemon=True)
                thread.start()
                start = time.monotonic()
                try:
                    result = service.run(max_ticks=1)
                    self.assertTrue(marker.is_file(), "Model never started")
                    self.assertEqual(result.action, "RECOVERY_REQUIRED")
                    self.assertLess(time.monotonic() - start, 6)
                    self.assertFalse((root / "jobs" / "job" / "unexpected.py").exists())
                    self.assertEqual(
                        service.engine.store.missions()[0].state, "RECOVERY_REQUIRED")
                finally:
                    thread.join(timeout=7)


if __name__ == "__main__":
    unittest.main()
