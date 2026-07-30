# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import functools
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot.api.message_components import Plain, Record
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


async def _registered_mimo_tool(plugin, event, text: str, **kwargs):
    plugin.tool_calls += 1
    return "tool should not be called directly"


class _FakeMimoVoiceClonePlugin:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.calls: list[tuple[str, dict]] = []
        self.tool_calls = 0

    async def synthesize_text(self, text: str, **kwargs):
        self.calls.append((text, kwargs))
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_bytes(b"RIFF-test")
        return [self.output_path]


class _ToolManager:
    def __init__(self, plugin=None) -> None:
        self.plugin = plugin
        self.requested: list[str] = []

    def get_tool(self, name: str):
        self.requested.append(name)
        if self.plugin is None or name != "mimo_tts_speak":
            return None
        return SimpleNamespace(
            active=True,
            handler=functools.partial(_registered_mimo_tool, self.plugin),
        )


class _Context:
    def __init__(self, manager: _ToolManager, provider=None) -> None:
        self.manager = manager
        self.provider = provider

    def get_llm_tool_manager(self):
        return self.manager

    def get_config(self, _umo: str):
        return {}

    def get_using_tts_provider(self, _umo: str):
        return self.provider


class _Harness(TtsEnhancementMixin):
    def __init__(self, context: _Context) -> None:
        self.context = context
        self.config = {}
        self.tts_synthesis_backend = "mimo_voice_clone"
        self.tts_mimo_tool_name = "mimo_tts_speak"
        self.tts_mimo_voice_name = "测试角色"
        self.tts_mimo_style_prompt = "温柔自然，像在私聊"
        self.tts_voice_language = "zh"
        self.tts_delivery_mode = "voice_and_text"
        self.tts_foreign_text_mode = "translation"
        self.tts_conversion_scope = "partial"
        self.tts_frequency_control_mode = "legacy"
        self._tts_session_last_at = {}
        self._after_tts_audio_generated = AsyncMock()


class _ActiveVoiceHarness(_Harness, ProactiveEngineMixin):
    enable_voice_action = True


class _Event:
    unified_msg_origin = "default:FriendMessage:10001"

    def get_result(self):
        return SimpleNamespace()

    def get_sender_id(self):
        return "10001"


class MimoVoiceCloneBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_mimo_backend_works_without_astrbot_tts_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = _FakeMimoVoiceClonePlugin(root / "plugin_data" / "mimo" / "voice.wav")
            manager = _ToolManager(plugin)
            harness = _Harness(_Context(manager, provider=None))

            with patch(
                "astrbot_plugin_private_companion.tts_enhancement.get_astrbot_data_path",
                return_value=str(root),
            ):
                components = await harness._process_tts_tags(
                    "<tts>今晚早点休息吧。</tts>",
                    _Event(),
                    fallback_plain="今晚早点休息吧。",
                )
                await asyncio.sleep(0)

        self.assertTrue(any(isinstance(component, Record) for component in components))
        self.assertTrue(any(isinstance(component, Plain) for component in components))
        self.assertEqual(["mimo_tts_speak"], manager.requested)
        self.assertEqual(0, plugin.tool_calls)
        self.assertEqual("今晚早点休息吧。", plugin.calls[0][0])
        self.assertEqual("测试角色", plugin.calls[0][1]["voice_name"])
        self.assertEqual("温柔自然，像在私聊", plugin.calls[0][1]["context"])
        self.assertEqual("10001", plugin.calls[0][1]["user_id"])
        self.assertEqual(_Event.unified_msg_origin, plugin.calls[0][1]["group_id"])
        self.assertFalse(plugin.calls[0][1]["split"])

    async def test_together_companion_realtime_bridge_uses_mimo_without_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = _FakeMimoVoiceClonePlugin(root / "plugin_data" / "mimo" / "together.wav")
            harness = _Harness(_Context(_ToolManager(plugin), provider=None))

            with patch(
                "astrbot_plugin_private_companion.tts_enhancement.get_astrbot_data_path",
                return_value=str(root),
            ):
                result = await harness._synthesize_realtime_voice(
                    "一起看吧。",
                    tts_provider=None,
                    source="together_companion",
                    play_local=False,
                )
                await asyncio.sleep(0)

        self.assertTrue(result["available"])
        self.assertEqual("", result["reason"])
        self.assertTrue(result["audio_path"].endswith("together.wav"))
        self.assertEqual("一起看吧。", plugin.calls[0][0])
        harness._after_tts_audio_generated.assert_awaited_once()
        self.assertEqual(
            "together_companion",
            harness._after_tts_audio_generated.await_args.kwargs["source"],
        )
        self.assertFalse(
            harness._after_tts_audio_generated.await_args.kwargs["allow_local_playback"]
        )

    def test_missing_mimo_plugin_falls_back_to_astrbot_provider(self) -> None:
        provider = SimpleNamespace(name="official-tts")
        harness = _Harness(_Context(_ToolManager(), provider=provider))

        resolved = harness._resolve_tts_synthesis_provider(_Event(), provider)

        self.assertIs(provider, resolved)

    def test_auto_mode_probes_mimo_before_falling_back_to_astrbot_provider(self) -> None:
        provider = SimpleNamespace(name="official-tts")
        manager = _ToolManager()
        harness = _Harness(_Context(manager, provider=provider))
        harness.tts_synthesis_backend = "auto"

        resolved = harness._resolve_tts_synthesis_provider(_Event(), provider)

        self.assertIs(provider, resolved)
        self.assertEqual(["mimo_tts_speak"], manager.requested)

    def test_proactive_voice_availability_accepts_mimo_without_official_tts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _FakeMimoVoiceClonePlugin(Path(tmp) / "voice.wav")
            harness = _ActiveVoiceHarness(_Context(_ToolManager(plugin), provider=None))

            available = harness._voice_available({"umo": _Event.unified_msg_origin})

        self.assertTrue(available)


if __name__ == "__main__":
    unittest.main()
