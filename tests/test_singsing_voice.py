"""Domain contract for turn-local singing on the existing voice protocol."""

from dataclasses import replace
import unittest

from companion_v2.conversation.voice_reply import (
    VoiceReplyPolicy, authored_singing, eligible, separate_spoken, strip_voice_markup, voice_wrapper_pairing,
)


class SingSingVoiceTests(unittest.TestCase):
    def test_only_complete_uniform_model_tags_request_singing(self):
        sung = '<pc_tts style="sing">月儿弯弯。</pc_tts>'
        self.assertTrue(authored_singing(sung))
        self.assertTrue(authored_singing(sung + "\n" + sung))
        for untrusted in (
            "文字 [singsing]",
            "<pc_tts>[singsing]月儿弯弯</pc_tts>",
            '<pc_tts style="sing">[singsing]</pc_tts>',
            '<pc_tts style="sing">月儿弯弯',
            '<pc_tts style="sing">前奏<pc_tts>月儿弯弯</pc_tts>',
            '<pc_tts style="singsing">月儿弯弯</pc_tts>',
            '<pc_tts style=sing>月儿弯弯</pc_tts>',
            '<pc_tts style="sing">月儿弯弯</tts>',
            "<pc_tts>先和你道晚安</pc_tts>" + sung,
            sung + "<pc_tts>再见</pc_tts>",
            sung + "<pc_tts>[laugh]</pc_tts>",
        ):
            with self.subTest(text=untrusted):
                self.assertFalse(authored_singing(untrusted))

    def test_transport_control_does_not_leak_or_replace_existing_cue(self):
        raw = '<pc_tts style="sing">[singsing][singing]月儿弯弯</pc_tts>'
        policy = VoiceReplyPolicy(trigger_mode="adaptive")
        self.assertEqual(separate_spoken(raw, policy, singsing=True), ("月儿弯弯", ((0, "[singing]月儿弯弯"),)))
        self.assertEqual(separate_spoken(raw, policy), ("月儿弯弯", ((0, "[singsing][singing]月儿弯弯"),)))
        self.assertEqual(strip_voice_markup(raw), "月儿弯弯")
        display = "普通文字：[singsing](https://example.test/) 或 `[singsing]`。"
        self.assertEqual(strip_voice_markup(display), display)
        self.assertEqual(separate_spoken(display, policy), (display, ()))
        self.assertEqual(separate_spoken(display, policy, singsing=True), (display, ()))
        self.assertEqual(separate_spoken("[singsing]月儿弯弯", policy, singsing=True), ("月儿弯弯", ()))
        self.assertEqual(separate_spoken("[singsing]月儿弯弯", policy), ("[singsing]月儿弯弯", ()))
        self.assertEqual(strip_voice_markup("[singsing]月儿弯弯"), "[singsing]月儿弯弯")

    def test_markdown_examples_remain_literal_and_cannot_author_voice(self):
        tag = '<pc_tts style="sing">示例歌词</pc_tts>'
        policy = VoiceReplyPolicy(trigger_mode="adaptive")
        for example in (f"用法：`{tag}`。", f"双反引号：`` `{tag}` ``",
                        f"示例：\n```xml\n{tag}\n```\n", f"~~~xml\n{tag}\n~~~\n",
                        f"````xml\n```\n{tag}\n````\n", f"```xml\n{tag}",
                        f"> ```xml\n> {tag}\n> ```", f"> > ~~~\n> > {tag}\n> > ~~~",
                        f"缩进示例：\n\n    {tag}\n", f">     {tag}\n",
                        f"跨行：`<pc_tts style=\"sing\">\n示例歌词</pc_tts>`。",
                        f"跨行缩进：`<pc_tts style=\"sing\">\n    示例歌词</pc_tts>`。",
                        f"- ```xml\n  {tag}\n  ```\n",
                        f"1. 示例\n    ~~~xml\n    {tag}\n    ~~~\n",
                        f"> - ```xml\n>   {tag}\n>   ```\n"):
            with self.subTest(example=example):
                self.assertEqual(strip_voice_markup(example), example)
                self.assertEqual(separate_spoken(example, policy), (example, ()))
                self.assertEqual(voice_wrapper_pairing(example), (0, 0))
                self.assertFalse(authored_singing(example))

    def test_only_tags_outside_code_author_voice_and_keep_offsets(self):
        example = '- ```xml\n  <pc_tts>示例</pc_tts>\n  ```\n\n'
        singing = '<pc_tts style="sing">月儿弯弯。</pc_tts>'
        spoken = '<pc_tts>晚上好。</pc_tts>'
        policy = VoiceReplyPolicy(trigger_mode="adaptive")
        self.assertTrue(authored_singing(example + singing))
        self.assertFalse(authored_singing(f"`{singing}`\n" + spoken))
        self.assertEqual(separate_spoken(example + singing, policy),
                         (example + "月儿弯弯。", ((len(example), "月儿弯弯。"),)))

    def test_legacy_wrapper_can_contain_inline_code_with_literal_tags(self):
        speech = "包含行内代码 `</pc_tts> [singsing]` 的台词"
        raw = f"前缀 <pc_tts>{speech}</pc_tts>。"
        policy = VoiceReplyPolicy(trigger_mode="adaptive")
        self.assertEqual(separate_spoken(raw, policy), (f"前缀 {speech}。", ((3, speech),)))
        self.assertEqual(strip_voice_markup(raw), f"前缀 {speech}。")
        self.assertEqual(voice_wrapper_pairing(raw), (1, 0))
        self.assertFalse(authored_singing(raw))

    def test_request_alias_is_opt_in_and_keeps_probability_gate(self):
        policy = VoiceReplyPolicy(trigger_mode="request", generation_mode="model_tag")
        options = {"kind": "private", "batch_id": "turn"}
        self.assertTrue(eligible(policy, input_text="请发语音", **options))
        for text in ("请给我唱一首", "唱段歌给我听", "Could you sing me a song?"):
            with self.subTest(text=text):
                self.assertFalse(eligible(policy, input_text=text, **options))
                self.assertTrue(eligible(policy, input_text=text, allow_singing=True, **options))
                self.assertFalse(eligible(replace(policy, probability=0), input_text=text,
                                          allow_singing=True, **options))
        self.assertFalse(eligible(policy, input_text="不要给我唱一首", allow_singing=True, **options))
        self.assertFalse(eligible(policy, input_text="这个歌手很会唱歌", allow_singing=True, **options))
