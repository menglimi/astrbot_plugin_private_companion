# -*- coding: utf-8 -*-
"""空间视图域。

由 tools/split_mixin_domain.py 从 llm_tool_actions.py 机械抽取（16 个方法 + 0 个模块级名字 + 0 个类级赋值 / 943 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsMixin）。
"""
from __future__ import annotations

import html
import json
import re
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import _safe_float, _safe_int, _single_line
from .llm_tool_actions_shared import _render_tool_prompt_section_labeled
from .qzone_selection import (
    QzoneViewTarget,
    classify_qzone_view_owner,
    normalize_qzone_uin,
    normalize_qzone_view_target_scope,
    parse_qzone_post_selection,
    qzone_view_owner_is_pronoun_safe,
    resolve_qzone_view_target,
)
from astrbot.api.event import AstrMessageEvent
from datetime import datetime, timedelta
from typing import Any



class LlmToolActionsQzoneMixin:
    """空间视图域（从 LlmToolActionsMixin 拆出）。"""


    def _qzone_tool_instruction_prompt_section(
        self,
        event: AstrMessageEvent | None = None,
    ) -> PromptSection | None:
        availability = getattr(self, "_qzone_available", None)
        if not (self.enabled and self.enable_qzone_integration):
            return None
        if callable(availability) and not availability(event):
            return None
        body = """当用户明确要求你查看说说、QQ 空间动态、点赞/评论说说,或要求你发一条说说时,可以使用 Private Companion 的 QQ 空间工具。
- 查看说说：用户说“我/我的/我自己”时传 `target_scope="current_user"`；说“你/你自己/你的”时传 `target_scope="bot_self"`（兼容 `self`）；有明确 QQ 号时传 `target_scope="explicit_uin"` 和 `target_uin`。用户原话中的明确归属高于模型生成参数，归属确实含糊时再向用户确认。
- “她/他/TA/自己的”必须先有可确认的前指对象：只有已明确指向当前 Bot 人格时才传 `bot_self`；没有明确前指时先向用户确认，不要把性别、人设或昵称当作 QQ 身份证据。
- 用户提到“今天下午6点多”“昨天 18:20”等发布时间时，把原话放进 `time_hint`；工具会按作者和时间共同匹配，不要退化成无条件查看最新一条。
- 用户问自己是否在 Bot 动态下留言，目标仍是 `target_scope=bot_self`；用户问 Bot 是否在用户动态下留言，目标是 `target_scope=current_user`。只依据工具返回的 `comments`、`current_user_commented` 和 `bot_commented` 回答；布尔值为 null 或 `comments_complete=false` 时只能说暂未确认。
- 查看结果中的 `identity.owner_role`、`identity.owner_uin`、`identity.current_persona_verified` 是归属事实，优先级高于 `author` 昵称。`current_user`、`third_party` 或共享账号中未经核验的当前人格动态，不得说成当前 Bot 人格亲自发布或经历过。
- 调用前不要先说“看到了/原来是/我还真发了”，也不要连续发送多个“让我看看”；直接调用一次，等结果后再自然回答。只有 `status=success` 且 `target_verified=true` 才能确认看到了目标动态。
- 发布说说：使用 `pc_qzone_publish_feed`。必须把最终要发布的正文放进 `text` 参数,例如 `{"text":"今天想慢一点。"}`；如需带图,可传 `{"text":"配图说说","images":["本地图片路径或图片URL"]}`；如果用户明确要求“发布刚才/最近生成的生活说说草稿”,可传 `{"use_latest_draft":true}`；不要空调用,不要把草稿当作已发布。
- 用户明确说“我刚刚给你评论了”“回复我刚才在你空间的评论”时，使用 `pc_qzone_reply_my_comment`。把用户记得的评论关键词放进 `comment_hint`；只有工具返回 `status=replied` 才能说已经回复。返回 `ambiguous`、`not_found` 或 `skipped` 时如实说明，不要猜测或回复错评论。
- 用户说“你发的说说/你刚发了什么/我看到你发的动态”时，“你”指 Bot 自己，不是当前用户。优先直接依据下方 Bot 自己的发布记录回答，不要反问用户内容，也不要让用户自己去看；需要查看时使用 `target_scope="bot_self"`，不要把对象不明的查看结果偷换成当前用户或 Bot。
- 发布内容必须服从当前人格与世界观,但不要泄露私聊隐私、内部状态数值、关系网资料或插件实现。
- 工具返回 `auth_required`、`target_mismatch`、`target_unverified`、`invalid_time_hint`、`not_found_time`、`empty` 或 `error` 时，简短说明对应原因，本轮不要用同一条件重复调用；不要假装已经发布、看到、评论或点赞。
""".strip()
        return prompt_section(
            key="tools.qzone",
            title="QQ 空间动态工具",
            source="tools",
            content=body,
        )

    def _qzone_tool_instruction(
        self,
        event: AstrMessageEvent | None = None,
        *,
        include_recent_context: bool = True,
    ) -> str:
        section = self._qzone_tool_instruction_prompt_section(event)
        if section is None:
            return ""
        rendered = _render_tool_prompt_section_labeled(section)
        if not include_recent_context:
            return rendered
        sections = self._qzone_tool_prompt_sections(event)
        if len(sections) <= 1:
            return rendered
        recent = render_prompt_sections(
            sections[1:],
            mode=PromptRenderMode.LABELED_BLOCK,
        )
        return f"{rendered}\n\n{recent}".strip()

    def _qzone_tool_prompt_sections(
        self,
        event: AstrMessageEvent | None = None,
    ) -> list[PromptSection]:
        instruction = self._qzone_tool_instruction_prompt_section(event)
        if instruction is None:
            return []
        sections = [instruction]
        context_getter = getattr(
            self,
            "_qzone_recent_self_publish_chat_prompt_section",
            None,
        )
        if callable(context_getter):
            recent_context = context_getter()
            if isinstance(recent_context, PromptSection) and str(
                recent_context.content or ""
            ).strip():
                sections.append(recent_context)
        return sections

    @staticmethod
    def _qzone_view_target_error_message(error: str) -> str:
        messages = {
            "missing_target": "无法确认要查看谁的 QQ 空间。请明确是 Bot 自己、当前用户，还是提供目标 QQ 号。",
            "missing_target_uin": "查看指定 QQ 空间时缺少 target_uin。",
            "invalid_target_uin": "target_uin 不是合法 QQ 号。",
            "invalid_legacy_user_id": "user_id 不是合法 QQ 号。",
            "conflicting_target_uin": "target_uin 与 user_id 指向不同 QQ 号。",
            "scope_target_conflict": "target_scope 与指定 QQ 号不一致。",
            "bot_uin_unavailable": "无法获取 Bot 当前登录的 QQ 号，不能确认 Bot 自己的空间。",
            "sender_uin_unavailable": "无法获取当前用户的 QQ 号，不能确认用户自己的空间。",
            "invalid_target_scope": "target_scope 无效。",
        }
        return messages.get(str(error or ""), "QQ 空间查看对象无法确认。")

    @staticmethod
    def _qzone_view_owner_guard(owner_role: str) -> str:
        guards = {
            "bot_self": "这条动态已按 UIN 确认为 Bot 自己的动态；只能按 Bot 自身经历表述。",
            "current_user": "这条动态已按 UIN 确认为当前用户的动态；不得表述为 Bot 自己发过或经历过。",
            "third_party": "这条动态已按 UIN 确认为第三方的动态；不得表述为 Bot 或当前用户自己的经历。",
            "shared_identity": "Bot 与当前用户使用同一 UIN，无法可靠区分人称；不要使用“我/你”的归属表述。",
            "identity_mismatch": "返回动态作者与请求目标 UIN 不一致，已停止处理。",
            "identity_unverified": "返回动态缺少可验证的作者 UIN，已停止处理。",
        }
        return guards.get(str(owner_role or ""), "QQ 空间动态归属无法确认。")

    @staticmethod
    def _qzone_view_normalize_post_text(value: Any) -> str:
        return re.sub(r"\s+", "", html.unescape(str(value or ""))).strip().casefold()

    def _qzone_view_persona_publish_match_basis(
        self,
        post: Any,
        active_persona: str,
    ) -> str:
        if not active_persona:
            return ""
        profile_data: Any = None
        profiles = getattr(self, "_persona_data_profiles", None)
        if isinstance(profiles, dict):
            profile_data = profiles.get(active_persona)
            if not isinstance(profile_data, dict):
                return ""
        else:
            profile_data = getattr(self, "data", None)
        state = (
            profile_data.get("qzone_integration")
            if isinstance(profile_data, dict)
            else None
        )
        records = (
            state.get("recent_life_publish_texts")
            if isinstance(state, dict)
            else None
        )
        if not isinstance(records, list):
            return ""

        post_ids = {
            _single_line(getattr(post, key, ""), 120)
            for key in ("tid", "fid")
        }
        post_ids.discard("")
        post_text = self._qzone_view_normalize_post_text(
            getattr(post, "text", "") or getattr(post, "rt_con", "")
        )
        post_time = _safe_float(
            getattr(post, "create_time", 0) or getattr(post, "abstime", 0),
            0,
        )
        for record in reversed(records):
            if not isinstance(record, dict) or record.get("verified") is not True:
                continue
            recorded_tid = _single_line(record.get("tid"), 120)
            if recorded_tid and recorded_tid in post_ids:
                return "verified_publish_tid"
            recorded_text = self._qzone_view_normalize_post_text(record.get("text"))
            if not post_text or recorded_text != post_text or len(post_text) < 8:
                continue
            # A known-but-different feed id wins over coincidentally equal text.
            if recorded_tid and post_ids:
                continue
            recorded_at = _safe_float(record.get("at"), 0)
            if post_time <= 0 or recorded_at <= 0:
                continue
            if abs(post_time - recorded_at) > 15 * 60:
                continue
            return "verified_publish_text"
        return ""

    def _qzone_view_identity_payload(
        self,
        target: QzoneViewTarget,
        post: Any,
        *,
        event: AstrMessageEvent | None = None,
    ) -> dict[str, Any]:
        owner_uin = getattr(post, "uin", "")
        normalized_owner = normalize_qzone_uin(owner_uin)
        owner_role = classify_qzone_view_owner(target, normalized_owner)
        multi_persona = bool(getattr(self, "enable_multi_persona_mode", False))
        active_getter = getattr(self, "_active_persona_scope", None)
        active_persona = ""
        if callable(active_getter):
            try:
                active_persona = _single_line(active_getter(), 96)
            except Exception:
                active_persona = ""
        if not active_persona and event is not None:
            active_persona = _single_line(
                getattr(event, "private_companion_persona_id", ""),
                96,
            )
        persona_match_basis = ""
        if owner_role == "bot_self" and multi_persona and active_persona:
            persona_match_basis = self._qzone_view_persona_publish_match_basis(
                post,
                active_persona,
            )
        current_persona_verified = bool(
            owner_role == "bot_self"
            and (not multi_persona or bool(persona_match_basis))
        )
        if owner_role == "bot_self" and not multi_persona:
            persona_match_basis = "single_persona_account"
        pronoun_safe = qzone_view_owner_is_pronoun_safe(owner_role)
        if owner_role == "bot_self" and not current_persona_verified:
            pronoun_safe = False
        if owner_role in {"identity_mismatch", "identity_unverified", "shared_identity"}:
            memory_policy = "not_recorded"
        elif owner_role in {"current_user", "third_party"}:
            memory_policy = "external_observation_only"
        elif owner_role == "bot_self" and current_persona_verified:
            memory_policy = "verified_persona_observation"
        else:
            memory_policy = "shared_account_observation"
        response_guard = self._qzone_view_owner_guard(owner_role)
        if owner_role == "bot_self" and not current_persona_verified:
            response_guard = (
                "这条动态只核验为多人格共享的 Bot 登录 QQ 账号动态，尚未核验为当前人格发布；"
                "不得表述为当前人格亲自发布或经历过。"
            )
        return {
            "requested_scope": target.scope,
            "target_uin": str(target.target_uin) if target.target_uin else "",
            "owner_uin": str(normalized_owner) if normalized_owner else "",
            "owner_role": owner_role,
            "owner_matches_target": bool(normalized_owner and normalized_owner == target.target_uin),
            "pronoun_safe": pronoun_safe,
            "current_persona_verified": current_persona_verified,
            "persona_verification_basis": persona_match_basis,
            "account_scope": "shared" if multi_persona else "single_persona",
            "memory_policy": memory_policy,
            "response_guard": response_guard,
        }

    @staticmethod
    def _qzone_note_view_memory_boundary(event: AstrMessageEvent | None, identity: dict[str, Any]) -> None:
        """Do not turn a fetched public feed into Bot autobiographical memory."""
        if event is None:
            return
        observations = getattr(event, "_private_companion_qzone_view_observations", None)
        if not isinstance(observations, list):
            observations = []
            setattr(event, "_private_companion_qzone_view_observations", observations)
        observations.append(
            {
                "requested_scope": str(identity.get("requested_scope") or ""),
                "owner_role": str(identity.get("owner_role") or ""),
                "owner_matches_target": bool(identity.get("owner_matches_target")),
                "current_persona_verified": bool(
                    identity.get("current_persona_verified")
                ),
                "memory_policy": str(identity.get("memory_policy") or ""),
            }
        )
        del observations[:-8]

    @staticmethod
    def _qzone_view_normalize_uin(value: Any) -> str:
        text = _single_line(value, 80).lstrip("oO")
        return text if text.isdigit() else ""

    @staticmethod
    def _qzone_view_clock_number(value: Any) -> int | None:
        text = _single_line(value, 8).translate(
            str.maketrans({"〇": "零", "两": "二", "兩": "二"})
        )
        if not text:
            return None
        if text.isdigit():
            return int(text)
        digits = {
            "零": 0,
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        if "十" in text:
            if text.count("十") != 1:
                return None
            left, _, right = text.partition("十")
            if left and left not in digits:
                return None
            if right and right not in digits:
                return None
            return (digits.get(left, 1) * 10) + digits.get(right, 0)
        if all(character in digits for character in text):
            return int("".join(str(digits[character]) for character in text))
        return None

    def _qzone_view_target_scope(
        self,
        event: AstrMessageEvent,
        *,
        user_id: Any = "",
        target_scope: Any = "",
        target_uin: Any = "",
    ) -> tuple[str, bool]:
        requested_user = _single_line(user_id, 80)
        requested_target = _single_line(target_uin, 80)
        requested_scope = _single_line(target_scope, 40)

        inbound = _single_line(getattr(event, "message_str", ""), 500)
        user_owns_post = bool(
            re.search(r"(?:我自己(?:的)?|我的)(?:\s*QQ)?(?:空间|动态|说说)", inbound, flags=re.I)
            or re.search(r"我.{0,14}(?:发了|发的|发布的).{0,10}(?:动态|说说)", inbound, flags=re.I)
            or re.search(r"你.{0,12}(?:在|给).{0,8}我(?:的)?(?:动态|说说).{0,8}(?:回复|评论|留言)", inbound, flags=re.I)
        )
        bot_owns_post = bool(
            re.search(r"(?:你自己(?:的)?|你的)(?:\s*QQ)?(?:空间|动态|说说)", inbound, flags=re.I)
            or re.search(r"你.{0,18}(?:发了|发的|发布了|发布的).{0,10}(?:动态|说说)", inbound, flags=re.I)
            or re.search(r"我.{0,18}给你.{0,10}(?:回复|评论|留言)", inbound, flags=re.I)
        )

        # Explicit ownership in the user's wording outranks generated tool args.
        if user_owns_post and not bot_owns_post:
            return "current_user", True
        if bot_owns_post and not user_owns_post:
            return "bot_self", True

        normalized_scope = normalize_qzone_view_target_scope(requested_scope)
        if requested_scope and not normalized_scope:
            return requested_scope, False
        if not requested_scope:
            argument_alias_scope = normalize_qzone_view_target_scope(
                requested_target or requested_user
            )
            if argument_alias_scope and argument_alias_scope != "explicit_uin":
                return argument_alias_scope, True
        if normalized_scope == "auto":
            normalized_scope = "explicit_uin" if (requested_target or requested_user) else "ambiguous"
        if not normalized_scope:
            normalized_scope = "explicit_uin" if (requested_target or requested_user) else "ambiguous"
        return normalized_scope, False

    def _qzone_view_time_filter(self, value: Any) -> dict[str, Any]:
        text = _single_line(value, 160)
        if not text:
            return {}
        now_getter = getattr(self, "_environment_now", None)
        try:
            now = now_getter() if callable(now_getter) else datetime.now()
        except Exception:
            now = datetime.now()
        target_day = now.date()
        has_day = False
        if "前天" in text:
            target_day = (now - timedelta(days=2)).date()
            has_day = True
        elif "昨天" in text or "昨日" in text:
            target_day = (now - timedelta(days=1)).date()
            has_day = True
        elif "大后天" in text:
            target_day = (now + timedelta(days=3)).date()
            has_day = True
        elif "后天" in text:
            target_day = (now + timedelta(days=2)).date()
            has_day = True
        elif "明天" in text or "明日" in text or "明晚" in text:
            target_day = (now + timedelta(days=1)).date()
            has_day = True
        elif "今天" in text or "今日" in text or "今晚" in text:
            has_day = True
        else:
            full_date = re.search(r"(?<!\d)(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})(?:日)?(?!\d)", text)
            short_date = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日?(?!\d)", text)
            try:
                if full_date:
                    target_day = datetime(
                        int(full_date.group(1)),
                        int(full_date.group(2)),
                        int(full_date.group(3)),
                    ).date()
                    has_day = True
                elif short_date:
                    target_day = datetime(
                        now.year,
                        int(short_date.group(1)),
                        int(short_date.group(2)),
                    ).date()
                    has_day = True
            except ValueError:
                return {"parse_error": "invalid_date", "source": text}

        period_pattern = r"凌晨|清晨|早上|上午|中午|下午|傍晚|晚上|夜里"
        number_pattern = r"[零〇一二两兩三四五六七八九十]{1,3}"
        boundary_pattern = r"(?![\d零〇一二两兩三四五六七八九十半刻多分])"
        period_hint_match = re.search(period_pattern, text)
        period_hint = (
            period_hint_match.group(0)
            if period_hint_match
            else "晚上"
            if "今晚" in text or "明晚" in text
            else ""
        )
        hour_left_boundary = r"(?<![\d零〇一二两兩三四五六七八九十])"
        colon_match = re.search(
            rf"(?:(?P<period>{period_pattern})\s*)?"
            rf"{hour_left_boundary}(?P<hour>(?:[01]?\d|2[0-3]|{number_pattern}))\s*[:：]\s*"
            rf"(?P<minute>[0-5]?\d|{number_pattern})\s*分?{boundary_pattern}",
            text,
        )
        point_match = re.search(
            rf"(?:(?P<period>{period_pattern})\s*)?"
            rf"{hour_left_boundary}(?P<hour>(?:[01]?\d|2[0-3]|{number_pattern}))\s*(?:点|时)\s*"
            rf"(?:(?P<minute_word>半|一刻|三刻)|"
            rf"(?P<minute>[0-5]?\d|{number_pattern})\s*分?|"
            rf"(?P<hour_more>多))?{boundary_pattern}",
            text,
        )
        time_match = colon_match or point_match
        explicit_clock_marker = bool(
            re.search(
                rf"(?:\d|{number_pattern})\s*(?:点|时|[:：])",
                text,
            )
        )
        minute_of_day: int | None = None
        tolerance_minutes = 180
        hour_window: tuple[int, int] | None = None
        if time_match:
            period = time_match.group("period") or period_hint
            hour = self._qzone_view_clock_number(time_match.group("hour"))
            groups = time_match.groupdict()
            minute_word = groups.get("minute_word") or ""
            if minute_word == "半":
                minute = 30
            elif minute_word == "一刻":
                minute = 15
            elif minute_word == "三刻":
                minute = 45
            else:
                minute = self._qzone_view_clock_number(groups.get("minute") or "0")
            if hour is None or minute is None or hour > 23 or minute > 59:
                return {"parse_error": "invalid_clock", "source": text}
            if period in {"下午", "傍晚", "晚上", "夜里"} and hour < 12:
                hour += 12
            elif period == "中午" and hour < 11:
                hour += 12
            elif period == "凌晨" and hour == 12:
                hour = 0
            minute_of_day = hour * 60 + minute
            if groups.get("hour_more"):
                hour_window = (hour * 60, hour * 60 + 59)
                tolerance_minutes = 59
            else:
                tolerance_minutes = 90 if re.search(r"(?:左右|前后|大概|约)", text) else 45
        elif explicit_clock_marker:
            return {"parse_error": "invalid_clock", "source": text}
        elif period_hint:
            hour_window = {
                "凌晨": (0, 5 * 60 + 59),
                "清晨": (5 * 60, 8 * 60 + 59),
                "早上": (6 * 60, 9 * 60 + 59),
                "上午": (6 * 60, 11 * 60 + 59),
                "中午": (11 * 60, 13 * 60 + 59),
                "下午": (12 * 60, 17 * 60 + 59),
                "傍晚": (17 * 60, 19 * 60 + 59),
                "晚上": (18 * 60, 23 * 60 + 59),
                "夜里": (20 * 60, 23 * 60 + 59),
            }.get(period_hint)
        if not has_day and minute_of_day is None and hour_window is None:
            if re.search(
                r"(?:大后天|后天|明天|明日|明晚|前天|昨天|昨日|今天|今日|今晚|"
                r"(?:上|下|这|本)?(?:周|星期|礼拜)[一二三四五六日天]?)",
                text,
            ):
                return {"parse_error": "unsupported_time_expression", "source": text}
            return {}
        return {
            "date": target_day.strftime("%Y-%m-%d"),
            "minute_of_day": minute_of_day,
            "tolerance_minutes": tolerance_minutes,
            "hour_window": hour_window,
            "source": text,
        }

    def _qzone_view_post_datetime(self, post: Any) -> datetime | None:
        timestamp = _safe_float(
            getattr(post, "create_time", 0) or getattr(post, "abstime", 0),
            0,
        )
        if timestamp <= 0:
            return None
        converter = getattr(self, "_environment_fromtimestamp", None)
        try:
            return converter(timestamp) if callable(converter) else datetime.fromtimestamp(timestamp)
        except Exception:
            return None

    async def _pc_qzone_view_feed_impl(
        self,
        event: AstrMessageEvent,
        user_id: str = "",
        target_scope: str = "",
        target_uin: str = "",
        pos: int = 0,
        like: bool = False,
        reply: bool = False,
        selector: str = "",
        fid: str = "",
        time_hint: str = "",
        **kwargs: Any,
    ) -> str:
        availability = getattr(self, "_qzone_available", None)
        if callable(availability) and not availability(event):
            supported = getattr(self, "_qzone_platform_supported", None)
            if callable(supported) and not supported(event):
                message_getter = getattr(self, "_qzone_platform_unavailable_message", None)
                message = message_getter() if callable(message_getter) else "当前平台不支持 QQ 空间"
                return json.dumps({"status": "unsupported_platform", "message": message}, ensure_ascii=False)
            return json.dumps({"status": "disabled", "message": "QQ 空间动态层未启用"}, ensure_ascii=False)
        if not callable(availability) and not self.enable_qzone_integration:
            return json.dumps({"status": "disabled", "message": "QQ 空间动态层未启用"}, ensure_ascii=False)
        try:
            def alias(*names: str) -> Any:
                for name in names:
                    value = kwargs.get(name)
                    if value not in (None, ""):
                        return value
                return ""

            requested_user = user_id or alias("legacy_user_id")
            requested_target_uin = target_uin or alias(
                "target_id",
                "target",
                "qq",
                "uin",
            )
            requested_scope = target_scope or alias("scope", "owner", "target_type")
            requested_selector = selector or alias("post_selector", "selection")
            requested_fid = fid or alias("post_id", "tid", "feed_id")
            requested_time = time_hint or alias(
                "time",
                "datetime",
                "date",
                "date_time",
                "published_at",
                "publish_time",
                "time_range",
            )
            explicit_time_argument = bool(_single_line(requested_time, 160))
            if not requested_time:
                selector_time = self._qzone_view_time_filter(requested_selector)
                inbound_message = getattr(event, "message_str", "")
                inbound_time = self._qzone_view_time_filter(inbound_message)
                requested_time = (
                    requested_selector
                    if selector_time
                    else inbound_message
                    if inbound_time
                    else ""
                )
            time_filter = self._qzone_view_time_filter(requested_time)
            if time_filter.get("parse_error") or (explicit_time_argument and not time_filter):
                return json.dumps(
                    {
                        "status": "invalid_time_hint",
                        "success": False,
                        "message": "无法可靠识别指定的发布时间，请换成“今天下午六点半”或“2026-08-04 18:30”等表达。",
                        "requested_time": time_filter.get("source", "") or _single_line(requested_time, 160),
                        "target_verified": False,
                        "must_not_claim_viewed": True,
                        "should_retry": False,
                        "final_response_instruction": "请用户确认发布时间，不要退化为查看最新动态，也不要声称已看到目标动态。",
                    },
                    ensure_ascii=False,
                )
            aliased_position = alias("position", "index")
            requested_pos = _safe_int(
                aliased_position if aliased_position not in (None, "") else pos,
                0,
                0,
            )
            try:
                sender_uin = event.get_sender_id() if event is not None else ""
            except Exception:
                sender_uin = ""
            effective_scope, semantic_override = self._qzone_view_target_scope(
                event,
                user_id=requested_user,
                target_scope=requested_scope,
                target_uin=requested_target_uin,
            )
            effective_target_uin = "" if semantic_override else requested_target_uin
            effective_legacy_user = "" if semantic_override else requested_user
            preliminary_target = resolve_qzone_view_target(
                target_scope=effective_scope,
                target_uin=effective_target_uin,
                legacy_user_id=effective_legacy_user,
                sender_uin=sender_uin,
            )
            if preliminary_target.error and preliminary_target.error != "bot_uin_unavailable":
                status = "needs_target" if preliminary_target.error in {"missing_target", "missing_target_uin"} else "invalid_target"
                return json.dumps(
                    {
                        "status": status,
                        "success": False,
                        "message": self._qzone_view_target_error_message(preliminary_target.error),
                        "target_verified": False,
                        "must_not_claim_viewed": True,
                        "should_retry": False,
                        "identity": {
                            "requested_scope": preliminary_target.scope,
                            "memory_policy": "not_recorded",
                        },
                    },
                    ensure_ascii=False,
                )
            cookie_header = await self._qzone_get_cookies(event)
            ctx = self._qzone_context_from_cookies(cookie_header)
            bot_uin = self._qzone_view_normalize_uin(ctx.get("uin"))
            target = resolve_qzone_view_target(
                target_scope=effective_scope,
                target_uin=effective_target_uin,
                legacy_user_id=effective_legacy_user,
                bot_uin=ctx.get("uin"),
                sender_uin=sender_uin,
            )
            if not target.resolved:
                status = "needs_target" if target.error in {"missing_target", "missing_target_uin"} else "invalid_target"
                return json.dumps(
                    {
                        "status": status,
                        "success": False,
                        "message": self._qzone_view_target_error_message(target.error),
                        "target_verified": False,
                        "must_not_claim_viewed": True,
                        "should_retry": False,
                        "identity": {
                            "requested_scope": target.scope,
                            "memory_policy": "not_recorded",
                        },
                    },
                    ensure_ascii=False,
                )
            selection = parse_qzone_post_selection(
                user_id=str(target.target_uin),
                selector=_single_line(requested_selector, 120),
                pos=requested_pos,
                fid=_single_line(requested_fid, 120),
            )
            selected_uin = normalize_qzone_uin(selection.target_id)
            if selected_uin != target.target_uin:
                return json.dumps(
                    {
                        "status": "invalid_target",
                        "success": False,
                        "message": "selector 中的 QQ 号与已确认的查看对象不一致。",
                        "target_verified": False,
                        "must_not_claim_viewed": True,
                        "should_retry": False,
                        "identity": {
                            "requested_scope": target.scope,
                            "target_uin": str(target.target_uin),
                            "memory_policy": "not_recorded",
                        },
                    },
                    ensure_ascii=False,
                )
            if selection.fid:
                candidates = await self._qzone_query_feeds(
                    event,
                    target_id=selection.target_id or None,
                    pos=0,
                    num=20,
                    with_detail=True,
                    cookie_header=cookie_header,
                )
                posts = [
                    item for item in candidates
                    if str(getattr(item, "tid", "") or "") == selection.fid
                    or str(self._qzone_post_value(item, "fid", "") or "") == selection.fid
                ][:1]
            elif selection.is_last:
                candidates = await self._qzone_query_feeds(
                    event,
                    target_id=selection.target_id or None,
                    pos=0,
                    num=10,
                    with_detail=True,
                    cookie_header=cookie_header,
                )
                posts = candidates[-1:] if candidates else []
            elif time_filter and selection.pos == 0:
                candidates = await self._qzone_query_feeds(
                    event,
                    target_id=selection.target_id or None,
                    pos=0,
                    num=30,
                    with_detail=True,
                    cookie_header=cookie_header,
                )
                dated_candidates: list[tuple[Any, datetime]] = []
                for candidate in candidates:
                    created = self._qzone_view_post_datetime(candidate)
                    if created is not None and created.strftime("%Y-%m-%d") == time_filter["date"]:
                        dated_candidates.append((candidate, created))
                requested_minute = time_filter.get("minute_of_day")
                hour_window = time_filter.get("hour_window")
                if isinstance(hour_window, tuple) and len(hour_window) == 2:
                    start_minute, end_minute = hour_window
                    dated_candidates = [
                        item
                        for item in dated_candidates
                        if int(start_minute)
                        <= item[1].hour * 60 + item[1].minute
                        <= int(end_minute)
                    ]
                if requested_minute is None:
                    dated_candidates.sort(key=lambda item: item[1], reverse=True)
                    posts = [dated_candidates[0][0]] if dated_candidates else []
                else:
                    dated_candidates.sort(
                        key=lambda item: abs((item[1].hour * 60 + item[1].minute) - int(requested_minute))
                    )
                    nearest = dated_candidates[0] if dated_candidates else None
                    nearest_diff = (
                        abs((nearest[1].hour * 60 + nearest[1].minute) - int(requested_minute))
                        if nearest
                        else 10**9
                    )
                    posts = [nearest[0]] if nearest and nearest_diff <= int(time_filter["tolerance_minutes"]) else []
                if not posts:
                    available_times = [item[1].strftime("%Y-%m-%d %H:%M") for item in dated_candidates[:8]]
                    return json.dumps(
                        {
                            "status": "not_found_time",
                            "success": False,
                            "message": "没有找到发布时间符合该时间提示的说说。",
                            "requested_time": time_filter.get("source", ""),
                            "target_scope": target.scope,
                            "available_times": available_times,
                            "must_not_claim_viewed": True,
                            "should_retry": False,
                            "final_response_instruction": "如实说明没有匹配到该时间的动态，不要把最新一条或其他作者的动态冒充目标。",
                        },
                        ensure_ascii=False,
                    )
            else:
                posts = await self._qzone_query_feeds(
                    event,
                    target_id=selection.target_id or None,
                    pos=max(0, int(selection.pos or 0)),
                    num=1,
                    with_detail=True,
                    cookie_header=cookie_header,
                )
            if not posts:
                return json.dumps(
                    {
                        "status": "empty",
                        "success": False,
                        "message": "查询结果为空",
                        "target_scope": target.scope,
                        "target_verified": False,
                        "must_not_claim_viewed": True,
                        "should_retry": False,
                        "identity": {
                            "requested_scope": target.scope,
                            "target_uin": str(target.target_uin),
                            "memory_policy": "not_recorded",
                        },
                    },
                    ensure_ascii=False,
                )
            post = posts[0]
            post_uin = self._qzone_view_normalize_uin(getattr(post, "uin", ""))
            identity = self._qzone_view_identity_payload(
                target,
                post,
                event=event,
            )
            owner_role = str(identity.get("owner_role") or "")
            if owner_role in {"identity_mismatch", "identity_unverified", "shared_identity"}:
                status = {
                    "identity_mismatch": "target_mismatch",
                    "identity_unverified": "target_unverified",
                    "shared_identity": "identity_ambiguous",
                }.get(owner_role, owner_role)
                return json.dumps(
                    {
                        "status": status,
                        "success": False,
                        "message": str(identity.get("response_guard") or "QQ 空间动态归属无法确认。"),
                        "target_scope": target.scope,
                        "target_verified": False,
                        "expected_uin": str(target.target_uin or ""),
                        "observed_uin": post_uin,
                        "observed_author": _single_line(getattr(post, "name", ""), 60),
                        "must_not_claim_viewed": True,
                        "should_retry": False,
                        "identity": identity,
                        "final_response_instruction": str(identity.get("response_guard") or ""),
                    },
                    ensure_ascii=False,
                )
            self._qzone_note_view_memory_boundary(event, identity)
            action_msg = ""
            if reply:
                comment = await self._qzone_comment_post(event, post)
                action_msg = f"已评论：{comment}"
            like_result: dict[str, Any] | None = None
            if like:
                like_result = await self._qzone_like_post(event, post)
                like_text = "已点赞" if like_result.get("verified") else "点赞请求已受理，等待 QQ 空间同步"
                action_msg = (action_msg + f"；{like_text}") if action_msg else like_text
            all_comments = list(getattr(post, "comments", []) or [])
            comments_payload: list[dict[str, Any]] = []
            for comment in all_comments[:30]:
                comment_uin = self._qzone_view_normalize_uin(getattr(comment, "uin", ""))
                comment_time = _safe_float(getattr(comment, "create_time", 0), 0)
                comments_payload.append(
                    {
                        "comment_id": _single_line(getattr(comment, "comment_id", ""), 100),
                        "author": _single_line(getattr(comment, "name", ""), 60),
                        "uin": comment_uin,
                        "text": _single_line(getattr(comment, "content", ""), 240),
                        "published_at": self._qzone_post_time_text(comment_time) if comment_time > 0 else "",
                    }
                )
            raw_post = getattr(post, "raw", None)
            reported_comment_count = len(all_comments)
            if isinstance(raw_post, dict):
                for key in (
                    "cmtnum",
                    "commentnum",
                    "comment_num",
                    "commentcount",
                    "comment_count",
                    "replynum",
                ):
                    if key in raw_post:
                        reported_comment_count = max(
                            reported_comment_count,
                            _safe_int(raw_post.get(key), 0, 0),
                        )
            comments_complete = reported_comment_count <= len(comments_payload)
            try:
                requester_uin = self._qzone_view_normalize_uin(event.get_sender_id())
            except Exception:
                requester_uin = ""
            published_ts = _safe_float(
                getattr(post, "create_time", 0) or getattr(post, "abstime", 0),
                0,
            )
            return json.dumps(
                {
                    "status": "success",
                    "success": True,
                    "action": action_msg,
                    "like_result": like_result or {},
                    "author": _single_line(getattr(post, "name", ""), 60),
                    "uin": post_uin,
                    "target_scope": target.scope,
                    "target_verified": True,
                    "identity": identity,
                    "text": _single_line(getattr(post, "text", "") or getattr(post, "rt_con", ""), 300),
                    "images": list(getattr(post, "images", []) or [])[:6],
                    "fid": _single_line(getattr(post, "fid", "") or getattr(post, "tid", ""), 120),
                    "published_at": self._qzone_post_time_text(published_ts) if published_ts > 0 else "",
                    "published_ts": int(published_ts) if published_ts > 0 else 0,
                    "requested_time": time_filter.get("source", "") if time_filter else "",
                    "comments_loaded": comments_complete or bool(comments_payload),
                    "comments_complete": comments_complete,
                    "reported_comment_count": reported_comment_count,
                    "comment_count": len(comments_payload),
                    "comments": comments_payload,
                    "current_user_commented": (
                        True
                        if requester_uin and any(item.get("uin") == requester_uin for item in comments_payload)
                        else False
                        if comments_complete
                        else None
                    ),
                    "bot_commented": (
                        True
                        if bot_uin and any(item.get("uin") == bot_uin for item in comments_payload)
                        else False
                        if comments_complete
                        else None
                    ),
                    "must_not_claim_viewed": False,
                    "should_retry": False,
                    "final_response_instruction": (
                        f"只依据本结果回答。{identity.get('response_guard') or ''}"
                        "评论是否存在只依据 comments/current_user_commented/bot_commented；值为 null 或 comments_complete=false 时只能说暂未确认，不要猜测。"
                    ),
                },
                ensure_ascii=False,
            )
        except Exception as exc:
            message = _single_line(exc, 160)
            auth_required = bool(
                re.search(
                    r"(?:登录态|登录|cookie|p_skey|skey|鉴权|认证|unauthorized|forbidden)",
                    message,
                    flags=re.I,
                )
                and re.search(
                    r"(?:失效|过期|缺失|缺少|为空|失败|未配置|重新绑定|无效|不可用|missing|expired|invalid|unauthorized|forbidden)",
                    message,
                    flags=re.I,
                )
            )
            return json.dumps(
                {
                    "status": "auth_required" if auth_required else "error",
                    "success": False,
                    "message": message or "QQ 空间查询失败",
                    "retryable": not auth_required,
                    "should_retry": False,
                    "must_not_claim_viewed": True,
                    "final_response_instruction": (
                        "QQ 空间登录态已失效；如实说明需要重新绑定 Cookie，本轮不要重复调用，也不要声称已经看到动态或评论。"
                        if auth_required
                        else "如实说明本次查询失败，本轮不要用同一参数连续重试，也不要声称已经看到动态或评论。"
                    ),
                },
                ensure_ascii=False,
            )

    async def _pc_qzone_publish_feed_impl(self, event: AstrMessageEvent, text: str = "", **kwargs) -> str:
        availability = getattr(self, "_qzone_available", None)
        if callable(availability) and not availability(event):
            supported = getattr(self, "_qzone_platform_supported", None)
            if callable(supported) and not supported(event):
                message_getter = getattr(self, "_qzone_platform_unavailable_message", None)
                message = message_getter() if callable(message_getter) else "当前平台不支持 QQ 空间"
                return json.dumps({"status": "unsupported_platform", "success": False, "message": message}, ensure_ascii=False)
            return json.dumps({"status": "disabled", "success": False, "message": "QQ 空间动态层未启用"}, ensure_ascii=False)
        content = _single_line(text or kwargs.get("content") or kwargs.get("message") or kwargs.get("draft"), 300)
        images: list[str] = []
        for key in ("images", "image_paths", "image_urls"):
            value = kwargs.get(key)
            if isinstance(value, (list, tuple)):
                images.extend(str(item).strip() for item in value if str(item or "").strip())
            elif isinstance(value, str) and value.strip():
                images.append(value.strip())
        for key in ("image", "image_path", "image_url", "path"):
            value = kwargs.get(key)
            if isinstance(value, str) and value.strip():
                images.append(value.strip())
        images = list(dict.fromkeys(images))[:9]
        if not content and kwargs.get("use_latest_draft"):
            state = self.data.get("qzone_integration") if isinstance(self.data.get("qzone_integration"), dict) else {}
            content = _single_line(state.get("last_life_publish_draft") or state.get("last_life_publish_text"), 300)
        if not content and not images:
            return json.dumps(
                {
                    "status": "need_text",
                    "success": False,
                    "message": "缺少 text 或 images 参数。请把要发布的说说正文作为 text 传入；如需带图,传 images；若要发布最近自动生成的生活草稿,传 use_latest_draft=true。",
                    "required_args": {"text": "要发布到 QQ 空间的说说正文", "images": "可选，本地图片路径或图片URL列表"},
                },
                ensure_ascii=False,
            )
        result = await self._publish_qzone_text(content, event, images=images, auto_generate_image=True)
        return json.dumps({"status": "success" if result.get("success") else "error", **result}, ensure_ascii=False)
