"""Real Live/Game model call sites keep actual receipts on their own ledgers."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from tests import test_astrbot_staging_host as host_support
from astrbot.core.provider.entities import LLMResponse, TokenUsage
from astrbot_host.native_extensions.game.runtime import GameRuntime
from astrbot_host.native_extensions.live.bilibili_live import LiveDanmakuEvent
from astrbot_host.native_extensions.live.runtime import LiveRuntime


class NativeLiveGameUsageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="v2-live-game-usage-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.response = LLMResponse(role="assistant", completion_text="苹果",
                                    usage=TokenUsage(80, 20, 12))
        self.provider = NS(text_chat=AsyncMock(return_value=self.response),
                           meta=lambda: NS(id="actual-provider"))
        self.context = NS(get_using_provider=lambda *args: self.provider,
                          get_all_stars=lambda: (), send_message=AsyncMock())
        controller = NS(native_extensions=None, session=None, settings=None)
        self.live = LiveRuntime(self.context, {"auto_connect": False,
            "bili_live_auto_reply_mode": "direct", "bili_live_auto_reply_force_full_tts": False,
            "twitch_auto_reply_tts_enabled": False, "subtitle_enabled": False,
            "mouth_sync_enabled": False}, self.root / "extensions" / "live", controller)
        self.game = GameRuntime(self.context, {"server": {"enabled": False},
            "xiangqi": {"allow_engine_download": False}}, self.root / "extensions" / "game", controller)
        self.addAsyncCleanup(self.live.terminate)
        self.addAsyncCleanup(self.game.terminate)
        self.game._persona_prompt = AsyncMock(return_value="一起认真玩这一局")
        self.game._memory_context = AsyncMock(return_value="")
        self.room = NS(session_id="qq:FriendMessage:200", game_type="gomoku", game=object())

    async def test_bili_and_twitch_direct_replies_record_actual_usage_and_flush_on_stop(self):
        self.live._get_bili_reply_session = AsyncMock(return_value=self.room.session_id)
        self.live._get_twitch_reply_session = AsyncMock(return_value=self.room.session_id)
        self.live._should_reply_to_bili_live_events_by_air = AsyncMock(return_value=(True, "fixture"))
        bili = LiveDanmakuEvent("danmaku", "观众A", "下次想听夜航", raw={"uid": 42})
        twitch = LiveDanmakuEvent("danmaku", "观众B", "下次想听银河", raw={"platform": "twitch", "user_id": "42"})
        await self.live._reply_to_bili_live_events([bili])
        self.assertTrue(await self.live._reply_to_twitch_live_events([twitch]))
        self.assertEqual(self.provider.text_chat.await_count, 2)
        self.assertEqual(self.context.send_message.await_count, 2)
        summary = self.live.token_usage.summary()
        self.assertEqual(summary["totals"]["reported_tokens"], 224)
        self.assertEqual(set(summary["by_task"]), {"live_bili_reply", "live_twitch_reply"})
        self.assertEqual(summary["by_provider"]["actual-provider"]["calls"], 2)
        self.assertEqual(summary["totals"]["cache_read_tokens"], 40)
        await self.live.terminate()
        saved = json.loads(self.live.token_usage.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["totals"]["calls"], 2)
        self.assertNotIn(bili.content, self.live.token_usage.path.read_text(encoding="utf-8"))

    async def test_all_three_game_model_paths_record_once_each(self):
        self.assertEqual(await self.game._generate_persona_text(self.room, "新一局开始"), "苹果")
        self.assertEqual(await self.game._call_room_model(self.room, system_prompt="游戏伙伴", prompt="给个线索", timeout=1), "苹果")
        self.assertEqual(await self.game._guess_drawing(self.room, NS(guesses=[]), "data:image/png;base64,fixture"), "苹果")
        self.assertEqual(self.provider.text_chat.await_count, 3)
        self.assertEqual(self.provider.text_chat.call_args.kwargs["image_urls"], ["data:image/png;base64,fixture"])
        summary = self.game.token_usage.summary()
        self.assertEqual(summary["totals"]["reported_tokens"], 336)
        self.assertEqual(set(summary["by_task"]), {"game_persona_reply", "game_room_model", "game_draw_guess"})
        self.assertEqual(summary["totals"]["cache_read_tokens"], 60)
        await self.game.terminate()
        saved = json.loads(self.game.token_usage.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["totals"]["calls"], 3)

    async def test_game_persona_prompt_uses_new_host_resolver_contract(self):
        del self.game._persona_prompt

        async def resolver(*, umo, platform_name):
            self.assertEqual((umo, platform_name), (self.room.session_id, "qq"))
            return "current", {"name": "current", "prompt": "当前人格提示词"}, False

        self.context.persona_manager = NS(
            resolve_selected_persona=resolver,
            acm=NS(get_conf=lambda _umo: {}),
            get_default_persona_v3=AsyncMock(side_effect=AssertionError("unexpected fallback")),
        )
        self.context.conversation_manager = NS(
            get_curr_conversation_id=AsyncMock(return_value=None),
            get_conversation=AsyncMock(side_effect=AssertionError("unexpected conversation lookup")),
        )

        self.assertEqual(await self.game._persona_prompt(self.room), "当前人格提示词")

    async def test_failed_or_unreported_game_attempts_stay_unknown_without_replay(self):
        self.provider.text_chat.side_effect = TimeoutError("synthetic provider timeout")
        with self.assertRaises(RuntimeError):
            await self.game._call_room_model(self.room, system_prompt="游戏伙伴", prompt="给个线索", timeout=1)
        self.provider.text_chat.assert_awaited_once()
        failed = self.game.token_usage.summary()["recent"][-1]
        self.assertFalse(failed["success"])
        self.assertEqual(failed["error"], "TimeoutError")
        self.assertIsNone(failed["reported_tokens"])
        self.provider.text_chat.side_effect = None
        self.provider.text_chat.return_value = LLMResponse(role="assistant", completion_text="继续一起猜", usage=TokenUsage())
        await self.game._call_room_model(self.room, system_prompt="游戏伙伴", prompt="给个线索", timeout=1)
        summary = self.game.token_usage.summary()
        self.assertEqual(summary["totals"]["calls"], 2)
        self.assertEqual(summary["totals"]["unconfirmed_calls"], 2)
        self.assertIsNone(summary["totals"]["total_tokens"])
        self.assertIsNone(summary["recent"][-1]["cached_tokens"])

    async def test_cancelled_model_call_records_one_unknown_attempt(self):
        entered = asyncio.Event()
        async def pending(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        self.provider.text_chat.side_effect = pending
        operation = asyncio.create_task(self.game._generate_persona_text(self.room, "开场"))
        await entered.wait()
        operation.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await operation
        self.provider.text_chat.assert_awaited_once()
        summary = self.game.token_usage.summary()
        self.assertEqual(summary["totals"]["calls"], 1)
        self.assertEqual(summary["recent"][-1]["error"], "cancelled")
        self.assertIsNone(summary["recent"][-1]["total_tokens"])


if __name__ == "__main__":
    unittest.main()
