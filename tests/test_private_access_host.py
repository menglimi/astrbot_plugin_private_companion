"""没有对话权限时的行为：不进对话，并按配置回一句。

锁四件事：
1. 被拒的消息**不进入对话**（没有 batch、没有模型调用），失败码是明确的
   `private_access_dialogue_denied`，而不是授权层的原始故障码；
2. 那一句话通过平台反向 WebSocket 直发（系统行为，不经过角色、不读记忆）；
3. 冷却期内不重复说同一句话，冷却为 0 时每句都回；
4. 文案留空保持安静；旧 `delivery_mode=preview` 只兼容读取，正式运行仍真实投递。
"""

import unittest

from companion_v2.relationships.private_access import DENIED_REPLY
from tests.multi_host_support import MultiFixture, multi_config


class SendingFixture(MultiFixture):
    """多目标夹具 + 真实投递模式（旧 preview 字段也归一为真实投递）。"""

    def setUp(self):
        MultiFixture.setUp(self)
        # multi_config(**changes) 是把它给的键并进 multi_host 段，所以直接给 delivery_mode。
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        for platform in self.native.platforms.values():
            platform.client._api._wsr_api.calls.clear()

    def event(self, *, identifier="event-1", target="first"):
        configured = self.controller.settings.target(target)
        event = MultiFixture.event(self, target=target, identifier=identifier)
        # 生产事件带 raw_message：礼拒要按它认证账号、平台、客户端与会话来源。
        # 账号与对方都取该目标自己的配置——写死成第一个目标的，路由就认不出来了。
        login, peer = int(configured.account_id), int(configured.participant_id)
        event.message_obj.raw_message = {
            "post_type": "message", "message_type": "private", "sub_type": "friend",
            "self_id": login, "user_id": peer, "message_id": identifier,
            "sender": {"user_id": peer}, "message": [{"type": "text", "data": {"text": "在吗"}}]}
        return event

    def records(self):
        """所有平台反向 WebSocket 客户端记下的 (action, params)。

        夹具里每个平台各有自己的客户端——只看第一个平台，发给第二个人的那句就看不见。
        """
        calls = []
        for platform in self.native.platforms.values():
            calls.extend(platform.client._api._wsr_api.calls)
        return calls

    def sent_texts(self):
        return [params.get("message") for action, params in self.records()
                if action == "send_private_msg"]


class PrivateAccessNoticeTests(SendingFixture, unittest.IsolatedAsyncioTestCase):
    async def host(self):
        app = await self.start_host()
        context = self.controller.authority.context("first", "role-a")
        return app, context

    def revoke_dialogue(self, context):
        identity = self.controller.authority.identities
        identity.revoke_grant(self.controller.authority.grant_id(context.scope, "conversation.reply"),
                              revoked_at=self.clock.now(), expected_revision=identity.revision)

    async def test_denied_dialogue_permission_refuses_and_sends_the_configured_line(self):
        app, context = await self.host()
        self.revoke_dialogue(context)

        result = await self.controller.receive_claimed(self.event())

        self.assertEqual(result.failure.code, "private_access_dialogue_denied")
        # 不进对话：没有批次，也没有模型调用。
        self.assertEqual(len(app.runtime.read_all("SELECT * FROM conversation_batches", ())), 0)
        self.assertEqual(self.native.provider.client.state.calls, [])
        # 但配置里那一句确实发出去了，而且只发一次。
        self.assertEqual(len(self.sent_texts()), 1)
        self.assertEqual(self.sent_texts()[0][0]["data"]["text"], DENIED_REPLY)
        # 冷却从那一次开始记账。
        document = app.private_notice.document(context)
        self.assertIsNotNone(document)
        self.assertIsNotNone(document["last_notice_at"])

    async def test_group_temporary_session_is_refused_as_a_private_message(self):
        app, context = await self.host()
        self.revoke_dialogue(context)
        event = self.event(identifier="temp-1")
        event.message_obj.raw_message.update(sub_type="group", group_id=300)

        result = await self.controller.receive_claimed(event)

        self.assertEqual(result.failure.code, "private_access_dialogue_denied")
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(len(self.sent_texts()), 1)
        sent = [params for action, params in self.records() if action == "send_private_msg"][0]
        self.assertEqual(sent["user_id"], int(self.controller.settings.target("first").participant_id))
        self.assertEqual(sent["group_id"], 300)

    async def test_cooldown_keeps_the_second_message_silent(self):
        _app, context = await self.host()
        self.revoke_dialogue(context)

        first = await self.controller.receive_claimed(self.event(identifier="first"))
        second = await self.controller.receive_claimed(self.event(identifier="second"))

        self.assertEqual(first.failure.code, "private_access_dialogue_denied")
        self.assertEqual(second.failure.code, "private_access_dialogue_denied")
        self.assertEqual(len(self.sent_texts()), 1, "冷却期内不该把同一句话再说一遍")

    async def test_zero_cooldown_sends_it_on_every_message(self):
        app, context = await self.host()
        self.revoke_dialogue(context)
        app.private_notice.settings = _Stub(denied_reply=DENIED_REPLY, notice_cooldown_seconds=0)

        await self.controller.receive_claimed(self.event(identifier="first"))
        await self.controller.receive_claimed(self.event(identifier="second"))

        self.assertEqual(len(self.sent_texts()), 2)

    async def test_blank_line_refuses_without_saying_anything(self):
        app, context = await self.host()
        self.revoke_dialogue(context)
        app.private_notice.settings = _Stub(denied_reply="   ", notice_cooldown_seconds=0)

        result = await self.controller.receive_claimed(self.event())

        self.assertEqual(result.failure.code, "private_access_dialogue_denied")
        self.assertEqual(self.sent_texts(), [])

    async def test_legacy_preview_mode_still_sends_the_line(self):
        """旧 preview 值仍可读取，但不再把正式投递降级成草稿。"""
        self.controller = self.make_controller(multi_config(delivery_mode="preview"))
        for platform in self.native.platforms.values():
            platform.client._api._wsr_api.calls.clear()
        _app = await self.start_host()
        context = self.controller.authority.context("first", "role-a")
        self.revoke_dialogue(context)

        result = await self.controller.receive_claimed(self.event())

        self.assertEqual(result.failure.code, "private_access_dialogue_denied")
        self.assertEqual(self.controller.settings.common.delivery_mode, "onebot_private")
        self.assertEqual(len(self.sent_texts()), 1)
        self.assertEqual(self.sent_texts()[0][0]["data"]["text"], DENIED_REPLY)

    async def test_allowed_dialogue_still_flows_into_a_conversation(self):
        _app, _context = await self.host()

        result = await self.controller.receive_claimed(self.event())

        self.assertTrue(result.is_success, result.failure)
        self.assertEqual(self.sent_texts(), [], "有权限的人不该收到拒绝文案")

    async def test_permission_stays_denied_after_the_notice(self):
        """回一句不等于把权限还给他：grant 仍然是收回去的状态。"""
        _app, context = await self.host()
        self.revoke_dialogue(context)

        await self.controller.receive_claimed(self.event())

        identity = self.controller.authority.identities
        access = identity.authorize(context.actor, scope=context.scope, capability="conversation.reply",
                                    observed_at=self.clock.now())
        self.assertEqual(access.failure.code, "authorization_revoked")


