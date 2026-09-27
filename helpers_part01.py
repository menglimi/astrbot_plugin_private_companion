# -*- coding: utf-8 -*-
"""helpers 拆分件 part01（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 helpers.py，仅调整模块级依赖的导入来源。
"""
import json
import re
import ipaddress
import socket
import time
import unicodedata
import zoneinfo
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse


_today_key_timezone = ""


_GROUP_MESSAGE_URL_PATTERN = re.compile(
    r"(?i)(?<![\w@])(?:https?://|www\.)[^\s<>\"“”‘’]+"
)

_GROUP_SHARE_MARKER_PATTERN = re.compile(
    r"(?i)(?:"
    r"[\[【](?:分享|链接|网页|网页分享|卡片|小程序|QQ小程序|转发消息|合并转发|JSON消息|XML消息)[\]】]"
    r"|\[CQ:(?:json|xml|share|miniapp)\b[^\]]*\]"
    r")"
)

_GROUP_SHARE_BOILERPLATE_PATTERN = re.compile(
    r"(?i)(?:"
    r"当前\s*QQ\s*版本不支持(?:此|该)?应用[，,、\s]*请升级"
    r"|当前\s*QQ\s*版本不支持查看(?:此|该)?内容[，,、\s]*请升级"
    r"|(?:你的|您(?:的)?|当前)?\s*QQ\s*版本过低[，,、\s]*"
    r"(?:暂不支持查看(?:此|该)?内容|请(?:升级|更新)(?:后)?查看)"
    r"|请使用最新版本(?:手机)?\s*QQ\s*查看"
    r")"
)


def _now_ts() -> float:
    return time.time()


def _normalize_timezone_name(timezone_name: Any, default: str = "Asia/Shanghai") -> str:
    candidate = str(timezone_name or "").strip() or default
    try:
        zoneinfo.ZoneInfo(candidate)
        return candidate
    except Exception:
        return default


def _normalize_timezone_setting(timezone_name: Any) -> str:
    candidate = str(timezone_name or "").strip()
    if candidate.lower() in {"", "global", "astrbot", "auto", "follow_global"}:
        return "global"
    return _normalize_timezone_name(candidate)


def _resolve_timezone_setting(
    timezone_name: Any,
    *,
    global_timezone: Any = "",
    system_timezone: Any = "",
) -> str:
    configured = _normalize_timezone_setting(timezone_name)
    if configured != "global":
        return configured
    for candidate in (global_timezone, system_timezone):
        normalized = _normalize_timezone_name(candidate, "")
        if normalized:
            return normalized
    return "Asia/Shanghai"


def _set_today_key_timezone(timezone_name: Any) -> None:
    global _today_key_timezone
    _today_key_timezone = _normalize_timezone_name(timezone_name)


def _today_key() -> str:
    if _today_key_timezone:
        try:
            return datetime.now(zoneinfo.ZoneInfo(_today_key_timezone)).strftime("%Y-%m-%d")
        except Exception:
            pass
    return datetime.now().strftime("%Y-%m-%d")


