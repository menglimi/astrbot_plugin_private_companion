from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_private_companion.memory_companion_adapter import MemoryCompanionAdapterMixin
from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class _Bridge:
    def __init__(
        self,
        *,
        fast_supported: bool,
        outfit_fast_supported: bool = False,
        response: str = "<MemoryCompanion-Context>schedule</MemoryCompanion-Context>",
    ) -> None:
        self.fast_supported = fast_supported
        self.outfit_fast_supported = outfit_fast_supported
        self.response = response
        self.calls: list[dict] = []
        self.record_calls: list[dict] = []

    def coordination_status(self) -> dict:
        return {
            "available": True,
            "schedule_fast_context": self.fast_supported,
            "outfit_fast_context": self.outfit_fast_supported,
        }

    async def compose_context(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self.response

    async def record_persona_life(self, **kwargs) -> str:
        self.record_calls.append(kwargs)
        return "dream-memory"


class _Harness(MemoryCompanionAdapterMixin, DailyStateMixin):
    enable_livingmemory_integration = True
    memory_companion_context_timeout_seconds = 1.2
    schedule_persona_prompt = "高一学生"
    schedule_worldview_prompt = "现代校园"

    def __init__(self, bridge: _Bridge) -> None:
        self.bridge = bridge

    def _memory_companion_bridge(self):
        return self.bridge

    _looks_like_internal_provider_error_text = staticmethod(
        ProactiveMessageMixin._looks_like_internal_provider_error_text
    )

    def _memory_companion_schedule_owner_context(self):
        return "u1", {"umo": "qq:FriendMessage:u1", "nickname": "user"}

    @staticmethod
    def _known_bot_self_ids() -> set[str]:
        return {"b1"}

    @staticmethod
    def _environment_now() -> datetime:
        return datetime(2026, 7, 14, 20, 12)

    @staticmethod
    def _memory_companion_bot_emotional_state() -> tuple[str, float]:
        return "neutral", 60.0


class MemoryCompanionScheduleFastContextTests(unittest.IsolatedAsyncioTestCase):
    def test_reply_context_source_contains_actor_binding_boundary(self) -> None:
        source = (ROOT / "forward_message.py").read_text(encoding="utf-8")

        self.assertIn("人物与动作优先逐项对应", source)
        self.assertIn("区分当前发言、引用作者和被提到的第三方", source)
        self.assertIn("旧记忆用于补充语气和连续性", source)

    async def test_schedule_requests_fast_profile_when_bridge_supports_it(self) -> None:
        bridge = _Bridge(fast_supported=True)
        harness = _Harness(bridge)

        result = await harness._memory_companion_compose_schedule_context(kind="detail")

        self.assertIn("schedule", result)
        self.assertEqual("schedule_fast", bridge.calls[0]["retrieval_profile"])
        self.assertEqual("b1", bridge.calls[0]["session_context"]["bot_id"])
        self.assertEqual("user", bridge.calls[0]["session_context"]["preferred_address"])
        self.assertTrue(bridge.calls[0]["session_context"]["preferred_address_locked"])

    async def test_schedule_remains_compatible_with_older_bridge(self) -> None:
        bridge = _Bridge(fast_supported=False)
        harness = _Harness(bridge)

        result = await harness._memory_companion_compose_schedule_context(kind="daily_plan")

        self.assertIn("schedule", result)
        self.assertNotIn("retrieval_profile", bridge.calls[0])

    async def test_schedule_memory_does_not_establish_an_unverified_mother(self) -> None:
        bridge = _Bridge(
            fast_supported=True,
            response="昨晚妈妈炖了汤，后来继续看书。",
        )
        harness = _Harness(bridge)

        result = await harness._memory_companion_compose_schedule_context(kind="daily_plan")

        self.assertNotIn("妈妈", result)
        self.assertIn("后来继续看书", result)

    async def test_daily_outfit_requests_fast_profile_with_owner_context(self) -> None:
        bridge = _Bridge(fast_supported=True, outfit_fast_supported=True)
        harness = _Harness(bridge)

        result = await harness._memory_companion_compose_feature_context(
            kind="daily_outfit_photo",
            query="今日穿搭、历史穿搭、服装偏好和最近自拍",
        )

        self.assertIn("schedule", result)
        self.assertEqual("outfit_fast", bridge.calls[0]["retrieval_profile"])
        self.assertEqual("u1", bridge.calls[0]["session_context"]["user_id"])
        self.assertEqual("private", bridge.calls[0]["session_context"]["scope"])
        self.assertEqual("b1", bridge.calls[0]["session_context"]["bot_id"])
        self.assertEqual("user", bridge.calls[0]["session_context"]["preferred_address"])
        self.assertTrue(bridge.calls[0]["session_context"]["preferred_address_locked"])

    async def test_dream_fragment_is_written_as_bot_self_not_private_user(self) -> None:
        bridge = _Bridge(fast_supported=True)
        harness = _Harness(bridge)

        await harness._memory_companion_record_dream_fragment(
            content="梦见窗外落下一颗很亮的星星",
            mood="平静",
            dream_type="日常梦",
            user_id="u1",
        )

        self.assertEqual(1, len(bridge.record_calls))
        payload = bridge.record_calls[0]
        self.assertEqual("unknown", payload["scope"])
        self.assertEqual("private_companion:dream", payload["session_id"])
        self.assertIn("dream_fragment", payload["tags"])

    async def test_ambiguous_bot_identity_is_not_guessed(self) -> None:
        bridge = _Bridge(fast_supported=True)
        harness = _Harness(bridge)
        harness._known_bot_self_ids = lambda: {"b1", "b2"}

        await harness._memory_companion_compose_schedule_context(kind="detail")

        self.assertEqual("", bridge.calls[0]["session_context"]["bot_id"])

    async def test_daily_outfit_remains_compatible_with_older_bridge(self) -> None:
        bridge = _Bridge(fast_supported=True, outfit_fast_supported=False)
        harness = _Harness(bridge)

        result = await harness._memory_companion_compose_feature_context(
            kind="daily_outfit_photo",
            query="今日穿搭",
        )

        self.assertIn("schedule", result)
        self.assertNotIn("retrieval_profile", bridge.calls[0])

    async def test_daily_diary_uses_owner_context_and_filters_unverified_relationships(self) -> None:
        bridge = _Bridge(
            fast_supported=True,
            response="昨天和主要用户聊完那本书后，妈妈又端来一杯茶。",
        )
        harness = _Harness(bridge)

        result = await harness._memory_companion_compose_feature_context(
            kind="daily_diary",
            query="每日日记连续性、主要用户共同经历和未完成心事",
        )

        session_context = bridge.calls[0]["session_context"]
        self.assertEqual("u1", session_context["user_id"])
        self.assertEqual("private", session_context["scope"])
        self.assertEqual("user", session_context["preferred_address"])
        self.assertIn("主要用户聊完那本书", result)
        self.assertNotIn("妈妈", result)

    async def test_generation_feature_memory_filters_unverified_relationships(self) -> None:
        bridge = _Bridge(
            fast_supported=True,
            response="桌边是妈妈洗的青提，穿着蓝色外套。",
        )
        harness = _Harness(bridge)
        user_id, user = harness._memory_companion_schedule_owner_context()

        result = await harness._memory_companion_compose_feature_context(
            kind="proactive_generation",
            query="当前日程和主动消息",
            user=user,
            user_id=user_id,
        )

        self.assertNotIn("妈妈", result)
        self.assertIn("穿着蓝色外套", result)

    async def test_generation_feature_memory_keeps_explicit_user_owned_relationship(self) -> None:
        bridge = _Bridge(
            fast_supported=True,
            response="用户：我妈妈最近有点忙，回应时只按用户家庭理解。",
        )
        harness = _Harness(bridge)
        user_id, user = harness._memory_companion_schedule_owner_context()

        result = await harness._memory_companion_compose_feature_context(
            kind="proactive_generation",
            query="当前日程和主动消息",
            user=user,
            user_id=user_id,
        )

        self.assertIn("我妈妈最近有点忙", result)

    async def test_generation_feature_memory_filters_provider_refusal_but_keeps_normal_context(self) -> None:
        bridge = _Bridge(
            fast_supported=True,
            response=(
                "[slot_memories]\n"
                "- content=Bot：刚才在厨房闻到蒸南瓜的甜香味。\n"
                "- content=Bot：The。 prompt。 could。not。be。submitted.。The。prompt。contains。"
                "sensitive。words。that。violate。Google's。Generative。AI。Prohibited。Use。policy。\n"
                "- content=Bot：晚饭后想去阳台吹一会儿风。"
            ),
        )
        harness = _Harness(bridge)
        user_id, user = harness._memory_companion_schedule_owner_context()

        result = await harness._memory_companion_compose_feature_context(
            kind="proactive_generation",
            query="当前日程和主动消息",
            user=user,
            user_id=user_id,
        )

        self.assertNotIn("prompt", result.lower())
        self.assertNotIn("Prohibited", result)
        self.assertIn("蒸南瓜的甜香味", result)
        self.assertIn("阳台吹一会儿风", result)


if __name__ == "__main__":
    unittest.main()
