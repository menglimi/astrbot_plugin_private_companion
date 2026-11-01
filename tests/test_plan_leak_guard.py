"""Plan-text leak guard: five real traces, English idioms, quotes and code fences."""

import unittest

from companion_v2.conversation.reply_preparation import prepare_reply
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.text_processing import (
    MAX_TEXT, is_isolated_protocol_token, plan_leak_guard, text_policy,
)

# 上游模型把隐藏的计划写进普通正文的五轮样本（只保留模型生成的计划与台词，
# 不含用户消息、账号或个人路径）。每轮的第一条气泡都以孤立协议词开头；
# 样本逐字取自一次生产回复的落库原文，共 7 条 payload
# （129 / 138 / 94 / 8 / 96 / 90 / 276 字符），05:52 是 276 字原文。
REAL = {
    "02:25 a": (
        "hthought\n Let's craft a concise, warm-yet-tsundere response in Kazusa's voice, with `"
        "什么多谢款待啊，别把别人的声音当成饭后甜点一样说好不好……嘛，既然满意了就赶紧睡吧，晚安"),
    "02:25 b": (
        "` in Japanese following the rules (Drama 3 tags, no reaction tag per "
        "`<companion_v2_native_reactions>`). Let's call `newapi_emit_text_v1`."),
    "02:29": (
        "hthought\n 呜……抱歉，刚在被炉里半梦半醒犯迷糊，不小心冒出奇怪的梦话了。我刚才是在吐槽你说什么"
        "多谢款待啊，别把别人的声音当成饭后甜点一样说好不好……嘛，既然满意了就赶紧睡吧，晚安"),
    "05:46": (
        "hthought\n Let's call `newapi_emit_text_v1` now. 唔……干嘛突然连着喊三遍啊，不准说我是笨蛋。"
        "我正缩在春希旁边暖烘烘地打盹呢，少随便偷我的台词用啊"),
    "05:51": (
        "hthought\n Let's call `newapi_emit_text_v1`. 什么可爱啊，别乱用这种词形容我。"
        "你明明就是在拿我刚在被炉里睡迷糊说错话的事寻开心吧，少管我啦"),
    "05:52": (
        "hthought\n Let's call `newapi_emit_text_v1`. Note that `<companion_v2_native_reactions>` "
        "says: \"本轮不使用图库表情：不要调用 reaction.search，也不要输出表情意图标签；按普通文字回复，"
        "不要向用户解释这条规则。\" So no `<pc_reaction_expression>` tag this turn! "
        "Let's emit the text. 喂，谁是你妈妈啊，我现在还缩在春希旁边的被炉里迷迷糊糊打盹呢，"
        "大清早的少用这种奇怪的称呼叫我啦"),
}

EXPECTED = {
    "02:25 a": "什么多谢款待啊，别把别人的声音当成饭后甜点一样说好不好……嘛，既然满意了就赶紧睡吧，晚安",
    "02:25 b": "",
    "02:29": (
        "呜……抱歉，刚在被炉里半梦半醒犯迷糊，不小心冒出奇怪的梦话了。我刚才是在吐槽你说什么多谢款待啊，"
        "别把别人的声音当成饭后甜点一样说好不好……嘛，既然满意了就赶紧睡吧，晚安"),
    "05:46": "唔……干嘛突然连着喊三遍啊，不准说我是笨蛋。我正缩在春希旁边暖烘烘地打盹呢，少随便偷我的台词用啊",
    "05:51": "什么可爱啊，别乱用这种词形容我。你明明就是在拿我刚在被炉里睡迷糊说错话的事寻开心吧，少管我啦",
    "05:52": (
        "喂，谁是你妈妈啊，我现在还缩在春希旁边的被炉里迷迷糊糊打盹呢，大清早的少用这种奇怪的称呼叫我啦"),
}

