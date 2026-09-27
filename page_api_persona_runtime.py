# -*- coding: utf-8 -*-
"""persona 运行时治理域。

由 tools/split_mixin_domain.py 从 page_api_persona.py 机械抽取（17 个方法 + 0 个模块级名字 + 0 个类级赋值 / 787 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiPersonaMixin）。
"""
from __future__ import annotations

import asyncio
import functools
import os
import time
import uuid
from .page_api_shared import _page_api_host_request as request
from .story_authority import story_legacy_operation
from copy import deepcopy
from pathlib import Path
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiPersonaRuntimeMixin:
    """persona 运行时治理域（从 PrivateCompanionPageApiPersonaMixin 拆出）。"""


    def _persona_scoped_route_handler(self, handler):
        """Bind every data-facing page request to the selected page persona."""
        @functools.wraps(handler)
        async def wrapper(*args, **kwargs):
            plugin = getattr(self, "plugin", None)
            activator = getattr(plugin, "_activate_persona_id", None)
            persona_id = self._single_line(request.args.get("_persona_id"), 96)
            if not persona_id and request.method != "GET":
                payload = await request.get_json(silent=True) or {}
                if isinstance(payload, dict):
                    persona_id = self._single_line(payload.get("_persona_id"), 96)
            config_getter = getattr(plugin, "_persona_config_profile_ids", None)
            known = set(config_getter() if callable(config_getter) else [])
            primary_getter = getattr(plugin, "_primary_persona_id", None)
            primary = str(primary_getter() if callable(primary_getter) else "").strip()
            if primary:
                known.add(primary)
            if persona_id not in known:
                persona_id = primary
            token = (
                activator(persona_id, allow_inactive=True)
                if callable(activator) and persona_id
                else None
            )
            try:
                return await handler(*args, **kwargs)
            finally:
                deactivator = getattr(plugin, "_deactivate_persona_for_event", None)
                if token is not None and callable(deactivator):
                    deactivator(token)

        return wrapper

    def _attach_multi_persona_token_stats(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict) or not bool(getattr(self.plugin, "enable_multi_persona_mode", False)):
            return
        by_persona: dict[str, Any] = {}
        profile_ids = getattr(self.plugin, "_persona_profile_ids", lambda: [])()
        for persona_id in profile_ids:
            try:
                profile = self.plugin._ensure_persona_profile(persona_id)
                usage = profile.get("token_usage", {}) if isinstance(profile, dict) else {}
                summary = self._token_stats_payload(usage)
                by_persona[str(persona_id)] = {
                    "persona_id": str(persona_id),
                    "totals": summary.get("totals", {}),
                    "by_day": summary.get("by_day", []),
                    "by_provider": summary.get("by_provider", []),
                    "by_task": summary.get("by_task", []),
                    "recent": summary.get("recent", [])[:20],
                }
            except Exception as exc:
                logger.debug("多人格 Token 分类读取失败 persona=%s error=%s", persona_id, exc)
        payload["multi_persona"] = {"enabled": True, "by_persona": by_persona}

    def _multi_persona_transition_snapshot(self) -> dict[str, Any]:
        """Capture every mutable boundary touched by a mode transition."""
        profiles_dir = Path(str(getattr(self.plugin, "_persona_profiles_dir", "") or ""))
        legacy_profile_files: dict[str, bytes] = {}
        profile_payloads: dict[str, dict[str, Any]] = {}
        profile_database_names: set[str] = set()
        if profiles_dir.is_dir():
            for path in profiles_dir.glob("*.json"):
                try:
                    legacy_profile_files[path.name] = path.read_bytes()
                except OSError:
                    continue
            for path in profiles_dir.glob("*.db"):
                persona_id = self.plugin._persona_id_from_profile_path(path)
                if not persona_id:
                    continue
                profile_database_names.add(path.name)
                profile = self.plugin._persona_profile_snapshot_if_exists(persona_id)
                if isinstance(profile, dict):
                    profile_payloads[persona_id] = deepcopy(profile)
        attrs = {
            key: deepcopy(getattr(self.plugin, key, None))
            for key in (
                "enable_multi_persona_mode",
                "multi_persona_ids",
                "plugin_specific_persona_id",
                "_page_current_persona_id",
            )
        }
        return {
            "config": deepcopy(dict(getattr(self.plugin, "config", {}) or {})),
            "attrs": attrs,
            "data_default": deepcopy(getattr(self.plugin, "_data_default", {})),
            "persona_data_profiles": deepcopy(
                getattr(self.plugin, "_persona_data_profiles", {})
            ),
            "profiles_dir": str(profiles_dir),
            "legacy_profile_files": legacy_profile_files,
            "profile_payloads": profile_payloads,
            "profile_database_names": sorted(profile_database_names),
        }

    @story_legacy_operation("page.settings.persona-rollback")
    async def _rollback_multi_persona_transition(
        self,
        snapshot: dict[str, Any],
    ) -> None:
        """Restore config, memory, profile files, and the legacy data snapshot."""
        config = getattr(self.plugin, "config", None)
        config_snapshot = snapshot.get("config")
        if isinstance(config, dict) and isinstance(config_snapshot, dict):
            config.clear()
            config.update(deepcopy(config_snapshot))
        attrs = snapshot.get("attrs")
        if isinstance(attrs, dict):
            for key, value in attrs.items():
                setattr(self.plugin, key, deepcopy(value))
        self.plugin._data_default = deepcopy(snapshot.get("data_default") or {})
        self.plugin._persona_data_profiles = deepcopy(
            snapshot.get("persona_data_profiles") or {}
        )

        profiles_dir = Path(str(snapshot.get("profiles_dir") or ""))
        legacy_profile_files = snapshot.get("legacy_profile_files")
        profile_payloads = snapshot.get("profile_payloads")
        profile_database_names = {
            str(value)
            for value in (snapshot.get("profile_database_names") or [])
            if str(value)
        }
        if profiles_dir:
            profiles_dir.mkdir(parents=True, exist_ok=True)
            for path in profiles_dir.glob("*.json"):
                if not isinstance(legacy_profile_files, dict) or path.name not in legacy_profile_files:
                    path.unlink(missing_ok=True)
            for path in profiles_dir.glob("*.db"):
                if path.name in profile_database_names:
                    continue
                registry = getattr(self.plugin, "_persona_sqlite_store_registry", None)
                discard = getattr(registry, "discard", None)
                if callable(discard):
                    discard(path)
                path.unlink(missing_ok=True)
                path.with_name(path.name + "-wal").unlink(missing_ok=True)
                path.with_name(path.name + "-shm").unlink(missing_ok=True)
            for name, payload in (legacy_profile_files or {}).items():
                if not isinstance(name, str) or not isinstance(payload, bytes):
                    continue
                path = profiles_dir / name
                temporary = path.with_name(f".{path.name}.rollback-{uuid.uuid4().hex}.tmp")
                try:
                    temporary.write_bytes(payload)
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            for persona_id, payload in (profile_payloads or {}).items():
                if not isinstance(payload, dict):
                    continue
                await asyncio.to_thread(
                    self.plugin._save_persona_profile_sync,
                    persona_id,
                    deepcopy(payload),
                )

        writer = getattr(self.plugin, "_write_data_snapshot_sync", None)
        if callable(writer):
            await asyncio.to_thread(writer, deepcopy(self.plugin._data_default))

    def _active_persona_routing_warnings(
        self,
        items: Any,
        *,
        max_age_seconds: float = 2 * 60 * 60,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        """Project current routing problems without mutating retained history."""
        if not isinstance(items, list):
            return []
        current_ts = time.time() if now is None else float(now)
        active: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            status = self._single_line(item.get("status"), 16).lower()
            if status and status != "active":
                continue
            last_ts = self._float(item.get("last_ts"))
            if last_ts <= 0 or current_ts - last_ts > max_age_seconds:
                continue
            active.append(item)
        active.sort(key=lambda item: self._float(item.get("last_ts")), reverse=True)
        return active

    def _bookshelf_access_persona_id(self) -> str:
        active_getter = getattr(self.plugin, "_active_persona_scope", None)
        active = self._single_line(active_getter() if callable(active_getter) else "", 96)
        if active:
            return active
        if bool(getattr(self.plugin, "enable_multi_persona_mode", False)):
            return self._single_line(getattr(self.plugin, "_page_current_persona_id", ""), 96)
        return ""

    async def _record_personality_auto_tune_manual_values(self, changed: dict[str, Any]) -> None:
        manual_changes = {
            key: deepcopy(value)
            for key, value in (changed or {}).items()
            if key in self.PERSONALITY_AUTO_TUNE_KEYS
        }
        if not manual_changes:
            return
        async with self.plugin._data_lock:
            state = self.plugin.data.setdefault("personality_iteration_auto_tune", {})
            if not isinstance(state, dict):
                state = {}
                self.plugin.data["personality_iteration_auto_tune"] = state
            manual_values = state.setdefault("manual_values", {})
            if not isinstance(manual_values, dict):
                manual_values = {}
                state["manual_values"] = manual_values
            applied = state.setdefault("applied", {})
            if not isinstance(applied, dict):
                applied = {}
                state["applied"] = applied
            for key, value in manual_changes.items():
                manual_values[key] = deepcopy(value)
                applied.pop(key, None)
            state["manual_updated_at"] = time.time()
            state["last_manual_changes"] = deepcopy(manual_changes)
            self._save_plugin_sections(self.plugin, {"personality_iteration_auto_tune"})

    async def _maybe_apply_personality_iteration_auto_tune(self, users: dict[str, Any], groups: dict[str, Any]) -> dict[str, Any]:
        if not bool(getattr(self.plugin, "enable_personality_iteration_experiment", False)):
            return await self._restore_personality_iteration_auto_tune("角色贴合校准已关闭")
        if not bool(getattr(self.plugin, "enable_personality_iteration_auto_tune", False)):
            return await self._restore_personality_iteration_auto_tune("角色贴合自主调节已关闭")

        suggestions = self._personality_iteration_suggestions(users, groups)
        state_snapshot = await self._personality_auto_tune_state_snapshot()
        manual_values = state_snapshot.get("manual_values") if isinstance(state_snapshot.get("manual_values"), dict) else {}
        applied_for_sync = state_snapshot.get("applied") if isinstance(state_snapshot.get("applied"), dict) else {}
        manual_values, applied_for_sync = await self._sync_personality_auto_tune_manual_snapshot(manual_values, applied_for_sync)
        reference_values = {
            key: deepcopy(manual_values.get(key, self._personality_auto_tune_current_value(key)))
            for key in self.PERSONALITY_AUTO_TUNE_KEYS
        }
        plan = self._personality_iteration_auto_tune_plan(suggestions, reference_values)
        applied_snapshot = applied_for_sync

        if not plan:
            if applied_snapshot:
                now = time.time()
                async with self.plugin._data_lock:
                    state = self.plugin.data.setdefault("personality_iteration_auto_tune", {})
                    if not isinstance(state, dict):
                        state = {}
                        self.plugin.data["personality_iteration_auto_tune"] = state
                    no_suggestion_since = self._float(state.get("no_suggestion_since"))
                    if no_suggestion_since <= 0:
                        no_suggestion_since = now
                    no_suggestion_streak = self._int(state.get("no_suggestion_streak"), 0, 0) + 1
                    state["no_suggestion_since"] = no_suggestion_since
                    state["no_suggestion_streak"] = no_suggestion_streak
                    state["last_suggestion_count"] = len(suggestions)
                    state["last_clear_observed_at"] = now
                    self._save_plugin_sections(self.plugin, {"personality_iteration_auto_tune"})
                stable_seconds = max(0.0, now - no_suggestion_since)
                ready_to_restore = (
                    no_suggestion_streak >= max(1, int(self.PERSONALITY_AUTO_TUNE_RECOVERY_STREAK))
                    and stable_seconds >= max(0.0, float(self.PERSONALITY_AUTO_TUNE_RECOVERY_MIN_SECONDS))
                )
                if not ready_to_restore:
                    result = {
                        "changed": False,
                        "pending_restore": True,
                        "reason": "角色贴合问题暂未再次出现，先保留当前自动值观察，避免参数来回切换",
                        "suggestion_count": len(suggestions),
                        "recovery_streak": no_suggestion_streak,
                        "recovery_required": max(1, int(self.PERSONALITY_AUTO_TUNE_RECOVERY_STREAK)),
                        "recovery_stable_seconds": int(stable_seconds),
                    }
                    await self._remember_personality_auto_tune_status(result)
                    return result
                return await self._restore_personality_iteration_auto_tune(
                    "角色贴合问题已连续消失并经过稳定观察，恢复用户手动参数"
                )
            async with self.plugin._data_lock:
                state = self.plugin.data.get("personality_iteration_auto_tune")
                if isinstance(state, dict) and (
                    self._int(state.get("no_suggestion_streak"), 0, 0) > 0
                    or self._float(state.get("no_suggestion_since")) > 0
                ):
                    state["no_suggestion_streak"] = 0
                    state["no_suggestion_since"] = 0
                    self.plugin._save_data_sync(sections={"personality_iteration_auto_tune"})
            await self._remember_personality_auto_tune_status(
                {
                    "changed": False,
                    "reason": "暂无需要自主调节的角色贴合问题",
                    "suggestion_count": len(suggestions),
                    "updated_at": time.time(),
                }
            )
            return {"changed": False, "reason": "暂无需要自主调节的角色贴合问题", "suggestion_count": len(suggestions)}

        changes: list[dict[str, Any]] = []
        async with self.plugin._data_lock:
            state = self.plugin.data.setdefault("personality_iteration_auto_tune", {})
            if not isinstance(state, dict):
                state = {}
                self.plugin.data["personality_iteration_auto_tune"] = state
            manual_values = state.setdefault("manual_values", {})
            if not isinstance(manual_values, dict):
                manual_values = {}
                state["manual_values"] = manual_values
            applied = state.setdefault("applied", {})
            if not isinstance(applied, dict):
                applied = {}
                state["applied"] = applied
            for key in self.PERSONALITY_AUTO_TUNE_KEYS:
                current_value = self._personality_auto_tune_current_value(key)
                applied_value = applied.get(key)
                manual_value = manual_values.get(key)
                if key not in manual_values:
                    manual_values[key] = deepcopy(current_value)
                elif key in applied and current_value != applied_value and current_value != manual_value:
                    # The value changed outside the auto tuner, so treat it as the user's new manual baseline.
                    manual_values[key] = deepcopy(current_value)
                    applied.pop(key, None)
                elif key not in applied and current_value != manual_value:
                    # The official config page can change values without calling this page's update endpoint.
                    manual_values[key] = deepcopy(current_value)
            for key, item in plan.items():
                if key not in self.PERSONALITY_AUTO_TUNE_KEYS:
                    continue
                next_value = self._normalize_setting_value(key, item.get("value"))
                current_value = self._personality_auto_tune_current_value(key)
                if current_value == next_value:
                    applied[key] = deepcopy(next_value)
                    continue
                applied[key] = deepcopy(next_value)
                changes.append(
                    {
                        "key": key,
                        "label": self._personality_auto_tune_label(key),
                        "from": current_value,
                        "to": next_value,
                        "manual": deepcopy(manual_values.get(key)),
                        "reason": self._single_line(item.get("reason"), 120),
                    }
                )
            state["enabled"] = True
            state["last_suggestion_count"] = len(suggestions)
            state["last_tuned_at"] = time.time()
            state["last_suggestion_at"] = state["last_tuned_at"]
            state["no_suggestion_streak"] = 0
            state["no_suggestion_since"] = 0
            if changes:
                state["last_changes"] = deepcopy(changes)
            self._save_plugin_sections(self.plugin, {"personality_iteration_auto_tune"})

        for change in changes:
            self._apply_config_value(change["key"], change["to"])
        config_saved = await self._save_config_if_possible() if changes else True
        result = {
            "changed": bool(changes),
            "changes": changes,
            "restored_changes": [],
            "config_saved": config_saved,
            "suggestion_count": len(suggestions),
            "reason": "已按角色贴合诊断临时覆盖参数" if changes else "当前自动覆盖值已经符合目标",
        }
        await self._remember_personality_auto_tune_status(result)
        if changes:
            logger.info(
                "角色贴合校准已自主调节参数: %s",
                "; ".join(f"{item['key']}={item['from']}->{item['to']}" for item in changes),
            )
        return result

    async def _personality_auto_tune_state_snapshot(self) -> dict[str, Any]:
        async with self.plugin._data_lock:
            state = self.plugin.data.get("personality_iteration_auto_tune")
            return deepcopy(state) if isinstance(state, dict) else {}

    async def _sync_personality_auto_tune_manual_snapshot(
        self,
        manual_values: dict[str, Any],
        applied: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        manual_values = dict(manual_values or {})
        applied = dict(applied or {})
        touched = False
        for key in self.PERSONALITY_AUTO_TUNE_KEYS:
            current_value = self._personality_auto_tune_current_value(key)
            manual_value = manual_values.get(key)
            applied_value = applied.get(key)
            if key not in manual_values:
                manual_values[key] = deepcopy(current_value)
                touched = True
            elif key in applied and current_value != applied_value and current_value != manual_value:
                manual_values[key] = deepcopy(current_value)
                applied.pop(key, None)
                touched = True
            elif key not in applied and current_value != manual_value:
                manual_values[key] = deepcopy(current_value)
                touched = True
        if touched:
            async with self.plugin._data_lock:
                state = self.plugin.data.setdefault("personality_iteration_auto_tune", {})
                if not isinstance(state, dict):
                    state = {}
                    self.plugin.data["personality_iteration_auto_tune"] = state
                state["manual_values"] = deepcopy(manual_values)
                state["applied"] = deepcopy(applied)
                state["manual_synced_at"] = time.time()
                self.plugin._save_data_sync(sections={"personality_iteration_auto_tune"})
        return manual_values, applied

    async def _remember_personality_auto_tune_status(self, status: dict[str, Any]) -> None:
        async with self.plugin._data_lock:
            state = self.plugin.data.setdefault("personality_iteration_auto_tune", {})
            if not isinstance(state, dict):
                state = {}
                self.plugin.data["personality_iteration_auto_tune"] = state
            state["last_status"] = deepcopy(status)
            state["last_status_at"] = time.time()
            self._save_plugin_sections(self.plugin, {"personality_iteration_auto_tune"})

    async def _restore_personality_iteration_auto_tune(
        self,
        reason: str = "",
        *,
        keys: list[str] | None = None,
        keep_state: bool = False,
    ) -> dict[str, Any]:
        async with self.plugin._data_lock:
            state = self.plugin.data.get("personality_iteration_auto_tune")
            if not isinstance(state, dict):
                return {}
            manual_values = state.get("manual_values")
            if not isinstance(manual_values, dict):
                manual_values = state.get("baseline") if isinstance(state.get("baseline"), dict) else {}
            applied = state.get("applied") if isinstance(state.get("applied"), dict) else {}
            target_keys = [key for key in (keys or list(applied.keys())) if key in self.PERSONALITY_AUTO_TUNE_KEYS]
            restore_values = {
                key: deepcopy(manual_values.get(key))
                for key in target_keys
                if key in manual_values
            }
        if not restore_values:
            return {}

        restored: dict[str, Any] = {}
        changes: list[dict[str, Any]] = []
        for key, manual_value in restore_values.items():
            current_value = self._personality_auto_tune_current_value(key)
            normalized = self._normalize_setting_value(key, manual_value)
            self._apply_config_value(key, normalized)
            restored[key] = normalized
            if current_value != normalized:
                changes.append(
                    {
                        "key": key,
                        "label": self._personality_auto_tune_label(key),
                        "from": current_value,
                        "to": normalized,
                        "reason": self._single_line(reason, 120) or "恢复用户最后一次手动设置",
                    }
                )
        config_saved = await self._save_config_if_possible()
        async with self.plugin._data_lock:
            state = self.plugin.data.setdefault("personality_iteration_auto_tune", {})
            if keep_state:
                applied = state.get("applied") if isinstance(state.get("applied"), dict) else {}
                for key in restored:
                    applied.pop(key, None)
                state["applied"] = applied
            else:
                state.clear()
            state["last_restore"] = {
                "restored": deepcopy(restored),
                "changes": deepcopy(changes),
                "reason": self._single_line(reason, 160),
                "restored_at": time.time(),
                "config_saved": config_saved,
            }
            self._save_plugin_sections(self.plugin, {"personality_iteration_auto_tune"})
        if changes:
            logger.info(
                "角色贴合校准已恢复用户手动参数: %s",
                "; ".join(f"{item['key']}={item['from']}->{item['to']}" for item in changes),
            )
        return {
            "restored": restored,
            "changes": changes,
            "reason": self._single_line(reason, 160),
            "config_saved": config_saved,
        }

    def _personality_iteration_auto_tune_plan(
        self,
        suggestions: list[dict[str, str]],
        reference_values: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        reference_values = reference_values or {}
        plan: dict[str, dict[str, Any]] = {}

        def current_int(key: str, default: int = 0) -> int:
            return self._int(reference_values.get(key, getattr(self.plugin, key, default)), default)

        def current_text(key: str, default: str = "") -> str:
            return str(reference_values.get(key, getattr(self.plugin, key, default)) or default).strip()

        def propose(key: str, value: Any, reason: str) -> None:
            normalized = self._normalize_setting_value(key, value)
            if self._normalize_setting_value(key, reference_values.get(key, self._personality_auto_tune_current_value(key))) == normalized:
                return
            existing = plan.get(key)
            if existing:
                existing["value"] = normalized
                existing["reason"] = f"{existing.get('reason')}; {reason}"
            else:
                plan[key] = {"value": normalized, "reason": reason}

        def raise_to(key: str, target: int, step: int, reason: str, default: int = 0) -> None:
            current = current_int(key, default)
            if current < target:
                propose(key, min(target, current + step), reason)

        def lower_to(key: str, target: int, step: int, reason: str, default: int = 0) -> None:
            current = current_int(key, default)
            if current > target:
                propose(key, max(target, current - step), reason)

        def set_review_balanced(reason: str) -> None:
            if current_text("proactive_review_strength", "lenient") == "lenient":
                propose("proactive_review_strength", "balanced", reason)

        def soften_high_preset(reason: str) -> None:
            preset = current_text("proactive_intensity_preset", "off")
            if preset in {"live", "high_private"}:
                propose("proactive_intensity_preset", "balanced", reason)

        for suggestion in suggestions:
            dimension = self._single_line(suggestion.get("dimension"), 80)
            level = self._single_line(suggestion.get("level"), 16)
            if level not in {"warn", "error", "info"}:
                continue
            if "外向性表现偏高" in dimension:
                soften_high_preset("外向性偏高时先从最高主动预设退到标准偏主动")
                lower_to("max_daily_messages", 8, 2, "外向性偏高，降低每日私聊主动上限", 8)
                raise_to("idle_minutes", 45, 15, "外向性偏高，拉长空闲判定", 60)
                raise_to("min_interval_minutes", 90, 30, "外向性偏高，拉长主动最小间隔", 120)
                raise_to("proactive_persona_judge_send_threshold", 58, 4, "外向性偏高，提高主动人格放行阈值", 62)
            elif "焦虑型追问倾向" in dimension:
                soften_high_preset("沉默后仍追问时先从最高主动预设退到标准偏主动")
                lower_to("max_daily_messages", 6, 2, "焦虑型追问倾向，降低每日主动上限", 8)
                raise_to("min_interval_minutes", 180, 60, "焦虑型追问倾向，显著拉长主动间隔", 120)
                raise_to("proactive_persona_judge_send_threshold", 64, 6, "焦虑型追问倾向，提高主动放行阈值", 62)
                set_review_balanced("焦虑型追问倾向，主动复核由宽松改为标准")
            elif "回避型收缩风险" in dimension:
                lower_to("min_interval_minutes", 240, 60, "回避型收缩风险，保留低打扰入口", 120)
                lower_to("idle_minutes", 120, 30, "回避型收缩风险，避免完全退开", 60)
                if current_int("max_daily_messages", 8) < 2:  # 上限已被压到 2 以下时保留低频兜底
                    propose("max_daily_messages", 2, "回避型收缩风险，保留很低频主动上限")
                if current_int("proactive_persona_judge_send_threshold", 62) > 70:
                    lower_to("proactive_persona_judge_send_threshold", 66, 4, "回避型收缩风险，放宽过高的主动阈值", 62)
            elif "动机质量偏低" in dimension:
                raise_to("proactive_persona_judge_send_threshold", 60, 4, "主动由头偏弱，提高放行阈值", 62)
                set_review_balanced("主动由头偏弱，主动复核由宽松改为标准")
            elif "讨好型修复倾向" in dimension:
                raise_to("proactive_persona_judge_send_threshold", 62, 4, "讨好型修复倾向，提高主动放行阈值", 62)
                set_review_balanced("讨好型修复倾向，主动复核由宽松改为标准")
        return plan

    def _personality_auto_tune_current_value(self, key: str) -> Any:
        return getattr(self.plugin, key, self._config_get(key))

    @staticmethod
    def _personality_auto_tune_label(key: str) -> str:
        labels = {
            "proactive_intensity_preset": "主动强度预设",
            "max_daily_messages": "每日私聊主动上限",
            "idle_minutes": "空闲判定分钟",
            "min_interval_minutes": "主动最小间隔分钟",
            "proactive_persona_judge_send_threshold": "主动人格放行阈值",
            "proactive_review_strength": "主动复核强度",
        }
        return labels.get(key, key)

    def _personality_auto_tune_diagnostic_item(self, result: dict[str, Any] | None) -> dict[str, str] | None:
        if not isinstance(result, dict) or not result:
            return None
        changes = result.get("changes") if isinstance(result.get("changes"), list) else []
        restored_changes = result.get("restored_changes") if isinstance(result.get("restored_changes"), list) else []
        if result.get("restored") is not None:
            restored_changes = changes
        if restored_changes:
            text = "；".join(
                f"{self._single_line(item.get('label'), 30)}：{item.get('from')} → {item.get('to')}"
                for item in restored_changes[:6]
            )
            return {
                "level": "ok",
                "title": "角色贴合校准：已恢复用户手动参数",
                "text": text,
                "action": "关闭角色贴合校准、关闭自主调节，或对应问题消失后，会恢复到用户最后一次手动设置的值。",
            }
        if not result.get("changed") or not changes:
            return None
        text = "；".join(
            f"{self._single_line(item.get('label'), 30)}：{item.get('from')} → {item.get('to')}（手动值 {item.get('manual')}；{self._single_line(item.get('reason'), 60)}）"
            for item in changes[:6]
        )
        return {
            "level": "info",
            "title": "角色贴合校准：已自主调节参数",
            "text": text,
            "action": "这是临时覆盖；用户再次手动调整后会更新手动值，关闭功能时恢复到最新手动值。",
        }

    def _personality_iteration_suggestions(self, users: dict[str, Any], groups: dict[str, Any]) -> list[dict[str, str]]:
        data = getattr(self.plugin, "data", {}) if isinstance(getattr(self.plugin, "data", {}), dict) else {}
        raw_candidates = data.get("proactive_candidate_pool") if isinstance(data.get("proactive_candidate_pool"), list) else []
        recent_candidates = [item for item in raw_candidates[-80:] if isinstance(item, dict)]
        suggestions: list[dict[str, str]] = []

        def add(level: str, dimension: str, evidence: str, risk: str, suggestion: str, scope: str, confidence: str = "中") -> None:
            text = f"校准依据：{dimension}；运行证据：{evidence}；可能不贴合：{risk}；调整位置：{scope}；建议写法：{suggestion}；置信度：{confidence}。"
            suggestions.append(
                {
                    "level": level,
                    "dimension": dimension,
                    "text": text,
                    "action": suggestion,
                }
            )

        def candidate_text(item: dict[str, Any]) -> str:
            parts = [
                item.get("reason"),
                item.get("action"),
                item.get("topic"),
                item.get("motive"),
                item.get("note"),
                item.get("semantic_kind"),
                item.get("semantic_note"),
            ]
            return " ".join(self._single_line(part, 120) for part in parts if part)

        candidate_texts = [candidate_text(item) for item in recent_candidates]
        joined_candidates = "\n".join(candidate_texts[-40:])
        generic_markers = (
            "check_in",
            "greeting",
            "morning",
            "evening",
            "近况",
            "问候",
            "早安",
            "晚安",
            "在吗",
            "忙不忙",
            "还好吗",
            "有没有空",
        )
        concrete_markers = (
            "qzone",
            "news",
            "bilibili",
            "reading",
            "web",
            "日程",
            "空间",
            "新闻",
            "视频",
            "阅读",
            "创作",
            "天气",
            "通勤",
            "图片",
            "照片",
            "说说",
        )
        generic_count = sum(1 for text in candidate_texts if any(marker in text.lower() for marker in generic_markers))
        concrete_count = sum(1 for text in candidate_texts if any(marker in text.lower() for marker in concrete_markers))
        pending_generic = [
            item
            for item in recent_candidates
            if self._single_line(item.get("status"), 24).lower() in {"", "accepted", "deferred", "queued", "pending", "unknown"}
            and any(marker in candidate_text(item).lower() for marker in generic_markers)
        ]

        enabled_user_items = [item for item in users.values() if isinstance(item, dict) and item.get("enabled", True)]
        active_users: list[dict[str, Any]] = []
        unanswered_users: list[dict[str, Any]] = []
        high_sent_users: list[dict[str, Any]] = []
        for user in enabled_user_items:
            if bool(getattr(self.plugin, "_user_enabled_for_proactive", lambda uid, profile: bool(profile and profile.get("enabled", True)))(
                str(user.get("user_id") or ""),
                user,
            )):
                active_users.append(user)
            ignored = self._int(user.get("ignored_streak"))
            sent_today = self._int(user.get("sent_today"))
            if ignored >= 2:
                unanswered_users.append(user)
            if sent_today >= 4:
                high_sent_users.append(user)

        effective_max_daily = self._int(getattr(self.plugin, "max_daily_messages", 0))
        max_daily_getter = getattr(self.plugin, "_runtime_max_daily_messages", None)
        if callable(max_daily_getter):
            try:
                effective_max_daily = self._int(max_daily_getter())
            except Exception:
                pass
        idle_minutes = self._int(getattr(self.plugin, "idle_minutes", 0))
        min_interval = self._int(getattr(self.plugin, "min_interval_minutes", 0))

        if active_users and (effective_max_daily >= 10 or (idle_minutes and idle_minutes <= 30) or high_sent_users):
            evidence_parts = []
            if effective_max_daily >= 10:
                evidence_parts.append(f"私聊主动上限 {effective_max_daily}")
            if idle_minutes and idle_minutes <= 30:
                evidence_parts.append(f"空闲 {idle_minutes} 分钟即可主动")
            if high_sent_users:
                labels = [self._single_line(item.get("nickname") or item.get("user_id"), 32) for item in high_sent_users[:3]]
                evidence_parts.append(f"今日主动较多：{'、'.join(labels)}")
            add(
                "warn",
                "艾森克 PEN：外向性表现偏高",
                "，".join(evidence_parts) or "主动频率较高",
                "如果角色基线不是高外向，容易从“自然有生活”变成“总想找人说话”。",
                "主动靠近需要具体由头；连续无人回应时优先安静生活，不继续泛泛问候。",
                "AstrBot 人格 / 主动策略",
                "中",
            )

        if unanswered_users and (pending_generic or generic_count >= 3):
            labels = [self._single_line(item.get("nickname") or item.get("user_id"), 32) for item in unanswered_users[:3]]
            add(
                "warn",
                "依恋风格：焦虑型追问倾向",
                f"{'、'.join(labels)} 未回应次数较高；近期仍存在 {len(pending_generic) or generic_count} 条问候/近况类主动候选",
                "如果角色应当给人稳定感，沉默后追问会像在索取回应。",
                "对方沉默时先降频；只在有明确事件、共同约定或用户关心的内容时再开口。",
                "AstrBot 人格 / 私聊主动强度 / 关系策略",
                "高" if len(unanswered_users) >= 2 else "中",
            )

        if unanswered_users and min_interval >= 360 and not pending_generic:
            labels = [self._single_line(item.get("nickname") or item.get("user_id"), 32) for item in unanswered_users[:3]]
            add(
                "info",
                "依恋风格：回避型收缩风险",
                f"{'、'.join(labels)} 已有未回应记录；当前最小主动间隔 {min_interval} 分钟，且近期没有可解释的轻量候选",
                "如果角色基线是亲近但有边界，完全退开会显得忽冷忽热。",
                "保留低打扰入口：只在日程节点、共同约定或用户明确关心的事情上轻轻接一次。",
                "私聊主动强度 / 关系策略",
                "低",
            )

        if recent_candidates and generic_count >= 4 and concrete_count <= max(1, generic_count // 3):
            add(
                "warn",
                "自我决定理论：动机质量偏低",
                f"近期主动候选里泛问候约 {generic_count} 条，具体生活/外部事件锚点约 {concrete_count} 条",
                "主动缺少关系感、能力感或自主感来源时，会像模板关心而不是角色自然想说。",
                "主动来源优先写成三类：共同经历、用户正在做的事、角色自己的生活发现；没有锚点宁可不发。",
                "世界知识 / 主动来源 / 日程细化",
                "中",
            )

        if getattr(self.plugin, "enable_group_companion", False) and not bool(getattr(self.plugin, "enable_group_persona_denoise", False)):
            add(
                "info",
                "大五人格：宜人性与公开边界",
                "群聊陪伴已启用，但群聊人格去噪未开启",
                "如果角色私聊很亲近，群聊里照搬亲密语气会破坏公开场合边界。",
                "开启群聊人格去噪；或写明：私聊可亲近，群聊公开场合更克制、更少暧昧和私密称呼。",
                "群聊页 / AstrBot 人格",
                "低",
            )

        if bool(getattr(self.plugin, "enable_emotion_simulation", False)) and bool(getattr(self.plugin, "enable_qzone_emotional_vent_publish", False)):
            add(
                "info",
                "艾森克 PEN：情绪稳定性外显",
                "情绪模拟和 QQ 空间情绪宣泄动态同时开启",
                "如果角色不是高敏感外显型，公开宣泄会显得比设定更戏剧化。",
                "公开动态偏生活化；强烈情绪先私下整理，不把用户压力公开化。",
                "QQ 空间配置 / 世界知识 / AstrBot 人格",
                "低",
            )

        if joined_candidates and ("抱歉" in joined_candidates or "对不起" in joined_candidates) and generic_count >= 2:
            add(
                "info",
                "依恋风格：讨好型修复倾向",
                "近期主动候选同时出现道歉/修复表达和泛问候",
                "角色可能把普通沉默解释成自己做错了，导致姿态过低。",
                "只有用户明确不适或发生冲突时才道歉；日常沉默按对方忙处理。",
                "AstrBot 人格 / 回复策略 / 主动策略",
                "低",
            )

        return suggestions[:5]
