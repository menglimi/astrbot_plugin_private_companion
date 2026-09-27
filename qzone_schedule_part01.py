# -*- coding: utf-8 -*-
"""QzoneSchedulePart01Mixin。

由 tools/split_mixin_domain.py 从 qzone_schedule.py 机械抽取（21 个方法 + 0 个模块级名字 + 0 个类级赋值 / 476 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 QzoneScheduleMixin）。
"""
from __future__ import annotations

from .qzone_schedule_shared import _qzone_compat_constant
from .qzone_schedule_shared import Any
from .qzone_schedule_shared import _day_start_ts
from .qzone_schedule_shared import _now_ts
from .qzone_schedule_shared import _safe_float
from .qzone_schedule_shared import _safe_int
from .qzone_schedule_shared import _single_line
from .qzone_schedule_shared import _today_key
from .qzone_schedule_shared import datetime
from .qzone_schedule_shared import hashlib
from .qzone_schedule_shared import hhmm_to_minutes
from .qzone_schedule_shared import json
from .qzone_schedule_shared import length_profile_range
from .qzone_schedule_shared import length_profile_sequence
from .qzone_schedule_shared import merge_windows
from .qzone_schedule_shared import parse_windows
from .qzone_schedule_shared import random
from .qzone_schedule_shared import runtime_persona_setting
from .qzone_schedule_shared import slot_is_night
from .qzone_schedule_shared import subtract_ranges



