# -*- coding: utf-8 -*-
#
# 以下 import 仅为 re-export：本模块的全部实现已拆到 config_migration_shared.py 与 config_migration_partNN.py，
# 对外 `from .config_migration import X` 的可用名字与拆分前完全一致。
from __future__ import annotations

from .config_migration_part01 import (
    _append_unique_text,
    _cleanup_legacy_section_markers,
    _coerce_bool,
    _coerce_schema_value,
    _config_root_mapping,
    _ensure_provider_config_mode,
    _first_present_value,
    _has_any_configured_provider,
    _looks_like_user_profile_line,
    _migrate_legacy_roleplay_image_hint,
    _migrate_photo_scope_quota_semantics,
    _migrate_relationship_switch_semantics,
    _normalize_provider_config_mode_value,
    _normalize_weather_source,
    _parse_legacy_action_list,
    _schema_group_items,
    _split_legacy_roleplay_image_hint,
    _weather_config_values,
    _weather_schema_group,
)
from .config_migration_part02 import (
    _cleanup_flat_schema_item_keys,
    _clear_weather_compatibility_value,
    _copy_into_schema_group,
    _ensure_config_parent_dir,
    _ensure_flat_schema_compat_defaults,
    _first_weather_configured_value,
    _has_weather_configured_value,
    _infer_legacy_weather_source,
    _is_empty,
    _is_empty_legacy_openweather_default,
    _migrate_command_photo_quota_semantics,
    _migrate_legacy_group_access_mode,
    _migrate_legacy_proactive_actions,
    _migrate_qweather_config,
    _preserve_legacy_photo_reference_config,
    _resolve_weather_source,
    _single_line,
    _write_weather_schema_value,
)
from .config_migration_shared import (
    Any,
    LEGACY_DEFAULT_VALUE_MIGRATIONS,
    LEGACY_KEY_ALIASES,
    LEGACY_PROACTIVE_ACTIONS_KEY,
    LEGACY_PROACTIVE_ACTION_FLAG_KEYS,
    MEMORY_COMPANION_BRIDGE_KEY,
    OBSOLETE_CONFIG_KEYS,
    PHOTO_GENERATION_SCOPE_LIMIT_KEYS,
    PRECISION_PROVIDER_MODE_KEYS,
    Path,
    QWEATHER_DEFAULT_SOURCE,
    _COMMAND_PHOTO_QUOTA_MIGRATION_MARKER,
    _COMMAND_PHOTO_QUOTA_MIGRATION_VERSION,
    _PHOTO_SCOPE_QUOTA_MIGRATION_MARKER,
    _PHOTO_SCOPE_QUOTA_MIGRATION_VERSION,
    _QWEATHER_GENERIC_FALLBACKS,
    _RELATIONSHIP_SWITCH_MIGRATION_MARKER,
    _RELATIONSHIP_SWITCH_MIGRATION_VERSION,
    _WEATHER_SOURCE_ALIASES,
    _WEATHER_SOURCE_VALUES,
    asyncio,
    deepcopy,
    get_module_logger,
    json,
    legacy_photo_generation_scope_limits,
    logger,
    normalize_photo_generation_scope_limit,
    os,
    re,
)


def migrate_flat_config_into_schema_groups(
    config: Any,
    *,
    schema_path: Path,
    logger: Any | None = None,
    save: bool = True,
) -> int:
    """Copy legacy flat config values into the new AstrBot schema groups."""
    root = _config_root_mapping(config)
    if not isinstance(root, dict):
        return 0
    try:
        # ``AstrBotConfig`` is a dict subclass with runtime locks.  Snapshot
        # only its JSON-like mapping payload, never the container internals.
        before = deepcopy(dict(root))
    except Exception as exc:
        if logger is not None:
            logger.warning(
                "配置分组迁移无法建立回滚快照，已安全跳过: %s",
                _single_line(exc, 160),
            )
        return 0

    def restore() -> None:
        root.clear()
        root.update(deepcopy(before))

    try:
        changed = _migrate_flat_config_into_schema_groups(
            config,
            schema_path=schema_path,
            logger=logger,
            save=False,
        )
        if changed <= 0 or not save:
            return changed
        migrated = deepcopy(dict(root))

        def restore_if_unchanged() -> None:
            # An async saver may fail after a later administrator edit.  Never
            # erase that newer edit; rollback only the exact migration image.
            if root == migrated:
                restore()
            elif logger is not None:
                logger.error(
                    "配置迁移异步保存失败，但配置已被后续修改，未覆盖新值"
                )

        if not _save_config_after_schema_migration(
            config,
            logger=logger,
            on_async_failure=restore_if_unchanged,
        ):
            restore()
            return 0
        return changed
    except Exception as exc:
        restore()
        if logger is not None:
            logger.warning(
                "配置分组迁移失败，已回滚且不影响插件加载: %s",
                _single_line(exc, 160),
            )
        return 0

