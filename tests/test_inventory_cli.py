"""The installer can query an inventory without modifying the target."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding import inventory as inv
from holding.inventory_cli import main


class InventoryCliTests(unittest.TestCase):
    def test_json_preflight_exposes_real_statuses(self):
        snapshot = inv.Inventory(
            os_name="Windows", os_architecture="AMD64", cpu_threads=16,
            ram_total_bytes=32 * 1024**3, installation_disk_free_bytes=100 * 1024**3,
            git=inv.Component("FOUND_VALID", "C:/Git/git.exe", "git version 2"),
            python=inv.Component("FOUND_VALID", "C:/Holding/python.exe", "3.13"),
            ollama=inv.Component("NOT_FOUND"), opencode=inv.Component("NOT_FOUND"),
            models=(), gpu="RTX 3060 Ti", vram_total_bytes=8 * 1024**3,
            vram_free_bytes=6 * 1024**3,
        )
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "next-volume"
            output = StringIO()
            with patch("holding.inventory_cli.scan", return_value=snapshot) as detector:
                with redirect_stdout(output):
                    self.assertEqual(main(["--install-target", str(target)]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["ollama"]["status"], "NOT_FOUND")
        self.assertEqual(result["gpu"], "RTX 3060 Ti")
        self.assertEqual(result["models"], [])
        detector.assert_called_once_with(target)

    def test_relative_path_rejected_before_probe(self):
        with patch("holding.inventory_cli.scan") as detector:
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main(["--install-target", "relative/path"])
            detector.assert_not_called()


if __name__ == "__main__":
    unittest.main()
