from __future__ import annotations

import unittest

from astrbot_plugin_private_companion import creative
from astrbot_plugin_private_companion import news_exploration
from astrbot_plugin_private_companion import proactive_engine
from astrbot_plugin_private_companion import proactive_message
from astrbot_plugin_private_companion import qzone_schedule
from astrbot_plugin_private_companion.reading_archive import ReadingArchiveMixin


class _ScopedSettings:
    provider_config_mode = "quick"

    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = dict(values or {})

    def persona_setting(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)


class _CreativeHarness(creative.CreativeMixin, _ScopedSettings):
    def __init__(self, values: dict[str, object]) -> None:
        _ScopedSettings.__init__(self, values)
        self.data = {"daily_state": {"energy": 70}}


class _ReadingHarness(ReadingArchiveMixin, _ScopedSettings):
    pass


class _EngineHarness(proactive_engine.ProactiveEngineMixin, _ScopedSettings):
    def _image_companion_backend_available(self, _backend: str) -> bool:
        return True


class _MessageHarness(proactive_message.ProactiveMessageMixin, _ScopedSettings):
    def _reaction_image_provider_available(self) -> bool:
        return True


class _NewsHarness(news_exploration.NewsExplorationMixin, _ScopedSettings):
    pass


class _QzoneHarness(qzone_schedule.QzoneScheduleMixin, _ScopedSettings):
    pass


class PersonaContentRuntimeTests(unittest.TestCase):
    def test_content_behaviour_uses_active_persona_settings(self) -> None:
        creative_harness = _CreativeHarness(
            {
                "bot_name": "次人格",
                "default_style": "平静",
                "schedule_persona_prompt": "",
                "creative_chars_per_session": 333,
            }
        )
        self.assertEqual(creative_harness._creative_chars_per_session(), 333)

        reading_harness = _ReadingHarness(
            {
                "enable_reading_archive_integration": True,
                "enable_reading_archive_boredom_read": True,
            }
        )
        self.assertFalse(reading_harness._reading_archive_available())
        self.assertFalse(reading_harness._reading_archive_read_available())
        self.assertEqual(
            "",
            reading_harness._format_bookshelf_reading_context_for_reply(
                "你最近读了什么"
            ),
        )

        engine_harness = _EngineHarness({"enable_photo_text_action": False})
        self.assertFalse(engine_harness._comfyui_photo_available())
        engine_harness.values["enable_photo_text_action"] = True
        self.assertTrue(engine_harness._comfyui_photo_available())

        message_harness = _MessageHarness(
            {
                "enable_reaction_expression_experiment": True,
                "reaction_expression_private_enabled": True,
                "reaction_expression_proactive_enabled": True,
            }
        )
        self.assertTrue(message_harness._proactive_reaction_expression_enabled("message"))

        news_harness = _NewsHarness({"news_sources": "测试源|https://example.com/feed"})
        self.assertEqual(news_harness._news_source_items()[0]["name"], "测试源")

        qzone_harness = _QzoneHarness(
            {
                "qzone_life_publish_window_mode": "custom",
                "qzone_life_publish_windows": "09:00-10:00",
            }
        )
        self.assertEqual(qzone_harness._qzone_life_publish_window_source(), "09:00-10:00")

    def test_provider_routes_follow_quick_and_precision_persona_values(self) -> None:
        helpers = (
            proactive_message._persona_provider_id,
            proactive_engine._persona_provider_id,
            news_exploration._persona_provider_id,
            creative._persona_provider_id,
            qzone_schedule._persona_provider_id,
        )
        quick = _ScopedSettings(
            {
                "FAST_RESPONSE_PROVIDER_ID": "persona-fast",
                "COMPLEX_REASONING_PROVIDER_ID": "persona-complex",
                "CREATIVE_MODEL_PROVIDER_ID": "persona-creative",
            }
        )
        for helper in helpers:
            with self.subTest(helper=helper.__module__, mode="quick"):
                self.assertEqual(
                    helper(quick, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
                    "persona-fast",
                )
                self.assertEqual(
                    helper(quick, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
                    "persona-complex",
                )
                self.assertEqual(
                    helper(quick, "CREATIVE_PROVIDER_ID", "creative_provider_id", "creative"),
                    "persona-creative",
                )

        precision = _ScopedSettings({"CREATIVE_PROVIDER_ID": "persona-precision"})
        precision.provider_config_mode = "precision"
        for helper in helpers:
            with self.subTest(helper=helper.__module__, mode="precision"):
                self.assertEqual(
                    helper(
                        precision,
                        "CREATIVE_PROVIDER_ID",
                        "creative_provider_id",
                        "creative",
                    ),
                    "persona-precision",
                )


if __name__ == "__main__":
    unittest.main()
