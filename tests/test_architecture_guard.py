"""High confidence, baseline-aware architecture guard contract tests."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.architecture_guard import analyze, new_violations, sources


def big_function(name: str) -> str:
    lines = [f"def {name}(value):", "    result = []"]
    lines.extend(f"    result.append(value + {i})" for i in range(18))
    return "\n".join([*lines, "    return result", ""])


class GuardTests(unittest.TestCase):
    def test_existing_cycle_is_not_new_debt(self):
        existing = {"holding/a.py": "from . import b\n",
                    "holding/b.py": "from . import a\n"}
        self.assertEqual([], new_violations(existing, existing))

    def test_detect_new_import_cycle(self):
        before = {"holding/a.py": "from . import b\n",
                  "holding/b.py": "value = 2\n"}
        after = {**before, "holding/b.py": "from . import a\n"}
        self.assertTrue(any("NEW_IMPORT_CYCLE" in entry
                            for entry in new_violations(before, after)))

    def test_detect_new_substantial_duplicate(self):
        before = {"holding/a.py": big_function("calculate")}
        after = {**before, "holding/b.py": big_function("recalculate")}
        self.assertTrue(any("NEW_DUPLICATE_FUNCTION" in entry
                            for entry in new_violations(before, after)))

    def test_ignore_small_common_helpers(self):
        before = {"holding/a.py": "def go(x):\n    return x\n"}
        after = {**before, "holding/b.py": "def go(x):\n    return x\n"}
        self.assertEqual([], new_violations(before, after))

    def test_ui_direct_privileged_import_blocked(self):
        before = {"holding/control_center_ui.py": "value = 1\n",
                  "holding/executor.py": "value = 2\n"}
        after = {**before, "holding/control_center_ui.py":
                 "from .executor import AuthorizedGit\n"}
        self.assertTrue(any("UI_PRIVILEGE_BOUNDARY" in entry
                            for entry in new_violations(before, after)))

    def test_parallel_engine_suffix_blocked(self):
        before = {"holding/engine.py": "value = 1\n"}
        after = {**before, "holding/engine_v2.py": "value = 2\n"}
        self.assertTrue(any("PARALLEL_MODULE" in entry
                            for entry in new_violations(before, after)))
        self.assertEqual([], new_violations(after, after))

    def test_real_new_responsibility_accepted(self):
        before = {"holding/engine.py": "value = 1\n"}
        after = {**before, "holding/new_feature.py": "value = 2\n"}
        self.assertEqual([], new_violations(before, after))

    def test_scan_does_not_execute_source(self):
        code = "raise RuntimeError('source executed')\n"
        self.assertEqual(set(), analyze({"holding/safe.py": code})["cycles"])

    def test_git_reads_exact_base_and_preserves_worktree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for command in (
                ["git", "init", "-q"],
                ["git", "config", "user.name", "test"],
                ["git", "config", "user.email", "test@example.invalid"],
            ):
                subprocess.run(command, cwd=root, check=True)
            (root / "holding").mkdir()
            path = root / "holding/a.py"
            path.write_text("value = 1\n", encoding="utf-8")
            for command in (["git", "add", "."],
                            ["git", "commit", "-qm", "initial"]):
                subprocess.run(command, cwd=root, check=True)
            path.write_text("value = 2\n", encoding="utf-8")
            self.assertEqual("value = 1\n", sources(root, "HEAD")["holding/a.py"])
            self.assertEqual("value = 2\n", sources(root)["holding/a.py"])


if __name__ == "__main__":
    unittest.main()
