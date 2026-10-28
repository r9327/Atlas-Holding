"""Markdown roadmap is strict, versioned, atomic and not a permission source."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from holding.markdown_roadmap import parse_markdown_roadmap
from holding.roadmap_import import RoadmapImporter
from holding.state_store import StateStore, StoreError


EXAMPLE = """# Atlas Holding Roadmap
Repository: r9327/Dof-Atlas

## architecture
Operation: code
Priority: 10
Acceptance:
- Architecture source builds
- Existing tests remain passing

## interface
Operation: code
Depends_on: architecture
External_required: false
Acceptance:
- Control Center shows real state
"""


class MarkdownRoadmapTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = StateStore(Path(temp.name) / "state.sqlite")
        self.addCleanup(self.store.close)

    def test_markdown_import_into_real_sqlite_dag(self):
        raw = parse_markdown_roadmap(EXAMPLE)
        self.assertEqual(raw["repo"], "r9327/Dof-Atlas")
        self.assertEqual(raw["missions"][1]["depends_on"], ["architecture"])
        importer = RoadmapImporter(self.store)
        first = importer.import_text(EXAMPLE, source="roadmap-v3.md")
        self.assertEqual(first.added, ("architecture", "interface"))
        self.assertEqual({m.id: m.dependencies for m in self.store.missions()},
                         {"architecture": (), "interface": ("architecture",)})
        again = importer.import_text(EXAMPLE, source="roadmap-v3.md")
        self.assertEqual(again.version, first.version)
        self.assertEqual(again.added, ())

    def test_markdown_goal_is_preserved_as_immutable_mission_context(self):
        goal='Concevoir une interface simple et utile, sans changer la sécurité.'
        text=EXAMPLE.replace('Repository: r9327/Dof-Atlas',
                             'Repository: r9327/Dof-Atlas\\nObjectif: '+goal)
        # Use a real newline in the roadmap header.
        text=text.replace('\\nObjectif:', '\nObjectif:')
        result=parse_markdown_roadmap(text)
        self.assertEqual(result['project_goal'],goal)
        RoadmapImporter(self.store).import_text(text,source='goal-roadmap')
        specs=[row[0] for row in self.store.db.execute(
            'SELECT spec_json FROM mission_specs ORDER BY id')]
        self.assertEqual(len(specs),2)
        self.assertTrue(all(json.loads(item)['project_goal']==goal for item in specs))

    def test_markdown_rejects_late_or_duplicate_goal(self):
        for text in (
            EXAMPLE.replace('## architecture','Objectif: premier\\nObjectif: second\\n## architecture'),
            EXAMPLE.replace('## interface','Objectif: tardif\\n## interface'),
            EXAMPLE.replace('## architecture','Objectif: '+'X'*4097+'\\n## architecture'),
        ):
            text=text.replace('\\n','\n')
            with self.subTest(text=text[:80]),self.assertRaises(StoreError):
                RoadmapImporter(self.store).import_text(text,source='invalid-goal')
            self.assertFalse(self.store.missions())

    def test_fenced_json_roadmaps_still_supported(self):
        text = ('```json\n'
                '{"repo":"r9327/Dof-Atlas","missions":'
                '[{"id":"one","operation":"code","acceptance":["test"]}]}'
                '\n```')
        result = RoadmapImporter(self.store).import_text(text, source="copy")
        self.assertEqual(result.added, ("one",))

    def test_bad_fields_and_ambiguity_fail_without_database_changes(self):
        variants = [
            EXAMPLE.replace("Operation: code", "Action: shell", 1),
            EXAMPLE.replace("Priority: 10", "Priority: ten"),
            EXAMPLE.replace("## interface", "### interface"),
            EXAMPLE.replace("Acceptance:\n- Architecture source builds",
                            "Acceptance: guaranteed PASS\n- Architecture source builds"),
            EXAMPLE.replace("Repository: r9327/Dof-Atlas",
                            "Repository: r9327/Dof-Atlas\nRepository: different/repo"),
            EXAMPLE.replace("Depends_on: architecture", "Depends_on: missing"),
            EXAMPLE.replace("Depends_on: architecture", "Depends_on: architecture,"),
            EXAMPLE.replace("Operation: code", "Operation: disable_ci", 1),
            EXAMPLE.replace("External_required: false", "External_required: maybe"),
        ]
        for index, text in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(StoreError):
                RoadmapImporter(self.store).import_text(text, source="failing-test")
            self.assertEqual(self.store.missions(), ())

    def test_dependency_cycle_fails_closed(self):
        a = EXAMPLE.replace("Priority: 10", "Depends_on: interface")
        with self.assertRaisesRegex(StoreError, "cycle"):
            RoadmapImporter(self.store).import_text(a, source="cycle")
        self.assertEqual(self.store.missions(), ())

    def test_missing_acceptance_is_never_inferred(self):
        raw = "Repository: r9327/Dof-Atlas\n## x\nOperation: code"
        with self.assertRaises(StoreError):
            parse_markdown_roadmap(raw)

    def test_markdown_size_bounded_before_parsing(self):
        text = EXAMPLE + "\n" + "X" * (2 * 1024 * 1024)
        with self.assertRaises(StoreError):
            RoadmapImporter(self.store).import_text(text, source="huge")


if __name__ == "__main__":
    unittest.main()
