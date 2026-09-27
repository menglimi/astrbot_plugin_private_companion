from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from tests.module_source_index import file_family_source_text
from astrbot_plugin_private_companion import creative, daily_review, dreaming, game_integration, planning
from astrbot_plugin_private_companion.conversation_prompt_section import prompt_section


ROOT = Path(__file__).resolve().parents[1]


def _family_source(module_name: str) -> str:
    """聚合模块族源码（门面 + 所有 part 文件），用于拆分后测试。"""
    return file_family_source_text(ROOT, f"{module_name}.py")


class BackgroundPromptAuthoringTests(unittest.TestCase):
    def test_task_renderers_preserve_background_wire_exactly(self) -> None:
        wire = "  前导空格\n\n【资料】\n{\"ok\":true}\n- item\n  尾部空格  "
        renderers = (
            creative._render_creative_prompt,
            dreaming._render_dreaming_prompt,
            planning._render_planning_prompt,
            game_integration._render_game_prompt,
        )

        for renderer in renderers:
            with self.subTest(renderer=renderer.__module__):
                section = prompt_section(
                    key="background.test.golden",
                    title="后台提示词字节契约",
                    source="test",
                    content=wire,
                )
                self.assertEqual(wire, renderer(section))

    def test_every_migrated_llm_task_declares_a_stable_section_key(self) -> None:
        expected = {
            creative: (
                "background.creative.project",
                "background.creative.outline",
                "background.creative.review",
                "background.creative.extract",
                "background.creative.writing",
            ),
            dreaming: (
                "background.dream.generate",
                "background.diary.rewrite",
                "background.diary.derivatives",
                "background.diary.generate",
            ),
            planning: (
                "background.schedule.detail.full",
                "background.schedule.detail.stable",
                "background.schedule.detail.segment",
                "background.schedule.detail.retry",
                "background.schedule.daily_plan",
                "background.schedule.daily_plan.retry_format",
                "background.schedule.daily_plan.retry_micro_segments",
                "background.schedule.daily_plan.retry_abstract_segments",
                "background.schedule.daily_plan.retry_calendar",
                "background.schedule.daily_plan.retry_repetition",
                "background.schedule.daily_plan.retry_quality",
            ),
            daily_review: ("background.daily_review",),
            game_integration: ("background.game.emotional_afterglow",),
        }

        for module, keys in expected.items():
            mod_name = module.__name__.split(".")[-1]
            source = _family_source(mod_name)
            for key in keys:
                with self.subTest(module=module.__name__, key=key):
                    self.assertIn(f'key="{key}"', source)

    def test_daily_review_legacy_entrypoint_renders_its_authored_section(self) -> None:
        source = inspect.getsource(daily_review.DailyReviewMixin._daily_review_prompt)

        self.assertIn("self._daily_review_prompt_section(snapshot)", source)
        self.assertIn("PromptRenderMode.BODY_ONLY", source)


if __name__ == "__main__":
    unittest.main()
