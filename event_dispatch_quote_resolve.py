# -*- coding: utf-8 -*-
"""EventDispatchQuoteResolveMixin。

由 tools/split_mixin_domain.py 从 event_dispatch.py 机械抽取（17 个方法 + 0 个模块级名字 + 0 个类级赋值 / 332 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 EventDispatchMixin）。
"""
from __future__ import annotations

import re
from .event_dispatch_shared import _persona_feature_enabled, _persona_value, logger
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from astrbot.api.event import AstrMessageEvent
from typing import Any
try:
    from astrbot.api.message_components import At, Image, Plain, Record, Reply
except ImportError:
    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.message.components import Record
    try:
        from astrbot.api.message_components import Reply
    except ImportError:
        try:
            from astrbot.core.message.components import Reply
        except ImportError:
            Reply = None



class EventDispatchQuoteResolveMixin:
    """EventDispatchQuoteResolveMixin（从 EventDispatchMixin 拆出）。"""


    async def _try_delete_message(self, event: AstrMessageEvent, message_id: str, *, reason: str = "") -> bool:
        message_id = _single_line(message_id, 120)
        if not message_id:
            return False
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("message_recall", event=event):
            profile_getter = getattr(self, "_platform_profile", None)
            try:
                profile = profile_getter(event=event) if callable(profile_getter) else {}
            except Exception:
                profile = {}
            platform_label = _single_line(
                (profile or {}).get("label") or (profile or {}).get("raw_platform"),
                40,
            ) or self._quote_cache_key(event)
            logger.debug(
                "当前平台不支持原生撤回，已跳过: platform=%s message_id=%s",
                platform_label,
                message_id,
            )
            return False
        call = getattr(self, "_call_platform_action", None)
        if not callable(call):
            return False
        attempts: list[Any] = [message_id]
        try:
            attempts.append(int(message_id))
        except (TypeError, ValueError):
            pass
        for value in attempts:
            try:
                await call(event, "delete_msg", message_id=value)
                logger.info("已尝试撤回消息: message_id=%s reason=%s", message_id, _single_line(reason, 80))
                return True
            except Exception as exc:
                logger.debug("撤回消息失败: message_id=%s error=%s", message_id, _single_line(exc, 120))
        return False

    def _candidate_trigger_message_id(self, candidate: dict[str, Any]) -> str:
        for key in ("trigger_message_id", "message_id", "msg_id"):
            value = _single_line(candidate.get(key), 120)
            if value:
                return value
        context = candidate.get("context")
        if isinstance(context, dict):
            for key in ("trigger_message_id", "message_id", "msg_id"):
                value = _single_line(context.get(key), 120)
                if value:
                    return value
        return ""

    def _clear_planned_proactive_trigger(self, user: dict[str, Any]) -> None:
        user["planned_proactive_trigger_message_id"] = ""
        user["planned_proactive_trigger_umo"] = ""
        user["planned_proactive_trigger_ts"] = 0
        user["planned_proactive_trigger_inbound_count"] = -1

    def _set_planned_proactive_trigger(
        self,
        user: dict[str, Any],
        *,
        message_id: str,
        umo: str = "",
        created_at: float = 0,
    ) -> None:
        message_id = _single_line(message_id, 120)
        if not message_id:
            self._clear_planned_proactive_trigger(user)
            return
        user["planned_proactive_trigger_message_id"] = message_id
        user["planned_proactive_trigger_umo"] = _single_line(umo, 160)
        user["planned_proactive_trigger_ts"] = created_at if created_at > 0 else _now_ts()
        user["planned_proactive_trigger_inbound_count"] = _safe_int(user.get("private_inbound_count"), 0)

    def _planned_proactive_quote_message_id(self, user: dict[str, Any], umo: str) -> str:
        if not _persona_value(self, 'enable_proactive_quote_trigger_message', False):
            return ""
        if not _persona_value(self, 'enable_quote_private_proactive', True):
            return ""
        message_id = _single_line(user.get("planned_proactive_trigger_message_id"), 120)
        if not message_id:
            return ""
        trigger_umo = _single_line(user.get("planned_proactive_trigger_umo"), 160)
        if trigger_umo and trigger_umo != _single_line(umo, 160):
            return ""
        trigger_ts = _safe_float(user.get("planned_proactive_trigger_ts"), 0)
        if trigger_ts > 0 and _now_ts() - trigger_ts > max(1, _persona_value(self, 'proactive_reply_context_hours', 12)) * 3600:
            return ""
        trigger_inbound_count = _safe_int(user.get("planned_proactive_trigger_inbound_count"), -1)
        if trigger_inbound_count >= 0 and _safe_int(user.get("private_inbound_count"), 0) > trigger_inbound_count:
            return ""
        if trigger_inbound_count < 0:
            latest_activity_getter = getattr(self, "_latest_private_user_activity_ts", None)
            if trigger_ts > 0 and callable(latest_activity_getter):
                try:
                    if _safe_float(latest_activity_getter(user), 0) > trigger_ts:
                        return ""
                except Exception:
                    pass
        return message_id

    def _quote_cache_key(self, event: AstrMessageEvent | None = None) -> str:
        if event is None:
            return "default"
        try:
            platform = str(event.get_platform_name() or "").strip()
        except Exception:
            platform = ""
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 160)
        origin = umo.split(":", 1)[0] if ":" in umo else umo
        return platform or origin or "default"

    def _make_reply_component(self, message_id: str, event: AstrMessageEvent | None = None) -> Any | None:
        platform_supports = getattr(self, "_platform_supports", None)
        if event is not None and callable(platform_supports) and not platform_supports("reply_quote", event=event):
            logger.debug(
                "当前平台不支持指定消息引用，已降级为普通发送: platform=%s",
                self._quote_cache_key(event),
            )
            return None
        if Reply is None:
            logger.debug("当前 AstrBot 运行环境缺少 Reply 组件，引用触发消息已降级。")
            return None
        message_id = _single_line(message_id, 120)
        if not message_id:
            return None
        cache_key = self._quote_cache_key(event)
        style_cache = getattr(self, "_reply_component_style_cache", None)
        if not isinstance(style_cache, dict):
            style_cache = {}
            self._reply_component_style_cache = style_cache
        candidate_ids: list[Any] = [message_id]
        try:
            candidate_ids.append(int(message_id))
        except (TypeError, ValueError):
            pass
        cached = style_cache.get(cache_key)
        if isinstance(cached, tuple) and len(cached) == 2:
            style, value_kind = cached
            for value in candidate_ids:
                if value_kind == "int" and not isinstance(value, int):
                    continue
                if value_kind == "str" and not isinstance(value, str):
                    continue
                try:
                    if style == "positional":
                        return Reply(value)
                    return Reply(**{style: value})
                except Exception:
                    break
            style_cache.pop(cache_key, None)
        for value in candidate_ids:
            for kwargs in ({"id": value}, {"message_id": value}, {"msg_id": value}):
                try:
                    style_cache[cache_key] = (next(iter(kwargs.keys())), "int" if isinstance(value, int) else "str")
                    return Reply(**kwargs)
                except Exception:
                    continue
            try:
                style_cache[cache_key] = ("positional", "int" if isinstance(value, int) else "str")
                return Reply(value)
            except Exception:
                continue
        logger.info(
            "当前平台未能构造 Reply 引用组件，已降级为普通发送: platform=%s message_id=%s",
            cache_key,
            message_id,
        )
        return None

    def _with_optional_reply(self, chain: list[Any], message_id: str, event: AstrMessageEvent | None = None) -> list[Any]:
        reply = self._make_reply_component(message_id, event=event)
        if reply is None:
            return chain
        return [reply, *chain]

    def _chain_has_reply_component(self, chain: list[Any]) -> bool:
        for item in chain:
            if Reply is not None and isinstance(item, Reply):
                return True
            if item.__class__.__name__.lower() == "reply":
                return True
        return False

    def _quote_plain_text_len(self, value: Any) -> int:
        if isinstance(value, list):
            parts: list[str] = []
            for comp in value:
                if hasattr(comp, "text"):
                    parts.append(str(getattr(comp, "text", "") or ""))
            text = "".join(parts)
        else:
            text = str(value or "")
        return len(re.sub(r"\s+", "", text))

    def _quote_skip_reason_for_short_reply(self, text_or_chain: Any = None) -> str:
        threshold = _safe_int(_persona_value(self, 'quote_skip_short_reply_chars', 0), 0, 0)
        if threshold <= 0 or text_or_chain is None:
            return ""
        length = self._quote_plain_text_len(text_or_chain)
        if 0 < length <= threshold:
            return f"short_reply:{length}<={threshold}"
        return ""

    def _quote_group_reply_continuity_window_seconds(self) -> int:
        followup_seconds = _safe_int(_persona_value(self, "group_conversation_followup_seconds", 120), 120, 0)
        return max(300, followup_seconds)

    def _quote_group_reply_should_skip_same_target(
        self,
        event: AstrMessageEvent,
        quote_id: str,
        *,
        text_or_chain: Any = None,
    ) -> bool:
        if not bool(_persona_value(self, 'quote_group_reply_once_per_target', True)):
            return False
        if text_or_chain is None:
            return False
        group_id = self._extract_group_id_from_event(event)
        sender_id = self._event_sender_id(event)
        if not group_id or not sender_id or not quote_id:
            return False
        cache = getattr(self, "_group_reply_quote_target_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._group_reply_quote_target_cache = cache
        now = _now_ts()
        window_seconds = self._quote_group_reply_continuity_window_seconds()
        for key, item in list(cache.items()):
            if not isinstance(item, dict) or now - _safe_float(item.get("ts"), 0) > max(600, window_seconds * 2):
                cache.pop(key, None)
        scope = f"group:{group_id}"
        previous = cache.get(scope) if isinstance(cache.get(scope), dict) else {}
        previous_ts = _safe_float(previous.get("ts"), 0)
        previous_sender = str(previous.get("sender_id") or "")
        if previous_sender == sender_id and previous_ts > 0 and now - previous_ts <= window_seconds:
            previous["ts"] = now
            previous["last_skipped_quote_id"] = _single_line(quote_id, 120)
            cache[scope] = previous
            setattr(event, "private_companion_quote_skip_reason", "same_group_reply_target")
            return True
        cache[scope] = {
            "sender_id": sender_id,
            "quote_id": _single_line(quote_id, 120),
            "ts": now,
        }
        return False

    def _event_quoted_original_message_id(self, event: AstrMessageEvent) -> str:
        for message_id in self._event_reply_message_ids(event):
            if message_id and message_id != self._event_message_id(event):
                return message_id
        return ""

    def _quote_scene_allowed(self, scene_name: str) -> bool:
        if not _persona_value(self, 'enable_proactive_quote_trigger_message', False):
            return False
        if scene_name == "group_reply":
            return bool(_persona_value(self, 'enable_quote_group_reply', True))
        if scene_name == "group_interjection":
            return bool(_persona_value(self, 'enable_quote_group_interjection', True))
        if scene_name == "private_proactive":
            return bool(_persona_value(self, 'enable_quote_private_proactive', True))
        return True

    def _resolve_quote_message_id(
        self,
        event: AstrMessageEvent,
        *,
        scene_name: str = "group_reply",
        text_or_chain: Any = None,
        force_refresh: bool = False,
    ) -> str:
        if not self._quote_scene_allowed(scene_name):
            return ""
        fixed = _single_line(getattr(event, "private_companion_quote_message_id", ""), 120)
        fixed_scene = _single_line(getattr(event, "private_companion_quote_scene", ""), 40)
        if fixed and not force_refresh and (not fixed_scene or fixed_scene == scene_name):
            if self._quote_skip_reason_for_short_reply(text_or_chain):
                return ""
            if scene_name == "group_reply" and self._quote_group_reply_should_skip_same_target(
                event,
                fixed,
                text_or_chain=text_or_chain,
            ):
                return ""
            return fixed
        if scene_name in {"group_reply", "group_interjection"}:
            group_enabled = _persona_feature_enabled(self, "enable_group_companion")
            if not group_enabled:
                return ""
            if not self._extract_group_id_from_event(event):
                return ""
            if scene_name == "group_reply":
                scene = getattr(event, "private_companion_group_scene", None)
                triggered = False
                if isinstance(scene, dict):
                    if str(scene.get("talking_to") or "") == "bot":
                        triggered = True
                    if str(scene.get("trigger") or "") in {
                        "at_bot",
                        "reply_bot",
                        "mention_bot_name",
                        "group_wakeup_direct_word",
                        "group_wakeup_context_word",
                        "group_wakeup_interest",
                        "group_wakeup_question",
                        "group_wakeup_cold_group",
                        "bot_conversation_followup",
                    }:
                        triggered = True
                if getattr(event, "is_at_or_wake_command", False) or getattr(event, "is_wake", False):
                    triggered = True
                if not triggered:
                    return ""
        current_id = self._event_message_id(event)
        if not current_id:
            return ""
        quote_id = current_id
        reason = "current_trigger"
        if scene_name == "group_reply":
            scene = getattr(event, "private_companion_group_scene", None)
            trigger = _single_line((scene or {}).get("trigger") if isinstance(scene, dict) else "", 40)
            quoted_id = self._event_quoted_original_message_id(event)
            strategy = _single_line(_persona_value(self, 'quote_target_strategy', "current"), 20).lower()
            if strategy not in {"current", "quoted", "auto"}:
                strategy = "current"
            if quoted_id and trigger == "reply_bot" and strategy in {"quoted", "auto"}:
                quote_id = quoted_id
                reason = f"{strategy}_quoted_bot_message"
        short_reason = self._quote_skip_reason_for_short_reply(text_or_chain)
        if short_reason:
            setattr(event, "private_companion_quote_skip_reason", short_reason)
            return ""
        if scene_name == "group_reply" and self._quote_group_reply_should_skip_same_target(
            event,
            quote_id,
            text_or_chain=text_or_chain,
        ):
            return ""
        setattr(event, "private_companion_quote_message_id", quote_id)
        setattr(event, "private_companion_quote_scene", scene_name)
        setattr(event, "private_companion_quote_reason", reason)
        return quote_id

    def _group_current_reply_quote_message_id(self, event: AstrMessageEvent, text_or_chain: Any = None) -> str:
        return self._resolve_quote_message_id(event, scene_name="group_reply", text_or_chain=text_or_chain)
