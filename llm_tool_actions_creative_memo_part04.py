# -*- coding: utf-8 -*-
"""LlmToolActionsCreativeMemoPart04Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_creative_memo.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 344 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsCreativeMemoMixin）。
"""
from __future__ import annotations

import json
import time
import uuid
from .helpers import _safe_float, _single_line
from .llm_tool_actions_shared import logger
from .memo_notes import apply_memo_note_action, memo_note_sort_key, normalize_memo_note
from astrbot.api.event import AstrMessageEvent
from typing import Any



class LlmToolActionsCreativeMemoPart04Mixin:
    """LlmToolActionsCreativeMemoPart04Mixin（从 LlmToolActionsCreativeMemoMixin 拆出）。"""


    async def _pc_manage_memo_impl(
        self,
        event: AstrMessageEvent,
        *,
        action: str = "list",
        title: str = "",
        content: str = "",
        selector: str = "",
        due_at: Any = "",
        repeat: str = "",
        color: str = "",
        remind_enabled: bool | None = None,
        include_completed: bool = False,
        status: str = "",
        query: str = "",
        clear_due: bool = False,
        clear_content: bool = False,
        confirmation_token: str = "",
    ) -> str:
        allowed, requester_id = self._memo_tool_authorization(event)
        if not allowed:
            return json.dumps(
                {"status": "forbidden", "saved": False, "message": "便签只允许配置的主要用户在私聊中管理。"},
                ensure_ascii=False,
            )
        action_key = _single_line(action, 30).lower()
        aliases = {
            "": "list", "查看": "list", "列表": "list", "查询": "list", "list": "list",
            "详情": "get", "查看详情": "get", "get": "get",
            "新增": "create", "添加": "create", "创建": "create", "记录": "create", "create": "create", "add": "create",
            "修改": "update", "编辑": "update", "update": "update", "edit": "update",
            "完成": "complete", "办完": "complete", "complete": "complete", "done": "complete",
            "恢复": "reopen", "重新打开": "reopen", "reopen": "reopen",
            "删除": "delete", "delete": "delete", "remove": "delete",
            "取消删除": "cancel_delete", "cancel_delete": "cancel_delete", "cancel": "cancel_delete",
            "置顶": "pin", "pin": "pin", "取消置顶": "unpin", "unpin": "unpin",
        }
        action_key = aliases.get(action_key, action_key)
        if action_key not in {"list", "get", "create", "update", "complete", "reopen", "delete", "cancel_delete", "pin", "unpin"}:
            return json.dumps({"status": "invalid_action", "saved": False, "message": "不支持的便签操作"}, ensure_ascii=False)

        now = time.time()
        status_key = _single_line(status, 20).lower()
        status_aliases = {
            "": "all" if include_completed else "active",
            "active": "active", "进行中": "active", "未完成": "active", "待办": "active",
            "completed": "completed", "完成": "completed", "已完成": "completed", "历史": "completed",
            "all": "all", "全部": "all",
        }
        status_key = status_aliases.get(status_key, status_key)
        if status_key not in {"active", "completed", "all"}:
            return json.dumps({"status": "invalid_status", "saved": False, "message": "便签状态只支持 active/completed/all"}, ensure_ascii=False)
        if action_key == "list":
            async with self._data_lock:
                raw_notes = self.data.get("memo_notes")
                source_notes = raw_notes if isinstance(raw_notes, list) else []
                notes = [item for item in (normalize_memo_note(raw, now=now) for raw in source_notes) if item]
            if status_key != "all":
                notes = [item for item in notes if item.get("status") == status_key]
            query_text = _single_line(query, 100).casefold()
            if query_text:
                notes = [
                    item for item in notes
                    if query_text in f"{item.get('title', '')}\n{item.get('content', '')}".casefold()
                ]
            notes.sort(key=lambda item: memo_note_sort_key(item, now=now))
            items = [self._memo_tool_note_view(item, number=index) for index, item in enumerate(notes[:20], start=1)]
            return json.dumps(
                {
                    "status": "success",
                    "saved": False,
                    "action": "list",
                    "view": status_key,
                    "query": query_text,
                    "count": len(notes),
                    "shown_count": len(items),
                    "truncated": len(notes) > len(items),
                    "items": items,
                    "message": "当前没有便签" if not notes else f"找到 {len(notes)} 张便签",
                },
                ensure_ascii=False,
            )

        due_timestamp = 0.0
        if action_key == "create" or due_at not in (None, ""):
            due_timestamp, due_error = self._parse_memo_due_time(due_at, now=now)
            if due_error:
                return json.dumps({"status": "invalid_time", "saved": False, "message": due_error}, ensure_ascii=False)

        pending_store = getattr(self, "_memo_delete_confirmations", None)
        if not isinstance(pending_store, dict):
            pending_store = {}
            setattr(self, "_memo_delete_confirmations", pending_store)
        for token, pending in list(pending_store.items()):
            if not isinstance(pending, dict) or _safe_float(pending.get("expires_at"), 0.0) <= now:
                pending_store.pop(token, None)

        token = _single_line(confirmation_token, 100)
        if action_key == "cancel_delete":
            removable = [
                key for key, pending in pending_store.items()
                if isinstance(pending, dict)
                and pending.get("requester_id") == requester_id
                and (not token or key == token)
            ]
            for key in removable:
                pending_store.pop(key, None)
            return json.dumps(
                {
                    "status": "success" if removable else "nothing_pending",
                    "saved": False,
                    "cancelled": bool(removable),
                    "action": "cancel_delete",
                    "message": "已取消删除，便签没有变化。" if removable else "当前没有等待确认的便签删除。",
                },
                ensure_ascii=False,
            )

        confirmed_delete_id = ""
        confirmed_pending: dict[str, Any] | None = None
        if action_key == "delete" and token:
            pending = pending_store.get(token)
            if not isinstance(pending, dict) or pending.get("requester_id") != requester_id:
                return json.dumps({"status": "confirmation_expired", "saved": False, "message": "删除确认已失效，请重新指定便签。"}, ensure_ascii=False)
            confirmed_pending = pending
            confirmed_delete_id = _single_line(pending.get("note_id"), 64)

        try:
            async with self._data_lock:
                raw_notes = self.data.get("memo_notes")
                source_notes = raw_notes if isinstance(raw_notes, list) else []
                notes = [item for item in (normalize_memo_note(raw, now=now) for raw in source_notes) if item]
                notes.sort(key=lambda item: memo_note_sort_key(item, now=now))
                if action_key == "create":
                    payload: dict[str, Any] = {
                        "action": "save",
                        "title": title,
                        "content": content,
                        "due_at": due_timestamp,
                        "repeat": repeat or "none",
                        "color": color or "yellow",
                        "pinned": False,
                        "remind_enabled": True if remind_enabled is None else remind_enabled,
                    }
                    updated_notes, affected = apply_memo_note_action(
                        raw_notes,
                        payload,
                        now=now,
                        fromtimestamp=self._environment_fromtimestamp,
                    )
                else:
                    match_status = "" if status_key == "all" else status_key
                    if not status:
                        match_status = "completed" if action_key == "reopen" else "active" if action_key == "complete" else ""
                    matches = self._memo_tool_find_matches(
                        notes,
                        confirmed_delete_id or selector,
                        status=match_status,
                    )
                    if not matches:
                        return json.dumps({"status": "not_found", "saved": False, "message": "没有找到匹配的便签"}, ensure_ascii=False)
                    if len(matches) > 1:
                        return json.dumps(
                            {
                                "status": "ambiguous",
                                "saved": False,
                                "message": "匹配到多张便签，请用编号、完整标题或 id 进一步指定。",
                                "matches": [self._memo_tool_note_view(item) for item in matches[:8]],
                            },
                            ensure_ascii=False,
                        )
                    target = matches[0]
                    if action_key == "get":
                        return json.dumps(
                            {
                                "status": "success",
                                "saved": False,
                                "action": "get",
                                "note": self._memo_tool_note_view(target, content_limit=800),
                            },
                            ensure_ascii=False,
                        )
                    if confirmed_pending is not None and _safe_float(target.get("updated_at"), 0.0) != _safe_float(confirmed_pending.get("updated_at"), 0.0):
                        pending_store.pop(token, None)
                        return json.dumps(
                            {
                                "status": "confirmation_stale",
                                "saved": False,
                                "message": "便签在确认前发生了变化，请重新发起删除并确认。",
                                "note": self._memo_tool_note_view(target),
                            },
                            ensure_ascii=False,
                        )
                    if action_key == "delete" and not confirmed_delete_id:
                        token = uuid.uuid4().hex
                        pending_store[token] = {
                            "requester_id": requester_id,
                            "note_id": target.get("id"),
                            "updated_at": _safe_float(target.get("updated_at"), 0.0),
                            "expires_at": now + 180,
                        }
                        return json.dumps(
                            {
                                "status": "confirmation_required",
                                "saved": False,
                                "message": "这张便签尚未删除，请让用户回复“确认删除”或“取消删除”。",
                                "note": self._memo_tool_note_view(target),
                                "confirmation_token": token,
                                "expires_in_seconds": 180,
                            },
                            ensure_ascii=False,
                        )
                    payload = {"action": action_key, "id": target.get("id")}
                    partial = False
                    if action_key == "update":
                        payload["action"] = "save"
                        partial = True
                        if title:
                            payload["title"] = title
                        if content or clear_content:
                            payload["content"] = "" if clear_content else content
                        if due_at not in (None, "") or clear_due:
                            payload["due_at"] = 0.0 if clear_due else due_timestamp
                        if repeat:
                            payload["repeat"] = repeat
                        elif clear_due:
                            payload["repeat"] = "none"
                        if color:
                            payload["color"] = color
                        if remind_enabled is not None:
                            payload["remind_enabled"] = remind_enabled
                        if len(payload) <= 2:
                            return json.dumps({"status": "need_changes", "saved": False, "message": "没有提供要修改的内容"}, ensure_ascii=False)
                    updated_notes, affected = apply_memo_note_action(
                        raw_notes,
                        payload,
                        now=now,
                        fromtimestamp=self._environment_fromtimestamp,
                        partial=partial,
                    )

                previous_notes = raw_notes
                self.data["memo_notes"] = updated_notes
                try:
                    self._save_data_sync(sections={"memo_notes"})
                except Exception:
                    self.data["memo_notes"] = previous_notes
                    raise
            if action_key == "delete" and token:
                pending_store.pop(token, None)
            if (
                action_key in {"create", "update"}
                and isinstance(affected, dict)
                and _single_line(affected.get("status"), 20) == "active"
                and _safe_float(affected.get("due_at"), 0.0) > 0
                and bool(affected.get("remind_enabled", True))
            ):
                try:
                    setattr(event, "private_companion_memo_reminder_saved", True)
                except Exception as exc:
                    logger.warning(
                        "便签提醒已保存但无法写入本轮去重标记: user=%s error=%s",
                        requester_id,
                        _single_line(exc, 160),
                    )
                else:
                    logger.info(
                        "便签提醒已保存,本轮将抑制重复临时定时: user=%s note=%s action=%s",
                        requester_id,
                        _single_line(affected.get("id"), 64) or "-",
                        action_key,
                    )
            return json.dumps(
                {
                    "status": "success",
                    "saved": True,
                    "action": action_key,
                    "message": {
                        "create": "便签已新增",
                        "update": "便签已更新",
                        "complete": "便签已完成",
                        "reopen": "便签已恢复",
                        "delete": "便签已删除",
                        "pin": "便签已置顶",
                        "unpin": "已取消便签置顶",
                    }[action_key],
                    "note": self._memo_tool_note_view(affected),
                },
                ensure_ascii=False,
            )
        except ValueError as exc:
            return json.dumps({"status": "invalid", "saved": False, "message": str(exc)}, ensure_ascii=False)
        except Exception as exc:
            logger.error("聊天便签操作失败: %s", _single_line(exc, 160), exc_info=True)
            return json.dumps({"status": "error", "saved": False, "message": f"便签操作失败: {_single_line(exc, 120)}"}, ensure_ascii=False)

    async def _note_photo_tool_quota_attempt(
        self,
        event: AstrMessageEvent,
        *,
        requester_id: str,
        requester: dict[str, Any] | None,
        photo_scope: str,
        image_path: str = "",
    ) -> None:
        if not str(requester_id or "").strip():
            return

        def update_counters() -> bool:
            changed = False
            user = requester
            user_getter = getattr(self, "_get_user", None)
            if not isinstance(user, dict) and callable(user_getter):
                user = user_getter(requester_id)
            if photo_scope == "proactive":
                proactive_notifier = getattr(self, "_note_photo_generation_attempt", None)
                if callable(proactive_notifier):
                    proactive_notifier(requester_id, image_path=image_path)
                    changed = True
            else:
                command_notifier = getattr(self, "_note_command_photo_generation_attempt", None)
                if callable(command_notifier) and isinstance(user, dict):
                    command_notifier(user, image_path=image_path)
                    changed = True
            scope_notifier = getattr(self, "_note_photo_generation_scope_attempt", None)
            if callable(scope_notifier):
                scope_notifier(
                    event,
                    user=user if isinstance(user, dict) else None,
                    user_id=requester_id,
                    scope=photo_scope,
                )
                changed = True
            if changed:
                saver = getattr(self, "_save_data_sync", None)
                if callable(saver):
                    saver(sections={"users", "photo_generation_scope_attempts"})
            return changed

        data_lock = getattr(self, "_data_lock", None)
        if data_lock is not None:
            async with data_lock:
                update_counters()
        else:
            update_counters()
