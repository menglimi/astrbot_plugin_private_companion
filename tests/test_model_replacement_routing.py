"""Native provider discovery and sealed-input replacement routing."""

from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.multi_settings import MultiHostSettings
from companion_v2.adapters.astrbot.provider import UsageTariff
from companion_v2.conversation.foreground import PromptMaterial, SealedForegroundContext
from companion_v2.execution.models import TaskError, TaskResult
from companion_v2.model_gateway.failures import ModelFailure
from companion_v2.model_gateway.models import Amount, CapturedModelOutput, ProviderResponse, ToolCall, ToolExchange, ToolSpec
from companion_v2.model_gateway.replacement_rules import checked_rules, match_route
from companion_v2.model_gateway.routing import ModelRouter
from companion_v2.model_gateway.service import LocalModelGateway
from tests.multi_host_support import MultiFixture, multi_config
from tests.native_adapter_support import FakeProvider, NativeFixture, completion
from tests.test_model_gateway import ModelFixture


def additional_provider(provider_id="native-backup", **changes):
    return {"provider_id": provider_id, "max_output_tokens": 100,
            "input_microusd_per_million": 3000000,
            "cached_input_microusd_per_million": 1000000,
            "output_microusd_per_million": 5000000, **changes}


class NativeRoutingSettingsTests(unittest.TestCase):
    def test_multi_host_uses_one_provider_catalog_and_keeps_identity_pin(self):
        original = multi_config()
        routed = multi_config()
        routed["model"].update(additional_providers=[additional_provider()],
                               fallback_provider_id="native-backup")
        before, after = MultiHostSettings.parse(original), MultiHostSettings.parse(routed)
        self.assertEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(len(after.common.additional_providers), 1)
        self.assertEqual(after.common.additional_providers[0].tariff, UsageTariff(3000000, 1000000, 5000000))
        self.assertEqual(after.common.fallback_provider_id, "native-backup")
        self.assertFalse(hasattr(after, "additional_providers"))

    def test_primary_profile_and_fallback_duplicates_are_ignored(self):
        config = multi_config()
        config["model"].update(additional_providers=[additional_provider("native-model")],
                               fallback_provider_id="native-model")

        settings = MultiHostSettings.parse(config)

        self.assertEqual(settings.common.additional_providers, ())
        self.assertEqual(settings.common.fallback_provider_id, "")

    def test_invalid_saved_provider_profiles_fail_during_config_parse(self):
        invalid = ([additional_provider("backup", typo_rate=1)],
                   [additional_provider("backup", output_microusd_per_million=-1)],
                   [additional_provider(f"backup-{index}") for index in range(9)])
        for providers in invalid:
            with self.subTest(providers=providers), self.assertRaises(ValueError):
                config = multi_config()
                config["model"]["additional_providers"] = providers
                MultiHostSettings.parse(config)

    def test_live_provider_ids_are_deferred_to_the_loaded_astrbot_catalog(self):
        for changes in (
            {"fallback_provider_id": "loaded-later"},
            {"replacement_rules": '[{"keywords":["private"],"provider_id":"loaded-later"}]'},
        ):
            with self.subTest(changes=changes):
                config = multi_config()
                config["model"].update(changes)
                self.assertEqual(MultiHostSettings.parse(config).common.provider_id, "native-model")

    def test_saved_provider_profiles_still_validate_their_limits(self):
        for changes in (
            {"additional_providers": [additional_provider(max_output_tokens=50)],
             "fallback_provider_id": "native-backup"},
            {"additional_providers": [additional_provider(max_output_tokens=50)],
             "replacement_rules": '[{"keywords":["private"],"provider_id":"native-backup"}]'},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                config = multi_config()
                config["model"].update(changes)
                MultiHostSettings.parse(config)

    def test_invalid_selectors_cannot_silently_become_time_only_rules(self):
        window = {"timezone": "Asia/Shanghai", "start": "07:00", "end": "09:00"}
        for changes in ({"keywords": 7}, {"keywords": [7]}, {"enabled": "false"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                checked_rules(json.dumps([{"provider_id": "primary", "time_window": window, **changes}]))
        with self.assertRaises(ValueError):
            checked_rules(json.dumps([{"id": identifier, "provider_id": "primary", "keywords": ["x"]}
                                      for identifier in ("same", " same ")]))


class NativeMultiRoutingTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_new_astrbot_default_is_adopted_without_restarting_host(self):
        application = await self.start_host()
        added = FakeProvider()
        added.provider_config["id"] = "added-after-start"
        providers = {"native-model": self.native.provider, "added-after-start": added}
        self.native.get_provider_by_id = providers.get
        self.native.get_all_providers = lambda: list(providers.values())
        self.native.get_using_provider = lambda _umo=None: added
        context, _instruction = await self.controller.session.synchronize(
            self.controller.settings.target("first"))

        provider_id, _model, selected = application.router.selected_route(
            context, "proactive.compose", NS(event=NS(), context=NS(materials=())),
            at=self.clock.now())

        self.assertEqual(provider_id, "added-after-start")
        self.assertEqual(selected[0].binding.provider, "added-after-start")
        self.assertIn("added-after-start", application.router.providers)

    async def test_probe_and_start_verify_additional_native_provider(self):
        backup = FakeProvider()
        backup.provider_config["id"] = "native-backup"
        providers = {"native-model": self.native.provider, "native-backup": backup}
        self.native.get_provider_by_id = providers.get
        self.native.get_all_providers = lambda: list(providers.values())
        config = multi_config()
        config["model"].update(additional_providers=[additional_provider()],
                               fallback_provider_id="native-backup")
        self.controller = self.make_controller(config)
        self.assertTrue((await self.controller.probe()).is_success)
        self.assertTrue((await self.controller.initialize_profile()).is_success)
        self.assertTrue((await self.controller.start()).is_success)
        self.assertEqual([provider.provider_id for provider in self.controller.session.models],
                         ["native-model", "native-backup"])

    async def test_missing_saved_provider_profile_does_not_block_loaded_provider(self):
        config = multi_config()
        config["model"].update(additional_providers=[additional_provider()],
                               fallback_provider_id="native-backup",
                               replacement_rules='[{"provider_id":"native-backup","keywords":["private"]}]')
        self.controller = self.make_controller(config)
        self.assertTrue((await self.controller.probe()).is_success)
        self.assertTrue((await self.controller.initialize_profile()).is_success)
        self.assertTrue((await self.controller.start()).is_success)
        self.assertEqual([model.provider_id for model in self.controller.session.models], ["native-model"])
        reply = self.controller.session.application.router.profiles["conversation.reply"]
        self.assertEqual(reply.default_fallback, "")
        self.assertEqual(reply.replacement_rules, "[]")


class ModelReplacementTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.providers = tuple(replace(self.provider(name), capabilities=("text", "tools"),
                                       supports_model_override=True)
                               for name in ("primary", "fallback", "special"))
        self.router = ModelRouter(self.configuration, self.profiles, self.providers)
        self.router.start(expected_generation=1)
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)

    async def test_explicit_refusal_uses_fallback_after_accounting_for_both_calls(self):
        self.patch_model(fallback_on_refusal=True)
        self.responses["primary"] = ProviderResponse("generated", captured_output=CapturedModelOutput("", "refusal"),
                                                     usage=Amount(12, 30), receipt_ref="primary:refusal")
        result = await self.gateway.generate(self.model_run(), self.request())
        self.assertEqual(result.draft.text, "private draft")
        self.assertEqual([name for name, _ in self.called], ["primary", "fallback"])
        observed = self.gateway.observe(self.context, "task-1").unwrap()
        self.assertTrue(observed["plan"]["fallback_on_refusal"])
        self.assertEqual([call["state"] for call in observed["calls"]], ["discarded", "generated"])
        self.assertEqual(observed["calls"][0]["failure"]["code"], ModelFailure.REFUSAL.value)
        self.assertEqual([call["actual_tokens"] for call in observed["calls"]], [12, 15])
        self.assertTrue(all(account["spent_tokens"] == 27 for account in self.accounts()))

    async def test_refusal_without_opt_in_stops_and_does_not_call_fallback(self):
        self.responses["primary"] = ProviderResponse("generated", captured_output=CapturedModelOutput("", "refusal"),
                                                     usage=Amount(12, 30), receipt_ref="primary:refusal")
        with self.assertRaises(TaskError) as failure:
            await self.gateway.generate(self.model_run(), self.request())
        self.assertEqual(failure.exception.code, "model_refused")
        self.assertEqual([name for name, _ in self.called], ["primary"])
        self.assertEqual(self.call_rows()[0]["state"], "discarded")
        self.assertTrue(all(account["spent_tokens"] == 12 for account in self.accounts()))

    async def test_sealed_input_selects_model_and_confirmed_nonstart_uses_plain_fallback(self):
        self.patch_model(replacement_rules=json.dumps([{
            "id": "private", "provider_id": "special", "model": "creative",
            "keywords": ["private"]}]))
        self.responses["special"] = ProviderResponse("not_started", usage=Amount(0, 0),
                                                      receipt_ref="special:not-started")
        result = await self.gateway.generate(self.model_run(), self.request())
        self.assertEqual(result.draft.text, "private draft")
        self.assertEqual([(name, envelope.model_override) for name, envelope in self.called],
                         [("special", "creative"), ("fallback", "")])
        plan = self.gateway.observe(self.context, "task-1").unwrap()["plan"]
        self.assertEqual(plan["replacement"]["rule_id"], "private")
        self.assertEqual(plan["replacement"]["input_basis"], "sealed_current_inputs")
        self.assertNotIn("private material", json.dumps(plan))

    def test_only_current_and_same_scope_merged_inputs_can_trigger_replacement(self):
        self.patch_model(replacement_rules='[{"provider_id":"special","keywords":["private"]}]')
        run, request = self.model_run(), self.request()
        source = request.event.source
        foreign = replace(source, scope=replace(source.scope, conversation_id="another-window"))
        materials = (
            replace(request.context.materials[0], payload="ordinary input"),
            PromptMaterial("memory", "recalled", "private memory", source),
            PromptMaterial("tool", "tool_result", "private result", source),
            PromptMaterial("conversation", "current_source", "private mismatched source", foreign),
            PromptMaterial("conversation", "merged_input:foreign", "private merged input", foreign),
        )
        request = replace(request, role_instruction="private persona", instruction="private instruction",
                          context=SealedForegroundContext(materials, (), ()))
        plan = self.router.resolve(run, request)
        self.assertEqual([p.binding.provider for p in plan.providers], ["primary", "fallback"])
        self.assertIsNone(plan.audit["replacement"])
        request = replace(request, context=replace(request.context, materials=materials + (
            PromptMaterial("conversation", "merged_input:current", "private merged input", source),)))
        plan = self.router.resolve(run, request)
        self.assertEqual(plan.providers[0].binding.provider, "special")

    def test_priority_time_and_fallback_deduplication_use_one_task_time(self):
        self.patch_model(replacement_rules=json.dumps([
            {"id": "ordinary", "provider_id": "special", "keywords": ["private"]},
            {"id": "morning", "provider_id": "fallback", "priority": 5, "keywords": ["private"],
             "time_window": {"timezone": "Asia/Shanghai", "start": "07:00", "end": "09:00", "weekdays": [0]}},
        ]))
        run, request = self.model_run(), self.request()
        plan = self.router.resolve(run, request)
        self.assertEqual([p.binding.provider for p in plan.providers], ["fallback"])
        self.assertEqual(plan.audit["replacement"]["reason"], "keyword_and_time")
        self.assertEqual(plan.audit["replacement"]["evaluated_at"], self.clock.now().isoformat())
        later = match_route(checked_rules(plan.snapshot.values["model.fixture.read.replacement_rules"]),
                            "private material", self.clock.now() + timedelta(hours=2))
        self.assertEqual(later.rule_id, "ordinary")

    async def test_tool_continuation_keeps_route_after_configuration_changes(self):
        self.patch_model(replacement_rules='[{"provider_id":"special","model":"creative","keywords":["private"]}]')
        run, request = self.model_run(), self.request()
        call = ToolCall("read", "memo.list", "{}")
        session = self.gateway.open_session(run, request, tools=(ToolSpec("memo.list", "Read memos", '{}'),))
        self.responses["special"] = ProviderResponse("generated", usage=Amount(15, 40),
                                                     receipt_ref="tool-read", tool_calls=(call,))
        await session.generate(request)
        self.patch_model(provider="fallback", fallback="", replacement_rules="[]")
        self.responses.pop("special")
        await session.generate(request, history=(ToolExchange(call, '{"state":"succeeded"}'),))
        self.assertEqual([(name, envelope.model_override) for name, envelope in self.called],
                         [("special", "creative"), ("special", "creative")])
        self.tasks.finish(run.claim, TaskResult("succeeded")).unwrap()
        await self.gateway.generate(self.model_run("next"), self.request())
        self.assertEqual((self.called[-1][0], self.called[-1][1].model_override), ("fallback", ""))

    async def test_unknown_replacement_call_never_dispatches_fallback(self):
        self.patch_model(replacement_rules='[{"provider_id":"special","keywords":["private"]}]')
        self.responses["special"] = ProviderResponse("unknown")
        with self.assertRaises(TaskError) as failure:
            await self.gateway.generate(self.model_run(), self.request())
        self.assertEqual(failure.exception.code, "model_usage_unknown")
        self.assertEqual([name for name, _ in self.called], ["special"])

    def test_invalid_rule_target_is_rejected_before_dispatch(self):
        run, request = self.model_run(), self.request()
        for rule in (
            {"provider_id": "missing", "keywords": ["private"]},
            {"provider_id": "special", "keywords": ["private"], "model": "creative"},
        ):
            with self.subTest(rule=rule):
                self.patch_model(replacement_rules=json.dumps([rule]))
                self.router.providers = {p.binding.provider: replace(p, supports_model_override=False)
                                         for p in self.providers}
                with self.assertRaises(TaskError) as failure:
                    self.router.resolve(run, request)
                self.assertEqual(failure.exception.code, "model_route_invalid")
        self.assertEqual(self.called, [])


class NativeModelOverrideTests(NativeFixture, unittest.IsolatedAsyncioTestCase):
    async def test_explicit_native_refusal_is_classified_even_when_host_parser_rejects_it(self):
        envelope = await self.envelope()
        refused = completion("", finish="content_filter")
        async def respond(*_):
            return refused
        self.native.provider.client.state.behavior = respond
        response = await self.native_model.generate(envelope)
        self.assertEqual(response.captured_output, CapturedModelOutput("", "refusal"))
        self.assertEqual(response.usage, Amount(40, 90, input_tokens=30, cached_input_tokens=10, output_tokens=10))

        refused.choices[0].message.refusal = "blocked"
        refused.choices[0].finish_reason = "stop"
        async def rejected_parser(*_):
            raise ValueError("host parser rejected the explicit refusal")
        with patch.object(FakeProvider, "_parse_openai_completion", rejected_parser):
            response = await self.native_model.generate(envelope)
        self.assertEqual(response.captured_output, CapturedModelOutput("", "refusal"))
        self.assertEqual(response.usage, Amount(40, 90, input_tokens=30, cached_input_tokens=10, output_tokens=10))

    async def test_override_reaches_native_payload_without_changing_provider_configuration(self):
        envelope = await self.envelope(model_override="creative")
        original = self.native.provider.get_model()
        response = await self.native_model.generate(envelope)
        self.assertEqual(response.status, "generated")
        self.assertEqual(self.native.provider.client.state.calls[0][0]["model"], "creative")
        self.assertEqual(self.native.provider.get_model(), original)