# 正常回复：闸门必须一个字都不动。含英文习语、引用他人英文、中英混排、
# 反引号工具名、成对语音/表情标签、代码块、URL、单词回复，以及"以 cot/thought/
# thinking 结尾但其实是普通英文词"的整条回复（闸门只认精确中转标记，不做后缀匹配）。
BENIGN = {
    "英文习语": "Let's call it a day. 今天到此为止吧。",
    "英文约定": "Let's meet at eight. 好呀，八点见。",
    "引用用户英文": "你刚才说 “Let's call it off” 对吧？我记下了。",
    "中文提到工具": "我刚刚用 reaction.search 找了一圈。",
    "成对语音标签": "<pc_tts>おやすみ</pc_tts>晚安，好好睡。",
    "代码块": "```bash\n# Let's call `parse_args()` first\nmake test\n```\n上面这段先解析参数，再跑测试。",
    "URL": "文档在 https://example.com/plan?x=1 这里，自己看吧。",
    "单词回复": "Okay",
    "单词行加正文": "Okay\n好，那就这样。",
    "纯英文 note that": "Note that the cache is cleared every hour.",
    "双语 note that": "Note that the server restarts at 3am. 记得三点前提交。",
    "纯英文 call 标识符": "Let's call `stripe` after the invoice is paid. That should sync everything.",
    "双语 use 命令": "Let's use `git stash` before switching branches. 别急着切，先把改动 stash 起来。",
    "双语 invoke": "Invoke `reset_state` if you see this error. 看到这个报错就调它。",
    "纯英文 use 命令": "Use `pip install -r requirements.txt` first, then run pytest.",
    "so no tag": "so no `--force` tag here? 那就别加 force。",
    "要求用英文": "Let's answer in English only, as you asked. 好，那我就用英文。",
    "纯英文计划口吻": "Let's craft the query with a WHERE clause. That keeps the scan small.",
    "中英混排": "Sure. The build passed. 不过日志里还有一个 warning，你看到了吗？",
    "歌词": "♪ Let's dance in the dark ♪ 哎，这歌真上头。",
    "反引号工具名": "你给的命令里 `emit_text` 那行有问题，参数少了一个。",
    "提到表情标签": "`<pc_reaction_expression>` 这个标签会被程序吃掉，所以你只看到文字。",
    "成对表情标签": (
        '<pc_reaction_expression>{"purpose":"轻吐槽","emotion":"无奈",'
        '"intensity":2}</pc_reaction_expression>哼，才不笨呢。'),
    "已登记控制标签": '正文。<private_companion_context>{"a":1}</private_companion_context>你好',
    "分段标记": "<<PRIVATE_COMPANION_SPLIT>>",
    "分段标记小写带空格": "<< private_companion_split >>",
    "中文正文": "知道呀～就是机器人内部给这条申请生成的唯一编号，方便后台批准或驳回时对上号。",
    # 假阳性族：以 cot / thought / thinking 结尾的普通英文词，一个字都不能动。
    "ascot": "ascot",
    "apricot": "apricot",
    "mascot": "mascot",
    "afterthought": "afterthought",
    "unthinking": "unthinking",
    "单词 thought": "thought",
    "单词行 ascot 加正文": "ascot\n这套衣服配这个领带挺好看的。",
    "afterthought 加正文": "afterthought\n对了，明天记得带伞。",
}


