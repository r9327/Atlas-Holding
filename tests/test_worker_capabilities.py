"""Unsupported operations must not consume retries or block independent work."""
from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from holding.autonomy import Mission, StandingGrant, choose_next
from holding.engine import HoldingEngine
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
SHA = "b" * 40


class WorkerCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "state.sqlite"
        self.jobs = [Mission("a-plan", REPO, operation="plan"),
                     Mission("b-code", REPO, operation="code")]
        with StateStore(self.db) as store:
            store.add_missions(self.jobs)
            store.grant_for_onboarding(StandingGrant(
                REPO, frozenset({"plan", "code"}), int(time.time()) + 3600))

    def test_scheduler_skips_unimplemented_role(self):
        now = int(time.time())
        choice = choose_next(self.jobs, [StandingGrant(
            REPO, frozenset({"plan", "code"}), now + 3600)],
            now_epoch=now, online=False, supported_operations=frozenset({"code"}))
        self.assertEqual((choice.action, choice.mission_id), ("RUN", "b-code"))

    def test_atomic_engine_claim_leaves_unsupported_mission_untouched(self):
        invoked = []
        def worker(job):
            invoked.append(job.id)
            return SHA
        with HoldingEngine(self.db, worker, online=lambda: False,
                           supported_operations=frozenset({"code"})) as engine:
            engine.start()
            self.assertEqual(engine.tick().action, "QA_PENDING")
            choice = engine.tick()
            self.assertEqual((choice.action, choice.mission_id),
                             ("WAIT_FOR_WORKER", "a-plan"))
            rows = {job.id: job for job in engine.store.missions()}
            self.assertEqual(invoked, ["b-code"])
            self.assertEqual(rows["a-plan"].state, "READY")
            self.assertEqual(rows["a-plan"].failures_without_progress, 0)
            self.assertEqual(rows["b-code"].state, "QA_PENDING")

    def test_none_capabilities_preserves_legacy_dispatch(self):
        now = int(time.time())
        choice = choose_next(self.jobs, [StandingGrant(
            REPO, frozenset({"plan", "code"}), now + 3600)],
            now_epoch=now, online=False)
        self.assertEqual((choice.action, choice.mission_id), ("RUN", "a-plan"))


if __name__ == "__main__":
    unittest.main()
