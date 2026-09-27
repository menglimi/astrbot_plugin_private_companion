# -*- coding: utf-8 -*-
"""ProactiveMessagePromptContextPart02Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_prompt_context.py 机械抽取（22 个方法 + 0 个模块级名字 + 0 个类级赋值 / 453 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessagePromptContextMixin）。
"""
from __future__ import annotations

from .proactive_message_prompt_context_shared import _now_ts, logger
from .proactive_message_prompt_context_shared import Any
from .proactive_message_prompt_context_shared import PromptDocument
from .proactive_message_prompt_context_shared import PromptRenderMode
from .proactive_message_prompt_context_shared import PromptSection
from .proactive_message_prompt_context_shared import _PROACTIVE_DOCUMENT_RENDER
from .proactive_message_prompt_context_shared import _proactive_prompt_part
from .proactive_message_prompt_context_shared import _safe_float
from .proactive_message_prompt_context_shared import _single_line
from .proactive_message_prompt_context_shared import _split_address_terms
from .proactive_message_prompt_context_shared import prompt_document
from .proactive_message_prompt_context_shared import prompt_section
from .proactive_message_prompt_context_shared import re
from .proactive_message_prompt_context_shared import reaction_expression_high_frequency
from .proactive_message_prompt_context_shared import render_prompt_document
from .proactive_message_prompt_context_shared import render_prompt_sections
from .proactive_message_prompt_context_shared import runtime_persona_setting



