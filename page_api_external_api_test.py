# -*- coding: utf-8 -*-
"""external_api_test 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（11 个方法 + 2 个模块级名字 + 0 个类级赋值 / 563 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import math
import re
from .helpers import _redact_outbound_secrets
from copy import copy, deepcopy
from typing import Any
from urllib.parse import urlparse



EXTERNAL_API_TEST_SETTING_ATTRS: dict[str, dict[str, str]] = {
    "weather_api": {
        "weather_source": "weather_source",
        "weather_api_host": "weather_api_host",
        "weather_token": "weather_token",
        "weather_location": "weather_location",
        "weather_api_key": "weather_api_key",
        "weather_city": "weather_city",
        "weather_amap_api_key": "weather_amap_api_key",
        "weather_amap_city": "weather_amap_city",
        "weather_lat": "weather_lat",
        "weather_lon": "weather_lon",
    },
    "balance_api": {
        "balance_api_url": "balance_api_url",
        "balance_api_key": "balance_api_key",
        "balance_api_auth_header": "balance_api_auth_header",
        "balance_api_auth_scheme": "balance_api_auth_scheme",
        "balance_api_custom_headers": "balance_api_custom_headers",
        "balance_json_path": "balance_json_path",
        "balance_total_json_path": "balance_total_json_path",
        "balance_used_json_path": "balance_used_json_path",
        "balance_value_divisor": "balance_value_divisor",
        "balance_currency_label": "balance_currency_label",
        "balance_request_timeout_seconds": "balance_request_timeout_seconds",
    },
    "web_search": {
        "WEB_EXPLORATION_API_BASE_URL": "web_exploration_api_base_url",
        "WEB_EXPLORATION_API_KEY": "web_exploration_api_key",
        "WEB_EXPLORATION_API_MODEL": "web_exploration_api_model",
        "web_exploration_max_results": "web_exploration_max_results",
    },
}

EXTERNAL_API_TEST_SECRET_FIELDS = frozenset(
    {
        "weather_token",
        "weather_api_key",
        "weather_amap_api_key",
        "balance_api_key",
        "balance_api_custom_headers",
        "WEB_EXPLORATION_API_KEY",
    }
)


class PrivateCompanionPageApiExternalApiTestMixin:
    """external_api_test 域（从 PrivateCompanionPageApi 拆出）。"""


    def _classify_test_failure(self, test_type: str, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("unsupported") or result.get("test_status") in {"unsupported", "skipped"}:
            return {
                "error_code": self._single_line(result.get("code"), 80) or "test_not_supported",
                "error_category": self._single_line(result.get("error_category"), 40) or "契约限制",
                "retryable": False,
                "suggestion": self._single_line(result.get("next_step"), 600)
                or "请使用对应插件提供的完整链路测试。",
            }
        if bool(result.get("ok")) or bool(result.get("pending")):
            return {
                "error_code": "",
                "error_category": "",
                "retryable": False,
                "suggestion": "",
            }
        text = " ".join(
            str(result.get(key) or "")
            for key in ("error", "delivery_error", "detail")
        ).lower()
        rules = [
            (
                "queue_timeout",
                "队列等待超时",
                True,
                ("排队超时", "释放队列", "queue timeout", "queue wait"),
                "当前任务队列繁忙；等待其他任务结束后重试，必要时检查是否有卡住的生成任务。",
            ),
            (
                "timeout",
                "请求超时",
                True,
                ("timeout", "timed out", "超时", "等待上限", "排队超时"),
                "检查服务和任务队列；若服务本身较慢，可适当提高该测试的超时后重试。",
            ),
            (
                "authentication",
                "鉴权失败",
                False,
                ("unauthorized", "forbidden", "invalid api key", "api key", "401", "403", "鉴权", "认证", "密钥"),
                "核对 API Key、请求地址和账号权限，保存配置后重新测试。",
            ),
            (
                "rate_limit",
                "额度或限流",
                True,
                ("rate limit", "too many requests", "429", "quota", "insufficient", "限流", "额度", "余额不足"),
                "检查额度和并发限制，等待限流窗口恢复后重试。",
            ),
            (
                "endpoint_mismatch",
                "端点不匹配",
                False,
                ("http 404", "http状态 404", "status=404", "status 404", "未找到生图接口", "端点不匹配"),
                "当前平台返回 HTTP 404，通常是生图端点协议不匹配；核对实际请求 URL、平台选择和参考图/文生图端点后再试。",
            ),
            (
                "network",
                "网络连接失败",
                True,
                ("connection", "connect", "network", "dns", "proxy", "ssl", "certificate", "网络", "连接", "代理", "证书"),
                "检查服务地址、DNS、代理和证书配置，再重试连接。",
            ),
            (
                "configuration",
                "配置不可用",
                False,
                ("未配置", "未启用", "不存在", "不可用", "不支持", "缺少", "尚未启用", "not found", "missing", "disabled"),
                "补齐或启用测试所需配置，确认目标能力已加载后重试。",
            ),
            (
                "delivery",
                "消息投递失败",
                True,
                ("投递", "发送", "delivery", "send_message", "平台未确认", "未能发送"),
                "检查主要用户私聊会话、平台适配器和消息发送链路后重试。",
            ),
            (
                "empty_result",
                "返回内容无效",
                True,
                ("未返回", "返回为空", "没有得到", "无有效", "empty", "invalid result", "不可投递"),
                "确认模型、工作流或 Provider 能返回该测试要求的有效内容。",
            ),
        ]
        for code, category, retryable, needles, suggestion in rules:
            if any(needle in text for needle in needles):
                return {
                    "error_code": code,
                    "error_category": category,
                    "retryable": retryable,
                    "suggestion": suggestion,
                }
        return {
            "error_code": "internal_error",
            "error_category": "执行异常",
            "retryable": True,
            "suggestion": "复制测试编号和诊断详情，并查看同一时间的 AstrBot 后端日志。",
        }

    @staticmethod
    def _normalize_external_api_test_setting(key: str, value: Any) -> Any:
        if key == "weather_source":
            source = str(value or "qweather").strip().lower()
            return source if source in {"qweather", "openweathermap", "openmeteo", "amap"} else "qweather"
        if key in {"weather_lat", "weather_lon"}:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0
        if key == "balance_value_divisor":
            try:
                return max(1e-12, min(1e12, float(value)))
            except (TypeError, ValueError):
                return 1.0
        if key == "balance_request_timeout_seconds":
            try:
                return max(2.0, min(60.0, float(value)))
            except (TypeError, ValueError):
                return 10.0
        if key == "web_exploration_max_results":
            try:
                return max(3, min(20, int(value)))
            except (TypeError, ValueError):
                return 6
        limits = {
            "weather_api_host": 800,
            "weather_token": 2000,
            "weather_location": 180,
            "weather_api_key": 1000,
            "weather_city": 180,
            "weather_amap_api_key": 1000,
            "weather_amap_city": 180,
            "balance_api_url": 1000,
            "balance_api_key": 1000,
            "balance_api_auth_header": 80,
            "balance_api_auth_scheme": 40,
            "balance_api_custom_headers": 4000,
            "balance_json_path": 180,
            "balance_total_json_path": 180,
            "balance_used_json_path": 180,
            "balance_currency_label": 20,
            "WEB_EXPLORATION_API_BASE_URL": 800,
            "WEB_EXPLORATION_API_KEY": 800,
            "WEB_EXPLORATION_API_MODEL": 160,
        }
        return str(value or "").strip()[: limits.get(key, 1000)]

    def _external_api_test_plugin_copy(
        self,
        test_type: str,
        raw_settings: Any,
    ) -> tuple[Any, dict[str, Any]]:
        settings = raw_settings if isinstance(raw_settings, dict) else {}
        tester = copy(self.plugin)
        live_data = getattr(self.plugin, "data", None)
        tester.data = dict(live_data) if isinstance(live_data, dict) else {}
        for state_key in ("daily_weather", "qweather_location", "web_search_runtime", "balance_awareness"):
            if state_key in tester.data:
                tester.data[state_key] = deepcopy(tester.data[state_key])
        tester._data_lock = asyncio.Lock()
        tester._qweather_location_resolve_lock = asyncio.Lock()
        tester._save_data_sync = lambda **_kwargs: None
        tester._external_api_test_mode = True

        normalized: dict[str, Any] = {}
        for key, attr in EXTERNAL_API_TEST_SETTING_ATTRS.get(test_type, {}).items():
            if key not in settings:
                continue
            value = self._normalize_external_api_test_setting(key, settings.get(key))
            setattr(tester, attr, value)
            normalized[key] = value

        # The current form value is authoritative during a test, including
        # when the user has just cleared a field that has a legacy alias.
        if test_type == "weather_api" and "weather_api_host" in normalized:
            tester.qweather_api_host = ""
            tester.weather_alert_api_host = ""
        if test_type == "weather_api" and "weather_token" in normalized:
            tester.qweather_token = ""
            tester.weather_alert_token = ""
            tester.weather_alert_jwt = ""
            tester.weather_alert_api_key = ""
        return tester, normalized

    @staticmethod
    def _safe_external_api_test_url(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            parsed = urlparse(raw)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return "<外部接口>"
            host = parsed.hostname
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://{host}{port}{parsed.path or ''}"
        except ValueError:
            return "<外部接口>"

    def _redact_external_api_test_text(
        self,
        value: Any,
        *,
        tester: Any,
        settings: dict[str, Any],
        limit: int = 1200,
    ) -> str:
        cleaned = _redact_outbound_secrets(str(value or ""), tester)
        secrets_to_hide: list[str] = []
        for key in EXTERNAL_API_TEST_SECRET_FIELDS:
            raw = str(settings.get(key) or "").strip()
            if not raw:
                continue
            if key == "balance_api_custom_headers":
                for line in raw.splitlines():
                    _, separator, header_value = line.partition(":")
                    if separator and len(header_value.strip()) >= 4:
                        secrets_to_hide.append(header_value.strip())
                continue
            if len(raw) >= 4:
                secrets_to_hide.append(raw)
        for secret in sorted(set(secrets_to_hide), key=len, reverse=True):
            cleaned = cleaned.replace(secret, "[密钥已隐藏]")
        cleaned = re.sub(
            r"https?://[^\s'\"<>]+",
            lambda match: self._safe_external_api_test_url(match.group(0)),
            cleaned,
            flags=re.IGNORECASE,
        )
        return self._multi_line(cleaned, max(80, limit))

    @staticmethod
    def _external_weather_location_label(tester: Any, result: Any = None) -> str:
        if isinstance(result, dict) and str(result.get("location_label") or "").strip():
            return str(result.get("location_label") or "").strip()[:120]
        source = str(getattr(tester, "weather_source", "qweather") or "qweather").strip().lower()
        attr = {
            "qweather": "weather_location",
            "amap": "weather_amap_city",
            "openweathermap": "weather_city",
        }.get(source, "")
        label = str(getattr(tester, attr, "") or "").strip() if attr else ""
        if label:
            return label[:120]
        try:
            latitude = float(getattr(tester, "weather_lat", 0))
            longitude = float(getattr(tester, "weather_lon", 0))
        except (TypeError, ValueError):
            return ""
        if math.isfinite(latitude) and math.isfinite(longitude) and (latitude != 0 or longitude != 0):
            return f"{longitude:g},{latitude:g}"
        return ""

    def _external_weather_configuration_error(self, tester: Any) -> str:
        source = str(getattr(tester, "weather_source", "qweather") or "qweather").strip().lower()
        location = self._external_weather_location_label(tester)
        if source == "qweather":
            missing = []
            if not str(getattr(tester, "weather_api_host", "") or "").strip():
                missing.append("专属 API Host")
            if not str(getattr(tester, "weather_token", "") or "").strip():
                missing.append("天气凭据")
            if not location:
                missing.append("天气地点")
            return f"和风天气缺少{'、'.join(missing)}" if missing else ""
        if source == "amap":
            missing = []
            if not str(getattr(tester, "weather_amap_api_key", "") or "").strip():
                missing.append("高德 API Key")
            if not str(getattr(tester, "weather_amap_city", "") or "").strip():
                missing.append("高德城市")
            return f"高德天气缺少{'、'.join(missing)}" if missing else ""
        if source == "openmeteo":
            return "" if location else "Open-Meteo 缺少有效经纬度"
        missing = []
        if not str(getattr(tester, "weather_api_key", "") or "").strip():
            missing.append("OpenWeatherMap API Key")
        if not location:
            missing.append("城市或经纬度")
        return f"OpenWeatherMap 缺少{'、'.join(missing)}" if missing else ""

    async def _run_weather_api_test(self, tester: Any, settings: dict[str, Any]) -> dict[str, Any]:
        title = "天气 API 请求测试"
        provider = self._single_line(getattr(tester, "weather_source", "qweather"), 40).lower() or "qweather"
        configuration_error = self._external_weather_configuration_error(tester)
        if configuration_error:
            return {
                "ok": False,
                "title": title,
                "provider": provider,
                "location_label": self._external_weather_location_label(tester),
                "error": configuration_error,
                "steps": [{"name": "检查天气配置", "status": "error", "detail": configuration_error}],
            }
        fetcher = getattr(tester, "_fetch_own_weather_prompt", None)
        if not callable(fetcher):
            return {"ok": False, "title": title, "provider": provider, "error": "当前插件未加载天气请求入口"}
        try:
            raw = await asyncio.wait_for(fetcher(), timeout=25.0)
        except asyncio.TimeoutError:
            error = "天气接口请求超时（25 秒）"
            return {
                "ok": False,
                "title": title,
                "provider": provider,
                "location_label": self._external_weather_location_label(tester),
                "error": error,
                "steps": [{"name": "请求天气接口", "status": "error", "detail": error}],
            }
        except Exception as exc:
            error = self._redact_external_api_test_text(exc, tester=tester, settings=settings, limit=800)
            return {
                "ok": False,
                "title": title,
                "provider": provider,
                "location_label": self._external_weather_location_label(tester),
                "error": error or "天气接口请求失败",
                "exception_type": exc.__class__.__name__,
                "steps": [{"name": "请求天气接口", "status": "error", "detail": error or "请求失败"}],
            }
        prompt = self._single_line(raw.get("prompt"), 320) if isinstance(raw, dict) else ""
        source = self._single_line(raw.get("source"), 80) if isinstance(raw, dict) else ""
        location_label = self._external_weather_location_label(tester, raw)
        if not isinstance(raw, dict) or not prompt or not source:
            error = "天气接口未返回有效天气结果；请核对地点、凭据、接口权限和网络状态"
            return {
                "ok": False,
                "title": title,
                "provider": provider,
                "source": source,
                "location_label": location_label,
                "error": error,
                "steps": [{"name": "校验天气结果", "status": "error", "detail": error}],
            }
        return {
            "ok": True,
            "title": title,
            "provider": provider,
            "source": source,
            "location_label": location_label,
            "detail": prompt,
            "steps": [{"name": "请求天气接口", "status": "ok", "detail": prompt}],
        }

    async def _run_balance_api_test(self, tester: Any, settings: dict[str, Any]) -> dict[str, Any]:
        title = "余额接口请求测试"
        fetcher = getattr(tester, "_fetch_balance_snapshot", None)
        if not callable(fetcher):
            return {"ok": False, "title": title, "error": "当前插件未加载余额查询入口"}
        try:
            snapshot = await asyncio.wait_for(fetcher(), timeout=45.0)
        except asyncio.TimeoutError:
            error = "余额接口请求超时（45 秒）"
            return {
                "ok": False,
                "title": title,
                "error": error,
                "steps": [{"name": "请求余额接口", "status": "error", "detail": error}],
            }
        except Exception as exc:
            safe_error = ""
            sanitizer = getattr(tester, "_balance_safe_error", None)
            if callable(sanitizer):
                try:
                    safe_error = sanitizer(exc)
                except Exception:
                    safe_error = ""
            error = self._redact_external_api_test_text(
                safe_error or exc,
                tester=tester,
                settings=settings,
                limit=800,
            )
            return {
                "ok": False,
                "title": title,
                "error": error or "余额接口请求失败",
                "exception_type": exc.__class__.__name__,
                "steps": [{"name": "请求余额接口", "status": "error", "detail": error or "请求失败"}],
            }
        if not isinstance(snapshot, dict) or snapshot.get("amount") is None:
            error = "余额接口未返回可用余额字段"
            return {
                "ok": False,
                "title": title,
                "error": error,
                "steps": [{"name": "校验余额结果", "status": "error", "detail": error}],
            }
        currency = self._single_line(getattr(tester, "balance_currency_label", "元"), 20) or "元"
        amount = snapshot.get("amount")
        detail = f"余额接口返回 {amount:g} {currency}" if isinstance(amount, (int, float)) else "余额接口返回有效结果"
        return {
            "ok": True,
            "title": title,
            "query_mode": self._single_line(snapshot.get("query_mode"), 20).lower(),
            "source_id": self._single_line(snapshot.get("source_id"), 100),
            "endpoint_path": self._single_line(snapshot.get("endpoint_path"), 180),
            "amount": snapshot.get("amount"),
            "total": snapshot.get("total"),
            "used": snapshot.get("used"),
            "remaining_percent": snapshot.get("remaining_percent"),
            "currency_label": currency,
            "detail": detail,
            "steps": [{"name": "请求余额接口", "status": "ok", "detail": detail}],
        }

    async def _run_web_search_test(
        self,
        tester: Any,
        settings: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        title = "搜索接口请求测试"
        query = self._single_line(payload.get("query"), 120)
        if not query:
            error = "请输入用于测试的搜索词"
            return {"ok": False, "title": title, "error": error, "steps": [{"name": "检查搜索词", "status": "error", "detail": error}]}
        topic = self._single_line(payload.get("topic"), 20).lower()
        topic = topic if topic in {"general", "news"} else "general"
        umo = self._single_line(payload.get("umo"), 180)
        if not umo:
            picker = getattr(tester, "_pick_available_web_search_umo", None)
            if callable(picker):
                try:
                    umo = self._single_line(picker(""), 180)
                except Exception:
                    umo = ""
        searcher = getattr(tester, "_run_astrbot_web_search", None)
        if not callable(searcher):
            return {"ok": False, "title": title, "error": "当前插件未加载统一网页搜索入口"}
        try:
            results = await asyncio.wait_for(
                searcher(query, umo=umo, topic=topic, usage="web_exploration"),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            error = "搜索接口请求超时（25 秒）"
            return {"ok": False, "title": title, "error": error, "steps": [{"name": "请求搜索接口", "status": "error", "detail": error}]}
        except Exception as exc:
            error = self._redact_external_api_test_text(exc, tester=tester, settings=settings, limit=800)
            return {
                "ok": False,
                "title": title,
                "error": error or "搜索接口请求失败",
                "exception_type": exc.__class__.__name__,
                "steps": [{"name": "请求搜索接口", "status": "error", "detail": error or "请求失败"}],
            }

        custom_configured = False
        custom_checker = getattr(tester, "_custom_web_exploration_search_configured", None)
        if callable(custom_checker):
            try:
                custom_configured = bool(custom_checker())
            except Exception:
                custom_configured = False
        preview: list[dict[str, str]] = []
        providers: list[str] = []
        for item in results[:3] if isinstance(results, list) else []:
            if not isinstance(item, dict):
                continue
            provider = self._single_line(item.get("provider"), 100)
            if provider and provider not in providers:
                providers.append(provider)
            title_text = self._redact_external_api_test_text(
                self._single_line(item.get("title"), 140), tester=tester, settings=settings, limit=140
            )
            snippet = self._redact_external_api_test_text(
                self._single_line(item.get("snippet"), 240), tester=tester, settings=settings, limit=240
            )
            if title_text or snippet:
                preview.append({"title": title_text, "snippet": snippet})
        provider = providers[0] if providers else ("custom_web_exploration" if custom_configured else "astrbot")
        if not isinstance(results, list) or not results:
            raw_error = self._single_line(getattr(tester, "_last_web_search_error", ""), 500)
            if not raw_error:
                available = getattr(tester, "_astrbot_any_web_search_available", None)
                astrbot_available = False
                if callable(available):
                    try:
                        astrbot_available = bool(available())
                    except Exception:
                        astrbot_available = False
                raw_error = (
                    "搜索接口未返回可用结果"
                    if custom_configured or astrbot_available
                    else "未配置可用的自定义搜索接口或 AstrBot 网页搜索 Provider"
                )
            error = self._redact_external_api_test_text(raw_error, tester=tester, settings=settings, limit=800)
            return {
                "ok": False,
                "title": title,
                "provider": provider,
                "result_count": 0,
                "result_preview": [],
                "error": error,
                "steps": [{"name": "校验搜索结果", "status": "error", "detail": error}],
            }
        count = len(results)
        detail = f"{provider} 返回 {count} 条可用搜索结果"
        return {
            "ok": True,
            "title": title,
            "provider": provider,
            "result_count": count,
            "result_preview": preview,
            "detail": detail,
            "steps": [{"name": "请求统一搜索入口", "status": "ok", "detail": detail}],
        }

    async def _run_external_api_test(self, test_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        tester, settings = self._external_api_test_plugin_copy(test_type, payload.get("settings"))
        if test_type == "weather_api":
            result = await self._run_weather_api_test(tester, settings)
        elif test_type == "balance_api":
            result = await self._run_balance_api_test(tester, settings)
        else:
            result = await self._run_web_search_test(tester, settings, payload)
        steps = result.get("steps") if isinstance(result.get("steps"), list) else []
        result["steps"] = [
            {
                "name": "应用临时配置",
                "status": "ok",
                "detail": f"已在隔离副本中应用 {len(settings)} 个当前表单字段",
            },
            *steps,
        ]
        return result
