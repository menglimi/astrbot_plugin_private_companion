# -*- coding: utf-8 -*-
"""PrivateCompanionPluginPromptPart01Mixin。

由 tools/split_mixin_domain.py 从 main_prompt.py 机械抽取（17 个方法 + 0 个模块级名字 + 0 个类级赋值 / 483 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginPromptMixin）。
"""
from __future__ import annotations

from .main_prompt_shared import _default_segmenting_prompt_for, logger
from .main_prompt_shared import Any
from .main_prompt_shared import AstrMessageEvent
from .main_prompt_shared import LLM_SEGMENT_MARKER
from .main_prompt_shared import PLACEMENT_DYNAMIC_SYSTEM
from .main_prompt_shared import PLACEMENT_TURN_TAIL
from .main_prompt_shared import PromptRenderMode
from .main_prompt_shared import PromptSection
from .main_prompt_shared import ProviderRequest
from .main_prompt_shared import _multi_persona_event_context
from .main_prompt_shared import _single_line
from .main_prompt_shared import get_conversation_injection_plan
from .main_prompt_shared import prompt_cdata
from .main_prompt_shared import prompt_section
from .main_prompt_shared import re
from .main_prompt_shared import render_prompt_sections
from .main_prompt_shared import runtime_persona_setting
from .main_prompt_shared import sanitize_llm_segment_control_tokens
from .main_prompt_shared import filter



