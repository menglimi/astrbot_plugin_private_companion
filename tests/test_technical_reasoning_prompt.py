import unittest
from pathlib import Path
from types import SimpleNamespace


class TechnicalReasoningPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[1]
        cls.main_source = (root / "main.py").read_text(encoding="utf-8")
        cls.command_source = (root / "command_handlers.py").read_text(encoding="utf-8")

    def test_plain_chat_only_injects_unit_guard_for_technical_questions(self) -> None:
        self.assertIn("def _format_technical_reasoning_prompt(", self.main_source)
        self.assertIn("event: AstrMessageEvent | None", self.main_source)
        self.assertIn("req: ProviderRequest | None = None", self.main_source)
        self.assertIn("technical_prompt = self._format_technical_reasoning_prompt(", self.main_source)
        self.assertIn("include_heading=False", self.main_source)
        self.assertIn('str(getattr(req, "prompt", "") or "").strip()', self.main_source)
        self.assertIn('"代码", "源码", "脚本", "python", "sleep("', self.main_source)

    def test_prompt_preserves_units_and_forbids_invented_math(self) -> None:
        for source in (self.main_source, self.command_source):
            self.assertIn("统一", source)
            self.assertIn("基本单位", source)
            self.assertIn("反向换算", source)
            self.assertIn("不能凭空加入 ln", source)
            self.assertIn("time.sleep(10 * 60)", source)
            self.assertIn("600 秒", source)
            self.assertIn("10 分钟", source)


if __name__ == "__main__":
    unittest.main()
