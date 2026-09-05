#!/usr/bin/env python3
"""Build and gate the repository's static Python module dependency graph.

The analysis is deliberately import-free: source files are parsed with ``ast``
so running the gate cannot initialize AstrBot, plugins, databases, or tests.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

EXCLUDED_PARTS = frozenset(
    {".git", ".pytest_cache", "__pycache__", "benchmarks", "data", "dist", "scripts", "tests", "verification"}
)
GIANT_MIXIN_LINES = 500
GIANT_MIXIN_METHODS = 30
THIN_MODULE_LINES = 30


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    line: int


@dataclass(frozen=True)
class MixinMetric:
    module: str
    name: str
    lines: int
    methods: int


def sources(root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in root.rglob("*.py")
            if not any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts)
        )
    )


def module_name(root: Path, path: Path) -> tuple[str, bool]:
    parts = list(path.relative_to(root).with_suffix("").parts)
    is_package = bool(parts and parts[-1] == "__init__")
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def local_target(candidate: str, modules: set[str], package: str) -> str | None:
    # Absolute imports sometimes include the installed package name while the
    # repository's historical fallback imports use top-level module names.
    candidates = [candidate]
    prefix = f"{package}."
    if candidate.startswith(prefix):
        candidates.append(candidate[len(prefix) :])
    for value in candidates:
        while value:
            if value in modules:
                return value
            value = value.rpartition(".")[0]
    return None


def imports(
    tree: ast.Module,
    module: str,
    is_package: bool,
    modules: set[str],
    package_name: str,
) -> tuple[Edge, ...]:
    package = module.split(".") if is_package else module.split(".")[:-1]
    found: set[Edge] = set()
    for node in ast.walk(tree):
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                trim = node.level - 1
                base = package[: max(0, len(package) - trim)]
                prefix = ".".join((*base, *((node.module or "").split("."))))
            else:
                prefix = node.module or ""
            if prefix:
                candidates.append(prefix)
            candidates.extend(
                ".".join(part for part in (prefix, alias.name) if part)
                for alias in node.names
                if alias.name != "*"
            )
        for candidate in candidates:
            target = local_target(candidate, modules, package_name)
            if target and target != module:
                found.add(Edge(module, target, node.lineno))
    return tuple(sorted(found, key=lambda edge: (edge.target, edge.line)))


def strongly_connected_components(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    active: set[str] = set()
    result: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        active.add(node)
        for dependency in sorted(graph[node]):
            if dependency not in indexes:
                visit(dependency)
                lowlinks[node] = min(lowlinks[node], lowlinks[dependency])
            elif dependency in active:
                lowlinks[node] = min(lowlinks[node], indexes[dependency])
        if indexes[node] != lowlinks[node]:
            return
        component: list[str] = []
        while True:
            member = stack.pop()
            active.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1 or node in graph[node]:
            result.append(tuple(sorted(component)))

    for node in sorted(graph):
        if node not in indexes:
            visit(node)
    return sorted(result, key=lambda item: (len(item), item))


def git_lines(root: Path, *arguments: str) -> list[str]:
    completed = subprocess.run(
        ["git", *arguments], cwd=root, check=True, text=True,
        encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return completed.stdout.splitlines()


def added_modules(root: Path, base: str | None, paths: dict[str, Path]) -> set[str]:
    if not base:
        return set()
    names = set(git_lines(root, "diff", "--name-only", "--diff-filter=A", f"{base}...HEAD", "--", "*.py"))
    return {module for module, path in paths.items() if path.relative_to(root).as_posix() in names}


def domain(module: str) -> str | None:
    parts = module.split(".")
    return parts[1] if len(parts) > 2 and parts[0] == "domains" else None


def analyze(root: Path, base: str | None, package_name: str) -> dict[str, object]:
    parse_errors: list[dict[str, object]] = []
    paths: dict[str, Path] = {}
    packages: dict[str, bool] = {}
    trees: dict[str, ast.Module] = {}
    line_counts: dict[str, int] = {}
    for path in sources(root):
        module, is_package = module_name(root, path)
        if not module:
            continue
        paths[module], packages[module] = path, is_package
        text = path.read_text(encoding="utf-8-sig")
        line_counts[module] = len(text.splitlines())
        try:
            trees[module] = ast.parse(text, filename=str(path))
        except (SyntaxError, UnicodeError) as exc:
            parse_errors.append({"module": module, "error": str(exc)})
    module_set = set(paths)
    edges = tuple(
        edge
        for module, tree in sorted(trees.items())
        for edge in imports(tree, module, packages[module], module_set, package_name)
    )
    graph = {module: set() for module in module_set}
    for edge in edges:
        graph[edge.source].add(edge.target)
    sccs = strongly_connected_components(graph)
    added = added_modules(root, base, paths)

    mixins: list[MixinMetric] = []
    for module, tree in trees.items():
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not node.name.endswith("Mixin"):
                continue
            methods = sum(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) for child in node.body)
            lines = (node.end_lineno or node.lineno) - node.lineno + 1
            if lines >= GIANT_MIXIN_LINES or methods >= GIANT_MIXIN_METHODS:
                mixins.append(MixinMetric(module, node.name, lines, methods))
    giant_modules = {item.module for item in mixins}
    reverse_giant = [asdict(edge) for edge in edges if edge.source in added and edge.target in giant_modules]

    cross_domain = []
    for edge in edges:
        source_domain, target_domain = domain(edge.source), domain(edge.target)
        if source_domain and target_domain and source_domain != target_domain:
            cross_domain.append(asdict(edge) | {"source_domain": source_domain, "target_domain": target_domain})
        elif source_domain and not target_domain and edge.target in giant_modules:
            cross_domain.append(asdict(edge) | {"source_domain": source_domain, "target_domain": "runtime-mixin"})

    thin_wrappers: list[dict[str, object]] = []
    managers: list[dict[str, object]] = []
    for module in sorted(added):
        tree = trees.get(module)
        if tree is None:
            continue
        definitions = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
        if line_counts[module] <= THIN_MODULE_LINES:
            thin_wrappers.append({
                "module": module, "lines": line_counts[module],
                "definitions": [node.name for node in definitions],
                "classification": "public-facade" if packages[module] else "small-policy-seam",
            })
        for node in definitions:
            if isinstance(node, ast.ClassDef) and node.name.lower().endswith("manager"):
                methods = sum(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) for child in node.body)
                managers.append({
                    "module": module, "name": node.name, "lines": (node.end_lineno or node.lineno) - node.lineno + 1,
                    "methods": methods, "meaningless": methods <= 1,
                })

    new_sccs = [list(component) for component in sccs if added.intersection(component)]
    new_cross_domain = [item for item in cross_domain if item["source"] in added]
    meaningless_managers = [item for item in managers if item["meaningless"]]
    failures = {
        "parse_errors": parse_errors,
        "new_cycle_sccs": new_sccs,
        "new_reverse_giant_mixin_dependencies": reverse_giant,
        "new_cross_domain_dependencies": new_cross_domain,
        "new_meaningless_managers": meaningless_managers,
    }
    return {
        "schema_version": 1,
        "candidate": git_lines(root, "rev-parse", "HEAD")[0],
        "base": base,
        "policy": {
            "giant_mixin_min_lines": GIANT_MIXIN_LINES,
            "giant_mixin_min_methods": GIANT_MIXIN_METHODS,
            "thin_module_max_lines": THIN_MODULE_LINES,
            "cross_domain": "domains.<owner> may not import another domain or a giant runtime mixin",
        },
        "summary": {
            "modules": len(module_set), "edges": len({(edge.source, edge.target) for edge in edges}),
            "added_modules": len(added), "cycle_sccs": len(sccs),
            "gate_failures": sum(len(value) for value in failures.values()),
        },
        "added_modules": sorted(added),
        "edges": [asdict(edge) for edge in edges],
        "cycle_sccs": [list(component) for component in sccs],
        "giant_mixins": [asdict(item) for item in sorted(mixins, key=lambda item: item.module)],
        "cross_domain_dependencies": cross_domain,
        "thin_wrapper_candidates": thin_wrappers,
        "manager_candidates": managers,
        "failures": failures,
    }


def write_dot(path: Path, report: dict[str, object]) -> None:
    added = set(report["added_modules"])
    lines = ["digraph python_dependencies {", "  rankdir=LR;"]
    for module in sorted({edge["source"] for edge in report["edges"]} | {edge["target"] for edge in report["edges"]}):
        attributes = ' [style="filled",fillcolor="#fff2a8"]' if module in added else ""
        lines.append(f"  {json.dumps(module)}{attributes};")
    for edge in report["edges"]:
        lines.append(f"  {json.dumps(edge['source'])} -> {json.dumps(edge['target'])};")
    lines.append("}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--package", default="astrbot_plugin_private_companion",
        help="Installed package prefix used by absolute local imports",
    )
    parser.add_argument("--base", help="Git base used to classify newly added modules")
    parser.add_argument("--output", type=Path, help="Write the JSON report here")
    parser.add_argument("--dot", type=Path, help="Write the complete Graphviz dependency graph here")
    parser.add_argument("--no-gate", action="store_true", help="Report findings without a failing exit status")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report = analyze(root, args.base, args.package)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    if args.dot:
        write_dot(args.dot, report)
    return 0 if args.no_gate or report["summary"]["gate_failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
