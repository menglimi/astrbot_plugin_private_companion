# -*- coding: utf-8 -*-
"""DailyStateDetailPart02Mixin。

由 tools/split_mixin_domain.py 从 daily_state_detail.py 机械抽取（16 个方法 + 0 个模块级名字 + 0 个类级赋值 / 481 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateDetailMixin）。
"""
from __future__ import annotations

from .daily_state_detail_shared import _now_ts, _today_key, logger
from .daily_state_detail_shared import Any
from .daily_state_detail_shared import PromptSection
from .daily_state_detail_shared import _safe_float
from .daily_state_detail_shared import _safe_int
from .daily_state_detail_shared import _single_line
from .daily_state_detail_shared import build_detail_enhancement_prompt
from .daily_state_detail_shared import build_detail_enhancement_prompt_section
from .daily_state_detail_shared import normalize_story_items
from .daily_state_detail_shared import normalize_story_plan
from .daily_state_detail_shared import runtime_persona_setting



class DailyStateDetailPart02Mixin:
    """DailyStateDetailPart02Mixin（从 DailyStateDetailMixin 拆出）。"""


    def _pick_story_items_with_coverage(
        self,
        ordered: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        total = len(ordered)
        if total <= limit:
            return ordered
        now_minutes = self._environment_now_minutes()
        selected: set[int] = {0, total - 1}
        closest_index = min(
            range(total),
            key=lambda idx: self._story_item_time_distance(ordered[idx], now_minutes),
        )
        for idx in range(max(0, closest_index - 2), min(total, closest_index + 3)):
            selected.add(idx)
        if limit == 1:
            selected = {closest_index}
        else:
            for slot in range(limit):
                selected.add(round(slot * (total - 1) / max(1, limit - 1)))
        if len(selected) < limit:
            for idx in range(total):
                selected.add(idx)
                if len(selected) >= limit:
                    break
        return [ordered[idx] for idx in sorted(selected)[:limit]]

    def _story_item_time_distance(self, item: dict[str, Any], now_minutes: int) -> int:
        start, end = self._parse_window_minutes(str(item.get("window") or ""))
        if start is None or end is None:
            return 99_999
        if end < start:
            end += 24 * 60
        current = now_minutes
        if current < start and end > 24 * 60:
            current += 24 * 60
        if start <= current < end:
            return 0
        return min(abs(current - start), abs(current - end))

    def _normalize_story_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return normalize_story_plan(self, payload)

    def _normalize_story_items(self, raw_items: Any, text_key: str) -> list[dict[str, Any]]:
        return normalize_story_items(self, raw_items, text_key)

    def _build_detail_enhancement_prompt(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
        memory_companion_context: str = "",
    ) -> str:
        return build_detail_enhancement_prompt(self, segment, plan, state, memory_companion_context=memory_companion_context)

    def _build_detail_enhancement_prompt_section(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
        memory_companion_context: str = "",
    ) -> PromptSection:
        return build_detail_enhancement_prompt_section(
            self,
            segment,
            plan,
            state,
            memory_companion_context=memory_companion_context,
        )

    def _current_detail_segment_for_update(self) -> dict[str, Any] | None:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return None
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return None
        for segment in self._collect_detail_segments(plan, {}):
            start = _safe_int(segment.get("start"), 0)
            end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
            lead = max(0, _safe_int(runtime_persona_setting(self, "detail_enhancement_lead_minutes", 3), 3, 0))
            if start - lead <= now_minutes < end:
                return segment
        return None

    def _current_detail_snapshot_for_update(self) -> dict[str, Any] | None:
        """Return the finished detail snapshot for the clock-current plan segment."""

        segment = self._current_detail_segment_for_update()
        if not isinstance(segment, dict):
            return None
        plan_date = _single_line(segment.get("plan_date"), 16)
        now_minutes = self._effective_plan_now_minutes(plan_date)
        if now_minutes is None:
            return None
        start = _safe_int(segment.get("start"), -1, minimum=-1)
        end = _safe_int(segment.get("end"), -1, minimum=-1)
        if start < 0 or end < 0:
            return None
        if end <= start:
            end += 24 * 60
        # Detail generation may select the next segment during its lead
        # window. Passive state material must remain tied to the actual clock
        # window so it cannot describe the next scene early.
        if not (start <= now_minutes < end):
            return None
        segment_item = segment.get("item")
        if isinstance(segment_item, dict):
            lifecycle = self._normalize_schedule_lifecycle_status(
                segment_item.get("lifecycle_status") or segment_item.get("status")
            )
            # A finished detail snapshot still describes its parent plan. If
            # that plan was changed, deferred, cancelled, or already closed,
            # the old atmosphere must not survive as current prompt material.
            if lifecycle not in {"", "planned", "active"}:
                return None
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return None
        snapshot = enhanced.get(str(segment.get("key") or ""))
        if not isinstance(snapshot, dict):
            return None
        status = _single_line(snapshot.get("status"), 24).lower()
        if status and status != "done":
            return None
        return snapshot

    def _record_detail_interaction_update(self, item: dict[str, Any]) -> None:
        segment = self._current_detail_segment_for_update()
        if not segment:
            return
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return
        key = str(segment.get("key") or "")
        snapshot = enhanced.get(key)
        if not isinstance(snapshot, dict):
            return
        updates = snapshot.setdefault("interaction_updates", [])
        if not isinstance(updates, list):
            updates = []
            snapshot["interaction_updates"] = updates
        source = _single_line(item.get("source"), 24)
        if source == "用户换装":
            updates[:] = [
                update
                for update in updates
                if not (isinstance(update, dict) and _single_line(update.get("source"), 24) == source)
            ]
        updates.append(
            {
                "at": self._environment_now().strftime("%H:%M"),
                "source": source,
                "user_text": _single_line(item.get("user_text"), 80),
                "intensity": _single_line(item.get("intensity"), 16),
                "scope": _single_line(item.get("scope"), 40),
                "reaction": _single_line(item.get("immediate_reaction"), 140),
                "state_updates": item.get("state_updates", []),
                "source_role": _single_line(item.get("source_role"), 20),
                "source_user_id": _single_line(item.get("source_user_id"), 80),
            }
        )
        del updates[:-6]
        self._apply_interaction_to_snapshot_state(snapshot, item)

    def _cleanup_false_sleep_interaction_updates(self) -> bool:
        plan = self.data.get("daily_plan", {})
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(plan, dict) or not isinstance(enhanced, dict):
            return False
        false_sources = {"睡眠中被用户唤醒", "睡眠中再次被唤醒", "睡眠中醒后续聊"}
        false_user_texts: set[str] = set()
        changed = False
        for segment in self._collect_detail_segments(plan, {}):
            if self._is_sleepy_plan_item(segment.get("item")):
                continue
            snapshot = enhanced.get(str(segment.get("key") or ""))
            if not isinstance(snapshot, dict):
                continue
            updates = snapshot.get("interaction_updates", [])
            if not isinstance(updates, list):
                continue
            removed = [
                item for item in updates
                if isinstance(item, dict) and _single_line(item.get("source"), 24) in false_sources
            ]
            if not removed:
                continue
            snapshot["interaction_updates"] = [item for item in updates if item not in removed]
            removed_state_names: set[str] = set()
            summary = str(snapshot.get("summary") or "")
            for item in removed:
                user_text = _single_line(item.get("user_text"), 120)
                if user_text:
                    false_user_texts.add(user_text)
                for state_update in item.get("state_updates", []) if isinstance(item.get("state_updates"), list) else []:
                    name, _value, _note = self._parse_state_update_text(state_update)
                    if name:
                        removed_state_names.add(name)
                reaction = _single_line(item.get("reaction"), 140)
                if reaction:
                    summary = summary.replace(f"；用户介入后：{reaction}", "").replace(f"用户介入后：{reaction}", "")
            snapshot["summary"] = _single_line(summary, 160)
            remaining_state_names = {
                self._parse_state_update_text(state_update)[0]
                for item in snapshot["interaction_updates"]
                if isinstance(item, dict) and isinstance(item.get("state_updates"), list)
                for state_update in item.get("state_updates", [])
            }
            variables = snapshot.get("state_variables", [])
            if isinstance(variables, list):
                snapshot["state_variables"] = [
                    variable for variable in variables
                    if not (
                        isinstance(variable, dict)
                        and _single_line(variable.get("name"), 32) in removed_state_names - remaining_state_names
                        and str(variable.get("note") or "").startswith("用户介入：")
                    )
                ]
            changed = True
        if false_user_texts:
            adjustments = self.data.get("schedule_adjustments", [])
            if isinstance(adjustments, list):
                kept = [
                    item for item in adjustments
                    if not (
                        isinstance(item, dict)
                        and _single_line(item.get("source"), 24) in false_sources
                        and _single_line(item.get("user_text"), 120) in false_user_texts
                    )
                ]
                if len(kept) != len(adjustments):
                    self.data["schedule_adjustments"] = kept
                    changed = True
            runtime = self.data.get("daily_state", {}).get("sleep_runtime") if isinstance(self.data.get("daily_state"), dict) else None
            if isinstance(runtime, dict) and runtime.get("phase") in {"woken", "sleeping_again"}:
                if _single_line(runtime.get("last_user_text"), 120) in false_user_texts:
                    runtime.update(
                        {
                            "phase": "awake",
                            "label": self._sleep_phase_label("awake"),
                            "updated_at": _now_ts(),
                            "last_event": "已清理普通休闲段的错误睡眠唤醒记录",
                            "source": "cleanup",
                        }
                    )
                    changed = True
        if changed:
            logger.info("已清理普通休闲段的错误睡眠唤醒记录")
        return changed

    def _invalidate_detail_after_interaction(self, *, now: float | None = None) -> None:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return
        enhanced = self.data.get("detail_enhanced_segments", {})
        if isinstance(enhanced, dict):
            for segment in self._collect_detail_segments(plan, {}):
                start = _safe_int(segment.get("start"), 0)
                if start > now_minutes:
                    key = str(segment.get("key") or "")
                    if key in enhanced:
                        enhanced.pop(key, None)
        story_plan = self.data.get("daily_story_plan", {})
        if isinstance(story_plan, dict):
            for key in ("today_events", "proactive_events"):
                items = story_plan.get(key, [])
                if not isinstance(items, list):
                    continue
                kept = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    start, end = self._parse_window_minutes(str(item.get("window") or ""))
                    if start is None or end is None:
                        kept.append(item)
                        continue
                    if end < start:
                        end += 24 * 60
                    if start <= now_minutes:
                        kept.append(item)
                story_plan[key] = kept

    def _infer_location_from_text(self, text: str) -> str:
        normalized = _single_line(text, 200)
        if not normalized:
            return ""
        location_rules = [
            (("被窝", "床上", "床边", "卧室", "房间", "书桌", "台灯", "家里", "客厅", "沙发", "洗漱台", "餐桌"), "家里"),
            (("教室", "课间", "食堂", "校门", "走廊", "操场", "上课", "下课", "自习", "老师", "书包", "制服"), "学校"),
            (("工位", "会议", "办公室", "上班", "下班", "通勤", "打卡"), "工作场所"),
            (("便利店", "超市", "商店"), "便利店附近"),
            (("路上", "街上", "出门", "楼下", "外面", "街边", "回家路上", "校门口"), "外面"),
            (("楼梯口", "走廊栏杆", "窗边", "阳台"), "过道或窗边"),
        ]
        for keywords, label in location_rules:
            if any(keyword in normalized for keyword in keywords):
                return label
        return ""

    def _infer_location_from_plan_context(
        self,
        *,
        plan: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> str:
        candidates: list[str] = []
        detail_allowed = self._detail_model_location_policy_allowed(detail)
        if isinstance(detail, dict) and detail_allowed:
            model_location = _single_line(detail.get("location"), 60)
            if model_location:
                return model_location
            for key in ("summary", "scene", "event", "topic"):
                text = _single_line(detail.get(key), 160)
                if text:
                    candidates.append(text)
            for list_key in ("today_events", "proactive_events"):
                raw_items = detail.get(list_key)
                if not isinstance(raw_items, list):
                    continue
                for item in raw_items[:6]:
                    if not isinstance(item, dict):
                        continue
                    candidates.append(
                        " ".join(
                            _single_line(item.get(key), 80)
                            for key in ("scene", "event", "content", "detail", "description", "topic", "why")
                            if _single_line(item.get(key), 80)
                        )
                    )
        plan = plan if isinstance(plan, dict) else self.data.get("daily_plan", {})
        current_item = self._get_current_plan_item(plan if isinstance(plan, dict) else {})
        if isinstance(current_item, dict):
            candidates.append(
                " ".join(
                    _single_line(current_item.get(key), 120)
                    for key in ("activity", "mood", "message_seed")
                    if _single_line(current_item.get(key), 120)
                )
            )
        # Do not inspect neighboring raw plan rows here.  They are future or
        # unverified projections and their clock distance cannot establish the
        # Bot's current location.  A current item above is already policy /
        # runtime qualified; a generated detail location is handled separately
        # by ``_refresh_daily_state_location_from_plan``.
        for text in candidates:
            inferred = self._infer_location_from_text(text)
            if inferred:
                return inferred
        return ""

    def _detail_model_location_policy_allowed(self, detail: dict[str, Any] | None = None) -> bool:
        """Allow a coherent schedule projection while keeping observed location distinct."""

        policy_getter = getattr(self, "_agenda_disclosure_view", None)
        if not callable(policy_getter):
            # Lightweight harnesses and legacy callers do not have C3 policy;
            # preserve their historical local projection behavior.
            return True
        payload = detail if isinstance(detail, dict) else {}
        evidence_kind = _single_line(payload.get("evidence_kind"), 48).lower()
        eligibility = _single_line(payload.get("fact_eligibility"), 48).lower()
        refs = payload.get("source_refs")
        has_refs = isinstance(refs, (list, tuple, set)) and any(_single_line(ref, 160) for ref in refs)
        if evidence_kind not in {"tool_action", "external_record"} or eligibility != "current_observed" or not has_refs:
            # A generated detail is part of the character's simulated day. It
            # may keep the active scene coherent, but is not observed evidence.
            location = _single_line(payload.get("location"), 60)
            basis = self._normalize_schedule_basis(payload.get("location_basis"), default=[])
            return bool(location and basis)
        try:
            view = policy_getter("current_fact", now=self._environment_now(), max_entries=128)
            entries = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
        except Exception:
            return False
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            if _single_line(entry.get("subject_actor_id"), 120) != "bot_self":
                continue
            if _single_line(entry.get("fact_eligibility"), 48).lower() != "current_observed":
                continue
            entry_refs = entry.get("source_refs")
            if isinstance(entry_refs, str):
                entry_refs = [entry_refs]
            if isinstance(entry_refs, (list, tuple, set)) and any(
                _single_line(ref, 160) in {_single_line(value, 160) for value in refs}
                for ref in entry_refs
            ):
                return True
        return False

    def _refresh_daily_state_location_from_plan(
        self,
        *,
        plan: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
        segment: dict[str, Any] | None = None,
    ) -> bool:
        state = self.data.get("daily_state")
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return False
        # Detail generation intentionally runs in a lead window.  A future
        # candidate may describe a likely place, but it is not current Bot
        # state until the segment actually starts.
        if isinstance(detail, dict) and isinstance(segment, dict):
            plan_date = _single_line((plan or {}).get("date"), 16) if isinstance(plan, dict) else ""
            now_minutes = self._effective_plan_now_minutes(plan_date)
            start_minutes = _safe_int(segment.get("start"), -1, minimum=-1)
            if now_minutes is not None and start_minutes >= 0 and now_minutes < start_minutes:
                return False
        override_ts = _safe_float(state.get("location_override_ts"), 0)
        model_location = (
            _single_line(detail.get("location"), 60)
            if isinstance(detail, dict) and self._detail_model_location_policy_allowed(detail)
            else ""
        )
        if override_ts > 0 and _now_ts() - override_ts < 4 * 3600 and not model_location:
            return False
        location = model_location or self._infer_location_from_plan_context(plan=plan, detail=detail)
        if not location and isinstance(segment, dict) and isinstance(segment.get("item"), dict):
            item = segment["item"]
            location = self._infer_location_from_text(
                " ".join(
                    _single_line(item.get(key), 120)
                    for key in ("activity", "mood", "message_seed")
                    if _single_line(item.get(key), 120)
                )
            )
        if not location:
            return False
        current = _single_line(state.get("location"), 40)
        if current == location:
            if not model_location:
                return False
            metadata_changed = False
            if _single_line(state.get("location_source"), 40) != "detail_model":
                state["location_source"] = "detail_model"
                metadata_changed = True
            projection_kind = (
                "observed"
                if _single_line(detail.get("fact_eligibility"), 48).lower() == "current_observed"
                else "schedule"
            )
            if _single_line(state.get("location_projection"), 24) != projection_kind:
                state["location_projection"] = projection_kind
                metadata_changed = True
            confidence = min(1.0, _safe_float(detail.get("location_confidence"), 0.72))
            basis = self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"])
            if _safe_float(state.get("location_confidence"), -1) != confidence:
                state["location_confidence"] = confidence
                metadata_changed = True
            if state.get("location_basis") != basis:
                state["location_basis"] = basis
                metadata_changed = True
            if override_ts > 0:
                state["location_override_ts"] = 0.0
                metadata_changed = True
            if metadata_changed:
                state["location_updated_at"] = self._environment_now().strftime("%H:%M")
            return metadata_changed
        state["location"] = location
        if model_location:
            observed_location = _single_line(detail.get("fact_eligibility"), 48).lower() == "current_observed"
            state["location_source"] = "detail_model"
            state["location_projection"] = "observed" if observed_location else "schedule"
        else:
            state["location_source"] = "detail" if isinstance(detail, dict) else "daily_plan"
            state["location_projection"] = "schedule"
        if model_location:
            state["location_confidence"] = min(1.0, _safe_float(detail.get("location_confidence"), 0.72))
            state["location_basis"] = self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"])
        state["location_updated_at"] = self._environment_now().strftime("%H:%M")
        if override_ts > 0:
            state["location_override_ts"] = 0.0
        return True

    def _apply_dialogue_location_override(self, location: str) -> None:
        """对话驱动的位置覆盖：用户带角色外出/回家时，立即更新 daily_state.location。

        这会覆盖日程推断的位置，直到下一次细化刷新自然恢复，或用户再次触发回家。
        """
        state = self.data.get("daily_state")
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return
        state["location"] = _single_line(location, 40)
        state["location_source"] = "dialogue_override"
        state["location_updated_at"] = self._environment_now().strftime("%H:%M")
        state["location_override_ts"] = _now_ts()
        self._save_data_sync(sections={"daily_state"})
