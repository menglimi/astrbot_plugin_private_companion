import json
import tempfile
import unittest
from pathlib import Path

from tools.build_v2_release import CORE_DIRS, ROOT, allowed, checked_payload, sources
from tools.check_tracked_privacy import is_private_path


class ReleaseSecretDetectionTests(unittest.TestCase):
    def test_git_inventory_rejects_runtime_and_host_config(self):
        for name in (
            "data/cmd_config.json", "plugin_data/companion-7/state.db",
            "AGENTS.md", ".env.local", "astrbot_plugin_private_companion_config.json",
            "docs/private.zip", "DATA/CMD_CONFIG.JSON", "docs/private.tar.gz",
            "docs/design-preview/index.html",
        ):
            with self.subTest(path=name):
                self.assertTrue(is_private_path(name))
        for name in ("_conf_schema.json", "fixtures/astrbot_staging.example.json"):
            with self.subTest(path=name):
                self.assertFalse(is_private_path(name))

    def test_core_release_sources_include_astrbot_changelog(self):
        self.assertIn(ROOT / "CHANGELOG.md", sources(ROOT, CORE_DIRS))

    def test_excludes_local_news_feed_capture_from_release_sources(self):
        root = Path("source-root")
        fixture = root / "fixtures" / "proactive" / "news_feed_juya_20261004.xml"
        self.assertFalse(allowed(fixture, root))

    def test_excludes_development_previews_backups_and_archived_tools(self):
        root = Path("source-root")
        excluded = (
            "pages/陪伴小屋/shell-preview.html",
            "pages/陪伴小屋/shell-preview-mock.html",
            "docs/page-designs/01-cottage-home.md",
            "docs/page-designs/learning-track-preview.html",
            "docs/design-preview/index.html",
            "pages/陪伴小屋/design-previews/old/index.html",
            "docs/RELEASE_V2_0.md",
            "docs/INTEGRATION_ACCEPTANCE_V2_0.md",
            "docs/V670_TO_V700_MIGRATION_20261019.md",
            "fixtures/reply_efficiency_preview.json",
            "tests/bubble_bonbon_smoke.mjs",
            "pages/陪伴小屋/semantic-page.css.backup",
            "tests/group-test.out",
            "tools/_archive/old_check.py",
        )
        for relative in excluded:
            with self.subTest(path=relative):
                self.assertFalse(allowed(root / relative, root))

    def test_release_inventory_omits_local_shell_preview_and_backup(self):
        names = {path.relative_to(ROOT).as_posix() for path in sources(ROOT, CORE_DIRS)}
        self.assertNotIn("pages/陪伴小屋/shell-preview.html", names)
        self.assertNotIn("pages/陪伴小屋/shell-preview-mock.html", names)
        self.assertNotIn("fixtures/reply_efficiency_preview.json", names)
        self.assertFalse(any("/page-designs/" in "/" + name for name in names))
        self.assertFalse(any("/design-previews/" in "/" + name for name in names))
        self.assertFalse(any("/design-preview/" in "/" + name for name in names))
        self.assertNotIn("docs/V670_TO_V700_MIGRATION_20261019.md", names)
        self.assertNotIn("docs/RELEASE_V2_0.md", names)
        self.assertNotIn("docs/INTEGRATION_ACCEPTANCE_V2_0.md", names)
        self.assertNotIn("tests/bubble_bonbon_smoke.mjs", names)
        self.assertFalse(any(name.startswith("games/") for name in names))
        self.assertNotIn("pages/陪伴小屋/semantic-page.css.backup", names)

    def test_page_build_manifest_omits_local_shell_preview(self):
        manifest = json.loads((ROOT / "pages" / "陪伴小屋" / "page-build.json").read_text(encoding="utf-8"))
        self.assertNotIn("shell-preview.html", manifest["files"])
        self.assertNotIn("semantic-page.css.backup", manifest["files"])

    def test_catalog_contains_navigation_only(self):
        catalog = (ROOT / "pages" / "陪伴小屋" / "catalog.html").read_text(encoding="utf-8")
        self.assertIn("function readLive()", catalog)
        self.assertIn("function pageHref(", catalog)
        self.assertNotRegex(catalog, r"\b(?:settings|metric)\s*:")

    def scan(self, payload):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "fixture.md"
            source.write_bytes(payload)
            return checked_payload(source, root)

    def test_flags_credential_prefixes_at_text_boundaries(self):
        for prefix in (b"ghp_", b"github_pat_", b"abk_", b"sk-proj-"):
            with self.subTest(prefix=prefix):
                with self.assertRaisesRegex(ValueError, "credential-like content rejected"):
                    self.scan(b"key=" + prefix + b"x" * 48)

    def test_does_not_treat_risk_article_slug_as_a_secret(self):
        payload = b"https://apnews.com/article/risk-" + b"supply-chain-" + b"a" * 40
        self.assertEqual(self.scan(payload), payload + b"\n")


if __name__ == "__main__":
    unittest.main()
