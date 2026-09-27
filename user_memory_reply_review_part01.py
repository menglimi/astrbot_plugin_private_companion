# -*- coding: utf-8 -*-
"""UserMemoryReplyReviewPart01Mixin。

由 tools/split_mixin_domain.py 从 user_memory_reply_review.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 485 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryReplyReviewMixin）。
"""
from __future__ import annotations
from .user_memory_reply_review_shared import Any
from .user_memory_reply_review_shared import _now_ts
from .user_memory_reply_review_shared import _safe_float
from .user_memory_reply_review_shared import _safe_int
from .user_memory_reply_review_shared import _single_line
from .user_memory_reply_review_shared import _strip_internal_message_blocks
from .user_memory_reply_review_shared import datetime
from .user_memory_reply_review_shared import math
from .user_memory_reply_review_shared import re
from .user_memory_reply_review_shared import runtime_persona_setting



class UserMemoryReplyReviewPart01Mixin:
    """UserMemoryReplyReviewPart01Mixin（从 UserMemoryReplyReviewMixin 拆出）。"""


    def _update_action_preferences_from_message(self, user: dict[str, Any], text: str) -> None:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return
        prefs = user.setdefault("action_preferences", {})
        if not isinstance(prefs, dict):
            prefs = {}
            user["action_preferences"] = prefs
        mapping = {
            "poke": ("戳", "戳一戳"),
            "voice": ("语音", "发语音", "声音"),
            "photo_text": ("图片", "照片", "图"),
            "screen_peek": ("看屏幕", "窥屏", "看我屏幕", "屏幕"),
        }
        negative = ("别", "不要", "不许", "讨厌", "少", "别再", "不喜欢")
        positive = ("喜欢", "可以", "多", "想要", "爱看", "爱听")
        for action, keywords in mapping.items():
            if not any(keyword in cleaned for keyword in keywords):
                continue
            item = prefs.setdefault(action, {"like": 0, "dislike": 0, "note": ""})
            if not isinstance(item, dict):
                item = {"like": 0, "dislike": 0, "note": ""}
                prefs[action] = item
            if any(token in cleaned for token in negative):
                item["dislike"] = min(20, _safe_int(item.get("dislike"), 0, 0) + 2)
                item["note"] = _single_line(cleaned, 90)
            elif any(token in cleaned for token in positive):
                item["like"] = min(20, _safe_int(item.get("like"), 0, 0) + 1)
                item["note"] = _single_line(cleaned, 90)
            item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    def _action_consequence_items(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        items = user.setdefault("action_consequences", [])
        if not isinstance(items, list):
            items = []
            user["action_consequences"] = items
        now = _now_ts()
        kept: list[dict[str, Any]] = []
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        for item in items:
            if not isinstance(item, dict):
                continue
            created = _safe_float(item.get("ts"), now)
            if now - created > 7 * 86400:
                continue
            if callable(meta_leak_checker) and meta_leak_checker(str(item.get("text") or "")):
                continue
            kept.append(item)
        if len(kept) != len(items):
            user["action_consequences"] = kept[-18:]
        return user["action_consequences"]

    def _classify_action_reply_feedback(self, text: str) -> str:
        cleaned = _single_line(text, 220)
        if not cleaned:
            return "neutral"
        negative = (
            "别",
            "不要",
            "不许",
            "烦",
            "打扰",
            "闭嘴",
            "硬",
            "生硬",
            "不喜欢",
            "不对",
            "不是",
            "笨",
            "怎么又",
            "没收到",
            "哪里",
            "图呢",
        )
        positive = (
            "好",
            "可以",
            "喜欢",
            "可爱",
            "聪明",
            "对",
            "正常",
            "收到",
            "摸摸",
            "抱抱",
            "谢谢",
            "不错",
        )
        if any(token in cleaned for token in negative):
            return "negative"
        if any(token in cleaned for token in positive):
            return "positive"
        return "neutral"

    @staticmethod
    def _decay_proactive_source_feedback_bucket(bucket: dict[str, Any], *, now: float) -> None:
        """Apply a 30-day half-life while retaining raw counters for diagnostics."""
        if not isinstance(bucket, dict):
            return
        last_update = _safe_float(bucket.get("weighted_updated_at"), 0.0)
        if last_update <= 0:
            last_update = max(
                _safe_float(bucket.get("last_sent_at"), 0.0),
                _safe_float(bucket.get("last_reply_at"), 0.0),
            )
            for metric in ("sent", "replied", "positive", "negative", "neutral"):
                bucket[f"weighted_{metric}"] = float(_safe_int(bucket.get(metric), 0, 0))
        if last_update > 0 and now > last_update:
            factor = math.pow(0.5, min(12.0, (now - last_update) / (30.0 * 86400.0)))
            for metric in ("sent", "replied", "positive", "negative", "neutral"):
                key = f"weighted_{metric}"
                bucket[key] = max(0.0, _safe_float(bucket.get(key), 0.0) * factor)
        bucket["weighted_updated_at"] = now

    def _record_proactive_conversation_closing(
        self,
        user: dict[str, Any],
        *,
        source: str = "",
        reason: str = "",
        motive: str = "",
        now: float | None = None,
    ) -> bool:
        """Record a bot-initiated conversational closing without text matching."""
        if not isinstance(user, dict):
            return False
        posture = _single_line(user.get("planned_proactive_conversation_posture"), 24).lower()
        if posture != "closing":
            return False
        at = _now_ts() if now is None else now
        grace_minutes = _safe_float(
            runtime_persona_setting(self, "proactive_closing_grace_minutes", 45),
            45.0,
        )
        grace_minutes = max(0.0, min(240.0, grace_minutes))
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["conversation_closing"] = {
            "at": at,
            "until": at + grace_minutes * 60.0,
            "posture": "closing",
            "source": _single_line(source, 40),
            "reason": _single_line(reason, 50),
            "motive": _single_line(motive, 120),
        }
        return True

    def _note_action_sent(
        self,
        user: dict[str, Any],
        action: str,
        *,
        reason: str = "",
        text: str = "",
        motive: str = "",
        action_summary: str = "",
        source: str = "",
    ) -> None:
        action = _single_line(action, 40) or "message"
        source = _single_line(source, 40) or _single_line(user.get("planned_proactive_source"), 40) or "unknown"
        affinity_tracker = getattr(self, "_note_action_affinity_sent", None)
        if callable(affinity_tracker):
            affinity_tracker(user, action)
        items = self._action_consequence_items(user)
        items.append(
            {
                "ts": _now_ts(),
                "action": action,
                "source": source,
                "reason": _single_line(reason, 50),
                "text": _single_line(_strip_internal_message_blocks(text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 120),
                "motive": _single_line(motive, 100),
                "summary": _single_line(action_summary, 120),
                "status": "awaiting_reply",
                "feedback": "",
                "reply_text": "",
                "reply_ts": 0,
            }
        )
        del items[:-18]
        source_feedback = user.setdefault("proactive_source_feedback", {})
        if not isinstance(source_feedback, dict):
            source_feedback = {}
            user["proactive_source_feedback"] = source_feedback
        bucket = source_feedback.setdefault(source, {})
        if not isinstance(bucket, dict):
            bucket = {}
            source_feedback[source] = bucket
        now = _now_ts()
        self._decay_proactive_source_feedback_bucket(bucket, now=now)
        bucket["sent"] = _safe_int(bucket.get("sent"), 0, 0) + 1
        bucket["weighted_sent"] = _safe_float(bucket.get("weighted_sent"), 0.0) + 1.0
        bucket["last_sent_at"] = now
        user["last_proactive_source"] = source
        self._note_proactive_afterglow_sent(
            user,
            action=action,
            reason=reason,
            text=text,
            motive=motive,
            action_summary=action_summary,
        )
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        self._record_proactive_conversation_closing(
            user,
            source=source,
            reason=reason,
            motive=motive,
            now=now,
        )
        continuity["last_action_ts"] = _now_ts()
        continuity["last_action"] = action
        continuity["last_action_reason"] = _single_line(reason, 50)
        cleaned_text = _single_line(_strip_internal_message_blocks(text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 120)
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        continuity["last_action_text"] = "" if callable(meta_leak_checker) and meta_leak_checker(cleaned_text) else cleaned_text

    def _note_proactive_afterglow_sent(
        self,
        user: dict[str, Any],
        *,
        action: str,
        reason: str = "",
        text: str = "",
        motive: str = "",
        action_summary: str = "",
    ) -> None:
        now = _now_ts()
        semantic_kind = _single_line(user.get("planned_proactive_semantic_kind"), 40)
        anchor_type = _single_line(user.get("planned_proactive_anchor_type"), 40)
        semantic_score = _safe_int(user.get("planned_proactive_semantic_score"), 50, 0, 100)
        ignored = _safe_int(user.get("ignored_streak"), 0, 0)
        if reason in {"group_share", "news_share", "bili_video_share", "web_exploration_share", "environment_change"} or semantic_kind == "external_share":
            label = "刚把一个外部小发现递过去，先看它会不会被接住"
            next_tendency = "稍后若还没回应，不要继续补同类分享"
        elif semantic_kind in {"self_share", "observation"} or reason in {"activity_share", "diary_share", "creative_share", "background_schedule"}:
            label = "刚把自己的一个小片段放过去，余味还在"
            next_tendency = "下一次优先换更轻的切口，不要连续汇报自己"
        elif semantic_kind in {"care", "check_in", "light_touch"} or reason in {"quiet_care", "state_share"}:
            label = "刚轻轻碰了一下关系，不急着要回应"
            next_tendency = "如果沉默继续，下一次更短更克制"
        elif semantic_kind in {"continuation", "reminder"}:
            label = "刚接了一次明确来源，等这条自然落地"
            next_tendency = "除非有真实新来源，否则不要反复续同一个话头"
        else:
            label = "刚发出一条主动，先把窗口留给对方"
            next_tendency = "下一次根据回应再决定靠近或收住"
        if ignored >= 1:
            label = f"{label}，但前面已经有未回应"
            next_tendency = "沉默累积时不要加压，不要连续追问"
        if semantic_score < 45:
            next_tendency = "这次由头不算硬，后续要更依赖具体上下文"
        afterglow = {
            "ts": now,
            "status": "awaiting_reply",
            "label": _single_line(label, 140),
            "next_tendency": _single_line(next_tendency, 160),
            "reason": _single_line(reason, 50),
            "action": _single_line(action, 50),
            "semantic_kind": semantic_kind,
            "anchor_type": anchor_type,
            "semantic_score": semantic_score,
            "text": _single_line(_strip_internal_message_blocks(text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 160),
            "motive": _single_line(motive, 120),
            "summary": _single_line(action_summary, 140),
            "feedback": "",
            "reply_text": "",
            "reply_ts": 0,
        }
        user["proactive_afterglow"] = afterglow
        recent = user.setdefault("recent_proactive_afterglows", [])
        if not isinstance(recent, list):
            recent = []
            user["recent_proactive_afterglows"] = recent
        recent.append(dict(afterglow))
        del recent[:-8]
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["proactive_afterglow"] = afterglow["label"]
        continuity["proactive_afterglow_tendency"] = afterglow["next_tendency"]

    def _note_action_reply_feedback(self, user: dict[str, Any], action: str, text: str = "") -> None:
        action = _single_line(action, 40) or "message"
        affinity_tracker = getattr(self, "_note_action_affinity_reply_feedback", None)
        if callable(affinity_tracker):
            affinity_tracker(user, action)

        feedback = self._classify_action_reply_feedback(text)
        now = _now_ts()
        source = _single_line(user.get("last_proactive_source"), 40) or "unknown"
        matched_consequence = False
        for item in reversed(self._action_consequence_items(user)):
            if not isinstance(item, dict):
                continue
            if item.get("status") != "awaiting_reply":
                continue
            if _single_line(item.get("action"), 40) != action:
                continue
            source = _single_line(item.get("source"), 40) or source
            item["status"] = "replied"
            item["feedback"] = feedback
            item["reply_text"] = _single_line(text, 120)
            item["reply_ts"] = now
            matched_consequence = True
            break
        self._note_proactive_afterglow_reply(user, action=action, text=text, feedback=feedback, now=now)
        if not matched_consequence:
            # Do not let an unrelated late/passive reply inflate the last source.
            return
        source_feedback = user.setdefault("proactive_source_feedback", {})
        if not isinstance(source_feedback, dict):
            source_feedback = {}
            user["proactive_source_feedback"] = source_feedback
        bucket = source_feedback.setdefault(source, {})
        if not isinstance(bucket, dict):
            bucket = {}
            source_feedback[source] = bucket
        self._decay_proactive_source_feedback_bucket(bucket, now=now)
        bucket["replied"] = _safe_int(bucket.get("replied"), 0, 0) + 1
        bucket["weighted_replied"] = _safe_float(bucket.get("weighted_replied"), 0.0) + 1.0
        feedback_key = {
            "positive": "positive",
            "negative": "negative",
        }.get(feedback, "neutral")
        bucket[feedback_key] = _safe_int(bucket.get(feedback_key), 0, 0) + 1
        bucket[f"weighted_{feedback_key}"] = _safe_float(bucket.get(f"weighted_{feedback_key}"), 0.0) + 1.0
        bucket["last_reply_at"] = now
        bucket["last_feedback"] = feedback
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["last_reply_ts"] = now
        continuity["last_reply_feedback"] = feedback
        continuity["last_reply_text"] = _single_line(text, 120)

    def _note_proactive_afterglow_reply(
        self,
        user: dict[str, Any],
        *,
        action: str,
        text: str = "",
        feedback: str = "neutral",
        now: float | None = None,
    ) -> None:
        current = user.get("proactive_afterglow")
        if not isinstance(current, dict):
            return
        check_now = _now_ts() if now is None else now
        if check_now - _safe_float(current.get("ts"), 0) > 48 * 3600:
            return
        current["status"] = "replied"
        current["feedback"] = _single_line(feedback, 24)
        current["reply_text"] = _single_line(text, 160)
        current["reply_ts"] = check_now
        if feedback == "positive":
            current["label"] = "上一条主动被接住了，关系余温往回亮了一点"
            current["next_tendency"] = "后续可以自然一点，但不要立刻连续加码"
        elif feedback == "negative":
            current["label"] = "上一条主动被顶回来了，先收住一点"
            current["next_tendency"] = "后续主动更短、更少、更低压，避开同类动作"
        else:
            current["label"] = "上一条主动被回应了，话头算是落地"
            current["next_tendency"] = "后续可以顺着真实回复走，不要机械续主动"
        recent = user.setdefault("recent_proactive_afterglows", [])
        if isinstance(recent, list):
            recent.append(dict(current))
            del recent[:-8]
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["proactive_afterglow"] = current["label"]
        continuity["proactive_afterglow_tendency"] = current["next_tendency"]

    def _note_proactive_afterglow_outcome(
        self,
        user: dict[str, Any],
        *,
        status: str,
        note: str = "",
    ) -> None:
        normalized_status = _single_line(status, 32)
        if normalized_status not in {"blocked", "cancelled", "dropped", "deferred", "failed"}:
            return
        now = _now_ts()
        reason = _single_line(user.get("planned_proactive_reason"), 50)
        action = _single_line(user.get("planned_proactive_action"), 50)
        semantic_kind = _single_line(user.get("planned_proactive_semantic_kind"), 40)
        anchor_type = _single_line(user.get("planned_proactive_anchor_type"), 40)
        clean_note = _single_line(note, 140)
        if normalized_status == "deferred":
            label = "刚才那个主动念头被先收住了"
            tendency = "如果之后再出现，要带一点犹豫后的自然感，不要机械重试"
        elif normalized_status == "failed":
            label = "刚才那次主动没能送出去"
            tendency = "下一次不要假装它已经发生，先重新找更稳的切口"
        else:
            label = "刚才那个主动念头被放下了"
            tendency = "下一次避开同一个别扭点，等更自然的由头"
        if clean_note:
            label = f"{label}：{clean_note}"
        afterglow = {
            "ts": now,
            "status": normalized_status,
            "label": _single_line(label, 160),
            "next_tendency": _single_line(tendency, 160),
            "reason": reason,
            "action": action,
            "semantic_kind": semantic_kind,
            "anchor_type": anchor_type,
            "semantic_score": _safe_int(user.get("planned_proactive_semantic_score"), 0, 0, 100),
            "text": "",
            "motive": _single_line(user.get("planned_proactive_motive"), 120),
            "summary": "",
            "feedback": "",
            "reply_text": "",
            "reply_ts": 0,
        }
        user["proactive_afterglow"] = afterglow
        recent = user.setdefault("recent_proactive_afterglows", [])
        if not isinstance(recent, list):
            recent = []
            user["recent_proactive_afterglows"] = recent
        recent.append(dict(afterglow))
        del recent[:-8]
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["proactive_afterglow"] = afterglow["label"]
        continuity["proactive_afterglow_tendency"] = afterglow["next_tendency"]

    def _format_action_consequence_hint(self, user: dict[str, Any]) -> str:
        items = self._action_consequence_items(user)
        if not items:
            return ""
        lines: list[str] = []
        for item in items[-5:]:
            if not isinstance(item, dict):
                continue
            action = _single_line(item.get("action"), 30)
            reason = _single_line(item.get("reason"), 40)
            text = _single_line(item.get("text"), 70)
            status = _single_line(item.get("status"), 24)
            feedback = _single_line(item.get("feedback"), 24)
            reply = _single_line(item.get("reply_text"), 70)
            if not action and not text:
                continue
            when = self._format_timestamp_elapsed(item.get("ts"))
            parts = [f"{when}主动{action or 'message'}"]
            if reason:
                parts.append(f"原因:{reason}")
            if text:
                parts.append(f"内容:{text}")
            if status == "awaiting_reply":
                parts.append("还没有自然接上,下次不要当作用户刚刚主动找你")
            elif reply:
                parts.append(f"用户反馈:{feedback or 'neutral'}:{reply}")
            lines.append("- " + "；".join(parts))
        if not lines:
            return ""
        return "\n".join(lines)

    def _response_reverses_recent_proactive_media_ownership(
        self,
        response_text: str,
        user: dict[str, Any],
        inbound_text: str,
    ) -> bool:
        if not self._recent_proactive_media_ownership_context(user, inbound_text):
            return False
        cleaned = _single_line(response_text, 500)
        if not cleaned:
            return False
        depicted_actions = r"(?:洒|撒|溅|打翻|碰倒|弄倒|摔|掉|弄坏|打碎|受伤|烫|割|磕|撞)"
        if re.search(rf"我[^。！？!?\n]{{0,16}}{depicted_actions}", cleaned):
            return False
        return bool(
            re.search(rf"你[^。！？!?\n]{{0,18}}{depicted_actions}", cleaned)
            or re.search(r"(?:怎么|这么|也太)[^。！？!?\n]{0,10}(?:笨手笨脚|不小心|毛手毛脚)", cleaned)
            or re.search(
                r"(?:有没有|有没|没|会不会|别|记得|赶紧|快|先|小心)"
                r"[^。！？!?\n]{0,14}"
                r"(?:溅到|伤到|烫到|割到|弄到|碰到|受伤|手上|身上|衣服|疼)",
                cleaned,
            )
            or re.search(r"(?:你没事吧|没伤着吧|有没有受伤|疼不疼)", cleaned)
        )
