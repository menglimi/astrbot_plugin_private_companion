"""Structured tasks register a thinking margin; replies keep the host budget.

The host-wide ``model.max_output_tokens`` was written for a conversational
reply. These cases pin down the three properties the per-task defaults owe the
user: an untouched task receives the larger default, an explicitly saved value
is never multiplied, and a default never exceeds the product or provider
ceiling.
"""

from dataclasses import replace
from unittest.mock import patch
from types import SimpleNamespace
import unittest

from companion_v2.adapters.astrbot.provider import AstrBotTextProvider
from companion_v2.configuration.models import ConfigLayer, ConfigTarget, EffectiveValue
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.configuration.service import ConfigurationService
from companion_v2.execution.models import Lane, TaskError
from companion_v2.model_gateway.management import ModelManagement
from companion_v2.model_gateway.models import MAX_MODEL_OUTPUT_TOKENS, ModelTaskProfile
from companion_v2.model_gateway.routing import ModelRouter, configuration_fields
from tests.multi_host_support import MultiFixture
from tests.test_astrbot_staging import StagingFixture
from tests.test_host_application import HostFixture
from tests.test_management_access import INSTALLATION, NOW, ROLE, SCOPE
from tests.test_model_gateway import ModelFixture


# The default table is the contract; the registrations are the reality. Both
# are compared dynamically below, so a task that starts registering without a
# thinking margin fails instead of quietly keeping the reply budget.
FIELDS = ("provider", "fallback", "prompt", "max_output_tokens", "replacement_rules",
          "fallback_on_refusal", "refusal_terms", "timeout_seconds", "replacement_scope")


VIDEO_CAPABLE = AstrBotTextProvider.local_provider


def video_capable(provider):
    """Declare video input so the capability-gated narration task registers."""
    local = VIDEO_CAPABLE(provider)
    return replace(local, capabilities=tuple(sorted(set(local.capabilities) | {"video"})))


class OutputDefaultTableTests(unittest.TestCase):
    def test_the_table_holds_one_default_per_kind(self):
        from companion_v2.model_gateway import output_defaults

        table = output_defaults.THINKING_MARGIN
        self.assertTrue(table)
        for kind, (factor, floor) in table.items():
            with self.subTest(kind=kind):
                self.assertIsInstance(kind, str)
                self.assertGreaterEqual(factor, 1)
                self.assertGreaterEqual(floor, 0)

    def test_no_default_exceeds_the_product_or_a_requested_ceiling(self):
        from companion_v2.model_gateway import output_defaults

        for kind in sorted(output_defaults.THINKING_MARGIN):
            with self.subTest(kind=kind):
                self.assertLessEqual(output_defaults.default_output_tokens(kind, MAX_MODEL_OUTPUT_TOKENS),
                                     MAX_MODEL_OUTPUT_TOKENS)
                self.assertLessEqual(output_defaults.default_output_tokens(kind, 2048, maximum=4096), 4096)
                self.assertGreaterEqual(output_defaults.default_output_tokens(kind, 1), 1)


    def test_reply_and_verdict_kinds_keep_the_host_budget(self):
        from companion_v2.model_gateway import output_defaults

        for kind in ("conversation.reply", "proactive.judge", "role.journal.select"):
            with self.subTest(kind=kind):
                self.assertEqual(output_defaults.default_output_tokens(kind, 2048), 2048)
        self.assertEqual(output_defaults.default_output_tokens("conversation.poke_reply", 128), 128)


