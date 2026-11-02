"""防骚扰：判断、处置与黑名单目录。

三个概念分开存放，避免"检测方式"和"处置动作"互相复制：

* 判断：关键词 / 正则 / 行为信号；命中只产出原因与证据；
* 处置：按命中次数（strike）统一决定 记录 / 冷却 / 拉黑；
* 名单：持久化的冷却与拉黑条目，带来源、证据、期限。

设置、名单、审计都放在运行库 ``runtime_metadata`` 里（与跨窗口待办同一种
形状），不新增表、不做迁移；旧版 ``relationship.harassment.settings.v1``
读取时会自动映射到新字段。
"""

from __future__ import annotations

import json
import re
from time import time

SETTINGS_KEY = "relationship.harassment.settings.v1"
BLACKLIST_KEY = "relationship.harassment.blacklist.v1"
AUDIT_KEY = "relationship.harassment.audit.v1"
MAX_ENTRIES = 2000
MAX_AUDIT = 300
MAX_KEYWORDS = 4000

DEFAULTS = {
    "enabled": False,
    "dry_run": True,
    "keywords_strong": "",
    "keywords_weak": "",
    "keyword_regex": "",
    "judge_enabled": False,
    "judge_threshold": 0.7,
    "model_tag_enabled": False,
    "rate_enabled": True,
    "rate_window_seconds": 300,
    "rate_max_messages": 20,
    "burst_enabled": True,
    "burst_window_seconds": 10,
    "burst_max_messages": 6,
    "duplicate_enabled": True,
    "duplicate_window_seconds": 60,
    "duplicate_max_repeats": 5,
    "mention_enabled": True,
    "mention_window_seconds": 60,
    "mention_max": 6,
    "link_enabled": True,
    "link_window_seconds": 120,
    "link_max": 3,
    "media_enabled": False,
    "media_window_seconds": 120,
    "media_max": 8,
    "split_enabled": False,
    "split_window_seconds": 60,
    "split_max": 12,
    "cooldown_seconds": 600,
    "auto_block_hours": 168,
    "strike_decay_hours": 24,
    "strike_block_at": 2,
    "strike_permanent_at": 3,
    "notice_mode": "silent",
    "notice_text": "",
    "notice_cooldown_hours": 6,
    "exempt_admins": True,
    "exempt_ids": "",
    "audit_retention_days": 30,
}

INT_LIMITS = {
    "rate_window_seconds": (10, 86400),
    "rate_max_messages": (2, 500),
    "burst_window_seconds": (2, 120),
    "burst_max_messages": (2, 100),
    "duplicate_window_seconds": (10, 3600),
    "duplicate_max_repeats": (2, 100),
    "mention_window_seconds": (10, 3600),
    "mention_max": (2, 100),
    "link_window_seconds": (10, 3600),
    "link_max": (1, 50),
    "media_window_seconds": (10, 3600),
    "media_max": (2, 100),
    "split_window_seconds": (10, 3600),
    "split_max": (2, 100),
    "cooldown_seconds": (30, 86400),
    "auto_block_hours": (0, 8760),
    "strike_decay_hours": (1, 720),
    "strike_block_at": (1, 10),
    "strike_permanent_at": (1, 12),
    "notice_cooldown_hours": (0, 168),
    "audit_retention_days": (1, 365),
}
BOOLEAN_FIELDS = (
    "enabled", "dry_run", "judge_enabled", "model_tag_enabled", "rate_enabled", "burst_enabled",
    "duplicate_enabled", "mention_enabled", "link_enabled", "media_enabled", "split_enabled",
    "exempt_admins",
)
TEXT_FIELDS = ("keywords_strong", "keywords_weak", "keyword_regex", "notice_text", "exempt_ids")

REASONS = {
    "keyword": "关键词",
    "regex": "正则命中",
    "judge": "模型判定",
    "model_tag": "模型标签",
    "rate": "频率过高",
    "burst": "短时突发",
    "duplicate": "复读",
    "mention": "@轰炸",
    "link": "链接轰炸",
    "media": "媒体刷屏",
    "split": "拆句轰炸",
    "manual": "手动",
}
REASON_GROUP = {
    "keyword": "骚扰", "regex": "骚扰", "judge": "骚扰", "model_tag": "骚扰", "manual": "骚扰",
    "rate": "刷屏", "burst": "刷屏", "duplicate": "复读", "mention": "刷屏",
    "link": "广告", "media": "刷屏", "split": "刷屏",
}

