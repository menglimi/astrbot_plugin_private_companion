from pathlib import Path
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
APP_JS = (PLUGIN_ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")


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


if __name__ == "__main__":
    unittest.main()
