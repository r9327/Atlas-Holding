"""Exact, vendor-aware version evidence for trusted toolchain health checks.

Never use substring matching: a requested 1.2 must not accept 1.20, 11.2 or
1.2-beta. Git for Windows appends a release suffix to its exact base version.
"""
from __future__ import annotations

import re


def matches_component_version(component: str, pinned: str, output: str) -> bool:
    if (component not in {"git", "ollama", "opencode"}
            or not isinstance(pinned, str) or not isinstance(output, str)
            or not re.fullmatch(r"[0-9]+(?:[.][0-9]+){1,3}(?:-[A-Za-z0-9.]+)?",
                                pinned)
            or not output or len(output) > 8192):
        return False
    suffix = r"(?:[.]windows[.][0-9]+)?" if component == "git" else ""
    expression = r"(?<![0-9A-Za-z._+-])" + re.escape(pinned) + suffix + r"(?![0-9A-Za-z._+-])"
    return re.search(expression, output) is not None
