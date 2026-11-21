"""Bounded, desensitized mobile location summary for one private model request.

The source is the user's own Reality mobile gateway. The phone shares foreground
location only after explicit pairing and on-device sharing; the gateway keeps it
in short-lived memory. This module projects only coarse, desensitized clues
(matched place label, district-level area, stay duration, update freshness and
sanitized activity note). It never exposes coordinates, street addresses or
arbitrary free-text labels, and it never carries the material into group chats.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math


SNAPSHOT_VERSION = "conversation.location.v1"
MAX_AGE_SECONDS = 30 * 60
STALE_SECONDS = 15 * 60
MAX_PLACE_NAME = 40
MAX_PLACE_KIND = 24
MAX_AREA_LABEL = 80
MAX_ACTIVITY_NOTE = 32
ACTIVITY_NOTE_PREFIXES = ("活动备注：", "活动备注:")
ACTIVITY_NOTE_BLOCKLIST = (
    "忽略", "系统提示", "提示词", "指令", "prompt", "instruction", "执行命令",
)

LOCATION_CONTEXT_INSTRUCTION = (
    "location_context 是当前私聊对象自己授权共享的脱敏位置摘要（仅含标记地点、行政区级区域、停留时长、更新时效与可选活动备注，不含精确坐标）："
    "只表示最近一次上报时的位置线索，不代表对方此刻一定还在原地，也不代表心情、意图、行程安排或安全状态。"
    "只在当前话题自然相关或对方主动问起时自然使用，不要主动播报或强调自己在查看对方定位，"
    "不要表现出监控、盘问或催促行踪的口吻，也不要编造具体街道、门牌、商家或同行人。"
    "updated_minutes_ago 越大越可能已经移动；stale=true 时只能说之前看到过，不说成此刻一定还在。"
    "没有数据、授权撤回、已过期或上报关闭时直接说不知道，不猜测、不补造，也不提内部字段名。"
    "群聊和其他窗口不得使用这份私聊位置材料。"
)


def _text(value, limit):
    if not isinstance(value, str):
        return ""
    cleaned = value.replace("\x00", " ").replace("<", " ").replace(">", " ")
    return " ".join(cleaned.split())[:limit]


def _number(value):
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return value


def _iso_epoch(value):
    number = _number(value)
    if number is None or number <= 0:
        return ""
    try:
        return datetime.fromtimestamp(number, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def sanitize_activity_note(raw_value, *, require_prefix=True):
    """Sanitize a user-authored activity note while rejecting prompt injection.

    When ``require_prefix`` is True (gateway ``label`` field), only strings
    explicitly prefixed with ``活动备注：`` / ``活动备注:`` are accepted so
    arbitrary device free-text labels never cross the prompt boundary.
    """
    if not isinstance(raw_value, str):
        return ""
    cleaned = " ".join(raw_value.replace("\x00", " ").split())[:80]
    if not cleaned:
        return ""
    body = cleaned
    if require_prefix:
        matched_prefix = next((prefix for prefix in ACTIVITY_NOTE_PREFIXES if cleaned.startswith(prefix)), "")
        if not matched_prefix:
            return ""
        body = cleaned[len(matched_prefix):]
    else:
        for prefix in ACTIVITY_NOTE_PREFIXES:
            if body.startswith(prefix):
                body = body[len(prefix):]
                break
    note = _text(body, MAX_ACTIVITY_NOTE)
    if not note:
        return ""
    lowered = note.casefold()
    if any(marker in lowered for marker in ACTIVITY_NOTE_BLOCKLIST):
        return ""
    return note


def project_location_context(snapshot, *, observed_at, max_age_seconds=MAX_AGE_SECONDS):
    """Project one bounded, coordinate-free location summary; ``None`` when unusable.

    The caller owns the private-window consent decision; this function enforces
    TTL freshness, strips all coordinates/street details and validates shapes.
    """
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise ValueError("location context requires a timezone-aware observation time")
    if type(snapshot) is not dict:
        return None
    location = snapshot.get("location")
    if type(location) is not dict or location.get("available") is not True:
        return None

    now_ts = observed_at.timestamp()
    received_at = _number(location.get("received_at"))
    if received_at is None:
        received_at = _number(location.get("last_report_at"))
    captured_at = _number(location.get("captured_at"))
    reported_age = _number(location.get("age_seconds"))

    updated_ts = received_at if (received_at is not None and received_at > 0) else (
        captured_at if (captured_at is not None and captured_at > 0) else None
    )
    if received_at is not None and received_at > 0:
        updated_age = max(0.0, now_ts - received_at)
    elif reported_age is not None and reported_age >= 0:
        updated_age = float(reported_age)
    elif captured_at is not None and captured_at > 0:
        updated_age = max(0.0, now_ts - captured_at)
    else:
        return None

    ttl_limit = max(60, int(max_age_seconds))
    if updated_age > ttl_limit:
        return None

    place = location.get("place") if isinstance(location.get("place"), dict) else {}
    matched = place.get("matched") is True
    place_name = _text(place.get("name"), MAX_PLACE_NAME) if matched else ""
    place_kind = _text(place.get("kind"), MAX_PLACE_KIND) if matched else ""
    area_label = _text(place.get("area_label"), MAX_AREA_LABEL)
    activity_note = sanitize_activity_note(location.get("activity_note"), require_prefix=False)
    if not activity_note:
        activity_note = sanitize_activity_note(location.get("label"), require_prefix=True)

    if not place_name and not area_label and not activity_note:
        return None

    arrived_at = _number(location.get("arrived_at"))
    stay_seconds_raw = _number(location.get("stay_seconds"))
    if arrived_at is not None and 0 < arrived_at <= now_ts:
        stay_seconds = max(0, round(now_ts - arrived_at))
    elif stay_seconds_raw is not None and stay_seconds_raw >= 0:
        stay_seconds = max(0, round(stay_seconds_raw))
    elif captured_at is not None and 0 < captured_at <= now_ts:
        stay_seconds = max(0, round(now_ts - captured_at))
    else:
        stay_seconds = None

    updated_minutes_ago = max(0, int(updated_age // 60))
    stay_minutes = max(0, int(stay_seconds // 60)) if stay_seconds is not None else None
    stale = updated_age > STALE_SECONDS

    result = {
        "version": SNAPSHOT_VERSION,
        "basis": "user_authorized_foreground_location",
        "privacy": "private_only",
        "source": "android_foreground_location",
        "updated_minutes_ago": updated_minutes_ago,
        "stale": stale,
    }
    updated_at = _iso_epoch(updated_ts)
    if updated_at:
        result["updated_at"] = updated_at
    if place_name:
        result["place_name"] = place_name
        if place_kind:
            result["place_kind"] = place_kind
    if area_label:
        result["area_label"] = area_label
    if stay_minutes is not None:
        result["stay_minutes"] = stay_minutes
    if activity_note:
        result["activity_note"] = activity_note

    parts = []
    if place_name:
        parts.append("地点：" + place_name)
    if area_label and area_label != place_name:
        parts.append("区域：" + area_label)
    if stay_minutes is not None and stay_minutes > 0:
        parts.append(f"已停留约 {stay_minutes} 分钟")
    if updated_minutes_ago == 0:
        parts.append("刚刚更新")
    else:
        parts.append(f"约 {updated_minutes_ago} 分钟前更新")
    if activity_note:
        parts.append("活动备注：" + activity_note)
    result["summary"] = "；".join(parts)
    return result


__all__ = [
    "ACTIVITY_NOTE_BLOCKLIST",
    "ACTIVITY_NOTE_PREFIXES",
    "LOCATION_CONTEXT_INSTRUCTION",
    "MAX_ACTIVITY_NOTE",
    "MAX_AGE_SECONDS",
    "MAX_AREA_LABEL",
    "MAX_PLACE_KIND",
    "MAX_PLACE_NAME",
    "SNAPSHOT_VERSION",
    "STALE_SECONDS",
    "project_location_context",
    "sanitize_activity_note",
]