def _migrate_flat_config_into_schema_groups(
    config: Any,
    *,
    schema_path: Path,
    logger: Any | None = None,
    save: bool = True,
) -> int:
    root = _config_root_mapping(config)
    if not isinstance(root, dict):
        return 0
    schema_map = _schema_group_items(schema_path, logger=logger)
    if not schema_map:
        return 0

    changed: list[str] = []
    legacy_group = root.get("legacy_compat_config")
    legacy_sources = [root]
    if isinstance(legacy_group, dict):
        legacy_sources.append(legacy_group)

    relationship_switch_changes = _migrate_relationship_switch_semantics(root, schema_map)
    changed.extend(relationship_switch_changes)

    photo_scope_quota_changes = _migrate_photo_scope_quota_semantics(root, schema_map)
    changed.extend(photo_scope_quota_changes)

    command_photo_quota_changes = _migrate_command_photo_quota_semantics(root, schema_map)
    changed.extend(command_photo_quota_changes)

    # 参考图目录升级需要先于通用的“分组值优先”处理。AstrBot 会为新版
    # 分组补上空默认值；这不代表用户主动清空，不能覆盖仍然非空的旧字段。
    photo_reference_changes = _preserve_legacy_photo_reference_config(
        root,
        schema_map,
        legacy_sources,
    )
    changed.extend(photo_reference_changes)

    # Resolve the weather provider and shared QWeather credentials before the
    # generic group-authority pass, while both raw grouped and flat values are
    # still available for the explicit-choice checks.
    weather_changes = _migrate_qweather_config(root, schema_map, legacy_sources)
    changed.extend(weather_changes)

    for key, (old_default, new_default) in LEGACY_DEFAULT_VALUE_MIGRATIONS.items():
        item = schema_map.get(key) or {}
        group = root.get(str(item.get("group") or ""))
        if not isinstance(group, dict) or key not in group or key not in root:
            continue
        flat_value = _coerce_schema_value(root.get(key), item)
        grouped_value = _coerce_schema_value(group.get(key), item)
        if flat_value != old_default or grouped_value != old_default:
            continue
        migrated_value = _coerce_schema_value(new_default, item)
        root[key] = migrated_value
        group[key] = migrated_value
        changed.append(f"{key}~legacy-default")

    for key, item in schema_map.items():
        if key == "provider_config_mode":
            continue
        if key not in root:
            continue
        group_key = str(item.get("group") or "")
        group = root.get(group_key)
        if isinstance(group, dict) and key in group:
            # The grouped value is what AstrBot's official config page exposes.
            # Once it exists it must be authoritative, including when the user
            # intentionally changes a setting back to its schema default.
            grouped_value = group.get(key)
            visible_value = _coerce_schema_value(grouped_value, item)
            if grouped_value != visible_value:
                group[key] = visible_value
                changed.append(f"{key}~schema-type")
            if root.get(key) != visible_value:
                root[key] = visible_value
                changed.append(f"{key}~group-authority")
            continue
        old_value = root.get(key)
        if old_value == item.get("default"):
            continue
        if _copy_into_schema_group(root, schema_map, key, old_value):
            changed.append(key)

    if _migrate_legacy_group_access_mode(root, schema_map):
        changed.append("require_target_group->group_access_mode")
    action_changes = _migrate_legacy_proactive_actions(root, schema_map, legacy_sources)
    changed.extend(action_changes)

    for old_key, new_keys in LEGACY_KEY_ALIASES.items():
        for source in legacy_sources:
            if old_key not in source:
                continue
            old_value = source.get(old_key)
            if _is_empty(old_value):
                continue
            for new_key in new_keys:
                if _copy_into_schema_group(root, schema_map, new_key, old_value):
                    changed.append(f"{old_key}->{new_key}")
                # Keep the hidden flat compatibility copy synchronized as well.
                # Without this, AstrBot may persist an empty default at the root
                # while integrations that read the flat key miss the migrated
                # credential (the grouped value remains authoritative for _flat_get).
                if old_key == "weather_alert_api_key":
                    target_item = schema_map.get(new_key) or {}
                    target_group = root.get(str(target_item.get("group") or ""))
                    if isinstance(target_group, dict) and new_key in target_group:
                        normalized = _coerce_schema_value(target_group.get(new_key), target_item)
                        if root.get(new_key) != normalized:
                            root[new_key] = normalized
                            changed.append(f"{new_key}~compat-sync")

    if _ensure_provider_config_mode(root, schema_map):
        changed.append("provider_config_mode~mode-infer")
    added_compat_defaults = _ensure_flat_schema_compat_defaults(root, schema_map)
    if added_compat_defaults:
        changed.extend(f"{key}~compat-default" for key in added_compat_defaults)
    removed_section_keys = _cleanup_legacy_section_markers(root)
    if removed_section_keys:
        changed.extend(f"{key}~section-cleanup" for key in removed_section_keys)
    roleplay_hint_changes = _migrate_legacy_roleplay_image_hint(root, schema_map)
    changed.extend(roleplay_hint_changes)

    # 旧别名键只负责迁移；仍在 schema 中登记的 flat 兼容键重置为默认值，
    # 避免 AstrBot 每次启动都反复补齐并刷屏。
    removed_legacy_keys: list[str] = []
    cleanup_keys = (
        set(LEGACY_KEY_ALIASES)
        | OBSOLETE_CONFIG_KEYS
        | {LEGACY_PROACTIVE_ACTIONS_KEY, "require_target_group"}
    )
    for old_key in cleanup_keys:
        item = schema_map.get(old_key)
        if old_key in root:
            if item:
                default_value = _coerce_schema_value(item.get("default"), item)
                if root.get(old_key) != default_value:
                    root[old_key] = default_value
                    removed_legacy_keys.append(old_key)
            else:
                root.pop(old_key, None)
                removed_legacy_keys.append(old_key)
        if isinstance(legacy_group, dict) and old_key in legacy_group:
            if item and str(item.get("group") or "") == "legacy_compat_config":
                default_value = _coerce_schema_value(item.get("default"), item)
                if legacy_group.get(old_key) != default_value:
                    legacy_group[old_key] = default_value
                    if old_key not in removed_legacy_keys:
                        removed_legacy_keys.append(old_key)
            else:
                legacy_group.pop(old_key, None)
                if old_key not in removed_legacy_keys:
                    removed_legacy_keys.append(old_key)
        if old_key in OBSOLETE_CONFIG_KEYS:
            for container in root.values():
                if isinstance(container, dict) and old_key in container:
                    container.pop(old_key, None)
                    if old_key not in removed_legacy_keys:
                        removed_legacy_keys.append(old_key)
    if removed_legacy_keys:
        changed.extend(f"{key}~cleanup" for key in removed_legacy_keys)

    if not changed:
        return 0
    if logger is not None:
        logger.info("已将旧版扁平配置迁移到新版分组配置: %s 项", len(changed))
    if save and not _save_config_after_schema_migration(config, logger=logger):
        raise RuntimeError("config_schema_migration_save_failed")
    return len(changed)

