# -*- coding: utf-8 -*-
"""GroupWakeupPart03Mixin。

由 tools/split_mixin_domain.py 从 group_wakeup.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 486 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GroupWakeupMixin）。
"""
from __future__ import annotations

from .group_wakeup_shared import _persona_value, logger
from .group_wakeup_shared import Any
from .group_wakeup_shared import AstrMessageEvent
from .group_wakeup_shared import Mapping
from .group_wakeup_shared import _group_link_message_context
from .group_wakeup_shared import _now_ts
from .group_wakeup_shared import _safe_float
from .group_wakeup_shared import _safe_int
from .group_wakeup_shared import _single_line
from .group_wakeup_shared import random
from .group_wakeup_shared import re



class GroupWakeupPart03Mixin:
    """GroupWakeupPart03Mixin（从 GroupWakeupMixin 拆出）。"""


    def _evaluate_group_wakeup(
        self,
        group: dict[str, Any],
        *,
        event: Any = None,
        scene: dict[str, Any],
        sender_id: str,
        sender_name: str,
        text: str,
        group_id: str = "",
    ) -> dict[str, Any]:
        if not _persona_value(self, "enable_group_wakeup_enhancement", False):
            return {}
        original = _single_line(text, 1000)
        cleaned, has_link_payload = _group_link_message_context(original)
        if not cleaned:
            return {}
        if str(scene.get("talking_to") or "") == "bot":
            return {}
        trigger = str(scene.get("trigger") or "")
        if trigger in {"at_other", "reply_other", "at_all"}:
            # Still check for direct wakeup words (keywords) even when @ing
            # others.  Only skip soft signals so the bot is not intrusive.
            return self._evaluate_wakeup_direct_words_only(
                group, scene, sender_id, cleaned,
            )
        fixture_group_settings: Mapping[str, Any] = {}
        fixture_adapter = getattr(self, "_lab_fixture_adapter", None)
        fixture_settings = getattr(fixture_adapter, "group_wakeup_settings", None)
        if callable(fixture_settings):
            try:
                candidate_settings = fixture_settings(event)
                if isinstance(candidate_settings, Mapping):
                    fixture_group_settings = candidate_settings
            except Exception as exc:
                logger.warning(
                    "LAB fixture 群唤醒投影失败，已使用生产配置: %s",
                    type(exc).__name__,
                )
        fixture_interest_words = tuple(
            str(item)
            for item in fixture_group_settings.get("interest_words", ())
            if str(item).strip()
        )
        interest_words = (
            list(
                dict.fromkeys(
                    (*fixture_interest_words, *self._group_wakeup_interest_words(group))
                )
            )
            if fixture_interest_words
            else None
        )
        now = _now_ts()
        if self._group_sender_is_primary_user(sender_id):
            owner_direct_words = self._configured_group_owner_direct_wakeup_words()
            for word in owner_direct_words:
                if self._text_contains_wakeup_word(cleaned, word):
                    strength = self._group_wakeup_strength("direct_word", group, scene)
                    return {
                        "type": "direct_word",
                        "word": word,
                        "strength": strength,
                        "reason": "owner_direct_wakeup_word",
                        "note": "主要用户使用了专属强唤醒词。",
                    }
        direct_words = self._configured_group_direct_wakeup_words()
        for word in direct_words:
            if self._text_contains_wakeup_word(cleaned, word):
                strength = self._group_wakeup_strength("direct_word", group, scene)
                return {
                    "type": "direct_word",
                    "word": word,
                    "strength": strength,
                    "reason": "direct_wakeup_word",
                    "note": "群友提到了 Bot 名字或强唤醒词。",
                }
        if has_link_payload or bool(scene.get("quoted_link_payload")):
            return {}
        question_signal = self._group_wakeup_question_signal(original) if bool(_persona_value(self, "enable_group_wakeup_question", True)) else {}
        cold_group_signal = self._group_wakeup_cold_group_signal(group, cleaned, now)
        soft_signal_hit = bool(
            question_signal
            or cold_group_signal
            or any(
                self._text_contains_wakeup_word(cleaned, word)
                for word in list(_persona_value(self, "group_wakeup_context_words", []) or [])
                + (
                    interest_words
                    if interest_words is not None
                    else self._group_wakeup_interest_words(group)
                )
            )
        )
        high_intensity = self._group_high_intensity_state(group)
        if high_intensity.get("active"):
            if soft_signal_hit:
                self._record_group_wakeup_log(
                    group,
                    scene=scene,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=cleaned,
                    wakeup={"type": "high_intensity", "word": "", "reason": "high_intensity", "probability": 0.0},
                    result="blocked",
                    strength="",
                    note="群聊处于高强度收口模式,暂停弱相关、解惑、冷群和兴趣唤醒,优先合并处理明确叫到 Bot 的消息。",
                )
            return {}
        cooldown_getter = getattr(self, "_effective_group_wakeup_cooldown_seconds", None)
        group_wakeup_cooldown = cooldown_getter() if callable(cooldown_getter) else _safe_int(_persona_value(self, "group_wakeup_cooldown_seconds", 0), 0, 0)
        if group_wakeup_cooldown > 0 and now - _safe_float(group.get("last_group_wakeup_at"), 0) < group_wakeup_cooldown:
            if soft_signal_hit:
                self._record_group_wakeup_log(
                    group,
                    scene=scene,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=cleaned,
                    wakeup={"type": "high_intensity", "word": "", "reason": "high_intensity", "probability": 0.0},
                    result="blocked",
                    strength="",
                    note="群聊处于高强度收口模式,暂停弱相关、解惑、冷群和兴趣唤醒,优先合并处理明确叫到 Bot 的消息。",
                )
            return {}
        cooldown_getter = getattr(self, "_effective_group_wakeup_cooldown_seconds", None)
        group_wakeup_cooldown = cooldown_getter() if callable(cooldown_getter) else _safe_int(_persona_value(self, "group_wakeup_cooldown_seconds", 0), 0, 0)
        if group_wakeup_cooldown > 0 and now - _safe_float(group.get("last_group_wakeup_at"), 0) < group_wakeup_cooldown:
            if soft_signal_hit:
                self._record_group_wakeup_log(
                    group,
                    scene=scene,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=cleaned,
                    wakeup={"type": "cooldown", "word": "", "reason": "cooldown", "probability": 0.0},
                    result="blocked",
                    strength="",
                    note="命中了可唤醒线索,但仍在冷却时间内,所以没有接入回复链。",
                )
            return {}
        for word in _persona_value(self, "group_wakeup_context_words", []) or []:
            if not self._text_contains_wakeup_word(cleaned, word):
                continue
            if self._group_wakeup_context_should_reply(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=cleaned,
                matched_word=word,
            ):
                strength = self._group_wakeup_strength("context_word", group, scene)
                return {
                    "type": "context_word",
                    "word": word,
                    "strength": strength,
                    "reason": "contextual_wakeup_word",
                    "note": "群友提到了可能和 Bot 有关的唤醒词。",
                }
        if question_signal:
            threshold_getter = getattr(self, "_effective_group_wakeup_question_threshold", None)
            threshold = threshold_getter() if callable(threshold_getter) else max(0, min(100, _safe_int(_persona_value(self, "group_wakeup_question_threshold", 65), 65, 0)))
            score, fatigue, score_notes = self._group_wakeup_question_score_context(group, scene, question_signal)
            if score >= threshold:
                strength = self._group_wakeup_strength("question", group, scene)
                final_intensity = "高" if score >= 85 else ("中" if score >= threshold else "低")
                reason_detail = self._group_wakeup_reason_detail(
                    {
                        **question_signal,
                        "type": "question",
                        "score": score,
                        "threshold": threshold,
                        "intensity": final_intensity,
                    },
                    score_notes=score_notes,
                )
                return {
                    "type": "question",
                    "word": _single_line(question_signal.get("word"), 60) or "疑问",
                    "strength": strength,
                    "score": score,
                    "threshold": threshold,
                    "intensity": final_intensity,
                    "help_type": _single_line(question_signal.get("help_type"), 24) or "解释",
                    "reason": _single_line(question_signal.get("reason"), 60) or "open_question",
                    "reason_label": _single_line(question_signal.get("reason_label"), 80) or self._group_wakeup_reason_label("question", question_signal.get("reason")),
                    "reason_detail": reason_detail,
                    "note": f"答疑唤醒：{reason_detail}。",
                }
            self._record_group_wakeup_log(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=cleaned,
                wakeup={
                    "type": "question",
                    "word": _single_line(question_signal.get("word"), 60) or "疑问",
                    "reason": question_signal.get("reason"),
                    "score": score,
                    "threshold": threshold,
                    "intensity": question_signal.get("intensity"),
                    "help_type": question_signal.get("help_type"),
                },
                result="missed",
                strength="",
                fatigue=fatigue,
                note=f"解惑强度 {score}/{threshold} 未达阈值" + (f"（{'、'.join(score_notes[:3])}）" if score_notes else ""),
            )
        if cold_group_signal:
            threshold_getter = getattr(self, "_effective_group_wakeup_cold_group_threshold", None)
            threshold = threshold_getter() if callable(threshold_getter) else max(0, min(100, _safe_int(_persona_value(self, "group_wakeup_cold_group_threshold", 65), 65, 0)))
            score = max(0, min(100, _safe_int(cold_group_signal.get("score"), 0, 0)))
            fatigue = self._group_wakeup_fatigue(group)
            if score >= threshold:
                strength = self._group_wakeup_strength("cold_group", group, scene)
                reason_detail = self._group_wakeup_reason_detail(
                    {
                        **cold_group_signal,
                        "type": "cold_group",
                        "score": score,
                        "threshold": threshold,
                    }
                )
                return {
                    "type": "cold_group",
                    "word": _single_line(cold_group_signal.get("word"), 60) or "冷群",
                    "strength": strength,
                    "score": score,
                    "threshold": threshold,
                    "reason": _single_line(cold_group_signal.get("reason"), 60) or "cold_group",
                    "reason_label": _single_line(cold_group_signal.get("reason_label"), 80) or self._group_wakeup_reason_label("cold_group", cold_group_signal.get("reason")),
                    "reason_detail": reason_detail,
                    "note": f"冷群唤醒：{reason_detail}。",
                }
            self._record_group_wakeup_log(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=cleaned,
                wakeup={
                    "type": "cold_group",
                    "word": _single_line(cold_group_signal.get("word"), 60) or "冷群",
                    "reason": cold_group_signal.get("reason"),
                    "score": score,
                    "threshold": threshold,
                },
                result="missed",
                strength="",
                fatigue=fatigue,
                note=f"冷群强度 {score}/{threshold} 未达阈值",
            )
        probability_getter = getattr(self, "_effective_group_wakeup_interest_probability", None)
        base_interest_probability = (
            probability_getter()
            if callable(probability_getter)
            else max(0.0, min(1.0, float(_persona_value(self, "group_wakeup_interest_probability", 0.0) or 0.0)))
        )
        fixture_minimum_probability = max(
            0.0,
            min(1.0, _safe_float(fixture_group_settings.get("minimum_probability"), 0.0, 0.0)),
        )
        base_interest_probability = max(base_interest_probability, fixture_minimum_probability)
        if base_interest_probability <= 0:
            return {}
        probability_misses: list[dict[str, Any]] = []
        for word in (
            interest_words
            if interest_words is not None
            else self._group_wakeup_interest_words(group)
        ):
            if not self._text_contains_wakeup_word(cleaned, word):
                continue
            probability, fatigue = self._group_wakeup_probability_context(group, scene, base_interest_probability, "interest")
            topic_weight = self._group_wakeup_topic_interest_weight(
                group,
                word,
                sender_id=sender_id,
                text=cleaned,
                group_id=group_id,
            )
            probability *= _safe_float(topic_weight.get("multiplier"), 1.0, 0.0)
            probability = max(probability, fixture_minimum_probability)
            probability = min(
                1.0 if fixture_minimum_probability >= 1.0 else 0.95,
                max(0.0, probability),
            )
            if random.random() <= probability:
                strength = self._group_wakeup_strength("interest", group, scene)
                return {
                    "type": "interest",
                    "word": word,
                    "strength": strength,
                    "probability": round(probability, 3),
                    "reason": "interest_keyword",
                    "topic_weight": topic_weight,
                    "note": f"群聊出现了兴趣词：{word}。",
                }
            self._record_group_wakeup_log(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=cleaned,
                wakeup={"type": "interest", "word": word, "reason": "probability_miss", "probability": round(probability, 3), "topic_weight": topic_weight},
                result="missed",
                strength="",
                fatigue=fatigue,
                note="命中了兴趣词,但本次概率未触发,所以没有接话。" + (f" 话题权重：{topic_weight.get('reason')}" if topic_weight.get("reason") else ""),
            )
            probability_misses.append(
                {
                    "word": word,
                    "probability": round(probability, 3),
                    "topic_weight": topic_weight,
                }
            )
        if probability_misses:
            # Keep this result on the transient scene so the same message cannot
            # immediately fall through to the independent general-interjection draw.
            scene["interest_keyword_probability_miss"] = {
                "words": [item["word"] for item in probability_misses],
                "attempts": probability_misses,
            }
        return {}

    def _evaluate_wakeup_direct_words_only(
        self,
        group: dict[str, Any],
        scene: dict[str, Any],
        sender_id: str,
        cleaned: str,
    ) -> dict[str, Any]:
        """Check only direct wakeup words when the message @s other users.

        Soft signals (question, cold group, context words, interest) are
        skipped so the bot is not intrusive in conversations between others.
        """
        if self._group_sender_is_primary_user(sender_id):
            owner_direct_words = self._configured_group_owner_direct_wakeup_words()
            for word in owner_direct_words:
                if self._text_contains_wakeup_word(cleaned, word):
                    strength = self._group_wakeup_strength("direct_word", group, scene)
                    return {
                        "type": "direct_word",
                        "word": word,
                        "strength": strength,
                        "reason": "owner_direct_wakeup_word",
                        "note": "主要用户使用了专属强唤醒词（消息中含 @他人）。",
                    }
        direct_words = self._configured_group_direct_wakeup_words()
        for word in direct_words:
            if self._text_contains_wakeup_word(cleaned, word):
                strength = self._group_wakeup_strength("direct_word", group, scene)
                return {
                    "type": "direct_word",
                    "word": word,
                    "strength": strength,
                    "reason": "direct_wakeup_word",
                    "note": "群友提到了强唤醒词（消息中含 @他人）。",
                }
        return {}

    def _infer_group_scene(
        self,
        event: AstrMessageEvent | None,
        group: dict[str, Any],
        *,
        sender_id: str,
        sender_name: str,
        text: str,
    ) -> dict[str, Any]:
        sender_id = str(sender_id or "").strip()
        sender_name = _single_line(sender_name, 40) or sender_id or "群友"
        cleaned = _single_line(text, 260)
        signals = self._event_scene_signals(event) if event is not None else {"self_id": "", "at_targets": [], "at_all": False, "reply_to_id": ""}
        self_id = str(signals.get("self_id") or "").strip()
        at_targets = signals.get("at_targets") if isinstance(signals.get("at_targets"), list) else []
        non_bot_targets = [item for item in at_targets if isinstance(item, dict) and not item.get("is_bot")]
        scene = {
            "trigger": "group_message",
            "sender_id": sender_id,
            "sender_name": sender_name,
            "talking_to": "group",
            "talking_to_name": "群里所有人",
            "reason": "default_group",
            "at_targets": at_targets,
            "reply_to_id": _single_line(signals.get("reply_to_id"), 40),
        }
        if any(isinstance(item, dict) and item.get("is_bot") for item in at_targets):
            scene.update({"trigger": "at_bot", "talking_to": "bot", "talking_to_name": "你", "reason": "explicit_at_bot"})
            return scene
        if signals.get("at_all"):
            scene.update({"trigger": "at_all", "reason": "at_all"})
            return scene
        if non_bot_targets:
            # Also check whether the bot is also @ed in the same message.
            # If the bot is also @ed, treat this as "bot conversation" rather
            # than "other-only", so downstream keyword/wakeup logic fires.
            bot_also_attended = any(
                isinstance(item, dict) and item.get("is_bot")
                for item in at_targets
            ) or bool(re.search(r"\[CQ:at,qq=" + re.escape(str(self_id or "")) + r"(?:,|\])", cleaned))
            if bot_also_attended:
                scene.update({
                    "trigger": "at_bot",
                    "talking_to": "bot",
                    "talking_to_name": "你",
                    "reason": "explicit_at_bot",
                    "at_other_targets": [
                        {"user_id": str(t.get("user_id", "")), "name": str(t.get("name", ""))}
                        for t in non_bot_targets
                    ],
                })
                return scene
            target = non_bot_targets[0]
            target_id = str(target.get("user_id") or "")
            scene.update({
                "trigger": "at_other",
                "talking_to": target_id,
                "talking_to_name": self._group_member_identity_label(target_id, target.get("name"), limit=40),
                "reason": "explicit_at_other",
            })
            return scene
        reply_to_id = _single_line(signals.get("reply_to_id"), 40)
        if reply_to_id:
            if self_id and reply_to_id == self_id:
                scene.update({"trigger": "reply_bot", "talking_to": "bot", "talking_to_name": "你", "reason": "reply_to_bot"})
                return scene
            recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
            target_name = ""
            for item in reversed(recent):
                if isinstance(item, dict) and str(item.get("sender_id") or "") == reply_to_id:
                    target_name = _single_line(item.get("identity_name") or item.get("name"), 40)
                    break
            scene.update({
                "trigger": "reply_other",
                "talking_to": reply_to_id,
                "talking_to_name": self._group_member_identity_label(reply_to_id, target_name, limit=40),
                "reason": "reply_to_other",
            })
            return scene
        setting_getter = getattr(self, "persona_setting", None)
        bot_name = str(setting_getter("bot_name", "") if callable(setting_getter) else getattr(self, "bot_name", "") or "")
        if bool(_persona_value(self, "enable_group_bot_name_wakeup", True)) and bot_name and bot_name in cleaned:
            scene.update({"trigger": "mention_bot_name", "talking_to": "bot", "talking_to_name": "你", "reason": "bot_name_mentioned"})
            return scene
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        last_other = None
        skipped_short = 0
        for item in reversed(recent[-8:]):
            if not isinstance(item, dict):
                continue
            if str(item.get("sender_id") or "") != sender_id:
                if self._group_scene_short_interjection(item.get("text")):
                    skipped_short += 1
                    continue
                last_other = item
                break
        if last_other:
            time_gap = _now_ts() - _safe_float(last_other.get("ts"), 0)
            if str(last_other.get("talking_to") or "") == sender_id and time_gap < 60:
                target_id = str(last_other.get("sender_id") or "")
                scene.update({
                    "trigger": "reply_in_flow",
                    "talking_to": target_id,
                    "talking_to_name": self._group_member_identity_label(target_id, last_other.get("identity_name") or last_other.get("name"), limit=40),
                    "reason": "recent_message_addressed_sender_after_short_interjection" if skipped_short else "recent_message_addressed_sender",
                })
            elif time_gap < 15 and str(last_other.get("talking_to") or "group") == "group":
                target_id = str(last_other.get("sender_id") or "")
                scene.update({
                    "trigger": "quick_follow",
                    "talking_to": target_id,
                    "talking_to_name": self._group_member_identity_label(target_id, last_other.get("identity_name") or last_other.get("name"), limit=40),
                    "reason": "quick_follow_after_group_message_after_short_interjection" if skipped_short else "quick_follow_after_group_message",
                })
        return scene

    def _scene_talking_to_text(self, scene: dict[str, Any]) -> str:
        target = str(scene.get("talking_to") or "group")
        name = _single_line(scene.get("talking_to_name"), 80)
        if target == "bot":
            return "你（Bot）"
        if target == "group":
            return "群里所有人（非特定对象）"
        return name or target
