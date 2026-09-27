# -*- coding: utf-8 -*-
"""helpers 拆分件 part03（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 helpers.py，仅调整模块级依赖的导入来源。
"""
import re
from typing import Any
from urllib.parse import urlparse

try:  # package import
    from .helpers_part01 import (
        _runtime_secret_values,
        _single_line,
    )
except ImportError:  # direct test/import from the plugin directory
    from helpers_part01 import (
        _runtime_secret_values,
        _single_line,
    )
try:  # package import
    from .helpers_part02 import (
        _MISSING,
    )
except ImportError:  # direct test/import from the plugin directory
    from helpers_part02 import (
        _MISSING,
    )


def _flat_get(config: Any, key: str, default: Any = None) -> Any:
    """Read both flat config keys and keys nested under schema object/items groups."""
    if isinstance(config, dict):
        # Prefer schema-group values over top-level legacy compatibility keys.
        # AstrBot may add invisible legacy flat defaults before plugin init; if
        # those are read first they would shadow the user's real grouped config.
        for value in config.values():
            if isinstance(value, dict):
                found = _flat_get(value, key, _MISSING)
                if found is not _MISSING:
                    return found
        if key in config:
            return config[key]
    for attr in ("data", "config"):
        target = getattr(config, attr, None)
        if isinstance(target, dict):
            found = _flat_get(target, key, _MISSING)
            if found is not _MISSING:
                return found
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            value = getter(key, _MISSING)
        except Exception:
            value = _MISSING
        if value is not _MISSING:
            return value
    return default


def _redact_outbound_secrets(text: Any, owner: Any = None) -> str:
    """Redact credentials from chat-bound text while preserving the useful reply."""
    cleaned = str(text or "")
    if not cleaned:
        return ""

    trusted_domains = getattr(owner, "outbound_secret_redaction_trusted_domains", None)
    if trusted_domains is None and owner is not None:
        trusted_domains = _flat_get(getattr(owner, "config", {}), "outbound_secret_redaction_trusted_domains", [])
    trusted = {
        str(domain or "").strip().lower().rstrip(".")
        for domain in (trusted_domains if isinstance(trusted_domains, (list, tuple, set)) else [])
        if str(domain or "").strip()
    }
    patterns = (
        (r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer [密钥已隐藏]"),
        (r"(?i)\b(?:sk|pk|rk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{10,}", "[密钥已隐藏]"),
        (r"\bAIza[A-Za-z0-9_-]{20,}\b", "[密钥已隐藏]"),
        (r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", "[密钥已隐藏]"),
        (r"(?i)([?&](?:api[_-]?key|access_token|refresh_token|token|secret|key)=)[^&#\s]+", r"\1[密钥已隐藏]"),
        (
            r"(?i)((?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|auth[_ -]?token|secret|password|passwd|密钥|令牌|口令)\s*(?::|：|=)\s*[\"']?)[^\s,，;；\"']{6,}",
            r"\1[密钥已隐藏]",
        ),
    )
    def redact_fragment(value: str) -> str:
        for pattern, replacement in patterns:
            value = re.sub(pattern, replacement, value)
        return value

    if trusted:
        # Process spans directly so user text cannot collide with placeholders.
        fragments: list[str] = []
        start = 0
        for match in re.finditer(r"https?://[^\s<>\"'“”‘’，。；！？）)\]`]+", cleaned, re.IGNORECASE):
            raw = match.group(0)
            try:
                parsed = urlparse(raw)
                hostname = (parsed.hostname or "").lower().rstrip(".")
                parsed.port  # Validate malformed ports before trusting the URL.
                allowed = bool(hostname) and parsed.username is None and "\\" not in raw and any(
                    hostname == domain or hostname.endswith("." + domain) for domain in trusted
                )
            except ValueError:
                allowed = False
            if allowed:
                fragments.extend((redact_fragment(cleaned[start:match.start()]), raw))
                start = match.end()
        fragments.append(redact_fragment(cleaned[start:]))
        cleaned = "".join(fragments)
    else:
        cleaned = redact_fragment(cleaned)
    # A trusted link never exempts a credential already present in plugin config.
    for secret in _runtime_secret_values(owner) if owner is not None else []:
        cleaned = cleaned.replace(secret, "[密钥已隐藏]")
    return cleaned


def _set_into_config(config: Any, key: str, value: Any, *, allow_flat_fallback: bool = True) -> bool:
    """Write a config value back to its existing flat or nested location."""

    def convert(existing: Any, new_value: Any) -> Any:
        if isinstance(existing, bool) and isinstance(new_value, str):
            text = new_value.strip().lower()
            if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
                return True
            if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
                return False
        if isinstance(existing, int) and not isinstance(existing, bool) and isinstance(new_value, str):
            try:
                return int(new_value)
            except (TypeError, ValueError):
                return new_value
        if isinstance(existing, float) and isinstance(new_value, str):
            try:
                return float(new_value)
            except (TypeError, ValueError):
                return new_value
        return new_value

    def find_and_set(target: dict[str, Any]) -> bool:
        # Match _flat_get(): schema-grouped values are searched before legacy
        # flat compatibility keys.  When a key exists in more than one place,
        # keep every location in sync so later readers cannot see a stale copy.
        changed = False
        for child in target.values():
            if isinstance(child, dict):
                changed = find_and_set(child) or changed
        if key in target:
            target[key] = convert(target.get(key), value)
            changed = True
        return changed

    if isinstance(config, dict) and find_and_set(config):
        return True
    for attr in ("data", "config"):
        target = getattr(config, attr, None)
        if isinstance(target, dict) and find_and_set(target):
            return True
    if not allow_flat_fallback:
        return False
    try:
        config[key] = value
        return True
    except Exception:
        pass
    setter = getattr(config, "set", None)
    if callable(setter):
        try:
            setter(key, value)
            return True
        except Exception:
            pass
    return False


def _memory_archive_warning(record: Any) -> str:
    """Return a user-visible warning when local-first Memory delivery is incomplete."""

    if not isinstance(record, dict):
        return ""
    result = record.get("memory_archive")
    if not isinstance(result, dict):
        return ""
    state = _single_line(result.get("state"), 40).lower() or "degraded"
    if bool(result.get("ok")) and state in {"sent", "deduplicated"}:
        return ""
    error = _single_line(result.get("error_code"), 80)
    detail = f"，原因：{error}" if error else ""
    if state in {"pending", "retry", "local_only"}:
        return f"⚠ Memory 归档尚未完成（{state}{detail}）；本地内容已保存，将按 outbox 策略补投。"
    return f"⚠ Memory 归档失败或降级（{state}{detail}）；本地内容已保存。"
