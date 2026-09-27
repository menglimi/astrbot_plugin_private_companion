import unittest
from pathlib import Path

from tests.module_source_index import file_family_source_text


class GroupStatusLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[1]
        cls.source = file_family_source_text(root, "group_observation.py")

    def test_status_exposes_global_group_allowlist_and_effective_layers(self) -> None:
        self.assertIn("群聊陪伴最终状态", self.source)
        self.assertIn("群聊陪伴总开关", self.source)
        self.assertIn("本群单独开关", self.source)
        self.assertIn("名单放行", self.source)
        self.assertIn("状态说明", self.source)

    def test_status_explains_manual_group_disable_without_overriding_it(self) -> None:
        self.assertIn("本群单独停用；可在群聊面板启用本群", self.source)
        self.assertIn("effective_enabled = global_enabled and group_enabled and allowed_by_mode", self.source)


if __name__ == "__main__":
    unittest.main()
