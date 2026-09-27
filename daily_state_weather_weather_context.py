# -*- coding: utf-8 -*-
"""DailyStateWeatherWeatherContextMixin。

由 tools/split_mixin_domain.py 从 daily_state_weather.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 274 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateWeatherMixin）。
"""
from __future__ import annotations

from .daily_state_weather_shared import _now_ts, _today_key, logger
from .daily_state_weather_shared import Any
from .daily_state_weather_shared import AstrMessageEvent
from .daily_state_weather_shared import PLACEMENT_DYNAMIC_SYSTEM
from .daily_state_weather_shared import PLACEMENT_TURN_TAIL
from .daily_state_weather_shared import ProviderRequest
from .daily_state_weather_shared import _safe_float
from .daily_state_weather_shared import _safe_int
from .daily_state_weather_shared import _single_line
from .daily_state_weather_shared import datetime
from .daily_state_weather_shared import get_conversation_injection_plan
from .daily_state_weather_shared import hashlib
from .daily_state_weather_shared import prompt_section
from .daily_state_weather_shared import re
from .daily_state_weather_shared import runtime_persona_setting



class DailyStateWeatherWeatherContextMixin:
    """DailyStateWeatherWeatherContextMixin（从 DailyStateWeatherMixin 拆出）。"""


    def _weather_context_config_key(self) -> str:
        """Return a credential-free identity for the active weather place."""

        source = str(runtime_persona_setting(self, "weather_source", "qweather") or "qweather").strip().lower()
        parts = [source, self._weather_window_timezone()]
        if source == "qweather":
            parts.extend((self._qweather_weather_api_host(), self._qweather_location_identity()))
        elif source == "amap":
            parts.append(_single_line(runtime_persona_setting(self, "weather_amap_city", ""), 80).casefold())
        elif source == "openweathermap":
            city = _single_line(runtime_persona_setting(self, "weather_city", ""), 120).casefold()
            if city:
                parts.append("city:" + city)
            else:
                location = self._qweather_legacy_weather_location()
                parts.append("coordinates:" + repr(location))
        elif source == "openmeteo":
            parts.append("coordinates:" + repr(self._qweather_legacy_weather_location()))
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]

    def _weather_window_timezone(self) -> str:
        """Resolve the effective timezone without requiring ProactiveMixin."""

        resolver = getattr(self, "_proactive_window_timezone", None)
        if callable(resolver):
            try:
                resolved = _single_line(resolver(), 64)
            except Exception:
                resolved = ""
            if resolved:
                return resolved
        return _single_line(
            getattr(self, "environment_perception_timezone", ""),
            64,
        ) or "Asia/Shanghai"

    async def _ensure_weather_context(self, force: bool = False) -> dict[str, Any]:
        today = _today_key()
        if not runtime_persona_setting(self, "enable_weather_context", True):
            return {"date": today, "prompt": "暂无天气信息", "source": "disabled"}
        weather_source = runtime_persona_setting(self, "weather_source", "qweather")
        config_key = self._weather_context_config_key()
        cached = self.data.get("daily_weather", {})
        if isinstance(cached, dict):
            fetched_at = _safe_float(cached.get("fetched_ts"), 0)
            if (
                not force
                and cached.get("date") == today
                and cached.get("weather_source", "qweather") == weather_source
                and cached.get("config_key") == config_key
                and _now_ts() - fetched_at < _safe_int(runtime_persona_setting(self, "weather_refresh_minutes", 90), 90, 1) * 60
            ):
                return cached
        prompt = "暂无天气信息"
        source = "none"
        own_result = await self._fetch_own_weather_prompt()
        text = _single_line(own_result.get("prompt"), 120) if isinstance(own_result, dict) else ""
        location_label = _single_line(own_result.get("location_label"), 120) if isinstance(own_result, dict) else ""
        if not location_label and str(weather_source).strip().lower() == "qweather":
            location_label = _single_line(self._qweather_location_snapshot().get("label"), 120)
        if text:
            prompt = text
            source = str(own_result.get("source") or "private_companion")
        else:
            plugin = self._get_screen_companion_plugin()
            if plugin is not None and hasattr(plugin, "_get_weather_prompt"):
                try:
                    result = await plugin._get_weather_prompt()
                    text = _single_line(result, 120)
                    if text:
                        prompt = text
                        source = "screen_companion"
                except Exception as e:
                    logger.debug(f"获取天气信息失败: {e}")
        weather = {
            "date": today,
            "prompt": prompt,
            "source": source,
            "weather_source": weather_source,
            "config_key": config_key,
            "location_label": location_label,
            "fetched_ts": _now_ts(),
        }
        async with self._data_lock:
            self.data["daily_weather"] = weather
            self._save_data_sync(sections={"daily_weather"})
        return weather

    @staticmethod
    def _user_asks_current_weather(text: Any) -> bool:
        """识别用户是否在询问本地当前天气，而不是讨论天气功能本身。"""

        cleaned = _single_line(text, 180)
        if not cleaned:
            return False
        compact = re.sub(r"[\s，。！？!?,.、~～…：:；;]+", "", cleaned).casefold()
        if not compact:
            return False

        meta_terms = (
            "天气api",
            "天气接口",
            "天气插件",
            "天气功能",
            "天气配置",
            "天气设置",
            "天气日志",
            "天气代码",
            "和风天气api",
            "和风天气接口",
            "和风天气配置",
        )
        if any(term in compact for term in meta_terms) or re.search(
            r"天气.{0,3}(?:api|接口|插件|功能|配置|设置|日志|代码)",
            compact,
            re.IGNORECASE,
        ):
            return False
        if "天气" in compact and any(
            term in compact
            for term in ("怎么接入", "如何接入", "怎么配置", "如何配置", "报错", "排障", "调试")
        ):
            return False

        # 当前实况接口不能证明未来天气，混合或未来预报问题交给真正的预报能力处理。
        if any(term in compact for term in ("明天", "后天", "大后天", "未来", "下周", "周末")):
            return False

        # “我这边”明确指向用户所在地，不能拿 Bot 配置地点的实况代答。
        if any(term in compact for term in ("我这边", "我们这边", "俺这边", "我这里", "我们这里")):
            return False
        current_terms = ("现在", "当前", "今天", "今日", "此刻", "这会儿", "外面", "当地", "这边", "你那边")
        asks_now = any(term in compact for term in current_terms)
        if "天气" in compact:
            if any(term in compact for term in ("喜欢什么天气", "讨厌什么天气", "什么天气最", "天气原理", "天气形成", "天气变化的原因")):
                return False
            return asks_now or len(compact) <= 12 or any(
                term in compact for term in ("天气怎么样", "天气如何", "天气咋样", "什么天气", "查天气", "看看天气", "天气好吗", "天气呢")
            )
        if re.search(r"(?:多少|几)(?:度|°c?|摄氏度)", compact, re.IGNORECASE):
            return True
        if any(term in compact for term in ("气温", "温度")):
            return asks_now or len(compact) <= 10
        if any(term in compact for term in ("要带伞", "需要带伞", "用带伞", "下雨吗", "在下雨", "下雪吗", "在下雪")):
            return asks_now or len(compact) <= 10
        if asks_now and any(term in compact for term in ("冷不冷", "热不热", "冷吗", "热吗")):
            return True
        return False

    async def _append_weather_query_context_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        current_user: dict[str, Any] | None = None,
    ) -> bool:
        """显式天气查询时按需获取实况，并把可信结果注入当前请求。"""

        marker = "<!-- private_companion_weather_query_v1 -->"
        if self._request_has_managed_prompt_marker(req, marker):
            return True
        inbound_text = _single_line(
            getattr(event, "private_companion_group_text", "")
            or getattr(event, "message_str", "")
            or getattr(req, "prompt", ""),
            180,
        )
        if not self._user_asks_current_weather(inbound_text):
            return False
        if not bool(runtime_persona_setting(self, "enable_weather_context", True)):
            return False

        weather = await self._ensure_weather_context(force=False)
        prompt = self._weather_summary_text(weather)
        valid = bool(prompt and prompt != "暂无天气信息")
        role = ""
        location_label = ""
        fetched_at = _safe_float(weather.get("fetched_ts"), 0) if isinstance(weather, dict) else 0
        configured_source = str(runtime_persona_setting(self, "weather_source", "qweather") or "qweather").strip().lower()
        expected_source = {
            "qweather": "qweather",
            "amap": "amap",
            "openmeteo": "openmeteo",
            "openweathermap": "private_companion",
        }.get(configured_source, configured_source)
        actual_source = _single_line(weather.get("source"), 40) if isinstance(weather, dict) else ""
        using_fallback = bool(valid and expected_source and actual_source != expected_source)
        if (not valid or using_fallback) and (fetched_at <= 0 or _now_ts() - fetched_at >= 60):
            weather = await self._ensure_weather_context(force=True)
            prompt = self._weather_summary_text(weather)
            valid = bool(prompt and prompt != "暂无天气信息")

        if valid:
            injection_title = "本轮当前天气查询"
            source = _single_line(weather.get("source"), 40) if isinstance(weather, dict) else ""
            source_label = {
                "qweather": "和风天气",
                "amap": "高德天气",
                "openmeteo": "Open-Meteo",
                "private_companion": "OpenWeatherMap",
                "screen_companion": "天气联动来源",
            }.get(source, "已配置的天气来源")
            details = [f"实况：{prompt}", f"来源：{source_label}"]
            role = self._private_user_role(current_user or {}) if isinstance(current_user, dict) else ""
            location_label = _single_line(weather.get("location_label"), 80) if isinstance(weather, dict) else ""
            if role == "owner" and location_label:
                details.insert(0, f"地点：{location_label}")
            fetched_ts = _safe_float(weather.get("fetched_ts"), 0) if isinstance(weather, dict) else 0
            if fetched_ts > 0:
                details.append(f"数据获取时间：{datetime.fromtimestamp(fetched_ts).strftime('%H:%M')}")
            injection = (
                "用户本轮明确询问当前天气。以下数据由本插件配置的天气来源直接取得，是本轮回答依据：\n"
                + "\n".join(details)
                + "\n请直接结合用户问题自然回答，不要再调用搜索、浏览器、地图、记忆或其他天气工具。"
                "只能陈述以上数据能够证明的当前实况；不要据此编造未来预报，也不要向用户提及系统提示词或注入过程。"
            )
        else:
            injection_title = "本轮天气查询暂未取得实况"
            injection = (
                "用户本轮明确询问当前天气，但本插件已尝试读取配置的天气来源，本轮没有取得有效实况。\n"
                "不要编造天气，也不要调用搜索、浏览器、地图、记忆或多个工具反复查找。"
                "如果当前确有一个明确、专用的天气工具，最多尝试一次；否则简短说明暂时没有取到天气即可。"
            )

        weather_section = prompt_section(
            key="weather.query",
            title=injection_title,
            source="weather_query",
            content=injection,
        )
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            weather_section,
            priority=24,
            force_dynamic=True,
        ) else "system_prompt"
        plan = get_conversation_injection_plan(req)
        if placement == "system_prompt":
            if plan is not None:
                plan.materialize_system_block(
                    req,
                    section=weather_section,
                    marker=marker,
                    priority=24,
                    placement=PLACEMENT_DYNAMIC_SYSTEM,
                )
        elif plan is not None and not plan.contains_marker(marker):
            plan.add(
                section=weather_section,
                marker=marker,
                priority=24,
                placement=PLACEMENT_TURN_TAIL,
            )
        recorder = getattr(self, "_record_request_prompt_fragment", None)
        if callable(recorder):
            await recorder(
                event,
                title="当前天气查询注入",
                key="weather.query",
                text=injection,
                source="weather_query",
                metadata={"注入位置": placement, "获取成功": valid},
            )
        logger.info(
            "当前天气查询已处理: session=%s success=%s source=%s location_visible=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            valid,
            _single_line(weather.get("source"), 40) if isinstance(weather, dict) else "none",
            bool(valid and isinstance(current_user, dict) and role == "owner" and location_label),
        )
        return True

    def _weather_summary_text(self, weather: dict[str, Any] | None) -> str:
        if not isinstance(weather, dict):
            return "暂无天气信息"
        text = _single_line(weather.get("prompt"), 120)
        return text or "暂无天气信息"
