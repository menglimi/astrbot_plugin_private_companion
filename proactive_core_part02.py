# -*- coding: utf-8 -*-
"""ProactivePart02Mixin。

由 tools/split_mixin_domain.py 从 proactive.py 机械抽取（27 个方法 + 0 个模块级名字 + 0 个类级赋值 / 565 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMixin）。
"""
from __future__ import annotations

from .proactive_core_shared import _proactive_setting_value
from .proactive_core_shared import Any
from .proactive_core_shared import _now_ts
from .proactive_core_shared import _safe_float
from .proactive_core_shared import _safe_int
from .proactive_core_shared import _single_line
from .proactive_core_shared import _today_key
from .proactive_core_shared import re
from .proactive_core_shared import req036_capability_summary



class ProactivePart02Mixin:
    """ProactivePart02Mixin（从 ProactiveMixin 拆出）。"""


    def _cycle_proactive_frequency_profile(self) -> dict[str, Any]:
        neutral = {
            "phase": "neutral",
            "private_interval_multiplier": 1.0,
            "group_interval_multiplier": 1.0,
            "group_probability_multiplier": 1.0,
        }
        if not bool(_proactive_setting_value(self, "enable_cycle_state", True)):
            return neutral
        data = getattr(self, "data", {})
        state = data.get("daily_state") if isinstance(data, dict) else {}
        if not isinstance(state, dict) or str(state.get("date") or "") not in {"", _today_key()}:
            return neutral
        cycle_text = _single_line(state.get("body_cycle"), 100)
        if not cycle_text or cycle_text in {"无明显周期影响", "不处于生理期", "生理期模拟未开启"}:
            return neutral
        phase = ""
        conditions = state.get("conditions")
        if isinstance(conditions, list):
            phase = next(
                (
                    str(item.get("phase") or "")
                    for item in conditions
                    if isinstance(item, dict) and str(item.get("kind") or "") == "body_cycle"
                ),
                "",
            )
        upper_cycle_text = cycle_text.upper()
        if not phase:
            if "PMS" in upper_cycle_text or "经前综合征" in cycle_text:
                phase = "pms"
            elif "排卵前期" in cycle_text:
                phase = "pre_ovulation"
            elif "月经期" in cycle_text:
                phase = "menstrual"
            elif "卵泡期" in cycle_text:
                phase = "follicular"
            elif "排卵期" in cycle_text:
                phase = "ovulation"
            elif "黄体期" in cycle_text:
                phase = "luteal"
            elif "后" in cycle_text or "恢复" in cycle_text:
                phase = "recovery"
            elif "前" in cycle_text:
                phase = "pre"
            elif "生理期" in cycle_text:
                phase = "period"
        if phase == "recovery":
            return {
                "phase": "recovery",
                "private_interval_multiplier": 1.05,
                "group_interval_multiplier": 1.08,
                "group_probability_multiplier": 0.92,
            }
        if phase in {"pre", "pms"}:
            return {
                "phase": phase,
                "private_interval_multiplier": 1.08,
                "group_interval_multiplier": 1.12,
                "group_probability_multiplier": 0.88,
            }
        if phase in {"period", "menstrual"}:
            return {
                "phase": phase,
                "private_interval_multiplier": 1.18,
                "group_interval_multiplier": 1.25,
                "group_probability_multiplier": 0.76,
            }
        if phase in {"follicular", "pre_ovulation", "ovulation", "luteal"}:
            return {**neutral, "phase": phase}
        return neutral

    def _cycle_group_interject_probability(self, probability: float) -> float:
        profile = self._cycle_proactive_frequency_profile()
        return max(0.0, min(1.0, float(probability) * profile["group_probability_multiplier"]))

    def _effective_group_interject_max_daily(self) -> int:
        if bool(self._proactive_intensity_effect("ignore_group_interject_daily_limit", False)):
            return self._PROACTIVE_DAILY_LIMIT_UNLIMITED
        return self._effective_proactive_int(
            "group_interject_max_daily",
            _safe_int(_proactive_setting_value(self, "group_interject_max_daily", 2), 2, 0, 12),
            minimum=0,
            maximum=48,
        )

    def _effective_group_wakeup_interest_probability(self) -> float:
        return self._effective_proactive_float(
            "group_wakeup_interest_probability",
            max(0.0, min(1.0, _safe_float(_proactive_setting_value(self, "group_wakeup_interest_probability", 0.18), 0.18, 0.0))),
            minimum=0.0,
            maximum=1.0,
        )

    def _effective_group_wakeup_question_threshold(self) -> int:
        return self._effective_proactive_int(
            "group_wakeup_question_threshold",
            _safe_int(_proactive_setting_value(self, "group_wakeup_question_threshold", 65), 65, 0, 100),
            minimum=0,
            maximum=100,
        )

    def _effective_group_wakeup_cold_group_threshold(self) -> int:
        return self._effective_proactive_int(
            "group_wakeup_cold_group_threshold",
            _safe_int(_proactive_setting_value(self, "group_wakeup_cold_group_threshold", 65), 65, 0, 100),
            minimum=0,
            maximum=100,
        )

    def _effective_group_wakeup_topic_interest_max_boost(self) -> float:
        return self._effective_proactive_float(
            "group_wakeup_topic_interest_max_boost",
            max(0.0, min(1.5, _safe_float(_proactive_setting_value(self, "group_wakeup_topic_interest_max_boost", 0.45), 0.45, 0.0))),
            minimum=0.0,
            maximum=1.5,
        )

    def _effective_proactive_persona_judge_send_threshold(self) -> int:
        return self._effective_proactive_int(
            "proactive_persona_judge_send_threshold",
            _safe_int(_proactive_setting_value(self, "proactive_persona_judge_send_threshold", 62), 62, 0, 100),
            minimum=0,
            maximum=100,
        )

    def _effective_proactive_review_strength(self) -> str:
        value = str(self._proactive_intensity_effect("proactive_review_strength", "") or "").strip().lower()
        if value in {"lenient", "balanced", "strict"}:
            return value
        configured = str(_proactive_setting_value(self, "proactive_review_strength", "lenient") or "lenient").strip().lower()
        return configured if configured in {"lenient", "balanced", "strict"} else "lenient"

    def _proactive_intensity_ignores_token_soft_limit(self, task: str | None = None) -> bool:
        return bool(self._proactive_intensity_effect("ignore_token_soft_limit", False))

    def _configured_target_ids(self) -> list[str]:
        raw = self.target_user_ids
        if isinstance(raw, str):
            parts = re.split(r"[,\s,、;；]+", raw)
        elif isinstance(raw, list):
            parts = raw
        else:
            parts = []
        ids = []
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        for part in parts:
            user_id = normalizer(part) if callable(normalizer) else _single_line(part, 128)
            if user_id and not self._is_bot_self_user_id(user_id) and user_id not in ids:
                ids.append(user_id)
        return ids

    def _proactive_identity_binding_is_verified(
        self,
        user_id: str,
        user: dict[str, Any] | None,
    ) -> bool:
        """Require a concrete platform/account binding before active delivery."""
        if not isinstance(user, dict):
            return False
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        canonicalizer = getattr(self, "_canonical_private_user_id", None)

        def normalize(value: Any) -> str:
            result = _single_line(value, 160)
            if callable(normalizer):
                try:
                    result = _single_line(normalizer(result), 160) or result
                except Exception:
                    return ""
            return result

        def canonical(value: Any) -> str:
            result = normalize(value)
            if callable(canonicalizer):
                try:
                    result = _single_line(canonicalizer(result), 160)
                except Exception:
                    return ""
            return result

        subject = normalize(user.get("identity_subject_id"))
        platform = _single_line(user.get("identity_platform_kind"), 40).lower()
        adapter = _single_line(user.get("identity_adapter_instance_id"), 120)
        bot_id = _single_line(user.get("identity_bot_id"), 120)
        if not subject or not platform or platform == "generic" or not (adapter or bot_id):
            return False

        # Plain storage keys must agree with the stamped subject. Scoped
        # transport keys (platform:subject:digest) keep the subject as source
        # of truth and are intentionally allowed here.
        storage_id = normalize(user.get("user_id") or user_id)
        if storage_id and ":" not in storage_id and canonical(storage_id) != canonical(subject):
            return False
        return True

    def _proactive_identity_binding_is_unique(
        self,
        user_id: str,
        user: dict[str, Any],
    ) -> bool:
        """Reject duplicate active records for one exact platform identity."""
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return True

        def binding(candidate: dict[str, Any]) -> tuple[str, str, str, str]:
            return (
                _single_line(candidate.get("identity_platform_kind"), 40).lower(),
                _single_line(candidate.get("identity_subject_id"), 128),
                _single_line(candidate.get("identity_adapter_instance_id"), 120),
                _single_line(candidate.get("identity_bot_id"), 120),
            )

        current_binding = binding(user)
        if not current_binding[0] or not current_binding[1] or not (current_binding[2] or current_binding[3]):
            return False
        current_key = _single_line(user_id, 160)
        for stored_id, candidate in users.items():
            if not isinstance(candidate, dict) or candidate is user or _single_line(stored_id, 160) == current_key:
                continue
            if not self._proactive_identity_binding_is_verified(str(stored_id), candidate):
                continue
            capabilities = candidate.get("unified_profile_capabilities")
            active = bool(
                candidate.get("manual_enabled")
                or candidate.get("auto_enabled")
                or candidate.get("proactive_private_enabled") is True
                or (isinstance(capabilities, dict) and capabilities.get("proactive_private_enabled") is True)
            )
            if active and binding(candidate) == current_binding:
                return False
        return True

    def _user_enabled_for_proactive(self, user_id: str, user: dict[str, Any] | None = None) -> bool:
        if not isinstance(user, dict):
            return False
        if not self._proactive_identity_binding_is_verified(user_id, user):
            return False
        if not self._proactive_identity_binding_is_unique(user_id, user):
            return False
        req036_gate = getattr(self, "_req036_proactive_private_allowed", None)
        if callable(req036_gate):
            try:
                if not bool(req036_gate(user)):
                    return False
            except Exception:
                return False
        elif not req036_capability_summary(user).get("proactive_private_enabled"):
            return False
        return bool(user_id and not self._is_bot_self_user_id(user_id))

    def _default_private_umo_for_user_id(self, user_id: str) -> str:
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        user_id = normalizer(user_id) if callable(normalizer) else _single_line(user_id, 128)
        if not user_id or self._is_bot_self_user_id(user_id):
            return ""
        platform = ""
        instance_resolver = getattr(self, "_preferred_platform_instance_id", None)
        if callable(instance_resolver):
            platform = _single_line(instance_resolver(), 80)
        if not platform:
            platform = _single_line(getattr(self, "target_platform", ""), 80) or "aiocqhttp"
        return f"{platform}:FriendMessage:{user_id}"

    def _private_delivery_user_id_for(self, user_id: str) -> str:
        canonical = self._canonical_private_user_id(str(user_id or "").strip())
        aliases = getattr(self, "private_user_delivery_aliases", {}) or {}
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        target = normalizer(aliases.get(canonical)) if callable(normalizer) else _single_line(aliases.get(canonical), 128)
        if target and not self._is_bot_self_user_id(target):
            return target
        return canonical

    def _private_delivery_alias_target(self, user_id: str) -> str:
        canonical = self._canonical_private_user_id(str(user_id or "").strip())
        aliases = getattr(self, "private_user_delivery_aliases", {}) or {}
        return _single_line(aliases.get(canonical), 240)

    def _private_umo_session_id(self, umo: str) -> str:
        clean_umo = _single_line(umo, 240)
        if not clean_umo or ":FriendMessage:" not in clean_umo:
            return ""
        parser = getattr(self, "_parse_message_session", None)
        if callable(parser):
            try:
                session = parser(clean_umo)
            except Exception:
                session = None
            if session is not None:
                return _single_line(getattr(session, "session_id", ""), 128)
        return _single_line(clean_umo.rsplit(":FriendMessage:", 1)[-1], 128)

    @staticmethod
    def _private_delivery_route_store(user: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw = user.setdefault("private_delivery_routes", {})
        if not isinstance(raw, dict):
            raw = {}
            user["private_delivery_routes"] = raw
        return raw

    def _remember_private_delivery_route(
        self,
        user: dict[str, Any] | None,
        umo: str,
        *,
        outcome: str,
        error: str = "",
    ) -> None:
        if not isinstance(user, dict):
            return
        clean_umo = _single_line(umo, 240)
        session_id = self._private_umo_session_id(clean_umo)
        if not clean_umo or not session_id:
            return
        routes = self._private_delivery_route_store(user)
        item = routes.get(clean_umo)
        if not isinstance(item, dict):
            item = {}
            routes[clean_umo] = item
        now = _now_ts()
        item["umo"] = clean_umo
        item["session_id"] = session_id
        kind_getter = getattr(self, "_platform_kind_for_umo", None)
        item["platform_kind"] = _single_line(kind_getter(clean_umo) if callable(kind_getter) else "", 40)
        if outcome == "success":
            item["last_success_at"] = now
            item["failure_count"] = 0
            item["last_error"] = ""
            user["preferred_delivery_umo"] = clean_umo
        elif outcome == "failure":
            item["last_failure_at"] = now
            item["failure_count"] = _safe_int(item.get("failure_count"), 0, 0) + 1
            item["last_error"] = _single_line(error, 240)
        else:
            item["last_seen_at"] = now
            if _safe_float(item.get("last_failure_at"), 0) <= _safe_float(item.get("last_seen_at"), 0):
                item["failure_count"] = 0
        if len(routes) > 12:
            ordered = sorted(
                routes.items(),
                key=lambda pair: max(
                    _safe_float(pair[1].get("last_success_at"), 0) if isinstance(pair[1], dict) else 0,
                    _safe_float(pair[1].get("last_seen_at"), 0) if isinstance(pair[1], dict) else 0,
                    _safe_float(pair[1].get("last_failure_at"), 0) if isinstance(pair[1], dict) else 0,
                ),
                reverse=True,
            )
            routes.clear()
            routes.update(ordered[:12])

    def _private_delivery_umo_is_verified(self, user_id: str, user: dict[str, Any], umo: str) -> bool:
        clean_umo = _single_line(umo, 240)
        if not clean_umo:
            return False
        explicit = self._private_delivery_alias_target(user_id)
        if explicit and ":FriendMessage:" in explicit and explicit == clean_umo:
            return True
        if clean_umo in {
            _single_line(user.get("bound_delivery_umo"), 240),
            _single_line(user.get("last_inbound_umo"), 240),
            _single_line(user.get("preferred_delivery_umo"), 240),
            _single_line(user.get("last_proactive_delivery_umo"), 240),
        }:
            return True
        routes = user.get("private_delivery_routes")
        item = routes.get(clean_umo) if isinstance(routes, dict) else None
        return bool(
            isinstance(item, dict)
            and (
                _safe_float(item.get("last_success_at"), 0) > 0
                or _safe_float(item.get("last_seen_at"), 0) > 0
            )
        )

    def _private_delivery_umo_candidates(self, user_id: str) -> list[str]:
        canonical = self._canonical_private_user_id(str(user_id or "").strip())
        delivery_id = self._private_delivery_user_id_for(canonical)
        if not delivery_id:
            return []
        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
        user = users.get(canonical) if isinstance(users, dict) and isinstance(users.get(canonical), dict) else {}
        candidates: list[str] = []

        def add(value: Any, *, trusted: bool = False) -> None:
            umo = _single_line(value, 240)
            if not umo or umo in candidates:
                return
            if not self._private_umo_matches_user_id(umo, delivery_id) and not (
                trusted and self._private_umo_session_id(umo)
            ):
                return
            platform_available = self._private_delivery_umo_platform_available(umo)
            if platform_available is False:
                return
            if umo and umo not in candidates:
                candidates.append(umo)

        if isinstance(user, dict):
            add(user.get("bound_delivery_umo"), trusted=True)

        explicit = self._private_delivery_alias_target(canonical)
        if ":FriendMessage:" in explicit:
            add(explicit)

        routes = user.get("private_delivery_routes") if isinstance(user, dict) else {}
        ranked_routes: list[tuple[tuple[int, int, float], str]] = []
        if isinstance(routes, dict):
            for route_umo, raw in routes.items():
                if not isinstance(raw, dict) or not self._private_umo_matches_user_id(route_umo, delivery_id):
                    continue
                success_at = _safe_float(raw.get("last_success_at"), 0)
                seen_at = _safe_float(raw.get("last_seen_at"), 0)
                failure_at = _safe_float(raw.get("last_failure_at"), 0)
                failed_latest = int(
                    _safe_int(raw.get("failure_count"), 0, 0) > 0
                    and failure_at >= max(success_at, seen_at)
                )
                ranked_routes.append(((1 - failed_latest, int(success_at > 0), max(success_at, seen_at)), route_umo))
        for _, route_umo in sorted(ranked_routes, key=lambda pair: pair[0], reverse=True):
            add(route_umo)

        if isinstance(user, dict):
            add(user.get("preferred_delivery_umo"))
            add(user.get("last_inbound_umo"))
            add(user.get("last_proactive_delivery_umo"))
            add(user.get("umo"))
        add(self._default_private_umo_for_user_id(delivery_id))
        return candidates

    def _private_delivery_umo_platform_available(self, umo: str) -> bool | None:
        """Return whether a saved UMO points to a currently usable platform instance.

        ``None`` keeps compatibility with harnesses or startup states where no platform
        manager is available yet; ``False`` rejects stale instance IDs such as ``default``
        when AstrBot has a different active instance.
        """
        manager = getattr(getattr(self, "context", None), "platform_manager", None)
        if manager is None:
            return None
        try:
            platforms = list(manager.get_insts())
        except Exception:
            platforms = list(getattr(manager, "platform_insts", []) or [])
        if not platforms:
            return False
        prefix = _single_line(umo, 240).split(":", 1)[0]
        if not prefix:
            return False
        for platform in platforms:
            try:
                meta = platform.meta()
            except Exception:
                continue
            instance_id = {
                _single_line(getattr(meta, "id", ""), 80),
                _single_line(getattr(meta, "name", ""), 80),
            }
            if prefix not in instance_id:
                continue
            status = getattr(platform, "status", None)
            status_text = _single_line(
                getattr(status, "name", "") or getattr(status, "value", "") or status,
                40,
            ).lower()
            if status_text and "running" not in status_text and any(
                token in status_text for token in ("stop", "disabled", "closed", "error", "failed")
            ):
                return False
            return True
        return False

    def _private_delivery_umo_for_user_id(self, user_id: str) -> str:
        candidates = self._private_delivery_umo_candidates(user_id)
        return candidates[0] if candidates else ""

    def _private_delivery_route_status(
        self,
        user_id: str,
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a read-only explanation of the active private delivery route."""
        canonical = self._canonical_private_user_id(str(user_id or "").strip())
        if not isinstance(user, dict):
            users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
            user = users.get(canonical) if isinstance(users, dict) and isinstance(users.get(canonical), dict) else {}
        selected = self._private_delivery_umo_for_user_id(canonical)
        bound = _single_line(user.get("bound_delivery_umo"), 240)
        explicit = self._private_delivery_alias_target(canonical)
        routes = user.get("private_delivery_routes") if isinstance(user.get("private_delivery_routes"), dict) else {}
        selected_item = routes.get(selected) if isinstance(routes.get(selected), dict) else {}
        success_at = _safe_float(selected_item.get("last_success_at"), 0)
        seen_at = _safe_float(selected_item.get("last_seen_at"), 0)
        failure_at = _safe_float(selected_item.get("last_failure_at"), 0)
        failure_count = _safe_int(selected_item.get("failure_count"), 0, 0)
        selected_recovered = max(success_at, seen_at) > failure_at

        source = "fallback"
        source_label = "平台兜底"
        if bound and selected == bound:
            source = "bound"
            source_label = "用户在当前私聊绑定"
        elif explicit and ":FriendMessage:" in explicit and selected == explicit:
            source = "explicit"
            source_label = "管理员指定完整会话"
        elif success_at > 0 and (failure_count <= 0 or selected_recovered):
            source = "success"
            source_label = "最近发送成功会话"
        elif (
            selected
            and (
                selected == _single_line(user.get("last_inbound_umo"), 240)
                or seen_at > 0
            )
            and (failure_count <= 0 or selected_recovered)
        ):
            source = "inbound"
            source_label = "最近实际入站会话"
        elif selected and selected in {
            _single_line(user.get("preferred_delivery_umo"), 240),
            _single_line(user.get("last_proactive_delivery_umo"), 240),
            _single_line(user.get("umo"), 240),
        }:
            source = "stored"
            source_label = "用户已保存会话"
        elif explicit:
            source = "mapped_id"
            source_label = "管理员指定目标 ID（平台兜底）"

        recent_failure_umo = ""
        recent_failure_item: dict[str, Any] = {}
        recent_failure_at = 0.0
        verified_count = 0
        for route_umo, raw in routes.items():
            if not isinstance(raw, dict):
                continue
            if _safe_float(raw.get("last_success_at"), 0) > 0 or _safe_float(raw.get("last_seen_at"), 0) > 0:
                verified_count += 1
            route_failure_at = _safe_float(raw.get("last_failure_at"), 0)
            if route_failure_at > recent_failure_at:
                recent_failure_at = route_failure_at
                recent_failure_umo = _single_line(route_umo, 240)
                recent_failure_item = raw
        recent_recovered_at = max(
            _safe_float(recent_failure_item.get("last_success_at"), 0),
            _safe_float(recent_failure_item.get("last_seen_at"), 0),
        )
        return {
            "umo": selected,
            "source": source,
            "source_label": source_label,
            "route_count": len(routes),
            "verified_route_count": verified_count,
            "explicit_target": explicit,
            "bound_umo": bound,
            "recent_error": _single_line(recent_failure_item.get("last_error"), 240),
            "recent_error_at": recent_failure_at,
            "recent_error_umo": recent_failure_umo,
            "recent_error_recovered": bool(recent_failure_at and recent_recovered_at > recent_failure_at),
        }

    def _bind_private_delivery_umo(
        self,
        user_id: str,
        user: dict[str, Any] | None,
        umo: str,
    ) -> tuple[bool, str]:
        if not isinstance(user, dict):
            return False, "当前用户资料不可用，暂时无法绑定主动消息会话。"
        clean_umo = _single_line(umo, 240)
        if not clean_umo or ":FriendMessage:" not in clean_umo or not self._private_umo_session_id(clean_umo):
            return False, "只能在需要接收主动消息的私聊窗口执行绑定。"
        self._note_private_user_umo(user_id, user, clean_umo)
        user["bound_delivery_umo"] = clean_umo
        user["preferred_delivery_umo"] = clean_umo
        user["umo"] = clean_umo
        return True, (
            "已绑定当前私聊为主动消息接收窗口。\n"
            f"会话：{clean_umo}\n"
            "现在可以打开陪伴面板的配置引导，刷新绑定状态后继续配置。"
        )

    def _unbind_private_delivery_umo(self, user: dict[str, Any] | None) -> tuple[bool, str]:
        if not isinstance(user, dict):
            return False, "当前用户资料不可用，暂时无法解绑。"
        bound = _single_line(user.pop("bound_delivery_umo", ""), 240)
        if not bound:
            return False, "当前没有人工绑定的主动消息会话，插件会继续自动选择可用路线。"
        if _single_line(user.get("preferred_delivery_umo"), 240) == bound:
            user.pop("preferred_delivery_umo", None)
        return True, "已取消人工绑定，之后会根据最近入站和发送结果自动选择可用会话。"
