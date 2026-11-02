"""陌生首触：不属于任何目标的私聊账号也会被婉拒一句。

这条路与"已绑定但被收回权限"的礼拒是两回事：陌生号没有身份、没有窗口、没有 grant，
所以这里锁的是**边界**，不是功能多少：

1. 只对本插件拥有的账号发（别的平台/别的号一律不发）；
2. 只发配置里那一行，且不建身份、不写对话表、不签发 grant；
3. 同一账号冷却期内只回一次；每天总量不超过上限；
4. 开关关掉或文案留空时一个字节都不出网；旧 `delivery_mode=preview` 仍按正式合同投递；
5. 真发了才关闭默认模型回复，其他插件仍能检测消息。
"""

import json
import unittest

from companion_v2.relationships.private_access import DENIED_REPLY
from tests.multi_host_support import MultiFixture, multi_config


class StrangerFixture(MultiFixture):
    """多目标夹具 + 真实投递模式；`stranger` 是一个没被配置进来的账号。"""

    def setUp(self):
        MultiFixture.setUp(self)
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        for platform in self.native.platforms.values():
            platform.client._api._wsr_api.calls.clear()

    def event(self, *, identifier="stranger-1", sender="999", login=None, platform_id="qq-main",
              post_type="message"):
        """一条来自"没被配置进来"的账号的私聊事件。

        直接用 FakeEvent 构造（夹具的 event() 会把发送者钉成目标本人，
        那正是我们要避开的"已配置"情形）。
        """
        from types import SimpleNamespace as NS

        from tests.native_adapter_support import FakeEvent

        configured = self.controller.settings.target("first")
        login = login or configured.account_id
        platform = self.native.platforms[platform_id]
        event = FakeEvent(NS(platform=platform), event_id=identifier, sender=sender, login=login, text="在吗")
        event.created_at = self.clock.now().timestamp()
        event.message_obj.raw_message = {
            "post_type": post_type, "message_type": "private", "sub_type": "friend",
            "self_id": int(login), "user_id": int(sender), "message_id": identifier,
            "sender": {"user_id": int(sender)}, "message": [{"type": "text", "data": {"text": "在吗"}}]}
        event.stopped = False
        event.stop_event = lambda: setattr(event, "stopped", True)
        return event

    def records(self):
        calls = []
        for platform in self.native.platforms.values():
            calls.extend(platform.client._api._wsr_api.calls)
        return calls

    def sent_texts(self):
        return [params.get("message") for action, params in self.records() if action == "send_private_msg"]


