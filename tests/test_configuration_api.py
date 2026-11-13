"""Dashboard routes for the image, reaction and budget groups the cottage edits.

These groups already had owners and consumers; the route is what turns them into
operator-editable settings. The tests pin the two scope shapes (role ceiling vs
conversation window), the per-section edit token, replay safety and the
authorization gate.

主动联系的公共编辑器固定在全局层，不再随着对话窗口改变地址；
运行期继续维护对象授权、个人资料和暂缓规则。
"""

import json
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from tests import test_astrbot_staging_host as support
from tests.multi_host_support import MultiFixture, multi_config
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi
from astrbot_host.controller import AstrBotStagingController
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.configuration.management import DiaryAndRestManagement, ProactiveContactManagement
from companion_v2.identity_policy.grants import PERSON_PERMISSION_GRANT_PREFIX
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.proactive.management import GRANT_PREFIX as PROACTIVE_GRANT_PREFIX
from companion_v2.proactive.configuration import GROUP as CONTACT_GROUP
from companion_v2.expression.configuration import read_settings as expression_read_settings
from companion_v2.foundation.outcome import Outcome
from companion_v2.role_work.growth.configuration import skill_settings
from companion_v2.role_profile.configuration import FIELD_LIMITS
from companion_v2.screen.configuration import (ScreenSettings, ScreenConfigurationOwner,
    configuration_fields as screen_configuration_fields, target_for as screen_target_for)
from types import SimpleNamespace as NS


class Query(dict):
    """Minimal stand-in for the host query object (keys/get/getlist)."""

    def getlist(self, key):
        return [self[key]] if key in self else []


async def call(handler, body=None, *, username="config-admin", query=None):
    """Invoke one configuration handler with the host request patched in."""
    async def read():
        return json.dumps(body, ensure_ascii=False).encode("utf-8")
    with patch("astrbot_host.configuration_api.request",
               NS(username=username, query=Query(query or {}), body=read)):
        return await handler()


