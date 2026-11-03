"""Core image tools consume only authenticated current attachments and quotes."""

import base64
from dataclasses import replace
import hashlib
import io
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_core_reply_images as image_support
from astrbot.core.message.components import Image, Plain, Reply
from PIL import Image as PILImage

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, prepare_core_reply_event
from astrbot_host.core_reply_media import (
    bind_native_quoted_image_sources, capture_native_input_images, direct_image_locators,
    frozen_image_locators,
)
from astrbot_host.core_reply_tools import inject_core_reply_tools
from companion_v2.adapters.astrbot.native import NativeConversationInput
from companion_v2.capabilities.models import CapabilityAuthorizationState
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.conversation.native_tool_sources import native_input_images
from companion_v2.conversation.recalls import MessageRecall
from companion_v2.execution.models import TaskContext, digest, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind
from companion_v2.identity_policy.group_audience import group_audience
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.references.configuration import (
    GROUP as REFERENCE_GROUP, ReferenceConfigurationOwner, configuration_fields, target_for,
)
from companion_v2.references.models import (
    ContentDigest, ReferenceAsset, ReferenceKind, ReferenceRole, ReferenceSource,
)
from companion_v2.references.repositories import ReferenceAssetRepository
from companion_v2.references.service import owner_key
from tests.native_image_support import NativeImageFixture
from tests.test_astrbot_core_reply import FakeEvent
from tests.test_conversation_images import image_bytes
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE


def image(raw):
    return Image.fromBase64(base64.b64encode(raw).decode("ascii"))


