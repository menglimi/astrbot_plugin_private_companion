# -*- coding: utf-8 -*-
"""DailyStatePlanRelationshipAuthoritySanitizeMixin。

由 tools/split_mixin_domain.py 从 daily_state_plan.py 机械抽取（23 个方法 + 0 个模块级名字 + 0 个类级赋值 / 618 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStatePlanMixin）。
"""
from __future__ import annotations

from .daily_state_plan_shared import _now_ts, logger
from .daily_state_plan_shared import Any
from .daily_state_plan_shared import PromptRenderMode
from .daily_state_plan_shared import PromptSection
from .daily_state_plan_shared import _safe_float
from .daily_state_plan_shared import _single_line
from .daily_state_plan_shared import prompt_section
from .daily_state_plan_shared import re
from .daily_state_plan_shared import render_prompt_sections
from .daily_state_plan_shared import runtime_persona_setting



class DailyStatePlanRelationshipAuthoritySanitizeMixin:
    """DailyStatePlanRelationshipAuthoritySanitizeMixin（从 DailyStatePlanMixin 拆出）。"""


    def _format_important_dates_injection(self) -> str:
        section = self._format_important_dates_prompt_section()
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_important_dates_prompt_section(self) -> PromptSection:
        important_dates = self._format_important_dates_for_prompt()
        body = ""
        if important_dates and important_dates != "（近期没有需要特别记住的日期）":
            body = (
                f"{important_dates}\n"
                "如果用户提到相关日期、纪念、生日、约定或计划,请自然承接；不要无故强行展开。"
            )
        return prompt_section(
            key="important.dates",
            title="近期重要日期",
            source="daily_state",
            content=body,
        )

    @staticmethod
    def _daily_plan_message_target_is_allowed(target: str) -> bool:
        normalized = re.sub(r"[\s“”\"'‘’《》【】\[\]（）()的那边这边身上手机微信QQqq号:：]+", "", str(target or ""))
        if not normalized:
            return False
        allowed_targets = (
            "你",
            "用户",
            "主人",
            "主要用户",
            "当前用户",
            "对方",
            "自己",
            "我",
        )
        neutral_targets = (
            "手机",
            "通知",
            "提醒",
            "闹钟",
            "系统",
            "日历",
            "输入框",
            "屏幕",
            "软件",
            "应用",
            "网页",
        )
        return any(token in normalized for token in allowed_targets) or normalized in neutral_targets

    @classmethod
    def _daily_plan_clause_has_named_message_interaction(cls, clause: str) -> bool:
        if not clause:
            return False
        target_patterns = (
            r"给(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:回了?(?:一?条)?(?:消息|微信|QQ|私信|短信|语音)?|回复了?|发了?(?:一?条)?(?:消息|微信|QQ|私信|短信|语音)?|发去(?:消息|微信|QQ|私信|短信|语音)?|私聊了?)",
            r"(?:收到|看见|看到|点开|翻到)(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:的)?(?:消息|微信|QQ|私信|短信|语音|提醒)",
            r"(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:发来|发了|传来|弹来|回了?)(?:一?条)?(?:消息|微信|QQ|私信|短信|语音|提醒)",
            r"(?:和|跟)(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:聊了?|聊天|私聊|互相吐槽|互相安慰|发消息|回消息)",
        )
        for pattern in target_patterns:
            for match in re.finditer(pattern, clause):
                target = _single_line(match.groupdict().get("target"), 24)
                if target and not cls._daily_plan_message_target_is_allowed(target):
                    return True
        relation_tokens = (
            "熟人",
            "同学",
            "老师",
            "朋友",
            "室友",
            "邻居",
            "前辈",
            "后辈",
            "家人",
            "妈妈",
            "爸爸",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
        )
        message_actions = (
            "发来消息",
            "发了消息",
            "回了消息",
            "回消息",
            "回复",
            "私聊",
            "聊天",
            "提醒她",
            "提醒他",
            "找她",
            "找他",
        )
        return any(token in clause for token in relation_tokens) and any(token in clause for token in message_actions)

    def _daily_plan_named_entity_is_known(self, name: Any) -> bool:
        normalized = _single_line(name, 32).casefold()
        if not normalized:
            return False
        known_names = [_single_line(runtime_persona_setting(self, "bot_name", "小星"), 80)]
        data = getattr(self, "data", {})
        users = data.get("users") if isinstance(data, dict) else None
        if isinstance(users, dict):
            for user in users.values():
                if not isinstance(user, dict):
                    continue
                known_names.extend(
                    _single_line(user.get(field), 80)
                    for field in ("nickname", "display_name", "user_name", "name")
                )
        if any(candidate and candidate.casefold() == normalized for candidate in known_names):
            return True
        persona_sources = (
            runtime_persona_setting(self, "schedule_persona_prompt", ""),
            runtime_persona_setting(self, "schedule_worldview_prompt", ""),
            getattr(self, "_default_persona_prompt_cache", ""),
        )
        boundary = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(normalized)}(?![A-Za-z0-9_])", re.IGNORECASE)
        return any(boundary.search(str(source or "")) for source in persona_sources)

    @staticmethod
    def _daily_plan_relationship_alias_groups() -> tuple[tuple[str, ...], ...]:
        """Stable relationship names that must come from an identity source."""
        return (
            ("妈妈", "母亲", "妈咪", "老妈", "娘亲", "阿妈"),
            ("爸爸", "父亲", "爹地", "老爸", "爹", "阿爸"),
            ("父母", "双亲"),
            ("家人", "家里人", "亲人"),
            ("祖母", "奶奶", "外婆", "姥姥"),
            ("祖父", "爷爷", "外公", "姥爷"),
            ("哥哥", "兄长", "大哥", "阿哥"),
            ("姐姐", "姊姊", "姊姐", "阿姐"),
            ("弟弟", "胞弟"),
            ("妹妹", "胞妹"),
            ("兄弟姐妹", "兄弟姊妹", "手足"),
            ("叔叔", "伯伯", "舅舅", "姨父", "姑父"),
            ("阿姨", "姑姑", "姨妈", "舅妈", "婶婶", "伯母"),
            ("亲戚", "亲属"),
            ("朋友", "好友", "闺蜜", "发小", "死党"),
            ("同学", "同桌", "同班同学", "校友"),
            ("学长", "学姐", "学弟", "学妹"),
            ("老师", "教师", "班主任", "导师"),
            ("师父", "师傅"),
            ("室友", "舍友"),
            ("同事", "同僚"),
            ("上司", "领导", "老板"),
            ("邻居", "邻家"),
            ("前辈", "后辈"),
            ("恋人", "爱人", "伴侣"),
            ("男朋友", "男友"),
            ("女朋友", "女友"),
            ("丈夫", "老公"),
            ("妻子", "老婆"),
            ("未婚夫", "未婚妻"),
            ("监护人", "养父", "养母", "继父", "继母"),
        )

    @staticmethod
    def _daily_plan_identity_bound_relationship_groups() -> set[str]:
        """Relationships too stable/private to infer from an old life fragment."""
        return {
            "妈妈",
            "爸爸",
            "父母",
            "家人",
            "祖母",
            "祖父",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
            "兄弟姐妹",
            "叔叔",
            "阿姨",
            "亲戚",
            "恋人",
            "男朋友",
            "女朋友",
            "丈夫",
            "妻子",
            "未婚夫",
            "监护人",
        }

    @staticmethod
    def _mask_non_relationship_phrases(text: Any) -> str:
        source = str(text or "")
        if not source:
            return ""
        for phrase in (
            "母亲节",
            "父亲节",
            "教师节",
            "父母官",
            "老师傅",
            "小姐姐",
            "小哥哥",
            "食堂阿姨",
            "宿管阿姨",
            "保洁阿姨",
            "清洁阿姨",
            "保安叔叔",
            "司机叔叔",
            "老婆饼",
        ):
            source = source.replace(phrase, "□" * len(phrase))
        return source

    def _daily_plan_relationship_authority_sources(self) -> tuple[str, ...]:
        sources = [
            str(runtime_persona_setting(self, "schedule_persona_prompt", "") or ""),
            str(runtime_persona_setting(self, "schedule_worldview_prompt", "") or ""),
            str(getattr(self, "_default_persona_prompt_cache", "") or ""),
        ]
        getter = getattr(self, "_get_default_persona_prompt", None)
        if callable(getter):
            try:
                sources.append(str(getter() or ""))
            except Exception:
                pass
        return tuple(dict.fromkeys(source for source in sources if source.strip()))

    def _daily_plan_declared_relation_tokens(self) -> set[str]:
        authority_text = self._mask_non_relationship_phrases(
            "\n".join(self._daily_plan_relationship_authority_sources())
        )
        declared: set[str] = set()
        groups = self._daily_plan_relationship_alias_groups()
        for aliases in groups:
            if any(alias in authority_text for alias in aliases):
                declared.update(aliases)

        # Institutional roles are an inherent part of an explicitly declared
        # school/work identity, while family roles are never inferred this way.
        if re.search(r"学生|校园|学校|上学|教室|班级|课程", authority_text):
            for aliases in groups:
                if aliases[0] in {"同学", "学长", "老师"}:
                    declared.update(aliases)
        if re.search(r"上班|职员|员工|公司|工位|办公室|职场", authority_text):
            for aliases in groups:
                if aliases[0] in {"同事", "上司"}:
                    declared.update(aliases)
        return declared

    def _daily_plan_undeclared_relationship_tokens(self, text: Any) -> list[str]:
        source = self._mask_non_relationship_phrases(text)
        if not source:
            return []
        declared = self._daily_plan_declared_relation_tokens()
        hits: list[str] = []
        identity_bound_groups = self._daily_plan_identity_bound_relationship_groups()
        all_aliases = sorted(
            {
                alias
                for group in self._daily_plan_relationship_alias_groups()
                if group[0] in identity_bound_groups
                for alias in group
            },
            key=len,
            reverse=True,
        )
        for alias in all_aliases:
            if alias not in declared and alias in source:
                hits.append(alias)
        return hits

    @staticmethod
    def _relationship_clause_is_explicitly_user_owned(clause: str, relation_tokens: list[str]) -> bool:
        if not clause or not relation_tokens:
            return False
        owner_marker = r"(?:主要用户|当前用户|这位用户|收件人|对方|用户|User|user)"
        for token in relation_tokens:
            escaped = re.escape(token)
            if re.search(rf"{owner_marker}[^，,。；;！？!?]{{0,24}}{escaped}", clause):
                return True
            if re.search(rf"{escaped}[^，,。；;！？!?]{{0,16}}(?:是|属于|来自)?{owner_marker}(?:的|那边)", clause):
                return True
        return False

    def _format_generation_relationship_authority_guard(self) -> str:
        return render_prompt_sections(
            [self._format_generation_relationship_authority_guard_prompt_section()],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_generation_relationship_authority_guard_prompt_section(self) -> PromptSection:
        declared = self._daily_plan_declared_relation_tokens()
        canonical = [
            aliases[0]
            for aliases in self._daily_plan_relationship_alias_groups()
            if any(alias in declared for alias in aliases)
        ]
        declared_text = "、".join(canonical) if canonical else "无"
        content = (
            "- 只有日程专用角色设定、日程世界观和当前默认人格能够建立 Bot 的稳定关系；"
            "旧日程、旧日记、旧动态、聊天摘要、MemoryCompanion、技能/创作记录和其他连续性材料不能单独证明一段新关系。\n"
            f"- 当前身份来源已声明的关系称谓：{declared_text}。只有这些关系及其同义称呼可以作为 Bot 的关系事实。\n"
            "- 连续性材料里的关系若不能和身份来源对上，就按未经核实的旧叙事略过；不要续写，也不要换个称呼继续沿用。\n"
            "- 用户谈到的亲友只属于用户，除非身份来源另有明确设定，不得转移成 Bot 自己的亲友。"
            "\n- 当前收件人与 Bot 的结构化关系由本轮收件人关系事实单独决定，不要用这份生活关系清单覆盖它。"
        )
        return prompt_section(
            key="relationship.generation_authority",
            title="关系事实权限",
            source="daily_state",
            content=content,
        )

    def _sanitize_generation_relationship_context(
        self,
        text: Any,
        *,
        source: str = "",
        max_chars: int = 0,
    ) -> str:
        """Remove undeclared relationship clauses before they reach a generator."""
        raw = str(text or "").strip()
        if not raw:
            return ""
        initial_hits = self._daily_plan_undeclared_relationship_tokens(raw)
        if not initial_hits:
            return raw[:max_chars] if max_chars > 0 else raw

        cleaned_lines: list[str] = []
        removed_any = False
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line:
                if cleaned_lines and cleaned_lines[-1]:
                    cleaned_lines.append("")
                continue
            pieces = re.split(r"([，,。；;！？!?]+)", line)
            kept: list[str] = []
            for index in range(0, len(pieces), 2):
                clause = pieces[index].strip()
                separator = pieces[index + 1] if index + 1 < len(pieces) else ""
                if not clause:
                    continue
                clause_hits = self._daily_plan_undeclared_relationship_tokens(clause)
                if clause_hits and not self._relationship_clause_is_explicitly_user_owned(clause, clause_hits):
                    removed_any = True
                    continue
                kept.append(clause)
                if separator:
                    kept.append(separator)
            clean_line = "".join(kept).strip(" ，,。；;！？!?")
            if clean_line and clean_line not in {"-", "*", "•"}:
                cleaned_lines.append(clean_line)
        if not removed_any:
            return raw[:max_chars] if max_chars > 0 else raw
        cleaned = "\n".join(cleaned_lines).strip()
        if max_chars > 0:
            cleaned = cleaned[:max_chars]
        if cleaned == (raw[:max_chars] if max_chars > 0 else raw):
            return cleaned

        log_key = f"{source or '-'}|{'/'.join(initial_hits[:4])}"
        now = _now_ts()
        recent_logs = getattr(self, "_recent_relationship_context_sanitize_logs", None)
        if not isinstance(recent_logs, dict):
            recent_logs = {}
            setattr(self, "_recent_relationship_context_sanitize_logs", recent_logs)
        if now - _safe_float(recent_logs.get(log_key), 0) >= 1800:
            logger.info(
                "生成前已剔除未声明关系上下文: source=%s relations=%s",
                source or "-",
                ",".join(initial_hits[:8]),
            )
            recent_logs[log_key] = now
        return cleaned

    def _daily_plan_clause_has_unsafe_social_fact(self, text: str) -> bool:
        clause = _single_line(text, 160)
        if not clause:
            return False
        if self._daily_plan_undeclared_relationship_tokens(clause):
            return True
        if self._daily_plan_clause_has_named_message_interaction(clause):
            return True
        future_commitment = (
            "约好",
            "约了",
            "约定",
            "约着",
            "约去",
            "约夜宵",
            "约饭",
            "约见",
            "约她",
            "约他",
            "约人",
            "下周",
            "下次一起",
            "改天一起",
            "明天一起",
            "后天一起",
            "之后一起",
            "过几天一起",
        )
        if any(token in clause for token in future_commitment):
            return True
        if re.search(r"(约|叫|喊|拉|找|邀)[^，。；;,.]{0,16}(一起|夜宵|吃|喝|看|玩|逛|见面|出门)", clause):
            return True
        if re.search(r"(和|跟)[^，。；;,.]{1,16}一起(去|吃|喝|看|玩|逛|见|出门|夜宵)", clause):
            return True
        if re.search(r"(消息|私信|电话|语音)[^，。；;,.]{0,16}(约|叫|喊|拉|邀)[^，。；;,.]{0,16}(一起|去|吃|喝|看|玩|逛|夜宵)", clause):
            return True
        concrete_relation = (
            "熟人",
            "同学",
            "老师",
            "朋友",
            "室友",
            "邻居",
            "前辈",
            "后辈",
            "家人",
            "父母",
            "妈妈",
            "爸爸",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
        )
        if any(token in clause for token in ("碰见", "遇见", "撞见", "碰到", "遇到")) and any(
            token in clause for token in concrete_relation
        ):
            return True
        if re.search(r"(碰见|遇见|撞见|遇到)[过了]?[一-龥]{2,4}", clause) and not any(
            token in clause for token in ("路人", "店员", "陌生人", "旁边的人", "小动物", "猫", "狗", "鸟")
        ):
            return True
        if re.search(r"(顺手|顺带|特意|回来时|回来的时候)?.{0,8}给[^，。；;,.]{1,12}(带|买|捎|留|放)了?", clause):
            return True
        named_companion = re.search(
            r"(?:与|和|跟)\s*[A-Z][A-Za-z0-9_.-]{1,23}\s*(?:一起)?(?:吃|喝|聊|逛|玩|看|见面|出门)",
            clause,
        )
        if named_companion:
            name_match = re.search(r"(?:与|和|跟)\s*([A-Z][A-Za-z0-9_.-]{1,23})", named_companion.group(0))
            if name_match and self._daily_plan_named_entity_is_known(name_match.group(1)):
                return False
            return True
        return False

    @staticmethod
    def _sanitize_schedule_model_artifacts(text: Any, *, limit: int = 180) -> str:
        """Remove model scratch fields and speaker continuations from schedule prose."""
        source = re.sub(r"\s+", " ", str(text or "")).strip()
        if not source:
            return ""
        source = source.replace("```json", "").replace("```", "")
        source = source.replace("**", "").replace("__", "").replace("`", "")

        scratch_pattern = re.compile(
            r"(?:^|[\s，。；;!?！？])(?:dream[_\s-]*seed|analysis|reasoning(?:_content)?|角色草稿|续写提示)\s*[:：]",
            re.IGNORECASE,
        )
        scratch = scratch_pattern.search(source)
        if scratch:
            source = source[: scratch.start()].rstrip(" ，。；;:：")

        speaker_pattern = re.compile(
            r"(?:^|[\s，。；;!?！？])(?:Fox|Assistant|Character|Bot|[A-Z][A-Za-z0-9_.-]{1,20})\s*[:：]"
        )
        speaker = speaker_pattern.search(source)
        if speaker:
            if not source[: speaker.start()].strip(" ，。；;:："):
                return ""
            source = source[: speaker.start()].rstrip(" ，。；;:：")
        return _single_line(source, limit)

    @staticmethod
    def _schedule_text_is_single_meal_action(text: Any) -> bool:
        source = _single_line(text, 240)
        if not source:
            return False
        meal_action = re.search(
            r"吃(?:着|了|完|过|点|一|顿|碗)?|用餐|进餐|品尝|享用|早餐|早饭|午餐|午饭|晚餐|晚饭|夜宵|喝粥",
            source,
        )
        if not meal_action:
            return False
        return not re.search(
            r"吃完|饭后|餐后|随后|然后|之后|接着|再去|再把|转而|余下|剩下|后来|收拾完.*(?:休息|做|处理|出门)",
            source,
        )

    @staticmethod
    def _sanitize_schedule_meal_time_wording(text: Any, start_minutes: int | None) -> str:
        source = _single_line(text, 180)
        if not source or start_minutes is None:
            return source
        minute = int(start_minutes) % (24 * 60)
        if minute < 16 * 60:
            source = re.sub(r"吃(?:晚饭|晚餐)", "吃点东西", source)
            source = re.sub(r"(?:晚饭|晚餐)", "用餐", source)
        if minute >= 15 * 60:
            source = re.sub(r"吃(?:早餐|早饭)", "吃点东西", source)
            source = re.sub(r"(?:早餐|早饭)", "用餐", source)
        return _single_line(source, 180)

    @classmethod
    def _sanitize_overlong_schedule_activity(cls, text: Any, duration_minutes: int | None) -> str:
        source = _single_line(text, 180)
        if not source or duration_minutes is None or duration_minutes <= 120:
            return source
        if not cls._schedule_text_is_single_meal_action(source):
            return source
        stem = source.rstrip("。；;，, ")
        return _single_line(f"这段开始时，{stem}；吃完后便按这段时间的节奏休息或处理手边的事。", 180)

    def _sanitize_daily_plan_social_fact_text(self, text: str, *, field: str = "") -> str:
        source = self._sanitize_schedule_model_artifacts(text, limit=180)
        if not source:
            return ""
        raw_clauses = [part for part in re.split(r"[，,。；;]+", source) if _single_line(part, 120)]
        unsafe_flags = [self._daily_plan_clause_has_unsafe_social_fact(part) for part in raw_clauses]
        if not any(unsafe_flags):
            return source
        kept = []
        for index, part in enumerate(raw_clauses):
            if unsafe_flags[index]:
                continue
            cleaned_part = _single_line(part, 120)
            if (
                index + 1 < len(raw_clauses)
                and unsafe_flags[index + 1]
                and len(cleaned_part) <= 20
                and re.search(r"(?:时|的时候|期间|过程中)$", cleaned_part)
            ):
                continue
            kept.append(cleaned_part)
        cleaned = "，".join(kept).strip("，,。；; ")
        if not cleaned:
            cleaned = "放慢节奏处理手边的小事，把这段时间过得轻一点"
        if cleaned == source:
            return source
        log_key = "|".join((field or "-", _single_line(source, 120), _single_line(cleaned, 120)))
        now = _now_ts()
        recent_logs = getattr(self, "_recent_social_fact_sanitize_logs", None)
        if not isinstance(recent_logs, dict):
            recent_logs = {}
            setattr(self, "_recent_social_fact_sanitize_logs", recent_logs)
        last_logged = _safe_float(recent_logs.get(log_key), 0)
        if now - last_logged >= 1800:
            logger.info(
                "已清理日程中的未授权社交事实: field=%s before=%s after=%s",
                field or "-",
                _single_line(source, 120),
                _single_line(cleaned, 120),
            )
            recent_logs[log_key] = now
            if len(recent_logs) > 200:
                cutoff = now - 3600
                for key, ts in list(recent_logs.items()):
                    if _safe_float(ts, 0) < cutoff:
                        recent_logs.pop(key, None)
        return cleaned

    @staticmethod
    def _sanitize_empty_daily_plan_message_seed(text: str) -> str:
        cleaned = _single_line(text, 140)
        if not cleaned:
            return ""
        normalized = re.sub(r"[。！？!?,，、；;\s]+", "", cleaned)
        empty_markers = (
            "这段没什么想说的",
            "没什么想说的",
            "这段没有什么想说的",
            "没有什么想说的",
            "这段先留白",
            "先留白",
            "留白",
            "脑子空空的",
            "脑袋空空的",
            "没什么可说的",
            "没有什么可说的",
            "这段没话说",
            "没话说",
            "先不吵你",
            "不吵你",
            "先不打扰你",
            "不打扰你",
            "这段先安静一下",
            "先安静一下",
            "下午空一下",
            "下午空一会",
            "下午空一会儿",
            "下午空了下",
        )
        if normalized in empty_markers:
            return ""
        if any(token in normalized for token in ("没什么想说", "没有什么想说", "没什么可说", "没有什么可说")):
            return ""
        if any(token in normalized for token in ("先不吵", "不打扰", "先留白")):
            return ""
        if re.fullmatch(r"(?:上午|中午|下午|晚上|午后|傍晚)?(?:先)?空(?:一下|一会儿?|了下)", normalized):
            return ""
        return cleaned

    def _sanitize_daily_plan_inplace(self, plan: dict[str, Any]) -> bool:
        if not isinstance(plan, dict):
            return False
        raw_items = plan.get("items") if isinstance(plan.get("items"), list) else plan.get("schedule")
        if not isinstance(raw_items, list):
            return False
        changed = self._normalize_plan_item_intervals(raw_items)
        parsed_starts = [
            self._parse_hhmm_to_minutes(item.get("time")) if isinstance(item, dict) else None
            for item in raw_items
        ]
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            for field in ("activity", "message_seed"):
                original = _single_line(item.get(field), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(original, field=field)
                if field == "activity":
                    start = parsed_starts[index]
                    next_start = next(
                        (candidate for candidate in parsed_starts[index + 1 :] if candidate is not None),
                        None,
                    )
                    end = self._plan_item_end_minutes(start, item, next_start=next_start) if start is not None else None
                    duration = end - start if start is not None and end is not None else None
                    cleaned = self._sanitize_schedule_meal_time_wording(cleaned, start)
                    cleaned = self._sanitize_overlong_schedule_activity(cleaned, duration)
                if field == "message_seed":
                    cleaned = self._sanitize_empty_daily_plan_message_seed(cleaned)
                if cleaned != original:
                    item[field] = cleaned
                    changed = True
        if changed:
            plan["sanitized_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
        return changed
