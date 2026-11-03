"""Per-session AstrBot defaults and explicit task routes use the same model plan."""

from dataclasses import replace
from types import SimpleNamespace as NS
import unittest

from astrbot_host.model_settings import conversation_provider_resolver
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.configuration.service import ConfigurationService
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind
from companion_v2.model_gateway.routing import ModelRouter, configuration_fields
from companion_v2.model_gateway.models import Amount, ProviderResponse
from companion_v2.model_gateway.service import LocalModelGateway
from tests.test_model_gateway import ModelFixture


class HostModelDefaultRoutingTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.profiles = (replace(self.profiles[0], default_fallback=""), self.profiles[1])
        fields = tuple(field for field in self.registry.fields.values()
                       if not field.field_id.startswith("model.fixture."))
        self.registry = FieldRegistry(version="host-model-defaults.v1",
                                      fields=fields + configuration_fields(self.profiles))
        self.configuration = ConfigurationService(self.runtime, self.registry)
        self.selected = "fallback"
        self.router = ModelRouter(self.configuration, self.profiles, self.providers,
            default_provider_resolver=lambda context, profile:
                self.selected if profile.kind == "fixture.read" else None)
        self.router.start(expected_generation=1)
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)

    async def test_host_default_dispatches_without_changing_saved_configuration(self):
        before = self.configuration.read(self.context.actor, target=self.window,
            group_id="model.fixture.read", observed_at=self.clock.now()).unwrap()
        run = self.model_run()
        request = self.request()
        session = self.gateway.open_session(run, request)
        completion = await session.generate(request)

        self.assertEqual([name for name, _ in self.called], ["fallback"])
        self.assertEqual(completion.draft.text, "private draft")
        self.assertEqual(session.plan.snapshot.values, before.values)
        self.assertEqual(session.plan.audit["fields"]["model.fixture.read.provider"]["source"],
                         "host_conversation")
        after = self.configuration.read(self.context.actor, target=self.window,
            group_id="model.fixture.read", observed_at=self.clock.now()).unwrap()
        self.assertEqual(after.values, before.values)
        status = self.configuration.application_status(self.context.actor, target=self.window,
            group_id="model.fixture.read", observed_at=self.clock.now()).unwrap()
        self.assertEqual(status.state, "applied")
        self.assertEqual(status.adopted_values, before.values)

    async def test_explicit_primary_dispatches_with_inherited_host_fallback(self):
        self.patch_model(provider="primary")
        self.responses["primary"] = ProviderResponse("not_started", usage=Amount(0, 0),
                                                     receipt_ref="not-started")

        completion = await self.gateway.generate(self.model_run(), self.request())

        self.assertEqual([name for name, _ in self.called], ["primary", "fallback"])
        self.assertEqual(completion.draft.text, "private draft")
        self.assertEqual(self.gateway.observe(self.context, "task-1").unwrap()["plan"]
                         ["fields"]["model.fixture.read.fallback"]["source"], "host_conversation")

    def test_saved_configuration_changes_still_reject_an_obsolete_plan(self):
        run = self.model_run()
        plan = self.router.resolve(run, self.request())
        self.patch_model(prompt="Changed after resolution")

        with self.assertRaises(TaskError) as caught:
            self.router.adopt(run, plan)

        self.assertEqual(caught.exception.code, "configuration_revision_conflict")
        self.assertEqual(self.called, [])

    def test_new_plans_follow_the_host_and_the_original_plan_keeps_its_provider(self):
        plan = self.router.resolve(self.model_run(), self.request())
        self.assertEqual([provider.binding.provider for provider in plan.providers], ["fallback"])
        self.assertEqual(plan.audit["fields"]["model.fixture.read.provider"]["source"], "host_conversation")
        self.assertEqual(plan.audit["provider_default"], "fallback")

        self.selected = "primary"
        provider, _, _ = self.router.selected_route(self.context, "fixture.read", self.request(),
                                                    at=self.clock.now())
        self.assertEqual(provider, "primary")
        self.assertEqual(plan.providers[0].binding.provider, "fallback")

    def test_explicit_task_provider_keeps_precedence_and_inherits_the_host_fallback(self):
        self.patch_model(provider="primary")
        plan = self.router.resolve(self.model_run(), self.request())
        self.assertEqual([provider.binding.provider for provider in plan.providers], ["primary", "fallback"])
        self.assertTrue(plan.snapshot.fields["model.fixture.read.provider"].explicit)
        self.assertEqual(plan.snapshot.fields["model.fixture.read.fallback"].value, "primary")

    def test_other_tasks_keep_the_plugin_default(self):
        provider, _, _ = self.router.selected_route(self.context, "fixture.background", self.request(),
                                                    at=self.clock.now())
        self.assertEqual(provider, "primary")

    def test_explicit_primary_and_fallback_work_without_a_host_default(self):
        self.patch_model(provider="primary", fallback="fallback")
        self.selected = "unloaded-host-model"
        plan = self.router.resolve(self.model_run(), self.request())
        self.assertEqual([provider.binding.provider for provider in plan.providers], ["primary", "fallback"])
        provider, _, providers = self.router.selected_route(self.context, "fixture.read", self.request(),
                                                            at=self.clock.now())
        self.assertEqual(provider, "primary")
        self.assertEqual([item.binding.provider for item in providers], ["primary", "fallback"])

    def test_explicitly_disabled_fallback_does_not_require_a_host_default(self):
        self.patch_model(provider="primary", fallback="")
        self.selected = "unloaded-host-model"
        plan = self.router.resolve(self.model_run(), self.request())
        self.assertEqual([provider.binding.provider for provider in plan.providers], ["primary"])

    def test_missing_host_adapter_does_not_silently_select_the_plugin_model(self):
        self.selected = "unloaded-host-model"
        with self.assertRaises(TaskError) as caught:
            self.router.resolve(self.model_run(), self.request())
        self.assertEqual(caught.exception.code, "model_provider_unavailable")
        self.assertEqual(self.call_rows(), ())


class NativeConversationModelTests(unittest.TestCase):
    def setUp(self):
        self.gemini = NS(provider_config={"id": "gemini"})
        self.deepseek = NS(provider_config={"id": "deepseek"})
        self.lookups = []
        self.models = {"gemini": self.gemini, "deepseek": self.deepseek}

        def selected(umo):
            self.lookups.append(umo)
            return self.gemini if ":FriendMessage:" in umo else self.deepseek

        self.resolve = conversation_provider_resolver(NS(get_using_provider=selected,
                                                         get_provider_by_id=self.models.get))

    def test_private_and_group_dialogue_use_their_native_session_origins(self):
        private = NS(scope=NS(kind=ScopeKind.PRIVATE, platform="qq", native_scope_id="200"))
        group = NS(scope=NS(kind=ScopeKind.GROUP, platform="qq", native_scope_id="300"))
        self.assertEqual(self.resolve(private, NS(kind="conversation.reply")), "gemini")
        self.assertEqual(self.resolve(private, NS(kind="proactive.compose")), "gemini")
        self.assertEqual(self.resolve(private, NS(kind="proactive.plan")), "gemini")
        self.assertEqual(self.resolve(group, NS(kind="conversation.reply")), "deepseek")
        self.assertEqual(self.lookups, ["qq:FriendMessage:200", "qq:FriendMessage:200",
                                        "qq:FriendMessage:200", "qq:GroupMessage:300"])

    def test_maintenance_does_not_read_the_conversation_model(self):
        self.assertIsNone(self.resolve(NS(), NS(kind="calendar.plan")))
        self.assertEqual(self.lookups, [])


if __name__ == "__main__":
    unittest.main()
