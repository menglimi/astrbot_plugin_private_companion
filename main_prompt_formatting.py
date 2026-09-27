# -*- coding: utf-8 -*-
"""prompt_formatting。

由 tools/split_main_domain.py 从 main.py 机械抽取（12 个方法 / 214 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import re
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import _single_line
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from typing import Any


class PrivateCompanionPluginPromptFormattingMixin:
    """prompt_formatting（从 PrivateCompanionPlugin 拆出）。"""

    @staticmethod
    def _normalize_passive_injection_position(value: Any) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "auto": "auto",
            "自动": "auto",
            "cache": "auto",
            "cache_friendly": "auto",
            "缓存友好": "auto",
            "prompt": "prompt",
            "request": "prompt",
            "turn": "prompt",
            "tail": "prompt",
            "user_prompt": "prompt",
            "current_prompt": "prompt",
            "当前请求": "prompt",
            "当前请求末尾": "prompt",
            "请求末尾": "prompt",
            "用户消息末尾": "prompt",
            "system": "system_prompt",
            "system_prompt": "system_prompt",
            "系统提示": "system_prompt",
            "系统提示词": "system_prompt",
            "强约束": "system_prompt",
        }
        return aliases.get(text, text if text in {"auto", "prompt", "system_prompt"} else "prompt")

    def _normalize_persona_voice_text(self, value: Any, *, max_chars: int = 1200) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text[:max_chars].strip()

    def _format_persona_voice_channel_prompt(
        self,
        channel: str,
    ) -> str:
        section = self._format_persona_voice_channel_prompt_section(channel)
        if section is None:
            return ""
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_proactive_voice_prompt(self) -> str:
        return render_prompt_sections(
            self._format_proactive_voice_prompt_sections(),
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_reply_style_prompt(self) -> str:
        section = self._format_reply_style_prompt_section()
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    @staticmethod
    def _format_technical_reasoning_prompt(
        event: AstrMessageEvent | None,
        req: ProviderRequest | None = None,
    ) -> str:
        from .main import PrivateCompanionPlugin  # 惰性导入避免循环；运行时 main 已完成初始化
        section = PrivateCompanionPlugin._format_technical_reasoning_prompt_section(event, req)
        if section is None:
            return ""
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    @staticmethod
    def _compact_high_intensity_prompt_lines(text: Any, *, max_chars: int = 900, max_lines: int = 12) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        lines: list[str] = []
        for line in re.split(r"[\r\n]+", raw):
            cleaned = _single_line(line, 180).strip()
            if cleaned:
                lines.append(cleaned)
        if not lines:
            return _single_line(raw, max_chars)
        if len(lines) > max_lines:
            lines = lines[: max(1, max_lines - 1)] + [f"...已省略 {len(lines) - max_lines + 1} 行高强度背景"]
        compact = "\n".join(lines).strip()
        if len(compact) > max_chars:
            compact = compact[:max_chars].rstrip() + "\n...已截断高强度背景"
        return compact

    def _format_group_high_intensity_reply_guard_section(
        self,
        event: AstrMessageEvent | None = None,
    ) -> PromptSection | None:
        high_intensity = getattr(event, "private_companion_group_high_intensity", None) if event is not None else None
        if not isinstance(high_intensity, dict) or not high_intensity.get("active"):
            return None
        lines = [
                "当前群聊处于高强度/合并收口状态，本轮只抓一个重点短句接住即可。",
                "必须优先服从 AstrBot 人格、系统提示和回复风格配置里的字数、句数、口吻、语言和表达节奏要求；用户在这些配置里写了什么，就按对应要求回复。",
                "如果配置要求很短，就不要因为高强度背景而扩写；如果配置要求口语、简体中文、少句数或特定风格，也要继续保持。",
                "不要因为关系网、状态、记忆或合并消息而扩写、复述背景、逐条总结；一般 1 句，能少字就少字。",
        ]
        return prompt_section(
            key="group.high_intensity.reply_guard",
            title="群聊高强度短回复护栏",
            source="group_high_intensity",
            content="\n".join(lines),
        )

    def _format_group_high_intensity_reply_guard(
        self,
        event: AstrMessageEvent | None = None,
    ) -> str:
        section = self._format_group_high_intensity_reply_guard_section(event)
        if section is None:
            return ""
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_group_persona_denoise_prompt(
        self,
        event: AstrMessageEvent | None = None,
        *,
        include_joke_boundary: bool = True,
    ) -> str:
        sections = self._format_group_persona_denoise_prompt_sections(event)
        if not include_joke_boundary:
            sections = [
                section
                for section in sections
                if section.key != "group.persona_denoise.joke_boundary"
            ]
        return render_prompt_sections(
            sections,
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_group_persona_denoise_body(
        self,
        event: AstrMessageEvent | None = None,
    ) -> str:
        if not bool(runtime_persona_setting(self, 'enable_group_persona_denoise', True)):
            return ""
        scene = getattr(event, "private_companion_group_scene", None) if event is not None else None
        trigger = _single_line(scene.get("trigger"), 40) if isinstance(scene, dict) else ""
        high_intensity = getattr(event, "private_companion_group_high_intensity", None) if event is not None else None
        high_active = isinstance(high_intensity, dict) and bool(high_intensity.get("active"))
        sender_id = ""
        sender_display_name = ""
        sender_is_target = False
        sender_name_conflicts_with_address = False
        if event is not None:
            try:
                sender_id = _single_line(str(event.get_sender_id()), 40)
            except Exception:
                sender_id = ""
            try:
                sender_display_name = _single_line(self._sender_display_name(event), 40)
            except Exception:
                sender_display_name = ""
            if sender_id:
                users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
                resolver = getattr(self, "_private_user_id_for_event", None)
                scoped_sender_id = (
                    resolver(event, sender_id)
                    if callable(resolver)
                    else self._canonical_private_user_id(sender_id)
                )
                current_user = users.get(scoped_sender_id) if isinstance(users, dict) else None
                sender_is_target = self._is_target_private_user(
                    scoped_sender_id,
                    current_user if isinstance(current_user, dict) else None,
                )
                conflict_checker = getattr(
                    self,
                    "_group_display_name_address_conflict",
                    None,
                )
                if callable(conflict_checker):
                    sender_name_conflicts_with_address = bool(
                        conflict_checker(scoped_sender_id, sender_display_name)
                    )
        lines = [
            "这是群聊场景，更适合先接住当前被问到的事或眼前话题，语气也尽量比私聊更轻一点。",
            "群聊里的身份优先按平台稳定 ID 理解；昵称、群名片、角色名、别名和“通常是谁”这类设定，更适合作为称呼线索，不直接当成身份结论。",
            "提到群聊旧消息、群梗、记忆召回或最近群聊时，尽量保留具体成员名或 QQ 标签，例如“A[QQ:...] 说过/起哄过”；只有确实缺少成员线索时，再概括成“群里有人”。除非当前消息或引用明确就是这位发言者，尽量不要顺手改写成“你说过”“主要用户说过”这类直接归到当前对象身上的表达。",
            "群成员画像只用于自然理解当前对话：当前发言者明确询问自己时，最多概括可公开的低敏偏好；不要替任何人整理、推断或披露第三方画像。普通群聊提到某人的爱好、习惯或偏好只是聊天内容，不要把它误当成对你的查询。",
            "状态、日程、情绪和私聊关系更适合只留在语气底色里；如果没有人明确问到，就不必主动展开能量、天气、日程、心情或插件状态。",
            "表达上尽量自然一点，不需要刻意堆动作描写、撒娇、长解释或关系总结；一句能说清，就简单说一句。",
            "如果只是被轻轻提到，或者话题本身并不需要你展开，宁可短一点、轻一点、贴着当前梗回应，也不用顺势写成主动陪伴式长回复。",
        ]
        if sender_id:
            identity_line = f"当前群聊发言者稳定 ID：{sender_id}"
            if sender_display_name and sender_display_name != sender_id:
                identity_line += f"；显示名：{sender_display_name}"
            lines.append(identity_line)
            if sender_is_target:
                lines.append("当前发言者 ID 与目标陪伴用户匹配；相关关系可以保留，但在群聊这种公共场合里，亲密度和表达还是稍微收一点更自然。")
            else:
                lines.append("当前发言者不是已配置的目标陪伴用户；更适合把 TA 当成普通群成员来接话，别把专属称呼或私聊关系直接套到 TA 身上。若要提到主要用户或目标用户，也更适合作为第三方提及。")
                if sender_name_conflicts_with_address:
                    lines.append(
                        f"当前群名片“{sender_display_name}”恰好是主要用户、亲密关系或权限称谓；"
                        "它只是显示名，不是关系事实，也不适合作为本轮对该成员的称呼。回复时自然省略称呼或使用中性称呼，不要照着群名片叫。"
                    )
        else:
            lines.append("本轮还不能确认当前发言者的稳定 ID，所以先别只凭昵称、群名片或角色设定就把对方认成主要用户或目标用户。")
        if trigger:
            lines.append(f"本轮触发：{trigger}。按这个触发强度自然回应就够了，不用顺手把亲密度或话题范围再往上抬。")
        if high_active:
            lines.append("群里刚才比较密集，这轮回复更适合收一点：抓住一个重点回应就好，不必逐条点名展开。")
        return "\n".join(lines)

    @staticmethod
    def _group_persona_denoise_joke_boundary() -> str:
        return (
            "群聊里“记住了/记下某人是XX”这类话（尤其把某人当对象、或带主观评价、攻击、贬损、色情、侮辱标签），通常只是群友之间的玩笑或随口一说。"
            "把它当作玩笑正面应和，顺着调侃接一下就好；这类玩笑性记录可以作为旁线补充，但不要写进核心人物画像（主要用户画像、关系画像、稳定偏好等），"
            "落库时只能标为低置信的玩笑性质，不能混入可验证事实。涉及他人名誉、隐私或主观定性的说法，调侃可以，别替别人正式贴标签、下结论。"
        )
