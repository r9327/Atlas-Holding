"""Real disposable Git/worktree smoke of autonomous signed-QA repair routing.

An independently checked FAIL is seeded at the database boundary; the full
HMAC verifier is tested separately. No network or production repository used.
"""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from holding.autonomy import StandingGrant
from holding.git_worktree import GitWorktrees
from holding.process_agent import AgentProgram
from holding.roadmap_import import RoadmapImporter
from holding.service import HoldingService, ServiceSettings
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True,
        text=True, check=True).stdout.strip()


class AutonomousRepairIntegrationTests(unittest.TestCase):
    def test_failed_exact_sha_repaired_on_same_isolated_branch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            canonical = root / "original"
            canonical.mkdir()
            git(canonical, "init", "-b", "main")
            git(canonical, "config", "user.name", "Test")
            git(canonical, "config", "user.email", "test@example.invalid")
            (canonical / "README.md").write_text("original", encoding="utf-8")
            git(canonical, "add", "README.md")
            git(canonical, "commit", "-m", "initial")
            git(canonical, "remote", "add", "origin",
                "https://github.com/r9327/Dof-Atlas.git")
            main_sha = git(canonical, "rev-parse", "HEAD")
            agent = root / "trusted_agent.py"
            agent.write_text('''import json, sys
req = json.load(sys.stdin)
if req["action"] == "health":
 print(json.dumps({"protocol": 1, "action": "health",
                   "status": "ok", "agent_ready": True}))
else:
 payload = req["payload"]
 repair = "previous_failed_candidate_sha" in payload
 code = "result=2\\n" if repair else "result=1\\n"
 print(json.dumps({"protocol": 1, "action": "plan_patch", "status": "ok",
    "plan": {"files": [{"path": "src/result.py", "content": code}],
             "commit_message": "fix: repair tested code" if repair else
                               "feat: first local candidate"}}))
''', encoding="utf-8")
            db = root / "holding.sqlite"
            with StateStore(db) as state:
                RoadmapImporter(state).import_text(json.dumps({
                    "repo": REPO, "missions": [{
                        "id": "task", "operation": "code",
                        "acceptance": ["result.py must produce 2"]}]}),
                    source="integration-test")
                state.grant_for_onboarding(StandingGrant(
                    REPO, frozenset({"code", "commit"}),
                    int(time.time()) + 600))
            manager = GitWorktrees(canonical, root / "worktrees", REPO)
            program = AgentProgram(Path(sys.executable).resolve(),
                                   (str(agent),), timeout_seconds=4)
            with HoldingService.from_local_agent(
                    ServiceSettings(db, idle_seconds=0.05), program=program,
                    git=manager, health_directory=root,
                    online=lambda: False) as service:
                service.start()
                self.assertEqual(service.run(max_ticks=1).action, "QA_PENDING")
                first_sha = service.engine.store.missions()[0].candidate_sha
                worktree = root / "worktrees" / "task"
                self.assertEqual((worktree / "src/result.py").read_text(),
                                 "result=1\n")
                with StateStore(db) as state:
                    with state._transaction():
                        state.db.execute("""CREATE TABLE signed_proofs (
                            mission_id TEXT PRIMARY KEY,
                            candidate_sha TEXT NOT NULL, verdict TEXT NOT NULL,
                            artifact TEXT NOT NULL, receipt_digest TEXT NOT NULL)""")
                        state.db.execute(
                            "INSERT INTO signed_proofs VALUES(?,?,?,?,?)",
                            ("task", first_sha, "FAIL", "qa-fixture", "c" * 64))
                        state._transition("task", "QA_PENDING", "BLOCKED",
                                          "independent QA fixture")
                self.assertEqual(service.run(max_ticks=1).action, "QA_PENDING")
                repaired = service.engine.store.missions()[0]
                self.assertNotEqual(repaired.candidate_sha, first_sha)
                self.assertEqual(repaired.evidence_sha, "")
                self.assertEqual(repaired.state, "QA_PENDING")
                self.assertEqual((worktree / "src/result.py").read_text(),
                                 "result=2\n")
                self.assertEqual(git(canonical, "rev-parse", "HEAD"), main_sha)
                self.assertEqual(git(worktree, "branch", "--show-current"),
                                 "holding/task")
                self.assertEqual(service.engine.store.db.execute(
                    "SELECT failed_sha FROM qa_repair_attempts "
                    "WHERE mission_id='task'").fetchone()[0], first_sha)


if __name__ == "__main__":
    unittest.main()
