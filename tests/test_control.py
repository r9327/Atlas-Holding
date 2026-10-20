import tempfile
from pathlib import Path
import time
import unittest

from holding.autonomy import Mission, StandingGrant
from holding.control import ControlPlane
from holding.engine import HoldingEngine
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite"
        with StateStore(self.path) as store:
            store.add_missions([Mission("a", REPO)])
            store.grant_for_onboarding(StandingGrant(
                REPO, frozenset({"code"}), int(time.time()) + 3600))
            store._event(None, "TEST_SETUP_COMPLETE")

    def test_external_pause_and_resume(self):
        with HoldingEngine(self.path, lambda m: SHA, online=lambda: False) as engine:
            engine.start()
            with ControlPlane(self.path) as control:
                control.pause()
                self.assertTrue(control.snapshot().paused)
                self.assertEqual(engine.tick().action, "WAIT")
                control.resume()
                self.assertEqual(engine.tick().action, "QA_PENDING")

    def test_external_stop_prevents_dispatch(self):
        with HoldingEngine(self.path, lambda m: SHA, online=lambda: False) as engine:
            engine.start()
            with ControlPlane(self.path) as control:
                control.request_stop()
                self.assertTrue(control.snapshot().stop_requested)
                self.assertEqual(engine.tick().action, "STOPPED")
                control.clear_stop_for_restart()
                self.assertEqual(engine.tick().action, "QA_PENDING")

    def test_snapshot_reads_real_state_and_events(self):
        with ControlPlane(self.path) as control:
            snap = control.snapshot()
            self.assertEqual(snap.mission_counts, {"READY": 1})
            self.assertFalse(snap.lease_valid)
            self.assertGreater(snap.last_event_seq, 0)

    def test_scoped_qa_shows_only_matching_pending_sha_and_not_certified(self):
        sha = "d" * 40
        with StateStore(self.path) as state:
            state.db.execute("UPDATE missions SET state='QA_PENDING', candidate_sha=?, "
                             "evidence_sha='' WHERE id='a'", (sha,))
            state.db.execute("""CREATE TABLE scoped_qa_advisories (
                mission_id TEXT PRIMARY KEY, candidate_sha TEXT NOT NULL,
                status TEXT NOT NULL, base_sha TEXT NOT NULL,
                report_sha256 TEXT NOT NULL, checked_epoch INTEGER NOT NULL
            )""")
            state.db.execute("""INSERT INTO scoped_qa_advisories
                (mission_id,candidate_sha,status,base_sha,report_sha256,checked_epoch)
                VALUES('a',?,'SCOPED_TESTS_PASS',?,?,123)""",
                (sha, "b" * 40, "c" * 64))
        with ControlPlane(self.path) as control:
            snap = control.snapshot()
            self.assertEqual(snap.scoped_qa_counts, {"SCOPED_TESTS_PASS": 1})
            self.assertEqual(snap.mission_counts, {"QA_PENDING": 1})
            self.assertEqual(control.scoped_qa_missions(limit=1)[0]["mission_id"], "a")
            with self.assertRaises(ValueError):
                control.scoped_qa_missions(limit=10000)
        with StateStore(self.path) as state:
            state.db.execute("UPDATE missions SET candidate_sha=? WHERE id='a'",
                             ("e" * 40,))
        with ControlPlane(self.path) as control:
            self.assertEqual(control.snapshot().scoped_qa_counts, {})
            self.assertEqual(control.scoped_qa_missions(), [])

    def test_qa_repair_numbers_are_from_persisted_attempts(self):
        with ControlPlane(self.path) as control:
            self.assertEqual(control.snapshot().repair_counts, {})
            control.store.db.execute("""CREATE TABLE qa_repair_attempts(
                mission_id TEXT NOT NULL, failed_sha TEXT NOT NULL,
                requeued_epoch INTEGER NOT NULL,
                PRIMARY KEY(mission_id,failed_sha))""")
            control.store.db.execute(
                "INSERT INTO qa_repair_attempts VALUES(?,?,?)",
                ("a", "c" * 40, 123))
            control.store.db.execute(
                "UPDATE missions SET state='BLOCKED' WHERE id='a'")
            counts = control.snapshot().repair_counts
            self.assertEqual(counts, {
                "attempts": 1, "missions": 1, "blocked": 1})

    def test_live_lease_is_reported_as_lease_only(self):
        with HoldingEngine(self.path, lambda m: SHA, online=lambda: False) as engine:
            engine.start()
            with ControlPlane(self.path) as control:
                self.assertTrue(control.snapshot().lease_valid)


if __name__ == "__main__":
    unittest.main()
