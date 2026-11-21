"""Pure stdlib time-of-day (分时定价) schedule validator and cross-midnight matcher.

Uses Python stdlib ``zoneinfo.ZoneInfo`` (IANA timezones), validates up to 64
non-overlapping weekly intervals in ``[0, 10080)``, attributes cross-midnight
morning spillover ``[00:00, end)`` strictly to ``previous_weekday``, and never
substitutes ``now()`` when a historical record lacks a timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_PRICING_TIMEZONE = "Asia/Shanghai"
MAX_SCHEDULE_PERIODS = 64
MINUTES_PER_DAY = 1440
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY  # 10080
MIN_SCHEDULE_MULTIPLIER = 0.0
MAX_SCHEDULE_MULTIPLIER = 100.0
COMMON_TIMEZONES: tuple[str, ...] = (
    "Asia/Shanghai",
    "UTC",
    "Asia/Hong_Kong",
    "Asia/Tokyo",
    "Asia/Singapore",
    "America/New_York",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Berlin",
)

_HHMM_RE = re.compile(r"^(\d{2}):(\d{2})\Z")


class PricingScheduleError(ValueError):
    """Raised when a time-of-day pricing schedule is invalid or overlapping."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def validate_timezone(tz_name: object) -> str:
    """Validate an IANA timezone name via stdlib ``zoneinfo.ZoneInfo``."""
    if type(tz_name) is not str:
        raise PricingScheduleError("pricing_schedule_invalid", "时区必须是有效的 IANA 时区字符串")
    clean = tz_name.strip()
    if not clean or len(clean) > 64 or clean != tz_name:
        raise PricingScheduleError("pricing_schedule_invalid", "时区名称格式无效")
    try:
        ZoneInfo(clean)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as error:
        raise PricingScheduleError("pricing_schedule_invalid", f"不支持的 IANA 时区: {clean}") from error
    return clean


def _parse_hhmm(value: object, *, allow_2400: bool = False, label: str = "时间") -> tuple[str, int]:
    if type(value) is not str:
        raise PricingScheduleError("pricing_schedule_invalid", f"{label}必须是 HH:MM 格式字符串")
    match = _HHMM_RE.fullmatch(value.strip())
    if match is None:
        raise PricingScheduleError("pricing_schedule_invalid", f"{label}必须是 HH:MM 格式 (如 23:00)")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if allow_2400 and hour == 24 and minute == 0:
        return "24:00", MINUTES_PER_DAY
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise PricingScheduleError("pricing_schedule_invalid", f"{label}超出有效时间范围: {value}")
    return f"{hour:02d}:{minute:02d}", hour * 60 + minute


def period_weekly_intervals(
    days: list[int],
    *,
    all_day: bool,
    start_min: int,
    end_min: int,
) -> list[tuple[int, int]]:
    """Expand one period into half-open ``[start, end)`` minute intervals in ``[0, 10080)``."""
    intervals: list[tuple[int, int]] = []
    for day in days:
        day_offset = (day - 1) * MINUTES_PER_DAY
        if all_day:
            intervals.append((day_offset, day_offset + MINUTES_PER_DAY))
        elif start_min < end_min:
            intervals.append((day_offset + start_min, day_offset + end_min))
        else:
            # Cross-midnight: [start, 24:00) on `day` + [00:00, end) on next ISO weekday (Sun->Mon wrap).
            intervals.append((day_offset + start_min, day_offset + MINUTES_PER_DAY))
            if end_min > 0:
                next_offset = (day % 7) * MINUTES_PER_DAY
                intervals.append((next_offset, next_offset + end_min))
    return intervals


