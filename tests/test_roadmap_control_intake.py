"""End-to-end local roadmap intake through the operator CLI and control plane."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from holding.control import ControlPlane
from holding.daemon_cli import main
from holding.state_store import StateStore, StoreError

REPO = "r9327/Dof-Atlas"


def payload(missions):
    return json.dumps({"repo": REPO, "missions": missions})


def mission(name, dependencies=()):
    return {"id": name, "operation": "code", "acceptance": ["Test passes"],
            "depends_on": list(dependencies)}


class RoadmapControlIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "holding.sqlite"
        self.file = self.root / "roadmap.json"

    def invoke(self, *, source=None):
        argv = ["--db", str(self.db), "import", "--roadmap-file", str(self.file)]
        if source is not None:
            argv.extend(["--source", source])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(argv), 0)
        return json.loads(output.getvalue())

    def test_cli_import_persists_dag_and_provenance_without_grants(self):
        self.file.write_text(payload([mission("first"), mission("second", ["first"]),
                                      mission("other")]), encoding="utf-8")
        result = self.invoke(source="user:roadmap-v1")
        self.assertEqual(result["added"], ["first", "second", "other"])
        self.assertEqual(len(result["digest"]), 64)
        with StateStore(self.db) as db:
            self.assertEqual({m.id for m in db.missions()}, {"first", "second", "other"})
            self.assertEqual(db.db.execute("SELECT count(*) FROM grants").fetchone()[0], 0)
            stored = db.db.execute("SELECT source FROM roadmap_versions").fetchone()[0]
            self.assertEqual(stored, "user:roadmap-v1")

    def test_optional_global_goal_is_stored_but_never_grants_permissions(self):
        goal='Permettre une expérience fiable et accessible sans perdre les données.'
        document=json.loads(payload([mission('a'),mission('b', ['a'])]))
        document['project_goal']=goal
        self.file.write_text(json.dumps(document),encoding='utf-8')
        self.assertEqual(self.invoke()['added'],['a','b'])
        with StateStore(self.db) as db:
            specs=[json.loads(row[0]) for row in db.db.execute(
                'SELECT spec_json FROM mission_specs ORDER BY id')]
            self.assertTrue(all(item['project_goal']==goal for item in specs))
            self.assertEqual(db.db.execute('SELECT count(*) FROM grants').fetchone()[0],0)

    def test_invalid_project_goal_never_partially_imports(self):
        for goal in ('', ' ', 'a'*4097, [], None):
            data=json.loads(payload([mission('a')]))
            data['project_goal']=goal
            self.file.write_text(json.dumps(data),encoding='utf-8')
            with self.subTest(goal=str(goal)[:12]):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    self.invoke()
                with StateStore(self.db) as db:
                    self.assertEqual(db.missions(), ())

    def test_idempotent_reimport_and_new_dependent_work(self):
        self.file.write_text(payload([mission("a")]), encoding="utf-8")
        first = self.invoke()
        second = self.invoke()
        self.assertEqual(first["version"], second["version"])
        self.assertEqual(second["added"], [])
        self.assertEqual(second["preserved"], ["a"])
        self.file.write_text(payload([mission("a"), mission("b", ["a"])]), encoding="utf-8")
        third = self.invoke()
        self.assertEqual(third["added"], ["b"])
        self.assertEqual(third["preserved"], ["a"])

    def test_conflicting_revision_is_atomic(self):
        self.file.write_text(payload([mission("a")]), encoding="utf-8")
        self.invoke()
        bad = mission("a")
        bad["acceptance"] = ["Different acceptance"]
        self.file.write_text(payload([bad, mission("b")]), encoding="utf-8")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
            self.invoke()
        self.assertEqual(failure.exception.code, 2)
        with StateStore(self.db) as db:
            self.assertEqual([m.id for m in db.missions()], ["a"])
            self.assertEqual(db.db.execute("SELECT COUNT(*) FROM roadmap_versions").fetchone()[0], 1)

    def test_malformed_operation_fails_closed_without_partial_import(self):
        for invalid_operation in ([], {"operation": "code"}):
            task = mission("x")
            task["operation"] = invalid_operation
            self.file.write_text(payload([task]), encoding="utf-8")
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
                self.invoke()
            self.assertEqual(failure.exception.code, 2)
        with StateStore(self.db) as db:
            self.assertEqual(db.missions(), ())

    def test_file_bounds_and_invalid_utf8_refused(self):
        self.file.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke()
        self.file.write_bytes(b"\xff")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke()

    def test_control_plane_import_does_not_override_pause(self):
        with ControlPlane(self.db) as control:
            control.pause()
            imported = control.import_roadmap(payload([mission("a")]), source="ui:roadmap-1")
            self.assertEqual(imported.added, ("a",))
            self.assertTrue(control.snapshot().paused)
            with self.assertRaises(StoreError):
                control.import_roadmap(payload([mission("b", ["nonexistent"])]),
                                       source="ui:invalid")
            self.assertEqual(control.snapshot().mission_counts, {"READY": 1})


if __name__ == "__main__":
    unittest.main()