class QzoneSchedulePart01Mixin:
    """QzoneSchedulePart01Mixin（从 QzoneScheduleMixin 拆出）。"""


    def _qzone_current_agenda_item(self) -> dict[str, Any] | None:
        getter = getattr(self, "_agenda_current_context_item", None)
        if callable(getter):
            try:
                item = getter()
            except Exception:
                return None
            return item if isinstance(item, dict) else None
        legacy_getter = getattr(self, "_get_current_plan_item", None)
        try:
            item = legacy_getter(self.data.get("daily_plan", {})) if callable(legacy_getter) else None
        except Exception:
            item = None
        return item if isinstance(item, dict) else None

    @staticmethod
    def _qzone_agenda_timestamp(value: Any) -> float:
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()
            return parsed.timestamp()
        except (TypeError, ValueError, OSError):
            return 0.0

    @classmethod
    def _qzone_parse_windows(cls, raw: Any) -> list[tuple[int, int]]:
        return parse_windows(raw, merge=cls._qzone_merge_windows)

    @staticmethod
    def _qzone_merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
        return merge_windows(windows)

    @staticmethod
    def _qzone_subtract_ranges(window: tuple[int, int], blocked: tuple[tuple[int, int], ...]) -> list[tuple[int, int]]:
        return subtract_ranges(window, blocked)

    def _qzone_life_publish_window_source(self) -> str:
        """Return the raw window text for the configured mode."""
        mode = _single_line(
            runtime_persona_setting(self, "qzone_life_publish_window_mode", "template_double"),
            32,
        ) or "template_double"
        if mode in {"custom", "自定义"}:
            raw = str(runtime_persona_setting(self, "qzone_life_publish_windows", "") or "")
            if not raw.strip():
                # Legacy field kept working so upgrades never lose a config.
                raw = str(getattr(self, "qzone_life_publish_custom_windows", "") or "")
            return raw
        if mode in {"all_day", "全天随机"}:
            return "00:00-24:00"
        if mode in {"template_double_night", "double_night"}:
            return str(
                _qzone_compat_constant("QZONE_WINDOW_TEMPLATE_DOUBLE_NIGHT")
            )
        legacy = str(getattr(self, "qzone_life_publish_double_windows", "") or "")
        return legacy if legacy.strip() else str(
            _qzone_compat_constant("QZONE_WINDOW_TEMPLATE_DOUBLE")
        )

    def _qzone_night_publish_allowed(self) -> bool:
        """Night windows only open when the bot is genuinely sleepless."""
        if not bool(
            runtime_persona_setting(self, "qzone_life_publish_allow_insomnia_night", False)
        ):
            return False
        checker = getattr(self, "_has_active_insomnia_state", None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    def _qzone_life_publish_effective_windows(self) -> list[tuple[int, int]]:
        """Windows usable right now, with night hours trimmed unless sleepless."""
        windows = self._qzone_parse_windows(self._qzone_life_publish_window_source())
        if not windows:
            mode = _single_line(
                runtime_persona_setting(self, "qzone_life_publish_window_mode", "template_double"),
                32,
            )
            if mode in {"custom", "自定义"}:
                return []
            windows = self._qzone_parse_windows(
                _qzone_compat_constant("QZONE_WINDOW_TEMPLATE_DOUBLE")
            )
        if self._qzone_night_publish_allowed():
            return windows
        trimmed: list[tuple[int, int]] = []
        for window in windows:
            trimmed.extend(
                self._qzone_subtract_ranges(
                    window,
                    _qzone_compat_constant("QZONE_NIGHT_RANGES"),
                )
            )
        return self._qzone_merge_windows(trimmed)

    def _qzone_cross_day_gap_seconds(self) -> float:
        """Minimum spacing carried over from the previous published post."""
        hours = _safe_int(
            runtime_persona_setting(self, "qzone_life_publish_min_interval_hours", 24),
            24,
            1,
            168,
        )
        return float(max(1, hours) * 3600)

    def _qzone_intra_day_gap_seconds(self) -> float:
        """Minimum spacing between two posts planned for the same day."""
        configured = _safe_int(
            runtime_persona_setting(self, "qzone_life_publish_intra_day_gap_minutes", 0),
            0,
            0,
            1440,
        )
        floor_minutes = int(
            _qzone_compat_constant("QZONE_INTRA_DAY_GAP_FLOOR_MINUTES")
        )
        if configured <= 0:
            configured = floor_minutes
        return float(max(floor_minutes, configured) * 60)

    def _qzone_life_publish_pick_slots(
        self,
        *,
        target_count: int,
        earliest: float,
        now: float,
    ) -> list[float]:
        """Pick up to target_count random moments spread across today's windows.

        One post per window comes first so a day's posts land in different parts
        of the day; only then are longer windows reused, and every extra slot
        still has to clear the intra-day gap.
        """
        day_start = _day_start_ts(now)
        day_end = day_start + 24 * 3600
        floor = max(earliest, now, day_start)
        gap = self._qzone_intra_day_gap_seconds()
        spans: list[tuple[float, float]] = []
        for start_min, end_min in self._qzone_life_publish_effective_windows():
            start = max(day_start + start_min * 60, floor)
            end = min(day_start + end_min * 60, day_end)
            if start < end:
                spans.append((start, end))
        if not spans or target_count <= 0:
            return []
        slots: list[float] = []

        def fits(candidate: float) -> bool:
            return all(abs(candidate - existing) >= gap for existing in slots)

        random.shuffle(spans)
        for start, end in spans:
            if len(slots) >= target_count:
                break
            for _ in range(6):
                candidate = random.uniform(start, end)
                if fits(candidate):
                    slots.append(candidate)
                    slots.sort()
                    break
        for start, end in spans:
            while len(slots) < target_count:
                placed = False
                for _ in range(6):
                    candidate = random.uniform(start, end)
                    if fits(candidate):
                        slots.append(candidate)
                        slots.sort()
                        placed = True
                        break
                if not placed:
                    break
            if len(slots) >= target_count:
                break
        return slots[:target_count]

    @staticmethod
    def _qzone_hhmm_to_minutes(value: Any) -> int | None:
        return hhmm_to_minutes(value)

    def _qzone_schedule_candidates_for_today(self) -> list[dict[str, Any]]:
        """Collect short-lived Bot current facts for a nearby publish slot."""
        disclosure = getattr(self, "_agenda_disclosure_view", None)
        if not callable(disclosure):
            return []
        try:
            view = disclosure("current_fact", max_entries=32)
            items = getattr(view, "entries", None)
            if items is None and hasattr(view, "get"):
                items = view.get("entries", [])
        except Exception:
            return []
        now = _now_ts()
        candidates: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            eligibility = _single_line(item.get("fact_eligibility"), 40).lower()
            phase = _single_line(item.get("temporal_phase"), 20).lower()
            if eligibility not in {"current_internal", "current_observed"} or phase != "current":
                continue
            activity = _single_line(item.get("title") or item.get("state") or item.get("activity"), 160)
            valid_from = self._qzone_agenda_timestamp(item.get("start_at") or item.get("committed_at") or item.get("created_at"))
            valid_until = self._qzone_agenda_timestamp(item.get("end_at") or item.get("valid_until") or item.get("expires_at"))
            if not activity or valid_until <= now or valid_until <= valid_from:
                continue
            candidates.append(
                {
                    "key": _single_line(
                        item.get("entry_id") or item.get("activity_id") or item.get("id"),
                        80,
                    ) or f"current-fact#{index}",
                    "label": activity,
                    "start_minutes": datetime.fromtimestamp(valid_from).hour * 60 + datetime.fromtimestamp(valid_from).minute,
                    "valid_from": valid_from,
                    "valid_until": valid_until,
                    "fact_eligibility": eligibility,
                    "evidence_kind": _single_line(item.get("evidence_kind"), 40),
                }
            )
        return candidates

    @staticmethod
    def _qzone_pick_schedule_for_slot(
        candidates: list[dict[str, Any]],
        planned_at: float,
        used_keys: set[str],
    ) -> dict[str, Any] | None:
        """Pick the unused schedule fragment closest to this planned moment."""
        if not candidates:
            return None
        target_minutes = (planned_at - _day_start_ts(planned_at)) / 60.0
        best: dict[str, Any] | None = None
        best_distance = float("inf")
        for candidate in candidates:
            if candidate.get("key") in used_keys:
                continue
            valid_from = _safe_float(candidate.get("valid_from"), 0.0)
            valid_until = _safe_float(candidate.get("valid_until"), 0.0)
            if valid_from <= 0 or valid_until <= planned_at or planned_at < valid_from:
                continue
            distance = abs(float(candidate.get("start_minutes") or 0) - target_minutes)
            if distance < best_distance:
                best = candidate
                best_distance = distance
        return best

    @staticmethod
    def _qzone_length_profile_sequence(count: int) -> list[str]:
        return length_profile_sequence(count, choose=random.choice, chance=random.random, choose_index=random.randrange, shuffle=random.shuffle)

    @staticmethod
    def _qzone_length_profile_range(profile: Any) -> tuple[int, int]:
        return length_profile_range(profile, _qzone_compat_constant("QZONE_LENGTH_PROFILES"))

    def _qzone_slot_is_night(self, planned_at: float) -> bool:
        return slot_is_night(planned_at, day_start=_day_start_ts(planned_at), night_ranges=_qzone_compat_constant("QZONE_NIGHT_RANGES"))

    def _qzone_life_publish_plan_signature(self) -> str:
        payload = {
            "max_daily": _safe_int(
                runtime_persona_setting(self, "qzone_life_publish_max_daily", 1), 1, 1
            ),
            "probability": _safe_float(
                runtime_persona_setting(self, "qzone_life_publish_probability", 0.18), 0.18
            ),
            "window_mode": _single_line(
                runtime_persona_setting(self, "qzone_life_publish_window_mode", "template_double"),
                32,
            ),
            "windows": self._qzone_life_publish_window_source(),
            "allow_night": bool(
                runtime_persona_setting(self, "qzone_life_publish_allow_insomnia_night", False)
            ),
            "cross_day_hours": _safe_int(
                runtime_persona_setting(self, "qzone_life_publish_min_interval_hours", 24),
                24,
                1,
                168,
            ),
            "intra_day_minutes": _safe_int(
                runtime_persona_setting(self, "qzone_life_publish_intra_day_gap_minutes", 0),
                0,
                0,
                1440,
            ),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:20]

    def _qzone_backfill_plan_schedule_labels(self, plan: dict[str, Any]) -> bool:
        candidates = self._qzone_schedule_candidates_for_today()
        items = plan.get("items") if isinstance(plan, dict) else None
        if not isinstance(items, list):
            return False
        used_keys = {
            _single_line(item.get("schedule_key"), 80)
            for item in items
            if isinstance(item, dict) and _single_line(item.get("schedule_key"), 80)
        }
        changed = False
        now = _now_ts()
        for item in items:
            if not isinstance(item, dict) or not _single_line(item.get("schedule_label"), 160):
                continue
            planned_at = _safe_float(item.get("planned_at"), 0.0)
            valid_from = _safe_float(item.get("schedule_valid_from"), 0.0)
            valid_until = _safe_float(item.get("schedule_valid_until"), 0.0)
            eligibility = _single_line(item.get("schedule_fact_eligibility"), 40).lower()
            if (
                eligibility not in {"current_internal", "current_observed"}
                or valid_until <= now
                or valid_until <= valid_from
                or (planned_at > 0 and (planned_at < valid_from or planned_at >= valid_until))
            ):
                item.pop("schedule_key", None)
                item.pop("schedule_label", None)
                item.pop("schedule_valid_from", None)
                item.pop("schedule_valid_until", None)
                item.pop("schedule_fact_eligibility", None)
                item.pop("schedule_evidence_kind", None)
                changed = True
        used_keys = {
            _single_line(item.get("schedule_key"), 80)
            for item in items
            if isinstance(item, dict) and _single_line(item.get("schedule_key"), 80)
        }
        if not candidates:
            if changed:
                plan["used_schedule_keys"] = sorted(
                    _single_line(item.get("schedule_key"), 80)
                    for item in items
                    if isinstance(item, dict) and _single_line(item.get("schedule_key"), 80)
                )
            return changed
        for item in items:
            if not isinstance(item, dict) or item.get("status") != "planned" or _single_line(item.get("schedule_label"), 160):
                continue
            schedule = self._qzone_pick_schedule_for_slot(
                candidates,
                _safe_float(item.get("planned_at"), 0),
                used_keys,
            )
            if not isinstance(schedule, dict):
                continue
            key = _single_line(schedule.get("key"), 80)
            item["schedule_key"] = key
            item["schedule_label"] = _single_line(schedule.get("label"), 160)
            item["schedule_valid_from"] = _safe_float(schedule.get("valid_from"), 0.0)
            item["schedule_valid_until"] = _safe_float(schedule.get("valid_until"), 0.0)
            item["schedule_fact_eligibility"] = _single_line(schedule.get("fact_eligibility"), 40)
            item["schedule_evidence_kind"] = _single_line(schedule.get("evidence_kind"), 40)
            if key:
                used_keys.add(key)
            changed = True
        if changed:
            plan["used_schedule_keys"] = sorted(used_keys)
        return changed

    def _qzone_life_publish_daily_plan(self, state: dict[str, Any], *, now: float) -> dict[str, Any]:
        """Return today's publish plan, building it once per day.

        qzone_life_publish_probability is sampled exactly once per day here: it
        decides whether a plan gets built at all. On a hit the target count is
        max_daily-1 or max_daily (always exactly 1 when max_daily is 1), and each
        item is anchored to a random moment inside a configured window.
        """
        today = _today_key()
        signature = self._qzone_life_publish_plan_signature()
        existing = state.get("life_publish_daily_plan")
        if isinstance(existing, dict) and _single_line(existing.get("date"), 24) == today:
            stored_signature = _single_line(existing.get("config_signature"), 40)
            if not stored_signature or stored_signature == signature:
                existing["config_signature"] = signature
                return existing
            delivered = any(
                isinstance(item, dict) and item.get("status") in {"published", "delivery_unknown"}
                for item in list(existing.get("items") or [])
            )
            if delivered:
                for item in list(existing.get("items") or []):
                    if isinstance(item, dict) and item.get("status") == "planned":
                        item["status"] = "cancelled"
                        item["failed_reason"] = "config_changed_after_delivery"
                        item["finished_at"] = now
                existing["config_signature"] = signature
                existing["skip_reason"] = "config_changed_after_delivery"
                existing["updated_at"] = now
                return existing

        # The user controls this limit; do not impose a product-level ceiling.
        # Scheduling still naturally limits actual items by windows and gaps.
        limit = max(
            1,
            _safe_int(runtime_persona_setting(self, "qzone_life_publish_max_daily", 1), 1, 1),
        )
        probability = max(
            0.0,
            min(
                1.0,
                _safe_float(
                    runtime_persona_setting(self, "qzone_life_publish_probability", 0.18),
                    0.18,
                ),
            ),
        )
        plan: dict[str, Any] = {
            "date": today,
            "configured_limit": limit,
            "target_count": 0,
            "published_count": 0,
            "used_schedule_keys": [],
            "items": [],
            "created_at": now,
            "night_allowed": self._qzone_night_publish_allowed(),
            "config_signature": signature,
        }
        mode = _single_line(
            runtime_persona_setting(self, "qzone_life_publish_window_mode", "template_double"),
            32,
        )
        if mode in {"custom", "自定义"} and not self._qzone_parse_windows(self._qzone_life_publish_window_source()):
            plan["skip_reason"] = "invalid_window_config"
            state["life_publish_daily_plan"] = plan
            return plan
        if random.random() >= probability:
            plan["skip_reason"] = "probability_miss"
            state["life_publish_daily_plan"] = plan
            return plan

        target_count = limit if limit <= 1 else random.choice((limit - 1, limit))
        target_count = max(1, min(limit, target_count))
        last_publish_at = _safe_float(state.get("last_life_publish_at"), 0)
        earliest = last_publish_at + self._qzone_cross_day_gap_seconds() if last_publish_at > 0 else 0.0
        slots = self._qzone_life_publish_pick_slots(target_count=target_count, earliest=earliest, now=now)
        if not slots:
            plan["skip_reason"] = "no_window"
            state["life_publish_daily_plan"] = plan
            return plan

        candidates = self._qzone_schedule_candidates_for_today()
        profiles = self._qzone_length_profile_sequence(len(slots))
        used_keys: set[str] = set()
        items: list[dict[str, Any]] = []
        for index, planned_at in enumerate(slots):
            schedule = self._qzone_pick_schedule_for_slot(candidates, planned_at, used_keys)
            if isinstance(schedule, dict):
                used_keys.add(str(schedule.get("key")))
            night = self._qzone_slot_is_night(planned_at)
            items.append(
                {
                    "id": f"{today}-{index + 1}",
                    "planned_at": planned_at,
                    "schedule_key": str(schedule.get("key")) if schedule else "",
                    "schedule_label": _single_line(schedule.get("label"), 160) if schedule else "",
                    "schedule_valid_from": _safe_float(schedule.get("valid_from"), 0.0) if schedule else 0.0,
                    "schedule_valid_until": _safe_float(schedule.get("valid_until"), 0.0) if schedule else 0.0,
                    "schedule_fact_eligibility": _single_line(schedule.get("fact_eligibility"), 40) if schedule else "",
                    "schedule_evidence_kind": _single_line(schedule.get("evidence_kind"), 40) if schedule else "",
                    # Night posts stay short: a sleepless 2am note is a fragment.
                    "length_profile": "short" if night else profiles[index],
                    "night": night,
                    "status": "planned",
                    "attempts": 0,
                }
            )
        plan["target_count"] = len(items)
        plan["items"] = items
        state["life_publish_daily_plan"] = plan
        return plan

    @staticmethod
    def _qzone_life_publish_due_item(plan: dict[str, Any], *, now: float) -> dict[str, Any] | None:
        """Return the earliest planned item whose moment has arrived."""
        items = plan.get("items") if isinstance(plan, dict) else None
        if not isinstance(items, list):
            return None
        due: dict[str, Any] | None = None
        for item in items:
            if not isinstance(item, dict) or item.get("status") != "planned":
                continue
            planned_at = _safe_float(item.get("planned_at"), 0)
            if planned_at <= 0 or now < planned_at:
                continue
            if due is None or planned_at < _safe_float(due.get("planned_at"), 0):
                due = item
        return due