def _day_start_ts(value: float | None = None) -> float:
    """Return the epoch timestamp of the local midnight of the day containing value.

    Uses the plugin-configured timezone (same source as _today_key) so that
    “今天”的分界与时区一致，避免用 UTC 零点切分造成偏差。
    """
    ts = time.time() if value is None else float(value)
    if _today_key_timezone:
        try:
            local = datetime.fromtimestamp(ts, zoneinfo.ZoneInfo(_today_key_timezone))
            return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        except Exception:
            pass
    local = datetime.fromtimestamp(ts)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _date_key(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def _safe_int(value: Any, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _unanswered_proactive_count(user: Any) -> int:
    """Return the durable unanswered count while mirroring the legacy key."""
    if not isinstance(user, dict):
        return 0
    if "unanswered_proactive_count" in user:
        count = _safe_int(user.get("unanswered_proactive_count"), 0, 0, 1000)
    else:
        count = _safe_int(user.get("ignored_streak"), 0, 0, 1000)
    user["unanswered_proactive_count"] = count
    user["ignored_streak"] = count
    return count


def _record_unanswered_proactive(user: Any, *, sent_at: float = 0.0) -> int:
    """Increment the durable unanswered count after an awaiting-reply send."""
    if not isinstance(user, dict):
        return 0
    count = _unanswered_proactive_count(user) + 1
    user["unanswered_proactive_count"] = count
    user["ignored_streak"] = count
    if sent_at > 0:
        user["unanswered_proactive_count_updated_at"] = float(sent_at)
    return count


def _reset_unanswered_proactive(user: Any, *, replied_at: float = 0.0) -> None:
    """Clear the durable unanswered count after user activity."""
    if not isinstance(user, dict):
        return
    user["unanswered_proactive_count"] = 0
    user["ignored_streak"] = 0
    if replied_at > 0:
        user["unanswered_proactive_count_updated_at"] = float(replied_at)


def _safe_float(
    value: Any,
    default: float,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _single_line(text: Any, limit: int = 80) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    return normalized[:limit]


_ADDRESS_SEPARATOR_PATTERN = re.compile(r"[/／、,，;；|]+")

_ADDRESS_TERM_STRIP_CHARS = " -*`_【】[]（）()<>《》\"'“”‘’"

_ADDRESS_TERM_TAIL_PATTERN = re.compile(r"[：:，,。.!！?？~～…]+$")


def _clean_address_term(value: Any) -> str:
    """Normalize a single display name or address alias."""
    token = unicodedata.normalize("NFKC", str(value or "")).strip()
    token = re.sub(r"\s+", " ", token)
    token = token.strip(_ADDRESS_TERM_STRIP_CHARS)
    token = token.lstrip("：:")
    token = _ADDRESS_TERM_TAIL_PATTERN.sub("", token).strip()
    return token


def _split_address_terms(text: Any, limit: int = 8) -> list[str]:
    """Split slash/separator-delimited aliases into stable, unique terms.

    Users can configure several accepted addresses at once, for example
    "诗岸/宝宝" or "老板，Sir".  Treating such a value as one token makes
    every consumer that matches or injects an address fall back to the first
    entry only, so callers share a single splitting rule here.
    """
    normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
    if not normalized:
        return []
    normalized = re.sub(r"\s+", " ", normalized)
    terms: list[str] = []
    for part in _ADDRESS_SEPARATOR_PATTERN.split(normalized):
        token = _clean_address_term(part)
        if not token or token.isdigit() or len(token) > 40:
            continue
        if token not in terms:
            terms.append(token)
    return terms[:limit]


def _single_address(text: Any, limit: int = 40) -> str:
    """Collapse delimited aliases into one joined display address."""
    terms = _split_address_terms(text, limit)
    return "、".join(terms)[:limit]


def _url_host_is_public(url: Any) -> bool:
    """Accept only HTTP(S) URLs whose DNS results are all public addresses."""
    text = str(url or "").strip()
    if not re.match(r"^https?://", text, flags=re.I):
        return False
    try:
        host = urlparse(text).hostname or ""
    except ValueError:
        return False
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError, ValueError):
        return False
    addresses = {
        info[4][0]
        for info in infos
        if isinstance(info, tuple) and len(info) >= 5 and info[4]
    }
    if not addresses:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(str(address).split("%", 1)[0])
        except ValueError:
            return False
        mapped = getattr(ip, "ipv4_mapped", None)
        for candidate in (ip, mapped):
            if candidate is None:
                continue
            if (
                candidate.is_private
                or candidate.is_loopback
                or candidate.is_link_local
                or candidate.is_reserved
                or candidate.is_multicast
                or candidate.is_unspecified
            ):
                return False
    return True


def normalize_bot_relationship_cards(value: Any, *, limit: int = 16) -> list[str]:
    """Normalize relationship cards shared by config, page API, and prompt injection."""
    if isinstance(value, (list, tuple)):
        raw_lines = [str(item or "") for item in value]
    else:
        raw_lines = str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    cards: list[str] = []
    seen_names: set[str] = set()
    max_cards = max(0, int(limit))
    if max_cards == 0:
        return cards
    for raw_line in raw_lines:
        parts = [
            _single_line(part, 200)
            for part in re.split(r"\s*(?:\|\||｜｜)\s*", raw_line, maxsplit=2)
        ]
        name = parts[0] if parts else ""
        if not name:
            continue
        name_key = name.casefold()
        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        relation = parts[1] if len(parts) > 1 else ""
        appearance = parts[2] if len(parts) > 2 else ""
        cards.append(f"{name} || {relation} || {appearance}")
        if len(cards) >= max_cards:
            break
    return cards


PHOTO_GENERATION_SCOPE_VALUES = (
    "private_owner",
    "private_friend",
    "group",
    "proactive",
)


def normalize_photo_generation_scopes(
    value: Any,
    *,
    default_if_missing: bool = False,
) -> list[str]:
    """Normalize every persisted/UI representation of the photo scope list."""
    if value is None:
        raw_items: Any = PHOTO_GENERATION_SCOPE_VALUES if default_if_missing else ()
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raw_items = ()
        else:
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = text
            if isinstance(parsed, (list, tuple, set)):
                raw_items = parsed
            else:
                raw_items = re.split(r"(?:\r?\n|\\n|[,，、;；])+", str(parsed or ""))
    elif isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = ()

    selected = {
        str(item or "").strip().lower()
        for item in raw_items
        if str(item or "").strip().lower() in PHOTO_GENERATION_SCOPE_VALUES
    }
    return [scope for scope in PHOTO_GENERATION_SCOPE_VALUES if scope in selected]


def _photo_group_request_matches(text: Any) -> bool:
    """Return whether a photo request explicitly asks multiple people to share the frame."""
    source = _single_line(text, 1600)
    if not source:
        return False
    compact = re.sub(r"\s+", "", source).lower()
    compact = re.sub(
        r"(?:不要|不想要?|不需要|无需|避免|禁止|拒绝|别|不是|并非)(?:生成|画|拍|做|来)?"
        r"(?:任何)?(?:合影|合照|双人照|双人自拍|多人照|多人自拍|大合照|一起入镜|一同入镜|"
        r"两人同框|二人同框|多人同框|同框照|情侣照|情侣写真)",
        "",
        compact,
    )
    compact = re.sub(
        r"(?:不要|不想要?|不需要|无需|避免|禁止|拒绝|别|不是|并非)"
        r"(?:(?:我(?:们|俩)?|咱(?:们|俩)?)(?:和|跟|与)[^，。！？]{1,18}|两个人|两位|三个人|大家|朋友们|一家人)"
        r".{0,18}(?:一起)?(?:拍照|自拍|拍张照片|拍一张照片|照片|相片|写真|同框|入镜)",
        "",
        compact,
    )
    english_source = re.sub(
        r"\b(?:no|not|avoid|without|do\s+not\s+(?:make|generate|draw|show)?)\s+"
        r"(?:a\s+)?(?:group|couple|two[-\s]+person|multi[-\s]+person)\s+"
        r"(?:photo|portrait|selfie)\b",
        " ",
        source,
        flags=re.I,
    )
    if any(
        marker in compact
        for marker in (
            "合影",
            "合照",
            "双人照",
            "双人自拍",
            "多人照",
            "多人自拍",
            "大合照",
            "一起入镜",
            "一同入镜",
            "两人同框",
            "二人同框",
            "多人同框",
            "同框照",
            "情侣照",
            "情侣写真",
        )
    ):
        return True
    if re.search(
        r"(?:(?:我(?:们|俩)?|咱(?:们|俩)?)(?:和|跟|与)[^，。！？]{1,18}|两个人|两位|三个人|大家|朋友们|一家人)"
        r".{0,18}(?:一起)?(?:拍照|自拍|拍张照片|拍一张照片|照片|相片|写真|同框|入镜)",
        compact,
    ):
        return True
    return bool(
        re.search(
            r"\b(?:group\s+(?:photo|portrait|selfie)|two[-\s]+person\s+(?:photo|portrait|selfie)|"
            r"couple\s+(?:photo|portrait|selfie)|(?:photo|portrait|selfie)\s+of\s+us\s+together|"
            r"(?:both|two\s+people)\s+in\s+(?:the\s+)?(?:same\s+)?(?:frame|photo)|"
            r"(?:photo|portrait|selfie)\s+(?:with|of)\s+(?:my\s+)?(?:friend|partner|family)|"
            r"(?:me|us)\s+(?:and|with)\s+[^,.!?]{1,40}\s+(?:photo|portrait|selfie))\b",
            english_source,
            flags=re.I,
        )
    )


def _path_text(value: Any, limit: int = 1000) -> str:
    """Normalize a configured path without changing legal internal whitespace."""
    normalized = str(value or "").replace("\r", "").replace("\n", "").strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {'"', "'"}:
        normalized = normalized[1:-1].strip()
    return normalized[:limit] if limit > 0 else normalized


def _normalize_photo_subject_owner(value: Any) -> str:
    normalized = _single_line(value, 40).strip().lower().replace("-", "_")
    if normalized in {"bot", "self", "persona", "character", "当前人格", "机器人", "角色本人"}:
        return "bot"
    if normalized in {"third_party", "thirdparty", "other_person", "第三方", "第三方人物", "其他人物"}:
        return "third_party"
    if normalized in {"scene", "object", "animal", "environment", "画面", "物体", "动物", "环境"}:
        return "scene"
    if normalized in {"unknown", "unclear", "ambiguous", "未知", "不明", "无法判断"}:
        return "unknown"
    return ""


def _photo_subject_owner_prompt_label(value: Any) -> str:
    owner = _normalize_photo_subject_owner(value) or "unknown"
    return {
        "bot": "Bot/当前人格（图片描述中的“我/她/角色本人”）",
        "third_party": "画面中的第三方人物（不是 Bot，也不是用户）",
        "scene": "画面中的物体、动物或环境主体（不是用户）",
        "unknown": "画面中的实际主体（归属不明，但不能据此归到用户）",
    }[owner]


def _group_link_message_context(text: Any, limit: int = 260) -> tuple[str, bool]:
    """Return non-link user text and whether the message contains a link/share payload."""
    raw = str(text or "")[:4000].replace("\u200b", "").replace("\ufeff", "")
    has_link_payload = bool(
        _GROUP_MESSAGE_URL_PATTERN.search(raw)
        or _GROUP_SHARE_MARKER_PATTERN.search(raw)
        or _GROUP_SHARE_BOILERPLATE_PATTERN.search(raw)
    )
    if not has_link_payload:
        return _single_line(raw, limit), False
    remainder = _GROUP_MESSAGE_URL_PATTERN.sub(" ", raw)
    remainder = _GROUP_SHARE_MARKER_PATTERN.sub(" ", remainder)
    remainder = _GROUP_SHARE_BOILERPLATE_PATTERN.sub(" ", remainder)
    remainder = re.sub(r"(?i)(?:网页)?(?:链接|网址|link)\s*[:：]", " ", remainder)
    remainder = re.sub(r"\s+", " ", remainder).strip(" \t\r\n,，。;；|｜-—")
    return _single_line(remainder, limit), True


_SECRET_FIELD_PATTERN = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|auth[_ -]?token|secret|password|passwd|cookie|密钥|令牌|口令)",
    flags=re.IGNORECASE,
)


