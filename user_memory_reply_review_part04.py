# -*- coding: utf-8 -*-
"""UserMemoryReplyReviewPart04Mixin。

由 tools/split_mixin_domain.py 从 user_memory_reply_review.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 30 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryReplyReviewMixin）。
"""
from __future__ import annotations
from .user_memory_reply_review_shared import re



class UserMemoryReplyReviewPart04Mixin:
    """UserMemoryReplyReviewPart04Mixin（从 UserMemoryReplyReviewMixin 拆出）。"""


    def _text_repeats_recent_message(self, text: str, recent_text: str) -> bool:
        current = self._compact_repeat_text(text)
        recent = self._compact_repeat_text(recent_text)
        if len(current) < 8 or len(recent) < 8:
            return False
        if current in recent or recent in current:
            return True
        current_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", text))
        recent_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", recent_text))
        stopwords = {
            "刚才", "现在", "今天", "这个", "那个", "一下", "一点", "有点", "还有",
            "什么", "安排", "用户", "你呢", "我呢", "就是", "已经", "容易",
        }
        current_tokens = {token for token in current_tokens if token not in stopwords}
        recent_tokens = {token for token in recent_tokens if token not in stopwords}
        if current_tokens and recent_tokens:
            common = current_tokens & recent_tokens
            if len(common) >= 3 and len(common) / max(1, min(len(current_tokens), len(recent_tokens))) >= 0.45:
                return True
        current_sig = self._proactive_topic_signature(text)
        recent_sig = self._proactive_topic_signature(recent_text)
        if current_sig and recent_sig and current_sig == recent_sig:
            shared_chunks = 0
            for idx in range(max(0, len(current) - 3)):
                chunk = current[idx : idx + 4]
                if chunk and chunk in recent:
                    shared_chunks += 1
                    if shared_chunks >= 2:
                        return True
        return False
