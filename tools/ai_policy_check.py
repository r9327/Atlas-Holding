"""Fail-closed static governance checks; never imply model or native certification."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from holding.tool_registry import ToolRegistryError, validate_tool_registry

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "AGENTS.md", "AI_ENTRYPOINT.md", "ZERO_TRUST_RULES.md", "CLAUDE.md",
    ".github/copilot-instructions.md", ".ai/README.md", ".ai/task_profiles.json",
    ".github/pull_request_template.md",
    ".github/workflows/holding-pr-certification.yml",
    "docs/HOLDING_CERTIFICATION.md", "docs/TOOLCHAIN.md", "docs/LOCAL_MCP.md",
    "config/tool_registry.json",
    ".agents/skills/evidence-driven-validation/SKILL.md",
    ".agents/skills/plugin-trust/SKILL.md",
    ".agents/skills/architecture-impact/SKILL.md",
)
PROFILES = frozenset({"BALANCED_VERIFIED", "QUALITY_FIRST"})
TASKS = frozenset({
    "read_only", "tiny_edit", "localized_fix", "feature",
    "refactor", "structural", "certification",
})
RISKS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})


def verify(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for name in REQUIRED:
        path = root / name
        if not path.is_file() or not path.read_bytes().strip():
            errors.append(f"missing or empty contract: {name}")
    if errors:
        return errors

    try:
        validate_tool_registry((root / "config/tool_registry.json").read_bytes())
    except (OSError, ToolRegistryError) as exc:
        errors.append(f"invalid tool registry: {exc}")

    try:
        data = json.loads((root / ".ai/task_profiles.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        return errors + [f"invalid task profile JSON: {exc}"]
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        return errors + ["unsupported task profile schema"]
    if data.get("status") != "advisory_only":
        errors.append("task profiles cannot claim executable model routing")
    available = data.get("model_profiles")
    if not isinstance(available, list) or len(available) != len(PROFILES) or set(available) != PROFILES:
        errors.append("model profiles differ from holding/model_selection.py")
    if data.get("default_recommended_profile") not in PROFILES:
        errors.append("invalid default model profile")
    for field, authority in (
        ("selection_authority", "holding/model_selection.py"),
        ("tool_authority", "config/tool_registry.json"),
        ("certification_authority", "docs/HOLDING_CERTIFICATION.md"),
    ):
        if data.get(field) != authority:
            errors.append(f"invalid {field}")
    tasks = data.get("task_profiles")
    if not isinstance(tasks, dict) or set(tasks) != TASKS:
        errors.append("task categories missing or unexpected")
    else:
        for name, item in tasks.items():
            if (not isinstance(item, dict)
                    or set(item) != {"recommended_profile", "risk", "validation"}
                    or item.get("recommended_profile") not in PROFILES
                    or item.get("risk") not in RISKS
                    or not isinstance(item.get("validation"), str)
                    or not item["validation"].strip()):
                errors.append(f"invalid task: {name}")

    for name in REQUIRED:
        path = root / name
        if name.endswith("/SKILL.md"):
            content = path.read_text(encoding="utf-8")
            if (not content.startswith("---\nname: ")
                    or "\ndescription: " not in content
                    or "\n---\n" not in content):
                errors.append(f"invalid skill frontmatter: {name}")

    for name in ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md"):
        if "AI_ENTRYPOINT.md" not in (root / name).read_text(encoding="utf-8"):
            errors.append(f"missing agent entrypoint pointer: {name}")

    workflow = (root / ".github/workflows/holding-pr-certification.yml").read_text(
        encoding="utf-8")
    for fragment in (
        "ready_for_review", "synchronize", "ci:validate",
        "github.event.pull_request.head.sha", "fetch-depth: 0",
        "ubuntu-latest", "windows-latest", "test_*.py",
        "tools.architecture_guard", "Reject new static architecture debt\n        shell: bash",
        "Holding Certification / Required",
        "needs.ai-policy.result", "needs.cross-platform-tests.result",
    ):
        if fragment not in workflow:
            errors.append(f"certification workflow missing: {fragment}")
    return errors


def main() -> int:
    errors = verify()
    for error in errors:
        print(f"BLOCKED: {error}", file=sys.stderr)
    if errors:
        return 2
    print("PASS: static AI contracts, profiles and tool registry coherent.")
    print("Not a Doctor, Windows-native, plugin availability or release certification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