def normalize_schedule(
    raw: object,
    *,
    rule_validator: Callable[[object], dict[str, Any]],
    default_timezone: str = DEFAULT_PRICING_TIMEZONE,
) -> dict[str, Any]:
    """Validate and normalize a provider/model time-of-day schedule."""
    if type(raw) is not dict:
        raise PricingScheduleError("pricing_schedule_invalid", "分时定价配置必须是对象")
    allowed_top = {"enabled", "timezone", "periods"}
    if set(raw) - allowed_top:
        raise PricingScheduleError("pricing_schedule_invalid", "分时定价配置包含不支持的字段")

    enabled = raw.get("enabled", True)
    if type(enabled) is not bool:
        raise PricingScheduleError("pricing_schedule_invalid", "分时定价启用开关必须是布尔值")

    tz_raw = raw.get("timezone")
    if tz_raw is None or tz_raw == "":
        tz_clean = validate_timezone(default_timezone)
    else:
        tz_clean = validate_timezone(tz_raw)

    raw_periods = raw.get("periods", [])
    if type(raw_periods) is not list:
        raise PricingScheduleError("pricing_schedule_invalid", "分时定价 periods 必须是数组")
    if len(raw_periods) > MAX_SCHEDULE_PERIODS:
        raise PricingScheduleError("pricing_schedule_invalid", f"分时定价最多支持 {MAX_SCHEDULE_PERIODS} 个时段")

    normalized_periods: list[dict[str, Any]] = []
    weekly_spans: list[tuple[int, int, int]] = []  # (start_week_min, end_week_min, period_idx)

    allowed_period_keys = {"id", "name", "days", "all_day", "start", "end", "mode", "multiplier", "override"}
    for idx, item in enumerate(raw_periods):
        if type(item) is not dict:
            raise PricingScheduleError("pricing_schedule_invalid", f"第 {idx + 1} 个分时时段必须是对象")
        if set(item) - allowed_period_keys:
            raise PricingScheduleError("pricing_schedule_invalid", f"第 {idx + 1} 个分时时段包含不支持的字段")

        raw_days = item.get("days")
        if type(raw_days) is not list or not raw_days:
            raise PricingScheduleError("pricing_schedule_invalid", f"第 {idx + 1} 个分时时段必须至少选择一个星期")
        day_set: set[int] = set()
        for day_val in raw_days:
            if type(day_val) is not int or not (1 <= day_val <= 7):
                raise PricingScheduleError("pricing_schedule_invalid", f"第 {idx + 1} 个分时时段星期必须在 1..7 之间")
            day_set.add(day_val)
        days = sorted(day_set)

        all_day = item.get("all_day", False)
        if type(all_day) is not bool:
            raise PricingScheduleError("pricing_schedule_invalid", f"第 {idx + 1} 个分时时段 all_day 必须是布尔值")

        if all_day:
            start_str, end_str = "00:00", "24:00"
            start_min, end_min = 0, MINUTES_PER_DAY
        else:
            start_str, start_min = _parse_hhmm(item.get("start"), allow_2400=False, label=f"第 {idx + 1} 个时段开始时间")
            end_str, end_min = _parse_hhmm(item.get("end"), allow_2400=True, label=f"第 {idx + 1} 个时段结束时间")
            if start_min == end_min:
                raise PricingScheduleError("pricing_schedule_invalid", f"第 {idx + 1} 个分时时段起止时间不能相同")

        mode = item.get("mode")
        if mode is None:
            mode = "override" if item.get("override") is not None else "multiplier"
        if mode not in ("multiplier", "override"):
            raise PricingScheduleError("pricing_schedule_invalid", f"第 {idx + 1} 个分时时段模式仅支持 multiplier 或 override")

        period_id = str(item.get("id") or f"period_{idx + 1}").strip()[:64] or f"period_{idx + 1}"
        period_name = str(item.get("name") or "").strip()[:64]

        entry: dict[str, Any] = {
            "id": period_id,
            "name": period_name,
            "days": days,
            "all_day": all_day,
            "start": start_str,
            "end": end_str,
            "mode": mode,
        }

        if mode == "multiplier":
            mult_raw = item.get("multiplier")
            if isinstance(mult_raw, bool) or type(mult_raw) not in (int, float):
                raise PricingScheduleError("pricing_schedule_invalid", f"第 {idx + 1} 个分时时段倍率必须是数字")
            mult = float(mult_raw)
            if not math.isfinite(mult) or not (MIN_SCHEDULE_MULTIPLIER <= mult <= MAX_SCHEDULE_MULTIPLIER):
                raise PricingScheduleError("pricing_schedule_invalid", f"第 {idx + 1} 个分时时段倍率必须在 0 到 100 之间")
            entry["multiplier"] = round(mult, 6)
        else:
            override_raw = item.get("override")
            if type(override_raw) is not dict:
                raise PricingScheduleError("pricing_schedule_invalid", f"第 {idx + 1} 个分时时段覆盖规则必须是对象")
            entry["override"] = rule_validator(override_raw)

        for span_start, span_end in period_weekly_intervals(
            days, all_day=all_day, start_min=start_min, end_min=end_min
        ):
            weekly_spans.append((span_start, span_end, idx))
        normalized_periods.append(entry)

    # Validate non-overlapping intervals across the entire 7-day [0, 10080) wheel.
    weekly_spans.sort(key=lambda span: (span[0], span[1]))
    for i in range(1, len(weekly_spans)):
        prev_start, prev_end, prev_idx = weekly_spans[i - 1]
        curr_start, curr_end, curr_idx = weekly_spans[i]
        if prev_end > curr_start:
            raise PricingScheduleError(
                "pricing_schedule_overlap",
                f"第 {prev_idx + 1} 个与第 {curr_idx + 1} 个分时时段存在时间重叠",
            )

    return {
        "enabled": enabled,
        "timezone": tz_clean,
        "periods": normalized_periods,
    }


