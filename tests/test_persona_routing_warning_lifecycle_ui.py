from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL_SCRIPTS = (
    ROOT / "pages" / "companion-panel" / "app.js",
    ROOT / "pages" / "陪伴面板" / "app.js",
)
PANEL_HTML = (
    ROOT / "pages" / "companion-panel" / "index.html",
    ROOT / "pages" / "陪伴面板" / "index.html",
)


class PersonaRoutingWarningLifecycleUiTests(unittest.TestCase):
    def test_panel_trees_share_visible_troubleshooting_refresh_contract(self) -> None:
        sources = [path.read_text(encoding="utf-8") for path in PANEL_SCRIPTS]
        self.assertEqual(sources[0], sources[1])
        source = sources[0]

        interval_match = re.search(
            r"TROUBLESHOOTING_REFRESH_INTERVAL_MS\s*=\s*(\d+)\s*\*\s*1000",
            source,
        )
        self.assertIsNotNone(interval_match)
        self.assertGreaterEqual(int(interval_match.group(1)), 30)
        self.assertLessEqual(int(interval_match.group(1)), 60)
        self.assertIn('state.activeTab !== "troubleshooting"', source)
        self.assertIn('document.visibilityState !== "visible"', source)
        self.assertIn('window.clearInterval(troubleshootingRefreshTimer)', source)
        self.assertIn('document.addEventListener("visibilitychange", syncTroubleshootingRefreshTimer)', source)
        self.assertIn('window.addEventListener("pagehide", stopTroubleshootingRefreshTimer)', source)

        html_sources = [path.read_text(encoding="utf-8") for path in PANEL_HTML]
        self.assertEqual(html_sources[0], html_sources[1])
        self.assertIn("troubleshooting=persona-routing-lifecycle-v1", html_sources[0])

    def test_refresh_reuses_one_in_flight_troubleshooting_request(self) -> None:
        source = PANEL_SCRIPTS[0].read_text(encoding="utf-8")
        self.assertIn("let troubleshootingLoadPromise = null", source)
        self.assertIn("if (!troubleshootingLoadPromise)", source)
        self.assertIn("const request = troubleshootingLoadPromise", source)
        self.assertIn("troubleshootingLoadPromise === request", source)


if __name__ == "__main__":
    unittest.main()
