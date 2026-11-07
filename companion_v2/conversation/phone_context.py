"""Bounded phone foreground summary for one authorized private model request.

The source is the user's own Reality mobile gateway. The phone reports a
foreground app summary only after the user enables activity reporting and
accepts the on-device consent; the gateway then keeps it for a short TTL. This
module projects that summary for the reply prompt. It never infers behavior,
mood, health or intent from an app name, and it never carries the material into
group chats.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math


SNAPSHOT_VERSION = "conversation.phone.v1"
MAX_LABEL = 60
STALE_SECONDS = 5 * 60

PHONE_CONTEXT_INSTRUCTION = (
    "phone_context 是当前私聊对象自己授权上报的手机前台摘要：只表示最近一次上报时停在哪个应用，"
    "不代表对方此刻一定还在用，也不代表心情、意图、健康或安全状态。"
    "只在当前话题自然相关或对方问起时使用，可以像熟人一样顺手接一句；不要主动播报正在查看对方手机，"
    "不要说得像监控或提醒对方正被看着，也不要据此编造应用内的页面内容、订单或聊天。"
    "age_seconds 越大越可能已经切换；stale=true 时只能说之前看到过，不说成现在。"
    "没有数据、授权撤回或上报关闭时直接说不知道，不猜测、不补造，也不提内部字段名。"
    "群聊和其他窗口不得使用这份私聊材料。"
)


def _text(value, limit=MAX_LABEL):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


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


def project_phone_context(snapshot, *, observed_at):
    """Project one bounded foreground summary; ``None`` when nothing is usable.

    The caller owns the consent decision; this function only validates the data
    shape and the freshness the gateway already reported.
    """
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise ValueError("phone context requires a timezone-aware observation time")
    if type(snapshot) is not dict:
        return None
    activity = snapshot.get("activity")
    if type(activity) is not dict or activity.get("available") is not True:
        return None
    app = _text(activity.get("app_label"))
    title = _text(activity.get("display_title"))
    if not app and not title:
        return None
    if not app or title.casefold() == app.casefold():
        title = ""
    age = _number(activity.get("age_seconds"))
    if age is None:
        last_report = _number(activity.get("last_report_at"))
        if last_report is not None:
            age = max(0.0, observed_at.timestamp() - last_report)
    age_seconds = max(0, round(age)) if age is not None else None
    stale = activity.get("stale") is True or (
        age_seconds is not None and age_seconds > STALE_SECONDS)
    music = activity.get("music") if isinstance(activity.get("music"), dict) else {}
    playing = music.get("playing") is True
    result = {
        "version": SNAPSHOT_VERSION,
        "basis": "user_authorized_foreground_activity",
        "privacy": "private_only",
        "source": "android_usage_summary",
        "app": app or title,
        "captured_at": _iso_epoch(activity.get("captured_at")),
        "age_seconds": age_seconds,
        "stale": stale,
    }
    category = _text(activity.get("category"), 24)
    if category and category != "other":
        result["category"] = category
    if title:
        result["title"] = title
    if playing:
        result["music"] = _text(music.get("source"), MAX_LABEL) or "正在播放"
    moments = [result["app"]]
    if result["age_seconds"] is not None:
        moments.append(("约 %d 秒前上报" % result["age_seconds"]) if not stale
                       else ("已超过 5 分钟未更新"))
    result["summary"] = "；".join(moments)
    return result


__all__ = [
    "MAX_LABEL", "PHONE_CONTEXT_INSTRUCTION", "SNAPSHOT_VERSION", "STALE_SECONDS",
    "project_phone_context",
]
