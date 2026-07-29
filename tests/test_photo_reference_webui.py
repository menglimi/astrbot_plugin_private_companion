from pathlib import Path
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
APP_JS = (PLUGIN_ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (PLUGIN_ROOT / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")


class PhotoReferenceWebUiTests(unittest.TestCase):
    def test_time_categories_round_trip_through_manager_draft(self) -> None:
        self.assertIn('metadata.time_categories = normalizePhotoReferenceMetadataList', APP_JS)
        self.assertIn('time_categories: Array.isArray(item.time_categories)', APP_JS)
        self.assertIn('time_categories: normalizePhotoReferenceMetadataList', APP_JS)
        self.assertIn('data-photo-reference-times', APP_JS)

    def test_role_shortcuts_are_rendered_and_applied(self) -> None:
        self.assertIn('status?.options?.role_shortcuts', APP_JS)
        self.assertIn('data-photo-reference-role-shortcut', APP_JS)
        self.assertIn('input.dataset.photoReferenceRoleShortcut', APP_JS)

    def test_selfie_workflow_help_describes_dynamic_image_count(self) -> None:
        self.assertIn("images=N 自拍/改图工作流", APP_JS)
        self.assertNotIn("优先寻找 images=1 的自拍工作流", APP_JS)

    def test_metadata_editor_uses_localized_select_controls(self) -> None:
        self.assertIn('<select data-photo-reference-outfit-category', APP_JS)
        self.assertIn('photoReferenceMultiSelectHtml("reference_roles"', APP_JS)
        self.assertIn('photoReferenceMultiSelectHtml("scene_categories"', APP_JS)
        self.assertIn('photoReferenceMultiSelectHtml("time_categories"', APP_JS)
        self.assertIn('<select data-photo-reference-preferred-preset', APP_JS)
        self.assertIn('photoReferenceSingleSelectOptions("outfit_categories"', APP_JS)
        self.assertIn('photoReferenceSingleSelectOptions("presets"', APP_JS)
        self.assertNotIn('placeholder="sleepwear / daily_outfit / formal"', APP_JS)
        self.assertNotIn('placeholder="home, bedroom, outdoor"', APP_JS)
        self.assertNotIn('placeholder="morning, evening, bedtime"', APP_JS)

    def test_metadata_editor_explains_each_decision_field(self) -> None:
        expected_help = (
            "展开后可指定这张图在生图时负责保留哪些信息。",
            "决定生成时从这张图保留哪些内容",
            "标记图片中的服装类型",
            "控制是否优先沿用参考图中的服装",
            "选择这张图适合使用的通用场景",
            "选择这张图适合使用的时间段",
            "选择使用这张图时优先套用的生图场景预设",
        )
        for help_text in expected_help:
            with self.subTest(help_text=help_text):
                self.assertIn(help_text, APP_JS)

    def test_metadata_editor_assets_have_a_matching_cache_version(self) -> None:
        version = "20260729-photo-trace-default-10240"
        self.assertIn(f'app.css?v={version}', INDEX_HTML)
        self.assertIn(f'app.js?v={version}', INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
