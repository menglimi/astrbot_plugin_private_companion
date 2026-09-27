from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _module_level_statements(tree: ast.Module):
    """Yield the statements that actually execute while ``main`` imports.

    Descends into ``try`` / ``if`` / ``for`` bodies (a fallback inside an
    ``except`` handler is still import-time code) but never into function or
    class bodies, which only run once the module has finished importing.
    """

    stack = list(tree.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.stmt, ast.ExceptHandler)):
                stack.append(child)


def _module_level_logger_binding(tree: ast.Module) -> int | None:
    """Return the line that binds ``logger = ...`` at import time, if any."""

    for node in _module_level_statements(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    return node.lineno
    return None


def _module_level_logger_calls(tree: ast.Module) -> list[int]:
    """Return lines of import-time ``logger.<method>(...)`` calls."""

    linenos: list[int] = []
    for node in _module_level_statements(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "logger"
        ):
            linenos.append(node.lineno)
    return sorted(linenos)


def _dunder_all_entries(tree: ast.Module) -> list[str] | None:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            return [
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
    return None


def _top_level_names(tree: ast.Module) -> set[str]:
    """Collect every name bound at module scope.

    Uses the import-time statement walk so names bound inside ``try`` /
    ``if`` blocks (for example a guarded re-import) are counted too.
    """

    names: set[str] = set()
    for node in _module_level_statements(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for element in target.elts:
                        if isinstance(element, ast.Name):
                            names.add(element.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


class OptionalModuleFallbackTests(unittest.TestCase):
    """``main.py`` optional-module fallbacks must remain fail-open.

    ``group_member_safety`` and ``self_timeline`` may be absent from an
    incomplete release package.  Each ``except ModuleNotFoundError`` branch
    installs a fallback class and then reports the missing module through
    ``logger``.  Because the binding of ``logger`` originally sat far below
    those branches, the degradation path itself raised ``NameError`` and
    aborted the whole plugin import.
    """

    def setUp(self) -> None:
        self.tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))

    def test_logger_binding_precedes_every_import_time_use(self) -> None:
        binding = _module_level_logger_binding(self.tree)
        self.assertIsNotNone(binding, "main.py must bind a module-level logger")

        calls = _module_level_logger_calls(self.tree)
        self.assertTrue(calls, "expected import-time logger calls in main.py")

        early = [lineno for lineno in calls if lineno < binding]
        self.assertEqual(
            early,
            [],
            "main.py calls `logger` at import time on line(s) "
            f"{early} but only binds it on line {binding}; an optional-module "
            "fallback reaching those lines raises NameError and turns "
            "fail-open degradation into an import crash",
        )


class ModuleExportContractTests(unittest.TestCase):
    """``__all__`` must not advertise names a module never defines.

    Star-importing a module whose ``__all__`` lists a missing name raises
    ``AttributeError`` at the import site, so the declaration has to stay in
    sync with what the module actually binds.
    """

    def test_all_entries_are_defined(self) -> None:
        offenders: list[str] = []
        for path in sorted(ROOT.rglob("*.py")):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "__all__" not in text:
                continue
            tree = ast.parse(text)
            entries = _dunder_all_entries(tree)
            if not entries:
                continue
            defined = _top_level_names(tree)
            missing = [name for name in entries if name not in defined]
            if missing:
                offenders.append(f"{path.relative_to(ROOT)}: {missing}")

        self.assertEqual(
            offenders,
            [],
            "__all__ declares names that are not defined in the module:\n"
            + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
