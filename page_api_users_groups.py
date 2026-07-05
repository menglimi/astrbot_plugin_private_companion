# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from astrbot.api import logger
from quart import request

from .helpers import _safe_int


class PrivateCompanionPageApiUsersGroupsMixin:
    async def list_users(self) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            limit = self._query_int("limit", 80, 1, 300)
            async with self.plugin._data_lock:
                users = self.plugin.data.get("users", {})
                if not isinstance(users, dict):
                    users = {}
                user_items = [(user_id, dict(user)) for user_id, user in users.items() if isinstance(user, dict)]
            items = [self._user_summary(user_id, user) for user_id, user in user_items]
            items.sort(key=lambda item: item.get("last_seen_ts") or 0, reverse=True)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if elapsed_ms > 1200:
                logger.warning("[PrivateCompanionPage] 用户列表接口耗时较高: elapsed=%sms users=%s", elapsed_ms, len(items))
            return self._ok({"items": items[:limit], "total": len(items)})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取用户列表失败: {exc}", exc_info=True)
            return self._error(str(exc))
    async def get_user(self) -> dict[str, Any]:
        user_id = str(request.args.get("user_id", "")).strip()
        if not user_id:
            return self._error("缺少 user_id")
        try:
            async with self.plugin._data_lock:
                user = deepcopy((self.plugin.data.get("users") or {}).get(user_id))
                worldbook_member = self._worldbook_member_for_private_user_locked(self.plugin.data, user_id, user if isinstance(user, dict) else {})
            if not isinstance(user, dict):
                return self._error("用户不存在")
            detail = self._user_summary(user_id, user)
            detail.update(
                {
                    "memory": user.get("companion_memory") if isinstance(user.get("companion_memory"), dict) else {},
                    "expression_profile": self._expression_profile_summary(user),
                    "intent_profile": user.get("intent_profile") if isinstance(user.get("intent_profile"), dict) else {},
                    "relationship_state": user.get("relationship_state") if isinstance(user.get("relationship_state"), dict) else {},
                    "behavior_habits": self._behavior_habit_summary(user),
                    "dialogue_episodes": self._limited_list(user.get("dialogue_episodes"), 12),
                    "open_loops": self._limited_list(user.get("open_loops"), 12),
                    "recent_reply_topics": self._limited_list(user.get("recent_reply_topics"), 16),
                    "last_user_message": self._display_message_text(user.get("last_user_message"), 500),
                    "last_companion_message": self._display_message_text(user.get("last_companion_message"), 500),
                    "worldbook_member": worldbook_member,
                    "formatted": {
                        "relationship": self.plugin._format_relationship_summary(user),
                        "action_affinity": self.plugin._format_action_affinity_summary(user),
                        "next_proactive": self.plugin._format_next_proactive(user),
                    },
                }
            )
            return self._ok(detail)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取用户详情失败: {exc}", exc_info=True)
            return self._error(str(exc))
    async def update_user(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        user_id = str(payload.get("user_id", "")).strip()
        if not user_id:
            return self._error("缺少 user_id")
        try:
            action_message = ""
            async with self.plugin._data_lock:
                user = self.plugin._get_user(user_id)
                if "enabled" in payload:
                    enabled = bool(payload.get("enabled"))
                    user["enabled"] = enabled
                    user["manual_enabled"] = enabled
                    user["manual_disabled"] = not enabled
                    if enabled:
                        self.plugin._ensure_private_user_umo(user_id, user)
                    if not enabled:
                        self.plugin._clear_pending_proactive_plan(user)
                if "nickname" in payload:
                    user["nickname"] = self._single_line(payload.get("nickname"), 24)
                if "style" in payload:
                    user["style"] = self._single_line(payload.get("style"), 24)
                if "relationship_role" in payload:
                    role = self.plugin._normalize_private_user_role(payload.get("relationship_role"))
                    if role:
                        user["relationship_role"] = role
                if "proactive_daily_limit" in payload:
                    user["proactive_daily_limit"] = _safe_int(payload.get("proactive_daily_limit"), -1, -1, 30)
                for key in (
                    "proactive_idle_minutes",
                    "proactive_min_interval_minutes",
                    "photo_daily_limit",
                    "screen_peek_daily_limit",
                    "poke_daily_limit",
                ):
                    if key in payload:
                        user[key] = _safe_int(payload.get(key), -1, -1)
                if self.plugin._private_user_role(user, user_id) == "friend":
                    user["photo_daily_limit"] = -1
                    user["photo_sent_today"] = 0
                    user["photo_sent_day"] = ""
                    user["photo_generated_today"] = 0
                    user["photo_generated_day"] = ""
                    user["last_generated_photo_path"] = ""
                    user["last_generated_photo_at"] = 0
                    user["screen_peek_daily_limit"] = -1
                    user["screen_peek_today"] = 0
                    user["screen_peek_day"] = ""
                    user["screen_peek_last_at"] = 0
                if "proactive_boundary_note" in payload:
                    user["proactive_boundary_note"] = self._single_line(payload.get("proactive_boundary_note"), 180)
                if payload.get("reset_daily"):
                    user["sent_today"] = 0
                    user["sent_day"] = ""
                    user["ignored_streak"] = 0
                    user["photo_sent_today"] = 0
                    user["photo_sent_day"] = ""
                    user["photo_generated_today"] = 0
                    user["photo_generated_day"] = ""
                    user["screen_peek_today"] = 0
                if payload.get("clear_schedule"):
                    self.plugin._clear_pending_proactive_plan(user)
                if payload.get("clear_emotion_state"):
                    user["intent_profile"] = {}
                    user["relationship_state"] = {}
                if payload.get("clear_learning"):
                    for key, empty in (
                        ("companion_memory", {}),
                        ("expression_profile", {}),
                        ("intent_profile", {}),
                        ("relationship_state", {}),
                        ("recent_reply_topics", []),
                        ("dialogue_episodes", []),
                        ("open_loops", []),
                        ("action_preferences", {}),
                    ):
                        user[key] = empty
                    user["episode_message_count"] = 0
                    user["last_episode_refresh_at"] = 0
                    user["last_memory_refresh_at"] = 0
                if payload.get("clear_open_loops"):
                    action_message = self.plugin._remove_open_loop_entry(user, "全部")
                remove_open_loop_text = self._single_line(payload.get("remove_open_loop_text"), 120)
                if remove_open_loop_text:
                    action_message = self.plugin._remove_open_loop_entry(user, remove_open_loop_text)
                expression_action = self._single_line(payload.get("expression_action"), 40)
                if expression_action:
                    action_message = self._apply_expression_profile_action(user, payload)
                self.plugin._save_data_sync()
                snapshot = deepcopy(user)
            result = self._user_summary(user_id, snapshot)
            result.update(
                {
                    "expression_profile": self._expression_profile_summary(snapshot),
                }
            )
            if action_message:
                result["message"] = action_message
            return self._ok(result)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新用户失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def delete_user(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        user_id = str(payload.get("user_id", "")).strip()
        if not user_id:
            return self._error("缺少 user_id")
        try:
            async with self.plugin._data_lock:
                users = self.plugin.data.get("users")
                if not isinstance(users, dict):
                    users = {}
                    self.plugin.data["users"] = users
                canonical_user_id = self.plugin._canonical_private_user_id(user_id)
                removed_ids = {user_id}
                removed_user = users.pop(user_id, None)
                if isinstance(removed_user, dict):
                    for alias_id in removed_user.get("alias_user_ids") if isinstance(removed_user.get("alias_user_ids"), list) else []:
                        alias_text = str(alias_id or "").strip()
                        if alias_text:
                            removed_ids.add(alias_text)
                removed_ids = {item for item in removed_ids if item}

                old_target_user_ids = self._normalize_id_list(getattr(self.plugin, "target_user_ids", []) or [])
                target_user_ids = [item for item in old_target_user_ids if item not in removed_ids]
                removed_target = len(target_user_ids) != len(old_target_user_ids)

                private_aliases = {
                    str(alias).strip(): str(target).strip()
                    for alias, target in (getattr(self.plugin, "private_user_aliases", {}) or {}).items()
                    if str(alias).strip()
                    and str(target).strip()
                    and str(alias).strip() not in removed_ids
                    and str(target).strip() not in removed_ids
                }
                delivery_aliases = {
                    str(alias).strip(): str(target).strip()
                    for alias, target in (getattr(self.plugin, "private_user_delivery_aliases", {}) or {}).items()
                    if str(alias).strip()
                    and str(target).strip()
                    and str(alias).strip() not in removed_ids
                    and str(target).strip() not in removed_ids
                }
                removed_private_aliases = len(private_aliases) != len(getattr(self.plugin, "private_user_aliases", {}) or {})
                removed_delivery_aliases = len(delivery_aliases) != len(getattr(self.plugin, "private_user_delivery_aliases", {}) or {})

                alias_text = self._format_private_alias_mapping(private_aliases)
                delivery_alias_text = self._format_private_alias_mapping(delivery_aliases)
                overrides = {
                    "target_user_ids": target_user_ids,
                    "private_user_aliases": alias_text,
                    "private_user_delivery_aliases": delivery_alias_text,
                }
                self._apply_config_value("target_user_ids", target_user_ids, overrides)
                self._apply_config_value("private_user_aliases", alias_text, overrides)
                self._apply_config_value("private_user_delivery_aliases", delivery_alias_text, overrides)
                self.plugin._save_data_sync()

            config_saved = await self._save_config_if_possible()
            message_parts = []
            if removed_user is not None:
                message_parts.append("已删除私聊用户记录")
            if removed_target:
                message_parts.append("已移出主动目标名单")
            if removed_private_aliases:
                message_parts.append("已清理身份归并映射")
            if removed_delivery_aliases:
                message_parts.append("已清理主动发送映射")
            message = "，".join(message_parts) if message_parts else "没有找到可删除的私聊用户记录"
            return self._ok(
                {
                    "user_id": user_id,
                    "canonical_user_id": canonical_user_id,
                    "removed_ids": sorted(removed_ids),
                    "removed_user": removed_user is not None,
                    "removed_target": removed_target,
                    "removed_private_aliases": removed_private_aliases,
                    "removed_delivery_aliases": removed_delivery_aliases,
                    "config_saved": config_saved,
                    "message": message,
                }
            )
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 删除用户失败: {exc}", exc_info=True)
            return self._error(str(exc))

    @staticmethod
    def _format_private_alias_mapping(mapping: dict[str, str]) -> str:
        return "\n".join(
            f"{alias}={target}"
            for alias, target in sorted(
                (
                    (str(alias or "").strip(), str(target or "").strip())
                    for alias, target in (mapping or {}).items()
                ),
                key=lambda item: (item[1], item[0]),
            )
            if alias and target and alias != target
        )

    async def list_groups(self) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            limit = self._query_int("limit", 80, 1, 300)
            async with self.plugin._data_lock:
                groups = self.plugin.data.get("groups", {})
                if not isinstance(groups, dict):
                    groups = {}
                visible_groups = [
                    (group_id, dict(group))
                    for group_id, group in groups.items()
                    if isinstance(group, dict) and not self._looks_like_member_shadow_group(str(group_id), group)
                ]
                shadow_count = len(groups) - len(visible_groups)
            await self._refresh_group_names_from_platform(visible_groups)
            items = [self._group_summary(group_id, group) for group_id, group in visible_groups]
            items.sort(key=lambda item: item.get("last_seen_ts") or 0, reverse=True)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if elapsed_ms > 1200:
                logger.warning("[PrivateCompanionPage] 群列表接口耗时较高: elapsed=%sms groups=%s", elapsed_ms, len(items))
            return self._ok({"items": items[:limit], "total": len(items), "shadow_total": shadow_count})
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取群列表失败: {exc}", exc_info=True)
            return self._error(str(exc))

    def _looks_like_member_shadow_group(self, group_id: str, group: dict[str, Any]) -> bool:
        """Hide historical records created when a sender id was mistaken for a group id."""
        gid = str(group_id or group.get("group_id") or "").strip()
        if not gid or not gid.isdigit():
            return False
        configured = set(self.plugin._configured_group_ids()) | set(self.plugin._configured_group_blacklist_ids())
        if gid in configured:
            return False
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        sender_ids = [
            str(item.get("sender_id") or "").strip()
            for item in recent
            if isinstance(item, dict) and str(item.get("sender_id") or "").strip()
        ]
        if not sender_ids:
            return False
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        same_sender_hits = sum(1 for sender_id in sender_ids if sender_id == gid)
        unique_senders = {sender_id for sender_id in sender_ids if sender_id}
        if gid in members and same_sender_hits >= max(1, int(len(sender_ids) * 0.8)) and len(unique_senders) <= 2:
            return True
        if not self._single_line(group.get("name") or group.get("group_name"), 80) and same_sender_hits == len(sender_ids) and len(members) <= 2:
            return True
        return False

    def _group_display_name_missing(self, group_id: str, group: dict[str, Any]) -> bool:
        name = self._single_line(group.get("name") or group.get("group_name") or group.get("display_name"), 80)
        gid = str(group_id or group.get("group_id") or "").strip()
        return not name or name == gid or name == f"群 {gid}" or name.isdigit()

    def _clean_group_display_name(self, value: Any, group_id: str = "") -> str:
        text = self._single_line(value, 80)
        gid = str(group_id or "").strip()
        if not text or text == gid or text == f"群 {gid}" or text.isdigit():
            return ""
        return text

    def _extract_onebot_list(self, result: Any) -> list[dict[str, Any]]:
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
            groups = result.get("groups") or result.get("items") or result.get("result")
            if isinstance(groups, list):
                return [item for item in groups if isinstance(item, dict)]
        return []

    def _extract_onebot_object(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {}
        data = result.get("data")
        if isinstance(data, dict):
            return data
        result_obj = result.get("result")
        if isinstance(result_obj, dict):
            return result_obj
        return result

    def _name_from_group_payload(self, item: dict[str, Any], group_id: str) -> str:
        return self._clean_group_display_name(
            item.get("group_name")
            or item.get("group_remark")
            or item.get("group_display_name")
            or item.get("name")
            or item.get("display_name")
            or item.get("title"),
            group_id,
        )

    def _group_names_from_loaded_history(self, target_ids: set[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        if not target_ids:
            return found
        patterns = {
            group_id: re.compile(rf"群号\s*{re.escape(group_id)}\(([^)\r\n]{{1,80}})\)")
            for group_id in target_ids
        }
        stack: list[Any] = [getattr(self.plugin, "data", {})]
        scanned_strings = 0
        while stack and len(found) < len(target_ids) and scanned_strings < 20000:
            value = stack.pop()
            if isinstance(value, dict):
                stack.extend(value.values())
                continue
            if isinstance(value, list):
                stack.extend(value)
                continue
            if not isinstance(value, str) or "群号" not in value:
                continue
            scanned_strings += 1
            for group_id, pattern in patterns.items():
                if group_id in found:
                    continue
                match = pattern.search(value)
                if not match:
                    continue
                name = self._clean_group_display_name(match.group(1), group_id)
                if name:
                    found[group_id] = name
        return found

    @staticmethod
    def _lookup_float(value: Any) -> float:
        try:
            return float(value or 0)
        except Exception:
            return 0.0

    def _page_onebot_call_actions(self) -> list[Any]:
        candidates: list[Any] = []
        finder = getattr(self.plugin, "_qzone_find_runtime_bot", None)
        if callable(finder):
            try:
                bot = finder()
                if bot is not None:
                    candidates.append(bot)
            except Exception:
                pass
        context = getattr(self.plugin, "context", None)
        if context is not None:
            try:
                platform = context.get_platform("aiocqhttp")
            except Exception:
                platform = None
            if platform is not None:
                candidates.append(platform)
                for attr in ("bot", "client", "adapter", "connection", "api"):
                    try:
                        value = getattr(platform, attr, None)
                    except Exception:
                        value = None
                    if value is not None:
                        candidates.append(value)
        platform_manager = getattr(context, "platform_manager", None) if context is not None else None
        for attr in ("platform_insts", "platform_instances", "instances", "platforms"):
            try:
                value = getattr(platform_manager, attr, None)
            except Exception:
                value = None
            if not value:
                continue
            try:
                iterable = value.values() if isinstance(value, dict) else value
                candidates.extend(list(iterable or []))
            except Exception:
                pass
        calls: list[Any] = []
        seen: set[int] = set()
        for candidate in candidates:
            if candidate is None or id(candidate) in seen:
                continue
            seen.add(id(candidate))
            api = getattr(candidate, "api", None)
            call_action = getattr(api, "call_action", None)
            if not callable(call_action):
                call_action = getattr(candidate, "call_action", None)
            if callable(call_action):
                calls.append(call_action)
        return calls

    async def _page_call_onebot_action(self, action: str, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for call_action in self._page_onebot_call_actions():
            try:
                result = call_action(action, **kwargs)
                return await result if hasattr(result, "__await__") else result
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("没有可用的 OneBot call_action")

    async def _refresh_group_names_from_platform(self, visible_groups: list[tuple[str, dict[str, Any]]], *, force: bool = False) -> None:
        now = time.time()
        display_missing = [
            (str(group_id), group)
            for group_id, group in visible_groups
            if self._group_display_name_missing(str(group_id), group)
        ]
        if not display_missing:
            return
        target_ids = {group_id for group_id, _ in display_missing if group_id}
        found: dict[str, str] = self._group_names_from_loaded_history(target_ids)
        missing = [
            (group_id, group)
            for group_id, group in display_missing
            if group_id not in found
            and (force or now - self._lookup_float(group.get("last_group_name_lookup_at")) > 5 * 60)
        ]
        platform_target_ids = {group_id for group_id, _ in missing if group_id}
        try:
            if platform_target_ids:
                raw_groups = await self._page_call_onebot_action("get_group_list")
                for item in self._extract_onebot_list(raw_groups):
                    group_id = str(item.get("group_id") or item.get("group_uin") or item.get("group_no") or "").strip()
                    if group_id not in platform_target_ids:
                        continue
                    name = self._name_from_group_payload(item, group_id)
                    if name:
                        found[group_id] = name
        except Exception as exc:
            logger.info("[PrivateCompanionPage] 群列表名称刷新失败: %s", self._single_line(exc, 120))
        if len(found) < len(target_ids) and platform_target_ids:
            for group_id, _ in missing[:30]:
                if group_id in found:
                    continue
                try:
                    raw_item = await self._page_call_onebot_action("get_group_info", group_id=int(group_id) if group_id.isdigit() else group_id)
                except Exception:
                    continue
                item = self._extract_onebot_object(raw_item)
                if not isinstance(item, dict):
                    continue
                name = self._name_from_group_payload(item, group_id)
                if name:
                    found[group_id] = name
        if not found and not missing:
            return
        changed = False
        async with self.plugin._data_lock:
            groups = self.plugin.data.get("groups")
            if not isinstance(groups, dict):
                return
            for group_id, snapshot in display_missing:
                group = groups.get(group_id)
                if not isinstance(group, dict):
                    continue
                if group_id in platform_target_ids:
                    group["last_group_name_lookup_at"] = now
                name = found.get(group_id, "")
                if name:
                    group["name"] = name
                    group["group_name"] = name
                    group["last_group_name_seen_at"] = now
                    snapshot["name"] = name
                    snapshot["group_name"] = name
                    snapshot["last_group_name_seen_at"] = now
                    changed = True
                if group_id in platform_target_ids:
                    snapshot["last_group_name_lookup_at"] = now
            if changed:
                self.plugin._save_data_sync()
    async def get_group(self) -> dict[str, Any]:
        group_id = str(request.args.get("group_id", "")).strip()
        if not group_id:
            return self._error("缺少 group_id")
        try:
            async with self.plugin._data_lock:
                group = deepcopy((self.plugin.data.get("groups") or {}).get(group_id))
            if not isinstance(group, dict):
                return self._error("群不存在")
            await self._refresh_group_names_from_platform([(group_id, group)], force=True)
            detail = self._group_summary(group_id, group)
            detail.update(
                {
                    "members": group.get("members") if isinstance(group.get("members"), dict) else {},
                    "recent_messages": self._limited_list(group.get("recent_messages"), 30),
                    "topic_threads": self._group_topic_thread_items(group),
                    "group_episodes": self._limited_list(group.get("group_episodes"), 12),
                    "relationship_edges": group.get("relationship_edges") if isinstance(group.get("relationship_edges"), dict) else {},
                    "interjection_feedback": group.get("interjection_feedback") if isinstance(group.get("interjection_feedback"), dict) else {},
                    "last_bot_interjection": self._sanitize_last_bot_interjection(group.get("last_bot_interjection")),
                    "group_wakeup_logs": self._group_wakeup_logs(group),
                    "slang_items": self._group_slang_items(group),
                    "formatted": {
                        "status": self.plugin._format_group_status(group),
                        "feedback": self.plugin._format_group_interjection_feedback(group),
                        "relationship_graph": self.plugin._format_group_relationship_graph_for_prompt(group),
                    },
                }
            )
            return self._ok(detail)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 获取群详情失败: {exc}", exc_info=True)
            return self._error(str(exc))
    async def update_group(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        group_id = str(payload.get("group_id", "")).strip()
        if not group_id:
            return self._error("缺少 group_id")
        try:
            async with self.plugin._data_lock:
                group = self.plugin._get_group(group_id)
                if "enabled" in payload:
                    group["enabled"] = bool(payload.get("enabled"))
                if payload.get("reset_interjection"):
                    group["last_interject_at"] = 0
                    group["interject_day"] = ""
                    group["interject_today"] = 0
                    group["last_bot_interjection"] = {}
                    group["interjection_feedback"] = {}
                if payload.get("clear_observation"):
                    enabled = bool(group.get("enabled", True))
                    group.clear()
                    group.update(
                        {
                            "enabled": enabled,
                            "group_id": group_id,
                            "message_count": 0,
                            "last_seen": 0,
                            "last_interject_at": 0,
                            "interject_day": "",
                            "interject_today": 0,
                            "recent_messages": [],
                            "members": {},
                            "slang_terms": [],
                            "slang_meanings": {},
                            "topic_signatures": [],
                            "topic_threads": [],
                            "group_episodes": [],
                            "relationship_edges": {},
                            "interjection_feedback": {},
                            "last_bot_interjection": {},
                            "last_speaker": {},
                            "atmosphere": {},
                            "last_summary_at": 0,
                            "last_episode_refresh_at": 0,
                            "last_slang_summary_at": 0,
                        }
                    )
                self.plugin._save_data_sync()
                snapshot = deepcopy(group)
            return self._ok(self._group_summary(group_id, snapshot))
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新群失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def delete_group(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        group_id = str(payload.get("group_id", "")).strip()
        if not group_id:
            return self._error("缺少 group_id")
        try:
            async with self.plugin._data_lock:
                groups = self.plugin.data.get("groups")
                if not isinstance(groups, dict):
                    groups = {}
                    self.plugin.data["groups"] = groups
                removed_group = groups.pop(group_id, None) is not None

                whitelist = [
                    str(item).strip()
                    for item in (getattr(self.plugin, "group_whitelist_ids", []) or [])
                    if str(item).strip() and str(item).strip() != group_id
                ]
                blacklist = [
                    str(item).strip()
                    for item in (getattr(self.plugin, "group_blacklist_ids", []) or [])
                    if str(item).strip() and str(item).strip() != group_id
                ]
                removed_whitelist = len(whitelist) != len(getattr(self.plugin, "group_whitelist_ids", []) or [])
                removed_blacklist = len(blacklist) != len(getattr(self.plugin, "group_blacklist_ids", []) or [])
                self._apply_config_value("group_whitelist_ids", whitelist, {"group_whitelist_ids": whitelist, "group_blacklist_ids": blacklist})
                self._apply_config_value("group_blacklist_ids", blacklist, {"group_whitelist_ids": whitelist, "group_blacklist_ids": blacklist})
                self.plugin._save_data_sync()

            config_saved = await self._save_config_if_possible()
            message_parts = []
            if removed_group:
                message_parts.append("已删除群聊观测")
            if removed_whitelist or removed_blacklist:
                message_parts.append("已移出群聊名单")
            message = "，".join(message_parts) if message_parts else "没有找到可删除的群聊记录"
            return self._ok(
                {
                    "group_id": group_id,
                    "removed_group": removed_group,
                    "removed_whitelist": removed_whitelist,
                    "removed_blacklist": removed_blacklist,
                    "config_saved": config_saved,
                    "message": message,
                }
            )
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 删除群失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def update_group_slang(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        group_id = str(payload.get("group_id", "")).strip()
        term = self._single_line(payload.get("term"), 40)
        if not group_id:
            return self._error("缺少 group_id")
        if not term:
            return self._error("缺少黑话词")
        try:
            async with self.plugin._data_lock:
                group = self.plugin._get_group(group_id)
                terms = group.setdefault("slang_terms", [])
                if not isinstance(terms, list):
                    terms = []
                    group["slang_terms"] = terms
                meanings = group.setdefault("slang_meanings", {})
                if not isinstance(meanings, dict):
                    meanings = {}
                    group["slang_meanings"] = meanings

                if payload.get("delete"):
                    group["slang_terms"] = [
                        item
                        for item in terms
                        if self._single_line(item.get("term") if isinstance(item, dict) else item, 40) != term
                    ]
                    meanings.pop(term, None)
                else:
                    existing_term = None
                    for item in terms:
                        if isinstance(item, dict) and self._single_line(item.get("term"), 40) == term:
                            existing_term = item
                            break
                    if existing_term is None:
                        existing_term = {"term": term, "count": 0, "last_seen": 0}
                        terms.append(existing_term)
                    previous = meanings.get(term) if isinstance(meanings.get(term), dict) else {}
                    confidence_raw = payload.get("confidence") if "confidence" in payload else previous.get("confidence", 0.85)
                    web_match_raw = payload.get("web_match") if "web_match" in payload else previous.get("web_match", 0.0)
                    confidence = max(0.0, min(1.0, self._float(confidence_raw)))
                    web_match = max(0.0, min(1.0, self._float(web_match_raw)))
                    meanings[term] = {
                        "meaning": self._single_line(payload.get("meaning"), 120),
                        "usage": self._single_line(payload.get("usage"), 120),
                        "type": self._single_line(payload.get("type"), 24),
                        "not_owner": self._single_line(payload.get("not_owner"), 90),
                        "evidence": self._single_line(payload.get("evidence"), 160),
                        "web_evidence": self._single_line(payload.get("web_evidence"), 220),
                        "confidence": f"{confidence:.2f}",
                        "web_match": f"{web_match:.2f}" if web_match > 0 else "",
                        "source": "manual",
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    }
                    terms.sort(key=lambda item: (_safe_int(item.get("count"), 0) if isinstance(item, dict) else 0), reverse=True)
                self.plugin._save_data_sync()
                snapshot = deepcopy(group)
            detail = self._group_summary(group_id, snapshot)
            detail["slang_items"] = self._group_slang_items(snapshot)
            return self._ok(detail)
        except Exception as exc:
            logger.error(f"[PrivateCompanionPage] 更新群黑话失败: {exc}", exc_info=True)
            return self._error(str(exc))
