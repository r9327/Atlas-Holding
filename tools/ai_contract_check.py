"""Static Holding AI/tool/plugin policy consistency checks (not certification)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "AGENTS.md",
    "AI_ENTRYPOINT.md",
    "docs/AI_CERTIFICATION.md",
    "docs/TOOLCHAIN.md",
    "docs/QUALITY_RELEASE_GATES.md",
    "docs/QUALITY_EFFECTIVENESS_ACCEPTANCE.md",
    "docs/SCOPED_QA_RUNTIME.md",
    "config/tool_registry.json",
    ".github/pull_request_template.md",
    ".github/workflows/ai_contract_validation.yml",
)
ENTRYPOINT_REFERENCES = (
    "AGENTS.md",
    "docs/TOOLCHAIN.md",
    "docs/AI_CERTIFICATION.md",
    "docs/QUALITY_RELEASE_GATES.md",
    "docs/QUALITY_EFFECTIVENESS_ACCEPTANCE.md",
    "docs/SCOPED_QA_RUNTIME.md",
    "config/tool_registry.json",
)
CORE_TOOL_IDS = frozenset({"ai_context", "graphify", "doctor", "ollama", "opencode", "git"})
REMOTE_OPTIONALS = frozenset({"context7", "firecrawl"})


def audit(root: Path) -> list[str]:
    """Fail closed on missing contracts or unsafe registry drift; no side effects."""
    errors: list[str] = []
    for name in REQUIRED:
        if not (root / name).is_file():
            errors.append(f"Missing governance contract: {name}")
    entry = root / "AI_ENTRYPOINT.md"
    if entry.is_file():
        content = entry.read_text(encoding="utf-8")
        for name in ENTRYPOINT_REFERENCES:
            if f"`{name}`" not in content:
                errors.append(f"AI entrypoint lacks canonical link: {name}")
    registry_path = root / "config/tool_registry.json"
    if not registry_path.is_file():
        return errors
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        return [*errors, f"Invalid tool registry JSON: {exc}"]
    if not isinstance(registry, dict):
        return [*errors, "Tool registry must be a JSON object"]
    if registry.get("schema_version") != 1:
        errors.append("Unknown tool registry schema version")
    if registry.get("allow_paid_services") is not False:
        errors.append("Paid services must remain disabled by default")
    if registry.get("default_network") != "offline":
        errors.append("Network must default to offline")
    if registry.get("plugin_selection_policy") != (
        "capability_local_verified_first_then_cached_then_authorized_remote_then_native_fallback"
    ):
        errors.append("Local-first capability routing must remain explicit")
    hub = registry.get("local_mcp_hub")
    if not isinstance(hub, dict) or hub.get("permissions") != "default_read_only":
        errors.append("Local MCP hub must default to read-only")
    entries = registry.get("tools")
    if not isinstance(entries, list):
        return [*errors, "Tool registry tools must be an array"]
    ids: set[str] = set()
    for idx, tool in enumerate(entries):
        if not isinstance(tool, dict):
            errors.append(f"Invalid tool entry at index {idx}")
            continue
        tool_id = tool.get("id")
        if not isinstance(tool_id, str) or not tool_id:
            errors.append(f"Missing tool id at index {idx}")
            continue
        if tool_id in ids:
            errors.append(f"Duplicate tool id: {tool_id}")
        ids.add(tool_id)
        if not isinstance(tool.get("capabilities"), list) or not tool["capabilities"]:
            errors.append(f"Missing capabilities for {tool_id}")
        if tool.get("network") not in (True, False):
            errors.append(f"Missing explicit network boundary for {tool_id}")
        if (tool.get("network") is True and tool.get("required") is True
                and tool.get("installed_locally") is False):
            errors.append(f"Uninstalled network tool cannot be required: {tool_id}")
        if tool_id in REMOTE_OPTIONALS:
            if tool.get("network") is not True or tool.get("required") is not False:
                errors.append(f"External plugin must remain optional and network-gated: {tool_id}")
    for missing in sorted(CORE_TOOL_IDS - ids):
        errors.append(f"Missing core tool contract: {missing}")
    for missing in sorted(REMOTE_OPTIONALS - ids):
        errors.append(f"Missing optional external plugin contract: {missing}")
    return errors


if __name__ == "__main__":
    problems = audit(ROOT)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        raise SystemExit(1)
    print("PASS: static AI/tool/plugin governance contracts (not runtime certification)")
