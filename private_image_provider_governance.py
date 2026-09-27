# -*- coding: utf-8 -*-
"""PrivateImageProviderGovernanceMixin。

由 tools/split_mixin_domain.py 从 private_image.py 机械抽取（19 个方法 + 0 个模块级名字 + 0 个类级赋值 / 387 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageMixin）。
"""
from __future__ import annotations

import re
from .helpers import _safe_float, _safe_int, _single_line
from .private_image_shared import _private_image_host, logger
from typing import Any



class PrivateImageProviderGovernanceMixin:
    """PrivateImageProviderGovernanceMixin（从 PrivateImageMixin 拆出）。"""


    def _astrbot_provider_settings_for_umo(self, umo: str = "") -> dict[str, Any]:
        def provider_settings_from_config(cfg: Any) -> dict[str, Any]:
            provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
            return dict(provider_settings) if isinstance(provider_settings, dict) else {}

        try:
            global_cfg = self.context.get_config()
        except Exception:
            global_cfg = {}
        merged = provider_settings_from_config(global_cfg)
        if not umo:
            return merged
        try:
            session_cfg = self.context.get_config(umo=umo)
        except Exception:
            session_cfg = {}
        session_settings = provider_settings_from_config(session_cfg)
        for key, value in session_settings.items():
            if isinstance(value, str):
                if value.strip() or key not in merged:
                    merged[key] = value
            elif value is not None:
                merged[key] = value
        return merged

    def _private_image_caption_provider_id(self, umo: str = "") -> tuple[str, str, str]:
        candidates = self._private_image_visual_provider_candidates(umo)
        if candidates:
            return candidates[0]
        provider_settings = self._astrbot_provider_settings_for_umo(umo)
        return "", "", str(provider_settings.get("image_caption_prompt") or "").strip()

    def _private_image_provider_by_id(self, provider_id: str) -> Any:
        provider_id = _single_line(provider_id, 160)
        if not provider_id:
            return None
        getter = getattr(self.context, "get_provider_by_id", None)
        if not callable(getter):
            return None
        try:
            return getter(provider_id)
        except Exception:
            return None

    def _private_image_base_visual_provider_candidates(self, umo: str = "") -> list[tuple[str, str, str]]:
        provider_settings = self._astrbot_provider_settings_for_umo(umo)
        prompt = str(provider_settings.get("image_caption_prompt") or "").strip()
        fallback_key = self._private_image_visual_provider_card_key()
        plugin_provider_id = _single_line(
            self._private_image_setting("PLUGIN_VISION_PROVIDER_ID", getattr(self, "plugin_vision_provider_id", "")),
            160,
        )
        fallback_getter = getattr(self, "_model_fallback_provider_id", None)
        plugin_fallback_id = (
            fallback_getter(fallback_key, plugin_provider_id)
            if callable(fallback_getter)
            else ""
        )
        return [
            (_single_line(provider_settings.get("default_image_caption_provider_id"), 160), "astrbot_image_caption", prompt),
            (plugin_provider_id, "plugin_vision", prompt),
            (plugin_fallback_id, "plugin_vision_fallback", prompt),
        ]

    def _private_image_visual_provider_card_key(self) -> str:
        # Image input is an independent capability in both provider modes.
        # Never route it through a text/narration card merely because precision
        # mode is active; providers such as DeepSeek may not support images.
        return "PLUGIN_VISION_PROVIDER_ID"

    @staticmethod
    def _normalize_private_image_vision_provider_priority(value: Any) -> str:
        text = _single_line(value, 80).lower()
        aliases = {
            "astrbot": "astrbot_first",
            "framework": "astrbot_first",
            "default": "astrbot_first",
            "官方优先": "astrbot_first",
            "plugin": "plugin_first",
            "插件优先": "plugin_first",
            "recent": "recent_success_first",
            "adaptive": "recent_success_first",
            "动态": "recent_success_first",
            "近期成功优先": "recent_success_first",
        }
        normalized = aliases.get(text, text)
        return normalized if normalized in {"astrbot_first", "plugin_first", "recent_success_first"} else "astrbot_first"

    @staticmethod
    def _private_image_visual_provider_source_allowed(provider_source: str) -> bool:
        return _single_line(provider_source, 80) in {
            "astrbot_image_caption",
            "plugin_vision",
            "plugin_vision_fallback",
            "recent_success",
        }

    def _private_image_visual_provider_state_store(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        state = data.setdefault("private_image_visual_provider_state", {})
        if not isinstance(state, dict):
            state = {}
            data["private_image_visual_provider_state"] = state
        recent = state.get("recent_successes")
        if not isinstance(recent, list):
            state["recent_successes"] = []
        else:
            filtered = [
                item for item in recent
                if isinstance(item, dict)
                and _single_line(item.get("provider_id"), 160)
                and self._private_image_visual_provider_source_allowed(str(item.get("source") or ""))
            ]
            if len(filtered) != len(recent):
                state["recent_successes"] = filtered
        last_success = state.get("last_success")
        if isinstance(last_success, dict) and not self._private_image_visual_provider_source_allowed(str(last_success.get("source") or "")):
            next_last = state.get("recent_successes", [])
            state["last_success"] = dict(next_last[0]) if isinstance(next_last, list) and next_last and isinstance(next_last[0], dict) else {}
        return state

    def _note_private_image_visual_provider_success(
        self,
        provider_id: str,
        provider_source: str,
        *,
        umo: str = "",
        scope: str = "private_image",
        chars: int = 0,
    ) -> None:
        provider_id = _single_line(provider_id, 160)
        if not provider_id:
            return
        provider_source = _single_line(provider_source, 80) or "unknown"
        if not self._private_image_visual_provider_source_allowed(provider_source):
            return
        clean_umo = _single_line(umo, 160)
        now = _private_image_host._now_ts()
        state = self._private_image_visual_provider_state_store()
        if not isinstance(state, dict):
            return
        recent = state.get("recent_successes")
        if not isinstance(recent, list):
            recent = []
        kept: list[dict[str, Any]] = []
        previous_successes = 0
        cutoff = now - 7 * 86400
        for item in recent:
            if not isinstance(item, dict):
                continue
            item_provider = _single_line(item.get("provider_id"), 160)
            item_source = _single_line(item.get("source"), 80)
            item_umo = _single_line(item.get("umo"), 160)
            item_ts = _safe_float(item.get("ts"), 0)
            if not self._private_image_visual_provider_source_allowed(item_source):
                continue
            if item_provider == provider_id and item_source == provider_source and item_umo == clean_umo:
                previous_successes = max(previous_successes, _safe_int(item.get("successes"), 0, 0))
                continue
            if item_provider and item_ts >= cutoff:
                kept.append(item)
        entry = {
            "provider_id": provider_id,
            "source": provider_source,
            "umo": clean_umo,
            "scope": _single_line(scope, 40) or "private_image",
            "ts": now,
            "successes": previous_successes + 1,
            "chars": max(0, int(chars or 0)),
        }
        state["recent_successes"] = [entry, *kept][:8]
        state["last_success"] = dict(entry)
        scheduler = getattr(self, "_schedule_data_save", None)
        try:
            if callable(scheduler):
                scheduler(sections={"private_image_visual_provider_state"}, delay=2.0)
            else:
                self._save_data_sync(sections={"private_image_visual_provider_state"})
        except Exception as exc:
            logger.debug("私聊图片视觉成功 provider 状态保存失败: %s", exc)

    def _private_image_visual_provider_candidates(self, umo: str = "") -> list[tuple[str, str, str]]:
        base = self._private_image_base_visual_provider_candidates(umo)
        by_provider: dict[str, tuple[str, str, str]] = {}
        for provider_id, provider_source, prompt in base:
            clean_id = _single_line(provider_id, 160)
            if clean_id and clean_id not in by_provider:
                by_provider[clean_id] = (clean_id, provider_source, prompt)
        if not by_provider:
            return []
        state = self._private_image_visual_provider_state_store()
        recent = state.get("recent_successes") if isinstance(state, dict) else []
        clean_umo = _single_line(umo, 160)
        now = _private_image_host._now_ts()
        ordered: list[tuple[str, str, str]] = []
        used: set[str] = set()
        priority = self._normalize_private_image_vision_provider_priority(
            self._private_image_setting("private_image_vision_provider_priority", "astrbot_first")
        )
        base_ordered = list(base)
        if priority == "plugin_first":
            source_rank = {
                "plugin_vision": 0,
                "plugin_vision_fallback": 1,
                "astrbot_image_caption": 2,
            }
            base_ordered = sorted(
                enumerate(base_ordered),
                key=lambda pair: (source_rank.get(_single_line(pair[1][1], 80), 9), pair[0]),
            )
            base_ordered = [item for _index, item in base_ordered]

        recent_rows: list[tuple[int, dict[str, Any]]] = []
        if isinstance(recent, list):
            recent_rows = [(index, item) for index, item in enumerate(recent) if isinstance(item, dict)]

            def recent_provider_sort_key(pair: tuple[int, dict[str, Any]]) -> tuple[int, float, int]:
                item = pair[1]
                same_session_rank = 0 if clean_umo and _single_line(item.get("umo"), 160) == clean_umo else 1
                return same_session_rank, -_safe_float(item.get("ts"), 0), pair[0]

            recent_rows.sort(**{"key": recent_provider_sort_key})

        if priority == "recent_success_first":
            for _index, item in recent_rows:
                provider_id = _single_line(item.get("provider_id"), 160)
                if not provider_id or provider_id in used or provider_id not in by_provider:
                    continue
                if now - _safe_float(item.get("ts"), 0) > 7 * 86400:
                    continue
                ordered.append(by_provider[provider_id])
                used.add(provider_id)

        for provider_id, provider_source, prompt in base_ordered:
            clean_id = _single_line(provider_id, 160)
            if not clean_id or clean_id in used:
                continue
            ordered.append((clean_id, provider_source, prompt))
            used.add(clean_id)
        return ordered

    def _select_private_image_visual_provider(self, umo: str = "") -> tuple[str, str, str, Any]:
        seen: set[str] = set()
        for provider_id, provider_source, prompt in self._private_image_visual_provider_candidates(umo):
            provider_id = _single_line(provider_id, 160)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            if self._private_image_provider_in_failure_cooldown(provider_id, provider_source):
                continue
            provider = self._private_image_provider_by_id(provider_id)
            if provider is not None and self._provider_supports_image(provider):
                return provider_id, provider_source, prompt, provider
        return "", "", "", None

    def _has_private_image_visual_provider(self, umo: str = "") -> bool:
        provider_id, _provider_source, _prompt, provider = self._select_private_image_visual_provider(umo)
        return bool(provider_id and provider is not None)

    def _private_image_provider_failure_cache(self) -> dict[str, Any]:
        cache = getattr(self, "_private_image_provider_failures", None)
        if not isinstance(cache, dict):
            cache = {}
            try:
                setattr(self, "_private_image_provider_failures", cache)
            except Exception:
                return {}
        return cache

    def _private_image_provider_failure_key(self, provider_id: str, provider_source: str = "") -> str:
        return f"{_single_line(provider_source, 80)}:{_single_line(provider_id, 160)}"

    def _private_image_provider_in_failure_cooldown(self, provider_id: str, provider_source: str = "") -> bool:
        cooldown = _safe_float(
            self._private_image_setting("private_image_provider_failure_cooldown_seconds", 0.0),
            0.0,
            0.0,
        )
        if cooldown <= 0:
            return False
        key = self._private_image_provider_failure_key(provider_id, provider_source)
        item = self._private_image_provider_failure_cache().get(key)
        if not isinstance(item, dict):
            return False
        until = _safe_float(item.get("until"), 0)
        if until <= _private_image_host._now_ts():
            self._private_image_provider_failure_cache().pop(key, None)
            return False
        return True

    def _mark_private_image_provider_failure(self, provider_id: str, provider_source: str, exc: Exception | str, *, task: str) -> None:
        key = self._private_image_provider_failure_key(provider_id, provider_source)
        cooldown = _safe_float(
            self._private_image_setting("private_image_provider_failure_cooldown_seconds", 0.0),
            0.0,
            0.0,
            3600.0,
        )
        if cooldown <= 0:
            self._private_image_provider_failure_cache().pop(key, None)
            logger.debug(
                "图片视觉 provider 本轮失败但未启用跨轮冷却: provider=%s source=%s task=%s error=%s",
                provider_id,
                provider_source,
                task,
                _single_line(exc, 160),
            )
            return
        self._private_image_provider_failure_cache()[key] = {
            "until": _private_image_host._now_ts() + cooldown,
            "provider_id": _single_line(provider_id, 160),
            "source": _single_line(provider_source, 80),
            "task": _single_line(task, 80),
            "error": _single_line(exc, 180),
        }
        logger.info(
            "图片视觉 provider 临时降权: provider=%s source=%s task=%s cooldown=%ss error=%s",
            provider_id,
            provider_source,
            task,
            int(cooldown),
            _single_line(exc, 160),
        )

    def _clear_private_image_provider_failure(self, provider_id: str, provider_source: str = "") -> None:
        self._private_image_provider_failure_cache().pop(
            self._private_image_provider_failure_key(provider_id, provider_source),
            None,
        )

    def _private_image_vision_summary_unusable(self, text: str, *, allow_unlabeled_transcription: bool = False) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return True
        failure_tokens = (
            "无法查看图片", "无法看到图片", "无法识别图片", "无法读取图片", "无法打开图片",
            "看不到图片", "看不见图片", "不能查看图片", "不能识别图片", "图片无法显示",
            "没有收到图片", "未收到图片", "没有图片可供", "不支持视觉", "不支持图片输入",
            "没有视觉能力", "不能看图", "imagecannot", "cannotview", "cannotseeimage",
            "doesnotsupportvision", "doesn'tsupportvision", "notsupportimage",
        )
        if any(token in compact.lower() for token in failure_tokens):
            visible = re.sub(r"\s+", "", self._private_image_visible_line(text))
            if not visible:
                if allow_unlabeled_transcription and len(compact) >= 120:
                    return False
                return True
            # A screenshot can legitimately contain an error sentence such as
            # "模型不支持视觉". Preserve it only when the model also identified
            # concrete screenshot/chat content; otherwise treat it as a refusal.
            if self._private_image_type_kind(text) in {"screenshot", "chat"}:
                return False
            return True
        return False

    def _private_image_visual_provider_runtime_summary(self, umo: str = "") -> dict[str, Any]:
        state = self._private_image_visual_provider_state_store()
        recent = state.get("recent_successes") if isinstance(state, dict) else []
        candidates: list[dict[str, Any]] = []
        for provider_id, provider_source, _prompt in self._private_image_visual_provider_candidates(umo):
            clean_id = _single_line(provider_id, 160)
            if not clean_id:
                continue
            provider = self._private_image_provider_by_id(clean_id)
            candidates.append(
                {
                    "provider_id": clean_id,
                    "source": _single_line(provider_source, 80),
                    "available": provider is not None,
                    "supports_image": bool(provider is not None and self._provider_supports_image(provider)),
                    "cooldown": bool(self._private_image_provider_in_failure_cooldown(clean_id, provider_source)),
                }
            )
        last_success = state.get("last_success") if isinstance(state.get("last_success"), dict) else {}
        failures = list(self._private_image_provider_failure_cache().values())
        failures = [item for item in failures if isinstance(item, dict)]
        def failure_until_sort_key(item: dict[str, Any]) -> float:
            return _safe_float(item.get("until"), 0)

        failures.sort(**{"key": failure_until_sort_key, "reverse": True})
        return {
            "priority": self._normalize_private_image_vision_provider_priority(
                self._private_image_setting("private_image_vision_provider_priority", "astrbot_first")
            ),
            "last_success": {
                "provider_id": _single_line(last_success.get("provider_id"), 160),
                "source": _single_line(last_success.get("source"), 80),
                "time": self._format_timestamp_elapsed(last_success.get("ts", 0)) if hasattr(self, "_format_timestamp_elapsed") else "",
                "scope": _single_line(last_success.get("scope"), 40),
                "chars": _safe_int(last_success.get("chars"), 0, 0),
            } if last_success else {},
            "candidates": candidates[:8],
            "cooldowns": [
                {
                    "provider_id": _single_line(item.get("provider_id"), 160),
                    "source": _single_line(item.get("source"), 80),
                    "error": _single_line(item.get("error"), 180),
                    "until": self._format_timestamp_elapsed(item.get("until", 0)) if hasattr(self, "_format_timestamp_elapsed") else "",
                }
                for item in failures[:6]
            ],
            "recent_success_count": len(recent) if isinstance(recent, list) else 0,
        }
