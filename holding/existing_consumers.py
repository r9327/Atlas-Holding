"""F10 bounded static IMPORT hints for existing Python modules at one Git SHA.

This augments the filename inventory, not the architecture/QA authority.
The trusted Git reader owns all subprocesses; no repository source is executed.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import re
from typing import Callable

_SHA = re.compile(r"^[0-9a-f]{40}$")
_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.py$")
_MAX_CANDIDATES = 6
_MAX_IMPORTERS = 24
_MAX_READS = 24
_MAX_SOURCE = 48 * 1024


@dataclass(frozen=True)
class StaticImportHint:
    module_path: str
    importer_path: str


@dataclass(frozen=True)
class ConsumerHints:
    repository_sha: str
    status: str
    candidates_checked: int
    python_files_read: int
    imports: tuple[StaticImportHint, ...]
    functionality_verified: bool = False
    consumers_exhaustive: bool = False
    can_skip_implementation: bool = False


def _module(path: str) -> str:
    name = path[:-3].replace("/", ".")
    if name.endswith(".__init__"):
        return name[:-9]
    return name


def _imports_module(tree: ast.AST, *, path: str, module: str) -> bool:
    own = _module(path)
    package = own if path.endswith("/__init__.py") else own.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == module for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                if node.level > len(parts):
                    continue
                prefix = ".".join(parts[:len(parts) - node.level + 1])
                base = ".".join(x for x in (prefix, node.module) if x)
            else:
                base = node.module or ""
            if base == module or any(
                base + "." + alias.name == module for alias in node.names
            ):
                return True
    return False


def inspect_static_import_consumers(
    git_read: Callable[..., str], *, repository_sha: str,
    candidate_paths: tuple[str, ...],
) -> ConsumerHints:
    """Return partial import edges without claiming runtime use or completeness.

    Limit grep to six candidate leaf names, inspect at most 24 Python files,
    skip oversized/invalid sources, and report unresolved Git read errors.
    The data is advisory even when a static import is observed.
    """
    if not isinstance(repository_sha, str) or not _SHA.fullmatch(repository_sha):
        raise ValueError("Exact Git SHA required")
    if not callable(git_read) or not isinstance(candidate_paths, tuple):
        raise ValueError("Trusted Git reader and immutable candidates required")
    if len(candidate_paths) > 12 or any(
        not isinstance(p, str) or len(p) > 240 or not _PATH.fullmatch(p)
        for p in candidate_paths
    ):
        raise ValueError("Invalid candidate path")
    candidates = tuple(dict.fromkeys(candidate_paths))[:_MAX_CANDIDATES]
    found: list[StaticImportHint] = []
    seen_pairs = set()
    reads = 0
    unresolved = False
    for path in candidates:
        name = path.rsplit("/", 1)[-1][:-3]
        if name == "__init__":
            unresolved = True  # package-level and dynamic users are not covered
            continue
        try:
            raw = git_read(
                "grep", "-l", "-F", "-e", name, repository_sha,
                "--", "*.py",
            )
        except (OSError, RuntimeError):
            # Git grep exit 1 (no match) is indistinguishable from read failure
            # in the existing trusted reader: do NOT claim a negative finding.
            unresolved = True
            continue
        if not isinstance(raw, str) or len(raw.encode("utf-8", "surrogateescape")) > 65536:
            unresolved = True
            continue
        paths = []
        for line in raw.splitlines():
            prefix = repository_sha + ":"
            if not line.startswith(prefix):
                unresolved = True
                continue
            importer = line[len(prefix):]
            if (not _PATH.fullmatch(importer) or len(importer) > 240
                    or importer == path or importer.startswith(("tests/", "test/"))):
                continue
            paths.append(importer)
        for importer in sorted(set(paths)):
            if reads >= _MAX_READS:
                unresolved = True
                break
            # Search results are indicative; inspect the actual exact-SHA AST.
            try:
                source = git_read("show", repository_sha + ":" + importer)
            except (OSError, RuntimeError):
                unresolved = True
                continue
            if (not isinstance(source, str) or
                    len(source.encode("utf-8", "surrogateescape")) > _MAX_SOURCE):
                unresolved = True
                continue
            reads += 1
            try:
                tree = ast.parse(source, filename=importer)
            except (SyntaxError, ValueError, UnicodeError, RecursionError):
                unresolved = True
                continue
            if _imports_module(tree, path=importer, module=_module(path)):
                pair = (path, importer)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    found.append(StaticImportHint(*pair))
                if len(found) >= _MAX_IMPORTERS:
                    unresolved = True
                    break
        if reads >= _MAX_READS or len(found) >= _MAX_IMPORTERS:
            break
    return ConsumerHints(
        repository_sha,
        "PARTIAL_STATIC_IMPORT_HINTS" if found else "NOT_VERIFIED",
        len(candidates), reads, tuple(found),
    )
