"""Fail-closed network input and Pause behavior of the core dispatcher."""
from pathlib import Path
import tempfile
import time
import unittest

from holding.autonomy import Mission, StandingGrant
from holding.engine import HoldingEngine
from holding.state_store import StateStore

REPOSITORY = "r9327/Dof-Atlas"
CANDIDATE = "a" * 40


class NetworkProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db_path = Path(temp.name) / "core.sqlite"
        with StateStore(self.db_path) as store:
            store.add_missions((
                Mission("external", REPOSITORY, priority=10, external_required=True),
                Mission("local", REPOSITORY, priority=0, external_required=False),
            ))
            store.grant_for_onboarding(StandingGrant(
                REPOSITORY, frozenset({"code"}), int(time.time()) + 600))

    def _engine(self, online):
        return HoldingEngine(self.db_path, lambda _: CANDIDATE, online=online)

    def test_paused_engine_does_not_probe_network_or_claim(self):
        count = []
        with self._engine(lambda: count.append("probed") or True) as engine:
            engine.start()
            engine.pause()
            self.assertEqual(engine.tick().action, "WAIT")
            self.assertEqual(count, [])
            self.assertTrue(all(m.state == "READY" for m in engine.store.missions()))
            engine.resume()
            result = engine.tick()
            self.assertEqual(result.action, "QA_PENDING")
            self.assertEqual(result.mission_id, "external")
            self.assertEqual(count, ["probed"])

    def test_stopped_engine_does_not_probe_network(self):
        count = []
        with self._engine(lambda: count.append("probed") or True) as engine:
            engine.start()
            engine.stop()
            self.assertEqual(engine.tick().action, "STOPPED")
            self.assertEqual(count, [])

    def test_unavailable_network_cannot_block_independent_local_code(self):
        for failure in (OSError("offline"), TimeoutError("timeout"), ValueError("bad reply")):
            with self.subTest(failure=type(failure).__name__):
                with self._engine(lambda: (_ for _ in ()).throw(failure)) as engine:
                    engine.start()
                    result = engine.tick()
                    self.assertEqual(result.action, "QA_PENDING")
                    self.assertEqual(result.mission_id, "local")
                    states = {m.id: m.state for m in engine.store.missions()}
                    self.assertEqual(states, {"external": "READY", "local": "QA_PENDING"})
                # Restore the fixture to test all failures independently.
                with StateStore(self.db_path) as state:
                    state.db.execute(
                        "UPDATE missions SET state='READY',candidate_sha='',evidence_sha='' "
                        "WHERE id='local'")

    def test_truthy_non_boolean_is_not_online_evidence(self):
        for value in ("ONLINE", 1, ["ready"]):
            with self.subTest(value=value):
                with self._engine(lambda: value) as engine:
                    engine.start()
                    result = engine.tick()
                    self.assertEqual(result.action, "QA_PENDING")
                    self.assertEqual(result.mission_id, "local")
                with StateStore(self.db_path) as state:
                    state.db.execute(
                        "UPDATE missions SET state='READY',candidate_sha='',evidence_sha='' "
                        "WHERE id='local'")

    def test_offline_only_tasks_skip_even_a_broken_network_detector(self):
        with StateStore(self.db_path) as state:
            state.db.execute("UPDATE missions SET state='CANCELLED' WHERE id='external'")
        calls = []
        def unexpected_probe():
            calls.append("network")
            raise AssertionError("No network probe for an offline-only roadmap")
        with self._engine(unexpected_probe) as engine:
            engine.start()
            self.assertEqual(engine.tick().mission_id, "local")
            self.assertEqual(engine.tick().action, "WAIT")
        self.assertEqual(calls, [])

    def test_true_boolean_enables_external_work(self):
        with self._engine(lambda: True) as engine:
            engine.start()
            result = engine.tick()
            self.assertEqual(result.action, "QA_PENDING")
            self.assertEqual(result.mission_id, "external")


if __name__ == "__main__":
    unittest.main()
