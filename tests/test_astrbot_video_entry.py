"""Subprocess-isolated smoke test for the real main.py video path."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


class AstrBotVideoEntryTests(unittest.TestCase):
    def test_plugin_entry_video_preview_and_teardown(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "tests.video_entry_process"],
            cwd=root, env=os.environ.copy(), text=True, encoding="utf-8", capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, (result.stdout or "") + (result.stderr or ""))
        self.assertTrue("PASS: main.py plugin entry" in result.stdout or "SKIP: FFmpeg" in result.stdout)
