"""F10: bounded, exact-commit inventory of existing code before generation.

Only supervisor-owned Git reads are allowed. A filename or Python symbol is
evidence of *presence*, not functional readiness, CI, PR status or ownership
approval. No repository code is imported or executed.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import re
import unicodedata
from typing import Callable

_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_PRIVATE = ("secret", "token", "password", "credential", "private", "apikey",
            "api_key", ".env", "id_rsa", "id_ed25519")
_EXT = (".py", ".md", ".toml", ".js", ".ts", ".tsx", ".json", ".yaml", ".yml")
_STOP = frozenset({
    "faire", "avec", "sans", "pour", "dans", "cette", "cela", "that", "from",
    "when", "must", "should", "then", "will", "where", "have", "code", "test",
    "tests", "make", "create", "added", "fonction", "existing",
})


class ExistingCodeError(ValueError):
    pass


@dataclass(frozen=True)
class ExistingCandidate:
    path: str
    symbols: tuple[str, ...]
    source: str = "GIT_COMMIT_PATH"


@dataclass(frozen=True)
class ExistingCodeReport:
    repository_sha: str
    state: str
    candidates: tuple[ExistingCandidate, ...]
    checked_paths: int
    scanned_python_files: int
    consumers_status: str = "NOT_VERIFIED"
    github_pr_status: str = "NOT_CHECKED"
    functionality_status: str = "NOT_VERIFIED"
    recommended_action: str = "REVIEW_AND_REUSE_OR_ADAPT"
    can_skip_implementation: bool = False


def _terms(criteria: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(criteria, tuple) or not 1 <= len(criteria) <= 30:
        raise ExistingCodeError("Bounded acceptance criteria required")
    tokens: set[str] = set()
    for line in criteria:
        if not isinstance(line, str) or not 1 <= len(line) <= 1200:
            raise ExistingCodeError("Invalid acceptance criterion")
        flat = unicodedata.normalize("NFKD", line.casefold())
        folded = "".join(x for x in flat if not unicodedata.combining(x))
        tokens.update(w for w in re.findall(r"[a-z][a-z0-9_]{3,}", folded)
                      if w not in _STOP)
    # Large free-text objectives must not create a large Git scan or model input.
    return tuple(sorted(tokens, key=lambda w: (-len(w), w))[:24])


def _candidate_path(path: str) -> bool:
    if not _SAFE.fullmatch(path) or len(path) > 240:
        return False
    parts = path.casefold().split("/")
    return (len(parts) <= 12
            and path.casefold().endswith(_EXT)
            and not any(part.startswith(".") or part in {
                "node_modules", "venv", "build", "dist", "__pycache__",
                ".github", "tests", ".git"} for part in parts)
            and not any(private in part for part in parts for private in _PRIVATE))


def inspect_existing_code(
    git_read: Callable[..., str], *, repository_sha: str,
    acceptance: tuple[str, ...],
) -> ExistingCodeReport:
    """Inspect a verified commit through the trusted GitWorktrees._git reader.

    The caller MUST verify checkout origin/cleanliness and HEAD == sha. This
    pure discovery function will never infer that an existing feature works.
    Missing Git evidence is an error, not an empty inventory.
    """
    if not _SHA.fullmatch(repository_sha):
        raise ExistingCodeError("Exact repository SHA required")
    if not callable(git_read):
        raise ExistingCodeError("Trusted Git reader required")
    tokens = _terms(acceptance)
    try:
        raw = git_read("ls-tree", "-r", "-z", repository_sha)
    except (OSError, RuntimeError) as exc:
        raise ExistingCodeError("Git inventory unavailable") from exc
    if not isinstance(raw, str) or len(raw.encode("utf-8", "surrogateescape")) > 4 * 1024 * 1024:
        raise ExistingCodeError("Git path inventory exceeds bound")
    records = raw.split("\\0")
    if records[-1] == "":
        records.pop()
    if len(records) > 20_000:
        raise ExistingCodeError("Unbounded Git inventory")
    names: list[str] = []
    seen = set()
    for record in records:
        try:
            header, path = record.split("\\t", 1)
            mode, kind, obj = header.split(" ")
        except ValueError as exc:
            raise ExistingCodeError("Malformed Git tree entry") from exc
        if (not re.fullmatch(r"[0-9a-f]{40}", obj)
                or path in seen or not path
                or "\\n" in path or "\\r" in path):
            raise ExistingCodeError("Unsafe or duplicate Git tree entry")
        seen.add(path)
        # Symlinks and submodule commits are never source candidates.
        if mode in {"100644", "100755"} and kind == "blob":
            names.append(path)
    matches = []
    for path in names:
        if not _candidate_path(path):
            continue
        normalized = path.casefold()
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].casefold()
        score = sum(4 if token in stem else 2 if token in normalized else 0
                    for token in tokens)
        if score > 0:
            matches.append((-score, path))
    # A bounded, deterministic shortlist, not an exhaustive semantic search.
    matches.sort()
    results: list[ExistingCandidate] = []
    inspected_py = 0
    for _, path in matches[:12]:
        symbols: list[str] = []
        if path.endswith(".py"):
            try:
                source = git_read("show", f"{repository_sha}:{path}")
            except (OSError, RuntimeError) as exc:
                raise ExistingCodeError("Cannot inspect candidate at exact SHA") from exc
            if not isinstance(source, str):
                raise ExistingCodeError("Invalid Python source from Git")
            if len(source.encode("utf-8", "surrogateescape")) <= 48 * 1024:
                try:
                    tree = ast.parse(source)
                except (SyntaxError, ValueError, UnicodeError):
                    pass
                else:
                    inspected_py += 1
                    symbols = [
                        node.name for node in tree.body
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                             ast.ClassDef))
                        and not node.name.startswith("_")
                    ][:12]
        results.append(ExistingCandidate(path, tuple(symbols)))
    return ExistingCodeReport(
        repository_sha,
        "CANDIDATES_FOUND" if results else "NO_PATH_MATCH_IN_BOUNDED_SCAN",
        tuple(results), len(records), inspected_py,
    )
