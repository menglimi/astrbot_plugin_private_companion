# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import html
import inspect
import json
import os
import random
import re
import shutil
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from astrbot.api.event import AstrMessageEvent
from astrbot.api.event import MessageChain
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
try:
    from astrbot.api.message_components import At, Plain
except ImportError:
    from astrbot.api.message_components import At, Plain

from .helpers import (
    _missing_optional_model_dependency,
    _now_ts,
    _path_text,
    _photo_group_request_matches,
    _redact_outbound_secrets,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
)
from .memo_notes import apply_memo_note_action, memo_note_sort_key, normalize_memo_note
from .persona_config import runtime_persona_setting
from .conversation_prompt_section import prompt_section
from .owned_reaction_asset_catalog import OwnedReactionAssetCatalog
from .qzone_selection import (
    QzoneViewTarget,
    classify_qzone_view_owner,
    normalize_qzone_uin,
    normalize_qzone_view_target_scope,
    parse_qzone_post_selection,
    qzone_view_owner_is_pronoun_safe,
    resolve_qzone_view_target,
)
from .reaction_expression import (
    append_reaction_expression_outcome,
    classify_reaction_expression_feedback,
    ensure_reaction_expression_state,
    evaluate_reaction_expression_gate,
    reaction_expression_explicit_opt_out,
    reaction_expression_explicit_request,
    reaction_expression_auto_disabled,
    reaction_expression_high_frequency,
    reaction_expression_normalize_probability,
    sync_reaction_expression_auto_preference,
    normalize_reaction_expression_intent,
    reaction_expression_effective_probability,
    reaction_expression_image_key,
    reaction_expression_image_keys,
    reaction_expression_reservation_owned,
    reaction_expression_selection_preferences,
    reaction_expression_scope_state,
    record_reaction_expression_feedback,
    record_reaction_expression_sent,
    release_reaction_expression_image,
    release_reaction_expression_reservation,
    reserve_reaction_expression_image,
    reserve_reaction_expression_intent,
)
from .reaction_asset_library import ReactionAssetLibrary, get_reaction_asset_library
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


PHOTO_TOOL_SILENT_SENTINEL = "[[PC_PHOTO_SENT_NO_FOLLOWUP]]"
_PHOTO_TOOL_REDACTED_LOCAL_PATH = "[本地路径已隐藏]"
_PHOTO_TOOL_WINDOWS_PATH_START_RE = re.compile(
    r"(?<!\w)(?:[A-Za-z]:[\\/]|\\\\(?=[^\\/]))"
)
_PHOTO_TOOL_POSIX_PATH_START_RE = re.compile(
    r"(?<![\w/])/(?=(?:"
    r"(?:Users|home|tmp|var|etc|opt|srv|root|mnt|run|private|usr|workspace|workspaces|app|data)/"
    r"|(?:[^/\s]+/){2,}[^/\s]+"
    r"|[^/\s]+/[^/\s]+\.[A-Za-z0-9]{1,12}(?:\s|$|[),;，；。])"
    r"))",
    flags=re.I,
)
_PHOTO_TOOL_RELATIVE_PATH_START_RE = re.compile(
    r"(?<![A-Za-z0-9.:/\\])(?:\.{1,2}[\\/])?(?:[^\\/\s,，;；]+[\\/]){2,}"
    r"[^\\/\s,，;；]+\.[A-Za-z0-9]{1,12}",
    flags=re.I,
)
_PHOTO_TOOL_HTTP_URL_RE = re.compile(
    r"https?://[^\s<>\[\]{}\"']+",
    flags=re.I,
)
_CURRENT_MEDIA_IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".jfif", ".avif"}
)
_CURRENT_MEDIA_MAX_BYTES = 32 * 1024 * 1024
_CURRENT_MEDIA_MAX_AGE_SECONDS = 30 * 60
_REACTION_LOG_STAGES = frozenset(
    {
        "gate",
        "authorization",
        "decision",
        "lookup",
        "reservation",
        "attachment",
        "delivery",
        "intent",
        "feedback",
        "degrade",
    }
)
_REACTION_LOG_DECISIONS = frozenset(
    {
        "allow",
        "deny",
        "skip",
        "hit",
        "miss",
        "prepared",
        "sent",
        "failed",
        "uncertain",
        "accepted",
        "discarded",
        "recorded",
        "scoped",
        "omit",
    }
)
_REACTION_LOG_REASONS = frozenset(
    {
        "allowed",
        "experiment_disabled",
        "provider_unavailable",
        "private_disabled",
        "group_disabled",
        "unknown_disabled",
        "missing_user",
        "probability",
        "cooldown",
        "in_progress",
        "repeated_intent",
        "not_preauthorized",
        "not_authorized",
        "authorization_consumed",
        "authorization_expired",
        "authorization_user_mismatch",
        "authorization_scope_mismatch",
        "send_disabled",
        "missing_visible_text",
        "missing_visible_caption",
        "existing_image",
        "proactive_only",
        "gate",
        "not_found",
        "unavailable",
        "error",
        "lookup_error",
        "missing_query",
        "library_unavailable",
        "missing_file",
        "matched",
        "reservation_lost",
        "duplicate_image",
        "attachment_state_failed",
        "attachment_appended",
        "attachment_prepared",
        "delivered_before_primary",
        "attachment_file_missing",
        "attachment_component_failed",
        "attachment_removed",
        "platform_not_sent",
        "primary_not_delivered",
        "delivery_not_started",
        "awaiting_platform_send",
        "delivered",
        "delivery_uncertain",
        "delivery_failed",
        "append_failed",
        "not_sent",
        "scene_snapshot_failed",
        "usage_mark_failed",
        "intent_extracted",
        "intent_discarded",
        "model_omitted_intent",
        "local_fallback_intent",
        "media_tools_scoped",
        "feedback_private",
        "feedback_group",
        "explicit_opt_out",
        "semantic_cooldown",
    }
)
_REACTION_LOG_STATUSES = frozenset(
    {
        "success",
        "prepared",
        "need_query",
        "unavailable",
        "not_found",
        "error",
        "missing_file",
        "delivery_failed",
        "delivery_uncertain",
        "disabled",
        "missing_user",
        "not_sent",
    }
)
_REACTION_LOG_DELIVERY_CODES = frozenset(
    {
        "current",
        "group",
        "private",
        "blocked",
        "error",
        "platform_sent",
        "delivered",
        "append_failed",
        "attachment_removed",
        "platform_not_sent",
        "attachment_file_missing",
        "attachment_component_failed",
    }
)
_REACTION_LOG_MATCH_BASES = frozenset(
    {"tags_emotions_intents", "provider_score"}
)
_REACTION_LOG_TRIGGER_MODES = frozenset(
    {
        "probability",
        "feedback_bias",
        "semantic_rule",
        "strong_emotion",
        "explicit_opt_out",
    }
)


