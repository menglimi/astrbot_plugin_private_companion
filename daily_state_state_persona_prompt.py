# -*- coding: utf-8 -*-
"""DailyStateStatePersonaPromptMixin。

由 tools/split_mixin_domain.py 从 daily_state_state.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 315 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateStateMixin）。
"""
from __future__ import annotations

from .daily_state_state_shared import DEFAULT_PERSONA_PROMPT_FALLBACK, _now_ts, logger
from .daily_state_state_shared import Any
from .daily_state_state_shared import PromptRenderMode
from .daily_state_state_shared import PromptSection
from .daily_state_state_shared import _safe_float
from .daily_state_state_shared import _single_line
from .daily_state_state_shared import asyncio
from .daily_state_state_shared import prompt_section
from .daily_state_state_shared import re
from .daily_state_state_shared import render_prompt_sections
from .daily_state_state_shared import runtime_persona_setting
from .daily_state_state_shared import sqlite3
from .daily_state_state_shared import unicodedata



class DailyStateStatePersonaPromptMixin:
    """DailyStateStatePersonaPromptMixin（从 DailyStateStateMixin 拆出）。"""


    @staticmethod
    def _normalize_schedule_basis(value: Any, *, default: list[str] | None = None) -> list[str]:
        allowed = {"calendar", "persona", "adjustment", "state", "weather", "continuity", "inspiration", "coarse_plan"}
        raw = value if isinstance(value, list) else re.split(r"[,，;；\s]+", str(value or ""))
        result: list[str] = []
        for item in raw:
            key = _single_line(item, 24).lower()
            if key in allowed and key not in result:
                result.append(key)
        return result[:3] or list(default or [])[:3]

    @staticmethod
    def _persona_prompt_cache_scope(umo: str = "", specific_id: str = "") -> str:
        if specific_id:
            return f"persona:{specific_id}"
        if umo:
            return f"session:{umo}"
        return "default"

    def _cached_persona_prompt_for_scope(self, umo: str = "", specific_id: str = "") -> tuple[str, float]:
        scope = self._persona_prompt_cache_scope(umo, specific_id)
        entries = getattr(self, "_default_persona_prompt_cache_by_scope", None)
        if isinstance(entries, dict):
            entry = entries.get(scope)
            if isinstance(entry, dict):
                return (
                    str(entry.get("prompt") or "").strip(),
                    _safe_float(entry.get("cached_at"), 0.0),
                )
        return "", 0.0

    def _store_persona_prompt_for_scope(self, prompt: str, *, umo: str = "", specific_id: str = "") -> str:
        cleaned = str(prompt or "").strip()
        if not cleaned:
            return ""
        entries = getattr(self, "_default_persona_prompt_cache_by_scope", None)
        if not isinstance(entries, dict):
            entries = {}
            self._default_persona_prompt_cache_by_scope = entries
        now = _now_ts()
        entries[self._persona_prompt_cache_scope(umo, specific_id)] = {
            "prompt": cleaned,
            "cached_at": now,
            "umo": umo,
            "persona_id": specific_id,
        }
        if len(entries) > 64:
            newest = sorted(
                entries.items(),
                key=lambda item: _safe_float(item[1].get("cached_at"), 0.0) if isinstance(item[1], dict) else 0.0,
                reverse=True,
            )[:64]
            self._default_persona_prompt_cache_by_scope = dict(newest)
        # Keep legacy fields synchronized for code paths that do not have a session key.
        self._default_persona_prompt_cache = cleaned
        self._default_persona_prompt_cache_at = now
        self._default_persona_prompt_cache_umo = umo
        self._default_persona_prompt_cache_persona_id = specific_id
        return cleaned

    def _get_default_persona_prompt(self, umo: str = "") -> str:
        specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
        scoped, _ = self._cached_persona_prompt_for_scope(umo, specific_id)
        if scoped:
            return scoped
        cached = str(getattr(self, "_default_persona_prompt_cache", "") or "").strip()
        cached_persona_id = str(getattr(self, "_default_persona_prompt_cache_persona_id", "") or "")
        cached_umo = str(getattr(self, "_default_persona_prompt_cache_umo", "") or "")
        if cached and (
            (specific_id and cached_persona_id == specific_id)
            or (not specific_id and not cached_persona_id and (not umo or cached_umo == umo))
        ):
            return cached
        return DEFAULT_PERSONA_PROMPT_FALLBACK

    def _extract_default_persona_prompt(self, persona: Any) -> str:
        if isinstance(persona, dict):
            return str(persona.get("prompt") or "").strip()
        if isinstance(persona, str):
            return persona.strip()
        for attr in ("prompt", "system_prompt", "content"):
            try:
                value = getattr(persona, attr, None)
            except Exception:
                value = None
            text = str(value or "").strip()
            if text:
                return text
        return ""

    async def _refresh_default_persona_prompt(self, umo: str = "") -> str:
        def _cancel_requested() -> bool:
            # A database/manager implementation may raise CancelledError for
            # its own failed lookup. Preserve cancellation requested for the
            # plugin task itself so shutdown remains responsive.
            try:
                task = asyncio.current_task()
                return bool(task is not None and task.cancelling())
            except RuntimeError:
                return False

        try:
            specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
            cached, cached_at = self._cached_persona_prompt_for_scope(umo, specific_id)
            if not cached:
                legacy_cached = str(getattr(self, "_default_persona_prompt_cache", "") or "").strip()
                legacy_umo = str(getattr(self, "_default_persona_prompt_cache_umo", "") or "")
                legacy_persona_id = str(getattr(self, "_default_persona_prompt_cache_persona_id", "") or "")
                if (
                    (specific_id and legacy_persona_id == specific_id)
                    or (not specific_id and not legacy_persona_id and (not umo or legacy_umo == umo))
                ):
                    cached = legacy_cached
                    cached_at = _safe_float(getattr(self, "_default_persona_prompt_cache_at", 0.0), 0.0)
            cache_fresh = cached and (_now_ts() - cached_at < 300.0)
            if cache_fresh:
                return cached

            manager = getattr(getattr(self, "context", None), "persona_manager", None)
            if manager and specific_id:
                try:
                    specific_getter = getattr(manager, "get_persona", None)
                    if callable(specific_getter):
                        result = await self._await_framework_db_query(
                            f"persona:{specific_id}",
                            lambda: specific_getter(specific_id),
                            timeout=2.0,
                        )
                        prompt = self._extract_default_persona_prompt(result)
                        if prompt:
                            return self._store_persona_prompt_for_scope(prompt, umo=umo, specific_id=specific_id)
                except asyncio.CancelledError:
                    if _cancel_requested():
                        raise
                    logger.debug(
                        "指定人格查询被管理器取消(ID: %s),本轮使用缓存人格",
                        specific_id,
                    )
                    return cached or self._get_default_persona_prompt(umo)
                except (sqlite3.OperationalError, sqlite3.ProgrammingError) as exc:
                    logger.debug(
                        "指定人格数据库暂不可用(ID: %s),本轮使用缓存人格: %s",
                        specific_id,
                        _single_line(exc, 160),
                    )
                    return cached or self._get_default_persona_prompt(umo)
                except asyncio.TimeoutError:
                    logger.warning("读取插件指定人格超时(ID: %s),本轮使用缓存人格", specific_id)
                    return cached or self._get_default_persona_prompt(umo)
                except Exception as e:
                    logger.warning(f"读取插件指定人格失败(ID: {specific_id}): {e}")
            getter = getattr(manager, "get_default_persona_v3", None) if manager else None
            if not callable(getter):
                return cached or self._get_default_persona_prompt(umo)
            def _read_default_persona() -> Any:
                try:
                    return getter(umo=umo)
                except TypeError:
                    try:
                        return getter(umo)
                    except TypeError:
                        return getter()

            result = await self._await_framework_db_query(
                f"default_persona:{umo}",
                _read_default_persona,
                timeout=2.0,
            )
            prompt = self._extract_default_persona_prompt(result)
            if prompt:
                return self._store_persona_prompt_for_scope(prompt, umo=umo, specific_id="")
        except asyncio.CancelledError:
            if _cancel_requested():
                raise
            logger.debug("默认人格查询被管理器取消,本轮使用缓存人格")
        except (sqlite3.OperationalError, sqlite3.ProgrammingError) as exc:
            logger.debug(
                "默认人格数据库暂不可用,本轮使用缓存人格: %s",
                _single_line(exc, 160),
            )
        except asyncio.TimeoutError:
            logger.warning("读取 AstrBot 默认人格超时,本轮使用缓存人格")
        except Exception as e:
            logger.warning(f"读取 AstrBot 默认人格失败: {e}")
        return self._get_default_persona_prompt(umo)

    def _schedule_default_persona_prompt_refresh(self, umo: str = "") -> None:
        specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
        cached, cached_at = self._cached_persona_prompt_for_scope(umo, specific_id)
        cache_fresh = cached and (_now_ts() - cached_at < 300.0)
        if cache_fresh:
            return
        scope = self._persona_prompt_cache_scope(umo, specific_id)
        tasks = getattr(self, "_default_persona_prompt_refresh_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._default_persona_prompt_refresh_tasks = tasks
        task = tasks.get(scope)
        if isinstance(task, asyncio.Task) and not task.done():
            return

        async def _runner() -> None:
            try:
                await self._refresh_default_persona_prompt(umo)
            finally:
                current_tasks = getattr(self, "_default_persona_prompt_refresh_tasks", None)
                if isinstance(current_tasks, dict):
                    current_tasks.pop(scope, None)

        operation = _runner()
        creator = getattr(self, "_create_lifecycle_background_task", None)
        try:
            task = (
                creator(operation, label="default_persona_prompt_refresh")
                if callable(creator)
                else asyncio.create_task(operation, name="private-companion-persona-prompt-refresh")
            )
            if task is not None:
                tasks[scope] = task
                self._default_persona_prompt_refresh_task = task
                if not callable(creator):
                    def consume(done_task: asyncio.Task) -> None:
                        try:
                            done_task.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception as exc:
                            logger.warning(
                                "默认人格后台刷新失败: %s",
                                _single_line(exc, 160),
                            )

                    task.add_done_callback(consume)
            else:
                close = getattr(operation, "close", None)
                if callable(close):
                    close()
        except RuntimeError:
            close = getattr(operation, "close", None)
            if callable(close):
                close()

    def _format_plugin_persona_request_injection(self) -> str:
        section = self._format_plugin_persona_request_prompt_section()
        return (
            render_prompt_sections(
                [section],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
            if section is not None
            else ""
        )

    def _format_plugin_persona_request_prompt_section(self) -> PromptSection | None:
        specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
        if not specific_id:
            return None
        persona = self._get_default_persona_prompt()
        if not persona or persona == DEFAULT_PERSONA_PROMPT_FALLBACK:
            return None
        return prompt_section(
            key="persona.plugin_specific",
            title="本插件指定人格",
            source="daily_state",
            content=(
                "本轮私聊陪伴相关回复请优先遵循下面的人格设定。"
                "如果它与更高优先级系统安全规则冲突,以安全规则为准；如果与插件的状态/记忆材料冲突,以人格设定为准。\n"
                f"{persona}"
            ),
        )

    def _persona_state_profile(self) -> dict[str, bool]:
        prompt = self._get_default_persona_prompt()
        role_prompt = str(runtime_persona_setting(self, "schedule_persona_prompt", "") or "")
        text = unicodedata.normalize("NFKC", f"{prompt}\n{role_prompt}").lower()
        compact = re.sub(r"\s+", "", text)

        def has_any(markers: tuple[str, ...]) -> bool:
            return any(marker in text or marker in compact for marker in markers)

        strong_non_human_markers = (
            "机器人", "机械体", "机体", "仿生", "android", "robot", "电子生命", "终端人格"
        )
        soft_non_human_markers = (
            "bot", "系统", "程序", "ai"
        )
        explicitly_human_markers = (
            "人类", "学生", "上班", "工作", "生活", "年龄", "岁",
            "吃饭", "睡觉", "起床", "洗漱", "身体", "生理期"
        )
        bodyless_markers = (
            "无实体", "没有实体", "没有身体", "无身体", "纯意识", "虚拟人格", "虚拟形象",
            "全息投影", "投影形态", "灵体", "幽灵", "意识体"
        )
        has_human_markers = has_any(explicitly_human_markers)
        has_bodyless_markers = has_any(bodyless_markers)
        has_strong_non_human = has_any(strong_non_human_markers)
        soft_non_human_hits = sum(1 for marker in soft_non_human_markers if marker in text)
        is_non_human = (has_strong_non_human or soft_non_human_hits >= 2) and not has_human_markers
        allow_health = bool(runtime_persona_setting(self, "enable_health_state", True))
        allow_hunger = bool(runtime_persona_setting(self, "enable_hunger_state", True))
        allow_cycle = bool(runtime_persona_setting(self, "enable_cycle_state", True))
        return {
            "non_human": is_non_human or has_bodyless_markers,
            "allow_health": allow_health,
            "allow_hunger": allow_hunger,
            "allow_cycle": allow_cycle,
        }

    def _base_state_values(self, profile: dict[str, bool] | None = None) -> dict[str, str]:
        profile = profile or self._persona_state_profile()
        values = {
            "sleep": "睡眠平稳",
            "dream": "没有记住梦",
            "health": "状态正常",
            "hunger": "无饥饿感",
            "body_cycle": "不处于生理期",
            "location": "",
        }
        if not profile.get("allow_health", True):
            values["health"] = "健康/不适状态未开启"
        if not profile.get("allow_hunger", True):
            values["hunger"] = "饥饿/胃口状态未开启"
        if not profile.get("allow_cycle", False):
            values["body_cycle"] = "生理期模拟未开启"
        return values
