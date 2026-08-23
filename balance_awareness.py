# -*- coding: utf-8 -*-
"""Provider/account balance polling and low-balance proactive impulses."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
from typing import Any
from urllib.parse import urlparse

from astrbot.api import logger

from .helpers import _now_ts, _safe_float, _single_line
from .persona_config import runtime_persona_setting


class BalanceAwarenessMixin:
    """Poll a configured JSON endpoint and turn low balances into persona-aware impulses."""

    _BALANCE_AUTO_PATHS = (
        "balance",
        "total_balance",
        "remaining_balance",
        "total_available",
        "remaining",
        "available",
        "credit",
        "quota",
        "balance_infos[0].total_balance",
        "data.balance",
        "data.total_balance",
        "data.remaining_balance",
        "data.total_available",
        "data.remaining",
        "data.available",
        "data.credit",
        "data.quota",
        "data.limit_remaining",
        "data.balance_infos[0].total_balance",
        "data.user.balance",
        "data.user.quota",
    )
    _BALANCE_TOTAL_AUTO_PATHS = (
        "total",
        "limit",
        "quota_limit",
        "total_granted",
        "total_credit",
        "data.total",
        "data.limit",
        "data.quota_limit",
        "data.total_granted",
        "data.total_credit",
        "data.user.total",
        "data.user.quota_limit",
    )
    _BALANCE_USED_AUTO_PATHS = (
        "used",
        "usage",
        "total_used",
        "data.used",
        "data.usage",
        "data.total_used",
        "data.used_quota",
        "data.user.used",
        "data.user.used_quota",
    )

    _BALANCE_GENERIC_ENDPOINT_PATHS = (
        "/dashboard/billing/credit_grants",
        "/dashboard/billing/subscription",
        "/v1/dashboard/billing/subscription",
        "/api/user/self",
        "/api/user/amount",
        "/v1/user/info",
        "/user/balance",
    )

    @staticmethod
    def _balance_json_path_tokens(path: str) -> list[str | int]:
        text = str(path or "").strip()
        if text.startswith("$."):
            text = text[2:]
        elif text == "$":
            return []
        tokens: list[str | int] = []
        for name, index in re.findall(r"(?:^|\.)([^.\[\]]+)|\[(\d+)\]", text):
            if name:
                tokens.append(name)
            elif index:
                tokens.append(int(index))
        return tokens

    @classmethod
    def _balance_json_path_get(cls, payload: Any, path: str) -> Any:
        current = payload
        tokens = cls._balance_json_path_tokens(path)
        if str(path or "").strip() and not tokens:
            return None
        for token in tokens:
            if isinstance(token, int):
                if not isinstance(current, list) or token >= len(current):
                    return None
                current = current[token]
            else:
                if not isinstance(current, dict) or token not in current:
                    return None
                current = current[token]
        return current

    @staticmethod
    def _balance_number(value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            number = float(value)
        else:
            text = str(value).strip().replace(",", "")
            match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
            if not match:
                return None
            try:
                number = float(match.group(0))
            except (TypeError, ValueError):
                return None
        return number if math.isfinite(number) else None

    def _balance_first_number(self, payload: Any, explicit_path: str, auto_paths: tuple[str, ...]) -> tuple[float | None, str]:
        paths = (str(explicit_path or "").strip(),) if str(explicit_path or "").strip() else auto_paths
        for path in paths:
            number = self._balance_number(self._balance_json_path_get(payload, path))
            if number is not None:
                return number, path
        return None, ""

    def _extract_balance_snapshot(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, (dict, list)):
            raise ValueError("余额接口返回值不是 JSON 对象或数组")
        amount, amount_path = self._balance_first_number(
            payload,
            getattr(self, "balance_json_path", ""),
            self._BALANCE_AUTO_PATHS,
        )
        total, total_path = self._balance_first_number(
            payload,
            getattr(self, "balance_total_json_path", ""),
            self._BALANCE_TOTAL_AUTO_PATHS,
        )
        used, used_path = self._balance_first_number(
            payload,
            getattr(self, "balance_used_json_path", ""),
            self._BALANCE_USED_AUTO_PATHS,
        )
        if amount is None and total is not None and used is not None:
            amount = max(0.0, total - used)
            amount_path = f"{total_path}-{used_path}"
        if amount is None:
            configured = _single_line(getattr(self, "balance_json_path", ""), 120)
            detail = f"（已配置路径 {configured}）" if configured else ""
            raise ValueError(f"余额接口 JSON 中未找到可用余额字段{detail}")
        divisor = max(1e-12, _safe_float(getattr(self, "balance_value_divisor", 1.0), 1.0, 1e-12))
        amount /= divisor
        if total is not None:
            total /= divisor
        if used is not None:
            used /= divisor
        remaining_percent = amount / total * 100.0 if total is not None and total > 0 else None
        return {
            "amount": amount,
            "total": total,
            "used": used,
            "remaining_percent": remaining_percent,
            "amount_path": amount_path,
            "total_path": total_path,
            "used_path": used_path,
        }

    def _balance_tier(self, snapshot: dict[str, Any]) -> str:
        amount = self._balance_number(snapshot.get("amount"))
        percent = self._balance_number(snapshot.get("remaining_percent"))
        low_amount = _safe_float(getattr(self, "balance_low_threshold", 10.0), 10.0, 0.0)
        critical_amount = min(
            low_amount,
            _safe_float(getattr(self, "balance_critical_threshold", 3.0), 3.0, 0.0),
        )
        low_percent = _safe_float(getattr(self, "balance_low_percent_threshold", 15.0), 15.0, 0.0)
        critical_percent = min(
            low_percent,
            _safe_float(getattr(self, "balance_critical_percent_threshold", 5.0), 5.0, 0.0),
        )
        critical = (
            (amount is not None and critical_amount > 0 and amount <= critical_amount)
            or (percent is not None and critical_percent > 0 and percent <= critical_percent)
        )
        if critical:
            return "critical"
        low = (
            (amount is not None and low_amount > 0 and amount <= low_amount)
            or (percent is not None and low_percent > 0 and percent <= low_percent)
        )
        return "low" if low else "normal"

    @staticmethod
    def _balance_parse_custom_headers(raw: Any) -> dict[str, str]:
        headers: dict[str, str] = {}
        blocked = {"host", "content-length", "transfer-encoding", "connection"}
        for line in str(raw or "").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not key or not value or key.lower() in blocked or "\r" in value or "\n" in value:
                continue
            headers[key] = value
        return headers

    def _balance_request_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "astrbot-private-companion/balance-awareness",
        }
        headers.update(self._balance_parse_custom_headers(getattr(self, "balance_api_custom_headers", "")))
        api_key = str(getattr(self, "balance_api_key", "") or "").strip()
        if api_key:
            header = _single_line(getattr(self, "balance_api_auth_header", "Authorization"), 80) or "Authorization"
            scheme = _single_line(getattr(self, "balance_api_auth_scheme", "Bearer"), 40)
            headers[header] = f"{scheme} {api_key}".strip()
        return headers

    @staticmethod
    def _balance_provider_source_key(source: dict[str, Any]) -> str:
        for key in ("key", "api_key", "token", "api_token"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
        return ""

    def _balance_provider_source_headers(self, source: dict[str, Any]) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "astrbot-private-companion/balance-awareness",
        }
        custom = source.get("custom_headers")
        if isinstance(custom, dict):
            blocked = {"host", "content-length", "transfer-encoding", "connection"}
            for raw_key, raw_value in custom.items():
                key = _single_line(raw_key, 80)
                value = _single_line(raw_value, 500)
                if key and value and key.lower() not in blocked:
                    headers[key] = value
        api_key = self._balance_provider_source_key(source)
        if api_key and not any(key.lower() == "authorization" for key in headers):
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _balance_astrbot_config(self) -> dict[str, Any]:
        context = getattr(self, "context", None)
        getter = getattr(context, "get_config", None)
        if not callable(getter):
            return {}
        try:
            config = getter()
        except TypeError:
            try:
                config = getter(umo="")
            except Exception:
                config = {}
        except Exception:
            config = {}
        return config if isinstance(config, dict) else {}

    @staticmethod
    def _balance_provider_source_id_from_model(value: Any) -> str:
        text = _single_line(value, 180)
        return text.split("/", 1)[0].strip() if "/" in text else text

    def _balance_auto_provider_sources(self) -> list[dict[str, Any]]:
        config = self._balance_astrbot_config()
        raw_sources = config.get("provider_sources")
        if not isinstance(raw_sources, list):
            return []
        by_id: dict[str, dict[str, Any]] = {}
        ordered_ids: list[str] = []
        for raw in raw_sources:
            if not isinstance(raw, dict) or raw.get("enable") is False:
                continue
            source_id = _single_line(raw.get("id") or raw.get("provider_source_id"), 120)
            api_base = str(raw.get("api_base") or raw.get("base_url") or raw.get("api_base_url") or "").strip()
            parsed = urlparse(api_base)
            if not source_id or parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            by_id[source_id] = raw

        preferred_values = [
            runtime_persona_setting(self, "llm_provider_id", ""),
            runtime_persona_setting(self, "mai_style_provider_id", ""),
            runtime_persona_setting(self, "response_review_provider_id", ""),
            runtime_persona_setting(self, "proactive_persona_judge_provider_id", ""),
            runtime_persona_setting(self, "fast_response_provider_id", ""),
        ]
        settings = config.get("provider_settings") if isinstance(config.get("provider_settings"), dict) else {}
        preferred_values.extend(
            [
                settings.get("default_provider_id"),
                settings.get("default_llm_provider_id"),
            ]
        )
        for value in preferred_values:
            source_id = self._balance_provider_source_id_from_model(value)
            if source_id and source_id in by_id and source_id not in ordered_ids:
                ordered_ids.append(source_id)
        for source_id in by_id:
            if source_id not in ordered_ids:
                ordered_ids.append(source_id)
        return [by_id[source_id] for source_id in ordered_ids[:6]]

    def _balance_auto_discovery_available(self) -> bool:
        return bool(self._balance_auto_provider_sources())

    def _balance_query_config_signature(self) -> str:
        """Return a secret-free signature so configuration changes bypass stale backoff."""
        configured_url = str(getattr(self, "balance_api_url", "") or "").strip()
        parts: list[str] = []
        if configured_url:
            parsed = urlparse(configured_url)
            parts.append(f"manual:{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}")
            parts.append(f"manual_key:{bool(str(getattr(self, 'balance_api_key', '') or '').strip())}")
        else:
            parts.append("auto")
            for source in self._balance_auto_provider_sources():
                source_id = _single_line(source.get("id") or source.get("provider_source_id"), 120)
                api_base = str(
                    source.get("api_base") or source.get("base_url") or source.get("api_base_url") or ""
                ).strip()
                parsed = urlparse(api_base)
                parts.append(
                    "|".join(
                        (
                            source_id,
                            f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}",
                            str(bool(self._balance_provider_source_key(source))),
                        )
                    )
                )
        parts.extend(
            (
                _single_line(getattr(self, "balance_json_path", ""), 120),
                _single_line(getattr(self, "balance_total_json_path", ""), 120),
                _single_line(getattr(self, "balance_used_json_path", ""), 120),
                str(getattr(self, "balance_value_divisor", 1) or 1),
            )
        )
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _balance_same_origin_url(api_base: str, path: str) -> str:
        parsed = urlparse(str(api_base or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        clean_path = "/" + str(path or "").lstrip("/")
        return f"{parsed.scheme}://{parsed.netloc}{clean_path}"

    def _balance_auto_endpoint_urls(self, source: dict[str, Any]) -> list[str]:
        api_base = str(source.get("api_base") or source.get("base_url") or source.get("api_base_url") or "").strip()
        parsed = urlparse(api_base)
        host = parsed.netloc.lower()
        provider_text = " ".join(
            _single_line(source.get(key), 120).lower()
            for key in ("id", "provider", "type", "provider_type")
        )
        paths: tuple[str, ...]
        if "deepseek" in host or "deepseek" in provider_text:
            paths = ("/user/balance",)
        elif "openrouter.ai" in host:
            paths = ("/api/v1/auth/key",)
        elif "siliconflow" in host:
            paths = ("/v1/user/info",)
        elif any(marker in host for marker in ("volces.com", "googleapis.com", "generativelanguage.googleapis.com")):
            paths = ()
        else:
            paths = self._BALANCE_GENERIC_ENDPOINT_PATHS
        urls: list[str] = []
        for path in paths:
            url = self._balance_same_origin_url(api_base, path)
            if url and url not in urls:
                urls.append(url)
        return urls

    async def _balance_request_json(self, url: str, headers: dict[str, str]) -> Any:
        try:
            import aiohttp
        except ImportError as exc:
            raise RuntimeError("当前环境缺少 aiohttp，无法拉取余额") from exc
        timeout_seconds = min(
            60.0,
            max(2.0, _safe_float(getattr(self, "balance_request_timeout_seconds", 10.0), 10.0, 2.0)),
        )
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as response:
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(f"余额接口返回 HTTP {response.status}")
                try:
                    return await response.json(content_type=None)
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    raise ValueError("余额接口没有返回有效 JSON") from exc

    async def _fetch_auto_balance_snapshot(self) -> dict[str, Any]:
        sources = self._balance_auto_provider_sources()
        if not sources:
            raise RuntimeError("没有可用于自动查询余额的 AstrBot Provider")
        state = self.data.get("balance_awareness") if isinstance(getattr(self, "data", None), dict) else {}
        cached_source_id = _single_line((state or {}).get("auto_source_id"), 120) if isinstance(state, dict) else ""
        cached_endpoint = str((state or {}).get("auto_endpoint") or "").strip() if isinstance(state, dict) else ""
        if cached_source_id:
            sources.sort(key=lambda item: 0 if _single_line(item.get("id"), 120) == cached_source_id else 1)
        attempted_sources: list[str] = []
        attempt_count = 0
        max_attempts = 12
        for source in sources:
            source_id = _single_line(source.get("id") or source.get("provider_source_id"), 120) or "provider"
            endpoints = self._balance_auto_endpoint_urls(source)
            api_base = str(source.get("api_base") or source.get("base_url") or source.get("api_base_url") or "").strip()
            if cached_source_id == source_id and cached_endpoint:
                cached_url = self._balance_same_origin_url(api_base, cached_endpoint)
                if cached_url:
                    endpoints = [cached_url] + [url for url in endpoints if url != cached_url]
            if not endpoints:
                continue
            attempted_sources.append(source_id)
            headers = self._balance_provider_source_headers(source)
            for endpoint in endpoints:
                if attempt_count >= max_attempts:
                    break
                attempt_count += 1
                try:
                    payload = await self._balance_request_json(endpoint, headers)
                    snapshot = self._extract_balance_snapshot(payload)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    continue
                snapshot["query_mode"] = "auto"
                snapshot["source_id"] = source_id
                snapshot["endpoint_path"] = urlparse(endpoint).path
                return snapshot
            if attempt_count >= max_attempts:
                break
        labels = "、".join(attempted_sources[:6]) or "已配置 Provider"
        raise RuntimeError(f"未从 {labels} 找到受支持的余额查询接口")

    def _balance_safe_error(self, exc: Exception) -> str:
        text = _single_line(exc, 500)
        api_key = str(getattr(self, "balance_api_key", "") or "").strip()
        if api_key:
            text = text.replace(api_key, "***")
        configured_url = str(getattr(self, "balance_api_url", "") or "").strip()
        if configured_url:
            text = text.replace(configured_url, "<余额接口>")
        text = re.sub(r"https?://[^\s'\"<>]+", "<余额接口>", text, flags=re.IGNORECASE)
        return _single_line(text, 180) or exc.__class__.__name__

    async def _fetch_balance_snapshot(self) -> dict[str, Any]:
        url = str(getattr(self, "balance_api_url", "") or "").strip()
        if not url:
            return await self._fetch_auto_balance_snapshot()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("余额接口地址必须是有效的 HTTP/HTTPS URL")
        payload = await self._balance_request_json(url, self._balance_request_headers())
        snapshot = self._extract_balance_snapshot(payload)
        snapshot["query_mode"] = "manual"
        snapshot["source_id"] = "custom"
        snapshot["endpoint_path"] = parsed.path
        return snapshot

    def _balance_display(self, snapshot: dict[str, Any]) -> str:
        amount = _safe_float(snapshot.get("amount"), 0.0)
        currency = _single_line(getattr(self, "balance_currency_label", "元"), 20) or "元"
        decimals = 2 if abs(amount) < 1000 else 1
        amount_text = f"{amount:.{decimals}f}".rstrip("0").rstrip(".")
        percent = self._balance_number(snapshot.get("remaining_percent"))
        if percent is not None:
            return f"{amount_text}{currency}（约剩 {max(0.0, percent):.0f}%）"
        return f"{amount_text}{currency}"

    def _format_balance_awareness_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        if reason != "low_balance" or not isinstance(user, dict):
            return ""
        context = user.get("planned_balance_context")
        if not isinstance(context, dict):
            return ""
        tier = _single_line(context.get("tier"), 20)
        display = _single_line(context.get("display"), 80)
        include_amount = bool(getattr(self, "balance_include_amount_in_message", True))
        level_text = "已经很紧张" if tier == "critical" else "有点少了"
        fact = f"当前可支配余额{level_text}" + (f"，参考值为 {display}" if include_amount and display else "")
        return (
            "【余额感知（已由用户主动配置）】\n"
            f"- {fact}。这是 Bot 自身服务/能力的可支配经费状态，不是用户欠款，也不是支付账单。\n"
            "- 可以结合当前人格与世界观，把它自然理解成零花钱、饭卡、钱包、电量或补给，轻轻提一次；也可以只含蓄提醒快不够用了。\n"
            "- 不要照抄后台字段，不要提接口、模型、Token、阈值或系统告警，不要伪造已经充值。\n"
            "- 不施压、不道德绑架、不催转账，不用断联、死亡、生病或停止陪伴来威胁；给对方留出不回应或稍后处理的空间。"
        )

    def _balance_owner_users(self) -> list[tuple[str, dict[str, Any]]]:
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return []
        result: list[tuple[str, dict[str, Any]]] = []
        for raw_user_id, user in users.items():
            user_id = str(raw_user_id or "").strip()
            if not user_id or not isinstance(user, dict) or not user.get("umo"):
                continue
            role_getter = getattr(self, "_private_user_role", None)
            role = role_getter(user, user_id) if callable(role_getter) else str(user.get("relationship_role") or "")
            if role != "owner":
                continue
            enabled_getter = getattr(self, "_user_enabled_for_proactive", None)
            if callable(enabled_getter) and not enabled_getter(user_id, user):
                continue
            result.append((user_id, user))
        return result

    def _queue_low_balance_candidates_locked(self, snapshot: dict[str, Any], *, now: float) -> int:
        tier = self._balance_tier(snapshot)
        if tier not in {"low", "critical"}:
            return 0
        display = self._balance_display(snapshot)
        delay_min = 2.0 if tier == "critical" else 5.0
        delay_max = 8.0 if tier == "critical" else 20.0
        offered = 0
        for user_id, user in self._balance_owner_users():
            scheduled = now + random.uniform(delay_min, delay_max) * 60.0
            context = {
                "tier": tier,
                "display": display,
                "checked_at": now,
                "amount": snapshot.get("amount"),
                "remaining_percent": snapshot.get("remaining_percent"),
            }
            candidate = {
                "source": "balance",
                "reason": "low_balance",
                "action": "message",
                "scheduled_ts": scheduled,
                "window_start_at": scheduled,
                "preferred_ts": scheduled,
                "best_until_at": scheduled + 90 * 60,
                "expire_at": scheduled + 4 * 3600,
                "topic": "零花钱快见底了" if tier == "critical" else "零花钱有点不够用",
                "motive": "察觉自己的可支配余额偏低，想贴合人设轻轻告诉主要用户，不施压也不催促",
                "score": 86 if tier == "critical" else 74,
                "context_key": "planned_balance_context",
                "context": context,
            }
            offer = getattr(self, "_offer_proactive_candidate", None)
            if callable(offer) and offer(user_id, user, candidate):
                offered += 1
        return offered

    async def _maybe_refresh_balance_awareness(self) -> None:
        if not bool(getattr(self, "enable_balance_awareness", False)):
            return
        lock = getattr(self, "_balance_awareness_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._balance_awareness_lock = lock
        async with lock:
            now = _now_ts()
            state = self.data.setdefault("balance_awareness", {})
            if not isinstance(state, dict):
                state = {}
                self.data["balance_awareness"] = state
            config_signature = self._balance_query_config_signature()
            if _single_line(state.get("config_signature"), 40) != config_signature:
                state["config_signature"] = config_signature
                state["next_check_at"] = 0
                state["consecutive_failures"] = 0
            if now < _safe_float(state.get("next_check_at"), 0.0):
                return
            interval = max(5.0, _safe_float(getattr(self, "balance_check_interval_minutes", 60.0), 60.0, 5.0)) * 60.0
            try:
                snapshot = await self._fetch_balance_snapshot()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                safe_error = self._balance_safe_error(exc)
                failures = int(_safe_float(state.get("consecutive_failures"), 0.0, 0.0)) + 1
                auto_unsupported = not str(getattr(self, "balance_api_url", "") or "").strip() and (
                    safe_error.startswith("未从 ")
                    or safe_error.startswith("没有可用于自动查询余额的 AstrBot Provider")
                )
                if auto_unsupported:
                    unsupported_hours = min(168.0, 12.0 * (2 ** min(max(failures - 1, 0), 4)))
                    retry = max(interval, unsupported_hours * 3600.0)
                else:
                    retry = min(interval, max(5 * 60.0, 5 * 60.0 * (2 ** min(failures - 1, 4))))
                state.update(
                    {
                        "last_check_at": now,
                        "next_check_at": now + retry,
                        "last_error": safe_error,
                        "consecutive_failures": failures,
                        "config_signature": config_signature,
                    }
                )
                schedule_save = getattr(self, "_schedule_data_save", None)
                if callable(schedule_save):
                    schedule_save(sections={"balance_awareness"}, delay=0.5)
                if auto_unsupported:
                    logger.warning(
                        "[PrivateCompanion] 当前 Provider 不支持余额查询,已延长探测间隔: "
                        "failures=%s retry_hours=%.1f max_retry_hours=168 error=%s",
                        failures,
                        retry / 3600.0,
                        safe_error,
                    )
                else:
                    logger.warning(
                        "[PrivateCompanion] 余额拉取失败,将在稍后重试: failures=%s error=%s",
                        failures,
                        safe_error,
                    )
                return

            tier = self._balance_tier(snapshot)
            previous_tier = _single_line(state.get("tier"), 20) or "unknown"
            cooldown_hours = max(
                1.0,
                _safe_float(getattr(self, "balance_message_cooldown_hours", 24.0), 24.0, 1.0),
            )
            if tier == "critical":
                cooldown_hours = max(1.0, cooldown_hours / 2.0)
            last_prompted_at = _safe_float(state.get("last_prompted_at"), 0.0)
            transitioned = tier in {"low", "critical"} and tier != previous_tier
            cooldown_due = tier in {"low", "critical"} and now - last_prompted_at >= cooldown_hours * 3600.0
            offered = 0
            data_lock = getattr(self, "_data_lock", None)
            if transitioned or cooldown_due:
                if data_lock is not None:
                    async with data_lock:
                        offered = self._queue_low_balance_candidates_locked(snapshot, now=now)
                else:
                    offered = self._queue_low_balance_candidates_locked(snapshot, now=now)
            state.update(
                {
                    "last_check_at": now,
                    "next_check_at": now + interval,
                    "last_success_at": now,
                    "last_error": "",
                    "consecutive_failures": 0,
                    "config_signature": config_signature,
                    "tier": tier,
                    "previous_tier": previous_tier,
                    "amount": snapshot.get("amount"),
                    "total": snapshot.get("total"),
                    "remaining_percent": snapshot.get("remaining_percent"),
                    "currency_label": _single_line(getattr(self, "balance_currency_label", "元"), 20),
                    "query_mode": _single_line(snapshot.get("query_mode"), 20) or "manual",
                    "auto_source_id": _single_line(snapshot.get("source_id"), 120),
                    "auto_endpoint": _single_line(snapshot.get("endpoint_path"), 160),
                }
            )
            if offered > 0:
                state["last_prompted_at"] = now
                state["last_prompted_tier"] = tier
                state["last_prompted_users"] = offered
            elif tier == "normal":
                state["last_recovered_at"] = now
            schedule_save = getattr(self, "_schedule_data_save", None)
            if callable(schedule_save):
                sections = {"balance_awareness"}
                if offered > 0:
                    sections.add("users")
                schedule_save(sections=sections, delay=0.5)
            if offered:
                logger.info(
                    "[PrivateCompanion] 余额偏低事件已进入主动候选链: tier=%s targets=%s",
                    tier,
                    offered,
                )
