"""Regression tests for dual OneBot history anchor compatibility and bot-sent quoted image recovery."""

from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from astrbot.core.message.components import Image, Plain, Reply

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, prepare_core_reply_event
from astrbot_host.core_reply_forwards import _try_fetch_multi_card_history
from astrbot_host.core_reply_media import (
    extract_bare_image_md5,
    find_session_generated_image_by_md5,
    prepare_native_input_images,
    repair_quoted_bot_generated_images,
)
from astrbot_host.core_reply_tools import inject_core_reply_tools
from companion_v2.adapters.astrbot.forwards import NativeForwardLocator, read_private_forwards
from companion_v2.adapters.astrbot.history import fetch_onebot_history_record, parse_signed_int
from companion_v2.adapters.astrbot.native import NativeConversationInput
from companion_v2.adapters.astrbot.quotes import NativeQuoteLocator, read_private_quote
from companion_v2.capabilities.models import CapabilityAuthorizationState
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.conversation.native_tool_sources import native_input_images
from companion_v2.conversation.recalls import MessageRecall
from companion_v2.execution.models import TaskContext, TaskError, require
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, AuthorizationTier
from companion_v2.identity_policy.personas import ConversationRoute
from tests import test_core_reply_images as image_support
from tests.forward_support import ForwardFixture, node_raw, quoted_forward_raw
from tests.native_adapter_support import FakeReverseApi
from tests.native_image_support import NativeImageFixture
from tests.test_astrbot_core_reply import FakeEvent
from tests.test_host_application import HostFixture
from tests.test_management_access import INSTALLATION, NOW, SCOPE


class OneBotHistoryHelperTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_signed_int_and_bare_md5_parser(self):
        self.assertEqual(parse_signed_int("500001"), 500001)
        self.assertEqual(parse_signed_int("-500002"), -500002)
        self.assertIsNone(parse_signed_int("bot-quote"))
        self.assertIsNone(parse_signed_int(True))

        md5 = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
        self.assertEqual(extract_bare_image_md5(md5 + ".png"), md5)
        self.assertEqual(extract_bare_image_md5(md5.upper() + ".JPG"), md5)
        self.assertEqual(extract_bare_image_md5("{A1B2C3D4-E5F6-0718-293A-4B5C6D7E8F90}.png"), md5)
        self.assertIsNone(extract_bare_image_md5("https://example.com/" + md5 + ".png"))
        self.assertIsNone(extract_bare_image_md5("../" + md5 + ".png"))
        self.assertIsNone(extract_bare_image_md5(r"C:\tmp\\" + md5 + ".png"))

    async def test_fetch_onebot_history_record_keeps_napcat_single_call_and_retries_message_id_anchor(self):
        # NapCat-style server matches on primary message_seq with zero fallback.
        napcat_calls = []

        async def napcat(action, params):
            napcat_calls.append((action, dict(params)))
            return {"messages": [{"message_id": 500001, "text": "target"}]}

        record, _ = await fetch_onebot_history_record(
            napcat, "get_friend_msg_history", expected_message_id="500001",
            primary_params={"user_id": "200", "message_seq": "500001", "count": 1},
            fallback_params={"user_id": 200, "message_id": 500001, "count": 1},
        )
        self.assertEqual(record["message_id"], 500001)
        self.assertEqual(len(napcat_calls), 1)

        # SnowLuma-style server ignores message_seq and returns the latest message;
        # fallback retries with integer message_id and resolves the exact anchor.
        alt_calls = []

        async def alt_backend(action, params):
            alt_calls.append((action, dict(params)))
            if params.get("message_id") == 500001:
                return {"messages": [{"message_id": 500001, "text": "anchored"}]}
            return {"messages": [{"message_id": 500999, "text": "latest"}]}

        record2, _ = await fetch_onebot_history_record(
            alt_backend, "get_friend_msg_history", expected_message_id="500001",
            primary_params={"user_id": "200", "message_seq": "500001", "count": 1},
            fallback_params={"user_id": 200, "message_id": 500001, "count": 1},
        )
        self.assertEqual(record2["message_id"], 500001)
        self.assertEqual(len(alt_calls), 2)
        self.assertEqual(alt_calls[1][1]["message_id"], 500001)

    async def test_multi_forward_outer_fetch_supports_message_id_fallback_and_negative_ids(self):
        calls = []

        async def call_action(action, **params):
            calls.append((action, dict(params)))
            if params.get("message_id") == -500002:
                return {"messages": [{
                    "message_id": -500002,
                    "message": [
                        {"type": "forward", "data": {"id": "f1", "content": [{"sender": {"nickname": "A"}, "message": [{"type": "text", "data": {"text": "one"}}]}]}},
                        {"type": "forward", "data": {"id": "f2", "content": [{"sender": {"nickname": "B"}, "message": [{"type": "text", "data": {"text": "two"}}]}]}},
                    ],
                }]}
            return {"messages": [{"message_id": 999999, "message": []}]}

        event = NS(message_obj=NS(
            message_id="-500002",
            type=NS(value="FriendMessage"),
            group_id=None,
            sender=NS(user_id="200"),
            raw_message={"message_id": -500002},
        ))
        cards = await _try_fetch_multi_card_history(
            event, call_action, expected_count=2, fetch_timeout=5.0, deadline=1000.0, clock=lambda: 0.0,
        )
        self.assertIsNotNone(cards)
        self.assertEqual(len(cards), 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1]["message_seq"], -500002)
        self.assertEqual(calls[1][1]["message_id"], -500002)


