"""Native chat evidence reaches background domain owners without reply batches."""

import asyncio
import base64
from datetime import timedelta
import json
import unittest
from unittest.mock import AsyncMock, patch

from companion_v2.conversation.recalls import MessageRecall
from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.expression import feedback, style_rotation
from companion_v2.expression.configuration import GROUP, ExpressionConfigurationOwner, configuration_fields, target_for
from companion_v2.expression.style_evidence import INPUT_SAMPLE_SOURCE, sample_document
from companion_v2.memory.person.access import owner_key as acquaintance_owner
from companion_v2.memory.person.models import aggregate
from tests.group_host_support import GroupFixture
from tests.multi_host_support import MultiFixture


class CorePrivateConsumerTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()

    async def prepared(self, identifier, text):
        result = await self.controller.prepare_core_reply(self.event(identifier=identifier, text=text))
        self.assertTrue(result.is_success, result.failure)
        prepared = result.unwrap()
        self.assertIsNotNone(await prepared["native_source_future"])
        return prepared

    def samples(self, context):
        return self.app.expression_samples.collect(context, self.clock.now(), source_kind=INPUT_SAMPLE_SOURCE)

    def bind_expression_owner(self, context):
        from companion_v2.role_profile.configuration import (GROUP as PROFILE_GROUP,
            RoleProfileConfigurationOwner, configuration_fields as role_profile_fields,
            target_for as role_profile_target_for)
        profile = RoleProfileConfigurationOwner(self.app.runtime)
        fields = role_profile_fields()
        target = role_profile_target_for(context)
        snapshot = profile.read(target, fields)
        profile.write(target, fields, changes={
            PROFILE_GROUP + ".main_companion_subject": context.actor.subject.subject_id,
        }, reset=(), expected_revision=snapshot.revision, observed_at=self.clock.now())

    def assert_core_only(self):
        self.assertEqual(self.app.runtime.read_all("SELECT batch_id FROM conversation_batches", ()), ())
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platforms["qq-main"].client._api._wsr_api.calls, [])

    async def test_native_input_updates_acquaintance_habits_samples_and_mood_once(self):
        prepared = await self.prepared("consumer-intake", "我刚吃完晚饭，今天吃了米饭")
        context = prepared["context"]
        row = self.app.messages.acquaintance.row(acquaintance_owner(context))
        self.assertEqual(aggregate(json.loads(row["document_json"]))["msg_count"], 1)
        habits = self.app.runtime.read_all("SELECT document_json FROM memory_views WHERE kind='user.habit.v1'", ())
        self.assertEqual(len(habits), 1)
        self.assertEqual(json.loads(habits[0]["document_json"])["patterns"][0]["count"], 1)
        self.assertEqual([sample.text for sample in self.samples(context).samples], [prepared["text"]])
        events = self.app.state_maintenance._events(context)
        self.assertEqual([event.event_id for event in events], [prepared["event_id"]])
        settings, _ = self.app.role_state.settings(context)
        mood = self.app.state_maintenance._mood(context, events, {**settings, "mood_enabled": True}, None, self.clock.now())
        self.assertEqual((mood["messages"], mood["characters"]), (1, len(prepared["text"])))
        self.assertTrue(self.app.state_maintenance.foreground_busy(context))
        await self.controller.session.core_reply_observations.phase(prepared, "completed")
        self.assertFalse(self.app.state_maintenance.foreground_busy(context))
        await self.prepared("consumer-intake", prepared["text"])
        row = self.app.messages.acquaintance.row(acquaintance_owner(context))
        self.assertEqual(aggregate(json.loads(row["document_json"]))["msg_count"], 1)
        self.assert_core_only()

    async def test_withdrawal_invalidates_native_samples_and_state_inputs(self):
        prepared = await self.prepared("consumer-withdrawn", "嗯哼，这样的说法很轻松")
        context = prepared["context"]
        samples = self.samples(context)
        self.assertEqual(len(samples.samples), 1)
        reference = await prepared["native_source_future"]
        self.app.role_state.evidence.validate(context, (reference,))
        self.assertTrue(self.app.expression_samples.resolve_handles((samples.samples[0].handle,),
            samples.opened_at, samples.closed_at, context, self.clock.now(), source_kind=INPUT_SAMPLE_SOURCE))
        require(self.app.messages.recalls.accept(MessageRecall.from_scope(
            context.scope, prepared["event_id"], context.actor.principal.native_account_id)))
        self.assertEqual(self.samples(context).samples, ())
        self.assertEqual(self.app.state_maintenance._events(context), ())
        with self.assertRaises(TaskError) as evidence_error:
            self.app.role_state.evidence.validate(context, (reference,))
        self.assertIn(evidence_error.exception.code, {"message_withdrawn", "source_revoked"})
        with self.assertRaises(TaskError):
            self.app.expression_samples.resolve_handles((samples.samples[0].handle,), samples.opened_at,
                samples.closed_at, context, self.clock.now(), source_kind=INPUT_SAMPLE_SOURCE)
        self.assert_core_only()

    async def test_native_samples_prepare_learning_without_old_batch_and_optional_observer_failure(self):
        owner = self.app.messages.acquaintance
        original = owner.observe
        owner.observe = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("derived card unavailable"))
        prepared = await self.prepared("consumer-observer-failed", "嗯哼，那就这样说定啦")
        owner.observe = original
        # Source capture finishes before the optional background role rebind.
        # Learning consumes that binding, so wait for it as the host does.
        await asyncio.gather(*tuple(self.controller.session.core_reply_maintenance._pending))
        context = prepared["context"]
        self.bind_expression_owner(context)
        ExpressionConfigurationOwner(self.app.runtime).write(target_for(context), configuration_fields(),
            changes={GROUP + ".min_samples": 1}, reset=(), expected_revision=0, observed_at=self.clock.now())
        job = self.app.expression_style.prepare(context, prepared["instruction"], self.clock.now())
        self.assertIsNotNone(job)
        self.assertEqual(job["catalogue"][0]["text"], prepared["text"])
        original_context = self.app.expression_samples.evidence_context({"context_key": context.key}, context)
        self.assertEqual(original_context, context)
        from companion_v2.expression.style_management import ExpressionStyleManagement
        self.assertEqual(ExpressionStyleManagement(self.app)._evidence_context({"context_key": context.key}, context), context)
        self.assert_core_only()

    async def test_adopted_native_rule_rotates_after_send_and_consumes_user_feedback(self):
        source = await self.prepared("consumer-style-source", "嗯哼，就这么定啦")
        context = source["context"]
        self.bind_expression_owner(context)
        samples = self.samples(context)
        owner, scope = digest(self.app.expression_samples.role_owner(context)), digest(context.scope)
        run_id, signal_id = "expression-run:native-consumer", "expression-run:native-consumer:signal:0"
        with self.app.runtime.transaction() as connection:
            connection.execute("""INSERT INTO expression_style_runs VALUES
                (?, ?, ?, ?, ?, '轻松接话', 'pending', ?, NULL)""", (run_id, owner, scope,
                encode({**sample_document(samples), "context_key": context.key}), samples.material_hash, stamp(self.clock.now())))
            connection.execute("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal, confidence,
                 evidence_json, state, created_at, decided_at, decided_by, decision_key, ordinal)
                VALUES (?, ?, ?, ?, 'diction', '轻松接话', '自然使用轻松的口语', 0.8, ?, 'adopted', ?, ?, 'operator', 'adopt', 0)""",
                (signal_id, run_id, owner, scope, encode([samples.samples[0].handle]), stamp(self.clock.now()), stamp(self.clock.now())))
        self.clock.advance(timedelta(seconds=1))
        prepared = await self.prepared("consumer-style-send", "今天怎么样")
        self.assertIn(signal_id, prepared["expression_signal_ids"])
        observer = self.controller.session.core_reply_observations
        await observer.output(prepared, "挺好呀", state="output_observed")
        self.assertEqual(style_rotation.usage(self.app.runtime, owner, scope), {})
        await observer.output(prepared, "挺好呀", state="send_returned")
        self.assertEqual(style_rotation.usage(self.app.runtime, owner, scope)[signal_id][0], 1)
        await observer.output(prepared, "挺好呀", state="send_returned")
        self.assertEqual(style_rotation.usage(self.app.runtime, owner, scope)[signal_id][0], 1)
        self.clock.advance(timedelta(seconds=1))
        await self.prepared("consumer-style-feedback", "这个语气怪怪的，别这样说")
        self.assertEqual(feedback.usage(self.app.runtime, owner, scope)[signal_id]["negative_count"], 1)
        self.assert_core_only()

    async def test_native_prompt_pins_rotation_rules_without_offering_the_whole_pool(self):
        source = await self.prepared("consumer-pool-source", "嗯哼，就这么定啦")
        context = source["context"]
        self.bind_expression_owner(context)
        samples = self.samples(context)
        owner, scope = digest(self.app.expression_samples.role_owner(context)), digest(context.scope)
        run_id = "expression-run:native-pool"
        identifiers = [run_id + ":signal:" + str(index) for index in range(3)]
        with self.app.runtime.transaction() as connection:
            connection.execute("""INSERT INTO expression_style_runs VALUES
                (?, ?, ?, ?, ?, '轻松接话', 'pending', ?, NULL)""", (run_id, owner, scope,
                encode({**sample_document(samples), "context_key": context.key}),
                samples.material_hash, stamp(self.clock.now())))
            for index, signal_id in enumerate(identifiers):
                connection.execute("""INSERT INTO expression_style_signals
                    (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal, confidence,
                     evidence_json, state, created_at, decided_at, decided_by, decision_key, ordinal)
                    VALUES (?, ?, ?, ?, 'diction', ?, ?, 0.8, ?, 'adopted', ?, ?, 'operator', 'adopt', ?)""",
                    (signal_id, run_id, owner, scope, f"观察{index}", f"自然使用口语{index}",
                     encode([samples.samples[0].handle]), stamp(self.clock.now()), stamp(self.clock.now()), index))
        self.clock.advance(timedelta(seconds=1))
        prepared = await self.prepared("consumer-pool-send", "今天怎么样")
        injected = prepared["expression_signal_ids"]
        self.assertEqual(len(injected), 2, "one turn pins rules_per_turn rules and counts exactly those")
        prompt = prepared["prompt_instruction"]
        self.assertTrue(all(identifier in prompt for identifier in injected))
        omitted = [identifier for identifier in identifiers if identifier not in injected]
        self.assertEqual(len(omitted), 1)
        self.assertNotIn(omitted[0], prompt, "the native prompt must not carry unused candidates")
        self.assert_core_only()

    async def media_collection(self, *, withdraw=False):
        from astrbot.core.message.components import Image
        from tests.test_semantic_integration import picture
        semantic = self.app.tools.semantic
        cfg = semantic.store.settings()
        semantic.store.configure({"reactions_collection_private_enabled": True,
            "reactions_collection_private_probability": 1.0}, cfg["revision"])
        original = picture()
        event = self.event(identifier="consumer-picture", text="这个表情很可爱")
        event.message_obj.message.append(Image.fromBase64(base64.b64encode(original.content).decode("ascii")))
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        await prepared["native_source_future"]
        download_started, download_continue = asyncio.Event(), asyncio.Event()
        review_started, review_continue = asyncio.Event(), asyncio.Event()

        async def load(locator):
            download_started.set()
            await download_continue.wait()
            return original

        async def analyze(provider, prompt, images=()):
            self.assertIn("判断图片是否适合作为独立的聊天表情素材", prompt)
            self.assertTrue(images)
            review_started.set()
            await review_continue.wait()
            return {"suitable": True, "name": "挥手", "description": "挥手的聊天表情", "intents": "回应招呼"}

        semantic.gateway.port.analyze = analyze
        observer = self.controller.session.core_reply_observations
        with patch("astrbot_host.core_reply_media.load_native_image", side_effect=load):
            job = observer.media(event, prepared)
            await asyncio.wait_for(download_started.wait(), timeout=3)
            self.assertFalse(job.done())
            self.assert_core_only()
            download_continue.set()
            image_event = await job
        self.assertIsNotNone(image_event)
        await asyncio.wait_for(review_started.wait(), timeout=3)
        self.assert_core_only()
        if withdraw:
            require(self.app.messages.recalls.accept(MessageRecall.from_scope(prepared["context"].scope,
                prepared["event_id"], prepared["context"].actor.principal.native_account_id)))
        review_continue.set()
        while semantic.background:
            await asyncio.gather(*tuple(semantic.background.values()))
        owner = semantic.reaction_owner(prepared["context"])
        items = semantic.store.list(owner, "reaction")
        if withdraw:
            self.assertEqual(items, [])
            self.assertEqual(semantic.errors["reaction_collection"], "message_withdrawn")
        else:
            self.assertEqual(len(items), 1)
            self.assertEqual(semantic.reactions.captured(items[0]).content, original.content)
            semantic.schedule_native_reaction_collection(prepared["context"], prepared["turn_id"], image_event)
            while semantic.background:
                await asyncio.gather(*tuple(semantic.background.values()))
            self.assertEqual(len(semantic.store.list(owner, "reaction")), 1)
        self.assert_core_only()

    async def test_native_image_download_and_reaction_collection_run_outside_reply(self):
        await self.media_collection()

    async def test_native_image_withdrawal_during_review_prevents_collection(self):
        await self.media_collection(withdraw=True)


class CoreGroupConsumerTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()

    async def prepared(self, identifier, text, sender="200"):
        result = await self.controller.prepare_core_reply(self.group_event(identifier=identifier, text=text, sender=sender))
        self.assertTrue(result.is_success, result.failure)
        prepared = result.unwrap()
        self.assertIsNotNone(await prepared["native_source_future"])
        return prepared

    async def test_listening_group_collects_image_only_animation_without_starting_a_reply(self):
        from astrbot.core.message.components import Image
        from tests.test_semantic_integration import animation
        semantic = self.app.tools.semantic
        cfg = semantic.store.settings()
        semantic.store.configure({"reactions_collection_group_enabled": True,
            "reactions_collection_group_probability": 1.0}, cfg["revision"])
        original = animation()
        event = self.group_event(identifier="listening-group-sticker", text="")
        event.message_obj.message.append(Image.fromBase64(base64.b64encode(original.content).decode("ascii")))
        semantic.gateway.port.analyze = AsyncMock(return_value={"suitable": True, "name": "挥手",
            "description": "挥手的聊天表情", "intents": "回应招呼"})
        observer = self.controller.session.core_reply_observations
        participation = self.app.tools.group_participation
        with patch.object(participation.admission, "candidate", return_value=""), \
                patch.object(observer, "media", wraps=observer.media) as collect:
            result = await self.controller.group_reply_candidate(event)
        self.assertTrue(result.is_success, result.failure)
        self.assertFalse(result.unwrap()["admitted"])
        collect.assert_called_once()
        self.assertTrue(collect.call_args.kwargs["collection_only"])
        prepared = collect.call_args.args[1]
        await asyncio.gather(*tuple(observer._pending))
        while semantic.background:
            await asyncio.gather(*tuple(semantic.background.values()))
        owner = semantic.reaction_owner(prepared["context"])
        items = semantic.store.list(owner, "reaction")
        self.assertEqual(len(items), 1, semantic.errors)
        self.assertEqual(items[0]["payload"]["collection_source"], "group_capture")
        self.assertEqual(semantic.reactions.captured(items[0]).content, original.content)
        self.assertEqual(len(semantic.reactions.captured(items[0]).frames), len(original.frames))
        self.assertEqual(semantic.store.reaction_collection_summary(owner)["totals"]["group_capture"]["added"], 1)
        self.assertEqual(self.app.runtime.read_all("SELECT batch_id FROM conversation_batches", ()), ())
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platforms["qq-main"].client._api._wsr_api.calls, [])
        self.assertFalse(self.app.state_maintenance.foreground_busy(prepared["context"]))

    async def test_listening_group_does_not_download_when_collection_is_disabled_or_not_sampled(self):
        from astrbot.core.message.components import Image
        from tests.test_semantic_integration import picture
        semantic = self.app.tools.semantic
        participation = self.app.tools.group_participation
        original = picture()
        for enabled in (False, True):
            cfg = semantic.store.settings()
            semantic.store.configure({"reactions_collection_group_enabled": enabled,
                "reactions_collection_group_probability": 0.0}, cfg["revision"])
            event = self.group_event(identifier="listening-disabled-" + str(enabled), text="")
            event.message_obj.message.append(Image.fromBase64(base64.b64encode(original.content).decode("ascii")))
            with patch.object(participation.admission, "candidate", return_value=""), \
                    patch("astrbot_host.core_reply_media.load_native_image", new_callable=AsyncMock) as download:
                result = await self.controller.group_reply_candidate(event)
                self.assertTrue(result.is_success, result.failure)
                self.assertFalse(result.unwrap()["admitted"])
                await asyncio.gather(*tuple(self.controller.session.core_reply_observations._pending))
                download.assert_not_awaited()

    async def test_native_group_samples_bind_learning_api_and_relationship_graph(self):
        from astrbot_host.group_reply_api import GroupReplyWebAPI
        prepared = await self.prepared("consumer-group-first", "今天一起去看星星")
        self.clock.advance(timedelta(seconds=1))
        second = await self.prepared("consumer-group-second", "好呀，今晚见", sender="201")
        context = second["context"]
        samples = self.app.expression_samples.collect(context, self.clock.now(), source_kind=INPUT_SAMPLE_SOURCE)
        self.assertEqual({sample.sender_id for sample in samples.samples}, {"200", "201"})
        self.assertEqual(self.app.expression_samples.new_inputs(context, self.clock.now())[0], 2)
        candidates = self.app.expression_style._group_candidates(self.clock.now())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0], context)
        self.assertIsNone(self.app.expression_style._bound(context, second["instruction"]))
        resolved = GroupReplyWebAPI._style_context(None, self.controller, context.scope.session_id,
            context.role_binding, context.scope)
        self.assertEqual(resolved, context)
        graph = self.app.messages.group_network.read(context)
        self.assertEqual({key: member["message_count"] for key, member in graph["members"].items()}, {"200": 1, "201": 1})
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(self.app.runtime.read_all("SELECT batch_id FROM conversation_batches", ()), ())
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_native_group_slang_learning_runs_in_background_and_rechecks_evidence(self):
        semantic = self.app.tools.semantic
        cfg = semantic.store.settings()
        semantic.store.configure({"enabled": True, "slang_enabled": True, "slang_learning": True,
            "slang_web_search_enabled": False}, cfg["revision"])
        entered, release = asyncio.Event(), asyncio.Event()
        prompts = []

        async def analyze(provider, prompt, images=()):
            prompts.append(prompt)
            entered.set()
            await release.wait()
            material = json.loads(prompt.rsplit("样例：", 1)[1])
            evidence_index = next(item["index"] for item in material["examples"]
                                  if "团建券" in item["text"])
            return [{"term": "团建券", "meaning": "群里用于团建的凭证", "usage": "讨论周末活动安排",
                     "confidence": 0.9, "evidence": [evidence_index]}]

        semantic.gateway.port.analyze = analyze
        inputs = (
            "今晚“观星局”几点开始？", "“观星局”九点开",
            "“摸鱼王”上线啦", "又是“摸鱼王”",
            "周末“团建券”怎么用", "“团建券”给谁了",
        )
        prepared = None
        for index, text in enumerate(inputs):
            prepared = await self.prepared("consumer-group-slang-" + str(index), text)
            if index < len(inputs) - 1:
                await asyncio.gather(*tuple(semantic.background.values()))
        await asyncio.wait_for(entered.wait(), timeout=3)
        self.assertTrue(any(key.startswith("native-slang:") for key in semantic.background))
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertIn('"candidates"', prompts[0])
        self.assertNotIn("普通聊天内容", prompts[0])
        release.set()
        await asyncio.gather(*tuple(semantic.background.values()))
        items = semantic.store.list(semantic.owner(prepared["context"]), "slang")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["payload"]["evidence"][0]["kind"], "native_input")
        self.assertTrue(semantic.slang.validate(items[0], prepared["context"]))
        self.assertEqual(len(prompts), 1)
        owner = semantic.owner(prepared["context"])
        with semantic.store.db() as db:
            db.execute("UPDATE learning SET checked_at=0,retry_after=0 WHERE owner=?", (owner,))
        failures = []

        async def fail_analysis(provider, prompt, images=()):
            failures.append(prompt)
            raise RuntimeError("synthetic model failure")

        semantic.gateway.port.analyze = fail_analysis
        with self.assertRaisesRegex(RuntimeError, "synthetic model failure"):
            await semantic.slang.observe(prepared["context"], prepared["turn_id"])
        with semantic.store.db() as db:
            cooldown = db.execute("SELECT retry_after FROM learning WHERE owner=?", (owner,)).fetchone()[0]
        self.assertGreater(cooldown, 0)
        await semantic.slang.observe(prepared["context"], prepared["turn_id"])
        self.assertEqual(len(failures), 1)
        require(self.app.messages.recalls.accept(MessageRecall.from_scope(prepared["context"].scope,
            prepared["event_id"], prepared["context"].actor.principal.native_account_id)))
        self.assertFalse(semantic.slang.validate(items[0], prepared["context"]))
        self.assertEqual(self.app.runtime.read_all("SELECT batch_id FROM conversation_batches", ()), ())


if __name__ == "__main__":
    unittest.main()
