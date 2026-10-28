"""Focused, dependency-free checks for Atlas Holding AI/tool governance.

This is a scoped contract check, NOT product validation or release certification.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (
    "AGENTS.md",
    "AI_ENTRYPOINT.md",
    "ZERO_TRUST_RULES.md",
    "docs/TOOLCHAIN.md",
    "docs/QUALITY_RELEASE_GATES.md",
    ".agents/skills/holding-governance/SKILL.md",
    ".github/copilot-instructions.md",
    ".github/pull_request_template.md",
    ".github/workflows/foundation_policy_validation.yml",
    "config/tool_registry.json",
)

def check(root: Path) -> list[str]:
    errors: list[str] = []
    for path in DOCUMENTS:
        if not (root / path).is_file():
            errors.append(f"missing: {path}")
    if errors:
        return errors

    sys.path.insert(0, str(root))
    try:
        from holding.tool_registry import validate_tool_registry, ToolRegistryError
        raw = (root / "config/tool_registry.json").read_bytes()
        try:
            registry = validate_tool_registry(raw)
        except (ToolRegistryError, ValueError) as exc:
            errors.append(f"unsafe tool registry: {exc}")
        else:
            owners = dict(registry.capability_owner)
            for capability, owner in (
                ("context.route", "ai_context"),
                ("architecture.graph", "graphify"),
                ("quality.validate", "doctor"),
                ("docs.lookup", "context7"),
                ("web.search", "firecrawl"),
                ("repo.sync", "github"),
            ):
                if owners.get(capability) != owner:
                    errors.append(f"capability routing drift: {capability}")
            if registry.paid_services_allowed or registry.executable_hub_ready:
                errors.append("unverified paid services or executable MCP hub")
    finally:
        sys.path.remove(str(root))

    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    if "AI_ENTRYPOINT.md" not in agents or "ZERO_TRUST_RULES.md" not in agents:
        errors.append("AGENTS.md must link canonical entrypoint and zero trust")
    entry = (root / "AI_ENTRYPOINT.md").read_text(encoding="utf-8")
    for anchor in ("AGENTS.md", "docs/QUALITY_RELEASE_GATES.md",
                   "config/tool_registry.json", "NOT_RUN", "CERTIFIED"):
        if anchor not in entry:
            errors.append(f"entrypoint missing: {anchor}")
    workflow = (root / ".github/workflows/foundation_policy_validation.yml").read_text(encoding="utf-8")
    for anchor in ("ready_for_review", "labeled",
                   "github.event.pull_request.head.sha", "persist-credentials: false",
                   "python -B -X utf8 -m unittest", "test_*.py",
                   "windows-latest", "ubuntu-latest"):
        if anchor not in workflow:
            errors.append(f"workflow missing: {anchor}")
    return errors

def main() -> int:
    errors = check(ROOT)
    if errors:
        for error in errors:
            print(f"AI_GOVERNANCE_FAIL: {error}", file=sys.stderr)
        return 1
    print("AI_GOVERNANCE_PASS: scoped rules and tool routing only; NOT_CERTIFIED")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
