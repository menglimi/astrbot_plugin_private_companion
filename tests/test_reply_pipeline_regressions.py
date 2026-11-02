"""Reproduce short replies and preserve explicit migrated presentation settings."""

from dataclasses import asdict, replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from companion_v2.configuration.registry import FieldRegistry
from companion_v2.conversation.reply_preparation import prepare_reply
from companion_v2.conversation.reply_settings import GROUP, configuration_fields
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.text_processing import ReplyTextPolicy, text_policy
from companion_v2.legacy.configuration_migration import (
    convert_configuration,
    convert_plugin_configuration,
    plugin_configuration_defaults,
    plugin_configuration_rules,
)
from tests.test_host_application import HostFixture
from tests.test_role_tool_loop import answered


ROOT = Path(__file__).resolve().parents[1]
WHOLE_PART_CLEANUP_SCOPES = frozenset(("leading", "middle", "trailing"))


def migrated_presentation(source, window_kind):
    """Fold the migrated V1 settings over the current V2 presentation defaults.

    The plugin-level segmentation section seeds the window defaults that the
    application builds, while the registry converter's
    ``conversation.presentation.*`` rows are stored settings that override those
    defaults.  The fold therefore applies the plugin converter first and the
    registry converter last, on top of today's field defaults.
    """
    fields = configuration_fields(SegmentationPolicy(), True, 1.5)
    registry = FieldRegistry(version="reply-pipeline-regressions", fields=fields)
    migrated = {field.field_id.removeprefix(GROUP + "."): field.default for field in fields}
    for changes in (convert_plugin_configuration(source, window_kind=window_kind).changes,
                    convert_configuration(source, registry=registry, window_kind=window_kind).changes):
        for field_id, value in changes.items():
            name = field_id.removeprefix(GROUP + ".").removeprefix("segmentation.")
            if name in migrated:
                migrated[name] = value
    return migrated


class ReplyPresetTests(unittest.TestCase):
    def test_migrated_settings_keep_explicit_cleanup_while_new_defaults_enable_cleanup(self):
        source = json.loads((ROOT / "fixtures/reply_segmentation.v1.json").read_text(encoding="utf-8"))
        # These dormant window values must never win while profiles are off.
        source["legacy_compat_config"].update({"segmented_proactive_private_min_segment_chars": 8,
            "segmented_proactive_private_max_segments": 3, "segmented_proactive_private_log_base": 1.8})
        migrated = migrated_presentation(source, "private")
        defaults = {field.field_id.removeprefix(GROUP + "."): field.default
                    for field in configuration_fields(SegmentationPolicy(), True, 1.5)}
        self.assertTrue(defaults["content_cleanup_enabled"])
        self.assertTrue(migrated["content_cleanup_enabled"])
        # V1's saved whole-part cleanup survives migration; only the V2 default
        # narrows cleanup to the end of each part.
        self.assertEqual(migrated["content_cleanup_scope"], "all")
        self.assertEqual(text_policy(migrated).cleanup_scopes, WHOLE_PART_CLEANUP_SCOPES)
        self.assertEqual(json.loads(migrated["content_cleanup_words"]), ["。", "，"])
        self.assertEqual(migrated["content_cleanup_rule"],
                         source["legacy_compat_config"]["segmented_proactive_content_cleanup_rule"])
        # Only the settings V1 saved with a different value leave today's defaults;
        # the dormant per-window values above and every other field stay on them.
        explicit = {"content_cleanup_rule", "content_cleanup_scope", "content_cleanup_words", "split_words"}
        self.assertEqual({key for key in migrated if migrated[key] != defaults[key]}, explicit)
        # V1's explicitly saved comma boundary survives migration, while new
        # replies keep comma-linked clauses together by default.
        self.assertIn("，", json.loads(migrated["split_words"]))
        self.assertNotIn("，", json.loads(defaults["split_words"]))
        self.assertEqual(migrated, migrated_presentation(source, "group"))
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        plugin_defaults = plugin_configuration_defaults()
        for key, item in schema["segmentation"]["items"].items():
            self.assertEqual(item["default"], asdict(SegmentationPolicy())[key], key)
            self.assertEqual(plugin_defaults["segmentation." + key], item["default"], key)
        split_rule = next(rule for rule in plugin_configuration_rules()
                          if rule["target"] == "segmentation.split_words")
        self.assertEqual(split_rule["contract"]["default"], defaults["split_words"])

    def test_profile_segmentation_overrides_the_plugin_defaults(self):
        source = json.loads((ROOT / "fixtures/reply_segmentation.v1.json").read_text(encoding="utf-8"))
        source["legacy_compat_config"].update({"enable_segmented_proactive_chat_profiles": True,
            "segmented_proactive_private_max_segments": 3,
            "segmented_proactive_private_min_segment_chars": 8,
            "segmented_proactive_private_threshold": 120})
        # The plugin-level segmentation section still migrates its own values ...
        global_max = source["legacy_compat_config"]["segmented_proactive_max_segments"]
        plugin = convert_plugin_configuration(source, window_kind="private").changes
        self.assertEqual(plugin["segmentation.max_segments"], global_max)
        private = migrated_presentation(source, "private")
        self.assertEqual(private["max_segments"], 3)
        self.assertEqual(private["min_segment_chars"], 8)
        self.assertEqual(private["threshold"], 120)
        # ... but only the selected window's rows override them.
        self.assertEqual(migrated_presentation(source, "group")["max_segments"], global_max)


