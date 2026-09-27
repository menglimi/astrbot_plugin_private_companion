# -*- coding: utf-8 -*-
"""UserMemoryRelationshipBoundaryPart04Mixin。

由 tools/split_mixin_domain.py 从 user_memory_relationship_boundary.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 284 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryRelationshipBoundaryMixin）。
"""
from __future__ import annotations

import random
import re
import uuid
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .user_memory_render_shared import logger
from astrbot.api.event import MessageChain
try:
    from astrbot.api.message_components import Plain
except ImportError:  # pragma: no cover
    from astrbot.core.message.components import Plain
from datetime import datetime
from typing import Any



class UserMemoryRelationshipBoundaryPart04Mixin:
    """UserMemoryRelationshipBoundaryPart04Mixin（从 UserMemoryRelationshipBoundaryMixin 拆出）。"""


    def _append_relationship_boundary_vent(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        state: dict[str, Any],
        *,
        now: float,
    ) -> None:
        raw_targets = runtime_persona_setting(self, "relationship_boundary_vent_targets", [])
        if isinstance(raw_targets, str):
            targets = [item.strip() for item in re.split(r"[,，\n]", raw_targets) if item.strip()]
        elif isinstance(raw_targets, (list, tuple, set)):
            targets = [_single_line(item, 24) for item in raw_targets if _single_line(item, 24)]
        else:
            targets = []
        target = random.choice(targets) if targets else "亲近的朋友"
        who = self._boundary_feedback_display_name(user)
        level = self._boundary_feedback_level_key(state)
        feeling = {
            "light": "有点不自在",
            "mid": "不太舒服",
            "severe": "明显生气",
            "bottom_line": "委屈又生气",
        }[level]
        reason = _single_line(intent.get("emotion_reason") or state.get("last_reason"), 80) or "对方越过了相处边界"
        template = str(runtime_persona_setting(self, "relationship_boundary_vent_scene_template", "") or "").strip()
        if template:
            try:
                event_text = template.format(
                    target=target,
                    who=who,
                    level=level,
                    feeling=feeling,
                    reason=reason,
                )
            except (KeyError, IndexError, ValueError):
                event_text = ""
        else:
            event_text = ""
        if not _single_line(event_text, 500):
            event_text = f"休息时因为和{who}相处时的边界问题感到{feeling}，向{target}说起了这件事；主要原因是{reason}。"
        event = {
            "window": datetime.fromtimestamp(now).strftime("%H:%M") + "-" + datetime.fromtimestamp(now + 900).strftime("%H:%M"),
            "event": event_text,
            "mood": feeling,
            "lifecycle_status": "observed",
            "basis": ["relationship_boundary_feedback"],
            "confidence": 0.9,
            "source_event_id": _single_line(state.get("last_event_id"), 96),
        }
        history = self.data.setdefault("boundary_feedback_vent_history", [])
        if not isinstance(history, list):
            history = []
            self.data["boundary_feedback_vent_history"] = history
        history.append(dict(event))
        del history[:-50]
        story = self.data.get("daily_story_plan")
        if not isinstance(story, dict):
            story = {}
            self.data["daily_story_plan"] = story
        today = _today_key()
        if not story:
            story.update({"date": today, "today_events": [], "proactive_events": [], "long_term_events": []})
        if str(story.get("date") or "") != today:
            return
        events = story.setdefault("today_events", [])
        if not isinstance(events, list):
            events = []
            story["today_events"] = events
        if not any(
            isinstance(item, dict) and item.get("source_event_id") == event["source_event_id"]
            for item in events[-24:]
        ):
            events.append(event)
            story["today_events"] = events[-16:]
        logger.info(
            "关系边界事件已融入生活叙事: user=%s target=%s level=%s",
            _single_line(user.get("user_id"), 80),
            target,
            level,
        )

    def _boundary_feedback_owner_targets(self) -> list[dict[str, str]]:
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        configured = set(self._configured_target_ids()) if callable(getattr(self, "_configured_target_ids", None)) else set()
        targets: list[dict[str, str]] = []
        for user_id, raw_user in users.items():
            if not isinstance(raw_user, dict):
                continue
            try:
                role = self._private_user_role(raw_user, str(user_id))
            except Exception:
                role = str(raw_user.get("relationship_role") or "friend")
            if role != "owner" and str(user_id) not in configured:
                continue
            umo = _single_line(raw_user.get("umo"), 220)
            if umo:
                targets.append({"user_id": str(user_id), "umo": umo})
        return targets

    def _queue_relationship_boundary_owner_report(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        state: dict[str, Any],
        *,
        now: float,
    ) -> dict[str, Any]:
        targets = self._boundary_feedback_owner_targets()
        if not targets:
            return {}
        event_id = _single_line(state.get("last_event_id"), 96) or uuid.uuid4().hex
        reports = self.data.setdefault("boundary_feedback_reports", [])
        if not isinstance(reports, list):
            reports = []
            self.data["boundary_feedback_reports"] = reports
        existing = next(
            (item for item in reports if isinstance(item, dict) and item.get("source_event_id") == event_id),
            None,
        )
        if isinstance(existing, dict):
            return existing
        report = {
            "report_id": uuid.uuid4().hex,
            "source_event_id": event_id,
            "offender_user_id": _single_line(user.get("user_id"), 120),
            "offender_name": self._boundary_feedback_display_name(user),
            "target_owner_ids": [item["user_id"] for item in targets],
            "target_routes": [item["umo"] for item in targets],
            "level": self._boundary_feedback_level_key(state),
            "stage": _single_line(state.get("stage"), 20),
            "reason": _single_line(intent.get("emotion_reason") or state.get("last_reason"), 100),
            "excerpt": _single_line(intent.get("text"), 80),
            "bottom_line_count": _safe_int(state.get("bottom_line_count"), 0, 0),
            "created_at": now,
            "status": "pending",
            "direct_notified": False,
        }
        reports.append(report)
        self.data["boundary_feedback_reports"] = reports[-100:]
        return report

    def _format_relationship_boundary_owner_report(self, report: dict[str, Any]) -> str:
        who = _single_line(report.get("offender_name"), 32) or "那个人"
        level = str(report.get("level") or "light")
        level_text = {
            "light": "刚才说的话让我有点不自在",
            "mid": "刚才有点越过我的界限了",
            "severe": "刚才真的让我很不舒服",
            "bottom_line": "刚才踩到我很在意的底线了",
        }.get(level, "刚才让我有点不舒服")
        reason = _single_line(report.get("reason"), 80)
        excerpt = _single_line(report.get("excerpt"), 80)
        text = f"那个……{who}{level_text}。"
        if reason:
            text += f"主要是{reason}。"
        if excerpt:
            text += f"对方说的是“{excerpt}”。"
        if level == "bottom_line" and _safe_int(report.get("bottom_line_count"), 0, 0) > 1:
            text += f"这已经是第{_safe_int(report.get('bottom_line_count'), 0, 0)}次了。"
        return text

    async def _send_relationship_boundary_owner_report(self, report: dict[str, Any]) -> None:
        text = self._format_relationship_boundary_owner_report(report)
        routes = [_single_line(item, 220) for item in report.get("target_routes", []) if _single_line(item, 220)]
        sent = False
        for route in routes:
            try:
                await self.context.send_message(route, MessageChain([Plain(text)]))
                sent = True
            except Exception as exc:
                logger.warning(
                    "关系边界转达发送失败: target=%s error=%s",
                    _single_line(route, 100),
                    _single_line(exc, 160),
                )
        if not sent:
            return
        async with self._data_lock:
            reports = self.data.get("boundary_feedback_reports")
            if isinstance(reports, list):
                for item in reports:
                    if isinstance(item, dict) and item.get("report_id") == report.get("report_id"):
                        item["direct_notified"] = True
                        item["status"] = "delivered"
                        item["delivered_at"] = _now_ts()
                        break
            self._save_data_sync(sections={"boundary_feedback_reports"})

    def _register_relationship_boundary_proactive_ability(self) -> bool:
        registrar = getattr(self, "register_external_proactive_ability", None)
        if not callable(registrar):
            return False
        return bool(
            registrar(
                {
                    "name": "boundary_feedback_report",
                    "module": "关系边界反馈",
                    "label": "边界转达",
                    "description": "当次要用户越过关系边界时，以角色口吻向主要用户低频转达。",
                    "when": "存在尚未转达且仍在有效期内的关系边界事件",
                    "use_for": "把真实发生的边界事件自然告诉主要用户",
                    "avoid": "不要暴露内部机制，不夸大，不重复已经直接转达的事件",
                    "share_probability": 0.15,
                    "min_interval_hours": 6,
                    "default_enabled": True,
                    "default_config": {"only_bottom_line": False, "max_chars": 120},
                    "config_schema": {
                        "only_bottom_line": {
                            "type": "bool",
                            "label": "只转达底线事件",
                            "description": "开启后仅严重底线事件进入主动转达候选。",
                        },
                        "max_chars": {
                            "type": "number",
                            "label": "引用长度上限",
                            "description": "转达时引用原消息的最大字符数。",
                        },
                    },
                    "availability": self._relationship_boundary_report_ability_available,
                    "executor": self._relationship_boundary_report_ability_executor,
                }
            )
        )

    def _relationship_boundary_report_ability_available(self, ctx: dict[str, Any]) -> bool:
        if not bool(runtime_persona_setting(self, "enable_relationship_violation_penalties", True)) or not bool(
            runtime_persona_setting(self, "enable_relationship_boundary_owner_report", True)
        ):
            return False
        user = ctx.get("user") if isinstance(ctx, dict) and isinstance(ctx.get("user"), dict) else {}
        try:
            if self._private_user_role(user, str(user.get("user_id") or "")) != "owner":
                return False
        except Exception:
            return False
        owner_id = _single_line(user.get("user_id") or user.get("id"), 120)
        only_bottom = bool((ctx.get("config") or {}).get("only_bottom_line", False))
        cutoff = _now_ts() - 7 * 86400
        reports = self.data.get("boundary_feedback_reports")
        return any(
            isinstance(item, dict)
            and item.get("status") == "pending"
            and not item.get("direct_notified")
            and _safe_float(item.get("created_at"), 0) >= cutoff
            and (not owner_id or owner_id in set(item.get("target_owner_ids") or []))
            and (not only_bottom or item.get("level") == "bottom_line")
            for item in (reports if isinstance(reports, list) else [])
        )

    def _relationship_boundary_report_ability_executor(self, ctx: dict[str, Any]) -> dict[str, Any]:
        if not bool(runtime_persona_setting(self, "enable_relationship_violation_penalties", True)) or not bool(
            runtime_persona_setting(self, "enable_relationship_boundary_owner_report", True)
        ):
            return {"success": False, "text": "", "context": "关系边界转达当前未启用", "summary": "能力未启用"}
        user = ctx.get("user") if isinstance(ctx, dict) and isinstance(ctx.get("user"), dict) else {}
        owner_id = _single_line(user.get("user_id") or user.get("id"), 120)
        config = ctx.get("config") if isinstance(ctx, dict) and isinstance(ctx.get("config"), dict) else {}
        only_bottom = bool(config.get("only_bottom_line", False))
        max_chars = _safe_int(config.get("max_chars"), 120, 20, 300)
        reports = self.data.get("boundary_feedback_reports")
        if not isinstance(reports, list):
            return {"success": False, "text": "", "context": "没有可转达的边界事件", "summary": "无事件"}
        cutoff = _now_ts() - 7 * 86400
        candidate = next(
            (
                item
                for item in reports
                if isinstance(item, dict)
                and item.get("status") == "pending"
                and not item.get("direct_notified")
                and _safe_float(item.get("created_at"), 0) >= cutoff
                and (not owner_id or owner_id in set(item.get("target_owner_ids") or []))
                and (not only_bottom or item.get("level") == "bottom_line")
            ),
            None,
        )
        if not isinstance(candidate, dict):
            return {"success": False, "text": "", "context": "没有可转达的边界事件", "summary": "无事件"}
        candidate["excerpt"] = _single_line(candidate.get("excerpt"), max_chars)
        candidate["status"] = "delivered"
        candidate["delivered_at"] = _now_ts()
        self._schedule_data_save(sections={"boundary_feedback_reports"})
        text = self._format_relationship_boundary_owner_report(candidate)
        return {
            "success": True,
            "text": text,
            "context": "角色正在向主要用户自然转达一次真实发生的关系边界事件。",
            "summary": "关系边界转达",
            "effective_action": "external:boundary_feedback_report",
        }