class StagingOutputDefaultTests(StagingFixture, unittest.IsolatedAsyncioTestCase):
    def staging_with_global_budget(self, tokens=2048):
        return self.staging(max_output_tokens=tokens)

    def snapshot_field(self, session, group, name="max_output_tokens"):
        snapshot = session.application.configuration.read(
            self.target.actor, target=session.config_target, group_id=group,
            observed_at=NOW).unwrap()
        return snapshot.fields[group + "." + name]

    def patch_group(self, session, group, **changes):
        service = session.application.configuration
        snapshot = service.read(self.target.actor, target=session.config_target,
                                group_id=group, observed_at=NOW).unwrap()
        return service.patch(self.target.actor, target=session.config_target, group_id=group,
                             expected_edit_token=snapshot.edit_token,
                             operation=self.operation(self.target.scope),
                             changes={group + "." + name: value for name, value in changes.items()},
                             observed_at=NOW).unwrap()

    async def test_an_untouched_role_receives_the_structured_defaults(self):
        from companion_v2.model_gateway.output_defaults import THINKING_MARGIN

        session = self.staging_with_global_budget()
        profiles = session.application.router.profiles
        self.assertLessEqual(set(profiles), set(THINKING_MARGIN))
        expected = {
            "role.expression.learn": 8192,
            "role.self_review": 8192,
            "memory.reflect": 8192,
            "content.authoring.draft": 8192,
            "role.expression.review": 4096,
            "role.state.maintain": 4096,
            "proactive.compose": 4096,
            "calendar.plan": 4096,
            "screen.interpretation": 4096,
            "conversation.reply": 2048,
            "conversation.poke_reply": 128,
            "proactive.judge": 2048,
        }
        for kind, tokens in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(profiles[kind].max_output_tokens, tokens)

    async def test_an_untouched_default_is_not_persisted(self):
        session = self.staging_with_global_budget()
        group = "model.role.expression.learn"
        field = self.snapshot_field(session, group)
        self.assertEqual(field.value, 8192)
        self.assertIsNone(field.source)
        self.assertFalse(field.explicit)
        self.assertIsNone(self.runtime.read_one(
            "SELECT 1 FROM configuration_values WHERE field_id = ?", (group + ".max_output_tokens",)))

    async def test_an_explicit_value_is_never_multiplied(self):
        session = self.staging_with_global_budget()
        group = "model.role.expression.learn"
        self.patch_group(session, group, max_output_tokens=4096)

        field = self.snapshot_field(session, group)
        self.assertEqual(field.value, 4096)
        self.assertTrue(field.explicit)
        self.assertEqual(session.application.router.profiles[group.removeprefix("model.")].max_output_tokens, 8192)


class StagingRegistrationTests(StagingFixture, unittest.IsolatedAsyncioTestCase):
    """Every task the single-role assembly registers must carry a default."""

    async def test_every_staging_task_kind_has_a_thinking_margin(self):
        from companion_v2.model_gateway.output_defaults import THINKING_MARGIN

        with patch.object(AstrBotTextProvider, "local_provider", video_capable):
            profiles = self.staging(max_output_tokens=2048).application.router.profiles
        self.assertEqual(sorted(set(profiles) - set(THINKING_MARGIN)), [])


class MultiHostRegistrationTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    """The multi-role host registers the whole table, and nothing outside it."""

    async def test_multi_host_registration_and_the_default_table_agree(self):
        from companion_v2.model_gateway.output_defaults import THINKING_MARGIN

        with patch.object(AstrBotTextProvider, "local_provider", video_capable):
            profiles = (await self.start_host()).router.profiles
        self.assertEqual(sorted(set(THINKING_MARGIN) ^ set(profiles)), [])


class StructuredCeilingRoutingTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    """A registry default above a provider capability is clamped, not rejected."""

    def setUp(self):
        super().setUp()
        self.profiles = (replace(self.profiles[0], max_output_tokens=8192),)
        fields = tuple(field for field in self.registry.fields.values()
                       if not field.field_id.startswith("model.fixture."))
        self.registry = FieldRegistry(version="structured-output-defaults.v1",
                                      fields=fields + configuration_fields(self.profiles))
        self.configuration = ConfigurationService(self.runtime, self.registry)
        # A 100-token provider capability sits below the 8192 registry default.
        self.providers = tuple(self.provider(name) for name in ("primary", "fallback"))
        self.router = ModelRouter(self.configuration, self.profiles, self.providers)
        self.router.start(expected_generation=1)

    def router_with_small_target(self):
        """Add a 50-token provider a replacement rule can route to."""
        self.providers = (*self.providers, replace(self.provider("small"), maximum_output_tokens=50))
        self.router = ModelRouter(self.configuration, self.profiles, self.providers)
        self.router.start(expected_generation=2)

    def test_assembly_survives_and_resolution_clamps_the_default(self):
        plan = self.router.resolve(self.model_run(), self.request())

        self.assertEqual(plan.envelope.max_output_tokens, 100)
        self.assertEqual(plan.audit["max_output_tokens"], 100)
        self.assertEqual(plan.audit["max_output_tokens_basis"], "registry_default_clamped")
        # The saved snapshot keeps the registry default: only the dispatched
        # envelope is clamped, so raising the provider capability restores it.
        self.assertEqual(plan.snapshot.fields["model.fixture.read.max_output_tokens"].value, 8192)

    def test_an_explicit_value_below_the_provider_is_kept(self):
        self.patch_model(max_output_tokens=50)
        plan = self.router.resolve(self.model_run(), self.request())

        self.assertEqual(plan.envelope.max_output_tokens, 50)
        self.assertEqual(plan.audit["max_output_tokens_basis"], "explicit")

    def test_an_explicit_value_above_the_provider_still_fails(self):
        self.patch_model(max_output_tokens=200)
        with self.assertRaises(TaskError) as error:
            self.router.resolve(self.model_run(), self.request())

        self.assertEqual(error.exception.code, "model_output_limit")

    def test_an_explicit_value_above_a_review_provider_still_fails(self):
        self.patch_model(max_output_tokens=200)
        with self.assertRaises(TaskError) as error:
            self.router.resolve(self.model_run(), self.request(), provider_id="fallback")

        self.assertEqual(error.exception.code, "model_output_limit")

    def test_assembly_accepts_a_replacement_target_below_the_default(self):
        profiles = (replace(self.profiles[0],
                            replacement_rules='[{"provider_id": "fallback", "keywords": ["private"]}]'),)
        fields = tuple(field for field in self.registry.fields.values()
                       if not field.field_id.startswith("model.fixture."))
        registry = FieldRegistry(version="structured-output-defaults-replacement.v1",
                                 fields=fields + configuration_fields(profiles))
        router = ModelRouter(ConfigurationService(self.runtime, registry), profiles, self.providers)

        self.assertEqual(router.profiles["fixture.read"].max_output_tokens, 8192)

    def test_a_default_is_clamped_to_a_replacement_target_it_never_matches(self):
        # The editor cannot know which rule will match, so the default has to
        # fit every reachable target up front; otherwise the shown value and
        # the dispatched value would differ on a keyword hit.
        self.router_with_small_target()
        self.patch_model(replacement_rules='[{"id": "small", "provider_id": "small", '
                                          '"keywords": ["absent-keyword"]}]')
        plan = self.router.resolve(self.model_run(), self.request())

        self.assertEqual(plan.envelope.max_output_tokens, 50)
        self.assertEqual(plan.audit["max_output_tokens"], 50)
        self.assertEqual(plan.audit["max_output_tokens_basis"], "registry_default_clamped")

    def test_an_explicit_value_above_a_route_target_is_still_refused(self):
        self.router_with_small_target()
        self.patch_model(max_output_tokens=80, replacement_rules='[{"id": "small", '
                         '"provider_id": "small", "keywords": ["private"]}]')
        with self.assertRaises(TaskError) as error:
            self.router.resolve(self.model_run(), self.request())

        self.assertEqual(error.exception.code, "model_route_invalid")


