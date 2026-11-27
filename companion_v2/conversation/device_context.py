"""Bounded mobile device status summary for one authorized private model request.

The source is the user's own Reality mobile gateway. The phone reports coarse
device state (online freshness, battery percentage, charging flag, and app
foreground/background state) only after explicit pairing and on-device sharing.
This module projects a compact, private-only summary and never carries device
material into group chats.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math


SNAPSHOT_VERSION = "conversation.device.v1"
MAX_AGE_SECONDS = 30 * 60
STALE_SECONDS = 5 * 60
ALLOWED_APP_STATES = frozenset(("foreground", "background", "inactive", "unknown"))
_APP_STATE_LABELS = {
    "foreground": "应用在前台",
    "background": "应用在后台",
    "inactive": "应用非活跃",
}

DEVICE_CONTEXT_INSTRUCTION = (
    "device_context 是当前私聊对象自己授权共享的手机设备状态摘要（仅含在线状态、电量百分比、是否充电、应用前后台状态与更新时效）："
    "只表示最近一次上报时的设备状态，不代表对方此刻一定正在看手机或处于特定情绪。"
    "当电量偏低（如低于 20% 且未在充电）、深夜仍在用手机，或与当前对话自然相关时，可以像亲近伙伴一样用角色自己的口吻顺口关心一句（例如提醒充电或早点休息）；"
    "不要每轮报数式复读电量或在线状态，不要表现出监控、查岗或盘问口吻，也不要据此编造未提供的活动。"
    "stale=true 或 online=false 时只能作为较早前的参考，不说成此刻状态。"
    "没有数据、授权撤回或上报关闭时直接说不知道，不猜测、不补造，也不提内部字段名。"
    "群聊和其他窗口不得使用这份私聊设备材料。"
)


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


def _normalized_app_state(value):
    if not isinstance(value, str):
        return "unknown"
    cleaned = value.strip().casefold()
    return cleaned if cleaned in ALLOWED_APP_STATES else "unknown"


def project_device_context(snapshot, *, observed_at, max_age_seconds=MAX_AGE_SECONDS):
    """Project one bounded private device summary; ``None`` when unavailable or expired."""
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise ValueError("device context requires a timezone-aware observation time")
    if type(snapshot) is not dict:
        return None
    device = snapshot.get("device")
    if type(device) is not dict or device.get("available") is not True:
        return None

    now_ts = observed_at.timestamp()
    last_report = _number(device.get("last_report_at"))
    if last_report is None:
        last_report = _number(device.get("received_at"))
    captured_at = _number(device.get("captured_at"))
    reported_age = _number(device.get("age_seconds"))

    if last_report is not None and last_report > 0:
        age = max(0.0, now_ts - last_report)
    elif reported_age is not None and reported_age >= 0:
        age = float(reported_age)
    elif captured_at is not None and captured_at > 0:
        age = max(0.0, now_ts - captured_at)
    else:
        return None

    ttl_limit = max(60, int(max_age_seconds))
    if age > ttl_limit:
        return None

    battery_raw = _number(device.get("battery_percent"))
    battery_percent = max(0, min(100, int(round(battery_raw)))) if battery_raw is not None else None
    charging = device.get("charging") if isinstance(device.get("charging"), bool) else None
    app_state = _normalized_app_state(device.get("app_state"))
    age_seconds = max(0, round(age))
    stale = device.get("stale") is True or age_seconds > STALE_SECONDS
    raw_online = device.get("online")
    online = (raw_online is True if isinstance(raw_online, bool) else not stale) and not stale

    if battery_percent is None and app_state == "unknown" and raw_online is None:
        return None

    result = {
        "version": SNAPSHOT_VERSION,
        "basis": "user_authorized_device_status",
        "privacy": "private_only",
        "source": "android_device_status",
        "online": online,
        "app_state": app_state,
        "age_seconds": age_seconds,
        "stale": stale,
    }
    if battery_percent is not None:
        result["battery_percent"] = battery_percent
    if charging is not None:
        result["charging"] = charging
    captured_iso = _iso_epoch(captured_at or last_report)
    if captured_iso:
        result["captured_at"] = captured_iso

    parts = []
    if battery_percent is not None:
        charge_text = "充电中" if charging is True else ("未充电" if charging is False else "")
        parts.append(f"电量 {battery_percent}%" + (f"（{charge_text}）" if charge_text else ""))
    state_label = _APP_STATE_LABELS.get(app_state)
    if state_label:
        parts.append(state_label)
    parts.append("在线" if online else "离线或超过 5 分钟未更新")
    if age_seconds < 60:
        parts.append("刚刚更新")
    else:
        parts.append(f"约 {age_seconds // 60} 分钟前更新")
    result["summary"] = "；".join(parts)
    return result


__all__ = [
    "ALLOWED_APP_STATES",
    "DEVICE_CONTEXT_INSTRUCTION",
    "MAX_AGE_SECONDS",
    "SNAPSHOT_VERSION",
    "STALE_SECONDS",
    "project_device_context",
]
