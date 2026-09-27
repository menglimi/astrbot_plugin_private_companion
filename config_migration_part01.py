# -*- coding: utf-8 -*-
"""config_migration_part01：从 config_migration.py 机械抽取的模块级函数。

由 tmp/split4/mod_split.py 生成（19 个函数 / 413 行）。函数体逐字节原样，仅位置变化。
对外经由宿主 config_migration.py re-export，接口不变。
"""
from __future__ import annotations

from .config_migration_shared import (
    Any,
    PHOTO_GENERATION_SCOPE_LIMIT_KEYS,
    PRECISION_PROVIDER_MODE_KEYS,
    Path,
    _PHOTO_SCOPE_QUOTA_MIGRATION_MARKER,
    _PHOTO_SCOPE_QUOTA_MIGRATION_VERSION,
    _RELATIONSHIP_SWITCH_MIGRATION_MARKER,
    _RELATIONSHIP_SWITCH_MIGRATION_VERSION,
    _WEATHER_SOURCE_ALIASES,
    _WEATHER_SOURCE_VALUES,
    json,
    legacy_photo_generation_scope_limits,
    normalize_photo_generation_scope_limit,
    re,
)


def _migrate_relationship_switch_semantics(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
) -> list[str]:
    """Preserve the pre-v6.0.7 meaning of the relationship switch once.

    The old key was a policy *selection* toggle whose default was ``false``;
    both values still left the built-in relationship ledger active.  The new
    release uses the same persisted key as a total enable/disable switch.
    Existing values therefore need a one-time normalization to ``true``.
    A private marker prevents a user's later explicit disable from being
    rewritten on every startup.
    """

    marker = root.get(_RELATIONSHIP_SWITCH_MIGRATION_MARKER)
    if marker == _RELATIONSHIP_SWITCH_MIGRATION_VERSION:
        return []
    key = "enable_custom_relationship_stage_policy"
    item = schema_map.get(key)
    if not isinstance(item, dict):
        return []
    group_key = str(item.get("group") or "")
    group = root.get(group_key) if group_key else None
    has_legacy_value = key in root or (isinstance(group, dict) and key in group)
    if not has_legacy_value:
        # A brand-new config will receive the schema default later; there is
        # no legacy choice to migrate and no marker to persist.
        return []

    changed: list[str] = []
    if root.get(key) is not True:
        root[key] = True
        changed.append(f"{key}~relationship-switch-v1")
    if isinstance(group, dict) and key in group and group.get(key) is not True:
        group[key] = True
        changed.append(f"{group_key}.{key}~relationship-switch-v1")
    if root.get(_RELATIONSHIP_SWITCH_MIGRATION_MARKER) != _RELATIONSHIP_SWITCH_MIGRATION_VERSION:
        root[_RELATIONSHIP_SWITCH_MIGRATION_MARKER] = _RELATIONSHIP_SWITCH_MIGRATION_VERSION
        changed.append(f"{_RELATIONSHIP_SWITCH_MIGRATION_MARKER}~set")
    return changed