def parse_timestamp_utc(value: object) -> datetime | None:
    """Parse an ISO timestamp or datetime into UTC-aware datetime; never uses ``now()``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif type(value) is str:
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def schedule_is_active(schedule: dict[str, Any] | None) -> bool:
    """Return True iff schedule is enabled and contains at least one period."""
    return (
        isinstance(schedule, dict)
        and schedule.get("enabled", True) is True
        and isinstance(schedule.get("periods"), list)
        and len(schedule["periods"]) > 0
    )


def match_schedule_period(
    schedule: dict[str, Any] | None,
    timestamp: object,
    *,
    default_timezone: str = DEFAULT_PRICING_TIMEZONE,
) -> dict[str, Any] | None:
    """Return the active schedule period for ``timestamp``, or ``None`` if unmatched/missing."""
    if not schedule_is_active(schedule):
        return None
    assert schedule is not None
    utc_dt = parse_timestamp_utc(timestamp)
    if utc_dt is None:
        return None

    tz_name = str(schedule.get("timezone") or default_timezone or DEFAULT_PRICING_TIMEZONE).strip()
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        try:
            tz = ZoneInfo(default_timezone)
        except (ZoneInfoNotFoundError, KeyError, ValueError):
            tz = timezone.utc

    local_dt = utc_dt.astimezone(tz)
    iso_day = local_dt.isoweekday()  # 1 (Mon) .. 7 (Sun)
    prev_day = 7 if iso_day == 1 else iso_day - 1
    minute_of_day = local_dt.hour * 60 + local_dt.minute

    for period in schedule["periods"]:
        days = period.get("days") or ()
        if period.get("all_day") is True:
            if iso_day in days:
                return period
            continue
        try:
            _, start_min = _parse_hhmm(period.get("start"), allow_2400=False)
            _, end_min = _parse_hhmm(period.get("end"), allow_2400=True)
        except PricingScheduleError:
            continue
        if start_min < end_min:
            if iso_day in days and start_min <= minute_of_day < end_min:
                return period
        elif start_min > end_min:
            # Cross-midnight: [start, 24:00) on today's weekday, [00:00, end) on previous_weekday!
            if (iso_day in days and minute_of_day >= start_min) or (
                prev_day in days and minute_of_day < end_min
            ):
                return period
    return None