_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


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


def _legacy_map(stored):
    """旧版只有 enabled/window_seconds/max_messages/keywords/notice，自动映射。"""
    mapped = {}
    if "window_seconds" in stored:
        mapped["rate_window_seconds"] = stored["window_seconds"]
    if "max_messages" in stored:
        mapped["rate_max_messages"] = stored["max_messages"]
    if "keywords" in stored:
        mapped["keywords_weak"] = stored["keywords"]
    if "notice" in stored:
        mapped["notice_text"] = stored["notice"]
    return mapped


def read_settings(runtime):
    stored = _read(runtime, SETTINGS_KEY, {})
    stored = {**_legacy_map(stored), **stored}
    settings = {**DEFAULTS, **{key: stored[key] for key in DEFAULTS if key in stored}}
    for name in BOOLEAN_FIELDS:
        if type(settings.get(name)) is not bool:
            settings[name] = DEFAULTS[name]
    for name, (minimum, maximum) in INT_LIMITS.items():
        value = settings.get(name)
        settings[name] = value if type(value) is int and minimum <= value <= maximum else DEFAULTS[name]
    for name in TEXT_FIELDS:
        if type(settings.get(name)) is not str:
            settings[name] = DEFAULTS[name]
    try:
        threshold = float(settings.get("judge_threshold"))
    except (TypeError, ValueError):
        threshold = DEFAULTS["judge_threshold"]
    settings["judge_threshold"] = threshold if 0.05 <= threshold <= 0.99 else DEFAULTS["judge_threshold"]
    if settings.get("notice_mode") not in ("silent", "text"):
        settings["notice_mode"] = DEFAULTS["notice_mode"]
    return settings


def save_settings(runtime, changes):
    if type(changes) is not dict or not changes or set(changes) - set(DEFAULTS):
        raise ValueError("harassment settings have unknown fields")
    current = read_settings(runtime)
    updated = {**current, **changes}
    for name in BOOLEAN_FIELDS:
        if type(updated[name]) is not bool:
            raise ValueError(f"{name} must be boolean")
    for name, (minimum, maximum) in INT_LIMITS.items():
        value = updated[name]
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
    for name in TEXT_FIELDS:
        if type(updated[name]) is not str or len(updated[name]) > MAX_KEYWORDS:
            raise ValueError(f"{name} must be bounded text")
    threshold = updated["judge_threshold"]
    if type(threshold) not in (int, float) or not 0.05 <= float(threshold) <= 0.99:
        raise ValueError("judge_threshold must be between 0.05 and 0.99")
    if updated["notice_mode"] not in ("silent", "text"):
        raise ValueError("notice_mode is unsupported")
    _write(runtime, SETTINGS_KEY, updated)
    return updated


def _items(text):
    text = (text or "").replace("，", ",").replace("\n", ",")
    return tuple(item.strip() for item in text.split(",") if item.strip())


def keywords_strong(settings):
    return _items(settings.get("keywords_strong"))


def keywords_weak(settings):
    return _items(settings.get("keywords_weak"))


def regex_hit(settings, text):
    pattern = settings.get("keyword_regex") or ""
    if not pattern:
        return ""
    try:
        match = re.search(pattern, text or "")
    except re.error:
        return ""
    return match.group(0)[:80] if match else ""


def urls(text):
    return tuple(match[:2048] for match in _URL.findall(text or ""))


def now_value(at=None):
    return time() if at is None else float(at)


def _prune_expired(stored, now):
    expired = [key for key, item in stored.items()
               if isinstance(item, dict) and item.get("until") and item["until"] <= now]
    for key in expired:
        stored.pop(key, None)
    return bool(expired)