def _migrate_photo_scope_quota_semantics(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
) -> list[str]:
    """Convert the legacy scope allow-list into independent daily quotas once."""
    if root.get(_PHOTO_SCOPE_QUOTA_MIGRATION_MARKER) == _PHOTO_SCOPE_QUOTA_MIGRATION_VERSION:
        return []

    legacy_key = "photo_generation_allowed_scopes"
    legacy_item = schema_map.get(legacy_key) or {}
    legacy_group_key = str(legacy_item.get("group") or "photo_action_config")
    group = root.get(legacy_group_key)
    if not isinstance(group, dict):
        group = {}
        root[legacy_group_key] = group

    if legacy_key in group:
        raw_legacy = group.get(legacy_key)
    elif legacy_key in root:
        raw_legacy = root.get(legacy_key)
    else:
        raw_legacy = None
    legacy_limits = legacy_photo_generation_scope_limits(raw_legacy)

    # AstrBot adds newly introduced grouped fields with their schema defaults
    # before plugin startup.  Four default ``-1`` values therefore still mean
    # "migrate the legacy list".  A non-default new value, however, proves the
    # quota form has already been persisted and must remain authoritative even
    # if an older AstrBot build discarded the private migration marker.
    existing_limits: dict[str, int] = {}
    has_nondefault_new_value = False
    for scope, key in PHOTO_GENERATION_SCOPE_LIMIT_KEYS.items():
        item = schema_map.get(key) or {}
        group_key = str(item.get("group") or "photo_action_config")
        target_group = root.get(group_key)
        if isinstance(target_group, dict) and key in target_group:
            raw_value = target_group.get(key)
        elif key in root:
            raw_value = root.get(key)
        else:
            continue
        value = normalize_photo_generation_scope_limit(raw_value)
        existing_limits[scope] = value
        default_value = normalize_photo_generation_scope_limit(item.get("default", -1))
        if value != default_value:
            has_nondefault_new_value = True

    limits = (
        {
            scope: existing_limits.get(scope, legacy_limits.get(scope, -1))
            for scope in PHOTO_GENERATION_SCOPE_LIMIT_KEYS
        }
        if has_nondefault_new_value
        else legacy_limits
    )

    changed: list[str] = []
    for scope, key in PHOTO_GENERATION_SCOPE_LIMIT_KEYS.items():
        item = schema_map.get(key)
        if not isinstance(item, dict):
            continue
        value = normalize_photo_generation_scope_limit(limits.get(scope, -1))
        group_key = str(item.get("group") or "photo_action_config")
        target_group = root.get(group_key)
        if not isinstance(target_group, dict):
            target_group = {}
            root[group_key] = target_group
        if root.get(key) != value:
            root[key] = value
            changed.append(f"{key}~scope-quota-v1")
        if target_group.get(key) != value:
            target_group[key] = value
            changed.append(f"{group_key}.{key}~scope-quota-v1")

    root[_PHOTO_SCOPE_QUOTA_MIGRATION_MARKER] = _PHOTO_SCOPE_QUOTA_MIGRATION_VERSION
    changed.append(f"{_PHOTO_SCOPE_QUOTA_MIGRATION_MARKER}~set")
    return changed

def _weather_config_values(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    key: str,
    legacy_sources: list[dict[str, Any]],
) -> list[Any]:
    values: list[Any] = []
    group = _weather_schema_group(root, schema_map, key)
    if isinstance(group, dict) and key in group:
        values.append(group.get(key))
    if key in root:
        values.append(root.get(key))
    for source in legacy_sources[1:]:
        if key in source:
            values.append(source.get(key))
    return values

def _weather_schema_group(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    key: str,
) -> dict[str, Any] | None:
    item = schema_map.get(key) or {}
    group_key = str(item.get("group") or "")
    group = root.get(group_key) if group_key else None
    return group if isinstance(group, dict) else None

