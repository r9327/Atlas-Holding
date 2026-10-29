"""F02: read-only mission feasibility before any autonomous execution."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from holding.autonomy import Mission, StandingGrant
from holding.inventory import Component, Inventory
from holding.inventory_cli import main
from holding.mission_preflight import inspect_mission
from holding.state_store import StateStore, StoreError

REPO = "r9327/Dof-Atlas"
NOW = 1_900_000_000


class MissionPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite"
        self.store = StateStore(self.path)
        self.addCleanup(self.store.close)
        self.store.add_missions([Mission("code-1", REPO)])

    def inspect(self, mission="code-1", now=NOW):
        return inspect_mission(self.path, mission, now_epoch=now)

    def grant(self, *, repo=REPO, operation="code", expires=NOW + 100):
        self.store.grant_for_onboarding(
            StandingGrant(repo, frozenset({operation}), expires))

    def test_absent_grant_never_claims_ready(self):
        result = self.inspect()
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.permission, "MISSING_OR_EXPIRED")
        self.assertIn("NO_ACTIVE_SCOPED_GRANT", result.blockers)
        self.assertEqual(result.worker_validation, "NOT_RUN")
        self.assertEqual(result.model_benchmark, "NOT_RUN")
        self.assertFalse(result.executes_actions)

    def test_correct_grant_is_not_execution_or_model_proof(self):
        self.grant()
        result = self.inspect()
        self.assertEqual(result.status, "PRECHECK_PASSED")
        self.assertEqual(result.permission, "SCOPED_GRANT_PRESENT")
        self.assertEqual(result.model_benchmark, "NOT_RUN")
        self.assertEqual(result.worker_validation, "NOT_RUN")
        self.assertFalse(result.executes_actions)
        self.assertEqual(self.store.missions()[0].state, "READY")

    def test_expired_wrong_repo_and_wrong_operation_grants_fail_closed(self):
        for kwargs in (
            {"expires": NOW},
            {"repo": "r9327/Other"},
            {"operation": "push"},
        ):
            with self.subTest(kwargs=kwargs):
                self.store.db.execute("DELETE FROM grants")
                self.grant(**kwargs)
                self.assertEqual(self.inspect().permission, "MISSING_OR_EXPIRED")

    def test_dependency_requires_matching_proven_sha(self):
        self.store.add_missions([Mission("code-2", REPO, dependencies=("code-1",))])
        self.grant()
        waiting = self.inspect("code-2")
        self.assertEqual(waiting.status, "WAITING_FOR_DEPENDENCIES")
        self.assertEqual(waiting.waiting_for, ("code-1",))
        self.store.db.execute(
            "UPDATE missions SET state='DONE',candidate_sha='abc',evidence_sha='def' "
            "WHERE id='code-1'")
        self.assertEqual(self.inspect("code-2").waiting_for, ("code-1",))
        self.store.db.execute(
            "UPDATE missions SET evidence_sha='abc' WHERE id='code-1'")
        self.assertEqual(self.inspect("code-2").status, "PRECHECK_PASSED")

    def test_operator_stop_and_running_writer_prevent_preflight_pass(self):
        self.grant()
        self.store.db.execute("UPDATE settings SET value='1' WHERE key='paused'")
        self.assertIn("OPERATOR_SUSPENDED", self.inspect().blockers)
        self.store.db.execute("UPDATE settings SET value='0' WHERE key='paused'")
        self.store.add_missions([Mission("other", REPO)])
        self.store.db.execute("UPDATE missions SET state='RUNNING' WHERE id='other'")
        self.assertIn("OTHER_MISSION_RUNNING", self.inspect().blockers)
        self.assertEqual(self.inspect("other").status, "NOT_READY")

    def test_external_probe_is_not_faked(self):
        self.store.add_missions([Mission("sync-1", REPO, operation="push",
                                          external_required=True)])
        self.grant(operation="push")
        r = self.inspect("sync-1")
        self.assertEqual(r.status, "BLOCKED")
        self.assertIn("EXTERNAL_HEALTH_NOT_VERIFIED", r.blockers)
        self.assertEqual(r.network_verification, "NOT_RUN")

    def test_database_is_not_mutated_or_created(self):
        self.grant()
        before = self.store.db.total_changes
        old = self.store.db.execute("SELECT count(*) FROM events").fetchone()[0]
        self.inspect()
        self.assertEqual(self.store.db.total_changes, before)
        self.assertEqual(self.store.db.execute(
            "SELECT count(*) FROM events").fetchone()[0], old)
        missing = Path(self.temp.name) / "absent.sqlite"
        with self.assertRaises(StoreError):
            inspect_mission(missing, "code-1")
        self.assertFalse(missing.exists())

    def test_bad_identity_rejected_before_open(self):
        for mission in ("", "../escape", "x" * 90):
            with self.subTest(mission=mission), self.assertRaises(StoreError):
                self.inspect(mission)
        with self.assertRaises(StoreError):
            inspect_mission(Path("relative.sqlite"), "code-1")
        with self.assertRaises(StoreError):
            self.inspect(now=True)

    def test_cli_preserves_inventory_only_output_and_adds_mission_when_requested(self):
        self.grant()
        snapshot = Inventory(
            "Windows", "AMD64", 8, 16 * 1024**3, 10 * 1024**3,
            Component("FOUND_VALID"), Component("FOUND_VALID"),
            Component("FOUND_VALID"), Component("FOUND_VALID"), (),
            "", None, None,
        )
        with patch("holding.inventory_cli.scan", return_value=snapshot):
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    "--install-target", str(Path(self.temp.name)),
                    "--plan", "--state-db", str(self.path), "--mission-id", "code-1"
                ]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["mission_preflight"]["status"], "PRECHECK_PASSED")
            self.assertEqual(result["network_verification"], "NOT_RUN")
            self.assertFalse(result["executes_actions"])
            output = StringIO()
            with redirect_stdout(output):
                main(["--install-target", str(Path(self.temp.name))])
            self.assertNotIn("mission_preflight", json.loads(output.getvalue()))

    def test_cli_rejects_partial_flags_before_scanning(self):
        with patch("holding.inventory_cli.scan") as scanner:
            for flags in (
                ["--mission-id", "code-1"],
                ["--state-db", str(self.path)],
                ["--state-db", str(self.path), "--mission-id", "code-1"],
            ):
                with self.subTest(flags=flags), redirect_stderr(StringIO()), \
                     self.assertRaises(SystemExit):
                    main(["--install-target", str(Path(self.temp.name)), *flags])
            scanner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
