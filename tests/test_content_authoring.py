"""Contracts for native Content creation and fixed-version continuation."""

import unittest

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.conversation.content import access as content_access
from companion_v2.conversation.content import authoring, authoring_service as service
from companion_v2.execution.models import TaskError
from tests.test_daily_token_soft_limit import ModelFixture
from tests.test_management_access import NOW


class AuthoringContractTests(unittest.TestCase):
    def test_only_actions_connected_to_native_content_are_available(self):
        self.assertEqual(service.AUTHORING_ACTIONS, frozenset({"draft", "continue"}))
        for action in ("publish", "revise", "polish"):
            with self.subTest(action=action), self.assertRaises(TaskError) as error:
                service.prepare(action=action, brief="写点什么")
            self.assertEqual(error.exception.code, "content_authoring_action")

    def test_prepare_rejects_empty_requests_and_unanchored_continuations(self):
        cases = (
            ({"action": "draft", "brief": "   "}, "content_authoring_brief"),
            ({"action": "continue", "brief": "接着写"}, "content_authoring_base_version"),
        )
        for arguments, code in cases:
            with self.subTest(arguments=arguments), self.assertRaises(TaskError) as error:
                service.prepare(**arguments)
            self.assertEqual(error.exception.code, code)

    def test_continuation_prompt_names_the_fixed_version(self):
        continuation = service.prepare(action="continue", brief="接着写第三章", base_version="v-3")
        rendered = service.prompt(continuation, {"base-version": "前两章"},
                                  settings=service.AuthoringSettings.from_values({}))
        self.assertIn("已经固定的版本 v-3", rendered)
        self.assertIn("不重写已发布部分", rendered)
        self.assertIn("[base-version]", rendered)
        self.assertIn("不会自动采用、发布或发送", rendered)
        self.assertIn("核对上文最后的时间、地点", rendered)
        self.assertIn("检查时间线、人物关系、因果和重复问题", rendered)

    def test_create_and_continue_have_distinct_strict_output_shapes(self):
        created = authoring.parse_output(
            {"title": "夜航", "work_type": "短篇", "body": "正文"}, action="draft")
        self.assertEqual((created.title, created.work_type, created.body), ("夜航", "短篇", "正文"))
        continued = authoring.parse_output({"body": "接续正文"}, action="continue")
        self.assertEqual(continued.body, "接续正文")
        for action, value in (("draft", {"body": "缺标题"}),
                              ("continue", {"title": "多余字段", "body": "正文"})):
            with self.subTest(action=action), self.assertRaises(ValueError):
                authoring.parse_output(value, action=action)

    def test_body_has_no_plugin_owned_character_ceiling(self):
        body = "字" * 22000
        parsed = service.parse_draft({"title": "长篇", "work_type": "小说", "body": body}, action="draft")
        self.assertEqual(parsed.body, body)

    def test_direction_prompt_is_optional_role_guidance(self):
        request = service.prepare(action="draft", brief="写一段介绍")
        settings = service.AuthoringSettings.from_values({"direction_prompt": "保持平实"})
        self.assertIn("保持平实", service.prompt(request, {}, settings=settings))
        self.assertNotIn("保持平实", service.prompt(
            request, {}, settings=service.AuthoringSettings.from_values({})))

    def test_writing_prompt_carries_v1_continuity_and_quality_guidance(self):
        request = service.prepare(action="draft", brief="写一个完整短篇")
        rendered = service.prompt(request, {}, settings=service.AuthoringSettings.from_values({}))
        self.assertIn("不得以提纲、摘要或创作说明代替", rendered)
        self.assertIn("避免重复意象", rendered)
        self.assertIn("不设插件自有的字数目标", rendered)

    def test_writing_prompt_restores_v1_genre_specific_output_forms(self):
        request = service.prepare(action="continue", brief="接着写", base_version="v-3")
        rendered = service.prompt(request, {}, settings=service.AuthoringSettings.from_values({}),
                                  work_type="短剧剧本")
        self.assertIn("按剧本体裁写作", rendered)

    def test_title_lookup_requires_one_exact_work(self):
        items = [{"work_id": "work-a", "title": "夜航"}, {"work_id": "work-b", "title": "海岸线"}]
        self.assertEqual(service.select_work_id(" 夜航 ", items), "work-a")
        for title, catalogue, code in (
            ("不存在", items, "content_work_not_found"),
            ("夜航", items + [{"work_id": "work-c", "title": "夜航"}], "content_work_ambiguous"),
        ):
            with self.subTest(title=title), self.assertRaises(TaskError) as error:
                service.select_work_id(title, catalogue)
            self.assertEqual(error.exception.code, code)

    def test_writing_route_has_time_for_the_authoring_workflow(self):
        from companion_v2.conversation.content.authoring_tasks import (AUTHORING_BUDGET_SECONDS,
            AUTHORING_PROVIDER_TIMEOUT_SECONDS, authoring_profiles)
        self.assertEqual(authoring_profiles("primary")[0].timeout_seconds, AUTHORING_PROVIDER_TIMEOUT_SECONDS)
        self.assertGreaterEqual(AUTHORING_PROVIDER_TIMEOUT_SECONDS, 120,
                                "a 4096-token draft still needs minutes, not seconds")
        self.assertLess(AUTHORING_PROVIDER_TIMEOUT_SECONDS, AUTHORING_BUDGET_SECONDS,
                        "the writing round must expire before the task that owns it")

    def test_continuation_must_save_against_the_version_it_read(self):
        request = service.prepare(action="continue", brief="接着写", base_version="v-3")
        saved = service.continuation(
            request, {"work_id": "w-1", "base_version": "v-3", "base_revision": 3})
        self.assertEqual(saved.document(), {
            "work_id": "w-1", "base_version": "v-3", "base_revision": 3})
        with self.assertRaises(TaskError) as stale:
            service.continuation(
                request, {"work_id": "w-1", "base_version": "v-4", "base_revision": 4})
        self.assertEqual(stale.exception.code, "content_authoring_stale_base")
        with self.assertRaises(TaskError):
            service.continuation(request, {"work_id": "w-1", "base_version": "v-3"})

