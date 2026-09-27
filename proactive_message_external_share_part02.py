# -*- coding: utf-8 -*-
"""ProactiveMessageExternalSharePart02Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_external_share.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 479 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageExternalShareMixin）。
"""
from __future__ import annotations

from .proactive_message_external_share_shared import _now_ts
from .proactive_message_external_share_shared import Any
from .proactive_message_external_share_shared import PromptDocument
from .proactive_message_external_share_shared import PromptRenderMode
from .proactive_message_external_share_shared import PromptSection
from .proactive_message_external_share_shared import _PROACTIVE_DOCUMENT_RENDER
from .proactive_message_external_share_shared import _persona_provider_id
from .proactive_message_external_share_shared import _proactive_prompt_part
from .proactive_message_external_share_shared import _safe_float
from .proactive_message_external_share_shared import _safe_int
from .proactive_message_external_share_shared import _single_line
from .proactive_message_external_share_shared import _today_key
from .proactive_message_external_share_shared import prompt_document
from .proactive_message_external_share_shared import prompt_section
from .proactive_message_external_share_shared import render_prompt_document
from .proactive_message_external_share_shared import render_prompt_sections
from .proactive_message_external_share_shared import runtime_persona_setting



class ProactiveMessageExternalSharePart02Mixin:
    """ProactiveMessageExternalSharePart02Mixin（从 ProactiveMessageExternalShareMixin 拆出）。"""


    def _format_recent_ai_daily_context_prompt_section(
        self,
        inbound_text: str = "",
    ) -> PromptSection | None:
        if not runtime_persona_setting(self, "enable_news_integration", False):
            return None
        if not self._user_asks_ai_daily_context(inbound_text):
            return None
        def build_section(content: str) -> PromptSection:
            return prompt_section(
                key="news.ai_daily_context",
                title="新闻阅读上下文",
                source="news",
                content=content,
            )

        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        ai_state = state.get("ai_daily") if isinstance(state.get("ai_daily"), dict) else {}
        digest, selected_item = self._select_ai_daily_digest_item(ai_state)
        record_date = _single_line(ai_state.get("last_success_date"), 20) or _single_line(ai_state.get("date"), 20)
        source_name = _single_line(ai_state.get("last_source_name"), 40)
        source_author = _single_line(ai_state.get("last_source_author"), 60)
        source_schedule = _single_line(ai_state.get("last_source_schedule"), 10)
        video_title = _single_line(ai_state.get("last_video_title"), 120)
        video_link = _single_line(ai_state.get("last_video_link"), 360)
        text_link = _single_line(ai_state.get("last_text_link"), 360)
        headline = _single_line(digest.get("headline") or digest.get("topic"), 120)
        impression = _single_line(digest.get("impression"), 220)
        read_basis = _single_line(ai_state.get("last_read_basis"), 40)
        text_readable_raw = ai_state.get("last_text_readable")
        text_readable = bool(text_readable_raw) if isinstance(text_readable_raw, bool) else bool(selected_item.get("article_readable") and selected_item.get("article_text"))
        subtitle_status = _single_line(ai_state.get("last_video_subtitle_status") or selected_item.get("video_subtitle_status"), 40)
        if not any((record_date, source_name, video_title, headline, impression, video_link, text_link)):
            body = (
                "用户正在询问 AI 日报/早报,但当前没有可用的 AI 日报记录。请直接说明最近还没读到可确认的 AI 日报,不要编造。"
            )
            return build_section(body)
        today = _today_key()
        rows: list[str] = []
        if record_date:
            if record_date != today and self._ai_daily_query_requires_freshness(inbound_text):
                rows.append(f"时间说明：今天是 {today}；最近一次可用 AI 日报记录日期是 {record_date}，不是今天。")
            else:
                rows.append(f"记录日期：{record_date}")
        if source_name or source_author or source_schedule:
            rows.append("来源：" + "｜".join(part for part in (source_name, source_author, source_schedule) if part))
        if video_title:
            rows.append(f"视频标题：{video_title}")
        if headline:
            rows.append(f"摘要重点：{headline}")
        if impression:
            rows.append(f"阅读印象：{impression}")
        if read_basis:
            rows.append(f"整理依据：{read_basis}")
        if text_link:
            rows.append(f"文字版链接：{text_link}")
        elif video_link:
            rows.append(f"视频链接：{video_link}")
        rows.append(f"正文可读：{'是' if text_readable else '否'}")
        if subtitle_status:
            rows.append(f"字幕状态：{subtitle_status}")
        body = (
            "用户正在询问 AI 日报/早报。下面是最近一次真实读到的 AI 日报记录；如果日期不是今天，请明确说出具体日期，不要说成今天刚读到。"
            "回答只能基于这些内容，不要编造额外新闻。\n"
            + "\n".join(rows[:10])
        )
        return build_section(body)

    def _format_recent_news_context_for_reply(
        self,
        inbound_text: str = "",
    ) -> str:
        section = self._format_recent_news_context_prompt_section(inbound_text)
        if section is None:
            return ""
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_recent_news_context_prompt_section(
        self,
        inbound_text: str = "",
    ) -> PromptSection | None:
        if not runtime_persona_setting(self, "enable_news_integration", False):
            return None
        ai_daily_context = self._format_recent_ai_daily_context_prompt_section(inbound_text)
        if ai_daily_context is not None:
            return ai_daily_context
        if not self._user_asks_news_context(inbound_text):
            return None
        def build_section(content: str) -> PromptSection:
            return prompt_section(
                key="news.recent",
                title="新闻阅读上下文",
                source="news",
                content=content,
            )

        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        digest = state.get("last_digest") if isinstance(state.get("last_digest"), dict) else {}
        digests = state.get("digests") if isinstance(state.get("digests"), list) else []
        latest_items = state.get("latest_items") if isinstance(state.get("latest_items"), list) else []
        if not digest and not digests and not latest_items:
            body = (
                "用户正在询问今天的新闻/AI 新闻,但当前还没有可用的新闻阅读记录。请自然说明自己还没读到今天的新闻,不要编造新闻。"
            )
            return build_section(body)
        rows: list[str] = []
        if digest:
            rows.append(
                "最近一次整理："
                + "｜".join(
                    part
                    for part in (
                        _single_line(digest.get("headline") or digest.get("topic"), 120),
                        _single_line(digest.get("selected_source"), 40),
                        _single_line(digest.get("impression"), 220),
                        _single_line(digest.get("selected_link"), 360),
                    )
                    if part
                )
            )
        for item in reversed([item for item in digests if isinstance(item, dict)][-4:]):
            headline = _single_line(item.get("headline") or item.get("topic"), 120)
            impression = _single_line(item.get("impression"), 180)
            source = _single_line(item.get("selected_source"), 40)
            if headline or impression:
                rows.append("- " + "｜".join(part for part in (headline, source, impression) if part))
        if latest_items:
            rows.append("候选标题：")
            for item in latest_items[:6]:
                if not isinstance(item, dict):
                    continue
                title = _single_line(item.get("title"), 120)
                source = _single_line(item.get("source"), 40)
                summary = _single_line(item.get("summary"), 160)
                if title:
                    rows.append("- " + "｜".join(part for part in (title, source, summary) if part))
        body = (
            "用户正在询问今天的新闻/AI 新闻。下面是 Bot 近期真实读过或抓到的新闻记录；回答时只能基于这些内容,不要编造额外新闻。"
            "可以按人格自然概括,如果记录不够新或不完整,要直接说明。\n"
            + "\n".join(rows[:12])
        )
        return build_section(body)

    def _format_news_digest_for_command(self) -> str:
        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        if not runtime_persona_setting(self, "enable_news_integration", False):
            return "新闻阅读功能没有开启。"
        status = _single_line(state.get("last_status"), 60) or "未知"
        digest = state.get("last_digest") if isinstance(state.get("last_digest"), dict) else {}
        latest_items = state.get("latest_items") if isinstance(state.get("latest_items"), list) else []
        if not digest and not latest_items:
            return f"这次没有读到可用新闻。\n状态：{status}"
        lines = ["今日新闻见闻："]
        if digest:
            headline = _single_line(digest.get("headline") or digest.get("topic"), 120)
            source = _single_line(digest.get("selected_source"), 40)
            impression = _single_line(digest.get("impression"), 260)
            link = _single_line(digest.get("selected_link"), 420)
            if headline:
                lines.append(f"- 重点：{headline}")
            if source:
                lines.append(f"- 来源：{source}")
            if impression:
                lines.append(f"- 印象：{impression}")
            if link:
                lines.append(f"- 链接：{link}")
        if latest_items:
            lines.append("候选标题：")
            for item in latest_items[:6]:
                if not isinstance(item, dict):
                    continue
                title = _single_line(item.get("title"), 100)
                source = _single_line(item.get("source"), 30)
                if title:
                    lines.append(f"- {title}" + (f"（{source}）" if source else ""))
        return "\n".join(lines)

    def _format_ai_daily_digest_for_command(self) -> str:
        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        if not runtime_persona_setting(self, "enable_news_integration", False):
            return "新闻阅读功能没有开启。"
        if not runtime_persona_setting(self, "enable_ai_daily_watch", True):
            return "AI 日报/早报追踪没有开启。"
        ai_state = state.get("ai_daily") if isinstance(state.get("ai_daily"), dict) else {}
        digest, selected_item = self._select_ai_daily_digest_item(ai_state)
        record_date = _single_line(ai_state.get("last_success_date"), 20) or _single_line(ai_state.get("date"), 20)
        source_name = _single_line(ai_state.get("last_source_name"), 40)
        source_author = _single_line(ai_state.get("last_source_author"), 60)
        source_schedule = _single_line(ai_state.get("last_source_schedule"), 10)
        video_title = _single_line(ai_state.get("last_video_title"), 120)
        video_link = _single_line(ai_state.get("last_video_link"), 420)
        text_link = _single_line(ai_state.get("last_text_link"), 420)
        headline = _single_line(digest.get("headline") or digest.get("topic"), 120)
        impression = _single_line(digest.get("impression"), 260)
        read_basis = _single_line(ai_state.get("last_read_basis"), 40) or ("完整文字版正文" if bool(selected_item.get("article_readable") and selected_item.get("article_text")) else "视频标题/简介")
        if not any((record_date, source_name, video_title, headline, impression, video_link, text_link)):
            status = _single_line(ai_state.get("status"), 60) or "未知"
            return f"最近还没有可用的 AI 日报记录。\n状态：{status}"
        today = _today_key()
        lines = ["最近的 AI 日报/早报："]
        if record_date:
            lines.append(f"- 日期：{record_date}")
            if record_date != today:
                lines.append(f"- 说明：今天是 {today}，最近一次成功记录不是今天。")
        if source_name or source_author or source_schedule:
            lines.append("- 来源：" + "｜".join(part for part in (source_name, source_author, source_schedule) if part))
        if video_title:
            lines.append(f"- 视频：{video_title}")
        if headline:
            lines.append(f"- 重点：{headline}")
        if impression:
            lines.append(f"- 印象：{impression}")
        if read_basis:
            lines.append(f"- 整理依据：{read_basis}")
        if text_link:
            lines.append(f"- 文字版：{text_link}")
        elif video_link:
            lines.append(f"- 视频链接：{video_link}")
        return "\n".join(lines)

    def _format_ai_daily_status_for_command(self) -> str:
        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        ai_state = state.get("ai_daily") if isinstance(state.get("ai_daily"), dict) else {}
        status_labels = {
            "read": "已阅读",
            "waiting_schedule": "等待定时",
            "all_sources_done": "今日来源已处理",
            "waiting_window": "等待窗口",
            "checking": "正在检查",
            "waiting_today_video": "等待今日视频",
            "today_video_without_text": "今日视频暂无文字版",
            "already_read_today_video": "今日已读",
            "missed_today_ai_daily": "今日窗口已过",
            "digest_failed": "整理失败",
        }
        status = _single_line(ai_state.get("status"), 60) or "未知"
        lines = [
            "AI 日报/早报测试结果：",
            f"- 新闻集成：{'开启' if runtime_persona_setting(self, 'enable_news_integration', False) else '关闭'}",
            f"- AI日报/早报追踪：{'开启' if runtime_persona_setting(self, 'enable_ai_daily_watch', True) else '关闭'}",
            f"- 状态：{status_labels.get(status, status)}",
        ]
        sources = ai_state.get("sources") if isinstance(ai_state.get("sources"), list) else []
        configured_sources = str(runtime_persona_setting(self, "ai_daily_sources", "") or "").strip()
        if configured_sources:
            lines.append("- 来源计划：")
            for raw_line in configured_sources.splitlines()[:8]:
                parts = [part.strip() for part in raw_line.split("|")]
                if len(parts) >= 5:
                    lines.append(f"  - {parts[0]}｜{parts[1]}｜{parts[4]}｜UID {parts[2]}")
        elif sources:
            lines.append("- 来源计划：")
            for item in sources[:8]:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"  - {_single_line(item.get('name'), 30)}｜{_single_line(item.get('author_name'), 40)}"
                    f"｜{_single_line(item.get('schedule'), 10)}｜UID {_single_line(item.get('mid'), 32)}"
                )
        date = _single_line(ai_state.get("date"), 20)
        checked = self._format_timestamp_elapsed(ai_state.get("last_checked_at", 0))
        success_date = _single_line(ai_state.get("last_success_date"), 20)
        title = _single_line(ai_state.get("last_video_title"), 120)
        video_link = _single_line(ai_state.get("last_video_link"), 420)
        text_link = _single_line(ai_state.get("last_text_link"), 420)
        candidate_count = _safe_int(ai_state.get("last_candidate_count"), 0, 0)
        digest, selected_item = self._select_ai_daily_digest_item(ai_state)
        if date:
            lines.append(f"- 状态日期：{date}")
        if checked:
            lines.append(f"- 最近检查：{checked}")
        if success_date:
            lines.append(f"- 最近成功日期：{success_date}")
        last_source = _single_line(ai_state.get("last_source_name"), 40)
        last_author = _single_line(ai_state.get("last_source_author"), 60)
        last_schedule = _single_line(ai_state.get("last_source_schedule"), 10)
        if last_source or last_author:
            lines.append(
                "- 最近来源："
                + "｜".join(part for part in (last_source, last_author, last_schedule) if part)
            )
        if title:
            lines.append(f"- 视频：{title}")
        if video_link:
            lines.append(f"- 视频链接：{video_link}")
        owner_name = _single_line(ai_state.get("last_video_owner_name") or selected_item.get("video_owner_name"), 80)
        tname = _single_line(ai_state.get("last_video_tname") or selected_item.get("video_tname"), 60)
        duration = _safe_int(ai_state.get("last_video_duration") or selected_item.get("video_duration"), 0, 0)
        video_context_chars = _safe_int(ai_state.get("last_video_context_chars"), 0, 0)
        if not video_context_chars and selected_item:
            video_context_chars = len(str(selected_item.get("video_context_text") or ""))
        video_tags = ai_state.get("last_video_tags") if isinstance(ai_state.get("last_video_tags"), list) else selected_item.get("video_tags")
        video_tags = [_single_line(tag, 30) for tag in video_tags if _single_line(tag, 30)] if isinstance(video_tags, list) else []
        video_comments = ai_state.get("last_video_hot_comments") if isinstance(ai_state.get("last_video_hot_comments"), list) else selected_item.get("video_hot_comments")
        video_comments = [_single_line(comment, 60) for comment in video_comments if _single_line(comment, 60)] if isinstance(video_comments, list) else []
        meta_parts = []
        if owner_name:
            meta_parts.append(f"UP主 {owner_name}")
        if tname:
            meta_parts.append(f"分区 {tname}")
        if duration:
            meta_parts.append(f"时长 {duration // 60}分{duration % 60}秒")
        if meta_parts:
            lines.append("- 视频信息：" + "｜".join(meta_parts))
        if video_context_chars:
            lines.append(f"- 视频公开信息：已读取 {video_context_chars} 字")
        if video_tags:
            lines.append(f"- 视频标签：{'、'.join(video_tags[:8])}")
        if video_comments:
            lines.append(f"- 热门评论：已读取 {len(video_comments)} 条")
        if text_link:
            lines.append(f"- 文字版链接：{text_link}")
        text_readable_raw = ai_state.get("last_text_readable")
        text_readable = bool(text_readable_raw) if isinstance(text_readable_raw, bool) else bool(selected_item.get("article_readable") and selected_item.get("article_text"))
        text_chars = _safe_int(ai_state.get("last_text_chars"), 0, 0)
        if not text_chars and selected_item:
            text_chars = len(str(selected_item.get("article_text") or ""))
        subtitle_readable_raw = ai_state.get("last_video_subtitle_readable")
        subtitle_readable = bool(subtitle_readable_raw) if isinstance(subtitle_readable_raw, bool) else bool(selected_item.get("video_subtitle_readable") and selected_item.get("video_subtitle_text"))
        subtitle_chars = _safe_int(ai_state.get("last_video_subtitle_chars"), 0, 0)
        if not subtitle_chars and selected_item:
            subtitle_chars = len(str(selected_item.get("video_subtitle_text") or ""))
        subtitle_status = _single_line(ai_state.get("last_video_subtitle_status") or selected_item.get("video_subtitle_status"), 40)
        subtitle_status_labels = {
            "read": "已读取字幕",
            "missing": "公开视频暂无字幕",
            "unavailable": "字幕不可用",
        }
        read_basis = _single_line(ai_state.get("last_read_basis"), 40) or ("完整文字版正文" if text_readable else "视频标题/简介")
        if text_link or selected_item or video_link:
            lines.append(f"- 文字版读取：{'已读取完整正文' if text_readable else '未读取到正文'}")
        if text_chars:
            lines.append(f"- 文字版正文字数：{text_chars}")
        if video_link or selected_item:
            lines.append(f"- 字幕读取：{subtitle_status_labels.get(subtitle_status, '已读取字幕' if subtitle_readable else '未读取到字幕')}")
        if subtitle_chars:
            lines.append(f"- 字幕字数：{subtitle_chars}")
        if read_basis:
            lines.append(f"- 整理依据：{read_basis}")
        if candidate_count:
            lines.append(f"- 候选数量：{candidate_count}")
        source_states = ai_state.get("source_states") if isinstance(ai_state.get("source_states"), dict) else {}
        if source_states:
            lines.append("来源状态：")
            for item in source_states.values():
                if not isinstance(item, dict):
                    continue
                source_title = _single_line(item.get("last_video_title"), 80)
                lines.append(
                    f"- {_single_line(item.get('name'), 30) or '来源'}｜{_single_line(item.get('schedule'), 10) or '未定时'}"
                    f"｜{status_labels.get(_single_line(item.get('status'), 60), _single_line(item.get('status'), 60) or '未知')}"
                    + (f"｜{source_title}" if source_title else "")
                )
        if digest:
            headline = _single_line(digest.get("headline") or digest.get("topic"), 120)
            impression = _single_line(digest.get("impression"), 220)
            if headline:
                lines.append(f"- 摘要重点：{headline}")
            if impression:
                lines.append(f"- 阅读印象：{impression}")
        candidates = ai_state.get("last_candidates") if isinstance(ai_state.get("last_candidates"), list) else []
        if candidates:
            lines.append("最近候选：")
            for item in candidates[:5]:
                if not isinstance(item, dict):
                    continue
                title_line = _single_line(item.get("title"), 90) or "未命名"
                published = _single_line(item.get("published"), 24) or "无发布时间"
                today_mark = "今天" if item.get("is_today") else "非今天"
                lines.append(f"- [{today_mark}] {published}｜{title_line}")
        if not runtime_persona_setting(self, "enable_news_integration", False):
            lines.append("提示：新闻集成关闭时不会执行抓取。")
        elif not runtime_persona_setting(self, "enable_ai_daily_watch", True):
            lines.append("提示：AI 日报/早报追踪关闭时不会执行抓取。")
        return "\n".join(lines)

    def _format_creative_share_action_context(self, user: dict[str, Any]) -> str:
        creative = user.get("creative_share_context")
        if not isinstance(creative, dict):
            return ""
        if _now_ts() - _safe_float(creative.get("created_ts"), 0) > 8 * 3600:
            return ""
        title = _single_line(creative.get("title"), 50)
        work_type = _single_line(creative.get("work_type"), 30) or "作品"
        premise = _single_line(creative.get("premise"), 140)
        tone = _single_line(creative.get("tone"), 40)
        source = _single_line(creative.get("source"), 120)
        snippet = _single_line(creative.get("snippet"), 260)
        current_chars = _safe_int(creative.get("current_chars"), 0, 0)
        target_chars = _safe_int(creative.get("target_chars"), 0, 0)
        parts = [
            "创作分享线索",
            f"作品类型：{work_type}" if work_type else "",
            f"标题：{title}" if title else "",
            f"设定：{premise}" if premise else "",
            f"灵感来源：{source}" if source else "",
            f"行文气质：{tone}" if tone else "",
            f"披露类型：{_single_line(creative.get('disclosure_kind'), 30) or 'milestone'}",
            f"节点：{_single_line(creative.get('milestone'), 30)}" if creative.get("milestone") else "",
            f"当前进度：约 {current_chars}/{target_chars} 字" if current_chars and target_chars else "",
            f"刚写到的片段：{snippet}" if snippet else "",
        ]
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _creative_share_excerpt_prompt_section() -> PromptSection:
        return prompt_section(
            key="proactive.creative_excerpt",
            title="创作分享的正文边界",
            source="proactive_message",
            content=(
                "- 如果要把作品原文发给对方，只能从“刚写到的片段”中连续截取，不得改写、拼接或另编一段冒充原文。\n"
                "- 把实际作品摘录完整放在一组成对的 `「...」` 中；`「」` 内只放作品原文，聊天式引入、感受、提问和收尾都放在引号外。\n"
                "- 不要把整条聊天都包进 `「」`。如果本轮只聊创作进度、没有实际摘录作品，就不要使用 `「」`。\n"
                "- `「...」` 会作为一个完整作品气泡发送；它前后的普通聊天仍按自然聊天节奏分段。"
            ),
        )

    @classmethod
    def _creative_share_excerpt_prompt_hint(cls) -> str:
        return render_prompt_sections(
            [cls._creative_share_excerpt_prompt_section()],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    @staticmethod
    def _screen_narration_prompt_document(
        *,
        screen_term: str,
        worldview_adaptation: str,
        cleaned_context: str,
    ) -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.screen_narration",
                    title="屏幕观察内部摘要",
                    source="proactive_message",
                    template=(
                        "请把下面的{screen_term}观察结果转成“视觉识别后的内部摘要”,供角色继续私聊使用。\n"
                        "要求：\n"
                        "1. 只描述视觉上看出来的内容,不要猜测工具调用过程,不要输出工具名、action 名、报错栈。\n"
                        "2. 只概括用户大概正在看什么、做什么、情绪上是否像在忙,不要复述完整文字、账号、聊天原文、隐私细节。\n"
                        "3. 绝对不要直接对用户说话,不要安慰、提醒、陪伴、劝休息,不要写成一条完整回复。\n"
                        "4. 要像看了一眼{screen_term}后留在脑子里的印象,不要写成建议列表。\n"
                        "5. 50 字以内,只输出摘要本身。\n\n"
                        "{worldview_adaptation}\n\n"
                        "原始结果：\n"
                        "{cleaned_context}"
                    ),
                    variables={
                        "screen_term": screen_term,
                        "worldview_adaptation": worldview_adaptation,
                        "cleaned_context": cleaned_context,
                    },
                ), mode=PromptRenderMode.BODY_ONLY),
            ),
            metadata={"task": "screen_narration"},
        )

    async def _narrate_action_context(self, action: str, action_context: str) -> str:
        narration_provider_id = _persona_provider_id(
            self, "NARRATION_PROVIDER_ID", "narration_provider_id", "fast"
        )
        if not narration_provider_id:
            return self._sanitize_action_context_text(action, action_context)
        if action in {"message", "photo_text", "poke", "voice"} or "photo_text" in action or "voice" in action or "poke" in action or not action_context:
            return self._sanitize_action_context_text(action, action_context)
        cleaned_context = self._sanitize_action_context_text(action, action_context)
        terms = self._worldview_terms()
        worldview_adaptation = self._format_worldview_adaptation_prompt()
        prompt = render_prompt_document(
            self._screen_narration_prompt_document(
                screen_term=terms["screen"],
                worldview_adaptation=worldview_adaptation,
                cleaned_context=cleaned_context,
            )
        )["user"]
        text = await self._llm_call(
            prompt,
            max_tokens=80,
            provider_id=narration_provider_id,
            task="screen_narration",
        )
        return _single_line(text, 120) if text else cleaned_context
