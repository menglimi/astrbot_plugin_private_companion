# -*- coding: utf-8 -*-
"""FinalResponsePersistencePart02Mixin。

由 tools/split_mixin_domain.py 从 final_response_persistence.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 439 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 FinalResponsePersistenceMixin）。
"""
from __future__ import annotations

import hashlib
import uuid
from .final_response_persistence_shared import logger
from .helpers import _now_ts, _safe_float, _single_line, _strip_internal_message_blocks
from .persona_config import runtime_persona_setting
from .segmented_message import sanitize_llm_segment_control_tokens
from astrbot.api.event import AstrMessageEvent
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.star.star import star_map
from typing import Any



class FinalResponsePersistencePart02Mixin:
    """FinalResponsePersistencePart02Mixin（从 FinalResponsePersistenceMixin 拆出）。"""


    async def _record_final_assistant_in_livingmemory(
        self,
        *,
        umo: str,
        assistant_response: str,
        delivery_id: str,
        event: AstrMessageEvent | None = None,
    ) -> bool:
        response_text = sanitize_llm_segment_control_tokens(assistant_response)
        umo = str(umo or "").strip()
        if not umo or not response_text:
            return False

        handlers = self._livingmemory_response_handlers()
        if event is not None and bool(
            getattr(event, "_private_companion_persistence_managed", False)
        ):
            selected_names = set(
                getattr(event, "_private_companion_livingmemory_plugin_names", ()) or ()
            )
            if not selected_names:
                return False
            handlers = [
                handler
                for handler in handlers
                if self._handler_plugin_name(handler) in selected_names
            ]
        if not handlers:
            return False

        dedup_key = str(delivery_id or "").strip() or hashlib.sha1(
            f"{umo}\0{response_text}".encode("utf-8", errors="ignore")
        ).hexdigest()
        recorded = getattr(self, "_livingmemory_final_delivery_ids", None)
        if not isinstance(recorded, dict):
            recorded = {}
            self._livingmemory_final_delivery_ids = recorded
        if dedup_key in recorded:
            return True

        dispatch_event = event or self._proactive_synthetic_event(
            umo,
            prompt="",
            name=str(runtime_persona_setting(self, "bot_name", "小星") or "PrivateCompanion"),
        )
        if dispatch_event is None:
            return False
        setattr(dispatch_event, "_private_companion_final_memory_dispatch", True)
        response = LLMResponse(role="assistant", completion_text=response_text)
        delivered = False
        invoked_plugins: set[int] = set()
        for handler in handlers:
            plugin_metadata = star_map.get(
                str(getattr(handler, "handler_module_path", "") or "")
            )
            plugin_instance = getattr(plugin_metadata, "star_cls", None)
            direct_handler = getattr(plugin_instance, "handle_memory_reflection", None)
            try:
                if callable(direct_handler) and id(plugin_instance) not in invoked_plugins:
                    await direct_handler(dispatch_event, response)
                    invoked_plugins.add(id(plugin_instance))
                elif not callable(direct_handler):
                    await handler.handler(dispatch_event, response)
                else:
                    continue
                delivered = True
            except Exception as exc:
                logger.warning(
                    "LivingMemory 最终回复写入失败: session=%s handler=%s error=%s",
                    _single_line(umo, 140),
                    _single_line(getattr(handler, "handler_name", ""), 80),
                    _single_line(exc, 160),
                )
        if delivered:
            recorded[dedup_key] = _now_ts()
            if len(recorded) > 512:
                for old_key, _ in sorted(
                    recorded.items(), key=lambda item: item[1]
                )[:-384]:
                    recorded.pop(old_key, None)
            logger.info(
                "已将实际发送回复交给 LivingMemory 记录: %s",
                _single_line(umo, 140),
            )
        return delivered

    async def _memory_companion_record_confirmed_assistant_message(
        self,
        event: Any,
        *,
        content: str,
        delivery_id: str = "",
    ) -> bool:
        response_text = sanitize_llm_segment_control_tokens(content)[:2000]
        session_id = _single_line(getattr(event, "unified_msg_origin", ""), 200)
        if not response_text or not session_id:
            return False
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_visible_turn", None) if bridge else None
        if not callable(recorder):
            return False
        try:
            private_chat = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            private_chat = False
        try:
            user_id = _single_line(event.get_sender_id(), 80)
        except Exception:
            user_id = ""
        try:
            user_name = _single_line(self._sender_display_name(event), 80)
        except Exception:
            user_name = user_id
        try:
            await recorder(
                role="assistant",
                content=response_text,
                scope="private" if private_chat else "group",
                session_id=session_id,
                platform=session_id.split(":", 1)[0] if ":" in session_id else "",
                user_id=user_id,
                user_name=user_name,
                message_id=(
                    "private_companion_delivered_"
                    f"{_single_line(delivery_id, 120) or uuid.uuid4().hex}"
                ),
                source="private_companion_confirmed_reply",
                metadata={
                    "clean_visible_text": response_text,
                    "delivery_confirmed": True,
                    "conversation_turn": "passive_reply",
                },
            )
            return True
        except Exception as exc:
            optional_failed = getattr(
                self, "_memory_companion_optional_dependency_failed", None
            )
            if callable(optional_failed) and optional_failed(
                exc, where="record_confirmed_assistant_message"
            ):
                return False
            logger.debug(
                "MemoryCompanion 实际回复写入失败: %s",
                _single_line(exc, 120),
            )
            return False

    def _confirmed_delivery_cache_key(
        self,
        event: Any,
        delivery_id: str,
    ) -> str:
        persona_getter = getattr(self, "_active_persona_scope", None)
        try:
            persona_id = str(persona_getter() if callable(persona_getter) else "")
        except Exception:
            persona_id = ""
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        return "\0".join((persona_id, umo, str(delivery_id or "")))

    def _claim_confirmed_delivery_locked(
        self,
        event: Any,
        delivery_id: str,
    ) -> bool:
        """Claim one delivery id while the caller holds the data lock."""
        cache = getattr(self, "_private_companion_final_delivery_ids", None)
        if not isinstance(cache, dict):
            cache = {}
            self._private_companion_final_delivery_ids = cache
        key = self._confirmed_delivery_cache_key(event, delivery_id)
        if key in cache:
            return False
        cache[key] = _now_ts()
        if len(cache) > 512:
            for stale_key, _ in sorted(cache.items(), key=lambda item: item[1])[:-384]:
                cache.pop(stale_key, None)
        return True

    def _release_confirmed_delivery_claim(
        self,
        event: Any,
        delivery_id: str,
    ) -> None:
        cache = getattr(self, "_private_companion_final_delivery_ids", None)
        if isinstance(cache, dict):
            cache.pop(self._confirmed_delivery_cache_key(event, delivery_id), None)

    def _record_confirmed_private_bot_state_locked(
        self,
        event: Any,
        *,
        response_text: str,
        now: float,
    ) -> set[str]:
        visible_text = _single_line(
            _strip_internal_message_blocks(
                response_text,
                enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)),
                tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
            ),
            500,
        )
        if not visible_text:
            return set()
        recorder = getattr(self, "_record_confirmed_bot_continuity", None)
        try:
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return set()
            resolver = getattr(self, "_private_user_id_for_event", None)
            user_id = (
                resolver(event)
                if callable(resolver)
                else self._canonical_private_user_id(str(event.get_sender_id()))
            )
        except Exception:
            return set()
        users = (
            self.data.get("users", {})
            if isinstance(getattr(self, "data", None), dict)
            else {}
        )
        user = users.get(user_id) if isinstance(users, dict) else None
        if not isinstance(user, dict):
            return set()

        user["last_companion_message"] = visible_text
        user["last_companion_message_at"] = now
        screen_scheduler = getattr(self, "_maybe_schedule_goodnight_screen_check", None)
        if callable(screen_scheduler):
            screen_scheduler(user, visible_text, now=now)

        updated_sections = {"users"}
        expression_rule_details = getattr(
            event,
            "private_companion_expression_rule_details",
            None,
        )
        semantic_rules = getattr(
            event,
            "private_companion_semantic_expression_rules",
            None,
        )
        expression_context = getattr(
            event,
            "private_companion_semantic_expression_context",
            None,
        )
        usage_recorder = getattr(self, "_record_expression_rule_injection", None)
        if callable(usage_recorder) and (
            isinstance(expression_rule_details, dict)
            or (isinstance(semantic_rules, list) and semantic_rules)
        ):
            usage = usage_recorder(
                user,
                expression_rule_details
                if isinstance(expression_rule_details, dict)
                else {},
                visible_text,
                semantic_rules=semantic_rules if isinstance(semantic_rules, list) else [],
                context=expression_context
                if isinstance(expression_context, dict)
                else {"channel": "private"},
            )
            if isinstance(usage, dict):
                updated_sections.update(usage.get("updated_sections") or ())

        topic_recorder = getattr(self, "_remember_passive_reply_topic", None)
        if callable(topic_recorder):
            topic_recorder(
                user,
                visible_text,
                _single_line(user.get("last_user_message"), 260),
            )
        if callable(recorder):
            recorder(user, visible_text, now=now)
        reunion_observed_at = _safe_float(
            getattr(event, "_private_companion_reunion_observed_at", 0),
            0,
        )
        if reunion_observed_at > _safe_float(user.get("last_reunion_ack_at"), 0):
            user["last_reunion_ack_at"] = reunion_observed_at
        return updated_sections

    def _record_confirmed_group_bot_state_locked(
        self,
        event: Any,
        *,
        response_text: str,
        now: float,
        delivery_id: str = "",
        llm_segments: tuple[str, ...] = (),
    ) -> set[str]:
        visible_text = _single_line(
            _strip_internal_message_blocks(
                sanitize_llm_segment_control_tokens(response_text),
                enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)),
                tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
            ),
            500,
        )
        if not visible_text:
            return set()
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return set()
        except Exception:
            pass
        group_id_getter = getattr(self, "_extract_group_id_from_event", None)
        group_id = _single_line(
            group_id_getter(event) if callable(group_id_getter) else "",
            80,
        )
        if not group_id:
            return set()
        feature_checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        if callable(feature_checker) and not feature_checker("enable_group_companion"):
            return set()
        group_getter = getattr(self, "_get_group", None)
        if not callable(group_getter):
            return set()
        group = group_getter(group_id)
        if not isinstance(group, dict):
            return set()

        updated_sections: set[str] = set()
        semantic_rules = getattr(
            event,
            "private_companion_semantic_expression_rules",
            None,
        )
        expression_context = getattr(
            event,
            "private_companion_semantic_expression_context",
            None,
        )
        usage_recorder = getattr(self, "_record_expression_rule_injection", None)
        if (
            callable(usage_recorder)
            and isinstance(semantic_rules, list)
            and semantic_rules
        ):
            usage = usage_recorder(
                group,
                {},
                visible_text,
                semantic_rules=semantic_rules,
                context=expression_context
                if isinstance(expression_context, dict)
                else {"channel": "group"},
            )
            if isinstance(usage, dict) and usage:
                updated_sections.update(usage.get("updated_sections") or ("groups",))
                try:
                    setattr(
                        event,
                        "private_companion_group_semantic_usage_recorded",
                        True,
                    )
                except Exception:
                    pass

        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        scene = getattr(event, "private_companion_group_scene", None)
        talking_to_bot = (
            isinstance(scene, dict) and str(scene.get("talking_to") or "") == "bot"
        )
        reply_recorder = getattr(self, "_record_group_bot_reply", None)
        if callable(reply_recorder):
            recorded = reply_recorder(
                group,
                text=visible_text,
                reply_to_id=sender_id,
                kind="passive_reply",
                talking_to_bot=talking_to_bot,
                ts=now,
                delivery_id=delivery_id,
                llm_segments=llm_segments,
            )
            if isinstance(recorded, dict):
                updated_sections.add("groups")
        active_getter = getattr(self, "_group_active_conversation", None)
        active = active_getter(group) if callable(active_getter) else {}
        if talking_to_bot or (
            isinstance(active, dict)
            and str(active.get("sender_id") or "") == str(sender_id or "")
        ):
            active["last_bot_reply"] = visible_text
            active["last_bot_reply_ts"] = now
            if talking_to_bot:
                refresher = getattr(
                    self,
                    "_refresh_group_bot_conversation_after_reply",
                    None,
                )
                if callable(refresher):
                    refresher(group, sender_id, now=now)
            updated_sections.add("groups")
        return updated_sections

    async def _record_confirmed_outbound_state(
        self,
        event: Any,
        *,
        response_text: str,
        delivery_id: str,
        llm_segments: tuple[str, ...] = (),
    ) -> tuple[bool, set[str]]:
        """Commit all local continuity for one confirmed delivery exactly once."""
        if not response_text:
            return False, set()

        def record() -> tuple[bool, set[str]]:
            if not self._claim_confirmed_delivery_locked(event, delivery_id):
                return True, set()
            try:
                now = _now_ts()
                private_sections = self._record_confirmed_private_bot_state_locked(
                    event,
                    response_text=response_text,
                    now=now,
                )
                group_sections = self._record_confirmed_group_bot_state_locked(
                    event,
                    response_text=response_text,
                    now=now,
                    delivery_id=delivery_id,
                    llm_segments=llm_segments,
                )
                sections = private_sections | group_sections
                if sections:
                    self._save_data_sync(sections=sections)
                return False, sections
            except Exception:
                self._release_confirmed_delivery_claim(event, delivery_id)
                raise

        lock = getattr(self, "_data_lock", None)
        if lock is not None and hasattr(lock, "__aenter__"):
            async with lock:
                return record()
        return record()
