# -*- coding: utf-8 -*-
"""NewsExplorationWebSearchInfraCustomMixin。

由 tools/split_mixin_domain.py 从 news_exploration.py 机械抽取（17 个方法 + 0 个模块级名字 + 0 个类级赋值 / 376 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 NewsExplorationMixin）。
"""
from __future__ import annotations

from .news_exploration_shared import logger
from .news_exploration_shared import Any
from .news_exploration_shared import PLUGIN_NAME
from .news_exploration_shared import _now_ts
from .news_exploration_shared import _safe_float
from .news_exploration_shared import _single_line
from .news_exploration_shared import asyncio
from .news_exploration_shared import datetime
from .news_exploration_shared import hashlib
from .news_exploration_shared import json
from .news_exploration_shared import quote
from .news_exploration_shared import re
from .news_exploration_shared import runtime_persona_setting
from .news_exploration_shared import timedelta



class NewsExplorationWebSearchInfraCustomMixin:
    """NewsExplorationWebSearchInfraCustomMixin（从 NewsExplorationMixin 拆出）。"""


    def _astrbot_web_search_provider_settings(self, umo: str = "") -> dict[str, Any]:
        try:
            cfg = self.context.get_config(umo=umo) if umo else self.context.get_config()
        except Exception:
            try:
                cfg = self.context.get_config()
            except Exception:
                cfg = {}
        settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        return dict(settings or {}) if isinstance(settings, dict) else {}

    def _astrbot_web_search_available(self, umo: str = "") -> bool:
        settings = self._astrbot_web_search_provider_settings(umo)
        if not settings.get("web_search", False):
            return False
        provider = str(settings.get("websearch_provider") or "").strip()
        if provider == "tavily":
            return bool(settings.get("websearch_tavily_key"))
        if provider == "bocha":
            return bool(settings.get("websearch_bocha_key"))
        if provider == "brave":
            return bool(settings.get("websearch_brave_key"))
        if provider == "firecrawl":
            return bool(settings.get("websearch_firecrawl_key"))
        if provider == "baidu_ai_search":
            return bool(settings.get("websearch_baidu_app_builder_key"))
        return False

    def _web_search_candidate_umos(self) -> list[str]:
        candidates = [""]
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        for uid, item in users.items():
            if not isinstance(item, dict):
                continue
            if not self._is_target_private_user(str(uid), item) or not item.get("enabled", True):
                continue
            umo = str(item.get("umo") or "").strip()
            if umo and umo not in candidates:
                candidates.append(umo)
        return candidates

    def _astrbot_any_web_search_available(self) -> bool:
        return any(self._astrbot_web_search_available(umo) for umo in self._web_search_candidate_umos())

    @staticmethod
    def _normalize_web_exploration_api_base_url(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if raw.startswith(("http://", "https://")):
            return raw
        if re.match(r"^[a-z][a-z0-9+.-]*://", raw, flags=re.I):
            return ""
        local_pattern = r"^(localhost|127\.|10\.|172\.(1[6-9]|2\d|3[0-1])\.|192\.168\.|\[?::1\]?)"
        scheme = "http://" if re.match(local_pattern, raw, flags=re.I) else "https://"
        return f"{scheme}{raw}"

    def _custom_web_exploration_search_configured(self) -> bool:
        return bool(self._normalize_web_exploration_api_base_url(getattr(self, "web_exploration_api_base_url", "")))

    def _custom_web_exploration_get_url(self, base_url: str, query: str, *, topic: str = "general", model: str = "") -> str:
        cleaned_query = _single_line(query, 240)
        if not base_url or not cleaned_query:
            return ""
        max_results = str(
            max(1, min(20, runtime_persona_setting(self, "web_exploration_max_results", 6)))
        )
        values = {
            "query": cleaned_query,
            "q": cleaned_query,
            "keyword": cleaned_query,
            "keywords": cleaned_query,
            "wd": cleaned_query,
            "text": cleaned_query,
            "topic": "news" if topic == "news" else "general",
            "max_results": max_results,
            "limit": max_results,
            "count": max_results,
            "model": str(model or "").strip(),
        }
        formatted = base_url
        replaced = False
        for key, value in values.items():
            encoded = quote(str(value), safe="")
            for token in (f"{{{{{key}}}}}", f"{{{key}}}", f"${{{key}}}"):
                if token in formatted:
                    formatted = formatted.replace(token, encoded)
                    replaced = True
        if replaced:
            return formatted
        if formatted.endswith(("?", "&")):
            return f"{formatted}query={quote(cleaned_query, safe='')}"
        if re.search(r"[?&][^=&?#]+=$", formatted):
            return f"{formatted}{quote(cleaned_query, safe='')}"
        return ""

    def _web_exploration_search_available(self) -> bool:
        return self._custom_web_exploration_search_configured() or self._astrbot_any_web_search_available()

    def _pick_available_web_search_umo(self, preferred: str = "") -> str:
        candidates = []
        preferred = str(preferred or "").strip()
        if preferred:
            candidates.append(preferred)
        for umo in self._web_search_candidate_umos():
            if umo not in candidates:
                candidates.append(umo)
        for umo in candidates:
            if self._astrbot_web_search_available(umo):
                return umo
        return ""

    def _web_search_runtime_state(self) -> dict[str, Any]:
        state = self.data.setdefault("web_search_runtime", {})
        if not isinstance(state, dict):
            self.data["web_search_runtime"] = {}
            state = self.data["web_search_runtime"]
        return state

    def _web_search_cooldown_remaining(self, provider: str) -> float:
        provider = _single_line(provider, 80) or "unknown"
        state = self.data.get("web_search_runtime") if isinstance(self.data.get("web_search_runtime"), dict) else {}
        item = state.get(provider) if isinstance(state, dict) else None
        if not isinstance(item, dict):
            return 0.0
        retry_after = _safe_float(item.get("retry_after"), 0.0, 0.0)
        return max(0.0, retry_after - _now_ts())

    def _web_search_error_cooldown_seconds(self, error: Any) -> tuple[float, str]:
        text = str(error or "")
        if "QUOTA_USER_DAILY_FREE" in text or "Daily free quota" in text:
            now_dt = datetime.now()
            tomorrow = (now_dt + timedelta(days=1)).replace(hour=0, minute=10, second=0, microsecond=0)
            return max(3600.0, min(24 * 3600.0, (tomorrow - now_dt).total_seconds())), "daily_quota"
        if "RATE_LIMIT_SEARCH_QPS" in text or "QPS" in text:
            return 10 * 60.0, "qps_limit"
        if "429" in text or "rate limit" in text.lower() or "quota" in text.lower():
            return 30 * 60.0, "rate_or_quota"
        return 0.0, ""

    def _mark_web_search_cooldown(self, provider: str, error: Any) -> None:
        seconds, reason = self._web_search_error_cooldown_seconds(error)
        if seconds <= 0:
            return
        provider = _single_line(provider, 80) or "unknown"
        state = self._web_search_runtime_state()
        state[provider] = {
            "retry_after": _now_ts() + seconds,
            "reason": reason,
            "last_error": _single_line(error, 240),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            self._save_data_sync(sections={"web_search_runtime"})
        except Exception:
            pass
        logger.warning(
            "网页搜索进入冷却: provider=%s reason=%s retry=%ss error=%s",
            provider,
            reason,
            int(seconds),
            _single_line(error, 180),
        )

    @staticmethod
    def _extract_custom_web_search_content(payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if not isinstance(payload, dict):
            return ""
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            message = first.get("message") if isinstance(first, dict) else {}
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts: list[str] = []
                    for item in content:
                        if isinstance(item, dict):
                            text = item.get("text") or item.get("content")
                            if isinstance(text, str):
                                parts.append(text)
                        elif isinstance(item, str):
                            parts.append(item)
                    return "\n".join(parts)
            text = first.get("text") if isinstance(first, dict) else ""
            if isinstance(text, str):
                return text
        for key in ("answer", "content", "text", "summary"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        return ""

    def _custom_web_search_items_from_payload(self, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, str):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", payload.strip(), flags=re.I | re.S).strip()
            candidates = [text]
            match = re.search(r"\[.*\]", text, flags=re.S)
            if match:
                candidates.append(match.group(0))
            for candidate in candidates:
                try:
                    parsed = json.loads(candidate)
                except Exception:
                    continue
                if isinstance(parsed, list):
                    return parsed
                if isinstance(parsed, dict):
                    return self._custom_web_search_items_from_payload(parsed)
            return []
        if not isinstance(payload, dict):
            return []
        for key in ("results", "items", "search_results", "web_results", "documents", "organic"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("results", "items", "search_results", "web_results", "documents", "organic"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        content = self._extract_custom_web_search_content(payload)
        items = self._custom_web_search_items_from_payload(content)
        if items:
            return items
        parsed = self._parse_json_object(content)
        if isinstance(parsed, dict):
            return self._custom_web_search_items_from_payload(parsed)
        return []

    def _normalize_custom_web_search_results(self, payload: Any, query: str) -> list[dict[str, Any]]:
        cleaned_query = _single_line(query, 120)
        items = self._custom_web_search_items_from_payload(payload)
        results: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                text = _single_line(item, 360)
                if not text:
                    continue
                title = text[:80]
                url = ""
                snippet = text
            else:
                title = _single_line(
                    item.get("title")
                    or item.get("name")
                    or item.get("headline")
                    or item.get("source_title")
                    or item.get("site_name"),
                    140,
                )
                url = _single_line(
                    item.get("url")
                    or item.get("link")
                    or item.get("href")
                    or item.get("source_url")
                    or item.get("page_url"),
                    420,
                )
                snippet = _single_line(
                    item.get("snippet")
                    or item.get("summary")
                    or item.get("content")
                    or item.get("description")
                    or item.get("text")
                    or item.get("body"),
                    360,
                )
            if not title and not snippet:
                continue
            key = hashlib.sha1(f"custom|{cleaned_query}|{title}|{url}|{snippet}".encode("utf-8", errors="ignore")).hexdigest()[:16]
            results.append({"key": key, "title": title or snippet[:80], "url": url, "snippet": snippet, "provider": "custom_web_exploration"})
            if len(results) >= runtime_persona_setting(self, "web_exploration_max_results", 6):
                break
        if results:
            return results[: runtime_persona_setting(self, "web_exploration_max_results", 6)]

        content = _single_line(self._extract_custom_web_search_content(payload), 720)
        if content:
            key = hashlib.sha1(f"custom|{cleaned_query}|{content}".encode("utf-8", errors="ignore")).hexdigest()[:16]
            return [
                {
                    "key": key,
                    "title": cleaned_query or "自定义主动搜索结果",
                    "url": "",
                    "snippet": content,
                    "provider": "custom_web_exploration",
                }
            ]
        return []

    async def _run_custom_web_exploration_search(self, query: str, *, topic: str = "general") -> list[dict[str, Any]]:
        cleaned_query = _single_line(query, 120)
        base_url = self._normalize_web_exploration_api_base_url(getattr(self, "web_exploration_api_base_url", ""))
        api_key = str(getattr(self, "web_exploration_api_key", "") or "").strip()
        model = str(getattr(self, "web_exploration_api_model", "") or "").strip()
        if not cleaned_query or not base_url:
            return []
        if not base_url.startswith(("http://", "https://")):
            self._last_web_search_error = "custom_web_exploration:invalid_url"
            return []
        cooldown = self._web_search_cooldown_remaining("custom_web_exploration")
        if cooldown > 0:
            self._last_web_search_error = f"custom_web_exploration_cooldown:{int(cooldown)}s"
            logger.info(
                "自定义主动搜索冷却中,跳过请求: retry=%ss query=%s",
                int(cooldown),
                cleaned_query,
            )
            return []

        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": f"{PLUGIN_NAME}/web-exploration",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        lower_url = base_url.lower()
        get_url = self._custom_web_exploration_get_url(base_url, cleaned_query, topic=topic, model=model)
        if get_url:
            payload = None
        elif "/chat/completions" in lower_url:
            payload: dict[str, Any] = {
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是搜索接口适配器。请基于可用联网搜索能力返回 JSON, 格式为 "
                            '{"results":[{"title":"","url":"","snippet":""}]}。'
                        ),
                    },
                    {"role": "user", "content": cleaned_query},
                ],
                "temperature": 0.2,
            }
            if model:
                payload["model"] = model
        else:
            payload = {
                "query": cleaned_query,
                "topic": "news" if topic == "news" else "general",
                "max_results": max(
                    1,
                    min(20, runtime_persona_setting(self, "web_exploration_max_results", 6)),
                ),
            }
            if model:
                payload["model"] = model
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                if get_url:
                    resp_ctx = session.get(get_url)
                else:
                    post_headers = {"Content-Type": "application/json"}
                    resp_ctx = session.post(base_url, headers=post_headers, json=payload)
                async with resp_ctx as resp:
                    body_text = await resp.text()
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}: {_single_line(body_text, 180)}")
                    try:
                        raw_payload: Any = json.loads(body_text)
                    except Exception:
                        raw_payload = body_text
        except asyncio.TimeoutError as exc:
            detail = "请求超时"
            self._last_web_search_error = f"custom_web_exploration:{detail}"
            self._mark_web_search_cooldown("custom_web_exploration", detail)
            logger.warning("自定义主动搜索失败: query=%s err=%s", cleaned_query, detail)
            return []
        except Exception as exc:
            detail = _single_line(exc, 220) or exc.__class__.__name__
            self._last_web_search_error = f"custom_web_exploration:{detail}"
            self._mark_web_search_cooldown("custom_web_exploration", exc)
            logger.warning("自定义主动搜索失败: query=%s err=%s", cleaned_query, exc)
            return []

        results = self._normalize_custom_web_search_results(raw_payload, cleaned_query)
        if not results:
            self._last_web_search_error = "custom_web_exploration:no_usable_results"
        return results
