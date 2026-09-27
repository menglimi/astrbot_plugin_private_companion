# -*- coding: utf-8 -*-
"""GroupObservationObservationUpdateMixin。

由 tools/split_mixin_domain.py 从 group_observation.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 312 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GroupObservationMixin）。
"""
from __future__ import annotations

from .group_observation_shared import _persona_value, logger
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from typing import Any



class GroupObservationObservationUpdateMixin:
    """GroupObservationObservationUpdateMixin（从 GroupObservationMixin 拆出）。"""


    def _update_group_observation(
        self,
        group: dict[str, Any],
        *,
        sender_id: str,
        sender_name: str,
        text: str,
        group_id: str = "",
        scene: dict[str, Any] | None = None,
        message_id: str = "",
        event: Any = None,
    ) -> None:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return
        now = _now_ts()
        injection_guard = self._analyze_group_injection_guard(cleaned, sender_id=sender_id)
        blocked_by_guard = bool(injection_guard.get("blocked"))
        group["group_id"] = str(group_id or group.get("group_id") or group.get("id") or "")
        group_name = self._group_name_from_event(event)
        manual_group_name = _single_line(group.get("manual_group_name"), 80)
        if manual_group_name:
            group["name"] = manual_group_name
            group["group_name"] = manual_group_name
        elif group_name and group_name != group["group_id"]:
            group["name"] = group_name
            group["group_name"] = group_name
            group["last_group_name_seen_at"] = now
        group["last_seen"] = now
        group["message_count"] = _safe_int(group.get("message_count"), 0, 0) + 1
        qq_nickname = ""
        nickname_getter = getattr(self, "_sender_qq_nickname", None)
        if callable(nickname_getter) and event is not None:
            try:
                qq_nickname = _single_line(nickname_getter(event), 40)
            except Exception:
                qq_nickname = ""
        sender_role = self._group_sender_role_from_event(event) if event is not None else "unknown"
        if event is not None:
            self._observe_group_role_from_event(
                group,
                event,
                sender_id=sender_id,
                sender_name=sender_name,
            )

        recent = group.setdefault("recent_messages", [])
        if not isinstance(recent, list):
            recent = []
            group["recent_messages"] = recent
        record = {
            "ts": now,
            "sender_id": sender_id,
            "name": _single_line(sender_name, 30) or sender_id,
            "identity_name": _single_line(sender_name, 30) or "群成员",
            "group_role": sender_role,
            "group_role_label": self._GROUP_ROLE_LABELS.get(sender_role, "未知"),
            "text": cleaned,
            "message_id": _single_line(message_id, 120),
            "injection_guard_blocked": blocked_by_guard,
            "injection_guard_score": _safe_int(injection_guard.get("score"), 0, 0),
            "injection_guard_reasons": injection_guard.get("reasons") if isinstance(injection_guard.get("reasons"), list) else [],
        }
        if isinstance(scene, dict):
            record.update({
                "talking_to": _single_line(scene.get("talking_to"), 40) or "group",
                "talking_to_name": _single_line(scene.get("talking_to_name"), 80),
                "scene_trigger": _single_line(scene.get("trigger"), 40),
                "scene_reason": _single_line(scene.get("reason"), 60),
                "wakeup_word": _single_line(scene.get("wakeup_word"), 60),
                "wakeup_strength": _single_line(scene.get("wakeup_strength"), 24),
                "wakeup_strength_label": _single_line(scene.get("wakeup_strength_label"), 24),
                "wakeup_note": _single_line(scene.get("wakeup_note") or scene.get("wakeup_instruction"), 180),
                "wakeup_topic_weight": scene.get("wakeup_topic_weight") if isinstance(scene.get("wakeup_topic_weight"), dict) else {},
                "reply_to_id": _single_line(scene.get("reply_to_id"), 40),
                "at_targets": scene.get("at_targets") if isinstance(scene.get("at_targets"), list) else [],
            })
        recent.append(record)
        self._trim_group_history_lists(group)
        # Group transcripts are useful for the live context window, but they
        # should be flushed in batches instead of causing a full store write
        # for every inbound message.
        setattr(self, "_group_observation_dirty", True)
        if _persona_value(self, "enable_group_relationship_graph", False):
            members = group.setdefault("members", {})
            if not isinstance(members, dict):
                members = {}
                group["members"] = members
            member = members.setdefault(sender_id, {"name": sender_name, "count": 0, "recent_phrases": []})
            if not isinstance(member, dict):
                member = {"name": sender_name, "count": 0, "recent_phrases": []}
                members[sender_id] = member
            member["user_id"] = sender_id
            display_name = _single_line(sender_name, 30) or sender_id
            previous_display_name = _single_line(member.get("name"), 30)
            if previous_display_name and display_name and previous_display_name != display_name:
                events = member.setdefault("display_name_events", [])
                if not isinstance(events, list):
                    events = []
                    member["display_name_events"] = events
                last = events[-1] if events and isinstance(events[-1], dict) else {}
                if not (
                    _single_line(last.get("old"), 30) == previous_display_name
                    and _single_line(last.get("new"), 30) == display_name
                    and now - _safe_float(last.get("ts"), 0) < 3600
                ):
                    events.append({"ts": now, "old": previous_display_name, "new": display_name})
                    del events[:-12]
            member["name"] = _single_line(sender_name, 30) or member.get("name") or sender_id
            member["identity_name"] = _single_line(sender_name, 30) or "群成员"
            member.pop("identity_note", None)
            member.pop("boundary_note", None)
            member["count"] = _safe_int(member.get("count"), 0, 0) + 1
            member["last_seen"] = now
            if sender_role != "unknown":
                member["group_role"] = sender_role
                member["group_role_label"] = self._GROUP_ROLE_LABELS[sender_role]
                member["group_role_updated_at"] = now
            phrases = member.setdefault("recent_phrases", [])
            if not isinstance(phrases, list):
                phrases = []
                member["recent_phrases"] = phrases
            if 2 <= len(cleaned) <= 50 and not blocked_by_guard:
                phrases.insert(0, cleaned)
                member["recent_phrases"] = list(dict.fromkeys(phrases))[:8]

        if blocked_by_guard:
            logger.info(
                "群聊防注入已阻断学习链路: group=%s sender=%s score=%s reasons=%s text=%s",
                group.get("group_id") or group_id or "",
                sender_id,
                _safe_int(injection_guard.get("score"), 0, 0),
                ",".join(_single_line(item, 24) for item in injection_guard.get("reasons", []) if _single_line(item, 24)),
                _single_line(cleaned, 120),
            )
        if not blocked_by_guard:
            expression_feedback_updater = getattr(self, "_apply_expression_rule_feedback", None)
            if callable(expression_feedback_updater):
                expression_feedback_updater(group, cleaned, channel="group")
        if not blocked_by_guard and self._expression_group_learning_source_enabled(group.get("group_id") or group_id):
            self._update_group_expression_profile_from_message(group, cleaned)
            self._refresh_expression_voice_profile()
        if _persona_value(self, "enable_group_slang_learning", False) and not blocked_by_guard:
            self._learn_group_nickname_correction(group, cleaned)
            self._learn_group_slang(group, cleaned)
        if _persona_value(self, "enable_group_topic_threads", False) and not blocked_by_guard:
            self._update_group_topic_threads(group, sender_id=sender_id, sender_name=sender_name, text=cleaned)
        if _persona_value(self, "enable_group_relationship_graph", False) and not blocked_by_guard:
            self._update_group_relationship_graph(group, sender_id=sender_id, sender_name=sender_name, text=cleaned)
        if _persona_value(self, "enable_group_interjection_feedback", False) and not blocked_by_guard:
            self._update_group_interjection_feedback(group, sender_id=sender_id, text=cleaned)
        self._update_group_atmosphere(group)
        if _persona_value(self, "enable_group_social_context", False):
            try:
                self._update_group_social_context(group, now=now)
            except Exception as exc:
                logger.debug(
                    "[PrivateCompanion] 群聊社交氛围/名场面/边界更新失败: %s",
                    _single_line(exc, 120),
                )

    def _group_observation_event_text(self, event: Any, *, limit: int = 260) -> str:
        text = _single_line(getattr(event, "message_str", ""), limit)
        if text:
            return text
        labels: list[str] = []
        component_aliases = (
            (("image", "photo", "picture"), "[图片]"),
            (("record", "audio", "voice"), "[语音]"),
            (("video",), "[视频]"),
            (("forward", "node"), "[合并转发]"),
            (("json", "xml", "share", "card"), "[分享卡片]"),
            (("file",), "[文件]"),
        )
        component_getter = getattr(self, "_event_components", None)
        components = component_getter(event) if callable(component_getter) else []
        for component in components if isinstance(components, list) else []:
            component_name = type(component).__name__.lower()
            for aliases, label in component_aliases:
                if any(alias in component_name for alias in aliases):
                    if label not in labels:
                        labels.append(label)
                    break
        return _single_line(" ".join(labels), limit)

    @staticmethod
    def _group_observation_marker_matches(
        marker: Any,
        *,
        group_id: str,
        sender_id: str,
        text: str,
        message_id: str,
    ) -> bool:
        if not isinstance(marker, dict):
            return False
        if _single_line(marker.get("group_id"), 80) != _single_line(group_id, 80):
            return False
        marker_message_id = _single_line(marker.get("message_id"), 120)
        if message_id and marker_message_id:
            return marker_message_id == message_id
        return (
            _single_line(marker.get("sender_id"), 80) == _single_line(sender_id, 80)
            and _single_line(marker.get("text"), 260) == _single_line(text, 260)
        )

    def _merge_group_observation_scene(
        self,
        group: dict[str, Any],
        *,
        sender_id: str,
        text: str,
        message_id: str,
        scene: dict[str, Any] | None,
    ) -> None:
        if not isinstance(scene, dict) or not scene:
            return
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        target = None
        for item in reversed(recent[-8:]):
            if not isinstance(item, dict):
                continue
            item_message_id = _single_line(item.get("message_id"), 120)
            if message_id and item_message_id == message_id:
                target = item
                break
            if (
                not message_id
                and _single_line(item.get("sender_id"), 80) == _single_line(sender_id, 80)
                and _single_line(item.get("text"), 260) == _single_line(text, 260)
            ):
                target = item
                break
        if not isinstance(target, dict):
            return
        target.update(
            {
                "talking_to": _single_line(scene.get("talking_to"), 40) or target.get("talking_to") or "group",
                "talking_to_name": _single_line(scene.get("talking_to_name"), 80) or target.get("talking_to_name") or "",
                "scene_trigger": _single_line(scene.get("trigger"), 40) or target.get("scene_trigger") or "",
                "scene_reason": _single_line(scene.get("reason"), 60) or target.get("scene_reason") or "",
                "wakeup_word": _single_line(scene.get("wakeup_word"), 60) or target.get("wakeup_word") or "",
                "wakeup_strength": _single_line(scene.get("wakeup_strength"), 24) or target.get("wakeup_strength") or "",
                "wakeup_strength_label": _single_line(scene.get("wakeup_strength_label"), 24) or target.get("wakeup_strength_label") or "",
                "wakeup_note": _single_line(scene.get("wakeup_note") or scene.get("wakeup_instruction"), 180) or target.get("wakeup_note") or "",
                "wakeup_topic_weight": scene.get("wakeup_topic_weight") if isinstance(scene.get("wakeup_topic_weight"), dict) else target.get("wakeup_topic_weight") or {},
                "reply_to_id": _single_line(scene.get("reply_to_id"), 40) or target.get("reply_to_id") or "",
                "at_targets": scene.get("at_targets") if isinstance(scene.get("at_targets"), list) else target.get("at_targets") or [],
            }
        )

    def _capture_group_observation_once(
        self,
        group: dict[str, Any],
        *,
        sender_id: str,
        sender_name: str,
        text: str,
        group_id: str,
        scene: dict[str, Any] | None = None,
        message_id: str = "",
        event: Any = None,
    ) -> bool:
        cleaned = _single_line(text, 260)
        clean_message_id = _single_line(message_id, 120)
        if not cleaned:
            return False
        marker = getattr(event, "private_companion_group_observation_capture", None) if event is not None else None
        already_captured = self._group_observation_marker_matches(
            marker,
            group_id=group_id,
            sender_id=sender_id,
            text=cleaned,
            message_id=clean_message_id,
        )
        if not already_captured and clean_message_id:
            recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
            already_captured = any(
                isinstance(item, dict) and _single_line(item.get("message_id"), 120) == clean_message_id
                for item in recent[-12:]
            )
        if already_captured:
            self._merge_group_observation_scene(
                group,
                sender_id=sender_id,
                text=cleaned,
                message_id=clean_message_id,
                scene=scene,
            )
            return False
        self._update_group_observation(
            group,
            sender_id=sender_id,
            sender_name=sender_name,
            text=cleaned,
            group_id=group_id,
            scene=scene,
            message_id=clean_message_id,
            event=event,
        )
        if event is not None:
            try:
                setattr(
                    event,
                    "private_companion_group_observation_capture",
                    {
                        "group_id": _single_line(group_id, 80),
                        "sender_id": _single_line(sender_id, 80),
                        "text": cleaned,
                        "message_id": clean_message_id,
                        "ts": _now_ts(),
                    },
                )
            except Exception:
                pass
        return True