class ReplyPreparationTests(unittest.TestCase):
    def test_live_short_reply_splits_and_has_no_edge_blank_lines(self):
        reply = prepare_reply("\n上课呢…偷偷瞄一眼。怎么啦，这个点找我？\n\n",
                              SegmentationPolicy(), ReplyTextPolicy())
        self.assertEqual(reply.parts, ("上课呢…偷偷瞄一眼。", "怎么啦，这个点找我？"))
        self.assertTrue(reply.source_parts[0].startswith("\n"))

    def test_explicit_split_discards_blank_lines_around_message_boundaries(self):
        marker = "<<PRIVATE_COMPANION_SPLIT>>"
        reply = prepare_reply("\n早安。\n\n" + marker + "\n\n下午好。\n",
                              SegmentationPolicy(enabled=True, llm_controlled=True), ReplyTextPolicy())
        self.assertEqual(reply.parts, ("早安。", "下午好。"))

    def test_blank_edge_cleanup_preserves_indentation_and_internal_paragraphs(self):
        reply = prepare_reply(" \n\n    内容\n\n第二行\n \n", SegmentationPolicy(enabled=False),
                              ReplyTextPolicy(content_cleanup_enabled=False))
        self.assertEqual(reply.parts, ("    内容\n\n第二行",))

    def test_short_sentences_merged_in_one_part_keep_internal_punctuation(self):
        reply = prepare_reply("好。嗯，明白了。", SegmentationPolicy(), ReplyTextPolicy())
        self.assertEqual(reply.parts, ("好。嗯，明白了。",))

    def test_explicit_cleanup_only_removes_the_end_of_each_sent_part(self):
        reply = prepare_reply("好。嗯，明白了。", SegmentationPolicy(),
                              ReplyTextPolicy(content_cleanup_enabled=True,
                                              content_cleanup_scope="trailing",
                                              content_cleanup_words=json.dumps(["。", "，"])))
        self.assertEqual(reply.parts, ("好。嗯，明白了",))

    def test_v1_regex_keeps_zero_width_punctuation_boundaries(self):
        policy = SegmentationPolicy(rule_mode="regex")
        source = "这是第一句话。这是第二句话！"
        self.assertEqual(policy.parts(source), ("这是第一句话。", "这是第二句话！"))
        self.assertEqual("".join(policy.parts(source)), source)


class ReplyDispatchOrderTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_rule_split_short_reply_sends_trimmed_text_without_format_prompt(self):
        async def model(envelope):
            return answered("哦？被拆穿了就骂我笨蛋…烛雨大人好过分呢。\n才不生气，反正我说的是真的嘛。")

        self.model_behavior = model
        segmentation = SegmentationPolicy()
        registry = tuple(replace(field, default="fixed")
                         if field.field_id == "conversation.presentation.part_gap_mode" else field
                         for field in configuration_fields(segmentation, True, 0))
        self.registry = type(self.registry)(version=self.registry.version, fields=(
            *self.registry.fields.values(),
            *(field for field in registry if field.field_id not in self.registry.fields),
        ))
        host = await self.start(self.host(segmentation=segmentation))
        batch = host.receive(self.event("short-reply-blank-edge")).unwrap()
        app = host.application
        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual([envelope.text for envelope in self.sent], [
            "哦？被拆穿了就骂我笨蛋…", "烛雨大人好过分呢。", "才不生气，反正我说的是真的嘛。",
        ])
        stored = app.runtime.read_all(
            "SELECT text FROM conversation_parts WHERE batch_id = ? ORDER BY sequence", (batch,))
        self.assertEqual([part["text"] for part in stored], [envelope.text for envelope in self.sent])
        self.assertNotIn("消息正文组织", self.called[0].original.instruction)
        self.assertNotIn("日常短消息正文内部不插入空行", self.called[0].original.instruction)

    async def test_new_foreground_work_starts_before_periodic_maintenance(self):
        host = await self.start()
        app = host.application
        events = []
        run, advance = app.runner.run, app.memory_schedule.advance

        async def recording_run(context, task_id):
            events.append("dispatch")
            return await run(context, task_id)

        def recording_advance():
            if app._workers:
                events.append("maintenance")
            return advance()

        with patch.object(app.runner, "run", recording_run), patch.object(app.memory_schedule, "advance", recording_advance):
            batch = host.receive(self.event("foreground-first")).unwrap()
            self.advance(app)
            self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertIn("maintenance", events)
        self.assertEqual(events[0], "dispatch")