class ConfiguredDefaultTests(SendingFixture, unittest.IsolatedAsyncioTestCase):
    """配置里的默认值必须真的决定"新用户一开始有没有权限"。

    这一层是"默认新用户对话权限开启、主动权限关闭，可以进行配置"的落点：
    默认关的能力**不签发** grant（没有授权就是没有权限），默认开的能力才签发。
    """

    def set_defaults(self, app, **changes):
        """用与页面同一条路径（configuration.patch）改默认值。

        操作者用 dashboard 身份（那是唯一有 configuration.write 的主体）；写在**安装层**，
        因为夹具里两个目标分属不同角色，只改一个角色测不出"默认值对新人的作用"。
        """
        from astrbot_host.management_identity import dashboard_operator

        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.foundation.refs import OperationRef, VersionRef
        from companion_v2.relationships.private_access import GROUP

        target = ConfigTarget(app.runtime.installation_id, ConfigLayer.INSTALLATION)
        operator = dashboard_operator(app.runtime, "tester", at=self.clock.now())
        snapshot = app.configuration.read(operator, target=target, group_id=GROUP,
                                         observed_at=self.clock.now()).unwrap()
        identifier = "private-access-defaults-" + ",".join(sorted(changes))
        # operation 的 owner 必须与该组字段声明的 owner 一致（这一组跟随默认 owner）。
        owner = app.configuration.registry.group(GROUP)[0].owner_id
        return app.configuration.patch(
            operator, target=target, group_id=GROUP, expected_edit_token=snapshot.edit_token,
            changes={GROUP + "." + key: value for key, value in changes.items()},
            operation=OperationRef(identifier, owner, target.authorization_scope,
                                   VersionRef(identifier, 1), identifier),
            observed_at=self.clock.now()).unwrap()

    async def test_dialogue_default_off_is_not_undone_by_the_next_message(self):
        """默认值改成"关"之后，供给不该把对话权限又补回来。

        顺序刻意如此：先让这个人正常出现（默认开），再关掉默认值并收回权限——
        这时"下一条消息会不会又把他放进来"才是真正要测的东西。
        """
        app = await self.start_host()
        first = await self.controller.receive_claimed(self.event(target="second"))
        self.assertTrue(first.is_success, first.failure)

        second = self.controller.authority.context("second", "role-a")
        identity = self.controller.authority.identities
        identity.revoke_grant(self.controller.authority.grant_id(second.scope, "conversation.reply"),
                              revoked_at=self.clock.now(), expected_revision=identity.revision)
        self.set_defaults(app, dialogue_default=False)

        result = await self.controller.receive_claimed(self.event(identifier="after-default-off", target="second"))

        self.assertEqual(result.failure.code, "private_access_dialogue_denied")
        self.assertEqual(len(self.sent_texts()), 1, "拒绝时仍然按配置回一句")
        grants = [grant.capability for grant in app.tasks.identity.snapshot().grants
                  if grant.capability == "conversation.reply"
                  and grant.scope == second.scope and grant.state.value == "active"]
        self.assertEqual(grants, [], "默认关的能力不该被供给重新签发")

    async def test_proactive_default_on_grants_the_new_person(self):
        app = await self.start_host()
        self.set_defaults(app, proactive_default=True)

        result = await self.controller.receive_claimed(self.event(target="second"))

        self.assertTrue(result.is_success, result.failure)
        second = self.controller.authority.context("second", "role-a")
        self.assertFalse(app.proactive.proactive_denied(second, at=self.clock.now()),
                         "默认打开主动权限后，新用户应当直接被授权")


class _Stub:
    def __init__(self, **values):
        self.values = values

    def defaults(self, context):
        return dict(self.values)
