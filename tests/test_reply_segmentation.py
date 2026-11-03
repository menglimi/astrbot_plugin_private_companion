from dataclasses import replace
import json
import unittest

from companion_v2.conversation.segmentation import SegmentationPolicy, LLM_SEGMENT_MARKER as MARKER


class ReplySegmentationTests(unittest.TestCase):
    def setUp(self):
        self.policy = SegmentationPolicy(enabled=True, llm_controlled=True, rules_enabled=False)

    def test_explicit_boundaries_keep_order_and_all_ordinary_text(self):
        self.assertEqual(self.policy.parts(f"第一段\n{MARKER}\n第二段"), ("第一段\n", "第二段"))
        self.assertEqual(self.policy.parts("第一行\n\n第二行"), ("第一行\n\n第二行",))
        for source in ("  原文\r\n第二行\r\n", "emoji 👩‍💻，café。", "普通。句子！"):
            self.assertEqual("".join(self.policy.parts(source)), source)

    def test_v1_edge_markers_recover_but_inline_markers_only_clean(self):
        for source in (f"甲{MARKER}\n乙", f"甲\n{MARKER}乙", f"甲\n{MARKER}\n乙"):
            self.assertEqual(tuple(p.strip() for p in self.policy.parts(source)), ("甲", "乙"))
        for token in (MARKER, "<< private_companion_split >>", "{{ split_marker }}",
                      "&lt;&lt;PRIVATE_COMPANION_SPLIT&gt;&gt;", "&#x3C;&#x3C;PRIVATE_COMPANION_SPLIT&#x3E;&#x3E;",
                      r"\<\<PRIVATE_COMPANION_SPLIT\>\>"):
            self.assertEqual(self.policy.parts("hello" + token + "world"), ("hello world",))

    def test_repeated_leading_trailing_markers_never_send_empty_messages(self):
        source = f"{MARKER}\n{MARKER}\n甲\n{MARKER}\n{MARKER}\n乙\n{MARKER}\n"
        self.assertEqual(self.policy.parts(source), ("甲\n", "乙\n"))
        with self.assertRaises(ValueError):
            self.policy.parts(f"{MARKER}\n{MARKER}")

    def test_disabled_and_llm_disabled_still_remove_control_tokens(self):
        for policy in (SegmentationPolicy(), replace(self.policy, llm_controlled=False)):
            self.assertEqual(policy.parts(f"甲\n{MARKER}\n乙"), ("甲\n乙",))

    def test_rule_only_prompt_injects_nothing(self):
        policy = replace(self.policy, llm_controlled=False,
                         prompt="只在需要时用 {{ split_marker }}。")
        self.assertEqual(policy.instruction, "")
        self.assertNotIn(MARKER, policy.instruction)
        self.assertNotIn("split_marker", policy.instruction)
        self.assertEqual(replace(policy, enabled=False).instruction, "")

    def test_maximum_merges_tail_without_truncating(self):
        source = f"\n{MARKER}\n".join(["甲", "乙", "丙", "丁"])
        for maximum in (1, 2, 3):
            parts = replace(self.policy, max_segments=maximum).parts(source)
            self.assertEqual(len(parts), maximum)
            self.assertEqual("".join(parts), "甲\n乙\n丙\n丁")

    def test_fences_quotes_lists_tables_links_and_lazy_lines_stay_atomic(self):
        # Only a fence or a quote keeps the marker as literal text (V1's rule).
        blocks = (f"````text\n```\n{MARKER}\n代码。\n````",
                  f"~~~\n{MARKER}\n代码！\n~~~", f"```\n{MARKER}\n未闭合代码。",
                  f"> 引用。\n> {MARKER}\n懒续行。",
                  "|A|B|\n|---|---|\n|甲。|乙。|", "保留 `a。b` 和 **重点。**。",
                  "[一个链接。](https://example.com/a.b)", "保留 *强调？* 和 _斜体。_。",
                  "A | B\n---|---\n甲。|乙。", "保留 <em>强调。</em> 的格式。")
        policy = replace(self.policy, rules_enabled=True, threshold=500, min_segment_chars=1, max_segments=8)
        for block in blocks:
            with self.subTest(block=block):
                parts = policy.parts(block)
                self.assertEqual(len(parts), 1)
                self.assertNotIn(MARKER, parts[0])
                self.assertEqual(parts[0], block.replace(MARKER, ""))
        source = "开场一。开场二。\n\n" + blocks[0] + "\n\n结尾。"
        parts = policy.parts(source)
        self.assertTrue(any(blocks[0].replace(MARKER, "") in p for p in parts))
        self.assertEqual("".join(parts), source.replace(MARKER, ""))

    def test_a_marker_line_is_a_boundary_unless_fenced_or_quoted(self):
        """V1's rule: indentation never removes a boundary, fences and quotes do."""
        policy = replace(self.policy, threshold=1, min_segment_chars=1, max_segments=8)
        listed = f"- 列表。\n  {MARKER}\n  后续。"
        self.assertEqual(policy.parts(listed), ("- 列表。\n", "  后续。"))
        quoted = f"> 引用。\n> {MARKER}\n懒续行。"
        self.assertEqual(policy.parts(quoted), (quoted.replace(MARKER, ""),))
        fenced = f"```\n{MARKER}\n代码。\n```"
        self.assertEqual(policy.parts(fenced), (fenced.replace(MARKER, ""),))

    def test_rules_respect_threshold_minimum_and_model_priority(self):
        policy = replace(self.policy, rules_enabled=True, threshold=500, min_segment_chars=1, max_segments=3)
        self.assertEqual(policy.parts("甲。乙！丙？丁。"), ("甲。", "乙！", "丙？丁。"))
        self.assertEqual(policy.parts(f"开场一。\n{MARKER}\n乙一。乙二。乙三。"),
                         ("开场一。\n", "乙一。", "乙二。乙三。"))
        self.assertEqual(replace(policy, min_segment_chars=3).parts("甲。乙。丙。丁。"), ("甲。乙。", "丙。丁。"))
        self.assertEqual(replace(policy, threshold=3).parts("甲。乙。"), ("甲。乙。",))
        self.assertEqual(replace(policy, rule_mode="paragraphs").parts("甲。乙。\n\n丙。"), ("甲。乙。\n\n", "丙。"))

    def test_rule_boundaries_are_balanced_within_the_segment_limit(self):
        policy = replace(self.policy, llm_controlled=False, threshold=500,
                         min_segment_chars=1, max_segments=5)
        source = "甲。乙。丙。丁。戊。己。庚。辛。壬。癸。"
        parts = policy.parts(source)
        self.assertEqual(parts, ("甲。乙。", "丙。丁。", "戊。己。", "庚。辛。", "壬。癸。"))
        self.assertEqual("".join(parts), source)

    def test_excess_model_boundaries_do_not_leave_one_long_tail(self):
        source = f"\n{MARKER}\n".join(("先接住。", "再回应对方。", "补充原因。", "接着说一点。",
                                             "再补一层。", "收住这一段。", "回应后续问题。", "最后自然结束。"))
        parts = replace(self.policy, max_segments=4).parts(source)
        self.assertEqual(len(parts), 4)
        self.assertEqual("".join(parts), source.replace(f"\n{MARKER}\n", "\n"))
        lengths = tuple(sum(not char.isspace() for char in part) for part in parts)
        self.assertLessEqual(max(lengths) - min(lengths), 20)

    def test_legacy_rules_switch_cannot_disable_rule_completion(self):
        legacy = replace(self.policy, rules_enabled=False, threshold=500,
                         min_segment_chars=1, max_segments=3)
        self.assertEqual(legacy.parts("第一句。第二句！第三句？"),
                         ("第一句。", "第二句！", "第三句？"))

    def test_v1_rule_length_ceiling_short_reply_merging_and_lossless_content(self):
        words = json.dumps(["，", "。", "\n"], ensure_ascii=False)
        policy = replace(self.policy, rules_enabled=True, threshold=500,
                         split_words=words, min_segment_chars=5, max_segments=5)
        source = "我刚才趴在窗边看了一会儿雨，玻璃上全是细细的水线。忽然想起你说今天会很忙，所以来轻轻报个到。"
        parts = policy.parts(source)
        self.assertEqual(tuple(part.strip() for part in parts), (
            "我刚才趴在窗边看了一会儿雨，", "玻璃上全是细细的水线。",
            "忽然想起你说今天会很忙，", "所以来轻轻报个到。"))
        self.assertEqual("".join(parts), source)
        self.assertEqual(replace(policy, threshold=20).parts(source), (source,))

        marked = f"嗯。\n{MARKER}\n我刚才去买咖啡了。\n{MARKER}\n哈哈！\n{MARKER}\n还有点小蛋糕。"
        merged = replace(self.policy, llm_controlled=False, rules_enabled=True, threshold=500).parts(marked)
        self.assertEqual(tuple(part.strip() for part in merged),
                         ("嗯。\n我刚才去买咖啡了。", "哈哈！\n还有点小蛋糕。"))
        self.assertEqual("".join(merged), marked.replace(f"\n{MARKER}\n", "\n"))

    def test_adjacent_punctuation_and_commas_respect_the_configured_minimum(self):
        policy = replace(self.policy, llm_controlled=False, threshold=500,
                         split_words=json.dumps(["。", "？", "！", "?", ".", "!", "，"], ensure_ascii=False),
                         min_segment_chars=5, max_segments=5)
        source = "笨蛋比折大人，大半夜的突然说什么呢...？真那么想看的话，真就只能看一眼哦？"
        parts = policy.parts(source)
        self.assertEqual(tuple(part.strip() for part in parts), (
            "笨蛋比折大人，", "大半夜的突然说什么呢...？",
            "真那么想看的话，", "真就只能看一眼哦？"))
        self.assertEqual("".join(parts), source)
        self.assertFalse(any(part.lstrip().startswith(("？", "！", "。")) for part in parts[1:]))
        self.assertEqual(replace(policy, min_segment_chars=10).parts(source), (
            "笨蛋比折大人，大半夜的突然说什么呢...？",
            "真那么想看的话，真就只能看一眼哦？"))

    def test_short_interjection_merges_but_configured_comma_splits_the_reply(self):
        policy = SegmentationPolicy(split_words=json.dumps(["，", "…", "."], ensure_ascii=False),
                                    min_segment_chars=5)
        source = "哼，才不笨呢，比折大人才是大笨蛋…"
        self.assertEqual(policy.parts(source), ("哼，才不笨呢，", "比折大人才是大笨蛋…"))
        self.assertEqual(replace(policy, min_segment_chars=10).parts(source), (source,))
        self.assertEqual(replace(policy, min_segment_chars=1).parts("1,000.50"), ("1,000.50",))

    def test_english_abbreviations_and_domains_do_not_become_a_separate_segment(self):
        policy = SegmentationPolicy(split_words=json.dumps(["。", "？", "！", "?", ".", "!"], ensure_ascii=False),
                                    min_segment_chars=1, max_segments=6)
        source = "例如 e.g. 这个例子，域名是 example.com，版本是 U.S. 版，都没问题。"
        parts = policy.parts(source)
        self.assertEqual("".join(parts), source)
        self.assertTrue(all(part.strip() not in ("e.g.", "example.com", "U.S.") for part in parts), parts)
        self.assertIn("e.g.", "".join(parts))
        self.assertIn("example.com", "".join(parts))
        self.assertIn("U.S.", "".join(parts))

    def test_explicit_boundary_before_and_after_markdown_without_blank_lines(self):
        for block in ("```python\nprint('x。')\n```", "> 引用。\n> 后续。", "- 第一项\n- 第二项"):
            source = f"开头\n{MARKER}\n{block}\n{MARKER}\n结尾"
            self.assertEqual(self.policy.parts(source), ("开头\n", block + "\n", "结尾"))

    def test_opening_quote_never_ends_a_part(self):
        # 2026-10-25 现场：一条回复被切成「不过你之前那句“」+「生日结束也就
        # 意味着什么”，话才说一半呢」，上一段以一个孤零零的开引号结尾。
        # 原因是默认分段词里带着开引号，断点落在它之后。
        policy = SegmentationPolicy(split_words=json.dumps(["。", "，", "“"], ensure_ascii=False),
                                    min_segment_chars=2, max_segments=6)
        source = "今天是周日下午，农历九月十六呀。 不过你之前那句“生日结束也就意味着什么”，话才说一半呢。"
        parts = policy.parts(source)
        self.assertEqual("".join(parts), source)
        self.assertTrue(all(not part.rstrip().endswith("“") for part in parts), parts)
        quoted = next(part for part in parts if "生日结束" in part)
        self.assertIn("那句“", quoted, parts)

    def test_default_split_words_do_not_split_inside_a_quote(self):
        from companion_v2.conversation.segmentation import DEFAULT_SPLIT_WORDS
        self.assertNotIn("“", DEFAULT_SPLIT_WORDS)
        self.assertNotIn(".", DEFAULT_SPLIT_WORDS)
        self.assertNotIn("）", DEFAULT_SPLIT_WORDS)
        self.assertNotIn(" ", DEFAULT_SPLIT_WORDS)
        source = "不过你之前那句“生日结束也就意味着什么”，话才说一半呢。"
        parts = self.policy.parts(source)
        self.assertEqual("".join(parts), source)
        quoted = next(part for part in parts if "生日结束" in part)
        self.assertIn("那句“", quoted, parts)
        self.assertIn("什么”", quoted, parts)

    def test_legacy_default_word_list_migrates_but_custom_dots_are_kept(self):
        from companion_v2.conversation.segmentation import (
            DEFAULT_SPLIT_WORDS, LEGACY_DEFAULT_SPLIT_WORDS,
        )
        legacy = SegmentationPolicy(split_words=json.dumps(LEGACY_DEFAULT_SPLIT_WORDS, ensure_ascii=False))
        self.assertEqual(tuple(json.loads(legacy.split_words)), DEFAULT_SPLIT_WORDS)
        variant = SegmentationPolicy(split_words=json.dumps(
            [*LEGACY_DEFAULT_SPLIT_WORDS, "，", "\n", " "], ensure_ascii=False))
        self.assertNotIn(".", json.loads(variant.split_words))
        self.assertNotIn("）", json.loads(variant.split_words))
        self.assertNotIn(" ", json.loads(variant.split_words))
        custom = SegmentationPolicy(split_words=json.dumps(["。", ".", "）", "!!"], ensure_ascii=False))
        self.assertEqual(json.loads(custom.split_words), ["。", ".", "）", "!!"])

    def test_prompt_uses_v1_placeholder_and_policy_validates_types(self):
        policy = replace(self.policy, prompt="只在需要时用 {{ split_marker }}。")
        self.assertIn(f"只在需要时用 {MARKER}。", policy.instruction)
        self.assertNotIn("{{", policy.instruction)
        self.assertNotIn("消息正文组织", self.policy.instruction)
        self.assertNotIn("称呼要和所在的完整句子放在一起", self.policy.instruction)
        self.assertIn("不要借此删改、概括或重排原文", self.policy.instruction)
        self.assertIn("代码、引用、列表、表格、链接", self.policy.instruction)
        for changes in ({"enabled": 1}, {"max_segments": True}, {"max_segments": 65},
                        {"threshold": 0}, {"rule_mode": "unknown"}, {"prompt": "x" * 4001}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(policy, **changes)

    def test_default_prompt_keeps_one_connected_reply_together(self):
        instruction = replace(self.policy, prompt="").instruction
        self.assertIn("一次完整回应放在一个气泡里", instruction)
        self.assertIn("不按每个句号逐条发送", instruction)
