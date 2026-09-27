# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiUsersGroupsPart04Mixin。

由 tools/split_mixin_domain.py 从 page_api_users_groups.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 314 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiUsersGroupsMixin）。
"""
from __future__ import annotations

import time
from .helpers import _safe_int
from .page_api_shared import _page_api_host_request as request
from .page_api_users_groups_shared import logger
from copy import deepcopy
from datetime import datetime
from typing import Any



class PrivateCompanionPageApiUsersGroupsPart04Mixin:
    """PrivateCompanionPageApiUsersGroupsPart04Mixin（从 PrivateCompanionPageApiUsersGroupsMixin 拆出）。"""


    async def update_group_member_safety(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        group_id = self._normalize_page_group_id(payload.get("group_id", ""))
        user_id = str(payload.get("user_id", "")).strip()
        action = str(payload.get("action", "")).strip().lower()
        if not group_id:
            return self._error("缺少 group_id")
        if not user_id:
            return self._error("缺少成员 ID")
        if action not in {"manual_block", "unblock", "clear_strikes", "exempt", "unexempt"}:
            return self._error("不支持的成员风控操作")
        try:
            async with self.plugin._data_lock:
                group = self.plugin._get_group(group_id)
                profiles = group.get("members") if isinstance(group.get("members"), dict) else {}
                profile = profiles.get(user_id) if isinstance(profiles.get(user_id), dict) else {}
                name = self._single_line(payload.get("name") or profile.get("name") or profile.get("identity_name"), 60)
                updater = getattr(self.plugin, "_apply_group_member_safety_action", None)
                getter = getattr(self.plugin, "_group_member_safety_summary", None)
                if not callable(updater) or not callable(getter):
                    return self._error("当前插件版本不支持成员风控")
                item = updater(group, user_id=user_id, action=action, name=name)
                self.plugin._save_data_sync(sections={"groups"})
                summary = getter(group)
            return self._ok({"item": item, "summary": summary})
        except ValueError as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.error(f"更新成员风控失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def update_group(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        group_id = self._normalize_page_group_id(payload.get("group_id", ""))
        if not group_id:
            return self._error("缺少 group_id")
        try:
            async with self.plugin._data_lock:
                group = self.plugin._get_group(group_id)
                if "group_name" in payload or "name" in payload:
                    previous_manual_name = self._single_line(group.get("manual_group_name"), 80)
                    manual_name = self._clean_manual_group_display_name(
                        payload.get("group_name") if "group_name" in payload else payload.get("name"),
                        group_id,
                    )
                    if manual_name:
                        group["manual_group_name"] = manual_name
                        group["name"] = manual_name
                        group["group_name"] = manual_name
                        group["group_name_source"] = "manual"
                        group["manual_group_name_updated_at"] = time.time()
                    else:
                        group["manual_group_name"] = ""
                        group.pop("group_name_source", None)
                        group.pop("manual_group_name_updated_at", None)
                        if previous_manual_name and self._single_line(group.get("name"), 80) == previous_manual_name:
                            group.pop("name", None)
                        if previous_manual_name and self._single_line(group.get("group_name"), 80) == previous_manual_name:
                            group.pop("group_name", None)
                if "enabled" in payload:
                    group["enabled"] = bool(payload.get("enabled"))
                if payload.get("reset_interjection"):
                    group["last_interject_at"] = 0
                    group["interject_day"] = ""
                    group["interject_today"] = 0
                    group["last_bot_interjection"] = {}
                    group["interjection_feedback"] = {}
                if payload.get("reset_atmosphere"):
                    current_atmosphere = group.get("atmosphere") if isinstance(group.get("atmosphere"), dict) else {}
                    group["atmosphere"] = {**current_atmosphere, "reset_at": time.time()}
                    updater = getattr(self.plugin, "_update_group_atmosphere", None)
                    if callable(updater):
                        updater(group)
                    else:
                        group["atmosphere"].update(
                            {
                                "pace": "安静",
                                "mood": "平稳",
                                "active_speakers": 0,
                                "recent_count": 0,
                                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            }
                        )
                if payload.get("clear_observation"):
                    enabled = bool(group.get("enabled", True))
                    manual_group_name = self._single_line(group.get("manual_group_name"), 80)
                    group.clear()
                    group.update(
                        {
                            "enabled": enabled,
                            "group_id": group_id,
                            "manual_group_name": manual_group_name,
                            "name": manual_group_name,
                            "group_name": manual_group_name,
                            "group_name_source": "manual" if manual_group_name else "",
                            "message_count": 0,
                            "last_seen": 0,
                            "last_interject_at": 0,
                            "interject_day": "",
                            "interject_today": 0,
                            "recent_messages": [],
                            "recent_bot_replies": [],
                            "members": {},
                            "member_safety": {},
                            "slang_terms": [],
                            "slang_meanings": {},
                            "topic_signatures": [],
                            "topic_threads": [],
                            "group_episodes": [],
                            "relationship_edges": {},
                            "interjection_feedback": {},
                            "last_bot_interjection": {},
                            "last_speaker": {},
                            "active_bot_conversation": {},
                            "atmosphere": {},
                            "last_summary_at": 0,
                            "last_episode_refresh_at": 0,
                            "last_slang_summary_at": 0,
                        }
                    )
                    voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if callable(voice_refresher):
                        voice_refresher()
                save_sections = {"groups"}
                if payload.get("clear_observation") and callable(
                    getattr(self.plugin, "_refresh_expression_voice_profile", None)
                ):
                    save_sections.add("expression_voice_profile")
                self.plugin._save_data_sync(sections=save_sections)
                snapshot = deepcopy(group)
            return self._ok(self._group_summary(group_id, snapshot))
        except Exception as exc:
            logger.error(f"更新群失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def delete_group(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        group_id = self._normalize_page_group_id(payload.get("group_id", ""))
        if not group_id:
            return self._error("缺少 group_id")
        try:
            resetter = getattr(self.plugin, "reset_group_scoped_data", None)
            if callable(resetter):
                reset_result = await resetter(group_id)
                if not reset_result.get("ok"):
                    return self._error(str(reset_result.get("code") or "群聊分域清理失败"))
                if reset_result.get("state") != "not_required":
                    message_parts = []
                    if reset_result.get("removed_group"):
                        message_parts.append("已删除群聊观测")
                    if reset_result.get("removed_whitelist") or reset_result.get("removed_blacklist"):
                        message_parts.append("已移出群聊名单")
                    if reset_result.get("removed_expression_scope"):
                        message_parts.append("已清理表达学习范围")
                    return self._ok({
                        "group_id": group_id,
                        "removed_group": bool(reset_result.get("removed_group")),
                        "removed_whitelist": bool(reset_result.get("removed_whitelist")),
                        "removed_blacklist": bool(reset_result.get("removed_blacklist")),
                        "removed_expression_scope": bool(reset_result.get("removed_expression_scope")),
                        "config_saved": bool(reset_result.get("config_saved")),
                        "scoped_cleanup": reset_result.get("scoped_cleanup") or {},
                        "operation_id": str(reset_result.get("operation_id") or ""),
                        "message": "，".join(message_parts) if message_parts else "没有找到可删除的群聊记录",
                    })
            async with self.plugin._data_lock:
                groups = self.plugin.data.get("groups")
                groups_repaired = not isinstance(groups, dict)
                if not isinstance(groups, dict):
                    groups = {}
                    self.plugin.data["groups"] = groups
                removed_group = groups.pop(group_id, None) is not None

                old_expression_learning_ids = self._normalize_id_list(
                    getattr(self.plugin, "expression_group_learning_source_ids", []) or []
                )
                old_expression_application_ids = self._normalize_id_list(
                    getattr(self.plugin, "expression_group_application_ids", []) or []
                )
                expression_learning_ids = [
                    item
                    for item in old_expression_learning_ids
                    if self._normalize_page_group_id(item) != group_id
                ]
                expression_application_ids = [
                    item
                    for item in old_expression_application_ids
                    if self._normalize_page_group_id(item) != group_id
                ]
                removed_expression_scope = (
                    expression_learning_ids != old_expression_learning_ids
                    or expression_application_ids != old_expression_application_ids
                )

                whitelist = [
                    str(item).strip()
                    for item in (getattr(self.plugin, "group_whitelist_ids", []) or [])
                    if str(item).strip() and self._normalize_page_group_id(item) != group_id
                ]
                blacklist = [
                    str(item).strip()
                    for item in (getattr(self.plugin, "group_blacklist_ids", []) or [])
                    if str(item).strip() and self._normalize_page_group_id(item) != group_id
                ]
                removed_whitelist = len(whitelist) != len(getattr(self.plugin, "group_whitelist_ids", []) or [])
                removed_blacklist = len(blacklist) != len(getattr(self.plugin, "group_blacklist_ids", []) or [])
                self._apply_config_value("group_whitelist_ids", whitelist, {"group_whitelist_ids": whitelist, "group_blacklist_ids": blacklist})
                self._apply_config_value("group_blacklist_ids", blacklist, {"group_whitelist_ids": whitelist, "group_blacklist_ids": blacklist})
                expression_overrides = {
                    "expression_group_learning_source_ids": expression_learning_ids,
                    "expression_group_application_ids": expression_application_ids,
                }
                self._apply_config_value("expression_group_learning_source_ids", expression_learning_ids, expression_overrides)
                self._apply_config_value("expression_group_application_ids", expression_application_ids, expression_overrides)
                save_sections: set[str] = set()
                if removed_group or groups_repaired:
                    save_sections.add("groups")
                if removed_group or removed_expression_scope:
                    voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if callable(voice_refresher):
                        voice_refresher()
                        save_sections.add("expression_voice_profile")
                if save_sections:
                    self.plugin._save_data_sync(sections=save_sections)

            config_saved = await self._save_config_if_possible()
            message_parts = []
            if removed_group:
                message_parts.append("已删除群聊观测")
            if removed_whitelist or removed_blacklist:
                message_parts.append("已移出群聊名单")
            if removed_expression_scope:
                message_parts.append("已清理表达学习范围")
            message = "，".join(message_parts) if message_parts else "没有找到可删除的群聊记录"
            return self._ok(
                {
                    "group_id": group_id,
                    "removed_group": removed_group,
                    "removed_whitelist": removed_whitelist,
                    "removed_blacklist": removed_blacklist,
                    "removed_expression_scope": removed_expression_scope,
                    "config_saved": config_saved,
                    "scoped_cleanup": {
                        "ok": True, "code": "scoped_group_erase_not_required", "count": 0,
                    },
                    "message": message,
                }
            )
        except Exception as exc:
            logger.error(f"删除群失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def update_group_slang(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        group_id = self._normalize_page_group_id(payload.get("group_id", ""))
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
                budget_removed = self.plugin._enforce_group_slang_meanings_budget(group)
                if budget_removed:
                    logger.info("[PrivateCompanionPage] 已按预算收缩群黑话释义: group=%s removed=%s", group_id, budget_removed)
                self.plugin._save_data_sync(sections={"groups"})
                snapshot = deepcopy(group)
            detail = self._group_summary(group_id, snapshot)
            detail["slang_items"] = self._group_slang_items(snapshot)
            return self._ok(detail)
        except Exception as exc:
            logger.error(f"更新群黑话失败: {exc}", exc_info=True)
            return self._error(str(exc))
