"""Deterministic subset of Markdown roadmaps; no LLM guesswork or shell input.

Use a strict, auditable textual contract for human-edited task plans:
    Repository: r9327/Dof-Atlas
    ## mission-id
    Operation: code
    Depends on: prerequisite-a, prerequisite-b
    Priority: 5
    Acceptance:
    - Criterion A
    - Criterion B

Ambiguous documents must go to the dedicated planning/clarification role.
"""
from __future__ import annotations

import re

from .state_store import StoreError

_REPO_LINE = re.compile(r"^Repository:\s*(\S+)\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^##\s+([A-Za-z0-9][A-Za-z0-9_.-]{0,79})\s*$")
_KV = re.compile(r"^([A-Za-z_]+):\s*(.*?)\s*$")
_ALLOWED = frozenset({
    "operation", "depends_on", "priority", "external_required",
    "product_decision_required", "acceptance",
})


def parse_markdown_roadmap(raw: str) -> dict:
    """Convert explicit human text to canonical JSON-shaped task data.

    Never infer dependencies, operations, acceptance criteria, or approvals.
    The canonical importer validates DAG, identity, grants, and atomic storage.
    """
    if not isinstance(raw, str):
        raise StoreError("Markdown roadmap input must be UTF-8 text")
    repository = None
    missions: list[dict] = []
    current: dict | None = None
    fields: set[str] = set()
    in_acceptance = False
    title_seen = False
    for line_no, original in enumerate(raw.splitlines(), 1):
        if len(original) > 4096:
            raise StoreError("Markdown roadmap line exceeds 4096 characters")
        line = original.strip()
        if not line:
            continue
        if line.startswith("# ") and current is None and not title_seen:
            title_seen = True
            continue
        repo_line = _REPO_LINE.fullmatch(line)
        if repo_line is not None:
            if repository is not None or current is not None:
                raise StoreError("Repository must appear once before missions")
            repository = repo_line.group(1)
            continue
        heading = _HEADING.fullmatch(line)
        if heading is not None:
            if repository is None:
                raise StoreError("Markdown roadmap needs Repository first")
            current = {"id": heading.group(1), "acceptance": []}
            missions.append(current)
            if len(missions) > 10000:
                raise StoreError("Markdown roadmap exceeds mission limit")
            fields = set()
            in_acceptance = False
            continue
        if current is None:
            raise StoreError(f"Unknown roadmap heading or content at line {line_no}")
        if line.startswith("- "):
            if not in_acceptance:
                raise StoreError("Bullets are only permitted under Acceptance")
            criterion = line[2:].strip()
            if not criterion or len(criterion) > 1000:
                raise StoreError("Empty or oversized acceptance criterion")
            if len(current["acceptance"]) >= 128:
                raise StoreError("Too many acceptance criteria in one mission")
            current["acceptance"].append(criterion)
            continue
        match = _KV.fullmatch(line)
        if match is None:
            raise StoreError(f"Ambiguous Markdown roadmap syntax at line {line_no}")
        name, value = match.group(1).casefold(), match.group(2)
        if name not in _ALLOWED or name in fields:
            raise StoreError(f"Unknown or repeated Markdown field at line {line_no}")
        fields.add(name)
        if name == "acceptance":
            if value:
                raise StoreError("Acceptance criteria must use explicit bullets")
            in_acceptance = True
        elif name == "depends_on":
            parts = [x.strip() for x in value.split(",")] if value else []
            if any(not item for item in parts):
                raise StoreError("Dependencies must be explicit, without empty entries")
            current["depends_on"] = parts
            in_acceptance = False
        elif name == "priority":
            try:
                current["priority"] = int(value)
            except ValueError as exc:
                raise StoreError("Invalid Markdown task priority") from exc
            if str(current["priority"]) != value:
                raise StoreError("Priority must be a canonical decimal integer")
            in_acceptance = False
        elif name in ("external_required", "product_decision_required"):
            if value.casefold() not in ("true", "false"):
                raise StoreError("Flags must be explicitly true or false")
            current[name] = value.casefold() == "true"
            in_acceptance = False
        elif name == "operation":
            current[name] = value
            in_acceptance = False
    if repository is None or not missions:
        raise StoreError("Markdown roadmap must declare a repository and missions")
    for task in missions:
        if "operation" not in task or not task["acceptance"]:
            raise StoreError("Every Markdown mission needs operation and acceptance")
    return {"repo": repository, "missions": missions}
