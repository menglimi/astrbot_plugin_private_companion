"""Proactive sharing should show a slice of life, not report a status."""

import unittest

from companion_v2.proactive.prompts import INSTRUCTION, PURPOSE_INSTRUCTIONS


class ProactivePromptGuidanceTests(unittest.TestCase):
    def test_proactive_instruction_prefers_a_concrete_slice_over_a_status_report(self):
        self.assertIn("报备进度", INSTRUCTION)
        self.assertIn("我正在吃饭", INSTRUCTION)
        self.assertIn("不是禁句", INSTRUCTION)
        self.assertIn("让对方有得接", INSTRUCTION)
        self.assertIn("表达偏好", INSTRUCTION)

    def test_meal_purpose_prefers_a_food_detail_or_a_light_guess(self):
        meal = PURPOSE_INSTRUCTIONS["meal"]
        self.assertIn("我正在吃饭", meal)
        self.assertIn("猜", meal)
        self.assertIn("图片/表情", meal)
        self.assertIn("也不是不能说", meal)


if __name__ == "__main__":
    unittest.main()
