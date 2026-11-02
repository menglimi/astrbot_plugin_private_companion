"""防骚扰：按人限流/关键词命中后进入可管理的黑名单。

设置与名单都存在运行库的 ``runtime_metadata`` 里，和跨窗口待办同一种形状：
没有第二套权限，也不改对话表。命中后只是让主流程不再受理这个人的私聊，
名单由页面管理、随时可解除。
"""

from __future__ import annotations

import json
from time import time

SETTINGS_KEY = "relationship.harassment.settings.v1"
BLACKLIST_KEY = "relationship.harassment.blacklist.v1"
MAX_ENTRIES = 2000

DEFAULTS = {
    "enabled": False,
    "window_seconds": 300,
    "max_messages": 20,
    "keywords": "",
    "notice": "",
}
LIMITS = {"window_seconds": (10, 86400), "max_messages": (2, 500)}

_REASONS = {"rate": "短时间消息过多", "keyword": "命中关键词", "manual": "手动加入"}


def _read(runtime, key, fallback):
    row = runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (key,))
    if row is None:
        return fallback
    try:
        value = json.loads(row["value"])
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, dict) else fallback


def _write(runtime, key, value):
    with runtime.transaction() as connection:
        connection.execute(
            "INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        )


def read_settings(runtime):
    stored = _read(runtime, SETTINGS_KEY, {})
    settings = {**DEFAULTS, **{key: stored[key] for key in DEFAULTS if key in stored}}
    if type(settings["enabled"]) is not bool:
        settings["enabled"] = DEFAULTS["enabled"]
    for name, (minimum, maximum) in LIMITS.items():
        value = settings.get(name)
        settings[name] = value if type(value) is int and minimum <= value <= maximum else DEFAULTS[name]
    for name in ("keywords", "notice"):
        if type(settings.get(name)) is not str:
            settings[name] = DEFAULTS[name]
    return settings


def save_settings(runtime, changes):
    if type(changes) is not dict or not changes or set(changes) - set(DEFAULTS):
        raise ValueError("harassment settings have unknown fields")
    current = read_settings(runtime)
    updated = {**current, **changes}
    if type(updated["enabled"]) is not bool:
        raise ValueError("enabled must be boolean")
    for name, (minimum, maximum) in LIMITS.items():
        value = updated[name]
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
    for name in ("keywords", "notice"):
        if type(updated[name]) is not str or len(updated[name]) > 4000:
            raise ValueError(f"{name} must be bounded text")
    _write(runtime, SETTINGS_KEY, updated)
    return updated


def keywords(settings):
    text = settings.get("keywords") or ""
    return tuple(item.strip() for item in text.replace("，", ",").replace("\n", ",").split(",") if item.strip())


def entries(runtime):
    stored = _read(runtime, BLACKLIST_KEY, {})
    items = []
    for key, item in stored.items():
        if not isinstance(item, dict) or not isinstance(item.get("user_id"), str):
            continue
        items.append({"key": key, **item})
    items.sort(key=lambda item: item.get("created_at", 0), reverse=True)
    return items[:MAX_ENTRIES]


def entry_for(runtime, scope, user_id):
    stored = _read(runtime, BLACKLIST_KEY, {})
    item = stored.get(f"{scope}|{user_id}")
    return {"key": f"{scope}|{user_id}", **item} if isinstance(item, dict) else None


def add_entry(runtime, *, scope, user_id, reason, count=0, at=None):
    if type(scope) is not str or not scope or type(user_id) is not str or not user_id:
        raise ValueError("harassment entry needs a scope and user")
    stored = _read(runtime, BLACKLIST_KEY, {})
    key = f"{scope}|{user_id}"
    existing = stored.get(key) if isinstance(stored.get(key), dict) else {}
    item = {
        "scope": scope,
        "user_id": user_id,
        "reason": reason if reason in _REASONS else "manual",
        "reason_text": _REASONS.get(reason, _REASONS["manual"]),
        "count": max(int(count or 0), int(existing.get("count", 0))),
        "created_at": existing.get("created_at") or (time() if at is None else float(at)),
        "updated_at": time() if at is None else float(at),
    }
    stored[key] = item
    if len(stored) > MAX_ENTRIES:
        ordered = sorted(stored.items(), key=lambda pair: pair[1].get("updated_at", 0))
        for stale, _ in ordered[:len(stored) - MAX_ENTRIES]:
            stored.pop(stale, None)
    _write(runtime, BLACKLIST_KEY, stored)
    return {"key": key, **item}


def remove_entry(runtime, *, scope, user_id):
    stored = _read(runtime, BLACKLIST_KEY, {})
    key = f"{scope}|{user_id}"
    if key not in stored:
        return False
    stored.pop(key, None)
    _write(runtime, BLACKLIST_KEY, stored)
    return True


def reason_for(settings, count, text):
    """Return "" to allow, or the trigger reason for one inbound private message."""
    if not settings.get("enabled"):
        return ""
    lowered = (text or "").casefold()
    if any(word.casefold() in lowered for word in keywords(settings)):
        return "keyword"
    if count > settings["max_messages"]:
        return "rate"
    return ""