class PrivateHistoryCompatibilityTests(ForwardFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.private_scope = ScopeRef(
            INSTALLATION, "qq-main", ScopeKind.PRIVATE, "200", "bot-a", "100", "conversation-1", "session-1",
        )

    async def test_read_private_quote_retries_with_integer_message_id_and_allows_omitted_self_id(self):
        calls = []
        target = quoted_forward_raw(sender="100", nodes=[node_raw(sender="200")])
        target.pop("self_id", None)
        target["target_id"] = 200
        wrong_latest = quoted_forward_raw(sender="200", nodes=[node_raw(sender="200")])
        wrong_latest["message_id"] = 999999

        async def dispatch(action, **params):
            calls.append((action, dict(params)))
            if params.get("message_id") == 345:
                return {"messages": [target]}
            return {"messages": [wrong_latest]}

        self.api.call_action = dispatch
        quote = await read_private_quote(
            NativeQuoteLocator(0, "345"),
            client=self.native.platform.client,
            scope=self.private_scope,
            reverse_api_type=FakeReverseApi,
            image_loader=AsyncMock(),
        )
        self.assertEqual(quote.message_id, "345")
        self.assertEqual(quote.sender_id, "100")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1]["message_id"], 345)
        self.assertEqual(calls[1][1]["user_id"], 200)

    async def test_read_private_quote_rejects_mismatched_target_id_on_outgoing_bot_message(self):
        target = quoted_forward_raw(sender="100", nodes=[node_raw(sender="200")])
        target.pop("self_id", None)
        target["target_id"] = 777

        async def dispatch(action, **params):
            return {"messages": [target]}

        self.api.call_action = dispatch
        with self.assertRaises(TaskError) as raised:
            await read_private_quote(
                NativeQuoteLocator(0, "345"),
                client=self.native.platform.client,
                scope=self.private_scope,
                reverse_api_type=FakeReverseApi,
                image_loader=AsyncMock(),
            )
        self.assertEqual(raised.exception.code, "host_quote_scope_mismatch")

    async def test_read_private_forwards_retries_message_id_and_allows_omitted_self_id(self):
        calls = []
        outer = {
            "message_id": 345,
            "message_type": "private",
            "sub_type": "friend",
            "sender": {"user_id": 200},
            "user_id": 200,
            "message": [
                {"type": "forward", "data": {"id": "345", "content": [node_raw("第一张")]}},
                {"type": "forward", "data": {"id": "345", "content": [node_raw("第二张")]}},
            ],
        }
        wrong_latest = {**outer, "message_id": 888}

        async def dispatch(action, **params):
            calls.append((action, dict(params)))
            if params.get("message_id") == 345:
                return {"messages": [outer]}
            return {"messages": [wrong_latest]}

        self.api.call_action = dispatch
        captured = await read_private_forwards(
            (NativeForwardLocator(0, "345"), NativeForwardLocator(1, "345")),
            message_id="345",
            client=self.native.platform.client,
            scope=self.private_scope,
            reverse_api_type=FakeReverseApi,
            image_loader=AsyncMock(),
        )
        self.assertEqual(len(captured), 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1]["message_id"], 345)


