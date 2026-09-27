# -*- coding: utf-8 -*-
"""NewsExplorationNewsSummaryWishMixin。

由 tools/split_mixin_domain.py 从 news_exploration.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 355 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 NewsExplorationMixin）。
"""
from __future__ import annotations

from .news_exploration_shared import _persona_provider_id
from .news_exploration_shared import Any
from .news_exploration_shared import PromptRenderMode
from .news_exploration_shared import _now_ts
from .news_exploration_shared import _safe_float
from .news_exploration_shared import _safe_int
from .news_exploration_shared import _single_line
from .news_exploration_shared import datetime
from .news_exploration_shared import prompt_section
from .news_exploration_shared import random
from .news_exploration_shared import re
from .news_exploration_shared import render_prompt_sections
from .news_exploration_shared import runtime_persona_setting



class NewsExplorationNewsSummaryWishMixin:
    """NewsExplorationNewsSummaryWishMixin（从 NewsExplorationMixin 拆出）。"""


    async def _summarize_news_items(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        if not items:
            return {}
        provider_id = self._task_provider(
            _persona_provider_id(self, "NEWS_PROVIDER_ID", "news_provider_id", "fast"),
            _persona_provider_id(self, "NARRATION_PROVIDER_ID", "narration_provider_id", "fast"),
            _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
        )
        if not provider_id:
            return self._news_fallback_digest(items)
        lines = []
        for idx, item in enumerate(items[:8], 1):
            title = _single_line(item.get("title"), 120)
            source = _single_line(item.get("source"), 24)
            if item.get("article_readable") and item.get("article_text"):
                article_text = str(item.get("article_text") or "").strip()
                lines.append(
                    f"{idx}. [{source}] {title}\n"
                    f"文字版链接：{_single_line(item.get('link'), 220)}\n"
                    f"完整文字版正文：\n{article_text}"
                )
                continue
            if item.get("video_subtitle_readable") and item.get("video_subtitle_text"):
                subtitle_text = str(item.get("video_subtitle_text") or "").strip()
                lines.append(
                    f"{idx}. [{source}] {title}\n"
                    f"视频链接：{_single_line(item.get('video_link') or item.get('link'), 220)}\n"
                    f"视频字幕正文：\n{subtitle_text}"
                )
                continue
            if item.get("video_context_text"):
                context_text = str(item.get("video_context_text") or "").strip()
                lines.append(
                    f"{idx}. [{source}] {title}\n"
                    f"视频链接：{_single_line(item.get('video_link') or item.get('link'), 220)}\n"
                    f"视频公开信息：\n{context_text}"
                )
                continue
            summary = _single_line(item.get("summary"), 180)
            lines.append(f"{idx}. [{source}] {title}" + (f"｜{summary}" if summary else ""))
        prompt_body = f"""
请作为 Bot 资料归档新闻后的内部整理,从下面新闻里挑一条最适合轻轻提起的内容。

要求：
1. 不要写成新闻播报,要像“刚看到一条消息后脑子里的印象”。
2. 不要夸大事实,不要补充列表外没有的信息。
3. 如果候选里包含“完整文字版正文”,必须以完整正文为准；如果没有文字版但包含“视频字幕正文”,必须以视频字幕为准；如果只有“视频公开信息”,可以参考 UP 主、分区、时长、简介、标签和热门评论,不要只看标题或链接。
4. 如果都不适合分享,仍挑一个最普通、风险最低的话题。
5. 输出 JSON,字段为 topic, headline, impression, selected_index。
6. topic 20字以内；headline 80字以内；impression 160字以内。

新闻候选：
{chr(10).join(lines)}
""".strip()
        prompt = render_prompt_sections(
            [
                prompt_section(
                    key="news.digest",
                    title="新闻内部整理",
                    source="news_exploration",
                    content=prompt_body,
                )
            ],
            mode=PromptRenderMode.BODY_ONLY,
        )
        raw = await self._llm_call(prompt, max_tokens=260, provider_id=provider_id, task="news_digest")
        parsed = self._parse_json_object(raw)
        if not isinstance(parsed, dict):
            return self._news_fallback_digest(items)
        index = _safe_int(parsed.get("selected_index"), 1, 1, len(items[:8])) - 1
        selected = items[index] if 0 <= index < len(items) else items[0]
        return {
            "topic": _single_line(parsed.get("topic"), 40) or _single_line(selected.get("title"), 40),
            "headline": _single_line(parsed.get("headline"), 100) or _single_line(selected.get("title"), 100),
            "impression": _single_line(parsed.get("impression"), 240) or _single_line(selected.get("summary"), 180),
            "selected_key": _single_line(selected.get("key"), 32),
            "selected_link": _single_line(selected.get("link"), 400),
            "selected_source": _single_line(selected.get("source"), 40),
            "items": items[:8],
            "created_ts": _now_ts(),
        }

    def _external_event_self_link_provider_id(self) -> str:
        return self._task_provider(
            _persona_provider_id(self, "NEWS_PROVIDER_ID", "news_provider_id", "fast"),
            _persona_provider_id(
                self, "WEB_EXPLORATION_PROVIDER_ID", "web_exploration_provider_id", "fast"
            ),
            _persona_provider_id(self, "NARRATION_PROVIDER_ID", "narration_provider_id", "fast"),
            _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
        )

    def _format_external_event_stable_self_context(self) -> str:
        model_lines = []
        plugin_main = self._task_provider(
            _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex")
        )
        if plugin_main:
            model_lines.append(f"插件主模型：{self._provider_identity_label(plugin_main)}")
        news_provider_id = _persona_provider_id(self, "NEWS_PROVIDER_ID", "news_provider_id", "fast")
        if news_provider_id:
            model_lines.append(f"新闻整理模型：{self._provider_identity_label(news_provider_id)}")
        web_provider_id = _persona_provider_id(
            self, "WEB_EXPLORATION_PROVIDER_ID", "web_exploration_provider_id", "fast"
        )
        if web_provider_id:
            model_lines.append(f"主动搜索整理模型：{self._provider_identity_label(web_provider_id)}")
        return "\n".join(
            part
            for part in (
                f"Bot 名称：{runtime_persona_setting(self, 'bot_name', '小星')}",
                "当前模型环境：" + "；".join(model_lines) if model_lines else "",
                f"人格：{_single_line(self._get_default_persona_prompt(), 900)}",
                self._format_worldview_adaptation_prompt(),
            )
            if part
        )

    def _format_external_event_current_self_context(self) -> str:
        state = self.data.get("daily_state", {}) if isinstance(self.data.get("daily_state"), dict) else {}
        current_item = self._news_current_agenda_item()
        mood = _single_line(state.get("mood_bias"), 40)
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        activity = _single_line((current_item or {}).get("activity"), 120)
        return "\n".join(
            part
            for part in (
                f"当前状态：{mood or '平稳'}，心理能量 {energy}/100",
                f"当前日程：{activity}" if activity else "",
            )
            if part
        )

    def _format_external_event_self_context(self) -> str:
        stable = self._format_external_event_stable_self_context()
        current = self._format_external_event_current_self_context()
        return "\n".join(part for part in (stable, current) if part)

    def _external_event_fallback_wish(self, payload: dict[str, Any], *, source_type: str) -> dict[str, Any]:
        title = _single_line(payload.get("headline") or payload.get("topic") or payload.get("source_title"), 100)
        note = _single_line(payload.get("impression") or payload.get("note") or payload.get("summary"), 220)
        haystack = f"{title} {note}".lower()
        self_tokens = (
            "ai", "模型", "大模型", "llm", "视觉", "多模态", "上下文", "记忆", "agent", "智能体",
            "机器人", "bot", "搜索", "新闻", "bilibili", "doubao", "deepseek", "glm", "gemini",
            "claude", "gpt", "qwen", "豆包", "火山", "openai",
        )
        matched = []
        for token in self_tokens:
            if token.isascii():
                if re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", haystack):
                    matched.append(token)
            elif token in haystack:
                matched.append(token)
        matched = [
            token
            for token in matched
            if token.isascii()
            or not any(token != other and not other.isascii() and token in other for other in matched)
        ]
        relevance = min(10, 3 + len(matched) * 2)
        desire = min(10, 4 + len(matched))
        return {
            "relevance": relevance,
            "desire": desire,
            # A lone generic word such as AI/model/search is not enough to
            # manufacture proactive sharing intent when no judgement model is
            # available.  Stronger multi-signal matches can still pass.
            "should_share": relevance >= 7 and desire >= 7,
            "share_probability": min(0.85, 0.18 + relevance * 0.055 + desire * 0.035),
            "motive": _single_line(
                f"刚读到的{('新闻' if source_type == 'news' else '搜索结果')}和自己的能力、兴趣或最近状态有一点关系,想按人格私下找用户说说",
                180,
            ),
            "self_link": _single_line("这条外界消息可能影响我会做什么、想学什么、想试什么,或让我联想到自己的状态。", 180),
            "tone": "有分享欲,自然一点",
            "boundary": "不要说成系统通知；不要要求用户一定做什么；只表达自己的反应和一点想法。",
        }

    def _external_event_life_opportunity_wish(self, payload: dict[str, Any], *, source_type: str) -> dict[str, Any]:
        title = _single_line(payload.get("headline") or payload.get("topic") or payload.get("source_title"), 120)
        note = _single_line(payload.get("impression") or payload.get("note") or payload.get("summary"), 260)
        haystack = f"{title} {note}".lower()
        tokens = (
            "免单", "免费", "送", "抽奖", "福利", "优惠", "券", "红包", "周年庆",
            "奶茶", "茶百道", "咖啡", "甜品", "饮品", "碰碰运气", "试试", "薅",
        )
        if not any(token in haystack for token in tokens):
            return {}
        return {
            "relevance": 8,
            "desire": 8,
            "should_share": True,
            "share_probability": 0.86,
            "self_link": "这像是能和用户一起碰碰运气的小福利，和日常吃喝、撒娇分享都很贴近。",
            "motive": "这条活动是实在的生活福利",
            "tone": "轻快,有点心动,不要像广告",
            "boundary": "不要保证一定抢到；不要催促用户；如果正在休息，就当作醒来后顺口提起。",
            "created_ts": _now_ts(),
            "source_type": source_type,
            "boost_reason": "life_opportunity",
        }

    async def _build_external_event_wish(self, payload: dict[str, Any], *, source_type: str) -> dict[str, Any]:
        if not runtime_persona_setting(self, "enable_external_event_self_link", True) or not isinstance(payload, dict):
            return {}
        cached = self._cached_external_event_wish(payload, source_type=source_type)
        if cached:
            return cached
        provider_id = self._external_event_self_link_provider_id()
        fallback = self._external_event_fallback_wish(payload, source_type=source_type)
        life_wish = self._external_event_life_opportunity_wish(payload, source_type=source_type)
        if not provider_id:
            result = life_wish or fallback
            self._remember_external_event_wish_cache(payload, result, source_type=source_type)
            return result
        title = _single_line(payload.get("headline") or payload.get("topic") or payload.get("source_title"), 120)
        impression = _single_line(payload.get("impression") or payload.get("note") or payload.get("summary"), 360)
        source = _single_line(payload.get("selected_source") or payload.get("source_title") or payload.get("source"), 80)
        link = _single_line(payload.get("selected_link") or payload.get("source_url") or payload.get("link"), 420)
        stable_self_context = self._format_external_event_stable_self_context()
        current_self_context = self._format_external_event_current_self_context()
        instruction = """
请判断 Bot 刚读到的一条外界信息,是否会在它心里产生“和我自己有关,想找用户说说”的主动意愿。

这不是写给用户的消息,只是内部决策。请不要把关键词当硬触发,要根据人格、当前状态、能力边界、兴趣、近期见闻和用户关系判断。

判断维度：
1. 自我关联：这条信息是否和 Bot 的能力、模型、创作、日程、兴趣、世界观身份、正在做的事或与用户的关系有关。
2. 意愿强度：Bot 是否会产生分享欲、求助欲、试试看、撒娇、吐槽、担心、好奇、想让用户知道等动机。
3. 主动边界：是否适合现在主动私聊；不适合就 should_share=false。
4. 如果适合, motive 要写成内部动机,不是最终发言。不要写插件、后台、任务、JSON 这类词。

输出 JSON：
{{
  "relevance": 0到10,
  "desire": 0到10,
  "should_share": true或false,
  "share_probability": 0到1,
  "self_link": "这件事和 Bot 自己有什么关系,80字内",
  "motive": "为什么想找用户说,100字内",
  "tone": "适合的表达气质,如撒娇/好奇/嘴硬/认真/轻轻分享",
  "boundary": "主动时要避开的表达,80字内"
}}
""".strip()

        prompt = "\n\n".join(
            part
            for part in (
                render_prompt_sections(
                    [
                        prompt_section(
                            key="external_event.self_link.instruction",
                            title="外界信息自我关联判断",
                            source="news_exploration",
                            content=instruction,
                        )
                    ],
                    mode=PromptRenderMode.BODY_ONLY,
                ),
                render_prompt_sections(
                    [
                        prompt_section(key="external_event.stable_self", title="Bot 稳定自我上下文", source="news_exploration", content=stable_self_context),
                        prompt_section(key="external_event.source_type", title="来源类型", source="news_exploration", content=source_type),
                        prompt_section(
                            key="external_event.material",
                            title="外界信息",
                            source="news_exploration",
                            content=f"标题：{title}\n来源：{source}\n链接：{link}\n内部印象：{impression}",
                        ),
                        prompt_section(key="external_event.current_self", title="Bot 当前短时状态", source="news_exploration", content=current_self_context),
                    ],
                    mode=PromptRenderMode.LABELED_BLOCK,
                ),
            )
            if part
        )
        raw = await self._llm_call(prompt, max_tokens=360, provider_id=provider_id, task="external_event_self_link")
        parsed = self._parse_json_object(raw)
        if not isinstance(parsed, dict):
            result = life_wish or fallback
            self._remember_external_event_wish_cache(payload, result, source_type=source_type)
            return result
        relevance = _safe_int(parsed.get("relevance"), fallback["relevance"], 0, 10)
        desire = _safe_int(parsed.get("desire"), fallback["desire"], 0, 10)
        probability = _safe_float(parsed.get("share_probability"), fallback["share_probability"])
        result = {
            "relevance": relevance,
            "desire": desire,
            "should_share": bool(parsed.get("should_share", relevance >= 5 and desire >= 5)),
            "share_probability": max(0.0, min(1.0, probability)),
            "self_link": _single_line(parsed.get("self_link"), 180) or fallback["self_link"],
            "motive": _single_line(parsed.get("motive"), 180) or fallback["motive"],
            "tone": _single_line(parsed.get("tone"), 60) or fallback["tone"],
            "boundary": _single_line(parsed.get("boundary"), 140) or fallback["boundary"],
            "created_ts": _now_ts(),
            "source_type": source_type,
        }
        # User-configured relaxation: when the judgement model says
        # should_share=false but relevance/desire clear a configured floor,
        # promote the item to shareable instead of dropping it silently.
        # boost_reason marks strong self-link so downstream probability/idle
        # boosts apply. Life-opportunity wishes still win below.
        override_min_rel = _safe_int(
            runtime_persona_setting(self, "external_event_self_link_override_min_relevance", 0),
            0,
            0,
            10,
        )
        override_min_des = _safe_int(
            runtime_persona_setting(self, "external_event_self_link_override_min_desire", 0),
            0,
            0,
            10,
        )
        if (
            not result["should_share"]
            and override_min_rel > 0
            and override_min_des > 0
            and relevance >= override_min_rel
            and desire >= override_min_des
        ):
            result["should_share"] = True
            result["share_probability"] = max(
                result["share_probability"],
                _safe_float(
                    runtime_persona_setting(
                        self, "external_event_self_link_override_probability", 0.6
                    ),
                    0.6,
                ),
            )
            result["boost_reason"] = "override_by_user_threshold"
        if life_wish:
            if not result["should_share"] or result["share_probability"] < life_wish["share_probability"]:
                result = {
                    **result,
                    "relevance": max(result["relevance"], life_wish["relevance"]),
                    "desire": max(result["desire"], life_wish["desire"]),
                    "should_share": True,
                    "share_probability": max(result["share_probability"], life_wish["share_probability"]),
                    "self_link": result["self_link"] if result["relevance"] >= 5 else life_wish["self_link"],
                    "motive": life_wish["motive"],
                    "tone": life_wish["tone"],
                    "boundary": life_wish["boundary"],
                    "boost_reason": life_wish["boost_reason"],
                }
        self._remember_external_event_wish_cache(payload, result, source_type=source_type)
        return result

    def _bot_currently_bored_enough_for_news(self) -> bool:
        now_dt = datetime.now()
        if now_dt.hour < 7 or now_dt.hour >= 24:
            return False
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        mood = _single_line(state.get("mood_bias") if isinstance(state, dict) else "", 24)
        current_item = self._news_current_agenda_item()
        activity = _single_line((current_item or {}).get("activity"), 80)
        text = f"{mood} {activity}"
        if any(token in text for token in ("无聊", "发呆", "摸鱼", "休息", "闲", "空", "刷")):
            return True
        return 30 <= energy <= 80 and random.random() < 0.28
