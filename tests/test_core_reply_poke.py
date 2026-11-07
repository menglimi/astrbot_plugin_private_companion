"""按需戳一戳：一次明确的平台动作，不建回复批次，也不猜同名。"""

from types import SimpleNamespace as NS
import unittest

from astrbot_host.core_reply import prepare_core_reply_event
from astrbot_host.core_reply_tools import inject_core_reply_tools
from companion_v2.execution.models import require
from tests.group_host_support import GroupFixture
from tests.multi_host_support import MultiFixture
from tests.test_core_reply_owner_tools import NativeOwnerCalls


class PokePlatform:
    def __init__(self):
        self.calls = []
        self.error = None
        self.members = [
            {"user_id": 200, "nickname": "比折", "card": "比折"},
            {"user_id": 202, "nickname": "小白金", "card": "小白金"},
            {"user_id": 203, "nickname": "小白金二号", "card": "小白金二号"},
        ]

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if self.error:
            raise self.error
        if action == "get_group_member_list":
            return self.members
        return {"message_id": 1}


class CoreReplyGroupPokeTests(NativeOwnerCalls, GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.poke_platform = PokePlatform()
        self.app = await self.start_host()

    def event_with_platform(self, **kwargs):
        event = self.group_event(**kwargs)
        event.message_obj.message.insert(0, NS(type="At", qq="100"))
        event.bot.call_action = self.poke_platform.call_action
        return event

    async def bind_group(self, event):
        candidate = (await self.controller.group_reply_candidate(event)).unwrap()
        prepared = require(await self.controller.prepare_core_reply(event, group_admission=candidate))
        prepare_core_reply_event(event, prepared,
            authorize=lambda value: self.controller.authorize_core_reply(event, value))
        request = NS(func_tool=None, system_prompt="原人格")
        inject_core_reply_tools(event, request, application=self.app)
        return prepared, request

    async def test_group_poke_resolves_card_and_calls_the_platform_once(self):
        event = self.event_with_platform(text="去摸摸小白金")
        _, request = await self.bind_group(event)
        result = await self.call(event, request, "interaction.poke", {"target": "小白金"})
        self.assertEqual(result["state"], "succeeded", result)
        self.assertEqual(self.poke_platform.calls[0], ("get_group_member_list", {"group_id": 300}))
        self.assertEqual(self.poke_platform.calls[1], ("group_poke", {"user_id": 202, "group_id": 300}))
        again = await self.call(event, request, "interaction.poke", {"target": "小白金"})
        self.assertEqual((again["state"], again["code"]), ("failed", "interaction_poke_repeated"), again)
        self.assertEqual([action for action, _ in self.poke_platform.calls].count("group_poke"), 1,
                         "重复调用不应再戳一次")

    async def test_ambiguous_name_requires_a_qq_instead_of_guessing(self):
        self.poke_platform.members.append({"user_id": 204, "nickname": "小白金", "card": "小白金"})
        event = self.event_with_platform(text="去摸摸小白金")
        _, request = await self.bind_group(event)
        result = await self.call(event, request, "interaction.poke", {"target": "小白金"})
        self.assertEqual((result["state"], result["code"]), ("failed", "interaction_target_ambiguous"), result)
        self.assertEqual([action for action, _ in self.poke_platform.calls], ["get_group_member_list"])

    async def test_unknown_member_is_not_poked_and_unknown_result_is_not_retried(self):
        event = self.event_with_platform(text="去摸摸不存在的人")
        _, request = await self.bind_group(event)
        missing = await self.call(event, request, "interaction.poke", {"target": "不存在的人"})
        self.assertEqual(missing["code"], "interaction_target_not_found", missing)
        self.poke_platform.error = RuntimeError("socket closed")
        failed = await self.call(event, request, "interaction.poke", {"target": "202"})
        self.assertEqual(failed["code"], "interaction_poke_unknown", failed)
        self.poke_platform.error = None
        retried = await self.call(event, request, "interaction.poke", {"target": "202"})
        self.assertEqual((retried["state"], retried["code"]),
                         ("failed", "interaction_poke_unconfirmed"), retried)
        self.assertEqual([action for action, _ in self.poke_platform.calls],
                         ["get_group_member_list", "group_poke"])


class CoreReplyPrivatePokeTests(NativeOwnerCalls, MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.poke_platform = PokePlatform()
        self.app = await self.start_host()

    async def test_private_poke_uses_the_current_peer(self):
        event = self.event(identifier="poke-private")
        event.bot.call_action = self.poke_platform.call_action
        _, request = await self.bind(event)
        result = await self.call(event, request, "interaction.poke", {})
        self.assertEqual(result["state"], "succeeded", result)
        self.assertEqual(self.poke_platform.calls[0][0], "friend_poke")
        self.assertEqual(self.poke_platform.calls[0][1], {"user_id": 200})


if __name__ == "__main__":
    unittest.main()
