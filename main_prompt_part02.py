# -*- coding: utf-8 -*-
"""PrivateCompanionPluginPromptPart02Mixin。

由 tools/split_mixin_domain.py 从 main_prompt.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 340 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginPromptMixin）。
"""
from __future__ import annotations

from .main_prompt_shared import logger
from .main_prompt_shared import Any
from .main_prompt_shared import AstrMessageEvent
from .main_prompt_shared import CollectedPromptContext
from .main_prompt_shared import DELIVERY_GROUP_MARKER_METADATA_KEY
from .main_prompt_shared import Iterable
from .main_prompt_shared import PLACEMENT_DYNAMIC_SYSTEM
from .main_prompt_shared import PLACEMENT_STABLE_SYSTEM
from .main_prompt_shared import PLACEMENT_TURN_TAIL
from .main_prompt_shared import PromptRenderMode
from .main_prompt_shared import PromptSection
from .main_prompt_shared import ProviderRequest
from .main_prompt_shared import _safe_float
from .main_prompt_shared import _safe_int
from .main_prompt_shared import _single_line
from .main_prompt_shared import asyncio
from .main_prompt_shared import get_conversation_injection_plan
from .main_prompt_shared import prompt_section
from .main_prompt_shared import render_prompt_sections
from .main_prompt_shared import runtime_persona_setting
from .main_prompt_shared import sanitize_private_request_group_artifacts
from .main_prompt_shared import time



