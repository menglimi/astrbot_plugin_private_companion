# -*- coding: utf-8 -*-
"""外部能力注册域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 252 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations

import inspect
import re
from .constants import PROACTIVE_ABILITY_REGISTRY
from .helpers import _now_ts, _safe_float, _single_line
from copy import deepcopy
from typing import Any

from .logging_util import get_module_logger
from .proactive_engine_shared import _engine_host

logger = get_module_logger(__name__)



class ProactiveEngineAbilityMixin:
    """外部能力注册域（从 ProactiveEngineMixin 拆出）。"""


    @staticmethod
    def _normalize_external_ability_name(value: Any) -> str:
        text = str(value or "").strip().lower()
        text = re.sub(r"[^a-z0-9_.:-]+", "_", text)
        return text[:64].strip("_")

    def _external_ability_store(self) -> dict[str, Any]:
        if not isinstance(getattr(self, "data", None), dict):
            self.data = {}
        store = self.data.setdefault("external_proactive_abilities", {})
        if not isinstance(store, dict):
            store = {}
            self.data["external_proactive_abilities"] = store
        return store

    def register_external_proactive_ability(self, spec: dict[str, Any]) -> bool:
        if not isinstance(spec, dict):
            return False
        name = self._normalize_external_ability_name(spec.get("name"))
        executor = spec.get("executor")
        availability = spec.get("availability")
        if not name or not callable(executor):
            logger.warning("外部主动能力注册失败: name/executor 无效")
            return False
        default_config = spec.get("default_config") if isinstance(spec.get("default_config"), dict) else {}
        config_schema = spec.get("config_schema") if isinstance(spec.get("config_schema"), dict) else {}
        meta = {
            "name": name,
            "module": _single_line(spec.get("module"), 24) or "外部主动能力",
            "label": _single_line(spec.get("label"), 32) or name,
            "description": _single_line(spec.get("description"), 160),
            "when": _single_line(spec.get("when"), 120) or "外部插件认为合适的场景",
            "use_for": _single_line(spec.get("use_for"), 120) or _single_line(spec.get("description"), 120),
            "avoid": _single_line(spec.get("avoid"), 120) or "不要暴露插件调用过程,不要硬触发",
            "default_enabled": bool(spec.get("default_enabled", False)),
            "share_probability": max(0.0, min(1.0, _safe_float(spec.get("share_probability"), _safe_float(default_config.get("share_probability"), 0.12)))),
            "min_interval_hours": max(0.0, _safe_float(spec.get("min_interval_hours"), _safe_float(default_config.get("min_interval_hours"), 12))),
            "config_schema": deepcopy(config_schema),
            "default_config": deepcopy(default_config),
        }
        self._external_proactive_abilities[name] = {
            **meta,
            "executor": executor,
            "availability": availability if callable(availability) else None,
        }
        try:
            store = self._external_ability_store()
            item = store.get(name) if isinstance(store.get(name), dict) else {}
            config = item.get("config") if isinstance(item.get("config"), dict) else {}
            merged_config = {**default_config, **config}
            item.update({
                "name": name,
                "module": meta["module"],
                "label": meta["label"],
                "description": meta["description"],
                "when": meta["when"],
                "use_for": meta["use_for"],
                "avoid": meta["avoid"],
                "enabled": bool(item.get("enabled", meta["default_enabled"])),
                "share_probability": _safe_float(item.get("share_probability"), meta["share_probability"], 0.0),
                "min_interval_hours": _safe_float(item.get("min_interval_hours"), meta["min_interval_hours"], 0.0),
                "config": merged_config,
                "config_schema": deepcopy(config_schema),
                "registered": True,
                "updated_ts": _engine_host._now_ts(),
            })
            store[name] = item
            self._save_data_sync(sections={"external_proactive_abilities"})
        except Exception as exc:
            logger.debug("外部主动能力状态保存失败: %s", exc)
        logger.info("已注册外部主动能力: %s", name)
        return True

    def unregister_external_proactive_ability(self, name: str) -> bool:
        normalized = self._normalize_external_ability_name(name)
        removed = self._external_proactive_abilities.pop(normalized, None) is not None
        try:
            store = self._external_ability_store()
            item = store.get(normalized)
            if isinstance(item, dict):
                item["registered"] = False
                item["updated_ts"] = _engine_host._now_ts()
                self._save_data_sync(sections={"external_proactive_abilities"})
        except Exception:
            pass
        return removed

    def external_proactive_abilities(self) -> list[dict[str, Any]]:
        store = self.data.get("external_proactive_abilities") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(store, dict):
            store = {}
        names = sorted(set(store.keys()) | set(self._external_proactive_abilities.keys()))
        items: list[dict[str, Any]] = []
        for name in names:
            runtime = self._external_proactive_abilities.get(name, {})
            stored = store.get(name) if isinstance(store.get(name), dict) else {}
            merged = {
                **{
                    k: v
                    for k, v in runtime.items()
                    if k not in {"executor", "availability"}
                },
                **stored,
            }
            merged["name"] = name
            merged["available"] = callable(runtime.get("executor"))
            merged["registered"] = bool(runtime)
            merged["enabled"] = bool(merged.get("enabled", merged.get("default_enabled", False)))
            merged["share_probability"] = max(0.0, min(1.0, _safe_float(merged.get("share_probability"), 0.0)))
            merged["min_interval_hours"] = max(0.0, _safe_float(merged.get("min_interval_hours"), 0.0))
            items.append(merged)
        return items

    def _external_ability_config(self, name: str) -> dict[str, Any]:
        store = self.data.get("external_proactive_abilities") if isinstance(self.data.get("external_proactive_abilities"), dict) else {}
        item = store.get(name) if isinstance(store.get(name), dict) else {}
        config = item.get("config") if isinstance(item.get("config"), dict) else {}
        return dict(config)

    def _external_ability_enabled(self, name: str) -> bool:
        item = next((entry for entry in self.external_proactive_abilities() if entry.get("name") == name), None)
        if not isinstance(item, dict):
            return False
        return bool(item.get("enabled") and item.get("available"))

    def _available_external_proactive_abilities(self, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        now = _engine_host._now_ts()
        items: list[dict[str, Any]] = []
        has_user_context = bool(
            isinstance(user, dict)
            and _single_line(
                user.get("user_id") or user.get("id") or user.get("umo"),
                180,
            )
        )
        for item in self.external_proactive_abilities():
            name = str(item.get("name") or "")
            if not name or not item.get("enabled") or not item.get("available"):
                continue
            user_last = (
                user.get("external_proactive_ability_last")
                if isinstance(user, dict)
                and isinstance(user.get("external_proactive_ability_last"), dict)
                else {}
            )
            last = _safe_float(
                user_last.get(name, 0)
                if has_user_context and isinstance(user_last, dict)
                else item.get("last_executed_ts"),
                0,
            )
            cooldown = _safe_float(item.get("min_interval_hours"), 0) * 3600
            if cooldown > 0 and last > 0 and now - last < cooldown:
                continue
            runtime = self._external_proactive_abilities.get(name, {})
            availability = runtime.get("availability") if isinstance(runtime, dict) else None
            if callable(availability):
                try:
                    allowed = availability(
                        {
                            "user": dict(user or {}),
                            "config": self._external_ability_config(name),
                            "plugin": self,
                        }
                    )
                    if inspect.isawaitable(allowed):
                        closer = getattr(allowed, "close", None)
                        if callable(closer):
                            closer()
                        continue
                    if not bool(allowed):
                        continue
                except Exception as exc:
                    logger.debug(
                        "外部主动能力可用性检查失败: %s: %s",
                        name,
                        _single_line(exc, 120),
                    )
                    continue
            items.append(item)
        return items

    def _available_proactive_abilities(self, user: dict[str, Any] | None = None) -> list[dict[str, str]]:
        user = user if isinstance(user, dict) else {}
        available = {"message"}
        if self._screen_glance_available(user):
            available.add("screen_peek")
        if self._photo_text_available(user):
            available.add("photo_text")
        if self._poke_available() and self._effective_user_poke_daily_limit(user) > 0 and self._poke_action_cooldown_remaining(user) <= 0:
            available.add("poke")
        if self._voice_available(user):
            available.add("voice")
        items: list[dict[str, str]] = []
        for raw in PROACTIVE_ABILITY_REGISTRY:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name or name not in available:
                continue
            items.append({str(key): str(value) for key, value in raw.items()})
        for raw in self._available_external_proactive_abilities(user):
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            items.append(
                {
                    "module": _single_line(raw.get("module"), 24) or "外部主动能力",
                    "name": f"external:{name}",
                    "label": _single_line(raw.get("label"), 32) or name,
                    "when": _single_line(raw.get("when"), 120) or "外部插件认为合适时",
                    "use_for": _single_line(raw.get("use_for"), 120) or _single_line(raw.get("description"), 120),
                    "avoid": _single_line(raw.get("avoid"), 120) or "不要暴露插件调用过程",
                }
            )
        return items

    def _format_proactive_ability_search_hint(self, user: dict[str, Any] | None = None) -> str:
        abilities = self._available_proactive_abilities(user)
        if not abilities:
            return "可用动作：message=普通文字。"
        terms = self._worldview_terms()
        lines = ["可用动作："]
        for item in abilities:
            name = _single_line(item.get("name"), 24)
            label = _single_line(item.get("label"), 16)
            when = _single_line(item.get("when"), 80)
            use_for = _single_line(item.get("use_for"), 80)
            if name == "screen_peek":
                label = f"观察{terms['screen']}"
                when = when.replace("轻窥屏", f"看一眼{terms['screen']}").replace("探头一下", "轻轻确认一下")
            elif name == "photo_text" and terms.get("mode") in {"fantasy", "sci_fi"}:
                label = "画面加一句话"
            lines.append(
                "- {name}（{label}）：{when}；{use_for}".format(
                    name=name,
                    label=label,
                    when=when,
                    use_for=use_for,
                )
            )
        preference_hint = self._action_preference_hint(user)
        if preference_hint:
            lines.append("用户媒介偏好：\n" + preference_hint)
        return "\n".join(lines)

    def _format_proactive_ability_list_for_user(self, user: dict[str, Any] | None = None) -> str:
        abilities = self._available_proactive_abilities(user)
        if not abilities:
            return "当前主动能力：文字私聊。"
        terms = self._worldview_terms()
        lines = ["当前主动能力："]
        for item in abilities:
            name = str(item.get("name") or "")
            label = item.get("label")
            when = item.get("when")
            if name == "screen_peek":
                label = f"观察{terms['screen']}"
            lines.append(
                f"- {item.get('module')}/{name}：{label}｜{when}"
            )
        return "\n".join(lines)
