# -*- coding: utf-8 -*-
"""PrivateCompanionPluginReq036UnifiedPersonPart02Mixin。

由 tools/split_mixin_domain.py 从 main_req036_unified_person.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 458 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginReq036UnifiedPersonMixin）。
"""
from __future__ import annotations

from .main_req036_unified_person_shared import logger
from .main_req036_unified_person_shared import Any
from .main_req036_unified_person_shared import DEFAULT_UNAUTHORIZED_PRIVATE_REPLY
from .main_req036_unified_person_shared import NamespaceContext
from .main_req036_unified_person_shared import _safe_float
from .main_req036_unified_person_shared import _safe_int
from .main_req036_unified_person_shared import _single_line
from .main_req036_unified_person_shared import apply_legacy_relationship_delta
from .main_req036_unified_person_shared import asyncio
from .main_req036_unified_person_shared import build_identity_key
from .main_req036_unified_person_shared import math
from .main_req036_unified_person_shared import re
from .main_req036_unified_person_shared import req036_build_person_ref
from .main_req036_unified_person_shared import req036_build_portrait_request
from .main_req036_unified_person_shared import time



class PrivateCompanionPluginReq036UnifiedPersonPart02Mixin:
    """PrivateCompanionPluginReq036UnifiedPersonPart02Mixin（从 PrivateCompanionPluginReq036UnifiedPersonMixin 拆出）。"""


    async def _req036_reject_unauthorized_private_event(self, event: Any, gate: dict[str, Any]) -> None:
        """Reply before any LLM, bridge, tool, portrait, or relationship path."""
        inbound_checker = getattr(self, "_event_is_inbound_chat_message", None)
        if callable(inbound_checker) and not inbound_checker(event):
            return
        if bool(getattr(event, "private_companion_req036_denied", False)):
            try:
                event.stop_event()
            except Exception:
                pass
            return
        try:
            setattr(event, "private_companion_req036_denied", True)
            setattr(event, "private_companion_req036_denial_code", str(gate.get("code") or "private_companion_disabled"))
        except Exception:
            pass
        try:
            event.stop_event()
        except Exception:
            pass

        # Adapter redelivery can reconstruct the same genuine message as a
        # different event object.  Deduplicate only by its stable platform
        # message identity; this is not a time-based user rate limit.
        message_id = ""
        message_id_getter = getattr(self, "_event_message_id", None)
        if callable(message_id_getter):
            try:
                message_id = _single_line(message_id_getter(event), 120)
            except Exception:
                message_id = ""
        denial_cache_key = ""
        denial_cache: dict[str, float] | None = None
        denial_cache_stamp = time.monotonic()
        if message_id:
            try:
                sender_id = _single_line(event.get_sender_id(), 120)
            except Exception:
                sender_id = ""
            platform_getter = getattr(self, "_platform_kind_for_event", None)
            try:
                platform = _single_line(platform_getter(event), 80) if callable(platform_getter) else ""
            except Exception:
                platform = ""
            scope_getter = getattr(self, "_event_req036_scope", None)
            try:
                denial_scope = _single_line(scope_getter(event), 480) if callable(scope_getter) else ""
            except Exception:
                denial_scope = ""
            denial_scope = denial_scope or f"{platform or 'unknown'}:{sender_id or 'unknown'}"
            denial_cache_key = f"{denial_scope}:{message_id}"
            denial_cache = getattr(self, "_req036_recent_denial_message_ids", None)
            if not isinstance(denial_cache, dict):
                denial_cache = {}
                self._req036_recent_denial_message_ids = denial_cache
            for cache_key, cached_at in list(denial_cache.items()):
                age = denial_cache_stamp - _safe_float(cached_at, 0.0)
                if age < 0 or age > 180.0:
                    denial_cache.pop(cache_key, None)
            if denial_cache_key in denial_cache:
                logger.debug(
                    "已忽略重复的未授权私聊拒绝: sender=%s message_id=%s",
                    sender_id or "-",
                    message_id,
                )
                return
            denial_cache[denial_cache_key] = denial_cache_stamp
        reply_text = str(gate.get("reply") or DEFAULT_UNAUTHORIZED_PRIVATE_REPLY)
        echo_entry = None
        echo_remember = getattr(self, "_remember_req036_denial_echo", None)
        if callable(echo_remember):
            try:
                echo_entry = echo_remember(event, reply_text)
            except Exception:
                echo_entry = None

        def release_reply_reservations() -> None:
            if denial_cache is not None and denial_cache_key and denial_cache.get(denial_cache_key) == denial_cache_stamp:
                denial_cache.pop(denial_cache_key, None)
            echo_forget = getattr(self, "_forget_req036_denial_echo", None)
            if callable(echo_forget) and echo_entry is not None:
                try:
                    echo_forget(echo_entry)
                except Exception:
                    pass

        try:
            reply_result = await self._reply(event, reply_text)
        except Exception:
            release_reply_reservations()
            raise
        if reply_result is False:
            release_reply_reservations()
            logger.debug("未授权私聊拒绝未发送，已释放回流与消息去重占位")
            return
        echo_confirm = getattr(self, "_confirm_req036_denial_echo", None)
        if callable(echo_confirm) and echo_entry is not None:
            try:
                echo_confirm(echo_entry)
            except Exception:
                pass

    @staticmethod
    def _req036_group_portrait_query_kind(text: Any) -> str:
        value = _single_line(text, 240)
        # A preference phrase followed by advice or a conclusion is ordinary
        # group chatter, not a request to summarize anyone's profile.
        ordinary_statement_patterns = (
            r"(?:^|[\s，,：:@])(?:自己|按自己|个人|各自)\s*(?:喜欢|爱)(?:吃|喝|玩|看|听)?什么\s*(?:就|便|吧|呀|喵|都|随便)",
            r"(?:喜欢|爱)(?:吃|喝|玩|看|听)?什么\s*(?:就|便|吧|呀|喵|都|随便)",
            # Questions about choosing/feeding an item are ordinary chatter,
            # not requests to summarize a person's preference profile.
            r"(?:要|该|应该|可以|能|想|准备)?\s*(?:喂|选|挑|买|点|吃|喝|做|换).{0,8}(?:什么|啥|哪种|哪个)口味",
            # Negative interest statements ("现在干啥都提不起兴趣" etc.) are
            # ordinary venting chatter. Without this the stray 啥 in 干啥
            # before 兴趣 false-positives as a third-party portrait probe.
            r"(?:提不起|不感|没(?:有|啥|什么)?|毫无|失去|缺(?:乏)?)(?:任何的?\s*)?兴趣",
        )
        if any(re.search(pattern, value) for pattern in ordinary_statement_patterns):
            return ""
        probe_patterns = (
            r"喜欢(?:吃|喝|玩|看|听)?什么",
            r"爱(?:吃|喝|玩|看|听)什么",
            r"(?:爱好|兴趣|偏好|习惯|口味|画像)(?:是|有|包括)?(?:什么|啥|哪些|怎么样)",
            r"(?:什么|啥|哪些|有啥|有哪些).{0,8}(?:爱好|兴趣|偏好|习惯|口味)",
            r"(?:说说|看看|查查|总结|整理).{0,12}(?:爱好|兴趣|偏好|习惯|口味|画像)",
        )
        if not any(re.search(pattern, value) for pattern in probe_patterns):
            return ""
        self_subject = r"(?:我自己|我的|我|本人自己|本人的|本人|俺自己|俺的|俺|咱自己|咱的|咱)"
        self_predicate = (
            r"(?:平时|一般|通常|到底|最)?(?:"
            r"喜欢(?:吃|喝|玩|看|听)?什么|爱(?:吃|喝|玩|看|听)什么|"
            r"(?:有|有什么|有啥|有哪些).{0,8}(?:爱好|兴趣|偏好|习惯)|"
            r"(?:的)?(?:爱好|兴趣|偏好|习惯|口味|画像)(?:是|有|包括)?(?:什么|啥|哪些|怎么样)"
            r")"
        )
        direct_self_query = rf"{self_subject}\s*{self_predicate}"
        reflective_self_query = (
            rf"(?:^|[\s，,：:@])我\s*(?:想知道|想问|想看看|想了解)\s*"
            rf"(?:一下)?\s*(?:自己|我自己|我的)\s*{self_predicate}"
        )
        if re.search(direct_self_query, value) or re.search(reflective_self_query, value):
            return "self"
        bot_subject = r"(?:你自己|你的|你)"
        direct_bot_query = rf"{bot_subject}\s*{self_predicate}"
        reflective_bot_query = (
            rf"(?:^|[\s，,：:@])我\s*(?:想知道|想问|想看看|想了解)\s*"
            rf"(?:一下)?\s*(?:你自己|你的|你)\s*{self_predicate}"
        )
        if re.search(direct_bot_query, value) or re.search(reflective_bot_query, value):
            return "bot_self"
        # An omitted subject is ambiguous in natural group speech. Let the
        # normal reply chain decide whether the user means the Bot, instead of
        # treating a prompt such as "喜欢什么发型" as a third-party probe.
        subjectless_query = (
            r"^(?:@[^\s]+\s*)?(?:(?:你觉得|你认为|请问|我想(?:知道|问|看看|了解))(?:一下)?\s*)?"
            r"(?:喜欢|爱)(?:吃|喝|玩|看|听)?什么"
            r"|^(?:@[^\s]+\s*)?(?:(?:你觉得|你认为|请问|我想(?:知道|问|看看|了解))(?:一下)?\s*)?"
            r"(?:什么|啥|哪些).{0,8}(?:爱好|兴趣|偏好|习惯|口味|画像)"
        )
        if re.search(subjectless_query, value):
            return ""
        return "third_party"

    def _req036_group_portrait_query_is_directed(self, event: Any) -> bool:
        """Use adapter addressing metadata so ordinary group chatter never triggers this guard."""
        # ``is_wake`` only means that some handler accepted the event; it does
        # not prove that the user addressed this Bot. Keep the more specific
        # command flag and structured At/Reply evidence below.
        if bool(getattr(event, "is_at_or_wake_command", False)):
            return True
        try:
            signals = self._event_scene_signals(event)
        except Exception:
            signals = {}
        if not isinstance(signals, dict):
            return False
        if any(
            isinstance(item, dict) and bool(item.get("is_bot"))
            for item in (signals.get("at_targets") or [])
        ):
            return True
        self_id = _single_line(signals.get("self_id"), 80)
        return bool(self_id and _single_line(signals.get("reply_to_id"), 80) == self_id)

    async def _req036_read_group_self_portrait(self, event: Any) -> str:
        dto = getattr(event, "private_companion_unified_profile_context", None)
        if not isinstance(dto, dict):
            return "这部分画像暂时不可用。"
        capabilities = dto.get("capability_summary")
        if not isinstance(capabilities, dict) or capabilities.get("portrait_usage_enabled") is not True:
            return "智能画像当前未开启。"
        person_ref = dto.get("person_ref") if isinstance(dto.get("person_ref"), dict) else {}
        person_id = _single_line(person_ref.get("person_id"), 80)
        overlays = dto.get("context_overlays") if isinstance(dto.get("context_overlays"), dict) else {}
        scope = _single_line(overlays.get("group_scope"), 80)
        if not scope.startswith("group:"):
            return "这部分画像暂时不可用。"
        request = req036_build_portrait_request(
            person_ref=person_ref,
            requester_person_id=person_id,
            target_person_id=person_id,
            scope=scope,
            purpose="summarize_to_subject",
        )
        namespace_context = getattr(event, "private_companion_namespace_context", None)
        if isinstance(namespace_context, dict):
            request["namespace_context"] = dict(namespace_context)
        bridge = self._memory_companion_bridge()
        reader = getattr(bridge, "read_unified_profile_portrait", None) if bridge is not None else None
        if not callable(reader):
            return "这部分画像暂时不可用。"
        try:
            result = reader(request, limit=5)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                result = await result
        except Exception:
            return "这部分画像暂时不可用。"
        if not isinstance(result, dict) or not result.get("ok"):
            return "这部分画像暂时不可用。"
        summaries = [
            _single_line(item.get("summary"), 80)
            for item in result.get("items", [])
            if isinstance(item, dict) and _single_line(item.get("summary"), 80)
        ]
        return "我目前只记得这些公开的低敏偏好：" + "；".join(summaries[:5]) if summaries else "我还没有整理出可公开的低敏画像。"

    async def _req036_portrait_bridge_status_for_user(self, user: Any) -> dict[str, Any]:
        """Read synchronization state only; facts stay in Memory's admin UI."""
        source = user if isinstance(user, dict) else {}
        person_id = _single_line(source.get("unified_person_id"), 80)
        if not person_id:
            return {"available": False, "code": "identity_pending", "last_synced_at": "", "portrait_revision": 0}
        bridge = self._memory_companion_bridge()
        reader = getattr(bridge, "unified_profile_portrait_status", None) if bridge is not None else None
        if not callable(reader):
            return {"available": False, "code": "bridge_unavailable", "last_synced_at": "", "portrait_revision": 0}
        try:
            result = reader(person_id)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                result = await result
        except Exception:
            return {"available": False, "code": "bridge_degraded", "last_synced_at": "", "portrait_revision": 0}
        if not isinstance(result, dict):
            return {"available": False, "code": "bridge_degraded", "last_synced_at": "", "portrait_revision": 0}
        response = {
            "available": bool(result.get("ok")),
            "code": _single_line(result.get("code"), 80) or "bridge_degraded",
            "last_synced_at": _single_line(result.get("last_synced_at"), 80),
            "portrait_revision": _safe_int(result.get("portrait_revision"), 0, 0),
        }
        projection = self.get_unified_person_projection(person_id)
        portrait_reader = getattr(bridge, "read_unified_profile_portrait", None) if bridge is not None else None
        if not response["available"] or not isinstance(projection, dict) or not callable(portrait_reader):
            return response
        try:
            request = req036_build_portrait_request(
                person_ref=req036_build_person_ref(projection),
                requester_person_id=person_id,
                target_person_id=person_id,
                scope="private",
                purpose="summarize_to_subject",
            )
            namespace_getter = getattr(self, "_req041_scoped_context_for_user", None)
            if callable(namespace_getter):
                namespace_context = namespace_getter(
                    source, kind="private", purpose="profile_read"
                )
                if isinstance(namespace_context, NamespaceContext) and not namespace_context.errors():
                    request["namespace_context"] = namespace_context.to_dict()
            portrait = portrait_reader(request, limit=3)
            if asyncio.iscoroutine(portrait) or hasattr(portrait, "__await__"):
                portrait = await portrait
            response["summaries"] = [
                _single_line(item.get("summary"), 80)
                for item in (portrait.get("items", []) if isinstance(portrait, dict) else [])
                if isinstance(item, dict) and _single_line(item.get("summary"), 80)
            ][:3]
        except Exception:
            response["summaries"] = []
        return response

    async def _req036_preferred_address_from_portrait(self, user: Any) -> str:
        """Resolve this exact private subject's latest explicit address hint."""
        source = user if isinstance(user, dict) else {}
        capabilities = self._req036_capability_summary_for_user(source)
        if capabilities.get("portrait_usage_enabled") is not True:
            return ""
        person_id = _single_line(source.get("unified_person_id"), 80)
        if not person_id:
            return ""
        projection = self.get_unified_person_projection(person_id)
        if not isinstance(projection, dict):
            return ""
        bridge = self._memory_companion_bridge()
        reader = (
            getattr(bridge, "read_unified_profile_portrait", None)
            if bridge is not None
            else None
        )
        if not callable(reader):
            return ""
        request = req036_build_portrait_request(
            person_ref=req036_build_person_ref(projection),
            requester_person_id=person_id,
            target_person_id=person_id,
            scope="private",
            purpose="summarize_to_subject",
        )
        namespace_getter = getattr(self, "_req041_scoped_context_for_user", None)
        if callable(namespace_getter):
            namespace_context = namespace_getter(
                source, kind="private", purpose="profile_read"
            )
            if (
                isinstance(namespace_context, NamespaceContext)
                and not namespace_context.errors()
            ):
                request["namespace_context"] = namespace_context.to_dict()
        result = reader(request, limit=8)
        if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
            result = await result
        if not isinstance(result, dict) or not result.get("ok"):
            return ""
        for item in result.get("items", []):
            if not isinstance(item, dict):
                continue
            if _single_line(item.get("dimension"), 80) != "preferred_address":
                continue
            summary = _single_line(item.get("summary"), 180)
            match = re.fullmatch(r"希望被称为\s+(.+)", summary)
            preferred = _single_line(match.group(1) if match else "", 24)
            if preferred:
                return preferred
        return ""

    def read_p4_effect_state(self, person_id: str) -> dict[str, Any]:
        return self._active_unified_person_registry().read_p4_effect_state(person_id)

    def read_p4_live_state(self, person_id: str) -> dict[str, Any]:
        return self._active_unified_person_registry().read_p4_live_state(person_id)

    def _p4_b_apply_legacy_relationship_delta(
        self,
        user: dict[str, Any],
        delta: int,
        *,
        reason_code: str = "",
    ) -> bool:
        del reason_code
        return apply_legacy_relationship_delta(
            user,
            delta,
            isolate=bool(getattr(self, "enable_p4_b_legacy_score_isolation", False)),
        )

    def _p4_live_state_for_event(self, event: Any) -> dict[str, Any] | None:
        try:
            if not bool(event.is_private_chat()):
                return None
        except Exception:
            return None
        resolution = self.resolve_unified_person_for_event(event)
        if resolution.get("state") != "resolved":
            return None
        person_id = _single_line(resolution.get("person_id"), 160)
        if not person_id:
            return None
        result = self.read_p4_live_state(person_id)
        if result.get("ok") is not True:
            return {"_p4_live_invalid": True}
        return result.get("state")

    def _bounded_p4_reply_temperature_signals(self, event: Any) -> dict[str, Any]:
        """Return transient, bounded advisory inputs for the P4 reply projection."""
        data = getattr(self, "data", None)
        daily_state = data.get("daily_state") if isinstance(data, dict) else None
        energy = daily_state.get("energy") if isinstance(daily_state, dict) else None
        if (
            isinstance(energy, bool)
            or not isinstance(energy, (int, float))
            or not math.isfinite(float(energy))
        ):
            energy = None
        else:
            energy = max(0, min(100, energy))
        mood = ""
        if isinstance(daily_state, dict):
            mood = _single_line(daily_state.get("mood_bias") or daily_state.get("mood"), 64)

        schedule_parts: list[str] = []
        segment_getter = getattr(self, "_current_detail_segment_for_update", None)
        if callable(segment_getter):
            try:
                segment = segment_getter()
            except Exception:
                segment = None
            if isinstance(segment, dict):
                schedule_parts.extend(
                    _single_line(segment.get(key), 80)
                    for key in ("title", "name", "summary", "activity", "location")
                    if _single_line(segment.get(key), 80)
                )
        if not schedule_parts and isinstance(data, dict):
            try:
                current_item = self._get_current_plan_item(data.get("daily_plan", {}))
            except Exception:
                current_item = None
            if isinstance(current_item, dict):
                schedule_parts.extend(
                    _single_line(current_item.get(key), 80)
                    for key in ("title", "name", "summary", "activity", "location")
                    if _single_line(current_item.get(key), 80)
                )

        return {
            "energy": energy,
            "mood": mood or None,
            "schedule": " ".join(schedule_parts)[:240] or None,
            "context": _single_line(getattr(event, "message_str", ""), 280) or None,
        }

    def record_p4_effect_event(
        self,
        person_id: str,
        event: dict[str, Any],
        *,
        operation_id: str,
        actor_id: str = "system",
    ) -> dict[str, Any]:
        result = self._active_unified_person_registry().record_p4_effect_event(
            person_id,
            event,
            operation_id=operation_id,
            actor_id=actor_id,
        )
        if result.get("ok") and result.get("changed"):
            saver = getattr(self, "_schedule_data_save", None)
            if callable(saver):
                saver(sections={"unified_person"})
        return result

    def create_unified_person_for_event(
        self,
        event: Any | None = None,
        *,
        operation_id: str = "",
        profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        identity = self._unified_person_event_identity(event)
        if not identity:
            return {"ok": False, "state": "pending", "code": "event_identity_missing", "person_id": ""}
        try:
            identity_key = build_identity_key(identity)
        except (TypeError, ValueError):
            return {"ok": False, "state": "invalid", "code": "identity_invalid", "person_id": ""}
        user_profile = dict(profile) if isinstance(profile, dict) else {}
        if event is not None and not user_profile.get("display_name"):
            name_getter = getattr(self, "_sender_display_name", None)
            if callable(name_getter):
                try:
                    user_profile["display_name"] = _single_line(name_getter(event), 80)
                except Exception:
                    pass
        return self.create_unified_person(
            identity,
            profile=user_profile,
            operation_id=operation_id or f"companion.person.create:{identity_key[-24:]}",
        )