class PlanLeakGuardTests(unittest.TestCase):
    def test_real_traces_keep_only_the_reply_body(self):
        for label, text in REAL.items():
            with self.subTest(sample=label):
                cleaned, spans, reasons = plan_leak_guard(text)
                self.assertEqual(cleaned, EXPECTED[label])
                self.assertTrue(spans)
                self.assertTrue(reasons)
                for marker in ("hthought", "Let's", "newapi_emit", "Note that", "So no",
                               "companion_v2_native_reactions"):
                    self.assertNotIn(marker, cleaned)

    def test_standalone_protocol_token_is_dropped(self):
        # 02:29 那轮「hthought」被切成一条单独气泡；整段纯计划时返回空串，
        # 由 note_removed_text 走「全部被清掉 → 不发」的既有路径。
        self.assertEqual(plan_leak_guard("hthought")[0], "")
        self.assertEqual(plan_leak_guard("hthought\n")[0], "")
        self.assertEqual(plan_leak_guard("hthought\r\n 抱歉。")[0], "抱歉。")

    def test_pure_plan_segment_is_dropped(self):
        self.assertEqual(plan_leak_guard(REAL["02:25 b"])[0], "")
        self.assertEqual(plan_leak_guard("Let's call `newapi_emit_text_v1` now.")[0], "")

    def test_unclosed_voice_tag_belongs_to_the_voice_path(self):
        # 未闭合 <pc_tts> 是语音链路的确认缺陷，本闸门不越权处理。
        text = "<pc_tts>なにがごちそうさまよ、人の声をデザートみたいに言わないでよね……おやすみ"
        self.assertEqual(plan_leak_guard(text), (text, (), ()))

    def test_strong_marker_unlocks_the_weak_plan_clause(self):
        plan = "hthought\n Let's craft a warm reply. 你好呀。"
        self.assertEqual(plan_leak_guard(plan)[0], "你好呀。")
        # 没有强标记时弱标记只记日志，一个字都不动。
        weak_only = "Let's craft a warm reply. 你好呀。"
        self.assertEqual(plan_leak_guard(weak_only)[0], weak_only)

    def test_semantic_thought_words_need_context(self):
        # 闸门只认精确中转标记；thought/thinking/… 是普通英文词，只有本条里另有
        # 弱标记或中日文正文时才按协议词处理（不做任何后缀匹配）。
        for text in ("thought", "reasoning", "scratchpad", "cot"):
            self.assertEqual(plan_leak_guard(text)[0], text)
        english = "Thinking\nWait, the cache is cleared every hour."
        self.assertEqual(plan_leak_guard(english)[0], english)
        self.assertEqual(plan_leak_guard("thought\n Let's craft a warm reply. 你好呀。")[0], "你好呀。")
        self.assertEqual(plan_leak_guard("thinking\n嗯，让我想想。")[0], "嗯，让我想想。")
        for text in ("ascot", "apricot", "mascot", "afterthought", "unthinking"):
            self.assertEqual(plan_leak_guard(text)[0], text)

    def test_paired_control_tags_survive_a_plan_prefix(self):
        # 计划前缀 + 成对语音标签：开标签不能被删，孤立 </pc_tts> 与朗读原文
        # 都不能漏成可见正文。
        tts = plan_leak_guard("hthought\n Let's call `newapi_emit_text_v1`.\n"
                              "<pc_tts>おやすみ</pc_tts>晚安，好好睡。")[0]
        self.assertEqual(tts, "<pc_tts>おやすみ</pc_tts>晚安，好好睡。")
        self.assertNotIn("</pc_tts>", tts.replace("<pc_tts>おやすみ</pc_tts>", ""))
        reaction = plan_leak_guard(
            'hthought\n plan. <pc_reaction_expression>{"emotion":"无奈"}'
            '</pc_reaction_expression>哼，才不笨呢。')[0]
        self.assertEqual(
            reaction,
            '<pc_reaction_expression>{"emotion":"无奈"}</pc_reaction_expression>哼，才不笨呢。')
        self.assertEqual(reaction.count("pc_reaction_expression"), 2)

    def test_quoted_or_backticked_reply_body_is_kept(self):
        # 计划把正文用引号/反引号包住时，删除后整段为空会让整轮静默；此时保留
        # 引号内（含中日文）的正文。
        self.assertEqual(plan_leak_guard('hthought\n Let\'s reply "晚安，做个好梦".')[0],
                         "晚安，做个好梦")
        self.assertEqual(plan_leak_guard("hthought\n Let's reply `晚安，做个好梦`.")[0],
                         "晚安，做个好梦")
        self.assertEqual(plan_leak_guard("hthought\n Let's reply 「おやすみ、いい夢を」.")[0],
                         "おやすみ、いい夢を")
        # 引号里没有中日文时仍然是纯计划：整轮不发。
        self.assertEqual(plan_leak_guard('hthought\n Let\'s reply "good night".')[0], "")

    def test_multiple_plan_segments_are_all_removed(self):
        # 第二段计划（被成对语音标签隔开）同样要删，且标签保持完整。
        self.assertEqual(plan_leak_guard(
            "hthought\n plan. <pc_tts>おやすみ</pc_tts>hthought\n plan2. 晚安。")[0],
            "<pc_tts>おやすみ</pc_tts>晚安。")
        # 正文之后又出现的计划段不会连正文一起吞掉，也不会原样下发。
        text = "hthought\n plan. 晚安。\nhthought\n plan2. 再聊。"
        cleaned = plan_leak_guard(text)[0]
        self.assertEqual(cleaned, "晚安。\n再聊。")
        self.assertNotIn("hthought", cleaned)
        self.assertEqual(plan_leak_guard(cleaned)[0], cleaned)

    def test_recognized_reply_bodies_are_kept_verbatim(self):
        for label, text in BENIGN.items():
            with self.subTest(sample=label):
                cleaned, spans, reasons = plan_leak_guard(text)
                self.assertEqual(cleaned, text)
                self.assertEqual(spans, ())
                self.assertTrue(all(reason.startswith("weak_only:") for reason in reasons))

    def test_internal_section_name_in_body_only_loses_the_name(self):
        # 正文里提到内部段落名时只删这个名字：句子与正文都留下，且可在日志里查到。
        cleaned, spans, reasons = plan_leak_guard("刚才那个 `<companion_v2_native_reactions>` 你其实看不见。")
        self.assertEqual(cleaned, "刚才那个 你其实看不见。")
        self.assertIn("inline_mention", reasons)
        self.assertEqual(spans, ((5, 38),))

    def test_fenced_code_and_url_survive_inside_the_removed_interval(self):
        fenced = plan_leak_guard("hthought\n Let's call `newapi_emit_text_v1`.\n```py\nx = 1\n```\n你好呀。")[0]
        self.assertEqual(fenced, "```py\nx = 1\n``` 你好呀。")
        linked = plan_leak_guard("hthought\n Let's read https://example.com/spec first. 唔，那我先睡了。")[0]
        self.assertEqual(linked, "https://example.com/spec 唔，那我先睡了。")

    def test_guard_is_idempotent(self):
        samples = (*REAL.values(), *BENIGN.values(),
                   '<pc_tts>おやすみ</pc_tts>晚安。', "晚安。\n再聊。", "晚安，做个好梦")
        for text in samples:
            with self.subTest(head=text[:20]):
                once = plan_leak_guard(text)[0]
                self.assertEqual(plan_leak_guard(once)[0], once)

    def test_bounded_and_typed_input_is_returned_unchanged(self):
        huge = "hthought\n" + "x" * (MAX_TEXT + 1)
        self.assertEqual(plan_leak_guard(huge), (huge, (), ()))
        for value in (None, 3, "", "   "):
            cleaned, spans, reasons = plan_leak_guard(value)
            self.assertEqual(cleaned, value)
            self.assertEqual((spans, reasons), ((), ()))

    def test_isolated_protocol_token_only_matches_an_exact_relay_marker(self):
        for text in ("hthought", "hthought\n", "  hthinking  ", "hcot"):
            self.assertTrue(is_isolated_protocol_token(text), text)
        for text in ("thought", "reasoning", "okay", "Okay", "ascot", "apricot", "mascot",
                     "afterthought", "unthinking", "cot", "hthought 你好", "hthought\n你好", None, 1):
            self.assertFalse(is_isolated_protocol_token(text), text)

    def test_registered_control_tag_is_not_read_as_an_internal_section_name(self):
        # 已登记标签由 strip_internal_expression_protocol 成对处理：闸门即使先跑，
        # 也不能拆散标签配对、把载荷留成孤立正文。
        text = '正文。<private_companion_context>{"a":1}</private_companion_context>你好'
        self.assertEqual(plan_leak_guard(text), (text, (), ()))

    def test_prepare_reply_drops_the_plan_prefix_and_keeps_the_body(self):
        prepared = prepare_reply("hthought\n Let's call `newapi_emit_text_v1` now. 你好呀。",
                                 SegmentationPolicy(), text_policy({}))
        self.assertEqual(prepared.parts, ("你好呀。",))

    def test_prepare_reply_rejects_a_plan_only_reply(self):
        with self.assertRaises(ValueError):
            prepare_reply("Let's call `newapi_emit_text_v1` now.", SegmentationPolicy(), text_policy({}))


if __name__ == "__main__":
    unittest.main()
