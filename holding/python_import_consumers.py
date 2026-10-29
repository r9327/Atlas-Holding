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
    # A package __init__.py is itself the package: "from . import child"
    # resolves against that package, not against its parent.
    parts = _module(importer).split(".")
    package = parts if importer.endswith("/__init__.py") else parts[:-1]
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
    # Candidate-sensitive preselection avoids spending the entire 24-file
    # AST budget on alphabetically early, unrelated same-folder sources.
    # This is one fixed read-only Git grep at the *same* exact commit.
    # It is ONLY a scheduling hint: every match is AST-checked below.
    highlighted: set[str] = set()
    if len(sources) > _MAX_FILES:
        leaves = sorted({
            p.rsplit("/", 1)[-1][:-3] for p in targets
            if not p.endswith("/__init__.py")
        })
        if leaves:
            args = ["grep", "-l", "-F"]
            for leaf in leaves:
                args.extend(("-e", leaf))
            try:
                raw = git_read(*args, repository_sha, "--", "*.py")
            except (OSError, RuntimeError):
                # No-match and unavailable Git grep cannot safely be
                # distinguished by the existing fixed Git runner. Fall back
                # to the old bounded source ordering, not a negative verdict.
                raw = ""
            if (isinstance(raw, str)
                    and len(raw.encode("utf-8", "surrogateescape")) <= 65536):
                prefix = repository_sha + ":"
                valid_sources = set(sources)
                highlighted = {
                    line[len(prefix):] for line in raw.splitlines()
                    if line.startswith(prefix)
                    and line[len(prefix):] in valid_sources
                }
    sources.sort(key=lambda p: (
        0 if p in highlighted else 1,
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
