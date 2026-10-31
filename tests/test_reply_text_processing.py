from dataclasses import asdict, replace
import json
import unittest

from companion_v2.configuration.models import ConfigurationError
from companion_v2.conversation.reply_preparation import prepare_reply
from companion_v2.conversation.reply_settings import validate
from companion_v2.conversation.segmentation import SegmentationPolicy, LLM_SEGMENT_MARKER as MARKER
from companion_v2.conversation.text_processing import (
    InternalExpressionProtocolFilter, ReplyTextPolicy, TEXT_DEFAULTS,
    strip_internal_expression_protocol,
)
from companion_v2.conversation.voice_reply import VoiceReplyPolicy


def policy(**changes):
    return ReplyTextPolicy(**{"content_cleanup_enabled": False, "content_cleanup_scope": "all", **changes})


class ReplyTextProcessingTests(unittest.TestCase):
    def test_default_word_segmentation_keeps_comma_linked_clauses_together(self):
        text = "知道呀～就是机器人内部给这条申请生成的唯一编号，方便后台批准或驳回时对上号。你不用管它，看昵称和问答就行。"
        self.assertEqual(SegmentationPolicy().parts(text), (
            "知道呀～就是机器人内部给这条申请生成的唯一编号，方便后台批准或驳回时对上号。",
            "你不用管它，看昵称和问答就行。"))

    def test_forbidden_word_guard_is_opt_in_scoped_and_case_aware(self):
        disabled = policy(recall_forbidden_words='["秘密"]')
        self.assertIsNone(disabled.forbidden_word_guard("这里有秘密"))
        group_only = policy(enable_forbidden_word_recall=True, recall_forbidden_words='["Secret"]',
                            recall_forbidden_scope="group_only")
        self.assertIsNone(group_only.forbidden_word_guard("secret", scope_kind="private"))
        self.assertEqual(group_only.forbidden_word_guard("secret", scope_kind="group"), "Secret")
        bot_only = policy(enable_forbidden_word_recall=True, recall_forbidden_words='["Secret"]',
                          recall_forbidden_scope="bot_only")
        self.assertIsNone(bot_only.forbidden_word_guard("secret", is_bot_output=False))
        self.assertEqual(bot_only.forbidden_word_guard("secret", is_bot_output=True), "Secret")
        bot_and_group = policy(enable_forbidden_word_recall=True, recall_forbidden_words='["Secret"]',
                               recall_forbidden_scope="bot_and_group")
        self.assertIsNone(bot_and_group.forbidden_word_guard("secret", scope_kind="private", is_bot_output=False))
        self.assertEqual(bot_and_group.forbidden_word_guard("secret", scope_kind="group", is_bot_output=False), "Secret")
        sensitive = policy(enable_forbidden_word_recall=True, recall_forbidden_words='["Secret"]',
                           recall_forbidden_word_case_sensitive=True)
        self.assertIsNone(sensitive.forbidden_word_guard("secret"))
        self.assertEqual(sensitive.forbidden_word_guard("Secret"), "Secret")

    def test_forbidden_word_configuration_is_registered_and_validated(self):
        fields = asdict(ReplyTextPolicy())
        fields.update({"enable_forbidden_word_recall": True, "recall_forbidden_words": '["spam"]',
                       "recall_forbidden_scope": "bot_and_group"})
        validate({**asdict(SegmentationPolicy()), "streaming": True, "part_gap_ms": 0,
                  **fields})
        with self.assertRaises(ConfigurationError):
            validate({**asdict(SegmentationPolicy()), "streaming": True, "part_gap_ms": 0,
                      **fields, "recall_forbidden_scope": "invalid"})

    def test_cleanup_is_enabled_by_default_while_replacement_stays_disabled(self):
        presentation = ReplyTextPolicy()
        self.assertTrue(presentation.content_cleanup_enabled)
        self.assertFalse(presentation.content_replacement_enabled)
        self.assertEqual(presentation.clean_parts(("原文。\n",)), ("原文。",))

    def test_configured_trailing_punctuation_cleanup_preserves_internal_punctuation(self):
        presentation = ReplyTextPolicy(content_cleanup_enabled=True, content_cleanup_scope="trailing",
                                       content_cleanup_words='["。", "，"]')
        self.assertEqual(presentation.content_cleanup_mode, "words")
        self.assertEqual(presentation.content_cleanup_scope, "trailing")
        self.assertEqual(json.loads(presentation.content_cleanup_words), ["。", "，"])
        self.assertTrue(presentation.content_cleanup_enabled)
        self.assertEqual(presentation.clean_parts(("甲\n乙。",)), ("甲\n乙",))
        self.assertEqual(presentation.clean_parts(("甲，乙。丙。， \n", "丁，戊，")),
                         ("甲，乙。丙", "丁，戊"))

    def test_cleanup_scope_accepts_multiple_positions_and_chinese_aliases(self):
        presentation = ReplyTextPolicy(content_cleanup_enabled=True,
                                       content_cleanup_scope="句首、句尾",
                                       content_cleanup_words=json.dumps(["。"], ensure_ascii=False))
        self.assertEqual(presentation.cleanup_scopes, frozenset({"leading", "trailing"}))
        self.assertEqual(presentation.clean_parts(("。开头。中间。结尾。",)),
                         ("开头。中间。结尾",))
        both_edges_and_middle = ReplyTextPolicy(
            content_cleanup_enabled=True, content_cleanup_scope="leading, middle, trailing",
            content_cleanup_words=json.dumps(["。", "，"], ensure_ascii=False))
        self.assertEqual(both_edges_and_middle.cleanup_scopes,
                         frozenset({"leading", "middle", "trailing"}))
        self.assertEqual(both_edges_and_middle.clean_parts(("。开头，中间。结尾。",)),
                         ("开头中间结尾",))
        with self.assertRaises(ValueError):
            ReplyTextPolicy(content_cleanup_enabled=True, content_cleanup_scope="句首、未知")

    def test_internal_reaction_expression_metadata_is_removed_before_segmentation(self):
        metadata = ('<pc_reaction_expression>{"purpose":"轻吐槽","emotion":"无奈",'
                    '"intensity":2}</pc_reaction_expression>')
        prepared = prepare_reply("在的在的，刚刚被签到局屏蔽过去了...不是不理你啦。\n" + metadata,
                                 SegmentationPolicy(enabled=False), policy())
        self.assertEqual(prepared.visible, "在的在的，刚刚被签到局屏蔽过去了...不是不理你啦。\n")
        self.assertEqual(prepared.parts, (prepared.visible.rstrip("\n"),))
        incomplete = prepare_reply("正文。<pc_reaction_expression>{\"emotion\":\"无奈\"",
                                   SegmentationPolicy(), policy())
        self.assertEqual(incomplete.parts, ("正文。",))
        escaped = prepare_reply("正文。&lt;pc_reaction_expression&gt;{&quot;emotion&quot;:&quot;无奈&quot;}"
                                "&lt;/pc_reaction_expression&gt;", SegmentationPolicy(), policy())
        self.assertEqual(escaped.parts, ("正文。",))
        hexadecimal = prepare_reply("正文。&#x3C;pc_reaction_expression&#x3E;{}"
                                    "&#x3C;/pc_reaction_expression&#x3E;", SegmentationPolicy(), policy())
        self.assertEqual(hexadecimal.parts, ("正文。",))

    def test_internal_reaction_expression_metadata_is_removed_across_stream_chunks(self):
        protocol = InternalExpressionProtocolFilter()
        visible = "".join((
            protocol.feed("正文。<pc_reaction_"),
            protocol.feed('expression\">{\"intent\":\"内部态度，害羞\"}'),
            protocol.feed("</pc_reaction_"),
            protocol.feed("expression>后续正文。"),
            protocol.finish(),
        ))
        self.assertEqual(visible, "正文。后续正文。")

    def test_paired_control_blocks_are_removed_without_eating_delivery_sentinels(self):
        reaction = ('<pc_reaction_expression>{"purpose":"轻吐槽","emotion":"无奈",'
                    '"intensity":2}</pc_reaction_expression>')
        self.assertEqual(strip_internal_expression_protocol("正文。" + reaction + "后续"), "正文。后续")
        self.assertEqual(
            strip_internal_expression_protocol('好开心<pc_reaction_expression>{"x":1}[[PCTTS:abc123]]'),
            "好开心[[PCTTS:abc123]]")
        self.assertEqual(
            strip_internal_expression_protocol(
                '前半<pc_reaction_expression>{"x":1}<<PRIVATE_COMPANION_SPLIT>>后半'),
            "前半<<PRIVATE_COMPANION_SPLIT>>后半")
        self.assertEqual(
            strip_internal_expression_protocol(
                '好的<pc_reaction_expression>{"purpose":"用`表情`回应",'
                '"emotion":"开心","intensity":2}</pc_reaction_expression>'),
            "好的")

    def test_control_block_cleanup_handles_escaping_fullwidth_orphans_and_unknown_names(self):
        self.assertEqual(strip_internal_expression_protocol(
            "正文。&lt;pc_reaction_expression&gt;{}&lt;/pc_reaction_expression&gt;"), "正文。")
        self.assertEqual(strip_internal_expression_protocol(
            "正文。&#x3C;pc_reaction_expression&#x3E;{}"
            "&#x3C;/pc_reaction_expression&#x3E;"), "正文。")
        self.assertEqual(strip_internal_expression_protocol(
            "正文。＜pc_reaction_expression＞{}＜/pc_reaction_expression＞后续"), "正文。后续")
        self.assertEqual(strip_internal_expression_protocol(
            "正文。</pc_reaction_expression>"), "正文。")
        self.assertEqual(strip_internal_expression_protocol(
            "<pc_member_safety>{\"safe\":true}</pc_member_safety>"
            "<personality_sync>{\"ok\":true}</personality_sync>"
            "<pc_unregistered>{\"x\":1}</pc_unregistered>正文"), "正文")

    def test_control_block_cleanup_preserves_code_and_tts_but_not_prose_after_a_partial_tag(self):
        code = '示例：`<pc_reaction_expression>{"x":1}</pc_reaction_expression>` 保留'
        self.assertEqual(strip_internal_expression_protocol(code), code)
        self.assertEqual(strip_internal_expression_protocol(
            "<pc_tts>朗读</pc_tts>正文"), "<pc_tts>朗读</pc_tts>正文")
        self.assertEqual(strip_internal_expression_protocol(
            "你可以在 <thinking> 里放草稿，但不要外发。这句话很重要。"),
            "你可以在 <thinking> 里放草稿，但不要外发。这句话很重要。")

    def test_stream_filter_finish_preserves_sentinels_trapped_in_an_unclosed_block(self):
        protocol = InternalExpressionProtocolFilter()
        visible = protocol.feed('正文<pc_reaction_expression>{"x":1}[[PCTTS:abc123]]')
        self.assertEqual(visible + protocol.finish(), "正文[[PCTTS:abc123]]")

    def test_voice_wrappers_are_not_visible_when_voice_is_disabled(self):
        prepared = prepare_reply("<pc_tts>前置。</pc_tts>普通正文。", SegmentationPolicy(), policy())
        self.assertEqual(prepared.parts, ("前置。普通正文。",))
        self.assertNotIn("pc_tts", prepared.visible)
        encoded = prepare_reply("&#x3C;pc_tts&#x3E;正文。&#x3C;/pc_tts&#x3E;", SegmentationPolicy(), policy())
        self.assertEqual(encoded.parts, ("正文。",))

    def test_voice_tag_variants_remain_protected_from_content_replacement(self):
        presentation = policy(content_replacement_enabled=True,
                              content_replacements='["preserve => changed"]')
        source = "&lt;PC-TTS lang='ja'&gt;preserve\nstill spoken.</PCC_TTS>"
        self.assertEqual(presentation.replace_text(source), source)

    def test_ordered_replacements_precede_rule_length_and_support_tokens(self):
        presentation = policy(content_replacement_enabled=True,
            content_replacements=json.dumps(["hi => 你好呀", {"from": "呀", "to": "哇"},
                                              "<tab> => <space>", "删 => <empty>"]))
        segmentation = SegmentationPolicy(enabled=True, rules_enabled=True,
            threshold=500, min_segment_chars=1, max_segments=3)
        result = prepare_reply("hi。hi。\t删", segmentation, presentation)
        self.assertEqual(result.parts, ("你好哇。", "你好哇。 "))
        self.assertNotIn("格式数据", presentation.instruction)

    def test_protected_markdown_nested_quotes_urls_and_declared_facts_are_literal(self):
        protected = ('（外层 hi（内层 hi））', '“hi，原句”', 'https://host.test/hi?q=hi',
                     '```text\nhi\n```', '[hi](https://host.test/hi)', '<pc_tts>hi</pc_tts>',
                     "hi 是事实。")
        text = "hi\n\n" + "\n\n".join(protected)
        presentation = policy(content_replacement_enabled=True,
                              content_replacements='[{"from":"hi","to":"嗨"}]')
        replaced = presentation.replace_text(text, statements=(protected[-1],))
        self.assertTrue(replaced.startswith("嗨\n\n"))
        for literal in protected:
            self.assertIn(literal, replaced)

    def test_inline_media_and_url_do_not_protect_adjacent_prose(self):
        presentation = policy(content_replacement_enabled=True, content_replacements='["hi => 嗨"]')
        text = 'hi <pc_tts>hi</pc_tts> hi https://host.test/hi_name hi'
        self.assertEqual(presentation.replace_text(text),
                         '嗨 <pc_tts>hi</pc_tts> 嗨 https://host.test/hi_name 嗨')
        self.assertEqual(presentation.replace_text('**hi <pc_tts>hi</pc_tts> hi**'),
                         '**hi <pc_tts>hi</pc_tts> hi**')

    def test_cleanup_all_and_trailing_words_preserve_protected_spans_across_parts(self):
        presentation = policy(content_cleanup_enabled=True, content_cleanup_words='["。", "，"]')
        self.assertEqual(presentation.clean_parts(("甲。乙，", "（丙。", "丁，）戊。")),
                         ("甲乙", "（丙。", "丁，）戊"))
        middle = replace(presentation, content_cleanup_scope="middle")
        self.assertEqual(middle.clean_parts(("甲。乙，", "（丙。", "丁，）戊。")),
                         ("甲乙，", "（丙。", "丁，）戊。"))
        leading = replace(presentation, content_cleanup_scope="leading")
        self.assertEqual(leading.clean_parts(("。甲。乙", "，丙。", "丁，）戊。")),
                         ("甲。乙", "丙。", "丁，）戊。"))
        trailing = replace(presentation, content_cleanup_scope="trailing")
        self.assertEqual(trailing.clean_parts(("甲。乙。， \n", "丙。（丁。）", "戊。（己。）。，")),
                         ("甲。乙", "丙。（丁。）", "戊。（己。）"))
        facts = ("甲。", "乙。")
        self.assertEqual(presentation.clean_parts(facts, statements=("甲。乙。",)), facts)

    def test_words_cleanup_handles_fullwidth_and_halfwidth_entries(self):
        presentation = policy(content_cleanup_enabled=True, content_cleanup_scope="trailing",
                              content_cleanup_words=json.dumps(["。", "."]))
        self.assertEqual(presentation.clean_parts(("甲。乙。", "丙.丁.")), ("甲。乙", "丙.丁"))

    def test_empty_word_list_falls_back_to_the_configured_regex_rule(self):
        presentation = policy(content_cleanup_enabled=True, content_cleanup_scope="trailing",
                              content_cleanup_words="[]", content_cleanup_rule=r"[。.．\s]")
        self.assertEqual(presentation.clean_parts(("甲。乙。 \n", "丙.丁．")), ("甲。乙", "丙.丁"))

    def test_empty_word_list_applies_the_regex_rule_to_every_position(self):
        presentation = policy(content_cleanup_enabled=True, content_cleanup_scope="all",
                              content_cleanup_words="[]", content_cleanup_rule=r"[。.]")
        self.assertEqual(presentation.clean_parts(("甲。乙.丙",)), ("甲乙丙",))

    def test_regex_cleanup_zero_width_and_execution_budget(self):
        presentation = policy(content_cleanup_enabled=True, content_cleanup_mode="regex",
                              content_cleanup_rule=r"[。!]+")
        self.assertEqual(presentation.clean_parts(("甲。乙!!（丙!）",)), ("甲乙（丙!）",))
        self.assertEqual(replace(presentation, content_cleanup_scope="trailing").clean_parts(("甲。乙!!",)), ("甲。乙",))
        self.assertEqual(replace(presentation, content_cleanup_rule=r"(?=$)",
                                content_cleanup_scope="trailing").clean_parts(("甲。",)), ("甲。",))
        with self.assertRaises(ValueError):
            replace(presentation, content_cleanup_rule=r"(a|aa)+$").clean_parts(("a" * 2000 + "!",))

    def test_empty_parts_are_removed_without_moving_authored_speech_to_another_part(self):
        presentation = policy(content_cleanup_enabled=True, content_cleanup_scope="leading",
                              content_cleanup_words='["删", "\\n"]',
                              content_replacement_enabled=True,
                              content_replacements='["说 => 改", "语 => 换"]')
        segmentation = SegmentationPolicy(enabled=True, llm_controlled=True)
        voice = VoiceReplyPolicy(generation_mode="model_tag", segment_mode="first")
        text = f"删\n{MARKER}\n<pc_tts>语。</pc_tts>说\n{MARKER}\n后文"
        prepared = prepare_reply(text, segmentation, presentation, voice)
        self.assertEqual(prepared.parts, ("语。改", "后文"))
        self.assertEqual(prepared.kept_indices, (1, 2))
        self.assertEqual(prepared.speech(voice), (("语。", "planned"), ("", "segment_skipped")))
        derived = replace(voice, generation_mode="plain", segment_mode="each")
        self.assertEqual([entry[0] for entry in prepared.speech(derived)], list(prepared.parts))
        with self.assertRaises(ValueError):
            prepare_reply("删", segmentation, presentation)

    def test_reserved_markers_survive_replacements_until_normal_segmentation(self):
        presentation = policy(content_replacement_enabled=True,
                              content_replacements='["PRIVATE => OTHER", "甲 => 嗨"]')
        prepared = prepare_reply(f"甲\n{MARKER}\n乙", SegmentationPolicy(enabled=True, llm_controlled=True), presentation)
        self.assertEqual(prepared.parts, ("嗨", "乙"))

    def test_settings_are_complete_or_legacy_and_invalid_rules_fail_before_save(self):
        legacy = {**asdict(SegmentationPolicy()), "streaming": False, "part_gap_ms": 0}
        self.assertEqual(validate(legacy), SegmentationPolicy())
        self.assertEqual(validate({**legacy, **TEXT_DEFAULTS}), SegmentationPolicy())
        with self.assertRaises(ConfigurationError):
            validate({**legacy, "content_cleanup_enabled": True})
        invalid = ({"content_cleanup_enabled": 1}, {"content_cleanup_rule": "["},
                   {"content_cleanup_scope": "head"}, {"content_cleanup_words": '[""]'},
                   {"content_replacements": '[{"from":"a","from":"b","to":"c"}]'},
                   {"content_replacements": '[{"from":"","to":"x"}]'},
                   {"content_replacements": '["broken"]'})
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ConfigurationError):
                validate({**legacy, **TEXT_DEFAULTS, **changes})
        expansion = policy(content_replacement_enabled=True,
                           content_replacements=json.dumps([{"from": "a", "to": "b" * 500}]))
        with self.assertRaises(ValueError):
            expansion.replace_text("a" * 200)

    def test_host_and_provider_control_tag_families_are_registered(self):
        # The host injects <system_reminder> blocks itself
        # (astrbot/core/astr_main_agent.py:1033, and
        # astrbot/builtin_stars/astrbot/group_chat_context.py:33-39), and AstrBot
        # names <think> beside <thinking> as a real provider spelling
        # (astrbot/core/provider/sources/openai_source.py:862 comment, :864 regex).
        #
        # <reasoning> is deliberately NOT covered and NOT asserted here: nothing in
        # AstrBot 4.29.1 produces it.  There is no such literal anywhere in the
        # tree, and Gemini reads reasoning from the structured ``part.thought``
        # flag instead of a text tag (gemini_source.py:527-529).  Registering it
        # would be speculation.
        for spelled in (
            "<system_reminder>群聊上下文</system_reminder>正文",
            "正文<system_reminder>群聊上下文</system_reminder>",
            "&lt;system_reminder&gt;x&lt;/system_reminder&gt;正文",
            "＜system_reminder＞x＜/system_reminder＞正文",
            "<think>hmm</think>正文",
            "正文</think>",
        ):
            with self.subTest(spelled=spelled):
                self.assertEqual(strip_internal_expression_protocol(spelled), "正文")

        # Registering the family must not borrow the truncate authorisation the
        # prompt-only blocks have: an unclosed tail is prose the model still wrote.
        self.assertEqual(
            strip_internal_expression_protocol("正文<think>还没想完"),
            "正文<think>还没想完")

        # Code examples and delivery sentinels keep their existing exemptions.
        self.assertEqual(
            strip_internal_expression_protocol("看这个`<think>示例</think>`就行"),
            "看这个`<think>示例</think>`就行")
        self.assertEqual(
            strip_internal_expression_protocol("正文<think>x</think>[[PCTTS:abc123]]"),
            "正文[[PCTTS:abc123]]")

        # The long spelling that was already registered keeps working unchanged.
        self.assertEqual(
            strip_internal_expression_protocol("<thinking>hmm</thinking>正文"), "正文")


if __name__ == "__main__":
    unittest.main()
