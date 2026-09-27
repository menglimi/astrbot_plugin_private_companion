# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from astrbot.api.event import AstrMessageEvent

from .helpers import _single_line

from .atrelay_shared import (
    DEFAULT_AI_DAILY_NEWS_SOURCE,
    DEFAULT_NEWS_SOURCES,
    LEGACY_DEFAULT_NEWS_SOURCES,
    PREVIOUS_TECH_DEFAULT_NEWS_SOURCES,
    _ALMANAC_JI,
    _ALMANAC_YI,
    _LUNAR_DAY_NAMES,
    _LUNAR_MONTH_NAMES,
    _PLATFORM_DISPLAY_NAMES,
    _SOLAR_TERM_DATES,
    _render_atrelay_prompt_section_labeled,
    logger,
)
from .atrelay_shared import logger
from .atrelay_part03 import AtRelayPart03Mixin
from .atrelay_part02 import AtRelayPart02Mixin
from .atrelay_part01 import AtRelayPart01Mixin
class AtRelayMixin(AtRelayPart01Mixin, AtRelayPart02Mixin, AtRelayPart03Mixin):
    """跨群/私聊转发工具的目标解析、边界和队列"""
    def _normalize_atrelay_group_target_id(self, value: Any) -> str:
        normalizer = getattr(self, "_normalize_group_identity_id", None)
        return normalizer(value) if callable(normalizer) else _single_line(value, 160)

    def _atrelay_known_group_ids(self) -> set[str]:
        known: set[str] = set()

        def add(value: Any) -> None:
            try:
                group_id = self._normalize_atrelay_group_target_id(value)
            except Exception:
                return
            if group_id:
                known.add(group_id)

        configured = getattr(self, "_configured_group_ids", None)
        if callable(configured):
            try:
                for group_id in configured():
                    add(group_id)
            except Exception:
                pass
        try:
            raw_data = getattr(self, "data", None)
        except Exception:
            raw_data = None
        data = raw_data if isinstance(raw_data, dict) else {}
        for key in ("groups", "worldbook_group_profiles"):
            try:
                records = data.get(key)
                if not isinstance(records, dict):
                    continue
                for record_id, record in records.items():
                    try:
                        record_group_id = (
                            record.get("group_id")
                            if isinstance(record, dict)
                            else record_id
                        )
                    except Exception:
                        record_group_id = record_id
                    add(record_group_id)
                    add(record_id)
            except Exception:
                continue
        return known

    def _atrelay_tool_authorization(self, event: AstrMessageEvent | None) -> tuple[bool, str]:
        """Require the configured private-companion owner for relay actions."""
        requester_id = ""
        if event is not None:
            try:
                identity_for_event = getattr(self, "_event_permission_identity_id", None)
                requester_id = (
                    identity_for_event(event)
                    if callable(identity_for_event)
                    else self._permission_identity_id(event.get_sender_id())
                )
            except Exception:
                requester_id = ""
        checker = getattr(self, "_is_private_companion_owner_user_id", None)
        allowed = bool(requester_id and callable(checker) and checker(requester_id))
        if not allowed:
            logger.info(
                "relay authorization denied: sender=%s umo=%s",
                requester_id or "-",
                _single_line(getattr(event, "unified_msg_origin", ""), 120),
            )
        return allowed, requester_id

    def _atrelay_target_group_allowed(self, group_id: Any, event: AstrMessageEvent | None = None) -> str:
        try:
            target = self._normalize_atrelay_group_target_id(group_id)
        except Exception:
            target = ""
        if not target:
            return "发送失败：群号格式不正确"
        blacklist_getter = getattr(self, "_configured_group_blacklist_ids", None)
        blacklist = set(blacklist_getter()) if callable(blacklist_getter) else set()
        for blocked in tuple(blacklist):
            try:
                normalized_blocked = self._normalize_atrelay_group_target_id(blocked)
            except Exception:
                normalized_blocked = ""
            if normalized_blocked:
                blacklist.add(normalized_blocked)
        if target in blacklist:
            logger.info("relay group denied by blacklist: group=%s", target)
            return "发送失败：目标群不在允许转述范围内"
        whitelist_getter = getattr(self, "_configured_group_ids", None)
        allowed = set(whitelist_getter()) if callable(whitelist_getter) else set()
        allowed |= self._atrelay_known_group_ids()
        extractor = getattr(self, "_extract_group_id_from_event", None)
        if event is not None and callable(extractor):
            try:
                current_group = self._normalize_atrelay_group_target_id(
                    extractor(event)
                )
            except Exception:
                current_group = ""
            if current_group:
                allowed.add(current_group)
        if not allowed:
            logger.warning("relay group allow-set empty; compatibility pass: group=%s", target)
            return ""
        if target not in allowed:
            logger.info("relay group denied: group=%s", target)
            return "发送失败：目标群不在允许转述范围内"
        return ""

