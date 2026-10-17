from __future__ import annotations

import inspect
import unittest

from astrbot_plugin_private_companion.conversation_prompt_section import prompt_section
from astrbot_plugin_private_companion.user_memory import (
    UserMemoryMixin,
    _render_user_memory_background_prompt,
)


class UserMemoryBackgroundPromptAuthoringTests(unittest.TestCase):
    def test_background_renderer_preserves_existing_wire_exactly(self) -> None:
        wire = "  instruction\n\n【输入】\n{\"decision\":\"send\"}\n  tail  "
        section = prompt_section(
            key="background.memory.test",
            title="后台记忆提示词测试",
            source="test",
            content=wire,
        )

        self.assertEqual(wire, _render_user_memory_background_prompt(section))

    def test_all_user_memory_llm_tasks_use_stable_section_keys(self) -> None:
        source = inspect.getsource(UserMemoryMixin)
        keys = (
            "background.memory.emotion_judgement",
            "background.memory.smart_silence",
            "background.memory.response_review",
            "background.memory.dialogue_episode",
            "background.memory.profile",
            "background.memory.open_loop_judge",
        )

        for key in keys:
            with self.subTest(key=key):
                self.assertIn(f'key="{key}"', source)
        self.assertEqual(
            6,
            source.count("_render_user_memory_background_prompt(prompt)"),
        )


if __name__ == "__main__":
    unittest.main()
