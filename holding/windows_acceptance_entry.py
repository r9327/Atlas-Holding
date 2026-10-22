"""NONRELEASE standalone Windows acceptance runner.

The exact checked-out, test-owned code mission uses an isolated temporary Git
fixture only. This executable is NOT the production Holding agent or installer.
No Python distribution is required on the operator's Windows machine.
"""
from __future__ import annotations

import ast

from tests.native_windows_code_mission_smoke import main as run_mission


def main(argv: list[str] | None = None) -> int:
    import sys
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--self-test"]:
        from tests.native_windows_code_mission_smoke import _EXACT_ANSWER_AST
        expected = ast.dump(
            ast.parse("def answer():\n    return 42\n"),
            include_attributes=False,
        )
        if _EXACT_ANSWER_AST != expected:
            return 2
        print("NONRELEASE_ACCEPTANCE_RUNTIME_SELF_TEST_PASS")
        return 0
    if "--self-test" in arguments:
        return 2
    return run_mission(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
