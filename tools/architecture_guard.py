"""Fail-closed static architecture regression check against a Git base."""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from itertools import combinations
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVILEGED = {
    "holding.daemon_lease", "holding.engine", "holding.executor",
    "holding.git_push", "holding.git_worktree",
    "holding.github_privileged_transport", "holding.state_store",
    "holding.windows_secret_acl",
}
PARALLEL = re.compile(r"_(?:v[2-9]\d*|legacy|old|new|copy|backup)\.py$")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True,
        text=True, encoding="utf-8", timeout=30,
    ).stdout


def sources(root: Path, base: str | None = None) -> dict[str, str]:
    if base is None:
        return {
            path.relative_to(root).as_posix(): path.read_text(encoding="utf-8-sig")
            for path in sorted((root / "holding").rglob("*.py"))
        }
    names = git(root, "ls-tree", "-r", "--name-only", base, "--", "holding").splitlines()
    return {
        path: git(root, "show", base + ":" + path)
        for path in names if path.startswith("holding/") and path.endswith(".py")
    }


def module_of(path: str) -> str:
    parts = list(Path(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def edges_for(tree: ast.AST, current: str, known: set[str]) -> set[str]:
    dependencies: set[str] = set()
    parent = current if current == "holding" else current.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            choices = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parents = parent.split(".")
                if node.level > len(parents):
                    continue
                prefix = ".".join(parents[:len(parents) - node.level + 1])
                target = ".".join(part for part in (prefix, node.module) if part)
            else:
                target = node.module or ""
            choices = [target] + [target + "." + item.name for item in node.names]
        else:
            continue
        dependencies.update(item for item in choices if item in known and item != current)
    return dependencies


def cycles(graph: dict[str, set[str]]) -> set[tuple[str, ...]]:
    counter = 0
    stack: list[str] = []
    active: set[str] = set()
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    found: set[tuple[str, ...]] = set()

    def visit(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        active.add(node)
        for dep in sorted(graph[node]):
            if dep not in index:
                visit(dep)
                low[node] = min(low[node], low[dep])
            elif dep in active:
                low[node] = min(low[node], index[dep])
        if low[node] == index[node]:
            component = []
            while True:
                last = stack.pop()
                active.remove(last)
                component.append(last)
                if last == node:
                    break
            if len(component) > 1:
                found.add(tuple(sorted(component)))

    for module in sorted(graph):
        if module not in index:
            visit(module)
    return found


def analyze(files: dict[str, str]) -> dict[str, set]:
    trees = {
        module_of(path): ast.parse(source, filename=path)
        for path, source in files.items()
        if path.startswith("holding/") and path.endswith(".py")
    }
    known = set(trees)
    graph = {name: edges_for(tree, name, known) for name, tree in trees.items()}
    identical: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for module, tree in trees.items():
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Constant
            ) and isinstance(body[0].value.value, str):
                body = body[1:]
            if not body or node.end_lineno - node.lineno < 14:
                continue
            if sum(1 for part in body for _ in ast.walk(part)) < 65:
                continue
            key = ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False)
            identical[key].append((module, node.name))
    duplicate_pairs = {
        tuple(sorted((a, b)))
        for matches in identical.values()
        for a, b in combinations(matches, 2)
        if a[0] != b[0]
    }
    ui_edges = {
        (module, dep) for module, imports in graph.items()
        if module == "holding.control_center_ui"
        for dep in imports if dep in PRIVILEGED
    }
    return {"cycles": cycles(graph), "duplicates": duplicate_pairs, "ui": ui_edges}


def new_violations(before: dict[str, str], after: dict[str, str]) -> list[str]:
    old, new = analyze(before), analyze(after)
    findings = []
    for category, label in (
        ("cycles", "NEW_IMPORT_CYCLE"),
        ("duplicates", "NEW_DUPLICATE_FUNCTION"),
        ("ui", "UI_PRIVILEGE_BOUNDARY"),
    ):
        findings += [f"{label}: {item}" for item in sorted(new[category] - old[category])]
    for path in sorted(after.keys() - before.keys()):
        if path.startswith("holding/") and PARALLEL.search(path):
            findings.append("PARALLEL_MODULE: " + path)
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        changes = new_violations(sources(args.root, args.base_ref), sources(args.root))
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
            SyntaxError, UnicodeError, RecursionError, ValueError) as error:
        print(f"BLOCKED: architecture evidence unavailable: {error}", file=sys.stderr)
        return 2
    for change in changes:
        print("BLOCKED: " + change)
    if changes:
        return 1
    print("PASS: no new static cycles, substantial exact duplicates or UI boundary violations.")
    print("Static check only: dynamic imports, callbacks and workers require real tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