def entries(runtime, at=None):
    now = now_value(at)
    stored = _read(runtime, BLACKLIST_KEY, {})
    if _prune_expired(stored, now):
        _write(runtime, BLACKLIST_KEY, stored)
    items = []
    for key, item in stored.items():
        if not isinstance(item, dict) or not isinstance(item.get("user_id"), str):
            continue
        items.append({"key": key, **item})
    items.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
    return items[:MAX_ENTRIES]


def entry_for(runtime, scope, user_id, at=None):
    now = now_value(at)
    stored = _read(runtime, BLACKLIST_KEY, {})
    key = f"{scope}|{user_id}"
    item = stored.get(key)
    if not isinstance(item, dict):
        return None
    if item.get("until") and item["until"] <= now:
        stored.pop(key, None)
        _write(runtime, BLACKLIST_KEY, stored)
        return None
    return {"key": key, **item}


def add_entry(runtime, *, scope, user_id, kind, reasons, evidence="", strike=1, hours=0,
              seconds=0, at=None):
    if type(scope) is not str or not scope or type(user_id) is not str or not user_id:
        raise ValueError("harassment entry needs a scope and user")
    if kind not in ("cooldown", "block"):
        raise ValueError("harassment entry kind is unsupported")
    now = now_value(at)
    stored = _read(runtime, BLACKLIST_KEY, {})
    _prune_expired(stored, now)
    key = f"{scope}|{user_id}"
    existing = stored.get(key) if isinstance(stored.get(key), dict) else {}
    if seconds:
        until = now + int(seconds)
    else:
        until = 0 if not hours else now + int(hours) * 3600
    item = {
        "scope": scope,
        "user_id": user_id,
        "kind": kind,
        "reasons": [reason for reason in reasons if reason in REASONS],
        "evidence": str(evidence)[:500],
        "strike": int(strike),
        "until": until,
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
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


def extend_entry(runtime, *, scope, user_id, hours, at=None):
    now = now_value(at)
    stored = _read(runtime, BLACKLIST_KEY, {})
    key = f"{scope}|{user_id}"
    item = stored.get(key)
    if not isinstance(item, dict):
        return None
    item["until"] = 0 if not hours else now + int(hours) * 3600
    item["updated_at"] = now
    _write(runtime, BLACKLIST_KEY, stored)
    return {"key": key, **item}


def audit(runtime):
    stored = _read(runtime, AUDIT_KEY, {})
    items = [item for item in stored.values() if isinstance(item, dict)]
    items.sort(key=lambda item: item.get("at", 0), reverse=True)
    return items[:MAX_AUDIT]


def record_audit(runtime, *, scope, user_id, action, reasons, evidence="", at=None):
    now = now_value(at)
    stored = _read(runtime, AUDIT_KEY, {})
    key = f"{int(now * 1000000)}-{len(stored)}"
    stored[key] = {
        "at": now,
        "scope": scope,
        "user_id": user_id,
        "action": action,
        "reasons": [reason for reason in reasons if reason in REASONS],
        "evidence": str(evidence)[:300],
    }
    if len(stored) > MAX_AUDIT:
        ordered = sorted(stored.items(), key=lambda pair: pair[1].get("at", 0))
        for stale, _ in ordered[:len(stored) - MAX_AUDIT]:
            stored.pop(stale, None)
    _write(runtime, AUDIT_KEY, stored)


def strike_for(settings, state, now):
    """返回 (strike, 是否已被衰减)。"""
    hits = int(state.get("strikes", 0))
    last = float(state.get("last_strike", 0))
    if hits and now - last > settings["strike_decay_hours"] * 3600:
        return 0, True
    return hits, False


def action_for(settings, strike, strong=False):
    """把命中次数映射成动作：冷却 / 拉黑 / 永久。"""
    if strong:
        return "block", 0 if strike >= settings["strike_permanent_at"] else settings["auto_block_hours"]
    if strike >= settings["strike_permanent_at"]:
        return "block", 0
    if strike >= settings["strike_block_at"]:
        return "block", settings["auto_block_hours"]
    return "cooldown", 0


def evidence_text(reasons, details=""):
    parts = [REASONS.get(reason, reason) for reason in reasons]
    if details:
        parts.append(details)
    return "；".join(parts)
