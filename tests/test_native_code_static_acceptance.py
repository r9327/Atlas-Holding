"""Independent non-executing acceptance rules for live model and CI stub.

The Python AST must match exactly. Never eval/import/execute generated source.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

# unittest discover -s tests adds this folder to sys.path, but direct execution
# must also import the exact checked-out native mission helper.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_windows_code_mission_smoke import _check_answer


class NativeCodeStaticAcceptanceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="atlas-static-acceptance-")
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name).resolve() / "feature.py"

    def source(self, code: str) -> None:
        self.path.write_text(code, encoding="utf-8")

    def test_exclusively_one_answer_function_with_comments_is_accepted(self):
        for code in (
            "def answer():\n    return 42\n",
            "# harmless comment\n\n\ndef answer():\n    return 42  # okay\n",
            "def answer():\n\n    return (42)\n",
        ):
            with self.subTest(code=code):
                self.source(code)
                _check_answer(self.path)

    def test_hidden_import_time_execution_never_passes_static_qa(self):
        payloads = (
            "@__import__('os').system('echo unsafe')\ndef answer():\n    return 42\n",
            "def answer(*, dangerous=__import__('os').system('echo unsafe')):\n    return 42\n",
            "def answer(x=__import__('os').system('echo unsafe')):\n    return 42\n",
            "def answer() -> __import__('os').system('echo unsafe'):\n    return 42\n",
            "def answer(x: __import__('os').system('echo unsafe') = 1):\n    return 42\n",
            "from os import system\ndef answer():\n    return 42\n",
            "def answer():\n    return 42\n__import__('os').system('echo unsafe')\n",
            "async def answer():\n    return 42\n",
            "def answer(*args):\n    return 42\n",
            "def answer(**kwargs):\n    return 42\n",
            "def answer(*, x=1):\n    return 42\n",
            "def answer(x, /):\n    return 42\n",
            "def answer():\n    pass\n    return 42\n",
            "def answer():\n    return 42.0\n",
            "def answer():\n    return True\n",
            "def answer():\n    return 42\n\ndef answer():\n    return 42\n",
        )
        for code in payloads:
            with self.subTest(code=code):
                self.source(code)
                with self.assertRaisesRegex(RuntimeError, "static acceptance"):
                    _check_answer(self.path)

    def test_malformed_oversized_and_nonregular_source_is_rejected(self):
        for code in ("def answer(:\n", "def answer():\n    return 42\n" + "x" * 8193):
            with self.subTest(code=code[:50]):
                self.source(code)
                with self.assertRaises(RuntimeError):
                    _check_answer(self.path)
        self.path.write_bytes(b"\xff\xfe\xfd")
        with self.assertRaises(RuntimeError):
            _check_answer(self.path)
        self.path.unlink()
        with self.assertRaises(RuntimeError):
            _check_answer(self.path)


if __name__ == "__main__":
    unittest.main()
