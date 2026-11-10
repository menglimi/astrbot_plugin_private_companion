"""Group quote media retains original-member governance across turns and restarts."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace as NS
import unittest

from astrbot_host.group_authority import GroupAuthority
from companion_v2.adapters.astrbot.model_wire import tool_alias
from companion_v2.conversation.quotes.service import quote_reference
from companion_v2.execution.models import TaskContext, TaskError
from tests.audio_support import audio_bytes, silk_bytes
from tests.forward_support import node_raw
from tests.group_quote_media_support import GroupQuoteMediaFixture, Forward, Plain, Reply, FakeRecord, FakeVideo
from tests.group_host_support import group_config
from tests.native_adapter_support import completion, final_text
from tests.staging_bootstrap_support import wait_for_batch
from tests.video_support import video_bytes


class GroupQuoteMediaTests(GroupQuoteMediaFixture, unittest.IsolatedAsyncioTestCase):
    async def test_known_quote_tool_continuation_restart_and_later_metadata_history(self):
        # Without a native video profile, keep video bytes and frames out of the
        # reply model and expose the explicit unavailable status instead.
        self.config = group_config(group_delivery_mode="onebot_group")
        self.config["model"]["maximum_input_estimate"] = 500000
        self.config["runtime"]["budget_tokens"] = 10000000
        self.controller = self.make_controller(self.config)
        await self.start_host()
        a, original_batch = await self.original(parts=[Plain("原资料"), FakeRecord(silk_bytes()), FakeVideo(),
            Forward(id="group-quoted-forward")], nodes=[node_raw("外层", sender="888", nested=[
                node_raw("节点资料", sender="999", audios=(audio_bytes(),), videos=(video_bytes(tint=40),))])])
        original_request = self.request(a, original_batch)
        views = []
        async def respond(payload, _):
            content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
            views.append(content)
            materials = json.loads(content[0]["text"])["materials"]
            video_status = [m for m in materials if m["owner"] == "conversation.video_narration"]
            self.assertEqual(len(video_status), 2)
            self.assertTrue(all("没有可用的原生视频理解模型" in m["text"] for m in video_status))
            if len(views) == 1:
                return completion(tools=[NS(id="quoted-query", type="function", function=NS(
                    name=tool_alias("group.memory.query"), arguments="{}"))])
            audio = next(m for m in materials if m["purpose"].startswith("forwarded_audio:"))
            return completion(final_text("引用节点附带这些资料。", evidence=audio["evidence"]))
        self.native.provider.client.state.behavior = respond
        b, quoted_batch = await self.quoted()
        request = self.request(b, quoted_batch)
        self.assertEqual((len(request.audios), len(request.videos)), (2, 2))
        self.assertEqual([len(v.frames) for v in request.videos], [4, 4])
        self.assertEqual(self.history_calls[-1], ("get_group_msg_history", dict(self_id="100", group_id="300",
            message_seq="345", count=1, reverse_order=False, parse_mult_msg=True)))
        self.assertEqual(len(views), 2)
        self.assertEqual(views[0][1:], views[1][1:])
        self.assertEqual(sum(p["type"] == "input_audio" for p in views[0]), 2)
        self.assertFalse(any(p["type"] in ("image_url", "video_data") for p in views[0]))
        evidence = [[m for m in json.loads(view[0]["text"])["materials"] if m["evidence"]] for view in views]
        self.assertEqual(evidence[0], evidence[1])
        materials = request.context.materials
        direct = next(json.loads(m.payload) for m in materials if m.purpose.startswith("quoted_audio:"))
        forwarded = next(json.loads(m.payload) for m in materials if m.purpose.startswith("forwarded_audio:"))
        self.assertEqual((direct["sender"], direct["quoted_by"], direct["original_message_id"]), ("201", "200", "345"))
        self.assertEqual((forwarded["sender"], forwarded["sender_claim"], forwarded["forwarded_by"], forwarded["quoted_by"]),
                         ("unknown", "999", "201", "200"))
        self.assertEqual(forwarded["node_path"], [0, 1, 0])
        self.assertEqual(self.app.messages.sources.read(original_request.audios[0].source,
            resolved=b.actor, capability="text", observed_at=self.clock.now(),
            authorizer=self.app.tasks.identity).failure.code, "source_audience_denied")
        await self.restart()
        restored = self.request(b, quoted_batch)
        self.assertEqual((restored.audios, restored.videos), (request.audios, request.videos))
        self.native.provider.client.state.behavior = None
        c, later = await self.receive_parts([Plain("下一位接着聊")], sender="202")
        later_request = self.request(c, later)
        self.assertFalse(later_request.images or later_request.audios or later_request.videos)
        history = [json.loads(m.payload) for m in later_request.context.materials if m.purpose == "group_conversation_history"]
        quoted = next(m for m in history if m.get("quotations"))
        self.assertEqual(quoted["speaker_id"], "200")
        self.assertEqual(quoted["quotations"][0]["sender"], "201")
        root = quoted["forwarded_records"][0]
        self.assertEqual((root["forwarded_by"], root["quoted_by"], root["original_message_id"]), ("201", "200", "345"))
        self.assertEqual([n["displayed_sender"] for n in root["nodes"]], ["888", "999"])
        self.assertEqual(len(quoted["attachments"]), 4)
        self.assertTrue(all(m["view"] == "historical_metadata_only" for m in quoted["attachments"]))
        self.assertNotIn("base64", json.dumps(history))

    async def test_changed_original_media_nodes_or_missing_attachments_roll_back_quote(self):
        await self.start_host()
        await self.original()
        original = deepcopy(self.records["345"])
        mutations = (
            lambda r: r["message"][1]["data"].update(file=FakeRecord(audio_bytes(value=2000)).file),
            lambda r: r["message"][2]["data"].update(file=FakeVideo(video_bytes(tint=90)).file),
            lambda r: r["message"][3]["data"]["content"][0]["message"][0]["data"].update(text="变过的节点"),
            lambda r: r["message"][3]["data"]["content"][0]["sender"].update(user_id="777"),
            lambda r: r["message"].pop(1),
            lambda r: r["message"].pop(2),
            lambda r: r["message"].pop(3),
        )
        before = {table: self.app.runtime.read_one("SELECT COUNT(*) FROM " + table, ())[0]
                  for table in ("sources", "assets", "identity_bindings", "conversation_inputs")}
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                raw = deepcopy(original)
                mutate(raw)
                self.records["345"] = raw
                result = await self.controller.receive_claimed(self.media_event([Reply(id="345")], sender="777"))
                self.assertEqual(result.failure.code, "quote_original_changed")
                self.assertEqual({table: self.app.runtime.read_one("SELECT COUNT(*) FROM " + table, ())[0]
                                  for table in before}, before)

    async def test_known_media_keeps_member_persona_and_session_identity(self):
        await self.start_host()
        await self.original()
        self.records["345"].update(sender={"user_id": 202}, user_id=202)
        result = await self.controller.receive_claimed(self.media_event([Reply(id="345")]))
        self.assertEqual(result.failure.code, "quote_original_scope_mismatch")
        self.records["345"].update(sender={"user_id": 201}, user_id=201)
        for options in ({"unique": True}, {}):
            if not options:
                self.native.selections["qq-main:GroupMessage:300"] = "second"
            result = await self.controller.receive_claimed(self.media_event([Reply(id="345")], **options))
            self.assertEqual(result.failure.code, "quote_original_scope_mismatch")

    async def test_confirmed_bot_text_cannot_acquire_extra_media_or_forward_nodes(self):
        await self.with_delivery()
        _, batch = await self.receive_parts([Plain("原问题")])
        part = self.app.runtime.read_one("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,))
        identifier = part["platform_ref"].split(":")[-1]
        bridge = self.controller.session.bridge.groups
        read = bridge.quote_loader
        async def text_unchanged(*args, **kwargs):
            captured = await read(*args, **kwargs)
            return replace(captured, text=part["text"])
        bridge.quote_loader = text_unchanged
        for media in (FakeRecord(), FakeVideo(), Forward(id="group-quoted-forward")):
            self.store(identifier, sender="100", parts=[media], nodes=[node_raw()])
            result = await self.controller.receive_claimed(self.media_event([Reply(id=identifier)]))
            self.assertEqual(result.failure.code, "quote_original_changed")

    async def test_withdrawal_revokes_quoted_media_and_prevents_reimport_after_restart(self):
        await self.with_delivery()
        a, first = await self.original()
        b, quoted = await self.quoted()
        c, later = await self.receive_parts([Plain("接着聊引用")], sender="202")
        event = self.input(b, quoted)
        request = self.request(b, quoted)
        references = (quote_reference(event.source, event.quotes[0].position),
                      *(audio.source for audio in request.audios), *(video.source for video in request.videos),
                      *(frame.source for video in request.videos for frame in video.frames))
        receipts = [tuple(r) for r in self.app.runtime.read_all("SELECT * FROM conversation_delivery_receipts", ())]
        self.app.withdraw(a, self.input(a, first).scoped_key).unwrap()
        await self.restart()
        for ref in references:
            self.assertEqual(self.app.messages.sources.validate_reference(ref, observed_at=self.clock.now()).failure.code, "source_revoked")
        for context, batch in ((b, quoted), (c, later)):
            with self.assertRaises(TaskError):
                self.request(context, batch)
        self.assertEqual([tuple(r) for r in self.app.runtime.read_all("SELECT * FROM conversation_delivery_receipts", ())], receipts)
        failure = await self.controller.receive_claimed(self.media_event([Reply(id="345")]))
        self.assertFalse(failure.is_success)
        d, fresh = await self.receive_parts([Plain("新话题")], sender="202")
        history = [json.loads(m.payload) for m in self.request(d, fresh).context.materials if m.purpose == "group_conversation_history"]
        self.assertEqual([m["text"] for m in history], ["接着聊引用"])
        self.assertTrue(all(m["type"] == "group_member_message" and "attachments" not in m for m in history))

    async def test_original_member_revoke_during_quoted_generation_prevents_delivery(self):
        await self.with_delivery()
        a, _ = await self.original()
        async def revoke(payload, _):
            grant = GroupAuthority.grant_id(a.scope, a.actor.subject, "text")
            identity = self.controller.authority.identities
            identity.revoke_grant(grant, revoked_at=self.clock.now(), expected_revision=identity.revision)
            return completion(final_text("不能发出这条引用答复"))
        self.native.provider.client.state.behavior = revoke
        result = (await self.controller.receive_claimed(self.media_event([Reply(id="345")]))).unwrap()
        await wait_for_batch(self.controller.session, result)
        self.assertEqual(self.app.flow.row(result)["state"], "blocked")
        self.assertEqual(self.app.runtime.read_all("SELECT * FROM conversation_parts WHERE batch_id = ?", (result,)), ())
        self.assertEqual([action for action, _ in self.api.calls], ["send_group_msg"])

    async def test_unknown_quoted_send_is_not_replayed_after_restart(self):
        await self.with_delivery()
        await self.original()
        self.api.error = TimeoutError()
        event = self.media_event([Reply(id="345")])
        batch = (await self.controller.receive_claimed(event)).unwrap()
        await wait_for_batch(self.controller.session, batch)
        context = TaskContext.restore(json.loads(self.app.messages.batch(batch)["context_json"]))
        request = self.request(context, batch)
        self.assertEqual(self.app.runtime.read_one("SELECT state FROM conversation_parts WHERE batch_id = ?", (batch,))[0], "unknown")
        calls = (len(self.native.provider.client.state.calls), len(self.api.calls))
        self.api.error = None
        await self.restart()
        restored = self.request(context, batch)
        self.assertEqual((restored.audios, restored.videos), (request.audios, request.videos))
        self.assertEqual((await self.controller.receive_claimed(event)).unwrap(), batch)
        self.assertEqual((len(self.native.provider.client.state.calls), len(self.api.calls)), calls)
        self.assertEqual(self.app.runtime.read_one("SELECT state FROM conversation_parts WHERE batch_id = ?", (batch,))[0], "unknown")
