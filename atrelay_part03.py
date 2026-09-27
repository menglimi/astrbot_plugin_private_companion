# -*- coding: utf-8 -*-
"""AtRelayPart03Mixin。

由 tools/split_mixin_domain.py 从 atrelay.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 255 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 AtRelayMixin）。
"""
from __future__ import annotations

from .atrelay_shared import logger
from .atrelay_shared import Any
from .atrelay_shared import AstrMessageEvent
from .atrelay_shared import At
from .atrelay_shared import MessageChain
from .atrelay_shared import Plain
from .atrelay_shared import _now_ts
from .atrelay_shared import _safe_float
from .atrelay_shared import _single_line
from .atrelay_shared import re
from .atrelay_shared import uuid



class AtRelayPart03Mixin:
    """AtRelayPart03Mixin（从 AtRelayMixin 拆出）。"""


    def _note_atrelay_send(
        self,
        kind: str,
        target: str,
        text: str,
        at_user: str = "",
        *,
        event: AstrMessageEvent | None = None,
        source_user: str = "",
        source_name: str = "",
    ) -> None:
        log = self._atrelay_send_log()
        normalized_text = self._normalize_atrelay_text(text, limit=300)
        log.append(
            {
                "ts": _now_ts(),
                "kind": kind,
                "target": str(target),
                "at_user": _single_line(at_user, 80),
                "signature": self._atrelay_send_signature(kind, target, normalized_text, at_user),
            }
        )
        del log[:-80]
        if event is not None and (not source_user or not source_name):
            event_source_user, event_source_name = self._atrelay_source_snapshot_for_event(event)
            source_user = source_user or event_source_user
            source_name = source_name or event_source_name
        self._note_atrelay_recent_context(
            kind=kind,
            target=target,
            text=normalized_text,
            at_user=at_user,
            source_user=source_user,
            source_name=source_name,
        )

    def _atrelay_receipt_tasks(self) -> list[dict[str, Any]]:
        tasks = self.data.setdefault("pending_atrelay_receipts", [])
        if not isinstance(tasks, list):
            tasks = []
            self.data["pending_atrelay_receipts"] = tasks
        now = _now_ts()
        kept = [
            item for item in tasks
            if isinstance(item, dict)
            and _safe_float(item.get("expires_at"), 0) > now
            and _single_line(item.get("status"), 24) in {"waiting_reply", "waiting_confirm"}
        ]
        if len(kept) != len(tasks):
            self.data["pending_atrelay_receipts"] = kept
        return kept

    def _atrelay_receipt_label_for_user(self, user_id: str, fallback: str = "") -> str:
        user_id = _single_line(user_id, 40)
        profile = self._worldbook_profile_by_user_id(user_id) if user_id else None
        if isinstance(profile, dict):
            name = _single_line(profile.get("name"), 40)
            if name and name != user_id:
                return name
        return _single_line(fallback, 40) or user_id or "对方"

    def _note_atrelay_private_receipt_task(
        self,
        event: AstrMessageEvent,
        *,
        target_user: str,
        target_name: str = "",
        question: str,
        sent_text: str,
        confirm_before_report: bool = False,
        expire_hours: Any = 12,
    ) -> dict[str, Any]:
        source_umo = _single_line(getattr(event, "unified_msg_origin", ""), 120)
        source_user = _single_line(self._atrelay_event_user_id(event), 40)
        source_name = self._atrelay_identity_label(source_user)
        platform = source_umo.split(":", 1)[0] if ":" in source_umo else (self.target_platform or "aiocqhttp")
        if not source_umo and source_user:
            source_umo = f"{platform}:FriendMessage:{source_user}"
        ttl = max(1.0, min(72.0, _safe_float(expire_hours, 12.0)))
        task = {
            "id": uuid.uuid4().hex[:16],
            "created_at": _now_ts(),
            "expires_at": _now_ts() + ttl * 3600,
            "status": "waiting_reply",
            "source_umo": source_umo,
            "source_user_id": source_user,
            "source_name": source_name,
            "target_user_id": _single_line(target_user, 40),
            "target_name": self._atrelay_receipt_label_for_user(target_user, target_name),
            "question": _single_line(question, 300),
            "sent_text": _single_line(sent_text, 300),
            "confirm_before_report": bool(confirm_before_report),
        }
        tasks = self._atrelay_receipt_tasks()
        tasks.append(task)
        del tasks[:-40]
        return task

    def _atrelay_receipt_confirmation_intent(self, text: str) -> str:
        cleaned = _single_line(text, 80)
        if not cleaned:
            return ""
        if re.search(r"(不行|不可以|别|不要|算了|别转|别说|不方便|拒绝|否)", cleaned):
            return "no"
        if re.search(r"^(可以|可|行|好|好的|嗯|嗯嗯|对|没事|转吧|说吧|告诉他|告诉她|发吧|ok|OK)[。！？!?\s]*$", cleaned):
            return "yes"
        return ""

    def _format_atrelay_receipt_report(self, task: dict[str, Any], reply_text: str) -> str:
        target = _single_line(task.get("target_name") or task.get("target_user_id"), 40) or "对方"
        question = _single_line(task.get("question") or task.get("sent_text"), 120)
        reply = _single_line(reply_text, 600)
        if question:
            return f"{target}回复你刚才让我问的「{question}」：{reply}"
        return f"{target}回复了：{reply}"

    async def _send_atrelay_receipt_to_source(self, task: dict[str, Any], text: str) -> bool:
        source_umo = _single_line(task.get("source_umo"), 120)
        if not source_umo:
            source_user = _single_line(task.get("source_user_id"), 40)
            if not source_user:
                return False
            platform = self.target_platform or "aiocqhttp"
            source_umo = f"{platform}:FriendMessage:{source_user}"
        await self.context.send_message(source_umo, MessageChain([Plain(text)]))
        return True

    async def _maybe_handle_atrelay_private_receipt_reply(
        self,
        event: AstrMessageEvent,
        user_id: str,
        sender_display_name: str,
        text: str,
    ) -> bool:
        if not getattr(self, "enable_atrelay_tools", True):
            return False
        target_user = _single_line(user_id, 40)
        cleaned = _single_line(text, 800)
        if not target_user or not cleaned:
            return False
        tasks = self._atrelay_receipt_tasks()
        task = None
        for item in tasks:
            if _single_line(item.get("target_user_id"), 40) == target_user:
                task = item
                break
        if not isinstance(task, dict):
            return False
        status = _single_line(task.get("status"), 24)
        if status == "waiting_confirm":
            intent = self._atrelay_receipt_confirmation_intent(cleaned)
            if not intent:
                return False
            tasks.remove(task)
            if intent == "yes":
                reply_text = _single_line(task.get("pending_reply_text"), 800)
                report = self._format_atrelay_receipt_report(task, reply_text)
                await self._send_atrelay_receipt_to_source(task, report)
                await self.context.send_message(event.unified_msg_origin, MessageChain([Plain("好，我帮你带回去了。")]))
            else:
                target = self._atrelay_receipt_label_for_user(target_user, sender_display_name)
                await self._send_atrelay_receipt_to_source(task, f"{target}回复了，但说不方便转回来。")
                await self.context.send_message(event.unified_msg_origin, MessageChain([Plain("好，那我不转回去。")]))
            self._save_data_sync(sections={"pending_atrelay_receipts"})
            try:
                event.stop_event()
            except Exception:
                pass
            return True
        if status != "waiting_reply":
            return False
        if bool(task.get("confirm_before_report")):
            task["status"] = "waiting_confirm"
            task["pending_reply_text"] = cleaned
            task["target_name"] = self._atrelay_receipt_label_for_user(target_user, sender_display_name)
            self._save_data_sync(sections={"pending_atrelay_receipts"})
            source_name = _single_line(task.get("source_name"), 30) or "对方"
            await self.context.send_message(
                event.unified_msg_origin,
                MessageChain([Plain(f"收到。我可以把你刚才这句转回给{source_name}吗？回“可以”或“不行”就好。")]),
            )
            try:
                event.stop_event()
            except Exception:
                pass
            return True
        tasks.remove(task)
        task["target_name"] = self._atrelay_receipt_label_for_user(target_user, sender_display_name)
        report = self._format_atrelay_receipt_report(task, cleaned)
        await self._send_atrelay_receipt_to_source(task, report)
        self._save_data_sync(sections={"pending_atrelay_receipts"})
        try:
            event.stop_event()
        except Exception:
            pass
        return True

    def _atrelay_target_resting_reason(self, user_id: str, *, now: float | None = None) -> str:
        target_user_id = _single_line(user_id, 40)
        if not target_user_id:
            return ""
        users = self.data.get("users", {})
        user = users.get(target_user_id) if isinstance(users, dict) else None
        if not isinstance(user, dict):
            return ""
        check_now = _now_ts() if now is None else now
        rest_until = self._user_rest_silence_until(user, now=check_now)
        if rest_until <= check_now:
            return ""
        reason = _single_line(user.get("user_rest_reason"), 80)
        until_text = self._environment_fromtimestamp(rest_until).strftime("%m-%d %H:%M")
        return f"目标用户明确在休息中（静默至 {until_text}" + (f"，原因：{reason}" if reason else "") + "）"

    def _pop_due_atrelay_tasks_for_sender(self, group_id: str, sender_id: str) -> list[dict[str, Any]]:
        group = self._get_group(group_id)
        tasks = group.get("pending_atrelay_tasks")
        if not isinstance(tasks, list) or not sender_id:
            return []
        now = _now_ts()
        due: list[dict[str, Any]] = []
        kept: list[dict[str, Any]] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            if _safe_float(task.get("expires_at"), 0) <= now:
                continue
            if str(task.get("target_user_id") or "") == str(sender_id):
                due.append(dict(task))
            else:
                kept.append(task)
        group["pending_atrelay_tasks"] = kept
        return due[:3]

    async def _dispatch_due_atrelay_tasks(self, event: AstrMessageEvent, group_id: str, sender_id: str) -> None:
        if not self.enable_atrelay_tools or not group_id or not sender_id:
            return
        async with self._data_lock:
            due = self._pop_due_atrelay_tasks_for_sender(group_id, sender_id)
            if due:
                self._save_data_sync(sections={"groups"})
        if not due:
            return
        platform = str(getattr(event, "unified_msg_origin", "") or "").split(":")[0] or self.target_platform or "aiocqhttp"
        target_umo = f"{platform}:GroupMessage:{group_id}"
        for task in due:
            text = _single_line(task.get("message"), 800)
            if not text:
                continue
            duplicate = self._atrelay_duplicate_guard("group", group_id, text, sender_id)
            if duplicate:
                logger.info("延迟转述重复拦截: group=%s user=%s", group_id, sender_id)
                continue
            try:
                await self.context.send_message(target_umo, MessageChain([At(qq=sender_id), Plain(" "), Plain(text)]))
                self._note_atrelay_send(
                    "group",
                    group_id,
                    text,
                    sender_id,
                    source_user=_single_line(task.get("source_user"), 40),
                    source_name=_single_line(task.get("source_name"), 80),
                )
                self._save_data_sync(sections={"groups", "recent_atrelay_contexts", "atrelay_send_log"})
            except Exception as exc:
                logger.warning("延迟转述发送失败: group=%s user=%s err=%s", group_id, sender_id, _single_line(exc, 160))