class StrangerNoticeTests(StrangerFixture, unittest.IsolatedAsyncioTestCase):
    async def handle(self, event):
        from astrbot_host.stranger_notice import refuse_stranger

        return await refuse_stranger(self.controller, event)

    def set_config(self, app, **changes):
        from astrbot_host.management_identity import dashboard_operator

        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.foundation.refs import OperationRef, VersionRef
        from companion_v2.relationships.private_access import GROUP

        target = ConfigTarget(app.runtime.installation_id, ConfigLayer.INSTALLATION)
        operator = dashboard_operator(app.runtime, "tester", at=self.clock.now())
        snapshot = app.configuration.read(operator, target=target, group_id=GROUP,
                                         observed_at=self.clock.now()).unwrap()
        identifier = "stranger-notice-" + ",".join(sorted(changes))
        owner = app.configuration.registry.group(GROUP)[0].owner_id
        return app.configuration.patch(
            operator, target=target, group_id=GROUP, expected_edit_token=snapshot.edit_token,
            changes={GROUP + "." + key: value for key, value in changes.items()},
            operation=OperationRef(identifier, owner, target.authorization_scope,
                                   VersionRef(identifier, 1), identifier),
            observed_at=self.clock.now()).unwrap()

    async def test_a_stranger_gets_exactly_one_configured_line(self):
        app = await self.start_host()
        grants_before = len(app.tasks.identity.snapshot().grants)

        receipt = await self.handle(self.event())

        self.assertIsNotNone(receipt)
        self.assertEqual(len(self.sent_texts()), 1)
        self.assertEqual(self.sent_texts()[0][0]["data"]["text"], DENIED_REPLY)
        # 不建身份、不写对话表、不签发 grant。
        self.assertEqual(len(app.runtime.read_all("SELECT * FROM conversation_batches", ())), 0)
        self.assertEqual(len(app.runtime.read_all("SELECT * FROM conversation_inputs", ())), 0)
        self.assertEqual(len(app.runtime.read_all("SELECT * FROM identity_bindings WHERE native_account_id = ?",
                                                  ("999",))), 0, "陌生号不该被写进身份表")
        self.assertEqual(len(app.tasks.identity.snapshot().grants), grants_before,
                         "婉拒陌生人不该签发任何授权")
        self.assertEqual(receipt["count_today"], 1)

    async def test_cooldown_and_daily_cap_both_hold(self):
        app = await self.start_host()

        first = await self.handle(self.event(identifier="one"))
        again = await self.handle(self.event(identifier="two"))
        other = await self.handle(self.event(identifier="three", sender="998"))

        self.assertIsNotNone(first)
        self.assertIsNone(again, "冷却期内不该再回同一个账号")
        self.assertIsNotNone(other, "换一个陌生号是另一段冷却")
        self.assertEqual(len(self.sent_texts()), 2)
        self.assertEqual(other["count_today"], 2)
        # 每日上限收到 2 之后，第三个人也不该再发。
        self.set_config(app, stranger_notice_daily_cap=2)
        self.assertIsNone(await self.handle(self.event(identifier="four", sender="997")))
        self.assertEqual(len(self.sent_texts()), 2)

    async def test_switch_off_and_blank_line_send_nothing(self):
        app = await self.start_host()

        self.set_config(app, stranger_notice_enabled=False)
        self.assertIsNone(await self.handle(self.event(identifier="off")))
        self.set_config(app, stranger_notice_enabled=True, denied_reply="   ")
        self.assertIsNone(await self.handle(self.event(identifier="blank", sender="996")))
        self.assertEqual(self.sent_texts(), [])

    async def test_another_platform_or_account_is_never_answered(self):
        await self.start_host()

        # 没被配置进来的账号（不是本插件的号）→ 不发。
        self.assertIsNone(await self.handle(self.event(identifier="foreign", login="555")))
        self.assertEqual(self.sent_texts(), [])

    async def test_legacy_preview_mode_still_sends(self):
        """旧 preview 值只为兼容读取，陌生首触仍按正式合同发送。"""
        self.controller = self.make_controller(multi_config(delivery_mode="preview"))
        for platform in self.native.platforms.values():
            platform.client._api._wsr_api.calls.clear()
        await self.start_host()

        receipt = await self.handle(self.event())
        self.assertIsNotNone(receipt)
        self.assertEqual(self.controller.settings.common.delivery_mode, "onebot_private")
        self.assertEqual(len(self.sent_texts()), 1)
        self.assertEqual(self.sent_texts()[0][0]["data"]["text"], DENIED_REPLY)

    async def test_a_configured_person_is_not_treated_as_a_stranger(self):
        """已配置的人走正常入站（包括"没有权限时的那一句"），不该被这条路抢走。"""
        await self.start_host()

        self.assertIsNone(await self.handle(self.event(sender="200")),
                          "目标自己的账号不是陌生号")
        self.assertEqual(self.sent_texts(), [])

    async def test_group_and_non_message_events_are_never_answered(self):
        """只处理私聊消息：群消息与通知事件都不归这条路管。"""
        await self.start_host()

        group = self.event(identifier="group-1")
        group.message_obj.raw_message["message_type"] = "group"
        group.message_obj.raw_message["group_id"] = 300
        notice = self.event(identifier="notice-1", post_type="notice")

        self.assertIsNone(await self.handle(group))
        self.assertIsNone(await self.handle(notice))
        self.assertEqual(self.sent_texts(), [])

    async def test_every_stranger_attempt_is_visible_even_when_nothing_is_sent(self):
        """被拒也是发生过的私聊：开关关掉、冷却拦住时仍要留下可解释的记录。"""
        from astrbot_host.stranger_notice import CONTACT_KIND

        app = await self.start_host()

        await self.handle(self.event(identifier="first", sender="991"))
        await self.handle(self.event(identifier="second", sender="991"))
        self.set_config(app, stranger_notice_enabled=False)
        await self.handle(self.event(identifier="third", sender="992"))
        # 已配置的人与别的号不属于这条边界，不该被记进被拒名单。
        await self.handle(self.event(identifier="configured", sender="200"))
        await self.handle(self.event(identifier="foreign", login="555", sender="993"))

        rows = app.runtime.read_all(
            "SELECT document_json FROM memory_views WHERE kind = ?", (CONTACT_KIND,))
        documents = {json.loads(row["document_json"])["peer"]: json.loads(row["document_json"])
                     for row in rows}
        self.assertEqual(set(documents), {"991", "992"})
        self.assertEqual(documents["991"]["attempts"], 2)
        self.assertEqual(documents["991"]["last_outcome"], "stranger_notice_cooldown")
        self.assertEqual(documents["992"]["last_outcome"], "stranger_notice_disabled")
