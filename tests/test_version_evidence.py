"""Exact component version comparison regression tests."""
from __future__ import annotations
import unittest
from holding.version_evidence import matches_component_version

class VersionEvidenceTests(unittest.TestCase):
    def test_git_suffix(self):
        self.assertTrue(matches_component_version("git", "2.55.0", "git version 2.55.0.windows.1"))
        self.assertTrue(matches_component_version("git", "2.55.0", "git version 2.55.0"))
        for version in ("2.55.01.windows.1", "2.55.0.1", "12.55.0"):
            with self.subTest(version=version):
                self.assertFalse(matches_component_version("git", "2.55.0", "git version " + version))

    def test_model_toolchain_versions(self):
        for component in ("ollama", "opencode"):
            self.assertTrue(matches_component_version(component, "1.2.3", component + " version 1.2.3"))
            for version in ("11.2.3", "1.2.30", "1.2.3-alpha"):
                self.assertFalse(matches_component_version(component, "1.2.3", component + " version " + version))

if __name__ == "__main__":
    unittest.main()
