"""Operator CLI covers Markdown import, live replan, coverage and closure."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from holding.daemon_cli import main
from holding.state_store import StateStore


FIRST = """Repository: r9327/Dof-Atlas
## a
Operation: code
Acceptance:
- Pass first contract
"""
SECOND = """Repository: r9327/Dof-Atlas
## a
Operation: code
Acceptance:
- Pass first contract
## b
Operation: code
Depends_on: a
Acceptance:
- Pass second contract
"""


class OperatorRoadmapCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "holding.sqlite"
        self.path = self.root / "direction.md"

    def run_cli(self, action: str, *, expect=0):
        args = ["--db", str(self.db), action]
        if action in {"import", "replan"}:
            args.extend(["--roadmap-file", str(self.path),
                         "--source", "operator:direction"])
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(args)
        self.assertEqual(status, expect)
        return json.loads(output.getvalue())

    def test_markup_import_then_safe_replan_and_real_coverage(self):
        self.path.write_text(FIRST, encoding="utf-8")
        self.assertEqual(self.run_cli("import")["added"], ["a"])
        self.path.write_text(SECOND, encoding="utf-8")
        updated = self.run_cli("replan")
        self.assertEqual(updated["added"], ["b"])
        self.assertEqual(updated["preserved"], ["a"])
        self.assertTrue(updated["applied"])
        summary = self.run_cli("coverage")
        self.assertEqual(summary["requirements_total"], 2)
        self.assertEqual(summary["proven"], 0)
        self.assertFalse(summary["ready_to_close"])
        closure = self.run_cli("closure", expect=1)
        self.assertFalse(closure["ready"])
        self.assertEqual(closure["reason"], "ROADMAP_REQUIREMENTS_UNPROVEN")

    def test_replan_same_text_is_noop_after_restart(self):
        self.path.write_text(FIRST, encoding="utf-8")
        self.run_cli("import")
        self.path.write_text(SECOND, encoding="utf-8")
        self.run_cli("replan")
        same = self.run_cli("replan")
        self.assertFalse(same["applied"])
        self.assertEqual(same["added"], [])
        with StateStore(self.db) as state:
            self.assertEqual(len(state.missions()), 2)
            self.assertTrue(state.integrity_check())


if __name__ == "__main__":
    unittest.main()
