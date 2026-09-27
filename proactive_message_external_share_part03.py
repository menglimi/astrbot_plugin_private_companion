# -*- coding: utf-8 -*-
"""ProactiveMessageExternalSharePart03Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_external_share.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 321 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageExternalShareMixin）。
"""
from __future__ import annotations
from .proactive_message_external_share_shared import Any
from .proactive_message_external_share_shared import _single_line
from .proactive_message_external_share_shared import re
from .proactive_message_external_share_shared import runtime_persona_setting



class ProactiveMessageExternalSharePart03Mixin:
    """ProactiveMessageExternalSharePart03Mixin（从 ProactiveMessageExternalShareMixin 拆出）。"""


    def _sanitize_action_context_text(self, action: str, action_context: str) -> str:
        text = str(action_context or "").strip()
        if "screen_peek" not in action:
            return text
        text = re.sub(r"^screen_peek[:：]\s*", "", text).strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        cleaned_lines = []
        for line in lines:
            if re.search(r"(我会一直在这里陪着你|要注意休息|记得休息|辛苦|别太累|我会陪着你)", line):
                continue
            line = re.sub(r"^(?:还在|你还在|感觉|看起来)", "", line).strip(",。！？ ")
            cleaned_lines.append(line)
        collapsed = ",".join(line for line in cleaned_lines if line)
        collapsed = collapsed.replace("用户", "")
        collapsed = re.sub(r"\s+", " ", collapsed).strip(",。！？ ")
        if not collapsed:
            collapsed = lines[0]
        return _single_line(collapsed, 140)

    def _external_share_source_consistency_decision(
        self,
        user: dict[str, Any],
        text: str,
        *,
        reason: str = "",
        topic: str = "",
        motive: str = "",
        action_context: str = "",
    ) -> dict[str, Any] | None:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return None
        source_text = self._external_share_anchor_text(
            user,
            reason=reason,
            topic=topic,
            motive=motive,
            action_context=action_context,
        )
        if not source_text:
            return {
                "decision": "drop",
                "reason": "外界分享缺少可见来源",
                "hard": True,
            }
        source_link_match = re.search(r"https?://[^\s；，。！？!?]+", source_text, flags=re.I)
        source_link = source_link_match.group(0).rstrip("）)】]》>。.") if source_link_match else ""
        expected_platform = self._external_share_platform_from_url(source_link)
        claimed_platform = self._external_share_claimed_platform(cleaned)
        platform_mismatch = bool(
            source_link
            and claimed_platform
            and (not expected_platform or claimed_platform != expected_platform)
        )
        if platform_mismatch:
            expected_label = expected_platform or "该网页来源"
            reference = self._external_share_fallback_reference(source_text)
            if reference:
                return {
                    "decision": "rewrite",
                    "reason": f"来源平台错配：链接属于{expected_label}，正文却写成{claimed_platform}",
                    "reference_text": reference,
                    "source_text": source_text,
                    "hard": True,
                }
            return {
                "decision": "drop",
                "reason": f"来源平台错配：应为{expected_label}而不是{claimed_platform}",
                "hard": True,
            }
        require_source_link = bool(
            runtime_persona_setting(self, "external_share_require_source_link", True)
        )
        if require_source_link and source_link and source_link not in cleaned:
            reference = self._external_share_fallback_reference(source_text)
            if reference:
                return {
                    "decision": "rewrite",
                    "reason": "外界分享正文遗漏真实来源链接",
                    "reference_text": reference,
                    "source_text": source_text,
                    "hard": True,
                }
        if self._external_share_text_mentions_source(cleaned, source_text):
            return None
        reference = self._external_share_fallback_reference(source_text)
        if reference:
            return {
                "decision": "rewrite",
                "reason": "外界分享正文偏离来源",
                "reference_text": reference,
                "source_text": source_text,
                "hard": True,
            }
        return {
            "decision": "defer",
            "reason": "外界分享缺少可承接来源",
            "delay_minutes": 75,
            "hard": True,
        }

    def _external_share_anchor_text(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
        topic: str = "",
        motive: str = "",
        action_context: str = "",
    ) -> str:
        parts: list[str] = []

        def add(value: Any, limit: int = 160) -> None:
            text = self._clean_external_share_source_field(value, limit)
            if text and text not in parts:
                parts.append(text)

        add(topic, 180)
        if action_context:
            for raw_line in str(action_context or "").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if self._looks_like_internal_provider_error_text(line):
                    continue
                if re.match(
                    r"^(?:标题|话题|摘要重点|搜索词|参考来源|来源|链接|UP|短评|回味|内部印象|留下的印象)[:：]",
                    line,
                    flags=re.I,
                ):
                    add(line, 220)
        if isinstance(user, dict):
            context_keys = {
                "bili_video_share": ("bilibili_video_context",),
                "news_share": ("news_context",),
                "web_exploration_share": ("web_exploration_context",),
            }.get(str(reason or "").strip(), ())
            for key in context_keys:
                payload = user.get(key)
                if not isinstance(payload, dict):
                    continue
                prefixed_fields = {
                    "topic": "话题",
                    "headline": "标题",
                    "title": "标题",
                    "source": "来源",
                    "selected_source": "来源",
                    "source_title": "参考来源",
                    "selected_link": "链接",
                    "source_url": "链接",
                    "link": "链接",
                    "url": "链接",
                }
                for field, prefix in prefixed_fields.items():
                    value = payload.get(field)
                    if value:
                        add(f"{prefix}：{value}", 180)
                for field in ("summary", "impression", "comment", "review", "bvid"):
                    add(payload.get(field), 180)
        if not parts:
            add(motive, 140)
        return _single_line("；".join(parts), 760)

    def _external_share_is_vague_pointer(self, text: str) -> bool:
        message = _single_line(text, 260)
        if not message:
            return False
        if re.search(r"https?://|(?:^|[^A-Za-z0-9])BV[0-9A-Za-z]{8,16}(?:$|[^A-Za-z0-9])", message):
            return False
        if re.search(r"[《“\"『「][^》”\"』」]{2,80}[》”\"』」]", message):
            return False
        compact = re.sub(r"[\s，,。！？!?、~～…]+", "", message)
        vague_patterns = (
            "你快看这个",
            "快看这个",
            "看这个",
            "你看看这个",
            "看看这个",
            "给你看个东西",
            "刷到个东西",
            "这个也太",
            "这个太",
            "这个好",
            "这条也太",
            "这条太",
            "这也太",
            "居然这么",
        )
        if any(pattern in compact for pattern in vague_patterns):
            return True
        if len(compact) <= 26 and any(token in compact for token in ("这个", "这条", "那条", "东西")) and any(
            token in compact for token in ("离谱", "逆天", "好笑", "绷不住", "惊了", "怪")
        ):
            return True
        return False

    def _external_share_text_mentions_source(self, text: str, source_text: str) -> bool:
        message = _single_line(text, 260).lower()
        source = _single_line(source_text, 760).lower()
        if not message or not source:
            return False
        if self._looks_like_internal_provider_error_text(message):
            return False
        if self._external_share_is_vague_pointer(message):
            return False
        anchor_tokens = self._external_share_anchor_tokens(source)
        for token in anchor_tokens:
            if token and token in message:
                return True
        return False

    def _external_share_anchor_tokens(self, source_text: str) -> list[str]:
        text = _single_line(source_text, 760)
        if not text:
            return []
        tokens: list[str] = []

        def add(value: str) -> None:
            clean = value.strip(" \t\r\n，。！？；：、,.!?;:()（）[]【】《》“”\"'")
            if len(clean) >= 2 and clean not in tokens:
                tokens.append(clean)

        for item in re.findall(r"[A-Za-z]+[-_A-Za-z0-9]*|[0-9]+(?:多年|年|月|日|次|个|%)?", text):
            add(item.lower())
        for chunk in re.split(r"[\s，。！？；：、,.!?;:|｜/\\\\()（）\\[\\]【】《》“”\"']+", text):
            chunk = chunk.strip()
            if not chunk:
                continue
            if re.fullmatch(r"[\u4e00-\u9fff]{2,12}", chunk):
                add(chunk)
                if len(chunk) > 4:
                    for size in (4, 3, 2):
                        for index in range(0, max(0, len(chunk) - size + 1)):
                            add(chunk[index:index + size])
            elif re.search(r"[\u4e00-\u9fff]", chunk):
                for item in re.findall(r"[\u4e00-\u9fff]{2,8}", chunk):
                    add(item)
        generic = {
            "标题", "视频", "新闻", "文章", "资料", "来源", "分享", "短评", "回味", "评分", "链接",
            "这个", "这条", "那条", "那个", "东西", "内容", "感觉", "有点", "刚刚", "刚才",
            "离谱", "逆天", "好笑", "有趣", "震惊", "惊了", "神奇", "奇怪", "贴", "轻轻",
            "刚刷到一个视频", "刚刷到", "刷到一", "到一个", "一个视", "个视频", "一个视频",
            "b站视频分享线索", "站视频分享线索", "视频分享线索", "分享线索",
            "新闻阅读线索", "阅读线索", "刚扫过", "扫过几", "几条新", "条新闻",
            "网页探索线索", "探索线索", "内部探索笔记", "探索笔记",
            "http", "https", "www", "com", "cn", "bilibili", "video",
        }
        generic_phrases = (
            "b站视频分享线索刚刷到一个视频",
            "新闻阅读线索刚扫过几条新闻其中一条让自己有点想私下提一句",
            "网页探索线索bot刚刚按自己的兴趣主动搜索并了解了一点新东西这是一条内部探索笔记",
            "表达要求不要像播报新闻不要夸大或补充未知事实",
        )
        return [
            token
            for token in tokens
            if token not in generic and not any(token in phrase for phrase in generic_phrases)
        ][:24]

    def _external_share_fallback_reference(self, source_text: str) -> str:
        source = _single_line(source_text, 760)
        if not source:
            return ""
        title = ""
        link = ""
        link_match = re.search(r"https?://[^\s；，。！？!?]+", source, flags=re.I)
        if link_match:
            link = _single_line(link_match.group(0).rstrip("）)】]》>。."), 220)
        source_platform = self._external_share_platform_from_url(link)
        bvid_match = re.search(r"\bBV[0-9A-Za-z]{8,16}\b", source)
        if not link and bvid_match:
            link = f"https://www.bilibili.com/video/{bvid_match.group(0)}"
        reference_match = re.search(r"(?:参考来源|source_title)[:：]\s*([^；。\n\r|｜]{2,90})", source, flags=re.I)
        if reference_match:
            title = _single_line(reference_match.group(1), 64)
        book_match = re.search(r"[《“\"『「]([^》”\"』」]{2,90})[》”\"』」]", source)
        if not title and book_match:
            title = _single_line(book_match.group(1), 64)
        for pattern in (
            r"(?:标题|摘要重点|话题|参考来源|source_title|headline|topic)[:：]\s*([^；。\n\r|｜]{2,90})",
            r"^([^；。\n\r]{4,90})",
        ):
            if title:
                break
            match = re.search(pattern, source, flags=re.I)
            if match:
                title = _single_line(match.group(1), 64)
                title = re.split(
                    r"\s+(?:链接|UP|评分|心情|短评|回味|来源|内部印象|表达气质|额外边界|参考来源|搜索词)[:：]",
                    title,
                    maxsplit=1,
                    flags=re.I,
                )[0]
                break
        if not title:
            if link:
                return _single_line(link, 260)
            return ""
        title = title.strip(" ，。！？；：、,.!?;:|｜")
        if not title:
            if link:
                return _single_line(link, 260)
            return ""
        if self._looks_like_internal_provider_error_text(title):
            return ""
        impression_match = re.search(
            r"(?:留下的印象|内部印象|短评|回味)[:：]\s*([^；\n\r]{4,70})",
            source,
            flags=re.I,
        )
        impression = _single_line(impression_match.group(1), 42).rstrip("。！？!?；;，,") if impression_match else ""
        impression = re.sub(r"让人", "让我", impression)
        if source_platform:
            base = f"刚在{source_platform}刷到“{title}”"
        else:
            base = f"刚看到“{title}”这条内容"
        if impression and impression != title and len(base) + len(impression) <= 96:
            base = f"{base}，{impression}"
        else:
            base = f"{base}，有点想给你看看"
        if link:
            base = f"{base}。{link}"
        else:
            base = f"{base}。"
        return _single_line(base, 300)