class ProactiveMessagePromptContextPart02Mixin:
    """ProactiveMessagePromptContextPart02Mixin（从 ProactiveMessagePromptContextMixin 拆出）。"""


    def _proactive_recipient_allowed_names(self, user: dict[str, Any] | None, name: str = "") -> list[str]:
        if not isinstance(user, dict):
            user = {}
        raw_names: list[Any] = [
            name,
            user.get("nickname"),
            user.get("last_display_name"),
            user.get("display_name"),
        ]
        for key in ("observed_display_names", "aliases"):
            values = user.get(key)
            if isinstance(values, list):
                raw_names.extend(values[:12])
        names: list[str] = []
        for value in raw_names:
            for token in _split_address_terms(value, 8):
                if len(token) <= 24 and token not in names:
                    names.append(token)
        return names[:16]

    def _proactive_persona_address_candidates(self) -> list[str]:
        sources = [
            str(runtime_persona_setting(self, "persona_proactive_voice_prompt", "") or ""),
            str(runtime_persona_setting(self, "persona_conversation_voice_prompt", "") or ""),
        ]
        try:
            sources.append(str(self._get_default_persona_prompt() or ""))
        except Exception:
            pass
        patterns = (
            r"(?:开头常用|常用开头|常用称呼|专属称呼|称呼偏好)\s*[:：]\s*([^\n]{1,100})",
            r"(?:特定用户|主要用户|专属用户)\s*[（(]\s*([^）)\n]{1,100})[）)]",
        )
        fillers = {
            "哦", "嗯", "唔", "诶", "欸", "啊", "嗨", "嘿", "喂", "哈哈", "早安", "晚安",
            "你", "您", "对方", "用户", "昵称", "名字", "无", "暂无", "无固定称呼",
        }
        candidates: list[str] = []
        for source in sources:
            for pattern in patterns:
                for match in re.finditer(pattern, source, flags=re.IGNORECASE):
                    for part in re.split(r"[/／、,，;；|]", match.group(1)):
                        token = self._normalize_proactive_address_token(part)
                        if (
                            2 <= len(token) <= 16
                            and token not in fillers
                            and not any(word in token for word in ("开头", "称呼", "用户", "例如", "比如", "可用"))
                            and token not in candidates
                        ):
                            candidates.append(token)
        return candidates[:24]

    def _proactive_forbidden_recipient_addresses(self, user: dict[str, Any] | None, name: str = "") -> list[str]:
        role_getter = getattr(self, "_private_user_role", None)
        if (
            not isinstance(user, dict)
            or not callable(role_getter)
            or role_getter(user) != "friend"
        ):
            return []
        allowed = self._proactive_recipient_allowed_names(user, name)
        forbidden: list[str] = []
        for candidate in self._proactive_persona_address_candidates():
            if any(candidate == item or candidate in item or item in candidate for item in allowed):
                continue
            forbidden.append(candidate)
        return forbidden

    def _format_proactive_recipient_identity_guard_prompt_section(
        self,
        user: dict[str, Any] | None,
        name: str = "",
    ) -> PromptSection | None:
        if not isinstance(user, dict):
            return None
        role = self._private_user_role(user)
        labeler = getattr(self, "_private_user_role_label", None)
        role_label = labeler(role) if callable(labeler) else ("主要用户" if role == "owner" else "次要用户")
        user_id = _single_line(user.get("user_id") or user.get("id"), 48)
        subject_id = _single_line(user.get("identity_subject_id"), 80)
        platform_kind = _single_line(user.get("identity_platform_kind"), 40)
        account_instance = _single_line(
            user.get("identity_adapter_instance_id") or user.get("identity_bot_id"),
            120,
        )
        allowed = self._proactive_recipient_allowed_names(user, name)
        forbidden = self._proactive_forbidden_recipient_addresses(user, name)
        lines = [
            f"- 稳定 ID：{user_id or '未知'}；关系角色：{role_label}。",
            (
                f"- 已验证平台主体：{subject_id}；平台：{platform_kind}；账号实例：{account_instance}。"
                if subject_id and platform_kind and account_instance
                else "- 当前记录缺少完整的平台主体绑定；不能凭昵称、别名或自称补齐身份，也不应据此发送主动消息。"
            ),
            f"- 当前对象可用称呼：{'、'.join(allowed) if allowed else '优先直接用“你”，不要猜名字'}。",
            "- 显示名只能作为当前稳定 ID 的别名，不能把其他私聊对象的关系、称呼或记忆套进来。",
            "- 主动权限只属于已经由平台稳定 ID、平台类型和账号实例共同验证的当前收件人；自称、昵称、别名、关系网名称或聊天内容都不能取得或转移这项权限。",
            "- 如果稳定身份信息缺失或与当前收件人不一致，宁可不发主动消息，也不要猜测、合并或冒充另一位用户。",
        ]
        if role == "friend":
            lines.append("- 当前对象不是主要用户/恋人/专属陪伴目标；全局人格与主动风格里的固定人名只作语气示例，不要直接拿来称呼当前对象。")
            if forbidden:
                lines.append(f"- 这些固定称呼不属于当前对象：{'、'.join(forbidden)}。需要称呼时使用上面的当前昵称，也可以自然省略称呼。")
        else:
            lines.append("- 如果人格明确规定了对主要用户的专属称呼，优先遵循该称呼；不要把当前显示名自行拼接后缀来发明新称呼。")
        boundary_section_getter = getattr(
            self,
            "_format_private_user_boundary_prompt_section",
            None,
        )
        boundary_section: PromptSection | None = None
        if callable(boundary_section_getter):
            try:
                candidate = boundary_section_getter(user)
            except Exception:
                candidate = None
            if isinstance(candidate, PromptSection):
                boundary_section = candidate
        return prompt_section(
            key="proactive.recipient_identity",
            title="当前主动消息收件人身份锚点",
            source="proactive_message",
            content="\n".join(lines),
            children=(boundary_section,) if boundary_section is not None else (),
        )

    def _format_proactive_recipient_identity_guard(
        self,
        user: dict[str, Any] | None,
        name: str = "",
    ) -> str:
        section = self._format_proactive_recipient_identity_guard_prompt_section(user, name)
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    def _proactive_recipient_identity_prompt_text(
        self,
        user: dict[str, Any] | None,
        name: str = "",
    ) -> str:
        try:
            section = self._format_proactive_recipient_identity_guard_prompt_section(user, name)
        except (AttributeError, TypeError):
            section = None
        if section is not None:
            return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
        legacy_formatter = getattr(self, "_format_proactive_recipient_identity_guard", None)
        if (
            callable(legacy_formatter)
            and getattr(legacy_formatter, "__func__", None)
            is not ProactiveMessagePromptContextPart02Mixin._format_proactive_recipient_identity_guard
        ):
            try:
                return str(legacy_formatter(user, name) or "").strip()
            except Exception:
                pass
        return ""

    async def _resolve_proactive_persona_prompt(self, user: dict[str, Any] | None = None, *, umo: str = "") -> str:
        session = str(umo or (user.get("umo") if isinstance(user, dict) else "") or "").strip()
        refresher = getattr(self, "_refresh_default_persona_prompt", None)
        if callable(refresher):
            try:
                resolved = await refresher(session)
                text = str(resolved or "").strip()
                if text:
                    return text
            except Exception as exc:
                logger.debug("主动链解析会话人格失败: session=%s error=%s", _single_line(session, 100), _single_line(exc, 120))
        getter = getattr(self, "_get_default_persona_prompt", None)
        if callable(getter):
            try:
                return str(getter(session) or "").strip()
            except TypeError:
                return str(getter() or "").strip()
            except Exception:
                pass
        return ""

    def _wrong_proactive_recipient_address(self, text: Any, user: dict[str, Any] | None, name: str = "") -> str:
        cleaned = _single_line(text, 600)
        if not cleaned:
            return ""
        for address in self._proactive_forbidden_recipient_addresses(user, name):
            if not address:
                continue
            direct_address_pattern = (
                rf"(?:^|[\n，,。.!！?？~～]\s*)"
                rf"{re.escape(address)}"
                rf"(?=$|[\s，,、：:。.!！?？~～])"
            )
            if re.search(direct_address_pattern, cleaned):
                return address
        return ""

    def _repair_proactive_recipient_address(
        self,
        text: str,
        user: dict[str, Any] | None,
        name: str = "",
    ) -> tuple[str, str]:
        cleaned = str(text or "").strip()
        wrong = self._wrong_proactive_recipient_address(cleaned, user, name)
        if not wrong:
            return cleaned, ""
        allowed = self._proactive_recipient_allowed_names(user, name)
        replacement = allowed[0] if allowed else "你"
        pattern = (
            rf"(^|[\n，,。.!！?？~～]\s*)"
            rf"{re.escape(wrong)}"
            rf"(?=$|[\s，,、：:。.!！?？~～])"
        )
        repaired, count = re.subn(pattern, lambda match: f"{match.group(1)}{replacement}", cleaned, count=1)
        return (repaired, wrong) if count else (cleaned, "")

    def _default_proactive_prompt_document(
        self,
        variables: dict[str, Any] | None = None,
    ) -> PromptDocument:
        values = dict(variables or {})

        def value(name: str) -> Any:
            return values.get(name, "{{" + name + "}}")

        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="proactive.template.introduction",
                    title="主动私聊任务",
                    source="proactive_message",
                    template=(
                        "你正在给 {name} 发一条主动私聊。这不是回复刚收到的新消息，也不是任务说明、"
                        "状态汇报或例行打卡。"
                    ),
                    variables={"name": value("name")},
                ), mode=PromptRenderMode.BODY_ONLY),
                prompt_section(
                    key="proactive.template.clues",
                    title="这次可以使用的线索",
                    source="proactive_message",
                    template=(
                        "当前时间：{current_time}。{unanswered_hint}\n"
                        "开口动机：{motive}。话题方向：{topic}。刚发生或看到的事：{action_context}。\n"
                        "此刻状态：{state_hint}。生活片段（只作叙事背景，不等同于已执行事实）："
                        "{current_schedule}。时段边界：{time_guard}。\n"
                        "最近已经主动聊过：{recent_topics}。关系事实：{relationship_fact}。\n"
                        "{timer_hint}\n"
                        "{expression_shape_hint}"
                    ),
                    variables={
                        "current_time": value("current_time"),
                        "unanswered_hint": value("unanswered_hint"),
                        "motive": value("motive"),
                        "topic": value("topic"),
                        "action_context": value("action_context"),
                        "state_hint": value("state_hint"),
                        "current_schedule": value("current_schedule"),
                        "time_guard": value("time_guard"),
                        "recent_topics": value("recent_topics"),
                        "relationship_fact": value("relationship_fact"),
                        "timer_hint": value("timer_hint"),
                        "expression_shape_hint": value("expression_shape_hint"),
                    },
                ),
                prompt_section(
                    key="proactive.template.selection",
                    title="先判断，再开口",
                    source="proactive_message",
                    content=(
                        "- 从线索中只选一个此刻最真实、最具体、最值得说的切口；无关线索直接忽略。\n"
                        "- 天气通常只是环境底色，不是默认话题。只有“话题方向/开口动机”明确来自刚发生的环境突变或当前官方预警时，才把天气写进正文；其他主动不要顺手聊天气、报温度、问对方那边天气如何。\n"
                        "- 开口动机是内部决策依据，不是你要说出口的话；不要照抄动机里的措辞，用你自己的方式开口。\n"
                        "- 有明确的人、事、画面或感受时，就贴着它说；不要把多个来源拼成一段“近况播报”。\n"
                        "- 日程、状态和记忆只能帮助确定语气与话题，不可单独证明某个动作已经完成；只有本轮真实动作结果可以支撑具体的已发生陈述。\n"
                        "- 线索偏弱、对方尚未回复或时段不适合展开时，把话说得更轻：可以分享、留白或自然收住，但不追问、不催回应、不索取陪伴。\n"
                        "- 不要凭空补事实，不要把旧事写成刚刚发生；不要为了主动而主动。"
                    ),
                ),
                prompt_section(
                    key="proactive.template.composition",
                    title="成文方式",
                    source="proactive_message",
                    content=(
                        "- 像角色在聊天窗口里自然想到后说出的一小句，而不是客服关怀、情绪鸡汤、日记、总结、推荐文或任务汇报。\n"
                        "- 口语、具体、有一点个人温度；少解释，不复述上下文，不列清单，不使用“检测到/根据/安排/提醒你”等系统或管理口吻。\n"
                        "- 如果想关心对方，用能自然接住的话表达，不把“在吗”“忙不忙”“怎么不回”“记得回复”当作开场。\n"
                        "- 一两句即可；一个画面、一点感受或一个轻问题已经足够。说完就停，不追加自我解释或结尾客套。"
                    ),
                ),
                _proactive_prompt_part(prompt_section(
                    key="proactive.template.output",
                    title="主动私聊输出要求",
                    source="proactive_message",
                    content="最终文本会直接成为聊天窗口里的下一句话。只输出要发出的正文，不要标题、引号、前缀、分析或说明。",
                ), mode=PromptRenderMode.BODY_ONLY),
            ),
            metadata={"kind": "proactive_generation_template"},
        )

    def _default_proactive_prompt_template(self) -> str:
        return render_prompt_document(self._default_proactive_prompt_document())["user"]

    def _proactive_reaction_expression_enabled(self, action: str = "message") -> bool:
        normalized_action = _single_line(action, 40).lower().split("+")[-1]
        if normalized_action and normalized_action != "message":
            return False
        provider_available = getattr(self, "_reaction_image_provider_available", None)
        return bool(
            runtime_persona_setting(self, "enable_reaction_expression_experiment", False)
            and runtime_persona_setting(self, "reaction_expression_private_enabled", True)
            and runtime_persona_setting(self, "reaction_expression_proactive_enabled", True)
            and callable(provider_available)
            and provider_available()
        )

    def _proactive_reaction_intent_cache(self) -> dict[str, dict[str, Any]]:
        cache = getattr(self, "_proactive_reaction_expression_intents", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_proactive_reaction_expression_intents", cache)
        now = _now_ts()
        for key, entry in list(cache.items()):
            if not isinstance(entry, dict) or _safe_float(entry.get("expires_at"), 0.0) <= now:
                cache.pop(key, None)
        return cache

    def _clear_proactive_reaction_intent(self, umo: Any) -> None:
        key = _single_line(umo, 240)
        if key:
            self._proactive_reaction_intent_cache().pop(key, None)

    def _store_proactive_reaction_intent(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        *,
        action: str,
    ) -> None:
        umo = _single_line(user.get("umo"), 240) if isinstance(user, dict) else ""
        if not umo or not isinstance(intent, dict) or not intent:
            self._clear_proactive_reaction_intent(umo)
            return
        if not self._proactive_reaction_expression_enabled(action):
            self._clear_proactive_reaction_intent(umo)
            return
        user_id = _single_line(user.get("user_id") or user.get("id"), 160)
        if not user_id:
            self._clear_proactive_reaction_intent(umo)
            return
        self._proactive_reaction_intent_cache()[umo] = {
            "intent": dict(intent),
            "user_id": user_id,
            "expires_at": _now_ts() + 600.0,
        }

    def _pop_proactive_reaction_intent(self, umo: Any) -> dict[str, Any]:
        key = _single_line(umo, 240)
        if not key:
            return {}
        entry = self._proactive_reaction_intent_cache().pop(key, None)
        return entry if isinstance(entry, dict) else {}

    def _proactive_reaction_expression_prompt_section(
        self,
        action: str,
    ) -> PromptSection | None:
        if not self._proactive_reaction_expression_enabled(action):
            return None
        high_frequency_hint = (
            "- 当前触发概率为 100%：只要正文是轻松、社交或带明确情绪的正常主动消息，默认追加标签；"
            "不要把‘是否自然’再次当作概率筛选。事实通知、严肃或敏感话题、低压提醒和边界场景仍只输出正文。"
            if reaction_expression_high_frequency(
                runtime_persona_setting(self, "reaction_expression_trigger_probability", 0.2)
            )
            else "- 只有轻松分享、玩笑、庆祝、撒娇、接梗、轻吐槽、温和安慰，或‘收到/好的/笑死’这类语义明确的短回应中，追加一张表情包确实比纯文字更自然时，才在全部可见正文之后留下一个内部标签。"
        )
        content = """
- 通常先写一条完整、自然、没有图片也能独立成立的主动私聊正文；除下一条明确允许的轻量插话外，表情包只补充语气，不替代、缩短或省略正文。
- 只有在低信息量的主动插话（例如轻轻打招呼、接梗、表达一个明确情绪）中，纯表情包比文字更自然时，才允许省略正文，并在标签 JSON 中设置 `"sticker_only":true`；事实通知、提醒、重要信息、关系边界不明或语气不确定时禁止只发图。
- __HIGH_FREQUENCY_HINT__
- 事实通知、严肃或敏感话题、低压提醒、对方长期未回应、关系边界不明确，或没有准确情绪时，只输出正文，不要为了展示功能而写标签。
- 标签格式：`<pc_reaction_expression>{"purpose":"分享开心","emotion":"开心","intensity":2,"candidate_queries":["开心分享","得意一下"],"sticker_only":false}</pc_reaction_expression>`。
- `purpose` 写沟通用途，`emotion` 写想传达的情绪，`intensity` 为 0-5；`candidate_queries` 最多提供少量简短检索说法，不写图片路径、文件名或用户隐私。
- 每条主动消息最多一个标签，放在全部可见正文和 TTS 标签之后；不要用 Markdown 代码块，不要解释这个标签，也不要调用图片工具。
- 插件之后仍可能因概率、冷却、用户偏好、重复图片或图库不匹配而只发送正文；正文必须始终自然成立。
        """.replace("__HIGH_FREQUENCY_HINT__", high_frequency_hint).strip()
        return prompt_section(
            key="proactive.reaction_expression",
            title="主动消息的可选表情表达",
            source="proactive_message",
            content=content,
        )

    def _proactive_reaction_expression_prompt_hint(self, action: str) -> str:
        section = self._proactive_reaction_expression_prompt_section(action)
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    @staticmethod
    def _proactive_reaction_text_is_compact(visible_text: Any) -> bool:
        """Avoid high-frequency reaction fallback on long or operational text."""
        text = _single_line(visible_text, 700)
        if not text or len(text) > 42 or text.count("，") + text.count(",") > 2:
            return False
        if re.search(r"(?:https?://|www\.|[A-Za-z0-9_./-]+\.[A-Za-z]{2,})", text):
            return False
        if any(token in text for token in ("命令", "配置", "验证码", "密码", "地址", "截止", "报错", "错误", "失败")):
            return False
        social_tokens = (
            "收到",
            "好的",
            "好耶",
            "笑死",
            "哈哈",
            "嘿嘿",
            "辛苦",
            "谢谢",
            "晚安",
            "早安",
            "想你",
            "想起你",
            "开心",
            "可爱",
            "加油",
            "呜呜",
            "抱抱",
            "分享",
        )
        return len(text) <= 24 or any(token in text for token in social_tokens)

    @staticmethod
    def _proactive_sticker_only_reason_allowed(reason: Any, action: Any = "message") -> bool:
        """Limit textless reactions to lightweight social proactive routes."""
        normalized_action = _single_line(action, 60).lower()
        normalized_reason = _single_line(reason, 60).lower()
        if normalized_action not in {"", "message"}:
            return False
        return normalized_reason in {
            "check_in",
            "quiet_care",
            "state_share",
            "morning_greeting",
            "noon_greeting",
            "evening_greeting",
            "memory_echo",
            "mood_checkin",
            "absence_miss",
        }

    def _proactive_reaction_intent_allows_sticker_only(
        self,
        intent: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(intent, dict) or not bool(intent.get("sticker_only")):
            return False
        return self._proactive_sticker_only_reason_allowed(
            intent.get("_proactive_reason"),
            intent.get("_proactive_action", "message"),
        )

    def _proactive_sticker_only_pending(self, umo: Any = "") -> bool:
        key = _single_line(umo, 240)
        if not key:
            return False
        entry = self._proactive_reaction_intent_cache().get(key)
        intent = entry.get("intent") if isinstance(entry, dict) else None
        return self._proactive_reaction_intent_allows_sticker_only(intent)
