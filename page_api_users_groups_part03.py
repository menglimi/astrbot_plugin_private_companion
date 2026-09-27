# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiUsersGroupsPart03Mixin。

由 tools/split_mixin_domain.py 从 page_api_users_groups.py 机械抽取（21 个方法 + 0 个模块级名字 + 0 个类级赋值 / 575 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiUsersGroupsMixin）。
"""
from __future__ import annotations

import re
import time
from .page_api_shared import _page_api_host_request as request
from .page_api_users_groups_shared import logger
from copy import deepcopy
from typing import Any



class PrivateCompanionPageApiUsersGroupsPart03Mixin:
    """PrivateCompanionPageApiUsersGroupsPart03Mixin（从 PrivateCompanionPageApiUsersGroupsMixin 拆出）。"""


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
                stored_user_id = canonical_user_id if canonical_user_id in users else user_id
                existing_user = users.get(stored_user_id)
                if (
                    isinstance(existing_user, dict)
                    and self._single_line(existing_user.get("unified_person_id"), 80)
                ):
                    status = self._identity_archive_status()
                    if not status["ready"]:
                        return self._error(
                            "该用户已属于统一人物，当前无法归档。"
                            + status["reason"] + status["recovery"]
                        )
                    return self._error(
                        "该用户已属于统一人物，请在“身份与隔离”中预览并归档，不能绕过统一数据链直接删除"
                    )
                removed_ids = {user_id, canonical_user_id, stored_user_id}
                removed_user = users.pop(stored_user_id, None)
                if isinstance(removed_user, dict):
                    for alias_id in removed_user.get("alias_user_ids") if isinstance(removed_user.get("alias_user_ids"), list) else []:
                        alias_text = str(alias_id or "").strip()
                        if alias_text:
                            removed_ids.add(alias_text)
                removed_ids = {item for item in removed_ids if item}
                merge_backups = self.plugin.data.get("private_user_alias_merge_backups")
                merge_backups_changed = False
                if isinstance(merge_backups, dict):
                    for removed_id in removed_ids:
                        if removed_id in merge_backups:
                            merge_backups.pop(removed_id, None)
                            merge_backups_changed = True

                def keep_expression_scope_ids(raw_values: Any) -> list[str]:
                    kept: list[str] = []
                    for item in self._normalize_id_list(raw_values or []):
                        canonical_item_getter = getattr(self.plugin, "_expression_private_scope_id", None)
                        canonical_item = canonical_item_getter(item) if callable(canonical_item_getter) else item
                        if item in removed_ids or canonical_item in removed_ids:
                            continue
                        if item not in kept:
                            kept.append(item)
                    return kept

                old_expression_learning_ids = self._normalize_id_list(
                    getattr(self.plugin, "expression_private_learning_source_ids", []) or []
                )
                old_expression_application_ids = self._normalize_id_list(
                    getattr(self.plugin, "expression_private_application_user_ids", []) or []
                )
                expression_learning_ids = keep_expression_scope_ids(old_expression_learning_ids)
                expression_application_ids = keep_expression_scope_ids(old_expression_application_ids)
                removed_expression_scope = (
                    expression_learning_ids != old_expression_learning_ids
                    or expression_application_ids != old_expression_application_ids
                )

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
                    "expression_private_learning_source_ids": expression_learning_ids,
                    "expression_private_application_user_ids": expression_application_ids,
                }
                self._apply_config_value("target_user_ids", target_user_ids, overrides)
                self._apply_config_value("private_user_aliases", alias_text, overrides)
                self._apply_config_value("private_user_delivery_aliases", delivery_alias_text, overrides)
                self._apply_config_value("expression_private_learning_source_ids", expression_learning_ids, overrides)
                self._apply_config_value("expression_private_application_user_ids", expression_application_ids, overrides)
                save_sections: set[str] = set()
                if removed_user is not None:
                    save_sections.add("users")
                if merge_backups_changed:
                    save_sections.add("private_user_alias_merge_backups")
                if removed_user is not None or removed_expression_scope:
                    voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if callable(voice_refresher):
                        voice_refresher()
                        save_sections.add("expression_voice_profile")
                if save_sections:
                    self.plugin._save_data_sync(sections=save_sections)

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
            if removed_expression_scope:
                message_parts.append("已清理表达学习范围")
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
                    "removed_expression_scope": removed_expression_scope,
                    "config_saved": config_saved,
                    "message": message,
                }
            )
        except Exception as exc:
            logger.error(f"删除用户失败: {exc}", exc_info=True)
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
            items = [
                self._group_summary(group_id, self._refresh_group_atmosphere_for_page(group))
                for group_id, group in visible_groups
            ]
            items.sort(key=lambda item: item.get("last_seen_ts") or 0, reverse=True)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if elapsed_ms > 1200:
                logger.warning("群列表接口耗时较高: elapsed=%sms groups=%s", elapsed_ms, len(items))
            return self._ok({"items": items[:limit], "total": len(items), "shadow_total": shadow_count})
        except Exception as exc:
            logger.error(f"获取群列表失败: {exc}", exc_info=True)
            return self._error(str(exc))

    def _refresh_group_atmosphere_for_page(self, group: dict[str, Any]) -> dict[str, Any]:
        updater = getattr(self.plugin, "_update_group_atmosphere", None)
        if callable(updater):
            try:
                updater(group)
            except Exception as exc:
                logger.info(
                    "群气氛读取时重算失败: %s",
                    self._single_line(exc, 120),
                )
        return group

    def _group_page_identity_names(self, group: dict[str, Any]) -> dict[str, str]:
        """Project relationship-network names onto group members for page display only."""
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        resolver = getattr(self.plugin, "_group_member_identity_name", None)
        profile_getter = getattr(self.plugin, "_worldbook_profile_by_user_id", None)
        if not callable(resolver) or not callable(profile_getter):
            return {}
        names: dict[str, str] = {}
        for user_id, raw_member in members.items():
            uid = self._single_line(user_id, 40)
            if not uid or not isinstance(raw_member, dict):
                continue
            fallback = self._single_line(
                raw_member.get("display_name")
                or raw_member.get("nickname")
                or raw_member.get("name")
                or raw_member.get("card")
                or uid,
                40,
            )
            try:
                profile = profile_getter(uid, include_observation=True)
                if not isinstance(profile, dict):
                    continue
                identity_name = self._single_line(resolver(uid, fallback, limit=40), 40)
            except Exception:
                identity_name = ""
            if identity_name and identity_name != uid:
                raw_member["identity_name"] = identity_name
                names[uid] = identity_name
        return names

    def _group_page_recent_messages(
        self,
        group: dict[str, Any],
        identity_names: dict[str, str],
    ) -> list[dict[str, Any]]:
        items = self._limited_list(group.get("recent_messages"), 30)
        projected: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            sender_id = self._single_line(item.get("sender_id") or item.get("user_id") or item.get("qq"), 40)
            if sender_id in identity_names:
                item["identity_name"] = identity_names[sender_id]
            projected.append(item)
        return projected

    def _group_page_recent_bot_replies(self, group: dict[str, Any]) -> list[dict[str, Any]]:
        items = self._limited_list(group.get("recent_bot_replies"), 30)
        projected: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item: dict[str, Any] = {
                "ts": raw.get("ts", 0),
                "text": self._display_message_text(raw.get("text"), 500),
                "reply_to_id": self._single_line(
                    raw.get("reply_to_id") or raw.get("sender_id"),
                    80,
                ),
                "kind": self._single_line(raw.get("kind"), 40) or "bot_reply",
                "talking_to_bot": bool(raw.get("talking_to_bot")),
            }
            for key in ("message_id", "delivery_id"):
                value = self._single_line(raw.get(key), 160)
                if value:
                    item[key] = value
            projected.append(item)
        return projected

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
        manual_name = self._single_line(group.get("manual_group_name"), 80)
        if manual_name:
            return False
        name = self._single_line(group.get("name") or group.get("group_name") or group.get("display_name"), 80)
        gid = str(group_id or group.get("group_id") or "").strip()
        return not name or name == gid or name == f"群 {gid}" or name.isdigit()

    def _clean_manual_group_display_name(self, value: Any, group_id: str = "") -> str:
        text = self._single_line(value, 80)
        gid = str(group_id or "").strip()
        if not text or text == gid or text == f"群 {gid}":
            return ""
        return text

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
        # QQ Official group_openid values are opaque and cannot be queried through
        # OneBot's get_group_list/get_group_info API. Their names stay event/manual driven.
        platform_target_ids = {group_id for group_id, _ in missing if group_id.isdigit()}
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
            logger.info("群列表名称刷新失败: %s", self._single_line(exc, 120))
        if len(found) < len(target_ids) and platform_target_ids:
            for group_id, _ in [item for item in missing if item[0] in platform_target_ids][:30]:
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
                if self._single_line(group.get("manual_group_name"), 80):
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
                self.plugin._save_data_sync(sections={"groups"})

    async def get_group(self) -> dict[str, Any]:
        group_id = self._normalize_page_group_id(request.args.get("group_id", ""))
        if not group_id:
            return self._error("缺少 group_id")
        try:
            async with self.plugin._data_lock:
                group = deepcopy((self.plugin.data.get("groups") or {}).get(group_id))
            if not isinstance(group, dict):
                return self._error("群不存在")
            await self._refresh_group_names_from_platform([(group_id, group)], force=True)
            self._refresh_group_atmosphere_for_page(group)
            identity_names = self._group_page_identity_names(group)
            detail = self._group_summary(group_id, group)
            safety_getter = getattr(self.plugin, "_group_member_safety_compact_summary", None)
            member_safety = safety_getter(group) if callable(safety_getter) else {}
            detail.update(
                {
                    "members": group.get("members") if isinstance(group.get("members"), dict) else {},
                    "recent_messages": self._group_page_recent_messages(group, identity_names),
                    "recent_bot_replies": self._group_page_recent_bot_replies(group),
                    "topic_threads": self._group_topic_thread_items(group),
                    "group_episodes": self._limited_list(group.get("group_episodes"), 12),
                    "relationship_edges": group.get("relationship_edges") if isinstance(group.get("relationship_edges"), dict) else {},
                    "interjection_feedback": group.get("interjection_feedback") if isinstance(group.get("interjection_feedback"), dict) else {},
                    "last_bot_interjection": self._sanitize_last_bot_interjection(group.get("last_bot_interjection")),
                    "group_wakeup_logs": self._group_wakeup_logs(group),
                    "slang_items": self._group_slang_items(group),
                    "member_safety": member_safety,
                    "formatted": {
                        "status": self.plugin._format_group_status(group),
                        "feedback": self.plugin._format_group_interjection_feedback(group),
                        "relationship_graph": self.plugin._format_group_relationship_graph_for_prompt(group),
                    },
                }
            )
            return self._ok(detail)
        except Exception as exc:
            logger.error(f"获取群详情失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def get_group_member_safety(self) -> dict[str, Any]:
        group_id = self._normalize_page_group_id(request.args.get("group_id", ""))
        if not group_id:
            return self._error("缺少 group_id")
        try:
            async with self.plugin._data_lock:
                group = deepcopy((self.plugin.data.get("groups") or {}).get(group_id))
            if not isinstance(group, dict):
                return self._error("群不存在")
            getter = getattr(self.plugin, "_group_member_safety_summary", None)
            if not callable(getter):
                return self._error("当前插件版本不支持成员风控")
            summary = getter(group)
            summary["group_id"] = group_id
            summary["group_name"] = self._single_line(
                group.get("manual_group_name") or group.get("name") or group.get("group_name") or group.get("display_name"),
                80,
            )
            return self._ok(summary)
        except Exception as exc:
            logger.error(f"获取成员风控失败: {exc}", exc_info=True)
            return self._error(str(exc))