class PrivateCompanionPluginPromptPart01Mixin:
    """PrivateCompanionPluginPromptPart01Mixin（从 PrivateCompanionPluginPromptMixin 拆出）。"""


    def _format_body_monitor_health_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
    ) -> PromptSection | None:
        integration = getattr(self, "_body_monitor_integration", None)
        if integration is None:
            return None
        builder = getattr(integration, "format_health_prompt_section", None)
        return builder(user, reason=reason) if callable(builder) else None

    def _llm_controlled_segmenting_allowed(self, event: AstrMessageEvent | None = None) -> bool:
        """Return whether the current conversation may use LLM boundaries."""
        if not bool(runtime_persona_setting(self, "enable_segmented_proactive_reply", False)):
            return False
        if not bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False)):
            return False
        scope = self._segmented_setting(
            "scope",
            event=event,
            default="proactive_only",
        )
        external_proactive = bool(
            event is not None
            and (
                bool(getattr(event, "private_companion_proactive_framework", False))
                or bool(getattr(event, "_private_companion_external_proactive_source", ""))
            )
        )
        if str(scope or "proactive_only").strip().lower() != "all_llm" and not external_proactive:
            return False
        try:
            if event is not None and not bool(self._segmented_scope_allows_event(event)):
                return False
            if event is not None and not bool(self._segmented_platform_allows(event=event)):
                return False
            return True
        except Exception:
            return False

    def _llm_controlled_segmenting_prompt(self) -> str:
        """Resolve the active persona's user-facing segmentation instruction."""
        custom = str(
            runtime_persona_setting(self, "llm_controlled_segmenting_prompt", "")
            or ""
        ).strip()[:4000]
        template = custom or _default_segmenting_prompt_for(self)
        return re.sub(
            r"\{\{\s*split_marker\s*\}\}",
            lambda _match: LLM_SEGMENT_MARKER,
            template,
            flags=re.IGNORECASE,
        )

    def _llm_controlled_segmenting_prompt_section(self) -> PromptSection:
        return prompt_section(
            key="reply.segmentation",
            title="回复分段控制",
            source="segmented_reply",
            content=prompt_cdata(self._llm_controlled_segmenting_prompt()),
        )

    @filter.on_llm_request(priority=-253000)
    @_multi_persona_event_context
    async def inject_llm_controlled_segmenting_instruction(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ) -> None:
        """Tell only the main conversation model about the outbound marker."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        if not self._llm_controlled_segmenting_allowed(event):
            return
        marker = "<!-- private_companion_reply_segmentation_v1 -->"
        if self._request_has_managed_prompt_marker(req, marker):
            return
        section = self._llm_controlled_segmenting_prompt_section()
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            section,
            priority=90,
        ) else "system_prompt"
        if placement == "system_prompt":
            self._materialize_conversation_system_block(
                req,
                section=section,
                marker=marker,
                priority=90,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
            )

    async def _append_environment_perception_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_environment_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        environment_section = await self._format_environment_perception_prompt_section(event)
        environment_injection = str(environment_section.content or "")
        if environment_injection:
            placement = self._place_conversation_prompt_section(
                req,
                marker,
                environment_section,
                priority=30,
            )
            await self._record_request_prompt_fragment(
                event,
                title="请求级环境感知注入",
                key="environment.request",
                text=environment_injection,
                source="environment",
                metadata={"注入位置": placement},
            )

    def _format_persona_voice_channel_prompt_section(
        self,
        channel: str,
    ) -> PromptSection | None:
        if not bool(runtime_persona_setting(self, 'enable_persona_voice_channels', True)):
            return None
        channel = str(channel or "").strip().lower()
        specs = {
            "conversation": (
                "对话风格",
                "persona_conversation_voice_prompt",
                "只用于私聊/群聊里真正说出口的聊天回复。不要把创作腔、日程计划或内心分析写进外发消息；用户要求详细说明时可优先保证信息完整。",
            ),
            "creative": (
                "创作风格",
                "persona_creative_voice_prompt",
                "只用于日记、QQ 空间、私下创作、文案和公开动态。允许比聊天更完整,但仍应像角色本人写的,避免模型作文、升华总结和营销文案腔。",
            ),
            "planning": (
                "计划风格",
                "persona_planning_voice_prompt",
                "只影响日程、计划、候选排序和行动倾向。这里描述角色会怎样安排自己、被什么驱动、什么时候收住,不是最终聊天台词。",
            ),
            "inner": (
                "内心活动风格",
                "persona_inner_voice_prompt",
                "只用于内部动机、念头、犹豫和状态余波。它默认不可直接外发,不能泄露系统、插件、模型或自我分析过程。",
            ),
            "proactive": (
                "主动开口风格",
                "persona_proactive_voice_prompt",
                "只用于把主动动机改写成最终私聊/群聊开口。优先具体由头、低压力、短句和可接话落点；不要写成回复空气、任务汇报或询问是否继续。",
            ),
        }
        label, attr, note = specs.get(channel, ("表达风格", f"persona_{channel}_voice_prompt", "只在对应链路使用。"))
        text = self._normalize_persona_voice_text(
            runtime_persona_setting(self, attr, ""),
            max_chars=1400,
        )
        if not text:
            return None
        body = f"{text}\n使用边界：{note}"
        return prompt_section(
            key=f"persona.voice.{channel or 'default'}",
            title=f"人格标准化：{label}",
            source="persona_voice",
            content=body,
        )

    def _format_proactive_voice_prompt_sections(self) -> list[PromptSection]:
        sections: list[PromptSection] = []
        base = self._normalize_persona_voice_text(runtime_persona_setting(self, 'reply_style_prompt', ""), max_chars=900)
        if base:
            sections.append(
                prompt_section(
                    key="proactive.base_voice",
                    title="主动消息基础表达约束",
                    source="persona_voice",
                    content=(
                        f"{base}\n"
                        "这里只保留句数、口语化和简洁度等通用约束；不要把普通被动接话方式直接当成主动开口。"
                    ),
                )
            )
        proactive = self._format_persona_voice_channel_prompt_section("proactive")
        if proactive is not None:
            sections.append(proactive)
        conversation = self._format_persona_voice_channel_prompt_section("conversation")
        if conversation is not None and proactive is None:
            sections.append(
                prompt_section(
                    key=conversation.key,
                    title=conversation.title,
                    source=conversation.source,
                    content=(
                        f"{conversation.content}\n"
                        "补充边界：当前没有单独配置主动开口风格,因此只把对话风格作为轻量回退；仍必须围绕主动由头自然开口。"
                    ),
                    metadata=conversation.metadata,
                )
            )
        return sections

    def _format_reply_style_prompt_section(self) -> PromptSection:
        text = str(runtime_persona_setting(self, 'reply_style_prompt', "") or "").strip()
        persona_voice_section = self._format_persona_voice_channel_prompt_section("conversation")
        persona_voice = ""
        if persona_voice_section is not None:
            persona_voice = render_prompt_sections(
                [persona_voice_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
        content = ""
        if text or persona_voice:
            text = self._normalize_persona_voice_text(text)
            parts: list[str] = []
            if text:
                parts.append(text)
            if persona_voice:
                parts.append(persona_voice)
            content = (
                "\n\n".join(parts)
                + "\n这些规则用于普通聊天的表达节奏；如果当前问题确实需要排障、教程、代码说明、复杂解释或用户明确要求详细说明，可以优先保证信息完整。"
                + "\n无论工具或模型返回什么内容，外发正文都不要照抄英文报错、内容策略提示、政策链接或内部诊断；遇到这类结果时，用当前人格的一句简短中文说明，再自然收住或邀请用户换一种说法。"
            )
        return prompt_section(
            key="reply.style",
            title="回复风格约束",
            source="reply_style",
            content=content,
        )

    @staticmethod
    def _format_technical_reasoning_prompt_section(
        event: AstrMessageEvent | None,
        req: ProviderRequest | None = None,
    ) -> PromptSection | None:
        text = "\n".join(
            part
            for part in (
                str(getattr(event, "message_str", "") or "").strip(),
                str(getattr(req, "prompt", "") or "").strip(),
            )
            if part
        )
        compact = re.sub(r"\s+", "", text).lower()
        if not compact:
            return None
        technical_markers = (
            "代码", "源码", "脚本", "python", "sleep(", "报错", "日志", "执行结果",
            "计算", "公式", "换算", "单位", "耗时", "延迟", "超时", "秒", "分钟", "小时",
        )
        if not any(marker in compact for marker in technical_markers):
            return None
        return prompt_section(
            key="reply.technical_accuracy",
            title="技术解释准确性",
            source="reply_style",
            content=(
                "解释代码、公式、日志耗时或单位换算时，先逐项读取用户给出的原表达式和原始数值，写清每个量的单位；"
                "先统一换算到同一种基本单位，再换算成用户需要的展示单位，并用一次反向换算复核。"
                "严格区分配置/代码要求的时长、程序实际运行耗时、日志记录值和界面格式化后的显示值，不要把它们当成同一个量。\n"
                "不得引入源码、日志或用户材料中没有出现的运算、常数、倍率、对数或所谓解释器规则来凑结果；"
                "尤其不能凭空加入 ln、log、指数或除法。如果结果与原表达式不一致，明确指出缺少哪段真实代码或日志，不要虚构原因。\n"
                "例如 `time.sleep(10 * 60)` 的参数是 600 秒，也就是 10 分钟；除非真实代码另有运算，不能解释成 4.35 分钟。"
            ),
        )

    async def _append_reply_style_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        mode: str = "passive",
        priority: int = 12,
    ) -> None:
        sections = [self._format_reply_style_prompt_section()]
        technical_section = self._format_technical_reasoning_prompt_section(
            event,
            req,
        )
        if technical_section is not None:
            sections.append(technical_section)
        combined_prompt = render_prompt_sections(sections)
        if not combined_prompt:
            return
        marker = "<!-- private_companion_reply_style_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement, _, _ = self._place_conversation_prompt_sections(
            req,
            marker,
            sections,
            priority=priority,
        )
        await self._record_request_prompt_fragment(
            event,
            title="回复风格约束",
            key="reply.style",
            text=combined_prompt,
            source="reply_style",
            mode=mode,
            metadata={"注入位置": placement},
        )

    async def _append_group_high_intensity_reply_guard_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        guard_section = self._format_group_high_intensity_reply_guard_section(event)
        if guard_section is None:
            return
        guard_text = render_prompt_sections(
            [guard_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        marker = "<!-- private_companion_group_high_intensity_reply_guard_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = self._place_conversation_prompt_section(
            req,
            marker,
            guard_section,
            priority=11,
        )
        await self._record_request_prompt_fragment(
            event,
            title="群聊高强度短回复护栏",
            key="group.high_intensity.reply_guard",
            text=guard_text,
            source="group_high_intensity",
            mode="group",
            priority=11,
            metadata={"注入位置": placement},
        )

    def _place_conversation_prompt_section(
        self,
        req: ProviderRequest,
        marker: str,
        section: PromptSection,
        *,
        priority: int = 50,
        force_dynamic: bool = False,
    ) -> str:
        """Place one authored section and render it exactly once through the plan."""

        if not isinstance(section, PromptSection):
            raise TypeError("conversation prompt placement requires PromptSection")
        position = self._normalize_passive_injection_position(
            runtime_persona_setting(self, "passive_injection_position", "prompt")
        )
        marker = _single_line(marker, 120) or "<!-- private_companion_turn_fragment -->"
        plan = get_conversation_injection_plan(req)
        if plan is None:
            raise RuntimeError("conversation injection plan is unavailable")
        if not plan.contains_marker(marker):
            use_system_prompt = position == "system_prompt" and not force_dynamic
            plan.add(
                section=section,
                marker=marker,
                priority=int(priority),
                placement=(
                    PLACEMENT_DYNAMIC_SYSTEM
                    if use_system_prompt
                    else PLACEMENT_TURN_TAIL
                ),
                materialized=False,
            )
        setattr(req, "_private_companion_turn_prompt_fragments", plan.turn_fragments())
        plan.render_into(req, prefer_extra_user_content=True)
        if position == "system_prompt" and not force_dynamic:
            return "system_prompt"
        return _single_line(
            getattr(req, "_private_companion_turn_prompt_placement", "prompt"),
            40,
        ) or "prompt"

    def _append_turn_prompt_fragment_by_position(
        self,
        req: ProviderRequest,
        marker: str,
        section: PromptSection,
        *,
        priority: int = 50,
        force_dynamic: bool = False,
    ) -> bool:
        if not isinstance(section, PromptSection):
            raise TypeError("turn prompt fragment requires PromptSection")
        if (
            section.content is None
            or (isinstance(section.content, str) and not section.content.strip())
        ) and not section.children:
            return False
        try:
            placement = self._place_conversation_prompt_section(
                req,
                marker,
                section,
                priority=priority,
                force_dynamic=force_dynamic,
            )
            return placement not in {"none", "system_prompt"}
        except Exception as exc:
            logger.debug("指定位置 prompt 注入失败,回退 system_prompt: %s", _single_line(exc, 120))
            return False

    @staticmethod
    def _request_has_managed_prompt_marker(req: ProviderRequest, marker: str) -> bool:
        """Only trust markers placed by the plugin, never raw user prompt text."""
        marker_text = _single_line(marker, 120)
        if not marker_text:
            return False
        plan = get_conversation_injection_plan(req, create=False)
        if plan is not None and plan.contains_marker(marker_text):
            return True
        if marker_text in str(getattr(req, "system_prompt", "") or ""):
            return True
        fragments = getattr(req, "_private_companion_turn_prompt_fragments", None)
        if isinstance(fragments, list) and any(
            isinstance(item, dict) and item.get("marker") == marker_text
            for item in fragments
        ):
            return True
        extra_parts = getattr(req, "extra_user_content_parts", None)
        if not isinstance(extra_parts, list):
            return False
        for part in extra_parts:
            if not bool(getattr(part, "_private_companion_turn_fragments", False)):
                continue
            text = str(getattr(part, "text", "") or getattr(part, "content", "") or "")
            if marker_text in text:
                return True
        return False

    def _request_prompt_context_surface(self, req: ProviderRequest) -> str:
        parts = [str(getattr(req, "prompt", "") or ""), str(getattr(req, "system_prompt", "") or "")]
        extra_parts = getattr(req, "extra_user_content_parts", None)
        if isinstance(extra_parts, list):
            for part in extra_parts:
                if isinstance(part, dict):
                    parts.append(str(part.get("text") or part.get("content") or ""))
                else:
                    parts.append(str(getattr(part, "text", "") or getattr(part, "content", "") or ""))
        return "\n".join(item for item in parts if item)

    @staticmethod
    def _strip_private_companion_prompt_artifacts(text: Any) -> str:
        cleaned = sanitize_llm_segment_control_tokens(text)
        if not cleaned or "private_companion_" not in cleaned:
            return cleaned
        cleaned = re.sub(
            r"\n*\s*<!--\s*private_companion_turn_fragments_start\s*-->.*?<!--\s*private_companion_turn_fragments_end\s*-->\s*",
            "\n",
            cleaned,
            flags=re.DOTALL,
        )
        block_markers = (
            "state",
            "static",
            "reply_style",
            "environment",
            "reply_image_anchor",
            "atrelay_tools",
            "relation_lookup",
            "qzone_tools",
            "photo_generation_tool",
            "cross_user_memory",
            "group_persona_denoise",
            "group_high_intensity_reply_guard",
            "group_context",
            "recall_query",
            "self_timeline",
            "rest_backlog",
            "atrelay_target_summary",
            "worldbook_mentions",
            "non_target_private_guard",
            "capability_boundary",
            "forward_message",
            "group_injection_guard",
            "reply_chain",
            "reply_segmentation",
            "media_delivery_truth",
            "tool_protocol",
            "period_boundary",
        )
        marker_pattern = "|".join(re.escape(f"private_companion_{name}_v1") for name in block_markers)
        cleaned = re.sub(
            rf"\n*\s*<!--\s*(?:{marker_pattern})\s*-->.*?(?=\n\s*<!--\s*private_companion_[a-z0-9_]+_v1\s*-->|\Z)",
            "\n",
            cleaned,
            flags=re.DOTALL,
        )
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()
