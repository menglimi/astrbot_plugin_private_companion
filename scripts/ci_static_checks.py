# -*- coding: utf-8 -*-
"""Dependency-free CI checks for source architecture and packaged imports."""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Iterable


_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".github",
        ".pytest_cache",
        "__pycache__",
        "benchmarks",
        "data",
        "dist",
        "scripts",
        "tests",
        "verification",
    }
)


def _sources(root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in root.rglob("*.py")
            if not any(part in _EXCLUDED_DIRS for part in path.relative_to(root).parts)
        )
    )


def _module_name(root: Path, path: Path) -> tuple[str, bool]:
    relative = path.relative_to(root)
    parts = list(relative.with_suffix("").parts)
    is_package = bool(parts and parts[-1] == "__init__")
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def _local_target(candidate: str, modules: set[str]) -> str | None:
    value = candidate
    while value:
        if value in modules:
            return value
        value = value.rpartition(".")[0]
    return None


def _imports(
    tree: ast.Module,
    *,
    module: str,
    is_package: bool,
    modules: set[str],
) -> set[str]:
    result: set[str] = set()
    current = module.split(".") if module else []
    package = current if is_package else current[:-1]
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _local_target(alias.name, modules)
                if target:
                    result.add(target)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            trim = node.level - 1
            base = package[: max(0, len(package) - trim)]
            if node.module:
                candidate = ".".join((*base, *node.module.split(".")))
                target = _local_target(candidate, modules)
                if target:
                    result.add(target)
            else:
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    target = _local_target(".".join((*base, alias.name)), modules)
                    if target:
                        result.add(target)
        elif node.module:
            target = _local_target(node.module, modules)
            if target:
                result.add(target)
    result.discard(module)
    return result


def _property_extension(node: ast.AST, name: str) -> bool:
    decorators = getattr(node, "decorator_list", ())
    return any(
        isinstance(decorator, ast.Attribute)
        and decorator.attr in {"setter", "deleter"}
        and isinstance(decorator.value, ast.Name)
        and decorator.value.id == name
        for decorator in decorators
    )


def _duplicate_findings(path: Path, tree: ast.Module) -> list[str]:
    findings: list[str] = []
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    counts = Counter(node.name for node in definitions)
    for name, count in sorted(counts.items()):
        if count > 1:
            findings.append(f"{path}: duplicate top-level symbol {name!r}")
    for class_node in (node for node in definitions if isinstance(node, ast.ClassDef)):
        bases = [ast.unparse(base) for base in class_node.bases]
        for base, count in Counter(bases).items():
            if count > 1:
                findings.append(
                    f"{path}:{class_node.lineno}: duplicate base {base!r} in {class_node.name}"
                )
        members = [
            node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        by_name: dict[str, list[ast.AST]] = {}
        for member in members:
            by_name.setdefault(member.name, []).append(member)
        for name, duplicates in sorted(by_name.items()):
            if len(duplicates) <= 1:
                continue
            if all(_property_extension(node, name) for node in duplicates[1:]):
                continue
            findings.append(
                f"{path}:{duplicates[1].lineno}: duplicate class member {class_node.name}.{name}"
            )
    return findings


def _cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    found: set[tuple[str, ...]] = set()
    visiting: list[str] = []
    active: set[str] = set()
    complete: set[str] = set()

    def canonical(cycle: list[str]) -> tuple[str, ...]:
        body = cycle[:-1]
        rotations = [tuple(body[index:] + body[:index]) for index in range(len(body))]
        return min(rotations)

    def visit(node: str) -> None:
        if node in complete:
            return
        if node in active:
            index = visiting.index(node)
            found.add(canonical(visiting[index:] + [node]))
            return
        active.add(node)
        visiting.append(node)
        for dependency in sorted(graph.get(node, ())):
            visit(dependency)
        visiting.pop()
        active.remove(node)
        complete.add(node)

    for name in sorted(graph):
        visit(name)
    return sorted(found)


def check_architecture(root: Path) -> None:
    sources = _sources(root)
    module_rows = [(path, *_module_name(root, path)) for path in sources]
    modules = {module for _path, module, _is_package in module_rows if module}
    graph: dict[str, set[str]] = {module: set() for module in modules}
    findings: list[str] = []
    for path, module, is_package in module_rows:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            findings.append(f"{path}: cannot parse: {exc}")
            continue
        findings.extend(_duplicate_findings(path, tree))
        if module:
            graph[module] = _imports(
                tree,
                module=module,
                is_package=is_package,
                modules=modules,
            )
    for cycle in _cycles(graph):
        findings.append("import cycle: " + " -> ".join((*cycle, cycle[0])))
    if findings:
        raise SystemExit("\n".join(findings))


def check_artifact_import(root: Path, package: str, module: str) -> None:
    with tempfile.TemporaryDirectory(prefix="plugin-artifact-") as temporary:
        archive = Path(temporary) / "plugin.zip"
        extracted = Path(temporary) / "extracted"
        files = tuple(
            sorted(
                path
                for path in root.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and not any(
                    part in _EXCLUDED_DIRS
                    for part in path.relative_to(root).parts
                )
            )
        )
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            has_init = False
            for path in files:
                relative = path.relative_to(root)
                if relative == Path("__init__.py"):
                    has_init = True
                bundle.write(path, f"{package}/{relative.as_posix()}")
            if not has_init:
                bundle.writestr(f"{package}/__init__.py", "# artifact package\n")
        with zipfile.ZipFile(archive, "r") as bundle:
            bundle.extractall(extracted)
        statement = (
            "import importlib,sys;"
            f"sys.path.insert(0,{str(extracted)!r});"
            f"importlib.import_module({f'{package}.{module}'!r})"
        )
        subprocess.run(
            [sys.executable, "-I", "-c", statement],
            check=True,
            cwd=temporary,
        )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--import-module", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    root = args.root.resolve()
    check_architecture(root)
    check_artifact_import(root, args.package, args.import_module)
    print("architecture and artifact checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
