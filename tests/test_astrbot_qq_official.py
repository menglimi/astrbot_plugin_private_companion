"""QQ Official account, route and AstrBot-session adaptation."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from astrbot.core.message.components import At, Plain
from astrbot_host.account_session_api import AccountSessionWebAPI
from astrbot_host.acquaintance_api import AcquaintanceWebAPI
from astrbot_host.management_identity import dashboard_operator
from astrbot_host.multi_controller import AstrBotMultiController
from astrbot_host.group_admission import group_trigger_facts
from astrbot_host.multi_settings import MultiHostSettings
from astrbot_host.session_topology import session_route
from companion_v2.adapters.astrbot.native import (
    HostAccount, HostPersona, NativePrivateBridge, event_account_id, event_peer_id,
    persona_instruction, route_id,
)
from companion_v2.adapters.astrbot.group import GroupEventRoute, NativeGroupBridge, group_event_route
from companion_v2.adapters.astrbot.platform_sender import AstrBotPlatformSender
from companion_v2.execution.models import TaskContext, digest
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal
from companion_v2.identity_policy.personas import PersonaBinding, PersonaSelector, SelectorKind
from companion_v2.memory.person.access import owner_key
from companion_v2.memory.person.admin_names import PersonAcquaintanceNameManagement
from tests.multi_host_support import MultiFixture, multi_config
from tests.group_host_support import Query
from tests.staging_bootstrap_support import wait_for_batch
from astrbot_host.group_settings import GroupTarget
from tests.native_adapter_support import NativeFixture
from tests.test_management_access import INSTALLATION, NOW, ROLE


OFFICIAL_OPENID = "5796478421661CC514BE6FB066620E53"
GROUP_OPENID = "0F3A7B_GROUP_OPENID"
ACCOUNT = "test"


class OfficialClient:
    def __init__(self):
        self.is_shutting_down = False

    def is_closed(self):
        return False


class OfficialPlatform:
    def __init__(self):
        self.metadata = NS(name="qq_official", id=ACCOUNT)
        self.client = OfficialClient()
        self.sent = []

    def meta(self):
        return self.metadata

    def get_client(self):
        return self.client

    async def send_by_session(self, session, chain):
        self.sent.append((session, chain))


class OfficialEvent:
    def __init__(self, platform, *, sender=OFFICIAL_OPENID, text="你好", group=False):
        self.bot = platform.client
        self.platform_meta = platform.meta()
        kind = NS(value="GroupMessage" if group else "FriendMessage")
        peer = GROUP_OPENID if group else ""
        self.message_obj = NS(
            type=kind,
            group_id=peer,
            group=NS(group_id=peer, group_name="官方测试群") if group else None,
            self_id="qq_official",
            sender=NS(user_id=sender, nickname="官方用户"),
            message_id="official-event-1",
            message=[At(qq="qq_official"), Plain(text=text)],
            session_id=peer if group else sender,
            raw_message=NS(content=text, id="official-event-1"),
        )
        self.session = NS(
            message_type=kind,
            platform_name=ACCOUNT,
            platform_id=ACCOUNT,
            session_id=peer if group else sender,
        )
        self.unified_msg_origin = (
            f"{ACCOUNT}:GroupMessage:{peer}" if group else f"{ACCOUNT}:FriendMessage:{sender}"
        )
        self.created_at = NOW.timestamp()
        self._extra = {}

    def get_extra(self, key, default=None):
        return self._extra.get(key, default)

    def set_extra(self, key, value):
        self._extra[key] = value


class OfficialFixture(NativeFixture):
    def setUp(self):
        super().setUp()
        self.native.platform = OfficialPlatform()
        self.native.platforms = {ACCOUNT: self.native.platform}
        self.account = HostAccount(ACCOUNT, ACCOUNT, "bot-test")
        self.persona = HostPersona(
            "bot-test", "original", ROLE, digest(persona_instruction(self.native.persona)))
        principal = AuthenticatedHostPrincipal(
            INSTALLATION, "host", ACCOUNT, OFFICIAL_OPENID, "bot-test", ACCOUNT)
        self.identity.bind(
            binding_id="official-native-user", principal=principal, subject=self.important.subject,
            valid_from=NOW, expected_revision=self.identity.revision)
        self.personas.set_binding(
            PersonaBinding("official-base", PersonaSelector(
                INSTALLATION, SelectorKind.BOT, bot_id="bot-test"),
                ROLE, 0, NOW, "fixture", "admin"),
            expected_revision=0, observed_at=NOW)
        self.bridge = NativePrivateBridge(
            self.native, version="4.27.2", event_type=OfficialEvent,
            installation_id=INSTALLATION, host_id="host", identities=self.identity,
            accounts=(self.account,), personas=(self.persona,),
            session_persona_override_lookup=self.official_persona,
        )

    async def official_persona(self, umo):
        return "original"

    def official_event(self, **kwargs):
        return OfficialEvent(self.native.platform, **kwargs)

    async def capture(self, event=None):
        event = event or self.official_event()
        captured = (await self.bridge.capture(event)).unwrap()
        actor = self.identity.resolve_authenticated(captured.principal, observed_at=NOW).unwrap()
        self.grant(actor, captured.route.scope, "text")
        return captured


class OfficialAdapterTests(OfficialFixture, unittest.IsolatedAsyncioTestCase):
    async def test_official_route_ids_and_session_directory(self):
        self.assertEqual(route_id(ACCOUNT), ACCOUNT)
        self.assertEqual(route_id(OFFICIAL_OPENID), OFFICIAL_OPENID)
        self.assertEqual(
            session_route(f"{ACCOUNT}:FriendMessage:{OFFICIAL_OPENID}"),
            (ACCOUNT, "private", OFFICIAL_OPENID))
        self.assertEqual(
            session_route(f"{ACCOUNT}:GroupMessage:{GROUP_OPENID}"),
            (ACCOUNT, "group", GROUP_OPENID))
        self.assertNotEqual(route_id("test-openid"), route_id("test-openid-2"))
        self.assertEqual(route_id("0721"), "0721")
        for invalid in ("0", "bad:id", " bad", "bad id"):
            with self.assertRaises(ValueError):
                route_id(invalid)

    async def test_numeric_official_instance_with_leading_zero_is_a_valid_account_key(self):
        platform = OfficialPlatform()
        platform.metadata.id = "0721"
        event = self.official_event()
        event.platform_meta = platform.meta()

        self.assertEqual(event_account_id(event), "0721")
        settings = MultiHostSettings.parse(multi_config(
            accounts=[{"platform_id": "0721", "account_id": "0721", "bot_id": "bot-test"}],
            targets=[{"target_id": "official-private", "platform_id": "0721",
                      "account_id": "0721", "bot_id": "bot-test", "participant_id": "*"}],
            personas=[{"bot_id": "bot-test", "persona_id": "星缘-私聊", "role_id": "星缘"}]))
        self.assertEqual(settings.accounts[0].self_id, "0721")

    async def test_official_role_mapping_accepts_opaque_accounts_and_peers(self):
        config = multi_config(
            accounts=[{"platform_id": ACCOUNT, "account_id": ACCOUNT, "bot_id": "bot-test"}],
            targets=[{"target_id": "official-private", "platform_id": ACCOUNT,
                      "account_id": ACCOUNT, "bot_id": "bot-test", "participant_id": "*"}],
            personas=[{"bot_id": "bot-test", "persona_id": "星缘-私聊", "role_id": "星缘"}],
            group_targets=[{"target_id": "official-group", "platform_id": ACCOUNT,
                            "account_id": ACCOUNT, "bot_id": "bot-test", "group_id": GROUP_OPENID}],
        )
        settings = MultiHostSettings.parse(config)
        self.assertEqual(settings.accounts[0].self_id, ACCOUNT)
        self.assertEqual(settings.targets[0].participant_id, "*")
        self.assertEqual(settings.group_targets[0].group_id, GROUP_OPENID)

    async def test_private_capture_uses_platform_instance_as_its_account_key(self):
        event = self.official_event(text="捏")
        self.assertEqual(event_account_id(event), ACCOUNT)
        self.assertEqual(event_peer_id(event), OFFICIAL_OPENID)
        captured = await self.capture(event)
        self.assertEqual(captured.principal.platform, ACCOUNT)
        self.assertEqual(captured.principal.account_id, ACCOUNT)
        self.assertEqual(captured.principal.native_account_id, OFFICIAL_OPENID)
        self.assertEqual(captured.route.scope.account_id, ACCOUNT)
        self.assertEqual(captured.route.scope.native_scope_id, OFFICIAL_OPENID)
        self.assertEqual(captured.text, "捏")

    async def test_official_group_route_and_bot_mention_are_normalized(self):
        event = self.official_event(group=True, text="大家好吗")
        route = group_event_route(event)
        self.assertEqual(route.platform_id, ACCOUNT)
        self.assertEqual(route.account_id, ACCOUNT)
        self.assertEqual(route.sender_id, OFFICIAL_OPENID)
        self.assertEqual(route.group_id, GROUP_OPENID)
        self.assertEqual(route.session_id, GROUP_OPENID)
        facts = group_trigger_facts(event, ACCOUNT)
        self.assertTrue(facts["mentioned"])

    async def test_official_group_text_capture_uses_platform_account_and_openids(self):
        member = "5A6B7C8D9E0F_MEMBER"
        principal = AuthenticatedHostPrincipal(
            INSTALLATION, "host", ACCOUNT, member, "bot-test", ACCOUNT)
        self.identity.bind(
            binding_id="official-group-member", principal=principal, subject=self.important.subject,
            valid_from=NOW, expected_revision=self.identity.revision)
        target = GroupTarget("official-group", ACCOUNT, ACCOUNT, "bot-test", GROUP_OPENID)
        bridge = NativeGroupBridge(
            self.native, version="4.27.2", event_type=OfficialEvent,
            installation_id=INSTALLATION, host_id="host", identities=self.identity,
            accounts=(self.account,), personas=(self.persona,), group_targets=(target,),
            session_persona_override_lookup=self.official_persona,
        )
        event = self.official_event(group=True, sender=member, text="群消息验证")
        captured = (await bridge.capture_text_only(event)).unwrap()
        self.assertEqual(captured.route.scope.account_id, ACCOUNT)
        self.assertEqual(captured.route.scope.native_scope_id, GROUP_OPENID)
        self.assertEqual(captured.route.scope.session_id, GROUP_OPENID)
        self.assertEqual(captured.principal.native_account_id, member)
        self.assertEqual(captured.text, "<提及自己>群消息验证")
        full = (await bridge.capture(event, allow_text_fallback=True)).unwrap()
        self.assertEqual(full.route.scope, captured.route.scope)
        self.assertEqual(full.principal, captured.principal)
        self.assertEqual(full.text, captured.text)

    async def test_platform_sender_uses_astrbot_session_transport(self):
        from companion_v2.delivery.models import DeliveryEnvelope
        captured = await self.capture()
        self.personas.register_route(captured.route, observed_at=NOW)
        role = self.personas.resolve(captured.route.scope, observed_at=NOW).unwrap().binding
        actor = self.identity.resolve_authenticated(captured.principal, observed_at=NOW).unwrap()
        context = TaskContext(actor, role, captured.recipients)
        delivery = DeliveryEnvelope(
            part_id="part-official", operation_id="op-official", context=context,
            sequence=1, text="官方回复", payload_hash=digest("官方回复"), sender=self.sender.binding,
            before_dispatch=lambda: True)
        sender = AstrBotPlatformSender(
            self.bridge, binding=self.sender.binding, version="4.27.2",
            reverse_api_type=object, authorize=lambda: None, now=self.clock.now)
        receipt = await sender.send(delivery)
        self.assertEqual(receipt.status, "delivered", receipt)
        self.assertEqual(len(self.native.platform.sent), 1)
        session, chain = self.native.platform.sent[0]
        self.assertEqual(str(session), f"{ACCOUNT}:FriendMessage:{OFFICIAL_OPENID}")
        self.assertEqual(chain.get_plain_text(), "官方回复")

    def test_production_native_types_use_the_generic_event_base(self):
        from astrbot.api.event import AstrMessageEvent
        from astrbot_host.native import installed_native_types
        self.assertIs(installed_native_types().event, AstrMessageEvent)

    async def test_dashboard_inline_role_can_register_an_official_account(self):
        config = multi_config()
        before = deepcopy(config)
        discovered = {
            "accounts": [{"platform_id": ACCOUNT, "account_id": ACCOUNT, "bot_id": "bot-test"}],
            "personas": [{"platform_id": ACCOUNT, "account_id": ACCOUNT,
                          "bot_id": "bot-test", "persona_id": "星缘-私聊"}],
            "targets": [],
        }

        async def apply(candidate, *, expected_config=None):
            self.assertEqual(expected_config, config)
            config.clear()
            config.update(candidate)
            return {"saved": True, "requires_reload": False, "requires_initialize": False}

        controller = NS(config=config, apply_bindings=AsyncMock(side_effect=apply))
        api = AccountSessionWebAPI(controller)
        api._discovered = AsyncMock(return_value=discovered)
        request = NS(username="admin", query={}, body=AsyncMock(return_value=json.dumps({
            "platform_id": ACCOUNT, "account_id": ACCOUNT, "bot_id": "bot-test",
            "persona_ids": ["星缘-私聊"], "role_id": "星缘",
        }, ensure_ascii=False).encode("utf-8")))
        with patch("astrbot_host.account_session_api.request", request):
            result = await api.add_role()
        self.assertEqual(result["status"], "ok", result)
        section = config["multi_host"]
        self.assertIn({"platform_id": ACCOUNT, "account_id": ACCOUNT, "bot_id": "bot-test"}, section["accounts"])
        self.assertIn({"bot_id": "bot-test", "persona_id": "星缘-私聊", "role_id": "星缘"}, section["personas"])
        official = next(row for row in section["targets"] if row["platform_id"] == ACCOUNT)
        self.assertEqual(official["participant_id"], "*")
        self.assertEqual(before["multi_host"]["targets"], [row for row in section["targets"]
                                                            if row["platform_id"] != ACCOUNT])


class OfficialMultiHostTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        official = OfficialPlatform()
        self.native.platform = official
        self.native.platforms = {ACCOUNT: official}
        self.native.selections = {ACCOUNT: "original"}
        self.native.conversations = {ACCOUNT: "conversation-official"}
        self.types = replace(self.types, event=OfficialEvent)
        self.window_personas = {f"{ACCOUNT}:FriendMessage:{OFFICIAL_OPENID}": "original"}
        self.config = multi_config(
            accounts=[{"platform_id": ACCOUNT, "account_id": ACCOUNT, "bot_id": "bot-test"}],
            targets=[{"target_id": "official-private", "platform_id": ACCOUNT,
                      "account_id": ACCOUNT, "bot_id": "bot-test",
                      "participant_id": OFFICIAL_OPENID}],
            personas=[{"bot_id": "bot-test", "persona_id": "original", "role_id": "role-a"}],
            group_targets=[],
        )
        self.controller = self.make_controller(self.config)

    async def test_original_mentions_reach_observation_provider_continuation_and_history(self):
        from astrbot.core.agent.message import Message, dump_messages_with_checkpoints
        from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
        from astrbot.core.provider.entities import ProviderRequest
        from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
        from astrbot_host.core_reply_perception import inject_core_reply_perception_context
        from astrbot_host.core_reply_decision import core_decision_facts
        from tests.test_group_mention_positions import official_message

        self.config["multi_host"]["group_targets"] = [dict(target_id="official-group",
            platform_id=ACCOUNT, account_id=ACCOUNT, bot_id="bot-test", group_id=GROUP_OPENID)]
        self.window_personas[f"{ACCOUNT}:GroupMessage:{GROUP_OPENID}"] = "original"
        self.controller = self.make_controller(self.config)
        app = await self.start_host()
        source = "介绍一下你们的项目，让 <@BOT> 了解一下 <@OTHER>"
        canonical = source.replace("<@BOT>", "<提及自己>")
        parsed = await official_message(source)
        event = OfficialEvent(self.native.platform, group=True)
        event.created_at = self.clock.now().timestamp()
        event.message_obj = parsed.message_obj
        event.message_obj.group_id = GROUP_OPENID
        event.message_obj.session_id = GROUP_OPENID
        event.message_obj.sender.user_id = OFFICIAL_OPENID
        admission = (await self.controller.group_reply_candidate(event)).unwrap()
        prepared = (await self.controller.prepare_core_reply(event, group_admission=admission)).unwrap()
        plain = "".join(part.text for part in event.message_obj.message if isinstance(part, Plain))
        self.assertEqual(prepared["native_input"].text, plain)
        self.assertEqual(prepared["native_input"].group_message_text, canonical)
        facts = core_decision_facts(event, prepared, app, at=self.clock.now())
        self.assertEqual(facts["user_text"], canonical)
        self.assertTrue(facts["mentioned"])
        self.assertTrue(facts["mentions_others"])
        self.assertIn("不代表要求本角色回答", prepared["prompt_instruction"])
        observed, _ = app.tools.group_participation.admission.native_participation_history(
            prepared["context"], prepared["native_input"].instruction, at=self.clock.now())
        self.assertIn(canonical, [item["text"] for item in observed])

        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": prepared})
        request = ProviderRequest(prompt=plain)
        await inject_core_reply_perception_context(event, request)
        self.assertEqual(request.prompt, canonical)
        provider = object.__new__(ProviderOpenAIOfficial)
        assembled = await provider.assemble_context(request.prompt,
            extra_user_content_parts=request.extra_user_content_parts)
        self.assertEqual(assembled["content"][0]["text"], canonical)
        user = Message.model_validate(await request.assemble_context())
        continuation = [user, Message(role="assistant", tool_calls=[{
            "id": "call-synthetic", "type": "function",
            "function": {"name": "test_lookup", "arguments": "{}"}}]),
            Message(role="tool", tool_call_id="call-synthetic", content="测试结果")]
        saved = dump_messages_with_checkpoints(continuation)
        self.assertEqual(saved[0]["content"][0]["text"], canonical)
        self.assertEqual(saved[1]["tool_calls"][0]["id"], saved[2]["tool_call_id"])
        # The persisted current utterance is independent of temporary perception.
        self.assertNotIn("companion_v2_context", json.dumps(saved, ensure_ascii=False))
        restored = Message.model_validate(saved[0])
        self.assertEqual(restored.content[0].text, canonical)
        self.assertEqual(self.native.platform.sent, [])
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_native_group_prompt_keeps_bot_mentions_with_other_bot_markup(self):
        self.config["multi_host"]["group_targets"] = [dict(target_id="official-group",
            platform_id=ACCOUNT, account_id=ACCOUNT, bot_id="bot-test", group_id=GROUP_OPENID)]
        self.window_personas[f"{ACCOUNT}:GroupMessage:{GROUP_OPENID}"] = "original"
        self.controller = self.make_controller(self.config)
        await self.start_host()
        for name, self_id, mentioned in (("openid", "BOT_OPENID", True),
                                        ("legacy", "qq_official", True),
                                        ("other-only", "qq_official", False)):
            with self.subTest(name=name):
                text = "<@OTHER_BOT_OPENID> 来看图"
                event = OfficialEvent(self.native.platform, group=True, text=text)
                event.created_at = self.clock.now().timestamp()
                event.message_obj.message_id = "official-mention-" + name
                event.message_obj.self_id = self_id
                event.message_obj.message = ([At(qq=self_id)] if mentioned else []) + [Plain(text)]
                admission = (await self.controller.group_reply_candidate(event)).unwrap()
                result = await self.controller.prepare_core_reply(
                    event, group_admission=admission if admission["admitted"] else None)
                self.assertTrue(result.is_success, result.failure)
                prepared = result.unwrap()
                self.assertEqual(prepared["native_input"].text, text)
                self.assertEqual(prepared["group_trigger"]["mentioned"], mentioned)
                expected = "本轮明确 @ 了本角色" if mentioned else "本轮 @ 的是其他账号"
                self.assertIn(expected, prepared["prompt_instruction"])
                if mentioned:
                    self.assertNotIn("本轮没有明确 @ 本角色", prepared["prompt_instruction"])
        self.assertEqual(self.native.platform.sent, [])
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def _check_name_upgrade(self, *, group):
        if group:
            self.config["multi_host"]["group_targets"] = [dict(target_id="official-group",
                platform_id=ACCOUNT, account_id=ACCOUNT, bot_id="bot-test", group_id=GROUP_OPENID)]
            self.window_personas[f"{ACCOUNT}:GroupMessage:{GROUP_OPENID}"] = "original"
            self.controller = self.make_controller(self.config)
        app = await self.start_host()
        topology = self.controller.session_topology
        sequence = 0

        async def receive(nickname, group_name=None):
            nonlocal sequence
            sequence += 1
            event = OfficialEvent(self.native.platform, group=group)
            event.message_obj.message_id = f"official-name-{sequence}"
            event.message_obj.sender.nickname = OFFICIAL_OPENID
            author = {"member_openid" if group else "user_openid": OFFICIAL_OPENID}
            if nickname is not None:
                author["username"] = nickname
            event.message_obj.raw_message = NS(id=event.message_obj.message_id, content="你好",
                raw_data={"author": author})
            if group:
                event.message_obj.group = NS(group_id=GROUP_OPENID, group_name=group_name)
            result = await self.controller.receive_claimed(event)
            self.assertTrue(result.is_success, result.failure)
            batch = result.unwrap()
            await topology.observe(event)
            context = TaskContext.restore(json.loads(app.messages.batch(batch)["context_json"]))
            return context, event

        context, event = await receive(None, GROUP_OPENID)
        card = app.messages.acquaintance
        self.assertIsNone(json.loads(card.row(owner_key(context))["document_json"])["platform_nickname"])
        # Simulate a stored fallback from before a host upgrade.
        card.record_platform_names(context, nickname=OFFICIAL_OPENID)
        context, event = await receive("升级后昵称", "升级后群名")
        document = json.loads(card.row(owner_key(context))["document_json"])
        self.assertEqual(document["platform_nickname"], "升级后昵称")
        self.assertEqual(document["display_name_events"][0]["old"], OFFICIAL_OPENID)
        self.assertEqual(document["native_account_id"], OFFICIAL_OPENID)

        # Use the real relation directory, including native group-name merging.
        if group:
            self.native.conversation_manager.session_conversations = {
                event.unified_msg_origin: self.native.conversations[ACCOUNT]}
        with patch("astrbot_host.acquaintance_api.request", NS(username="admin", query={})), \
                patch.object(topology, "services", AsyncMock(return_value={})):
            result = await AcquaintanceWebAPI(self.controller).directory()
        self.assertEqual(result["status"], "ok", result)
        person = next(p for p in result["data"]["persons"] if p["native_account_id"] == OFFICIAL_OPENID)
        self.assertEqual(person["display_name"], "升级后昵称")
        if group:
            named_group = next(g for g in result["data"]["groups"] if g["group_id"] == GROUP_OPENID)
            self.assertEqual(named_group["display_name"], "升级后群名")
            network = next(g for g in result["data"]["group_networks"] if g["group_id"] == GROUP_OPENID)
            self.assertEqual(network["members"][0]["display_name"], "升级后昵称")

        # Missing fields and ID fallbacks must not downgrade a recognized name.
        await receive(OFFICIAL_OPENID, GROUP_OPENID)
        self.assertEqual(json.loads(card.row(owner_key(context))["document_json"])["platform_nickname"], "升级后昵称")
        route = document["route"]
        route_fields = {key: route[key] for key in ("platform", "bot_id", "account_id", "role_id")}
        operator = dashboard_operator(app.runtime, "admin", at=self.clock.now())
        PersonAcquaintanceNameManagement(app.runtime).set_nickname(operator,
            routes=(route_fields,), observed_at=self.clock.now(), subject_id=route["subject_id"],
            **route_fields, native_account_id=OFFICIAL_OPENID, nickname="管理员备注",
            expected_revision=card.row(owner_key(context))["revision"], operation_key="official-admin-name").unwrap()
        await receive("再次改名", "再次改群名")
        document = json.loads(card.row(owner_key(context))["document_json"])
        self.assertEqual(document["platform_nickname"], "再次改名")
        self.assertEqual(document["admin_nickname"], "管理员备注")
        with patch("astrbot_host.acquaintance_api.request", NS(username="admin", query=Query(read_mode="stored"))), \
                patch.object(topology, "inventory", AsyncMock(
                    side_effect=AssertionError("stored names must not query native sessions"))) as inventory:
            result = await AcquaintanceWebAPI(self.controller).directory()
        self.assertEqual(result["status"], "ok", result)
        person = next(p for p in result["data"]["persons"] if p["native_account_id"] == OFFICIAL_OPENID)
        self.assertEqual(person["display_name"], "管理员备注")
        inventory.assert_not_awaited()
        if group:
            named_group = next(g for g in result["data"]["groups"] if g["group_id"] == GROUP_OPENID)
            self.assertEqual(named_group["group_name"], "再次改群名")
            self.assertEqual(named_group["display_name"], "再次改群名")

    async def test_official_private_name_upgrade_replaces_id_and_preserves_admin_remark(self):
        await self._check_name_upgrade(group=False)

    async def test_official_group_name_upgrade_reaches_directory_and_member_network(self):
        await self._check_name_upgrade(group=True)

    async def test_blank_shared_provider_follows_astrbot_current_default(self):
        self.config["model"]["provider_id"] = ""
        self.controller.refresh_settings()
        self.assertEqual(self.controller.settings.common.provider_id, "native-model")
        await self.start_host()
    async def test_official_account_directory_reports_adapter_family_for_ui_labels(self):
        accounts = self.controller.session_topology.accounts()
        official = next(row for row in accounts if row["platform_id"] == ACCOUNT)
        self.assertEqual(official["platform_name"], "qq_official")
        self.assertEqual(official["account_id"], ACCOUNT)

    async def test_account_discovery_keeps_unconfigured_official_groups_visible(self):
        private_umo = f"{ACCOUNT}:FriendMessage:{OFFICIAL_OPENID}"
        group_umo = f"{ACCOUNT}:GroupMessage:{GROUP_OPENID}"
        sessions = {private_umo: "private-cid", group_umo: "group-cid"}
        rows = [{"session_id": umo, "conversation_id": cid, "persona_id": "original"}
                for umo, cid in sessions.items()]
        self.native.conversation_manager = NS(
            get_curr_conversation_id=AsyncMock(side_effect=sessions.get),
            db=NS(get_session_conversations=AsyncMock(return_value=(rows, len(rows)))),
            get_conversation=AsyncMock(side_effect=AssertionError("history must not be loaded")),
        )
        self.types = replace(self.types, persona_selection_lookup=lambda *_args: "original")
        request = NS(username="admin", query={})
        for platform_name in ("qq_official", "qq_official_webhook"):
            for private_bound in (True, False):
                with self.subTest(platform_name=platform_name, private_bound=private_bound):
                    self.native.platform.metadata.name = platform_name
                    config = deepcopy(self.config)
                    if not private_bound:
                        config["multi_host"]["targets"] = []
                    controller = self.make_controller(config)
                    before = deepcopy(controller.config)
                    with patch("astrbot_host.account_session_api.request", request), \
                            patch("astrbot_host.setup_api.request", request), \
                            patch("astrbot_host.setup_api.display_names",
                                  return_value=NS(targets=AsyncMock(side_effect=lambda items: items))), \
                            patch.object(controller.session_topology, "services", AsyncMock(return_value={})):
                        response = await AccountSessionWebAPI(controller).get()
                        self.assertEqual(response["status"], "ok", response)
                        discovered = response["data"]["discovered"]
                        self.assertTrue(discovered["directory"]["complete"])
                        windows = {row["unified_msg_origin"]: row for row in discovered["targets"]
                                   if row["native"]}
                        self.assertEqual(set(windows), set(sessions))
                        self.assertEqual(windows[private_umo]["scope_kind"], "private")
                        group = windows[group_umo]
                        self.assertEqual(group["scope_kind"], "group")
                        self.assertEqual(group["group_id"], GROUP_OPENID)
                        self.assertEqual(group["account_id"], ACCOUNT)
                        self.assertEqual(group["roles"][0]["role_id"], "role-a")
                        self.assertFalse(group["managed"])
                        # Runtime preparation must still require an explicit group binding.
                        runtime = await controller.session_topology.inventory(settings=controller.settings)
                        self.assertFalse(any(row["scope_kind"] == "group" for row in runtime))
                    self.assertEqual(controller.config, before)
        self.native.conversation_manager.get_conversation.assert_not_awaited()
        self.assertEqual(self.native.platform.sent, [])

    async def test_fresh_public_plugin_data_root_can_open_installations_storage(self):
        public = self.root / "astrbot-data" / "plugin_data" / "astrbot_plugin_private_companion"
        public.mkdir(parents=True)
        controller = AstrBotMultiController(
            self.native, self.config, public, native_types=self.types, now=self.clock.now)
        self.addAsyncCleanup(controller.terminate)
        initialized = await controller.initialize_profile()
        self.assertTrue(initialized.is_success, initialized.failure)
        started = await controller.start()
        self.assertTrue(started.is_success, started.failure)
        self.assertTrue((public / "installations").is_dir())

    async def test_official_private_event_runs_through_shared_host_and_astrbot_sender(self):
        await self.start_host()
        event = OfficialEvent(self.native.platform, sender=OFFICIAL_OPENID, text="官bot接入验证")
        result = await self.controller.receive_claimed(event)
        self.assertTrue(result.is_success, result.failure)
        batch = result.unwrap()
        await wait_for_batch(self.controller.session, batch)
        self.assertEqual(len(self.native.platform.sent), 1)
        session, chain = self.native.platform.sent[0]
        self.assertEqual(str(session), f"{ACCOUNT}:FriendMessage:{OFFICIAL_OPENID}")
        self.assertIn("我在这里", chain.get_plain_text())

    async def test_official_group_event_runs_through_shared_host_and_astrbot_sender(self):
        member = "5A6B7C8D9E0F_MEMBER"
        self.window_personas[f"{ACCOUNT}:GroupMessage:{GROUP_OPENID}"] = "original"
        self.config = multi_config(
            accounts=[{"platform_id": ACCOUNT, "account_id": ACCOUNT, "bot_id": "bot-test"}],
            targets=[],
            personas=[{"bot_id": "bot-test", "persona_id": "original", "role_id": "role-a"}],
            group_targets=[{"target_id": "official-group", "platform_id": ACCOUNT,
                            "account_id": ACCOUNT, "bot_id": "bot-test", "group_id": GROUP_OPENID}],
        )
        self.controller = self.make_controller(self.config)
        await self.start_host()
        event = OfficialEvent(self.native.platform, group=True, sender=member, text="群聊官bot验证")
        result = await self.controller.receive_claimed(event)
        self.assertTrue(result.is_success, result.failure)
        batch = result.unwrap()
        await wait_for_batch(self.controller.session, batch)
        self.assertEqual(len(self.native.platform.sent), 1)
        session, chain = self.native.platform.sent[0]
        self.assertEqual(str(session), f"{ACCOUNT}:GroupMessage:{GROUP_OPENID}")
        self.assertIn("我在这里", chain.get_plain_text())