def _normalize_weather_source(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _WEATHER_SOURCE_ALIASES.get(text, text if text in _WEATHER_SOURCE_VALUES else "")

def _ensure_provider_config_mode(root: dict[str, Any], schema_map: dict[str, dict[str, Any]]) -> bool:
    item = schema_map.get("provider_config_mode")
    if not item:
        return False
    group_key = str(item.get("group") or "")
    group = root.get(group_key)
    if not isinstance(group, dict):
        group = {}
        root[group_key] = group

    root_mode = _normalize_provider_config_mode_value(root.get("provider_config_mode"))
    group_mode = _normalize_provider_config_mode_value(group.get("provider_config_mode"))
    quick_keys = (
        "FAST_RESPONSE_PROVIDER_ID",
        "COMPLEX_REASONING_PROVIDER_ID",
        "CREATIVE_MODEL_PROVIDER_ID",
        "PLUGIN_VISION_PROVIDER_ID",
    )
    has_quick_provider = _has_any_configured_provider(root, group, quick_keys)
    has_precision_provider = _has_any_configured_provider(root, group, PRECISION_PROVIDER_MODE_KEYS)
    explicit = ""
    if group_mode and root_mode and group_mode != root_mode:
        # Official AstrBot config pages save the visible schema group first.
        # When it disagrees with the hidden flat compatibility key, prefer what
        # the user can actually see and just sync the hidden copy afterward.
        explicit = group_mode
    elif group_mode:
        if not root_mode and group_mode == "quick" and has_precision_provider and not has_quick_provider:
            explicit = ""
        else:
            explicit = group_mode
    elif root_mode:
        explicit = root_mode
    if explicit:
        changed = False
        if group.get("provider_config_mode") != explicit:
            group["provider_config_mode"] = explicit
            changed = True
        if root.get("provider_config_mode") != explicit:
            root["provider_config_mode"] = explicit
            changed = True
        return changed

    inferred = "precision" if has_precision_provider else "quick"
    group["provider_config_mode"] = inferred
    root["provider_config_mode"] = inferred
    return True

def _normalize_provider_config_mode_value(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "quick": "quick",
        "fast": "quick",
        "simple": "quick",
        "快速": "quick",
        "快速配置": "quick",
        "precision": "precision",
        "precise": "precision",
        "advanced": "precision",
        "detail": "precision",
        "detailed": "precision",
        "精准": "precision",
        "精准配置": "precision",
        "分流": "precision",
        "分流模型": "precision",
    }
    return aliases.get(text, "")

def _has_any_configured_provider(
    root: dict[str, Any],
    mode_group: dict[str, Any],
    keys: tuple[str, ...],
) -> bool:
    for key in keys:
        if str(mode_group.get(key) or "").strip():
            return True
        if str(root.get(key) or "").strip():
            return True
        for value in root.values():
            if isinstance(value, dict) and str(value.get(key) or "").strip():
                return True
    return False

def _cleanup_legacy_section_markers(root: dict[str, Any]) -> list[str]:
    removed: list[str] = []
    for key in list(root.keys()):
        if str(key).startswith("_section_"):
            root.pop(key, None)
            removed.append(str(key))
    return removed

def _migrate_legacy_roleplay_image_hint(root: dict[str, Any], schema_map: dict[str, dict[str, Any]]) -> list[str]:
    image_item = schema_map.get("private_image_self_recognition_hint") or {}
    image_group_key = str(image_item.get("group") or "")
    profile_item = schema_map.get("roleplay_user_profile_prompt") or {}
    profile_group_key = str(profile_item.get("group") or "")
    if not image_group_key or not profile_group_key:
        return []
    image_group = root.get(image_group_key)
    if not isinstance(image_group, dict):
        return []
    raw_hint = str(image_group.get("private_image_self_recognition_hint") or "").strip()
    if not raw_hint:
        return []
    split = _split_legacy_roleplay_image_hint(raw_hint)
    user_text = "\n".join(split["user"]).strip()
    image_text = "\n".join(split["image"]).strip()
    if not user_text:
        return []
    profile_group = root.get(profile_group_key)
    if not isinstance(profile_group, dict):
        profile_group = {}
        root[profile_group_key] = profile_group
    old_profile = str(profile_group.get("roleplay_user_profile_prompt") or "").strip()
    new_profile = _append_unique_text(old_profile, user_text)
    changed: list[str] = []
    if new_profile != old_profile:
        profile_group["roleplay_user_profile_prompt"] = new_profile[:2000]
        changed.append("private_image_self_recognition_hint->roleplay_user_profile_prompt")
    if image_text != raw_hint:
        image_group["private_image_self_recognition_hint"] = image_text[:1200]
        changed.append("private_image_self_recognition_hint~user-profile-cleanup")
    return changed

def _split_legacy_roleplay_image_hint(text: str) -> dict[str, list[str]]:
    user_lines: list[str] = []
    image_lines: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if _looks_like_user_profile_line(line):
            user_lines.append(raw_line.strip())
        else:
            image_lines.append(raw_line.strip())
    return {"user": user_lines, "image": image_lines}

def _looks_like_user_profile_line(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    lower = text.lower()
    if any(token in lower for token in ("user profile", "user_profile", "master profile")):
        return True
    if re.search(r"(对用户的称呼|用户[：:的]|主人[：:的]|主人的|用户性别|用户生日|用户年龄|用户职业|用户身份|用户资料|用户设定|用户画像|用户偏好|用户边界|用户关系|用户称呼|称呼用户|如何称呼用户|与用户关系|和用户关系|彼此关系|相处方式|关系补充|是角色的XX|与角色的相处方式)", text):
        return True
    label = re.split(r"[：:]", text, maxsplit=1)[0].strip()
    if label in {"称呼", "昵称", "性别", "生日", "年龄", "职业", "专业", "身份", "关系", "边界", "偏好", "是角色的XX", "与角色的相处方式", "其他补充信息"}:
        return True
    if re.search(r"(专业是|职业是|生日是|性别是|称呼.*主人|叫.*主人|把用户|用户是|主人是)", text):
        return True
    return False

def _append_unique_text(existing: str, addition: str) -> str:
    existing = str(existing or "").strip()
    addition_lines = [line.strip() for line in str(addition or "").splitlines() if line.strip()]
    if not addition_lines:
        return existing
    if not existing:
        return "\n".join(addition_lines)
    existing_lines = [line.strip() for line in existing.splitlines() if line.strip()]
    existing_set = set(existing_lines)
    merged = list(existing_lines)
    for line in addition_lines:
        if line not in existing_set:
            merged.append(line)
            existing_set.add(line)
    return "\n".join(merged)

def _first_present_value(sources: list[dict[str, Any]], key: str) -> Any:
    for source in sources:
        if key in source:
            return source.get(key)
    return None

def _parse_legacy_action_list(raw: Any) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return set()
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            raw_parts = parsed
        else:
            raw_parts = re.split(r"[,\s、;；|]+", text)
    elif isinstance(raw, list):
        raw_parts = raw
    elif isinstance(raw, tuple | set):
        raw_parts = list(raw)
    else:
        raw_parts = []
    actions: set[str] = set()
    for part in raw_parts:
        text = str(part or "").strip()
        if not text:
            continue
        for action in text.split("+"):
            action = action.strip()
            if action:
                actions.add(action)
    return actions

def _config_root_mapping(config: Any) -> dict[str, Any] | None:
    if isinstance(config, dict):
        return config
    for attr in ("data", "config"):
        target = getattr(config, attr, None)
        if isinstance(target, dict):
            return target
    return None

def _schema_group_items(schema_path: Path, *, logger: Any | None = None) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    try:
        raw = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as exc:
        if logger is not None:
            logger.debug("读取配置 schema 用于分组迁移失败: %s", exc)
        return mapping
    if not isinstance(raw, dict):
        return mapping
    for group_key, group in raw.items():
        if not isinstance(group, dict) or group.get("type") != "object":
            continue
        items = group.get("items")
        if not isinstance(items, dict):
            continue
        for key, item in items.items():
            if isinstance(item, dict):
                copied = dict(item)
                copied["group"] = str(group_key)
                mapping[str(key)] = copied
    return mapping

def _coerce_schema_value(value: Any, item: dict[str, Any]) -> Any:
    item_type = str(item.get("type") or "")
    if item_type == "bool":
        return _coerce_bool(value)
    if item_type == "int":
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return item.get("default")
    if item_type == "float":
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return item.get("default")
        slider = item.get("slider")
        if (
            isinstance(slider, dict)
            and float(slider.get("max", 0) or 0) <= 1.0
            and parsed > 1.0
            and ("probability" in str(item.get("description") or "").lower() or "概率" in str(item.get("description") or ""))
        ):
            parsed /= 100.0
        return parsed
    if item_type == "list":
        if isinstance(value, list):
            return value
        text = str(value or "").strip()
        if not text:
            return []
        return [part.strip() for part in re.split(r"[\n,，、;；]+", text) if part.strip()]
    if item_type in {"string", "text"}:
        return str(value or "")
    return value

def _coerce_bool(value: Any) -> bool:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
            return True
        if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
            return False
    return bool(value)