class OutputCeilingProjectionTests(unittest.TestCase):
    """The task editor shows the ceiling routing will actually enforce."""

    def manager(self, *, limit, rules="[]", explicit_limit=False, capacities=None):
        group = "model.role.expression.learn"
        source = ConfigTarget(INSTALLATION, ConfigLayer.ROLE, ROLE) if explicit_limit else None
        values = {"provider": "primary", "fallback": "fallback", "prompt": "", "max_output_tokens": limit,
                  "replacement_rules": rules, "fallback_on_refusal": False, "refusal_terms": "",
                  "timeout_seconds": 60, "replacement_scope": "all"}
        snapshot = SimpleNamespace(
            fields={group + "." + name: EffectiveValue(value, source if name == "max_output_tokens" else None,
                                                      explicit_limit and name == "max_output_tokens")
                    for name, value in values.items()},
            edit_token="token", apply_mode=SimpleNamespace(value="immediate"), revision=1)
        manager = object.__new__(ModelManagement)
        manager.app = SimpleNamespace(
            router=SimpleNamespace(providers={
                name: SimpleNamespace(maximum_output_tokens=ceiling, capabilities=("text",))
                for name, ceiling in (capacities or {"primary": 8192, "fallback": 8192, "small": 50}).items()}),
            configuration=SimpleNamespace(application_status=lambda *a, **k: SimpleNamespace(
                failure=None, unwrap=lambda: SimpleNamespace(state="applied"))),
            now=lambda: NOW)
        manager.context = SimpleNamespace(
            scope=SimpleNamespace(installation_id=INSTALLATION, bot_id=SCOPE.bot_id),
            role_binding=SimpleNamespace(role=ROLE))
        manager._snapshots = lambda operator, profile, layer=None: (snapshot, snapshot, None)
        manager._prompt_sections = lambda *a, **k: []
        return manager, ModelTaskProfile(kind="role.expression.learn", lane=Lane.BACKGROUND,
                                         default_provider="primary", default_prompt="任务提示",
                                         default_fallback="fallback", max_output_tokens=limit)

    def projected_limit(self, **options):
        manager, profile = self.manager(**options)
        return manager._project(object(), profile)["fields"]["max_output_tokens"]

    def test_a_default_is_projected_with_the_ceiling_routing_would_apply(self):
        limit = self.projected_limit(limit=8192, rules='[{"id": "small", "provider_id": '
                                    '"small", "keywords": ["absent-keyword"]}]')

        self.assertEqual((limit["value"], limit["inherited_value"]), (50, 50))

    def test_a_default_that_fits_every_target_is_projected_unchanged(self):
        limit = self.projected_limit(limit=8192)

        self.assertEqual((limit["value"], limit["inherited_value"]), (8192, 8192))

    def test_an_explicit_value_is_projected_unchanged(self):
        limit = self.projected_limit(limit=8192, explicit_limit=True,
                                     capacities={"primary": 4096, "fallback": 4096})

        self.assertEqual(limit["value"], 8192)


class InheritedDefaultSaveTests(unittest.TestCase):
    """Editing an unrelated field must not trip a provider's output ceiling."""

    def setUp(self):
        self.profile = ModelTaskProfile(kind="role.expression.learn", lane=Lane.BACKGROUND,
                                        default_provider="primary", default_prompt="任务提示",
                                        default_fallback="fallback", max_output_tokens=8192)
        self.definitions = configuration_fields((self.profile,))
        self.snapshot = SimpleNamespace(fields={
            item.field_id: EffectiveValue(item.default, None, False) for item in self.definitions})
        self.manager = object.__new__(ModelManagement)
        self.manager.app = SimpleNamespace(router=SimpleNamespace(providers={
            provider: SimpleNamespace(maximum_output_tokens=4096, capabilities=("text",))
            for provider in ("primary", "fallback")}))

    def saved_limit(self, source):
        """A snapshot whose output limit was saved at ``source`` on an outer layer."""
        field_id = self.profile.group + ".max_output_tokens"
        return SimpleNamespace(fields={
            item.field_id: EffectiveValue(item.default,
                source if item.field_id == field_id else None, False)
            for item in self.definitions})

    def changes(self, current=None, inherited=None, **changes):
        return self.manager._changes(self.profile, {"changes": changes, "reset": []},
                                     current or self.snapshot, inherited or self.snapshot)[0]

    def test_an_inherited_default_above_the_cap_does_not_block_other_edits(self):
        self.assertEqual(self.changes(timeout_seconds=120),
                         {"model.role.expression.learn.timeout_seconds": 120})

    def test_an_explicit_value_above_the_cap_is_still_refused(self):
        with self.assertRaises(TaskError) as error:
            self.changes(max_output_tokens=8192)

        self.assertEqual(error.exception.code, "model_output_limit")

    def test_a_limit_saved_at_the_installation_layer_refuses_other_edits(self):
        # ``explicit`` is only true at the edited layer, but routing treats a value
        # saved on any layer as the user's contract. Saving an unrelated field must
        # fail here instead of letting dispatch raise model_output_limit later.
        saved = self.saved_limit(ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION))
        with self.assertRaises(TaskError) as error:
            self.changes(current=saved, inherited=self.snapshot, timeout_seconds=120)

        self.assertEqual(error.exception.code, "model_output_limit")

    def test_a_limit_saved_at_the_role_layer_refuses_a_window_edit(self):
        saved = self.saved_limit(ConfigTarget(INSTALLATION, ConfigLayer.ROLE, ROLE))
        with self.assertRaises(TaskError) as error:
            self.changes(current=saved, inherited=self.snapshot, timeout_seconds=120)

        self.assertEqual(error.exception.code, "model_output_limit")

    def test_resetting_only_to_a_saved_upstream_limit_is_refused(self):
        # Restoring inheritance exposes the parent's value, which routing also
        # judges by its layer. Resetting to a bare default stays allowed.
        saved = self.saved_limit(ConfigTarget(INSTALLATION, ConfigLayer.INSTALLATION))
        request = {"changes": {}, "reset": ["max_output_tokens"]}
        with self.assertRaises(TaskError) as error:
            self.manager._changes(self.profile, request, self.snapshot, saved)
        self.assertEqual(error.exception.code, "model_output_limit")

        own = self.saved_limit(ConfigTarget(INSTALLATION, ConfigLayer.ROLE, ROLE))
        changes, reset = self.manager._changes(self.profile, request, own, self.snapshot)
        self.assertEqual((changes, reset), ({}, ("model.role.expression.learn.max_output_tokens",)))


class OutputCeilingSaveTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    """The task editor's save path refuses exactly what routing would refuse."""

    GROUP = "model.conversation.reply"

    def setUp(self):
        super().setUp()
        self.profiles = (replace(self.profiles[0], max_output_tokens=8192),)
        # The provider is below the 8192 registry default: only a saved value is
        # refused, a bare default is clamped at dispatch time.
        self.providers = (replace(self.providers[0], maximum_output_tokens=4096),)
        self.registry = FieldRegistry(version="structured-output-save.v1",
                                      fields=configuration_fields(self.profiles))

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.host_instance = await self.start()
        self.app = self.host_instance.application
        batch = self.host_instance.receive(self.event("output-ceiling-save")).unwrap()
        self.advance(self.app)
        await self.done(self.app, batch)
        self.manager = ModelManagement(self.app, self.context(self.app, batch))

    def save(self, target, **fields):
        """Save configuration without the task editor so the value sits on an outer layer."""
        snapshot = self.app.configuration.read(self.core, target=target, group_id=self.GROUP,
                                               observed_at=self.app.now()).unwrap()
        return self.app.configuration.patch(self.core, target=target, group_id=self.GROUP,
            operation=self.operation(target.authorization_scope),
            expected_edit_token=snapshot.edit_token,
            changes={self.GROUP + "." + name: value for name, value in fields.items()},
            observed_at=self.app.now())

    def request(self, layer, key, **changes):
        status = self.manager.status(self.core, layer=layer).unwrap()
        task = status["tasks"][0]
        return {"kind": task["kind"], "layer": layer, "operation_key": key,
                "edit_token": task["edit_token"], "changes": changes, "reset": []}

    def test_a_limit_saved_at_the_installation_layer_refuses_unrelated_edits(self):
        self.save(self.global_target, max_output_tokens=8192).unwrap()
        for index, layer in enumerate(("role", "window")):
            with self.subTest(layer=layer):
                result = self.manager.configure(self.core,
                    self.request(layer, f"installation-output-cap-{index}", timeout_seconds=120))
                self.assertEqual(result.failure.code, "model_output_limit")
        # The refused saves left the stored value alone and wrote nothing.
        self.assertIsNone(self.app.runtime.read_one(
            "SELECT 1 FROM configuration_values WHERE field_id = ?",
            (self.GROUP + ".timeout_seconds",)))

    def test_a_limit_saved_at_the_role_layer_refuses_a_window_edit(self):
        role_target = ConfigTarget(INSTALLATION, ConfigLayer.ROLE, ROLE, bot_id=SCOPE.bot_id)
        self.save(role_target, max_output_tokens=8192).unwrap()

        result = self.manager.configure(self.core,
            self.request("window", "role-output-cap", timeout_seconds=120))

        self.assertEqual(result.failure.code, "model_output_limit")

    def test_only_the_registry_default_above_the_provider_is_clamped(self):
        result = self.manager.configure(self.core,
            self.request("window", "default-output-clamp", timeout_seconds=120)).unwrap()

        limit = result["task"]["fields"]["max_output_tokens"]
        self.assertEqual((limit["value"], limit["source"]), (4096, "default"))
        self.assertIsNone(self.app.runtime.read_one(
            "SELECT 1 FROM configuration_values WHERE field_id = ?",
            (self.GROUP + ".max_output_tokens",)))


if __name__ == "__main__":
    unittest.main()
