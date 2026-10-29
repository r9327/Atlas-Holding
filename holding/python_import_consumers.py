"""F10: bounded, exact-SHA Python import references for shortlisted modules.

Import statements are only references, never a complete dynamic call graph.
This helper does not import or execute repository code and cannot approve edits.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import re
from typing import Callable

_SHA = re.compile(r"^[0-9a-f]{40}$")
_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_MAX_FILES = 24
_MAX_BYTES = 48 * 1024


class ImportConsumerError(ValueError):
    pass


@dataclass(frozen=True)
class PythonImportConsumers:
    importer_paths: dict[str, tuple[str, ...]]
    parsed_files: int
    status: str = "PARTIAL_AST_IMPORT_SCAN"
    certifies_completeness: bool = False


def _module(path: str) -> str:
    if path.endswith("/__init__.py"):
        return path[:-12].replace("/", ".")
    return path[:-3].replace("/", ".")


def _imports(tree: ast.AST, importer: str, target: str) -> bool:
    package = _module(importer).split(".")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == target for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level > len(package) + 1:
                    continue
                prefix = package[:len(package) - node.level + 1]
                base = ".".join(prefix + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if base == target or any(
                    (base + "." + alias.name if base else alias.name) == target
                    for alias in node.names if alias.name != "*"):
                return True
    return False


def inspect_python_import_consumers(
    git_read: Callable[..., str], *, repository_sha: str,
    source_paths: tuple[str, ...], candidate_paths: tuple[str, ...],
) -> PythonImportConsumers:
    """Find *some* importers of candidate modules in a bounded source sample.

    No-match means 'not found among parsed files', never 'no consumers'.
    The caller must supply file paths from a verified Git tree, not model data.
    """
    if (not isinstance(repository_sha, str) or not _SHA.fullmatch(repository_sha)
            or not callable(git_read)
            or not isinstance(source_paths, tuple)
            or not isinstance(candidate_paths, tuple)
            or len(source_paths) > 20_000 or len(candidate_paths) > 12
            or any(not isinstance(p, str) or not _PATH.fullmatch(p)
                   or not p.endswith(".py") for p in source_paths + candidate_paths)):
        raise ImportConsumerError("Invalid exact-SHA Python source inventory")
    targets = tuple(dict.fromkeys(candidate_paths))
    if not targets:
        return PythonImportConsumers({}, 0)
    if not set(targets).issubset(source_paths):
        raise ImportConsumerError("Candidate not contained in verified source tree")
    sources = sorted(set(source_paths) - set(targets))
    target_dirs = {p.rpartition("/")[0] for p in targets}
    sources.sort(key=lambda p: (
        0 if p.rpartition("/")[0] in target_dirs else 1,
        p,
    ))
    consumers: dict[str, list[str]] = {path: [] for path in targets}
    parsed = 0
    for path in sources[:_MAX_FILES]:
        try:
            source = git_read("show", f"{repository_sha}:{path}")
        except (RuntimeError, OSError) as exc:
            raise ImportConsumerError("Exact Git consumer source unreadable") from exc
        if not isinstance(source, str):
            raise ImportConsumerError("Invalid exact-SHA consumer source")
        if len(source.encode("utf-8", "surrogateescape")) > _MAX_BYTES:
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, UnicodeError, ValueError):
            continue
        parsed += 1
        for target in targets:
            if _imports(tree, path, _module(target)):
                if len(consumers[target]) < 12:
                    consumers[target].append(path)
    return PythonImportConsumers(
        {key: tuple(value) for key, value in consumers.items()},
        parsed,
    )
