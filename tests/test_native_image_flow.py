"""Native generation/edit -> governed adoption -> local sender and restart."""

import asyncio
import base64
from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from companion_v2.capabilities.models import CapabilityAuthorizationState
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.conversation.delivery import DeliveryReceipt
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.conversation.media.reply import ImageReplyDraft
from companion_v2.execution.models import TaskContext, TaskError, digest, encode
from companion_v2.foundation.refs import AudienceRef
from companion_v2.model_gateway.models import Amount, BudgetPolicy, ProviderResponse, ToolCall
from tests.native_image_support import NativeImageFixture
from tests.test_conversation_images import image_bytes
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE
from tests.test_role_tool_loop import answered, requested


class NativeImageFlowTests(NativeImageFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.image_directory = self.root.parent / "image-native-v2"
        self.native_registry = self.create_image_port(self.image_directory, self.clock.now)
        self.providers = tuple(replace(p, capabilities=("text", "tools", "image")) for p in self.providers)
        self.sender = replace(self.sender, supports_images=True)
        self.package = self.native_registry.declarations()[0].descriptor.package
        self.capabilities = CapabilityRepository(self.runtime)
        self.capabilities.register_descriptor(self.package, registered_at=NOW).unwrap()
        self.capabilities.acquire(extension_id=self.package.extension_id, package_id=self.package.package_id,
            package_version=self.package.package_version, expected_revision=0, observed_at=NOW).unwrap()
        self.capabilities.set_role_enabled(extension_id=self.package.extension_id, role=self.window.role,
            bot_id=SCOPE.bot_id, enabled=True, expected_revision=0, observed_at=NOW).unwrap()
        for capability in self.package.capabilities:
            self.consent(capability, CapabilityAuthorizationState.ACTIVE, 0)
        self.model_behavior = self.image_model

    def consent(self, capability, state, revision):
        return self.capabilities.set_authorization(extension_id=self.package.extension_id, capability_id=capability,
            subject=self.important.subject, scope=SCOPE, state=state, expected_revision=revision,
            observed_at=self.clock.now()).unwrap()

    async def image_model(self, envelope):
        if not envelope.tool_history:
            return requested(ToolCall("generate", "image.generate", encode({"prompt": "合成红色图片"})))
        if not envelope.original.images:
            return answered("尚未得到可采用的图片。")
        return self.image_answer(envelope)

    def image_answer(self, envelope, text="这是本次生成的图片。"):
        return ProviderResponse("generated", ImageReplyDraft(
            text, (), images=(envelope.original.images[-1].source,)),
                                Amount(30, 30), "model-image-answer")

    async def image_host(self, **options):
        options.setdefault("policy", ConversationPolicy(0, 0, 180, 0))
        return await self.start(self.host(tool_port=self.native_registry, **options))

    async def restart_images(self, host):
        await host.application.close()
        await self.image_owner.close()
        old_generation = self.image_owner.generation
        self.native_registry = self.create_image_port(self.image_directory, self.clock.now)
        self.assertEqual(self.image_owner.generation, old_generation)
        new = self.host(runtime=self.open_runtime(), tool_port=self.native_registry,
                        policy=ConversationPolicy(0, 0, 180, 0))
        return await self.start(new, 1, 1)

    async def complete_image(self):
        host = await self.image_host()
        batch = host.receive(self.event()).unwrap()
        state = await self.done(host.application, batch)
        self.assertEqual(state["state"], "complete", dict(state))
        return host, batch, self.context(host.application, batch)

    async def test_native_generation_is_adopted_and_sent_once_with_original_receipt(self):
        host, batch, context = await self.complete_image()
        app = host.application
        self.assertEqual(len(self.image_backend.calls), 1)
        self.assertEqual(len(self.called), 2)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(base64.b64decode(self.sent[0].images[0].data_base64), image_bytes("red"))
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) FROM conversation_inputs", ())[0], 1)
        row = self.runtime.read_one("SELECT * FROM extension_tool_invocations", ())
        self.assertEqual((row["state"], row["effect"]), ("succeeded", "committed"))
        self.assertIsNotNone(row["receipt_ref"])
        history = app.messages.history(context).unwrap()["items"]
        self.assertTrue(history[0]["body_available"])
        self.assertEqual(history[0]["images"][0]["content_hash"], self.sent[0].images[0].descriptor.content_hash)
        sources = app.runtime.read_all("SELECT payload_json FROM sources WHERE owner IN ('image.native','conversation.generated_images')", ())
        self.assertTrue(sources)
        self.assertNotIn("data:image/", str([tuple(r) for r in sources]))
        self.assertEqual(host.receive(self.event()).unwrap(), batch)
        self.assertEqual(len(self.sent), 1)

    async def test_daily_outfit_generates_from_persona_agenda_and_weather_with_an_empty_wardrobe(self):
        from companion_v2.agenda.planning import planning_profile
        from companion_v2.configuration.registry import FieldRegistry
        from companion_v2.model_gateway.routing import configuration_fields
        from companion_v2.proactive.configuration import target_for as contact_target
        from companion_v2.proactive.external_materials import WeatherSnapshot
        from companion_v2.proactive.service import contact_profile
        from companion_v2.references.configuration import configuration_fields as reference_fields
        from companion_v2.references.management import ReferenceMaterialManagement
        from companion_v2.role_profile.configuration import target_for as profile_target
        from companion_v2.role_work.state.management import StateManagement
        from tests.test_calendar_planning import output as planned_event
        from tests.test_role_state import calendar_data
        from tests.proactive_support import ContactFixture

        class Weather:
            async def fetch(self, location):
                return WeatherSnapshot("open-meteo", location.name, NOW, 14, "小雨", None, 12, ())

        self.grant(self.important, SCOPE, "extension.tool.invoke")
        self.grant(self.important, SCOPE, "proactive.contact")
        self.profiles += (planning_profile("primary", max_output_tokens=100), contact_profile("primary"))
        self.registry = FieldRegistry(version="outfit-flow", fields=configuration_fields(self.profiles))
        host = await self.image_host(weather_port=Weather())
        app = host.application
        context = TaskContext(self.important, self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
                              AudienceRef(SCOPE, (self.important.subject,)))
        state = StateManagement(app, context)
        config = state.status(self.core).unwrap()
        state.command(self.core, {"action": "configure", "operation_key": "outfit-weather",
            "edit_token": config["edit_token"], "role_edit_token": config["role_edit_token"], "changes": {
                "planning_enabled": True,
                "planning_weather_enabled": True,
                "planning_weather_location": json.dumps({"name": "杭州"}, ensure_ascii=False),
            }}).unwrap()
        profile = app.messages.role_profile_context
        target = profile_target(context)
        profile.owner.write(target, profile.fields, changes={"role.profile.name": "澄汐"},
                            reset=(), expected_revision=profile.owner.read(target, profile.fields).revision,
                            observed_at=NOW)
        persona = "你是澄汐，留着齐肩黑发，平时喜欢宽松便装。"
        app.state_maintenance.bind(context, persona)
        app.calendar_planning.next_checks[context.key] = NOW + timedelta(days=1)
        app.calendar.command(context, identifier="outfit-user", action="create",
            expected_revision=0, operation_key="outfit-user-agenda",
            data=calendar_data(kind="user_report", title="用户自己的安排"))
        settings = app.reference_settings
        target, fields = settings.target(context), reference_fields()
        settings.owner.write(target, fields,
            changes={"photo.reference.daily_outfit_enabled": True}, reset=(),
            expected_revision=settings.owner.read(target, fields).revision, observed_at=NOW)

        manager = ReferenceMaterialManagement(app)
        planner_started, plan_can_finish = asyncio.Event(), asyncio.Event()

        async def plan_day(envelope):
            planner_started.set()
            await plan_can_finish.wait()
            activity = planned_event(title="乘车去展馆", detail="要步行穿过室外广场")
            return answered(json.dumps({"items": [activity]}, ensure_ascii=False))

        self.model_behavior = plan_day
        planner = app.calendar_planning
        role_settings = app.role_state.settings(context)[0]
        mode, slot, _ = planner.opportunity(context, role_settings, NOW)
        planning_task = asyncio.create_task(planner.run(context, persona, mode=mode, slot=slot, automatic=True))
        try:
            await asyncio.wait_for(planner_started.wait(), timeout=self.wait_seconds)
            app.daily_outfit.next_check = None
            app.daily_outfit.advance()
            self.assertEqual(manager.daily_outfit_photo(self.core, context, at=NOW)["state"], "empty")
        finally:
            plan_can_finish.set()
            plan_result = await planning_task
        self.assertEqual(plan_result["state"], "succeeded", plan_result)
        app.daily_outfit.next_check = None
        app.daily_outfit.advance()
        await self.until(lambda: not app.daily_outfit.workers)
        result = manager.daily_outfit_photo(self.core, context, at=NOW)
        self.assertEqual(result["state"], "succeeded", result)
        self.assertTrue(result["photo"]["thumbnail"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(result["photo"]["activities"], ["乘车去展馆"])
        self.assertEqual(len(self.image_backend.calls), 1)
        invocation, references = self.image_backend.calls[0]
        self.assertFalse(references)
        prompt = invocation.prompt
        self.assertIn('"name":"澄汐"', prompt)
        self.assertIn("齐肩黑发", prompt)
        self.assertIn("步行穿过室外广场", prompt)
        self.assertIn('"temperature_c":14', prompt)
        self.assertIn('"condition":"小雨"', prompt)
        self.assertNotIn("用户自己的安排", prompt)
        self.assertEqual(app.messages.image_quota._used(context, "proactive", NOW), 1)
        self.assertEqual(app.daily_outfit.request(context)["state"], "succeeded")
        self.assertEqual(app.daily_outfit.status(context, at=NOW + timedelta(days=1))["state"], "empty")
        self.assertEqual(len(self.image_backend.calls), 1)
        request = manager.generate_daily_outfit_photo
        operation = "outfit-photo-synthetic"
        self.assertEqual(request(self.core, context, at=NOW, operation_key=operation)["state"], "created")
        self.assertEqual(request(self.core, context, at=NOW, operation_key=operation)["state"], "created")
        await self.until(lambda: not app.daily_outfit.workers)
        self.assertEqual(request(self.core, context, at=NOW, operation_key=operation)["state"], "succeeded")
        self.assertEqual(len(self.image_backend.calls), 2)

        async def share(envelope):
            self.assertIn("角色当天已经生成的穿搭图", envelope.original.user_instruction)
            return answered('{"decision":"send","text":"今天的搭配想试试轻便一点的外套。"}')

        self.model_behavior = share
        ContactFixture.settings(self, app, {"enabled": True, "automatic_enabled": True,
            "life.enabled": True, "proactive_share_probability": 100,
            "proactive_hour_activity_curve": ",".join(["1.00"] * 24)},
            target=contact_target(context), actor=self.important).unwrap()
        app.notify()
        await self.until(lambda: len(self.sent) == 2)
        self.assertEqual(base64.b64decode(self.sent[1].images[0].data_base64), image_bytes("red"))
        self.assertEqual(len(self.image_backend.calls), 2)

    def use_nai(self):
        from astrbot_host.native_extensions.image_native.output_routing import OutputRoutingBackend
        path = self.root.parent / "nai-generated.png"

        async def generate(owner, request):
            self.assertNotIn("api_key", request)
            path.write_bytes(image_bytes("red"))
            return {"handled": True, "image_path": str(path)}

        self.nai_api = NS(status=lambda: {"call_mode": "direct"},
            capability_status=lambda owner: {"available": True, "reason": ""},
            generate_for_companion=AsyncMock(side_effect=generate))
        self.image_context.get_registered_star = lambda name: NS(
            activated=True, star_cls=NS(extension_api=self.nai_api))
        self.image_owner.backend = OutputRoutingBackend(
            {"enabled": True, "backend": "nai", "operation_routes": {"generate": ["nai"], "edit": []}},
            self.root.parent / "nai-transport", context=self.image_context)

    def nai_budget(self):
        return BudgetPolicy(*(Amount(100000, 100000) for _ in range(5)), enabled=False)

    async def test_nai_generation_is_accounted_adopted_and_counted_once_in_the_daily_quota(self):
        self.use_nai()
        host = await self.image_host(budget=self.nai_budget())
        batch = host.receive(self.event()).unwrap()
        self.assertEqual((await self.done(host.application, batch))["state"], "complete")
        context = self.context(host.application, batch)
        self.nai_api.generate_for_companion.assert_awaited_once()
        self.assertEqual(len(self.sent[0].images), 1)
        rows = self.runtime.read_all(
            "SELECT * FROM model_calls WHERE json_extract(provider_json, '$.provider') = 'image.nai'", ())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "generated")
        self.assertIsNone(rows[0]["actual_cost"])
        gate = host.application.messages.image_quota
        self.assertEqual(gate._used(context, gate._classify_context(context), self.clock.now()), 1)
        self.assertEqual(host.receive(self.event()).unwrap(), batch)
        self.nai_api.generate_for_companion.assert_awaited_once()

    async def test_core_reply_nai_call_uses_the_attached_supervisor_and_the_same_quota(self):
        from astrbot_host.core_reply_tool_execution import executor_for
        self.use_nai()
        host = await self.image_host(budget=self.nai_budget())
        # Capture a real bound context without creating a Companion reply task.
        context = TaskContext(self.important, self.identity.resolve_role(SCOPE, observed_at=self.clock.now()).unwrap(),
                              AudienceRef(SCOPE, (self.important.subject,)))
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.conversation.media.quota import GROUP, QUOTA_FIELDS, ImageQuotaConfigurationOwner
        quota = ImageQuotaConfigurationOwner(self.runtime)
        target = ConfigTarget(SCOPE.installation_id, ConfigLayer.ROLE, context.role_binding.role, bot_id=SCOPE.bot_id)
        gate = host.application.messages.image_quota
        quota.write(target, QUOTA_FIELDS,
            changes={GROUP + "." + gate._classify_context(context) + "_max_daily": 1}, reset=(),
            expected_revision=quota.read(target, QUOTA_FIELDS).revision, observed_at=self.clock.now())
        owner = executor_for(host.application)
        self.addAsyncCleanup(owner.close)
        session = NS(context=context, turn_id="synthetic-core-turn", assert_current=lambda: None)
        result = await owner.execute(session, "synthetic-core-image", "image.generate", {"prompt": "1girl, red dress"})
        self.assertEqual(result["state"], "succeeded", result)
        self.assertEqual(len(result["_images"]), 1)
        self.nai_api.generate_for_companion.assert_awaited_once()
        row = self.runtime.read_one(
            "SELECT * FROM model_calls WHERE json_extract(provider_json, '$.provider') = 'image.nai'", ())
        self.assertEqual(row["state"], "generated")
        gate = host.application.messages.image_quota
        self.assertEqual(gate._used(context, gate._classify_context(context), self.clock.now()), 1)
        with self.assertRaises(TaskError) as caught:
            await owner.execute(session, "another-core-image", "image.generate", {"prompt": "1girl"})
        self.assertEqual(caught.exception.code, "image_quota_exhausted")
        self.nai_api.generate_for_companion.assert_awaited_once()

    async def test_hard_budget_nai_refusal_creates_no_remote_call_and_consumes_no_image_quota(self):
        self.use_nai()
        host = await self.image_host()
        batch = host.receive(self.event()).unwrap()
        self.assertEqual((await self.done(host.application, batch))["state"], "complete")
        self.nai_api.generate_for_companion.assert_not_awaited()
        row = self.runtime.read_one(
            "SELECT * FROM model_calls WHERE json_extract(provider_json, '$.provider') = 'image.nai'", ())
        self.assertEqual(row["state"], "not_started")
        self.assertEqual(row["actual_cost"], 0)
        gate = host.application.messages.image_quota
        context = self.context(host.application, batch)
        self.assertEqual(gate._used(context, gate._classify_context(context), self.clock.now()), 0)
        observed = host.application.messages.generated_images.observability_page(context, self.clock.now())
        self.assertEqual(observed["items"][0]["error_code"], "image_usage_unavailable")

    async def test_edit_uses_same_origin_saved_artifact_and_only_selected_output_is_sent(self):
        async def model(envelope):
            if len(envelope.tool_history) != 1:
                return await self.image_model(envelope)
            native = next(json.loads(m.payload)["native"] for m in envelope.original.context.materials if m.owner == "image.native")
            artifact_id = native["artifacts"][0]["artifact_id"]
            return requested(ToolCall("edit", "image.edit", encode({"prompt": "改为蓝色", "artifact_id": artifact_id})))
        self.model_behavior = model
        await self.complete_image()
        self.assertEqual(len(self.image_backend.calls), 2)
        self.assertEqual(len(self.image_backend.calls[1][1]), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(base64.b64decode(self.sent[0].images[0].data_base64), image_bytes("blue"))
        self.assertEqual(len(self.called[-1].original.images), 2)

    async def test_unselected_generation_stays_saved_without_automatic_image_send(self):
        async def model(envelope):
            return await self.image_model(envelope) if not envelope.tool_history else answered("已保存，暂时不附图。")
        self.model_behavior = model
        host, batch, context = await self.complete_image()
        self.assertEqual(len(host.application.messages.generated_images.read_batch(batch, context, self.clock.now())), 1)
        self.assertFalse(getattr(self.sent[0], "images", ()))

    async def test_unknown_backend_produces_no_image_and_never_replays(self):
        self.image_backend.unknown = True
        host, batch, context = await self.complete_image()
        self.assertEqual(len(self.image_backend.calls), 1)
        self.assertEqual(host.application.messages.generated_images.read_batch(batch, context, self.clock.now()), ())
        self.assertFalse(getattr(self.sent[0], "images", ()))
        row = self.runtime.read_one("SELECT state,effect FROM extension_tool_invocations", ())
        self.assertEqual(tuple(row), ("unknown", "unknown"))
        self.assertEqual(host.receive(self.event()).unwrap(), batch)
        self.assertEqual(len(self.image_backend.calls), 1)

    async def test_revocation_before_final_adoption_prevents_entire_send(self):
        async def model(envelope):
            response = await self.image_model(envelope)
            if envelope.tool_history:
                self.consent("image.generate", CapabilityAuthorizationState.REVOKED, 1)
            return response
        self.model_behavior = model
        host = await self.image_host()
        batch = host.receive(self.event()).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.image_backend.calls), 1)

    async def test_corrupt_original_blocks_adoption_and_history(self):
        host, batch, context = await self.complete_image()
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_image_payloads SET content = ?", (b"corrupt",))
        with self.assertRaises(TaskError):
            host.application.messages.generated_images.read_batch(batch, context, self.clock.now())
        self.assertFalse(host.application.messages.history(context).unwrap()["items"][0]["body_available"])

    async def test_default_restart_reads_pending_image_and_original_receipt_without_regenerating(self):
        async def model(envelope):
            if not envelope.tool_history:
                return await self.image_model(envelope)
            return self.image_answer(envelope, "第一段\n这是生成图。")
        self.model_behavior = model
        host = await self.image_host(policy=ConversationPolicy(0, 0, 180, 5))
        batch = host.receive(self.event()).unwrap()
        await self.until(lambda: host.application.flow.row(batch)["reason"] == "part_spacing", seconds=8)
        original_receipt = tuple(self.runtime.read_one("SELECT operation_id,receipt_ref FROM extension_tool_invocations", ()))
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(getattr(self.sent[0], "images", ()))
        self.assertEqual(len(self.image_backend.calls), 1)
        new = await self.restart_images(host)
        self.advance(new.application, 5)
        self.assertEqual((await self.done(new.application, batch))["state"], "complete")
        self.assertEqual(len(self.called), 2)
        self.assertEqual(self.image_backend.calls, [])
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(base64.b64decode(self.sent[-1].images[0].data_base64), image_bytes("red"))
        restored = tuple(new.application.runtime.read_one("SELECT operation_id,receipt_ref FROM extension_tool_invocations", ()))
        self.assertEqual(restored, original_receipt)

    async def test_unknown_send_remains_unknown_after_restart_without_resending(self):
        async def send(envelope):
            return self.receipt(envelope, "unknown")
        self.send_behavior = send
        host = await self.image_host()
        batch = host.receive(self.event()).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(len(self.sent), 1)
        new = await self.restart_images(host)
        self.advance(new.application)
        await self.done(new.application, batch)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.image_backend.calls, [])
        self.assertEqual(len(self.called), 2)
        app = new.application
        part = app.runtime.read_one("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,))
        self.assertEqual(part["state"], "unknown")
        context = self.context(app, batch)
        receipt = DeliveryReceipt(part["part_id"], part["operation_id"], context.key, part["payload_hash"],
            self.sender.binding, "delivered", "late-native-image-ack", "synthetic-native-image", self.clock.now())
        self.assertEqual(app.record_receipt(replace(receipt, payload_hash=digest(part["text"]))).failure.code,
                         "message_receipt_mismatch")
        app.record_receipt(receipt).unwrap()
        app.reconcile_delivery(context, part["part_id"]).unwrap()
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(len(app.messages.history(context).unwrap()["items"][0]["images"]), 1)
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