def _save_config_after_schema_migration(
    config: Any,
    *,
    logger: Any | None = None,
    on_async_failure: Any | None = None,
) -> bool:
    def schedule(result: Any) -> bool:
        if not (asyncio.iscoroutine(result) or hasattr(result, "__await__")):
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            close = getattr(result, "close", None)
            if callable(close):
                close()
            if logger is not None:
                logger.debug("config migration async save skipped: no event loop")
            return False
        tasks = getattr(config, "_private_companion_config_save_tasks", None)
        if not isinstance(tasks, set):
            tasks = set()
            try:
                setattr(config, "_private_companion_config_save_tasks", tasks)
            except Exception:
                tasks = None
        task = loop.create_task(result, name="private-companion-config-save")
        if isinstance(tasks, set):
            tasks.add(task)

        def consume(done_task: asyncio.Task) -> None:
            try:
                done_task.result()
            except asyncio.CancelledError:
                if callable(on_async_failure):
                    try:
                        on_async_failure()
                    except Exception as rollback_exc:
                        if logger is not None:
                            logger.error(
                                "config migration async rollback failed: %s",
                                _single_line(rollback_exc, 160),
                            )
            except Exception as exc:
                if logger is not None:
                    logger.warning("config migration async save failed: %s", _single_line(exc, 160))
                if callable(on_async_failure):
                    try:
                        on_async_failure()
                    except Exception as rollback_exc:
                        if logger is not None:
                            logger.error(
                                "config migration async rollback failed: %s",
                                _single_line(rollback_exc, 160),
                            )
            finally:
                if isinstance(tasks, set):
                    tasks.discard(done_task)

        task.add_done_callback(consume)
        return True

    for method_name in ("save_config", "save", "save_conf"):
        save = getattr(config, method_name, None)
        if not callable(save):
            continue
        try:
            _ensure_config_parent_dir(config, logger=logger)
            result = save()
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                return schedule(result)
            return result is not False
        except TypeError:
            continue
        except FileNotFoundError as exc:
            if _ensure_config_parent_dir(config, error=exc, logger=logger):
                try:
                    result = save()
                    if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                        return schedule(result)
                    return result is not False
                except Exception as retry_exc:
                    if logger is not None:
                        logger.warning("重试保存配置分组迁移结果失败: %s", _single_line(retry_exc, 160))
                    return False
            if logger is not None:
                logger.warning("保存配置分组迁移结果失败: %s", _single_line(exc, 160))
            return False
        except Exception as exc:
            if logger is not None:
                logger.warning("保存配置分组迁移结果失败: %s", _single_line(exc, 160))
            return False
    return False