class LlmToolActionsMixin:
    """Implementation bodies for LLM tools registered in main.py."""

    @staticmethod
    def _character_photo_request_matches(text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        if any(
            marker in compact
            for marker in (
                "腿照",
                "脚照",
                "手照",
                "全身照",
                "半身照",
                "近照",
                "生活照",
                "穿搭照",
            )
        ):
            return True
        return bool(
            re.search(
                r"(?:看看|看下|看一下|想看|要看|让我看看|给我看看|发来看看).{0,10}"
                r"(?:腿|脚|手|脸|全身|半身|穿搭|衣服|样子)",
                compact,
                flags=re.I,
            )
        )

    def _photo_generation_instruction_matches(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        clauses = [
            part
            for part in re.split(
                r"(?:[，,。！？!?；;]+|但是|不过|然而|然后)", compact
            )
            if part
        ] or [compact]
        non_reaction_tokens = (
                "生图",
                "画图",
                "绘图",
                "生成图片",
                "出图",
                "画一张",
                "画张",
                "来张图",
                "来一张图",
                "自拍",
                "拍照",
                "照片",
                "相片",
                "头像",
                "壁纸",
                "改图",
                "修图",
                "重绘",
                "P图",
                "p图",
                "参考图",
                "穿搭图",
                "COS",
                "cosplay",
        )
        generated_reaction = re.compile(
            r"(?:生成|制作|做|画|绘制|设计|重绘).{0,10}"
            r"(?:表情包|贴纸|反应图|梗图)"
            r"|(?:表情包|贴纸|反应图|梗图).{0,10}"
            r"(?:生成|制作|做|画|绘制|设计|重绘)",
            flags=re.I,
        )
        rejected_generation = re.compile(
            r"(?:别|不要|不用).{0,8}(?:生成|制作|做|画|绘制|设计|重绘).{0,10}"
            r"(?:表情包|贴纸|反应图|梗图)",
            flags=re.I,
        )
        for clause in clauses:
            if reaction_expression_explicit_opt_out(clause):
                continue
            if self._character_photo_request_matches(clause):
                return True
            if any(token in clause for token in non_reaction_tokens):
                return True
            if rejected_generation.search(clause):
                continue
            if reaction_expression_explicit_request(clause):
                return True
            if generated_reaction.search(clause):
                return True
            if clause in {"斗图", "来斗图", "开始斗图"}:
                return True
        return False

    def _plaintext_photo_recovery_intent_matches(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        explicit_request = bool(
            re.search(
                r"(?:帮我|给我|替我|想要|想看|要看|拍|生成|画|绘制|做|来|发).{0,10}"
                r"(?:照片|图片|自拍|头像|表情包|贴纸|壁纸|穿搭|腿|脚|手|脸|全身|半身)",
                compact,
                flags=re.I,
            )
        ) or any(token in compact for token in ("改图", "修图", "重绘", "P图", "p图"))
        explanatory = any(token in compact for token in ("解释", "分析", "日志", "代码", "JSON", "json", "工具调用", "为什么"))
        if explanatory and not explicit_request:
            return False
        return explicit_request or self._character_photo_request_matches(compact)

    def _photo_generation_runtime_available(self) -> bool:
        """Require the optional Image runtime only on the production host."""
        required = getattr(self, "_image_companion_required", None)
        if not callable(required):
            return True
        try:
            if not bool(required()):
                return True
        except Exception:
            return False
        available = getattr(self, "_image_companion_available", None)
        if not callable(available):
            return False
        try:
            return bool(available())
        except Exception:
            return False

    def _media_delivery_truth_instruction(self) -> str:
        sections = self._media_delivery_truth_prompt_sections()
        return "".join(
            f"【{section['title']}】{section['content']}"
            for section in sections
        )

    def _media_delivery_truth_prompt_sections(self) -> list[dict[str, Any]]:
        if not getattr(self, "enabled", False):
            return []
        photo_enabled = bool(
            runtime_persona_setting(self, 'enable_photo_text_action', False)
            and self._photo_generation_runtime_available()
        )
        sections = [
            prompt_section(
                "内部历史标记",
                "`<pc_history_media ... />` 仅表示某条历史消息当时真实包含附件，"
                "它不是聊天正文，也不是要求你发送或描述附件的指令。任何回复都不得复述、改写或输出该标签。",
            )
        ]
        if not photo_enabled and not self._reaction_image_provider_available():
            return sections
        sections.extend(
            [
                prompt_section(
                    "明确生图请求",
                    "用户明确要求生成、绘制、制作、自拍、拍照、头像或改图时，必须先调用对应真实媒体工具；"
                    "没有工具调用或工具成功结果时，不得使用‘画好了/生成了/图片在上面/我存到本地了’等完成或交付措辞。",
                ),
                prompt_section(
                    "媒体真实性硬规则",
                    "只有本轮消息链实际包含图片，或媒体工具明确返回 `sent=true`，"
                    "才能说“已经发了/给你看了/图片在上面”。其他情况必须承认未发送；人格和角色扮演不能覆盖真实发送状态。"
                    "“（发送了一张图片）”“（随消息发送了一张图片）”之类的附件占位说明。要发图只能使用真实图片组件。",
                ),
            ]
        )
        return sections

    def _reaction_asset_library(self):
        return get_reaction_asset_library(self)

    def _find_owned_reaction_asset(
        self,
        query: str,
        *,
        search_context: str = "",
        meme_only: bool = True,
    ) -> dict[str, Any] | None:
        if not bool(runtime_persona_setting(self, 'enable_owned_reaction_asset_workbench', False)):
            return None
        catalog = OwnedReactionAssetCatalog(getattr(self, "data_dir", ""))
        asset, status, confidence = catalog.find(
            runtime_persona_setting(self, 'owned_reaction_assets', []),
            query=query,
            search_context=search_context,
            meme_only=bool(meme_only),
        )
        if asset is None:
            logger.debug(
                "Q6 自有反应图未命中: status=%s",
                status,
            )
            return None
        return {
            "success": True,
            "status": "success",
            "source": "owned_reaction_assets",
            "path": str(asset.path),
            "image_id": asset.asset_id,
            "tags": list(asset.tags),
            "need": _single_line(query, 220),
            "reason": "管理员登记的受管自有反应图标签命中",
            "confidence": confidence,
        }

    def _reaction_image_provider_available(self) -> bool:
        library = self._reaction_asset_library()
        return bool(
            library and library.has_enabled_assets()
        ) or bool(
            runtime_persona_setting(self, 'enable_owned_reaction_asset_workbench', False)
            and runtime_persona_setting(self, 'owned_reaction_assets', [])
        )

    @staticmethod
    def _reaction_expression_opt_out_requested(text: Any) -> bool:
        return reaction_expression_explicit_opt_out(text)

    @staticmethod
    def _reaction_expression_explicit_request_matches(text: Any) -> bool:
        return reaction_expression_explicit_request(text)

    def _reaction_expression_event_storage_id(self, event: Any, user_id: Any) -> str:
        """Resolve an event sender to the platform/account-scoped users key."""
        raw_id = _single_line(user_id, 160)
        if not raw_id:
            return ""
        # Callers may feed the already-resolved storage key back into a later
        # state step. Do not namespace that key a second time.
        try:
            event_sender = _single_line(event.get_sender_id(), 160)
        except Exception:
            event_sender = ""
        platform_getter = getattr(self, "_platform_kind_for_event", None)
        try:
            platform = _single_line(platform_getter(event), 40).lower() if callable(platform_getter) else ""
        except Exception:
            platform = ""
        if event_sender and raw_id != event_sender and platform and raw_id.startswith(f"{platform}:"):
            return raw_id
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            try:
                resolved = _single_line(resolver(event, raw_id), 160)
            except Exception:
                resolved = ""
            if resolved:
                return resolved
        return raw_id

    def _reaction_expression_feedback_user(
        self,
        user_id: Any,
        text: Any,
        *,
        create_for_opt_out: bool = False,
        event: Any = None,
    ) -> dict[str, Any] | None:
        """Resolve the canonical user that owns per-conversation feedback."""
        normalized_id = _single_line(user_id, 160)
        if event is not None and self._reaction_expression_scope(event) == "group":
            return self._reaction_expression_state_owner(
                event,
                normalized_id,
                create=bool(create_for_opt_out and reaction_expression_explicit_opt_out(text)),
            )
        if event is not None:
            normalized_id = self._reaction_expression_event_storage_id(event, normalized_id)
        data = getattr(self, "data", None)
        users = data.get("users") if isinstance(data, dict) else None
        if not normalized_id or not isinstance(users, dict):
            return None

        canonical_id = normalized_id
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        if callable(canonicalizer):
            try:
                canonical_id = (
                    _single_line(canonicalizer(normalized_id), 160)
                    or normalized_id
                )
            except Exception:
                canonical_id = normalized_id

        for candidate_id in dict.fromkeys((normalized_id, canonical_id)):
            candidate = users.get(candidate_id)
            if isinstance(candidate, dict):
                return candidate
        for candidate in users.values():
            if not isinstance(candidate, dict):
                continue
            aliases = candidate.get("alias_user_ids")
            if (
                _single_line(candidate.get("user_id"), 160) == normalized_id
                or isinstance(aliases, list) and normalized_id in aliases
            ):
                return candidate

        if not (
            create_for_opt_out
            and reaction_expression_explicit_opt_out(text)
        ):
            return None
        getter = getattr(self, "_get_user", None)
        if not callable(getter):
            return None
        try:
            created = getter(canonical_id)
        except Exception:
            return None
        return created if isinstance(created, dict) else None

    def _mark_reaction_asset_used(
        self,
        image_id: Any,
        *,
        event: Any = None,
        trace_id: str = "",
    ) -> None:
        normalized = _single_line(image_id, 160)
        if not normalized.startswith("pc-local:"):
            return
        library = self._reaction_asset_library()
        if library is None:
            return
        try:
            library.mark_used(normalized)
        except Exception as exc:
            self._log_reaction_expression_event(
                event,
                trace_id=trace_id,
                stage="degrade",
                decision="failed",
                reason="usage_mark_failed",
                image_id=normalized,
                error_type=type(exc).__name__,
            )

    @staticmethod
    def _mark_private_companion_skip_reaction_expression(event: Any) -> None:
        """Mark this event after a real image delivery to avoid a second reaction image."""
        if event is None:
            return
        try:
            setattr(event, "_private_companion_skip_reaction_expression", True)
        except Exception:
            pass
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            try:
                setter("private_companion_skip_reaction_expression", True)
            except Exception:
                pass

    def _photo_tool_call_timeout_seconds(self) -> float:
        context = getattr(self, "context", None)
        getter = getattr(context, "get_config", None)
        if not callable(getter):
            return 120.0
        try:
            cfg = getter()
        except Exception:
            return 120.0
        provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        if not isinstance(provider_settings, dict):
            return 120.0
        return _safe_float(provider_settings.get("tool_call_timeout"), 120.0, 1.0, 3600.0)

    def _cross_user_memory_query_instruction(self, *, include_heading: bool = True) -> str:
        if not (self.enabled and getattr(self, "enable_cross_user_memory_bridge", False)):
            return ""
        body = """用户在私聊里问“你和某人聊了什么”“最近和某群互动怎样”“某人在群里说过什么”时，可以用 `pc_query_interaction` 读取近期互动摘要。
- 只用于查询，不发送消息。
- 优先传 scope=private/group、user_hint 或 group_hint；不确定时传原始称呼给 hint。
- “最近和他私聊说了什么”传 scope=private,user_hint=对象；“他在群里说了什么”传 scope=group,user_hint=对象，有具体群再加 group_hint。
- 回答时概括最近互动和重点即可，不要大段复述原文。
""".strip()
        return f"【跨用户记忆互通】\n{body}" if include_heading else body

    def _relation_lookup_instruction(self, *, include_heading: bool = True) -> str:
        if not (self.enabled and runtime_persona_setting(self, 'enable_worldbook_member_recognition', False)):
            return ""
        body = """用户明确要求“查一下关系网/帮我查某个 QQ 或昵称”时，可以用 `pc_query_relation_person` 查询关系网。
- 如果刚用 LivingMemory/长期记忆召回到某个人名、昵称、QQ 或群成员别名,并且需要判断 TA 是谁、和用户什么关系、能不能套用某段关系时,也可以先查关系网再回答。
- 只用于确认是否认识和读取稳定称呼、别名、简短身份备注；不要发送消息。
- 参数用 keyword 传 QQ 号、昵称、别名或用户原话里最像名字的部分。
- 查不到就自然说明没在关系网里确认过，不要编造。
""".strip()
        return f"【关系网查询】\n{body}" if include_heading else body

    def _qzone_tool_instruction(
        self,
        event: AstrMessageEvent | None = None,
        *,
        include_heading: bool = True,
    ) -> str:
        availability = getattr(self, "_qzone_available", None)
        if not (self.enabled and self.enable_qzone_integration):
            return ""
        if callable(availability) and not availability(event):
            return ""
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
        instruction = f"【QQ 空间动态工具】\n{body}" if include_heading else body
        context_getter = getattr(self, "_qzone_recent_self_publish_chat_context", None)
        recent_context = context_getter() if callable(context_getter) else ""
        return f"{instruction}\n\n{recent_context}".strip() if recent_context else instruction

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

    def _user_photo_generation_prompt_enabled(
        self,
        event: AstrMessageEvent | None = None,
        *,
        spontaneous_only: bool = False,
    ) -> bool:
        if spontaneous_only or not getattr(self, "enabled", False):
            return False
        if not runtime_persona_setting(self, "enable_photo_text_action", False):
            return False
        if not self._photo_generation_runtime_available():
            return False
        scope_getter = getattr(self, "_photo_generation_scope", None)
        scope = ""
        if callable(scope_getter):
            try:
                scope = _single_line(scope_getter(event), 40).lower()
            except Exception:
                scope = ""
        if not scope and bool(
            getattr(event, "private_companion_proactive_framework", False)
        ):
            scope = "proactive"
        if scope == "proactive":
            return True
        permission_getter = getattr(
            self,
            "_user_requested_photo_generation_allowed",
            None,
        )
        if callable(permission_getter):
            try:
                if not bool(permission_getter(event)):
                    return False
            except Exception:
                return False
        elif not runtime_persona_setting(
            self,
            "enable_user_requested_photo_generation",
            True,
        ):
            return False
        mode = _single_line(
            runtime_persona_setting(
                self,
                "natural_language_photo_generation_mode",
                "tool_first",
            ),
            40,
        ).lower()
        return mode != "off"

    def _photo_generation_tool_instruction(
        self,
        event: AstrMessageEvent | None = None,
        *,
        include_spontaneous: bool | None = None,
        spontaneous_only: bool = False,
        include_heading: bool = True,
    ) -> str:
        if not getattr(self, "enabled", False):
            return ""
        reaction_enabled = self._reaction_image_provider_available()
        photo_enabled = self._user_photo_generation_prompt_enabled(
            event,
            spontaneous_only=spontaneous_only,
        )
        if not reaction_enabled and not photo_enabled:
            return ""
        if spontaneous_only:
            high_frequency_hint = (
                "- 当前触发概率为 100%：对轻松、社交或有明确情绪的正常回复，默认追加一个标签；"
                "不要把‘是否自然’再次当作概率筛选。纯事实、严肃、敏感或明确边界场景仍只发正文。"
                if reaction_expression_high_frequency(
                    runtime_persona_setting(self, 'reaction_expression_trigger_probability', 0.2)
                )
                else "- 轻松闲聊、玩笑、安慰、撒娇、庆祝、惊讶、接梗或轻吐槽等能自然补充语气的场景，通常应在完整回复末尾追加内部标签。只有纯事实答复、严肃或敏感情境，或确实没有合适情绪时才省略。"
            )
            spontaneous_lines = [
                    "- 先完成一条正常、完整、可以独立发送的文字回复。表情图片只能作为文字后的补充，绝对不能替代文字回复。",
                    "- 本轮已经由插件完成概率抽样并获得一次表情表达机会；不要再次按概率决定，也不要因为‘不确定’而默认省略标签。",
                    high_frequency_hint,
                    '-最小标签格式为 `<pc_reaction_expression>{"purpose":"轻吐槽","emotion":"无语","intensity":2}</pc_reaction_expression>`。',
                    "- `purpose` 写沟通用途，`emotion` 写希望传达的情绪，`intensity` 为 0-5；需要帮助检索时可选填 `candidate_queries`，提供 1-3 个简短说法。不要填写图片路径。",
                    "- 每轮最多写一个标签，必须放在全部可见文字和 TTS 标签之后；不要使用 Markdown 代码块，不要解释标签，也不要调用图片或生图工具。",
                    "- 即使图库最终没有匹配、图片重复或发送失败，前面的完整文字也必须仍然自然成立。",
                ]
            if include_heading:
                spontaneous_lines.insert(0, "【实验性表情表达】")
            return "\n".join(spontaneous_lines).strip()
        lines: list[str] = []
        if include_heading:
            lines.append(
                "【图库表情与生图工具】"
                if photo_enabled and reaction_enabled
                else (
                    "【生图工具】"
                    if photo_enabled
                    else "【图库表情工具】"
                )
            )
        # Only describe the gallery when its runtime provider is actually usable.
        if reaction_enabled and not spontaneous_only:
            reaction_availability = (
                "- 表情包素材库当前已有可用素材，用户请求现成表情包或反应图时可直接调用 `pc_find_reaction_image` 检索。"
                if reaction_enabled
                else "- 表情包素材库可能暂无可用的现成素材，用户仍可尝试调用 `pc_find_reaction_image` 检索；库为空时工具会返回对应提示。"
            )
            raw_probability = runtime_persona_setting(
                self, 'reaction_expression_trigger_probability', 0.2
            )
            if reaction_expression_high_frequency(raw_probability):
                spontaneous_hint = (
                    "- 自动追加表情包目前为高频触发：对轻松、社交或有明确情绪的动作/表情描述回复"
                    "（如[委屈巴巴地缩了缩脖子]、[开心地蹦跶了两下]），默认在正文后调用 `pc_find_reaction_image` "
                    "追加一个匹配表情包；纯事实、严肃、敏感或明确边界场景仍只发文字，不追加。"
                )
            else:
                chance = reaction_expression_normalize_probability(raw_probability, 0.2)
                spontaneous_hint = (
                    f"- 自动追加表情包是低概率点缀而非每轮默认动作：当前配置下约 {int(round(chance * 100))}% 的情境才自然带一个匹配表情包。"
                    "若回复中出现动作或表情描述（如[委屈巴巴地缩了缩脖子]、[开心地蹦跶了两下]），是典型的追加时机，"
                    "但请按上述概率自然把握：不要每轮都加，也不要因偶尔没加而向用户解释。"
                )
            lines.extend(
                [
                    reaction_availability,
                    "- 用户要“找/发/来一张已有表情包”、要用现成反应图回应当前语境时，优先使用 `pc_find_reaction_image`，把需求和当前语境写进 `query/search_context`。",
                    "- `pc_find_reaction_image` 在 `send=true` 时必须填写 `caption`，内容应是一条完整、自然、可独立成立的正文；图片只能追加在正文后，不能替代、缩短或省略正文。",
                    "- 决定调用 `pc_find_reaction_image` 时，把可见正文只放进 `caption` 参数；发起工具调用的同一轮不要再额外输出正文或声称图片已经发送。拿到工具结果后再按结果完成最终回复。",
                    "- 图库未匹配时可以自然改用文字回应，不要擅自声称已发图。",
                    spontaneous_hint,
                ]
            )
        if reaction_enabled:
            experiment_enabled = bool(
                runtime_persona_setting(self, 'enable_reaction_expression_experiment', False)
            )
            spontaneous_enabled = experiment_enabled and (
                include_spontaneous is not False
            )
            if spontaneous_enabled:
                lines.extend(
                    [
                        "- 普通闲聊中，先生成一条完整、可独立成立的文字回复；只有在正文之后追加表情图能明显补足语气、且符合本轮关系边界时，才可把 `spontaneous=true` 调用 `pc_find_reaction_image`。图片不能替代、缩短或省略正文；不要每轮调用，不确定时只保留自然文字回复。",
                        "- 发起自发表情工具调用时，把这条完整正文只放进 `caption` 参数，同一工具调用轮不要再额外输出正文或提前描述发送结果；等待工具结果后再完成最终回复。",
                        "- 自发表情调用应填写 `purpose`（沟通用途）、`emotion`（想传达的情绪）、`intensity`（0-5）与 `candidate_queries`（少量候选检索说法）；这些是本轮结构化表达意图，不要另行解释给用户。",
                        "- 自发表情允许因概率、冷却、重复或图库不匹配而返回 `decision=skip`。遇到跳过时不要解释内部原因，继续按原语境自然文字回复即可。",
                    ]
                )
        if photo_enabled:
            lines.append(
                "- 只有用户明确要求“生成/画/制作”新的角色表情包或贴纸时，才使用 `pc_generate_photo(kind=\"sticker\")`。不要把普通的现成表情包请求误当成生图。"
            )
            lines.extend(
                [
                    "- 用户明确要求生成图片、画图、出图、自拍、拍照、头像，或要求基于参考图改图时，可以使用 `pc_generate_photo`。",
                    '- 普通场景/物件/风景：仅当画面中不出现角色本人时，传 `{"prompt":"画面描述","kind":"text2img"}`，可用 `scene_preset` 建议“可拍画面/房间日常”；该字段只是建议，不会覆盖用户原话或参考图约束。把它写成角色镜头看到的画面，不要擅自加入拍摄者、陌生女孩或人物背影。纯梗图或无角色贴纸才用 `text2img + scene_preset="表情包场景"`。',
                    '- 角色本人以任何形式出镜，包括自拍、背影、侧脸、环境人像、头像、穿搭或 COS：传 `{"prompt":"画面要求","kind":"selfie"}`，可用 `scene_preset` 建议“角色自拍/COS自拍/日常穿搭/居家睡衣/镜前穿搭/头像特写”；明确睡衣、睡裙、睡袍或睡前卧室自拍时优先建议“居家睡衣”，普通穿搭才建议“日常穿搭”，只有明确“镜前/对镜/镜子”时才建议镜前穿搭；最终只采用一个兼容预设。只有开启参考图一致性时，未传参考图才会自动使用配置的人设参考图或今日穿搭参考图。',
                    '- 自拍也应延续角色此刻的生活状态。结合本轮已有的当前日程、位置和对话判断：如果角色正在上课、通勤或处理别的事，而用户想看海边、旅行地等明显不在当前现场的自拍，优先保持生活连续性，不要让角色像瞬间换了地点。用户只是想看这类画面时，通常可以自然理解为分享之前拍的、相册里的照片；仍可调用 `pc_generate_photo`，在 prompt 中说明按此前拍摄的照片呈现，并在 `caption` 里用角色口吻轻轻交代来源。',
                    '- 这不是固定拒绝规则。当前状态没有明显冲突、用户是在延续刚才的拍摄情境，或语境本来就是设想/COS/创作时，可以照常生成；只有用户明确强调“现在、立刻、现场拍”且与当前活动明显不合适时，再自然商量晚点拍。不要向用户复述内部日程判断或规则。',
                    '- 用户引用上一张角色照片并要求“比个心、看镜头、换个动作/表情/角度、再来一张”等自然续拍时，仍使用 `kind="selfie"`，并在 prompt 中说明只改变这次要求的部分、其余人物穿搭与场景继续保持；工具会读取本轮引用图片，不要猜测或手填图片路径。若本轮没有引用或携带图片，则按普通新自拍处理，选图器不会自动复用上一张成图。明确换装、换地点、换人物或另起主题时按新要求生成。',
                    '- 合影、合照、双人或多人同框必须有可验证的其他人物参考图：优先使用本轮携带或引用的图片；若请求明确点名了已在 Bot 关系网角色卡中绑定可用参考图的角色，也可直接调用 `pc_generate_photo` 并让工具自动选图，不要填写或猜测路径。Bot 单人人设图、今日穿搭图和纯文字关系卡都不算其他人物参考，模型自行填写的本地路径/URL 也不能单独授权合影。两类参考来源都没有时不要调用生图，也不要凭文字捏造另一张脸；可以说明需要先为该角色绑定参考图，或让用户发送/引用人物图片。',
                    '- 如果前几轮文字剧情已经明确让角色换装，而本轮只说“继续、再拍一张、保持刚才的穿搭”等，不要把它理解成恢复今日穿搭。必须把仍有效的具体服装展开写进 prompt，例如“角色当前仍穿 JK 校服，保持本轮地点和人物连续性”；当前对话已发生的换装高于日程、人格默认衣着、每日穿搭参考图和旧图片。',
                    '- “JK”在服装语境下请规范写成“JK 校服/JK 制服”；只有用户明确改变服装时才替换连续状态，提问、假设或用户自己换衣不算角色已换装。',
                    '- 角色表情包/贴纸：传 `{"prompt":"表情和画面要求","kind":"sticker"}`；默认走自拍/人像链路并使用“表情包场景”预设，让角色仍可识别。',
                    '- 改图/重绘：当前消息或引用消息已经带图时，传 `{"prompt":"修改要求","kind":"edit"}`，不要猜测、抄写或回传本地临时路径，插件会从当前事件安全取图。只有明确使用公网图片 URL 或插件已管理的参考图时才传 `reference_image_path`；多图职责组合可传 `reference_image_paths` 数组，并在 prompt 中说明每张图承担的脸、衣服、姿势等职责。没有任何当前/引用/已管理参考图时不要调用改图。',
                    "- `pc_generate_photo` 会自行发送成图，调用它之后绝对不要再调用 `pc_send_current_media`。如果另一个生图工具或图像编辑工具已经生成或编辑图片并明确返回了本地图片路径、且结果没有确认图片已发送，必须立即把该路径交给 `pc_send_current_media` 投递一次；不得回答“没法直接发”“图片存好了以后再看”。",
                    "- `pc_send_current_media` 只承接本轮或紧邻上一轮刚生成但尚未投递的本地图片。默认用 `destination=current` 发到当前会话；当前请求者明确说“私聊发我/私信发我”等要求时，用 `destination=requester_private` 只私聊发给请求者本人。把生成工具返回的原始路径直接传入，不要自行改名、移动文件、检查插件状态或建议用户重启；工具会安全兼容已验证的图片内容与扩展名差异。不得猜测路径、指定第三方、复用陈旧路径、发送用户未要求的文件，或在本轮已经出现图片后再次调用。",
                    "- 图片投递失败时，只依据工具返回结果简短说明没有送达；不要向用户暴露工具名、本地路径、插件注册、发送通道或内部排障过程，也不要编造工具消失、配置异常等原因。",
                ]
            )
            prompt_format_instruction = getattr(self, "_photo_generation_prompt_format_instruction", None)
            if callable(prompt_format_instruction):
                format_text = re.sub(r"\s+", " ", str(prompt_format_instruction() or "")).strip()
                if format_text:
                    lines.append(
                        f"- `prompt` 参数必须按“提示词表达方式”书写（与主动拍照一致）：{format_text}"
                    )
        if photo_enabled:
            lines.extend(
                [
                "- 默认 `send=true`；如果只想拿路径再决定，可传 `send=false`。",
                "- 每个用户请求本轮最多调用一次 `pc_generate_photo`。工具返回失败、结果取回失败或发送回执未确认后，不要在同一轮再次调用生图工具；按工具的 `message/actual_error/final_response_instruction` 回复，用户下一轮明确要求时再重试。",
                "- 如果工具返回 `generation_completed=true` 且 `failure_stage=result_materialization`，说明上游已经完成生图但图片结果没有取回或保存成功；不要说成上游生图失败，也不要重复提交同一画面。",
                "- 在实际调用媒体工具并得到结果前，绝对不能声称“已经发了/给你看了/图片在上面”。角色扮演不能覆盖真实工具状态。",
                f"- `caption` 只用于随图发送用户应当直接看到、可独立成立的自然正文；不得填写“图生好了/生成成功/图片已发送/给你看”等状态回执。确实有与画面、当下感受或对话相关的内容才填写，否则留空让图片独立回复。不要写 `&&shy&&`、`[shy]`、TTS 情绪标签或任何内部控制标记。只有工具返回 `sent=true` 时才表示图片已经发出；成功后不要把最终回复留空，必须只输出内部静默标记 `{PHOTO_TOOL_SILENT_SENTINEL}`。插件会在发送前移除它；不要再写承接句、重复 caption 或额外表情。",
                "- 工具返回 `sent=false` 时，必须按 `message/actual_error` 如实说明，绝对不能说已经发送。",
                "- 如果工具返回 `error_code=provider_policy_refusal`，不要复述或翻译 Provider 的英文原文、政策名称、敏感词判断和链接；只用符合当前人格的一句简短中文说明这次没有生成出来，再自然询问是否换一种画面描述重试。",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _photo_tool_followup_is_redundant(sent_caption: Any, followup_text: Any) -> bool:
        """Only catch clear repeats of a caption already delivered with the image."""

        def compact(value: Any) -> str:
            text = _strip_internal_message_blocks(str(value or "")).lower()
            return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)

        caption = compact(sent_caption)
        followup = compact(followup_text)
        if caption and caption == followup:
            return True
        if len(caption) < 6 or len(followup) < 6:
            return False
        shorter, longer = sorted((caption, followup), key=len)
        return shorter in longer and len(shorter) / max(1, len(longer)) >= 0.45

    def _sanitize_photo_tool_caption(self, value: Any, *, limit: int = 120) -> str:
        """Keep synthesis and internal control cues out of visible image captions."""
        cleaned = _strip_internal_message_blocks(str(value or ""))
        cleaned = re.sub(r"&&[A-Za-z_][A-Za-z0-9_ -]{0,31}&&", "", cleaned)
        cue_cleaner = getattr(self, "_strip_visible_tts_emotion_cues", None)
        if callable(cue_cleaner):
            cleaned = cue_cleaner(cleaned)
        return _single_line(cleaned, max(1, int(limit or 120)))

    @staticmethod
    def _photo_caption_is_generic(value: Any) -> bool:
        text = re.sub(
            r"[\s。.!！?？,，；;:：、~～…\"'“”‘’（）()【】\[\]]+",
            "",
            str(value or ""),
        ).casefold()
        if not text:
            return True
        polite_tail = r"(?:啦|了|哦|噢|喔|呀|哈|呢)*"
        handoff_tail = (
            r"(?:(?:给|发|送)你(?:看|看看|了)?|"
            r"给你看(?:看)?|请查收)?" + polite_tail
        )
        return any(
            re.fullmatch(pattern, text)
            for pattern in (
                rf"(?:我)?(?:按(?:你|您)(?:的)?要求|按要求)?(?:这张)?"
                rf"(?:图|图片|照片|画面)(?:已经|已|刚刚)?"
                rf"(?:生|生成|画|绘制|改|修改|做|拍|处理|出)?"
                rf"(?:成功|好了?|完成|完毕|出来了?){handoff_tail}",
                rf"(?:我)?(?:按(?:你|您)(?:的)?要求|按要求)?(?:已经|已|刚刚)?"
                rf"(?:生图|出图|生成|画|绘制|改|修改|做好|做|拍|处理)"
                rf"(?:成功|好了?|完成|完毕|出来了?){handoff_tail}",
                rf"(?:图|图片|照片)?(?:已经|已)?(?:发送|发出|送达)"
                rf"(?:成功|完成|好了?)?{polite_tail}",
                rf"(?:已经|已)?(?:发|发送|送)给你{polite_tail}",
                rf"(?:给|发)(?:你)?(?:看|看看){polite_tail}",
                rf"(?:完成|完成了|好了|成功){polite_tail}",
            )
        )

    @staticmethod
    def _photo_generation_policy_refusal(value: Any) -> bool:
        """Recognize a provider refusal without judging the user's prompt locally."""
        normalized = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
        if not normalized:
            return False
        refusal_markers = (
            "prompt could not be submitted",
            "prompt was not submitted",
            "try rephrasing the prompt",
            "request was rejected",
            "request was blocked",
            "内容政策拒绝",
            "内容策略拒绝",
            "安全策略拒绝",
            "请求被安全策略拦截",
        )
        policy_markers = (
            "generative ai prohibited use policy",
            "content policy violation",
            "sensitive words",
            "violates google's",
            "violates the policy",
            "policy violation",
            "不符合内容政策",
            "违反内容政策",
            "敏感词",
        )
        return any(marker in normalized for marker in refusal_markers) and any(
            marker in normalized for marker in policy_markers
        )

    def _sanitize_photo_tool_result_payload(
        self,
        value: Any,
        *,
        known_paths: tuple[Any, ...] = (),
    ) -> Any:
        """Remove local filesystem details from the model-visible tool receipt."""

        absolute_known_paths: list[str] = []
        for candidate in known_paths:
            text = str(candidate or "").strip()
            if not text:
                continue
            if text.lower().startswith(("http://", "https://", "data:")):
                continue
            if (
                _PHOTO_TOOL_WINDOWS_PATH_START_RE.match(text)
                or _PHOTO_TOOL_POSIX_PATH_START_RE.match(text)
                or (len(text) >= 3 and ("/" in text or "\\" in text))
            ):
                absolute_known_paths.append(text)
        absolute_known_paths.sort(key=len, reverse=True)

        def redact_text(raw: Any) -> str:
            cleaned = _redact_outbound_secrets(raw, self)
            protected_urls: dict[str, str] = {}

            def protect_url(match: re.Match[str]) -> str:
                token = f"PCPHOTOURL{uuid.uuid4().hex}TOKEN"
                protected_urls[token] = match.group(0)
                return token

            cleaned = _PHOTO_TOOL_HTTP_URL_RE.sub(protect_url, cleaned)
            for path in absolute_known_paths:
                cleaned = cleaned.replace(path, _PHOTO_TOOL_REDACTED_LOCAL_PATH)
            starts = [
                match.start()
                for pattern in (
                    _PHOTO_TOOL_WINDOWS_PATH_START_RE,
                    _PHOTO_TOOL_POSIX_PATH_START_RE,
                    _PHOTO_TOOL_RELATIVE_PATH_START_RE,
                )
                if (match := pattern.search(cleaned)) is not None
            ]
            if starts:
                prefix = cleaned[: min(starts)].rstrip()
                cleaned = f"{prefix} {_PHOTO_TOOL_REDACTED_LOCAL_PATH}".strip()
            for token, url in protected_urls.items():
                cleaned = cleaned.replace(token, url)
            return cleaned

        sensitive_path_keys = {
            "path",
            "paths",
            "image_path",
            "image_paths",
            "reference_path",
            "reference_paths",
            "reference_image_path",
            "reference_image_paths",
            "resolved_path",
            "prompt_path",
        }

        def sanitize(item: Any) -> Any:
            if isinstance(item, dict):
                cleaned_dict: dict[Any, Any] = {}
                for key, child in item.items():
                    normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key or "").lower()).strip("_")
                    if normalized_key in sensitive_path_keys or normalized_key.endswith("_local_path"):
                        continue
                    cleaned_dict[key] = sanitize(child)
                return cleaned_dict
            if isinstance(item, (list, tuple, set)):
                return [sanitize(child) for child in item]
            if isinstance(item, str):
                return redact_text(item)
            return item

        return sanitize(value)

    @staticmethod
    def _current_turn_has_delivered_media(event: AstrMessageEvent) -> bool:
        if bool(getattr(event, "_private_companion_photo_tool_sent", False)):
            return True
        chains = getattr(event, "_private_companion_confirmed_send_chains", None)
        if not isinstance(chains, list):
            return False
        for chain in chains:
            if not isinstance(chain, (list, tuple)):
                continue
            for component in chain:
                component_name = type(component).__name__.casefold()
                if component_name in {"image", "file", "video", "record", "audio"}:
                    return True
        return False

    @staticmethod
    def _referenced_media_edit_instruction_matches(text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        return bool(
            re.search(
                r"(?:把|将|给|帮我|替我).{0,18}"
                r"(?:改成|变成|换成|调成|染成|改为|变为|换为|调为)"
                r"|(?:改|换|调|染).{0,10}(?:颜色|色调|背景|尺寸|大小|亮度|对比度|饱和度)",
                compact,
                flags=re.I,
            )
        )

    @staticmethod
    def _current_media_private_delivery_instruction_matches(text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or "")).casefold()
        if not compact:
            return False
        return bool(
            re.search(
                r"(?:私聊|私信|私发|dm).{0,8}(?:发|给|传|丢|送)?(?:给)?我"
                r"|(?:发|给|传|丢|送).{0,8}(?:到|去)?(?:我)?(?:私聊|私信|dm)",
                compact,
                flags=re.I,
            )
        )

    @classmethod
    def _current_media_delivery_instruction_matches(cls, text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        if cls._current_media_private_delivery_instruction_matches(compact):
            return True
        if re.search(
            r"(?:把|将|给|帮我|麻烦)?(?:这张|那张|刚才的|上面的|改好的)?"
            r"(?:图|图片|照片|成图).{0,8}(?:发|传|给|贴|丢)(?:出来|过来|给我|我)?"
            r"|(?:发|传|给|贴|丢).{0,8}(?:这张|那张|刚才的|上面的|改好的)?"
            r"(?:图|图片|照片|成图)",
            compact,
            flags=re.I,
        ):
            return True
        # Follow-up requests often refer to the failed result indirectly, for
        # example "不要生成新图，把刚刚没发出来的发给我". Keep the
        # delivery tool available when the same sentence still contains an
        # image anchor, a recent-result anchor, and an actual send instruction.
        has_media_anchor = bool(
            re.search(
                r"(?:图|图片|照片|成图|图像|画面|这张|那张|这一张|那一张)",
                compact,
                flags=re.I,
            )
        )
        has_recent_anchor = bool(
            re.search(
                r"(?:刚才|刚刚|之前|上次|前面|上面|原来|已有|现成|生成|画好|做好|改好|没发|未发|没送|未送)",
                compact,
                flags=re.I,
            )
        )
        has_delivery_action = bool(
            re.search(
                r"(?:发|传|贴|丢|送)(?:出来|过来|给我|我|一下|一次)?",
                compact,
                flags=re.I,
            )
        )
        return has_media_anchor and has_recent_anchor and has_delivery_action

    def _current_media_allowed_roots(self) -> list[Path]:
        roots: list[Path] = []

        def add(candidate: Any) -> None:
            text = str(candidate or "").strip()
            if not text:
                return
            try:
                resolved = Path(text).expanduser().resolve()
            except Exception:
                return
            if resolved not in roots:
                roots.append(resolved)

        try:
            add(Path(get_astrbot_data_path()) / "temp")
        except Exception:
            pass
        data_dir = str(getattr(self, "data_dir", "") or "").strip()
        if data_dir:
            add(Path(data_dir) / "generated_photos")
        return roots

    @staticmethod
    def _current_media_image_signature_suffix(path: Path) -> str:
        try:
            with path.open("rb") as handle:
                header = handle.read(16)
        except OSError:
            return ""
        if header.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if header.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return ".webp"
        if header.startswith(b"BM"):
            return ".bmp"
        if len(header) >= 12 and header[4:12] in {b"ftypavif", b"ftypavis"}:
            return ".avif"
        return ""

    @classmethod
    def _normalize_current_media_image_suffix(cls, path: Path) -> Path | None:
        actual_suffix = cls._current_media_image_signature_suffix(path)
        if not actual_suffix:
            return None
        current_suffix = path.suffix.casefold()
        if current_suffix == actual_suffix or {
            current_suffix,
            actual_suffix,
        } <= {".jpg", ".jpeg", ".jfif"}:
            return path

        temporary: Path | None = None
        try:
            stat = path.stat()
            fingerprint = hashlib.sha256(
                f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
            ).hexdigest()[:12]
            normalized = path.with_name(
                f"{path.stem}.pc-media-{fingerprint}{actual_suffix}"
            )
            temporary = normalized.with_name(
                f"{normalized.name}.{uuid.uuid4().hex}.tmp"
            )
            shutil.copyfile(path, temporary)
            os.replace(temporary, normalized)
            return normalized.resolve(strict=True)
        except Exception as exc:
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            except Exception:
                pass
            logger.warning(
                "当前媒体扩展名规范化失败: file=%s error=%s",
                path.name,
                _single_line(exc, 160),
            )
            return None

    def _resolve_current_media_image(self, value: Any) -> tuple[Path | None, str]:
        raw = str(value or "").strip().strip('"').strip("'")
        if not raw or raw.casefold().startswith(("http://", "https://", "data:", "base64://")):
            return None, "只支持本轮工具返回的本地图片路径"
        try:
            path = Path(raw).expanduser().resolve(strict=True)
        except Exception:
            return None, "本轮生成的图片文件不存在"
        if not path.is_file() or path.suffix.casefold() not in _CURRENT_MEDIA_IMAGE_SUFFIXES:
            return None, "只允许发送本轮生成的常见图片文件"
        if not any(path.is_relative_to(root) for root in self._current_media_allowed_roots()):
            return None, "图片不在允许的 AstrBot 临时目录或本插件成图目录内"
        try:
            stat = path.stat()
        except OSError:
            return None, "无法读取本轮生成的图片文件"
        if stat.st_size <= 0 or stat.st_size > _CURRENT_MEDIA_MAX_BYTES:
            return None, "图片为空或超过 32 MB 发送上限"
        age = time.time() - float(stat.st_mtime or 0)
        if age < -60 or age > _CURRENT_MEDIA_MAX_AGE_SECONDS:
            return None, "图片不是本轮近期生成的文件"
        normalized_path = self._normalize_current_media_image_suffix(path)
        if normalized_path is None:
            return None, "文件内容不是支持的实际图片格式"
        return normalized_path, ""

    async def _pc_send_current_media_impl(
        self,
        event: AstrMessageEvent,
        *,
        media_path: str = "",
        caption: str = "",
        destination: str = "current",
        **kwargs: Any,
    ) -> str:
        if self._current_turn_has_delivered_media(event):
            setattr(event, "_private_companion_photo_tool_sent", True)
            setattr(event, "_private_companion_photo_tool_sent_caption", "")
            return json.dumps(
                {
                    "status": "already_sent",
                    "success": True,
                    "sent": True,
                    "message": "本轮已经发送过媒体，不再重复投递。",
                    "same_turn_retry_allowed": False,
                    "final_response_instruction": f"不要追加回执或正文，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。",
                },
                ensure_ascii=False,
            )
        raw_path = media_path or kwargs.get("image_path") or kwargs.get("path")
        path, rejection = self._resolve_current_media_image(raw_path)
        if path is None:
            return json.dumps(
                {
                    "status": "invalid_media",
                    "success": False,
                    "sent": False,
                    "message": rejection or "图片不可用",
                    "must_not_claim_sent": True,
                    "same_turn_retry_allowed": False,
                    "final_response_instruction": "不要再次猜测或改写本地路径；如实说明这次图片没有发送。",
                },
                ensure_ascii=False,
            )
        destination_raw = _single_line(
            destination or kwargs.get("target_scope") or kwargs.get("scope") or "current",
            40,
        ).casefold()
        requester_private = destination_raw in {
            "requester_private",
            "requester-private",
            "private",
            "private_requester",
            "dm",
            "私聊",
            "私信",
        }
        if requester_private and not self._current_media_private_delivery_instruction_matches(
            getattr(event, "message_str", "")
        ):
            return json.dumps(
                {
                    "status": "destination_not_confirmed",
                    "success": False,
                    "sent": False,
                    "message": "当前消息没有明确要求把图片私聊发给请求者",
                    "must_not_claim_sent": True,
                    "same_turn_retry_allowed": False,
                    "final_response_instruction": "不要私聊发送，也不要声称已经发送；按当前会话自然回复。",
                },
                ensure_ascii=False,
            )
        sent_paths = getattr(event, "_private_companion_current_media_sent_paths", None)
        if not isinstance(sent_paths, set):
            sent_paths = set()
            setattr(event, "_private_companion_current_media_sent_paths", sent_paths)
        destination_key = "requester_private" if requester_private else "current"
        path_key = f"{destination_key}:{path}".casefold()
        if path_key in sent_paths:
            setattr(event, "_private_companion_photo_tool_sent", True)
            return json.dumps(
                {
                    "status": "already_sent",
                    "success": True,
                    "sent": True,
                    "message": "这张图片本轮已经投递，不再重复发送。",
                    "same_turn_retry_allowed": False,
                    "final_response_instruction": f"不要追加回执或正文，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。",
                },
                ensure_ascii=False,
            )
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=120)
        try:
            if requester_private:
                try:
                    target_user = _single_line(event.get_sender_id(), 128)
                except Exception:
                    target_user = ""
                sender = getattr(self, "_send_atrelay_chain_to_target", None)
                chain_builder = getattr(self, "_build_outbound_chain", None)
                if not target_user:
                    delivery = {
                        "sent": False,
                        "destination": "requester_private",
                        "message": "无法识别当前请求者，图片没有私聊发送",
                    }
                elif not callable(sender) or not callable(chain_builder):
                    delivery = {
                        "sent": False,
                        "destination": "requester_private",
                        "message": "当前平台没有可用的私聊图片投递链路",
                    }
                else:
                    chain = chain_builder(visible_caption, str(path))
                    ok, error, used_umo = await sender(
                        event,
                        message_type="private",
                        target_id=target_user,
                        chain=chain,
                    )
                    delivery = {
                        "sent": bool(ok),
                        "destination": "requester_private",
                        "message": (
                            "图片已私聊发送给当前请求者"
                            if ok
                            else f"图片私聊发送失败：{_single_line(error, 180) or '没有可用私聊会话'}"
                        ),
                        "target_umo": _single_line(used_umo, 160),
                    }
            else:
                delivery = await self._deliver_generated_image_to_event(
                    event,
                    image_path=str(path),
                    caption=visible_caption,
                )
        except Exception as exc:
            delivery = {
                "sent": False,
                "uncertain": isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)),
                "destination": destination_key,
                "message": f"图片发送失败：{_single_line(exc, 180) or '未知错误'}",
            }
        sent = bool(delivery.get("sent"))
        uncertain = bool(delivery.get("uncertain"))
        if sent:
            sent_paths.add(path_key)
            setattr(event, "_private_companion_photo_tool_sent", True)
            setattr(event, "_private_companion_photo_tool_sent_caption", visible_caption)
        payload = {
            "status": "success" if sent else "delivery_uncertain" if uncertain else "delivery_failed",
            "success": sent,
            "sent": sent,
            "delivery_uncertain": uncertain,
            "delivery": _single_line(delivery.get("destination"), 30),
            "message": _single_line(delivery.get("message"), 220) or ("图片已发送" if sent else "图片发送失败"),
            "must_not_claim_sent": not sent,
            "same_turn_retry_allowed": False,
            "final_response_instruction": (
                f"图片及可选 caption 已作为本轮唯一可见回复发送，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                if sent
                else "不要再次发送或重新生成；按 message 如实说明当前投递结果。"
            ),
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _reaction_expression_has_visible_text(value: Any) -> bool:
        """Require actual reply text before an experimental image may be attached."""
        text = _strip_internal_message_blocks(str(value or ""))
        text = re.sub(r"<[^>]{1,240}>", "", text, flags=re.DOTALL)
        return bool(re.search(r"\w", text, flags=re.UNICODE))

    def _extract_reaction_expression_hidden_intent(
        self,
        value: Any,
    ) -> tuple[str, dict[str, Any]]:
        """Remove the internal expression tag and parse at most one valid intent."""
        source = str(value or "")
        if not source:
            return "", {}

        literal_open = r"(?:<|\\<)\s*pc_reaction_expression\s*(?:>|\\>)"
        literal_close = r"(?:<|\\<)\s*/\s*pc_reaction_expression\s*(?:>|\\>)"
        escaped_open = r"&lt;\s*pc_reaction_expression\s*&gt;"
        escaped_close = r"&lt;\s*/\s*pc_reaction_expression\s*&gt;"
        complete_pattern = re.compile(
            rf"(?:{literal_open}(.*?){literal_close}|{escaped_open}(.*?){escaped_close})",
            flags=re.IGNORECASE | re.DOTALL,
        )
        parsed_intent: dict[str, Any] = {}

        def parse_payload(payload: str) -> dict[str, Any]:
            candidates = [str(payload or "").strip()]
            unescaped = html.unescape(candidates[0]).strip()
            if unescaped and unescaped not in candidates:
                candidates.append(unescaped)
            for candidate in list(candidates):
                if "\\\"" in candidate:
                    candidates.append(candidate.replace("\\\"", '"'))
            for candidate in candidates:
                if not candidate:
                    continue
                try:
                    payload_obj: Any = json.loads(candidate)
                    if isinstance(payload_obj, str):
                        payload_obj = json.loads(payload_obj)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(payload_obj, dict):
                    continue
                raw_queries = payload_obj.get("candidate_queries")
                normalized = normalize_reaction_expression_intent(
                    query=payload_obj.get("query", ""),
                    context=payload_obj.get("context", ""),
                    purpose=payload_obj.get("purpose", ""),
                    emotion=payload_obj.get("emotion", ""),
                    intensity=payload_obj.get("intensity", 0),
                    candidate_queries=raw_queries,
                    candidate_limit=_safe_int(
                        runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6),
                        6,
                        1,
                        16,
                    ),
                )
                meaningful = any(
                    str(payload_obj.get(key) or "").strip()
                    for key in ("query", "purpose", "emotion")
                ) or bool(normalized.get("candidate_queries"))
                if not meaningful:
                    continue
                return normalized
            return {}

        def remove_complete(match: re.Match[str]) -> str:
            nonlocal parsed_intent
            if not parsed_intent:
                parsed_intent = parse_payload(match.group(1) or match.group(2) or "")
            return ""

        cleaned = complete_pattern.sub(remove_complete, source)
        # A malformed or truncated internal tag must never become visible chat text.
        cleaned = re.sub(literal_close, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(escaped_close, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"(?:<|\\<|&lt;)\s*/?\s*pc[_-]?reaction.*$",
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = re.sub(r"[ \t]+(?=\r?$)", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned, parsed_intent

    def _creative_work_tool_instruction(self, *, include_heading: bool = True) -> str:
        if not self.enabled or not runtime_persona_setting(self, 'enable_creative_work_read_guard', True):
            return ""
        body = """当用户询问能否看到资料柜/书架、资料柜是否为空、里面有什么或有几篇作品时，必须先调用 `pc_view_creative_work`，action=list。list 返回的是插件当前真实保存的资料柜库存；主要用户还会得到日记、资料归档和便签的分类数量。
当用户询问你自己的某篇创作写了什么、某一部分/片段的内容、你如何看待这篇创作、为什么这样写，或要求你结合原文讲讲时，必须先调用 `pc_view_creative_work` 读取真实创作，再依据工具结果回答。
- 按标题读取：action=get，selector 传用户提到的作品标题；只有用户明确指定“第 N 部分/第 N 段”时才传 part=N。
- 不确定有哪些作品或用户泛问“最近写了什么”：先 action=list；拿到准确标题后，如需正文再 action=get。
- 讨论整篇作品时 part=0，工具会按顺序返回预算内的正文；结果若 truncated=true，可继续用 next_part 读取。
- 工具返回 success 前，不要说“我看过了/我刚检查了”；也不要先发送“我先去看看”等准备动作。直接调用工具，取得结果后一次性自然回答。
- 回复必须直接说读取结果，不要用“（翻了翻资料柜）”“（挠挠头）”之类括号动作代替结果。
- 不得把被动提示中的短片段、长期记忆或聊天印象冒充完整原文；找不到作品或部分时如实说明，并可根据 candidates 请用户进一步说明。
- 这是只读工具，不能修改、续写或删除创作。
- 用户只是让你讲一个、编一个或说一个新故事，或泛泛地让你讲“你的故事”时，不是在读取资料柜作品，不要调用此工具；只有用户明确提到你写过的故事、某篇作品、资料柜内容、原文或具体章节时才读取。
- 用户要求查看配置文件、数据文件、日志、源码、代码、脚本、插件目录或配置项时，不是在读取资料柜作品；即使文件或配置名称中包含“创作”“作品”等词，也不要调用此工具，不要把技术文件问答改写成创作原文读取失败。
""".strip()
        return f"【资料柜与自己的创作读取工具】\n{body}" if include_heading else body

    @staticmethod
    def _creative_work_inventory_query_matches(text: Any) -> bool:
        normalized = _single_line(text, 260)
        if not normalized or any(
            token in normalized
            for token in ("资料柜密码", "书架密码", "夹层密码", "抽屉密码", "输出密码", "重置密码")
        ):
            return False
        shelf_terms = ("资料柜", "书架", "作品柜", "创作柜")
        query_terms = (
            "能看到", "看得到", "能看见", "可以看到", "能不能看", "能读到",
            "看看", "看一下", "查一下", "查查", "查询", "检索", "列一下", "列出",
            "里面有什么", "有什么", "有哪些",
            "有几", "多少", "空不空", "是不是空", "还是空", "空的", "现在有",
        )
        return any(token in normalized for token in shelf_terms) and any(
            token in normalized for token in query_terms
        )

    def _creative_work_query_instruction_matches(self, text: Any) -> bool:
        normalized = _single_line(text, 260)
        if not normalized:
            return False
        technical_file_terms = (
            "配置文件", "数据文件", "日志文件", "代码文件", "项目文件", "插件文件",
            "配置项", "配置键", "配置目录", "插件目录", "文件目录", "文件夹",
            "源码", "源代码", "代码", "脚本", "仓库", "数据库", "报错日志",
        )
        technical_extensions = re.search(
            r"(?:^|[\\/\s])[^\\/\s]{1,100}\.(?:json|ya?ml|toml|ini|cfg|conf|env|py|js|ts|tsx|jsx|md|txt|log|db|sqlite3?)\b",
            normalized,
            flags=re.IGNORECASE,
        )
        if any(token in normalized for token in technical_file_terms) or technical_extensions:
            return False
        if self._creative_work_inventory_query_matches(normalized):
            return True

        # “故事”也常用于临时讲述或现场创作。只有句子同时指向一篇已经
        # 存在的作品时，才把它当作资料柜读取请求。
        if "故事" in normalized:
            existing_story_anchors = (
                "你写的", "你写过的", "你以前写的", "你之前写的", "你最近写的",
                "你创作的", "你创作过的", "自己写的", "自己创作的",
                "那篇", "这篇", "哪篇", "那部", "这部", "哪部",
                "那篇故事", "这篇故事", "哪篇故事", "那个故事", "这个故事",
                "上次的故事", "之前的故事", "资料柜里的故事", "书架里的故事",
                "故事原文", "故事正文", "故事全文", "故事片段", "故事章节",
                "故事的原文", "故事的正文", "故事的全文", "故事的片段", "故事的章节",
                "故事第", "故事写了什么", "故事写的什么", "写过什么故事",
                "写了什么故事", "创作过什么故事", "创作了什么故事",
            )
            has_existing_story_anchor = any(
                token in normalized for token in existing_story_anchors
            ) or bool(
                re.search(r"《[^》]{1,80}》", normalized)
                or re.search(r"故事.{0,12}第\s*[一二三四五六七八九十百零两\d]+\s*(?:部分|章|节|段)", normalized)
            )
            if not has_existing_story_anchor:
                return False
        work_terms = (
            "创作", "作品", "写作", "札记", "随笔", "散文", "小说", "故事",
            "诗", "歌词", "剧本", "手稿", "草稿", "正文", "片段", "章节",
        )
        query_terms = (
            "讲讲", "说说", "看看", "看一下", "读", "回顾", "总结", "内容",
            "写了什么", "写过什么", "写的什么", "创作过什么",
            "怎么看", "看待", "觉得", "想法", "为什么",
            "第", "部分", "哪一段", "这一段", "那一段", "原文", "全文",
        )
        return any(token in normalized for token in work_terms) and any(
            token in normalized for token in query_terms
        )

    @staticmethod
    def _creative_work_tool_result_payload(tool_result: Any) -> dict[str, Any]:
        """Extract the plugin JSON from AstrBot's CallToolResult wrapper."""
        pending: list[Any] = [tool_result]
        seen: set[int] = set()
        while pending and len(seen) < 24:
            value = pending.pop(0)
            if value is None:
                continue
            marker = id(value)
            if marker in seen:
                continue
            seen.add(marker)
            if isinstance(value, dict):
                if "status" in value:
                    return dict(value)
                for key in (
                    "structuredContent", "structured_content", "result", "data", "content", "text",
                ):
                    if key in value:
                        pending.append(value.get(key))
                continue
            if isinstance(value, (list, tuple)):
                pending.extend(value)
                continue
            if isinstance(value, str):
                text = value.strip()
                if text.startswith("```"):
                    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    if "status" in parsed:
                        return parsed
                    pending.append(parsed)
                continue
            for attr in (
                "structuredContent", "structured_content", "result", "data", "content", "text",
            ):
                try:
                    nested = getattr(value, attr, None)
                except Exception:
                    nested = None
                if nested is not None:
                    pending.append(nested)
        return {}

    @staticmethod
    def _record_creative_work_tool_result(
        event: AstrMessageEvent,
        tool: Any,
        tool_args: Any,
        tool_result: Any,
    ) -> bool:
        if _single_line(getattr(tool, "name", ""), 80) != "pc_view_creative_work":
            return False
        try:
            setattr(event, "private_companion_creative_work_tool_attempted", True)
            action = _single_line(
                (tool_args or {}).get("action") if isinstance(tool_args, dict) else "",
                20,
            ).lower() or "get"
            payload = LlmToolActionsMixin._creative_work_tool_result_payload(tool_result)
            success = bool(
                action in {"list", "get"}
                and _single_line(payload.get("status"), 24).lower() == "success"
                and not bool(getattr(tool_result, "isError", False))
            )
            setattr(event, "private_companion_creative_work_read_success", success)
            setattr(event, "private_companion_creative_work_tool_action", action)
            setattr(event, "private_companion_creative_work_tool_status", _single_line(payload.get("status"), 24))
            setattr(
                event,
                "private_companion_bookshelf_inventory_complete",
                bool(action == "list" and isinstance(payload.get("bookshelf"), dict)),
            )
        except Exception:
            pass
        return True

    @staticmethod
    def _strip_bookshelf_stage_directions(text: Any) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        action_terms = (
            "查", "看", "翻", "找", "确认", "检查", "扫", "数", "挠头", "挠挠头",
            "点头", "摇头", "眨眼", "歪头", "低头", "抬头", "叹气", "笑", "脸红",
            "不好意思", "认真", "仔细", "凑近", "摊手", "耸肩",
        )
        pattern = re.compile(r"(?:^|\n)\s*[（(]([^（）()\n]{1,80})[）)]\s*")

        def replace(match: re.Match[str]) -> str:
            content = match.group(1)
            return "\n" if any(token in content for token in action_terms) else match.group(0)

        cleaned = pattern.sub(replace, raw)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

    @staticmethod
    def _bookshelf_requester_is_owner(event: AstrMessageEvent, plugin: Any) -> bool:
        try:
            requester = event.get_sender_id()
        except Exception:
            requester = ""
        identity_for_event = getattr(plugin, "_event_permission_identity_id", None)
        identity = getattr(plugin, "_permission_identity_id", None)
        if callable(identity_for_event):
            try:
                requester = identity_for_event(event)
            except Exception:
                requester = ""
        elif callable(identity):
            try:
                requester = identity(requester)
            except Exception:
                requester = ""
        checker = getattr(plugin, "_is_private_companion_owner_user_id", None)
        if not requester or not callable(checker):
            return False
        try:
            return bool(checker(requester))
        except Exception:
            return False

    def _bookshelf_inventory_snapshot(
        self,
        event: AstrMessageEvent,
        projects: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        source_projects = projects
        if source_projects is None:
            raw_projects = self.data.get("creative_projects") if isinstance(getattr(self, "data", None), dict) else []
            source_projects = list(raw_projects) if isinstance(raw_projects, list) else []
        eligible = self._creative_work_project_candidates(source_projects, "")
        snapshot: dict[str, Any] = {
            "scope": "public",
            "creative_count": len(eligible),
            "creative_projects": [
                self._creative_work_project_summary(project, index)
                for index, project in enumerate(eligible[-20:], start=max(1, len(eligible) - 19))
            ],
        }
        if not self._bookshelf_requester_is_owner(event, self):
            return snapshot

        raw_diaries = self.data.get("bot_diaries") if isinstance(self.data.get("bot_diaries"), list) else []
        diaries = [item for item in raw_diaries if isinstance(item, dict)]
        raw_shelf_items = self.data.get("bookshelf_items") if isinstance(self.data.get("bookshelf_items"), list) else []
        reading_items: list[dict[str, Any]] = []
        raw_notes = self.data.get("memo_notes") if isinstance(self.data.get("memo_notes"), list) else []
        notes = [note for note in (normalize_memo_note(item) for item in raw_notes) if note]
        snapshot.update(
            {
                "scope": "owner",
                "diary_count": len(diaries),
                "reading_archive_count": len(reading_items),
                "reading_archive_titles": [
                    _single_line(item.get("title"), 80) or "未命名阅读记录"
                    for item in reading_items[-8:]
                ],
                "memo_active_count": sum(1 for note in notes if note.get("status") == "active"),
                "memo_completed_count": sum(1 for note in notes if note.get("status") == "completed"),
            }
        )
        return snapshot

    def _format_bookshelf_inventory_reply(self, event: AstrMessageEvent) -> str:
        snapshot = self._bookshelf_inventory_snapshot(event)
        creative_projects = snapshot.get("creative_projects") if isinstance(snapshot.get("creative_projects"), list) else []
        titles = [
            _single_line(item.get("title"), 60)
            for item in creative_projects[-5:]
            if isinstance(item, dict) and _single_line(item.get("title"), 60)
        ]
        creative_count = _safe_int(snapshot.get("creative_count"), 0, 0)
        sections: list[str] = []
        if creative_count:
            title_text = f"，最近的是{'、'.join(f'《{title}》' for title in titles)}" if titles else ""
            sections.append(f"创作区有 {creative_count} 篇带正文的作品{title_text}")
        else:
            sections.append("创作区暂时没有带正文的作品")
        if snapshot.get("scope") == "owner":
            sections.extend(
                (
                    f"日记本有 {_safe_int(snapshot.get('diary_count'), 0, 0)} 天记录",
                    f"资料归档有 {_safe_int(snapshot.get('reading_archive_count'), 0, 0)} 条记录",
                    f"便签区有 {_safe_int(snapshot.get('memo_active_count'), 0, 0)} 张进行中便签",
                )
            )
        return "能看到。现在" + "；".join(sections) + "。"

    def _bookshelf_reply_conflicts_with_inventory(self, event: AstrMessageEvent, text: Any) -> bool:
        cleaned = _single_line(text, 500)
        if not cleaned:
            return True
        snapshot = self._bookshelf_inventory_snapshot(event)
        visible_count = _safe_int(snapshot.get("creative_count"), 0, 0)
        if snapshot.get("scope") == "owner":
            visible_count += _safe_int(snapshot.get("diary_count"), 0, 0)
            visible_count += _safe_int(snapshot.get("reading_archive_count"), 0, 0)
            visible_count += _safe_int(snapshot.get("memo_active_count"), 0, 0)
            visible_count += _safe_int(snapshot.get("memo_completed_count"), 0, 0)
        claims_empty = bool(
            re.search(
                r"(?:资料柜|书架)?[^。！？!?\n]{0,12}(?:还是|仍然|依旧|目前|现在)?"
                r"(?:空空的|是空的|空着|什么都没有|没有东西|没东西|没有内容)",
                cleaned,
            )
        )
        return visible_count > 0 and claims_empty

    def _guard_unread_creative_work_response(self, event: AstrMessageEvent, text: Any) -> str:
        raw = str(text or "")
        if not runtime_persona_setting(self, 'enable_creative_work_read_guard', True):
            return raw
        if not bool(getattr(event, "private_companion_creative_work_tool_required", False)):
            return raw
        inbound_text = str(getattr(event, "message_str", "") or "")
        inventory_query = self._creative_work_inventory_query_matches(inbound_text)
        cleaned = self._strip_bookshelf_stage_directions(raw) if inventory_query else raw.strip()
        read_success = bool(getattr(event, "private_companion_creative_work_read_success", False))
        inventory_complete = bool(getattr(event, "private_companion_bookshelf_inventory_complete", False))
        if read_success and cleaned and not (
            inventory_query
            and (
                not inventory_complete
                or self._bookshelf_reply_conflicts_with_inventory(event, cleaned)
            )
        ):
            return cleaned
        if inventory_query:
            logger.warning(
                "资料柜查询未形成可信正文，已按本地真实库存回答: attempted=%s status=%s inventory_complete=%s session=%s",
                bool(getattr(event, "private_companion_creative_work_tool_attempted", False)),
                _single_line(getattr(event, "private_companion_creative_work_tool_status", ""), 24) or "none",
                inventory_complete,
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return self._format_bookshelf_inventory_reply(event)
        if bool(getattr(event, "private_companion_creative_work_tool_attempted", False)):
            return "我这次没能实际读取到对应的创作原文，先不凭印象乱讲。你可以再告诉我准确标题或第几部分，我读到后再认真和你说。"
        logger.warning(
            "指定创作问答未实际调用读取工具，已阻止凭片段作答: session=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
        )
        return "我这次还没能实际读取到对应的创作原文，先不凭印象乱讲。你可以再告诉我准确标题或第几部分，我读到后再认真和你说。"

    @staticmethod
    def _plaintext_tool_call_from_object(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        function = value.get("function")
        source = function if isinstance(function, dict) else value
        name = _single_line(source.get("name") or value.get("tool_name"), 80)
        known_names = {
            "pc_qzone_view_feed",
            "pc_qzone_publish_feed",
            "pc_generate_photo",
            "pc_send_current_media",
            "pc_find_reaction_image",
            "pc_manage_memo",
            "pc_manage_schedule",
            "pc_view_creative_work",
            "pc_get_group_id_by_name",
            "pc_get_user_id_by_name",
            "pc_query_relation_person",
            "pc_get_specified_group_members",
            "pc_query_interaction",
            "pc_relay_message",
            "pc_send_to_group",
            "pc_send_to_private_user",
            "pc_send_to_groups",
            "pc_send_to_private_users",
            "pc_schedule_group_relay",
            "future_task",
            "send_message_to_user",
        }
        if name not in known_names:
            return None
        parameters = source.get("parameters")
        if parameters is None:
            parameters = source.get("arguments")
        if parameters is None:
            parameters = source.get("args")
        if parameters is None:
            parameters = value.get("parameters", value.get("arguments", value.get("args", {})))
        if isinstance(parameters, str):
            try:
                parameters = json.loads(parameters)
            except Exception:
                return None
        if not isinstance(parameters, dict):
            return None
        return {"name": name, "parameters": dict(parameters)}

    @staticmethod
    def _creative_work_project_candidates(
        projects: list[dict[str, Any]],
        selector: Any,
    ) -> list[dict[str, Any]]:
        eligible = [
            item
            for item in projects
            if isinstance(item, dict)
            and str(item.get("status") or "") in {"drafting", "finished"}
            and isinstance(item.get("draft_chunks"), list)
            and any(
                isinstance(chunk, dict) and str(chunk.get("text") or "").strip()
                for chunk in item.get("draft_chunks", [])
            )
        ]
        value = _single_line(selector, 120)
        if not value:
            return eligible
        folded = value.casefold()
        exact = [
            item
            for item in eligible
            if folded
            in {
                _single_line(item.get("id"), 40).casefold(),
                _single_line(item.get("title"), 80).casefold(),
            }
        ]
        if exact:
            return exact
        contains = [
            item
            for item in eligible
            if folded in _single_line(item.get("title"), 80).casefold()
            or _single_line(item.get("title"), 80).casefold() in folded
        ]
        if contains:
            return contains
        number_match = re.fullmatch(r"(?:第\s*)?(\d+)(?:\s*(?:个|篇|项))?", value)
        if number_match:
            index = _safe_int(number_match.group(1), 0) - 1
            if 0 <= index < len(eligible):
                return [eligible[index]]
        return []

    @staticmethod
    def _creative_work_project_summary(project: dict[str, Any], index: int = 0) -> dict[str, Any]:
        chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
        valid_chunks = [
            chunk
            for chunk in chunks
            if isinstance(chunk, dict) and str(chunk.get("text") or "").strip()
        ]
        return {
            "index": index,
            "id": _single_line(project.get("id"), 40),
            "title": _single_line(project.get("title"), 80) or "未定标题",
            "work_type": _single_line(project.get("work_type"), 40) or "文本作品",
            "status": _single_line(project.get("status"), 24),
            "part_count": len(valid_chunks),
            "current_chars": _safe_int(project.get("current_chars"), 0, 0),
        }

    async def _pc_view_creative_work_impl(
        self,
        event: AstrMessageEvent,
        *,
        action: str = "get",
        selector: str = "",
        part: int = 0,
        max_chars: int = 6000,
    ) -> str:
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if not is_private:
            return json.dumps(
                {"status": "forbidden", "message": "创作正文只允许在私聊中读取。"},
                ensure_ascii=False,
            )

        normalized_action = _single_line(action, 20).lower() or "get"
        if normalized_action not in {"list", "get"}:
            return json.dumps(
                {"status": "invalid_action", "message": "action 仅支持 list/get。"},
                ensure_ascii=False,
            )
        async with self._data_lock:
            raw_projects = self.data.get("creative_projects")
            projects = list(raw_projects) if isinstance(raw_projects, list) else []
            eligible = self._creative_work_project_candidates(projects, "")
            if normalized_action == "list":
                summaries = [
                    self._creative_work_project_summary(project, index)
                    for index, project in enumerate(eligible, start=1)
                ]
                return json.dumps(
                    {
                        "status": "success",
                        "action": "list",
                        "count": len(summaries),
                        "projects": summaries[-20:],
                        "bookshelf": self._bookshelf_inventory_snapshot(event, projects),
                        "instruction": "直接依据这份真实库存回答，不要写查找动作，也不要把未列出的内容补成存在。",
                    },
                    ensure_ascii=False,
                )

            matches = self._creative_work_project_candidates(projects, selector)
            if not _single_line(selector, 120):
                matches = eligible[-1:] if eligible else []
            if not matches:
                candidates = [
                    self._creative_work_project_summary(project, index)
                    for index, project in enumerate(eligible[-10:], start=max(1, len(eligible) - 9))
                ]
                return json.dumps(
                    {
                        "status": "not_found",
                        "message": "没有找到对应的创作。",
                        "selector": _single_line(selector, 120),
                        "candidates": candidates,
                    },
                    ensure_ascii=False,
                )
            if len(matches) > 1:
                return json.dumps(
                    {
                        "status": "ambiguous",
                        "message": "匹配到多篇创作，请使用准确标题或 id 再读取。",
                        "candidates": [
                            self._creative_work_project_summary(project, index)
                            for index, project in enumerate(matches[:10], start=1)
                        ],
                    },
                    ensure_ascii=False,
                )

            project = matches[0]
            chunks = [
                chunk
                for chunk in project.get("draft_chunks", [])
                if isinstance(chunk, dict) and str(chunk.get("text") or "").strip()
            ]
            requested_part = _safe_int(part, 0, 0)
            if part and not (1 <= requested_part <= len(chunks)):
                return json.dumps(
                    {
                        "status": "part_not_found",
                        "message": f"这篇创作目前只有 {len(chunks)} 个正文部分。",
                        "title": _single_line(project.get("title"), 80) or "未定标题",
                        "part_count": len(chunks),
                    },
                    ensure_ascii=False,
                )

            budget = _safe_int(max_chars, 6000, 600, 12000)
            selected_parts: list[dict[str, Any]] = []
            used_chars = 0
            start_index = requested_part - 1 if requested_part > 0 else 0
            for index in range(start_index, len(chunks)):
                if requested_part > 0 and index != start_index:
                    break
                text_value = str(chunks[index].get("text") or "").strip()
                remaining = budget - used_chars
                if remaining <= 0:
                    break
                shown_text = text_value[:remaining]
                selected_parts.append(
                    {
                        "part": index + 1,
                        "text": shown_text,
                        "chars": len(text_value),
                        "truncated": len(shown_text) < len(text_value),
                    }
                )
                used_chars += len(shown_text)
                if len(shown_text) < len(text_value):
                    break
            last_part = selected_parts[-1]["part"] if selected_parts else 0
            truncated = bool(
                selected_parts
                and (
                    selected_parts[-1].get("truncated")
                    or (requested_part == 0 and last_part < len(chunks))
                )
            )
            payload = {
                "status": "success",
                "action": "get",
                "project": self._creative_work_project_summary(project),
                "premise": _single_line(project.get("premise"), 500),
                "tone": _single_line(project.get("tone"), 120),
                "parts": selected_parts,
                "truncated": truncated,
                "next_part": last_part + 1 if truncated and last_part < len(chunks) else 0,
                "instruction": "只能依据返回的真实正文讨论，不要补写未读取内容。",
            }
            return json.dumps(payload, ensure_ascii=False)

    def _strip_plaintext_tool_call_envelopes(self, text: Any) -> tuple[str, list[dict[str, Any]]]:
        raw = str(text or "")
        if not raw or "{" not in raw:
            return raw, []
        decoder = json.JSONDecoder()
        calls: list[dict[str, Any]] = []
        ranges: list[tuple[int, int]] = []
        cursor = 0
        while cursor < len(raw):
            start = raw.find("{", cursor)
            if start < 0:
                break
            try:
                value, consumed = decoder.raw_decode(raw[start:])
            except Exception:
                cursor = start + 1
                continue
            end = start + consumed
            call = self._plaintext_tool_call_from_object(value)
            if call is None:
                cursor = start + 1
                continue
            calls.append(call)
            ranges.append((start, end))
            cursor = end
        if not ranges:
            return raw, []
        pieces: list[str] = []
        cursor = 0
        for start, end in ranges:
            pieces.append(raw[cursor:start])
            cursor = end
        pieces.append(raw[cursor:])
        cleaned = "".join(pieces)
        cleaned = re.sub(r"</?(?:tool_call|function_call)\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(?im)^[ \t]*```(?:json)?[ \t]*$", "", cleaned)
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned, calls

    async def _recover_plaintext_photo_tool_call(
        self,
        event: AstrMessageEvent,
        resp: Any,
        text: Any,
    ) -> tuple[str, dict[str, Any] | None]:
        raw = str(text or "")
        if bool(getattr(event, "_private_companion_plaintext_tool_checked", False)):
            previous = getattr(event, "_private_companion_plaintext_tool_recovery", None)
            return raw, previous if isinstance(previous, dict) else None
        cleaned, calls = self._strip_plaintext_tool_call_envelopes(raw)
        if not calls:
            return raw, None
        setattr(event, "_private_companion_plaintext_tool_checked", True)
        logger.warning(
            "检测到模型将工具调用写入普通正文，已阻止外发: session=%s tools=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            ",".join(call.get("name", "") for call in calls),
        )
        recovery: dict[str, Any] = {
            "status": "sanitized_only",
            "sent": False,
            "tools": [call.get("name", "") for call in calls],
        }
        setattr(event, "_private_companion_plaintext_tool_recovery", recovery)
        photo_calls = [call for call in calls if call.get("name") == "pc_generate_photo"]
        if len(calls) != 1 or len(photo_calls) != 1:
            return cleaned, recovery
        try:
            called_names = getattr(resp, "tools_call_name", None)
            if isinstance(called_names, str) and called_names.strip() == "pc_generate_photo":
                recovery["status"] = "already_called"
                return cleaned, recovery
            if isinstance(called_names, (list, tuple, set)) and "pc_generate_photo" in {str(item) for item in called_names}:
                recovery["status"] = "already_called"
                return cleaned, recovery
            if self._proactive_only_blocks_passive_event(event, "pc_generate_photo"):
                recovery["status"] = "blocked"
                return cleaned, recovery
        except Exception:
            pass
        inbound_text = str(getattr(event, "message_str", "") or "")
        if not self._plaintext_photo_recovery_intent_matches(inbound_text):
            recovery["status"] = "intent_mismatch"
            return cleaned, recovery

        raw_parameters = photo_calls[0].get("parameters")
        parameters = dict(raw_parameters) if isinstance(raw_parameters, dict) else {}
        allowed_keys = {
            "prompt",
            "kind",
            "reference_image_path",
            "reference_image_paths",
            "image_size",
            "caption",
            "scene_preset",
        }
        parameters = {key: value for key, value in parameters.items() if key in allowed_keys}
        parameters["send"] = True
        try:
            result_raw = await self._pc_generate_photo_impl(event, **parameters)
            try:
                result = json.loads(result_raw) if isinstance(result_raw, str) else dict(result_raw or {})
            except Exception:
                result = {"status": "error", "sent": False, "message": "生图工具返回无法解析"}
        except Exception as exc:
            logger.error(
                "明文生图工具调用恢复失败: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(exc, 160),
                exc_info=True,
            )
            result = {"status": "error", "sent": False, "message": "图片生成调用失败"}
        sent = bool(result.get("sent"))
        recovery.update({"status": "recovered" if sent else "failed", "sent": sent, "result": result})
        setattr(event, "_private_companion_plaintext_tool_recovery", recovery)
        if sent:
            setattr(event, "_private_companion_plaintext_photo_sent", True)
            logger.info(
                "已恢复并执行明文生图工具调用: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return cleaned, recovery
        failure = _single_line(result.get("message") or result.get("actual_error") or "图片没有生成成功", 180)
        failure = _redact_outbound_secrets(failure, self)
        failure_text = f"这次图片没能发出来：{failure}" if failure else "这次图片没能发出来。"
        cleaned = "\n".join(part for part in (cleaned, failure_text) if str(part or "").strip()).strip()
        return cleaned, recovery

    @staticmethod
    def _memo_management_instruction_matches(text: Any) -> bool:
        value = str(text or "")
        return bool(
            re.search(
                r"便签|便笺|备忘录?|待办|帮我记(?:一下|下来)?|记(?:一下|下来)|"
                r"(?:确认|确定|取消)(?:删除|删掉|移除)|"
                r"(?:完成|恢复|置顶|取消置顶|删除|删掉).{0,4}(?:第?\s*\d+|这张|那张)|"
                r"第?\s*\d+(?:张|条|个)?.{0,8}(?:完成|恢复|置顶|删除|删掉|改到|改成)|"
                r"(?:只看|查看|看看).{0,4}(?:已完成|进行中|全部)",
                value,
                flags=re.I,
            )
        )

    def _remove_future_task_for_memo_request(self, req: Any, text: Any) -> bool:
        """明确的便签操作只保留便签工具，避免同轮再创建官方定时任务。"""
        if not self._memo_management_instruction_matches(text):
            return False
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return False
        has_future_task = False
        get_tool = getattr(tool_set, "get_tool", None)
        if callable(get_tool):
            try:
                has_future_task = get_tool("future_task") is not None
            except Exception:
                pass
        tools = getattr(tool_set, "tools", None)
        if not has_future_task and isinstance(tools, list):
            has_future_task = any(
                _single_line(getattr(tool, "name", ""), 120) == "future_task"
                for tool in tools
            )
        if not has_future_task:
            return False
        remove_tool = getattr(tool_set, "remove_tool", None)
        try:
            if callable(remove_tool):
                remove_tool("future_task")
            elif isinstance(tools, list):
                tool_set.tools = [
                    tool
                    for tool in tools
                    if _single_line(getattr(tool, "name", ""), 120) != "future_task"
                ]
            else:
                return False
        except Exception as exc:
            logger.warning("便签请求移除 future_task 失败: %s", _single_line(exc, 160))
            return False
        return True

    @staticmethod
    def _scope_reaction_media_tools_for_request(
        req: Any,
        *,
        explicit_media_request: bool,
        reaction_authorized: bool,
        reaction_evaluated: bool,
    ) -> list[str]:
        """Keep ordinary experimental replies on the single-pass intent path."""
        if explicit_media_request:
            return []
        # Current-media delivery is only meaningful after an explicit request
        # caused another tool to produce a local image in this same turn.
        blocked = {"pc_send_current_media"}
        # Distinguish "not evaluated" (ordinary passive turn, keep the media
        # tools for regular regeneration/expression use) from "evaluated":
        # an evaluated reaction turn must hide the automatic reaction-media
        # tools regardless of whether it was authorized. An authorized turn
        # switches to the internal response tag, while a denied turn must not
        # fall through the legacy media-tool path. Explicit media requests
        # were already returned above and keep every tool visible.
        if reaction_evaluated:
            blocked.update({"pc_generate_photo", "pc_find_reaction_image"})
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return []
        tools = getattr(tool_set, "tools", None)
        names = {
            _single_line(getattr(tool, "name", ""), 120)
            for tool in tools
        } if isinstance(tools, list) else set()
        get_tool = getattr(tool_set, "get_tool", None)
        if callable(get_tool):
            for name in blocked:
                try:
                    if get_tool(name) is not None:
                        names.add(name)
                except Exception:
                    pass
        remove_tool = getattr(tool_set, "remove_tool", None)
        removed: list[str] = []
        for name in sorted(blocked):
            if name not in names and isinstance(tools, list):
                continue
            try:
                if callable(remove_tool):
                    remove_tool(name)
                elif isinstance(tools, list):
                    tool_set.tools = [
                        tool
                        for tool in tool_set.tools
                        if _single_line(getattr(tool, "name", ""), 120) != name
                    ]
                else:
                    continue
                removed.append(name)
            except Exception as exc:
                logger.debug(
                    "裁剪实验性表情工具失败: tool=%s error=%s",
                    name,
                    _single_line(exc, 160),
                )
        return removed

    @staticmethod
    def _mark_memo_request_tool_boundary(event: AstrMessageEvent, req: Any) -> None:
        try:
            setattr(event, "private_companion_explicit_memo_request", True)
            setattr(event, "_private_companion_memo_provider_request", req)
        except Exception:
            pass

    def _finalize_memo_request_tool_boundary(self, event: AstrMessageEvent) -> bool:
        """在 AstrBot 补齐内置工具后再次执行便签/定时工具互斥。"""
        if not bool(getattr(event, "private_companion_explicit_memo_request", False)):
            return False
        req = getattr(event, "_private_companion_memo_provider_request", None)
        get_extra = getattr(event, "get_extra", None)
        if callable(get_extra):
            try:
                final_req = get_extra("provider_request")
            except Exception:
                final_req = None
            if final_req is not None:
                req = final_req
        if req is None:
            return False
        return self._remove_future_task_for_memo_request(
            req,
            getattr(event, "message_str", ""),
        )

    def _memo_management_tool_instruction(self, *, include_heading: bool = True) -> str:
        body = """主要用户在私聊里要求新增、查看、修改、完成、恢复、置顶或删除便签时，使用 `pc_manage_memo`，不要只用口头承诺代替实际操作。
- 只有用户明确说“便签/便笺/备忘/待办/帮我记一下/记下来”或正在继续操作已有便签时，才把请求路由到本工具。普通“提醒我/叫醒我/定时/半小时后通知我/别忘了”属于临时提醒，不要擅自建成便签。
- 新增：action=create，title/content 至少传一项；提醒时间传 due_at，可传 `2026-07-15 09:00`，也支持“明早9点”“两小时后”“周五下午3点”等常见表达。
- 查看：action=list；默认 status=active，可用 status=completed/all 查看已完成或全部便签，query 可按标题/正文筛选。列表正文只是预览，需要完整正文时用 action=get + selector。后续用编号操作时要传回相同 status，优先使用返回的 id。
- 修改/完成/恢复/置顶：action=update/complete/reopen/pin/unpin，并用 selector 传便签标题、编号或工具返回的 id。匹配到多张时必须让用户进一步指定，不能自行选择。
- 删除：首次 action=delete 只会返回 confirmation_required，必须让用户回复“确认删除”或“取消删除”；确认时把 confirmation_token 原样传给下一次 delete，取消时 action=cancel_delete。不能绕过确认。
- 含 due_at 且开启提醒的便签，其提醒已经由便签自身负责；成功保存后不得再调用 `future_task`，也不得再输出 `<timer>`，否则会重复提醒。
- 只有工具明确返回 `saved=true`，才能说便签已经新增、修改、完成、恢复、置顶或删除；cancel_delete 返回 `cancelled=true` 时才能说已取消删除。其他 `saved=false`、失败、歧义或等待确认必须如实说明。
- 便签是待办，不是已经发生的经历；不要把未完成事项说成用户已经做过。
""".strip()
        return f"【备忘便签工具】\n{body}" if include_heading else body

    @staticmethod
    def _schedule_management_instruction_matches(text: Any) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 240))
        if not compact:
            return False
        operation = bool(re.search(r"(重置|重做|重新细化|重新生成|刷新|取消|删除|删掉|移除|去掉)", compact))
        target = bool(
            re.search(r"(日程|行程|安排|计划|时段|时间段|这段|那段|第[一二两三四五六七八九十\d]+段)", compact)
            or re.search(r"(?:凌晨|早上|上午|中午|下午|傍晚|晚上|今晚)?(?:\d{1,2}|[一二两三四五六七八九十]+)(?:点|时|:|：).{0,10}(?:那段|的安排|的计划)", compact)
        )
        return bool(operation and target)

    def _schedule_management_tool_instruction(self, *, include_heading: bool = True) -> str:
        body = """主要用户在私聊中明确要求重置、重做、重新细化、取消或删除某一段今日日程时，使用 `pc_manage_schedule`，不要只口头承诺。
- 重新细化：action=regenerate；取消/删除/移除：action=cancel。“删除”采用取消语义，保留历史依据，但不会再作为当前活动、细化重试或主动消息契机。
- selector 必须保留用户明确给出的时间、序号或活动关键词，例如“下午三点”“第二段”“整理房间”；不要自行猜一个日程段。工具返回歧义或未命中时，把候选自然列给用户继续选择。
- 只有用户明确要求操作已有日程时才调用。普通聊天中的“我下午出门”“今晚想晚点睡”“你可以休息”等生活信息仍按对话和柔性日程调整理解，不得擅自取消或重置日程。
- 只有工具返回 `saved=true` 才能说操作已经完成；失败、歧义或未找到时必须如实说明。
""".strip()
        return f"【指定日程管理工具】\n{body}" if include_heading else body

    def _memo_tool_authorization(self, event: AstrMessageEvent) -> tuple[bool, str]:
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        try:
            identity_for_event = getattr(self, "_event_permission_identity_id", None)
            requester_id = (
                identity_for_event(event)
                if callable(identity_for_event)
                else self._permission_identity_id(event.get_sender_id())
            )
        except Exception:
            requester_id = ""
        allowed = bool(is_private and requester_id and self._is_private_companion_owner_user_id(requester_id))
        if not allowed:
            logger.info(
                "便签管理权限未通过: private=%s sender=%s umo=%s",
                is_private,
                requester_id or "-",
                _single_line(getattr(event, "unified_msg_origin", ""), 120),
            )
        return allowed, requester_id

    def _parse_memo_due_time(self, value: Any, *, now: float) -> tuple[float, str]:
        if value is None or value == "":
            return 0.0, ""
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            return (timestamp, "") if timestamp > 0 else (0.0, "提醒时间无效")

        text = _single_line(value, 100).strip()
        if not text:
            return 0.0, ""
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            timestamp = float(text)
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            return (timestamp, "") if timestamp > 0 else (0.0, "提醒时间无效")

        base = self._environment_fromtimestamp(now)
        normalized = text.replace("／", "/").replace("：", ":").strip()
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            if parsed.tzinfo is None and base.tzinfo is not None:
                parsed = parsed.replace(tzinfo=base.tzinfo)
            if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", normalized):
                parsed = parsed.replace(hour=9)
            return parsed.timestamp(), ""
        except ValueError:
            pass
        for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y/%m/%d", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(normalized, fmt)
            except ValueError:
                continue
            if parsed.tzinfo is None and base.tzinfo is not None:
                parsed = parsed.replace(tzinfo=base.tzinfo)
            if fmt in {"%Y/%m/%d", "%Y-%m-%d"}:
                parsed = parsed.replace(hour=9)
            return parsed.timestamp(), ""

        def natural_number(raw: str) -> float:
            if re.fullmatch(r"\d+(?:\.\d+)?", raw):
                return float(raw)
            digits = {
                "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
            }
            if raw == "十":
                return 10.0
            if "十" in raw:
                left, right = raw.split("十", 1)
                return float((digits.get(left, 1) * 10) + digits.get(right, 0))
            return float(digits.get(raw, 0))

        relative_day_offset: int | None = None
        duration_match = re.search(r"(\d+(?:\.\d+)?|[一二两三四五六七八九十]+)\s*(分钟|小时|天|周)后", normalized)
        if duration_match:
            amount = natural_number(duration_match.group(1))
            unit = duration_match.group(2)
            seconds = amount * {"分钟": 60, "小时": 3600, "天": 86400, "周": 7 * 86400}[unit]
            has_clock = bool(re.search(r"点|时|:\d|早上|上午|中午|下午|傍晚|晚上|凌晨", normalized))
            if unit in {"天", "周"} and has_clock and amount.is_integer():
                relative_day_offset = int(amount) * (7 if unit == "周" else 1)
            else:
                return now + seconds, ""
        if "半小时后" in normalized:
            return now + 1800, ""

        day_offset: int | None = relative_day_offset
        if "大后天" in normalized:
            day_offset = 3
        elif "后天" in normalized:
            day_offset = 2
        elif any(token in normalized for token in ("明天", "明早", "明晚", "明日下午", "明日上午")):
            day_offset = 1
        elif any(token in normalized for token in ("今天", "今早", "今晚", "今夜", "今日")):
            day_offset = 0

        target_date = (base + timedelta(days=day_offset or 0)).date()
        weekday_match = re.search(r"(下|本|这)?\s*(?:周|星期)([一二三四五六日天])", normalized)
        if weekday_match:
            target_weekday = "一二三四五六日".index("日" if weekday_match.group(2) == "天" else weekday_match.group(2))
            prefix = weekday_match.group(1) or ""
            if prefix == "下":
                days = 7 - base.weekday() + target_weekday
            else:
                days = target_weekday - base.weekday()
                if days < 0:
                    days += 7
            target_date = (base + timedelta(days=days)).date()
            day_offset = days
        else:
            month_day_match = re.search(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})(?:日|号)?", normalized)
            if month_day_match:
                year = int(month_day_match.group(1) or base.year)
                month = int(month_day_match.group(2))
                day = int(month_day_match.group(3))
                try:
                    candidate = base.replace(year=year, month=month, day=day).date()
                except ValueError:
                    return 0.0, "提醒日期无效"
                if not month_day_match.group(1) and candidate < base.date():
                    try:
                        candidate = base.replace(year=base.year + 1, month=month, day=day).date()
                    except ValueError:
                        return 0.0, "提醒日期无效"
                target_date = candidate
                day_offset = (candidate - base.date()).days

        clock_number = r"(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})"
        time_match = re.search(
            rf"(?<!\d)({clock_number})\s*(?:点|时|:)(?:\s*({clock_number})\s*分?)?",
            normalized,
        )
        has_half = bool(re.search(r"(?:点|时)\s*半", normalized))
        quarter_match = re.search(r"(?:点|时)\s*([一三])刻", normalized)
        if time_match:
            hour = int(natural_number(time_match.group(1)))
            if has_half:
                minute = 30
            elif quarter_match:
                minute = 15 if quarter_match.group(1) == "一" else 45
            else:
                minute = int(natural_number(time_match.group(2) or "0"))
            if hour > 23 or minute > 59:
                return 0.0, "提醒时间无效"
        elif day_offset is not None:
            if "凌晨" in normalized:
                hour, minute = 0, 0
            elif "中午" in normalized:
                hour, minute = 12, 0
            elif "下午" in normalized:
                hour, minute = 15, 0
            elif "傍晚" in normalized:
                hour, minute = 18, 0
            elif any(token in normalized for token in ("晚上", "今晚", "今夜", "明晚")):
                hour, minute = 20, 0
            else:
                hour, minute = 9, 0
        else:
            return 0.0, "无法识别提醒时间，请提供例如“明早9点”或“2026-07-15 09:00”"

        evening = any(token in normalized for token in ("晚上", "今晚", "今夜", "明晚"))
        if evening and hour in {0, 12}:
            hour = 0
            target_date += timedelta(days=1)
        elif any(token in normalized for token in ("下午", "傍晚")) and hour < 12:
            hour += 12
        elif evening and hour < 12:
            hour += 12
        elif "中午" in normalized and hour < 11:
            hour += 12
        elif "凌晨" in normalized and hour == 12:
            hour = 0
        try:
            parsed = base.replace(
                year=target_date.year,
                month=target_date.month,
                day=target_date.day,
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
        except ValueError:
            return 0.0, "提醒时间无效"
        if weekday_match and parsed.timestamp() <= now:
            parsed += timedelta(days=7)
        elif day_offset is None and parsed.timestamp() <= now:
            parsed += timedelta(days=1)
        return parsed.timestamp(), ""

    def _memo_tool_note_view(
        self,
        note: dict[str, Any],
        *,
        number: int = 0,
        content_limit: int = 240,
    ) -> dict[str, Any]:
        due_at = _safe_float(note.get("due_at"), 0.0)
        due_text = ""
        if due_at > 0:
            try:
                due_text = self._environment_fromtimestamp(due_at).strftime("%Y-%m-%d %H:%M")
            except Exception:
                due_text = datetime.fromtimestamp(due_at).strftime("%Y-%m-%d %H:%M")
        raw_content = str(note.get("content") or "")
        result = {
            "id": _single_line(note.get("id"), 64),
            "title": _single_line(note.get("title"), 60),
            "content": raw_content[:content_limit],
            "content_truncated": len(raw_content) > content_limit,
            "status": _single_line(note.get("status"), 20) or "active",
            "due_at": due_at,
            "due_text": due_text,
            "repeat": _single_line(note.get("repeat"), 20) or "none",
            "remind_enabled": bool(note.get("remind_enabled")),
            "pinned": bool(note.get("pinned")),
            "color": _single_line(note.get("color"), 20) or "yellow",
        }
        if number > 0:
            result["number"] = number
        return result

    def _memo_tool_find_matches(
        self,
        notes: list[dict[str, Any]],
        selector: Any,
        *,
        status: str = "",
    ) -> list[dict[str, Any]]:
        eligible = [item for item in notes if not status or item.get("status") == status]
        value = _single_line(selector, 100).strip(" \t\r\n‘’“”'\"《》【】[]")
        if not value:
            return []
        exact_id = [item for item in eligible if str(item.get("id") or "") == value]
        if exact_id:
            return exact_id
        number_match = re.fullmatch(r"第?\s*(\d+)\s*(?:张|条|个)?", value)
        if number_match:
            index = int(number_match.group(1)) - 1
            return [eligible[index]] if 0 <= index < len(eligible) else []
        folded = value.casefold()
        exact_title = [item for item in eligible if _single_line(item.get("title"), 60).casefold() == folded]
        if exact_title:
            return exact_title
        return [
            item
            for item in eligible
            if folded in f"{item.get('title', '')}\n{item.get('content', '')}".casefold()
        ]

    async def _pc_manage_memo_impl(
        self,
        event: AstrMessageEvent,
        *,
        action: str = "list",
        title: str = "",
        content: str = "",
        selector: str = "",
        due_at: Any = "",
        repeat: str = "",
        color: str = "",
        remind_enabled: bool | None = None,
        include_completed: bool = False,
        status: str = "",
        query: str = "",
        clear_due: bool = False,
        clear_content: bool = False,
        confirmation_token: str = "",
    ) -> str:
        allowed, requester_id = self._memo_tool_authorization(event)
        if not allowed:
            return json.dumps(
                {"status": "forbidden", "saved": False, "message": "便签只允许配置的主要用户在私聊中管理。"},
                ensure_ascii=False,
            )
        action_key = _single_line(action, 30).lower()
        aliases = {
            "": "list", "查看": "list", "列表": "list", "查询": "list", "list": "list",
            "详情": "get", "查看详情": "get", "get": "get",
            "新增": "create", "添加": "create", "创建": "create", "记录": "create", "create": "create", "add": "create",
            "修改": "update", "编辑": "update", "update": "update", "edit": "update",
            "完成": "complete", "办完": "complete", "complete": "complete", "done": "complete",
            "恢复": "reopen", "重新打开": "reopen", "reopen": "reopen",
            "删除": "delete", "delete": "delete", "remove": "delete",
            "取消删除": "cancel_delete", "cancel_delete": "cancel_delete", "cancel": "cancel_delete",
            "置顶": "pin", "pin": "pin", "取消置顶": "unpin", "unpin": "unpin",
        }
        action_key = aliases.get(action_key, action_key)
        if action_key not in {"list", "get", "create", "update", "complete", "reopen", "delete", "cancel_delete", "pin", "unpin"}:
            return json.dumps({"status": "invalid_action", "saved": False, "message": "不支持的便签操作"}, ensure_ascii=False)

        now = time.time()
        status_key = _single_line(status, 20).lower()
        status_aliases = {
            "": "all" if include_completed else "active",
            "active": "active", "进行中": "active", "未完成": "active", "待办": "active",
            "completed": "completed", "完成": "completed", "已完成": "completed", "历史": "completed",
            "all": "all", "全部": "all",
        }
        status_key = status_aliases.get(status_key, status_key)
        if status_key not in {"active", "completed", "all"}:
            return json.dumps({"status": "invalid_status", "saved": False, "message": "便签状态只支持 active/completed/all"}, ensure_ascii=False)
        if action_key == "list":
            async with self._data_lock:
                raw_notes = self.data.get("memo_notes")
                source_notes = raw_notes if isinstance(raw_notes, list) else []
                notes = [item for item in (normalize_memo_note(raw, now=now) for raw in source_notes) if item]
            if status_key != "all":
                notes = [item for item in notes if item.get("status") == status_key]
            query_text = _single_line(query, 100).casefold()
            if query_text:
                notes = [
                    item for item in notes
                    if query_text in f"{item.get('title', '')}\n{item.get('content', '')}".casefold()
                ]
            notes.sort(key=lambda item: memo_note_sort_key(item, now=now))
            items = [self._memo_tool_note_view(item, number=index) for index, item in enumerate(notes[:20], start=1)]
            return json.dumps(
                {
                    "status": "success",
                    "saved": False,
                    "action": "list",
                    "view": status_key,
                    "query": query_text,
                    "count": len(notes),
                    "shown_count": len(items),
                    "truncated": len(notes) > len(items),
                    "items": items,
                    "message": "当前没有便签" if not notes else f"找到 {len(notes)} 张便签",
                },
                ensure_ascii=False,
            )

        due_timestamp = 0.0
        if action_key == "create" or due_at not in (None, ""):
            due_timestamp, due_error = self._parse_memo_due_time(due_at, now=now)
            if due_error:
                return json.dumps({"status": "invalid_time", "saved": False, "message": due_error}, ensure_ascii=False)

        pending_store = getattr(self, "_memo_delete_confirmations", None)
        if not isinstance(pending_store, dict):
            pending_store = {}
            setattr(self, "_memo_delete_confirmations", pending_store)
        for token, pending in list(pending_store.items()):
            if not isinstance(pending, dict) or _safe_float(pending.get("expires_at"), 0.0) <= now:
                pending_store.pop(token, None)

        token = _single_line(confirmation_token, 100)
        if action_key == "cancel_delete":
            removable = [
                key for key, pending in pending_store.items()
                if isinstance(pending, dict)
                and pending.get("requester_id") == requester_id
                and (not token or key == token)
            ]
            for key in removable:
                pending_store.pop(key, None)
            return json.dumps(
                {
                    "status": "success" if removable else "nothing_pending",
                    "saved": False,
                    "cancelled": bool(removable),
                    "action": "cancel_delete",
                    "message": "已取消删除，便签没有变化。" if removable else "当前没有等待确认的便签删除。",
                },
                ensure_ascii=False,
            )

        confirmed_delete_id = ""
        confirmed_pending: dict[str, Any] | None = None
        if action_key == "delete" and token:
            pending = pending_store.get(token)
            if not isinstance(pending, dict) or pending.get("requester_id") != requester_id:
                return json.dumps({"status": "confirmation_expired", "saved": False, "message": "删除确认已失效，请重新指定便签。"}, ensure_ascii=False)
            confirmed_pending = pending
            confirmed_delete_id = _single_line(pending.get("note_id"), 64)

        try:
            async with self._data_lock:
                raw_notes = self.data.get("memo_notes")
                source_notes = raw_notes if isinstance(raw_notes, list) else []
                notes = [item for item in (normalize_memo_note(raw, now=now) for raw in source_notes) if item]
                notes.sort(key=lambda item: memo_note_sort_key(item, now=now))
                if action_key == "create":
                    payload: dict[str, Any] = {
                        "action": "save",
                        "title": title,
                        "content": content,
                        "due_at": due_timestamp,
                        "repeat": repeat or "none",
                        "color": color or "yellow",
                        "pinned": False,
                        "remind_enabled": True if remind_enabled is None else remind_enabled,
                    }
                    updated_notes, affected = apply_memo_note_action(
                        raw_notes,
                        payload,
                        now=now,
                        fromtimestamp=self._environment_fromtimestamp,
                    )
                else:
                    match_status = "" if status_key == "all" else status_key
                    if not status:
                        match_status = "completed" if action_key == "reopen" else "active" if action_key == "complete" else ""
                    matches = self._memo_tool_find_matches(
                        notes,
                        confirmed_delete_id or selector,
                        status=match_status,
                    )
                    if not matches:
                        return json.dumps({"status": "not_found", "saved": False, "message": "没有找到匹配的便签"}, ensure_ascii=False)
                    if len(matches) > 1:
                        return json.dumps(
                            {
                                "status": "ambiguous",
                                "saved": False,
                                "message": "匹配到多张便签，请用编号、完整标题或 id 进一步指定。",
                                "matches": [self._memo_tool_note_view(item) for item in matches[:8]],
                            },
                            ensure_ascii=False,
                        )
                    target = matches[0]
                    if action_key == "get":
                        return json.dumps(
                            {
                                "status": "success",
                                "saved": False,
                                "action": "get",
                                "note": self._memo_tool_note_view(target, content_limit=800),
                            },
                            ensure_ascii=False,
                        )
                    if confirmed_pending is not None and _safe_float(target.get("updated_at"), 0.0) != _safe_float(confirmed_pending.get("updated_at"), 0.0):
                        pending_store.pop(token, None)
                        return json.dumps(
                            {
                                "status": "confirmation_stale",
                                "saved": False,
                                "message": "便签在确认前发生了变化，请重新发起删除并确认。",
                                "note": self._memo_tool_note_view(target),
                            },
                            ensure_ascii=False,
                        )
                    if action_key == "delete" and not confirmed_delete_id:
                        token = uuid.uuid4().hex
                        pending_store[token] = {
                            "requester_id": requester_id,
                            "note_id": target.get("id"),
                            "updated_at": _safe_float(target.get("updated_at"), 0.0),
                            "expires_at": now + 180,
                        }
                        return json.dumps(
                            {
                                "status": "confirmation_required",
                                "saved": False,
                                "message": "这张便签尚未删除，请让用户回复“确认删除”或“取消删除”。",
                                "note": self._memo_tool_note_view(target),
                                "confirmation_token": token,
                                "expires_in_seconds": 180,
                            },
                            ensure_ascii=False,
                        )
                    payload = {"action": action_key, "id": target.get("id")}
                    partial = False
                    if action_key == "update":
                        payload["action"] = "save"
                        partial = True
                        if title:
                            payload["title"] = title
                        if content or clear_content:
                            payload["content"] = "" if clear_content else content
                        if due_at not in (None, "") or clear_due:
                            payload["due_at"] = 0.0 if clear_due else due_timestamp
                        if repeat:
                            payload["repeat"] = repeat
                        elif clear_due:
                            payload["repeat"] = "none"
                        if color:
                            payload["color"] = color
                        if remind_enabled is not None:
                            payload["remind_enabled"] = remind_enabled
                        if len(payload) <= 2:
                            return json.dumps({"status": "need_changes", "saved": False, "message": "没有提供要修改的内容"}, ensure_ascii=False)
                    updated_notes, affected = apply_memo_note_action(
                        raw_notes,
                        payload,
                        now=now,
                        fromtimestamp=self._environment_fromtimestamp,
                        partial=partial,
                    )

                previous_notes = raw_notes
                self.data["memo_notes"] = updated_notes
                try:
                    self._save_data_sync(sections={"memo_notes"})
                except Exception:
                    self.data["memo_notes"] = previous_notes
                    raise
            if action_key == "delete" and token:
                pending_store.pop(token, None)
            if (
                action_key in {"create", "update"}
                and isinstance(affected, dict)
                and _single_line(affected.get("status"), 20) == "active"
                and _safe_float(affected.get("due_at"), 0.0) > 0
                and bool(affected.get("remind_enabled", True))
            ):
                try:
                    setattr(event, "private_companion_memo_reminder_saved", True)
                except Exception as exc:
                    logger.warning(
                        "便签提醒已保存但无法写入本轮去重标记: user=%s error=%s",
                        requester_id,
                        _single_line(exc, 160),
                    )
                else:
                    logger.info(
                        "便签提醒已保存,本轮将抑制重复临时定时: user=%s note=%s action=%s",
                        requester_id,
                        _single_line(affected.get("id"), 64) or "-",
                        action_key,
                    )
            return json.dumps(
                {
                    "status": "success",
                    "saved": True,
                    "action": action_key,
                    "message": {
                        "create": "便签已新增",
                        "update": "便签已更新",
                        "complete": "便签已完成",
                        "reopen": "便签已恢复",
                        "delete": "便签已删除",
                        "pin": "便签已置顶",
                        "unpin": "已取消便签置顶",
                    }[action_key],
                    "note": self._memo_tool_note_view(affected),
                },
                ensure_ascii=False,
            )
        except ValueError as exc:
            return json.dumps({"status": "invalid", "saved": False, "message": str(exc)}, ensure_ascii=False)
        except Exception as exc:
            logger.error("聊天便签操作失败: %s", _single_line(exc, 160), exc_info=True)
            return json.dumps({"status": "error", "saved": False, "message": f"便签操作失败: {_single_line(exc, 120)}"}, ensure_ascii=False)

    async def _note_photo_tool_quota_attempt(
        self,
        event: AstrMessageEvent,
        *,
        requester_id: str,
        requester: dict[str, Any] | None,
        photo_scope: str,
        image_path: str = "",
    ) -> None:
        if not str(requester_id or "").strip():
            return

        def update_counters() -> bool:
            changed = False
            user = requester
            user_getter = getattr(self, "_get_user", None)
            if not isinstance(user, dict) and callable(user_getter):
                user = user_getter(requester_id)
            if photo_scope == "proactive":
                proactive_notifier = getattr(self, "_note_photo_generation_attempt", None)
                if callable(proactive_notifier):
                    proactive_notifier(requester_id, image_path=image_path)
                    changed = True
            else:
                command_notifier = getattr(self, "_note_command_photo_generation_attempt", None)
                if callable(command_notifier) and isinstance(user, dict):
                    command_notifier(user, image_path=image_path)
                    changed = True
            scope_notifier = getattr(self, "_note_photo_generation_scope_attempt", None)
            if callable(scope_notifier):
                scope_notifier(
                    event,
                    user=user if isinstance(user, dict) else None,
                    user_id=requester_id,
                    scope=photo_scope,
                )
                changed = True
            if changed:
                saver = getattr(self, "_save_data_sync", None)
                if callable(saver):
                    saver(sections={"users", "photo_generation_scope_attempts"})
            return changed

        data_lock = getattr(self, "_data_lock", None)
        if data_lock is not None:
            async with data_lock:
                update_counters()
        else:
            update_counters()

    async def _pc_generate_photo_impl(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        kind: str = "text2img",
        reference_image_path: str = "",
        reference_image_paths: Any = None,
        image_size: str = "",
        send: bool = True,
        caption: str = "",
        scene_preset: str = "",
        **kwargs,
    ) -> str:
        def public_receipt(
            payload: dict[str, Any],
            *,
            ensure_ascii: bool = False,
            known_paths: tuple[Any, ...] = (),
        ) -> str:
            return json.dumps(
                self._sanitize_photo_tool_result_payload(
                    payload,
                    known_paths=known_paths,
                ),
                ensure_ascii=ensure_ascii,
            )

        if not self._photo_generation_runtime_available():
            return public_receipt(
                {
                    "status": "unavailable",
                    "success": False,
                    "generated": False,
                    "sent": False,
                    "error_code": "image_extension_unavailable",
                    "message": "生图扩展未安装、未启用或尚未就绪。",
                    "must_not_claim_sent": True,
                    "final_response_instruction": "自然说明当前不能生成图片，不要声称图片已经生成或发送，也不要在本轮重试。",
                },
                ensure_ascii=False,
            )

        tool_started_at = time.monotonic()
        scope_getter = getattr(self, "_photo_generation_scope", None)
        initial_scope = ""
        if callable(scope_getter):
            try:
                initial_scope = _single_line(scope_getter(event), 40).lower()
            except Exception:
                initial_scope = ""
        proactive_request = bool(
            initial_scope == "proactive"
            or getattr(event, "private_companion_proactive_framework", False)
        )
        permission_getter = getattr(
            self,
            "_user_requested_photo_generation_allowed",
            None,
        )
        if callable(permission_getter):
            try:
                user_request_allowed = bool(permission_getter(event))
            except Exception:
                user_request_allowed = False
        else:
            user_request_allowed = bool(
                runtime_persona_setting(
                    self,
                    "enable_user_requested_photo_generation",
                    True,
                )
            )
        if not proactive_request and not user_request_allowed:
            return public_receipt(
                {
                    "status": "disabled",
                    "success": False,
                    "generated": False,
                    "sent": False,
                    "message": "管理员已关闭用户请求生图/改图。",
                    "must_not_claim_sent": True,
                    "retryable": False,
                },
                ensure_ascii=False,
            )
        mode = _single_line(runtime_persona_setting(self, 'natural_language_photo_generation_mode', "tool_first"), 40).lower()
        if mode == "off" and not proactive_request:
            return public_receipt({"status": "disabled", "message": "非指令生图/改图已关闭；显式指令仍可使用“陪伴 生图/自拍/改图”。"}, ensure_ascii=False)
        if not runtime_persona_setting(self, 'enable_photo_text_action', False):
            return public_receipt({"status": "disabled", "message": "主动拍照/生图能力未启用"}, ensure_ascii=False)
        scope_checker = getattr(self, "_photo_generation_scope_allowed", None)
        structured_generator = getattr(self, "_generate_photo_image_result", None)
        legacy_generator = getattr(self, "_generate_photo_image", None)
        if not callable(structured_generator) and not callable(legacy_generator):
            return public_receipt({"status": "disabled", "message": "缺少生图入口 _generate_photo_image"}, ensure_ascii=False)
        if not self._photo_text_available():
            return public_receipt({"status": "unavailable", "message": "当前没有可用生图后端，或已被负载/token 保护临时延后"}, ensure_ascii=False)

        content = _single_line(prompt or kwargs.get("text") or kwargs.get("description") or kwargs.get("prompt_text"), 900)
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=120)
        raw_kind = _single_line(kind or kwargs.get("workflow_kind") or kwargs.get("type"), 40).lower()
        if raw_kind in {"sticker", "emoji", "meme", "表情包", "贴纸"}:
            workflow_kind = "selfie"
            intent_kind = "sticker"
        elif raw_kind in {"selfie", "portrait", "自拍", "人像", "拍照", "头像", "avatar", "cos", "cosplay", "穿搭"}:
            workflow_kind = "selfie"
            intent_kind = "selfie"
        elif raw_kind in {"edit", "改图", "修图", "重绘", "p图", "P图"}:
            workflow_kind = "edit"
            intent_kind = "edit"
        else:
            workflow_kind = "text2img"
            intent_kind = "text2img"
        if not content:
            return public_receipt(
                {
                    "status": "need_prompt",
                    "message": "缺少 prompt。请把要生成的画面或修改要求传入 prompt。",
                },
                ensure_ascii=False,
            )
        compact_prompt = re.sub(r"\s+", "", content)
        group_photo_requested = _photo_group_request_matches(content)
        bot_name = re.sub(r"\s+", "", _single_line(runtime_persona_setting(self, 'bot_name', ""), 80))
        assistant_in_frame = bool(
            (bot_name and bot_name in compact_prompt)
            or any(
                token in compact_prompt
                for token in (
                    "我本人",
                    "我在画面",
                    "我站在",
                    "我坐在",
                    "我躺在",
                    "我走在",
                    "我的背影",
                    "我的侧脸",
                    "我的全身",
                    "角色本人",
                    "本人出镜",
                )
            )
            or re.search(r"\b(?:the\s+assistant|assistant\s+persona|bot\s+character)\b", content, flags=re.I)
        )
        if intent_kind == "text2img" and any(token in compact_prompt for token in ("表情包", "贴纸", "sticker", "meme")):
            workflow_kind = "selfie"
            intent_kind = "sticker"
        elif intent_kind == "text2img" and (
            self._character_photo_request_matches(content)
            or group_photo_requested
            or assistant_in_frame
            or any(
                token in compact_prompt
                for token in ("自拍", "拍照", "头像", "人像", "角色本人", "本人出镜", "露脸", "穿搭", "镜前", "cos", "COS", "cosplay")
            )
        ):
            workflow_kind = "selfie"
            intent_kind = "selfie"

        try:
            requester_id = str(event.get_sender_id())
        except Exception:
            requester_id = ""
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver) and requester_id:
            requester_id = resolver(event, requester_id)
        requester = None
        request_scope = "private"
        group_gate_message = "当前群聊未启用陪伴功能，或请求者身份不可用。"
        user_getter = getattr(self, "_get_user", None)
        if callable(user_getter):
            if not requester_id:
                return public_receipt(
                    {
                        "status": "unauthorized",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": "这个生图工具只对已启用的陪伴对象开放。",
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
            # Group senders are not private users by default.  Looking them up
            # through ``_get_user`` would create a new private record (and the
            # configured fallback nickname) before authorization can reject it.
            scope_getter = getattr(self, "_reaction_expression_scope", None)
            try:
                private_marker = getattr(event, "is_private_chat", None)
                event_is_private = (
                    bool(private_marker())
                    if callable(private_marker)
                    else (True if private_marker is None else bool(private_marker))
                )
                request_scope = (
                    _single_line(scope_getter(event), 16).casefold()
                    if callable(scope_getter)
                    else ("private" if event_is_private else "group")
                )
            except Exception:
                request_scope = "private"
            def existing_private_user(raw_id: str) -> dict[str, Any] | None:
                data = getattr(self, "data", None)
                users = data.get("users") if isinstance(data, dict) else None
                if not isinstance(users, dict):
                    return None
                normalized = _single_line(raw_id, 160)
                if not normalized:
                    return None
                canonical = normalized
                canonicalizer = getattr(self, "_canonical_private_user_id", None)
                if callable(canonicalizer):
                    try:
                        canonical = _single_line(canonicalizer(normalized), 160) or normalized
                    except Exception:
                        canonical = normalized
                for candidate_id in dict.fromkeys((normalized, canonical)):
                    candidate = users.get(candidate_id)
                    if isinstance(candidate, dict):
                        return candidate
                for candidate in users.values():
                    if not isinstance(candidate, dict):
                        continue
                    aliases = candidate.get("alias_user_ids")
                    if (
                        _single_line(candidate.get("user_id"), 160) in {normalized, canonical}
                        or isinstance(aliases, list)
                        and any(_single_line(alias, 160) in {normalized, canonical} for alias in aliases)
                    ):
                        return candidate
                return None

            data_lock = getattr(self, "_data_lock", None)
            if data_lock is not None:
                async with data_lock:
                    requester = (
                        existing_private_user(requester_id)
                        if request_scope == "group"
                        else user_getter(requester_id)
                    )
                    group_enabled = False
                    if request_scope == "group":
                        group_id_getter = getattr(self, "_extract_group_id_from_event", None)
                        group_id = group_id_getter(event) if callable(group_id_getter) else ""
                        checker = getattr(self, "_group_enabled_for_event", None)
                        group_enabled = bool(group_id and callable(checker) and checker(group_id))
                        if not runtime_persona_setting(self, "enable_group_companion", True):
                            group_gate_message = "群聊陪伴总开关未开启。"
                        elif callable(getattr(self, "_group_allowed_by_access_mode", None)) and not self._group_allowed_by_access_mode(group_id):
                            group_gate_message = "本群不在当前群聊访问名单内。"
                        elif not group_enabled:
                            group_gate_message = "本群单独停用；请在群聊面板启用本群。"
                    requester_authorized = (group_enabled if request_scope == "group" else isinstance(requester, dict))
            else:
                requester = (
                    existing_private_user(requester_id)
                    if request_scope == "group"
                    else user_getter(requester_id)
                )
                group_enabled = False
                if request_scope == "group":
                    group_id_getter = getattr(self, "_extract_group_id_from_event", None)
                    group_id = group_id_getter(event) if callable(group_id_getter) else ""
                    checker = getattr(self, "_group_enabled_for_event", None)
                    group_enabled = bool(group_id and callable(checker) and checker(group_id))
                    if not runtime_persona_setting(self, "enable_group_companion", True):
                        group_gate_message = "群聊陪伴总开关未开启。"
                    elif callable(getattr(self, "_group_allowed_by_access_mode", None)) and not self._group_allowed_by_access_mode(group_id):
                        group_gate_message = "本群不在当前群聊访问名单内。"
                    elif not group_enabled:
                        group_gate_message = "本群单独停用；请在群聊面板启用本群。"
                requester_authorized = (group_enabled if request_scope == "group" else isinstance(requester, dict))
            if not requester_authorized:
                return public_receipt(
                    {
                        "status": "unauthorized",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": group_gate_message,
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
        if requester_id and requester is None and callable(user_getter):
            data_lock = getattr(self, "_data_lock", None)
            if data_lock is not None:
                async with data_lock:
                    requester = user_getter(requester_id)
            else:
                requester = user_getter(requester_id)

        photo_scope_getter = getattr(self, "_photo_generation_scope", None)
        if callable(photo_scope_getter):
            photo_scope = photo_scope_getter(
                event,
                user=requester if isinstance(requester, dict) else None,
                user_id=requester_id,
            )
        elif bool(getattr(event, "private_companion_proactive_framework", False)):
            photo_scope = "proactive"
        elif request_scope == "group":
            photo_scope = "group"
        else:
            photo_scope = ""

        scope_quota_getter = getattr(self, "_photo_generation_scope_quota_left", None)
        scope_left = (
            scope_quota_getter(
                event,
                user=requester if isinstance(requester, dict) else None,
                user_id=requester_id,
                scope=photo_scope,
            )
            if callable(scope_quota_getter)
            else None
        )
        scope_blocked = scope_left is not None and scope_left <= 0
        if not callable(scope_quota_getter) and callable(scope_checker):
            scope_blocked = not scope_checker(
                event,
                user=requester if isinstance(requester, dict) else None,
                user_id=requester_id,
            )
        if scope_blocked:
            scope_message_getter = getattr(self, "_photo_generation_scope_quota_block_message", None)
            scope_message = (
                scope_message_getter(
                    event,
                    user=requester if isinstance(requester, dict) else None,
                    user_id=requester_id,
                    scope=photo_scope,
                )
                if callable(scope_message_getter)
                else "当前不允许在这个会话范围生图/改图，或今天该范围的额度已经用完。"
            )
            return public_receipt(
                {
                    "status": "quota_exhausted",
                    "success": False,
                    "generated": False,
                    "sent": False,
                    "message": scope_message,
                    "must_not_claim_sent": True,
                    "retryable": False,
                },
                ensure_ascii=False,
            )

        if photo_scope == "proactive" and isinstance(requester, dict):
            proactive_available = True
            photo_available = getattr(self, "_photo_text_available", None)
            if callable(photo_available):
                try:
                    proactive_available = bool(photo_available(requester))
                except TypeError:
                    proactive_available = bool(photo_available())
            if not proactive_available:
                return public_receipt(
                    {
                        "status": "quota_exhausted",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": "今天主动生图额度已经用完，或该陪伴用户不允许主动生图。",
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
        else:
            quota_getter = getattr(self, "_command_photo_quota_left", None)
            quota_left = (
                quota_getter(requester)
                if callable(quota_getter) and isinstance(requester, dict)
                else None
            )
            if quota_left is not None and quota_left <= 0:
                quota_message_getter = getattr(self, "_command_photo_quota_block_message", None)
                quota_message = (
                    quota_message_getter()
                    if callable(quota_message_getter)
                    else "当前不允许用户请求生图/改图，或今天的用户请求生图额度已经用完。"
                )
                return public_receipt(
                    {
                        "status": "quota_exhausted",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": quota_message,
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )

        def bool_arg(value: Any, default: bool = True) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "y", "on", "发送", "发出", "是"}:
                return True
            if text in {"0", "false", "no", "n", "off", "不发送", "否"}:
                return False
            return default

        send_image = bool_arg(send, True)
        if send_image:
            marker = getattr(self, "_mark_smart_imagechat_skip_proactive_emoji", None)
            if callable(marker):
                marker(event)
        reference_sources: list[str] = []

        def add_reference_source(value: Any) -> None:
            if isinstance(value, dict):
                value = value.get("path") or value.get("source") or value.get("url")
            path = _path_text(value, 1000)
            if path and path not in reference_sources:
                reference_sources.append(path)

        add_reference_source(
            reference_image_path
            or kwargs.get("reference")
            or kwargs.get("image")
            or kwargs.get("image_path")
            or kwargs.get("image_url")
        )
        raw_multi_references = (
            reference_image_paths
            if reference_image_paths is not None
            else kwargs.get("reference_images", kwargs.get("images"))
        )
        if isinstance(raw_multi_references, (list, tuple, set)):
            for raw_reference in raw_multi_references:
                add_reference_source(raw_reference)
        elif raw_multi_references:
            add_reference_source(raw_multi_references)

        if group_photo_requested:
            # 合影只能由本轮图片，或用户明确点名且已有托管参考图的关系角色授权。
            # 模型传入的路径始终不参与授权，关系角色图片仍交给下游选图器处理。
            reference_sources.clear()
            reference_path = ""
            role_reference_candidates: list[dict[str, Any]] = []
            role_reference_resolver = getattr(
                self,
                "_photo_reference_role_asset_candidates",
                None,
            )
            if bool(runtime_persona_setting(self, 'enable_photo_reference_image', False)) and callable(
                role_reference_resolver
            ):
                try:
                    resolved_candidates = role_reference_resolver(
                        request_text=content,
                    )
                    if isinstance(resolved_candidates, list):
                        role_reference_candidates = [
                            candidate
                            for candidate in resolved_candidates
                            if isinstance(candidate, dict)
                            and candidate.get("kind") == "relation_role"
                            and bool(candidate.get("role_explicit_mention"))
                            and _path_text(candidate.get("path"), 1000)
                        ]
                except Exception as exc:
                    logger.info(
                        "合影关系网角色参考图解析失败，继续检查本轮图片: %s",
                        _single_line(exc, 160),
                    )
            has_named_role_reference = bool(role_reference_candidates)
            context_resolver = getattr(self, "_photo_reference_image_from_command_context", None)
            saw_image = False
            if callable(context_resolver):
                try:
                    resolved_path, _resolved_label, saw_image = await context_resolver(event, requester_id)
                    reference_path = _path_text(resolved_path, 1000)
                except Exception as exc:
                    if not has_named_role_reference:
                        missing = _missing_optional_model_dependency(exc)
                        message = (
                            f"合影参考图解析缺少可选依赖 {missing}，请让用户重新发送或引用人物图片。"
                            if missing
                            else f"合影参考图解析失败：{_single_line(exc, 160)}"
                        )
                        return public_receipt(
                            {
                                "status": "need_reference",
                                "success": False,
                                "generated": False,
                                "sent": False,
                                "message": message,
                                "must_not_claim_sent": True,
                                "retryable": False,
                            },
                            ensure_ascii=False,
                        )
            if not reference_path and not has_named_role_reference:
                return public_receipt(
                    {
                        "status": "need_reference",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": (
                            "看到了本轮图片，但没能保存成可用的其他人物参考图；请让用户重新发送或引用人物图片。"
                            if saw_image
                            else "合影需要本轮随消息发送或引用的其他人物参考图，或明确点名已绑定可用参考图的关系网角色。Bot 单人人设图、今日穿搭图、纯文字描述或单独传入的路径都不算，已停止生成。"
                        ),
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
            if reference_path:
                add_reference_source(reference_path)

        resolver = getattr(self, "_photo_reference_source_to_stable_path", None)
        event_bound_resolver = getattr(self, "_photo_reference_event_bound_stable_path", None)
        resolved_reference_paths: list[str] = []
        for index, source in enumerate(reference_sources):
            # Keep the mixin compatible with lightweight/legacy hosts that do
            # not expose the optional reference normalizer. Full plugin hosts
            # still pass model-controlled sources through the untrusted path
            # guard below; Q5 managed assets use their separate ticketed sink.
            stable = source if not callable(resolver) else ""
            if callable(resolver):
                try:
                    stable = await resolver(source, stem=f"tool_{index + 1}", event=event, trusted=False)
                except Exception as exc:
                    logger.info(
                        "tool reference %s rejected: %s",
                        index + 1,
                        _single_line(exc, 160),
                    )
            if not stable and callable(event_bound_resolver):
                try:
                    stable = await event_bound_resolver(
                        event,
                        requester_id,
                        source,
                        stem=f"tool_event_{index + 1}",
                    )
                except Exception as exc:
                    logger.info(
                        "current-event reference %s could not be persisted: %s",
                        index + 1,
                        _single_line(exc, 160),
                    )
                if stable:
                    logger.info(
                        "accepted model reference after exact current-event source verification: index=%s",
                        index + 1,
                    )
            if not stable:
                logger.warning(
                    "model-controlled image reference rejected: source=%s",
                    _single_line(source, 200),
                )
                return public_receipt(
                    {
                        "status": "invalid_reference",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": "这张参考图不能使用。参考图只支持当前消息里的图片、插件数据目录内的图片，或公网图片链接。",
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
            resolved = stable
            if resolved and resolved not in resolved_reference_paths:
                resolved_reference_paths.append(resolved)
        reference_path = resolved_reference_paths[0] if resolved_reference_paths else ""
        if intent_kind == "edit" and not reference_path:
            context_resolver = getattr(self, "_photo_reference_image_from_command_context", None)
            if callable(context_resolver):
                try:
                    try:
                        user_id = str(event.get_sender_id())
                    except Exception:
                        user_id = ""
                    resolved_path, resolved_label, saw_image = await context_resolver(event, user_id)
                    if resolved_path:
                        reference_path = resolved_path
                        resolved_reference_paths = [resolved_path]
                    elif saw_image:
                        return public_receipt(
                            {
                                "status": "need_reference",
                                "message": "看到了图片，但没能保存成可用参考图；请让用户重新发送图片，或用“陪伴 参考图 查看”检查平台是否能取到原图。",
                            },
                            ensure_ascii=False,
                        )
                except Exception as exc:
                    missing = _missing_optional_model_dependency(exc)
                    if missing:
                        return public_receipt(
                            {
                                "status": "need_reference",
                                "message": f"改图参考图解析缺少可选依赖 {missing}，请让用户直接提供本地图片路径或图片 URL。",
                            },
                            ensure_ascii=False,
                        )
                    return public_receipt(
                        {"status": "error", "message": f"改图参考图解析失败：{_single_line(exc, 160)}"},
                        ensure_ascii=False,
                    )
            if not reference_path:
                return public_receipt(
                    {
                        "status": "need_reference",
                        "message": "改图/重绘需要参考图。可以让用户把图片和要求一起发，或引用近期图片再说“改成……”。",
                    },
                    ensure_ascii=False,
                )
        if not reference_path and intent_kind in {"selfie", "sticker"}:
            wants_indexed_references = bool(
                re.search(
                    r"(?:第(?:[一二三四五六七八九十\d]+)张|"
                    r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth)\s+"
                    r"(?:image|photo|picture))",
                    compact_prompt,
                    flags=re.I,
                )
            )
            try:
                try:
                    user_id = str(event.get_sender_id())
                except Exception:
                    user_id = ""
                saw_image = False
                if wants_indexed_references:
                    multi_resolver = getattr(
                        self,
                        "_photo_reference_images_from_command_context",
                        None,
                    )
                else:
                    multi_resolver = None
                if callable(multi_resolver):
                    images, saw_image = await multi_resolver(event, user_id, limit=8)
                    resolved_reference_paths = [
                        _path_text(item[0], 1000)
                        for item in images
                        if isinstance(item, (list, tuple))
                        and item
                        and _path_text(item[0], 1000)
                    ]
                    if resolved_reference_paths:
                        reference_path = resolved_reference_paths[0]
                else:
                    context_resolver = getattr(
                        self,
                        "_photo_reference_image_from_command_context",
                        None,
                    )
                    if callable(context_resolver):
                        resolved_path, resolved_label, saw_image = await context_resolver(event, user_id)
                        if resolved_path:
                            reference_path = resolved_path
                            resolved_reference_paths = [resolved_path]
                if saw_image and not resolved_reference_paths:
                    return public_receipt(
                        {
                            "status": "need_reference",
                            "message": "看到了图片，但没能保存成可用参考图；请让用户重新发送图片，或用“陪伴 参考图 查看”检查平台是否能取到原图。",
                        },
                        ensure_ascii=False,
                    )
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if missing:
                    return public_receipt(
                        {
                            "status": "need_reference",
                            "message": f"参考图解析缺少可选依赖 {missing}；如已开启参考图一致性，会改用已配置的人设参考图或今日穿搭图。",
                        },
                        ensure_ascii=False,
                    )
                return public_receipt(
                    {"status": "error", "message": f"参考图解析失败：{_single_line(exc, 160)}"},
                    ensure_ascii=False,
                )
        prompt_format_mode = ""
        prompt_format_getter = getattr(self, "_photo_generation_prompt_format_mode", None)
        if callable(prompt_format_getter):
            try:
                prompt_format_mode = (
                    _single_line(prompt_format_getter(), 40).lower() or "traditional"
                )
            except Exception as exc:
                logger.debug(
                    "tool 生图读取提示词格式失败，保留原始提示词: %s",
                    _single_line(exc, 160),
                )
                prompt_format_mode = "traditional"
        prompt_builder = getattr(self, "_build_natural_language_photo_prompt", None)
        use_natural_prompt_builder = not callable(prompt_format_getter) or prompt_format_mode in {
            "natural_language",
            "natural",
            "prose",
            "description",
            "自然语言",
            "自然语言描述",
        }
        if callable(prompt_builder) and use_natural_prompt_builder:
            prompt_sections = prompt_builder(
                prompt=content,
                kind="selfie" if intent_kind == "sticker" else intent_kind,
                has_reference=bool(resolved_reference_paths),
                memory_context="",
                structured=True,
            )
            prompt_text = content
        else:
            prompt_sections = None
            prompt_text = content
        preset_text = _single_line(scene_preset or kwargs.get("preset") or kwargs.get("scene"), 80)
        workflow_default_preset = "表情包场景" if intent_kind == "sticker" else ""

        event_umo = _single_line(getattr(event, "unified_msg_origin", ""), 240)
        session_key = event_umo or "tool_photo"
        continuity_composer = getattr(self, "_compose_photo_continuity_key", None)
        continuity_key = (
            continuity_composer(event_umo, requester_id)
            if callable(continuity_composer)
            else ""
        )
        generation_session_key = f"tool_photo_{session_key}"
        outer_timeout = self._photo_tool_call_timeout_seconds()
        timeout_margin = max(2.0, min(8.0, outer_timeout * 0.1))
        generation_timeout = outer_timeout - (time.monotonic() - tool_started_at) - timeout_margin
        if generation_timeout <= 0:
            generation_timeout = 0.01
        generation_kwargs = {
            "workflow_kind": workflow_kind,
            "prompt_text": prompt_text,
            "request_text": content,
            "session_key": generation_session_key,
            "continuity_key": continuity_key,
            "requester_user_id": requester_id,
            "requester_is_private": bool(
                (getattr(event, "is_private_chat", lambda: False)() if callable(getattr(event, "is_private_chat", None)) else getattr(event, "is_private_chat", False))
            ),
            "reference_image_path": reference_path,
            "reference_image_paths": list(resolved_reference_paths),
            "image_size": _single_line(image_size or kwargs.get("size"), 40),
            "requested_scene_preset": preset_text,
            "suggested_scene_preset": preset_text,
            "workflow_default_scene_preset": workflow_default_preset,
            "prompt_sections": prompt_sections,
        }
        if callable(prompt_format_getter):
            generation_kwargs["prompt_format"] = prompt_format_mode
        try:
            generation_output = await asyncio.wait_for(
                structured_generator(**generation_kwargs)
                if callable(structured_generator)
                else legacy_generator(**generation_kwargs),
                timeout=generation_timeout,
            )
        except asyncio.TimeoutError:
            actual_error = (
                f"生图未能在 AstrBot 工具调用时限 {outer_timeout:g} 秒内完成；"
                "本次工具调用没有生成或发送图片。"
            )
            logger.warning(
                "pc_generate_photo 在外层工具超时前主动结束: session=%s timeout=%.1fs budget=%.1fs",
                session_key,
                outer_timeout,
                generation_timeout,
            )
            await self._note_photo_tool_quota_attempt(
                event,
                requester_id=requester_id,
                requester=requester if isinstance(requester, dict) else None,
                photo_scope=photo_scope,
                image_path="",
            )
            return public_receipt(
                {
                    "status": "timeout",
                    "success": False,
                    "generated": False,
                    "send_requested": send_image,
                    "sent": False,
                    "message": actual_error,
                    "actual_error": actual_error,
                    "actionable_hint": "请如实告诉用户本次没有出图、没有发送；不要声称已经发出。可稍后重试，或让管理员提高 AstrBot tool_call_timeout/缩短生图后端超时。",
                    "must_not_claim_sent": True,
                    "retryable": True,
                },
                ensure_ascii=False,
            )
        generation_metadata: dict[str, Any] = {}
        if hasattr(generation_output, "as_legacy_tuple"):
            backend_name, image_path, note = generation_output.as_legacy_tuple()
            generation_metadata = {
                "trace_id": _single_line(getattr(generation_output, "trace_id", ""), 80),
                "reference_used": bool(getattr(generation_output, "reference_used", False)),
                "reference_path": _path_text(getattr(generation_output, "reference_selected_path", ""), 1000),
                "reference_id": _single_line(getattr(generation_output, "reference_id", ""), 60),
                "reference_kind": _single_line(getattr(generation_output, "reference_kind", ""), 40),
                "reference_roles": list(getattr(generation_output, "reference_roles", ()) or ()),
                "wardrobe_mode": _single_line(getattr(generation_output, "wardrobe_mode", ""), 40),
                "wardrobe_category": _single_line(getattr(generation_output, "wardrobe_category", ""), 40),
                "outfit_locked": bool(getattr(generation_output, "outfit_locked", False)),
                "daily_outfit_removed": bool(getattr(generation_output, "daily_outfit_removed", False)),
                "preset_names": list(getattr(generation_output, "preset_names", ()) or ()),
                "preset_hint": _single_line(getattr(generation_output, "preset_hint", ""), 80),
                "preset_source": _single_line(getattr(generation_output, "preset_source", ""), 40),
                "suggestion_status": _single_line(getattr(generation_output, "suggestion_status", ""), 60),
                "prompt_hash": _single_line(getattr(generation_output, "prompt_hash", ""), 80),
                "prompt_path": _single_line(getattr(generation_output, "prompt_path", ""), 1000),
                "reference_requested_roles": list(getattr(generation_output, "reference_requested_roles", ()) or ()),
                "reference_excluded_roles": list(getattr(generation_output, "reference_excluded_roles", ()) or ()),
                "continuity_mode": _single_line(getattr(generation_output, "continuity_mode", ""), 30),
                "reference_confidence": getattr(generation_output, "reference_confidence", 0.0),
                "reference_plan": list(getattr(generation_output, "reference_plan", ()) or ()),
                "reference_fulfilled_roles": list(getattr(generation_output, "reference_fulfilled_roles", ()) or ()),
                "reference_missing_roles": list(getattr(generation_output, "reference_missing_roles", ()) or ()),
                "reference_fallback_message": _single_line(getattr(generation_output, "reference_fallback_message", ""), 260),
                # A provider may have accepted and generated the image while
                # its result URL could not be materialized locally. Keep that
                # state separate from ``generated`` (which means a usable
                # local file) so the reply model receives an accurate receipt.
                "generation_completed": bool(getattr(generation_output, "generation_completed", False)),
                "failure_stage": _single_line(getattr(generation_output, "failure_stage", ""), 40),
            }
        else:
            backend_name, image_path, note = generation_output
            metadata_getter = getattr(self, "_photo_generation_result_metadata", None)
            if callable(metadata_getter):
                generation_metadata = metadata_getter(
                    image_path=image_path,
                    session_key=generation_session_key,
                ) or {}
        generation_completed = bool(generation_metadata.get("generation_completed"))
        failure_stage = _single_line(generation_metadata.get("failure_stage"), 40)
        reference_usage_known = "reference_used" in generation_metadata
        actual_reference_path = _path_text(
            generation_metadata.get("reference_path") or reference_path,
            1000,
        )
        used_reference = bool(generation_metadata.get("reference_used"))
        final_presets = [
            _single_line(value, 60)
            for value in (
                generation_metadata.get("preset_names")
                or generation_metadata.get("presets")
                or []
            )
            if _single_line(value, 60)
        ][:1]
        final_scene_preset = final_presets[0] if final_presets else ""
        ok = bool(image_path and os.path.exists(image_path))
        annotator = getattr(self, "_annotate_recent_photo_generation", None)
        if callable(annotator):
            annotator(
                image_path=image_path,
                session_key=generation_session_key,
                trigger="llm_tool",
                intent_kind=intent_kind,
                sent=False,
                caption=visible_caption,
                preset_hint=preset_text,
                tool_name="pc_generate_photo",
            )
        billable_attempt = bool(ok or generation_completed)
        failure_counter = getattr(self, "_photo_generation_failure_counts_as_attempt", None)
        if not billable_attempt and callable(failure_counter):
            billable_attempt = bool(failure_counter(note))
        if billable_attempt:
            await self._note_photo_tool_quota_attempt(
                event,
                requester_id=requester_id,
                requester=requester if isinstance(requester, dict) else None,
                photo_scope=photo_scope,
                image_path=image_path if ok else "",
            )
        sent = False
        delivery_deferred = False
        delivery: dict[str, Any] = {}
        generation_trace_id = _single_line(generation_metadata.get("trace_id"), 80)
        if ok and send_image:
            # 图片本身就是成功结果。纯状态 caption 不应成为可见回执；
            # 只有包含实际语境信息的自然正文才随图发送。
            usable_caption = "" if self._photo_caption_is_generic(visible_caption) else visible_caption
            message = usable_caption
            fallback_message = _single_line(
                generation_metadata.get("reference_fallback_message"),
                260,
            )
            if fallback_message:
                message = f"{message}\n{fallback_message}".strip()
            trace_writer = getattr(self, "_append_photo_generation_trace_event", None)
            if callable(trace_writer):
                trace_writer(
                    generation_trace_id,
                    "delivery_started",
                    data={"caption": message, "image_path": image_path},
                )
            delivery_deferred = bool(
                getattr(event, "private_companion_proactive_framework", False)
            )
            if delivery_deferred:
                delivery = {
                    "sent": False,
                    "destination": "proactive_framework",
                    "message": "图片已生成，等待主动消息发送链统一投递",
                    "deferred": True,
                }
                try:
                    setattr(event, "_private_companion_photo_tool_deferred", True)
                    setattr(event, "_private_companion_photo_tool_deferred_path", image_path)
                    setattr(event, "_private_companion_photo_tool_deferred_caption", message)
                    setattr(event, "_private_companion_photo_tool_deferred_intent_kind", intent_kind)
                except Exception:
                    pass
                logger.info(
                    "pc_generate_photo 成图已交由主动发送链统一投递: session=%s kind=%s",
                    session_key,
                    intent_kind,
                )
            else:
                try:
                    delivery = await self._deliver_generated_image_to_event(
                        event,
                        image_path=image_path,
                        caption=message,
                    )
                except Exception as exc:
                    delivery = {
                        "sent": False,
                        "destination": "error",
                        "message": f"图片发送失败：{_single_line(exc, 180) or '未知错误'}",
                    }
                    logger.warning(
                        "pc_generate_photo 图片投递异常: session=%s err=%s",
                        session_key,
                        _single_line(exc, 180),
                    )
            sent = bool(delivery.get("sent"))
            if callable(trace_writer):
                trace_writer(
                    generation_trace_id,
                    "delivery_deferred"
                    if delivery_deferred
                    else "delivery_completed"
                    if sent
                    else "delivery_failed",
                    status="ok" if sent or delivery_deferred else "error",
                    data={
                        "sent": sent,
                        "deferred": delivery_deferred,
                        "destination": delivery.get("destination"),
                        "message": delivery.get("message"),
                        "review_label": delivery.get("review_label"),
                    },
                )
            if sent:
                try:
                    setattr(event, "_private_companion_photo_tool_sent", True)
                    setattr(event, "_private_companion_photo_tool_sent_caption", message)
                except Exception:
                    pass
        if callable(annotator):
            annotator(
                image_path=image_path,
                session_key=generation_session_key,
                trigger="llm_tool",
                intent_kind=intent_kind,
                sent=sent,
                caption=visible_caption,
                preset_hint=preset_text,
                tool_name="pc_generate_photo",
            )
        if ok:
            memory_recorder = getattr(self, "_memory_companion_record_photo_generation", None)
            if callable(memory_recorder):
                await memory_recorder(
                    event,
                    prompt=content,
                    kind=workflow_kind,
                    intent_kind=intent_kind,
                    backend=backend_name,
                    image_path=image_path,
                    note=note,
                    sent=sent,
                    trigger="llm_tool",
                    scene_preset=final_scene_preset,
                    reference_image_path=actual_reference_path,
                    reference_used=used_reference if reference_usage_known else None,
                )
        delivery_uncertain = bool(delivery.get("uncertain"))
        overall_success = bool(ok and (not send_image or sent or delivery_deferred))
        public_reference_plan = [
            {
                key: value
                for key, value in binding.items()
                if key in {"reference_id", "roles", "priority", "preserve", "ignore", "submitted"}
            }
            for binding in (generation_metadata.get("reference_plan") or [])[:8]
            if isinstance(binding, dict)
        ]
        result_payload = {
            "status": (
                "success"
                if overall_success
                else "delivery_uncertain"
                if ok and send_image and delivery_uncertain
                else "delivery_failed"
                if ok
                else "result_retrieval_failed"
                if generation_completed and failure_stage == "result_materialization"
                else "error"
            ),
            "success": overall_success,
            "generated": ok,
            "generation_completed": generation_completed,
            "failure_stage": failure_stage,
            "send_requested": send_image,
            "message": (
                _single_line(delivery.get("message"), 220)
                if ok and send_image and delivery
                else ("图片已生成但按请求未发送" if ok and not send_image else (
                    "上游已完成生图，但图片结果没有成功取回，未发送。"
                    if generation_completed and failure_stage == "result_materialization"
                    else (_single_line(note, 220) or "生图失败")
                ))
            ),
            "backend": _single_line(backend_name, 80),
            "kind": workflow_kind,
            "intent_kind": intent_kind,
            "used_reference": used_reference,
            "reference_id": _single_line(generation_metadata.get("reference_id"), 60),
            "reference_kind": _single_line(generation_metadata.get("reference_kind"), 40),
            "reference_roles": list(generation_metadata.get("reference_roles") or [])[:8],
            "reference_intent": {
                "requested_roles": list(generation_metadata.get("reference_requested_roles") or [])[:8],
                "excluded_roles": list(generation_metadata.get("reference_excluded_roles") or [])[:8],
                "continuity_mode": _single_line(generation_metadata.get("continuity_mode"), 30),
                "confidence": generation_metadata.get("reference_confidence", 0.0),
            },
            "reference_plan": public_reference_plan,
            "reference_fulfilled_roles": list(generation_metadata.get("reference_fulfilled_roles") or [])[:8],
            "reference_missing_roles": list(generation_metadata.get("reference_missing_roles") or [])[:8],
            "reference_fallback_message": _single_line(generation_metadata.get("reference_fallback_message"), 260),
            "wardrobe_mode": _single_line(generation_metadata.get("wardrobe_mode"), 40),
            "wardrobe_category": _single_line(generation_metadata.get("wardrobe_category"), 40),
            "outfit_locked": bool(generation_metadata.get("outfit_locked")),
            "daily_outfit_removed": bool(generation_metadata.get("daily_outfit_removed")),
            "preset_hint": preset_text,
            "preset_source": _single_line(generation_metadata.get("preset_source"), 40),
            "suggestion_status": _single_line(generation_metadata.get("suggestion_status"), 60),
            "final_presets": final_presets,
            "prompt_hash": _single_line(generation_metadata.get("prompt_hash"), 80),
            "sent": sent,
            "delivery_deferred": delivery_deferred,
            "delivery_uncertain": delivery_uncertain,
            "delivery": _single_line(delivery.get("destination"), 30),
            "safety_review": _single_line(delivery.get("review_label"), 30),
            "note": _single_line(note, 220),
            "must_not_claim_sent": not sent,
            "same_turn_retry_allowed": False,
            "final_response_instruction": (
                f"图片及可选的自然 caption 已作为本轮唯一可见回复发送。最终回复不要留空，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                if sent
                else f"图片已生成并交给主动发送链；只有非回执的自然 caption 才会随图发送。不要输出状态回执，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                if delivery_deferred
                else ""
            ),
        }
        if ok and send_image and not sent and not delivery_deferred:
            delivery_error = _single_line(delivery.get("message"), 360) or "图片发送失败"
            result_payload.update(
                {
                    "failure_stage": "delivery",
                    "delivery_error": delivery_error,
                    "actual_error": delivery_error,
                    "actionable_hint": (
                        "图片已经提交给平台，但发送回执未确认。不要断言用户已收到，也不要断言发送失败；"
                        "如需回复，只能简短说明回执未确认并请用户查看，绝对不要立即再次发送。"
                        if delivery_uncertain
                        else "图片文件已经生成，但用户没有收到图片。请明确说发送失败，绝对不能说已经发出。"
                    ),
                    "retryable": not delivery_uncertain,
                }
            )
        elif not ok:
            note_text = _single_line(note, 360) or "生图失败"
            lowered_note = note_text.lower()
            upstream_submission_unconfirmed = bool(
                re.search(r"HTTP\s*(?:500|502|503|504)\b", note_text, flags=re.I)
                or "上游生图服务临时失败" in note_text
                or "网关中断" in note_text
                or ("在线图片 API" in note_text and "超时" in note_text)
            )
            hint = "请按 actual_error 里的真实原因回复用户，不要改写成未出现的超时、排队或权限问题。"
            policy_refusal = self._photo_generation_policy_refusal(note_text)
            if policy_refusal:
                public_error = "图片服务拒绝了这次画面描述，本次没有生成或发送图片。"
                logger.warning(
                    "pc_generate_photo 被图片服务策略拒绝: backend=%s error=%s",
                    _single_line(backend_name, 80),
                    note_text,
                )
                result_payload.update(
                    {
                        "message": public_error,
                        "note": public_error,
                        "error_code": "provider_policy_refusal",
                        "failure_reason": public_error,
                        "actual_error": public_error,
                        "actionable_hint": "请用当前人格简短说明这次没有生成出来，并自然询问用户是否换一种画面描述重试；不要复述 Provider 原文、政策名称、敏感词判断或链接。",
                        "do_not_claim_timeout": True,
                        "must_not_claim_sent": True,
                        "retryable": True,
                        "final_response_instruction": "不要复述或翻译 Provider 的英文原文、政策名称、敏感词判断和链接。只用符合当前人格的一句简短中文说明这次没有生成出来，再自然询问是否换一种画面描述重试。",
                    }
                )
            elif "404" in note_text or "not found" in lowered_note or "未找到" in note_text:
                hint = "在线生图接口返回 404，通常是 API 地址端点不对或缺少 /v1；请让用户检查在线图片 API 地址是否支持 /images/generations。"
            elif "图片模型" in note_text or "image model" in lowered_note:
                hint = "当前模型可能不是生图模型；请让用户把在线图片模型改成对应平台的图片模型。"
            elif "api key" in lowered_note or "unauthorized" in lowered_note or "401" in note_text or "403" in note_text:
                hint = "请让用户检查在线图片 API Key、权限和额度。"
            if not policy_refusal:
                result_payload.update(
                    {
                        "failure_reason": note_text,
                        "actual_error": note_text,
                        "actionable_hint": hint,
                        "do_not_claim_timeout": "超时" not in note_text and "timeout" not in lowered_note,
                        "must_not_claim_sent": True,
                    }
                )
            if upstream_submission_unconfirmed:
                result_payload.update(
                    {
                        "status": "submission_unconfirmed",
                        "failure_stage": "upstream_response",
                        "retryable": False,
                        "same_turn_retry_allowed": False,
                        "possible_upstream_execution": True,
                        "actionable_hint": (
                            "网关失败不代表上游任务没有执行，且可能已经计费。"
                            "本轮绝对不要重新调用任何生图工具；请用户先检查服务端任务或账单，稍后再明确决定是否重试。"
                        ),
                        "final_response_instruction": (
                            "简短说明本次没有取回图片，但上游可能仍在执行；不要声称确定失败，"
                            "不要自动重试，也不要建议用户立刻重复提交。"
                        ),
                    }
                )
            if generation_completed and failure_stage == "result_materialization":
                retrieval_message = (
                    "上游已经完成生图，但返回的图片结果未能取回或保存到本地；"
                    "本轮没有发送图片，也不要再次提交同一生图请求。"
                )
                result_payload.update(
                    {
                        "status": "result_retrieval_failed",
                        "message": retrieval_message,
                        "note": retrieval_message,
                        "failure_reason": retrieval_message,
                        "actual_error": note_text,
                        "failure_stage": "result_materialization",
                        "upstream_generated": True,
                        "retryable": False,
                        "same_turn_retry_allowed": False,
                        "actionable_hint": (
                            "如实说明上游已生成但图片结果取回失败，未发送；"
                            "不要说成上游生图请求失败，也不要在本轮再次调用 pc_generate_photo。"
                        ),
                        "final_response_instruction": (
                            "本轮不要再次调用 pc_generate_photo。简短说明图片结果取回失败、没有发送；"
                            "不要声称用户已经收到图片。用户下一轮明确要求时再重试。"
                        ),
                    }
                )
        known_private_paths: list[Any] = [
            image_path,
            actual_reference_path,
            generation_metadata.get("prompt_path"),
            *resolved_reference_paths,
        ]
        for binding in generation_metadata.get("reference_plan") or []:
            if not isinstance(binding, dict):
                continue
            known_private_paths.extend(
                value
                for key, value in binding.items()
                if "path" in str(key or "").lower()
            )
        return public_receipt(
            result_payload,
            ensure_ascii=False,
            known_paths=tuple(known_private_paths),
        )

    @staticmethod
    def _reaction_expression_bool_arg(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on", "是", "发送"}:
            return True
        if normalized in {"0", "false", "no", "off", "否", "不发送"}:
            return False
        return default

    @staticmethod
    def _reaction_expression_scope(event: Any) -> str:
        checker = getattr(event, "is_private_chat", None)
        if callable(checker):
            try:
                if bool(checker()):
                    return "private"
            except Exception:
                pass
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if ":FriendMessage:" in origin:
            return "private"
        if ":GroupMessage:" in origin:
            return "group"
        return "group" if callable(checker) else "unknown"

    @classmethod
    def _reaction_expression_scope_key(cls, event: Any, user_id: str = "") -> str:
        origin = _single_line(getattr(event, "unified_msg_origin", ""), 240)
        if origin:
            return origin
        scope = cls._reaction_expression_scope(event)
        return f"{scope}:{_single_line(user_id, 160) or 'unknown'}"

    def _reaction_expression_state_owner(
        self,
        event: Any,
        user_id: Any,
        *,
        create: bool = True,
        scope: str = "",
        scope_key: str = "",
    ) -> dict[str, Any] | None:
        """Return the state owner without turning group senders into private users."""
        normalized_id = _single_line(user_id, 160)
        if not normalized_id:
            return None
        resolved_scope = _single_line(scope, 16).casefold()
        if not resolved_scope:
            resolved_scope = self._reaction_expression_scope(event) if event is not None else "private"
        if resolved_scope != "group":
            if event is not None:
                normalized_id = self._reaction_expression_event_storage_id(event, normalized_id)
            getter = getattr(self, "_get_user", None)
            if not callable(getter):
                return None
            try:
                owner = getter(normalized_id)
            except Exception:
                return None
            return owner if isinstance(owner, dict) else None

        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return None
        resolved_scope_key = _single_line(scope_key, 240)
        if not resolved_scope_key:
            resolved_scope_key = self._reaction_expression_scope_key(event, normalized_id)
        state_key = _single_line(f"{resolved_scope_key}|sender:{normalized_id}", 420)
        if not state_key:
            return None
        states = data.get("reaction_expression_group_states")
        if not isinstance(states, dict):
            if not create:
                return None
            states = {}
            data["reaction_expression_group_states"] = states
        owner = states.get(state_key)
        if not isinstance(owner, dict):
            if not create:
                return None
            owner = {}
            states[state_key] = owner
        return owner

    @staticmethod
    def _reaction_expression_authorization(event: Any) -> dict[str, Any]:
        raw = getattr(
            event,
            "_private_companion_reaction_expression_authorization",
            None,
        )
        if isinstance(raw, dict):
            return raw
        getter = getattr(event, "get_extra", None)
        if callable(getter):
            try:
                raw = getter("private_companion_reaction_expression_authorization")
            except Exception:
                raw = None
        if not isinstance(raw, dict):
            extras = getattr(event, "extras", None)
            raw = (
                extras.get("private_companion_reaction_expression_authorization")
                if isinstance(extras, dict)
                else None
            )
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _set_reaction_expression_authorization(
        event: Any, authorization: dict[str, Any]
    ) -> None:
        try:
            setattr(
                event,
                "_private_companion_reaction_expression_authorization",
                authorization,
            )
        except Exception:
            pass
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            try:
                setter(
                    "private_companion_reaction_expression_authorization",
                    authorization,
                )
            except Exception:
                pass

    def _reaction_expression_trace_id(self, event: Any) -> str:
        authorization = self._reaction_expression_authorization(event)
        trace_id = _single_line(
            authorization.get("trace_id") or authorization.get("nonce"),
            12,
        ).casefold()
        if not re.fullmatch(r"[0-9a-f]{12}", trace_id):
            trace_id = _single_line(
                getattr(
                    event,
                    "_private_companion_reaction_expression_trace_id",
                    "",
                ),
                12,
            ).casefold()
        if not re.fullmatch(r"[0-9a-f]{12}", trace_id):
            trace_id = uuid.uuid4().hex[:12]
            try:
                setattr(
                    event,
                    "_private_companion_reaction_expression_trace_id",
                    trace_id,
                )
            except Exception:
                pass
        return trace_id

    def _log_reaction_expression_event(
        self,
        event: Any,
        *,
        stage: str,
        decision: str,
        trace_id: Any = "",
        reason: Any = "",
        scope: Any = "",
        status: Any = "",
        found: bool | None = None,
        sent: bool | None = None,
        image_id: Any = "",
        confidence: Any = None,
        cache_hit: bool | None = None,
        latency_ms: Any = None,
        delivery: Any = "",
        match_basis: Any = "",
        error_type: Any = "",
        feedback_signal: Any = "",
        feedback_score: Any = None,
        trigger_mode: Any = "",
        trigger_confidence: Any = None,
        configured_probability: Any = None,
        effective_probability: Any = None,
        cooldown_seconds: Any = None,
    ) -> None:
        """Write one privacy-safe, correlation-friendly reaction runtime event."""

        def safe_code(value: Any, allowed: frozenset[str]) -> str:
            normalized = _single_line(value, 80).casefold()
            if not normalized:
                return ""
            return normalized if normalized in allowed else "other"

        try:
            normalized_trace_id = _single_line(trace_id, 12).casefold()
            if not re.fullmatch(r"[0-9a-f]{12}", normalized_trace_id):
                normalized_trace_id = self._reaction_expression_trace_id(event)
            payload: dict[str, Any] = {
                "trace_id": normalized_trace_id,
                "stage": safe_code(stage, _REACTION_LOG_STAGES) or "decision",
                "decision": safe_code(decision, _REACTION_LOG_DECISIONS) or "skip",
            }
            for key, value, allowed in (
                ("status", status, _REACTION_LOG_STATUSES),
                ("reason", reason, _REACTION_LOG_REASONS),
                ("delivery", delivery, _REACTION_LOG_DELIVERY_CODES),
                ("match_basis", match_basis, _REACTION_LOG_MATCH_BASES),
            ):
                normalized = safe_code(value, allowed)
                if normalized:
                    payload[key] = normalized
            normalized_scope = safe_code(
                scope,
                frozenset({"private", "group", "unknown"}),
            )
            if normalized_scope:
                payload["scope"] = normalized_scope
            normalized_image_id = _single_line(image_id, 160)
            if normalized_image_id:
                if re.fullmatch(
                    r"pc-local:[0-9a-f]{16,64}",
                    normalized_image_id,
                    flags=re.I,
                ):
                    payload["asset_ref"] = normalized_image_id
                else:
                    payload["asset_ref"] = hashlib.sha256(
                        normalized_image_id.encode("utf-8", errors="replace")
                    ).hexdigest()[:12]
            normalized_error_type = _single_line(error_type, 80)
            if normalized_error_type and re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_.]{0,79}",
                normalized_error_type,
            ):
                payload["error_type"] = normalized_error_type
            normalized_feedback_signal = safe_code(
                feedback_signal,
                frozenset({"positive", "negative", "neutral"}),
            )
            if normalized_feedback_signal:
                payload["feedback_signal"] = normalized_feedback_signal
            normalized_trigger_mode = safe_code(
                trigger_mode,
                _REACTION_LOG_TRIGGER_MODES,
            )
            if normalized_trigger_mode:
                payload["trigger_mode"] = normalized_trigger_mode
            for key, value in (
                ("found", found),
                ("sent", sent),
                ("cache_hit", cache_hit),
            ):
                if value is not None:
                    payload[key] = bool(value)
            if feedback_score is not None:
                payload["feedback_score"] = max(
                    -20,
                    min(20, _safe_int(feedback_score, 0, -20, 20)),
                )
            for key, value, maximum, digits in (
                ("confidence", confidence, 1.0, 3),
                ("latency_ms", latency_ms, 3_600_000.0, 2),
                ("configured_probability", configured_probability, 1.0, 4),
                ("effective_probability", effective_probability, 1.0, 4),
                ("cooldown_seconds", cooldown_seconds, 86_400.0, 2),
            ):
                if value is not None:
                    payload[key] = round(
                        _safe_float(value, 0.0, 0.0, maximum),
                        digits,
                    )
            logger.info(
                "[ReactionExpression] %s",
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        except Exception:
            # Diagnostics must never alter the reply or delivery path.
            return

    @staticmethod
    def _reaction_expression_match_basis(lookup: Any) -> str:
        if not isinstance(lookup, dict):
            return ""
        provider = _single_line(lookup.get("provider"), 80)
        image_id = _single_line(lookup.get("image_id"), 160)
        if provider == "private_companion_library" or image_id.startswith("pc-local:"):
            return "tags_emotions_intents"
        return "provider_score" if lookup.get("success") else ""

    def _reaction_expression_local_trigger(
        self,
        event: Any,
        user: dict[str, Any],
        *,
        configured_probability: float,
        scope_key: str = "",
    ) -> dict[str, Any]:
        """Resolve a local trigger layer without another model pass.

        The inbound pipeline already maintains a small intent/emotion profile.
        Reuse only high-confidence, non-boundary signals here.  A configured
        probability of zero remains an explicit opt-out; cooldown and duplicate
        protection are still enforced by ``evaluate_reaction_expression_gate``.
        """
        base_probability = reaction_expression_normalize_probability(
            configured_probability,
            0.2,
        )
        default = {
            "mode": "probability",
            "reason": "random_offer",
            "source": "configured_probability",
            "confidence": 0.0,
            "bypass_probability": False,
        }
        if not isinstance(user, dict):
            return default
        inbound_text = ""
        try:
            inbound_text = _single_line(getattr(event, "message_str", ""), 500)
        except Exception:
            inbound_text = ""
        if reaction_expression_explicit_opt_out(inbound_text):
            return {
                "mode": "explicit_opt_out",
                "reason": "explicit_opt_out",
                "source": "user_message",
                "confidence": 1.0,
                "bypass_probability": False,
            }
        state = ensure_reaction_expression_state(user)
        # Keep an explicit user boundary across turns. A direct request for a
        # particular reaction image is still allowed and handled by the tool
        # path; it does not silently re-enable automatic attachments.
        auto_disabled = reaction_expression_auto_disabled(state, scope_key)
        if auto_disabled and not reaction_expression_explicit_request(inbound_text):
            return {
                "mode": "explicit_opt_out",
                "reason": "explicit_opt_out_persisted",
                "source": "user_preference",
                "confidence": 1.0,
                "bypass_probability": False,
            }
        if auto_disabled and reaction_expression_explicit_request(inbound_text):
            # Explicit requests use the ordinary tool path for this turn but
            # do not erase the persisted automatic-attachment boundary.
            return {
                "mode": "explicit_request",
                "reason": "explicit_request",
                "source": "user_message",
                "confidence": 1.0,
                "bypass_probability": False,
            }
        if base_probability <= 0:
            return default
        if not bool(runtime_persona_setting(self, 'reaction_expression_semantic_trigger_enabled', True)):
            return default

        profile = user.get("intent_profile")
        if not isinstance(profile, dict) or not profile:
            return default
        profile_text = re.sub(r"\s+", "", _single_line(profile.get("text"), 500))
        current_text = re.sub(r"\s+", "", inbound_text)
        if profile_text and (not current_text or profile_text != current_text):
            return default
        intent = _single_line(profile.get("intent"), 32).casefold()
        emotion_event = _single_line(profile.get("emotion_event"), 40).casefold()
        emotion_target = _single_line(profile.get("emotion_target"), 40).casefold()
        source = _single_line(profile.get("source"), 48).casefold()
        confidence = max(
            _safe_float(profile.get("confidence"), 0.0, 0.0, 1.0),
            _safe_float(profile.get("emotion_confidence"), 0.0, 0.0, 1.0),
        )
        intensity = _safe_float(profile.get("emotion_intensity"), 0.0, 0.0, 100.0)
        profile_snapshot = {
            "intent": intent,
            "emotion_event": emotion_event,
            "emotion_target": emotion_target,
            "emotion_intensity": intensity,
            "confidence": _safe_float(
                profile.get("confidence"), 0.0, 0.0, 1.0
            ),
            "emotion_confidence": _safe_float(
                profile.get("emotion_confidence"), 0.0, 0.0, 1.0
            ),
            "source": source,
            "boundary_durable": bool(profile.get("boundary_durable")),
            "text": _single_line(profile.get("text"), 240),
        }
        semantic_bypass_blocked = bool(
            profile_snapshot["boundary_durable"]
            or intent in {"boundary", "help", "task", "code", "search", "empty"}
            or source
            in {
                "diagnostic_skip",
                "durable_boundary_rule",
                "single_turn_boundary",
                "weak_boundary_ignored",
            }
            or emotion_event in {"hurt", "external_negative"}
        )
        preference = state.get("preference")
        preference_score = (
            _safe_int(preference.get("score"), 0, -20, 20)
            if isinstance(preference, dict)
            else 0
        )

        # A negative reaction history should not be overridden by a semantic
        # shortcut.  It still participates in the ordinary probability path.
        if preference_score < 0:
            return {
                "mode": "feedback_bias",
                "reason": "negative_feedback_respect",
                "source": "feedback_preference",
                "confidence": 0.0,
                "bypass_probability": False,
                "profile_snapshot": profile_snapshot,
            }

        # These are existing local classifier outcomes, not a second model
        # judgement.  Deliberately exclude hurt/boundary/diagnostic signals so
        # a tense conversation does not receive an unwanted reaction image.
        positive_events = {"comfort_need", "comfort", "praise", "apology"}
        positive_intents = {"play", "intimacy"}
        target_allows_event = emotion_target in {"", "self", "bot"}
        if emotion_event == "praise" and emotion_target not in {"", "bot"}:
            target_allows_event = False
        strong_event = (
            emotion_event in positive_events
            and target_allows_event
            and intensity >= 38
            and confidence >= 0.62
        )
        strong_intent = intent in positive_intents and confidence >= 0.72
        if not semantic_bypass_blocked and (strong_event or strong_intent):
            return {
                "mode": "strong_emotion" if strong_event else "semantic_rule",
                "reason": "local_emotion_signal" if strong_event else "local_intent_signal",
                "source": source or ("emotion_event" if strong_event else "intent"),
                "confidence": round(confidence, 3),
                "bypass_probability": True,
                "profile_snapshot": profile_snapshot,
            }

        if preference_score > 0:
            return {
                "mode": "feedback_bias",
                "reason": "positive_feedback_bias",
                "source": "feedback_preference",
                "confidence": min(1.0, preference_score / 10.0),
                "bypass_probability": False,
                "profile_snapshot": profile_snapshot,
            }
        default["profile_snapshot"] = profile_snapshot
        return default

    def _reaction_expression_local_fallback_intent(
        self,
        event: Any,
        visible_text: Any,
        authorization: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a conservative intent when the model omits the hidden tag.

        This reuses the inbound classifier state that already authorized the
        opportunity. Normal rates stay limited to high-confidence social or
        emotional turns; the explicit 100% mode also covers a plain social
        reply when the model omitted its optional tag.
        """
        if not isinstance(authorization, dict) or not authorization.get("authorized"):
            return {}
        if authorization.get("consumed"):
            return {}
        trigger_mode = _single_line(authorization.get("trigger_mode"), 40).casefold()
        high_frequency = bool(authorization.get("high_frequency_mode")) or reaction_expression_high_frequency(
            authorization.get(
                "configured_probability",
                runtime_persona_setting(self, 'reaction_expression_trigger_probability', 0.2),
            )
        )
        allowed_modes = {"semantic_rule", "strong_emotion"}
        if high_frequency:
            # At 100% the probability gate has already granted the opportunity;
            # do not make delivery depend on the model remembering an optional
            # hidden tag. Boundary and feedback checks below still apply.
            allowed_modes.add("probability")
        if trigger_mode not in allowed_modes:
            return {}
        try:
            user_id = _single_line(
                authorization.get("user_id")
                or getattr(event, "get_sender_id", lambda: "")(),
                160,
            )
        except Exception:
            user_id = ""
        if not user_id:
            return {}
        profile = authorization.get("profile_snapshot")
        if not isinstance(profile, dict) or not profile:
            user = self._reaction_expression_state_owner(
                event,
                user_id,
                create=False,
            )
            if not isinstance(user, dict):
                user = None
            profile = user.get("intent_profile") if isinstance(user, dict) else None
        if not isinstance(profile, dict) or not profile:
            if not high_frequency:
                return {}
            context_text = _single_line(visible_text, 700)
            if not self._reaction_expression_has_visible_text(context_text):
                return {}
            return normalize_reaction_expression_intent(
                query="开心回应",
                context=context_text,
                purpose="日常回应",
                emotion="开心",
                intensity=2,
                candidate_queries=["开心回应", "轻松互动", "日常分享"],
                candidate_limit=_safe_int(
                    runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6),
                    6,
                    1,
                    16,
                ),
            )
        profile_text = re.sub(r"\s+", "", _single_line(profile.get("text"), 500))
        current_text = re.sub(
            r"\s+",
            "",
            _single_line(getattr(event, "message_str", ""), 500),
        )
        if profile_text and (not current_text or profile_text != current_text):
            return {}
        intent_name = _single_line(profile.get("intent"), 32).casefold()
        emotion_event = _single_line(profile.get("emotion_event"), 40).casefold()
        emotion_target = _single_line(profile.get("emotion_target"), 40).casefold()
        confidence = max(
            _safe_float(profile.get("confidence"), 0.0, 0.0, 1.0),
            _safe_float(profile.get("emotion_confidence"), 0.0, 0.0, 1.0),
        )
        intensity = _safe_float(profile.get("emotion_intensity"), 0.0, 0.0, 100.0)
        confidence_floor = 0.62 if high_frequency else 0.72
        if confidence < confidence_floor or bool(profile.get("boundary_durable")):
            return {}
        if emotion_target not in {"", "self", "bot"}:
            return {}

        presets: dict[str, tuple[str, str, list[str]]] = {
            "play": ("接住玩笑", "轻松", ["轻松接梗", "开心吐槽", "无语摊手"]),
            "intimacy": ("回应亲近", "亲昵", ["害羞亲近", "撒娇回应", "温柔陪伴"]),
            "comfort": ("温柔安慰", "心疼", ["安慰陪伴", "温柔抱抱", "心疼安慰"]),
        }
        event_presets: dict[str, tuple[str, str, list[str]]] = {
            "praise": ("回应夸奖", "开心", ["开心被夸", "害羞开心", "收到夸奖"]),
            "apology": ("温和回应道歉", "温柔", ["温柔原谅", "轻轻安慰", "没关系"]),
            "comfort_need": ("接住低落", "温柔", ["安慰陪伴", "抱抱安慰", "温柔鼓励"]),
        }
        if emotion_event == "comfort" and emotion_target in {"", "bot"}:
            preset = (
                "回应安抚",
                "安心",
                ["被安慰后安心", "收到安抚", "温柔回应关心"],
            )
        else:
            preset = event_presets.get(emotion_event) or presets.get(intent_name)
            if not preset and high_frequency and intent_name in {
                "chat",
                "social",
                "conversation",
                "greeting",
            }:
                preset = (
                    "日常回应",
                    "开心",
                    ["开心回应", "轻松互动", "日常分享"],
                )
        if not preset:
            return {}
        purpose_text, emotion_text, candidates = preset
        level = max(0, min(5, int(round(intensity / 20.0))))
        if level <= 0:
            level = 2 if trigger_mode == "semantic_rule" else 3
        context_text = _single_line(visible_text, 700)
        inbound_text = _single_line(profile.get("text"), 240)
        if inbound_text and inbound_text not in context_text:
            context_text = _single_line(
                f"用户语境：{inbound_text}；回复正文：{context_text}",
                1000,
            )
        return normalize_reaction_expression_intent(
            query=candidates[0],
            context=context_text,
            purpose=purpose_text,
            emotion=emotion_text,
            intensity=level,
            candidate_queries=candidates,
            candidate_limit=_safe_int(
                runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6),
                6,
                1,
                16,
            ),
        )

    async def _preauthorize_reaction_expression_prompt(
        self, event: Any
    ) -> bool:
        now = _now_ts()
        existing = self._reaction_expression_authorization(event)
        if existing:
            if now > _safe_float(existing.get("expires_at"), 0.0):
                self._log_reaction_expression_event(
                    event,
                    stage="gate",
                    decision="deny",
                    reason="authorization_expired",
                    scope=existing.get("scope") or self._reaction_expression_scope(event),
                )
                return False
            return bool(existing.get("authorized") and not existing.get("consumed"))
        scope = self._reaction_expression_scope(event)
        authorization: dict[str, Any] = {
            "authorized": False,
            "reason": "experiment_disabled",
            "authorized_at": now,
            "expires_at": now + 600.0,
            "consumed": False,
            "model_omission_recorded": False,
            "scope": scope,
            "trace_id": self._reaction_expression_trace_id(event),
        }
        if not bool(runtime_persona_setting(self, 'enable_reaction_expression_experiment', False)):
            self._set_reaction_expression_authorization(event, authorization)
            self._log_reaction_expression_event(
                event,
                stage="gate",
                decision="deny",
                reason=authorization["reason"],
                scope=scope,
            )
            return False
        if not self._reaction_image_provider_available():
            authorization["reason"] = "provider_unavailable"
            self._set_reaction_expression_authorization(event, authorization)
            self._log_reaction_expression_event(
                event,
                stage="gate",
                decision="deny",
                reason=authorization["reason"],
                scope=scope,
            )
            return False

        allowed = (
            bool(runtime_persona_setting(self, 'reaction_expression_private_enabled', True))
            if scope == "private"
            else bool(runtime_persona_setting(self, 'reaction_expression_group_enabled', False))
            if scope == "group"
            else False
        )
        if not allowed:
            authorization["reason"] = f"{scope}_disabled"
            self._set_reaction_expression_authorization(event, authorization)
            self._log_reaction_expression_event(
                event,
                stage="gate",
                decision="deny",
                reason=authorization["reason"],
                scope=scope,
            )
            return False
        try:
            user_id = _single_line(event.get_sender_id(), 160)
        except Exception:
            user_id = ""
        if scope == "private" and user_id:
            user_id = self._reaction_expression_event_storage_id(event, user_id)
        if not user_id:
            authorization["reason"] = "missing_user"
            self._set_reaction_expression_authorization(event, authorization)
            self._log_reaction_expression_event(
                event,
                stage="gate",
                decision="deny",
                reason=authorization["reason"],
                scope=scope,
            )
            return False

        scope_key = self._reaction_expression_scope_key(event, user_id)
        configured_probability = reaction_expression_normalize_probability(
            runtime_persona_setting(self, 'reaction_expression_trigger_probability', 0.2),
            0.2,
        )
        cooldown = _safe_float(
            runtime_persona_setting(self, 'reaction_expression_cooldown_seconds', 180),
            180.0,
            0.0,
            86400.0,
        )
        async with self._data_lock:
            user = self._reaction_expression_state_owner(event, user_id)
            if not isinstance(user, dict):
                return False
            state = ensure_reaction_expression_state(user)
            scoped_state = reaction_expression_scope_state(state, scope_key)
            probability = reaction_expression_effective_probability(
                state, configured_probability
            )
            swing_probability = getattr(self, "_swing_probability", None)
            if callable(swing_probability):
                probability = swing_probability(probability, user=user)
            trigger = self._reaction_expression_local_trigger(
                event,
                user,
                configured_probability=configured_probability,
                scope_key=scope_key,
            )
            gate_probability = (
                1.0 if bool(trigger.get("bypass_probability")) else probability
            )
            if trigger.get("mode") == "explicit_opt_out":
                gate = {"allowed": False, "reason": "explicit_opt_out", "probability": gate_probability}
            else:
                semantic_offer_cooldown = (
                    min(60.0, cooldown) if cooldown > 0 and trigger.get("bypass_probability") else 0.0
                )
                last_offer_at = _safe_float(scoped_state.get("last_offer_at"), 0.0)
                if (
                    semantic_offer_cooldown > 0
                    and last_offer_at > 0
                    and now - last_offer_at < semantic_offer_cooldown
                ):
                    gate = {
                        "allowed": False,
                        "reason": "semantic_cooldown",
                        "probability": gate_probability,
                    }
                else:
                    gate = evaluate_reaction_expression_gate(
                        scoped_state,
                        {"signature": ""},
                        now=now,
                        probability=gate_probability,
                        cooldown_seconds=cooldown,
                        # Avoid drawing random state for deterministic semantic tiers;
                        # this keeps ordinary probability behavior and testability
                        # unchanged while making the bypass explicit in diagnostics.
                        random_value=random.random() if gate_probability < 1.0 else 0.0,
                    )
        authorization.update(
            {
                "authorized": bool(gate.get("allowed")),
                "reason": _single_line(gate.get("reason"), 80) or "gate",
                "user_id": user_id,
                "scope": scope,
                "scope_key": scope_key,
                "nonce": uuid.uuid4().hex,
                "configured_probability": configured_probability,
                "effective_probability": probability,
                "high_frequency_mode": reaction_expression_high_frequency(
                    configured_probability
                ),
                "gate_probability": gate_probability,
                "trigger_mode": _single_line(trigger.get("mode"), 40) or "probability",
                "trigger_reason": _single_line(trigger.get("reason"), 80),
                "trigger_source": _single_line(trigger.get("source"), 80),
                "trigger_confidence": _safe_float(
                    trigger.get("confidence"), 0.0, 0.0, 1.0
                ),
                "profile_snapshot": (
                    dict(trigger.get("profile_snapshot"))
                    if isinstance(trigger.get("profile_snapshot"), dict)
                    else {}
                ),
            }
        )
        self._set_reaction_expression_authorization(event, authorization)
        if authorization["authorized"]:
            if authorization.get("trigger_mode") in {"semantic_rule", "strong_emotion"}:
                async with self._data_lock:
                    user = self._reaction_expression_state_owner(event, user_id)
                    if not isinstance(user, dict):
                        return bool(authorization["authorized"])
                    state = ensure_reaction_expression_state(user)
                    reaction_expression_scope_state(state, scope_key)["last_offer_at"] = now
                    self._persist_reaction_expression_state(
                        sections={"reaction_expression_group_states"}
                        if scope == "group"
                        else {"users"}
                    )
            self._note_reaction_expression_runtime(offers=1, last_reason="offered")
        self._note_reaction_expression_runtime(
            trigger_mode=authorization.get("trigger_mode"),
            last_reason=authorization.get("trigger_reason") or authorization.get("reason"),
        )
        self._log_reaction_expression_event(
            event,
            stage="gate",
            decision="allow" if authorization["authorized"] else "deny",
            reason=authorization["reason"],
            scope=scope,
            configured_probability=configured_probability,
            effective_probability=gate_probability,
            cooldown_seconds=cooldown,
            trigger_mode=trigger.get("mode"),
            trigger_confidence=trigger.get("confidence"),
        )
        return bool(authorization["authorized"])

    def _consume_reaction_expression_authorization(
        self,
        event: Any,
        *,
        user_id: str,
        scope_key: str,
    ) -> tuple[bool, str]:
        authorization = self._reaction_expression_authorization(event)
        if not authorization:
            return False, "not_preauthorized"
        reason = _single_line(authorization.get("reason"), 80) or "not_preauthorized"
        if not authorization.get("authorized"):
            return False, reason
        if authorization.get("consumed"):
            return False, "authorization_consumed"
        if _now_ts() > _safe_float(authorization.get("expires_at"), 0.0):
            return False, "authorization_expired"
        if _single_line(authorization.get("user_id"), 160) != user_id:
            return False, "authorization_user_mismatch"
        if _single_line(authorization.get("scope_key"), 240) != scope_key:
            return False, "authorization_scope_mismatch"
        authorization["consumed"] = True
        self._set_reaction_expression_authorization(event, authorization)
        return True, "authorized"

    def _reaction_expression_skip_result(
        self,
        reason: str,
        *,
        event: Any = None,
        stage: str = "decision",
        scope: str = "",
        message: str = "本轮不使用表情表达，继续自然文字回复即可",
        **extra: Any,
    ) -> dict[str, Any]:
        self._note_reaction_expression_runtime(skipped=1, last_reason=reason)
        payload: dict[str, Any] = {
            "status": "skipped",
            "success": True,
            "found": False,
            "sent": False,
            "experimental": True,
            "decision": "skip",
            "skip_reason": _single_line(reason, 80),
            "message": _single_line(message, 240),
            "must_not_claim_sent": True,
            "final_response_instruction": "无需向用户解释跳过原因，按原语境继续自然文字回复。",
        }
        payload.update(extra)
        self._log_reaction_expression_event(
            event,
            stage=stage,
            decision="skip",
            reason=reason,
            scope=scope or (self._reaction_expression_scope(event) if event is not None else ""),
            found=bool(payload.get("found")),
            sent=False,
            image_id=payload.get("image_id"),
            confidence=payload.get("confidence") if "confidence" in payload else None,
            cache_hit=payload.get("cache_hit") if "cache_hit" in payload else None,
            latency_ms=(
                payload.get("lookup_latency_ms")
                if "lookup_latency_ms" in payload
                else None
            ),
            delivery=payload.get("delivery"),
        )
        return payload

    def _note_reaction_expression_runtime(
        self,
        *,
        attempts: int = 0,
        offers: int = 0,
        model_omissions: int = 0,
        local_fallbacks: int = 0,
        lookups: int = 0,
        cache_hits: int = 0,
        sent: int = 0,
        skipped: int = 0,
        last_reason: str = "",
        trigger_mode: Any = "",
        latency_ms: float | None = None,
        lookup_elapsed_ms: float = 0.0,
    ) -> None:
        runtime = getattr(self, "_reaction_expression_runtime", None)
        if not isinstance(runtime, dict):
            runtime = {}
            setattr(self, "_reaction_expression_runtime", runtime)
        for key, increment in (
            ("attempts", attempts),
            ("offers", offers),
            ("model_omissions", model_omissions),
            ("local_fallbacks", local_fallbacks),
            ("lookups", lookups),
            ("cache_hits", cache_hits),
            ("sent", sent),
            ("skipped", skipped),
        ):
            if increment:
                runtime[key] = max(0, _safe_int(runtime.get(key), 0)) + max(
                    0, _safe_int(increment, 0)
                )
        if lookup_elapsed_ms > 0:
            runtime["total_lookup_ms"] = round(
                max(0.0, _safe_float(runtime.get("total_lookup_ms"), 0.0))
                + max(0.0, _safe_float(lookup_elapsed_ms, 0.0)),
                2,
            )
        if latency_ms is not None:
            runtime["last_latency_ms"] = round(
                max(0.0, _safe_float(latency_ms, 0.0)), 2
            )
        if last_reason:
            runtime["last_reason"] = _single_line(last_reason, 120)
        normalized_trigger_mode = _single_line(trigger_mode, 40).casefold()
        if normalized_trigger_mode in _REACTION_LOG_TRIGGER_MODES:
            trigger_counts = runtime.get("trigger_modes")
            if not isinstance(trigger_counts, dict):
                trigger_counts = {}
                runtime["trigger_modes"] = trigger_counts
            trigger_counts[normalized_trigger_mode] = (
                max(0, _safe_int(trigger_counts.get(normalized_trigger_mode), 0)) + 1
            )
        runtime["last_at"] = _now_ts()

    def _persist_reaction_expression_state(
        self,
        *,
        sections: set[str] | None = None,
    ) -> None:
        scheduler = getattr(self, "_schedule_data_save", None)
        if callable(scheduler):
            try:
                scheduler(sections=sections)
                return
            except Exception:
                pass
        saver = getattr(self, "_save_data_sync", None)
        if callable(saver):
            requested = sections or {"users", "reaction_expression_group_states"}
            try:
                saver(sections=requested)
            except TypeError:
                saver()

    @staticmethod
    def _is_reaction_embedding_provider(provider: Any) -> bool:
        return any(
            callable(getattr(provider, name, None))
            for name in ("get_embedding", "get_embeddings", "get_embeddings_batch")
        )

    @staticmethod
    def _reaction_embedding_provider_runtime_id(provider: Any) -> str:
        try:
            meta = provider.meta() if callable(getattr(provider, "meta", None)) else None
        except Exception:
            meta = None
        if isinstance(meta, dict):
            value = meta.get("id")
        else:
            value = getattr(meta, "id", "") if meta is not None else ""
        if value:
            return _single_line(value, 160)
        config = getattr(provider, "provider_config", None)
        if isinstance(config, dict) and config.get("id"):
            return _single_line(config.get("id"), 160)
        direct = _single_line(getattr(provider, "id", "") or getattr(provider, "provider_id", ""), 160)
        if direct:
            return direct
        provider_class = provider.__class__
        return _single_line(
            f"auto:{getattr(provider_class, '__module__', '')}.{getattr(provider_class, '__qualname__', provider_class.__name__)}",
            160,
        )

    async def _embedding_provider_for_configured_id(self, configured_id: Any = "") -> tuple[Any, str]:
        configured = _single_line(configured_id, 160)
        context = getattr(self, "context", None)
        if context is None:
            return None, configured

        async def resolve(getter_name: str, provider_id: str = "") -> Any:
            getter = getattr(context, getter_name, None)
            if not callable(getter):
                return None
            try:
                value = getter(provider_id) if provider_id else getter()
                return await value if inspect.isawaitable(value) else value
            except Exception:
                return None

        if configured:
            for getter_name in ("get_embedding_provider_by_id", "get_provider_by_id"):
                provider = await resolve(getter_name, configured)
                if self._is_reaction_embedding_provider(provider):
                    return provider, configured
            manager = getattr(context, "provider_manager", None)
            candidates = list(getattr(manager, "embedding_provider_insts", []) or [])
            if isinstance(getattr(manager, "inst_map", None), dict):
                candidates.extend(manager.inst_map.values())
            for provider in candidates:
                if (
                    self._reaction_embedding_provider_runtime_id(provider) == configured
                    and self._is_reaction_embedding_provider(provider)
                ):
                    return provider, configured
            logger.warning(
                "Embedding Provider 不可用，回退本地语义与关键词: provider_id=%s",
                configured,
            )
            return None, configured

        for getter_name in ("get_all_embedding_providers", "get_all_providers"):
            providers = await resolve(getter_name)
            provider_rows = providers.values() if isinstance(providers, dict) else providers or []
            for provider in provider_rows:
                if self._is_reaction_embedding_provider(provider):
                    return provider, self._reaction_embedding_provider_runtime_id(provider) or "<auto>"
        manager = getattr(context, "provider_manager", None)
        for provider in (
            list(getattr(manager, "embedding_provider_insts", []) or [])
            + list(getattr(manager, "inst_map", {}).values() if manager is not None else [])
        ):
            if self._is_reaction_embedding_provider(provider):
                return provider, self._reaction_embedding_provider_runtime_id(provider) or "<auto>"
        return None, ""

    async def _shared_embedding_provider(self) -> tuple[Any, str]:
        configured = _single_line(
            runtime_persona_setting(self, "embedding_provider_id", "")
            or runtime_persona_setting(
                self,
                "reaction_expression_embedding_provider_id",
                "",
            ),
            160,
        )
        if not configured:
            return None, ""
        return await self._embedding_provider_for_configured_id(configured)

    async def _reaction_embedding_provider(self) -> tuple[Any, str]:
        configured = _single_line(
            runtime_persona_setting(
                self,
                "reaction_expression_embedding_provider_id",
                "",
            )
            or runtime_persona_setting(self, "embedding_provider_id", ""),
            160,
        )
        return await self._embedding_provider_for_configured_id(configured)

    @staticmethod
    def _reaction_embedding_input_text(value: Any) -> str:
        """Keep BGE-style embedding requests below common 512-token limits.

        Providers expose different tokenizers and many local BGE servers reject
        an oversized request before they can truncate it.  A conservative
        character budget keeps the semantic labels at both ends of a catalog
        entry while avoiding a provider-specific dependency in the plugin.
        """
        text = _single_line(value, 1800)
        limit = 480
        if len(text) <= limit:
            return text
        head = 360
        tail = limit - head - 3
        return f"{text[:head]}...{text[-tail:]}"

    async def _reaction_embedding_vector(self, provider: Any, text: str) -> list[float]:
        if not self._is_reaction_embedding_provider(provider):
            return []
        limit = max(0, _safe_int(runtime_persona_setting(self, 'reaction_expression_embedding_timeout_ms', 5000), 5000, 0))
        async def wait_result(value: Any) -> Any:
            if not inspect.isawaitable(value):
                return value
            if limit <= 0:
                return await value
            return await asyncio.wait_for(value, timeout=limit / 1000.0)
        get_embedding = getattr(provider, "get_embedding", None)
        input_text = self._reaction_embedding_input_text(text)
        if callable(get_embedding):
            payload = await wait_result(get_embedding(input_text))
        else:
            get_embeddings = getattr(provider, "get_embeddings", None)
            if callable(get_embeddings):
                payload = await wait_result(get_embeddings([input_text]))
            else:
                get_batch = getattr(provider, "get_embeddings_batch", None)
                if not callable(get_batch):
                    return []
                try:
                    payload = await wait_result(get_batch([input_text], batch_size=1, tasks_limit=1, max_retries=1))
                except TypeError:
                    payload = await wait_result(get_batch([input_text]))
        return ReactionAssetLibrary.normalize_embedding_vector(payload)

    async def _reaction_embedding_vectors(self, provider: Any, texts: list[str]) -> list[list[float]]:
        cleaned = [
            self._reaction_embedding_input_text(item)
            for item in texts
            if self._reaction_embedding_input_text(item)
        ]
        if not cleaned or not self._is_reaction_embedding_provider(provider):
            return []
        if len(cleaned) == 1:
            vector = await self._reaction_embedding_vector(provider, cleaned[0])
            return [vector] if vector else []

        limit = max(0, _safe_int(runtime_persona_setting(self, 'reaction_expression_embedding_timeout_ms', 5000), 5000, 0))

        async def wait_result(value: Any) -> Any:
            if not inspect.isawaitable(value):
                return value
            if limit <= 0:
                return await value
            return await asyncio.wait_for(value, timeout=limit / 1000.0)

        payload: Any = None
        get_embeddings = getattr(provider, "get_embeddings", None)
        get_batch = getattr(provider, "get_embeddings_batch", None)
        if callable(get_embeddings):
            try:
                payload = await wait_result(get_embeddings(cleaned))
            except Exception as exc:
                logger.debug(
                    "批量表情向量请求失败，回退逐条生成: error_type=%s",
                    type(exc).__name__,
                )
                return await asyncio.gather(
                    *(self._reaction_embedding_vector(provider, item) for item in cleaned)
                )
        elif callable(get_batch):
            try:
                payload = await wait_result(
                    get_batch(cleaned, batch_size=min(32, len(cleaned)), tasks_limit=2, max_retries=1)
                )
            except TypeError:
                try:
                    payload = await wait_result(get_batch(cleaned))
                except Exception as exc:
                    logger.debug(
                        "批量表情向量请求失败，回退逐条生成: error_type=%s",
                        type(exc).__name__,
                    )
                    return await asyncio.gather(
                        *(self._reaction_embedding_vector(provider, item) for item in cleaned)
                    )
            except Exception as exc:
                logger.debug(
                    "批量表情向量请求失败，回退逐条生成: error_type=%s",
                    type(exc).__name__,
                )
                return await asyncio.gather(
                    *(self._reaction_embedding_vector(provider, item) for item in cleaned)
                )
        else:
            return await asyncio.gather(
                *(self._reaction_embedding_vector(provider, item) for item in cleaned)
            )

        rows = payload
        if isinstance(payload, dict):
            rows = next(
                (payload.get(key) for key in ("data", "embeddings", "vectors") if isinstance(payload.get(key), list)),
                payload,
            )
        elif not isinstance(payload, (list, tuple)):
            for attribute in ("data", "embeddings", "vectors"):
                value = getattr(payload, attribute, None)
                if isinstance(value, (list, tuple)):
                    rows = value
                    break
        if not isinstance(rows, (list, tuple)):
            return []
        vectors = [ReactionAssetLibrary.normalize_embedding_vector(item) for item in rows]
        return vectors if len(vectors) == len(cleaned) and all(vectors) else []

    async def _reaction_embedding_backfill(self, library: Any, provider: Any, provider_id: str) -> None:
        try:
            batch_size = max(1, min(100, _safe_int(runtime_persona_setting(self, 'reaction_expression_embedding_backfill_batch_size', 24), 24, 1)))
            rows = await asyncio.to_thread(library.list_embedding_missing, provider_id, limit=batch_size)
            updates: list[dict[str, Any]] = []
            for item, text_hash in rows:
                try:
                    vector = await self._reaction_embedding_vector(provider, library.embedding_text(item))
                except Exception as exc:
                    logger.debug("表情向量补齐失败: provider=%s error_type=%s", provider_id, type(exc).__name__)
                    continue
                if vector:
                    updates.append({"id": item.get("id"), "text_hash": text_hash, "vector": vector})
            if updates:
                await asyncio.to_thread(library.upsert_embeddings, provider_id, updates)
                logger.info("已补齐表情语义向量: provider=%s count=%s", provider_id, len(updates))
        finally:
            inflight = getattr(self, "_reaction_embedding_backfill_inflight", set())
            inflight.discard(provider_id)

    def _schedule_reaction_embedding_backfill(self, library: Any, provider: Any, provider_id: str) -> None:
        if not bool(runtime_persona_setting(self, 'reaction_expression_embedding_backfill_enabled', True)):
            return
        inflight = getattr(self, "_reaction_embedding_backfill_inflight", None)
        if not isinstance(inflight, set):
            inflight = set()
            setattr(self, "_reaction_embedding_backfill_inflight", inflight)
        if provider_id in inflight:
            return
        now = time.monotonic()
        last_runs = getattr(self, "_reaction_embedding_backfill_last_run", None)
        if not isinstance(last_runs, dict):
            last_runs = {}
            setattr(self, "_reaction_embedding_backfill_last_run", last_runs)
        interval = max(0, _safe_int(runtime_persona_setting(self, 'reaction_expression_embedding_backfill_interval_seconds', 300), 300, 0))
        if interval and now - _safe_float(last_runs.get(provider_id), 0.0, 0.0) < interval:
            return
        inflight.add(provider_id)
        last_runs[provider_id] = now
        coroutine = self._reaction_embedding_backfill(library, provider, provider_id)
        creator = getattr(self, "_create_lifecycle_background_task", None)
        try:
            if callable(creator):
                creator(coroutine, label=f"reaction_embedding:{provider_id[:24]}")
            else:
                asyncio.create_task(coroutine)
        except Exception:
            inflight.discard(provider_id)
            coroutine.close()

    @staticmethod
    def _reaction_expression_lookup_cache_key(
        provider: Any,
        query: str,
        context: str,
        meme_only: bool,
        scope: str = "",
        revision: str = "",
    ) -> tuple[int, str, str, bool, str, str]:
        def normalize(value: Any) -> str:
            return re.sub(r"\s+", " ", str(value or "")).strip().casefold()

        return (
            id(provider),
            normalize(query),
            normalize(context),
            bool(meme_only),
            normalize(scope),
            normalize(revision),
        )

    @staticmethod
    def _reaction_expression_lookup_cache_revision(provider: Any) -> str:
        """Return a cheap catalog revision so UI edits do not leave stale hits alive."""
        revision_getter = getattr(provider, "selection_revision", None)
        if not callable(revision_getter):
            revision_getter = getattr(provider, "lookup_revision", None)
        if callable(revision_getter):
            try:
                return str(revision_getter() or "")
            except Exception:
                pass
        catalog_path = getattr(provider, "catalog_path", None)
        if catalog_path is None:
            return ""
        try:
            stat = os.stat(catalog_path)
            return f"{int(stat.st_mtime_ns)}:{int(stat.st_size)}"
        except (OSError, TypeError, ValueError):
            return ""

    @staticmethod
    def _reaction_expression_selection_revision(
        selection_preferences: Any,
        selection_signature: Any = "",
    ) -> str:
        """Hash the bounded preference snapshot used by reaction selection."""
        if not isinstance(selection_preferences, dict):
            return ""
        signature = _single_line(
            selection_signature or selection_preferences.get("intent_signature"),
            40,
        )
        raw_assets = selection_preferences.get("assets")
        rows: list[dict[str, Any]] = []
        if isinstance(raw_assets, dict):
            raw_assets = [
                {"key": key, **value}
                for key, value in raw_assets.items()
                if isinstance(value, dict)
            ]
        if isinstance(raw_assets, list):
            for raw_item in raw_assets:
                if not isinstance(raw_item, dict):
                    continue
                key = _single_line(raw_item.get("key"), 180)
                if not key:
                    continue
                rows.append(
                    {
                        "key": key,
                        "score": _safe_int(raw_item.get("score"), 0, -20, 20),
                        "positive_count": _safe_int(
                            raw_item.get("positive_count"), 0, 0, 1000
                        ),
                        "negative_count": _safe_int(
                            raw_item.get("negative_count"), 0, 0, 1000
                        ),
                        "intent_score": _safe_int(
                            raw_item.get("intent_score"), 0, -8, 8
                        ),
                    }
                )
        if not rows:
            return ""
        rows.sort(key=lambda item: item["key"])
        payload = json.dumps(
            {"intent_signature": signature, "assets": rows},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _reaction_expression_lookup_cache_get(
        self,
        key: tuple[int, str, str, bool, str, str],
    ) -> dict[str, Any] | None:
        cache = getattr(self, "_reaction_expression_lookup_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_reaction_expression_lookup_cache", cache)
        now = time.monotonic()
        for cached_key, entry in list(cache.items()):
            if not isinstance(entry, dict) or now > _safe_float(entry.get("expires_at"), 0.0):
                cache.pop(cached_key, None)
        entry = cache.get(key)
        if not isinstance(entry, dict):
            return None
        lookup = entry.get("lookup")
        if not isinstance(lookup, dict):
            cache.pop(key, None)
            return None
        if lookup.get("success"):
            cached_path = _path_text(lookup.get("path"), 1000)
            if not cached_path or not os.path.isfile(cached_path):
                cache.pop(key, None)
                return None
        return dict(lookup)

    def _reaction_expression_lookup_cache_put(
        self,
        key: tuple[int, str, str, bool, str, str],
        lookup: dict[str, Any],
    ) -> None:
        if not isinstance(lookup, dict):
            return
        status = _single_line(lookup.get("status"), 40).lower()
        success = bool(lookup.get("success"))
        if success:
            image_path = _path_text(lookup.get("path"), 1000)
            if not image_path or not os.path.isfile(image_path):
                return
            ttl_seconds = 120.0
        elif status in {"not_found", "empty_library"}:
            ttl_seconds = 30.0
        else:
            return
        cache = getattr(self, "_reaction_expression_lookup_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_reaction_expression_lookup_cache", cache)
        now = time.monotonic()
        cache[key] = {
            "lookup": dict(lookup),
            "created_at": now,
            "expires_at": now + ttl_seconds,
        }
        if len(cache) > 48:
            oldest = sorted(
                cache.items(),
                key=lambda item: _safe_float(item[1].get("created_at"), 0.0)
                if isinstance(item[1], dict)
                else 0.0,
            )
            for cached_key, _entry in oldest[: len(cache) - 48]:
                cache.pop(cached_key, None)

    def _reaction_expression_lookup_context(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        *,
        profile_snapshot: dict[str, Any] | None = None,
    ) -> str:
        parts = [
            "实验性表情表达：仅在候选自然贴合时选择，不合适时允许不返回图片。",
            f"沟通用途：{_single_line(intent.get('purpose'), 120)}"
            if intent.get("purpose")
            else "",
            f"表达情绪：{_single_line(intent.get('emotion'), 80)}"
            if intent.get("emotion")
            else "",
            f"表达强度：{_safe_int(intent.get('intensity'), 0, 0, 5)}/5",
            f"当前语境：{_single_line(intent.get('context'), 500)}"
            if intent.get("context")
            else "",
        ]
        candidates = intent.get("candidate_queries")
        if isinstance(candidates, list) and candidates:
            parts.append(f"候选检索表达：{'；'.join(_single_line(item, 100) for item in candidates)}")
        intent_profile = (
            profile_snapshot
            if isinstance(profile_snapshot, dict) and profile_snapshot
            else user.get("intent_profile")
        )
        if isinstance(intent_profile, dict) and intent_profile:
            parts.append(
                "近期用户意图："
                + _single_line(json.dumps(intent_profile, ensure_ascii=False), 260)
            )
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if callable(expression_builder):
            try:
                decision = expression_builder(
                    user,
                    message_intent={"requested_content_tier": "normal"},
                    passive_reengagement=True,
                )
                expression = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
                parts.append(
                    "统一表达边界："
                    f"档位={_single_line(expression.get('expression_band'), 24) or 'relaxed'}，"
                    f"语气={_single_line(expression.get('tone'), 24) or 'steady'}，"
                    f"追问={'允许' if expression.get('followup') else '关闭'}，"
                    f"内容尺度={_single_line(expression.get('content_tier'), 16) or 'normal'}"
                )
            except Exception:
                pass
        preference = ensure_reaction_expression_state(user).get("preference")
        if isinstance(preference, dict):
            score = _safe_int(preference.get("score"), 0, -20, 20)
            if score:
                parts.append(f"用户对近期表情表达的轻量偏好分：{score}")
        return _single_line("；".join(part for part in parts if part), 1000)

    def _record_reaction_expression_feedback(
        self,
        user: dict[str, Any],
        signal: str,
        text: str,
        *,
        scope_key: str = "",
    ) -> dict[str, Any]:
        state = ensure_reaction_expression_state(user)
        return record_reaction_expression_feedback(
            state,
            signal,
            text,
            now=_now_ts(),
            event_limit=max(8, _safe_int(runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6), 6, 1, 16) * 2),
            scope_key=scope_key,
        )

    def _apply_reaction_expression_feedback(
        self,
        user: dict[str, Any],
        text: str,
        *,
        scope_key: str = "",
    ) -> dict[str, Any]:
        state = ensure_reaction_expression_state(user)
        now = _now_ts()
        preference_change = sync_reaction_expression_auto_preference(
            state, text, now=now, scope_key=scope_key
        )
        signal = classify_reaction_expression_feedback(
            state,
            text,
            now=now,
            scope_key=scope_key,
        )
        if not signal:
            if preference_change:
                return {
                    "auto_preference": preference_change,
                    "score": _safe_int(
                        (state.get("preference") or {}).get("score"),
                        0,
                        -20,
                        20,
                    ),
                }
            return {}
        result = record_reaction_expression_feedback(
            state,
            signal,
            text,
            now=now,
            event_limit=max(8, _safe_int(runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6), 6, 1, 16) * 2),
            scope_key=scope_key,
        )
        if preference_change:
            result["auto_preference"] = preference_change
        return result

    async def _pc_reaction_expression_impl(
        self,
        event: AstrMessageEvent,
        *,
        query: str = "",
        context: str = "",
        meme_only: bool = True,
        send: bool = True,
        caption: str = "",
        purpose: str = "",
        emotion: str = "",
        intensity: int = 0,
        candidate_queries: Any = "",
        attach_only: bool = False,
    ) -> str:
        self._note_reaction_expression_runtime(attempts=1)
        if not bool(runtime_persona_setting(self, 'enable_reaction_expression_experiment', False)):
            return json.dumps(
                self._reaction_expression_skip_result(
                    "experiment_disabled",
                    event=event,
                ),
                ensure_ascii=False,
            )
        if not attach_only and not self._reaction_expression_bool_arg(send, True):
            return json.dumps(
                self._reaction_expression_skip_result(
                    "send_disabled",
                    event=event,
                ),
                ensure_ascii=False,
            )

        scope = self._reaction_expression_scope(event)
        scope_enabled = (
            bool(runtime_persona_setting(self, 'reaction_expression_private_enabled', True))
            if scope == "private"
            else bool(runtime_persona_setting(self, 'reaction_expression_group_enabled', False))
            if scope == "group"
            else False
        )
        if not scope_enabled:
            return json.dumps(
                self._reaction_expression_skip_result(
                    f"{scope}_disabled",
                    event=event,
                    scope=scope,
                ),
                ensure_ascii=False,
            )

        try:
            user_id = _single_line(event.get_sender_id(), 160)
        except Exception:
            user_id = ""
        if scope == "private" and user_id:
            user_id = self._reaction_expression_event_storage_id(event, user_id)
        if not user_id:
            return json.dumps(
                self._reaction_expression_skip_result(
                    "missing_user",
                    event=event,
                    scope=scope,
                ),
                ensure_ascii=False,
            )
        scope_key = self._reaction_expression_scope_key(event, user_id)
        authorization = self._reaction_expression_authorization(event)
        profile_snapshot = (
            dict(authorization.get("profile_snapshot"))
            if isinstance(authorization.get("profile_snapshot"), dict)
            else None
        )
        authorized, authorization_reason = self._consume_reaction_expression_authorization(
            event,
            user_id=user_id,
            scope_key=scope_key,
        )
        if not authorized:
            return json.dumps(
                self._reaction_expression_skip_result(
                    authorization_reason,
                    event=event,
                    scope=scope,
                ),
                ensure_ascii=False,
            )

        candidate_limit = _safe_int(
            runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6), 6, 1, 16
        )
        intent = normalize_reaction_expression_intent(
            query=query,
            context=context,
            purpose=purpose,
            emotion=emotion,
            intensity=intensity,
            candidate_queries=candidate_queries,
            candidate_limit=candidate_limit,
        )
        signature = _single_line(intent.get("signature"), 40)
        reservation_token = uuid.uuid4().hex
        now = _now_ts()
        async with self._data_lock:
            user = self._reaction_expression_state_owner(event, user_id)
            if not isinstance(user, dict):
                return json.dumps(
                    self._reaction_expression_skip_result(
                        "state_unavailable",
                        event=event,
                        scope=scope,
                    ),
                    ensure_ascii=False,
                )
            state = ensure_reaction_expression_state(user)
            scoped_state = reaction_expression_scope_state(state, scope_key)
            gate = evaluate_reaction_expression_gate(
                scoped_state,
                intent,
                now=now,
                probability=1.0,
                cooldown_seconds=_safe_float(
                    runtime_persona_setting(self, 'reaction_expression_cooldown_seconds', 180),
                    180.0,
                    0.0,
                    86400.0,
                ),
                random_value=0.0,
            )
            if not gate.get("allowed"):
                reason = _single_line(gate.get("reason"), 80) or "gate"
                append_reaction_expression_outcome(
                    state,
                    status="skipped",
                    reason=reason,
                    intent_signature=signature,
                    now=now,
                    candidate_limit=candidate_limit,
                )
                self._persist_reaction_expression_state(
                    sections={"reaction_expression_group_states"}
                    if scope == "group"
                    else {"users"}
                )
                return json.dumps(
                    self._reaction_expression_skip_result(
                        reason,
                        event=event,
                        scope=scope,
                        intent=intent,
                    ),
                    ensure_ascii=False,
                )
            selection_preferences = reaction_expression_selection_preferences(
                state,
                intent_signature=signature,
            )
            reserve_reaction_expression_intent(
                scoped_state,
                intent,
                now=now,
                reservation_token=reservation_token,
            )
            lookup_context = self._reaction_expression_lookup_context(
                user,
                intent,
                profile_snapshot=profile_snapshot,
            )

        # Keep alternate model-provided search phrases explicit in the local
        # lookup context. The owned catalog can score these phrases directly
        # without another provider/model request.
        candidate_queries = intent.get("candidate_queries")
        if isinstance(candidate_queries, list) and candidate_queries:
            candidate_text = "；".join(
                _single_line(item, 100) for item in candidate_queries if _single_line(item, 100)
            )
            if candidate_text and "候选检索表达：" not in lookup_context:
                lookup_context = _single_line(
                    "；".join(part for part in (lookup_context, f"候选检索表达：{candidate_text}") if part),
                    1000,
                )

        low_latency = bool(runtime_persona_setting(self, 'reaction_expression_low_latency_mode', True))
        raw_lookup = await self._pc_find_reaction_image_impl(
            event,
            query=_single_line(intent.get("provider_query"), 500),
            search_context=lookup_context,
            meme_only=meme_only,
            send=False,
            caption="",
            low_latency=low_latency,
            internal_attachment=True,
            selection_preferences=selection_preferences,
            selection_signature=signature,
        )
        try:
            lookup = json.loads(raw_lookup)
        except (TypeError, ValueError, json.JSONDecodeError):
            lookup = {}
        lookup_cache_hit = bool(lookup.get("cache_hit")) if isinstance(lookup, dict) else False
        lookup_latency_ms = (
            _safe_float(lookup.get("lookup_latency_ms"), 0.0, 0.0, 3_600_000.0)
            if isinstance(lookup, dict)
            else 0.0
        )
        if not isinstance(lookup, dict) or not lookup.get("success") or not lookup.get("found"):
            reason = _single_line(lookup.get("status") if isinstance(lookup, dict) else "", 80) or "not_found"
            async with self._data_lock:
                state_owner = self._reaction_expression_state_owner(event, user_id)
                if not isinstance(state_owner, dict):
                    return json.dumps(
                        self._reaction_expression_skip_result(
                            "state_unavailable",
                            event=event,
                            scope=scope,
                            intent=intent,
                        ),
                        ensure_ascii=False,
                    )
                state = ensure_reaction_expression_state(state_owner)
                scoped_state = reaction_expression_scope_state(state, scope_key)
                release_reaction_expression_reservation(
                    scoped_state,
                    intent_signature=signature,
                    reservation_token=reservation_token,
                )
                append_reaction_expression_outcome(
                    state,
                    status="skipped",
                    reason=reason,
                    intent_signature=signature,
                    now=_now_ts(),
                    candidate_limit=candidate_limit,
                    cache_hit=lookup_cache_hit,
                    latency_ms=lookup_latency_ms,
                )
                self._persist_reaction_expression_state(
                    sections={"reaction_expression_group_states"}
                    if scope == "group"
                    else {"users"}
                )
            return json.dumps(
                self._reaction_expression_skip_result(
                    reason,
                    event=event,
                    scope=scope,
                    intent=intent,
                    provider_status=reason,
                    cache_hit=lookup_cache_hit,
                    lookup_latency_ms=lookup_latency_ms,
                ),
                ensure_ascii=False,
            )

        image_path = _path_text(lookup.get("path"), 1000)
        image_id = _single_line(lookup.get("image_id"), 160)
        image_key = reaction_expression_image_key(image_id, image_path)
        image_keys = reaction_expression_image_keys(image_id, image_path)
        duplicate_window = max(
            600.0,
            _safe_float(
                runtime_persona_setting(self, 'reaction_expression_cooldown_seconds', 180),
                180.0,
                0.0,
                86400.0,
            )
            * 3,
        )
        async with self._data_lock:
            state_owner = self._reaction_expression_state_owner(event, user_id)
            if not isinstance(state_owner, dict):
                return json.dumps(
                    self._reaction_expression_skip_result(
                        "state_unavailable",
                        event=event,
                        scope=scope,
                        intent=intent,
                    ),
                    ensure_ascii=False,
                )
            state = ensure_reaction_expression_state(state_owner)
            scoped_state = reaction_expression_scope_state(state, scope_key)
            final_reason = ""
            if not reaction_expression_reservation_owned(
                scoped_state,
                reservation_token,
            ):
                final_reason = "reservation_lost"
            else:
                last_sent_at = _safe_float(scoped_state.get("last_sent_at"), 0.0)
                cooldown_seconds = _safe_float(
                    runtime_persona_setting(self, 'reaction_expression_cooldown_seconds', 180),
                    180.0,
                    0.0,
                    86400.0,
                )
                current_time = _now_ts()
                if (
                    last_sent_at > 0
                    and cooldown_seconds > 0
                    and current_time - last_sent_at < cooldown_seconds
                ):
                    final_reason = "cooldown"
                else:
                    scoped_state["reservation"]["at"] = current_time
            if final_reason:
                release_reaction_expression_reservation(
                    scoped_state,
                    intent_signature=signature,
                    reservation_token=reservation_token,
                )
                append_reaction_expression_outcome(
                    state,
                    status="skipped",
                    reason=final_reason,
                    intent_signature=signature,
                    now=_now_ts(),
                    candidate_limit=candidate_limit,
                    image_key=image_key,
                    cache_hit=lookup_cache_hit,
                    latency_ms=lookup_latency_ms,
                )
                self._persist_reaction_expression_state(
                    sections={"reaction_expression_group_states"}
                    if scope == "group"
                    else {"users"}
                )
                return json.dumps(
                    self._reaction_expression_skip_result(
                        final_reason,
                        event=event,
                        scope=scope,
                        intent=intent,
                        found=True,
                        image_id=image_id,
                        cache_hit=lookup_cache_hit,
                        lookup_latency_ms=lookup_latency_ms,
                    ),
                    ensure_ascii=False,
                )
            image_reserved = reserve_reaction_expression_image(
                state,
                image_key=image_key,
                image_keys=image_keys,
                now=_now_ts(),
                duplicate_window_seconds=duplicate_window,
                reservation_token=reservation_token,
            )
            if not image_reserved:
                release_reaction_expression_reservation(
                    scoped_state,
                    intent_signature=signature,
                    reservation_token=reservation_token,
                )
                append_reaction_expression_outcome(
                    state,
                    status="skipped",
                    reason="duplicate_image",
                    intent_signature=signature,
                    now=_now_ts(),
                    candidate_limit=candidate_limit,
                    image_key=image_key,
                    cache_hit=lookup_cache_hit,
                    latency_ms=lookup_latency_ms,
                )
                self._persist_reaction_expression_state(
                    sections={"reaction_expression_group_states"}
                    if scope == "group"
                    else {"users"}
                )
                return json.dumps(
                    self._reaction_expression_skip_result(
                        "duplicate_image",
                        event=event,
                        scope=scope,
                        intent=intent,
                        found=True,
                        image_id=image_id,
                        cache_hit=lookup_cache_hit,
                        lookup_latency_ms=lookup_latency_ms,
                    ),
                    ensure_ascii=False,
                )

        tags = [
            _single_line(item, 60)
            for item in lookup.get("tags", [])
            if _single_line(item, 60)
        ]
        need = _single_line(lookup.get("need"), 220) or _single_line(
            intent.get("provider_query"), 220
        )
        match_reason = _single_line(lookup.get("reason"), 220)
        snapshot_caption = "；".join(
            part
            for part in (
                f"图库标签：{'、'.join(tags[:8])}" if tags else "",
                f"表达需求：{need}" if need else "",
                f"选图依据：{match_reason}" if match_reason else "",
            )
            if part
        )
        if attach_only:
            pending_attachment = {
                "trace_id": self._reaction_expression_trace_id(event),
                "user_id": user_id,
                "scope": scope,
                "scope_key": scope_key,
                "intent": intent,
                "intent_signature": signature,
                "reservation_token": reservation_token,
                "candidate_limit": candidate_limit,
                "duplicate_window_seconds": duplicate_window,
                "image_path": image_path,
                "image_id": image_id,
                "image_key": image_key,
                "image_keys": image_keys,
                "tags": tags,
                "need": need,
                "match_reason": match_reason,
                "match_basis": self._reaction_expression_match_basis(lookup),
                "snapshot_caption": snapshot_caption,
                "cache_hit": lookup_cache_hit,
                "lookup_latency_ms": lookup_latency_ms,
                "confidence": _safe_float(
                    lookup.get("confidence"), 0.0, 0.0, 1.0
                ),
                "attached": False,
                "settled": False,
            }
            try:
                setattr(
                    event,
                    "_private_companion_reaction_expression_pending_attachment",
                    pending_attachment,
                )
            except Exception:
                await self._settle_reaction_expression_attachment_data(
                    pending_attachment,
                    sent=False,
                    reason="attachment_state_failed",
                )
                return json.dumps(
                    {
                        "status": "skipped",
                        "success": True,
                        "found": True,
                        "sent": False,
                        "experimental": True,
                        "decision": "skip",
                        "skip_reason": "attachment_state_failed",
                        "intent": intent,
                        "image_id": image_id,
                        "must_not_claim_sent": True,
                    },
                    ensure_ascii=False,
                )
            self._log_reaction_expression_event(
                event,
                stage="attachment",
                decision="prepared",
                reason="awaiting_platform_send",
                scope=scope,
                status="prepared",
                found=True,
                sent=False,
                image_id=image_id,
                confidence=lookup.get("confidence"),
                cache_hit=lookup_cache_hit,
                latency_ms=lookup_latency_ms,
                match_basis=self._reaction_expression_match_basis(lookup),
            )
            return json.dumps(
                {
                    "status": "prepared",
                    "success": True,
                    "found": True,
                    "sent": False,
                    "experimental": True,
                    "decision": "attach",
                    "path": image_path,
                    "image_id": image_id,
                    "tags": tags,
                    "need": need,
                    "reason": match_reason,
                    "confidence": _safe_float(
                        lookup.get("confidence"), 0.0, 0.0, 1.0
                    ),
                    "cache_hit": lookup_cache_hit,
                    "lookup_latency_ms": lookup_latency_ms,
                    "intent": intent,
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        visible_caption = self._sanitize_photo_tool_caption(caption, limit=120)
        try:
            delivery = await self._deliver_generated_image_to_event(
                event,
                image_path=image_path,
                caption=visible_caption,
            )
        except Exception as exc:
            delivery = {
                "sent": False,
                "destination": "error",
                "message": f"图片发送失败：{_single_line(exc, 180) or '未知错误'}",
            }
        if not isinstance(delivery, dict):
            delivery = {"sent": False, "destination": "error", "message": "图片发送失败"}
        sent = bool(delivery.get("sent"))
        if not sent:
            delivery_reason = (
                "delivery_uncertain"
                if bool(delivery.get("uncertain"))
                else "delivery_failed"
            )
            async with self._data_lock:
                state_owner = self._reaction_expression_state_owner(event, user_id)
                if not isinstance(state_owner, dict):
                    return json.dumps(
                        self._reaction_expression_skip_result(
                            "state_unavailable",
                            event=event,
                            scope=scope,
                            intent=intent,
                        ),
                        ensure_ascii=False,
                    )
                state = ensure_reaction_expression_state(state_owner)
                scoped_state = reaction_expression_scope_state(state, scope_key)
                release_reaction_expression_image(
                    state,
                    image_key,
                    image_keys=image_keys,
                    reservation_token=reservation_token,
                )
                release_reaction_expression_reservation(
                    scoped_state,
                    intent_signature=signature,
                    reservation_token=reservation_token,
                )
                append_reaction_expression_outcome(
                    state,
                    status="skipped",
                    reason=delivery_reason,
                    intent_signature=signature,
                    now=_now_ts(),
                    candidate_limit=candidate_limit,
                    image_key=image_key,
                    cache_hit=lookup_cache_hit,
                    latency_ms=lookup_latency_ms,
                )
                self._persist_reaction_expression_state(
                    sections={"reaction_expression_group_states"}
                    if scope == "group"
                    else {"users"}
                )
            return json.dumps(
                self._reaction_expression_skip_result(
                    delivery_reason,
                    event=event,
                    stage="delivery",
                    scope=scope,
                    intent=intent,
                    found=True,
                    image_id=image_id,
                    cache_hit=lookup_cache_hit,
                    lookup_latency_ms=lookup_latency_ms,
                    delivery=_single_line(delivery.get("destination"), 40),
                ),
                ensure_ascii=False,
            )

        try:
            setattr(event, "_private_companion_photo_tool_sent", True)
            setattr(event, "_private_companion_photo_tool_sent_caption", visible_caption)
        except Exception:
            pass
        settled_at = _now_ts()
        async with self._data_lock:
            user = self._reaction_expression_state_owner(event, user_id)
            if not isinstance(user, dict):
                return json.dumps(
                    self._reaction_expression_skip_result(
                        "state_unavailable",
                        event=event,
                        scope=scope,
                        intent=intent,
                    ),
                    ensure_ascii=False,
                )
            state = ensure_reaction_expression_state(user)
            record_reaction_expression_sent(
                state,
                intent,
                image_id=image_id,
                image_path=image_path,
                image_key=image_key,
                image_keys=image_keys,
                now=settled_at,
                candidate_limit=candidate_limit,
                duplicate_window_seconds=duplicate_window,
                scope_key=scope_key,
                reservation_token=reservation_token,
                cache_hit=lookup_cache_hit,
                latency_ms=lookup_latency_ms,
            )
            if snapshot_caption:
                self._remember_recent_photo_share_snapshot(
                    user,
                    caption=snapshot_caption,
                    topic=need,
                    motive=match_reason,
                    reason="reaction_expression_experiment",
                    subject_owner="unknown",
                )
            self._persist_reaction_expression_state(
                sections={"reaction_expression_group_states"}
                if scope == "group"
                else {"users"}
            )

        self._note_reaction_expression_runtime(sent=1, last_reason="delivered")
        self._mark_reaction_asset_used(image_id, event=event)
        self._log_reaction_expression_event(
            event,
            stage="delivery",
            decision="sent",
            reason="delivered",
            scope=scope,
            status="success",
            found=True,
            sent=True,
            image_id=image_id,
            confidence=lookup.get("confidence"),
            cache_hit=lookup_cache_hit,
            latency_ms=lookup_latency_ms,
            delivery=delivery.get("destination"),
            match_basis=self._reaction_expression_match_basis(lookup),
        )

        return json.dumps(
            {
                "status": "success",
                "success": True,
                "found": True,
                "sent": True,
                "experimental": True,
                "decision": "send",
                "message": _single_line(delivery.get("message"), 220),
                "path": image_path,
                "image_id": image_id,
                "tags": tags,
                "need": need,
                "reason": match_reason,
                "confidence": _safe_float(lookup.get("confidence"), 0.0, 0.0, 1.0),
                "delivery": _single_line(delivery.get("destination"), 40),
                "cache_hit": lookup_cache_hit,
                "lookup_latency_ms": lookup_latency_ms,
                "intent": intent,
                "must_not_claim_sent": False,
                "final_response_instruction": (
                    "图片和文字 caption 已作为本轮组合回复发送。"
                    f"最终回复不要留空，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                ),
            },
            ensure_ascii=False,
        )

    async def _settle_reaction_expression_attachment_data(
        self,
        pending: dict[str, Any],
        *,
        sent: bool,
        reason: str,
    ) -> bool:
        """Commit an attached image only after the platform send phase."""
        if not isinstance(pending, dict) or pending.get("settled"):
            return False
        pending["settled"] = True
        pending["sent"] = bool(sent)
        pending["settled_reason"] = _single_line(reason, 80)

        user_id = _single_line(pending.get("user_id"), 160)
        trace_id = _single_line(pending.get("trace_id"), 12)
        scope = _single_line(pending.get("scope"), 16)
        scope_key = _single_line(pending.get("scope_key"), 240)
        intent = pending.get("intent") if isinstance(pending.get("intent"), dict) else {}
        signature = _single_line(pending.get("intent_signature"), 40)
        reservation_token = _single_line(pending.get("reservation_token"), 80)
        candidate_limit = _safe_int(pending.get("candidate_limit"), 6, 1, 16)
        image_path = _path_text(pending.get("image_path"), 1000)
        image_id = _single_line(pending.get("image_id"), 160)
        image_key = _single_line(pending.get("image_key"), 1000)
        image_keys = pending.get("image_keys")
        cache_hit = bool(pending.get("cache_hit"))
        latency_ms = _safe_float(
            pending.get("lookup_latency_ms"), 0.0, 0.0, 3_600_000.0
        )
        confidence = _safe_float(pending.get("confidence"), 0.0, 0.0, 1.0)
        settled_at = _now_ts()

        if not user_id:
            self._note_reaction_expression_runtime(
                skipped=1,
                last_reason=reason or "missing_user",
            )
            self._log_reaction_expression_event(
                None,
                trace_id=trace_id,
                stage="delivery",
                decision="failed",
                reason=reason or "missing_user",
                scope=scope,
                status="missing_user",
                found=bool(image_id),
                sent=False,
                image_id=image_id,
                confidence=confidence,
                cache_hit=cache_hit,
                latency_ms=latency_ms,
            )
            return False

        async with self._data_lock:
            user = self._reaction_expression_state_owner(
                None,
                user_id,
                scope=scope,
                scope_key=scope_key,
            )
            if not isinstance(user, dict):
                self._note_reaction_expression_runtime(
                    skipped=1,
                    last_reason=reason or "state_unavailable",
                )
                return False
            state = ensure_reaction_expression_state(user)
            scoped_state = reaction_expression_scope_state(state, scope_key)
            if sent:
                record_reaction_expression_sent(
                    state,
                    intent,
                    image_id=image_id,
                    image_path=image_path,
                    image_key=image_key,
                    image_keys=image_keys,
                    now=settled_at,
                    candidate_limit=candidate_limit,
                    duplicate_window_seconds=_safe_float(
                        pending.get("duplicate_window_seconds"),
                        600.0,
                        60.0,
                        86400.0 * 7,
                    ),
                    scope_key=scope_key,
                    reservation_token=reservation_token,
                    cache_hit=cache_hit,
                    latency_ms=latency_ms,
                )
                snapshot_caption = _single_line(
                    pending.get("snapshot_caption"), 700
                )
                remember_snapshot = getattr(
                    self, "_remember_recent_photo_share_snapshot", None
                )
                if snapshot_caption and callable(remember_snapshot):
                    remember_snapshot(
                        user,
                        caption=snapshot_caption,
                        topic=_single_line(pending.get("need"), 220),
                        motive=_single_line(pending.get("match_reason"), 220),
                        reason="reaction_expression_experiment",
                        subject_owner="unknown",
                    )
            else:
                release_reaction_expression_image(
                    state,
                    image_key,
                    image_keys=image_keys,
                    reservation_token=reservation_token,
                )
                release_reaction_expression_reservation(
                    scoped_state,
                    intent_signature=signature,
                    reservation_token=reservation_token,
                )
                append_reaction_expression_outcome(
                    state,
                    status="skipped",
                    reason=_single_line(reason, 80) or "not_sent",
                    intent_signature=signature,
                    now=settled_at,
                    candidate_limit=candidate_limit,
                    image_key=image_key,
                    cache_hit=cache_hit,
                    latency_ms=latency_ms,
                )
            self._persist_reaction_expression_state(
                sections={"reaction_expression_group_states"}
                if scope == "group"
                else {"users"}
            )

        if sent:
            self._note_reaction_expression_runtime(sent=1, last_reason="delivered")
            self._mark_reaction_asset_used(image_id, trace_id=trace_id)
        else:
            self._note_reaction_expression_runtime(
                skipped=1,
                last_reason=_single_line(reason, 80) or "not_sent",
            )
        self._log_reaction_expression_event(
            None,
            trace_id=trace_id,
            stage="delivery",
            decision="sent" if sent else "failed",
            reason="delivered" if sent else (_single_line(reason, 80) or "not_sent"),
            scope=scope,
            status="success" if sent else "not_sent",
            found=bool(image_id),
            sent=sent,
            image_id=image_id,
            confidence=confidence,
            cache_hit=cache_hit,
            latency_ms=latency_ms,
            delivery=_single_line(reason, 80),
            match_basis=pending.get("match_basis"),
        )
        return True

    async def _pc_find_reaction_image_impl(
        self,
        event: AstrMessageEvent,
        query: str = "",
        search_context: str = "",
        meme_only: bool = True,
        send: bool = True,
        caption: str = "",
        low_latency: bool = False,
        internal_attachment: bool = False,
        context: str = "",
        selection_preferences: Any = None,
        selection_signature: str = "",
    ) -> str:
        scope = self._reaction_expression_scope(event)
        preference_snapshot = (
            selection_preferences if isinstance(selection_preferences, dict) else {}
        )
        preference_signature = _single_line(
            selection_signature or preference_snapshot.get("intent_signature"),
            40,
        )
        preference_revision = self._reaction_expression_selection_revision(
            preference_snapshot,
            preference_signature,
        )
        query_text = _single_line(query, 500)
        if not query_text:
            getter = getattr(event, "get_message_str", None)
            query_text = _single_line(
                getter() if callable(getter) else getattr(event, "message_str", ""),
                500,
            )
        if not query_text:
            self._log_reaction_expression_event(
                event,
                stage="lookup",
                decision="miss",
                reason="missing_query",
                scope=scope,
                status="need_query",
                found=False,
                sent=False,
            )
            return json.dumps(
                {
                    "status": "need_query",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "缺少表情包检索需求",
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        def bool_arg(value: Any, default: bool) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            normalized = str(value).strip().lower()
            if normalized in {"1", "true", "yes", "on", "是", "发送"}:
                return True
            if normalized in {"0", "false", "no", "off", "否", "不发送"}:
                return False
            return default

        send_image = bool_arg(send, True)
        meme_filter = bool_arg(meme_only, True)
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=500)
        if send_image and not visible_caption:
            return json.dumps(
                {
                    "status": "missing_visible_caption",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "发送表情包前需要同时提供一条完整的可见正文",
                    "must_not_claim_sent": True,
                    "final_response_instruction": "请保留完整自然文字回复；不要用图片替代正文。",
                },
                ensure_ascii=False,
            )
        if send_image:
            caption = visible_caption
        if not search_context and isinstance(context, str):
            search_context = context
        lookup_context = _single_line(search_context, 1000)
        snapshot_builder = getattr(self, "_build_companion_scene_snapshot", None)
        snapshot_formatter = getattr(self, "_format_companion_scene_snapshot", None)
        if callable(snapshot_builder) and callable(snapshot_formatter):
            try:
                sender_getter = getattr(event, "get_sender_id", None)
                sender_id = self._reaction_expression_event_storage_id(
                    event,
                    sender_getter() if callable(sender_getter) else "",
                )
                users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) and isinstance(self.data.get("users"), dict) else {}
                current_user = users.get(sender_id) if sender_id else None
                if isinstance(current_user, dict):
                    current_user = dict(current_user)
                    current_user.setdefault("user_id", sender_id)
                scene_text = _single_line(
                    snapshot_formatter(
                        snapshot_builder(current_user if isinstance(current_user, dict) else None),
                        purpose="image_search",
                    ),
                    620,
                )
                if scene_text:
                    scene_note = f"Bot当前情境（仅辅助判断回应情绪，不覆盖用户的明确需求）：{scene_text}"
                    lookup_context = _single_line(
                        "；".join(part for part in (lookup_context, scene_note) if part),
                        1000,
                    )
            except Exception as exc:
                self._log_reaction_expression_event(
                    event,
                    stage="degrade",
                    decision="failed",
                    reason="scene_snapshot_failed",
                    scope=scope,
                    error_type=type(exc).__name__,
                )

        # Q6 is an optional, hash-locked local source. A hit wins before the
        # editable reaction library, while every miss preserves its existing
        # lookup, authorization, reservation and delivery behavior.
        owned_lookup_finder = getattr(self, "_find_owned_reaction_asset", None)
        owned_lookup = (
            owned_lookup_finder(
                query_text,
                search_context=lookup_context,
                meme_only=meme_filter,
            )
            if callable(owned_lookup_finder)
            else None
        )
        library = self._reaction_asset_library()
        if owned_lookup is None and (library is None or not library.has_enabled_assets()):
            self._log_reaction_expression_event(
                event,
                stage="lookup",
                decision="miss",
                reason="library_unavailable",
                scope=scope,
                status="unavailable",
                found=False,
                sent=False,
            )
            return json.dumps(
                {
                    "status": "unavailable",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "Private Companion 表情包素材库为空，请先在实验功能页导入并启用素材",
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        embedding_provider = None
        embedding_provider_id = ""
        embedding_query: list[float] = []
        if bool(runtime_persona_setting(self, 'reaction_expression_embedding_enabled', False)):
            try:
                embedding_provider, embedding_provider_id = await self._reaction_embedding_provider()
                if embedding_provider is not None and embedding_provider_id:
                    setattr(self, "_reaction_embedding_active_provider_id", embedding_provider_id)
                    self._schedule_reaction_embedding_backfill(
                        library, embedding_provider, embedding_provider_id
                    )
                    embedding_query = await self._reaction_embedding_vector(
                        embedding_provider,
                        "；".join(part for part in (query_text, lookup_context) if part),
                    )
            except Exception as exc:
                logger.debug(
                    "表情查询向量生成失败，回退关键词: provider=%s error_type=%s",
                    embedding_provider_id or "<auto>",
                    type(exc).__name__,
                )
                embedding_query = []

        lookup_started = time.perf_counter()
        cache_hit = False
        lookup_error_type = ""
        lookup = dict(owned_lookup) if isinstance(owned_lookup, dict) else None
        if lookup is None:
            lookup_revision = self._reaction_expression_lookup_cache_revision(library)
            if embedding_provider_id:
                lookup_revision = f"{lookup_revision}|embedding:{embedding_provider_id}"
            if preference_revision:
                lookup_revision = f"{lookup_revision}|preference:{preference_revision}"
            cache_key = self._reaction_expression_lookup_cache_key(
                library,
                query_text,
                lookup_context,
                meme_filter,
                scope,
                lookup_revision,
            )
            lookup = (
                self._reaction_expression_lookup_cache_get(cache_key)
                if low_latency
                else None
            )
            if isinstance(lookup, dict):
                cache_hit = True
        if lookup is None:
            try:
                find_kwargs = {
                    "context": lookup_context,
                    "scope": scope,
                    "selection_preferences": preference_snapshot,
                    "selection_signature": preference_signature,
                }
                if embedding_query and embedding_provider_id:
                    find_kwargs.update(
                        {
                            "embedding_query": embedding_query,
                            "embedding_provider_id": embedding_provider_id,
                            "embedding_score_threshold": runtime_persona_setting(self, 'reaction_expression_embedding_score_threshold', 0.42),
                            "embedding_weight": runtime_persona_setting(self, 'reaction_expression_embedding_weight', 0.7),
                            "embedding_candidate_limit": runtime_persona_setting(self, 'reaction_expression_embedding_candidate_limit', 1200),
                        }
                    )
                lookup = await asyncio.to_thread(
                    library.find,
                    query_text,
                    **find_kwargs,
                )
                if lookup is None:
                    lookup = {
                        "success": False,
                        "status": "not_found",
                        "message": "素材库中没有足够贴合当前语境的表情包",
                    }
            except Exception as exc:
                lookup_error_type = type(exc).__name__
                logger.warning(
                    "自有表情包素材库检索失败: error_type=%s",
                    lookup_error_type,
                )
                lookup = {
                    "success": False,
                    "status": "error",
                    "message": f"图库检索失败：{_single_line(exc, 160)}",
                }
            if low_latency and isinstance(lookup, dict) and owned_lookup is None:
                self._reaction_expression_lookup_cache_put(cache_key, lookup)
        lookup_latency_ms = round(
            max(0.0, (time.perf_counter() - lookup_started) * 1000.0), 2
        )
        if low_latency:
            self._note_reaction_expression_runtime(
                lookups=0 if cache_hit else 1,
                cache_hits=1 if cache_hit else 0,
                last_reason="cache_hit" if cache_hit else "lookup",
                latency_ms=lookup_latency_ms,
                lookup_elapsed_ms=0.0 if cache_hit else lookup_latency_ms,
            )
        if not isinstance(lookup, dict) or not lookup.get("success"):
            lookup = lookup if isinstance(lookup, dict) else {}
            lookup_status = _single_line(lookup.get("status"), 40) or "not_found"
            self._log_reaction_expression_event(
                event,
                stage="lookup",
                decision="miss",
                reason="lookup_error" if lookup_status == "error" else lookup_status,
                scope=scope,
                status=lookup_status,
                found=False,
                sent=False,
                cache_hit=cache_hit,
                latency_ms=lookup_latency_ms,
                error_type=lookup_error_type,
            )
            return json.dumps(
                {
                    "status": lookup_status,
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": _single_line(lookup.get("message"), 220) or "图库中没有找到合适的表情包",
                    "need": _single_line(lookup.get("need"), 220),
                    "reason": _single_line(lookup.get("reason"), 220),
                    "cache_hit": cache_hit,
                    "lookup_latency_ms": lookup_latency_ms,
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        image_path = _path_text(lookup.get("path"), 1000)
        if not image_path or not os.path.isfile(image_path):
            self._log_reaction_expression_event(
                event,
                stage="lookup",
                decision="miss",
                reason="missing_file",
                scope=scope,
                status="missing_file",
                found=False,
                sent=False,
                image_id=lookup.get("image_id"),
                confidence=lookup.get("confidence"),
                cache_hit=cache_hit,
                latency_ms=lookup_latency_ms,
                match_basis=self._reaction_expression_match_basis(lookup),
            )
            return json.dumps(
                {
                    "status": "missing_file",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "匹配到的图库图片文件不可用",
                    "cache_hit": cache_hit,
                    "lookup_latency_ms": lookup_latency_ms,
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        self._log_reaction_expression_event(
            event,
            stage="lookup",
            decision="hit",
            reason="matched",
            scope=scope,
            status=_single_line(lookup.get("status"), 40) or "success",
            found=True,
            sent=False,
            image_id=lookup.get("image_id"),
            confidence=lookup.get("confidence"),
            cache_hit=cache_hit,
            latency_ms=lookup_latency_ms,
            match_basis=self._reaction_expression_match_basis(lookup),
        )

        sent = False
        delivery: dict[str, Any] = {}
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=120)
        if send_image:
            try:
                delivery = await self._deliver_generated_image_to_event(
                    event,
                    image_path=image_path,
                    caption=visible_caption,
                    reaction_image=True,
                )
            except Exception as exc:
                delivery = {
                    "sent": False,
                    "destination": "error",
                    "message": f"图片发送失败：{_single_line(exc, 180) or '未知错误'}",
                }
            sent = bool(delivery.get("sent"))
            if sent:
                try:
                    setattr(event, "_private_companion_photo_tool_sent", True)
                    setattr(event, "_private_companion_photo_tool_sent_caption", visible_caption)
                except Exception:
                    pass

        tags = [
            _single_line(item, 60)
            for item in lookup.get("tags", [])
            if _single_line(item, 60)
        ]
        need = _single_line(lookup.get("need"), 220) or query_text
        match_reason = _single_line(lookup.get("reason"), 220)
        snapshot_caption = "；".join(
            part
            for part in (
                f"图库标签：{'、'.join(tags[:8])}" if tags else "",
                f"表达需求：{need}" if need else "",
                f"选图依据：{match_reason}" if match_reason else "",
            )
            if part
        )
        if sent and snapshot_caption:
            try:
                user_id = self._reaction_expression_event_storage_id(event, event.get_sender_id())
            except Exception:
                user_id = ""
            if user_id:
                async with self._data_lock:
                    user = self._reaction_expression_state_owner(event, user_id)
                    if not isinstance(user, dict):
                        return json.dumps(
                            self._reaction_expression_skip_result(
                                "state_unavailable",
                                event=event,
                                scope=scope,
                            ),
                            ensure_ascii=False,
                        )
                    self._remember_recent_photo_share_snapshot(
                        user,
                        caption=snapshot_caption,
                        topic=need,
                        motive=match_reason,
                        reason="reaction_library_image",
                        subject_owner="unknown",
                    )
                    try:
                        self._save_data_sync(sections={"users"})
                    except TypeError:
                        # Keep lightweight hosts/test doubles compatible with
                        # the historical no-argument persistence hook.
                        self._save_data_sync()

        if sent:
            self._mark_reaction_asset_used(lookup.get("image_id"), event=event)
        delivery_uncertain = bool(delivery.get("uncertain"))
        success = bool(image_path and (not send_image or sent))
        if send_image:
            self._log_reaction_expression_event(
                event,
                stage="delivery",
                decision=(
                    "sent"
                    if sent
                    else "uncertain"
                    if delivery_uncertain
                    else "failed"
                ),
                reason=(
                    "delivered"
                    if sent
                    else "delivery_uncertain"
                    if delivery_uncertain
                    else "delivery_failed"
                ),
                scope=scope,
                status=(
                    "success"
                    if sent
                    else "delivery_uncertain"
                    if delivery_uncertain
                    else "delivery_failed"
                ),
                found=True,
                sent=sent,
                image_id=lookup.get("image_id"),
                confidence=lookup.get("confidence"),
                cache_hit=cache_hit,
                latency_ms=lookup_latency_ms,
                delivery=delivery.get("destination"),
                match_basis=self._reaction_expression_match_basis(lookup),
            )
        result_payload = {
            "status": (
                "success"
                if success
                else "delivery_uncertain"
                if delivery_uncertain
                else "delivery_failed"
            ),
            "success": success,
            "found": True,
            "send_requested": send_image,
            "sent": sent,
            "delivery_uncertain": delivery_uncertain,
            "message": (
                _single_line(delivery.get("message"), 220)
                if send_image
                else "已找到图库图片，但按请求未发送"
            ),
            "image_id": _single_line(lookup.get("image_id"), 120),
            "tags": tags,
            "need": need,
            "reason": match_reason,
            "confidence": _safe_float(lookup.get("confidence"), 0.0, 0.0, 1.0),
            "delivery": _single_line(delivery.get("destination"), 40),
            "cache_hit": cache_hit,
            "lookup_latency_ms": lookup_latency_ms,
            "must_not_claim_sent": not sent,
            "final_response_instruction": (
                f"完整正文 caption 与图片已一并发送。最终回复不要留空，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                if sent
                else ""
            ),
        }
        if (
            _single_line(lookup.get("source"), 60) != "owned_reaction_assets"
            or internal_attachment
        ):
            result_payload["path"] = image_path
        return json.dumps(result_payload, ensure_ascii=False)

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

    def _interaction_query_platform(self, event: AstrMessageEvent) -> str:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        platform = origin.split(":", 1)[0] if ":" in origin else ""
        return platform or getattr(self, "target_platform", "") or "aiocqhttp"

    def _interaction_query_private_targets(self, hint: str = "") -> list[dict[str, str]]:
        query = _single_line(hint, 128)
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        profiles = self.data.get("worldbook_member_profiles") if isinstance(self.data.get("worldbook_member_profiles"), dict) else {}
        targets: dict[str, dict[str, str]] = {}

        def add(user_id: str, label: str = "", source: str = "") -> None:
            user_id = _single_line(user_id, 128)
            if not user_id:
                return
            existing = targets.setdefault(user_id, {"user_id": user_id, "label": "", "source": ""})
            if label and (not existing.get("label") or existing.get("label") == user_id):
                existing["label"] = label
            if source and not existing.get("source"):
                existing["source"] = source

        if query and query.isdigit():
            add(query, query, "qq")
        configured_ids = set(self._configured_target_ids()) if callable(getattr(self, "_configured_target_ids", None)) else set()
        for configured_id in configured_ids:
            uid = _single_line(configured_id, 128)
            if uid and (not query or query == uid or query in uid):
                add(uid, uid, "target_config")
        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            uid = _single_line(user.get("user_id") or user_id, 128)
            try:
                uid = self._canonical_private_user_id(uid)
            except Exception:
                pass
            if not uid or not self._is_target_private_user(uid, user) or not bool(user.get("enabled", True)):
                continue
            tokens = [
                uid,
                user.get("nickname"),
                user.get("display_name"),
                user.get("last_display_name"),
                user.get("stable_name"),
                *(user.get("observed_display_names") if isinstance(user.get("observed_display_names"), list) else []),
                *(user.get("aliases") if isinstance(user.get("aliases"), list) else []),
            ]
            clean_tokens = [_single_line(token, 60) for token in tokens if _single_line(token, 60)]
            if not query or any(query == token or (query and query in token) for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != uid), uid)
                add(uid, label, "private_user")
        for user_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            uid = _single_line(profile.get("linked_qq_user_id") or profile.get("user_id") or user_id, 40)
            if not uid or not uid.isdigit():
                continue
            try:
                uid = self._canonical_private_user_id(uid)
            except Exception:
                pass
            linked_user = users.get(uid) if isinstance(users, dict) else None
            configured_target = uid in configured_ids
            if not configured_target and not (
                isinstance(linked_user, dict)
                and self._is_target_private_user(uid, linked_user)
                and bool(linked_user.get("enabled", True))
            ):
                continue
            tokens = [
                uid,
                profile.get("name"),
                *(profile.get("aliases") if isinstance(profile.get("aliases"), list) else []),
                *(profile.get("observed_names") if isinstance(profile.get("observed_names"), list) else []),
            ]
            clean_tokens = [_single_line(token, 60) for token in tokens if _single_line(token, 60)]
            if not query or any(query == token or (query and query in token) for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != uid), uid)
                add(uid, label, "worldbook")
        return list(targets.values())[:12]

    async def _interaction_query_group_targets(self, event: AstrMessageEvent, hint: str = "") -> list[dict[str, str]]:
        query = _single_line(hint, 80)
        targets: dict[str, dict[str, str]] = {}

        def group_allowed(group_id: str) -> bool:
            checker = getattr(self, "_group_enabled_for_event", None)
            if not callable(checker):
                return False
            try:
                return bool(checker(group_id))
            except Exception:
                return False

        def add(group_id: str, label: str = "", source: str = "") -> None:
            group_id = _single_line(group_id, 40)
            if not group_id or not group_allowed(group_id):
                return
            existing = targets.setdefault(group_id, {"group_id": group_id, "label": "", "source": ""})
            if label and (not existing.get("label") or existing.get("label") == group_id):
                existing["label"] = label
            if source and not existing.get("source"):
                existing["source"] = source

        if query and query.isdigit():
            add(query, query, "group_id")
        groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            gid = _single_line(group.get("group_id") or group_id, 40)
            tokens = [
                gid,
                group.get("name"),
                group.get("group_name"),
                group.get("display_name"),
                group.get("nickname"),
            ]
            clean_tokens = [_single_line(token, 80) for token in tokens if _single_line(token, 80)]
            if not query or any(query == token or (query and query in token) for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != gid), gid)
                add(gid, label, "plugin_group")
        profiles = self.data.get("worldbook_group_profiles") if isinstance(self.data.get("worldbook_group_profiles"), dict) else {}
        for group_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            gid = _single_line(profile.get("group_id") or group_id, 40)
            tokens = [gid, profile.get("name"), profile.get("title"), profile.get("display_name")]
            clean_tokens = [_single_line(token, 80) for token in tokens if _single_line(token, 80)]
            if not query or any(query == token or (query and query in token) for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != gid), gid)
                add(gid, label, "worldbook_group")
        return list(targets.values())[:12]

    async def _interaction_query_read_history(self, umo: str, *, limit: int = 40, hours: int = 72) -> list[dict[str, Any]]:
        getter = getattr(self, "_get_current_conversation_safely", None)
        try:
            if callable(getter):
                conv = await getter(umo, label="cross_user_memory_query")
            else:
                conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
                if not conv_id:
                    return []
                conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
        except Exception:
            return []
        history = self._load_conversation_history_items(conv)
        if not history:
            return []
        max_items = max(5, min(120, _safe_int(limit, 40, 5)))
        cutoff = _now_ts() - max(1, min(24 * 30, _safe_int(hours, 72, 1))) * 3600
        dated: list[dict[str, Any]] = []
        undated: list[dict[str, Any]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            if not self._history_item_content_text(item):
                continue
            ts = self._history_item_timestamp(item)
            if ts is None:
                undated.append(item)
            elif ts >= cutoff:
                dated.append(item)
        selected = dated[-max_items:]
        return [item for item in selected if isinstance(item, dict)][-max_items:]

    def _interaction_query_lines(self, history: list[dict[str, Any]], *, limit: int = 24) -> list[str]:
        lines: list[str] = []
        for item in history[-max(1, limit):]:
            line = self._format_history_item_for_summary(item)
            if not line:
                continue
            line = re.sub(r"\s+", " ", line).strip()
            if line and line not in lines:
                lines.append(line)
        return lines

    def _interaction_query_user_filter_tokens(self, user_hint: str = "") -> tuple[set[str], set[str]]:
        user_hint = _single_line(user_hint, 128)
        ids: set[str] = set()
        names: set[str] = set()
        if user_hint:
            if user_hint.isdigit():
                ids.add(user_hint)
            else:
                names.add(user_hint)
        for target in self._interaction_query_private_targets(user_hint):
            user_id = _single_line(target.get("user_id"), 40)
            label = _single_line(target.get("label"), 60)
            if user_id:
                ids.add(user_id)
            if label and label != user_id:
                names.add(label)
        return ids, names

    def _interaction_query_group_recent_lines(self, group_id: str, *, limit: int = 24, user_hint: str = "", hours: int = 72) -> list[str]:
        groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
        group = groups.get(str(group_id))
        if not isinstance(group, dict):
            return []
        checker = getattr(self, "_group_enabled_for_event", None)
        if not callable(checker):
            return []
        try:
            if not checker(str(group_id)):
                return []
        except Exception:
            return []
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        filter_ids, filter_names = self._interaction_query_user_filter_tokens(user_hint)
        cutoff = _now_ts() - max(1, min(24 * 30, _safe_int(hours, 72, 1))) * 3600
        lines: list[str] = []
        for item in recent[-max(1, limit):]:
            if not isinstance(item, dict):
                continue
            sender_id = _single_line(item.get("sender_id") or item.get("user_id"), 40)
            speaker = _single_line(item.get("identity_name") or item.get("name") or item.get("sender_name") or sender_id, 40) or "群友"
            if user_hint:
                speaker_hit = any(token and (token == speaker or token in speaker) for token in filter_names)
                if not ((sender_id and sender_id in filter_ids) or speaker_hit):
                    continue
            text = _single_line(item.get("text") or item.get("message"), 220)
            if not text:
                continue
            ts = _safe_float(item.get("ts") or item.get("time") or item.get("timestamp"), 0)
            if ts > 10_000_000_000:
                ts /= 1000
            if ts <= 0 or ts < cutoff:
                continue
            prefix = ""
            if ts > 0:
                try:
                    prefix = self._environment_fromtimestamp(ts).strftime("%m-%d %H:%M") + " "
                except Exception:
                    prefix = ""
            lines.append(f"{prefix}{speaker}: {text}")
        return lines

    def _interaction_query_group_user_recent_lines(self, user_hint: str, *, limit: int = 36, hours: int = 72) -> list[str]:
        user_hint = _single_line(user_hint, 128)
        if not user_hint:
            return []
        groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
        lines: list[str] = []
        per_group_limit = max(4, min(12, limit // 3 or 8))
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            group_label = _single_line(group.get("name") or group.get("group_name") or group_id, 60)
            group_lines = self._interaction_query_group_recent_lines(
                str(group_id),
                limit=per_group_limit,
                user_hint=user_hint,
                hours=hours,
            )
            for line in group_lines:
                lines.append(f"{group_label}｜{line}")
        return lines[-max(1, limit):]

    async def _pc_query_interaction_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not getattr(self, "enable_cross_user_memory_bridge", False):
            return json.dumps({"status": "disabled", "message": "跨用户记忆互通未启用"}, ensure_ascii=False)
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        try:
            identity_for_event = getattr(self, "_event_permission_identity_id", None)
            requester_id = (
                identity_for_event(event)
                if callable(identity_for_event)
                else self._permission_identity_id(event.get_sender_id())
            )
        except Exception:
            requester_id = ""
        owner_only = bool(getattr(self, "cross_user_memory_owner_only", True))
        owner_allowed = bool(requester_id and self._is_private_companion_owner_user_id(requester_id))
        admin_allowed = bool(requester_id and self._is_configured_admin_user_id(requester_id))
        allowed = owner_allowed or (not owner_only and admin_allowed)
        forbidden_message = "只有配置的主要用户可以查询 Bot 与其他人的互动。" if owner_only else "只有配置的主要用户或 AstrBot 全局管理员可以查询 Bot 与其他人的互动。"
        if not is_private or not allowed:
            logger.info(
                "跨用户互动查询权限未通过: sender=%s owner=%s admin=%s owner_only=%s umo=%s",
                requester_id or "-",
                owner_allowed,
                admin_allowed,
                owner_only,
                _single_line(getattr(event, "unified_msg_origin", ""), 120),
            )
            return json.dumps({"status": "forbidden", "message": forbidden_message}, ensure_ascii=False)
        scope = _single_line(kwargs.get("scope") or kwargs.get("type") or "auto", 20).lower()
        user_hint = _single_line(kwargs.get("user_hint") or kwargs.get("user") or kwargs.get("user_id") or kwargs.get("target_user") or "", 128)
        group_hint = _single_line(kwargs.get("group_hint") or kwargs.get("group") or kwargs.get("group_id") or kwargs.get("target_group") or "", 80)
        hint = _single_line(kwargs.get("hint") or kwargs.get("target") or kwargs.get("name") or "", 80)
        hours = max(1, min(24 * 30, _safe_int(kwargs.get("hours"), 72, 1)))
        limit = max(5, min(80, _safe_int(kwargs.get("limit"), 36, 5)))
        if scope in {"群", "群聊", "group_message"}:
            scope = "group"
        elif scope in {"私聊", "好友", "friend", "private_message", "user"}:
            scope = "private"
        elif scope not in {"auto", "private", "group"}:
            scope = "auto"
        if scope == "auto":
            if group_hint:
                scope = "group"
            elif user_hint:
                scope = "private"
            elif hint and "群" in hint:
                scope = "group"
            else:
                scope = "private"
        platform = self._interaction_query_platform(event)
        target_hint = user_hint or group_hint or hint
        if scope == "private":
            targets = self._interaction_query_private_targets(user_hint or hint)
            if not targets:
                return json.dumps({"status": "not_found", "message": "没有找到匹配的私聊对象", "hint": target_hint}, ensure_ascii=False)
            if len(targets) > 1 and not (user_hint or hint).isdigit():
                return json.dumps({"status": "ambiguous", "message": "匹配到多个私聊对象，需要补充用户 ID 或更明确称呼", "matches": targets[:8]}, ensure_ascii=False)
            target = targets[0]
            user_id = target.get("user_id", "")
            umo = f"{platform}:FriendMessage:{user_id}"
            history = await self._interaction_query_read_history(umo, limit=limit, hours=hours)
            lines = self._interaction_query_lines(history, limit=min(limit, 28))
            return json.dumps(
                {
                    "status": "success" if lines else "empty",
                    "scope": "private",
                    "target": target,
                    "session": umo,
                    "hours": hours,
                    "message_count": len(lines),
                    "recent_lines": lines,
                    "reply_hint": "请用自然口吻向主要用户概括最近互动；可以提到对象和大致话题，不要大段复述原文。",
                },
                ensure_ascii=False,
            )
        if user_hint and not (group_hint or hint):
            lines = self._interaction_query_group_user_recent_lines(user_hint, limit=min(limit, 36), hours=hours)
            return json.dumps(
                {
                    "status": "success" if lines else "empty",
                    "scope": "group_user",
                    "target": {"user_hint": user_hint},
                    "hours": hours,
                    "message_count": len(lines),
                    "recent_lines": lines,
                    "reply_hint": "请概括这个人最近在群里的发言和互动；如果线索不足，就说明目前只看到这些近期群聊记录。",
                },
                ensure_ascii=False,
            )
        targets = await self._interaction_query_group_targets(event, group_hint or hint)
        if not targets:
            return json.dumps({"status": "not_found", "message": "没有找到匹配的群聊", "hint": target_hint}, ensure_ascii=False)
        if len(targets) > 1 and not (group_hint or hint).isdigit():
            return json.dumps({"status": "ambiguous", "message": "匹配到多个群聊，需要补充群号或更明确群名", "matches": targets[:8]}, ensure_ascii=False)
        target = targets[0]
        group_id = target.get("group_id", "")
        umo = f"{platform}:GroupMessage:{group_id}"
        if user_hint:
            history = []
            lines = self._interaction_query_group_recent_lines(group_id, limit=min(limit, 28), user_hint=user_hint, hours=hours)
        else:
            history = await self._interaction_query_read_history(umo, limit=limit, hours=hours)
            lines = self._interaction_query_lines(history, limit=min(limit, 28))
            if not lines:
                lines = self._interaction_query_group_recent_lines(group_id, limit=min(limit, 28), hours=hours)
        return json.dumps(
            {
                "status": "success" if lines else "empty",
                "scope": "group",
                "target": target,
                "user_hint": user_hint,
                "session": umo,
                "hours": hours,
                "message_count": len(lines),
                "recent_lines": lines,
                "reply_hint": "请用自然口吻向主要用户概括 Bot 最近在这个群里的互动；不要把群聊原文整段搬出来。",
            },
            ensure_ascii=False,
        )

    async def _pc_get_group_id_by_name_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return json.dumps({"status": "disabled", "message": "跨群转述工具未启用"}, ensure_ascii=False)
        group_name = kwargs.get("group_name") or kwargs.get("name") or kwargs.get("keyword") or kwargs.get("group_id") or ""
        keyword = _single_line(group_name, 80)
        cached = self._atrelay_cached_group_matches(keyword)
        if cached:
            return json.dumps(
                {
                    "status": "success",
                    "count": len(cached),
                    "groups": cached[:20],
                    "source": "local_cache",
                    "message": "已从插件群缓存/关系网群档案匹配，未依赖平台群列表。",
                },
                ensure_ascii=False,
            )
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if not callable(call_action):
            return json.dumps({"status": "error", "message": "当前平台不支持获取群列表，本地群缓存/关系网群档案也未命中"}, ensure_ascii=False)
        try:
            groups = await call_action("get_group_list")
            matches = []
            for item in groups if isinstance(groups, list) else []:
                group_id = str(item.get("group_id") or "")
                name = _single_line(item.get("group_name") or item.get("group_remark"), 100)
                if not keyword or keyword in name or keyword in group_id:
                    matches.append({"group_id": group_id, "group_name": name})
            return json.dumps({"status": "success", "count": len(matches), "groups": matches[:20]}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "message": f"获取群列表失败: {_single_line(exc, 120)}"}, ensure_ascii=False)

    def _relation_lookup_authorized(self, event: AstrMessageEvent) -> bool:
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if not is_private:
            return False
        try:
            identity_for_event = getattr(self, "_event_permission_identity_id", None)
            requester_id = (
                identity_for_event(event)
                if callable(identity_for_event)
                else self._permission_identity_id(event.get_sender_id())
            )
        except Exception:
            requester_id = ""
        owner_allowed = bool(requester_id and self._is_private_companion_owner_user_id(requester_id))
        admin_allowed = bool(requester_id and self._is_configured_admin_user_id(requester_id))
        allowed = owner_allowed or admin_allowed
        if not allowed:
            logger.info(
                "关系网查询权限未通过: private=%s sender=%s owner=%s admin=%s umo=%s",
                is_private,
                requester_id or "-",
                owner_allowed,
                admin_allowed,
                _single_line(getattr(event, "unified_msg_origin", ""), 120),
            )
        return allowed

    def _relation_lookup_clean_keyword(self, value: Any) -> str:
        text = _single_line(value, 120)
        if not text:
            return ""
        match = re.search(r"\d{5,12}", text)
        if match:
            return match.group(0)
        text = re.sub(r"(这个|那个|此人|这人|那人|用户|群友|qq号|QQ号|QQ|qq)", "", text, flags=re.I)
        text = re.sub(r"(你认识吗|认识吗|认得吗|知道吗|是谁呀|是谁啊|是谁|什么人|哪位|吗|呀|啊|呢)", "", text)
        return _single_line(text.strip(" ：:，,。？?"), 60)

    async def _pc_query_relation_person_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not runtime_persona_setting(self, 'enable_worldbook_member_recognition', False):
            return json.dumps({"status": "disabled", "message": "关系网未启用"}, ensure_ascii=False)
        if not self._relation_lookup_authorized(event):
            return json.dumps({"status": "forbidden", "message": "关系网查询只允许主要用户/管理员在私聊中使用"}, ensure_ascii=False)
        keyword = self._relation_lookup_clean_keyword(
            kwargs.get("keyword")
            or kwargs.get("name")
            or kwargs.get("user")
            or kwargs.get("user_id")
            or kwargs.get("nickname")
            or kwargs.get("query")
            or ""
        )
        if not keyword:
            return json.dumps({"status": "error", "message": "缺少要查询的 QQ 号、昵称或别名"}, ensure_ascii=False)

        matches: list[dict[str, Any]] = []
        if keyword.isdigit():
            matches.extend(self._resolve_worldbook_member_by_name(keyword))
            if not matches:
                users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
                user = users.get(keyword) if isinstance(users, dict) else None
                if isinstance(user, dict):
                    label = _single_line(
                        user.get("stable_name") or user.get("nickname") or user.get("display_name") or user.get("last_display_name"),
                        60,
                    )
                    matches.append({"user_id": keyword, "name": label or keyword, "source": "private_user"})
        else:
            matches.extend(self._resolve_worldbook_member_by_name(keyword))
            existing_ids = {str(item.get("user_id") or "") for item in matches}
            for target in self._interaction_query_private_targets(keyword):
                uid = _single_line(target.get("user_id"), 40)
                if uid and uid not in existing_ids and target.get("source") != "qq":
                    matches.append({
                        "user_id": uid,
                        "name": _single_line(target.get("label"), 60) or uid,
                        "source": target.get("source") or "private_user",
                    })
                    existing_ids.add(uid)

        if not matches:
            logger.info("关系网查询未命中: keyword=%s", keyword)
            return json.dumps({"status": "not_found", "keyword": keyword, "message": "关系网里没有确认匹配对象"}, ensure_ascii=False)
        status = "success" if len(matches) == 1 else "ambiguous"
        logger.info("关系网查询命中: keyword=%s count=%s", keyword, len(matches))
        return json.dumps(
            {
                "status": status,
                "keyword": keyword,
                "count": len(matches),
                "matches": matches[:8],
            },
            ensure_ascii=False,
        )

    async def _pc_get_user_id_by_name_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return json.dumps({"status": "disabled", "message": "跨群转述工具未启用"}, ensure_ascii=False)
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("group_name") or ""
        nickname = kwargs.get("nickname") or kwargs.get("name") or kwargs.get("keyword") or kwargs.get("user_name") or kwargs.get("user") or ""
        target_group = _single_line(group_id, 40) or self._extract_group_id_from_event(event)
        query = _single_line(nickname, 128)
        if not query:
            return json.dumps({"status": "error", "message": "缺少 nickname/name 参数"}, ensure_ascii=False)
        resolved = await self._resolve_atrelay_target_user(event, target_group, query)
        if resolved.get("ambiguous"):
            return json.dumps({"status": "ambiguous", "message": "匹配到多个群友,需要用户补充 QQ 或更明确称呼", "matches": resolved.get("matches", [])}, ensure_ascii=False)
        if resolved.get("user_id"):
            return json.dumps({"status": "success", **resolved}, ensure_ascii=False)
        return json.dumps({"status": "not_found", "message": "未找到匹配群友"}, ensure_ascii=False)

    async def _pc_get_specified_group_members_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return json.dumps({"status": "disabled", "message": "跨群转述工具未启用"}, ensure_ascii=False)
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("group_name") or ""
        keyword = kwargs.get("keyword") or kwargs.get("name") or kwargs.get("nickname") or kwargs.get("user") or ""
        target_group = _single_line(group_id, 40) or self._extract_group_id_from_event(event)
        if not target_group:
            return json.dumps({"status": "error", "message": "未指定群号且当前不在群聊环境中"}, ensure_ascii=False)
        query = _single_line(keyword, 60)
        try:
            members = await self._get_group_member_list_for_tool(event, target_group)
            formatted = [self._format_atrelay_member(item) for item in members]
            if query:
                formatted = [
                    item for item in formatted
                    if query in item.get("user_id", "")
                    or query in item.get("nickname", "")
                    or query in item.get("group_card", "")
                    or query in item.get("relation_name", "")
                ]
            if runtime_persona_setting(self, 'enable_worldbook_member_recognition', True):
                async with self._data_lock:
                    self._save_data_sync(sections={"worldbook_member_profiles"})
            return json.dumps({"status": "success", "group_id": target_group, "count": len(formatted), "members": formatted[:80]}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "message": f"查询群成员失败: {_single_line(exc, 120)}"}, ensure_ascii=False)

    def _atrelay_platform_prefix_candidates(self, event: AstrMessageEvent) -> list[str]:
        prefixes: list[str] = []

        def add(value: Any) -> None:
            text = _single_line(value, 80)
            if not text:
                return
            prefix = text.split(":", 1)[0] if ":" in text else text
            if prefix and prefix not in prefixes:
                prefixes.append(prefix)

        add(getattr(event, "unified_msg_origin", ""))
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver) and sender_id:
            try:
                sender_id = _single_line(resolver(event, sender_id), 160) or sender_id
            except Exception:
                pass
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        user = users.get(sender_id) if sender_id and isinstance(users, dict) else None
        if isinstance(user, dict):
            add(user.get("umo"))
        add(getattr(self, "target_platform", ""))
        manager = getattr(getattr(self, "context", None), "platform_manager", None)
        if manager is not None:
            try:
                platforms = list(manager.get_insts())
            except Exception:
                platforms = list(getattr(manager, "platform_insts", []) or [])
            for platform in platforms:
                try:
                    meta = platform.meta()
                except Exception:
                    continue
                add(getattr(meta, "id", ""))
                add(getattr(meta, "name", ""))
        return prefixes

    def _atrelay_target_umo_candidates(self, event: AstrMessageEvent, message_type: str, target_id: str) -> list[str]:
        message_type = "GroupMessage" if message_type == "group" else "FriendMessage"
        target = _single_line(target_id, 40 if message_type == "GroupMessage" else 128)
        if not target:
            return []
        candidates: list[str] = []

        def add_umo(value: Any) -> None:
            umo = _single_line(value, 160)
            if not umo or f":{message_type}:{target}" not in umo:
                return
            if umo not in candidates:
                candidates.append(umo)

        if message_type == "GroupMessage":
            groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
            group = groups.get(target) if isinstance(groups, dict) else None
            if isinstance(group, dict):
                add_umo(group.get("umo"))
        else:
            users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
            user = users.get(target) if isinstance(users, dict) else None
            if isinstance(user, dict):
                add_umo(user.get("umo"))
        for prefix in self._atrelay_platform_prefix_candidates(event):
            add_umo(f"{prefix}:{message_type}:{target}")
        return candidates

    async def _send_atrelay_chain_to_target(
        self,
        event: AstrMessageEvent,
        *,
        message_type: str,
        target_id: str,
        chain: list[Any],
    ) -> tuple[bool, str, str]:
        errors: list[str] = []
        candidates = self._atrelay_target_umo_candidates(event, message_type, target_id)
        if not candidates:
            return False, "没有可用目标会话", ""
        for umo in candidates:
            session = self._parse_message_session(umo)
            platform = self._get_platform_for_session(session) if session else None
            if session and platform:
                try:
                    session_obj = MessageSession(
                        platform_name=str(getattr(session, "platform_id", "") or ""),
                        message_type=self._message_type_for_session(session),
                        session_id=str(getattr(session, "session_id", "") or ""),
                    )
                    await platform.send_by_session(session_obj, MessageChain(chain))
                    logger.info("转述已通过精确平台发送: umo=%s", _single_line(umo, 160))
                    return True, "", umo
                except Exception as exc:
                    errors.append(f"{umo}: 精确发送失败 {self._format_send_exception(exc)}")
                try:
                    result = await self.context.send_message(umo, MessageChain(chain))
                    if result is not False:
                        logger.info("转述已通过 AstrBot 核心发送: umo=%s", _single_line(umo, 160))
                        return True, "", umo
                    errors.append(f"{umo}: 核心发送返回 False")
                except Exception as exc:
                    errors.append(f"{umo}: 核心发送失败 {self._format_send_exception(exc)}")
            elif session:
                errors.append(f"{umo}: 未找到匹配平台，跳过 AstrBot 核心发送")
            else:
                errors.append(f"{umo}: UMO 无法解析，跳过 AstrBot 核心发送")
            try:
                direct_ok, direct_error = await self._send_chain_components_via_onebot_direct(umo, session, chain)
            except Exception as exc:
                direct_ok, direct_error = False, self._format_send_exception(exc)
            if direct_ok:
                return True, "", umo
            if direct_error:
                errors.append(f"{umo}: OneBot 兜底失败 {direct_error}")
        return False, "；".join(errors[-5:]) or "所有发送链路都失败", candidates[0]

    async def _pc_relay_message_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return json.dumps({"status": "disabled", "message": "跨会话转述工具未启用"}, ensure_ascii=False)
        authorized, _requester_id = self._atrelay_tool_authorization(event)
        if not authorized:
            return json.dumps({"status": "forbidden", "message": "跨会话转述仅允许主人使用"}, ensure_ascii=False)
        destination_raw = _single_line(
            kwargs.get("destination")
            or kwargs.get("target_scope")
            or kwargs.get("scope")
            or kwargs.get("target_type")
            or kwargs.get("type")
            or "auto",
            40,
        ).lower()
        group_hint = kwargs.get("group_hint") or kwargs.get("group_id") or kwargs.get("group") or kwargs.get("target_group") or ""
        recipient_hint = (
            kwargs.get("recipient_hint")
            or kwargs.get("recipient")
            or kwargs.get("to")
            or kwargs.get("at_user")
            or kwargs.get("target_user")
            or kwargs.get("user_id")
            or kwargs.get("nickname")
            or kwargs.get("name")
            or ""
        )
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        delay_until_seen = self._atrelay_bool_flag(
            kwargs.get("delay_until_recipient_seen", kwargs.get("delay", kwargs.get("wait_until_seen", False)))
        )
        need_receipt = self._atrelay_bool_flag(
            kwargs.get("need_receipt", kwargs.get("wait_for_reply", kwargs.get("receipt", kwargs.get("report_back", False))))
        )
        confirm_before_report = self._atrelay_bool_flag(
            kwargs.get("confirm_before_report", kwargs.get("require_reply_confirmation", kwargs.get("confirm_reply", False)))
        )
        at_recipient = self._atrelay_bool_flag(kwargs.get("at_recipient", kwargs.get("at", False)))
        expire_hours = kwargs.get("expire_hours", kwargs.get("ttl_hours", 24))

        text = self._normalize_atrelay_text(message, limit=800)
        recipient = _single_line(recipient_hint, 128)
        if not text:
            return json.dumps({"status": "error", "message": "缺少 message/text 内容"}, ensure_ascii=False)

        if destination_raw in {"group", "groups", "群", "群聊", "send_group", "to_group"}:
            destination = "group"
        elif destination_raw in {"private", "user", "friend", "私聊", "私发", "私信", "to_user", "dm"}:
            destination = "private"
        else:
            if group_hint:
                destination = "group"
            elif recipient:
                destination = "private"
            else:
                destination = "auto"

        if destination == "auto":
            return json.dumps({"status": "need_target", "message": "需要说明发到哪个群或私聊给谁"}, ensure_ascii=False)

        boundary = self._atrelay_boundary_guard(text)
        if boundary:
            return json.dumps({"status": "error", "message": boundary}, ensure_ascii=False)
        guard = self._atrelay_confirmation_guard(
            text,
            relay_mode=self._normalize_atrelay_relay_mode(relay_mode),
            sensitive_confirmed=self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
        )
        if guard:
            return json.dumps({"status": "need_confirm", "message": guard}, ensure_ascii=False)

        if destination == "group":
            group_result = {}
            current_group_id = self._extract_group_id_from_event(event)
            if not _single_line(group_hint, 80) and recipient:
                group_result = await self._resolve_atrelay_active_group_for_recipient(
                    event,
                    recipient,
                    exclude_current_group=bool(current_group_id),
                )
            if not _single_line(group_hint, 80) and current_group_id and not group_result:
                return json.dumps(
                    {
                        "status": "need_group",
                        "message": "需要补充要发到哪个群；群聊里不会默认发回当前群。",
                    },
                    ensure_ascii=False,
                )
            if not group_result:
                group_result = await self._resolve_atrelay_target_group(event, group_hint)
            if group_result.get("status") != "success":
                return json.dumps(group_result, ensure_ascii=False)
            group_id = _single_line(group_result.get("group_id"), 40)
            group_guard = self._atrelay_target_group_allowed(group_id, event)
            if group_guard:
                return json.dumps({"status": "forbidden", "message": group_guard}, ensure_ascii=False)
            send_text = await self._rewrite_atrelay_message_with_llm(
                event,
                destination="group",
                recipient_hint=recipient,
                text=text,
                relay_mode=relay_mode,
            )
            send_text = self._normalize_atrelay_text(send_text, limit=800)
            if delay_until_seen:
                if not recipient:
                    return json.dumps({"status": "need_recipient", "message": "延迟转述需要目标群友"}, ensure_ascii=False)
                result = await self._pc_schedule_group_relay_impl(
                    event,
                    group_id=group_id,
                    at_user=recipient,
                    message=send_text,
                    relay_mode=relay_mode,
                    sensitive_confirmed=sensitive_confirmed,
                    expire_hours=expire_hours,
                )
                return json.dumps({"status": "scheduled" if result.startswith("已挂起") else "error", "message": result}, ensure_ascii=False)
            result = await self._pc_send_to_group_impl(
                event,
                group_id=group_id,
                message=send_text,
                at_user=recipient if (recipient and (at_recipient or recipient)) else "",
                relay_mode=relay_mode,
                sensitive_confirmed=sensitive_confirmed,
            )
            ok = result.startswith("消息已发送")
            if ok:
                setattr(
                    event,
                    "private_companion_atrelay_tool_result",
                    {
                        "status": "success",
                        "destination": "group",
                        "final_reply": "带到了。",
                        "final_reply_reference": "参考意图：转述已经成功发到目标群；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。",
                        "sent_text": send_text,
                        "recipient": recipient,
                        "group_id": group_id,
                    },
                )
            return json.dumps(
                {
                    "status": "success" if ok else "error",
                    "message": "带到了。" if ok else result,
                    "final_reply": "带到了。" if ok else "",
                    "final_reply_reference": "参考意图：转述已经成功发到目标群；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。" if ok else "",
                    "sent_text": send_text if ok else "",
                },
                ensure_ascii=False,
            )

        target_user = recipient
        if not target_user:
            return json.dumps({"status": "need_recipient", "message": "需要补充私聊目标用户 ID 或称呼"}, ensure_ascii=False)
        if not target_user.isdigit():
            resolved = await self._resolve_atrelay_target_user(event, "", target_user)
            if not resolved.get("user_id") and not resolved.get("ambiguous"):
                group_result = await self._resolve_atrelay_target_group(event, group_hint)
            else:
                group_result = {}
            group_id = _single_line(group_result.get("group_id"), 40) if group_result.get("status") == "success" else ""
            if not group_id and self._extract_group_id_from_event(event):
                group_id = self._extract_group_id_from_event(event)
            if not resolved.get("user_id") and not resolved.get("ambiguous") and not group_id:
                return json.dumps(
                    {
                        "status": "need_group_or_user_id",
                        "message": "关系网里没有唯一确认这个称呼；请补充目标所在群号/群名，或直接提供用户 ID。",
                    },
                    ensure_ascii=False,
                )
            if not resolved.get("user_id") and not resolved.get("ambiguous"):
                resolved = await self._resolve_atrelay_target_user(event, group_id, target_user)
            if resolved.get("ambiguous"):
                return json.dumps(
                    {
                        "status": "ambiguous",
                        "message": "匹配到多个用户，请补充用户 ID",
                        "matches": resolved.get("matches", [])[:8],
                    },
                    ensure_ascii=False,
                )
            target_user = _single_line(resolved.get("user_id"), 128)
            if not target_user:
                return json.dumps({"status": "not_found", "message": "未找到私聊目标"}, ensure_ascii=False)
        send_text = await self._rewrite_atrelay_message_with_llm(
            event,
            destination="private",
            recipient_hint=target_user,
            text=text,
            relay_mode=relay_mode,
        )
        send_text = self._normalize_atrelay_text(send_text, limit=800)
        result = await self._pc_send_to_private_user_impl(
            event,
            user_id=target_user,
            message=send_text,
            relay_mode=relay_mode,
            sensitive_confirmed=sensitive_confirmed,
            need_receipt=need_receipt,
            confirm_before_report=confirm_before_report,
            receipt_expire_hours=expire_hours,
        )
        ok = result.startswith("已向")
        if ok:
            setattr(
                event,
                "private_companion_atrelay_tool_result",
                {
                        "status": "success",
                        "destination": "private",
                        "final_reply": "带到了。" if not need_receipt else "带到了，有回复我再告诉你。",
                        "final_reply_reference": (
                            "参考意图：转述已经成功发给目标私聊用户，并且如果对方回复会再告诉当前用户；只给一个很短的成功回执。"
                            if need_receipt
                            else "参考意图：转述已经成功发给目标私聊用户；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。"
                        ),
                        "sent_text": send_text,
                        "recipient": target_user,
                    },
                )
        return json.dumps(
            {
                "status": "success" if ok else "error",
                "message": "带到了，有回复我再告诉你。" if ok and need_receipt else ("带到了。" if ok else result),
                "final_reply": "带到了，有回复我再告诉你。" if ok and need_receipt else ("带到了。" if ok else ""),
                "final_reply_reference": (
                    "参考意图：转述已经成功发给目标私聊用户，并且如果对方回复会再告诉当前用户；只给一个很短的成功回执。"
                    if ok and need_receipt
                    else (
                        "参考意图：转述已经成功发给目标私聊用户；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。"
                        if ok
                        else ""
                    )
                ),
                "sent_text": send_text if ok else "",
            },
            ensure_ascii=False,
        )

    async def _pc_send_to_group_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return "发送失败：跨群转述工具未启用"
        authorized, _requester_id = self._atrelay_tool_authorization(event)
        if not authorized:
            return "发送失败：跨会话转述仅允许主人使用"
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("target_group") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        at_user = kwargs.get("at_user") or kwargs.get("at") or kwargs.get("target_user") or kwargs.get("user_id") or ""
        at_qq_list = kwargs.get("at_qq_list") or kwargs.get("at_users") or kwargs.get("at_list")
        if not at_user and isinstance(at_qq_list, list) and at_qq_list:
            at_user = str(at_qq_list[0])
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        target_group = self._normalize_atrelay_group_target_id(group_id)
        group_guard = self._atrelay_target_group_allowed(target_group, event)
        if group_guard:
            return group_guard
        text = self._normalize_atrelay_text(message, limit=800)
        relay_mode_normalized = self._normalize_atrelay_relay_mode(relay_mode)
        if not target_group:
            return "发送失败：群 ID 格式不正确"
        if not text:
            return "发送失败：消息内容为空"
        boundary = self._atrelay_boundary_guard(text)
        if boundary:
            return boundary
        duplicate = self._atrelay_duplicate_guard("group", target_group, text, at_user)
        if duplicate:
            return duplicate
        guard = self._atrelay_confirmation_guard(
            text,
            relay_mode=relay_mode_normalized,
            sensitive_confirmed=self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
        )
        if guard:
            return guard
        at_qq = ""
        at_label = ""
        if _single_line(at_user, 60):
            resolved = await self._resolve_atrelay_target_user(event, target_group, at_user)
            if resolved.get("ambiguous"):
                names = "、".join(_single_line(item.get("name") or item.get("relation_name") or item.get("nickname") or item.get("user_id"), 30) for item in resolved.get("matches", [])[:5] if isinstance(item, dict))
                return f"发送失败：@ 对象不唯一，请补充 QQ。候选：{names or '多个成员'}"
            at_qq = _single_line(resolved.get("user_id"), 40)
            at_label = _single_line(resolved.get("name"), 60)
            if not at_qq:
                return "发送失败：未找到要 @ 的群友"
            resting = self._atrelay_target_resting_reason(at_qq)
            if resting:
                return f"发送失败：{resting}，不会在群里继续 @ 打扰；可以改用延迟转述，等对方出现时再说。"
        chain: list[Any] = []
        if at_qq:
            chain.extend([At(qq=at_qq), Plain(" ")])
        chain.append(Plain(text))
        ok, error, used_umo = await self._send_atrelay_chain_to_target(
            event,
            message_type="group",
            target_id=target_group,
            chain=chain,
        )
        if not ok:
            logger.warning(
                "跨群转述发送失败: group=%s at=%s error=%s",
                target_group,
                at_qq or at_user or "-",
                _single_line(error, 240),
            )
            return f"发送失败：{_single_line(error, 180)}"
        self._note_atrelay_send("group", target_group, text, at_qq or at_user, event=event)
        self._save_data_sync(sections={"recent_atrelay_contexts", "atrelay_send_log"})
        logger.info(
            "跨群转述发送完成: group=%s at=%s umo=%s",
            target_group,
            at_qq or at_user or "-",
            _single_line(used_umo, 160),
        )
        return f"消息已发送到群 {target_group}" + (f", 已 @ {at_label or at_qq}" if at_qq else "")

    async def _pc_send_to_private_user_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return "发送失败：跨群转述工具未启用"
        authorized, _requester_id = self._atrelay_tool_authorization(event)
        if not authorized:
            return "发送失败：跨会话转述仅允许主人使用"
        user_id = kwargs.get("user_id") or kwargs.get("qq") or kwargs.get("target_user") or kwargs.get("target") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        need_receipt = self._atrelay_bool_flag(
            kwargs.get("need_receipt", kwargs.get("wait_for_reply", kwargs.get("receipt", kwargs.get("report_back", False))))
        )
        confirm_before_report = self._atrelay_bool_flag(
            kwargs.get("confirm_before_report", kwargs.get("require_reply_confirmation", kwargs.get("confirm_reply", False)))
        )
        receipt_expire_hours = kwargs.get("receipt_expire_hours", kwargs.get("expire_hours", kwargs.get("ttl_hours", 12)))
        target_user = self._normalize_atrelay_private_target_id(user_id)
        text = self._normalize_atrelay_text(message, limit=800)
        relay_mode_normalized = self._normalize_atrelay_relay_mode(relay_mode)
        if not target_user:
            return "发送失败：目标用户 ID 无效或尚未登记"
        if not text:
            return "发送失败：消息内容为空"
        boundary = self._atrelay_boundary_guard(text)
        if boundary:
            return boundary
        resting = self._atrelay_target_resting_reason(target_user)
        if resting:
            return f"私聊发送失败：{resting}，不会私聊叫醒；可以改成延迟转述或等对方醒来后再发。"
        duplicate = self._atrelay_duplicate_guard("private", target_user, text)
        if duplicate:
            return duplicate
        guard = self._atrelay_confirmation_guard(
            text,
            relay_mode=relay_mode_normalized,
            sensitive_confirmed=self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
        )
        if guard:
            return guard
        ok, error, used_umo = await self._send_atrelay_chain_to_target(
            event,
            message_type="private",
            target_id=target_user,
            chain=[Plain(text)],
        )
        if not ok:
            logger.warning(
                "私聊转述发送失败: user=%s error=%s",
                target_user,
                _single_line(error, 240),
            )
            return f"私聊发送失败：{_single_line(error, 180)}"
        self._note_atrelay_send("private", target_user, text, event=event)
        if need_receipt:
            self._note_atrelay_private_receipt_task(
                event,
                target_user=target_user,
                question=text,
                sent_text=text,
                confirm_before_report=confirm_before_report,
                expire_hours=receipt_expire_hours,
            )
        self._save_data_sync(sections={"pending_atrelay_receipts", "recent_atrelay_contexts", "atrelay_send_log"})
        logger.info(
            "私聊转述发送完成: user=%s umo=%s",
            target_user,
            _single_line(used_umo, 160),
        )
        return f"已向 {target_user} 发送私聊消息" + ("，会等待对方回复后带回回执" if need_receipt else "")

    async def _pc_send_to_groups_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        group_ids = kwargs.get("group_ids") or kwargs.get("groups") or kwargs.get("group_id") or kwargs.get("targets") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        at_user = kwargs.get("at_user") or kwargs.get("at") or kwargs.get("target_user") or kwargs.get("user_id") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        targets = []
        for item in self._parse_atrelay_target_list(group_ids, limit=self.atrelay_multi_target_limit):
            target = self._normalize_atrelay_group_target_id(item)
            if target and target not in targets:
                targets.append(target)
        if not targets:
            return "发送失败：没有有效群 ID"
        results = []
        for group_id in targets:
            result = await self._pc_send_to_group_impl(
                event,
                group_id=group_id,
                message=message,
                at_user=at_user,
                relay_mode=relay_mode,
                sensitive_confirmed=sensitive_confirmed,
            )
            results.append(f"{group_id}: {result}")
        return "多群通知完成：\n" + "\n".join(results[: self.atrelay_multi_target_limit])

    async def _pc_send_to_private_users_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        user_ids = kwargs.get("user_ids") or kwargs.get("users") or kwargs.get("user_id") or kwargs.get("targets") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        targets = []
        for item in self._parse_atrelay_target_list(user_ids, limit=self.atrelay_multi_target_limit):
            target = self._normalize_atrelay_private_target_id(item)
            if target and target not in targets:
                targets.append(target)
        if not targets:
            return "发送失败：没有有效私聊目标用户 ID"
        results = []
        for user_id in targets:
            result = await self._pc_send_to_private_user_impl(
                event,
                user_id=user_id,
                message=message,
                relay_mode=relay_mode,
                sensitive_confirmed=sensitive_confirmed,
            )
            results.append(f"{user_id}: {result}")
        return "多人转述完成：\n" + "\n".join(results[: self.atrelay_multi_target_limit])

    async def _pc_schedule_group_relay_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return "挂起失败：跨群转述工具未启用"
        authorized, _requester_id = self._atrelay_tool_authorization(event)
        if not authorized:
            return "挂起失败：跨会话转述仅允许主人使用"
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("target_group") or ""
        at_user = kwargs.get("at_user") or kwargs.get("target_user") or kwargs.get("user_id") or kwargs.get("name") or kwargs.get("nickname") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        expire_hours = kwargs.get("expire_hours", kwargs.get("ttl_hours", 24))
        target_group = self._normalize_atrelay_group_target_id(group_id) or self._extract_group_id_from_event(event)
        group_guard = self._atrelay_target_group_allowed(target_group, event)
        if group_guard:
            return group_guard.replace("发送失败", "挂起失败", 1)
        text = self._normalize_atrelay_text(message, limit=800)
        if not target_group:
            return "挂起失败：群 ID 格式不正确"
        if not text:
            return "挂起失败：消息内容为空"
        boundary = self._atrelay_boundary_guard(text)
        if boundary:
            return boundary.replace("发送失败", "挂起失败", 1)
        relay_mode_normalized = self._normalize_atrelay_relay_mode(relay_mode)
        guard = self._atrelay_confirmation_guard(
            text,
            relay_mode=relay_mode_normalized,
            sensitive_confirmed=self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
        )
        if guard:
            return guard.replace("不能直接转述", "不能直接挂起转述")
        resolved = await self._resolve_atrelay_target_user(event, target_group, at_user)
        if resolved.get("ambiguous"):
            names = "、".join(
                _single_line(item.get("name") or item.get("relation_name") or item.get("nickname") or item.get("user_id"), 30)
                for item in resolved.get("matches", [])[:5]
                if isinstance(item, dict)
            )
            return f"挂起失败：目标不唯一，请补充 QQ。候选：{names or '多个成员'}"
        target_user = _single_line(resolved.get("user_id"), 40)
        target_name = _single_line(resolved.get("name"), 60) or target_user
        if not target_user:
            return "挂起失败：未找到目标群友"
        now = _now_ts()
        expire_seconds = max(1, min(168, _safe_int(expire_hours, 24, 1, 168))) * 3600
        source_user, source_name = self._atrelay_source_snapshot_for_event(event)
        async with self._data_lock:
            group = self._get_group(target_group)
            tasks = group.setdefault("pending_atrelay_tasks", [])
            if not isinstance(tasks, list):
                tasks = []
                group["pending_atrelay_tasks"] = tasks
            signature = self._atrelay_send_signature("delayed_group", target_group, text, target_user)
            for task in tasks:
                if isinstance(task, dict) and task.get("signature") == signature and _safe_float(task.get("expires_at"), 0) > now:
                    return f"已存在相同延迟转述：等 {target_name} 在群 {target_group} 出现时发送"
            tasks.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "created_at": now,
                    "expires_at": now + expire_seconds,
                    "target_user_id": target_user,
                    "target_name": target_name,
                    "message": text,
                    "source_user": source_user,
                    "source_name": source_name,
                    "relay_mode": relay_mode_normalized,
                    "sensitive_confirmed": self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
                    "signature": signature,
                }
            )
            del tasks[:-30]
            self._save_data_sync(sections={"groups"})
        return f"已挂起：等 {target_name} 在群 {target_group} 出现时转述"