class PrivateCompanionPluginPromptPart02Mixin:
    """PrivateCompanionPluginPromptPart02Mixin（从 PrivateCompanionPluginPromptMixin 拆出）。"""


    def _sanitize_private_companion_prompt_artifacts_in_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        contexts = getattr(req, "contexts", None)
        changed = sanitize_private_request_group_artifacts(event, req)

        def clean_content(value: Any) -> tuple[Any, bool]:
            if isinstance(value, str):
                cleaned = self._strip_private_companion_prompt_artifacts(value)
                return cleaned, cleaned != value
            if isinstance(value, dict):
                updated = dict(value)
                dirty = False
                for key in ("text", "content", "value"):
                    if key in updated and isinstance(updated.get(key), str):
                        cleaned = self._strip_private_companion_prompt_artifacts(updated.get(key))
                        if cleaned != updated.get(key):
                            updated[key] = cleaned
                            dirty = True
                return updated, dirty
            if isinstance(value, list):
                new_items = []
                dirty = False
                for item in value:
                    cleaned_item, item_dirty = clean_content(item)
                    new_items.append(cleaned_item)
                    dirty = dirty or item_dirty
                return new_items, dirty
            return value, False

        if isinstance(contexts, list) and contexts:
            sanitized: list[Any] = []
            for item in contexts:
                if isinstance(item, dict):
                    updated = dict(item)
                    cleaned_content, dirty = clean_content(updated.get("content"))
                    if dirty:
                        updated["content"] = cleaned_content
                        changed += 1
                    sanitized.append(updated)
                else:
                    cleaned_item, dirty = clean_content(item)
                    if dirty:
                        changed += 1
                    sanitized.append(cleaned_item)
            try:
                req.contexts = sanitized
            except Exception:
                return
        if changed <= 0:
            return
        logger.info(
            "已清理请求中的跨轮/跨作用域动态注入残留: session=%s surfaces_changed=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            changed,
        )

    async def _record_request_prompt_fragment(
        self,
        event: AstrMessageEvent,
        *,
        title: str,
        key: str,
        text: str,
        source: str = "",
        mode: str = "",
        priority: int = 50,
        metadata: dict[str, Any] | None = None,
        section_manifest: list[PromptSection] | tuple[PromptSection, ...] | None = None,
    ) -> None:
        recorder = getattr(self, "_record_prompt_injection_snapshot", None)
        content = str(text or "").strip()
        if not callable(recorder) or not content:
            return
        await recorder(
            kind="request",
            session=_single_line(getattr(event, "unified_msg_origin", ""), 160) or self._event_scope_key(event),
            title=title,
            text=content,
            mode=mode,
            trace_id=self._prompt_injection_trace_id_for_event(event),
            message_preview=self._prompt_injection_message_preview_for_event(event),
            sender_label=self._prompt_injection_sender_label_for_event(event),
            section_manifest=(
                section_manifest
                if section_manifest is not None
                else [
                    {
                        "key": key,
                        "title": title,
                        "source": source,
                        "priority": priority,
                        "content": content,
                        "chars": len(content),
                    }
                ]
            ),
            metadata={
                **(metadata or {}),
                "会话": _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown",
                "发送者": _single_line(self._event_sender_id(event), 80),
            },
        )

    async def _resolve_prompt_context_collector(
        self,
        spec: dict[str, Any],
    ) -> CollectedPromptContext:
        key = _single_line(spec.get("key"), 80)
        source = _single_line(spec.get("source"), 80)
        priority = _safe_int(spec.get("priority"), 100, 0)
        timeout = max(0.05, _safe_float(spec.get("timeout"), 0.8, 0.05))
        started = time.time()
        metadata = dict(spec.get("metadata") if isinstance(spec.get("metadata"), dict) else {})
        metadata.setdefault("来源", source or key)
        metadata.setdefault("超时秒数", round(timeout, 2))
        try:
            func = spec.get("func")
            if not callable(func):
                raise TypeError("collector is not callable")
            result = func()
            if asyncio.iscoroutine(result):
                result = await asyncio.wait_for(result, timeout=timeout)
            if result is None:
                sections: tuple[PromptSection, ...] = ()
            elif isinstance(result, PromptSection):
                sections = (result,)
            elif isinstance(result, (list, tuple)) and all(
                isinstance(item, PromptSection) for item in result
            ):
                sections = tuple(result)
            else:
                raise TypeError(
                    f"prompt collector {key or source or 'unknown'} must return "
                    "PromptSection, a PromptSection sequence, or None"
                )
            content = "\n\n".join(
                render_prompt_sections(
                    [item],
                    mode=PromptRenderMode.BODY_ONLY,
                )
                for item in sections
            ).strip()
            elapsed_ms = int((time.time() - started) * 1000)
            metadata.update(
                {
                    "耗时ms": elapsed_ms,
                    "状态": "命中" if content else "空",
                    "字符数": len(content),
                }
            )
            return CollectedPromptContext(
                key=key,
                priority=priority,
                sections=sections,
                metadata=metadata,
                status="hit" if content else "empty",
            )
        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - started) * 1000)
            metadata.update({"耗时ms": elapsed_ms, "状态": "超时"})
            logger.warning(
                "请求上下文收集超时: key=%s source=%s timeout=%.2fs",
                key or "-",
                source or "-",
                timeout,
            )
            return CollectedPromptContext(
                key=key,
                priority=priority,
                sections=(),
                metadata=metadata,
                status="timeout",
            )
        except Exception as exc:
            elapsed_ms = int((time.time() - started) * 1000)
            metadata.update({"耗时ms": elapsed_ms, "状态": "失败", "错误": _single_line(exc, 120)})
            logger.debug(
                "请求上下文收集失败: key=%s source=%s error=%s",
                key or "-",
                source or "-",
                _single_line(exc, 120),
            )
            return CollectedPromptContext(
                key=key,
                priority=priority,
                sections=(),
                metadata=metadata,
                status="error",
            )

    async def _format_passive_environment_prompt_section(
        self,
        event: AstrMessageEvent,
        *,
        lightweight: bool = False,
    ) -> PromptSection:
        if not lightweight:
            return await self._format_environment_perception_prompt_section(event)
        lines: list[str] = []
        if self._feature_enabled_or_temp_unlocked("enable_environment_perception"):
            current = self._environment_now()
            lines = [
                "这是当前消息的轻量背景边界，主要影响时间感、平台语境和回复节奏；如果用户刚好在问时间、平台或环境感受，可以按需要自然带出，没问到时就只当背景参考。",
                f"时间：{current.strftime('%Y-%m-%d %H:%M')}",
                "时间锚点必须以这一行真实时间为准；不要把未来日程、睡眠段、旧记忆或上次对话里的时间说成当前时间。",
            ]
            current_minutes = current.hour * 60 + current.minute
            if not (22 * 60 <= current_minutes or current_minutes <= 90):
                lines.append(
                    "当前没有进入深夜时段；即使人格、作息或旧上下文提到“可能很晚”“晚上睡觉”，也不能主动说快十一点、困不困、该睡了或晚安。"
                )
            platform = await self._format_platform_perception(event)
            if platform:
                lines.append(f"会话：{platform}")
        return prompt_section(
            key="environment.lightweight",
            title="轻量环境感知",
            source="environment",
            content="\n".join(lines),
        )

    async def _append_capability_boundary_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_capability_boundary_v1 -->"
        current_prompt = req.system_prompt or ""
        if marker in current_prompt:
            return
        boundary = (
            "你不能假装自己能影响现实、网络、游戏房间、他人设备或用户身体动作。"
            "没有可用工具且没有实际执行结果时,不要承诺“我这就拉你/我帮你操作/我已经处理/我去修/我给你弄好”。"
            "遇到拉人、开房间、修网、重启、登录、下载、现实代办等请求,只能自然说明自己做不到实际操作,可以提醒、陪用户确认、建议对方找能操作的人,或在确有工具时调用工具后再描述结果。"
        )
        boundary_sections = [
            prompt_section(
                key="guard.capability_boundary",
                title="能力边界",
                source="guard",
                content=boundary,
            )
        ]
        platform_boundary_getter = getattr(
            self,
            "_platform_capability_prompt_section",
            None,
        )
        if callable(platform_boundary_getter):
            platform_boundary = platform_boundary_getter(event)
            if platform_boundary is not None and not isinstance(
                platform_boundary,
                PromptSection,
            ):
                raise TypeError("platform capability prompt must return PromptSection or None")
            if isinstance(platform_boundary, PromptSection) and render_prompt_sections(
                [platform_boundary],
                mode=PromptRenderMode.BODY_ONLY,
            ).strip():
                boundary_sections.append(platform_boundary)
        boundary = render_prompt_sections(boundary_sections)
        for index, section in enumerate(boundary_sections):
            self._materialize_conversation_system_block(
                req,
                section=section,
                marker=marker if index == 0 else "",
                priority=30,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
                metadata={DELIVERY_GROUP_MARKER_METADATA_KEY: marker},
            )
        await self._record_request_prompt_fragment(
            event,
            title="能力边界注入",
            key="capability.boundary",
            text=boundary,
            source="guard",
            mode="group",
        )

    async def _append_media_delivery_truth_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        sections = self._media_delivery_truth_prompt_sections()
        media_truth_marker = "<!-- private_companion_media_delivery_truth_v1 -->"
        if not sections or self._request_has_managed_prompt_marker(req, media_truth_marker):
            return
        plan = get_conversation_injection_plan(req)
        for index, section in enumerate(sections):
            plan.materialize_system_block(
                req,
                section=section,
                marker=media_truth_marker if index == 0 else "",
                priority=30 + index,
                placement=PLACEMENT_STABLE_SYSTEM,
            )
        media_truth_instruction = render_prompt_sections(sections)
        await self._record_request_prompt_fragment(
            event,
            title="媒体发送真实性约束",
            key="tools.media_delivery_truth",
            text=media_truth_instruction,
            source="tools",
            mode="always",
            metadata={"注入位置": "system_prompt"},
            section_manifest=sections,
        )

    def _place_conversation_prompt_sections(
        self,
        req: ProviderRequest,
        marker: str,
        sections: Iterable[PromptSection],
        *,
        priority: int,
    ) -> tuple[str, str, tuple[PromptSection, ...]]:
        authored = tuple(
            section
            for section in sections
            if isinstance(section, PromptSection)
            and render_prompt_sections(
                [section],
                mode=PromptRenderMode.BODY_ONLY,
            ).strip()
        )
        if not authored:
            return "none", "", ()
        visible = render_prompt_sections(authored)
        position = self._normalize_passive_injection_position(
            runtime_persona_setting(self, "passive_injection_position", "prompt")
        )
        marker = _single_line(marker, 120) or "<!-- private_companion_turn_fragment -->"
        plan = get_conversation_injection_plan(req)
        if plan is None:
            raise RuntimeError("conversation injection plan is unavailable")
        use_system_prompt = position == "system_prompt"
        if not plan.contains_marker(marker):
            for index, section in enumerate(authored):
                plan.add(
                    section=section,
                    marker=marker if index == 0 else "",
                    priority=int(priority),
                    placement=(
                        PLACEMENT_DYNAMIC_SYSTEM
                        if use_system_prompt
                        else PLACEMENT_TURN_TAIL
                    ),
                    materialized=False,
                    metadata={DELIVERY_GROUP_MARKER_METADATA_KEY: marker},
                )
        setattr(req, "_private_companion_turn_prompt_fragments", plan.turn_fragments())
        rendered_placement = plan.render_into(req, prefer_extra_user_content=True)
        placement = "system_prompt" if use_system_prompt else rendered_placement
        return placement, visible, authored
