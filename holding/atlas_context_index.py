"""Reproduce Dof-Atlas's committed AI Context index WITHOUT running repo code.

The canonical Dof-Atlas pre-commit hook calls tools.ai_context.sync, but an
autonomous code worker must never execute repository-supplied hooks. Mirror
only its small, frozen v2 Git-tree serialization using supervisor-owned Git.
This is metadata, not proof of source correctness or QA authorization.
"""
from __future__ import annotations

import json
import re

INDEX_PATH = ".ai/context_index.json"
_INDEX_EXCLUDED = frozenset({".ai"})
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_MODE = re.compile(r"[0-7]{6}\Z")
_SAFE_ENTRY = re.compile(r"[^\x00\r\n\t]+\Z")


class AtlasContextIndexError(ValueError):
    pass


def render_atlas_context_index(top_level_tree: str) -> str:
    """Exact stable JSON emitted by Dof-Atlas tools.ai_context v2.

    Input is the trusted Git ls-tree <staged_tree_sha> text, NEVER file
    contents or model-generated JSON. The .ai directory is excluded to avoid
    the self-referential hash of context_index.json.
    """
    if not isinstance(top_level_tree, str) or len(top_level_tree) > 4 * 1024 * 1024:
        raise AtlasContextIndexError("Invalid bounded Git top-level tree")
    entries: dict[str, dict[str, str]] = {}
    for line in top_level_tree.splitlines():
        if not line.strip():
            continue
        try:
            metadata, path = line.split("\t", 1)
            mode, kind, sha = metadata.split(" ", 2)
        except ValueError as exc:
            raise AtlasContextIndexError("Malformed Git tree entry") from exc
        if (not path or not _SAFE_ENTRY.fullmatch(path)
                or not _MODE.fullmatch(mode)
                or kind not in ("blob", "tree", "commit")
                or not _SHA40.fullmatch(sha) or path in entries):
            raise AtlasContextIndexError("Untrusted Git tree entry")
        if path in _INDEX_EXCLUDED:
            continue
        entries[path] = {"mode": mode, "type": kind, "sha": sha}
    return (json.dumps({
        "schema_version": 2,
        "source": "git-top-level-fingerprints",
        "excluded_top_level": sorted(_INDEX_EXCLUDED),
        "entries": entries,
    }, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
