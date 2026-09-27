# -*- coding: utf-8 -*-
"""ContentCompanionBridgePart02Mixin。

由 tools/split_mixin_domain.py 从 content_companion_bridge.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 260 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ContentCompanionBridgeMixin）。
"""
from __future__ import annotations

from .content_companion_bridge_shared import _raise_story_write_fence
from .content_companion_bridge_shared import Any
from .content_companion_bridge_shared import _safe_float
from .content_companion_bridge_shared import _single_line
from .content_companion_bridge_shared import asyncio
from .content_companion_bridge_shared import random
from .content_companion_bridge_shared import runtime_persona_setting
from .content_companion_bridge_shared import _content_companion_bridge_host
from .content_companion_bridge_shared import time



class ContentCompanionBridgePart02Mixin:
    """ContentCompanionBridgePart02Mixin（从 ContentCompanionBridgeMixin 拆出）。"""


    async def _content_story_maybe_start_current(
        self,
        *,
        idle_checked: bool,
    ) -> tuple[bool, bool]:
        state = _content_companion_bridge_host.story_authority_controller().authority_state()
        if state in {"created", "open"}:
            return False, False
        if state != "committed":
            _raise_story_write_fence(state)
        if not runtime_persona_setting(self, "enable_creative_writing", False):
            return True, False
        idle_checker = getattr(self, "_bot_currently_idle_for_creative_writing", None)
        if not idle_checked and callable(idle_checker) and not idle_checker():
            return True, False

        lock = getattr(self, "_content_story_start_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._content_story_start_lock = lock
        async with lock:
            claimed, listing = await self._content_story_execute("list")
            if not claimed or listing is None:
                return True, False
            projects = listing.get("projects")
            if type(projects) is not list:
                return True, False
            active = [
                item
                for item in projects
                if isinstance(item, dict) and item.get("status") == "drafting"
            ]
            try:
                maximum_active = int(
                    runtime_persona_setting(self, "creative_max_active_projects", 2)
                )
            except (TypeError, ValueError):
                maximum_active = 2
            if len(active) >= max(1, min(maximum_active, 20)):
                return True, False
            last_created = max(
                (
                    _safe_float(item.get("created_at"), 0)
                    for item in projects
                    if isinstance(item, dict)
                ),
                default=0,
            )
            if time.time() - last_created < 10 * 3600:
                return True, False
            try:
                probability = float(
                    runtime_persona_setting(
                        self,
                        "creative_inspiration_probability",
                        0.2,
                    )
                )
            except (TypeError, ValueError):
                probability = 0.2
            if random.random() > max(0.0, min(probability, 1.0)):
                return True, False
            source_getter = getattr(self, "_creative_inspiration_source", None)
            source = source_getter() if callable(source_getter) else None
            if not isinstance(source, dict):
                return True, False
            prompt = _single_line(source.get("text"), 220)
            if not prompt:
                return True, False
            style = _single_line(
                runtime_persona_setting(self, "default_style", ""),
                80,
            )
            claimed, result = await self._content_story_execute(
                "start",
                author_prompt=prompt,
                style=style,
            )
            return claimed, bool(
                isinstance(result, dict) and isinstance(result.get("project"), dict)
            )

    async def _maybe_advance_creative_projects(self) -> None:
        if not getattr(self, "_content_companion_delegating", False):
            state = _content_companion_bridge_host.story_authority_controller().authority_state()
            if state not in {"created", "open"}:
                if state == "committed":
                    if not runtime_persona_setting(
                        self,
                        "enable_creative_writing",
                        False,
                    ):
                        return
                    pending_checker = getattr(
                        self,
                        "_creative_has_pending_proactive_plan",
                        None,
                    )
                    if callable(pending_checker) and pending_checker():
                        return
                    idle_checker = getattr(
                        self,
                        "_bot_currently_idle_for_creative_writing",
                        None,
                    )
                    if callable(idle_checker) and not idle_checker():
                        return
                    await self._content_story_maybe_start_current(idle_checked=True)
                    try:
                        base_budget = int(
                            runtime_persona_setting(
                                self,
                                "creative_chars_per_session",
                                220,
                            )
                        )
                    except (TypeError, ValueError):
                        base_budget = 220
                    output_limit = max(
                        60,
                        min(1200, int(base_budget * random.uniform(0.72, 1.18))),
                    )
                    await self._content_story_execute(
                        "advance",
                        output_char_limit=output_limit,
                    )
                    return
                _raise_story_write_fence(state)
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("advance_creative_projects")
            if result is not None:
                return
        return await super()._maybe_advance_creative_projects()

    async def _maybe_start_creative_project(self, *, idle_checked: bool = False) -> bool:
        if not getattr(self, "_content_companion_delegating", False):
            claimed, started = await self._content_story_maybe_start_current(
                idle_checked=idle_checked,
            )
            if claimed:
                return started
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("maybe_start_creative_project", idle_checked=idle_checked)
            if result is not None:
                return bool(result)
        return bool(await super()._maybe_start_creative_project(idle_checked=idle_checked))

    async def _generate_creative_project(self, source: dict[str, str]) -> Any:
        if not getattr(self, "_content_companion_delegating", False):
            claimed, result = await self._content_story_execute(
                "generate_project",
                author_prompt=_single_line(source.get("text"), 220),
                style=_single_line(
                    runtime_persona_setting(self, "default_style", ""),
                    80,
                ),
            )
            if claimed:
                project_result = result.get("project") if isinstance(result, dict) else None
                return dict(project_result) if isinstance(project_result, dict) else None
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("generate_creative_project", source)
            if result is not None:
                return result
        return await super()._generate_creative_project(source)

    async def _generate_creative_chunk(self, project: dict[str, Any], budget: int) -> str:
        if not getattr(self, "_content_companion_delegating", False):
            try:
                output_limit = max(60, min(1200, int(budget)))
            except (TypeError, ValueError):
                output_limit = 60
            claimed, result = await self._content_story_execute(
                "generate_chunk",
                work_id=_single_line(project.get("id"), 80),
                output_char_limit=output_limit,
            )
            if claimed:
                chunk_result = result.get("chunk") if isinstance(result, dict) else None
                return chunk_result if type(chunk_result) is str else ""
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("generate_creative_chunk", project, budget)
            if result is not None:
                return str(result)
        return await super()._generate_creative_chunk(project, budget)

    async def _review_creative_chunk(self, *args: Any, **kwargs: Any) -> Any:
        if not getattr(self, "_content_companion_delegating", False):
            project = args[0] if args and isinstance(args[0], dict) else {}
            outline = str(args[2] if len(args) > 2 else kwargs.get("outline", ""))[:300]
            excerpt = str(args[3] if len(args) > 3 else kwargs.get("chunk_text", ""))[:600]
            claimed, result = await self._content_story_execute(
                "review_chunk",
                work_id=_single_line(project.get("id"), 80),
                outline=outline,
                recent_excerpt=excerpt,
                context_char_limit=900,
            )
            if claimed:
                review_result = result.get("review") if isinstance(result, dict) else None
                return dict(review_result) if isinstance(review_result, dict) else {}
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("review_creative_chunk", *args, **kwargs)
            if result is not None:
                return result
        return await super()._review_creative_chunk(*args, **kwargs)

    async def _apply_creative_manual_edit(self, *args: Any, **kwargs: Any) -> Any:
        if not getattr(self, "_content_companion_delegating", False):
            project_id = args[0] if args else kwargs.get("project_id", "")
            edit_type = args[1] if len(args) > 1 else kwargs.get("edit_type", "")
            edit_content = args[2] if len(args) > 2 else kwargs.get("edit_content", "")
            edit_title = args[3] if len(args) > 3 else kwargs.get("edit_title", "")
            part_index = args[4] if len(args) > 4 else kwargs.get("part_index", -1)
            if part_index is None:
                part_index = -1
            edit_text = str(edit_content or "")
            if len(edit_text) > 900:
                return {
                    "success": False,
                    "error": "story_edit_content_too_large",
                }
            claimed, result = await self._content_story_execute(
                "manual_edit",
                work_id=_single_line(project_id, 80),
                edit_type=_single_line(edit_type, 24),
                edit_title=_single_line(edit_title, 60),
                recent_excerpt=edit_text,
                context_char_limit=900,
                part_index=part_index,
            )
            if claimed:
                edit_result = result.get("result") if isinstance(result, dict) else None
                return dict(edit_result) if isinstance(edit_result, dict) else {}
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("apply_creative_manual_edit", *args, **kwargs)
            if result is not None:
                return result
        return await super()._apply_creative_manual_edit(*args, **kwargs)

    async def _rebuild_creative_memory_from_project(self, project_id: str) -> Any:
        if not getattr(self, "_content_companion_delegating", False):
            claimed, result = await self._content_story_execute(
                "rebuild_memory",
                work_id=_single_line(project_id, 80),
            )
            if claimed:
                rebuild_result = result.get("result") if isinstance(result, dict) else None
                return dict(rebuild_result) if isinstance(rebuild_result, dict) else {}
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("rebuild_creative_memory", project_id)
            if result is not None:
                return result
        return await super()._rebuild_creative_memory_from_project(project_id)

    async def _maybe_generate_creative_cover(self, project_id: str, *, force: bool = False) -> Any:
        state = _content_companion_bridge_host.story_authority_controller().authority_state()
        if state == "committed":
            # No managed current-contract cover operation exists yet.  After
            # handoff, absence is safer than re-entering the former owner.
            return None
        if state not in {"created", "open"}:
            _raise_story_write_fence(state)
        if self._content_companion_available():
            result = await self._content_companion_call("maybe_generate_creative_cover", project_id, force=force)
            if result is not None:
                return result
        return await super()._maybe_generate_creative_cover(project_id, force=force)
