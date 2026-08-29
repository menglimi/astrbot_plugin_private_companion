# -*- coding: utf-8 -*-
"""QQ Zone authentication state, H5 token discovery, and platform actions."""
from __future__ import annotations

import time
from typing import Any

from astrbot.api.event import AstrMessageEvent

from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .qzone_recent_parser import parse_qzone_h5_index_html
from .logging_util import get_module_logger

logger = get_module_logger(__name__)

__all__ = ("QzoneAuthMixin",)

class QzoneAuthMixin:
    """Authentication state, H5 session token, and platform action helpers."""
    @staticmethod
    def _qzone_response_code(payload: Any) -> Any:
        """Return an explicit failure code even when normalized data has code=null."""
        if not isinstance(payload, dict):
            return 0
        values = [payload.get("code"), payload.get("ret"), payload.get("_raw_code")]
        for value in values:
            if value is not None and value != "" and value != 0 and value != "0":
                return value
        return 0

    @staticmethod
    def _qzone_auth_failure_message(message: Any) -> bool:
        text = str(message or "").lower()
        if not text:
            return False
        markers = (
            "code=-3000",
            "请先登录",
            "未登录",
            "login",
            "cookie",
            "p_skey",
            "pskey",
            "skey",
            "g_tk",
            "gtk",
        )
        return any(marker in text for marker in markers)

    def _qzone_state_dict(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        state = data.setdefault("qzone_integration", {})
        if not isinstance(state, dict):
            data["qzone_integration"] = {}
            state = data["qzone_integration"]
        return state

    def _qzone_format_block_until(self, ts: float) -> str:
        try:
            return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
        except Exception:
            return ""

    def _qzone_auto_publish_block_reason(self, state: dict[str, Any] | None = None, *, now: float | None = None) -> str:
        state = state if isinstance(state, dict) else self._qzone_state_dict()
        if not isinstance(state, dict):
            return ""
        current = _now_ts() if now is None else float(now)
        until = _safe_float(state.get("auth_block_until"), 0)
        if until <= current:
            return ""
        reason = _single_line(state.get("last_auth_failure_reason") or "QQ 空间登录状态异常", 100)
        if (
            ("没有可用的 OneBot 连接" in reason or "未配置手动 QZONE_COOKIE" in reason)
            and callable(getattr(self, "_qzone_find_runtime_bot", None))
            and self._qzone_find_runtime_bot() is not None
        ):
            self._qzone_clear_auth_failure(state)
            return ""
        if "qzonetoken" in reason.lower() or "qzonetoken 未在 H5 首页中找到" in reason:
            self._qzone_clear_auth_failure(state)
            return ""
        until_text = self._qzone_format_block_until(until)
        return f"{reason}，自动说说已暂停到 {until_text}" if until_text else reason

    def _qzone_mark_auth_failure(
        self,
        reason: str,
        *,
        source: str = "",
        cooldown_hours: float = 12.0,
        state: dict[str, Any] | None = None,
        save: bool = True,
    ) -> None:
        state = state if isinstance(state, dict) else self._qzone_state_dict()
        if not isinstance(state, dict):
            return
        now = _now_ts()
        clean_reason = _single_line(reason or "QQ 空间登录状态异常", 160)
        last_failed_at = _safe_float(state.get("last_auth_failed_at"), 0)
        previous_count = _safe_int(state.get("auth_failure_count"), 0, 0, 999)
        if last_failed_at and now - last_failed_at > 7 * 24 * 3600:
            previous_count = 0
        failure_count = previous_count + 1
        if failure_count <= 1:
            effective_hours = max(1.0, float(cooldown_hours or 12.0))
            status = "blocked"
        elif failure_count == 2:
            effective_hours = 24.0
            status = "blocked"
        else:
            effective_hours = 24.0 * 7
            status = "stopped"
        cooldown_seconds = effective_hours * 3600.0
        state["last_auth_failed_at"] = now
        state["last_auth_failure_reason"] = clean_reason
        state["last_auth_failure_source"] = _single_line(source, 40)
        state["auth_failure_count"] = failure_count
        state["auth_block_until"] = now + cooldown_seconds
        state["last_auth_status"] = status
        if status == "stopped":
            logger.warning(
                "QQ 空间认证连续失败,自动说说进入保守等待: count=%s until=%s reason=%s",
                failure_count,
                self._qzone_format_block_until(state["auth_block_until"]),
                clean_reason,
            )
        if save:
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                try:
                    saver(sections={"qzone_integration"})
                except Exception:
                    pass

    def _qzone_clear_auth_failure(self, state: dict[str, Any] | None = None) -> None:
        state = state if isinstance(state, dict) else self._qzone_state_dict()
        if not isinstance(state, dict):
            return
        changed = False
        for key in (
            "auth_block_until",
            "last_auth_status",
            "auth_failure_count",
            "last_auth_failure_source",
            "last_auth_failure_reason",
        ):
            if key in state:
                state.pop(key, None)
                changed = True
        for key in ("last_life_publish_status", "last_emotional_vent_status"):
            value = str(state.get(key) or "")
            if value.startswith("paused:auth:"):
                state[key] = "ready:auth_ok"
                changed = True
        if changed:
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                try:
                    saver(sections={"qzone_integration"})
                except Exception:
                    pass

    async def _qzone_preflight_auto_publish(
        self,
        event: AstrMessageEvent | None,
        *,
        state: dict[str, Any] | None = None,
        source: str = "auto",
    ) -> str:
        state = state if isinstance(state, dict) else self._qzone_state_dict()
        block_reason = self._qzone_auto_publish_block_reason(state)
        if block_reason:
            return block_reason
        try:
            cookie_header = await self._qzone_get_cookies(event)
            ctx = self._qzone_context_from_cookies(cookie_header)
            token = ctx.get("qzonetoken") or await self._qzone_ensure_qzonetoken(event, cookie_header=cookie_header, ctx=ctx)
            if not str(token or "").strip():
                state["last_qzonetoken_status"] = "missing:h5_index"
                logger.info("QQ 空间自动发布预检继续: qzonetoken 未找到,纯文字发布仍可使用 g_tk")
            else:
                state["last_qzonetoken_status"] = "ok"
        except Exception as exc:
            reason = _single_line(exc, 160)
            self._qzone_mark_auth_failure(reason, source=source, state=state, save=False)
            return reason
        self._qzone_clear_auth_failure(state)
        return ""

    async def _qzone_h5_index_snapshot(
        self,
        event: AstrMessageEvent | None,
        *,
        cookie_header: str,
        ctx: dict[str, Any],
        max_cache_age: float = 0.0,
    ) -> dict[str, Any]:
        cache = getattr(self, "_qzone_qzonetoken_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._qzone_qzonetoken_cache = cache
        cache_key = str(ctx.get("uin") or "")
        cached = cache.get(cache_key)
        if (
            max_cache_age > 0
            and isinstance(cached, dict)
            and _now_ts() - _safe_float(cached.get("at"), 0) < max_cache_age
            and isinstance(cached.get("payload"), dict)
        ):
            return {
                "token": str(cached.get("token") or "").strip(),
                "payload": dict(cached.get("payload") or {}),
                "cached": True,
                "http_status": _safe_int(cached.get("http_status"), 200, 0),
            }

        import aiohttp

        url = "https://h5.qzone.qq.com/mqzone/index"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "Cookie": ctx.get("cookie_header") or cookie_header,
            "Referer": f"https://user.qzone.qq.com/{ctx['uin']}",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as response:
                    text = await response.text()
                    if response.status >= 400:
                        logger.info("QQ 空间 qzonetoken 获取失败: HTTP %s", response.status)
                        return {"token": "", "payload": {}, "cached": False, "http_status": response.status}
        except Exception as exc:
            logger.info("QQ 空间 qzonetoken 获取失败: %s", _single_line(exc, 120))
            return {"token": "", "payload": {}, "cached": False, "http_status": 0}
        parsed = parse_qzone_h5_index_html(text)
        parsed_token = str(parsed.get("token") or "").strip()
        token = str(
            parsed_token
            or ctx.get("qzonetoken")
            or (cached.get("token") if isinstance(cached, dict) else "")
            or ""
        ).strip()
        payload = parsed.get("payload") if isinstance(parsed.get("payload"), dict) else {}
        cache[cache_key] = {
            "token": token,
            "payload": payload,
            "at": _now_ts(),
            "http_status": response.status,
        }
        if not parsed_token:
            logger.info("QQ 空间 qzonetoken 未在 H5 首页中找到")
        else:
            logger.info("QQ 空间 qzonetoken 已自动获取: uin=%s", ctx.get("uin"))
        return {
            "token": token,
            "payload": payload,
            "cached": False,
            "http_status": response.status,
        }

    async def _qzone_ensure_qzonetoken(
        self,
        event: AstrMessageEvent | None,
        *,
        cookie_header: str,
        ctx: dict[str, Any],
    ) -> str:
        token = str(ctx.get("qzonetoken") or "").strip()
        if token:
            return token
        cache = getattr(self, "_qzone_qzonetoken_cache", None)
        cached = cache.get(str(ctx.get("uin") or "")) if isinstance(cache, dict) else None
        if isinstance(cached, dict) and _now_ts() - _safe_float(cached.get("at"), 0) < 1800:
            token = str(cached.get("token") or "").strip()
            if token:
                return token
        snapshot = await self._qzone_h5_index_snapshot(
            event,
            cookie_header=cookie_header,
            ctx=ctx,
            max_cache_age=1800,
        )
        return str(snapshot.get("token") or "").strip()

    @staticmethod
    def _qzone_find_first(payload: Any, keys: tuple[str, ...], *, _depth: int = 0, _seen: set[int] | None = None) -> Any:
        if payload is None or _depth > 8:
            return None
        if _seen is None:
            _seen = set()
        if isinstance(payload, dict):
            obj_id = id(payload)
            if obj_id in _seen:
                return None
            _seen.add(obj_id)
            normalized = {str(key).lower().replace("-", "_"): value for key, value in payload.items()}
            for key in keys:
                value = normalized.get(str(key).lower().replace("-", "_"))
                if value not in (None, ""):
                    return value
            for value in payload.values():
                found = QzoneAuthMixin._qzone_find_first(value, keys, _depth=_depth + 1, _seen=_seen)
                if found not in (None, ""):
                    return found
        elif isinstance(payload, (list, tuple)):
            for item in payload:
                found = QzoneAuthMixin._qzone_find_first(item, keys, _depth=_depth + 1, _seen=_seen)
                if found not in (None, ""):
                    return found
        return None

    async def _qzone_call_platform_action(self, event: AstrMessageEvent | None, action: str, **kwargs: Any) -> Any:
        if event is None:
            return None
        caller = getattr(self, "_call_platform_action", None)
        if callable(caller):
            try:
                return await caller(event, action, **kwargs)
            except Exception:
                pass
        bot = getattr(event, "bot", None)
        direct = getattr(bot, action, None)
        if callable(direct):
            try:
                maybe = direct(**kwargs)
                return await maybe if hasattr(maybe, "__await__") else maybe
            except Exception:
                pass
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if callable(call_action):
            try:
                maybe = call_action(action, **kwargs)
                return await maybe if hasattr(maybe, "__await__") else maybe
            except Exception:
                return None
        return None
