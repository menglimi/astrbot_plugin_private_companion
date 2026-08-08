# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MetadataHelpRenderingTests(unittest.TestCase):
    def test_command_help_uses_table_safe_parameter_notation(self) -> None:
        text = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
        in_help = False
        command_lines: list[str] = []
        for line in text.splitlines():
            if line == "help: |":
                in_help = True
                continue
            if in_help and line and not line.startswith("  "):
                break
            if in_help and line.startswith("  - "):
                command_lines.append(line)

        self.assertGreater(len(command_lines), 20)
        self.assertTrue(any("陪伴 增添状态" in line for line in command_lines))
        self.assertTrue(any("陪伴 参考图" in line for line in command_lines))
        for line in command_lines:
            self.assertNotIn("<", line)
            self.assertNotIn(">", line)
            self.assertNotIn("|", line)

    def test_readme_command_examples_use_the_same_notation(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        command_lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith("陪伴 ") and not line.strip().startswith("陪伴面板")
        ]
        self.assertGreater(len(command_lines), 20)
        for line in command_lines:
            self.assertNotIn("<", line)
            self.assertNotIn(">", line)
            self.assertNotIn("|", line)


if __name__ == "__main__":
    unittest.main()