class ConfigurationWebAPITests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def routes(self):
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        await self.start_host()
        (await self.controller.authorize_delivery("first")).unwrap()
        self.controller.register_routes()
        handlers = {route: handler for route, handler, _, _ in self.native.registered_web_apis}
        def editor(target_id):
            # The editor route lives on the role path; /model/image/config also
            # ends with /config, so the target comes from the route itself.
            return [route for route in handlers
                    if route.split("/")[-1] == "config" and "/model/" not in route
                    and "/targets/" + target_id + "/" in route][0]
        return handlers, editor("first"), editor("second")

    async def test_scoped_qzone_config_accepts_matching_page_selection(self):
        handlers, first, _ = await self.routes()
        selection = {"target_id": "first", "role_id": handlers[first].__self__.controller.role_id}
        try:
            response = await call(handlers[first], query={"sections": "qzone", **selection})
            self.assertEqual(response["status"], "ok", response)
            section = response["data"]["sections"]["qzone"]
            saved = await call(handlers[first + "/configure"], {
                "section": "qzone", "operation_key": "qzone-selected-private",
                "edit_token": section["edit_token"], "changes": {"like_enabled": False},
                **selection,
            })
            self.assertEqual(saved["status"], "ok", saved)
        finally:
            self.controller.unregister_routes()

    async def test_scoped_configuration_rejects_conflicting_page_selection(self):
        handlers, first, _ = await self.routes()
        role_id = handlers[first].__self__.controller.role_id
        try:
            for selection in ({"target_id": "second", "role_id": role_id},
                              {"target_id": "first", "role_id": "another-role"}):
                response = await call(handlers[first], query={"sections": "qzone", **selection})
                self.assertEqual(response["status"], "error", response)
                self.assertEqual(response["message"], "configuration_target_changed", response)
        finally:
            self.controller.unregister_routes()

    async def test_official_catalogue_and_saved_missing_sources_are_visible(self):
        handlers, first, _ = await self.routes()
        official = NS(list_sources=AsyncMock(return_value=[{
            "id": "kb:kb-1", "type": "knowledge_base", "kb_id": "kb-1", "name": "星河设定",
            "documents": [{"id": "doc:kb-1:doc-1", "name": "角色资料.md"}],
        }]))
        self.native.kb_manager = NS()
        try:
            with patch("astrbot_host.configuration_api.AstrBotKnowledgeSources", return_value=official):
                status = (await call(handlers[first], query={"sections": "knowledge"}))["data"]["sections"]["knowledge"]
                saved = await call(handlers[first + "/configure"], {
                    "section": "knowledge", "operation_key": "official-kb-selection",
                    "edit_token": status["edit_token"],
                    "changes": {"source_ids": "doc:kb-1:doc-1\nkb:missing"},
                }, query={"sections": "knowledge"})
                reread = (await call(handlers[first], query={"sections": "knowledge"}))[
                    "data"]["sections"]["knowledge"]
            self.assertEqual(saved["status"], "ok", saved)
            self.assertNotIn("status", saved["data"])
            status = reread
            self.assertTrue(status["knowledge_sources_available"])
            self.assertEqual(status["knowledge_sources"][0]["documents"][0]["id"], "doc:kb-1:doc-1")
            self.assertEqual(status["knowledge_sources"][-1]["type"], "missing")
            self.assertEqual(status["knowledge_sources"][-1]["id"], "kb:missing")
            official.list_sources.side_effect = RuntimeError("catalogue unavailable")
            with patch("astrbot_host.configuration_api.AstrBotKnowledgeSources", return_value=official):
                unavailable = (await call(handlers[first], query={"sections": "knowledge"}))["data"]["sections"]["knowledge"]
            self.assertFalse(unavailable["knowledge_sources_available"])
            self.assertEqual([item["id"] for item in unavailable["knowledge_sources"]],
                             ["doc:kb-1:doc-1", "kb:missing"])
        finally:
            self.controller.unregister_routes()

    async def test_every_section_reads_with_its_own_scope_and_token(self):
        handlers, first, _ = await self.routes()
        try:
            status = (await call(handlers[first]))["data"]["sections"]
            installation_route = "/astrbot_plugin_private_companion/config"
            installation_status = (await call(handlers[installation_route]))["data"]["sections"]
            self.assertEqual(set(installation_status), {"budget", "proactive.contact", "reaction",
                                                       "private_access_installation"})
            self.assertTrue({"budget", "proactive.contact", "reaction"}.isdisjoint(status))
            status.update(installation_status)
            expected_sections = {"budget", "busy_gate", "group_participation", "image_review",
                                 "knowledge", "library", "life", "person", "photo.reference",
                                 "private_access", "private_access_installation", "quota", "reaction", "recall",
                                 "content.authoring",
                                 "expression.learning", "role.growth.skills",
                                 # qzone 是真实配置分区（companion_v2/qzone/configuration.py:18
                                 # 的 GROUP = "qzone"）。它加入注册表时这份硬编码清单没跟上，
                                 # 于是 assertEqual 把"多了一个真分区"报成失败——这正是本会话
                                 # 反复出现的模式：新增能力却不更新按字面枚举的名单。
                                 "qzone",
                                 "relay",
                                 "relationship_profile",
                                 "role_profile", "screen", "wardrobe", "web_understanding", "proactive.contact",
                                 "system.prompts"}
            expected_sections.add("image.output")
            self.assertTrue(expected_sections.issubset(status))
            self.assertEqual(set(status), expected_sections)
            self.assertEqual(status["quota"]["scope"], "role")
            self.assertEqual(status["budget"]["scope"], "installation")
            self.assertEqual(status["library"]["scope"], "role")
            self.assertEqual(status["role_profile"]["scope"], "role")
            self.assertEqual(status["role_profile"]["layers"], ["installation", "role"])
            self.assertEqual(status["role_profile"]["fields"]["world"]["maximum_chars"], 4200)
            self.assertEqual(set(status["role_profile"]["settings"]), set(FIELD_LIMITS))
            self.assertEqual(status["relationship_profile"]["scope"], "relationship")
            self.assertEqual(status["relationship_profile"]["layers"],
                             ["installation", "role", "relationship"])
            self.assertEqual(status["relationship_profile"]["fields"]["role_relation"]["editable_layers"],
                             ["relationship"])
            # 参考图与衣柜同样按角色生效：单个会话不能收窄整个角色的设置。
            self.assertEqual(status["wardrobe"]["scope"], "role")
            self.assertEqual(status["photo.reference"]["scope"], "role")
            self.assertEqual(status["image_review"]["scope"], "role")
            self.assertEqual(status["busy_gate"]["scope"], "role")
            self.assertEqual(status["reaction"]["scope"], "installation")
            self.assertEqual(status["private_access"]["scope"], "role")
            self.assertEqual(status["private_access_installation"]["scope"], "installation")
            # 原文回溯和屏幕材料保留私有窗口范围。
            for name in ("recall", "screen"):
                self.assertEqual(status[name]["scope"], "window", name)
            self.assertEqual(status["person"]["scope"], "role")
            self.assertEqual(status["person"]["layers"], ["installation", "role"])
            self.assertEqual(status["person"]["fields"]["acquaintance_enabled"]["editable_layers"], ["role"])
            self.assertEqual(status["person"]["fields"]["acquaintance_card_chars"]["editable_layers"], ["role"])
            for name in ("knowledge", "group_participation"):
                self.assertEqual(status[name]["scope"], "role", name)
            self.assertEqual(status["life"]["scope"], "window")
            self.assertEqual(status["life"]["fields"]["sleep_enabled"]["editable_layers"],
                             ["role"])
            self.assertEqual(status["life"]["fields"]["sleep_enabled"]["write_layer"], "role")
            self.assertEqual(status["life"]["fields"]["diary_enabled"]["editable_layers"], ["window"])
            self.assertEqual(status["life"]["fields"]["diary_enabled"]["write_layer"], "window")
            self.assertEqual(status["quota"]["settings"]["private_owner_max_daily"], -1)
            self.assertEqual(status["reaction"]["settings"]["candidate_limit"], 6)
            self.assertFalse(status["budget"]["settings"]["enable_daily_token_soft_limit"])
            self.assertTrue(status["recall"]["settings"]["transcribe_enabled"])
            self.assertFalse(status["screen"]["settings"]["interpretation_enabled"])
            self.assertTrue(status["person"]["settings"]["acquaintance_enabled"])
            # 群聊参与：V1 的默认行为——跟读默认开、主动插话默认关。
            self.assertTrue(status["group_participation"]["settings"]["enable_group_repeat_follow"])
            self.assertFalse(status["group_participation"]["settings"]["enable_group_interjection"])
            # 参考图与衣柜的两个总闸默认关闭；轮换天数沿用 14 天。
            self.assertFalse(status["wardrobe"]["settings"]["enabled"])
            self.assertFalse(status["photo.reference"]["settings"]["enabled"])
            self.assertFalse(status["photo.reference"]["settings"]["group_enabled"])
            self.assertFalse(status["photo.reference"]["settings"]["daily_outfit_enabled"])
            self.assertEqual(status["photo.reference"]["settings"]["daily_outfit_rotation_days"], 14)
            for name, section in status.items():
                self.assertEqual(len(section["edit_token"]), 64, name)
                # 组名以各自域开头：既有组用 conversation./expression./model.，
                # 新增组分别是 conversation.recall / screen / person / role.growth /
                # conversation.group_participation / photo.reference 等；
                # private_access（私聊用户权限的默认值与拒绝文案）本身就是一个小域，
                # 不带厂商前缀，字段是按人权限的默认值而不是某个能力的实现细节。
                # qzone（QQ 空间拓展的角色级开关与节流）同样是自己的一个小域。
                # 这份白名单是**按字面枚举**的，所以每加一个不带前缀的域都要跟上——
                # 今天 qzone 就因此判红过一次，而失败信息只说"qzone"，看不出是
                # "多了一个真域"还是"组名写错了"。加域时两处都要改：
                # 这里的前缀白名单，以及下面 expected_sections 的组清单。
                self.assertTrue(section["group_id"].split(".")[0] in
                                ("conversation", "expression", "model", "screen", "person",
                                 "role", "knowledge", "library", "photo", "wardrobe",
                                 "proactive", "private_access", "relationship", "content",
                                 "qzone", "system"), name)
            # The workbench renders its layer rail from these, so every section
            # must publish the chain and a per-field source, not just its values.
            for name, section in status.items():
                self.assertIn(section["scope"], ("installation", "role", "relationship", "window"), name)
                self.assertTrue(section["layers"], name)
                self.assertEqual(section["layers"][-1], section["scope"], name)
                self.assertTrue(section["provenance"], name)
                self.assertTrue(section["fields"], name)
                self.assertIn("kind", section["fields"][next(iter(section["settings"]))], name)
            # Window groups and the role ceiling are different targets, so their
            # tokens must not be interchangeable.
            self.assertNotEqual(status["quota"]["edit_token"], status["image_review"]["edit_token"])
            restricted = await call(handlers[first], query={"sections": "quota"})
            self.assertEqual(sorted(restricted["data"]["sections"]), ["quota"])
            self.assertEqual((await call(handlers[first], query={"sections": "nope"}))["data"]["code"],
                             "configuration_request_invalid")
        finally:
            self.controller.unregister_routes()

    async def test_stranger_settings_save_to_installation_while_known_users_keep_role_settings(self):
        from astrbot_host.stranger_notice import StrangerNotice

        handlers, first, _ = await self.routes()
        installation_route = "/astrbot_plugin_private_companion/config"
        try:
            role = (await call(handlers[first], query={"sections": "private_access"}))[
                "data"]["sections"]["private_access"]
            global_settings = (await call(handlers[installation_route],
                query={"sections": "private_access_installation"}))["data"]["sections"]["private_access_installation"]
            self.assertEqual(set(role["settings"]), {"dialogue_default", "proactive_default",
                                                     "denied_reply", "notice_cooldown_seconds"})
            self.assertEqual(set(global_settings["settings"]), {"stranger_notice_enabled",
                "stranger_notice_daily_cap", "denied_reply", "notice_cooldown_seconds"})
            self.assertEqual(set(role["fields"]), set(role["settings"]))
            self.assertEqual(set(global_settings["provenance"]), set(global_settings["settings"]))

            saved = await call(handlers[installation_route + "/configure"], {
                "section": "private_access_installation", "operation_key": "stranger-installation",
                "edit_token": global_settings["edit_token"],
                "changes": {"stranger_notice_enabled": False, "stranger_notice_daily_cap": 9,
                            "denied_reply": "陌生号请先联系管理员", "notice_cooldown_seconds": 0},
            })
            self.assertEqual(saved["status"], "ok", saved)
            self.assertEqual(StrangerNotice(self.controller).defaults()["stranger_notice"], False)
            self.assertEqual(StrangerNotice(self.controller).defaults()["denied_reply"], "陌生号请先联系管理员")
            self.assertEqual(StrangerNotice(self.controller).defaults()["stranger_daily_cap"], 9)

            denied = await call(handlers[first + "/configure"], {
                "section": "private_access", "operation_key": "invalid-role-stranger",
                "edit_token": role["edit_token"], "changes": {"stranger_notice_enabled": True},
            })
            self.assertEqual(denied["data"]["code"], "private_access_configuration_invalid")
            refreshed = (await call(handlers[first], query={"sections": "private_access"}))[
                "data"]["sections"]["private_access"]
            role_saved = await call(handlers[first + "/configure"], {
                "section": "private_access", "operation_key": "known-user-refusal",
                "edit_token": refreshed["edit_token"], "changes": {"denied_reply": "已绑定用户暂不能回复"},
            })
            self.assertEqual(role_saved["status"], "ok", role_saved)
            self.assertEqual(StrangerNotice(self.controller).defaults()["denied_reply"], "陌生号请先联系管理员")
        finally:
            self.controller.unregister_routes()

    async def test_multi_section_status_isolates_failed_sections(self):
        handlers, first, _ = await self.routes()
        api = handlers[first].__self__

        class RejectedSection:
            def __init__(self, app, context):
                pass

            def status(self, operator):
                return Outcome.failed("synthetic_section_unavailable", "private detail")

        class BrokenSection:
            def __init__(self, app, context):
                pass

            def status(self, operator):
                raise RuntimeError("simulated corrupt section data")

        try:
            with patch.dict(api.sections, {"quota": RejectedSection, "image_review": BrokenSection}):
                with self.assertLogs("astrbot_host.configuration_api", level="ERROR") as captured:
                    result = await call(handlers[first], query={"sections": "quota,image_review,person"})
                single = await call(handlers[first], query={"sections": "quota"})

            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["data"]["section_errors"]["quota"]["code"],
                             "synthetic_section_unavailable")
            self.assertEqual(result["data"]["section_errors"]["image_review"]["code"],
                             "configuration_section_read_failed")
            self.assertNotIn("private detail", str(result["data"]))
            self.assertIn("person", result["data"]["sections"])
            self.assertNotIn("quota", result["data"]["sections"])
            self.assertNotIn("image_review", result["data"]["sections"])
            self.assertTrue(any("section=image_review" in line for line in captured.output))
            self.assertEqual(single["status"], "error", single)
            self.assertEqual(single["data"]["code"], "synthetic_section_unavailable")
        finally:
            self.controller.unregister_routes()

    async def test_global_contact_and_role_learning_settings_use_distinct_targets(self):
        handlers, first, second = await self.routes()
        global_route = "/astrbot_plugin_private_companion/config"
        try:
            self.assertIn(global_route, handlers)
            status = await call(handlers[global_route], query={"sections": "proactive.contact"})
            contact = status["data"]["sections"]["proactive.contact"]
            self.assertEqual(contact["scope"], "installation")
            for name in ("role.growth.skills", "expression.learning"):
                denied = await call(handlers[global_route], query={"sections": name})
                self.assertEqual(denied["data"]["code"], "configuration_request_invalid")

            role_sections = (await call(handlers[first], query={
                "sections": "role.growth.skills,expression.learning"}))["data"]["sections"]
            for name in ("role.growth.skills", "expression.learning"):
                self.assertEqual(role_sections[name]["scope"], "role", name)
                self.assertEqual(role_sections[name]["layers"], ["installation", "role"], name)
            self.assertEqual(role_sections["role.growth.skills"]["fields"]["enabled"]["editable_layers"],
                             ["role"])

            denied = await call(handlers[global_route], query={"sections": "screen"})
            self.assertEqual(denied["status"], "error")
            self.assertEqual(denied["data"]["code"], "configuration_request_invalid")

            saved = await call(handlers[global_route + "/configure"], {
                "section": "proactive.contact", "operation_key": "installation-contact-route",
                "edit_token": contact["edit_token"], "changes": {"interval_seconds": 1234},
            })
            self.assertEqual(saved["status"], "ok", saved)
            self.assertEqual(len(saved["data"]["result"]["edit_token"]), 64,
                             "save response must carry the next write token")
            role_sections = (await call(handlers[first], query={
                "sections": "role.growth.skills,expression.learning"}))["data"]["sections"]
            role_saved = await call(handlers[first + "/configure"], {
                "section": "expression.learning", "operation_key": "role-learning-default",
                "edit_token": role_sections["expression.learning"]["edit_token"],
                "changes": {"window_hours": 96},
            })
            self.assertEqual(role_saved["status"], "ok", role_saved)
            from companion_v2.expression.configuration import read_settings
            app = self.controller.session.application
            first_context = self.controller.authority.context("first", "role-a")
            another_role = self.controller.authority.context("second", "role-b")
            self.assertEqual(read_settings(app.runtime, first_context).window_hours, 96)
            self.assertEqual(read_settings(app.runtime, another_role).window_hours, 720)
            for route in (first, second):
                scoped = await call(handlers[route], query={"sections": "proactive.contact"})
                self.assertEqual(scoped["data"]["code"], "configuration_request_invalid")
        finally:
            self.controller.unregister_routes()

    async def test_reaction_retrieval_settings_are_shared_across_windows(self):
        handlers, first, second = await self.routes()
        global_route = "/astrbot_plugin_private_companion/config"
        try:
            denied = await call(handlers[first], query={"sections": "reaction"})
            self.assertEqual(denied["data"]["code"], "configuration_request_invalid")
            first_status = (await call(handlers[global_route], query={"sections": "reaction"}))[
                "data"]["sections"]["reaction"]
            second_status = (await call(handlers[global_route], query={"sections": "reaction"}))[
                "data"]["sections"]["reaction"]
            self.assertEqual(first_status["scope"], "installation")
            self.assertEqual(second_status["scope"], "installation")
            self.assertEqual(first_status["layers"], ["installation"])
            self.assertEqual(first_status["edit_token"], second_status["edit_token"])

            saved = await call(handlers[global_route + "/configure"], {
                "section": "reaction", "operation_key": "reaction-installation-setting",
                "edit_token": first_status["edit_token"], "changes": {"candidate_limit": 9}})
            self.assertEqual(saved["status"], "ok", saved)

            reread = (await call(handlers[global_route], query={"sections": "reaction"}))[
                "data"]["sections"]["reaction"]
            self.assertEqual(reread["settings"]["candidate_limit"], 9)
            self.assertEqual(reread["revision"], first_status["revision"] + 1)
        finally:
            self.controller.unregister_routes()

    async def test_role_and_window_sections_save_independently_and_replay(self):
        handlers, first, second = await self.routes()
        try:
            status = (await call(handlers[first]))["data"]["sections"]
            quota = {"section": "quota", "operation_key": "quota-1",
                     "edit_token": status["quota"]["edit_token"],
                     "changes": {"group_max_daily": 5, "proactive_max_daily": 0}}
            saved = await call(handlers[first + "/configure"], quota)
            self.assertEqual(saved["status"], "ok", saved)
            self.assertTrue((await call(handlers[first + "/configure"], quota))["data"]["result"]["replayed"])
            after = (await call(handlers[first], query={"sections": "quota"}))["data"]["sections"]["quota"]["settings"]
            self.assertEqual((after["group_max_daily"], after["proactive_max_daily"]), (5, 0))
            # Another target keeps its own ceiling.
            other = (await call(handlers[second]))["data"]["sections"]["quota"]["settings"]
            self.assertEqual(other["group_max_daily"], -1)
            # Behavior defaults are role-scoped and reach every conversation for
            # that role unless a window stores an explicit override.
            review_status = (await call(handlers[first], query={"sections": "image_review"}))
            self.assertEqual(review_status["data"]["sections"]["image_review"]["scope"], "role")
            review = {"section": "image_review", "operation_key": "review-1",
                      "edit_token": review_status["data"]["sections"]["image_review"]["edit_token"],
                      "changes": {"enable_group_nsfw_private_fallback": True,
                                  "group_nsfw_image_review_mode": "dual"}}
            reviewed = await call(handlers[first + "/configure"], review)
            self.assertEqual(reviewed["status"], "ok", reviewed)
            sections = (await call(handlers[first], query={"sections": "image_review"}))["data"]["sections"]
            self.assertTrue(sections["image_review"]["settings"]["enable_group_nsfw_private_fallback"])
            self.assertEqual(sections["image_review"]["settings"]["group_nsfw_image_review_mode"], "dual")
            other_review = (await call(handlers[second], query={"sections": "image_review"}))[
                "data"]["sections"]["image_review"]
            self.assertEqual(other_review["settings"]["group_nsfw_image_review_mode"], "single")
            self.assertIsNone(other_review["provenance"]["group_nsfw_image_review_mode"]["layer"])
            self.assertFalse(other_review["provenance"]["group_nsfw_image_review_mode"]["explicit"])
            from companion_v2.configuration.models import ConfigLayer, ConfigTarget
            from companion_v2.conversation.media.review import GROUP as REVIEW_GROUP, configuration_fields as review_fields
            app = self.controller.session.application
            first_context = self.controller.authority.context("first", "role-a")
            second_context = self.controller.authority.context("second", "role-a")
            definitions = review_fields()
            owner = app.configuration._owners[definitions[0].owner_id]
            first_window = ConfigTarget(first_context.scope.installation_id, ConfigLayer.WINDOW,
                first_context.role_binding.role, first_context.scope)
            first_local = owner.read(first_window, definitions)
            owner.write(first_window, definitions,
                changes={REVIEW_GROUP + ".group_nsfw_image_review_sensitivity": "strict"},
                reset=(), expected_revision=first_local.revision, observed_at=app.now())
            first_override = owner.read(first_window, definitions).fields[
                REVIEW_GROUP + ".group_nsfw_image_review_sensitivity"]
            second_window = ConfigTarget(second_context.scope.installation_id, ConfigLayer.WINDOW,
                second_context.role_binding.role, second_context.scope)
            second_default = owner.read(second_window, definitions).fields[
                REVIEW_GROUP + ".group_nsfw_image_review_sensitivity"]
            self.assertEqual((first_override.value, first_override.source, first_override.explicit),
                             ("strict", first_window, True))
            self.assertEqual((second_default.value, second_default.source, second_default.explicit),
                             ("balanced", None, False))

            busy_status = (await call(handlers[first], query={"sections": "busy_gate"}))[
                "data"]["sections"]["busy_gate"]
            self.assertEqual(busy_status["scope"], "role")
            busy_saved = await call(handlers[first + "/configure"], {
                "section": "busy_gate", "operation_key": "role-busy-gate",
                "edit_token": busy_status["edit_token"],
                "changes": {"busy_reply_gate_enabled": True},
            })
            self.assertEqual(busy_saved["status"], "ok", busy_saved)
            from companion_v2.conversation.busy_gate import SettingsReader
            another_scope = replace(first_context.scope, native_scope_id="another-private-user",
                conversation_id="another-conversation", session_id="another-session")
            same_role_window = replace(first_context,
                role_binding=replace(first_context.role_binding, scope=another_scope),
                audience=replace(first_context.audience, scope=another_scope))
            self.assertTrue(SettingsReader(app.runtime).read(same_role_window).busy_reply_gate_enabled)

            life = (await call(handlers[first], query={"sections": "life"}))[
                "data"]["sections"]["life"]
            self.assertEqual(life["scope"], "window")
            self.assertEqual(life["fields"]["sleep_enabled"]["editable_layers"], ["role"])
            self.assertEqual(life["fields"]["sleep_enabled"]["write_layer"], "role")
            self.assertEqual(life["fields"]["diary_enabled"]["editable_layers"], ["window"])
            self.assertEqual(life["fields"]["diary_enabled"]["write_layer"], "window")
            quota_after = (await call(handlers[first], query={"sections": "quota"}))
            self.assertEqual(quota_after["data"]["sections"]["quota"]["settings"]["group_max_daily"], 5)
            # The target route rejects installation-owned groups; it cannot use a
            # role or window path as a shortcut to shared settings.
            quota_token = quota_after["data"]["sections"]["quota"]["edit_token"]
            crossed = await call(handlers[first + "/configure"],
                                    {"section": "reaction", "operation_key": "cross",
                                     "edit_token": quota_token, "changes": {"candidate_limit": 3}})
            self.assertEqual(crossed["data"]["code"], "configuration_request_invalid")
        finally:
            self.controller.unregister_routes()

    async def test_budget_editor_saves_global_controls_and_disables_legacy_role_limits(self):
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from companion_v2.configuration.owners import SparseConfigurationOwner
        from companion_v2.model_gateway.budget import GROUP, SOFT_FIELDS, effective_soft

        handlers, first, second = await self.routes()
        global_route = "/astrbot_plugin_private_companion/config"
        try:
            denied = await call(handlers[first], query={"sections": "budget"})
            self.assertEqual(denied["data"]["code"], "configuration_request_invalid")
            status = (await call(handlers[global_route], query={"sections": "budget"}))["data"]["sections"]["budget"]
            self.assertEqual((status["scope"], status["layers"]), ("installation", ["installation"]))
            enabled = await call(handlers[global_route + "/configure"], {
                "section": "budget", "operation_key": "global-budget-enable",
                "edit_token": status["edit_token"],
                "changes": {"enable_daily_token_soft_limit": True, "daily_token_soft_limit": 1000},
            })
            self.assertEqual(enabled["status"], "ok", enabled)
            app = self.controller.session.application
            context = self.controller.authority.context("first", "role-a")
            self.assertTrue(effective_soft(app.runtime, context, app.gateway.ledger.soft).enabled)

            role = ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                                context.role_binding.role, bot_id=context.scope.bot_id)
            owner = SparseConfigurationOwner(app.runtime)
            owner.write(role, SOFT_FIELDS, changes={GROUP + ".enable_daily_token_soft_limit": True},
                reset=(), expected_revision=owner.read(role, SOFT_FIELDS).revision, observed_at=self.clock.now())
            other = (await call(handlers[global_route], query={"sections": "budget"}))["data"]["sections"]["budget"]
            self.assertTrue(other["settings"]["enable_daily_token_soft_limit"])
            self.assertEqual(other["settings"]["daily_token_soft_limit"], 1000)
            disabled = await call(handlers[global_route + "/configure"], {
                "section": "budget", "operation_key": "global-budget-disable",
                "edit_token": other["edit_token"], "changes": {"enable_daily_token_soft_limit": False},
            })
            self.assertEqual(disabled["status"], "ok", disabled)
            reread = (await call(handlers[global_route], query={"sections": "budget"}))["data"]["sections"]["budget"]
            self.assertFalse(reread["settings"]["enable_daily_token_soft_limit"])
            resolved = effective_soft(app.runtime, context, app.gateway.ledger.soft)
            self.assertEqual((resolved.enabled, resolved.tokens), (False, 1000))
        finally:
            self.controller.unregister_routes()

    async def test_role_profile_is_saved_for_one_role_and_not_another(self):
        handlers, first, second = await self.routes()
        try:
            profile = (await call(handlers[first]))["data"]["sections"]["role_profile"]
            response = await call(handlers[first + "/configure"], {
                "section": "role_profile", "operation_key": "profile-first",
                "edit_token": profile["edit_token"],
                "changes": {"identity": "住在旧城边的电台编辑", "world": "信件总会晚一个雨季才抵达。"},
            })
            self.assertEqual(response["status"], "ok", response)
            first_saved = (await call(handlers[first], query={"sections": "role_profile"}))[
                "data"]["sections"]["role_profile"]["settings"]
            self.assertEqual(first_saved["identity"], "住在旧城边的电台编辑")
            self.assertEqual(first_saved["world"], "信件总会晚一个雨季才抵达。")
            other = (await call(handlers[second]))["data"]["sections"]["role_profile"]["settings"]
            self.assertEqual(other["identity"], "")
            self.assertEqual(other["world"], "")
        finally:
            self.controller.unregister_routes()

    async def test_relationship_profile_is_saved_per_person_for_the_same_role(self):
        config = multi_config(delivery_mode="onebot_private")
        config["multi_host"]["targets"].append({
            "target_id": "third", "platform_id": "qq-main", "account_id": "100",
            "bot_id": "bot-a", "participant_id": "201",
        })
        self.controller = self.make_controller(config)
        await self.start_host()
        (await self.controller.authorize_delivery("first")).unwrap()
        self.controller.register_routes()
        handlers = {route: handler for route, handler, _, _ in self.native.registered_web_apis}
        first = next(route for route in handlers if route.endswith("/config") and "/model/" not in route
                     and "/targets/first/" in route)
        third = next(route for route in handlers if route.endswith("/config") and "/model/" not in route
                     and "/targets/third/" in route)
        try:
            relationship = (await call(handlers[first]))["data"]["sections"]["relationship_profile"]
            response = await call(handlers[first + "/configure"], {
                "section": "relationship_profile", "operation_key": "relationship-first",
                "edit_token": relationship["edit_token"],
                "changes": {"default_nickname": "小舟", "role_relation": "经确认的搭档"},
            })
            self.assertEqual(response["status"], "ok", response)
            first_saved = (await call(handlers[first], query={"sections": "relationship_profile"}))[
                "data"]["sections"]["relationship_profile"]["settings"]
            self.assertEqual(first_saved["default_nickname"], "小舟")
            self.assertEqual(first_saved["role_relation"], "经确认的搭档")
            other = (await call(handlers[third]))["data"]["sections"]["relationship_profile"]["settings"]
            self.assertEqual(other["default_nickname"], "")
            self.assertEqual(other["role_relation"], "")
        finally:
            self.controller.unregister_routes()

    async def test_the_contact_section_uses_one_window_daily_contact_quota(self):
        handlers, first, _ = await self.routes()
        global_route = "/astrbot_plugin_private_companion/config"
        try:
            contact = (await call(handlers[global_route], query={"sections": "proactive.contact"}))[
                "data"]["sections"]["proactive.contact"]
            self.assertEqual(contact["group_id"], "proactive.contact")
            self.assertEqual(contact["scope"], "installation")
            self.assertEqual(contact["layers"], ["installation"])
            self.assertEqual(set(contact["settings"]), set(contact["provenance"]))
            self.assertTrue({"enabled", "interval_seconds", "quiet_timezone", "max_contacts_24h"}
                            .issubset(contact["settings"]))
            self.assertFalse(contact["settings"]["enabled"])
            self.assertEqual(contact["settings"]["interval_seconds"], 3600)
            self.assertEqual(contact["settings"]["quiet_timezone"], "Asia/Shanghai")
            self.assertNotIn("max_relationship_contacts_24h", contact["settings"])
            self.assertNotIn("max_relationship_attempts_24h", contact["settings"])
            self.assertNotIn("max_attempts_24h", contact["settings"])
            self.assertNotIn("proactive_persona_judge_daily_limit", contact["settings"])
            self.assertIn("window", contact["fields"]["max_contacts_24h"]["editable_layers"])
            self.assertNotIn("relationship", contact["fields"]["max_contacts_24h"]["editable_layers"])
            self.assertNotIn("max_attempts_24h", contact["fields"])
            self.assertNotIn("proactive_persona_judge_daily_limit", contact["fields"])
            self.assertIn("window", contact["fields"]["interval_seconds"]["editable_layers"])
            budget = await call(handlers[global_route + "/configure"],
                {"section": "proactive.contact", "operation_key": "contact-budget",
                 "edit_token": contact["edit_token"],
                 "changes": {"max_contacts_24h": 12}})
            self.assertEqual(budget["status"], "ok", budget)
            contact = (await call(handlers[global_route], query={"sections": CONTACT_GROUP}))[
                "data"]["sections"][CONTACT_GROUP]
            saved = await call(handlers[global_route + "/configure"],
                {"section": "proactive.contact", "operation_key": "contact-rhythm",
                 "edit_token": contact["edit_token"],
                 "changes": {"interval_seconds": 900, "quiet_enabled": True}})
            self.assertEqual(saved["status"], "ok", saved)
            after = (await call(handlers[global_route], query={"sections": "proactive.contact"}))[
                "data"]["sections"]["proactive.contact"]
            self.assertEqual(after["settings"]["interval_seconds"], 900)
            self.assertTrue(after["settings"]["quiet_enabled"])
            self.assertEqual(after["settings"]["max_contacts_24h"], 12)
            self.assertNotIn("max_attempts_24h", after["settings"])
            # 保存后编辑令牌必然更新，否则两次保存会互相覆盖。
            self.assertNotEqual(after["edit_token"], contact["edit_token"])
        finally:
            self.controller.unregister_routes()

    async def test_contact_save_returns_receipt_without_reading_status(self):
        from astrbot_host.configuration_api import ConfigurationWebAPI

        handlers, first, _ = await self.routes()
        global_route = "/astrbot_plugin_private_companion/config"
        try:
            initial = (await call(handlers[global_route], query={"sections": "proactive.contact"}))
            contact = initial["data"]["sections"]["proactive.contact"]
            body = {"section": "proactive.contact", "operation_key": "contact-save-isolated",
                    "edit_token": contact["edit_token"], "changes": {"interval_seconds": 1200}}
            with patch.object(ConfigurationWebAPI, "_status", new_callable=AsyncMock) as api_status, \
                    patch("companion_v2.configuration.management.GroupEditor.status",
                          side_effect=AssertionError("a write must not read its post-save status")) as editor_status:
                saved = await call(handlers[global_route + "/configure"], body)
                replayed = await call(handlers[global_route + "/configure"], body)

            api_status.assert_not_awaited()
            editor_status.assert_not_called()
            self.assertEqual(saved["status"], "ok", saved)
            self.assertEqual(set(saved["data"]), {"result"})
            self.assertFalse(saved["data"]["result"]["replayed"])
            self.assertTrue(replayed["data"]["result"]["replayed"])
            self.assertEqual(saved["data"]["result"]["operation_id"],
                             replayed["data"]["result"]["operation_id"])
            refreshed = (await call(handlers[global_route], query={"sections": "proactive.contact"}))[
                "data"]["sections"]["proactive.contact"]
            self.assertEqual(refreshed["settings"]["interval_seconds"], 1200)
        finally:
            self.controller.unregister_routes()

    async def test_contact_global_switch_saves_without_changing_any_participant_grant(self):
        handlers, first, _ = await self.routes()
        global_route = "/astrbot_plugin_private_companion/config"
        try:
            identity = self.controller.session.application.tasks.identity
            contact = (await call(handlers[global_route], query={"sections": CONTACT_GROUP}))[
                "data"]["sections"][CONTACT_GROUP]
            before = identity.snapshot()
            body = {"section": CONTACT_GROUP, "operation_key": "contact-enable-first",
                    "edit_token": contact["edit_token"], "changes": {"enabled": True}}
            saved = await call(handlers[global_route + "/configure"], body)
            self.assertEqual(saved["status"], "ok", saved)
            self.assertFalse(saved["data"]["result"]["replayed"])

            contact = (await call(handlers[global_route], query={"sections": CONTACT_GROUP}))[
                "data"]["sections"][CONTACT_GROUP]
            self.assertTrue(contact["settings"]["enabled"])
            disabled = await call(handlers[global_route + "/configure"], {
                "section": CONTACT_GROUP, "operation_key": "contact-disable",
                "edit_token": contact["edit_token"], "changes": {"enabled": False}})
            self.assertEqual(disabled["status"], "ok", disabled)
            contact = (await call(handlers[global_route], query={"sections": CONTACT_GROUP}))[
                "data"]["sections"][CONTACT_GROUP]
            self.assertFalse(contact["settings"]["enabled"])
            enabled_again = await call(handlers[global_route + "/configure"], {
                "section": CONTACT_GROUP, "operation_key": "contact-enable-again",
                "edit_token": contact["edit_token"], "changes": {"enabled": True}})
            self.assertEqual(enabled_again["status"], "ok", enabled_again)
            self.assertEqual(identity.snapshot(), before)
        finally:
            self.controller.unregister_routes()

    async def test_contact_global_settings_survive_participant_role_account_changes_and_host_restart(self):
        config = multi_config(delivery_mode="onebot_private")
        config["multi_host"]["targets"].append({"target_id": "third", "platform_id": "qq-main",
            "account_id": "100", "bot_id": "bot-a", "participant_id": "201"})
        self.controller = self.make_controller(config)
        await self.start_host()

        global_route = "/astrbot_plugin_private_companion/config"

        def registered_paths():
            self.controller.register_routes()
            handlers = {route: handler for route, handler, _, _ in self.native.registered_web_apis}
            return handlers

        handlers = registered_paths()
        read = lambda: call(handlers[global_route], query={"sections": CONTACT_GROUP})
        try:
            before = (await read())["data"]["sections"][CONTACT_GROUP]
            identity = self.controller.session.application.tasks.identity
            identity_before = identity.snapshot()
            saved = await call(handlers[global_route + "/configure"], {"section": CONTACT_GROUP,
                "operation_key": "stable-global-rhythm", "edit_token": before["edit_token"],
                "changes": {"enabled": True, "automatic_enabled": True, "quiet_start": "21:15",
                            "proactive_hour_activity_enabled": True}})
            self.assertEqual(saved["status"], "ok", saved)
            self.assertEqual(identity.snapshot(), identity_before)
            reread = (await read())["data"]["sections"][CONTACT_GROUP]
            self.assertTrue(reread["settings"]["enabled"])
            self.assertTrue(reread["settings"]["automatic_enabled"])
            self.assertEqual(reread["settings"]["quiet_start"], "21:15")
            stale = await call(handlers[global_route + "/configure"], {"section": CONTACT_GROUP,
                "operation_key": "stale-other-participant", "edit_token": before["edit_token"],
                "changes": {"enabled": False}})
            self.assertEqual(stale["data"]["code"], "configuration_revision_conflict")
            other = (await read())["data"]["sections"][CONTACT_GROUP]
            self.assertTrue(other["settings"]["enabled"])
            self.assertEqual(other["edit_token"], reread["edit_token"])

            self.controller.unregister_routes()
            await self.controller.terminate()
            self.controller = self.make_controller(config)
            (await self.controller.load_existing()).unwrap()
            (await self.controller.start()).unwrap()
            handlers = registered_paths()
            after_restart = (await read())["data"]["sections"][CONTACT_GROUP]
            self.assertEqual(after_restart["settings"], reread["settings"])
            self.assertEqual(after_restart["revision"], reread["revision"])
            self.assertEqual(self.native.provider.client.state.calls, [])
        finally:
            self.controller.unregister_routes()

    async def test_contact_replayed_global_save_never_reauthorizes_a_revoked_participant(self):
        handlers, first, _ = await self.routes()
        global_route = "/astrbot_plugin_private_companion/config"
        try:
            app = self.controller.session.application
            context = self.controller.authority.context("first", "role-a")
            identity = app.tasks.identity
            grant = identity.issue_grant(grant_id=PROACTIVE_GRANT_PREFIX + "existing-contact",
                subject=context.actor.subject, scope=context.scope, capability=CONTACT_GROUP,
                tier=AuthorizationTier.STANDARD, valid_from=self.clock.now(), expected_revision=identity.revision)
            contact = (await call(handlers[global_route], query={"sections": CONTACT_GROUP}))[
                "data"]["sections"][CONTACT_GROUP]
            body = {"section": CONTACT_GROUP, "operation_key": "contact-legacy-save",
                    "edit_token": contact["edit_token"], "changes": {"enabled": True}}
            with patch.object(ProactiveContactManagement, "_after_configure"):
                first_save = await call(handlers[global_route + "/configure"], body)
            self.assertEqual(first_save["status"], "ok", first_save)

            recovered = await call(handlers[global_route + "/configure"], body)
            self.assertEqual(recovered["status"], "ok", recovered)
            self.assertTrue(recovered["data"]["result"]["replayed"])
            access = lambda: identity.authorize(context.actor, scope=context.scope,
                capability=CONTACT_GROUP, observed_at=self.clock.now())
            self.assertTrue(access().is_success)
            identity.revoke_grant(grant.grant_id, revoked_at=self.clock.now(),
                                  expected_revision=identity.revision)
            revision = identity.revision

            replay_after_revoke = await call(handlers[global_route + "/configure"], body)
            self.assertEqual(replay_after_revoke["status"], "ok", replay_after_revoke)
            self.assertTrue(replay_after_revoke["data"]["result"]["replayed"])
            self.assertFalse(access().is_success)
            self.assertEqual(identity.revision, revision)
        finally:
            self.controller.unregister_routes()

    async def test_contact_switch_does_not_override_an_explicit_person_revocation(self):
        handlers, first, _ = await self.routes()
        global_route = "/astrbot_plugin_private_companion/config"
        try:
            contact = (await call(handlers[global_route], query={"sections": CONTACT_GROUP}))[
                "data"]["sections"][CONTACT_GROUP]
            app = self.controller.session.application
            context = self.controller.authority.context("first", "role-a")
            identity = app.tasks.identity
            grant_id = PERSON_PERMISSION_GRANT_PREFIX + "explicit-contact-revocation"
            identity.issue_grant(grant_id=grant_id, subject=context.actor.subject, scope=context.scope,
                capability=CONTACT_GROUP, tier=AuthorizationTier.STANDARD, valid_from=self.clock.now(),
                expected_revision=identity.revision)
            identity.revoke_grant(grant_id, revoked_at=self.clock.now(), expected_revision=identity.revision)
            contact = (await call(handlers[global_route], query={"sections": CONTACT_GROUP}))[
                "data"]["sections"][CONTACT_GROUP]

            saved = await call(handlers[global_route + "/configure"], {
                "section": CONTACT_GROUP, "operation_key": "contact-after-explicit-revoke",
                "edit_token": contact["edit_token"], "changes": {"enabled": True}})
            self.assertEqual(saved["status"], "ok", saved)
            self.assertEqual([grant for grant in identity.snapshot().grants
                              if grant.grant_id.startswith(PROACTIVE_GRANT_PREFIX)], [])
            denied = identity.authorize(context.actor, scope=context.scope,
                capability=CONTACT_GROUP, observed_at=self.clock.now())
            self.assertEqual(denied.failure.code, "authorization_revoked")
        finally:
            self.controller.unregister_routes()

    async def test_life_enabled_editor_keeps_its_window_grants_in_sync(self):
        handlers, first, _ = await self.routes()
        try:
            life = (await call(handlers[first], query={"sections": "life"}))["data"]["sections"]["life"]
            app = self.controller.session.application
            with patch.object(app, "notify") as notify:
                enabled = await call(handlers[first + "/configure"], {
                    "section": "life", "operation_key": "life-enable-first",
                    "edit_token": life["edit_token"], "changes": {"enabled": True}})
            self.assertEqual(enabled["status"], "ok", enabled)
            self.assertFalse(enabled["data"]["result"]["replayed"])
            notify.assert_called_once()
            context = self.controller.authority.context("first", "role-a")
            identity = app.tasks.identity
            actions = ("role.life.run", "role.life.read", "role.journal.write", "role.reading.record")
            access = lambda action: identity.authorize(context.actor, scope=context.scope,
                capability=action, observed_at=self.clock.now())
            self.assertTrue(all(access(action).is_success for action in actions),
                [(action, access(action).failure.code if access(action).failure else None)
                 for action in actions] + [grant.grant_id for grant in identity.snapshot().grants
                                           if grant.grant_id.startswith("life-grant-")])

            life = (await call(handlers[first], query={"sections": "life"}))["data"]["sections"]["life"]
            disabled = await call(handlers[first + "/configure"], {
                "section": "life", "operation_key": "life-disable",
                "edit_token": life["edit_token"], "changes": {"enabled": False}})
            self.assertEqual(disabled["status"], "ok", disabled)
            self.assertTrue(all(not access(action).is_success for action in actions))

            life = (await call(handlers[first], query={"sections": "life"}))["data"]["sections"]["life"]
            enabled_again = await call(handlers[first + "/configure"], {
                "section": "life", "operation_key": "life-enable-again",
                "edit_token": life["edit_token"], "changes": {"enabled": True}})
            self.assertEqual(enabled_again["status"], "ok", enabled_again)
            self.assertTrue(all(access(action).is_success for action in actions))
        finally:
            self.controller.unregister_routes()

    async def test_life_replayed_save_recovers_missing_grants_but_not_revoked_lineages(self):
        handlers, first, _ = await self.routes()
        try:
            life = (await call(handlers[first], query={"sections": "life"}))["data"]["sections"]["life"]
            body = {"section": "life", "operation_key": "life-legacy-save",
                    "edit_token": life["edit_token"], "changes": {"enabled": True}}
            with patch.object(DiaryAndRestManagement, "_after_configure"):
                first_save = await call(handlers[first + "/configure"], body)
            self.assertEqual(first_save["status"], "ok", first_save)

            recovered = await call(handlers[first + "/configure"], body)
            self.assertEqual(recovered["status"], "ok", recovered)
            self.assertTrue(recovered["data"]["result"]["replayed"])
            app = self.controller.session.application
            context = self.controller.authority.context("first", "role-a")
            identity = app.tasks.identity
            actions = ("role.life.run", "role.life.read", "role.journal.write", "role.reading.record")
            grants = [grant for grant in identity.snapshot().grants
                      if grant.grant_id.startswith("life-grant-") and grant.scope == context.scope
                      and grant.subject == context.actor.subject]
            self.assertEqual({grant.capability for grant in grants}, set(actions))
            for grant in grants:
                identity.revoke_grant(grant.grant_id, revoked_at=self.clock.now(),
                                      expected_revision=identity.revision)

            replay_after_revoke = await call(handlers[first + "/configure"], body)
            self.assertEqual(replay_after_revoke["status"], "ok", replay_after_revoke)
            self.assertTrue(replay_after_revoke["data"]["result"]["replayed"])
            self.assertTrue(all(not identity.authorize(context.actor, scope=context.scope, capability=action,
                observed_at=self.clock.now()).is_success for action in actions))
        finally:
            self.controller.unregister_routes()

    async def test_window_and_role_configuration_editors_use_runtime_targets(self):
        handlers, first, _ = await self.routes()
        global_route = "/astrbot_plugin_private_companion/config"
        try:
            screen = (await call(handlers[first], query={"sections": "screen"}))[
                "data"]["sections"]["screen"]
            saved = await call(handlers[first + "/configure"], {
                "section": "screen", "operation_key": "screen-window-settings",
                "edit_token": screen["edit_token"],
                "changes": {"interpretation_enabled": True, "journal_material_enabled": True,
                            "journal_material_max_chars": 840}})
            self.assertEqual(saved["status"], "ok", saved)

            app = self.controller.session.application
            context = self.controller.authority.context("first", "role-a")
            settings = ScreenSettings(app.configuration).read(context, self.clock.now())
            self.assertEqual(settings, {"interpretation_enabled": True,
                "journal_material_enabled": True, "journal_material_max_chars": 840})
            other_scope = replace(context.scope, native_scope_id="another-person-window",
                conversation_id="another-person-conversation", session_id="another-person-session")
            other_window = replace(context,
                role_binding=replace(context.role_binding, scope=other_scope),
                audience=replace(context.audience, scope=other_scope))
            # This other person is intentionally not registered as a dashboard
            # target. Inspect the owner directly so the assertion tests storage
            # isolation instead of failing on the configuration-read grant.
            other_settings = ScreenConfigurationOwner(app.runtime).read(
                screen_target_for(other_window), screen_configuration_fields()).fields
            self.assertFalse(other_settings["screen.interpretation_enabled"].value)
            self.assertEqual(screen["fields"]["interpretation_enabled"]["editable_layers"], ["window"])

            global_skills = await call(handlers[global_route], query={"sections": "role.growth.skills"})
            self.assertEqual(global_skills["data"]["code"], "configuration_request_invalid")
            skills = (await call(handlers[first], query={"sections": "role.growth.skills"}))[
                "data"]["sections"]["role.growth.skills"]
            expression = (await call(handlers[first], query={"sections": "expression.learning"}))[
                "data"]["sections"]["expression.learning"]
            self.assertEqual(skills["scope"], "role")
            self.assertEqual(skills["layers"], ["installation", "role"])
            self.assertEqual(skills["fields"]["enabled"]["editable_layers"], ["role"])
            self.assertEqual(expression["scope"], "role")
            self.assertEqual(expression["layers"], ["installation", "role"])
            first_skills = await call(handlers[first + "/configure"], {
                "section": "role.growth.skills", "operation_key": "skills-role-value",
                "edit_token": skills["edit_token"],
                "changes": {"enabled": True, "custom_skills": '["摄影"]',
                            "schedule_influence_strength": 1}})
            self.assertEqual(first_skills["status"], "ok", first_skills)
            expression = (await call(handlers[first], query={"sections": "expression.learning"}))[
                "data"]["sections"]["expression.learning"]
            expression_save = await call(handlers[first + "/configure"], {
                "section": "expression.learning", "operation_key": "expression-role-value",
                "edit_token": expression["edit_token"], "changes": {"window_hours": 96}})
            self.assertEqual(expression_save["status"], "ok", expression_save)
            skills = (await call(handlers[first], query={"sections": "role.growth.skills"}))[
                "data"]["sections"]["role.growth.skills"]
            updated_skills = await call(handlers[first + "/configure"], {
                "section": "role.growth.skills", "operation_key": "skills-role-update",
                "edit_token": skills["edit_token"], "changes": {"custom_skills": '["天文"]'}})
            self.assertEqual(updated_skills["status"], "ok", updated_skills)
            self.assertEqual(skill_settings(app.runtime, context), (("天文",), 1))
            second_context = self.controller.authority.context("second", "role-b")
            self.assertEqual(skill_settings(app.runtime, second_context), ((), 0))
            self.assertEqual(expression_read_settings(app.runtime, context).window_hours, 96)
            self.assertEqual(expression_read_settings(app.runtime, second_context).window_hours, 720)

            private_status = (await call(handlers[first], query={"sections": "role.growth.skills"}))
            self.assertEqual(private_status["status"], "ok")
            self.assertEqual(private_status["data"]["sections"]["role.growth.skills"]["scope"], "role")
        finally:
            self.controller.unregister_routes()

    async def test_system_prompt_section_round_trips_and_restores_the_builtin_text(self):
        handlers, first, _ = await self.routes()
        try:
            response = await call(handlers[first], query={"sections": "system.prompts"})
            self.assertEqual(response["status"], "ok", response)
            section = response["data"]["sections"]["system.prompts"]
            default = section["fields"]["voice_drama"]["default"]
            self.assertEqual(section["settings"]["voice_drama"], default)
            self.assertEqual(section["fields"]["voice_drama"]["maximum_chars"], 8000)
            saved = await call(handlers[first + "/configure"], {
                "section": "system.prompts", "operation_key": "system-prompt-save",
                "edit_token": section["edit_token"],
                "changes": {"voice_drama": "自定义 Drama 规则"}})
            self.assertEqual(saved["status"], "ok", saved)
            changed = (await call(handlers[first], query={"sections": "system.prompts"}))[
                "data"]["sections"]["system.prompts"]
            self.assertEqual(changed["settings"]["voice_drama"], "自定义 Drama 规则")
            restored = await call(handlers[first + "/configure"], {
                "section": "system.prompts", "operation_key": "system-prompt-restore",
                "edit_token": changed["edit_token"], "changes": {}, "reset": ["voice_drama"]})
            self.assertEqual(restored["status"], "ok", restored)
            final = (await call(handlers[first], query={"sections": "system.prompts"}))[
                "data"]["sections"]["system.prompts"]
            self.assertEqual(final["settings"]["voice_drama"], default)
        finally:
            self.controller.unregister_routes()

    async def test_growth_permissions_are_not_exposed_by_the_generic_config_route(self):
        handlers, first, _ = await self.routes()
        try:
            response = await call(handlers[first], query={"sections": "role_growth"})
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["data"]["code"], "configuration_request_invalid")
            sections = (await call(handlers[first]))["data"]["sections"]
            self.assertNotIn("role_growth", sections)
        finally:
            self.controller.unregister_routes()

    async def test_the_busy_gate_section_round_trips_its_keyword_lists(self):
        handlers, first, _ = await self.routes()
        try:
            gate = (await call(handlers[first]))["data"]["sections"]["busy_gate"]
            self.assertFalse(gate["settings"]["busy_reply_gate_enabled"])
            self.assertEqual(gate["fields"]["busy_reply_min_delay_seconds"]["maximum"], 900)
            answer = await call(handlers[first + "/configure"],
                {"section": "busy_gate", "operation_key": "gate-1", "edit_token": gate["edit_token"],
                 "changes": {"busy_reply_gate_enabled": True, "busy_reply_min_delay_seconds": 45,
                             "busy_activity_keywords": "开会, 赶稿、写代码"}})
            self.assertEqual(answer["status"], "ok", answer)
            after = (await call(handlers[first], query={"sections": "busy_gate"}))["data"]["sections"]["busy_gate"]
            settings = after["settings"]
            self.assertTrue(settings["busy_reply_gate_enabled"])
            self.assertEqual(settings["busy_reply_min_delay_seconds"], 45)
            # The stored form keeps the separators the operator typed; the parsed
            # keyword list is what the gate reads.
            self.assertEqual(settings["busy_activity_keywords"], "开会, 赶稿、写代码")
            # A keyword list the gate would reject never reaches storage.
            refused = await call(handlers[first + "/configure"],
                {"section": "busy_gate", "operation_key": "gate-2",
                 "edit_token": after["edit_token"],
                 "changes": {"busy_activity_keywords": "x" * 25}})
            self.assertEqual(refused["data"]["code"], "busy_gate_settings_invalid")
        finally:
            self.controller.unregister_routes()

    async def test_content_authoring_settings_round_trip_at_role_scope(self):
        handlers, first, _ = await self.routes()
        try:
            initial = (await call(handlers[first]))["data"]["sections"]["content.authoring"]
            self.assertEqual(initial["scope"], "role")
            self.assertEqual(initial["layers"], ["installation", "role"])
            answer = await call(handlers[first + "/configure"],
                {"section": "content.authoring", "operation_key": "authoring-1",
                 "edit_token": initial["edit_token"],
                 "changes": {"enabled": True, "direction_prompt": "保持清晰、克制的叙事风格。"}})
            self.assertEqual(answer["status"], "ok", answer)
            settings = (await call(handlers[first], query={"sections": "content.authoring"}))[
                "data"]["sections"]["content.authoring"]["settings"]
            self.assertTrue(settings["enabled"])
            self.assertEqual(settings["direction_prompt"], "保持清晰、克制的叙事风格。")
        finally:
            self.controller.unregister_routes()

    async def test_invalid_requests_and_unknown_fields_are_refused(self):
        handlers, first, _ = await self.routes()
        global_route = "/astrbot_plugin_private_companion/config"
        try:
            status = (await call(handlers[global_route]))["data"]["sections"]
            token = status["reaction"]["edit_token"]
            denied = await call(handlers[first + "/configure"],
                {"section": "reaction", "operation_key": "wrong-route", "edit_token": token,
                 "changes": {"candidate_limit": 3}})
            self.assertEqual(denied["data"]["code"], "configuration_request_invalid")
            for changes in ({"nope": 1}, {"candidate_limit": 0}, {"candidate_limit": 99},
                            {"embedding_score_threshold": 2}, {"vision_verify_mode": "sometimes"}):
                answer = await call(handlers[global_route + "/configure"],
                                       {"section": "reaction", "operation_key": "bad-" + str(len(changes)),
                                        "edit_token": token, "changes": changes})
                self.assertEqual(answer["status"], "error", changes)
                self.assertNotEqual(answer["data"]["code"], "staging_host_terminated", changes)
            self.assertEqual((await call(handlers[global_route + "/configure"],
                                            {"section": "unknown", "operation_key": "k", "edit_token": token,
                                             "changes": {"candidate_limit": 3}}))["data"]["code"],
                             "configuration_request_invalid")
            self.assertEqual((await call(handlers[global_route + "/configure"],
                                            {"section": "reaction", "operation_key": "k", "edit_token": token,
                                             "changes": {"candidate_limit": 3}, "extra": 1}))["data"]["code"],
                             "configuration_request_invalid")
            self.assertEqual((await call(handlers[first], username="api_key:x"))["data"]["code"],
                             "dashboard_identity_required")
        finally:
            self.controller.unregister_routes()


class SingleHostConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_host_registers_the_route(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from tests.test_management_access import NOW

        temporary = TemporaryDirectory(prefix="companion-config-api-")
        self.addCleanup(temporary.cleanup)
        native = support.WebContext()
        config = support.host_config(staging={"profile_id": "config-api", "delivery_mode": "onebot_private"})
        types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi)
        controller = AstrBotStagingController(native, config, Path(temporary.name), native_types=types, now=lambda: NOW)
        self.addAsyncCleanup(controller.terminate)
        (await controller.initialize_profile()).unwrap()
        (await controller.authorize_delivery()).unwrap()
        controller.register_routes()
        routes = {route for route, _, _, _ in native.registered_web_apis}
        self.assertIn("/astrbot_plugin_private_companion/config", routes)
        self.assertIn("/astrbot_plugin_private_companion/config/configure", routes)
        controller.unregister_routes()
        self.assertEqual(native.registered_web_apis, [])


if __name__ == "__main__":
    unittest.main()