def _runtime_secret_values(owner: Any) -> list[str]:
    """Collect configured credentials without exposing them to logs or prompts."""
    values: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, (str, bytes)):
            text = value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else value
            text = text.strip()
            if len(text) >= 6:
                values.add(text)

    for attr in (
        "external_image_api_key",
        "backup_external_image_api_key",
        "balance_api_key",
        "weather_api_key",
        "weather_token",
        "qweather_token",
        "weather_alert_token",
        "weather_alert_jwt",
        "weather_alert_api_key",
        "web_exploration_api_key",
    ):
        add(getattr(owner, attr, ""))
    endpoints = getattr(owner, "external_image_api_endpoints", None)
    if isinstance(endpoints, list):
        for endpoint in endpoints[:24]:
            if not isinstance(endpoint, dict):
                continue
            for key, value in endpoint.items():
                if _SECRET_FIELD_PATTERN.search(str(key or "")):
                    add(value)

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 5:
            return
        try:
            items = value.items() if hasattr(value, "items") else None
        except Exception:
            items = None
        if items is not None:
            try:
                for key, child in list(items)[:500]:
                    if _SECRET_FIELD_PATTERN.search(str(key or "")):
                        add(child)
                    elif isinstance(child, (dict, list, tuple)) or hasattr(child, "items"):
                        walk(child, depth + 1)
            except Exception:
                return
        elif isinstance(value, (list, tuple)):
            for child in value[:100]:
                if isinstance(child, (dict, list, tuple)) or hasattr(child, "items"):
                    walk(child, depth + 1)

    walk(getattr(owner, "config", None))
    return sorted(values, key=len, reverse=True)
