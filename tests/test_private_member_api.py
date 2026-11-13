"""Private-member settings affect live consumers and preserve exact routes."""

import json
from dataclasses import replace
from datetime import timedelta
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from tests.multi_host_support import MultiFixture, multi_config
from tests.test_person_authorization import _Query, _body
from astrbot_host.acquaintance_api import AcquaintanceWebAPI, _group_target_access, _persona_metadata
from astrbot_host.management_identity import dashboard_operator
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.memory.person.access import owner_key
from companion_v2.proactive.management import ContactManagement
from companion_v2.configuration.management import AcquaintanceManagement, ProactiveContactManagement
from companion_v2.configuration.models import ConfigLayer
from companion_v2.expression.configuration import read_settings as expression_settings
from companion_v2.agenda.models import AgendaTarget
from companion_v2.agenda.service import AgendaService
from companion_v2.foundation.refs import OperationRef, VersionRef
from companion_v2.proactive.configuration import target_for
from companion_v2.proactive.rhythm_configuration import relationship_target_for
from companion_v2.conversation.reply_management import ReplyManagement


class AcquaintancePersonaMetadataTests(unittest.TestCase):
    def test_persona_label_is_resolved_from_bot_and_role(self):
        settings = NS(all_targets=(), personas=(
            NS(bot_id="bot-a", role_id="role-a", persona_id="星缘-私聊"),
            NS(bot_id="bot-a", role_id="role-b", persona_id="星缘-私聊"),
            NS(bot_id="bot-a", role_id="role-default", persona_id=None),
            NS(bot_id="bot-b", role_id="role-a", persona_id="另一人格"),
        ))
        self.assertEqual(_persona_metadata(settings, "bot-a", "role-a"),
                         {"persona_ids": ["星缘-私聊"], "role_name": "星缘"})
        self.assertEqual(_persona_metadata(settings, "bot-a", "role-b"),
                         {"persona_ids": ["星缘-私聊"], "role_name": "星缘"})
        self.assertEqual(_persona_metadata(settings, "bot-a", "role-default"),
                         {"persona_ids": [""], "role_name": "默认人格"})
        self.assertEqual(_persona_metadata(settings, "bot-c", "role-a"), {})


class GroupTargetAccessTests(unittest.TestCase):
    def test_single_mode_never_advertises_group_target_editing(self):
        controller = NS(_multi_host=False, _accepting=False, _terminated=False, session=None)
        access = _group_target_access(controller, NS())
        self.assertFalse(access["editable"])
        self.assertEqual(access["accounts"], [])
        self.assertEqual(access["persona_bots"], [])

    def test_shared_mode_requires_a_stopped_host_and_reports_bound_routes(self):
        settings = NS(all_targets=(NS(platform_id="qq", account_id="100", bot_id="bot-a"),),
                      personas=(NS(bot_id="bot-a"),))
        stopped = NS(_multi_host=True, _accepting=False, _terminated=False,
                     session=NS(application=NS(state="stopped")))
        access = _group_target_access(stopped, settings)
        self.assertTrue(access["editable"])
        self.assertEqual(access["accounts"], [{"platform_id": "qq", "account_id": "100", "bot_id": "bot-a"}])
        self.assertEqual(access["persona_bots"], ["bot-a"])

        stopped.session.application.state = "running"
        access = _group_target_access(stopped, settings)
        self.assertTrue(access["running"])
        self.assertFalse(access["editable"])


class PrivateMemberTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def ready(self):
        app = await self.start_host()
        await self.controller.receive_claimed(self.event("first", "first-input"))
        await self.controller.receive_claimed(self.event("second", "second-input"))
        self.api = AcquaintanceWebAPI(self.controller)
        return app

    def person(self, target="first"):
        context = self.controller.authority.context(target, "role-a")
        return {"subject_id": context.actor.subject.subject_id,
                "route": {"platform": context.scope.platform, "bot_id": context.scope.bot_id,
                          "account_id": context.scope.account_id, "role_id": context.role_binding.role.subject_id}}

    async def send(self, action, *, person=None, command=None, username="member-admin"):
        person = person or self.person()
        query = "&".join(key + "=" + value for key, value in
                         {"subject_id": person["subject_id"], **person["route"]}.items()) if command is None else ""
        payload = json.dumps(command or {}, ensure_ascii=False).encode("utf-8")
        with patch("astrbot_host.acquaintance_api.request",
                   NS(username=username, query=_Query(query), body=_body(payload))):
            return await getattr(self.api, action)()

    async def settings(self, person=None):
        result = await self.send("member_settings", person=person)
        self.assertEqual(result["status"], "ok", result)
        return result["data"]["sections"]

    async def configure(self, changes, *, person=None, key="member-save", snapshots=None, resets=None):
        person = person or self.person()
        sections = snapshots or await self.settings(person)
        resets = resets or {}
        names = set(changes).union(resets)
        return await self.send("configure_member", command={**person, "operation_key": key,
            "sections": {section: {"edit_token": sections[section]["edit_token"],
                                    "changes": changes.get(section, {}),
                                    **({"reset_to_inherit": resets[section]} if section in resets else {})}
                         for section in names}})

    async def test_member_settings_read_failure_is_isolated_to_its_section(self):
        await self.ready()

        class BrokenGrowth:
            def __init__(self, app, context):
                pass

            def status(self, operator):
                raise RuntimeError("simulated corrupt growth configuration")

        with patch.dict("companion_v2.relationships.private_management.EDITORS", {"growth": BrokenGrowth}):
            with self.assertLogs("companion_v2.relationships.private_management", level="ERROR") as captured:
                result = await self.send("member_settings")

        self.assertEqual(result["status"], "ok", result)
        self.assertIn("reply", result["data"]["sections"])
        self.assertNotIn("growth", result["data"]["sections"])
        self.assertEqual(result["data"]["section_errors"]["growth"]["code"],
                         "member_settings_section_failed")
        self.assertTrue(any("section=growth" in line for line in captured.output))

    async def test_profile_is_the_prompt_source_and_accounts_are_isolated(self):
        app = await self.ready()
        written = await self.configure({"profile": {"default_nickname": "小舟", "role_relation": "搭档",
                                                    "interaction": "简洁自然，称呼小舟"}})
        self.assertEqual(written["status"], "ok", written)
        first = self.controller.authority.context("first", "role-a")
        second = self.controller.authority.context("second", "role-a")
        reader = app.messages.relationship_profile_context
        snapshot = reader.capture(first, self.clock.now())
        self.assertIn("对用户的称呼：小舟", reader.instruction(snapshot, first))
        self.assertIsNone(reader.capture(second, self.clock.now()))
        self.assertEqual((await self.settings(self.person("second")))["profile"]["settings"]["default_nickname"], "")

    async def test_contact_and_card_settings_use_existing_consumers(self):
        app = await self.ready()
        result = await self.configure({"contact": {"enabled": True, "max_contacts_24h": 5, "quiet_enabled": True},
                                       "person_learning": {"acquaintance_learning_enabled": False}})
        self.assertEqual(result["status"], "ok", result)
        context = self.controller.authority.context("first", "role-a")
        self.assertIsNone(app.messages.acquaintance.intake_observer(context))
        self.assertIsNotNone(app.messages.acquaintance.card_contribution(context, self.clock.now()))
        self.assertEqual((await self.settings())["contact"]["settings"]["max_contacts_24h"], 5)
        count = json.loads(app.messages.acquaintance.row(owner_key(context))["document_json"])["contexts"]
        await self.controller.receive_claimed(self.event("first", "without-learning"))
        self.assertEqual(json.loads(app.messages.acquaintance.row(owner_key(context))["document_json"])["contexts"], count)
        self.assertEqual((await self.configure({"person": {"acquaintance_enabled": False}}, key="disable-card"))["status"], "ok")
        self.assertIsNone(app.messages.acquaintance.card_contribution(context, self.clock.now()))
        self.assertEqual(app.messages.acquaintance.recall(context)["state"], "disabled")

    async def test_acquaintance_defaults_are_role_owned_and_member_overrides_and_learning_stay_private(self):
        config = multi_config()
        config["multi_host"]["targets"].append({"target_id": "third", "platform_id": "qq-main",
            "account_id": "100", "bot_id": "bot-a", "participant_id": "201"})
        self.controller = self.make_controller(config)
        app = await self.ready()
        await self.controller.receive_claimed(self.event("third", "third-input"))
        context = self.controller.authority.context("first", "role-a")
        operator = dashboard_operator(app.runtime, "member-admin", at=self.clock.now())
        editor = AcquaintanceManagement(app, context)
        role = editor.status(operator).unwrap()
        self.assertEqual(role["scope"], "role")
        self.assertEqual(role["fields"]["acquaintance_enabled"]["editable_layers"], ["role"])
        saved = editor.configure(operator, {
            "operation_key": "acquaintance-role-default", "edit_token": role["edit_token"],
            "changes": {"acquaintance_enabled": False, "acquaintance_card_chars": 1400}})
        self.assertTrue(saved.is_success, saved.failure)

        first = self.person("first")
        member = await self.configure({
            "person": {"acquaintance_enabled": True, "acquaintance_card_chars": 1600},
            "person_learning": {"acquaintance_learning_enabled": False},
        }, person=first, key="first-person-card")
        self.assertEqual(member["status"], "ok", member)

        first_context = self.controller.authority.context("first", "role-a")
        third_context = self.controller.authority.context("third", "role-a")
        first_settings = app.messages.acquaintance.settings.read(first_context)
        self.assertEqual(first_settings["person.acquaintance_card_chars"], 1600)
        self.assertTrue(first_settings["person.acquaintance_enabled"])
        self.assertFalse(first_settings["person.learning.acquaintance_learning_enabled"])
        third_settings = app.messages.acquaintance.settings.read(third_context)
        self.assertFalse(third_settings["person.acquaintance_enabled"])
        self.assertEqual(third_settings["person.acquaintance_card_chars"], 1400)
        self.assertTrue(third_settings["person.learning.acquaintance_learning_enabled"])

        sections = await self.settings(first)
        self.assertEqual(sections["person"]["scope"], "relationship")
        self.assertEqual(sections["person"]["layers"], ["installation", "role", "relationship"])
        self.assertEqual(sections["person"]["inherited_settings"]["acquaintance_card_chars"], 1400)
        reset = await self.configure({}, person=first, key="first-person-card-reset", snapshots=sections,
            resets={"person": ["acquaintance_enabled", "acquaintance_card_chars"]})
        self.assertEqual(reset["status"], "ok", reset)
        reset_settings = app.messages.acquaintance.settings.read(first_context)
        self.assertFalse(reset_settings["person.acquaintance_enabled"])
        self.assertEqual(reset_settings["person.acquaintance_card_chars"], 1400)

    async def test_legacy_window_acquaintance_overrides_remain_private_and_resettable(self):
        app = await self.ready()
        from companion_v2.configuration.owners import SparseConfigurationOwner
        from companion_v2.memory.person.configuration import legacy_window_fields, legacy_window_target_for
        context = self.controller.authority.context("first", "role-a")
        owner = SparseConfigurationOwner(app.runtime)
        fields = legacy_window_fields()
        target = legacy_window_target_for(context)
        owner.write(target, fields, changes={
            "person.acquaintance_enabled": False,
            "person.acquaintance_learning_enabled": False,
        }, reset=(), expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())

        first = self.person("first")
        status = await self.settings(first)
        self.assertFalse(status["person"]["settings"]["acquaintance_enabled"])
        self.assertTrue(status["person"]["provenance"]["acquaintance_enabled"]["legacy"])
        self.assertFalse(status["person_learning"]["settings"]["acquaintance_learning_enabled"])
        reset = await self.configure({}, person=first, key="reset-legacy-person", snapshots=status,
            resets={"person": ["acquaintance_enabled"],
                    "person_learning": ["acquaintance_learning_enabled"]})
        self.assertEqual(reset["status"], "ok", reset)
        current = app.messages.acquaintance.settings.read(context)
        self.assertTrue(current["person.acquaintance_enabled"])
        self.assertTrue(current["person.learning.acquaintance_learning_enabled"])

    async def test_growth_grants_and_expression_learning_exceptions_are_saved_per_private_person(self):
        app = await self.ready()
        first_person, second_person = self.person("first"), self.person("second")
        first_before = await self.settings(first_person)
        second_before = await self.settings(second_person)
        self.assertEqual(first_before["growth"]["scope"], "window")
        self.assertEqual(first_before["expression"]["scope"], "relationship")
        saved = await self.configure({
            "growth": {"read_enabled": True, "proposals_enabled": True},
            "expression": {"private_learning_enabled": False},
        }, person=first_person, key="first-person-learning")
        self.assertEqual(saved["status"], "ok", saved)

        first_context = self.controller.authority.context("first", "role-a")
        second_context = self.controller.authority.context("second", "role-a")
        for action in ("role.growth.read", "role.growth.propose"):
            self.assertTrue(app.tasks.check_context_access(first_context, action=action,
                observed_at=self.clock.now()).is_success, action)
            self.assertFalse(app.tasks.check_context_access(second_context, action=action,
                observed_at=self.clock.now()).is_success, action)
        self.assertFalse(expression_settings(app.runtime, first_context).private_learning_enabled)
        self.assertTrue(expression_settings(app.runtime, second_context).private_learning_enabled)
        self.assertEqual((await self.settings(second_person))["growth"]["settings"],
                         second_before["growth"]["settings"])

    async def test_private_contact_overrides_are_window_scoped_and_global_defaults_stay_global(self):
        config = multi_config()
        config["multi_host"]["targets"].append({"target_id": "third", "platform_id": "qq-main",
            "account_id": "100", "bot_id": "bot-a", "participant_id": "201"})
        self.controller = self.make_controller(config)
        app = await self.ready()
        context = self.controller.authority.context("first", "role-a")
        operator = dashboard_operator(app.runtime, "member-admin", at=self.clock.now())
        public = ProactiveContactManagement(app, context)
        public.configure(operator, {"operation_key": "shared-contact-defaults",
            "edit_token": public.status(operator).unwrap()["edit_token"],
            "changes": {"enabled": True, "automatic_enabled": True, "interval_seconds": 600,
                        "max_contacts_24h": 3}}).unwrap()
        inherited = (await self.settings())["contact"]
        self.assertEqual(inherited["scope"], "window")
        self.assertIn("schedule_diagnostic", inherited)
        self.assertIn("evaluation", inherited["schedule_diagnostic"])
        self.assertFalse(inherited["provenance"]["enabled"]["explicit"])
        self.assertEqual(inherited["provenance"]["enabled"]["layer"], "installation")
        saved = await self.configure({"contact": {"enabled": False, "interval_seconds": 1200,
            "max_contacts_24h": 5, "unanswered_slowdown_start": 2,
            "unanswered_pause_after": 5, "unanswered_max_interval_multiplier": 3.5,
            "note": "尽量避开工作时间联系", "news.enabled": False, "news.weight": 1.5,
            "news.pause_until": "2026-10-21T18:00:00+00:00"}},
            key="individual-contact")
        self.assertEqual(saved["status"], "ok", saved)
        self.assertTrue(saved["data"]["results"]["contact"]["operation_id"].startswith(
            "proactive.contact-window-config:"))
        personal = (await self.settings())["contact"]
        self.assertTrue(personal["provenance"]["enabled"]["explicit"])
        self.assertEqual(personal["provenance"]["enabled"]["layer"], "window")
        self.assertEqual(personal["settings"]["unanswered_slowdown_start"], 2)
        self.assertEqual(personal["settings"]["unanswered_pause_after"], 5)
        self.assertEqual(personal["settings"]["unanswered_max_interval_multiplier"], 3.5)
        self.assertEqual(personal["settings"]["note"], "尽量避开工作时间联系")
        self.assertFalse(personal["settings"]["news.enabled"])
        self.assertEqual(personal["settings"]["news.weight"], 1.5)
        self.assertEqual(personal["settings"]["news.pause_until"], "2026-10-21T18:00:00+00:00")
        self.assertFalse(ContactManagement(app, context).status(operator).unwrap()["settings"]["enabled"])
        for person in (self.person("third"), self.person("second"),
                       {**self.person(), "route": {**self.person()["route"], "role_id": "role-b"}}):
            other = (await self.settings(person))["contact"]["settings"]
            self.assertTrue(other["enabled"])
            self.assertEqual(other["interval_seconds"], 600)
        public.configure(operator, {"operation_key": "shared-contact-new-interval",
            "edit_token": public.status(operator).unwrap()["edit_token"],
            "changes": {"interval_seconds": 900}}).unwrap()
        self.assertEqual((await self.settings())["contact"]["settings"]["interval_seconds"], 1200)
        self.assertEqual((await self.settings(self.person("third")))["contact"]["settings"]["interval_seconds"], 900)
        await self.controller.terminate()
        self.controller = self.make_controller(config)
        (await self.controller.load_existing()).unwrap()
        (await self.controller.start()).unwrap()
        self.api = AcquaintanceWebAPI(self.controller)
        after = (await self.settings())["contact"]
        self.assertEqual(after["settings"], personal["settings"])
        self.assertTrue(after["provenance"]["enabled"]["explicit"])
        self.assertEqual(after["provenance"]["enabled"]["layer"], "window")
        reset = await self.configure({"contact": {}}, key="reset-personal-contact",
            resets={"contact": ["unanswered_slowdown_start", "unanswered_pause_after",
                                 "unanswered_max_interval_multiplier", "note", "news.enabled", "news.weight",
                                 "news.pause_until"]})
        self.assertEqual(reset["status"], "ok", reset)
        restored = (await self.settings())["contact"]
        self.assertEqual(restored["settings"]["unanswered_slowdown_start"], 1)
        self.assertEqual(restored["settings"]["unanswered_pause_after"], 0)
        self.assertEqual(restored["settings"]["unanswered_max_interval_multiplier"], 2.2)
        self.assertEqual(restored["settings"]["note"], "")
        self.assertTrue(restored["settings"]["news.enabled"])
        self.assertEqual(restored["settings"]["news.weight"], 1.0)
        self.assertEqual(restored["settings"]["news.pause_until"], "")
        self.assertFalse(restored["provenance"]["unanswered_pause_after"]["explicit"])
        self.assertFalse(restored["provenance"]["news.enabled"]["explicit"])

        await self.controller.terminate()
        self.native.conversations["qq-main"] = "conversation-after-restart"
        self.controller = self.make_controller(config)
        (await self.controller.load_existing()).unwrap()
        (await self.controller.start()).unwrap()
        self.api = AcquaintanceWebAPI(self.controller)
        new_window = (await self.settings())["contact"]
        self.assertEqual(new_window["settings"]["enabled"], True)
        self.assertEqual(new_window["settings"]["interval_seconds"], 900)
        self.assertEqual(new_window["settings"]["max_contacts_24h"], 3)
        self.assertEqual(new_window["settings"]["note"], "")
        self.assertEqual(new_window["provenance"]["enabled"]["layer"], "installation")
        public = ProactiveContactManagement(self.controller.session.application,
                                           self.controller.authority.context("third", "role-b"))
        global_settings = public.status(dashboard_operator(self.controller.runtime, "member-admin",
                                                          at=self.clock.now())).unwrap()["settings"]
        self.assertTrue(global_settings["enabled"])
        self.assertEqual(global_settings["interval_seconds"], 900)

    async def test_private_reply_settings_inherit_role_defaults_override_one_window_and_reset(self):
        config = multi_config()
        config["multi_host"]["targets"].append({"target_id": "third", "platform_id": "qq-main",
            "account_id": "100", "bot_id": "bot-a", "participant_id": "201"})
        self.controller = self.make_controller(config)
        app = await self.ready()
        await self.controller.receive_claimed(self.event("third", "third-input"))
        context = self.controller.authority.context("first", "role-a")
        operator = dashboard_operator(app.runtime, "member-admin", at=self.clock.now())
        role_reply = ReplyManagement(app, context, layer=ConfigLayer.ROLE)
        role_status = role_reply.status(operator).unwrap()
        saved_role = role_reply.configure(operator, {"operation_key": "role-reply-default",
            "edit_token": role_status["edit_token"], "changes": {"style_prompt": "保持角色一贯的轻松语气"}})
        self.assertTrue(saved_role.is_success, saved_role.failure)

        first = await self.settings(self.person("first"))
        reply = first["reply"]
        self.assertEqual(reply["scope"], "window")
        self.assertEqual(reply["settings"]["style_prompt"], "保持角色一贯的轻松语气")
        self.assertEqual(reply["inherited_settings"]["style_prompt"], "保持角色一贯的轻松语气")
        self.assertFalse(reply["provenance"]["style_prompt"]["explicit"])

        saved_window = await self.configure({"reply": {"style_prompt": "对这位用户多用简短回应"}},
                                            person=self.person("first"), key="private-reply-override")
        self.assertEqual(saved_window["status"], "ok", saved_window)
        first_reply = saved_window["data"]["sections"]["reply"]
        self.assertEqual(first_reply["settings"]["style_prompt"], "对这位用户多用简短回应")
        self.assertEqual(first_reply["inherited_settings"]["style_prompt"], "保持角色一贯的轻松语气")
        self.assertTrue(first_reply["provenance"]["style_prompt"]["explicit"])
        self.assertIn("video_understanding_prompt", first_reply["editable_fields"])
        self.assertIn("file_understanding_format_groups", first_reply["editable_fields"])
        self.assertEqual((await self.settings(self.person("third")))["reply"]["settings"]["style_prompt"],
                         "保持角色一贯的轻松语气")
        self.assertEqual((await self.settings(self.person("second")))["reply"]["settings"]["style_prompt"], "")

        reset = await self.send("configure_member", person=self.person("first"), command={
            **self.person("first"), "operation_key": "reset-private-reply",
            "sections": {"reply": {"edit_token": first_reply["edit_token"], "changes": {},
                                    "reset_to_inherit": ["style_prompt"]}},
        })
        self.assertEqual(reset["status"], "ok", reset)
        restored = reset["data"]["sections"]["reply"]
        self.assertEqual(restored["settings"]["style_prompt"], "保持角色一贯的轻松语气")
        self.assertFalse(restored["provenance"]["style_prompt"]["explicit"])

    async def test_legacy_relationship_preferences_remain_compatible_but_contact_quota_is_window_scoped(self):
        config = multi_config()
        config["multi_host"]["targets"].append({"target_id": "third", "platform_id": "qq-main",
            "account_id": "100", "bot_id": "bot-a", "participant_id": "201"})
        self.controller = self.make_controller(config)
        app = await self.ready()
        context = self.controller.authority.context("first", "role-a")
        operator = dashboard_operator(app.runtime, "member-admin", at=self.clock.now())

        class OldRelationshipContact(ProactiveContactManagement):
            target_layer = ConfigLayer.RELATIONSHIP

            def _target(self):
                return relationship_target_for(self.context)

        changes = {"contact": {
            "enabled": False, "automatic_enabled": False,
            "note": "这一位用户的单独偏好", "pause_until": (self.clock.now() + timedelta(hours=1)).isoformat(),
            "important_dates": '[{"calendar":"gregorian","month":3,"day":8,"label":"个人日期"}]',
        }}
        with patch.dict("companion_v2.relationships.private_management.EDITORS", {"contact": OldRelationshipContact}):
            original = await self.settings()
            saved = await self.configure(changes, key="old-relationship-contact", snapshots=original)
        self.assertEqual(saved["status"], "ok", saved)
        legacy_operation = saved["data"]["results"]["contact"]["operation_id"]
        count = app.runtime.read_one("SELECT COUNT(*) AS n FROM configuration_changes", ())["n"]
        replay = await self.configure(changes, key="old-relationship-contact", snapshots=original)
        self.assertEqual(replay["status"], "ok", replay)
        self.assertTrue(replay["data"]["results"]["contact"]["replayed"])
        self.assertEqual(replay["data"]["results"]["contact"]["operation_id"], legacy_operation)
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) AS n FROM configuration_changes", ())["n"], count)
        with patch.dict("companion_v2.relationships.private_management.EDITORS", {"contact": OldRelationshipContact}):
            quota_write = await self.configure({"contact": {"max_contacts_24h": 5}},
                                                key="relationship-contact-quota", snapshots=await self.settings())
        self.assertEqual(quota_write["status"], "error", quota_write)
        self.assertEqual(quota_write["data"]["code"], "configuration_scope_not_supported")
        public = ProactiveContactManagement(app, context)
        public_before = public.status(operator).unwrap()
        self.assertIsNone(public_before["provenance"]["enabled"]["layer"])
        public.configure(operator, {"operation_key": "global-after-old-personal-save",
            "edit_token": public_before["edit_token"], "changes": {
                "enabled": True, "automatic_enabled": True, "max_contacts_24h": 3,
            }}).unwrap()
        before = (await self.settings())["contact"]
        self.assertEqual(before["scope"], "window")
        self.assertFalse(before["settings"]["enabled"])
        self.assertEqual(before["settings"]["note"], "这一位用户的单独偏好")
        self.assertEqual(before["settings"]["max_contacts_24h"], 3)
        self.assertEqual(before["provenance"]["enabled"]["layer"], "relationship")
        self.assertFalse(before["provenance"]["enabled"]["explicit"])
        for person in (self.person("second"), self.person("third"),
                       {**self.person(), "route": {**self.person()["route"], "role_id": "role-b"}}):
            other = (await self.settings(person))["contact"]["settings"]
            self.assertTrue(other["enabled"])
            self.assertEqual(other["note"], "")
            self.assertEqual(other["important_dates"], "[]")
            self.assertEqual(other["pause_until"], "")
        changed = await self.configure({"contact": {"enabled": True, "pause_until": ""}},
                                       key="stable-personal-override")
        self.assertEqual(changed["status"], "ok", changed)
        changed_status = (await self.settings())["contact"]
        self.assertTrue(changed_status["provenance"]["enabled"]["explicit"])
        self.assertEqual(changed_status["provenance"]["enabled"]["layer"], "window")
        actual = ContactManagement(app, context).status(operator).unwrap()["settings"]
        self.assertTrue(actual["enabled"])
        self.assertEqual(actual["pause_until"], before["settings"]["pause_until"])

        await self.controller.terminate()
        self.native.conversations["qq-main"] = "old-personal-new-conversation"
        self.controller = self.make_controller(config)
        (await self.controller.load_existing()).unwrap()
        (await self.controller.start()).unwrap()
        self.api = AcquaintanceWebAPI(self.controller)
        after = (await self.settings())["contact"]["settings"]
        self.assertFalse(after["enabled"])
        self.assertEqual(after["note"], before["settings"]["note"])
        self.assertEqual(after["important_dates"], before["settings"]["important_dates"])
        self.assertEqual(after["max_contacts_24h"], 3)
        self.assertEqual(after["pause_until"], before["settings"]["pause_until"])
        original = self.controller.session.application.configuration.store.read_one(
            "SELECT result_json FROM configuration_changes WHERE operation_id = ?", (legacy_operation,))
        self.assertIsNotNone(original)

    async def test_shared_owner_groups_save_and_replay_as_one_operation(self):
        await self.ready()
        original = await self.settings()
        changes = {"contact": {"max_contacts_24h": 5}, "person_learning": {"acquaintance_learning_enabled": False},
                   "profile": {"default_nickname": "一起保存"}}
        first = await self.configure(changes, snapshots=original)
        self.assertEqual(first["status"], "ok", first)
        replay = await self.configure(changes, snapshots=original)
        self.assertEqual(replay["status"], "ok", replay)
        for name, result in first["data"]["results"].items():
            self.assertTrue(replay["data"]["results"][name]["replayed"])
            self.assertEqual(replay["data"]["results"][name]["operation_id"], result["operation_id"])
        different = await self.configure({**changes, "person_learning": {"acquaintance_learning_enabled": True}}, snapshots=original)
        self.assertEqual(different["data"]["code"], "idempotency_key_conflict")

    async def test_invalid_last_group_rolls_back_saved_groups_and_the_receipt(self):
        app = await self.ready()
        original = (await self.settings())["contact"]["settings"]["max_contacts_24h"]
        result = await self.configure({"contact": {"max_contacts_24h": 5},
                                       "person": {"acquaintance_card_chars": 1}})
        self.assertEqual(result["status"], "error", result)
        self.assertEqual((await self.settings())["contact"]["settings"]["max_contacts_24h"], original)
        self.assertIsNone(app.runtime.read_one("SELECT 1 FROM operations WHERE operation_id LIKE 'private-member-settings:%'", ()))

    async def test_configured_members_are_selectable_before_cards_exist(self):
        await self.start_host()
        self.api = AcquaintanceWebAPI(self.controller)
        with patch("astrbot_host.acquaintance_api.request", NS(username="member-admin", query=_Query(), body=_body(b""))):
            result = await self.api.directory()
        self.assertEqual(result["status"], "ok", result)
        persons = result["data"]["persons"]
        self.assertEqual(len(persons), 4)
        self.assertTrue(all(person["private_target"] and not person["private_chat"] for person in persons))
        self.assertEqual(result["data"]["counts"]["private_chat"], 0)
        self.assertEqual(result["data"]["counts"]["total"], 0)
        self.assertTrue(all(person["revision"] == 0 for person in persons))
        self.assertTrue(all(person["settings_available"] for person in persons), persons)
        for person in persons:
            sections = await self.settings({"subject_id": person["subject_id"],
                "route": {key: person[key] for key in ("platform", "bot_id", "account_id", "role_id")}})
            self.assertEqual(sections["profile"]["settings"]["default_nickname"], "")

    async def test_automatic_private_routes_expose_registered_people_and_their_settings(self):
        await self.start_host()
        self.api = AcquaintanceWebAPI(self.controller)
        self.controller.settings = replace(self.controller.settings, targets=tuple(
            replace(target, participant_id="*") for target in self.controller.settings.targets))
        self.controller.authority.settings = self.controller.settings
        with patch("astrbot_host.acquaintance_api.request", NS(username="member-admin", query=_Query(), body=_body(b""))):
            result = await self.api.directory()
        self.assertEqual(result["status"], "ok", result)
        persons = result["data"]["persons"]
        self.assertEqual(len(persons), 4)
        self.assertTrue(all(person["native_account_id"] == "200" and person["settings_available"] for person in persons))
        self.assertEqual((await self.settings())["profile"]["settings"]["default_nickname"], "")

    async def test_contact_configuration_cannot_restore_a_page_revocation(self):
        app = await self.ready()
        person = self.person()
        auth = (await self.send("authorization"))["data"]
        result = await self.send("authorize", command={**person, "operation_key": "contact-off",
            "capability": "proactive.contact", "enabled": False, "expected_revision": auth["policy_revision"]})
        self.assertEqual(result["status"], "ok", result)
        context = self.controller.authority.context("first", "role-a")
        manager = ContactManagement(app, context)
        operator = dashboard_operator(app.runtime, "member-admin", at=self.clock.now())
        before = manager.status(operator).unwrap()
        saved = manager.configure(operator, {"operation_key": "plan-on", "edit_token": before["edit_token"],
                                            "changes": {"enabled": True}})
        self.assertTrue(saved.is_success, saved.failure)
        current = (await self.send("authorization"))["data"]
        self.assertEqual(next(row for row in current["items"] if row["capability"] == "proactive.contact")["state"], "revoked")

    async def test_multisection_conflict_rolls_every_section_back(self):
        await self.ready()
        stale = await self.settings()
        self.assertEqual((await self.configure({"contact": {"max_contacts_24h": 7}}, key="other-edit"))["status"], "ok")
        result = await self.configure({"profile": {"default_nickname": "不能写入"},
                                       "contact": {"max_contacts_24h": 9}}, snapshots=stale)
        self.assertEqual(result["data"]["code"], "configuration_revision_conflict")
        current = await self.settings()
        self.assertEqual(current["profile"]["settings"]["default_nickname"], "")
        self.assertEqual(current["contact"]["settings"]["max_contacts_24h"], 7)

    async def test_settings_replay_returns_the_same_receipt(self):
        await self.ready()
        original = await self.settings()
        first = await self.configure({"profile": {"default_nickname": "小舟"}}, snapshots=original)
        replay = await self.configure({"profile": {"default_nickname": "小舟"}}, snapshots=original)
        self.assertEqual(first["status"], "ok", first)
        self.assertTrue(replay["data"]["results"]["profile"]["replayed"])
        changed = await self.configure({"profile": {"default_nickname": "不同称呼"}}, snapshots=original)
        self.assertEqual(changed["data"]["code"], "idempotency_key_conflict")

    async def test_identity_is_live_audited_and_overrides_are_explicit(self):
        app = await self.ready()
        auth = (await self.send("authorization"))["data"]
        result = await self.send("set_identity", command={**self.person(), "operation_key": "member-identity",
            "tier": "important", "expected_revision": auth["identity_revision"]})
        self.assertEqual(result["status"], "ok", result)
        state = result["data"]["status"]
        self.assertEqual(state["identity_tier"], "important")
        self.assertTrue(all(row["identity_override"] for row in state["items"]
                            if row["capability"] in {"proactive.contact", "conversation.reply"}))
        context = self.controller.authority.context("first", "role-a")
        decision = ObjectAccessPolicy(app.runtime).feature_access(context.actor, scope=context.scope,
            action="proactive.contact", observed_at=self.clock.now()).unwrap()
        self.assertEqual(decision.tier, AuthorizationTier.IMPORTANT)
        self.assertTrue(app.runtime.read_one("SELECT 1 FROM operations WHERE operation_id LIKE 'person-identity:%'", ()))
        stale = await self.send("set_identity", command={**self.person(), "operation_key": "stale-identity",
            "tier": "standard", "expected_revision": auth["identity_revision"]})
        self.assertEqual(stale["data"]["code"], "identity_revision_conflict")

    async def test_forged_or_ambiguous_routes_do_not_write(self):
        await self.ready()
        result = await self.send("configure_member", command={**self.person(), "operation_key": "bad-route",
            "route": {**self.person()["route"], "bot_id": "unconfigured"}, "sections": {}})
        self.assertEqual(result["data"]["code"], "acquaintance_route_unknown")
        with patch("astrbot_host.acquaintance_api.request", NS(username="member-admin",
                query=_Query("subject_id=" + self.person()["subject_id"]), body=_body(b""))):
            self.assertEqual((await self.api.authorization())["data"]["code"], "acquaintance_route_required")

    async def test_admin_gate_refuses_api_keys_and_noncore_operators(self):
        app = await self.ready()
        denied = await self.send("member_settings", username="api_key:synthetic")
        self.assertEqual(denied["data"]["code"], "dashboard_identity_required")
        operator = dashboard_operator(app.runtime, "limited", at=self.clock.now())
        policy = ObjectAccessPolicy(app.runtime)
        policy.set_tier(operator.subject, AuthorizationTier.IMPORTANT,
                        expected_revision=policy.revision, observed_at=self.clock.now())
        denied = await self.send("set_identity", username="limited", command={})
        self.assertEqual(denied["data"]["code"], "core_access_required")

    async def test_dialogue_off_on_and_settings_survive_host_reopen(self):
        await self.ready()
        person = self.person()
        for enabled in (False, True, False):
            auth = (await self.send("authorization"))["data"]
            result = await self.send("authorize", command={**person, "operation_key": "switch-" + str(auth["policy_revision"]),
                "capability": "conversation.reply", "enabled": enabled, "expected_revision": auth["policy_revision"]})
            self.assertEqual(result["status"], "ok", result)
            self.controller.authority.context("first", "role-a")
        await self.configure({"profile": {"default_nickname": "重载后的称呼"}}, key="before-reopen")
        await self.controller.terminate()
        self.controller = self.make_controller()
        (await self.controller.load_existing()).unwrap()
        self.api = AcquaintanceWebAPI(self.controller)
        auth = (await self.send("authorization", person=person))["data"]
        self.assertEqual(next(row for row in auth["items"] if row["capability"] == "conversation.reply")["state"], "revoked")
        self.assertEqual((await self.settings(person))["profile"]["settings"]["default_nickname"], "重载后的称呼")

    async def test_reenabled_permission_and_identity_survive_reopen(self):
        await self.ready()
        person = self.person()
        for enabled in (False, True):
            auth = (await self.send("authorization"))["data"]
            result = await self.send("authorize", command={**person, "operation_key": "reopen-" + str(enabled),
                "capability": "conversation.reply", "enabled": enabled, "expected_revision": auth["policy_revision"]})
            self.assertEqual(result["status"], "ok", result)
        await self.controller.terminate()
        self.controller = self.make_controller()
        (await self.controller.load_existing()).unwrap()
        self.api = AcquaintanceWebAPI(self.controller)
        auth = (await self.send("authorization", person=person))["data"]
        self.assertEqual(next(row for row in auth["items"] if row["capability"] == "conversation.reply")["state"], "active")
        result = await self.send("set_identity", command={**person, "operation_key": "important-reopen",
            "tier": "important", "expected_revision": auth["identity_revision"]})
        self.assertEqual(result["status"], "ok", result)
        await self.controller.terminate()
        self.controller = self.make_controller()
        (await self.controller.load_existing()).unwrap()
        self.api = AcquaintanceWebAPI(self.controller)
        self.assertEqual((await self.send("authorization", person=person))["data"]["identity_tier"], "important")

    async def test_open_loop_detail_reads_and_mutates_only_the_selected_person_and_role(self):
        await self.ready()
        person = self.person()
        context = self.controller.authority.context("first", "role-a")
        target = AgendaTarget(context.scope, context.role_binding.role, context.actor.subject)
        operator = dashboard_operator(self.controller.runtime, "member-admin", at=self.clock.now())
        agenda = AgendaService(self.controller.runtime)
        created = agenda.create_memo(operator, target=target, memo_id="profile-open-loop",
            title="等面试结果", body="用户说下周收到通知后继续聊",
            operation=OperationRef("open-loop-create", "agenda", context.scope,
                                   VersionRef("open-loop-create", 1), "open-loop-create"),
            expected_role_revision=context.role_binding.revision, observed_at=self.clock.now(),
            purpose="open_loop").unwrap()

        listed = await self.send("open_loops", person=person)
        self.assertEqual(listed["status"], "ok", listed)
        item = next(value for value in listed["data"]["active"] if value["memo_id"] == created.object_id)
        completed = await self.send("change_open_loop", person=person, command={**person,
            "memo_id": created.object_id, "expected_revision": item["revision"], "action": "complete",
            "operation_key": "profile-open-loop-complete"})
        self.assertEqual(completed["status"], "ok", completed)
        listed = await self.send("open_loops", person=person)
        self.assertEqual([value["memo_id"] for value in listed["data"]["completed"]], [created.object_id])


if __name__ == "__main__":
    unittest.main()
