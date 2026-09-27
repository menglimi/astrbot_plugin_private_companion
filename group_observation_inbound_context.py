# -*- coding: utf-8 -*-
"""GroupObservationInboundContextMixin。

由 tools/split_mixin_domain.py 从 group_observation.py 机械抽取（23 个方法 + 0 个模块级名字 + 0 个类级赋值 / 652 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GroupObservationMixin）。
"""
from __future__ import annotations

import re
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .group_observation_shared import (
    _GROUP_INJECTION_GUARD_THRESHOLD,
    _GROUP_INJECTION_META_MARKERS,
    _GROUP_INJECTION_PERSISTENCE_MARKERS,
    _GROUP_INJECTION_PERSONA_MARKERS,
    _GROUP_INJECTION_QUOTE_DAMPENERS,
    _GROUP_INJECTION_TARGET_MARKERS,
    _persona_value,
    logger,
)
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .segmented_message import sanitize_llm_segment_control_tokens
from typing import Any



class GroupObservationInboundContextMixin:
    """GroupObservationInboundContextMixin（从 GroupObservationMixin 拆出）。"""


    @staticmethod
    def _normalize_group_member_role(value: Any) -> str:
        text = _single_line(value, 24).lower()
        aliases = {
            "owner": "owner", "creator": "owner", "群主": "owner",
            "admin": "admin", "administrator": "admin", "manager": "admin", "管理员": "admin",
            "member": "member", "normal": "member", "user": "member", "成员": "member", "普通成员": "member",
        }
        return aliases.get(text, "unknown")

    @staticmethod
    def _group_role_source_value(source: Any, *keys: str) -> Any:
        if isinstance(source, dict):
            for key in keys:
                value = source.get(key)
                if value is not None and str(value).strip():
                    return value
            return ""
        for key in keys:
            try:
                value = getattr(source, key, None)
            except Exception:
                value = None
            if value is not None and str(value).strip():
                return value
        return ""

    def _group_sender_role_from_event(self, event: Any) -> str:
        message_obj = getattr(event, "message_obj", None)
        raw_getter = getattr(self, "_event_raw_payload", None)
        raw = raw_getter(event) if callable(raw_getter) else getattr(message_obj, "raw_message", None)
        raw = raw if isinstance(raw, dict) else {}
        sources = [raw.get("sender"), getattr(message_obj, "sender", None), raw]
        for source in sources:
            role = self._normalize_group_member_role(
                self._group_role_source_value(source, "role", "user_role", "group_role", "permission")
            )
            if role != "unknown":
                return role
        return "unknown"

    def _observe_group_role_from_event(
        self,
        group: dict[str, Any],
        event: Any,
        *,
        sender_id: str,
        sender_name: str,
    ) -> None:
        role = self._group_sender_role_from_event(event)
        if role == "unknown" or not sender_id:
            return
        now = _now_ts()
        snapshot = group.setdefault("role_snapshot", {})
        if not isinstance(snapshot, dict):
            snapshot = {}
            group["role_snapshot"] = snapshot
        observed = snapshot.setdefault("observed_roles", {})
        if not isinstance(observed, dict):
            observed = {}
            snapshot["observed_roles"] = observed
        observed[sender_id] = {
            "user_id": sender_id,
            "name": _single_line(sender_name, 60) or sender_id,
            "role": role,
            "observed_at": now,
        }
        snapshot["event_updated_at"] = now
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        member = members.get(sender_id)
        if isinstance(member, dict):
            member["group_role"] = role
            member["group_role_label"] = self._GROUP_ROLE_LABELS[role]
            member["group_role_updated_at"] = now

    def _apply_group_role_member_list(
        self,
        group: dict[str, Any],
        raw_members: list[dict[str, Any]],
        *,
        self_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        current = float(now if now is not None else _now_ts())
        owner: dict[str, Any] = {}
        admins: list[dict[str, Any]] = []
        bot: dict[str, Any] = {"user_id": self_id, "name": "", "role": "unknown"}
        observed_roles: dict[str, Any] = {}
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        for raw in raw_members:
            if not isinstance(raw, dict):
                continue
            user_id = _single_line(raw.get("user_id") or raw.get("uid") or raw.get("uin"), 128)
            if not user_id:
                continue
            role = self._normalize_group_member_role(raw.get("role") or raw.get("permission"))
            name = _single_line(raw.get("card") or raw.get("nickname") or raw.get("name"), 60) or user_id
            role_item = {"user_id": user_id, "name": name, "role": role, "observed_at": current}
            observed_roles[user_id] = role_item
            if role == "owner":
                owner = dict(role_item)
            elif role == "admin":
                admins.append(dict(role_item))
            if self_id and user_id == self_id:
                bot = dict(role_item)
            member = members.get(user_id)
            if isinstance(member, dict):
                member["group_role"] = role
                member["group_role_label"] = self._GROUP_ROLE_LABELS.get(role, "未知")
                member["group_role_updated_at"] = current
        admins.sort(key=lambda item: (str(item.get("name") or ""), str(item.get("user_id") or "")))
        snapshot = {
            "complete": True,
            "source": "onebot_group_member_list",
            "refreshed_at": current,
            "last_attempt_at": current,
            "member_count": len(observed_roles),
            "owner": owner,
            "admins": admins,
            "bot": bot,
            "observed_roles": observed_roles,
        }
        group["role_snapshot"] = snapshot
        return snapshot

    def _group_role_snapshot_summary(self, group: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        snapshot = group.get("role_snapshot") if isinstance(group.get("role_snapshot"), dict) else {}
        current = float(now if now is not None else _now_ts())
        refreshed_at = _safe_float(snapshot.get("refreshed_at") or snapshot.get("event_updated_at"), 0.0, 0.0)
        stale = not refreshed_at or current - refreshed_at > 24 * 3600
        owner = dict(snapshot.get("owner")) if isinstance(snapshot.get("owner"), dict) else {}
        admins = [dict(item) for item in snapshot.get("admins", []) if isinstance(item, dict)] \
            if isinstance(snapshot.get("admins"), list) else []
        bot = dict(snapshot.get("bot")) if isinstance(snapshot.get("bot"), dict) else {}
        observed = snapshot.get("observed_roles") if isinstance(snapshot.get("observed_roles"), dict) else {}
        admin_ids = {_single_line(item.get("user_id"), 128) for item in admins if isinstance(item, dict)}
        for raw in observed.values():
            if not isinstance(raw, dict):
                continue
            role = self._normalize_group_member_role(raw.get("role"))
            user_id = _single_line(raw.get("user_id"), 128)
            if role == "owner" and not owner:
                owner = dict(raw)
            elif role == "admin" and user_id and user_id not in admin_ids:
                admins.append(dict(raw))
                admin_ids.add(user_id)
        admins.sort(key=lambda item: (str(item.get("name") or ""), str(item.get("user_id") or "")))
        return {
            "known": bool(owner or admins or bot.get("role") in {"owner", "admin", "member"}),
            "complete": bool(snapshot.get("complete")),
            "stale": stale,
            "refreshed_at": refreshed_at,
            "member_count": _safe_int(snapshot.get("member_count"), len(observed), 0),
            "owner": owner,
            "admins": admins,
            "bot": bot,
            "bot_role": _single_line(bot.get("role"), 24) or "unknown",
            "bot_role_label": self._GROUP_ROLE_LABELS.get(_single_line(bot.get("role"), 24), "未知"),
        }

    @staticmethod
    def _group_role_context_requested(text: Any) -> bool:
        cleaned = _single_line(text, 320).lower()
        if not cleaned:
            return False
        direct_terms = (
            "群主", "管理员", "群管理", "管理身份", "管理权限", "谁是管理",
            "谁有权限", "你有权限", "你是管理", "bot是管理", "机器人是管理",
            "群身份", "本群身份", "群里的身份", "群内身份",
            "禁言", "解除禁言", "踢出群", "踢人", "移出群", "设置管理员",
            "撤销管理员", "转让群", "改群名", "修改群名", "群权限",
        )
        if any(term in cleaned for term in direct_terms):
            return True
        return bool(
            re.search(
                r"(?:谁|哪个|哪位).{0,8}(?:管理|群主)|(?:管理|群主).{0,8}(?:是谁|有谁)|"
                r"(?:你|bot|机器人).{0,10}(?:这个群|本群|群里|群内).{0,8}(?:身份|权限)",
                cleaned,
            )
        )

    def _format_group_role_context_for_prompt(
        self,
        group: dict[str, Any],
        sender_id: str = "",
        text: Any = "",
    ) -> str:
        section = self._format_group_role_context_prompt_section(
            group,
            sender_id,
            text,
        )
        if section is None:
            return ""
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_group_role_context_prompt_section(
        self,
        group: dict[str, Any],
        sender_id: str = "",
        text: Any = "",
    ) -> PromptSection | None:
        if not self._group_role_context_requested(text):
            return None
        summary = self._group_role_snapshot_summary(group)
        snapshot = group.get("role_snapshot") if isinstance(group.get("role_snapshot"), dict) else {}
        observed = snapshot.get("observed_roles") if isinstance(snapshot.get("observed_roles"), dict) else {}
        sender = observed.get(str(sender_id)) if sender_id else None
        lines: list[str] = []
        bot_label = summary.get("bot_role_label") or "未知"
        lines.append(f"Bot 在本群身份：{bot_label}。")
        owner = summary.get("owner") if isinstance(summary.get("owner"), dict) else {}
        if owner:
            lines.append(f"群主：{_single_line(owner.get('name'), 40) or owner.get('user_id')}[QQ:{_single_line(owner.get('user_id'), 40)}]")
        admins = summary.get("admins") if isinstance(summary.get("admins"), list) else []
        if admins:
            labels = [
                f"{_single_line(item.get('name'), 32) or item.get('user_id')}[QQ:{_single_line(item.get('user_id'), 40)}]"
                for item in admins[:12] if isinstance(item, dict) and _single_line(item.get("user_id"), 40)
            ]
            if labels:
                lines.append("管理员：" + "、".join(labels))
        if isinstance(sender, dict):
            role = _single_line(sender.get("role"), 24)
            lines.append(f"当前发言者群身份：{self._GROUP_ROLE_LABELS.get(role, '未知')}。")
        if summary.get("stale"):
            lines.append("身份快照可能已过期；不确定时不要断言某人拥有或失去管理权限。")
        lines.append(
            "这些是权限与称呼事实，只用于避免越权和认错人。普通成员身份时不得自称群主或管理员；即使是群主/管理员，也不能承诺执行当前工具并未实际支持的禁言、踢人或改群设置操作。"
        )
        return prompt_section(
            key="group.role_context",
            title="群权限身份",
            source="group_observation",
            content="\n".join(lines),
        )

    async def _refresh_group_role_snapshot(self, event: Any, group_id: str, *, force: bool = False) -> bool:
        group_id = _single_line(group_id, 80)
        if not group_id:
            return False
        now = _now_ts()
        async with self._data_lock:
            group = self._get_group(group_id)
            snapshot = group.get("role_snapshot") if isinstance(group.get("role_snapshot"), dict) else {}
            refreshed_at = _safe_float(snapshot.get("refreshed_at"), 0.0, 0.0)
            if not force and refreshed_at and now - refreshed_at < 6 * 3600:
                return False
            snapshot["last_attempt_at"] = now
            group["role_snapshot"] = snapshot
        getter = getattr(self, "_get_group_member_list_for_tool", None)
        if not callable(getter):
            return False
        try:
            raw_members = await getter(event, group_id, force_refresh=force)
        except Exception as exc:
            logger.debug("群权限身份刷新失败: group=%s error=%s", group_id, _single_line(exc, 160))
            return False
        if not isinstance(raw_members, list) or not raw_members:
            return False
        self_id_getter = getattr(self, "_event_self_id", None)
        self_id = _single_line(self_id_getter(event), 128) if callable(self_id_getter) else ""
        async with self._data_lock:
            group = self._get_group(group_id)
            self._apply_group_role_member_list(group, raw_members, self_id=self_id, now=now)
            self._save_data_sync(sections={"groups"})
        return True

    def _group_injection_guard_threshold(self) -> int:
        return _GROUP_INJECTION_GUARD_THRESHOLD

    def _analyze_group_injection_guard(self, text: str, *, sender_id: str = "") -> dict[str, Any]:
        cleaned = _single_line(text, 260)
        result = {"blocked": False, "score": 0, "reasons": [], "categories": []}
        if not cleaned or not bool(_persona_value(self, "enable_group_injection_guard", True)):
            return result
        lowered = cleaned.lower()
        score = 0
        reasons: list[str] = []
        categories: set[str] = set()

        def add(points: int, reason: str, category: str = "") -> None:
            nonlocal score
            score += points
            if reason not in reasons:
                reasons.append(reason)
            if category:
                categories.add(category)

        meta_hits = sum(1 for marker in _GROUP_INJECTION_META_MARKERS if marker in lowered)
        if meta_hits:
            add(3, "meta_prompt", "meta")
        target_hits = sum(1 for marker in _GROUP_INJECTION_TARGET_MARKERS if marker in lowered)
        if target_hits:
            add(1, "target_bot", "target")
        persistence_hits = sum(1 for marker in _GROUP_INJECTION_PERSISTENCE_MARKERS if marker in cleaned)
        if persistence_hits:
            add(min(2, persistence_hits), "persistent_rule", "persist")
        persona_hits = sum(1 for marker in _GROUP_INJECTION_PERSONA_MARKERS if marker in cleaned)
        if persona_hits:
            add(min(2, persona_hits), "persona_control", "persona")
        if re.search(r"(忽略|无视|覆盖|忘掉|重置|别按|不要按).{0,16}(设定|规则|提示词|系统|上下文|记忆|人格|人设)", cleaned, re.I):
            add(4, "override_rule", "override")
        if re.search(r"(以后|从现在开始|今后|往后|之后).{0,24}(叫我|称呼我|管我叫|语气|风格|人设|设定|人格|身份|口癖|后缀|每句|每次回复|回复时|说话时)", cleaned):
            add(3, "persistent_override", "persona")
        if re.search(r"(你|bot|机器人|astrbot|插件).{0,16}(要|得|必须|只能|以后|现在).{0,24}(叫我|称呼我|用.*语气|改成|换成|变成|装成|扮演|带上|加上)", cleaned, re.I):
            add(3, "direct_control", "persona")
        if re.search(r"(每句|每次回复|回复时|说话时|句尾|后面).{0,20}(都|必须|要).{0,20}(带|加|用|写).{0,20}(喵|括号|动作|后缀|口癖|颜文字)", cleaned, re.I):
            add(4, "format_override", "format")
        if re.search(r"(你现在是|你以后是|从现在开始你是|给我扮演|你给我装成|你就是).{0,20}(猫娘|魅魔|女仆|主人|恋人|老婆|妹妹|病娇|傲娇)", cleaned, re.I):
            add(4, "persona_assignment", "persona")
        if re.search(r"(叫我|称呼我|管我叫).{0,12}(主人|猫娘|魅魔|老公|老婆|宝贝|爹)", cleaned):
            add(3, "nickname_override", "persona")
        if re.search(r"(?:我(?:是|就是|才是|是不是)|这个号(?:是|就是)|本号(?:是|就是)|记住我(?:是|叫)|把我当(?:成|作)?|以后把我当(?:成|作)?).{0,10}(?:你的?|妳的?|您(?:的)?|bot的?|机器人(?:的)?)?(?:主人|主用户|目标用户)", cleaned):
            add(5, "identity_impersonation", "identity")
        sender_is_target = False
        clean_sender_id = _single_line(sender_id, 40)
        if clean_sender_id:
            users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
            current_user = users.get(clean_sender_id) if isinstance(users, dict) else None
            sender_is_target = self._is_target_private_user(
                clean_sender_id,
                current_user if isinstance(current_user, dict) else None,
            )
        owner_tokens: list[str] = []
        if not sender_is_target:
            token_getter = getattr(self, "_protected_owner_nickname_tokens", None)
            raw_tokens = token_getter() if callable(token_getter) else set()
            owner_tokens = sorted(
                {
                    _single_line(item, 24)
                    for item in raw_tokens
                    if _single_line(item, 24) and not _single_line(item, 24).isdigit()
                },
                key=len,
                reverse=True,
            )[:12]
        if owner_tokens:
            owner_alt = "|".join(re.escape(item) for item in owner_tokens)
            owner_boundary = r"(?=$|[吗么嘛吧呀啊哦呢诶欸？?！!。,.，、\s])"
            if re.search(
                rf"(?:我(?:是|就是|才是|是不是|叫)|叫我|称呼我|管我叫|以后叫我|以后称呼我|记住我(?:是|叫)|这个号(?:是|就是)|本号(?:是|就是)|把我当(?:成|作)?|以后把我当(?:成|作)?).{{0,8}}(?:{owner_alt}){owner_boundary}",
                cleaned,
                re.I,
            ):
                add(5, "identity_impersonation", "identity")
            elif re.search(
                rf"(?:{owner_alt}).{{0,8}}(?:是|就是).{{0,4}}(?:我|本人|这个号|本号){owner_boundary}",
                cleaned,
                re.I,
            ):
                add(5, "identity_impersonation", "identity")
        if re.search(r"(必须|只能|都要|记得|听我的|按我说的|照我说的|统一改成|全部改成)", cleaned):
            add(1, "imperative_control", "control")
        if any(marker in cleaned for marker in _GROUP_INJECTION_QUOTE_DAMPENERS):
            score -= 1
            if "quoted_context" not in reasons:
                reasons.append("quoted_context")
        score = max(0, score)
        strong_reasons = {
            "meta_prompt",
            "override_rule",
            "direct_control",
            "format_override",
            "persona_assignment",
            "nickname_override",
            "identity_impersonation",
        }
        has_strong_reason = any(reason in strong_reasons for reason in reasons)
        has_targeted_behavior_control = "target" in categories and bool(
            categories.intersection({"persona", "format", "control"})
        )
        has_targeted_persistent_shift = "target" in categories and "persist" in categories
        blocked = score >= self._group_injection_guard_threshold() and (
            has_strong_reason or has_targeted_behavior_control or has_targeted_persistent_shift
        )
        return {
            "blocked": blocked,
            "score": score,
            "reasons": reasons,
            "categories": sorted(categories),
        }

    def _group_text_blocked_by_injection_guard(self, text: Any, *, sender_id: str = "") -> bool:
        return bool(self._analyze_group_injection_guard(_single_line(text, 260), sender_id=sender_id).get("blocked"))

    def _group_message_blocked_by_injection_guard(self, item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        if "injection_guard_blocked" in item:
            return bool(item.get("injection_guard_blocked"))
        return self._group_text_blocked_by_injection_guard(item.get("text"))

    def _raw_group_recent_messages(self, group: dict[str, Any]) -> list[dict[str, Any]]:
        recent = group.get("recent_messages")
        if not isinstance(recent, list):
            return []
        return [item for item in recent if isinstance(item, dict)]

    def _effective_group_history_limit(self) -> int:
        return max(
            1,
            _safe_int(
                _persona_value(self, "max_group_recent_messages", 80),
                80,
                1,
            ),
        )

    def _trim_group_history_lists(self, group: dict[str, Any]) -> int:
        """Keep member and Bot timelines aligned to the active persona limit."""
        limit = self._effective_group_history_limit()
        for field in ("recent_messages", "recent_bot_replies"):
            raw = group.get(field)
            if not isinstance(raw, list):
                if raw is not None:
                    group[field] = []
                continue
            records = [item for item in raw if isinstance(item, dict)]
            group[field] = records[-limit:]
        return limit

    def _record_group_bot_reply(
        self,
        group: dict[str, Any],
        *,
        text: Any,
        reply_to_id: Any = "",
        kind: str,
        talking_to_bot: bool = False,
        ts: float | None = None,
        message_id: Any = "",
        delivery_id: Any = "",
        llm_segments: Any = None,
    ) -> dict[str, Any] | None:
        cleaned = _single_line(sanitize_llm_segment_control_tokens(text), 500)
        if not cleaned:
            return None
        allowed_kinds = {
            "passive_reply",
            "interjection",
            "repeat_follow",
            "repeat_interrupt",
        }
        normalized_kind = _single_line(kind, 40)
        if normalized_kind not in allowed_kinds:
            return None
        recent = group.setdefault("recent_bot_replies", [])
        if not isinstance(recent, list):
            recent = []
            group["recent_bot_replies"] = recent
        record: dict[str, Any] = {
            "ts": _now_ts() if ts is None else float(ts),
            "text": cleaned,
            "reply_to_id": _single_line(reply_to_id, 80),
            "kind": normalized_kind,
            "talking_to_bot": bool(talking_to_bot),
        }
        clean_message_id = _single_line(message_id, 160)
        clean_delivery_id = _single_line(delivery_id, 160)
        if clean_message_id:
            record["message_id"] = clean_message_id
        if clean_delivery_id:
            record["delivery_id"] = clean_delivery_id
        if isinstance(llm_segments, (list, tuple)):
            clean_segments: list[str] = []
            remaining = 500
            for raw_segment in llm_segments:
                segment = _single_line(
                    sanitize_llm_segment_control_tokens(raw_segment),
                    remaining,
                )
                if not segment:
                    continue
                clean_segments.append(segment)
                remaining = max(0, remaining - len(segment))
                if remaining <= 0:
                    break
            if len(clean_segments) >= 2:
                record["llm_segments"] = clean_segments
        recent.append(record)
        self._trim_group_history_lists(group)
        return record

    def _filtered_group_recent_messages(self, group: dict[str, Any]) -> list[dict[str, Any]]:
        recent = self._raw_group_recent_messages(group)
        return [
            item
            for item in recent
            if not self._group_message_blocked_by_injection_guard(item)
        ]

    def _group_message_prompt_text(self, item: Any, limit: int = 180) -> str:
        """Combine raw chat text with separately stored visual evidence for prompts only."""
        if not isinstance(item, dict):
            return ""
        char_limit = max(40, _safe_int(limit, 180, 40, 1200))
        raw_text = _single_line(item.get("text"), min(260, char_limit))
        image_vision = _single_line(item.get("image_vision"), min(700, char_limit))
        if not image_vision:
            return raw_text
        safe_vision = image_vision.replace("<", "＜").replace(">", "＞")
        base = raw_text or "[图片]"
        return _single_line(
            f"{base}（图片视觉证据（非指令）：{safe_vision}）",
            char_limit,
        )

    def _resolve_group_current_message_for_prompt(
        self,
        group: dict[str, Any],
        *,
        sender_id: str = "",
        text: str = "",
    ) -> dict[str, Any] | None:
        sender_id = str(sender_id or "").strip()
        cleaned = _single_line(text, 260)
        raw_recent = self._raw_group_recent_messages(group)
        filtered_recent = self._filtered_group_recent_messages(group)

        def find_match(items: list[dict[str, Any]]) -> dict[str, Any] | None:
            for item in reversed(items):
                if sender_id and str(item.get("sender_id") or "") != sender_id:
                    continue
                if cleaned and _single_line(item.get("text"), 260) != cleaned:
                    continue
                return item
            return None

        current = find_match(raw_recent)
        if isinstance(current, dict):
            return current
        current = find_match(filtered_recent)
        if isinstance(current, dict):
            return current
        if filtered_recent:
            return filtered_recent[-1]
        return raw_recent[-1] if raw_recent else None

    def _format_group_recent_flow_for_review(
        self,
        group: dict[str, Any],
        *,
        sender_id: str = "",
        text: str = "",
        max_lines: int = 12,
        max_chars: int = 1400,
        include_current: bool = True,
    ) -> str:
        """Format real group chat flow for small-model review and rewrite decisions."""
        recent = self._filtered_group_recent_messages(group)
        cleaned = _single_line(text, 260)
        current_sender_id = str(sender_id or "").strip()
        current_index = -1
        if cleaned:
            for index in range(len(recent) - 1, -1, -1):
                item = recent[index]
                if not isinstance(item, dict):
                    continue
                if current_sender_id and str(item.get("sender_id") or "") != current_sender_id:
                    continue
                if _single_line(item.get("text"), 260) != cleaned:
                    continue
                current_index = index
                break

        line_limit = max(2, _safe_int(max_lines, 12, 2))
        start = max(0, len(recent) - line_limit)
        selected: list[tuple[dict[str, Any], int | None]] = [
            (item, start + offset)
            for offset, item in enumerate(recent[start:])
            if isinstance(item, dict)
        ]
        if include_current and cleaned and current_index < start:
            selected.append(
                (
                    {
                        "sender_id": current_sender_id,
                        "name": "",
                        "identity_name": "",
                        "text": cleaned,
                        "_review_current": True,
                    },
                    None,
                )
            )
            if len(selected) > line_limit:
                selected = selected[-line_limit:]

        lines: list[str] = []
        for item, index in selected:
            msg = self._group_message_prompt_text(item, 180)
            if not msg:
                continue
            item_sender_id = _single_line(item.get("sender_id"), 40)
            name = self._group_member_identity_label(
                item_sender_id,
                item.get("identity_name") or item.get("name"),
                limit=24,
            )
            current_mark = "（当前）" if item.get("_review_current") or index == current_index else ""
            lines.append(f"- {current_mark}{name}: {msg}")

        char_limit = max(200, _safe_int(max_chars, 1400, 200))
        while lines and len("\n".join(lines)) > char_limit:
            lines.pop(0)
        return "\n".join(lines)

    def _group_name_from_event(self, event: Any) -> str:
        if event is None:
            return ""

        def clean(value: Any) -> str:
            text = _single_line(value, 80)
            if not text or text.isdigit():
                return ""
            return text

        getter = getattr(event, "get_group_name", None)
        if callable(getter):
            try:
                value = getter()
                if hasattr(value, "__await__"):
                    value = ""
                name = clean(value)
                if name:
                    return name
            except Exception:
                pass

        raw: dict[str, Any] = {}
        raw_getter = getattr(self, "_event_raw_payload", None)
        if callable(raw_getter):
            try:
                payload = raw_getter(event)
                raw = payload if isinstance(payload, dict) else {}
            except Exception:
                raw = {}
        for key in ("group_name", "group_card", "group_display_name", "group_remark", "name", "display_name", "title"):
            value = clean(raw.get(key))
            if value:
                return value
        for obj_key in ("group", "group_info", "sender_group", "guild"):
            group_obj = raw.get(obj_key) if isinstance(raw.get(obj_key), dict) else {}
            for key in ("group_name", "name", "display_name", "group_remark", "title", "card"):
                value = clean(group_obj.get(key))
                if value:
                    return value
        message_obj = getattr(event, "message_obj", None)
        sources = [message_obj]
        if message_obj is not None:
            raw_message = getattr(message_obj, "raw_message", None)
            if isinstance(raw_message, dict):
                sources.append(raw_message)
            sources.append(getattr(message_obj, "group", None))
            sources.append(getattr(message_obj, "group_info", None))
        for source in sources:
            if source is None:
                continue
            for attr in ("group_name", "name", "display_name", "group_card", "group_remark", "title", "card"):
                if isinstance(source, dict):
                    value = clean(source.get(attr))
                else:
                    try:
                        value = clean(getattr(source, attr, None))
                    except Exception:
                        value = ""
                if value:
                    return value
        return ""
