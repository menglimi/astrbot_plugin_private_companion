# -*- coding: utf-8 -*-
"""NewsExplorationWebExplorationTrendsTriggerMixin。

由 tools/split_mixin_domain.py 从 news_exploration.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 674 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 NewsExplorationMixin）。
"""
from __future__ import annotations

from .news_exploration_shared import _persona_provider_id, logger
from .news_exploration_shared import Any
from .news_exploration_shared import PLUGIN_NAME
from .news_exploration_shared import PromptRenderMode
from .news_exploration_shared import _now_ts
from .news_exploration_shared import _safe_float
from .news_exploration_shared import _safe_int
from .news_exploration_shared import _single_line
from .news_exploration_shared import asyncio
from .news_exploration_shared import hashlib
from .news_exploration_shared import prompt_section
from .news_exploration_shared import quote
from .news_exploration_shared import random
from .news_exploration_shared import re
from .news_exploration_shared import render_prompt_sections
from .news_exploration_shared import runtime_persona_setting



class NewsExplorationWebExplorationTrendsTriggerMixin:
    """NewsExplorationWebExplorationTrendsTriggerMixin（从 NewsExplorationMixin 拆出）。"""


    async def _run_astrbot_web_search(self, query: str, *, umo: str = "", topic: str = "general", usage: str = "general") -> list[dict[str, Any]]:
        cleaned_query = _single_line(query, 120)
        self._last_web_search_error = ""
        if not cleaned_query:
            return []
        if usage == "web_exploration" and self._custom_web_exploration_search_configured():
            return await self._run_custom_web_exploration_search(cleaned_query, topic=topic)
        settings = self._astrbot_web_search_provider_settings(umo)
        if not settings.get("web_search", False):
            return []
        provider = str(settings.get("websearch_provider") or "").strip()
        if provider == "default":
            return []
        cooldown = self._web_search_cooldown_remaining(provider)
        if cooldown > 0:
            self._last_web_search_error = f"web_search_cooldown:{provider}:{int(cooldown)}s"
            logger.info(
                "AstrBot 网页搜索冷却中,跳过请求: provider=%s retry=%ss query=%s",
                provider,
                int(cooldown),
                cleaned_query,
            )
            return []
        for key in (
            "websearch_tavily_key",
            "websearch_bocha_key",
            "websearch_brave_key",
            "websearch_firecrawl_key",
        ):
            value = settings.get(key)
            if isinstance(value, str):
                value = value.strip()
                settings[key] = [value] if value else []
        baidu_key = settings.get("websearch_baidu_app_builder_key")
        if isinstance(baidu_key, list):
            settings["websearch_baidu_app_builder_key"] = str(baidu_key[0] if baidu_key else "").strip()
        elif isinstance(baidu_key, str):
            settings["websearch_baidu_app_builder_key"] = baidu_key.strip()
        try:
            from astrbot.core.tools import web_search_tools as ws
            if provider == "tavily":
                payload = {
                    "query": cleaned_query,
                    "max_results": max(
                        5,
                        min(20, runtime_persona_setting(self, "web_exploration_max_results", 6)),
                    ),
                    "include_favicon": True,
                    "search_depth": "basic",
                    "topic": "news" if topic == "news" else "general",
                }
                if topic == "news":
                    payload["days"] = 7
                raw_results = await ws._tavily_search(settings, payload)
            elif provider == "bocha":
                raw_results = await ws._bocha_search(
                    settings,
                    {
                        "query": cleaned_query,
                        "count": max(
                            1,
                            min(50, runtime_persona_setting(self, "web_exploration_max_results", 6)),
                        ),
                        "summary": True,
                        "freshness": "noLimit",
                    },
                )
            elif provider == "brave":
                raw_results = await ws._brave_search(
                    settings,
                    {
                        "q": cleaned_query,
                        "count": max(
                            1,
                            min(20, runtime_persona_setting(self, "web_exploration_max_results", 6)),
                        ),
                        "country": "CN",
                        "search_lang": "zh-hans",
                    },
                )
            elif provider == "firecrawl":
                raw_results = await ws._firecrawl_search(
                    settings,
                    {
                        "query": cleaned_query,
                        "limit": max(
                            1,
                            min(20, runtime_persona_setting(self, "web_exploration_max_results", 6)),
                        ),
                        "sources": ["web"],
                    },
                )
            elif provider == "baidu_ai_search":
                raw_results = await ws._baidu_search(
                    settings,
                    {
                        "messages": [{"role": "user", "content": cleaned_query[:72]}],
                        "search_source": "baidu_search_v2",
                        "resource_type_filter": [
                            {
                                "type": "web",
                                "top_k": max(
                                    1,
                                    min(
                                        50,
                                        runtime_persona_setting(self, "web_exploration_max_results", 6),
                                    ),
                                ),
                            }
                        ],
                    },
                )
            else:
                return []
        except Exception as exc:
            self._last_web_search_error = _single_line(str(exc), 240)
            self._mark_web_search_cooldown(provider, exc)
            logger.warning("AstrBot 网页搜索失败: provider=%s query=%s err=%s", provider, cleaned_query, exc)
            return []
        results: list[dict[str, Any]] = []
        for item in raw_results or []:
            title = _single_line(getattr(item, "title", "") if not isinstance(item, dict) else item.get("title"), 140)
            url = _single_line(getattr(item, "url", "") if not isinstance(item, dict) else item.get("url"), 420)
            snippet = _single_line(getattr(item, "snippet", "") if not isinstance(item, dict) else item.get("snippet"), 360)
            if not title and not snippet:
                continue
            key = hashlib.sha1(f"{title}|{url}|{snippet}".encode("utf-8", errors="ignore")).hexdigest()[:16]
            results.append({"key": key, "title": title, "url": url, "snippet": snippet, "provider": provider})
        return results[: runtime_persona_setting(self, "web_exploration_max_results", 6)]

    def _web_exploration_recent_context(self) -> str:
        state = self.data.get("daily_state", {})
        mood = _single_line(state.get("mood_bias") if isinstance(state, dict) else "", 30)
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        current_item = self._news_current_agenda_item()
        activity = _single_line((current_item or {}).get("activity"), 80)
        recent_user = ""
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        latest_user = max(
            (item for item in users.values() if isinstance(item, dict)),
            key=lambda item: _safe_float(item.get("last_seen"), 0),
            default={},
        )
        if isinstance(latest_user, dict):
            recent_user = _single_line(latest_user.get("last_user_message"), 120)
        news_state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        news_digest = news_state.get("last_digest") if isinstance(news_state.get("last_digest"), dict) else {}
        news_topic = _single_line(news_digest.get("topic") or news_digest.get("headline"), 100)
        return "\n".join(
            part
            for part in (
                f"当前状态：{mood or '平稳'}，能量 {energy}/100",
                f"当前日程：{activity}" if activity else "",
                f"最近用户提到：{recent_user}" if recent_user else "",
                f"今日新闻见闻：{news_topic}" if news_topic else "",
                "兴趣倾向配置：{}".format(
                    _single_line(
                        runtime_persona_setting(
                            self,
                            "web_exploration_interests",
                            "按 Bot 人格自行决定；可偏向最近聊天、日程、人设兴趣、作品、技术、生活小知识、流行梗、时讯、新鲜事物。",
                        ),
                        240,
                    )
                ),
            )
            if part
        )

    def _hot_trend_source_names(self) -> list[str]:
        raw = str(runtime_persona_setting(self, "news_hot_sources", "weibo,hackernews") or "")
        names = []
        for item in re.split(r"[,，\n]+", raw):
            name = item.strip().lower()
            if name in {"weibo", "微博", "微博热搜"}:
                name = "weibo"
            elif name in {"hn", "hackernews", "hacker news"}:
                name = "hackernews"
            else:
                continue
            if name not in names:
                names.append(name)
        return names or ["weibo", "hackernews"]

    def _hot_trend_key(self, item: dict[str, Any]) -> str:
        raw = "|".join(
            _single_line(item.get(key), 240)
            for key in ("source", "title", "url")
            if _single_line(item.get(key), 240)
        )
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16] if raw else ""

    async def _fetch_weibo_hot_trends(self) -> list[dict[str, Any]]:
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=12)
            headers = {
                "User-Agent": f"{PLUGIN_NAME}/hot-trends",
                "Accept": "application/json",
                "Referer": "https://weibo.com/",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get("https://weibo.com/ajax/side/hotSearch") as resp:
                    if resp.status >= 400:
                        return []
                    data = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("微博热搜抓取失败: %s", exc)
            return []
        rows = (((data or {}).get("data") or {}).get("realtime") or []) if isinstance(data, dict) else []
        items: list[dict[str, Any]] = []
        hot_max_items = runtime_persona_setting(self, "news_hot_max_items", 12)
        for index, row in enumerate(rows[: max(1, hot_max_items)], 1):
            if not isinstance(row, dict):
                continue
            topic = _single_line(row.get("note") or row.get("word"), 80)
            if not topic:
                continue
            score = _safe_float(row.get("num") or row.get("raw_hot"), 0)
            item = {
                "source": "weibo",
                "title": topic,
                "snippet": f"微博热搜第 {index} 位" + (f"，热度 {int(score)}" if score > 0 else ""),
                "url": f"https://s.weibo.com/weibo?q={quote('#' + topic + '#')}",
                "score": score or max(1, hot_max_items - index + 1),
                "rank": index,
                "published": "",
                "published_ts": 0,
                "created_ts": _now_ts(),
            }
            item["summary"] = item["snippet"]
            item["link"] = item["url"]
            item["key"] = self._hot_trend_key(item)
            items.append(item)
        return items

    async def _fetch_hackernews_hot_trends(self) -> list[dict[str, Any]]:
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": f"{PLUGIN_NAME}/hot-trends"}) as session:
                async with session.get(
                    "https://hn.algolia.com/api/v1/search",
                    params={
                        "tags": "front_page",
                        "hitsPerPage": max(
                            3,
                            min(30, runtime_persona_setting(self, "news_hot_max_items", 12)),
                        ),
                    },
                ) as resp:
                    if resp.status >= 400:
                        return []
                    data = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("Hacker News 热点抓取失败: %s", exc)
            return []
        hits = data.get("hits") if isinstance(data, dict) else []
        items: list[dict[str, Any]] = []
        for index, row in enumerate(
            (hits or [])[: max(1, runtime_persona_setting(self, "news_hot_max_items", 12))],
            1,
        ):
            if not isinstance(row, dict):
                continue
            title = _single_line(row.get("title") or row.get("story_title"), 120)
            if not title:
                continue
            object_id = _single_line(row.get("objectID"), 40)
            url = _single_line(row.get("url"), 420) or (f"https://news.ycombinator.com/item?id={object_id}" if object_id else "")
            points = _safe_float(row.get("points"), 0)
            comments = _safe_int(row.get("num_comments"), 0, 0, 999999)
            item = {
                "source": "hackernews",
                "title": title,
                "snippet": f"Hacker News 首页热点，{int(points)} points，{comments} comments",
                "url": url,
                "score": points + comments * 0.35,
                "rank": index,
                "published": _single_line(row.get("created_at"), 80),
                "published_ts": self._news_parse_time(str(row.get("created_at") or "")),
                "created_ts": _now_ts(),
            }
            item["summary"] = item["snippet"]
            item["link"] = item["url"]
            item["key"] = self._hot_trend_key(item)
            items.append(item)
        return items

    async def _fetch_hot_trend_candidates(self) -> list[dict[str, Any]]:
        if not runtime_persona_setting(self, "enable_news_daily_hot_read", True):
            return []
        tasks = []
        for source in self._hot_trend_source_names():
            if source == "weibo":
                tasks.append(self._fetch_weibo_hot_trends())
            elif source == "hackernews":
                tasks.append(self._fetch_hackernews_hot_trends())
        if not tasks:
            return []
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for batch in batches:
            if not isinstance(batch, list):
                continue
            for item in batch:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                items.append(item)
        items.sort(key=lambda item: (_safe_float(item.get("score"), 0), -_safe_float(item.get("rank"), 999)), reverse=True)
        return items[
            : max(3, min(30, runtime_persona_setting(self, "news_hot_max_items", 12)))
        ]

    def _format_hot_trend_candidates_for_prompt(self, hot_items: list[dict[str, Any]]) -> str:
        lines = []
        for idx, item in enumerate(hot_items[:10], 1):
            lines.append(
                f"{idx}. [{_single_line(item.get('source'), 20)}] {_single_line(item.get('title'), 80)}"
                + (f"｜{_single_line(item.get('snippet'), 120)}" if _single_line(item.get("snippet"), 120) else "")
            )
        return "\n".join(lines)

    async def _choose_web_exploration_query(self, hot_items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        provider_id = self._task_provider(
            _persona_provider_id(
                self, "WEB_EXPLORATION_PROVIDER_ID", "web_exploration_provider_id", "fast"
            ),
            _persona_provider_id(self, "NEWS_PROVIDER_ID", "news_provider_id", "fast"),
            _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
        )
        fallback_topics = [
            "今天有什么有趣的新鲜事",
            "最近流行的网络梗",
            "最近值得了解的科技新闻",
            "适合睡前看的冷知识",
            "最近有什么有意思的游戏或动画消息",
        ]
        hot_items = hot_items or []
        if not provider_id:
            if hot_items:
                hot = random.choice(hot_items[: min(5, len(hot_items))])
                query = _single_line(hot.get("title"), 50) or random.choice(fallback_topics)
                return {"query": query, "reason": f"从{_single_line(hot.get('source'), 20)}热点里挑了一个想了解的话题", "topic": "news"}
            return {"query": random.choice(fallback_topics), "reason": "无模型时随机探索", "topic": "general"}
        hot_context = self._format_hot_trend_candidates_for_prompt(hot_items)
        instruction = f"""
请作为 Bot 自己,决定这会儿想上网搜索了解什么。

要求：
1. 选题要符合当前人格、状态、日程或最近聊天,可以是新闻、作品、技术、生活知识、流行梗、兴趣爱好、新鲜事物。
2. 不要总是搜新闻；也不要总是围着用户转。Bot 可以有自己的好奇心。
3. 如果热点候选里有符合 Bot 兴趣的内容,可以围绕它继续搜索；不合适也可以完全不选。
4. 搜索词要具体,适合直接丢给网页搜索。
5. 输出 JSON：query, reason, topic。topic 只能是 general 或 news。
6. query 40字以内；reason 80字以内。
""".strip()

        prompt = "\n\n".join(
            (
                render_prompt_sections(
                    [prompt_section(key="web_exploration.query.instruction", title="网页探索选题", source="news_exploration", content=instruction)],
                    mode=PromptRenderMode.BODY_ONLY,
                ),
                render_prompt_sections(
                    [
                        prompt_section(key="web_exploration.bot_name", title="Bot 名称", source="news_exploration", content=runtime_persona_setting(self, "bot_name", "小星")),
                        prompt_section(key="web_exploration.persona", title="人格", source="news_exploration", content=self._get_default_persona_prompt()),
                        prompt_section(key="web_exploration.context", title="当前上下文", source="news_exploration", content=self._web_exploration_recent_context()),
                        prompt_section(key="web_exploration.hot_candidates", title="公开热点候选", source="news_exploration", content=hot_context or "暂无可用热点候选"),
                    ],
                    mode=PromptRenderMode.LABELED_BLOCK,
                ),
            )
        )
        raw = await self._llm_call(prompt, max_tokens=220, provider_id=provider_id, task="web_exploration_query")
        parsed = self._parse_json_object(raw)
        if not isinstance(parsed, dict):
            query = random.choice(fallback_topics)
            return {"query": query, "reason": "模型选题失败后随机探索", "topic": "general"}
        topic = str(parsed.get("topic") or "general").strip().lower()
        if topic not in {"general", "news"}:
            topic = "general"
        query = _single_line(parsed.get("query"), 60) or random.choice(fallback_topics)
        return {"query": query, "reason": _single_line(parsed.get("reason"), 120), "topic": topic}

    async def _summarize_web_exploration(self, query_info: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
        if not results:
            return {}

        def fallback_digest() -> dict[str, Any]:
            first = results[0] if results else {}
            note = fallback_note() or _single_line(first.get("snippet"), 260) or _single_line(first.get("title"), 120)
            return {
                "query": _single_line(query_info.get("query"), 80),
                "topic": _single_line(first.get("title"), 80) or _single_line(query_info.get("query"), 80) or "主动搜索",
                "note": note or "这次搜索拿到了结果,但还没整理出清晰笔记。",
                "source_title": _single_line(first.get("title"), 120),
                "source_url": _single_line(first.get("url"), 420),
                "reason": _single_line(query_info.get("reason"), 120),
                "possible_share": False,
                "results": results[:6],
                "created_ts": _now_ts(),
                "fallback": True,
            }

        def fallback_note() -> str:
            parts = []
            for item in results[:3]:
                title = _single_line(item.get("title"), 90)
                snippet = _single_line(item.get("snippet"), 160)
                if title and snippet:
                    parts.append(f"{title}：{snippet}")
                elif title:
                    parts.append(title)
                elif snippet:
                    parts.append(snippet)
            return _single_line("；".join(parts), 360)

        provider_id = self._task_provider(
            _persona_provider_id(
                self, "WEB_EXPLORATION_PROVIDER_ID", "web_exploration_provider_id", "fast"
            ),
            _persona_provider_id(self, "NEWS_PROVIDER_ID", "news_provider_id", "fast"),
            _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
        )
        if not provider_id:
            first = results[0]
            return {
                "query": _single_line(query_info.get("query"), 80),
                "topic": _single_line(first.get("title"), 80),
                "note": _single_line(first.get("snippet"), 220) or fallback_note(),
                "source_title": _single_line(first.get("title"), 120),
                "source_url": _single_line(first.get("url"), 420),
                "reason": _single_line(query_info.get("reason"), 120),
                "results": results[:6],
                "created_ts": _now_ts(),
            }
        lines = []
        for idx, item in enumerate(results[:8], 1):
            lines.append(
                f"{idx}. {_single_line(item.get('title'), 120)}"
                + (f"｜{_single_line(item.get('snippet'), 180)}" if _single_line(item.get("snippet"), 180) else "")
                + (f"｜{_single_line(item.get('url'), 160)}" if _single_line(item.get("url"), 160) else "")
            )
        prompt_body = f"""
请把 Bot 这次自主网页探索整理成一条内部探索笔记。

要求：
1. 像 Bot 自己刚了解完后的留痕,不是给用户的正式回答。
2. 不要编造搜索结果外的事实；不确定就写成“看起来/大概/还想再查”。
3. 输出 JSON：topic, note, source_index, possible_share。
4. topic 40字以内；note 180字以内；source_index 是 1 到 {min(8, len(results))}；possible_share 是布尔值。

搜索动机：{_single_line(query_info.get('reason'), 120)}
搜索词：{_single_line(query_info.get('query'), 80)}

结果：
{chr(10).join(lines)}
""".strip()
        prompt = render_prompt_sections(
            [prompt_section(key="web_exploration.digest", title="网页探索内部笔记", source="news_exploration", content=prompt_body)],
            mode=PromptRenderMode.BODY_ONLY,
        )
        raw = await self._llm_call(prompt, max_tokens=280, provider_id=provider_id, task="web_exploration_digest")
        parsed = self._parse_json_object(raw)
        if not isinstance(parsed, dict):
            return fallback_digest()
        source_index = _safe_int(parsed.get("source_index"), 1, 1, len(results[:8])) - 1
        source = results[source_index] if 0 <= source_index < len(results) else results[0]
        note = (
            _single_line(parsed.get("note") or parsed.get("summary") or parsed.get("impression") or parsed.get("content"), 360)
            or _single_line(source.get("snippet"), 260)
            or fallback_note()
        )
        return {
            "query": _single_line(query_info.get("query"), 80),
            "topic": _single_line(parsed.get("topic"), 80) or _single_line(source.get("title"), 80),
            "note": note,
            "source_title": _single_line(source.get("title"), 120),
            "source_url": _single_line(source.get("url"), 420),
            "reason": _single_line(query_info.get("reason"), 120),
            "possible_share": bool(parsed.get("possible_share", True)),
            "results": results[:6],
            "created_ts": _now_ts(),
        }

    async def _maybe_trigger_web_exploration(self) -> None:
        if not (
            runtime_persona_setting(self, "enable_web_exploration", False)
            and runtime_persona_setting(self, "enable_web_exploration_boredom_search", True)
        ):
            return
        state = self.data.setdefault("web_exploration", {})
        if not isinstance(state, dict):
            self.data["web_exploration"] = {}
            state = self.data["web_exploration"]
        now = _now_ts()
        min_interval = max(
            1,
            runtime_persona_setting(self, "web_exploration_min_interval_hours", 8),
        ) * 3600
        if now - _safe_float(state.get("last_explore_at"), 0) < min_interval:
            return
        if now - _safe_float(state.get("last_probe_at"), 0) < 45 * 60:
            return
        if not self._bot_currently_bored_enough_for_news():
            return
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        target_users = [
            (str(uid), item)
            for uid, item in users.items()
            if isinstance(item, dict) and self._is_target_private_user(str(uid), item) and item.get("enabled", True) and item.get("umo")
        ]
        target_user = random.choice(target_users)[1] if target_users else {}
        target_umo = str((target_user.get("umo") if isinstance(target_user, dict) else "") or "")
        use_custom_search = self._custom_web_exploration_search_configured()
        search_umo = "" if use_custom_search else self._pick_available_web_search_umo(target_umo)
        if not (use_custom_search or search_umo):
            state["last_probe_at"] = now
            state["last_status"] = "web_search_disabled_or_unconfigured"
            self._save_data_sync(sections={"web_exploration"})
            return
        if random.random() > 0.46:
            return
        state["last_probe_at"] = now
        query_info = await self._choose_web_exploration_query()
        results: list[dict[str, Any]] = []
        results = await self._run_astrbot_web_search(
            str(query_info.get("query") or ""),
            umo=search_umo,
            topic=str(query_info.get("topic") or "general"),
            usage="web_exploration",
        )
        if not results:
            error_text = _single_line(getattr(self, "_last_web_search_error", ""), 240)
            state["last_status"] = "search_failed" if error_text else "no_results"
            state["last_query"] = query_info
            state["last_explore_at"] = now
            state["last_digest"] = {
                "query": _single_line(query_info.get("query"), 80),
                "topic": _single_line(query_info.get("query"), 80) or "主动搜索",
                "note": f"搜索调用失败：{error_text}" if error_text else "这次搜索没有拿到可用结果。",
                "reason": _single_line(query_info.get("reason"), 120),
                "results": [],
                "created_ts": now,
                "no_results": not bool(error_text),
                "search_failed": bool(error_text),
            }
            self._save_data_sync(sections={"web_exploration"})
            return
        digest = await self._summarize_web_exploration(query_info, results)
        notes = state.setdefault("notes", [])
        if not isinstance(notes, list):
            notes = []
            state["notes"] = notes
        wish = await self._build_external_event_wish(digest, source_type="web_exploration")
        if wish:
            digest["self_link"] = wish
        notes.append(
            {
                key: value
                for key, value in digest.items()
                if key not in {"results", "raw_results", "pages"}
            }
        )
        del notes[:-40]
        state["last_explore_at"] = now
        state["last_status"] = "explored"
        state["last_query"] = query_info
        state["last_digest"] = digest
        state["latest_results"] = results[:8]
        if digest.get("possible_share") and target_users:
            random.shuffle(target_users)
            for user_id, user in target_users[:3]:
                if now - _safe_float(user.get("last_seen"), 0) < max(
                    runtime_persona_setting(self, "idle_minutes", 60),
                    _safe_int(
                        runtime_persona_setting(self, "external_event_idle_minutes", 90),
                        90,
                        5,
                        1440,
                    ),
                ) * 60:
                    continue
                if now - _safe_float(user.get("last_web_exploration_share_at"), 0) < _safe_int(
                    runtime_persona_setting(self, "web_exploration_share_cooldown_hours", 10),
                    10,
                    0,
                    168,
                ) * 3600:
                    continue
                if self._external_link_share_cooldown_remaining(user, now=now) > 0:
                    continue
                if isinstance(wish, dict) and wish:
                    if (
                        now - _safe_float(user.get("last_external_event_self_link_at"), 0)
                        < runtime_persona_setting(self, "external_event_self_link_cooldown_hours", 12) * 3600
                    ):
                        continue
                    if not wish.get("should_share"):
                        continue
                    decision = self._external_event_share_decision(
                        user,
                        digest,
                        source_type="web_exploration",
                        wish=wish,
                        base_probability=runtime_persona_setting(
                            self, "web_exploration_share_probability", 0.18
                        ),
                        now=now,
                    )
                    if decision.get("duplicate") or not decision.get("should_share"):
                        continue
                    share_probability = max(
                        runtime_persona_setting(self, "web_exploration_share_probability", 0.18),
                        _safe_float(decision.get("probability"), 0.0),
                    )
                    share_probability *= runtime_persona_setting(
                        self, "external_event_self_link_probability", 0.62
                    )
                else:
                    decision = self._external_event_share_decision(
                        user,
                        digest,
                        source_type="web_exploration",
                        wish={"relevance": 4, "desire": 4, "should_share": True},
                        base_probability=runtime_persona_setting(
                            self, "web_exploration_share_probability", 0.18
                        ),
                        now=now,
                    )
                    if decision.get("duplicate") or not decision.get("should_share"):
                        continue
                    share_probability = runtime_persona_setting(
                        self, "web_exploration_share_probability", 0.18
                    )
                if random.random() > max(0.0, min(1.0, share_probability)):
                    continue
                self_link_motive = _single_line(wish.get("motive") if isinstance(wish, dict) else "", 180)
                self_link_tone = _single_line(wish.get("tone") if isinstance(wish, dict) else "", 60)
                self_link_boundary = _single_line(wish.get("boundary") if isinstance(wish, dict) else "", 140)
                accepted = self._offer_proactive_candidate(
                    user_id,
                    user,
                    {
                        "source": "web_exploration",
                        "reason": "web_exploration_share",
                        "action": "message",
                        "scheduled_ts": now + random.randint(12, 70) * 60,
                        "topic": _single_line(digest.get("topic"), 48) or "新发现",
                        "score": max(4 if self_link_motive else 3, _safe_int(decision.get("score"), 0, 0, 100)),
                        "motive": self_link_motive or "这条内容和对方可能感兴趣的东西相关",
                        "context_key": "web_exploration_context",
                        "context": {
                            **digest,
                            "share_tone": self_link_tone,
                            "share_boundary": self_link_boundary,
                            "share_decision": decision,
                        },
                    },
                )
                if accepted:
                    user["web_exploration_context"] = {
                        **digest,
                        "share_tone": self_link_tone,
                        "share_boundary": self_link_boundary,
                        "share_decision": decision,
                    }
                    user["last_web_exploration_share_at"] = now
                    self._note_external_link_candidate(user, now=now)
                    if isinstance(wish, dict) and wish:
                        user["last_external_event_self_link_at"] = now
                    self._remember_external_event(digest, source_type="web_exploration", reason="web_exploration_share")
                    break
        self._save_data_sync(sections={"web_exploration", "users", "proactive_candidate_pool", "external_event_pool", "external_event_self_link_cache"})
        logger.info("已完成一次网页探索: %s", _single_line(digest.get("topic"), 80))
