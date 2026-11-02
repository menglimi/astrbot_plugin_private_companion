"""Speech planning and durable route-specific TTS cooldowns."""

from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import json
import re
import unittest

from companion_v2.conversation.segmentation import LLM_SEGMENT_MARKER, SegmentationPolicy
from companion_v2.conversation.voice_reply import (
    VoiceReplyDelivery, VoiceReplyPolicy, capability_instruction, eligible, planned_speech,
    requested_voice_language, separate_spoken, speech_candidates, speech_chunks, speech_decisions,
    strip_voice_markup, voice_policy_for_turn,
)
from companion_v2.conversation.voice_markup import (
    slice_speech, strip_performance_controls, visible_speech,
)
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.storage.runtime import StorageRuntime
from tests.test_management_access import NOW


class VoicePolicyTests(unittest.TestCase):
    def test_schema_defaults_and_trigger_modes(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(encoding="utf-8"))
        defaults = {name: item["default"] for name, item in schema["voice_reply"]["items"].items()
                    if name != "enabled"}
        self.assertEqual(defaults, asdict(VoiceReplyPolicy()))
        request = VoiceReplyPolicy(trigger_mode="request", trigger_keywords="朗读, read aloud")
        self.assertTrue(eligible(request, input_text="请朗读这句话", kind="private", batch_id="a"))
        self.assertFalse(eligible(request, input_text="普通聊天", kind="private", batch_id="a"))
        targeted = VoiceReplyPolicy(trigger_mode="request", trigger_keywords="朗读",
            primary_mention_keywords="专属词\n定向短语，ONLY ME", primary_mention_probability=1)
        self.assertEqual(targeted.primary_mention_words(), ("专属词", "定向短语", "only me"))
        self.assertTrue(eligible(targeted, input_text="请回复专属词", kind="group", batch_id="a",
                                 primary_mention=True))
        self.assertFalse(eligible(targeted, input_text="普通聊天", kind="group", batch_id="a",
                                  primary_mention=True))
        self.assertFalse(eligible(replace(targeted, primary_mention_probability=0),
            input_text="专属词", kind="group", batch_id="a", primary_mention=True))
        probability = VoiceReplyPolicy(probability=0, group_probability=1)
        self.assertFalse(eligible(probability, input_text="", kind="private", batch_id="a"))
        self.assertTrue(eligible(probability, input_text="", kind="group", batch_id="a"))
        self.assertEqual(probability.provider_for_language(), "")
        self.assertEqual(VoiceReplyPolicy(language="ja", provider_id="fallback", ja_provider_id="japanese")
                         .provider_for_language(), "japanese")
        channel_policy = VoiceReplyPolicy(language="ja", ja_provider_id="japanese",
            private_ja_provider_id="private-drama", group_ja_provider_id="group-pro")
        self.assertEqual(channel_policy.provider_for_language("private"), "private-drama")
        self.assertEqual(channel_policy.provider_for_language("group"), "group-pro")
        self.assertEqual(channel_policy.provider_for_language(), "japanese")

    def test_voice_perception_describes_best_effort_delivery_without_provider_identity(self):
        policy = VoiceReplyPolicy(language="ja", delivery_mode="voice_only", provider_id="private-provider")
        instruction = capability_instruction(policy)
        self.assertIn("目标语种日语", instruction)
        self.assertIn("退回文字", instruction)
        self.assertNotIn("private-provider", instruction)

    def test_partial_conversion_uses_authored_short_spans_and_preserves_text_delivery(self):
        policy = VoiceReplyPolicy(conversion_scope="partial", delivery_mode="voice_and_text")
        self.assertIn("无需让语音覆盖整条回复", policy.instruction)
        self.assertNotIn("各段都保持一致", policy.instruction)
        visible, spans = separate_spoken("<pc_tts>晚安。</pc_tts>附上明天的安排。", policy)
        self.assertEqual(visible, "晚安。附上明天的安排。")
        self.assertEqual(planned_speech(visible, (visible,), spans, policy), ("晚安。",))
        self.assertEqual(policy.delivery_mode, "voice_and_text")

    def test_private_and_group_cooldowns_inherit_independently(self):
        policy = VoiceReplyPolicy(cooldown_seconds=90, private_cooldown_seconds=0,
                                  group_cooldown_seconds=180)
        self.assertEqual(policy.cooldown("private"), 0)
        self.assertEqual(policy.cooldown("group"), 180)
        self.assertEqual(replace(policy, group_cooldown_seconds=-1).cooldown("group"), 90)
        for changes in ({"private_cooldown_seconds": -2}, {"group_cooldown_seconds": True},
                        {"conversion_scope": "voice_only"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(policy, **changes)

    def test_turn_language_override_is_explicit_temporary_and_selects_matching_provider(self):
        saved = VoiceReplyPolicy(language="zh", provider_id="fallback", en_provider_id="english-tts",
                                 ja_provider_id="japanese-tts")
        for text, language, provider in (
            ("这次请用英语回答我", "en", "english-tts"),
            ("请用日语朗读", "ja", "japanese-tts"),
            ("Please reply in English", "en", "english-tts"),
            ("英語で答えてください", "en", "english-tts"),
        ):
            with self.subTest(text=text):
                current = voice_policy_for_turn(saved, text)
                self.assertEqual((current.language, current.provider_for_language()), (language, provider))
        self.assertEqual(saved.language, "zh")

    def test_turn_language_override_ignores_negation_and_translation_questions(self):
        for text in (
            "不要用英语回答",
            "Don't reply in English",
            "帮我翻译成英语",
            "Translate this sentence into Japanese",
            "英语单词怎么读",
            "请解释‘用英语回答’是什么意思",
        ):
            with self.subTest(text=text):
                self.assertEqual(requested_voice_language(text), "")

    def test_translation_prompt_pairs_each_foreign_span_with_a_complete_chinese_translation(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="en", foreign_text_mode="translation")
        self.assertIn("每个语音标签后紧接", policy.instruction)
        self.assertIn("完整对应", policy.instruction)

    def test_selected_always_and_request_modes_do_not_revert_to_optional_voice(self):
        for mode in ("always", "request"):
            policy = VoiceReplyPolicy(generation_mode="model_tag", trigger_mode=mode)
            self.assertIn("完整", policy.instruction)
            self.assertNotIn("没有合适内容就不用标签", policy.instruction)
            self.assertNotIn("可选语音", capability_instruction(policy))
        self.assertIn("按语境决定是否使用语音", VoiceReplyPolicy(trigger_mode="adaptive").instruction)

    def test_v1_voice_tags_keep_foreign_speech_and_chinese_translation(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="ja", foreign_text_mode="translation")
        for source in ("<tts>おやすみ。</tts>晚安。", "&lt;tts lang='ja'&gt;おやすみ。&lt;/tts&gt;晚安。"):
            self.assertEqual(separate_spoken(source, policy), ("晚安。", ((0, "おやすみ。"),)))
            self.assertEqual(separate_spoken(source, None), ("おやすみ。晚安。", ()))
        self.assertEqual(separate_spoken("<tts>保留正文", None), ("保留正文", ()))

    def test_voice_tags_tolerate_case_separator_entity_and_orphan_variants(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="ja", foreign_text_mode="translation")
        samples = (
            ("<PC-TTS lang='ja'>おやすみ。</pcc tts>晚安。", "おやすみ。", "晚安。"),
            ("&lt;P C TTS&gt;おはよう。&#x3C;/pc_tts&#x3E;早安。", "おはよう。", "早安。"),
            ("<PCC.TTS>ありがとう。</PC_TTS>谢谢。", "ありがとう。", "谢谢。"),
        )
        for source, speech, translation in samples:
            with self.subTest(source=source):
                self.assertEqual(separate_spoken(source, policy), (translation, ((0, speech),)))
        self.assertEqual(strip_voice_markup("正文 </pcc-tts> 后续 <PC_TTS"), "正文  后续 ")

    def test_model_tag_translation_keeps_spoken_spans_with_explicit_boundaries(self):
        policy = VoiceReplyPolicy(trigger_mode="adaptive", language="ja", foreign_text_mode="translation")
        source = (f"<pc_tts>こんにちは。</pc_tts>你好。\n{LLM_SEGMENT_MARKER}\n"
                  "<pc_tts>またね。</pc_tts>再见。")
        visible, spans = separate_spoken(source, policy)
        parts = SegmentationPolicy(enabled=True, llm_controlled=True).parts(visible)
        self.assertEqual(parts, ("你好。\n", "再见。"))
        self.assertEqual(planned_speech(visible, parts, spans, policy), ("こんにちは。", "またね。"))
        self.assertEqual(planned_speech(visible, parts, spans, replace(policy, segment_mode="last")),
                         ("", "またね。"))

    def test_missing_translation_preserves_original_and_stray_tags_do_not_shift_spans(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="en", foreign_text_mode="translation")
        source = f"<pc_tts>Hello.</pc_tts>\n{LLM_SEGMENT_MARKER}"
        visible, spans = separate_spoken(source, policy)
        self.assertEqual(SegmentationPolicy(enabled=True, llm_controlled=True).parts(visible), ("Hello.\n",))
        self.assertEqual(planned_speech(visible, ("Hello.\n",), spans, policy), ("Hello.",))
        visible, _ = separate_spoken("<pc_tts>Hello.</pc_tts>English continuation.", policy)
        self.assertEqual(visible, "Hello.English continuation.")
        visible, _ = separate_spoken("<pc_tts>Hello.</pc_tts>こんにちは。", policy)
        self.assertEqual(visible, "Hello.こんにちは。")
        visible, _ = separate_spoken("<pc_tts>Hello.</pc_tts>你好。", policy)
        self.assertEqual(visible, "你好。")
        source = f"stray </pc_tts>\n{LLM_SEGMENT_MARKER}\n<pc_tts>Goodbye.</pc_tts>再见。"
        visible, spans = separate_spoken(source, policy)
        self.assertNotIn("<pc_tts>", visible)
        self.assertEqual(planned_speech(visible, SegmentationPolicy(enabled=True, llm_controlled=True).parts(visible),
                                        spans, policy)[-1], "Goodbye.")

    def test_voice_wrappers_are_removed_when_voice_is_inactive_and_malformed_tags_are_unwrapped(self):
        source = "<pc_tts locale='en'>Hello.</pc_tts> &#x3C;pc_tts&#x3E;再见。&#x3C;/pc_tts&#x3E;"
        self.assertEqual(separate_spoken(source, None), ("Hello. 再见。", ()))
        self.assertEqual(separate_spoken("<pc_tts>普通正文。", VoiceReplyPolicy()), ("普通正文。", ()))

    def test_translation_fallback_is_per_tag_even_when_earlier_tag_has_translation(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="en", foreign_text_mode="translation")
        source = ("<pc_tts>Hello.</pc_tts>你好。\n" + LLM_SEGMENT_MARKER +
                  "\n<pc_tts>Goodbye.</pc_tts>")
        visible, spans = separate_spoken(source, policy)
        parts = SegmentationPolicy(enabled=True, llm_controlled=True).parts(visible)
        self.assertEqual(parts, ("你好。\n", "Goodbye."))
        self.assertEqual(planned_speech(visible, parts, spans, policy), ("Hello.", "Goodbye."))

    def test_plain_segment_selection_and_speech_limit(self):
        parts = ("第一段。", "第二段。")
        policy = VoiceReplyPolicy(segment_mode="first", max_speech_chars=4)
        self.assertEqual(planned_speech("".join(parts), parts, (), policy), ("第一段。", ""))
        self.assertEqual(planned_speech("".join(parts), parts, (), replace(policy, max_speech_chars=3)), ("", ""))
        self.assertEqual(speech_decisions("".join(parts), parts, (), policy),
                         (("第一段。", "planned"), ("", "segment_skipped")))
        self.assertEqual(speech_decisions("".join(parts), parts, (), replace(policy, max_speech_chars=3)),
                         (("", "too_long"), ("", "segment_skipped")))
        self.assertEqual(speech_decisions("", ("",), (), VoiceReplyPolicy()), (("", "empty_part"),))
        with self.assertRaises(ValueError):
            VoiceReplyPolicy(probability=2)
        with self.assertRaises(ValueError):
            VoiceReplyPolicy(timeout_seconds=True)

    def test_performance_cues_stay_in_speech_and_leave_ordinary_body_untouched(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag")
        source = "数组[0]：<pc_tts>[温柔地]别急，<soft>慢慢来。</soft></pc_tts>参考 <soft> 的说明。"
        visible, spans = separate_spoken(source, policy)
        self.assertEqual(visible, "数组[0]：别急，慢慢来。参考 <soft> 的说明。")
        self.assertEqual(spans, ((6, "[温柔地]别急，<soft>慢慢来。</soft>"),))
        self.assertEqual(separate_spoken(source, None)[0], visible)
        translated = VoiceReplyPolicy(generation_mode="model_tag", language="ja", foreign_text_mode="translation")
        self.assertEqual(separate_spoken(
            "<pc_tts>[優しく]おやすみ。<whisper>また明日。</whisper></pc_tts>晚安，明天见。", translated),
            ("晚安，明天见。", ((0, "[優しく]おやすみ。<whisper>また明日。</whisper>"),)))

    def test_speech_chunks_balance_nested_tags_without_splitting_directions(self):
        speech = "[轻声，慢一点]今天辛苦了。<soft>好好休息。<slow>明天见。</slow></soft>"
        chunks = speech_chunks(speech, 6)
        self.assertEqual("".join(visible_speech(chunk) for chunk in chunks), visible_speech(speech))
        self.assertEqual(chunks, ("[轻声，慢一点]今天辛苦了。", "<soft>好好休息。</soft>",
                                  "<soft><slow>明天见。</slow></soft>"))
        unbroken = speech_chunks("[温柔地]你好，<soft>晚安。</soft>", 20)
        self.assertEqual(unbroken, ("[温柔地]你好，<soft>晚安。</soft>",))

    def test_pause_controls_never_reach_the_displayed_reply(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag")
        for control in ("<pause>", "<pause/>", "<pause ms=\"500\">", "</pause>", "<PAUSE>",
                        "< Breath />", "<sigh>", "<inhale/>", "<exhale>", "<silence/>"):
            with self.subTest(control=control):
                self.assertEqual(visible_speech(f"A…{control}B…"), "A…B…")
                self.assertEqual(visible_speech(f"[温柔地]{control}晚安。"), "晚安。")
        leaked = "<pc_tts>[极度羞涩，喘息微弱]A…<pause>B…</pc_tts>"
        self.assertEqual(separate_spoken(leaked, policy)[0], "A…B…")
        # The wrapper belongs to separate_spoken/strip_voice_markup; this layer
        # only has to prove the pause mark itself is gone.
        self.assertEqual(visible_speech(leaked), "<pc_tts>A…B…</pc_tts>")
        self.assertEqual(strip_voice_markup(leaked), "A…B…")
        # Delivery-only marks also leave body text that no speech span claims,
        # and every path agrees on that result.
        for source, expected in (
            ("<pc_tts>晚安。</pc_tts>[pause]明天见。", "晚安。明天见。"),
            ("<pc_tts>晚安。</pc_tts><pause>明天见。", "晚安。明天见。"),
            ("晚安。<pause>明天见。", "晚安。明天见。"),
            ("晚安。[pause]明天见。", "晚安。明天见。"),
            ("晚安。<breath/>明天见。", "晚安。明天见。"),
            ("晚安。[停顿]明天见。", "晚安。明天见。"),
        ):
            with self.subTest(source=source):
                for active in (policy, VoiceReplyPolicy(), None):
                    self.assertEqual(separate_spoken(source, active)[0], expected)
                self.assertEqual(strip_voice_markup(source), expected)
        # Wording marks outside a span stay written out: prose may cite them.
        for sample in ("参考 <soft> 的说明。", "<whisper>明天见。</whisper>", "数组[0]：别急"):
            with self.subTest(sample=sample):
                self.assertEqual(strip_voice_markup(sample), sample)
                self.assertEqual(separate_spoken(sample, None)[0], sample)

    def test_ordinary_angle_brackets_and_cues_survive_pause_stripping(self):
        body = ("我<3你呀", "a<b 且 c>d", "笑<3", "1<2>0", "<em>斜体</em>", "<p>段落</p>",
                "<br/>", '<span class="x">字</span>', '<a href="#">链接</a>', "<stop>",
                "<pausex>", "<fastfood>", "<soft 的说明", "<pause后没闭合", "&lt;p&gt;标签",
                "见[1](https://x)", "<p>我<3你</p>", "<b>粗</b>", '<div class="x">',
                "<pause a='1\n2'>x")
        for sample in body:
            with self.subTest(sample=sample):
                self.assertEqual(visible_speech(sample), sample)
        self.assertEqual(visible_speech("保留[表情包]说明"), "保留说明")

    def test_delivery_cues_leaving_body_text_cover_chinese_and_spare_prose_brackets(self):
        # A live provider lets directions be written in Chinese or in the spoken
        # language, so a Chinese pause cue is a measure, not a word.
        cues = ("停顿", "停顿一下", "稍作停顿", "换气", "吸气", "呼气", "叹息", "沉默",
                "轻声", "轻笑", "低语", "嘟囔", "小声", "温柔地", "轻柔地", "小声地")
        prose = ("数组[0]：别急", "见[1](https://x)", "保留[表情包]说明", "[表情]", "[笑]",
                 "第[一]章", "选项[A]和[B]", "价格[￥100]", "[x]")
        for cue in cues:
            source = f"晚安。[{cue}]明天见。"
            with self.subTest(cue=cue):
                self.assertEqual(strip_performance_controls(source), "晚安。明天见。")
                self.assertEqual(strip_voice_markup(source), "晚安。明天见。")
                for active in (VoiceReplyPolicy(generation_mode="model_tag"), VoiceReplyPolicy(), None):
                    self.assertEqual(separate_spoken(source, active)[0], "晚安。明天见。")
        for sample in prose:
            with self.subTest(sample=sample):
                self.assertEqual(strip_performance_controls(sample), sample)
                self.assertEqual(strip_voice_markup(sample), sample)
                self.assertEqual(separate_spoken(sample, None)[0], sample)
        # The same words without brackets are prose, and a cue that prefixes a
        # longer one must not be cut short by alternation order.
        for sample in ("他很温柔地说", "小声地说话", "低声聊", "他轻笑了一声", "温柔地", "小声"):
            with self.subTest(sample=sample):
                self.assertEqual(strip_performance_controls(sample), sample)
        self.assertEqual(strip_performance_controls("[停顿一下][小声地]"), "")

    def test_attribute_tails_do_not_need_a_quote_aware_parser(self):
        # The tail runs to the closing angle on its own line, so a value carrying
        # either quote -- or an angle -- must still be recognized, not leak.
        controls = {
            "<pause note='don't hide'>": "", '<pause note="a\'b">': "",
            "<pause note='a\"b'>": "", "<pause ms='500'/>": "", '<pause label="pause">': "",
            "<pause label='a>b'>": "", "<BREATH >": "", "</soft/>": "",
            "＜pause＞": "", "&lt;pause&gt;": "", "&#60;pause&#62;": "", "&#x3c;pause&#x3e;": "",
        }
        for control, expected in controls.items():
            with self.subTest(control=control):
                self.assertEqual(visible_speech(control), expected)
        self.assertEqual(visible_speech('<pause label="pause">正文'), "正文")
        self.assertEqual(visible_speech("<pause label='pause'>[pause]正文"), "正文")
        # A control in the tail never re-matches and never stays behind.
        self.assertEqual(visible_speech("<pause label='pause' note='pause'>x"), "x")
        self.assertEqual(visible_speech("A<whisper>轻声<pause/>继续</whisper>B"), "A轻声继续B")

    def test_an_unterminated_control_never_swallows_the_text_after_it(self):
        for source, expected in (
            ("<pause 没有闭合 <soft>正文", "<pause 没有闭合 正文"),
            ("<pause>你好 <soft>慢慢说</soft> 正文结尾", "你好 慢慢说 正文结尾"),
            ("<pause>a<soft>b</soft>c", "abc"),
            ("<pause ms=\"1\">正文</pause>后文", "正文后文"),
            ("<pause>保留这段 <p>HTML</p> 吗", "保留这段 <p>HTML</p> 吗"),
        ):
            with self.subTest(source=source):
                self.assertEqual(visible_speech(source), expected)

    def test_pause_controls_stay_paired_across_chunks_and_offsets(self):
        speech = "[轻声]今天辛苦了。<pause>明天见。<soft>好好休息。</soft>"
        chunks = speech_chunks(speech, 7)
        self.assertEqual("".join(visible_speech(chunk) for chunk in chunks), visible_speech(speech))
        for chunk in chunks:
            self.assertEqual(chunk.count("<pause>"), chunk.count("</pause>"))
        self.assertEqual(slice_speech("[轻声]今天辛苦了。<pause>明天见。", 0, 6), "[轻声]今天辛苦了。")
        self.assertEqual(visible_speech(slice_speech("<pause>ABC", 0, 2)), "AB")
        pause_first = speech_chunks("<pause>开头就停顿，后面还有一句很长的话要分成很多块才够。", 5)
        self.assertEqual("".join(visible_speech(chunk) for chunk in pause_first),
                         visible_speech("<pause>开头就停顿，后面还有一句很长的话要分成很多块才够。"))


class SpokenDoubleWriteTests(unittest.TestCase):
    """One statement written inside and outside the wrapper is shown once."""

    zh = VoiceReplyPolicy(trigger_mode="always", generation_mode="model_tag",
                          conversion_scope="full", language="zh")

    def assert_anchored(self, visible, spans):
        """Every span must sit where the displayed reply really carries its words."""
        for offset, speech in spans:
            self.assertEqual(visible[offset:offset + len(visible_speech(speech))], visible_speech(speech),
                             f"span {speech!r} is not anchored at {offset} in {visible!r}")

    def separate(self, source, policy=None):
        """Separate, then hold every span to the displayed text it claims.

        A translated wrapper is the one case where the spoken words are not on
        screen -- the translation is -- so that anchor would be meaningless.
        """
        policy = policy or self.zh
        visible, spans = separate_spoken(source, policy)
        if not (policy.language != "zh" and policy.foreign_text_mode == "translation"):
            self.assert_anchored(visible, spans)
        return visible, spans

    def test_the_two_reported_replies_display_their_statement_once(self):
        # 07:00: the wrapper holds a pause mark the body copy does not.
        paused = "[紧张地，声音很轻]A…<pause>B…"
        visible, spans = self.separate(f"<pc_tts>{paused}</pc_tts>A…B…")
        self.assertEqual(visible, "A…B…")
        self.assertEqual(spans, ((0, paused),))

    def test_the_second_reported_reply_display_its_statement_once(self):
        # 07:03, verbatim from the incident: the body writes the statement again,
        # moving it onto a new line and closing it on the interjection ``mua~``.
        # The tail attaches with no space, so it decorates the ending it follows
        # and the copy is the wrapper's statement said twice.
        smiling = "[小声娇嗔，带着软软的鼻音]A…[轻声笑，舒服地蹭了蹭]B。"
        visible, spans = self.separate(f"<pc_tts>{smiling}</pc_tts>A…\nB，mua~")
        self.assertEqual(visible, "A…B。")
        self.assertEqual(spans, ((0, smiling),))

    def test_short_words_after_a_copy_are_never_displayed_away(self):
        # These are the words an interjection list would have swallowed.
        for outer in ("走吧 he", "好 ha", "直径 mm", "长度 hm", "是的 ah", "嗯 oh"):
            with self.subTest(outer=outer):
                speech = outer.split()[0]
                visible = self.separate(f"<pc_tts>{speech}。</pc_tts>{outer}")[0]
                self.assertEqual(visible, speech + "。" + outer)
                self.assertIn(outer.split()[-1], visible)

    def test_a_copy_behind_a_mark_is_cut_on_the_mark_not_inside_it(self):
        # A cut taken from the words must never land inside the mark that carries
        # them, or the reply shows a fragment such as ``</soft>``.
        for outer in ("[轻声]晚安。明天见。", "[停顿]晚安。明天见。"):
            with self.subTest(outer=outer):
                visible = self.separate(f"<pc_tts>晚安。</pc_tts>{outer}")[0]
                self.assertEqual(visible, "晚安。明天见。")
                for fragment in ("]", "ft>", "</soft>", "[轻声]", "[停顿]"):
                    self.assertNotIn(fragment, visible)

    def test_a_body_mark_is_never_split_or_dropped_by_merging(self):
        # A paired body mark exists only in the clause. Merging would have to drop
        # it with the copy, or split the pair and leave ``</soft>`` behind, so the
        # clause is kept whole -- an occasional repeat over losing what it holds.
        for source in ("<pc_tts>晚安。</pc_tts><soft>晚安。</soft>明天见。",
                       "<pc_tts>晚安。</pc_tts>晚安。<soft>明天见。</soft>",
                       "<pc_tts>晚安。</pc_tts><whisper>晚安。</whisper>"):
            with self.subTest(source=source):
                visible = self.separate(source)[0]
                self.assertIn("明天见" if "明天见" in source else "晚安", visible)
                self.assertEqual(visible.count("<soft>"), visible.count("</soft>"))
                self.assertEqual(visible.count("<whisper>"), visible.count("</whisper>"))

    def test_a_second_sentence_after_a_differently_punctuated_copy_is_kept(self):
        # The copy changes its closing mark, and says something new afterwards.
        # Punctuation is normalized for the copy, never for the new statement.
        visible = self.separate("<pc_tts>晚安！</pc_tts>晚安！！明天见。")[0]
        self.assertEqual(visible, "晚安！！明天见。")

    def test_a_copy_that_adds_its_own_sentence_is_kept_in_full(self):
        for outer in ("A…B，我要先睡了。", "A…B。明天见。"):
            with self.subTest(outer=outer):
                policy = replace(self.zh, trigger_mode="adaptive")
                visible = self.separate(f"<pc_tts>A…[轻声笑]B。</pc_tts>{outer}", policy)[0]
                self.assertTrue(visible.startswith("A…B。"), visible)
                self.assertIn("".join(outer.split()), "".join(visible.split()))

    def test_a_wrapper_followed_by_a_second_sentence_keeps_that_sentence(self):
        visible, spans = self.separate("<pc_tts>好的。</pc_tts>我们今天先去吃饭，然后去看电影，最后回家。")
        self.assertEqual(visible, "好的。我们今天先去吃饭，然后去看电影，最后回家。")
        self.assertEqual(spans, ((0, "好的。"),))

    def test_a_partial_clause_that_carries_on_the_wrapper_keeps_the_whole_statement(self):
        # The wrapper stops mid-statement and the body finishes it: the body is
        # the only place those words are written, so nothing is dropped.
        policy = replace(self.zh, conversion_scope="partial")
        visible, spans = self.separate("<pc_tts>A…</pc_tts>A…B…", policy)
        self.assertEqual(visible, "A…B…")
        self.assertEqual(spans, ((0, "A…"),))
        # The other way round the wrapper is the full statement and the body only
        # repeats its opening; that opening is the copy, so it goes.
        policy = replace(self.zh, conversion_scope="partial")
        visible, spans = self.separate("<pc_tts>A…B…C…</pc_tts>A…\n今天真冷。", policy)
        self.assertIn("今天真冷。", visible)
        self.assertTrue(visible.startswith("A…B…C…"), visible)

    def test_a_statement_the_body_continues_is_not_a_repeat(self):
        for source, expected in (
            ("<pc_tts>别急。</pc_tts>先吃饭，吃完再说。", "别急。先吃饭，吃完再说。"),
            ("<pc_tts>嗯。</pc_tts>嗯，我在听。", "嗯。嗯，我在听。"),
            ("<pc_tts>其实我今天特别想去江边吹吹风，因为心里有点闷。</pc_tts>其实我今天想去吃火锅。",
             "其实我今天特别想去江边吹吹风，因为心里有点闷。其实我今天想去吃火锅。"),
        ):
            with self.subTest(source=source):
                self.assertEqual(self.separate(source)[0], expected)

    def test_nothing_the_body_says_is_ever_dropped(self):
        # The wrapper is only ever allowed to stand in for the copy of itself.
        # Any wording the body adds stays, and every case here has to keep the
        # words listed on the right somewhere in the displayed reply.
        cases = (
            ("<pc_tts>A…</pc_tts>A…B…", ("A…B…",)),
            ("<pc_tts>A…</pc_tts>A…B…C…", ("A…B…C…",)),
            ("<pc_tts>走吧</pc_tts>走吧 he", ("走吧", "he")),
            ("<pc_tts>走吧</pc_tts>走吧 kk", ("走吧", "kk")),
            ("<pc_tts>晚安。</pc_tts>[轻声]晚安。明天见。", ("晚安", "明天见")),
            ("<pc_tts>晚安！</pc_tts>晚安！！明天见。", ("晚安", "明天见")),
            ("<pc_tts>[极度羞涩，喘息微弱]A…<pause>B…</pc_tts>A…B…", ("A", "B")),
            ("<pc_tts>[小声娇嗔，带着软软鼻音]A…[轻声笑]B。</pc_tts>A…\nB，mua~", ("A", "B")),
        )
        for source, must in cases:
            with self.subTest(source=source):
                visible = self.separate(source)[0]
                for words in must:
                    self.assertIn(words, visible)

    def test_the_copy_is_found_next_to_the_wrapper_but_nowhere_else(self):
        # Only the clause right after the wrapper is a candidate copy. Repeating
        # the statement later in the reply is how people actually talk.
        source = "<pc_tts>晚安。</pc_tts>\n" + LLM_SEGMENT_MARKER + "\n晚安。晚安。"
        visible, spans = self.separate(source)
        self.assertEqual(visible, "晚安。\n" + LLM_SEGMENT_MARKER + "\n晚安。晚安。")
        self.assertEqual(spans, ((0, "晚安。"),))

    def test_the_displayed_reply_never_shows_a_half_mark(self):
        # Whatever the wrapper/copy shape, a mark on screen is either whole or
        # absent: a lone ``</soft>`` or ``<whisper>`` would be visible garbage.
        sources = (
            "<pc_tts>晚安。</pc_tts>晚安。<soft>明天见。</soft>",
            "<pc_tts>晚安。</pc_tts><soft>晚安。</soft>明天见。",
            "<pc_tts>晚安。</pc_tts><whisper>晚安。</whisper>",
            "<pc_tts>[轻声]晚安。<soft>明天见。</soft></pc_tts>晚安。<soft>明天见。</soft>",
            "<pc_tts>晚安。</pc_tts>[轻声]晚安，做个好梦。",
            "<pc_tts>A…</pc_tts>A…<soft>B…</soft>",
        )
        for source in sources:
            with self.subTest(source=source):
                visible = self.separate(source)[0]
                opened, closed = {}, {}
                for match in re.finditer(r"<\s*(/?)\s*([A-Za-z][\w-]*)[^>]*>", visible):
                    counted = closed if match[1] else opened
                    counted[match[2].lower()] = counted.get(match[2].lower(), 0) + 1
                self.assertEqual(opened, closed, f"half mark left in {visible!r}")

    def test_body_cues_are_stripped_while_prose_brackets_stay(self):
        for source, shown in (
            ("<pc_tts>晚安。</pc_tts>[轻声]晚安，做个好梦。", "晚安。晚安，做个好梦。"),
            ("<pc_tts>晚安。</pc_tts>[轻声]晚安。明天见。", "晚安。明天见。"),
            ("<pc_tts>晚安。</pc_tts>[停顿]明天见。", "晚安。明天见。"),
        ):
            with self.subTest(source=source):
                self.assertEqual(self.separate(source)[0], shown)
        # A citation and a link are prose, not direction.
        visible = self.separate("数组[0]：<pc_tts>[温柔地]别急。</pc_tts>参考见[1](https://x)")[0]
        self.assertIn("数组[0]", visible)
        self.assertIn("[1](https://x)", visible)
        self.assertNotIn("[温柔地]", visible)

    def test_only_named_directions_lose_their_brackets(self):
        # Brackets are ordinary punctuation. Only a word naming a delivery is a
        # direction, so everything else is shown exactly as written -- on both the
        # policy path and the path that displays a reply without a voice policy.
        for text in ("[笑]", "保留[表情包]说明", "第[一]章", "选项[A]和[B]", "[x]",
                     "价格[￥100]", "数组[0]：别急", "见[1](https://x)"):
            with self.subTest(text=text):
                self.assertEqual(separate_spoken(text, None)[0], text)
                self.assertEqual(self.separate(text)[0], text)
        for source, shown in (("[轻声]晚安。", "晚安。"), ("[轻笑]好呀。", "好呀。"),
                              ("[嘟囔]随你。", "随你。"), ("[低语]过来。", "过来。")):
            with self.subTest(source=source):
                self.assertEqual(self.separate(source)[0], shown)

    def test_a_wrapper_that_carries_extra_wording_keeps_both_copies(self):
        for source in (
            "<pc_tts>[温柔地]晚安。</pc_tts>晚安，做个好梦。",
            "<pc_tts>晚安。</pc_tts>我要睡了，明天见。",
            "<pc_tts>A…</pc_tts>A…B…",
        ):
            with self.subTest(source=source):
                policy = replace(self.zh, trigger_mode="adaptive")
                visible, spans = self.separate(source, policy)
                body = source.split("</pc_tts>", 1)[1]
                self.assertTrue(visible.endswith(body), visible)
                self.assertNotIn("<pc_tts>", visible)

    def test_every_span_is_anchored_in_a_long_multi_wrapper_reply(self):
        source = ("开场白。\n"
                  "<pc_tts>[轻声]你好呀。</pc_tts>你好呀。\n" + LLM_SEGMENT_MARKER + "\n"
                  "<pc_tts>[笑着]今天也在呢。</pc_tts>今天也在呢。" + LLM_SEGMENT_MARKER + "\n"
                  "结尾的一句。\n"
                  "<pc_tts>[停顿]明天见。</pc_tts>[停顿]明天见。")
        visible, spans = self.separate(source)
        self.assertEqual(len(spans), 3)
        self.assertEqual(visible.count("你好呀"), 1)
        self.assertEqual(visible.count("今天也在呢"), 1)
        self.assertEqual(visible.count("明天见"), 1)
        self.assertIn("结尾的一句。", visible)

    def test_translation_keeps_the_translation_as_the_visible_body(self):
        policy = replace(self.zh, language="ja", foreign_text_mode="translation")
        self.assertEqual(self.separate("<pc_tts>おやすみ。</pc_tts>晚安。", policy),
                         ("晚安。", ((0, "おやすみ。"),)))
        policy = replace(self.zh, language="en", foreign_text_mode="translation")
        self.assertEqual(self.separate("<pc_tts>Good night.</pc_tts>晚安。", policy),
                         ("晚安。", ((0, "Good night."),)))

    def test_bilingual_shows_the_foreign_text_and_its_translation_and_never_merges_them(self):
        # The two sides carry different languages, so they can never be one
        # statement written twice.
        for language, foreign in (("ja", "おやすみ。"), ("en", "Good night.")):
            with self.subTest(language=language):
                policy = replace(self.zh, language=language, foreign_text_mode="bilingual")
                self.assertEqual(self.separate(f"<pc_tts>{foreign}</pc_tts>晚安。", policy),
                                 (foreign + "晚安。", ((0, foreign),)))

    def test_a_foreign_wrapper_repeated_in_the_body_still_keeps_its_speech_span(self):
        policy = replace(self.zh, language="en", foreign_text_mode="translation")
        visible, spans = self.separate(
            "<pc_tts>[softly]Good night.</pc_tts>Good night." + LLM_SEGMENT_MARKER + "\n晚安。", policy)
        self.assertEqual(visible, "Good night." + LLM_SEGMENT_MARKER + "\n晚安。")
        self.assertEqual(spans, ((0, "[softly]Good night."),))

    def test_partial_conversion_keeps_the_text_around_its_short_spoken_clause(self):
        policy = replace(self.zh, conversion_scope="partial")
        visible, spans = self.separate("<pc_tts>晚安。</pc_tts>附上明天的安排。", policy)
        self.assertEqual((visible, spans), ("晚安。附上明天的安排。", ((0, "晚安。"),)))
        visible, spans = self.separate("<pc_tts>晚安。</pc_tts>晚安。附上明天的安排。", policy)
        self.assertEqual((visible, spans), ("晚安。附上明天的安排。", ((0, "晚安。"),)))

    def test_adaptive_reply_without_any_wrapper_is_displayed_verbatim(self):
        policy = replace(self.zh, trigger_mode="adaptive")
        visible, spans = self.separate("今天也很冷，记得多穿一点。", policy)
        self.assertEqual((visible, spans), ("今天也很冷，记得多穿一点。", ()))
        visible, spans = self.separate("<pc_tts>嗯。<pause>我在。</pc_tts>嗯。我在。", policy)
        self.assertEqual((visible, spans), ("嗯。我在。", ((0, "嗯。<pause>我在。"),)))

    def test_each_segment_of_a_multi_part_reply_keeps_its_own_speech(self):
        policy = replace(self.zh, trigger_mode="adaptive")
        source = ("<pc_tts>A。</pc_tts>A。" + LLM_SEGMENT_MARKER + "\n"
                  "<pc_tts>B。<pause>B。</pc_tts>B。B。")
        visible, spans = self.separate(source, policy)
        self.assertEqual(visible, "A。" + LLM_SEGMENT_MARKER + "\nB。B。")
        parts = SegmentationPolicy(enabled=True, llm_controlled=True).parts(visible)
        self.assertEqual(speech_candidates(visible, parts, spans, policy), ("A。", "B。<pause>B。"))
        self.assertEqual(planned_speech(visible, parts, spans, policy), ("A。", "B。<pause>B。"))

    def test_boundary_tokens_survive_the_visible_reply_and_still_split_it(self):
        # A boundary token is how the reply is split into messages later, so it
        # must stay in the visible text even while a copy beside it is dropped.
        policy = replace(self.zh, trigger_mode="adaptive")
        source = ("<pc_tts>早点睡。</pc_tts>早点睡。" + LLM_SEGMENT_MARKER + "\n"
                  "<pc_tts>晚安。</pc_tts>晚安。晚安。" + LLM_SEGMENT_MARKER + "\n"
                  "<pc_tts>明天见。</pc_tts>"
                  + LLM_SEGMENT_MARKER + "\n<pc_tts>明天见。</pc_tts>")
        visible, spans = self.separate(source, policy)
        self.assertEqual(visible.count(LLM_SEGMENT_MARKER), 3)
        parts = SegmentationPolicy(enabled=True, llm_controlled=True).parts(visible)
        self.assertEqual(parts, ("早点睡。\n", "晚安。晚安。\n", "明天见。\n", "明天见。"))
        self.assertEqual(speech_candidates(visible, parts, spans, policy),
                         ("早点睡。", "晚安。", "明天见。", "明天见。"))
        # Each span sits at its own part's words, boundary tokens aside.
        for (offset, speech), part in zip(spans, parts):
            self.assertTrue(visible[offset:].startswith(visible_speech(speech)), (offset, speech, part))
        # A copy that carries its own words keeps them: "晚安。晚安。" stays whole.
        self.assertIn("晚安。晚安。", visible)

    def test_speech_is_placed_by_visible_offset_whatever_the_directions_weigh(self):
        # The first wrapper's direction is longer than the words it hides, so a
        # source offset would land in the wrong segment; the visible offset does
        # not, because parts and spans are measured on the same text.
        policy = replace(self.zh, trigger_mode="adaptive")
        first = "[温柔地，慢慢地，声音放得很轻很轻]头一段要说的话。"
        second = "[轻声]尾一段要说的话。"
        source = (f"<pc_tts>{first}</pc_tts>头一段要说的话。"
                  + LLM_SEGMENT_MARKER + "\n"
                  + f"<pc_tts>{second}</pc_tts>尾一段要说的话。")
        visible, spans = self.separate(source, policy)
        self.assertEqual(visible, "头一段要说的话。" + LLM_SEGMENT_MARKER + "\n尾一段要说的话。")
        parts = SegmentationPolicy(enabled=True, llm_controlled=True).parts(visible)
        self.assertEqual(parts, ("头一段要说的话。\n", "尾一段要说的话。"))
        self.assertEqual(speech_candidates(visible, parts, spans, policy), (first, second))
        # The control case: matching lengths must not be what makes it pass.
        self.assertNotEqual(spans[1][0], source.index(second))

    def test_segment_selection_never_moves_a_span_to_another_part(self):
        policy = replace(self.zh, trigger_mode="adaptive")
        source = ("<pc_tts>[慢慢地]第一句。</pc_tts>第一句。"
                  + LLM_SEGMENT_MARKER + "\n<pc_tts>第二句。</pc_tts>第二句。")
        visible, spans = self.separate(source, policy)
        parts = SegmentationPolicy(enabled=True, llm_controlled=True).parts(visible)
        for mode, expected in (("each", ("[慢慢地]第一句。", "第二句。")),
                               ("first", ("[慢慢地]第一句。", "")),
                               ("last", ("", "第二句。")),
                               ("whole", ("[慢慢地]第一句。", "第二句。"))):
            with self.subTest(mode=mode):
                self.assertEqual(planned_speech(visible, parts, spans, replace(policy, segment_mode=mode)),
                                 expected)

    def test_spans_are_measured_on_the_same_text_that_is_displayed(self):
        policy = replace(self.zh, trigger_mode="adaptive")
        source = ("<pc_tts>[温柔地]第一句。</pc_tts>第一句。" + LLM_SEGMENT_MARKER + "\n"
                  "<pc_tts>第二句。<pause>第三句。</pc_tts>第二句。第三句。")
        visible, spans = self.separate(source, policy)
        self.assertEqual(visible, "第一句。" + LLM_SEGMENT_MARKER + "\n第二句。第三句。")
        self.assertEqual([offset for offset, _ in spans],
                         [0, len("第一句。" + LLM_SEGMENT_MARKER + "\n")])


class VoiceCooldownTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-v2-voice-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "data"
        self.runtime = StorageRuntime.open(data_root=self.root, installation_id="voice-test")
        self.addCleanup(self.runtime.close)
        self.time = NOW
        self.delivery = VoiceReplyDelivery(self.runtime, lambda: self.time)

    def envelope(self, part_id, *, bot="bot-a", account="100", conversation="conversation-a",
                 persona="persona-a", kind=ScopeKind.PRIVATE):
        scope = ScopeRef("voice-test", "qq-main", kind, "200", bot, account, conversation, "session-a")
        role = SubjectRef("voice-test", SubjectKind.BOT, persona)
        return NS(part_id=part_id, context=NS(scope=scope, role_binding=NS(role=role)))

    def add_plan(self, part_id, *, cooldown=90, batch_id=None, sequence=1):
        batch_id = batch_id or part_id
        with self.runtime.transaction() as connection:
            if batch_id == part_id:
                connection.execute("""INSERT INTO conversation_batches
                    (batch_id, origin_key, context_json, role_instruction, state, revision, created_at, contributions_json)
                    VALUES (?, 'origin', '{}', '', 'ready', 1, ?, '{}')""", (batch_id, NOW.isoformat()))
            connection.execute("""INSERT INTO conversation_parts
                (part_id, batch_id, sequence, text, payload_hash, state)
                VALUES (?, ?, ?, 'hello', 'hash', 'pending')""", (part_id, batch_id, sequence))
            connection.execute("""INSERT INTO conversation_voice_parts
                (part_id, speech, provider_id, delivery_mode, position, timeout_seconds, cooldown_seconds)
                VALUES (?, 'hello', '', 'voice_and_text', 'after_media', 30, ?)""", (part_id, cooldown))

    def test_cooldown_survives_restart_and_isolates_bot_account_conversation_persona_and_kind(self):
        first = self.envelope("first")
        self.add_plan("first")
        self.assertIsNotNone(self.delivery.claim(first))
        self.runtime.close()
        self.runtime = StorageRuntime.open(data_root=self.root, installation_id="voice-test")
        self.addCleanup(self.runtime.close)
        self.delivery = VoiceReplyDelivery(self.runtime, lambda: self.time)
        self.add_plan("blocked")
        self.assertIsNone(self.delivery.claim(self.envelope("blocked")))
        for part, changes in (("bot", {"bot": "bot-b"}), ("account", {"account": "101"}),
                              ("conversation", {"conversation": "conversation-b"}),
                              ("persona", {"persona": "persona-b"}),
                              ("group", {"kind": ScopeKind.GROUP})):
            self.add_plan(part)
            self.assertIsNotNone(self.delivery.claim(self.envelope(part, **changes)))
        self.time += timedelta(seconds=91)
        self.assertIsNotNone(self.delivery.claim(self.envelope("blocked")))

    def test_release_is_owner_bound_and_zero_cooldown_does_not_claim(self):
        self.add_plan("first")
        self.add_plan("second")
        first = self.delivery.claim(self.envelope("first"))
        self.assertIsNotNone(first)
        self.delivery.release(first, "second")
        self.assertIsNone(self.delivery.claim(self.envelope("second")))
        self.delivery.release(first, "first")
        self.assertIsNotNone(self.delivery.claim(self.envelope("second")))
        self.add_plan("free", cooldown=0)
        self.assertNotIn("cooldown_key", self.delivery.claim(self.envelope("free")))

    def test_each_segment_in_same_reply_keeps_voice_while_next_reply_cools_down(self):
        self.add_plan("first")
        self.add_plan("next-part", batch_id="first", sequence=2)
        self.add_plan("next-reply")
        first = self.delivery.claim(self.envelope("first"))
        following = self.delivery.claim(self.envelope("next-part"))
        self.assertIsNotNone(first)
        self.assertIsNotNone(following)
        self.assertNotIn("cooldown_key", following)
        self.delivery.release(following, "next-part")
        self.assertIsNone(self.delivery.claim(self.envelope("next-reply")))
