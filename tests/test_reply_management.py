from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

from companion_v2.conversation.reply_management import ReplyManagement
from companion_v2.conversation.reply_settings import (FORWARD_DEFAULTS, GROUP, GROUP_AIR_DEFAULTS,
    OWNER, ROLE_PROMPT_FIELDS, validate as validate_reply_settings)
from companion_v2.conversation.segmentation import LLM_SEGMENT_MARKER
from companion_v2.conversation.text_processing import TEXT_DEFAULTS
from companion_v2.model_gateway.response_diagnostics import ResponseDiagnostics, ResponseTextSummary
from companion_v2.execution.models import encode
from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.foundation.refs import OperationRef, ScopeKind, VersionRef
from companion_v2.conversation.media.understanding import SCOPE_PERMISSION_FIELDS, media_scope_enabled
from tests.test_host_application import HostFixture
from tests.test_role_tool_loop import answered


class ReplyManagementTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.host = await self.start()
        self.app = self.host.application
        self.first_batch = self.host.receive(self.event("reply-settings-first")).unwrap()
        self.advance(self.app)
        await self.done(self.app, self.first_batch)
        self.context = self.context(self.app, self.first_batch)
        self.manager = ReplyManagement(self.app, self.context)

    def request(self, status, changes, key="reply-settings"):
        return {"operation_key": key, "edit_token": status["edit_token"], "changes": changes}

    async def test_status_diagnostics_and_preview_are_local_and_consistent(self):
        status = self.manager.status(self.core).unwrap()
        recent = status["recent"][0]
        self.assertEqual(recent["batch_id"], self.first_batch)
        self.assertEqual(recent["calls"][0]["state"], "generated")
        self.assertEqual(recent["turn_type"], "conversation.reply")
        self.assertEqual(recent["current_inputs"], 1)
        self.assertEqual(status["recent_limit"], 80)
        self.assertIsNone(recent["calls"][0]["response_diagnostics"])
        self.assertEqual(recent["parts"][0]["state"], "delivered")
        self.assertEqual(recent["parts"][0]["candidate_reason"], "voice_disabled")
        self.assertEqual(recent["parts"][0]["component_order"], [])
        self.assertEqual(recent["response_type"], "text_or_mixed")
        self.assertEqual(recent["group_history_items"], 0)
        self.assertEqual(recent["injected_materials"], [])
        self.assertNotIn("captured", recent)
        self.assertNotIn("自定义提示词", json.dumps(recent, ensure_ascii=False))
        self.assertIsInstance(recent["offered_tools"], list)
        self.assertIsInstance(recent["filtered_tools"], list)
        self.assertIsInstance(recent["rounds"], list)
        self.assertIsInstance(recent["tool_calls"], list)

        with self.app.runtime.transaction() as connection:
            connection.execute("DELETE FROM conversation_voice_diagnostics WHERE part_id IN "
                               "(SELECT part_id FROM conversation_parts WHERE batch_id = ?)", (self.first_batch,))
        old = self.manager.status(self.core).unwrap()["recent"][0]["parts"][0]
        self.assertIsNone(old["candidate_reason"])
        self.assertIsNone(old["component_order"])

        settings = dict(status["settings"])
        settings.update(enabled=True, llm_controlled=True, rules_enabled=False)
        text = "甲\n" + LLM_SEGMENT_MARKER + "\n乙"
        before = len(self.called)
        preview = self.manager.preview(self.core, settings, text).unwrap()
        self.assertEqual(preview["parts"], ("甲", "乙"))
        self.assertEqual(preview["mode"], "offline_preview")
        self.assertEqual(preview["image_position"], "after_text")
        self.assertEqual(len(self.called), before)

    async def test_reply_diagnostics_limit_and_response_summary_apply_to_the_current_window(self):
        diagnostic = ResponseDiagnostics(True, "stop", "validated",
            raw=ResponseTextSummary.from_text('{"text":"private model body"}'),
            visible=ResponseTextSummary.from_text("private model body"))
        call_id = self.app.runtime.read_one("SELECT call_id FROM model_calls ORDER BY rowid LIMIT 1", ())["call_id"]
        with self.app.runtime.transaction() as connection:
            connection.execute("INSERT INTO model_call_diagnostics VALUES (?, ?, ?)",
                               (call_id, encode(diagnostic.to_record()), self.app.now().isoformat()))
            for index in range(12):
                connection.execute("""INSERT INTO conversation_batches(batch_id, origin_key, context_json,
                    role_instruction, state, revision, created_at, contributions_json)
                    SELECT ?, origin_key, context_json, role_instruction, 'sealed', 1, created_at, '{}'
                    FROM conversation_batches WHERE batch_id = ?""", (f"diagnostic-batch-{index}", self.first_batch))
        status = self.manager.status(self.core).unwrap()
        self.assertEqual(len(status["recent"]), 13)
        original = next(item for item in status["recent"] if item["batch_id"] == self.first_batch)
        self.assertEqual(original["calls"][0]["response_diagnostics"], diagnostic.to_record())
        self.assertNotIn("private model body", json.dumps(status["recent"]))
        saved = self.manager.configure(self.core, self.request(status, {"reply_diagnostic_limit": 10}, "diagnostic-limit")).unwrap()
        self.assertEqual(saved["status"]["recent_limit"], 10)
        self.assertEqual(len(saved["status"]["recent"]), 10)

    async def test_configuration_is_cas_idempotent_and_captured_by_next_batch(self):
        before = self.manager.status(self.core).unwrap()
        changes = {"enabled": True, "rules_enabled": False, "threshold": 8, "max_segments": 3,
                   "image_position": "before_text"}
        first = self.manager.configure(self.core, self.request(before, changes, "enable-reply")).unwrap()
        self.assertFalse(first["replayed"])
        self.assertNotEqual(first["status"]["edit_token"], before["edit_token"])

        replay = self.manager.configure(self.core, self.request(before, changes, "enable-reply")).unwrap()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["operation_id"], first["operation_id"])

        conflicting = self.manager.configure(self.core, self.request(before, {"enabled": False}, "enable-reply"))
        self.assertEqual(conflicting.failure.code, "idempotency_key_conflict")

        stale = self.manager.configure(self.core, self.request(before, {"enabled": False}, "stale-token"))
        self.assertEqual(stale.failure.code, "configuration_revision_conflict")

        second_batch = self.host.receive(self.event("reply-settings-second")).unwrap()
        self.advance(self.app)
        await self.done(self.app, second_batch)
        captured = json.loads(self.app.messages.batch(second_batch)["contributions_json"])["reply_settings"]
        self.assertTrue(captured["settings"]["enabled"])
        self.assertTrue(captured["settings"]["rules_enabled"])
        self.assertEqual(captured["settings"]["threshold"], 8)
        self.assertEqual(captured["settings"]["image_position"], "before_text")

        legacy = dict(before["settings"])
        legacy.pop("image_position")
        legacy = {key: value for key, value in legacy.items() if not key.startswith("voice_")}
        self.assertEqual(self.manager.preview(self.core, legacy, "旧回复").unwrap()["image_position"], "after_text")

    async def test_group_air_settings_are_editable_and_sealed_per_batch(self):
        original = json.loads(self.app.messages.batch(self.first_batch)["contributions_json"])["reply_settings"]["settings"]
        self.assertEqual({key: original[key] for key in GROUP_AIR_DEFAULTS}, GROUP_AIR_DEFAULTS)
        status = self.manager.status(self.core).unwrap()
        changes = {"group_air_guard_enabled": False, "group_air_guard_window_seconds": 300,
                   "group_air_guard_max_bot_replies": 5, "group_air_guard_polite_loop_limit": 4}
        saved = self.manager.configure(self.core, self.request(status, changes, "group-air")).unwrap()
        self.assertEqual({key: saved["status"]["settings"][key] for key in changes}, changes)
        batch = self.host.receive(self.event("group-air-next")).unwrap()
        self.advance(self.app)
        await self.done(self.app, batch)
        captured = json.loads(self.app.messages.batch(batch)["contributions_json"])["reply_settings"]["settings"]
        self.assertEqual({key: captured[key] for key in changes}, changes)
        historical = json.loads(self.app.messages.batch(self.first_batch)["contributions_json"])["reply_settings"]["settings"]
        self.assertEqual({key: historical[key] for key in GROUP_AIR_DEFAULTS}, GROUP_AIR_DEFAULTS)

    async def test_forward_reading_setting_is_validated_and_sealed_per_batch(self):
        before = self.manager.status(self.core).unwrap()
        self.assertEqual({key: before["settings"][key] for key in FORWARD_DEFAULTS}, FORWARD_DEFAULTS)
        saved = self.manager.configure(self.core, self.request(
            before, {"forward_message_adaptation_enabled": False}, "disable-forward-reading")).unwrap()
        batch = self.host.receive(self.event("forward-reading-disabled")).unwrap()
        self.advance(self.app)
        await self.done(self.app, batch)
        captured = json.loads(self.app.messages.batch(batch)["contributions_json"])["reply_settings"]["settings"]
        self.assertFalse(captured["forward_message_adaptation_enabled"])
        self.assertTrue(json.loads(self.app.messages.batch(self.first_batch)["contributions_json"])
                        ["reply_settings"]["settings"]["forward_message_adaptation_enabled"])
        legacy = dict(before["settings"])
        legacy.pop("forward_message_adaptation_enabled")
        validate_reply_settings(legacy)
        with self.assertRaises(ConfigurationError):
            validate_reply_settings({**saved["status"]["settings"], "forward_message_adaptation_enabled": 1})

    async def test_image_narration_controls_are_bounded_and_saved_per_window(self):
        status = self.manager.status(self.core).unwrap()
        settings = status["settings"]
        self.assertFalse(settings["enable_private_image_self_recognition"])
        self.assertTrue(settings["enable_private_image_vision_cache"])
        self.assertTrue(settings["astrbot_animated_image_understanding_enabled"])
        changes = {"enable_private_image_self_recognition": True,
                   "astrbot_animated_image_understanding_enabled": False,
                   "private_image_vision_custom_prompt": "优先描述清晰可读的文字。",
                   "private_image_vision_max_chars": 600,
                   "private_image_vision_wait_seconds": 4,
                   "private_image_provider_failure_cooldown_seconds": 90}
        saved = self.manager.configure(self.core, self.request(status, changes, "image-narration")).unwrap()
        self.assertEqual({key: saved["status"]["settings"][key] for key in changes}, changes)
        invalid = dict(saved["status"]["settings"], private_image_vision_wait_seconds=31)
        self.assertEqual(self.manager.preview(self.core, invalid, "正文").failure.code,
                         "reply_configuration_invalid")

    async def test_media_scope_permissions_are_role_shared_and_gate_private_or_group_work(self):
        from companion_v2.configuration.models import ConfigLayer
        role_manager = ReplyManagement(self.app, self.context, layer=ConfigLayer.ROLE)
        status = role_manager.status(self.core).unwrap()
        changes = {
            "image_understanding_private_enabled": False,
            "image_understanding_group_enabled": True,
            "video_understanding_private_enabled": True,
            "video_understanding_group_enabled": False,
            "file_understanding_private_enabled": False,
            "file_understanding_group_enabled": True,
        }
        saved = role_manager.configure(self.core,
            self.request(status, changes, "media-scope-permissions")).unwrap()
        settings = saved["status"]["settings"]
        self.assertEqual({key: settings[key] for key in changes}, changes)
        for name in changes:
            expected_layers = (["role", "window"] if name in SCOPE_PERMISSION_FIELDS | ROLE_PROMPT_FIELDS
                               else ["window"])
            self.assertEqual(saved["status"]["fields"][name]["editable_layers"], expected_layers)
        self.assertFalse(media_scope_enabled(settings, "image_understanding", ScopeKind.PRIVATE))
        self.assertTrue(media_scope_enabled(settings, "image_understanding", ScopeKind.GROUP))
        self.assertTrue(media_scope_enabled(settings, "video_understanding", ScopeKind.PRIVATE))
        self.assertFalse(media_scope_enabled(settings, "video_understanding", ScopeKind.GROUP))
        self.assertFalse(media_scope_enabled(settings, "file_understanding", ScopeKind.PRIVATE))
        self.assertTrue(media_scope_enabled(settings, "file_understanding", ScopeKind.GROUP))

        group_scope = replace(self.context.scope, kind=ScopeKind.GROUP, native_scope_id="group-window")
        group_target = ConfigTarget(group_scope.installation_id, ConfigLayer.WINDOW,
            self.context.role_binding.role, group_scope)
        group_manager = ReplyManagement(self.app, target=group_target)
        group_settings = group_manager.status(self.core).unwrap()["settings"]
        self.assertEqual({key: group_settings[key] for key in changes}, changes)

    async def test_role_defaults_are_inherited_and_private_windows_can_override_them(self):
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        role_manager = ReplyManagement(self.app, self.context, layer=ConfigLayer.ROLE)
        role_status = role_manager.status(self.core).unwrap()
        role_value = "角色默认表达，保持自然并主动接住话题。"
        role_saved = role_manager.configure(self.core,
            self.request(role_status, {"style_prompt": role_value}, "role-style-default")).unwrap()
        self.assertEqual(role_saved["status"]["scope"], "role")
        self.assertEqual(role_saved["status"]["settings"]["style_prompt"], role_value)

        window_manager = ReplyManagement(self.app, self.context, layer=ConfigLayer.WINDOW)
        inherited = window_manager.status(self.core).unwrap()
        self.assertEqual(inherited["settings"]["style_prompt"], role_value)
        self.assertEqual(inherited["provenance"]["style_prompt"]["layer"], "role")
        local_value = "这个私聊里尽量简短。"
        local_saved = window_manager.configure(self.core,
            self.request(inherited, {"style_prompt": local_value}, "private-style-override")).unwrap()
        self.assertEqual(local_saved["status"]["settings"]["style_prompt"], local_value)
        self.assertEqual(local_saved["status"]["provenance"]["style_prompt"]["layer"], "window")
        reset_status = local_saved["status"]
        reset = window_manager.configure(self.core, {"operation_key": "private-style-reset",
            "edit_token": reset_status["edit_token"], "changes": {}, "reset_to_inherit": ["style_prompt"]}).unwrap()
        self.assertEqual(reset["status"]["settings"]["style_prompt"], role_value)
        self.assertEqual(reset["status"]["provenance"]["style_prompt"]["layer"], "role")
        self.assertFalse(reset["status"]["provenance"]["style_prompt"]["explicit"])
        self.assertEqual(role_manager.status(self.core).unwrap()["settings"]["style_prompt"], role_value)

        other_scope = replace(self.context.scope, native_scope_id="other-private-window",
            conversation_id="other-private-conversation", session_id="other-private-session")
        other_target = ConfigTarget(other_scope.installation_id, ConfigLayer.WINDOW,
            self.context.role_binding.role, other_scope)
        from companion_v2.conversation.reply_settings import GROUP, OWNER
        owner = self.app.configuration._owners[OWNER]
        other = owner.read(other_target, self.app.configuration.registry.group(GROUP))
        field = other.fields[GROUP + ".style_prompt"]
        self.assertEqual((field.value, field.source.layer.value, field.explicit), (role_value, "role", False))

    async def test_group_wakeup_role_default_can_be_overridden_and_reset_per_group(self):
        role_manager = ReplyManagement(self.app, self.context, layer=ConfigLayer.ROLE)
        role_status = role_manager.status(self.core).unwrap()
        self.assertEqual(role_status["fields"]["group_wakeup_enabled"]["editable_layers"], ["role", "window"])
        self.assertEqual(role_status["fields"]["group_wakeup_enabled"]["write_layer"], "role")
        role_manager.configure(self.core,
            self.request(role_status, {"group_wakeup_enabled": False}, "role-group-wakeup")).unwrap()

        group_scope = replace(self.context.scope, kind=ScopeKind.GROUP,
            native_scope_id="reply-settings-group", conversation_id="reply-settings-conversation",
            session_id="reply-settings-session")
        group_target = ConfigTarget(group_scope.installation_id, ConfigLayer.WINDOW,
            self.context.role_binding.role, group_scope)
        window_manager = ReplyManagement(self.app, target=group_target)
        window_status = window_manager.status(self.core).unwrap()
        self.assertFalse(window_status["settings"]["group_wakeup_enabled"])
        self.assertEqual(window_status["fields"]["group_wakeup_enabled"]["write_layer"], "window")
        self.assertEqual(window_status["fields"]["group_wakeup_enabled"]["editable_layers"], ["role", "window"])
        self.assertEqual(window_status["provenance"]["group_wakeup_enabled"]["layer"], "role")

        overridden = window_manager.configure(self.core,
            self.request(window_status, {"group_wakeup_enabled": True}, "group-wakeup-override")).unwrap()
        self.assertTrue(overridden["status"]["settings"]["group_wakeup_enabled"])
        self.assertEqual(overridden["status"]["provenance"]["group_wakeup_enabled"]["layer"], "window")

        another_conversation = replace(group_scope, conversation_id="reply-settings-new-conversation")
        stable_target = ConfigTarget(another_conversation.installation_id, ConfigLayer.WINDOW,
            self.context.role_binding.role, another_conversation)
        stable_status = ReplyManagement(self.app, target=stable_target).status(self.core).unwrap()
        self.assertTrue(stable_status["settings"]["group_wakeup_enabled"])

        another_group = replace(group_scope, native_scope_id="reply-settings-other-group",
            conversation_id="reply-settings-other-conversation")
        other_target = ConfigTarget(another_group.installation_id, ConfigLayer.WINDOW,
            self.context.role_binding.role, another_group)
        other_status = ReplyManagement(self.app, target=other_target).status(self.core).unwrap()
        self.assertFalse(other_status["settings"]["group_wakeup_enabled"])

        reset = ReplyManagement(self.app, target=stable_target).configure(self.core, {
            "operation_key": "group-wakeup-reset",
            "edit_token": stable_status["edit_token"], "changes": {},
            "reset_to_inherit": ["group_wakeup_enabled"]}).unwrap()
        self.assertFalse(reset["status"]["settings"]["group_wakeup_enabled"])
        self.assertEqual(reset["status"]["provenance"]["group_wakeup_enabled"]["layer"], "role")
        self.assertFalse(reset["status"]["provenance"]["group_wakeup_enabled"]["explicit"])
        self.assertFalse(role_manager.status(self.core).unwrap()["settings"]["group_wakeup_enabled"])

    async def test_old_group_conversation_override_is_found_and_cleared_after_stable_migration(self):
        from companion_v2.configuration.owners import SparseConfigurationOwner

        role_manager = ReplyManagement(self.app, self.context, layer=ConfigLayer.ROLE)
        role_status = role_manager.status(self.core).unwrap()
        role_manager.configure(self.core,
            self.request(role_status, {"group_wakeup_direct_words": '["角色默认"]'}, "role-wakeup-word")).unwrap()

        group_scope = replace(self.context.scope, kind=ScopeKind.GROUP,
            native_scope_id="legacy-group", conversation_id="legacy-conversation",
            session_id="stable-group-session")
        target = ConfigTarget(group_scope.installation_id, ConfigLayer.WINDOW,
            self.context.role_binding.role, group_scope)
        fields = self.app.configuration.registry.group(GROUP)
        local_fields = tuple(replace(field, layers=(ConfigLayer.WINDOW,)) for field in fields)
        owner = self.app.configuration._owners[OWNER]
        legacy = SparseConfigurationOwner.read(owner, target, local_fields)
        SparseConfigurationOwner.write(owner, target, local_fields,
            changes={GROUP + ".group_wakeup_direct_words": '["旧群配置"]'}, reset=(),
            expected_revision=legacy.revision, observed_at=self.app.now())

        current_scope = replace(group_scope, conversation_id="new-conversation")
        current_target = ConfigTarget(current_scope.installation_id, ConfigLayer.WINDOW,
            self.context.role_binding.role, current_scope)
        current_manager = ReplyManagement(self.app, target=current_target)
        inherited = current_manager.status(self.core).unwrap()
        self.assertEqual(inherited["settings"]["group_wakeup_direct_words"], '["旧群配置"]')
        self.assertEqual(inherited["provenance"]["group_wakeup_direct_words"]["layer"], "window")

        changed = current_manager.configure(self.core,
            self.request(inherited, {"group_wakeup_direct_words": '["新群配置"]'}, "stable-group-word")).unwrap()
        self.assertEqual(changed["status"]["settings"]["group_wakeup_direct_words"], '["新群配置"]')
        reset = current_manager.configure(self.core, {"operation_key": "stable-group-word-reset",
            "edit_token": changed["status"]["edit_token"], "changes": {},
            "reset_to_inherit": ["group_wakeup_direct_words"]}).unwrap()
        self.assertEqual(reset["status"]["settings"]["group_wakeup_direct_words"], '["角色默认"]')
        self.assertEqual(reset["status"]["provenance"]["group_wakeup_direct_words"]["layer"], "role")

    async def test_role_change_and_inherit_reset_share_one_complete_receipt(self):
        role_manager = ReplyManagement(self.app, self.context, layer=ConfigLayer.ROLE)
        role_status = role_manager.status(self.core).unwrap()
        role_manager.configure(self.core, self.request(role_status,
            {"group_wakeup_enabled": False}, "role-wakeup-before-combined-patch")).unwrap()

        window_manager = ReplyManagement(self.app, self.context)
        status = window_manager.status(self.core).unwrap()
        window_override = window_manager.configure(self.core, self.request(status,
            {"group_wakeup_enabled": True}, "window-wakeup-before-reset")).unwrap()["status"]
        operation_id = "reply-settings:role-change-and-reset"
        receipt = self.app.configuration.patch(self.core, target=window_manager.target, group_id=GROUP,
            operation=OperationRef(operation_id, OWNER, window_manager.target.authorization_scope,
                VersionRef(operation_id, 1), operation_id),
            expected_edit_token=window_override["edit_token"],
            changes={GROUP + ".group_wakeup_direct_words": '["星星"]'},
            reset_to_inherit=(GROUP + ".group_wakeup_enabled",), observed_at=self.app.now()).unwrap()

        self.assertEqual(receipt.changed_fields, tuple(sorted((
            GROUP + ".group_wakeup_direct_words", GROUP + ".group_wakeup_enabled"))))

    async def test_video_and_file_understanding_controls_are_saved_per_window(self):
        status = self.manager.status(self.core).unwrap()
        settings = status["settings"]
        changes = {
            "video_understanding_enabled": True,
            "video_understanding_max_chars": 3200,
            "video_understanding_wait_seconds": 25,
            "video_understanding_prompt": "先概括动作顺序。",
            "video_self_recognition_hint": "角色水印是蓝色月牙。",
            "file_understanding_enabled": True,
            "file_understanding_formats": "text_only",
            "file_understanding_format_groups": "文本\nPDF\nWord（DOCX）",
            "file_understanding_max_size_mb": 6,
            "file_understanding_max_chars": 12000,
            "file_understanding_timeout_seconds": 12,
            "file_understanding_prompt": "优先解释标题和表格。",
        }
        saved = self.manager.configure(self.core,
            self.request(status, changes, "media-understanding-preferences")).unwrap()
        self.assertEqual({key: saved["status"]["settings"][key] for key in changes}, changes)

        invalid = dict(saved["status"]["settings"], file_understanding_timeout_seconds=31)
        self.assertEqual(self.manager.preview(self.core, invalid, "正文").failure.code,
                         "reply_configuration_invalid")

    async def test_text_defaults_preview_and_sealed_send_use_the_same_policy(self):
        original = self.manager.status(self.core).unwrap()
        # HostFixture disables punctuation cleanup until this test enables it.
        self.assertEqual({key: original["settings"][key] for key in TEXT_DEFAULTS},
                         {**TEXT_DEFAULTS, "content_cleanup_enabled": False})
        changes = {"enabled": True, "llm_controlled": True, "content_cleanup_enabled": True,
                   "content_cleanup_words": '["。", "\\n"]',
                   "content_replacement_enabled": True, "content_replacements": '["hi => 你好"]'}
        saved = self.manager.configure(self.core, self.request(original, changes, "text-enable")).unwrap()["status"]
        text = "hi。\n" + LLM_SEGMENT_MARKER + "\nhi。\n"
        preview = self.manager.preview(self.core, saved["settings"], text).unwrap()
        self.assertEqual(preview["parts"], ("你好", "你好"))
        entered, release = asyncio.Event(), asyncio.Event()

        async def model(envelope):
            entered.set()
            await release.wait()
            return answered(text)

        self.model_behavior = model
        sent_before = len(self.sent)
        batch = self.host.receive(self.event("text-sealed")).unwrap()
        self.advance(self.app)
        await asyncio.wait_for(entered.wait(), 3)
        self.manager.configure(self.core, self.request(saved,
            {"content_replacements": '["hi => 新偏好"]'}, "text-next-preference")).unwrap()
        release.set()
        self.assertEqual((await self.done(self.app, batch))["state"], "complete")
        self.assertEqual(tuple(envelope.text for envelope in self.sent[sent_before:]), preview["parts"])
        captured = json.loads(self.app.messages.batch(batch)["contributions_json"])["reply_settings"]["settings"]
        self.assertEqual(captured["content_replacements"], changes["content_replacements"])
        self.assertNotIn("正文呈现偏好", self.called[-1].original.instruction)

    async def test_invalid_preview_is_rejected_without_model_or_storage_changes(self):
        status = self.manager.status(self.core).unwrap()
        before_calls = len(self.called)
        invalid = dict(status["settings"])
        invalid["max_segments"] = 0
        result = self.manager.preview(self.core, invalid, "正文")
        self.assertEqual(result.failure.code, "reply_configuration_invalid")
        self.assertEqual(len(self.called), before_calls)
        invalid = dict(status["settings"], image_position="unsupported")
        result = self.manager.preview(self.core, invalid, "正文")
        self.assertEqual(result.failure.code, "reply_configuration_invalid")

    async def test_voice_preview_uses_real_segment_and_component_rules_without_synthesis(self):
        settings = dict(self.manager.status(self.core).unwrap()["settings"])
        settings.update(enabled=True, llm_controlled=True, voice_enabled=True,
                        voice_generation_mode="model_tag", voice_segment_mode="last",
                        voice_delivery_mode="voice_only", voice_position="before_text",
                        image_position="before_text", voice_language="ja", voice_ja_provider_id="ja-tts")
        text = "<pc_tts>最初。</pc_tts>前文\n" + LLM_SEGMENT_MARKER + "\n<pc_tts>次。</pc_tts>"
        calls, sends = len(self.called), len(self.sent)
        preview = self.manager.preview(self.core, settings, text, 2).unwrap()
        self.assertEqual(preview["parts"], ("最初。前文", "次。"))
        self.assertEqual(preview["image_count"], 2)
        self.assertEqual(preview["voice"], {"enabled": True, "provider_id": "ja-tts",
                                           "instruction": preview["voice"]["instruction"]})
        self.assertEqual([item["speech"] for item in preview["components"]], ["", "次。"])
        self.assertEqual([item["success_order"] for item in preview["components"]],
                         [("text",), ("image", "image", "voice")])
        self.assertEqual(preview["components"][1]["fallback_order"], ("image", "image", "text"))
        self.assertTrue(preview["components"][1]["text_hidden"])
        self.assertEqual((len(self.called), len(self.sent)), (calls, sends))
        self.assertEqual(self.manager.preview(self.core, settings, text, True).failure.code, "reply_preview_invalid")
        self.assertEqual(self.manager.preview(self.core, settings, text, 9).failure.code, "reply_preview_invalid")

    async def test_voice_settings_are_validated_saved_and_captured(self):
        status = self.manager.status(self.core).unwrap()
        self.assertFalse(status["settings"]["voice_enabled"])
        change = {"voice_enabled": True, "voice_trigger_mode": "request", "voice_language": "en",
                  "voice_en_provider_id": "english-tts", "voice_probability": 0.4,
                  "voice_primary_mention_keywords": "专属词\n只对我",
                  "voice_primary_mention_probability": 0.95,
                  "voice_primary_mention_prompt": "语气更亲近一些。"}
        saved = self.manager.configure(self.core, self.request(status, change, "voice-enable")).unwrap()
        self.assertEqual(saved["status"]["settings"]["voice_en_provider_id"], "english-tts")
        self.assertEqual({key: saved["status"]["settings"][key] for key in change}, change)
        self.assertTrue(self.manager.configure(self.core, self.request(status, change, "voice-enable")).unwrap()["replayed"])
        batch = self.host.receive(self.event("voice-settings-next")).unwrap()
        self.advance(self.app)
        await self.done(self.app, batch)
        captured = json.loads(self.app.messages.batch(batch)["contributions_json"])["reply_settings"]
        self.assertEqual(captured["settings"]["voice_probability"], 0.4)
        self.assertEqual(captured["settings"]["voice_language"], "en")
        self.assertEqual(captured["settings"]["voice_primary_mention_keywords"], "专属词\n只对我")
        self.assertEqual(captured["settings"]["voice_primary_mention_probability"], 0.95)
        self.assertEqual(captured["settings"]["voice_primary_mention_prompt"], "语气更亲近一些。")
        current = saved["status"]
        self.assertEqual(self.manager.configure(self.core, self.request(current,
            {"voice_timeout_seconds": 0}, "invalid-voice")).failure.code, "invalid_configuration_value")
        self.assertEqual(self.manager.status(self.core).unwrap()["edit_token"], current["edit_token"])
        self.assertEqual(self.manager.configure(self.core, self.request(current,
            {"voice_primary_mention_probability": 1.01}, "invalid-primary-voice-probability")).failure.code,
            "invalid_probability")

    async def test_voice_language_is_a_window_override_with_a_role_default(self):
        role_manager = ReplyManagement(self.app, self.context, layer=ConfigLayer.ROLE)
        role_status = role_manager.status(self.core).unwrap()
        role_manager.configure(self.core, self.request(role_status, {"voice_language": "ja"},
                                                       "role-voice-language")).unwrap()
        self.assertEqual(role_manager.status(self.core).unwrap()["settings"]["voice_language"], "ja")
        self.assertEqual(self.manager.status(self.core).unwrap()["settings"]["voice_language"], "ja")

        window_status = self.manager.status(self.core).unwrap()
        self.assertEqual(window_status["fields"]["voice_language"]["write_layer"], "window")
        self.manager.configure(self.core, self.request(window_status, {"voice_language": "en"},
                                                       "window-voice-language")).unwrap()
        self.assertEqual(self.manager.status(self.core).unwrap()["settings"]["voice_language"], "en")
        self.assertEqual(role_manager.status(self.core).unwrap()["settings"]["voice_language"], "ja")

    async def test_group_voice_language_command_writes_only_this_group_window(self):
        from astrbot_host.functional_commands import execute_functional_command, parse_functional_command

        group_scope = replace(self.context.scope, kind=ScopeKind.GROUP,
                              native_scope_id="voice-command-group", conversation_id="voice-command-conversation",
                              session_id="voice-command-session")
        command_context = SimpleNamespace(key="voice-command-group-key", scope=group_scope,
                                          role_binding=self.context.role_binding, actor=self.core)
        command = parse_functional_command("陪伴群 切换语种 日语", channel="group")
        receipt = execute_functional_command(command, application=self.app, context=command_context)
        self.assertIn("已切换", receipt)
        self.assertIn("本群", receipt)

        target = ConfigTarget(group_scope.installation_id, ConfigLayer.WINDOW,
                              self.context.role_binding.role, group_scope)
        group_status = ReplyManagement(self.app, target=target).status(self.core).unwrap()
        self.assertEqual(group_status["settings"]["voice_language"], "ja")
        self.assertEqual(group_status["provenance"]["voice_language"]["layer"], "window")

    async def test_group_voice_language_command_rejects_a_standard_member(self):
        from astrbot_host.functional_commands import execute_functional_command, parse_functional_command

        group_scope = replace(self.context.scope, kind=ScopeKind.GROUP,
                              native_scope_id="voice-command-denied-group",
                              conversation_id="voice-command-denied-conversation",
                              session_id="voice-command-denied-session")
        command_context = SimpleNamespace(key="voice-command-denied-key", scope=group_scope,
                                          role_binding=self.context.role_binding, actor=self.standard)
        command = parse_functional_command("陪伴群 切换语种 日语", channel="group")
        receipt = execute_functional_command(command, application=self.app, context=command_context)
        self.assertIn("未执行", receipt)

        target = ConfigTarget(group_scope.installation_id, ConfigLayer.WINDOW,
                              self.context.role_binding.role, group_scope)
        group_status = ReplyManagement(self.app, target=target).status(self.core).unwrap()
        self.assertEqual(group_status["settings"]["voice_language"], "zh")

    async def test_explicit_turn_language_uses_matching_tts_provider_without_changing_saved_language(self):
        status = self.manager.status(self.core).unwrap()
        self.manager.configure(self.core, self.request(status, {
            "voice_enabled": True,
            "voice_trigger_mode": "always",
            "voice_language": "zh",
            "voice_en_provider_id": "english-tts",
        }, "voice-turn-language")).unwrap()
        self.model_behavior = lambda _envelope: asyncio.sleep(0, result=answered("Hello there."))

        batch = self.host.receive(self.event(text="这次请用英语回答我")).unwrap()
        self.advance(self.app)
        self.assertEqual((await self.done(self.app, batch))["state"], "complete")

        plans = self.app.runtime.read_all("""SELECT v.provider_id FROM conversation_voice_parts v
            JOIN conversation_parts p USING(part_id) WHERE p.batch_id = ? ORDER BY p.sequence""", (batch,))
        captured = json.loads(self.app.messages.batch(batch)["contributions_json"])["reply_settings"]["settings"]
        self.assertTrue(plans)
        self.assertEqual({row["provider_id"] for row in plans}, {"english-tts"})
        self.assertEqual(captured["voice_language"], "zh")
