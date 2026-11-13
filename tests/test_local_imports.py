"""Every absolute local import in the shipped source must resolve to a file.

Release packaging has shipped ZIPs that referenced modules absent from the
archive (for example ``astrbot_host.group_standing``).  This check runs against
the working tree, so a module that is created but never committed cannot pass.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOTS = ("astrbot_host", "companion_v2")


def resolves(module: str) -> bool:
    path = ROOT.joinpath(*module.split("."))
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def missing_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    missing = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules = [node.module]
        for module in modules:
            if module.split(".", 1)[0] in LOCAL_ROOTS and not resolves(module):
                missing.append(module)
    return missing


class LocalImportTests(unittest.TestCase):
    def test_every_local_absolute_import_resolves(self):
        failures = {}
        for directory in LOCAL_ROOTS:
            for path in sorted((ROOT / directory).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                missing = missing_imports(path)
                if missing:
                    failures[path.relative_to(ROOT).as_posix()] = missing
        self.assertEqual(failures, {}, "local imports without a source file")


if __name__ == "__main__":
    unittest.main()
