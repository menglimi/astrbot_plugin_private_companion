# -*- coding: utf-8 -*-
"""QQ Zone runtime discovery, cookies, and HTTP transport."""
from __future__ import annotations

import asyncio
import json
import re
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .helpers import _now_ts, _single_line
from .qzone_json import load_qzone_json
from .qzone_errors import QzoneIntegrationError

__all__ = ("QzoneRuntimeMixin",)

class QzoneRuntimeMixin:
    """OneBot runtime discovery, authentication, and HTTP transport helpers."""
    _QZONE_COOKIE_DOMAIN = "user.qzone.qq.com"
    _QZONE_COOKIE_ACTIONS = ("get_cookies", "get_credentials")
    _QZONE_LOGIN_INFO_ACTIONS = ("get_login_info",)
    _QZONE_ACTION_CALLER_ATTRS = ("call_action", "call_api", "call", "api_call", "send_action")
    _QZONE_ACTION_OWNER_ATTRS = (
        "api",
        "bot",
        "client",
        "adapter",
        "connection",
        "onebot",
        "platform",
        "platform_impl",
        "impl",
        "instance",
    )

    def _qzone_primary_persona_id(self) -> str:
        getter = getattr(self, "_primary_persona_id", None)
        if callable(getter):
            try:
                primary = getter()
            except Exception:
                primary = ""
            if str(primary or "").strip():
                return str(primary).strip()
        return str(getattr(self, "plugin_specific_persona_id", "") or "").strip()

    def _qzone_operation_lock(self, name: str) -> asyncio.Lock:
        attr = f"_qzone_{name}_lock"
        lock = getattr(self, attr, None)
        if lock is None:
            lock = asyncio.Lock()
            setattr(self, attr, lock)
        return lock

    def _qzone_automatic_persona_active(self) -> bool:
        """Run account-wide maintenance once, under the primary persona."""
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            return True
        active_getter = getattr(self, "_active_persona_scope", None)
        active = str(active_getter() if callable(active_getter) else "").strip()
        primary = self._qzone_primary_persona_id()
        return bool(primary and active == primary)

    def _qzone_activate_primary_persona(self) -> Any | None:
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            return None
        active_getter = getattr(self, "_active_persona_scope", None)
        active = str(active_getter() if callable(active_getter) else "").strip()
        primary = self._qzone_primary_persona_id()
        if not primary or active == primary:
            return None
        activator = getattr(self, "_activate_persona_id", None)
        return activator(primary) if callable(activator) else None

    def _qzone_deactivate_persona(self, token: Any | None) -> None:
        if token is None:
            return
        deactivator = getattr(self, "_deactivate_persona_for_event", None)
        if callable(deactivator):
            deactivator(token)

    _QZONE_COOKIE_VALUE_KEYS = (
        "cookies",
        "cookie",
        "cookie_text",
        "cookie_str",
        "cookies_str",
        "data",
        "result",
        "retdata",
        "ret_data",
        "payload",
        "response",
    )
    _QZONE_COOKIE_SECRET_KEYS = ("p_skey", "skey", "pskey", "skey2")
    _QZONE_COOKIE_DOMAIN_FALLBACKS = (
        "user.qzone.qq.com",
        "qzone.qq.com",
        "h5.qzone.qq.com",
        "mobile.qzone.qq.com",
        "taotao.qzone.qq.com",
        "qun.qzone.qq.com",
        "ti.qq.com",
        "qq.com",
    )

    def _qzone_plugin_dir(self) -> Path:
        candidates = [
            Path(__file__).resolve().parent.parent / "astrbot_plugin_qzone",
            Path(self.data_dir).parent.parent / "plugins" / "astrbot_plugin_qzone",
        ]
        for path in candidates:
            if (path / "main.py").exists():
                return path
        return candidates[0]

    def _find_qzone_instance(self) -> Any | None:
        return None

    def _qzone_platform_supported(self, event: AstrMessageEvent | None = None) -> bool:
        platform_supports = getattr(self, "_platform_supports", None)
        if event is not None and callable(platform_supports):
            return bool(platform_supports("qzone", event=event))
        platform_available = getattr(self, "_platform_kind_available", None)
        if callable(platform_available):
            return bool(platform_available("onebot"))
        return True

    @staticmethod
    def _qzone_platform_unavailable_message() -> str:
        return "QQ 官方机器人不支持 QQ 空间；该能力仅在 OneBot/aiocqhttp 平台可用。"

    def _qzone_available(self, event: AstrMessageEvent | None = None) -> bool:
        return bool(self.enable_qzone_integration and self._qzone_platform_supported(event))

    def _qzone_note_event_bot(self, event: AstrMessageEvent | None) -> None:
        """Cache the latest OneBot connection for background Qzone jobs."""
        if event is not None and not self._qzone_platform_supported(event):
            return
        bot = getattr(event, "bot", None) if event is not None else None
        if bot is None:
            return
        for candidate in self._qzone_runtime_bot_candidates(bot):
            if self._qzone_runtime_bot_usable(candidate):
                self._qzone_last_bot = candidate
                self._qzone_clear_no_onebot_auth_failure()
                return
        self._qzone_last_bot = bot

    def _qzone_clear_no_onebot_auth_failure(self) -> None:
        state = self._qzone_state_dict()
        if not isinstance(state, dict):
            return
        reason = str(state.get("last_auth_failure_reason") or "")
        if "没有可用的 OneBot 连接" not in reason and "未配置手动 QZONE_COOKIE" not in reason:
            return
        clearer = getattr(self, "_qzone_clear_auth_failure", None)
        if callable(clearer):
            clearer(state)

    @staticmethod
    def _qzone_runtime_bot_usable(candidate: Any) -> bool:
        if candidate is None:
            return False
        if any(callable(getattr(candidate, name, None)) for name in ("get_cookies", "get_credentials", "get_login_info")):
            return True
        if any(callable(getattr(candidate, name, None)) for name in QzoneRuntimeMixin._QZONE_ACTION_CALLER_ATTRS):
            return True
        api = getattr(candidate, "api", None)
        return any(callable(getattr(api, name, None)) for name in QzoneRuntimeMixin._QZONE_ACTION_CALLER_ATTRS)

    def _qzone_runtime_bot_candidates(self, source: Any) -> list[Any]:
        """Return likely OneBot client objects from an AstrBot platform wrapper."""
        if source is None:
            return []
        candidates: list[Any] = [source]
        for attr in (
            "bot",
            "client",
            "adapter",
            "connection",
            "onebot",
            "platform",
            "platform_impl",
            "impl",
            "instance",
        ):
            try:
                value = getattr(source, attr, None)
            except Exception:
                value = None
            if value is not None:
                candidates.append(value)
        api = getattr(source, "api", None)
        if api is not None:
            candidates.append(api)
        deduped: list[Any] = []
        seen: set[int] = set()
        for item in candidates:
            marker = id(item)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(item)
        return deduped

    @staticmethod
    def _qzone_unique_texts(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _qzone_cookie_domain_candidates(self, configured_domain: str = "") -> list[str]:
        domain = str(configured_domain or self._QZONE_COOKIE_DOMAIN or "").strip()
        candidates: list[str] = []
        if domain:
            candidates.append(domain)
            if "://" in domain:
                parsed = urlparse(domain)
                host = parsed.netloc or parsed.path
                if host:
                    candidates.extend([host, f"https://{host}", f"https://{host}/"])
            else:
                candidates.extend([f"https://{domain}", f"https://{domain}/"])
        for fallback in self._QZONE_COOKIE_DOMAIN_FALLBACKS:
            candidates.extend([fallback, f"https://{fallback}", f"https://{fallback}/"])
        return self._qzone_unique_texts(candidates)

    def _qzone_iter_action_callers(self, bot: Any) -> list[Any]:
        callers: list[Any] = []
        seen_owners: set[int] = set()
        seen_callers: set[int] = set()
        owners: list[Any] = [bot]
        index = 0
        while index < len(owners):
            owner = owners[index]
            index += 1
            if owner is None:
                continue
            marker = id(owner)
            if marker in seen_owners:
                continue
            seen_owners.add(marker)
            for attr in self._QZONE_ACTION_CALLER_ATTRS:
                try:
                    caller = getattr(owner, attr, None)
                except Exception:
                    caller = None
                if callable(caller) and id(caller) not in seen_callers:
                    seen_callers.add(id(caller))
                    callers.append(caller)
            for attr in self._QZONE_ACTION_OWNER_ATTRS:
                try:
                    nested = getattr(owner, attr, None)
                except Exception:
                    nested = None
                if nested is not None and id(nested) not in seen_owners:
                    owners.append(nested)
        return callers

    @staticmethod
    def _qzone_invoke_action_callable(callable_obj: Any, action: str, params: dict[str, Any]) -> Any:
        attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        if action:
            envelope = dict(params)
            attempts.extend(
                [
                    ((action,), dict(params)),
                    ((), {"action": action, **params}),
                    ((action, params), {}),
                    ((action,), {"params": params}),
                    ((), {"action": action, "params": params}),
                    (({"action": action, "params": envelope},), {}),
                    (({"action": action, "data": envelope},), {}),
                    (({"action": action, "payload": envelope},), {}),
                    (({"api": action, "params": envelope},), {}),
                    (({"api": action, "data": envelope},), {}),
                    ((action,), {"data": params}),
                    ((), {"action": action, "data": params}),
                    ((action,), {"payload": params}),
                    ((), {"action": action, "payload": params}),
                ]
            )
        else:
            attempts.extend(
                [
                    ((), dict(params)),
                    ((params,), {}),
                    ((), {"params": params}),
                    ((), {"data": params}),
                    ((), {"payload": params}),
                ]
            )
        last_error: TypeError | None = None
        for args, kwargs in attempts:
            try:
                return callable_obj(*args, **kwargs)
            except TypeError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        return callable_obj(action, **params) if action else callable_obj(**params)

    async def _qzone_call_onebot_action(self, bot: Any, action: str, **params: Any) -> Any:
        direct = getattr(bot, action, None)
        if callable(direct):
            result = self._qzone_invoke_action_callable(direct, "", params)
            return await result if hasattr(result, "__await__") else result
        last_error: Exception | None = None
        for caller in self._qzone_iter_action_callers(bot):
            try:
                result = self._qzone_invoke_action_callable(caller, action, params)
                return await result if hasattr(result, "__await__") else result
            except Exception as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise RuntimeError("OneBot client does not expose get_cookies/get_credentials")

    def _qzone_find_runtime_bot(self) -> Any | None:
        bot = getattr(self, "_qzone_last_bot", None)
        if self._qzone_runtime_bot_usable(bot):
            return bot
        context = getattr(self, "context", None)
        if context is not None:
            try:
                platform = context.get_platform("aiocqhttp")
            except Exception:
                platform = None
            if platform is not None:
                direct_bot = getattr(platform, "bot", None)
                if self._qzone_runtime_bot_usable(direct_bot):
                    self._qzone_last_bot = direct_bot
                    return direct_bot
                for candidate in self._qzone_runtime_bot_candidates(platform):
                    if self._qzone_runtime_bot_usable(candidate):
                        self._qzone_last_bot = candidate
                        return candidate
        for candidate in self._qzone_runtime_bot_candidates(bot):
            if self._qzone_runtime_bot_usable(candidate):
                self._qzone_last_bot = candidate
                return candidate
        platform_manager = getattr(getattr(self, "context", None), "platform_manager", None)
        platform_lists: list[Any] = []
        for attr in ("platform_insts", "platform_instances", "instances", "platforms"):
            try:
                value = getattr(platform_manager, attr, None)
            except Exception:
                value = None
            if value:
                platform_lists.append(value.values() if isinstance(value, dict) else value)
        for platforms in platform_lists:
            try:
                iterable = list(platforms or [])
            except Exception:
                iterable = []
            for inst in iterable:
                for candidate in self._qzone_runtime_bot_candidates(inst):
                    if self._qzone_runtime_bot_usable(candidate):
                        self._qzone_last_bot = candidate
                        return candidate
        return None

    async def _qzone_try_direct_cookie_fetch(self, bot: Any, domain: str) -> dict[str, str]:
        merged: dict[str, str] = {}
        login_uin = await self._qzone_fetch_login_uin(bot)
        for action in self._QZONE_COOKIE_ACTIONS:
            for candidate_domain in self._qzone_cookie_domain_candidates(domain):
                for params in ({"domain": candidate_domain}, {}):
                    try:
                        result = await asyncio.wait_for(self._qzone_call_onebot_action(bot, action, **params), timeout=8.0)
                    except Exception:
                        continue
                    cookie_text = self._qzone_extract_cookie_text(result)
                    if not cookie_text:
                        continue
                    cookies = self._qzone_parse_cookie_text(cookie_text)
                    if login_uin and not self._qzone_normalize_uin(cookies):
                        cookies["uin"] = f"o{login_uin}"
                        cookies["p_uin"] = f"o{login_uin}"
                    merged.update(cookies)
                    if self._qzone_cookie_has_identity_and_secret(merged):
                        return merged
        return merged

    @staticmethod
    def _qzone_gtk(p_skey: str) -> str:
        hash_val = 5381
        for ch in str(p_skey or ""):
            hash_val += (hash_val << 5) + ord(ch)
        return str(hash_val & 0x7FFFFFFF)

    @staticmethod
    def _qzone_normalize_cookie_fields(cookies: dict[str, Any]) -> dict[str, str]:
        aliases = {
            "pskey": "p_skey",
            "p-skey": "p_skey",
            "p_skey": "p_skey",
            "p_uin": "p_uin",
            "ptui_loginuin": "ptui_loginuin",
            "csrf-token": "csrf_token",
            "csrf_token": "csrf_token",
            "bkn": "g_tk",
            "gtk": "g_tk",
        }
        normalized: dict[str, str] = {}
        for key, value in (cookies or {}).items():
            if value in (None, ""):
                continue
            original = str(key).strip()
            if not original:
                continue
            text = str(value).strip().strip('"')
            if not text:
                continue
            alias_key = original.lower().replace("-", "_")
            canonical = aliases.get(alias_key, aliases.get(original.lower(), original))
            normalized.setdefault(original, text)
            normalized.setdefault(canonical, text)
        if "uin" in normalized and "p_uin" not in normalized:
            normalized["p_uin"] = normalized["uin"]
        if "p_uin" in normalized and "uin" not in normalized:
            normalized["uin"] = normalized["p_uin"]
        return normalized

    @classmethod
    def _qzone_parse_cookie_text(cls, cookie_text: str) -> dict[str, str]:
        raw = str(cookie_text or "").strip()
        if not raw:
            return {}
        if raw.lower().startswith("cookie:"):
            raw = raw.split(":", 1)[1].strip()
        raw = raw.replace("\r", ";").replace("\n", ";")
        if raw.startswith(("{", "[")):
            try:
                payload = json.loads(raw)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                return cls._qzone_normalize_cookie_fields(payload)
        try:
            return cls._qzone_normalize_cookie_fields({key: morsel.value for key, morsel in SimpleCookie(raw).items()})
        except Exception:
            parsed: dict[str, str] = {}
            for part in raw.split(";"):
                if "=" not in part:
                    continue
                key, value = part.split("=", 1)
                key = key.strip()
                if key:
                    parsed[key] = value.strip().strip('"')
            return cls._qzone_normalize_cookie_fields(parsed)

    @staticmethod
    def _qzone_cookie_header(cookies: dict[str, Any]) -> str:
        return "; ".join(f"{key}={value}" for key, value in (cookies or {}).items() if key and value not in (None, ""))

    def _qzone_extract_cookie_text(self, payload: Any, *, _depth: int = 0, _seen: set[int] | None = None) -> str:
        if _seen is None:
            _seen = set()
        if payload is None or _depth > 8:
            return ""
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8")
            except Exception:
                return ""
        if isinstance(payload, str):
            text = payload.strip()
            if text.startswith(("{", "[")):
                try:
                    return self._qzone_extract_cookie_text(json.loads(text), _depth=_depth + 1, _seen=_seen)
                except Exception:
                    pass
            return text if "=" in text and re.search(r"\b(?:uin|p_uin|skey|p_skey|pskey|g_tk|gtk|bkn)\s*=", text, re.I) else ""
        if isinstance(payload, (list, tuple)):
            parts = [self._qzone_extract_cookie_text(item, _depth=_depth + 1, _seen=_seen) for item in payload]
            cookies: dict[str, str] = {}
            for part in parts:
                cookies.update(self._qzone_parse_cookie_text(part))
            return self._qzone_cookie_header(cookies)
        if not isinstance(payload, dict):
            return ""
        obj_id = id(payload)
        if obj_id in _seen:
            return ""
        _seen.add(obj_id)
        name = payload.get("name") or payload.get("key")
        value = payload.get("value")
        if name and value not in (None, ""):
            return f"{name}={value}"
        cookie_keys = set(self._QZONE_COOKIE_VALUE_KEYS)
        allow = {
            "uin",
            "p_uin",
            "ptui_loginuin",
            "luin",
            "skey",
            "p_skey",
            "pskey",
            "skey2",
            "pt4_token",
            "pt_key",
            "pt_login_sig",
            "clientkey",
            "superkey",
            "qzonetoken",
            "qm_keyst",
            "qm_sid",
            "o_cookie",
            "uin_cookie",
            "rv2",
            "ptcz",
            "lskey",
            "ldw",
            "g_tk",
            "gtk",
            "bkn",
            "csrf_token",
            "qqmusic_key",
        }
        cookies = {
            str(key): value
            for key, value in payload.items()
            if str(key).lower().replace("-", "_") in allow and value not in (None, "")
        }
        parts = [self._qzone_cookie_header(self._qzone_normalize_cookie_fields(cookies))] if cookies else []
        for key in cookie_keys:
            if key in payload:
                text = self._qzone_extract_cookie_text(payload.get(key), _depth=_depth + 1, _seen=_seen)
                if text:
                    parts.append(text)
        for value in payload.values():
            if isinstance(value, (dict, list, tuple, str, bytes)):
                text = self._qzone_extract_cookie_text(value, _depth=_depth + 1, _seen=_seen)
                if text:
                    parts.append(text)
        merged: dict[str, str] = {}
        for part in parts:
            merged.update(self._qzone_parse_cookie_text(part))
        return self._qzone_cookie_header(merged)

    @staticmethod
    def _qzone_normalize_uin(cookies: dict[str, Any]) -> int:
        for key in ("uin", "p_uin", "ptui_loginuin", "luin"):
            raw = str(cookies.get(key) or "").strip().lstrip("oO")
            if raw.isdigit():
                return int(raw)
        return 0

    def _qzone_cookie_has_identity_and_secret(self, cookies: dict[str, Any]) -> bool:
        normalized = self._qzone_normalize_cookie_fields(cookies or {})
        return bool(
            self._qzone_normalize_uin(normalized)
            and any(str(normalized.get(key) or "").strip() for key in self._QZONE_COOKIE_SECRET_KEYS)
        )

    def _qzone_note_cookie_fetch_status(
        self,
        status: str,
        *,
        cookies: dict[str, Any] | None = None,
        ctx: dict[str, Any] | None = None,
        reason: str = "",
    ) -> None:
        try:
            state = self.data.setdefault("qzone_integration", {})
            if not isinstance(state, dict):
                self.data["qzone_integration"] = {}
                state = self.data["qzone_integration"]
            source = ctx.get("cookies") if isinstance(ctx, dict) else cookies
            normalized = self._qzone_normalize_cookie_fields(source or {})
            state["last_cookie_fetch_status"] = _single_line(status, 40)
            state["last_cookie_fetch_at"] = _now_ts()
            state["last_cookie_fetch_has_uin"] = bool(self._qzone_normalize_uin(normalized))
            state["last_cookie_fetch_has_skey"] = bool(normalized.get("skey"))
            state["last_cookie_fetch_has_p_skey"] = bool(normalized.get("p_skey") or normalized.get("pskey"))
            if isinstance(ctx, dict) and ctx.get("uin"):
                state["last_cookie_fetch_uin"] = str(ctx.get("uin"))
            if reason:
                state["last_cookie_fetch_reason"] = _single_line(reason, 160)
            elif status == "ok":
                state.pop("last_cookie_fetch_reason", None)
            if callable(getattr(self, "_save_data_sync", None)):
                self._save_data_sync(sections={"qzone_integration"})
        except Exception:
            logger.debug("[PrivateCompanion] QQ 空间 Cookie 状态记录失败", exc_info=True)

    async def _qzone_fetch_login_uin(self, bot: Any) -> int:
        for action in self._QZONE_LOGIN_INFO_ACTIONS:
            try:
                payload = await asyncio.wait_for(self._qzone_call_onebot_action(bot, action), timeout=5.0)
            except Exception:
                continue
            if isinstance(payload, str) and payload.strip().startswith(("{", "[")):
                try:
                    payload = json.loads(payload)
                except Exception:
                    pass
            candidates: list[Any] = []
            if isinstance(payload, dict):
                candidates.extend(
                    [
                        payload.get("user_id"),
                        payload.get("uin"),
                        payload.get("qq"),
                        payload.get("self_id"),
                    ]
                )
                for key in ("data", "result", "retdata", "payload", "response"):
                    nested = payload.get(key)
                    if isinstance(nested, dict):
                        candidates.extend([nested.get("user_id"), nested.get("uin"), nested.get("qq"), nested.get("self_id")])
            for value in candidates:
                cleaned = str(value or "").strip().lstrip("oO")
                if cleaned.isdigit():
                    return int(cleaned)
        return 0

    def _qzone_context_from_cookies(self, cookies_str: str) -> dict[str, Any]:
        parsed = self._qzone_parse_cookie_text(cookies_str)
        uin = self._qzone_normalize_uin(parsed)
        if not uin:
            raise RuntimeError("Cookie 中缺少合法 uin")
        p_skey = parsed.get("p_skey") or parsed.get("pskey") or ""
        skey = parsed.get("skey") or ""
        existing_gtk = str(parsed.get("g_tk") or parsed.get("gtk") or parsed.get("bkn") or parsed.get("csrf_token") or "")
        secret = p_skey or skey or parsed.get("skey2") or ""
        gtk = self._qzone_gtk(secret) if secret else (existing_gtk if existing_gtk.isdigit() else "")
        if not gtk:
            raise RuntimeError("Cookie 中缺少 p_skey/skey，无法计算 g_tk")
        cookies = {**parsed, "uin": f"o{uin}"}
        if skey:
            cookies["skey"] = skey
        if p_skey:
            cookies["p_skey"] = p_skey
        return {
            "uin": int(uin),
            "skey": skey,
            "p_skey": p_skey,
            "qzonetoken": parsed.get("qzonetoken") or parsed.get("qzone_token") or "",
            "gtk": gtk,
            "cookies": cookies,
            "cookie_header": self._qzone_cookie_header(cookies),
        }

    async def _qzone_get_cookies(self, event: AstrMessageEvent | None = None) -> str:
        if not self._qzone_platform_supported(event):
            raise QzoneIntegrationError("平台不支持", self._qzone_platform_unavailable_message())
        manual_cookie = str(getattr(self, "qzone_cookie", "") or "").strip()
        if manual_cookie:
            try:
                ctx = self._qzone_context_from_cookies(manual_cookie)
            except Exception as exc:
                self._qzone_note_cookie_fetch_status("manual_failed", cookies=self._qzone_parse_cookie_text(manual_cookie), reason=str(exc))
                raise RuntimeError(
                    "手动 QZONE_COOKIE 不可用："
                    f"{_single_line(exc, 120)}；"
                    "需包含 uin/p_uin 与 p_skey 或 skey，可从已登录 QQ 空间的浏览器请求头 Cookie 复制"
                ) from exc
            logger.debug("[PrivateCompanion] QQ 空间使用手动 QZONE_COOKIE: uin=%s", ctx.get("uin"))
            self._qzone_note_cookie_fetch_status("manual_ok", ctx=ctx)
            return ctx["cookie_header"]
        bot = getattr(event, "bot", None) if event is not None else None
        if bot is not None:
            usable_bot = None
            for candidate in self._qzone_runtime_bot_candidates(bot):
                if self._qzone_runtime_bot_usable(candidate):
                    usable_bot = candidate
                    break
            bot = usable_bot or bot
        if bot is None or not self._qzone_runtime_bot_usable(bot):
            bot = self._qzone_find_runtime_bot()
        if bot is not None:
            self._qzone_last_bot = bot
        if bot is None:
            self._qzone_note_cookie_fetch_status("failed", reason="没有可用的 OneBot 连接，且未配置手动 QZONE_COOKIE")
            raise RuntimeError(
                "没有可用的 OneBot 连接，且未配置手动 QZONE_COOKIE；"
                "请在配置页填写浏览器 QQ 空间 Cookie，或确认 OneBot 已连接并支持 get_cookies/get_credentials"
            )
        merged = await self._qzone_try_direct_cookie_fetch(bot, self._QZONE_COOKIE_DOMAIN)
        if self._qzone_cookie_has_identity_and_secret(merged):
            cookie_text = self._qzone_cookie_header(self._qzone_normalize_cookie_fields(merged))
            ctx = self._qzone_context_from_cookies(cookie_text)
            logger.debug("[PrivateCompanion] QQ 空间自动获取 Cookie 成功: uin=%s", ctx.get("uin"))
            self._qzone_note_cookie_fetch_status("ok", ctx=ctx)
            return ctx["cookie_header"]
        self._qzone_note_cookie_fetch_status("failed", cookies=merged)
        raise RuntimeError(
            "获取 QQ 空间 Cookie 失败"
            f"（uin={'有' if self._qzone_normalize_uin(merged) else '无'}"
            f"，skey={'有' if bool(merged.get('skey')) else '无'}"
            f"，p_skey={'有' if bool(merged.get('p_skey') or merged.get('pskey')) else '无'}）；"
            "可改填手动 QZONE_COOKIE，需包含 uin/p_uin 与 p_skey 或 skey"
        )

    @staticmethod
    def _qzone_response_object_candidates(raw: str) -> list[str]:
        source = str(raw or "")
        candidates: list[str] = []
        for start, char in enumerate(source):
            if char != "{":
                continue
            depth = 0
            quote = ""
            escaped = False
            for index in range(start, len(source)):
                current = source[index]
                if quote:
                    if escaped:
                        escaped = False
                    elif current == "\\":
                        escaped = True
                    elif current == quote:
                        quote = ""
                    continue
                if current in {"'", '"'}:
                    quote = current
                    continue
                if current == "{":
                    depth += 1
                    continue
                if current == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append(source[start : index + 1])
                        break
            if len(candidates) >= 24:
                break
        return candidates

    @staticmethod
    def _qzone_load_response_payload(payload: str) -> dict[str, Any]:
        parsed = load_qzone_json(payload)
        if isinstance(parsed, dict):
            return parsed
        return {"code": -1, "message": "接口响应不是对象"}

    @staticmethod
    def _qzone_parse_response(text: str) -> dict[str, Any]:
        raw = str(text or "")
        if not raw.strip():
            return {"code": -1, "message": "接口返回空响应"}
        candidates = QzoneRuntimeMixin._qzone_response_object_candidates(raw)
        if not candidates:
            return {"code": -1, "message": "接口响应缺少 JSON"}

        def normalize(parsed: dict[str, Any]) -> dict[str, Any]:
            if isinstance(parsed.get("data"), dict):
                nested = dict(parsed.get("data") or {})
                nested.setdefault("_raw_code", parsed.get("code", parsed.get("ret")))
                nested.setdefault("_raw_message", parsed.get("message") or parsed.get("msg"))
                return nested
            return parsed

        first_object: dict[str, Any] | None = None
        last_error = ""
        response_keys = {"code", "ret", "message", "msg", "data", "subcode"}
        for payload in candidates:
            try:
                parsed = QzoneRuntimeMixin._qzone_load_response_payload(payload)
            except Exception as exc:
                last_error = _single_line(exc, 80)
                continue
            if not isinstance(parsed, dict):
                continue
            if first_object is None:
                first_object = parsed
            if response_keys & set(parsed):
                return normalize(parsed)
        if first_object is not None:
            return normalize(first_object)
        return {"code": -1, "message": f"JSON 解析失败：{last_error or '没有可解析对象'}"}

    async def _qzone_request(
        self,
        event: AstrMessageEvent | None,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 20.0,
        cookie_header: str | None = None,
    ) -> dict[str, Any]:
        import aiohttp

        if cookie_header is None:
            cookie_header = await self._qzone_get_cookies(event)
        ctx = self._qzone_context_from_cookies(cookie_header)
        parsed_url = urlparse(url)
        request_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "Cookie": ctx["cookie_header"],
            "Referer": f"https://user.qzone.qq.com/{ctx['uin']}",
            "Origin": "https://user.qzone.qq.com",
            "Host": parsed_url.netloc or "user.qzone.qq.com",
            "Connection": "keep-alive",
        }
        if headers:
            request_headers.update(headers)
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=request_headers) as session:
                async with session.request(method, url, params=params, data=data) as response:
                    text = await response.text()
                    parsed = self._qzone_parse_response(text)
                    parsed.setdefault("_http_status", response.status)
                    if parsed.get("message") == "接口返回空响应":
                        parsed["message"] = f"接口返回空响应（HTTP {response.status}）"
                    if response.status == 403 and parsed.get("code") in {-1, None}:
                        parsed["message"] = "无权限访问 QQ 空间或 Cookie 已失效"
                    return parsed
        except aiohttp.ClientConnectorError as exc:
            raise QzoneIntegrationError("网络连接失败", _single_line(exc, 140), retryable=True) from exc
        except (asyncio.TimeoutError, aiohttp.ServerDisconnectedError, aiohttp.ClientPayloadError) as exc:
            mutating = str(method or "GET").upper() not in {"GET", "HEAD"}
            raise QzoneIntegrationError(
                "投递结果未知" if mutating else "网络读取失败",
                _single_line(exc, 140) or type(exc).__name__,
                retryable=not mutating,
                delivery_unknown=mutating,
            ) from exc
        except aiohttp.ClientError as exc:
            mutating = str(method or "GET").upper() not in {"GET", "HEAD"}
            raise QzoneIntegrationError(
                "投递结果未知" if mutating else "网络读取失败",
                _single_line(exc, 140),
                retryable=not mutating,
                delivery_unknown=mutating,
            ) from exc