class CoreReplyQuotedImageTests(NativeImageFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    asyncSetUp = image_support.CoreReplyImagesTests.asyncSetUp
    call = image_support.CoreReplyImagesTests.call
    result = image_support.CoreReplyImagesTests.result
    assert_no_reply_work = image_support.CoreReplyImagesTests.assert_no_reply_work

    def setUp(self):
        super().setUp()
        self.native_registry = self.create_image_port(self.root.parent / "image-native-v2", self.clock.now)
        self.native_registry._default_handlers = True
        self.providers = tuple(replace(provider, capabilities=("text", "tools", "image")) for provider in self.providers)
        self.sender = replace(self.sender, supports_images=True)
        self.package = self.native_registry.declarations()[0].descriptor.package
        self.capabilities = CapabilityRepository(self.runtime)
        require(self.capabilities.register_descriptor(self.package, registered_at=NOW))
        require(self.capabilities.acquire(extension_id=self.package.extension_id, package_id=self.package.package_id,
            package_version=self.package.package_version, expected_revision=0, observed_at=NOW))
        require(self.capabilities.set_role_enabled(extension_id=self.package.extension_id, role=self.window.role,
            bot_id=SCOPE.bot_id, enabled=True, expected_revision=0, observed_at=NOW))
        self.allow_images(SCOPE)
        self.authorize_override = None

    def allow_images(self, scope):
        for capability in self.package.capabilities:
            require(self.capabilities.set_authorization(extension_id=self.package.extension_id, capability_id=capability,
                subject=self.important.subject, scope=scope, state=CapabilityAuthorizationState.ACTIVE,
                expected_revision=0, observed_at=NOW))

    def use_group(self):
        scope = replace(SCOPE, kind=ScopeKind.GROUP, native_scope_id="image-group", session_id="image-group")
        self.personas.register_route(ConversationRoute(scope, group_id=scope.native_scope_id), observed_at=NOW)
        for action in ("text", "conversation.reply", "extension.tool.invoke"):
            self.grant(self.important, scope, action)
        self.allow_images(scope)
        self.context = TaskContext(self.important, require(self.identity.resolve_role(scope, observed_at=NOW)),
                                   group_audience(scope, self.important.subject))

    def bind(self, identifier, parts, *, dispatch=None):
        event = FakeEvent()
        event.message_obj = NS(message=list(parts))
        if dispatch is not None:
            event.bot = NS(api=NS(call_action=dispatch))
        scope = self.context.scope
        route = (ConversationRoute(scope, group_id=scope.native_scope_id) if scope.kind is ScopeKind.GROUP
                 else ConversationRoute(scope, user=self.context.actor.subject))
        original = NativeConversationInput(identifier, self.context.actor.principal, route, self.context.audience,
            "请修改引用图片", self.clock.now(), self.context.role_binding.role, "原人格")
        prepared = {"context": self.context, "event_id": identifier, "native_input": original}

        async def authorize(value):
            return self.authorize_override or self.app.tasks.check_context_access(value["context"],
                action="conversation.reply", observed_at=self.clock.now())

        prepare_core_reply_event(event, prepared, authorize=authorize)
        request = NS(func_tool=None, system_prompt="原人格", image_urls=[])
        inject_core_reply_tools(event, request, application=self.app)
        return event, request

    def original(self, event):
        session = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_tool_session"]
        return native_input_images(self.app.messages, self.context, session.turn_id)

    def raw(self, identifier, sender, raw_image, **changes):
        scope = self.context.scope
        result = {"message_id": identifier, "message_type": scope.kind.value, "self_id": scope.account_id,
            "sender": {"user_id": sender}, "user_id": sender, "time": int(self.clock.now().timestamp()),
            "message": [{"type": "image", "data": {"file": image(raw_image).file}}]}
        if scope.kind is ScopeKind.GROUP:
            result["group_id"] = scope.native_scope_id
        return {**result, **changes}

    async def test_embedded_quote_edit_uses_sdk_attachment_order_and_keeps_original_sender(self):
        source = image_bytes("green")
        reply = Reply(id="original-quote", sender_id=SCOPE.native_scope_id, chain=[image(source)])
        event, request = self.bind("quoted-order", [reply, Plain("修改第二张"), image(image_bytes("red"))])
        request.image_urls.append(image(image_bytes("purple")).file)
        # A later mutation of the SDK component cannot replace the frozen quote.
        reply.chain[:] = [image(image_bytes("purple"))]
        received = await self.call(event, request, "image.edit", {
            "prompt": "改成蓝色", "reference_indices": [1], "reference_roles": ["edit_source"]}, "quote-edit")
        self.result(received, "blue")
        self.assertEqual(self.image_backend.calls[0][1][0].data, source)
        original = self.original(event)
        record = self.app.messages._read_source(original.source, self.context, "text", self.clock.now())
        provenance = json.loads(record.payload)["image_sources"]
        self.assertEqual([item["origin"] for item in provenance], ["attachment", "quoted_message"])
        self.assertEqual(provenance[1]["original_message_id"], "original-quote")
        self.assertEqual(provenance[1]["original_sender"], SCOPE.native_scope_id)
        self.assertEqual(provenance[1]["quoted_by"], self.context.actor.principal.native_account_id)
        self.assert_no_reply_work(event)

    async def test_group_reply_id_uses_official_get_msg_only_when_edit_needs_original(self):
        self.use_group()
        calls = []
        source = image_bytes("green")

        async def dispatch(action, **parameters):
            calls.append((action, parameters))
            return {"data": self.raw("group-quote", "other-member", source)}

        event, request = self.bind("group-quote-edit", [Reply(id="group-quote", sender_id="other-member")], dispatch=dispatch)
        prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        observed = await capture_native_input_images(self.app, prepared, frozen_image_locators(event), authorize=lambda: None)
        self.assertIsNone(observed)
        self.assertEqual(calls, [])
        received = await self.call(event, request, "image.edit", {"prompt": "改成蓝色"}, "group-id-edit")
        self.result(received, "blue")
        self.assertEqual(calls, [("get_msg", {"message_id": "group-quote"})])
        self.assertEqual(self.image_backend.calls[0][1][0].data, source)
        self.assert_no_reply_work(event)

    async def test_host_resolved_quote_cache_path_reaches_image_backend(self):
        source = image_bytes("green")
        cached = self.root / "quoted-image-cache.png"
        cached.write_bytes(source)

        async def dispatch(action, **parameters):
            return {"data": self.raw("cached-quote", SCOPE.native_scope_id, source)}

        event, request = self.bind("cached-quote-edit", [
            Reply(id="cached-quote", sender_id=SCOPE.native_scope_id)], dispatch=dispatch)
        with patch("astrbot_host.core_reply_media.ImageResolver.resolve_for_llm",
                   AsyncMock(return_value=[str(cached)])) as resolve:
            received = await self.call(event, request, "image.edit", {
                "prompt": "模仿图一姿势", "reference_indices": [0], "reference_roles": ["pose"]}, "cached-quote-edit")

        self.result(received, "blue")
        resolve.assert_awaited_once()
        self.assertEqual(self.image_backend.calls[0][1][0].data, source)
        self.assertEqual(self.image_backend.calls[0][1][0].role, "pose")
        self.assert_no_reply_work(event)

    async def test_embedded_local_quote_uses_the_verified_original_image(self):
        original = image_bytes("green")
        untrusted = self.root / "embedded-quote.png"
        untrusted.write_bytes(image_bytes("red"))
        calls = []

        async def dispatch(action, **parameters):
            calls.append((action, parameters))
            return {"data": self.raw("local-quote", SCOPE.native_scope_id, original)}

        event, request = self.bind("local-quote-edit", [Reply(id="local-quote",
            sender_id=SCOPE.native_scope_id, chain=[Image.fromFileSystem(untrusted)])], dispatch=dispatch)
        prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
        observed = await capture_native_input_images(self.app, prepared, frozen_image_locators(event),
            authorize=lambda: None)
        self.assertIsNone(observed)
        self.assertEqual(calls, [])
        received = await self.call(event, request, "image.edit", {
            "prompt": "模仿图一姿势", "reference_indices": [0], "reference_roles": ["pose"]}, "local-quote-edit")

        self.result(received, "blue")
        self.assertEqual(calls, [("get_msg", {"message_id": "local-quote"})])
        self.assertEqual(self.image_backend.calls[0][1][0].data, original)
        self.assertEqual(self.image_backend.calls[0][1][0].role, "pose")

    async def test_verified_quote_file_uri_reaches_image_backend(self):
        original = image_bytes("green")
        cached = self.root / "verified-quote.png"
        cached.write_bytes(original)

        async def dispatch(action, **parameters):
            raw = self.raw("file-quote", SCOPE.native_scope_id, original)
            raw["message"][0]["data"]["file"] = cached.as_uri()
            return {"data": raw}

        event, request = self.bind("file-quote-edit", [Reply(id="file-quote",
            sender_id=SCOPE.native_scope_id, chain=[Image.fromFileSystem(cached)])], dispatch=dispatch)
        received = await self.call(event, request, "image.edit", {
            "prompt": "模仿图一姿势", "reference_indices": [0], "reference_roles": ["pose"]}, "file-quote-edit")

        self.result(received, "blue")
        self.assertEqual(self.image_backend.calls[0][1][0].data, original)

    async def test_quoted_pose_and_saved_identity_reach_backend_in_order(self):
        pose = image_bytes("green")
        identity = image_bytes("purple")
        cached = self.root / "quoted-pose.png"
        cached.write_bytes(pose)
        ReferenceConfigurationOwner(self.app.runtime).write(
            target_for(self.context), configuration_fields(),
            changes={REFERENCE_GROUP + ".enabled": True}, reset=(),
            expected_revision=0, observed_at=self.clock.now())
        asset = ReferenceAsset(
            "saved-identity", ReferenceKind.REFERENCE_IMAGE, ReferenceRole.IDENTITY,
            ContentDigest(hashlib.sha256(identity).hexdigest()), len(identity), "image/png",
            "role-reference:test:saved-identity", ReferenceSource.OPERATOR,
            created_at=self.clock.now())
        ReferenceAssetRepository(self.app.runtime).put(
            owner_key(self.context), asset, created_at=self.clock.now(), payload=identity)

        async def dispatch(action, **parameters):
            raw = self.raw("pose-quote", SCOPE.native_scope_id, pose)
            raw["message"][0]["data"]["file"] = cached.as_uri()
            return {"data": raw}

        event, request = self.bind("pose-and-identity", [
            Reply(id="pose-quote", sender_id=SCOPE.native_scope_id,
                  chain=[Image.fromFileSystem(cached)])], dispatch=dispatch)
        received = await self.call(event, request, "image.edit", {
            "prompt": "按图一姿势和图二身份生成", "reference_indices": [0],
            "reference_ids": ["saved-identity"], "reference_roles": ["pose", "identity"],
        }, "pose-identity-edit")

        self.result(received, "blue")
        invocation, references = self.image_backend.calls[0]
        self.assertEqual([item.data for item in references], [pose, identity])
        self.assertEqual([item.role for item in references], ["pose", "identity"])
        self.assertEqual(invocation.reference_ids, tuple(item.reference_id for item in references))

    async def test_private_bot_quote_requires_the_exact_peer_history(self):
        calls = []
        source = image_bytes("green")

        async def dispatch(action, **parameters):
            calls.append((action, parameters))
            raw = self.raw("bot-quote", SCOPE.account_id, source)
            return {"messages": [raw]} if action == "get_friend_msg_history" else {"data": raw}

        event, request = self.bind("private-bot-quote", [Reply(id="bot-quote", sender_id=SCOPE.account_id)], dispatch=dispatch)
        self.result(await self.call(event, request, "image.edit", {"prompt": "改成蓝色"}, "bot-id-edit"), "blue")
        self.assertEqual(calls[1], ("get_friend_msg_history", {"self_id": SCOPE.account_id,
            "user_id": SCOPE.native_scope_id, "message_seq": "bot-quote", "count": 1,
            "reverse_order": False, "parse_mult_msg": True}))
        self.assertEqual(self.image_backend.calls[0][1][0].data, source)
        self.assert_no_reply_work(event)

    async def test_quote_from_other_group_or_private_route_never_reaches_image_backend(self):
        self.use_group()
        for index, changes in enumerate(({"group_id": "another-group"}, {"message_type": "private", "group_id": None})):
            with self.subTest(changes=changes):
                async def dispatch(action, **parameters):
                    return {"data": self.raw("foreign-quote", "other-member", image_bytes("green"), **changes)}
                event, request = self.bind("foreign-route-" + str(index), [Reply(id="foreign-quote", sender_id="other-member")], dispatch=dispatch)
                received = await self.call(event, request, "image.edit", {"prompt": "改成蓝色"}, "foreign-edit-" + str(index))
                self.assertEqual(json.loads(received)["code"], "host_quote_scope_mismatch")
                self.assertEqual(self.image_backend.calls, [])
                self.assert_no_reply_work(event)

    async def test_mask_edit_selects_quoted_original_after_direct_mask(self):
        mask = PILImage.new("RGB", (3, 2), "black")
        mask.putpixel((0, 0), (255, 255, 255))
        buffer = io.BytesIO()
        mask.save(buffer, format="PNG")
        source = image_bytes("green")
        event, request = self.bind("quoted-mask", [Reply(id="masked-quote", sender_id=SCOPE.native_scope_id,
            chain=[image(source)]), image(buffer.getvalue())])
        received = await self.call(event, request, "image.mask_edit", {
            "prompt": "改成蓝色", "source_index": 1, "mask_index": 0}, "quoted-mask-edit")
        self.assertEqual(json.loads(received.content[0].text)["state"], "succeeded")
        with PILImage.open(io.BytesIO(base64.b64decode(received.content[1].data))) as result:
            self.assertEqual(result.getpixel((0, 0)), (0, 0, 255))
            self.assertEqual(result.getpixel((1, 0)), (0, 128, 0))
        references = self.image_backend.calls[0][1]
        self.assertEqual([reference.role for reference in references], ["edit_source", "edit_mask"])
        self.assertEqual(references[0].data, source)
        self.assert_no_reply_work(event)

    async def test_no_current_image_and_unrelated_request_image_urls_do_not_authorize_edit(self):
        event, request = self.bind("no-current-image", [Plain("没有图片")])
        request.image_urls.append(image(image_bytes("red")).file)
        received = await self.call(event, request, "image.edit", {"prompt": "改成蓝色"}, "no-image-edit")
        self.assertNotEqual(json.loads(received)["state"], "succeeded")
        self.assertEqual(self.image_backend.calls, [])
        self.assertIsNone(native_input_images(self.app.messages, self.context,
            event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_tool_session"].turn_id, optional=True))
        self.assert_no_reply_work(event)

    async def test_recalled_quote_is_checked_after_backend_and_when_source_guard_is_reattached(self):
        event, request = self.bind("withdrawn-during-edit", [Reply(id="withdrawn-original", sender_id=SCOPE.native_scope_id,
            chain=[image(image_bytes("green"))])])
        execute = self.image_backend.execute

        async def withdraw_after_dispatch(invocation, references, before_dispatch):
            result = await execute(invocation, references, before_dispatch)
            require(self.app.messages.recalls.accept(MessageRecall.from_scope(SCOPE, "withdrawn-original", SCOPE.native_scope_id)))
            return result

        self.image_backend.execute = withdraw_after_dispatch
        received = await self.call(event, request, "image.edit", {"prompt": "改成蓝色"}, "withdrawn-edit")
        self.assertIsInstance(received, str)
        result = json.loads(received)
        self.assertIn(result["effect"], ("committed", "unknown"), result)
        self.assertEqual(len(self.image_backend.calls), 1)
        reference = self.original(event).source
        guard = bind_native_quoted_image_sources(self.app)
        self.assertIs(guard, bind_native_quoted_image_sources(self.app))
        guard.close()
        reattached = bind_native_quoted_image_sources(self.app)
        self.assertIsNot(guard, reattached)
        denied = self.app.messages.sources.validate_reference(reference, observed_at=self.clock.now())
        self.assertFalse(denied.is_success)
        self.assertEqual(denied.failure.code, "message_withdrawn")
        self.assert_no_reply_work(event)

    async def test_revocation_while_reply_id_lookup_runs_prevents_backend_dispatch(self):
        async def dispatch(action, **parameters):
            self.authorize_override = Outcome.failed("revoked", "the admitted event was revoked")
            return {"data": self.raw("revoked-quote", SCOPE.native_scope_id, image_bytes("green"))}

        event, request = self.bind("revoked-quote-turn", [Reply(id="revoked-quote", sender_id=SCOPE.native_scope_id)], dispatch=dispatch)
        received = await self.call(event, request, "image.edit", {"prompt": "改成蓝色"}, "revoked-edit")
        self.assertEqual(json.loads(received)["code"], "revoked")
        self.assertEqual(self.image_backend.calls, [])
        self.assert_no_reply_work(event)


if __name__ == "__main__":
    unittest.main()
