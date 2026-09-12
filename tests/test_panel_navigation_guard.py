from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PanelNavigationTests(unittest.TestCase):
    def test_uninstalled_extensions_and_legacy_routes_preserve_active_panel(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        source = (ROOT / "pages/companion-panel/app.js").read_text(encoding="utf-8")
        start = source.index("function switchTab(")
        end = source.index("\nfunction ", start + 1)
        script = """
const assert = require('node:assert/strict');
const state = {activeTab: 'dashboard'};
const notices = [];
const photoReferenceManagerBusy = () => false;
const contentCompanionInstalled = () => false;
const realityCompanionInstalled = () => false;
const imageCompanionInstalled = () => false;
const showToast = (...args) => notices.push(args);
const hasUnsavedChanges = () => { throw new Error('guard must precede state or DOM mutations'); };
""" + source[start:end] + """
for (const tab of ['creative', 'reality', 'image', 'bookshelf', 'qzone']) {
  switchTab(tab);
  assert.equal(state.activeTab, 'dashboard');
}
assert.equal(notices.length, 5);
"""
        result = subprocess.run([node, "-e", script], text=True, encoding="utf-8", capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_mirrored_assets_and_unique_dom_ids(self):
        primary = ROOT / "pages/companion-panel"
        mirror = ROOT / "pages/陪伴面板"
        for name in ("app.js", "index.html", "css/polish.css", "js/panels/provider-tree.js"):
            self.assertEqual((primary / name).read_bytes(), (mirror / name).read_bytes())

        class IdParser(HTMLParser):
            ids = []

            def handle_starttag(self, tag, attrs):
                self.ids.extend(value for key, value in attrs if key == "id")

        parser = IdParser()
        parser.feed((primary / "index.html").read_text(encoding="utf-8"))
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