class BotGeneratedQuotedImageRecoveryTests(NativeImageFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    asyncSetUp = image_support.CoreReplyImagesTests.asyncSetUp
    call = image_support.CoreReplyImagesTests.call
    result = image_support.CoreReplyImagesTests.result

    def setUp(self):
        super().setUp()
        self.native_registry = self.create_image_port(self.root.parent / "image-native-v2", self.clock.now)
        self.native_registry._default_handlers = True
        self.providers = tuple(replace(provider, capabilities=("text", "tools", "image")) for provider in self.providers)
        self.sender = replace(self.sender, supports_images=True)
        self.package = self.native_registry.declarations()[0].descriptor.package
        self.capabilities = CapabilityRepository(self.runtime)
        require(self.capabilities.register_descriptor(self.package, registered_at=NOW))
        require(self.capabilities.acquire(
            extension_id=self.package.extension_id, package_id=self.package.package_id,
            package_version=self.package.package_version, expected_revision=0, observed_at=NOW,
        ))
        require(self.capabilities.set_role_enabled(
            extension_id=self.package.extension_id, role=self.window.role,
            bot_id=SCOPE.bot_id, enabled=True, expected_revision=0, observed_at=NOW,
        ))
        self.numeric_scope = replace(
            SCOPE, account_id="100", native_scope_id="200", session_id="200", conversation_id="conv-num",
        )
        numeric_principal = replace(self.important.principal, native_account_id="200", account_id="100")
        self.identity.bind(
            binding_id="numeric-important-bind", principal=numeric_principal, subject=self.important.subject,
            valid_from=NOW, expected_revision=self.identity.revision,
        )
        self.numeric_actor = require(self.identity.resolve_authenticated(numeric_principal, observed_at=NOW))
        self.personas.register_route(
            ConversationRoute(self.numeric_scope, user=self.important.subject), observed_at=NOW,
        )
        for action in ("text", "conversation.reply", "extension.tool.invoke"):
            self.grant(self.numeric_actor, self.numeric_scope, action)
        for scope in (SCOPE, self.numeric_scope):
            for capability in self.package.capabilities:
                require(self.capabilities.set_authorization(
                    extension_id=self.package.extension_id, capability_id=capability,
                    subject=self.important.subject, scope=scope, state=CapabilityAuthorizationState.ACTIVE,
                    expected_revision=0, observed_at=NOW,
                ))

    async def asyncSetUp(self):
        await image_support.CoreReplyImagesTests.asyncSetUp(self)
        self.context = TaskContext(
            self.numeric_actor,
            require(self.identity.resolve_role(self.numeric_scope, observed_at=NOW)),
            AudienceRef(self.numeric_scope, (self.important.subject,)),
        )

    def bind(self, identifier, parts, *, dispatch=None):
        event = FakeEvent()
        event.message_obj = NS(message=list(parts))
        if dispatch is not None:
            event.bot = NS(api=NS(call_action=dispatch))
        scope = self.context.scope
        route = ConversationRoute(scope, user=self.context.actor.subject)
        original = NativeConversationInput(
            identifier, self.context.actor.principal, route, self.context.audience,
            "请修改引用图片", self.clock.now(), self.context.role_binding.role, "原人格",
        )
        prepared = {"context": self.context, "event_id": identifier, "native_input": original}

        async def authorize(value):
            return self.app.tasks.check_context_access(
                value["context"], action="conversation.reply", observed_at=self.clock.now(),
            )

        prepare_core_reply_event(event, prepared, authorize=authorize)
        request = NS(func_tool=None, system_prompt="原人格", image_urls=[])
        inject_core_reply_tools(event, request, application=self.app)
        return event, request

    async def _generate_in_current_session(self, event_id: str, call_id: str):
        gen_event, gen_request = self.bind(event_id, [Plain("画一张图")])
        received = await self.call(gen_event, gen_request, "image.generate", {"prompt": "一幅测试画面"}, call_id)
        payload = json.loads(received.content[0].text)
        self.assertEqual((payload["state"], payload["effect"]), ("succeeded", "committed"), payload)
        generated_bytes = base64.b64decode(received.content[1].data)
        self.assertGreater(len(generated_bytes), 0)
        md5_hex = hashlib.md5(generated_bytes).hexdigest().lower()
        return generated_bytes, md5_hex

    async def test_bot_quote_with_empty_url_recovers_same_session_generated_image_by_md5(self):
        scope = self.context.scope
        generated_bytes, md5_hex = await self._generate_in_current_session("gen-turn-1", "gen-call-1")
        bare_filename = f"{md5_hex}.png"
        calls = []

        async def dispatch(action, **params):
            calls.append((action, dict(params)))
            row = {
                "message_id": 500001,
                "message_type": "private",
                "sub_type": "friend",
                "sender": {"user_id": int(scope.account_id)},
                "user_id": int(scope.account_id),
                "target_id": int(scope.native_scope_id),
                "time": int(self.clock.now().timestamp()),
                "message": [{"type": "image", "data": {"file": bare_filename, "url": ""}}],
            }
            if action == "get_msg":
                return {"data": row}
            if action == "get_friend_msg_history":
                if params.get("message_id") == 500001:
                    return {"messages": [row]}
                return {"messages": [{
                    "message_id": 500999,
                    "message_type": "private",
                    "sub_type": "friend",
                    "sender": {"user_id": int(scope.native_scope_id)},
                    "user_id": int(scope.native_scope_id),
                    "message": [{"type": "text", "data": {"text": "改成蓝色"}}],
                }]}
            return {}

        self.image_backend.calls.clear()
        event, request = self.bind(
            "edit-turn-2",
            [Reply(id="500001", sender_id=scope.account_id, chain=[Image(file=bare_filename, url="")])],
            dispatch=dispatch,
        )
        received = await self.call(event, request, "image.edit", {"prompt": "改成蓝色"}, "edit-call-2")
        self.result(received, "blue")
        self.assertEqual(len(self.image_backend.calls), 1)
        self.assertEqual(self.image_backend.calls[0][1][0].data, generated_bytes)
        history_calls = [p for a, p in calls if a == "get_friend_msg_history"]
        self.assertEqual(len(history_calls), 2)
        self.assertEqual(history_calls[0]["message_seq"], "500001")
        self.assertEqual(history_calls[1]["message_id"], 500001)
        self.assertEqual(history_calls[1]["user_id"], 200)

    async def test_bot_quote_with_expired_remote_url_falls_back_to_same_session_md5_original(self):
        scope = self.context.scope
        generated_bytes, md5_hex = await self._generate_in_current_session("gen-turn-exp", "gen-call-exp")
        bare_filename = f"{md5_hex}.png"
        expired_url = "https://cdn.example.invalid/download?fileid=expired"

        async def dispatch(action, **params):
            row = {
                "message_id": 500010,
                "message_type": "private",
                "sub_type": "friend",
                "sender": {"user_id": int(scope.account_id)},
                "user_id": int(scope.account_id),
                "target_id": int(scope.native_scope_id),
                "time": int(self.clock.now().timestamp()),
                "message": [{"type": "image", "data": {"file": bare_filename, "url": expired_url}}],
            }
            return {"messages": [row]} if action == "get_friend_msg_history" else {"data": row}

        self.image_backend.calls.clear()
        event, request = self.bind(
            "edit-turn-exp",
            [Reply(id="500010", sender_id=scope.account_id, chain=[Image(file=bare_filename, url="")])],
            dispatch=dispatch,
        )
        with patch(
            "astrbot_host.core_reply_media.load_native_image",
            AsyncMock(side_effect=TaskError("host_image_fetch_failed", "expired rkey")),
        ):
            received = await self.call(event, request, "image.edit", {"prompt": "改成蓝色"}, "edit-call-exp")
        self.result(received, "blue")
        self.assertEqual(len(self.image_backend.calls), 1)
        self.assertEqual(self.image_backend.calls[0][1][0].data, generated_bytes)

    async def test_md5_recovery_rejects_cross_session_non_bot_sender_and_recalled_quote(self):
        scope = self.context.scope
        _generated_bytes, md5_hex = await self._generate_in_current_session("gen-turn-iso", "gen-call-iso")
        bare_filename = f"{md5_hex}.png"

        # 1) Cross-session isolation: another private peer must NEVER recover session A's generated image.
        other_scope = replace(scope, native_scope_id="999", session_id="999", conversation_id="conv-other")
        other_subject = SubjectRef(other_scope.installation_id, SubjectKind.HUMAN, "other-peer")
        other_principal = replace(self.numeric_actor.principal, native_account_id="999")
        self.identity.bind(
            binding_id="other-peer-bind", principal=other_principal, subject=other_subject,
            valid_from=NOW, expected_revision=self.identity.revision,
        )
        self.policy.set_tier(other_subject, AuthorizationTier.STANDARD, expected_revision=self.policy.revision, observed_at=NOW)
        self.personas.register_route(ConversationRoute(other_scope, user=other_subject), observed_at=NOW)
        other_actor = require(self.identity.resolve_authenticated(other_principal, observed_at=NOW))
        other_role = require(self.identity.resolve_role(other_scope, observed_at=NOW))
        other_context = TaskContext(other_actor, other_role, AudienceRef(other_scope, (other_subject,)))
        self.assertIsNone(find_session_generated_image_by_md5(self.app, other_context, md5_hex))

        # 2) Non-bot sender in the SAME session must NOT recover bot's local generated image by bare MD5.
        async def user_sent_dispatch(action, **params):
            if action == "get_msg":
                return {"data": {
                    "message_id": 600001,
                    "message_type": "private",
                    "sub_type": "friend",
                    "sender": {"user_id": int(scope.native_scope_id)},
                    "user_id": int(scope.native_scope_id),
                    "time": int(self.clock.now().timestamp()),
                    "message": [{"type": "image", "data": {"file": bare_filename, "url": ""}}],
                }}
            return {}

        event_user, _req_user = self.bind(
            "edit-user-sender",
            [Reply(id="600001", sender_id=scope.native_scope_id, chain=[Image(file=bare_filename, url="")])],
            dispatch=user_sent_dispatch,
        )
        state_user = event_user.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        session_user = state_user["_tool_session"]
        await prepare_native_input_images(session_user, NS(context=NS(event=event_user)))
        self.assertIsNone(native_input_images(self.app.messages, self.context, session_user.turn_id, optional=True))

        repaired_user = await repair_quoted_bot_generated_images(event_user, state_user["prepared"], application=self.app)
        self.assertEqual(repaired_user, 0)
        self.assertEqual(event_user.message_obj.message[0].chain[0].url, "")

        # 3) Recalled bot message must NEVER be repaired or recovered in either Layer 2 or Layer 3.
        require(self.app.messages.recalls.accept(MessageRecall.from_scope(scope, "500022", scope.account_id)))

        async def recalled_dispatch(action, **params):
            row = {
                "message_id": 500022,
                "message_type": "private",
                "sub_type": "friend",
                "sender": {"user_id": int(scope.account_id)},
                "user_id": int(scope.account_id),
                "target_id": int(scope.native_scope_id),
                "time": int(self.clock.now().timestamp()),
                "message": [{"type": "image", "data": {"file": bare_filename, "url": ""}}],
            }
            return {"messages": [row]} if action == "get_friend_msg_history" else {"data": row}

        self.image_backend.calls.clear()
        event_recalled, req_recalled = self.bind(
            "edit-recalled-bot",
            [Reply(id="500022", sender_id=scope.account_id, chain=[Image(file=bare_filename, url="")])],
            dispatch=recalled_dispatch,
        )
        prepared_recalled = event_recalled.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        self.assertEqual(await repair_quoted_bot_generated_images(event_recalled, prepared_recalled, application=self.app), 0)
        recalled_res = json.loads(await self.call(event_recalled, req_recalled, "image.edit", {"prompt": "改成蓝色"}, "edit-recalled"))
        self.assertEqual(recalled_res["code"], "message_withdrawn")
        self.assertEqual(self.image_backend.calls, [])

    async def test_layer3_repairs_reply_chain_with_tracked_temp_file_and_still_verifies_edit_scope(self):
        scope = self.context.scope
        generated_bytes, md5_hex = await self._generate_in_current_session("gen-turn-l3", "gen-call-l3")
        bare_filename = f"{md5_hex}.png"
        tracked = []

        async def dispatch(action, **params):
            row = {
                "message_id": 500025,
                "message_type": "private",
                "sub_type": "friend",
                "sender": {"user_id": int(scope.account_id)},
                "user_id": int(scope.account_id),
                "target_id": int(scope.native_scope_id),
                "time": int(self.clock.now().timestamp()),
                "message": [{"type": "image", "data": {"file": bare_filename, "url": ""}}],
            }
            if action == "get_friend_msg_history":
                if params.get("message_id") == 500025:
                    return {"messages": [row]}
                return {"messages": [{**row, "message_id": 500999}]}
            return {"data": row}

        event, request = self.bind(
            "reply-l3-turn",
            [Reply(id="500025", sender_id=scope.account_id, chain=[Image(file=bare_filename, url="")])],
            dispatch=dispatch,
        )
        event.track_temporary_local_file = tracked.append
        prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]

        repaired = await repair_quoted_bot_generated_images(event, prepared, application=self.app)
        self.assertEqual(repaired, 1)
        self.assertEqual(len(tracked), 1)
        temp_path = Path(tracked[0])
        try:
            self.assertTrue(temp_path.is_file())
            self.assertEqual(temp_path.read_bytes(), generated_bytes)
            self.assertNotIn("conversation-images", str(temp_path))
            img_comp = event.message_obj.message[0].chain[0]
            self.assertEqual(img_comp.url, str(temp_path))
            self.assertEqual(img_comp.file, str(temp_path))
            self.assertEqual(img_comp.path, str(temp_path))

            # Even after Layer 3 repairs Reply.chain, image.edit still verifies quote scope via OneBot + MD5.
            self.image_backend.calls.clear()
            received = await self.call(event, request, "image.edit", {"prompt": "改成蓝色"}, "edit-after-l3")
            self.result(received, "blue")
            self.assertEqual(self.image_backend.calls[0][1][0].data, generated_bytes)
        finally:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
