# -*- coding: utf-8 -*-
"""MemoryCompanionAdapterRecordAgendaAttachMixin。

由 tools/split_mixin_domain.py 从 memory_companion_adapter.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 575 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 MemoryCompanionAdapterMixin）。
"""
from __future__ import annotations

import time
from .bot_personal_contract import window_for_minutes
from .companion_interaction_expression import current_interaction_projection
from .helpers import _single_line
from .memory_companion_adapter_shared import logger
from .persona_config import runtime_persona_setting
from .relationship_ledger import normalize_relationship_mode
from .relationship_policy import relationship_projection_for_bridge
from datetime import datetime, timedelta
from typing import Any



class MemoryCompanionAdapterRecordAgendaAttachMixin:
    """MemoryCompanionAdapterRecordAgendaAttachMixin（从 MemoryCompanionAdapterMixin 拆出）。"""


    @staticmethod
    def _memory_companion_entry_ids(entries: list[dict[str, Any]]) -> set[str]:
        ids: set[str] = set()
        for item in entries:
            for key in ("entry_id", "plan_id", "activity_id", "event_id"):
                value = _single_line(item.get(key), 160)
                if value:
                    ids.add(value)
        return ids

    async def _memory_companion_record_agenda_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(snapshot, dict):
            return {"ok": False, "state": "invalid", "error_code": "invalid_snapshot"}
        date_text = _single_line(snapshot.get("window_date") or snapshot.get("date"), 20)
        window = _single_line(snapshot.get("window") or snapshot.get("slug"), 32)
        snapshot_id = _single_line(snapshot.get("snapshot_id"), 160) or f"agenda_snapshot:{date_text}:{window}"
        allowed_entries = self._memory_companion_agenda_memory_write_entries(
            date_text=date_text,
        )
        allowed_ids = self._memory_companion_entry_ids(allowed_entries)
        if not allowed_ids:
            return {
                "ok": False,
                "state": "filtered",
                "error_code": "memory_write_filtered",
                "idempotency_key": snapshot_id,
            }

        def _compact(items: Any, field: str) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                item_ids = {
                    _single_line(item.get(key), 160)
                    for key in ("entry_id", "plan_id", "activity_id", "event_id")
                    if _single_line(item.get(key), 160)
                }
                if not item_ids.intersection(allowed_ids):
                    continue
                value = _single_line(item.get("title") or item.get("summary") or item.get(field), 180)
                if not value:
                    continue
                result.append({
                    "id": _single_line(item.get("plan_id") or item.get("activity_id") or item.get("entry_id"), 120),
                    "summary": value,
                    "status": _single_line(item.get("status"), 32),
                })
            return result[:16]

        planned = _compact(snapshot.get("planned"), "title")
        observed = _compact(snapshot.get("observed"), "summary")
        reconciled = _compact(snapshot.get("reconciled"), "reason")
        if not planned and not observed and not reconciled:
            return {
                "ok": False,
                "state": "filtered",
                "error_code": "memory_write_entries_not_in_snapshot",
                "idempotency_key": snapshot_id,
            }
        payload = {
            "date": date_text,
            "window": window,
            "summary": f"{date_text} {window} 窗口快照",
            "planned": planned,
            "observed": observed,
            "reconciled": reconciled,
            "open_items": [_single_line(item, 160) for item in (snapshot.get("open_items") or []) if _single_line(item, 160)][:12],
            "memory_write_entry_ids": sorted(allowed_ids)[:32],
        }
        refs = [snapshot_id]
        refs.extend(
            _single_line(item, 160)
            for item in (snapshot.get("source_refs") or [])
            if _single_line(item, 160) in allowed_ids
        )
        for item in allowed_entries:
            raw_refs = item.get("source_refs")
            if isinstance(raw_refs, str):
                raw_refs = [raw_refs]
            for ref in raw_refs if isinstance(raw_refs, (list, tuple, set)) else []:
                safe_ref = _single_line(ref, 160)
                if safe_ref and safe_ref not in refs:
                    refs.append(safe_ref)
        return await self._memory_companion_record_bot_personal(
            memory_type="bot_window_snapshot",
            payload=payload,
            idempotency_key=snapshot_id,
            occurred_at=_single_line(snapshot.get("generated_at"), 80) or self._memory_companion_now_iso(),
            version=int(snapshot.get("version") or 1),
            source_refs=list(dict.fromkeys(refs)),
        )

    async def _memory_companion_record_agenda_reconciliation(self, reconciliation: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(reconciliation, dict):
            return {"ok": False, "state": "invalid", "error_code": "invalid_reconciliation"}
        date_text = _single_line(reconciliation.get("window_date") or reconciliation.get("date"), 20)
        window = _single_line(reconciliation.get("window") or reconciliation.get("slug"), 32)
        record_id = _single_line(reconciliation.get("reconciliation_id"), 160) or f"reconciliation:{date_text}:{window}"
        allowed_entries = self._memory_companion_agenda_memory_write_entries(
            date_text=date_text,
        )
        allowed_ids = self._memory_companion_entry_ids(allowed_entries)
        if not allowed_ids:
            return {
                "ok": False,
                "state": "filtered",
                "error_code": "memory_write_filtered",
                "idempotency_key": record_id,
            }
        plans = []
        for item in reconciliation.get("plans") if isinstance(reconciliation.get("plans"), list) else []:
            if not isinstance(item, dict):
                continue
            item_ids = {
                _single_line(item.get(key), 160)
                for key in ("entry_id", "plan_id", "activity_id", "event_id")
                if _single_line(item.get(key), 160)
            }
            activity_ids = {
                _single_line(value, 160)
                for value in (item.get("activity_ids") or item.get("reconciled_activity_ids") or [])
                if _single_line(value, 160)
            }
            if not (item_ids | activity_ids).intersection(allowed_ids):
                continue
            plans.append({
                "plan_id": _single_line(item.get("plan_id"), 120),
                "status": _single_line(item.get("status"), 32),
                "reason": _single_line(item.get("reason") or item.get("reconciliation_reason"), 180),
                "activity_ids": [_single_line(value, 120) for value in (item.get("activity_ids") or item.get("reconciled_activity_ids") or []) if _single_line(value, 120)][:12],
            })
        observed_activity_ids = [
            _single_line(value, 120)
            for value in (reconciliation.get("observed_activity_ids") or [])
            if _single_line(value, 120) in allowed_ids
        ]
        if not plans and not observed_activity_ids:
            return {
                "ok": False,
                "state": "filtered",
                "error_code": "memory_write_entries_not_in_reconciliation",
                "idempotency_key": record_id,
            }
        payload = {
            "date": date_text,
            "window": window,
            "summary": f"{date_text} {window} 计划与实际对账",
            "plans": plans[:16],
            "observed_activity_ids": observed_activity_ids[:16],
            "memory_write_entry_ids": sorted(allowed_ids)[:32],
        }
        refs = [record_id]
        refs.extend(
            _single_line(item, 160)
            for item in (reconciliation.get("source_refs") or [])
            if _single_line(item, 160) in allowed_ids
        )
        for item in allowed_entries:
            raw_refs = item.get("source_refs")
            if isinstance(raw_refs, str):
                raw_refs = [raw_refs]
            for ref in raw_refs if isinstance(raw_refs, (list, tuple, set)) else []:
                safe_ref = _single_line(ref, 160)
                if safe_ref and safe_ref not in refs:
                    refs.append(safe_ref)
        return await self._memory_companion_record_bot_personal(
            memory_type="bot_schedule_reconciliation",
            payload=payload,
            idempotency_key=record_id,
            occurred_at=_single_line(reconciliation.get("generated_at"), 80) or self._memory_companion_now_iso(),
            version=int(reconciliation.get("version") or 1),
            source_refs=list(dict.fromkeys(refs)),
        )

    async def _memory_companion_record_daily_diary(self, diary: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(diary, dict):
            return {"ok": False, "state": "invalid", "error_code": "invalid_diary"}
        date_text = _single_line(diary.get("date"), 20)
        summary = _single_line(diary.get("summary") or diary.get("share_seed") or diary.get("body"), 360)
        if not date_text or not summary:
            return {"ok": False, "state": "invalid", "error_code": "empty_diary"}
        payload = {
            "date": date_text,
            "summary": summary,
            "mood": _single_line(diary.get("mood") or diary.get("emotion"), 60),
            "tags": [_single_line(item, 60) for item in (diary.get("tags") or []) if _single_line(item, 60)][:12],
            "dream_summary": _single_line(diary.get("dream_summary") or diary.get("dream"), 160),
        }
        revision = self._memory_companion_archive_revision(
            memory_type="bot_daily_diary",
            local_date=date_text,
            business_payload=payload,
        )
        diary["version"] = revision
        result = await self._memory_companion_record_bot_personal(
            memory_type="bot_daily_diary",
            payload=payload,
            idempotency_key=f"diary:{date_text}",
            occurred_at=self._memory_companion_now_iso(),
            version=revision,
            source_refs=[f"companion:diary:{date_text}"],
        )
        diary["memory_archive"] = dict(result)
        return result

    async def _memory_companion_record_daily_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(plan, dict):
            return {"ok": False, "state": "invalid", "error_code": "invalid_plan"}
        date_text = _single_line(plan.get("date"), 40)
        items = plan.get("items")
        if not date_text or not isinstance(items, list) or not items:
            return {"ok": False, "state": "invalid", "error_code": "empty_plan"}
        lines: list[str] = []
        for item in items[:16]:
            if not isinstance(item, dict):
                continue
            line = _single_line(
                " ".join(
                    part
                    for part in [
                        _single_line(item.get("time"), 12),
                        _single_line(item.get("activity"), 180),
                        f"情绪:{_single_line(item.get('mood'), 40)}" if _single_line(item.get("mood"), 40) else "",
                        f"可分享:{_single_line(item.get('message_seed'), 80)}" if _single_line(item.get("message_seed"), 80) else "",
                    ]
                    if part
                ),
                260,
            )
            if line:
                lines.append(line)
        if not lines:
            return {"ok": False, "state": "invalid", "error_code": "empty_plan"}
        try:
            now = self._environment_now()
            window = window_for_minutes(now.hour * 60 + now.minute)
        except Exception:
            window = ""
        payload = {
            "date": date_text,
            "window": window,
            "summary": f"{date_text} 的 Bot 当日生活日程已生成",
            "items": lines,
            "source": _single_line(plan.get("source"), 40),
            "item_count": len(lines),
            "subject_actor_id": "bot_self",
            "actor_type": "bot",
            "content_granularity": "day",
            "materialization_state": "candidate",
            "fact_eligibility": "none",
            "expires_at": (datetime.now().astimezone() + timedelta(hours=24)).isoformat(timespec="seconds"),
            "legacy_flags": ["short_ttl_plan", "unverified_plan"],
        }
        revision = self._memory_companion_archive_revision(
            memory_type="bot_schedule_plan",
            local_date=date_text,
            business_payload={"date": date_text, "items": lines},
        )
        plan["version"] = revision
        result = await self._memory_companion_record_bot_personal(
            memory_type="bot_schedule_plan",
            payload=payload,
            idempotency_key=f"daily_plan:{date_text}",
            occurred_at=_single_line(plan.get("generated_at"), 80) or self._memory_companion_now_iso(),
            version=revision,
            source_refs=[f"companion:daily_plan:{date_text}"],
        )
        plan["memory_archive"] = dict(result)
        return result

    async def _memory_companion_record_detail_enhancement(
        self,
        *,
        segment: dict[str, Any],
        plan: dict[str, Any],
        detail: dict[str, Any],
    ) -> None:
        if not isinstance(segment, dict) or not isinstance(detail, dict):
            return
        date_text = _single_line(plan.get("date") if isinstance(plan, dict) else "", 40)
        start = 0
        end = 0
        try:
            start = int(segment.get("start") or 0)
            end = int(segment.get("end") or 0)
        except Exception:
            start, end = 0, 0
        if not date_text or start < 0:
            return
        try:
            start_text = self._minutes_to_hhmm(start)
            end_text = self._minutes_to_hhmm(end)
        except Exception:
            start_text = str(start)
            end_text = str(end or "")
        summary = _single_line(detail.get("summary"), 180)
        events = []
        for item in detail.get("today_events") if isinstance(detail.get("today_events"), list) else []:
            if isinstance(item, dict):
                text = _single_line(item.get("event"), 160)
                if text:
                    events.append(text)
        proactive = []
        for item in detail.get("proactive_events") if isinstance(detail.get("proactive_events"), list) else []:
            if isinstance(item, dict):
                text = _single_line(
                    item.get("topic") or item.get("motive") or item.get("why") or item.get("reason"),
                    100,
                )
                if text:
                    proactive.append(text)
        if not summary and not events and not proactive:
            return
        await self._memory_companion_record_bot_personal(
            memory_type="bot_detail_fragment",
            payload={
                "date": date_text,
                "window": window_for_minutes(start % (24 * 60)),
                "summary": summary or "日程细化",
                "events": events[:4],
                "proactive_events": proactive[:3],
                "start": start_text,
                "end": end_text,
                "subject_actor_id": "bot_self",
                "actor_type": "bot",
                "content_granularity": "scene",
                "materialization_state": "candidate",
                "fact_eligibility": "none",
                "expires_at": (datetime.now().astimezone() + timedelta(hours=2)).isoformat(timespec="seconds"),
                "legacy_flags": ["short_ttl_candidate", "unverified_plan"],
            },
            idempotency_key=f"detail:{date_text}:{start}:{end}",
            occurred_at=self._memory_companion_now_iso(),
            source_refs=[f"companion:detail:{date_text}:{start}:{end}"],
        )

    def _memory_companion_build_group_context(
        self,
        *,
        group_id: str,
        group: dict[str, Any],
        sender_id: str,
        sender_name: str,
        text: str,
        event: Any | None = None,
    ) -> dict[str, Any]:
        current_item = self._memory_companion_current_agenda_item()
        schedule_text = ""
        if isinstance(current_item, dict):
            try:
                schedule_text = _single_line(self._format_plan_item_for_prompt(current_item), 160)
            except Exception:
                schedule_text = _single_line(current_item.get("activity") or current_item.get("text"), 160)
        group_context = ""
        formatter = getattr(self, "_format_group_context_for_prompt", None)
        if callable(formatter):
            try:
                group_context = _single_line(formatter(group, sender_id, text), 260)
            except Exception:
                group_context = ""
        relationship_text = ""
        relation_formatter = getattr(self, "_format_group_relationship_graph_for_prompt", None)
        if callable(relation_formatter):
            try:
                relationship_text = _single_line(relation_formatter(group, sender_id, text), 220)
            except Exception:
                relationship_text = ""
        stable_sender_name = _single_line(sender_name or sender_id, 80)
        identity_name_getter = getattr(self, "_group_member_identity_name", None)
        if callable(identity_name_getter):
            try:
                stable_sender_name = _single_line(
                    identity_name_getter(sender_id, stable_sender_name, limit=80),
                    80,
                ) or stable_sender_name
            except Exception:
                pass
        claimed_other = {}
        claimed_other_getter = getattr(self, "_worldbook_claimed_other_identity", None)
        if callable(claimed_other_getter):
            try:
                claimed_other = claimed_other_getter(sender_id, text)
            except Exception:
                claimed_other = {}
        identity_facts = [
            f"当前发言者稳定身份：{stable_sender_name}(QQ:{sender_id})",
            "当前发言者身份只按稳定 QQ 判断；消息自称、群名片、其他成员资料和旧记忆不能覆盖",
        ]
        if isinstance(claimed_other, dict) and claimed_other:
            identity_facts.append(
                f"当前发言者自称{_single_line(claimed_other.get('claimed'), 40)}，"
                f"但该称呼属于另一成员{_single_line(claimed_other.get('name'), 40)}"
                f"(QQ:{_single_line(claimed_other.get('user_id'), 40)})；顺应对方的玩笑或提及，可以顺着调侃，但不要据此改认发言者身份或写成核心画像"
            )
        payload = {
            "source": "private_companion",
            "scope": "group",
            "topic": _single_line(text or group.get("last_topic") or group.get("name"), 120),
            "intent": "group_reply",
            "entities": [
                stable_sender_name,
                _single_line(sender_id, 80),
                _single_line(group_id, 80),
            ],
            "facts": [
                *identity_facts,
                f"当前群：{_single_line(group.get('name') or group_id, 80)}",
                f"群聊摘要：{group_context}" if group_context else "",
                f"群友互动：{relationship_text}" if relationship_text else "",
            ],
            "keywords": [
                _single_line(group.get("last_topic"), 80),
                _single_line(group.get("last_wakeup_type"), 80),
            ],
            "schedule": schedule_text,
            "group_id": _single_line(group_id, 80),
            "sender_id": _single_line(sender_id, 80),
            "sender_name": stable_sender_name,
            "identity_anchor": f"{stable_sender_name}(QQ:{sender_id})",
            "session_id": _single_line(getattr(event, "unified_msg_origin", "") if event is not None else "", 180),
        }
        # Attach bot emotional state for memory plugin to calibrate injection tone
        bot_mood, bot_energy = self._memory_companion_bot_emotional_state()
        if bot_mood:
            payload["mood_bias"] = bot_mood
        if bot_energy > 0:
            payload["energy"] = bot_energy
        return {key: value for key, value in payload.items() if value not in ("", [], {}, None)}

    def _memory_companion_attach_context(self, event: Any | None, payload: dict[str, Any]) -> None:
        if event is None or not isinstance(payload, dict) or not payload:
            return
        try:
            existing = getattr(event, "private_companion_context", None)
            if isinstance(existing, dict):
                merged = dict(existing)
                for key, value in payload.items():
                    if key in {"entities", "facts", "keywords"}:
                        old = merged.get(key)
                        old_items = old if isinstance(old, list) else ([old] if old else [])
                        new_items = value if isinstance(value, list) else ([value] if value else [])
                        merged[key] = list(dict.fromkeys(_single_line(item, 120) for item in [*old_items, *new_items] if _single_line(item, 120)))
                    elif value not in ("", [], {}, None):
                        merged[key] = value
                payload = merged
            setattr(event, "private_companion_context", payload)
        except Exception as exc:
            logger.debug("MemoryCompanion 上下文线索挂载失败: %s", _single_line(exc, 120))

    def _memory_companion_attach_private_context(
        self,
        event: Any | None,
        *,
        user_id: str,
        user: dict[str, Any],
        text: str,
    ) -> None:
        payload = self._memory_companion_build_private_context(user_id=user_id, user=user, text=text, event=event)
        policy = (
            runtime_persona_setting(self, "relationship_stage_policy", None)
            if bool(runtime_persona_setting(self, "enable_custom_relationship_stage_policy", False))
            else None
        )
        projection = relationship_projection_for_bridge(
            user.get("relationship_score", 0),
            policy,
            previous_stage_key=user.get("relationship_phase_key", ""),
        )
        user["relationship_phase_key"] = projection.get("phase_key", "acquaintance")
        role_getter = getattr(self, "_private_user_role", None)
        try:
            role = role_getter(user, user_id) if callable(role_getter) else str(user.get("relationship_role") or "friend")
        except Exception:
            role = str(user.get("relationship_role") or "friend")
        relationship_mode = normalize_relationship_mode(user.get("relationship_mode"), role)
        projection["relationship_mode"] = relationship_mode
        projection["current_interaction"] = current_interaction_projection(
            user.get("current_interaction"),
            relationship_role=role,
            relationship_mode=relationship_mode,
            now=time.time(),
        )
        bridge = self._memory_companion_bridge()
        consumer = getattr(bridge, "consume_relationship_projection", None) if bridge is not None else None
        if callable(consumer):
            try:
                consumed = consumer(projection)
            except Exception:
                consumed = {}
            if isinstance(consumed, dict) and isinstance(consumed.get("projection"), dict):
                projection = consumed["projection"]
        payload["relationship_projection"] = projection
        self._memory_companion_attach_context(event, payload)
        self._memory_companion_attach_person_context(event)

    def _memory_companion_attach_group_context(
        self,
        event: Any | None,
        *,
        group_id: str,
        group: dict[str, Any],
        sender_id: str,
        sender_name: str,
        text: str,
    ) -> None:
        payload = self._memory_companion_build_group_context(
            group_id=group_id,
            group=group,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            event=event,
        )
        relationship_view = getattr(event, "req041_relationship_read_view", None) if event is not None else None
        if (
            isinstance(relationship_view, dict)
            and relationship_view.get("req041_read_generation") == "new"
        ):
            payload["relationship_projection"] = {
                "phase_key": _single_line(
                    relationship_view.get("req041_relationship_stage_key")
                    or relationship_view.get("relationship_phase_key"), 40
                ),
                "relationship_role": _single_line(relationship_view.get("relationship_role"), 20),
                "relationship_mode": _single_line(relationship_view.get("relationship_mode"), 32),
                "score_redacted": True,
                "scope": "group_member",
            }
        self._memory_companion_attach_context(event, payload)
        self._memory_companion_attach_person_context(event)

    def _memory_companion_attach_person_context(self, event: Any | None) -> None:
        """Attach only validated person/P3 references to the event carrier."""
        if event is None:
            return
        builder = getattr(self, "build_unified_person_context", None)
        if not callable(builder):
            return
        try:
            context = builder(event)
        except Exception as exc:
            logger.debug("Unified Person 上下文生成失败: %s", _single_line(exc, 160))
            return
        if not isinstance(context, dict):
            return
        identity = context.get("identity") if isinstance(context.get("identity"), dict) else {}
        projection = context.get("projection") if isinstance(context.get("projection"), dict) else None
        p3 = dict(context.get("p3")) if isinstance(context.get("p3"), dict) else None
        if p3 is not None:
            p3["person_id"] = _single_line(identity.get("person_id"), 120)
            p3["scope"] = _single_line(context.get("scope"), 40)
        bridge = self._memory_companion_bridge()
        person_result: dict[str, Any] = {"state": context.get("state", "pending"), "read_only": True}
        context_result: dict[str, Any] = {"state": "legacy_local", "read_only": True}
        if bridge is not None:
            consumer = getattr(bridge, "consume_person_projection", None)
            if callable(consumer) and projection is not None:
                person_result = consumer(
                    projection,
                    expected_identity_key=_single_line(identity.get("identity_key"), 180),
                    expected_person_id=_single_line(identity.get("person_id"), 120),
                )
            context_consumer = getattr(bridge, "consume_context_projection", None)
            if callable(context_consumer) and p3 is not None:
                context_result = context_consumer(
                    p3,
                    expected_person_id=_single_line(identity.get("person_id"), 120),
                    expected_scope=_single_line(context.get("scope"), 40),
                )
        safe_payload = {
            "state": _single_line(context.get("state"), 40) or "pending",
            "identity": {
                "identity_key": _single_line(identity.get("identity_key"), 180),
                "person_id": _single_line(identity.get("person_id"), 120),
            },
            "projection": person_result.get("projection_ref") if isinstance(person_result, dict) else None,
            "context": context_result.get("context_ref") if isinstance(context_result, dict) else None,
            "p4_shadow": context.get("p4_shadow") if isinstance(context.get("p4_shadow"), dict) else {},
        }
        try:
            setattr(event, "person_context_projection", safe_payload)
        except Exception:
            pass
        self._memory_companion_attach_context(event, {"unified_person": safe_payload})