class AuthoringSettingsTests(ModelFixture, unittest.TestCase):
    def target(self, layer=ConfigLayer.ROLE):
        if layer is ConfigLayer.INSTALLATION:
            return ConfigTarget(self.context.scope.installation_id, layer)
        context = self.context
        return ConfigTarget(context.scope.installation_id, layer, context.role_binding.role,
                            bot_id=context.scope.bot_id)

    def write(self, name, value, *, layer=ConfigLayer.ROLE):
        owner = SparseConfigurationOwner(self.runtime)
        target = self.target(layer)
        owner.write(target, service.FIELDS, changes={service.GROUP + "." + name: value}, reset=(),
                    expected_revision=owner.read(target, service.FIELDS).revision, observed_at=NOW)

    def test_native_authoring_is_on_by_default_without_output_budget_settings(self):
        settings = service.effective_settings(self.runtime, self.context)
        self.assertEqual(settings, service.AuthoringSettings.from_values({}))
        self.assertTrue(settings.enabled)
        self.assertEqual({field.field_id for field in service.FIELDS}, {
            "content.authoring.enabled", "content.authoring.direction_prompt"})

    def test_settings_resolve_at_installation_and_role_scope(self):
        self.write("direction_prompt", "字" * 3000, layer=ConfigLayer.INSTALLATION)
        self.write("enabled", False)
        settings = service.effective_settings(self.runtime, self.context)
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.direction_prompt, "字" * 3000)

    def test_configuration_cannot_be_overridden_by_a_conversation_window(self):
        for field in service.FIELDS:
            self.assertEqual(field.layers, service.LAYERS)
            self.assertNotIn(ConfigLayer.WINDOW, field.layers)


class RecordedContinuationTests(ModelFixture, unittest.TestCase):
    """A native read pins the version a later candidate is allowed to extend."""

    def test_a_read_pins_the_version_that_the_next_candidate_must_reuse(self):
        content_access.record_authoring_request(self.runtime, self.context, "work-1",
            {"action": "continue", "brief": "", "base_version": "v-3"})
        stored = content_access.recorded_authoring_request(self.runtime, self.context, "work-1")
        self.assertEqual(stored["base_version"], "v-3")
        self.assertIsNone(content_access.recorded_authoring_request(self.runtime, self.context, "work-2"))

    def test_a_corrupt_record_is_not_used_as_a_continuation_anchor(self):
        key = content_access.request_key(self.context, "work-1")
        with self.runtime.transaction() as connection:
            connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?)", (key, "not json"))
        self.assertIsNone(content_access.recorded_authoring_request(self.runtime, self.context, "work-1"))


if __name__ == "__main__":
    unittest.main()
