"""Static, fail-closed governance preflight. This does not certify runtime behavior."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

from holding.tool_registry import ToolRegistryError, validate_tool_registry

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = (
    "AGENTS.md", "AI_ENTRYPOINT.md", "AI_CONTEXT.md",
    "HOLDING_CERTIFICATION.md", "docs/PLUGIN_GOVERNANCE.md",
    "docs/TOOLCHAIN.md", "docs/QUALITY_RELEASE_GATES.md",
)
OWNERS = {
    "context.route": "ai_context",
    "architecture.graph": "graphify",
    "quality.plan": "doctor",
    "quality.check": "doctor",
    "quality.validate": "doctor",
    "llm.generate": "ollama",
    "code.edit": "opencode",
    "repo.commit": "git",
    "repo.sync": "github",
}


class GovernanceError(ValueError):
    """A mandatory static contract is absent or inconsistent."""


def inspect(root: Path, expected_sha: str | None = None) -> dict[str, object]:
    missing = [name for name in CONTRACTS if not (root / name).is_file()]
    if missing:
        raise GovernanceError("Missing canonical contracts: " + ", ".join(missing))

    path = root / "config" / "tool_registry.json"
    try:
        catalogue = validate_tool_registry(path.read_bytes())
    except (OSError, ToolRegistryError) as exc:
        raise GovernanceError(f"Invalid tool registry: {exc}") from exc
    actual_owners = dict(catalogue.capability_owner)
    differences = {
        capability: {"expected": owner, "actual": actual_owners.get(capability)}
        for capability, owner in OWNERS.items()
        if actual_owners.get(capability) != owner
    }
    if differences:
        raise GovernanceError("Missing or redirected core capabilities: " + json.dumps(differences, sort_keys=True))

    actual_sha = None
    if expected_sha is not None:
        if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
            raise GovernanceError("Expected SHA must be a full lowercase Git commit SHA")
        try:
            actual_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                text=True, encoding="utf-8", capture_output=True, timeout=20,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise GovernanceError("Unable to verify exact Git candidate") from exc
        if actual_sha != expected_sha:
            raise GovernanceError(f"Candidate SHA mismatch: expected {expected_sha}, got {actual_sha}")
    return {
        "status": "PASS", "scope": "STATIC_POLICY_ONLY",
        "candidate_sha": actual_sha, "contracts_checked": len(CONTRACTS),
        "capabilities_checked": len(OWNERS),
        "plugins_installed_verified": False,
        "windows_runtime_certified": False,
        "phase_certified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", help="Exact Git HEAD, mandatory for CI")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable result")
    args = parser.parse_args()
    try:
        report = inspect(ROOT, args.expected_sha)
    except GovernanceError as exc:
        report = {"status": "FAIL", "scope": "STATIC_POLICY_ONLY", "reason": str(exc)}
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"{report['status']}: {report.get('reason', 'static governance only')}")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
