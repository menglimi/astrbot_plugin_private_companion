# -*- coding: utf-8 -*-
"""ProactiveMessageExternalSharePart01Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_external_share.py 机械抽取（16 个方法 + 0 个模块级名字 + 0 个类级赋值 / 436 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageExternalShareMixin）。
"""
from __future__ import annotations

from .proactive_message_external_share_shared import _now_ts
from .proactive_message_external_share_shared import Any
from .proactive_message_external_share_shared import PromptRenderMode
from .proactive_message_external_share_shared import PromptSection
from .proactive_message_external_share_shared import _safe_float
from .proactive_message_external_share_shared import _safe_int
from .proactive_message_external_share_shared import _single_line
from .proactive_message_external_share_shared import prompt_section
from .proactive_message_external_share_shared import re
from .proactive_message_external_share_shared import render_prompt_sections
from .proactive_message_external_share_shared import runtime_persona_setting
from .proactive_message_external_share_shared import urlparse



class ProactiveMessageExternalSharePart01Mixin:
    """ProactiveMessageExternalSharePart01Mixin（从 ProactiveMessageExternalShareMixin 拆出）。"""


    @staticmethod
    def _looks_like_internal_provider_error_text(text: Any) -> bool:
        cleaned = _single_line(text, 1000).lower()
        if not cleaned:
            return False
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", " ", cleaned).strip()
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", "", cleaned)
        direct_markers = (
            "all chat models failed",
            "all llm providers failed",
            "prompt could not be submitted",
            "prompt was not submitted",
            "try rephrasing the prompt",
            "generative ai prohibited use policy",
            "prompt contains sensitive words",
            "badrequesterror",
            "api connection error",
            "apiconnectionerror",
            "api status error",
            "apistatuserror",
            "authenticationerror",
            "permissiondeniederror",
            "ratelimiterror",
            "notfounderror",
            "internalservererror",
            "provider api error",
            "unable to submit request",
            "invalid_request",
            "invalid request error",
            "主动消息专用模式下",
            "普通被动回复不可使用 private companion 工具",
            "主动渲染阶段不可使用 private companion 工具",
            "has sent the result directly to the user",
            "error code: 400",
            "error code 400",
            "400 bad request",
            "模型调用失败",
            "工具调用失败",
            "函数工具调用失败",
            "api 调用失败",
            "api调用失败",
            "provider 调用失败",
            "provider调用失败",
            "没有返回值，或者已将结果直接发送给用户",
            "没有返回值,或者已将结果直接发送给用户",
        )
        compact_markers = (
            "allchatmodelsfailed",
            "allllmprovidersfailed",
            "promptcouldnotbesubmitted",
            "promptwasnotsubmitted",
            "tryrephrasingtheprompt",
            "generativeaiprohibitedusepolicy",
            "promptcontainssensitivewords",
            "badrequesterror",
            "apiconnectionerror",
            "apistatuserror",
            "authenticationerror",
            "permissiondeniederror",
            "ratelimiterror",
            "notfounderror",
            "internalservererror",
            "providerapierror",
            "unabletosubmitrequest",
            "invalid_request",
            "invalidrequesterror",
            "主动消息专用模式下",
            "普通被动回复不可使用privatecompanion工具",
            "主动渲染阶段不可使用privatecompanion工具",
            "hassenttheresultdirectlytotheuser",
            "errorcode400",
            "400badrequest",
            "模型调用失败",
            "工具调用失败",
            "函数工具调用失败",
            "api调用失败",
            "provider调用失败",
            "没有返回值或者已将结果直接发送给用户",
        )
        if any(marker in cleaned or marker in normalized for marker in direct_markers):
            return True
        if any(marker in compact for marker in compact_markers):
            return True
        provider_error_context = any(
            token in compact
            for token in (
                "providerapierror",
                "errorcode",
                "statuscode",
                "badrequest",
                "invalidrequest",
                "requestfailed",
                "请求失败",
                "调用失败",
                "模型调用失败",
                "工具调用失败",
            )
        )
        if "errorcode" in compact and any(
            token in compact
            for token in (
                "badrequest",
                "invalidrequest",
                "provider",
                "apierror",
                "functiondeclaration",
            )
        ):
            return True
        if "functiondeclaration" in compact and provider_error_context and any(
            token in compact for token in ("schema", "properties", "parameters", "tool", "tools", "badrequest", "invalidrequest")
        ):
            return True
        if any(token in compact for token in ("schemadidntspecify", "toolschema", "image_url", "invalidparameter")) and provider_error_context:
            return True
        if "aisearch" in cleaned and any(
            marker in cleaned
            for marker in (
                "failed",
                "badrequest",
                "invalid_request",
                "unable to submit",
                "provider api",
            )
        ):
            return True
        return False

    def _clean_external_share_source_field(self, value: Any, limit: int = 160) -> str:
        text = _single_line(value, limit)
        if not text:
            return ""
        if self._looks_like_internal_provider_error_text(text):
            return ""
        if self._framework_agent_meta_summary_leak(text):
            return ""
        return text

    def _format_bilibili_video_action_context(self, user: dict[str, Any]) -> str:
        video = user.get("bilibili_video_context")
        if not isinstance(video, dict):
            return ""
        if _now_ts() - _safe_float(video.get("created_ts"), 0) > 6 * 3600:
            return ""
        title = self._clean_external_share_source_field(video.get("title"), 80)
        bvid = self._clean_external_share_source_field(video.get("bvid"), 32)
        up_name = self._clean_external_share_source_field(video.get("up_name"), 40)
        score = _safe_int(video.get("score"), 0, 0, 10)
        mood = self._clean_external_share_source_field(video.get("mood"), 24)
        comment = self._clean_external_share_source_field(video.get("comment"), 120)
        review = self._clean_external_share_source_field(video.get("review"), 180)
        source = self._clean_external_share_source_field(video.get("source"), 40)
        memory_context = video.get("memory_context") if isinstance(video.get("memory_context"), list) else []
        memory_lines = [
            text
            for item in memory_context
            for text in [self._clean_external_share_source_field(item, 160)]
            if text
        ][:3]
        if not title and not bvid and not comment and not review:
            return ""
        parts = [
            "B站视频分享线索",
            f"标题：{title}" if title else "",
            f"链接：https://www.bilibili.com/video/{bvid}" if bvid else "",
            f"UP：{up_name}" if up_name else "",
            f"评分：{score}/10" if score else "",
            f"心情：{mood}" if mood else "",
            f"短评：{comment}" if comment else "",
            f"回味：{review}" if review else "",
            f"来源：{source}" if source else "",
            "BiliBot记忆：" + " / ".join(memory_lines) if memory_lines else "",
        ]
        return "\n".join(part for part in parts if part)

    def _format_news_action_context(self, user: dict[str, Any]) -> str:
        news = user.get("news_context")
        if not isinstance(news, dict):
            return ""
        if _now_ts() - _safe_float(news.get("created_ts"), 0) > 8 * 3600:
            return ""
        topic = self._clean_external_share_source_field(news.get("topic"), 60)
        headline = self._clean_external_share_source_field(news.get("headline"), 100)
        source = self._clean_external_share_source_field(news.get("selected_source"), 40)
        impression = self._clean_external_share_source_field(news.get("impression"), 240)
        link = self._clean_external_share_source_field(news.get("selected_link"), 400)
        self_link = news.get("self_link") if isinstance(news.get("self_link"), dict) else {}
        self_link_text = self._clean_external_share_source_field(self_link.get("self_link") if isinstance(self_link, dict) else "", 180)
        self_link_tone = self._clean_external_share_source_field(news.get("share_tone") or (self_link.get("tone") if isinstance(self_link, dict) else ""), 80)
        self_link_boundary = self._clean_external_share_source_field(news.get("share_boundary") or (self_link.get("boundary") if isinstance(self_link, dict) else ""), 160)
        if not topic and not headline and not link and not impression:
            return ""
        parts = [
            "新闻阅读线索",
            f"话题：{topic}" if topic else "",
            f"标题：{headline}" if headline else "",
            f"来源：{source}" if source else "",
            f"内部印象：{impression}" if impression else "",
            f"和自己有关的地方：{self_link_text}" if self_link_text else "",
            f"表达气质：{self_link_tone}" if self_link_tone else "",
            f"额外边界：{self_link_boundary}" if self_link_boundary else "",
            f"链接：{link}" if link else "",
            "表达要求：不要像播报新闻,不要夸大或补充未知事实；按人格正常说话即可。",
        ]
        return "\n".join(part for part in parts if part)

    def _format_web_exploration_action_context(self, user: dict[str, Any]) -> str:
        exploration = user.get("web_exploration_context")
        if not isinstance(exploration, dict):
            return ""
        if _now_ts() - _safe_float(exploration.get("created_ts"), 0) > 10 * 3600:
            return ""
        query = self._clean_external_share_source_field(exploration.get("query"), 80)
        topic = self._clean_external_share_source_field(exploration.get("topic"), 80)
        note = self._clean_external_share_source_field(exploration.get("note"), 260)
        source_title = self._clean_external_share_source_field(exploration.get("source_title"), 120)
        source_url = self._clean_external_share_source_field(exploration.get("source_url"), 420)
        source_platform = self._external_share_platform_from_url(source_url)
        reason = self._clean_external_share_source_field(exploration.get("reason"), 140)
        self_link = exploration.get("self_link") if isinstance(exploration.get("self_link"), dict) else {}
        self_link_text = self._clean_external_share_source_field(self_link.get("self_link") if isinstance(self_link, dict) else "", 180)
        self_link_tone = self._clean_external_share_source_field(exploration.get("share_tone") or (self_link.get("tone") if isinstance(self_link, dict) else ""), 80)
        self_link_boundary = self._clean_external_share_source_field(exploration.get("share_boundary") or (self_link.get("boundary") if isinstance(self_link, dict) else ""), 160)
        if not query and not topic and not note and not source_title and not source_url:
            return ""
        parts = [
            "网页探索线索",
            f"搜索词：{query}" if query else "",
            f"为什么想查：{reason}" if reason else "",
            f"探索主题：{topic}" if topic else "",
            f"留下的印象：{note}" if note else "",
            f"和自己有关的地方：{self_link_text}" if self_link_text else "",
            f"表达气质：{self_link_tone}" if self_link_tone else "",
            f"额外边界：{self_link_boundary}" if self_link_boundary else "",
            f"参考来源：{source_title}" if source_title else "",
            f"来源平台（以链接域名为准）：{source_platform}" if source_platform else "",
            f"链接：{source_url}" if source_url else "",
            "表达要求：自然地向用户分享自己刚看的这条内容。标题、印象和链接只是事实参考，按当前人格正常说话，不要照抄字段。",
        ]
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _external_share_platform_from_url(url: Any) -> str:
        value = str(url or "").strip()
        if not value:
            return ""
        try:
            parsed = urlparse(value if "://" in value else f"//{value}")
            hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
        except Exception:
            hostname = ""
        if not hostname:
            return ""
        platform_domains = (
            (("bilibili.com", "b23.tv"), "B站"),
            (("douyin.com", "iesdouyin.com"), "抖音"),
            (("xiaohongshu.com", "xhslink.com"), "小红书"),
            (("weibo.com", "weibo.cn"), "微博"),
            (("zhihu.com",), "知乎"),
            (("youtube.com", "youtu.be"), "YouTube"),
            (("reddit.com", "redd.it"), "Reddit"),
            (("github.com",), "GitHub"),
            (("toutiao.com",), "今日头条"),
        )
        for domains, label in platform_domains:
            if any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains):
                return label
        return ""

    @staticmethod
    def _external_share_claimed_platform(text: Any) -> str:
        value = _single_line(text, 320)
        patterns = (
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}(?:B站|哔哩哔哩)|(?:B站|哔哩哔哩)(?:视频|上|里|《)", "B站"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}抖音|抖音(?:视频|上|里|《)", "抖音"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}小红书|小红书(?:笔记|上|里|《)", "小红书"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}微博|微博(?:上|里|《)", "微博"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}知乎|知乎(?:上|里|《)", "知乎"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}YouTube|YouTube(?:上|里)", "YouTube"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}Reddit|Reddit(?:上|里|《)", "Reddit"),
        )
        for pattern, label in patterns:
            if re.search(pattern, value, flags=re.I):
                return label
        return ""

    def _proactive_link_platform_mismatch_reason(self, text: Any) -> str:
        cleaned = _single_line(text, 600)
        claimed_platform = self._external_share_claimed_platform(cleaned)
        if not cleaned or not claimed_platform:
            return ""
        links = re.findall(r"https?://[^\s，。！？!?；;）)】\]》>]+", cleaned, flags=re.I)
        for link in links:
            actual_platform = self._external_share_platform_from_url(link)
            if actual_platform == claimed_platform:
                continue
            try:
                hostname = str(urlparse(link).hostname or "").strip().lower()
            except Exception:
                hostname = ""
            actual_label = actual_platform or hostname or "未知域名"
            return f"正文声称来源为{claimed_platform}，但链接实际属于{actual_label}"
        return ""

    def _user_asks_ai_daily_context(self, inbound_text: str) -> bool:
        text = str(inbound_text or "").strip()
        if not text:
            return False
        if any(
            token in text
            for token in (
                "AI日报", "ai日报", "AI 日报", "ai 日报",
                "AI早报", "ai早报", "AI 早报", "ai 早报",
                "大模型日报", "大模型早报", "人工智能日报", "人工智能早报",
            )
        ):
            return True
        lowered = text.lower()
        if any(token in lowered for token in ("ai daily", "daily ai", "llm daily", "ai digest")):
            return True
        return bool(("日报" in text or "早报" in text) and re.search(r"(ai|llm|大模型|人工智能|模型)", text, flags=re.IGNORECASE))

    def _ai_daily_query_requires_freshness(self, inbound_text: str) -> bool:
        text = str(inbound_text or "").strip()
        if not text:
            return False
        return bool(re.search(r"(今天|今日|今早|刚刚|刚才|最新|现在)", text, flags=re.IGNORECASE))

    def _select_ai_daily_digest_item(self, ai_state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        digest = ai_state.get("last_digest") if isinstance(ai_state.get("last_digest"), dict) else {}
        selected_item: dict[str, Any] = {}
        digest_items = digest.get("items") if isinstance(digest.get("items"), list) else []
        selected_key = _single_line(digest.get("selected_key"), 80)
        for candidate in digest_items:
            if not isinstance(candidate, dict):
                continue
            if selected_key and _single_line(candidate.get("key"), 80) == selected_key:
                selected_item = candidate
                break
        if not selected_item and digest_items and isinstance(digest_items[0], dict):
            selected_item = digest_items[0]
        return digest, selected_item

    def _user_asks_news_context(self, inbound_text: str) -> bool:
        text = str(inbound_text or "").strip()
        if not text:
            return False
        if any(token in text for token in ("新闻", "早报", "热点", "时讯", "资讯", "新消息")):
            return True
        lowered = text.lower()
        return any(token in lowered for token in ("ai news", "llm news", "daily ai", "tech news"))

    def _user_asks_web_exploration_context(self, inbound_text: str) -> bool:
        text = str(inbound_text or "").strip()
        if not text:
            return False
        if re.search(r"(主动搜索|网页探索|搜索记录|浏览记录|上网).{0,16}(什么|啥|哪|记录|看|查|搜|了解|发现)|((最近|刚才|今天|这两天|这会儿).{0,16}(搜|查|上网|浏览|了解|看了啥|看了什么|发现了什么))|你.{0,12}(搜了什么|查了什么|上网看了什么|上网看了啥|发现了什么新东西)", text):
            return True
        lowered = text.lower()
        return any(token in lowered for token in ("web exploration", "recent search", "search history", "browsing history"))

    def _format_recent_web_exploration_context_for_reply(
        self,
        inbound_text: str = "",
    ) -> str:
        section = self._format_recent_web_exploration_context_prompt_section(inbound_text)
        if section is None:
            return ""
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_recent_web_exploration_context_prompt_section(
        self,
        inbound_text: str = "",
    ) -> PromptSection | None:
        if not runtime_persona_setting(self, "enable_web_exploration", False):
            return None
        if not self._user_asks_web_exploration_context(inbound_text):
            return None
        def build_section(content: str) -> PromptSection:
            return prompt_section(
                key="web_exploration.recent",
                title="主动搜索上下文",
                source="web_exploration",
                content=content,
            )

        state = self.data.get("web_exploration") if isinstance(self.data.get("web_exploration"), dict) else {}
        digest = state.get("last_digest") if isinstance(state.get("last_digest"), dict) else {}
        notes = state.get("notes") if isinstance(state.get("notes"), list) else []
        latest_results = state.get("latest_results") if isinstance(state.get("latest_results"), list) else []
        if not digest and not notes and not latest_results:
            body = (
                "用户正在询问你最近主动搜索/上网探索过什么,但当前没有可用的主动搜索记录。请自然说明自己最近还没搜到能说的东西,不要编造搜索内容。"
            )
            return build_section(body)
        rows: list[str] = []
        if digest:
            rows.append(
                "最近一次搜索："
                + "｜".join(
                    part
                    for part in (
                        f"搜索词：{_single_line(digest.get('query'), 90)}" if _single_line(digest.get("query"), 90) else "",
                        f"主题：{_single_line(digest.get('topic'), 90)}" if _single_line(digest.get("topic"), 90) else "",
                        f"动机：{_single_line(digest.get('reason'), 140)}" if _single_line(digest.get("reason"), 140) else "",
                        f"笔记：{_single_line(digest.get('note'), 240)}" if _single_line(digest.get("note"), 240) else "",
                        f"来源：{_single_line(digest.get('source_title'), 120)}" if _single_line(digest.get("source_title"), 120) else "",
                    )
                    if part
                )
            )
        for item in reversed([item for item in notes if isinstance(item, dict)][-4:]):
            query = _single_line(item.get("query"), 90)
            topic = _single_line(item.get("topic"), 90)
            note = _single_line(item.get("note") or item.get("summary") or item.get("impression"), 180)
            reason = _single_line(item.get("reason"), 100)
            if query or topic or note:
                rows.append("- " + "｜".join(part for part in (f"搜索词：{query}" if query else "", topic, reason, note) if part))
        if latest_results:
            result_rows = []
            for item in latest_results[:4]:
                if not isinstance(item, dict):
                    continue
                title = _single_line(item.get("title"), 120)
                snippet = _single_line(item.get("snippet"), 160)
                if title:
                    result_rows.append("- " + "｜".join(part for part in (title, snippet) if part))
            if result_rows:
                rows.append("最近一次结果摘录：")
                rows.extend(result_rows)
        body = (
            "用户正在询问你最近主动搜索/网页探索过什么。下面是真实搜索记录；回答只能基于这些内容,不要编造额外搜索、来源或结论。"
            "可以用第一人称自然概括“我刚查了/我之前搜到”,但不要说成后台系统日志。\n"
            + "\n".join(rows[:12])
        )
        return build_section(body)

    def _format_recent_ai_daily_context_for_reply(
        self,
        inbound_text: str = "",
    ) -> str:
        section = self._format_recent_ai_daily_context_prompt_section(inbound_text)
        if section is None:
            return ""
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )
