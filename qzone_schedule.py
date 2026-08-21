# -*- coding: utf-8 -*-
"""QQ Zone automated publishing windows, plans, and lifecycle orchestration."""
from __future__ import annotations

import json
import hashlib
import random
import re
import sys
import time
from datetime import datetime
from typing import Any

from astrbot.api import logger

from .helpers import _day_start_ts, _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting


def _persona_provider_id(owner: Any, canonical_key: str, legacy_attr: str, quick_role: str) -> str:
    """Resolve canonical persona provider settings while preserving test harnesses."""
    fallback = str(getattr(owner, legacy_attr, "") or "").strip()
    if not callable(getattr(owner, "persona_setting", None)):
        return fallback
    mode = str(getattr(owner, "provider_config_mode", "quick") or "quick").strip().lower()
    if mode != "quick":
        return str(runtime_persona_setting(owner, canonical_key, fallback) or "").strip()
    complex_id = str(runtime_persona_setting(owner, "COMPLEX_REASONING_PROVIDER_ID", "") or "").strip()
    if quick_role == "complex":
        return complex_id or fallback
    if quick_role == "creative":
        creative_id = str(runtime_persona_setting(owner, "CREATIVE_MODEL_PROVIDER_ID", "") or "").strip()
        return creative_id or complex_id or fallback
    fast_id = str(runtime_persona_setting(owner, "FAST_RESPONSE_PROVIDER_ID", "") or "").strip()
    return fast_id or complex_id or fallback

__all__ = (
    "QZONE_INTRA_DAY_GAP_FLOOR_MINUTES",
    "QZONE_LENGTH_HARD_LIMIT",
    "QZONE_LENGTH_PROFILES",
    "QZONE_NIGHT_RANGES",
    "QZONE_PLAN_ITEM_MAX_ATTEMPTS",
    "QZONE_WINDOW_TEMPLATE_DOUBLE",
    "QZONE_WINDOW_TEMPLATE_DOUBLE_NIGHT",
    "QzoneScheduleMixin",
)

# Publish-window templates offered as one-click presets in the WebUI. Users stay
# free to edit them or add any number of extra windows afterwards.
QZONE_WINDOW_TEMPLATE_DOUBLE = "07:00-10:00\n18:00-22:00"
QZONE_WINDOW_TEMPLATE_DOUBLE_NIGHT = "00:30-03:30\n07:00-10:00\n18:00-22:00"
# Night range mirrors the existing insomnia-night definition (23:00-05:59).
QZONE_NIGHT_RANGES = ((0, 6 * 60), (23 * 60, 24 * 60))
QZONE_LENGTH_PROFILES = {
    "short": (20, 45),
    "medium": (45, 80),
    "long": (80, 110),
}
QZONE_LENGTH_HARD_LIMIT = 120
# Floor for spacing several posts inside one day so a high max_daily can never
# collapse into a burst of back-to-back posts.
QZONE_INTRA_DAY_GAP_FLOOR_MINUTES = 45
# A plan item that keeps failing retires instead of retrying every tick all day.
QZONE_PLAN_ITEM_MAX_ATTEMPTS = 3

_QZONE_COMPAT_BASELINE = {
    "QZONE_WINDOW_TEMPLATE_DOUBLE": QZONE_WINDOW_TEMPLATE_DOUBLE,
    "QZONE_WINDOW_TEMPLATE_DOUBLE_NIGHT": QZONE_WINDOW_TEMPLATE_DOUBLE_NIGHT,
    "QZONE_NIGHT_RANGES": QZONE_NIGHT_RANGES,
    "QZONE_LENGTH_PROFILES": dict(QZONE_LENGTH_PROFILES),
    "QZONE_LENGTH_HARD_LIMIT": QZONE_LENGTH_HARD_LIMIT,
    "QZONE_INTRA_DAY_GAP_FLOOR_MINUTES": QZONE_INTRA_DAY_GAP_FLOOR_MINUTES,
    "QZONE_PLAN_ITEM_MAX_ATTEMPTS": QZONE_PLAN_ITEM_MAX_ATTEMPTS,
}


def _qzone_compat_constant(name: str) -> Any:
    """Honor legacy patches applied through the qzone_integration facade."""
    local_value = globals()[name]
    facade = sys.modules.get(f"{__package__}.qzone_integration")
    if facade is None:
        return local_value
    facade_value = getattr(facade, name, local_value)
    if facade_value != _QZONE_COMPAT_BASELINE.get(name):
        return facade_value
    return local_value


class QzoneScheduleMixin:
    """Publish window planning and automated post lifecycle helpers."""

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
        """Parse "HH:MM-HH:MM" lines into (start_minute, end_minute) pairs.

        There is deliberately no cap on how many windows a user may configure;
        how many posts actually fit is decided later by the daily target count,
        the configured gaps and the remaining time in each window.
        """
        windows: list[tuple[int, int]] = []
        for line in str(raw or "").replace("，", "\n").replace(",", "\n").splitlines():
            text = line.strip()
            if not text:
                continue
            match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", text)
            if not match:
                continue
            start_h, start_m, end_h, end_m = (int(part) for part in match.groups())
            if start_h > 23 or start_m > 59 or end_h > 24 or end_m > 59:
                continue
            if end_h == 24 and end_m != 0:
                continue
            start = start_h * 60 + start_m
            end = min(end_h * 60 + end_m, 24 * 60)
            if end == start:
                continue
            if end < start:
                windows.extend(((start, 24 * 60), (0, end)))
            else:
                windows.append((start, end))
        return cls._qzone_merge_windows(windows)

    @staticmethod
    def _qzone_merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """Sort and merge overlapping windows so no minute is scheduled twice."""
        merged: list[tuple[int, int]] = []
        for start, end in sorted(windows):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    @staticmethod
    def _qzone_subtract_ranges(
        window: tuple[int, int],
        blocked: tuple[tuple[int, int], ...],
    ) -> list[tuple[int, int]]:
        """Remove blocked minute ranges from a window, keeping the remainder."""
        pieces = [window]
        for block_start, block_end in blocked:
            remaining: list[tuple[int, int]] = []
            for start, end in pieces:
                if block_end <= start or block_start >= end:
                    remaining.append((start, end))
                    continue
                if start < block_start:
                    remaining.append((start, min(block_start, end)))
                if end > block_end:
                    remaining.append((max(block_end, start), end))
            pieces = [(s, e) for s, e in remaining if e > s]
        return pieces

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
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 47 or minute > 59:
            return None
        return hour * 60 + minute

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
        """Vary post lengths so a multi-post day never reads as one long block."""
        if count <= 1:
            return [random.choice(("short", "medium"))]
        sequence: list[str] = []
        for index in range(count):
            sequence.append("short" if index % 2 == 0 else "medium")
        if count >= 3 and random.random() < 0.35:
            sequence[random.randrange(count)] = "long"
        random.shuffle(sequence)
        return sequence

    @staticmethod
    def _qzone_length_profile_range(profile: Any) -> tuple[int, int]:
        profiles = _qzone_compat_constant("QZONE_LENGTH_PROFILES")
        return profiles.get(
            _single_line(profile, 16) or "medium",
            profiles["medium"],
        )

    def _qzone_slot_is_night(self, planned_at: float) -> bool:
        minutes = (planned_at - _day_start_ts(planned_at)) / 60.0
        return any(
            start <= minutes < end
            for start, end in _qzone_compat_constant("QZONE_NIGHT_RANGES")
        )

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

    @staticmethod
    def _qzone_life_publish_next_planned_at(plan: dict[str, Any]) -> float:
        """Earliest still-pending moment, for status display."""
        items = plan.get("items") if isinstance(plan, dict) else None
        if not isinstance(items, list):
            return 0.0
        pending = [
            _safe_float(item.get("planned_at"), 0)
            for item in items
            if isinstance(item, dict) and item.get("status") == "planned"
        ]
        pending = [value for value in pending if value > 0]
        return min(pending) if pending else 0.0

    def _qzone_plan_item_finish(
        self,
        plan: dict[str, Any] | None,
        item: dict[str, Any] | None,
        status: str,
        *,
        now: float,
    ) -> None:
        """Move a plan item to a terminal state so the slot never fires twice.

        Consuming the item is what fixes the old single-planned_at bug, where a
        finished slot stayed eligible and fired again once the cooldown lapsed.
        """
        if not isinstance(item, dict):
            return
        item["status"] = status
        item["finished_at"] = now
        if not isinstance(plan, dict):
            return
        if status == "published":
            plan["published_count"] = _safe_int(plan.get("published_count"), 0, 0) + 1
            key = _single_line(item.get("schedule_key"), 80)
            if key:
                used = plan.get("used_schedule_keys")
                if not isinstance(used, list):
                    used = []
                if key not in used:
                    used.append(key)
                plan["used_schedule_keys"] = used

    @classmethod
    def _qzone_text_length_ok(cls, text: Any, profile: Any) -> bool:
        """Length gate: a hard ceiling plus a tolerant per-profile band."""
        length = len(re.sub(r"\s+", "", str(text or "")))
        if length > int(_qzone_compat_constant("QZONE_LENGTH_HARD_LIMIT")):
            return False
        low, high = cls._qzone_length_profile_range(profile)
        # Tolerance avoids discarding an otherwise good post over a few chars.
        return low - 8 <= length <= high + 12

    async def _qzone_life_publish_rewrite_to_length(
        self,
        text: str,
        profile: Any,
        *,
        prompt: str = "",
    ) -> str:
        """Ask once for a length-corrected rewrite, then re-run safety checks."""
        low, high = self._qzone_length_profile_range(profile)
        hard_limit = int(_qzone_compat_constant("QZONE_LENGTH_HARD_LIMIT"))
        rewrite_prompt = f"""
下面这条 QQ 空间说说草稿字数不合要求。请在保留原意、语气和具体生活细节的前提下改写到 {low} 到 {high} 字。
只输出正文，不要解释，不要加标题，绝对不要超过 {hard_limit} 字。

【原草稿】
{text}
""".strip()
        try:
            rewritten = await self._llm_call(
                rewrite_prompt,
                max_tokens=180,
                provider_id=self._task_provider(
                    _persona_provider_id(
                        self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                    ),
                    _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
                ),
                task="qzone_publish_length",
            )
        except Exception as exc:
            logger.warning("[PrivateCompanion] QQ 空间说说字数重写失败: %s", _single_line(exc, 120))
            return ""
        # A rewrite bypasses the original sanitizer, so re-run it here.
        return await self._sanitize_qzone_life_post_text(rewritten, prompt=rewrite_prompt)

    @staticmethod
    def _qzone_ngram_shared_count(left: Any, right: Any, *, n: int = 3) -> int:
        a = re.sub(r"\s+", "", str(left or ""))
        b = re.sub(r"\s+", "", str(right or ""))
        if len(a) < n or len(b) < n:
            return 1 if a and a == b else 0
        grams_a = {a[i : i + n] for i in range(len(a) - n + 1)}
        grams_b = {b[i : i + n] for i in range(len(b) - n + 1)}
        return len(grams_a & grams_b)

    def _qzone_life_publish_similar_recent(self, state: dict[str, Any], draft: Any) -> list[dict[str, Any]]:
        """Return recent posts whose shared 3-gram count with the draft meets the threshold."""
        items = state.get("recent_life_publish_texts") if isinstance(state, dict) else []
        if not isinstance(items, list):
            return []
        threshold = max(
            1,
            _safe_int(
                runtime_persona_setting(self, "qzone_life_publish_similarity_threshold", 2),
                2,
                1,
                20,
            ),
        )
        matches: list[dict[str, Any]] = []
        for item in items[-8:]:
            old = _single_line(item.get("text") if isinstance(item, dict) else item, 180)
            if not old:
                continue
            shared = self._qzone_ngram_shared_count(draft, old)
            if shared >= threshold:
                matches.append(
                    {
                        "text": old,
                        "shared": shared,
                        "at": _safe_float(item.get("at"), 0) if isinstance(item, dict) else 0,
                    }
                )
        return matches

    async def _qzone_life_publish_rewrite_deduplicated(
        self,
        text: str,
        similar: list[dict[str, Any]],
        *,
        prompt: str = "",
    ) -> str:
        recent_lines = "\n".join(f"- {_single_line(item['text'], 120)}" for item in similar[:3])
        rewrite_prompt = f"""
下面是一条 QQ 空间说说草稿，和最近发过的说说太像（同一场景、同一叙事套路）。
请改写成一条内容上明显不同的生活说说：换一个场景、换一个观察角度、换一种情绪，不要沿用原来的骨架和用词。
只输出正文，30 到 120 字，不要解释，不要加标题。

【最近已发的说说（避免重复）】
{recent_lines}

【原草稿】
{text}

【原任务背景】
{_single_line(prompt, 600)}
""".strip()
        try:
            rewritten = await self._llm_call(
                rewrite_prompt,
                max_tokens=180,
                provider_id=self._task_provider(
                    _persona_provider_id(
                        self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                    ),
                    _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
                ),
                task="qzone_publish_deduplicate",
            )
        except Exception as exc:
            logger.warning("[PrivateCompanion] QQ 空间说说去重重写失败: %s", _single_line(exc, 120))
            return ""
        # A rewrite bypasses the original sanitizer, so re-run it here.
        return await self._sanitize_qzone_life_post_text(rewritten, prompt=rewrite_prompt)

    async def _maybe_publish_qzone_life_post(self) -> None:
        if not self._qzone_automatic_persona_active():
            return
        async with self._qzone_operation_lock("life_publish"):
            await self._maybe_publish_qzone_life_post_locked()

    async def _maybe_publish_qzone_life_post_locked(self) -> None:
        if not (
            self._qzone_available()
            and runtime_persona_setting(self, "enable_qzone_life_publish", False)
        ):
            return
        now = _now_ts()
        state = self.data.setdefault("qzone_integration", {})
        if not isinstance(state, dict):
            self.data["qzone_integration"] = {}
            state = self.data["qzone_integration"]
        existing_plan = state.get("life_publish_daily_plan")
        signature = self._qzone_life_publish_plan_signature()
        plan_is_new = not (
            isinstance(existing_plan, dict)
            and _single_line(existing_plan.get("date"), 24) == _today_key()
            and _single_line(existing_plan.get("config_signature"), 40) == signature
        )
        daily_plan = self.data.get("daily_plan") if isinstance(self.data, dict) else None
        if plan_is_new and not (
            isinstance(daily_plan, dict) and _single_line(daily_plan.get("date"), 24) == _today_key()
        ):
            ensure_daily_plan = getattr(self, "_ensure_daily_plan", None)
            if callable(ensure_daily_plan):
                try:
                    await ensure_daily_plan()
                except Exception as exc:
                    logger.warning("[PrivateCompanion] QQ 空间建计划前确保今日日程失败，继续使用当下状态: %s", _single_line(exc, 120))
        plan = self._qzone_life_publish_daily_plan(state, now=now)
        plan_changed = self._qzone_backfill_plan_schedule_labels(plan)
        # The cross-day gap is applied while building the first slot. Once that
        # plan exists, same-day posts use their own explicit spacing instead of
        # accidentally inheriting a 24-hour cross-day cooldown.
        last_publish_at = _safe_float(state.get("last_life_publish_at"), 0)
        if (
            last_publish_at >= _day_start_ts(now)
            and now - last_publish_at < self._qzone_intra_day_gap_seconds()
        ):
            return
        # A new plan writes an initial status once; existing plans are stable
        # across ticks and process restarts.
        if plan.get("skip_reason"):
            if plan_is_new:
                state["last_life_publish_status"] = f"skipped:{_single_line(plan.get('skip_reason'), 40)}"
                state["last_life_publish_checked_at"] = now
                self._save_data_sync(sections={"qzone_integration"})
            return
        plan_item = self._qzone_life_publish_due_item(plan, now=now)
        if plan_item is None:
            if plan_is_new or plan_changed:
                next_at = self._qzone_life_publish_next_planned_at(plan)
                target = _safe_int(plan.get("target_count"), 0, 0)
                state["last_life_publish_status"] = (
                    f"ready:planned@{time.strftime('%m-%d %H:%M', time.localtime(next_at))}x{target}"
                    if next_at > 0
                    else "ready:planned"
                )
                state["last_life_publish_checked_at"] = now
                self._save_data_sync(sections={"qzone_integration"})
            return
        if plan_item.get("night") and not self._qzone_night_publish_allowed():
            self._qzone_plan_item_finish(plan, plan_item, "cancelled", now=now)
            plan_item["failed_reason"] = "night_state_inactive"
            state["last_life_publish_status"] = "cancelled:night_state_inactive"
            state["last_life_publish_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            return
        reusable_text = self._qzone_reusable_draft(state, "life_publish", now=now)
        block_reason = self._qzone_auto_publish_block_reason(state, now=now)
        if block_reason:
            state["last_life_publish_status"] = f"paused:auth:{_single_line(block_reason, 80)}"
            state["last_life_publish_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            return
        if now - _safe_float(state.get("last_life_publish_failed_at"), 0) < 15 * 60:
            return
        length_profile = "medium"
        schedule_label = ""
        if isinstance(plan_item, dict):
            length_profile = _single_line(plan_item.get("length_profile"), 16) or "medium"
            candidate_eligibility = _single_line(plan_item.get("schedule_fact_eligibility"), 40).lower()
            candidate_valid_until = _safe_float(plan_item.get("schedule_valid_until"), 0.0)
            schedule_label = (
                _single_line(plan_item.get("schedule_label"), 160)
                if candidate_eligibility in {"current_internal", "current_observed"}
                and candidate_valid_until > now
                else ""
            )
            # The item stays "planned" until it reaches a terminal state, so an
            # early return below simply retries on a later tick instead of
            # stranding it. The attempt counter is what stops an endless loop.
            attempts = _safe_int(plan_item.get("attempts"), 0, 0, 99) + 1
            plan_item["attempts"] = attempts
            if attempts > int(
                _qzone_compat_constant("QZONE_PLAN_ITEM_MAX_ATTEMPTS")
            ):
                plan_item["status"] = "failed"
                plan_item["failed_reason"] = "max_attempts"
                self._qzone_clear_pending_publish_assets(state, "life_publish")
                state["last_life_publish_status"] = "cancelled:max_attempts"
                state["last_life_publish_checked_at"] = now
                self._save_data_sync(sections={"qzone_integration"})
                return
        preflight_error = await self._qzone_preflight_auto_publish(None, state=state, source="life_publish")
        if preflight_error:
            state["last_life_publish_failed_at"] = now
            state["last_life_publish_status"] = f"paused:auth:{_single_line(preflight_error, 80)}"
            state["last_life_publish_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            return
        daily_state = self.data.get("daily_state", {})
        current_item = self._qzone_current_agenda_item()
        diary_context = self._recent_diary_context(count=2)
        theme_hint = self._qzone_publish_theme_hint()
        temporal_context = self._qzone_temporal_context()
        recent_publish_context = self._qzone_recent_publish_context(state)
        memory_context = await self._qzone_memory_companion_context(
            purpose="publish",
            query="QQ空间生活说说 今日公开可写生活 当前日程 今日穿搭 最近吃饭 日记余味 自我时间线",
        )
        public_state_hint = self._qzone_relationship_safe_source(
            self._qzone_public_state_hint(daily_state if isinstance(daily_state, dict) else {}),
            source="qzone.publish.current_state",
        )
        current_schedule_hint = self._qzone_relationship_safe_source(
            self._format_plan_item_for_prompt(current_item),
            source="qzone.publish.current_schedule",
        )
        diary_context = self._qzone_relationship_safe_source(
            diary_context,
            source="qzone.publish.recent_diary",
        )
        memory_context = self._qzone_relationship_safe_source(
            memory_context,
            source="qzone.publish.memory",
        )
        relationship_authority_guard = self._qzone_relationship_authority_guard()
        if reusable_text:
            text = reusable_text
            logger.info(
                "[PrivateCompanion] QQ 空间复用待发布生活说说草稿: age=%ds",
                int(now - _safe_float(state.get("last_life_publish_draft_at"), now)),
            )
        else:
            length_min, length_max = self._qzone_length_profile_range(length_profile)
            hard_limit = int(_qzone_compat_constant("QZONE_LENGTH_HARD_LIMIT"))
            length_rule = f"- {length_min} 到 {length_max} 字，最多不超过 {hard_limit} 字。"
            schedule_anchor = (
                f"\n【本条要写的生活片段】\n{schedule_label}\n只围绕这一个片段写，不要复述整天日程，也不要写成行程汇报。"
                if schedule_label
                else ""
            )
            night_note = (
                "\n【夜间状态】\n现在是失眠或浅睡的深夜，只写一句很短、低刺激的碎碎念，不要显得精神饱满。"
                if isinstance(plan_item, dict) and plan_item.get("night")
                else ""
            )
            prompt = f"""
请以当前 Bot 人格写一条 QQ 空间说说。
只输出说说正文,不要解释,不要加标题。

要求：
{length_rule}
- 像自然生活动态,不是公告、不是任务汇报。
- 开头直接进入一个具体画面或动作,不要总结式开场。
- 可以带一点公开可见的心情、天气或日记余味,但不要暴露插件、模型、内部状态数值。
- 禁止出现“能量”“心理能量”“/100”“状态变量”“当前状态”等内部汇报词。
- 不要 @ 用户,不要泄露私聊内容,不要写得像营销文。
- 写作角度：{theme_hint}{schedule_anchor}{night_note}

【说说风格提示】
{self._qzone_publish_style_prompt()}

【当前时间与季节】
{temporal_context}

【公开可写的状态余味】
{public_state_hint}

【当前/附近日程】
{current_schedule_hint or "无明确日程"}

【近日私密日记余味】
{diary_context or "暂无"}

【我会牢牢记住你 公开可写生活参考】
{memory_context or "暂无"}
使用方式：只选公开可写、不会泄露私聊或内部记忆来源的生活连续性。

【最近说说去重】
{recent_publish_context or "暂无最近记录。"}

{relationship_authority_guard}

{self._format_worldview_adaptation_prompt()}
""".strip()
            text = await self._llm_call(
                prompt,
                max_tokens=180,
                provider_id=self._task_provider(
                    _persona_provider_id(
                        self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                    ),
                    _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
                ),
                task="qzone_publish",
            )
            text = await self._sanitize_qzone_life_post_text(text, prompt=prompt)
            if not text:
                state["last_life_publish_failed_at"] = now
                state["last_life_publish_status"] = "cancelled:empty_or_unsafe_draft"
                state["last_life_publish_checked_at"] = now
                self._qzone_plan_item_finish(plan, plan_item, "cancelled", now=now)
                self._save_data_sync(sections={"qzone_integration"})
                logger.warning("[PrivateCompanion] QQ 空间生活动态草稿为空或不安全,已跳过发布")
                return
            if not self._qzone_text_length_ok(text, length_profile):
                relengthed = await self._qzone_life_publish_rewrite_to_length(
                    text,
                    length_profile,
                    prompt=prompt,
                )
                if relengthed and self._qzone_text_length_ok(relengthed, length_profile):
                    text = relengthed
                else:
                    state["last_life_publish_failed_at"] = now
                    state["last_life_publish_status"] = "cancelled:length"
                    state["last_life_publish_checked_at"] = now
                    self._qzone_plan_item_finish(plan, plan_item, "cancelled", now=now)
                    self._save_data_sync(sections={"qzone_integration"})
                    logger.info(
                        "[PrivateCompanion] QQ 空间说说字数不合要求且重写失败,已取消: profile=%s len=%s",
                        length_profile,
                        len(re.sub(r"\s+", "", text or "")),
                    )
                    return
            state["last_life_publish_draft"] = _single_line(text, 300)
            state["last_life_publish_draft_at"] = now
        similar = self._qzone_life_publish_similar_recent(state, text)
        if similar:
            if reusable_text:
                state["last_life_publish_failed_at"] = now
                state["last_life_publish_status"] = "cancelled:duplicate"
                state["last_life_publish_checked_at"] = now
                self._qzone_clear_pending_publish_assets(state, "life_publish")
                self._qzone_plan_item_finish(plan, plan_item, "cancelled", now=now)
                self._save_data_sync(sections={"qzone_integration"})
                logger.info("[PrivateCompanion] QQ 空间复用草稿与近期说说重复,已取消发布")
                return
            rewritten = await self._qzone_life_publish_rewrite_deduplicated(
                text,
                similar,
                prompt=prompt,
            )
            if (
                rewritten
                and self._qzone_text_length_ok(rewritten, length_profile)
                and not self._qzone_life_publish_similar_recent(state, rewritten)
            ):
                text = rewritten
                state["last_life_publish_draft"] = _single_line(text, 300)
                state["last_life_publish_draft_at"] = now
                logger.info("[PrivateCompanion] QQ 空间草稿与近期说说重复,已重写避开: %s", _single_line(text, 120))
            else:
                state["last_life_publish_failed_at"] = now
                state["last_life_publish_status"] = "cancelled:duplicate_after_retry"
                state["last_life_publish_checked_at"] = now
                self._qzone_plan_item_finish(plan, plan_item, "cancelled", now=now)
                self._save_data_sync(sections={"qzone_integration"})
                logger.info("[PrivateCompanion] QQ 空间草稿重写后仍与近期说说重复,已取消发布")
                return
        if reusable_text:
            image_sources = self._qzone_reusable_generated_image(state, "life_publish", text, now=now)
        else:
            image_sources = await self._maybe_generate_qzone_publish_image(
                post_text=text,
                reason="life_publish",
                daily_state=daily_state if isinstance(daily_state, dict) else {},
                current_item=current_item,
                diary_context=diary_context,
                state=state,
            )
        result = await self._publish_qzone_text(text, images=image_sources, publish_reason="life_publish")
        if result.get("success"):
            state["last_life_publish_at"] = now
            state.pop("last_life_publish_failed_at", None)
            state["last_life_publish_status"] = "published"
            if result.get("image_fallback"):
                self._qzone_note_publish_image_status(
                    state,
                    "life_publish",
                    "failed:upload_fallback",
                    result.get("image_fallback_message") or "配图发布失败，已降级纯文字发布",
                )
                state["last_life_publish_image_fallback"] = {
                    "stage": _single_line(result.get("image_fallback_stage"), 40),
                    "message": _single_line(result.get("image_fallback_message"), 180),
                    "at": now,
                }
            else:
                state.pop("last_life_publish_image_fallback", None)
            self._qzone_clear_pending_publish_assets(state, "life_publish")
            self._qzone_plan_item_finish(plan, plan_item, "published", now=now)
        else:
            state["last_life_publish_failed_at"] = now
            if result.get("delivery_unknown"):
                state["last_life_publish_status"] = f"delivery_unknown:{_single_line(result.get('message'), 80)}"
                self._qzone_plan_item_finish(plan, plan_item, "delivery_unknown", now=now)
                self._qzone_clear_pending_publish_assets(state, "life_publish")
            else:
                state["last_life_publish_status"] = f"failed:{_single_line(result.get('message'), 80)}"
            # Keep confirmed failures retryable until the attempt budget is exhausted.
            if not result.get("delivery_unknown") and (
                isinstance(plan_item, dict)
                and _safe_int(plan_item.get("attempts"), 0, 0, 99)
                >= int(_qzone_compat_constant("QZONE_PLAN_ITEM_MAX_ATTEMPTS"))
            ):
                self._qzone_plan_item_finish(plan, plan_item, "failed", now=now)
                self._qzone_clear_pending_publish_assets(state, "life_publish")
        state["last_life_publish_checked_at"] = now
        state["last_life_publish_text"] = _single_line(result.get("text") or text, 180)
        state["last_life_publish_images"] = _safe_int(result.get("image_count"), len(result.get("images") or []), 0, 99) if result.get("success") else 0
        self._save_data_sync(sections={"qzone_integration"})

    async def _maybe_publish_qzone_emotional_vent(
        self,
        *,
        user_snapshot: dict[str, Any] | None = None,
        interaction_state: dict[str, Any] | None = None,
        relationship_state: dict[str, Any] | None = None,
        intent: dict[str, Any] | None = None,
    ) -> None:
        if not (
            self._qzone_available()
            and runtime_persona_setting(self, "enable_emotion_simulation", True)
            and runtime_persona_setting(self, "enable_qzone_emotional_vent_publish", False)
        ):
            return
        # The short-lived interaction projection is the source of truth for
        # public expression. Keep the legacy relationship_state argument as a
        # compatibility bridge, but never use its mood score as the new gate.
        interaction = interaction_state if isinstance(interaction_state, dict) else {}
        if not interaction and isinstance(relationship_state, dict):
            legacy_projection = relationship_state.get("current_interaction")
            if isinstance(legacy_projection, dict):
                interaction = legacy_projection
        threshold = _safe_int(
            runtime_persona_setting(self, "qzone_emotional_vent_threshold", 90),
            90,
            40,
            100,
        )
        event_intensity = _safe_int((intent or {}).get("emotion_intensity"), 0, 0, 100)
        if event_intensity < threshold or interaction.get("expression_band") not in {"avoidant", "hurt"}:
            return
        if isinstance(user_snapshot, dict):
            role_getter = getattr(self, "_private_user_role", None)
            try:
                role = role_getter(user_snapshot, str(user_snapshot.get("user_id") or "")) if callable(role_getter) else ""
            except Exception:
                role = ""
            if role != "owner":
                logger.info(
                    "[PrivateCompanion] 公开心情动态跳过: user_role=%s intensity=%s",
                    role or "friend",
                    event_intensity,
                )
                return
        now = _now_ts()
        state = self.data.setdefault("qzone_integration", {})
        if not isinstance(state, dict):
            self.data["qzone_integration"] = {}
            state = self.data["qzone_integration"]
        cooldown = max(
            4,
            _safe_int(
                runtime_persona_setting(self, "qzone_emotional_vent_cooldown_hours", 72),
                72,
                4,
                336,
            ),
        ) * 3600
        if now - _safe_float(state.get("last_emotional_vent_at"), 0) < cooldown:
            logger.info("[PrivateCompanion] 公开心情动态跳过: cooldown intensity=%s", event_intensity)
            return
        block_reason = self._qzone_auto_publish_block_reason(state, now=now)
        if block_reason:
            state["last_emotional_vent_status"] = f"paused:auth:{_single_line(block_reason, 80)}"
            state["last_emotional_vent_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            return
        if now - _safe_float(state.get("last_emotional_vent_failed_at"), 0) < 15 * 60:
            return
        reusable_text = self._qzone_reusable_draft(state, "emotional_vent", now=now)
        probability = max(
            0.0,
            min(
                1.0,
                _safe_float(
                    runtime_persona_setting(self, "qzone_emotional_vent_probability", 0.35),
                    0.35,
                ),
            ),
        )
        if not reusable_text and random.random() > probability:
            state["last_emotional_vent_status"] = "skipped:probability_miss"
            state["last_emotional_vent_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            return
        preflight_error = await self._qzone_preflight_auto_publish(None, state=state, source="emotional_vent")
        if preflight_error:
            state["last_emotional_vent_failed_at"] = now
            state["last_emotional_vent_status"] = f"paused:auth:{_single_line(preflight_error, 80)}"
            state["last_emotional_vent_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            return
        daily_state = self.data.get("daily_state", {})
        current_item = self._qzone_current_agenda_item()
        public_state_hint = self._qzone_relationship_safe_source(
            self._qzone_public_state_hint(daily_state if isinstance(daily_state, dict) else {}),
            source="qzone.emotional_vent.current_state",
        )
        current_schedule_hint = self._qzone_relationship_safe_source(
            self._format_plan_item_for_prompt(current_item),
            source="qzone.emotional_vent.current_schedule",
        )
        reason = _single_line(
            self._qzone_relationship_safe_source(
                interaction.get("reason") or (intent or {}).get("emotion_reason"),
                source="qzone.emotional_vent.reason",
            ),
            80,
        )
        relationship_authority_guard = self._qzone_relationship_authority_guard()
        prompt = f"""
请以当前 Bot 人格写一条 QQ 空间说说,表达一种模糊的低落、委屈或想透气的心情。
只输出说说正文,不要解释,不要加标题。

要求：
- 20 到 80 字。
- 像自然生活动态,不要像控诉、公告、任务汇报。
- 只留一个画面、一个动作,不写排比和感想堆叠。
- 不要 @ 用户,不要提到任何具体用户、私聊内容、聊天截图或“刚才谁说了什么”。
- 不要出现“受伤分”“情绪分”“阈值”“插件”“模型”“Bot”“机器人”“/100”等内部词。
- 可以写天气、夜色、窗边、散步、想安静一会儿这类公开可见的余味。

【说说风格提示】
{self._qzone_publish_style_prompt(mood="emotional_vent")}

【公开可写的状态余味】
{public_state_hint}

【当前/附近日程】
{current_schedule_hint or "无明确日程"}

【内部触发原因，只能作为情绪方向，禁止复述】
{reason or "情绪有点低落"}

{relationship_authority_guard}

{self._format_worldview_adaptation_prompt()}
""".strip()
        try:
            if reusable_text:
                text = reusable_text
                logger.info(
                    "[PrivateCompanion] QQ 空间复用待发布心情动态草稿: age=%ds",
                    int(now - _safe_float(state.get("last_emotional_vent_draft_at"), now)),
                )
            else:
                text = await self._llm_call(
                    prompt,
                    max_tokens=140,
                    provider_id=self._task_provider(
                        _persona_provider_id(
                            self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                        ),
                        _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
                    ),
                    task="qzone_emotional_vent",
                )
                text = await self._sanitize_qzone_life_post_text(text, prompt=prompt)
                if not text:
                    state["last_emotional_vent_failed_at"] = now
                    state["last_emotional_vent_status"] = "cancelled:empty_or_unsafe_draft"
                    state["last_emotional_vent_checked_at"] = now
                    self._save_data_sync(sections={"qzone_integration"})
                    logger.warning("[PrivateCompanion] 公开心情动态草稿为空或不安全,已跳过发布")
                    return
                state["last_emotional_vent_draft"] = _single_line(text, 240)
                state["last_emotional_vent_draft_at"] = now
            if reusable_text:
                image_sources = self._qzone_reusable_generated_image(state, "emotional_vent", text, now=now)
            else:
                image_sources = await self._maybe_generate_qzone_publish_image(
                    post_text=text,
                    reason="emotional_vent",
                    daily_state=daily_state if isinstance(daily_state, dict) else {},
                    current_item=current_item,
                    diary_context="",
                    state=state,
                )
            result = await self._publish_qzone_text(text, images=image_sources, publish_reason="emotional_vent")
            if result.get("success"):
                state["last_emotional_vent_at"] = now
                state.pop("last_emotional_vent_failed_at", None)
                state["last_emotional_vent_status"] = "published"
                if result.get("image_fallback"):
                    self._qzone_note_publish_image_status(
                        state,
                        "emotional_vent",
                        "failed:upload_fallback",
                        result.get("image_fallback_message") or "配图发布失败，已降级纯文字发布",
                    )
                    state["last_emotional_vent_image_fallback"] = {
                        "stage": _single_line(result.get("image_fallback_stage"), 40),
                        "message": _single_line(result.get("image_fallback_message"), 180),
                        "at": now,
                    }
                else:
                    state.pop("last_emotional_vent_image_fallback", None)
                self._qzone_clear_pending_publish_assets(state, "emotional_vent")
                logger.info("[PrivateCompanion] 公开心情动态已发布: intensity=%s text=%s", event_intensity, _single_line(result.get("text") or text, 120))
            else:
                state["last_emotional_vent_failed_at"] = now
                state["last_emotional_vent_status"] = f"failed:{_single_line(result.get('message'), 80)}"
                logger.warning("[PrivateCompanion] 公开心情动态发布失败: %s", _single_line(result.get("message"), 120))
            state["last_emotional_vent_checked_at"] = now
            state["last_emotional_vent_text"] = _single_line(result.get("text") or text, 180)
            state["last_emotional_vent_images"] = _safe_int(result.get("image_count"), len(result.get("images") or []), 0, 99) if result.get("success") else 0
            self._save_data_sync(sections={"qzone_integration"})
        except Exception as exc:
            state["last_emotional_vent_failed_at"] = now
            state["last_emotional_vent_status"] = f"failed:{_single_line(exc, 80)}"
            state["last_emotional_vent_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            logger.warning("[PrivateCompanion] 公开心情动态异常: %s", _single_line(exc, 160), exc_info=True)
