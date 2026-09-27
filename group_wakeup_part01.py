# -*- coding: utf-8 -*-
"""GroupWakeupPart01Mixin。

由 tools/split_mixin_domain.py 从 group_wakeup.py 机械抽取（22 个方法 + 0 个模块级名字 + 0 个类级赋值 / 463 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GroupWakeupMixin）。
"""
from __future__ import annotations

from .group_wakeup_shared import _persona_value
from .group_wakeup_shared import Any
from .group_wakeup_shared import AstrMessageEvent
from .group_wakeup_shared import _now_ts
from .group_wakeup_shared import _safe_float
from .group_wakeup_shared import _safe_int
from .group_wakeup_shared import _single_line
from .group_wakeup_shared import re



class GroupWakeupPart01Mixin:
    """GroupWakeupPart01Mixin（从 GroupWakeupMixin 拆出）。"""


    @staticmethod
    def _group_wakeup_allows_general_interjection(scene: dict[str, Any] | None) -> bool:
        return not bool(
            isinstance(scene, dict)
            and scene.get("interest_keyword_probability_miss")
        )

    def _group_message_addresses_bot(self, event: AstrMessageEvent, text: str) -> bool:
        if getattr(event, "is_at_or_wake_command", False) or getattr(event, "is_wake", False):
            return True
        signals = self._event_scene_signals(event)
        at_targets = signals.get("at_targets") if isinstance(signals.get("at_targets"), list) else []
        if any(isinstance(item, dict) and item.get("is_bot") for item in at_targets):
            return True
        if any(isinstance(item, dict) and str(item.get("user_id") or "").strip() and not item.get("is_bot") for item in at_targets):
            return False
        cleaned = str(text or "")
        setting_getter = getattr(self, "persona_setting", None)
        bot_name = str(setting_getter("bot_name", "") if callable(setting_getter) else getattr(self, "bot_name", "") or "")
        if bool(_persona_value(self, "enable_group_bot_name_wakeup", True)) and bot_name and bot_name in cleaned:
            return True
        return False

    def _group_message_explicitly_ats_bot(self, event: AstrMessageEvent) -> bool:
        signals = self._event_scene_signals(event)
        at_targets = signals.get("at_targets") if isinstance(signals.get("at_targets"), list) else []
        if any(isinstance(item, dict) and item.get("is_bot") for item in at_targets):
            return True
        self_id = str(signals.get("self_id") or "").strip()
        raw_text = str(getattr(event, "message_str", "") or "")
        if self_id and re.search(rf"\[CQ:at,qq={re.escape(self_id)}(?:,|\])", raw_text):
            return True
        if self_id:
            return False
        has_at = any(isinstance(item, dict) and str(item.get("user_id") or "").strip() for item in at_targets)
        if not has_at and re.search(r"\[CQ:at,qq=\d+", raw_text):
            has_at = True
        return bool(has_at and getattr(event, "is_at_or_wake_command", False))

    @staticmethod
    def _text_contains_wakeup_word(text: str, word: str) -> bool:
        cleaned = _single_line(text, 260)
        token = _single_line(word, 60)
        if not cleaned or not token:
            return False
        if re.fullmatch(r"[A-Za-z0-9_\-]{2,}", token):
            return bool(re.search(rf"(?<![A-Za-z0-9_\-]){re.escape(token)}(?![A-Za-z0-9_\-])", cleaned, re.IGNORECASE))
        return token in cleaned

    def _configured_group_direct_wakeup_words(self) -> list[str]:
        words = list(_persona_value(self, "group_wakeup_direct_words", []) or [])
        bot_name = _single_line(_persona_value(self, "bot_name", ""), 40)
        if bool(_persona_value(self, "enable_group_bot_name_wakeup", True)) and bot_name and bot_name not in words:
            words.insert(0, bot_name)
        return list(dict.fromkeys(word for word in words if _single_line(word, 60)))

    def _configured_group_owner_direct_wakeup_words(self) -> list[str]:
        words = list(_persona_value(self, "group_wakeup_owner_direct_words", []) or [])
        return list(dict.fromkeys(word for word in words if _single_line(word, 60)))

    def _group_wakeup_from_image_vision_summary(self, summary: Any, *, sender_id: str = "") -> dict[str, Any]:
        if not bool(_persona_value(self, "enable_group_wakeup_enhancement", False)):
            return {}
        if not bool(_persona_value(self, "enable_group_image_wakeup", False)):
            return {}
        cleaned = _single_line(summary, 1000)
        if not cleaned:
            return {}
        if self._group_sender_is_primary_user(sender_id):
            for word in self._configured_group_owner_direct_wakeup_words():
                if self._text_contains_wakeup_word(cleaned, word):
                    return {
                        "type": "direct_word",
                        "word": word,
                        "reason": "image_direct_wakeup_word",
                        "note": "群聊图片视觉摘要命中了主要用户专属强唤醒词。",
                        "source": "image_vision",
                    }
        for word in self._configured_group_direct_wakeup_words():
            if self._text_contains_wakeup_word(cleaned, word):
                return {
                    "type": "direct_word",
                    "word": word,
                    "reason": "image_direct_wakeup_word",
                    "note": "群聊图片视觉摘要命中了 Bot 名称或强唤醒词。",
                    "source": "image_vision",
                }
        return {}

    def _group_sender_is_primary_user(self, sender_id: str) -> bool:
        raw_id = _single_line(sender_id, 128)
        if not raw_id:
            return False
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        try:
            canonical_id = _single_line(canonicalizer(raw_id), 128) if callable(canonicalizer) else raw_id
        except Exception:
            canonical_id = raw_id
        target_getter = getattr(self, "_configured_target_ids", None)
        try:
            target_ids = {
                _single_line(canonicalizer(item), 128) if callable(canonicalizer) else _single_line(item, 128)
                for item in (target_getter() if callable(target_getter) else [])
            }
        except Exception:
            target_ids = set()
        if canonical_id in target_ids:
            return True
        data = getattr(self, "data", {})
        users = data.get("users") if isinstance(data, dict) and isinstance(data.get("users"), dict) else {}
        user = users.get(canonical_id) or users.get(raw_id)
        if not isinstance(user, dict):
            return False
        role_getter = getattr(self, "_private_user_role", None)
        if callable(role_getter):
            try:
                return role_getter(user, canonical_id) == "owner"
            except Exception:
                pass
        return str(user.get("relationship_role") or "").strip().lower() in {"owner", "main", "primary"}

    def _generated_group_interest_keywords(self, group: dict[str, Any] | None = None) -> list[str]:
        words: list[str] = []

        def add(value: Any) -> None:
            text = _single_line(value, 32)
            if not text or len(text) < 2:
                return
            if text.isdigit():
                return
            if text in {"暂无", "未知", "平稳", "群聊", "用户", "大家", "有人", "什么", "怎么"}:
                return
            words.append(text)

        for item in self._parse_text_list_config(_persona_value(self, "web_exploration_interests", []), limit=40):
            add(item)
        skill_state = self.data.get("skill_growth") if isinstance(getattr(self, "data", None), dict) else {}
        skills = skill_state.get("skills") if isinstance(skill_state, dict) and isinstance(skill_state.get("skills"), dict) else {}
        for item in list(skills.values())[:16]:
            if isinstance(item, dict):
                add(item.get("name"))
        if isinstance(group, dict):
            slang = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
            for item in slang[:20]:
                if isinstance(item, dict) and _safe_int(item.get("count"), 0, 0) >= 2:
                    add(item.get("term"))
            threads = group.get("topic_threads") if isinstance(group.get("topic_threads"), list) else []
            for item in threads[:12]:
                if isinstance(item, dict):
                    add(item.get("topic") or item.get("title"))
        cleaned: list[str] = []
        seen: set[str] = set()
        for word in words:
            key = word.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(word)
            if len(cleaned) >= _safe_int(_persona_value(self, "group_wakeup_generated_keyword_limit", 12), 12, 1):
                break
        return cleaned

    def _group_wakeup_interest_words(self, group: dict[str, Any] | None = None) -> list[str]:
        manual = list(_persona_value(self, "group_wakeup_interest_keywords", []) or [])
        generated = self._generated_group_interest_keywords(group)
        values: list[str] = []
        seen: set[str] = set()
        for word in manual + generated:
            word = _single_line(word, 40)
            key = word.lower()
            if not word or key in seen:
                continue
            seen.add(key)
            values.append(word)
        generated_limit = _safe_int(_persona_value(self, "group_wakeup_generated_keyword_limit", 12), 12, 1)
        return values[: max(1, generated_limit + len(manual))]

    def _group_scene_short_interjection(self, text: Any) -> bool:
        cleaned = _single_line(text, 80)
        compact = re.sub(r"\s+", "", cleaned)
        if not compact:
            return True
        if re.fullmatch(r"(?:\[图片\]|\[表情\]|\[动画表情\]|\[语音\]|\[视频\]|图片|表情|草|艹|6+|w+|哈{1,6}|笑死|确实|雀食|对|嗯|啊|哦|好|好耶|离谱|绷|急|乐|？|\?|!|！|。|…|~|～){1,6}", compact, flags=re.I):
            return True
        if len(compact) <= 8 and not re.search(r"(你|妳|bot|Bot|吗|呢|啥|什么|怎么|为什么|咋|然后|觉得|对吧|是吧|咋办|怎么办|帮|求)", compact):
            return True
        return False

    def _group_implicit_reply_score(self, text: Any, *, matched_word: str = "", relation_hit: bool = False, mentions_bot: bool = False) -> int:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return 0
        score = 0
        if mentions_bot:
            score += 70
        # A weak wake word is only a contextual hint.  It must be accompanied
        # by a clear request or bot-directed sentence before entering the reply
        # chain; the direct-word path above remains unchanged for strong words.
        if matched_word:
            score += 18
        if relation_hit:
            score += 22
        if re.search(r"(你|妳|bot|Bot|机器人).{0,12}(觉得|看|说|怎么说|咋看|会不会|能不能|要不要|帮|解释|评价)", cleaned):
            score += 42
        if re.search(r"(你觉得|你看|你说|你来|问你|那你|所以你|按你说)", cleaned):
            score += 40
        if re.search(r"(然后呢|然后嘞|后来呢|接着呢|所以呢|咋办|怎么办|怎么说|怎么看|咋看)", cleaned):
            score += 32
        if re.search(r"(对吧|是吧|对不对|是不是|可以吧|行吧|没错吧)[。！？!?~～]*$", cleaned):
            score += 28
        if re.search(r"(吗|嘛|呢|？|\?)", cleaned):
            score += 20
        if re.search(r"(帮|求|救|解释|回答|评价|推荐|看看|分析|判断)", cleaned):
            score += 18
        if len(cleaned) <= 40 and (matched_word or mentions_bot):
            score += 10
        return score

    def _group_wakeup_context_should_reply(
        self,
        group: dict[str, Any],
        *,
        scene: dict[str, Any],
        sender_id: str,
        sender_name: str,
        text: str,
        matched_word: str,
    ) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        if re.search(r"(别回|不要回|不用回|不是叫你|不是问你|别理|不要理)", cleaned):
            return False
        if str(scene.get("talking_to") or "") not in {"group", "bot"}:
            return False
        if str(scene.get("trigger") or "") in {"at_other", "reply_other"}:
            return False
        relation_hit = bool(self._select_worldbook_member_profiles_for_group(group, sender_id=sender_id, text=cleaned))
        mentions_bot = any(self._text_contains_wakeup_word(cleaned, word) for word in self._configured_group_direct_wakeup_words())
        score = self._group_implicit_reply_score(
            cleaned,
            matched_word=matched_word,
            relation_hit=relation_hit,
            mentions_bot=mentions_bot,
        )
        threshold = 68 if str(scene.get("talking_to") or "") == "bot" else 72
        return score >= threshold

    def _group_wakeup_fatigue(self, group: dict[str, Any]) -> dict[str, Any]:
        raw = group.get("group_wakeup_fatigue") if isinstance(group.get("group_wakeup_fatigue"), dict) else {}
        now = _now_ts()
        value = _safe_float(raw.get("value"), 0.0, 0.0)
        last_ts = _safe_float(raw.get("updated_ts"), 0.0, 0.0)
        decay_minutes = max(5, _safe_int(_persona_value(self, "group_wakeup_fatigue_decay_minutes", 90), 90, 5))
        if last_ts > 0 and now > last_ts:
            value = max(0.0, value - ((now - last_ts) / max(1.0, decay_minutes * 60.0)))
        limit = max(1.0, float(_persona_value(self, "group_wakeup_fatigue_limit", 5) or 5))
        ratio = value / limit
        if ratio >= 1.0:
            level = "high"
            label = "疲劳高"
        elif ratio >= 0.55:
            level = "medium"
            label = "有点累"
        elif value >= 0.4:
            level = "low"
            label = "轻微"
        else:
            level = "none"
            label = "无"
        return {
            "value": round(value, 2),
            "limit": int(limit),
            "ratio": round(min(1.0, max(0.0, ratio)), 3),
            "level": level,
            "label": label,
            "updated_ts": now,
        }

    def _bump_group_wakeup_fatigue(self, group: dict[str, Any], wakeup_type: str) -> dict[str, Any]:
        fatigue = self._group_wakeup_fatigue(group)
        weight = 0.6 if wakeup_type == "interest" else (1.2 if wakeup_type == "direct_word" else 1.0)
        limit = max(1.0, float(fatigue.get("limit") or _persona_value(self, "group_wakeup_fatigue_limit", 5) or 5))
        value = min(limit + 2.0, _safe_float(fatigue.get("value"), 0.0, 0.0) + weight)
        group["group_wakeup_fatigue"] = {
            "value": round(value, 2),
            "limit": int(limit),
            "updated_ts": _now_ts(),
            "last_type": _single_line(wakeup_type, 40),
        }
        return self._group_wakeup_fatigue(group)

    def _group_wakeup_strength(self, wakeup_type: str, group: dict[str, Any], scene: dict[str, Any]) -> str:
        fatigue = self._group_wakeup_fatigue(group)
        level = str(fatigue.get("level") or "none")
        state = self.data.get("daily_state") if isinstance(getattr(self, "data", None), dict) else {}
        runtime = state.get("sleep_runtime") if isinstance(state, dict) and isinstance(state.get("sleep_runtime"), dict) else {}
        phase = str(runtime.get("phase") or "")
        if phase in {"falling_asleep", "light_sleep", "sleeping_again", "woken"}:
            return "interrupt" if wakeup_type in {"direct_word", "context_word"} else "light"
        if level == "high" and wakeup_type != "direct_word":
            return "light"
        if wakeup_type == "direct_word":
            return "strong"
        if wakeup_type == "context_word":
            return "normal"
        if wakeup_type == "question":
            return "normal"
        if wakeup_type == "cold_group":
            return "light"
        return "normal" if level == "none" and str(scene.get("trigger") or "") == "quiet_flow" else "light"

    @staticmethod
    def _group_wakeup_strength_label(strength: str) -> str:
        return {
            "light": "轻唤醒",
            "normal": "普通唤醒",
            "strong": "强唤醒",
            "interrupt": "打断休息",
        }.get(str(strength or ""), "普通唤醒")

    @staticmethod
    def _group_wakeup_reason_label(wakeup_type: str, reason: str = "") -> str:
        wakeup_type = str(wakeup_type or "")
        reason = str(reason or "")
        labels = {
            "direct_wakeup_word": "提到 Bot 名字或强唤醒词",
            "image_direct_wakeup_word": "图片内容命中 Bot 名字或强唤醒词",
            "owner_direct_wakeup_word": "主要用户使用专属强唤醒词",
            "contextual_wakeup_word": "提到弱相关唤醒词且语境需要 Bot 接话",
            "interest_keyword": "命中兴趣关键词",
            "probability_miss": "命中兴趣词但概率未触发",
            "cooldown": "命中线索但仍在冷却",
            "high_intensity": "高强度收口中暂停弱唤醒",
            "open_help": "群里有人向懂的人求助",
            "help_request": "群里有人明确求助",
            "explain_question": "群里有人问原因或含义",
            "identify_question": "群里有人请求识别或判断",
            "question_mark": "句末疑问且包含问题词",
            "question": "群里有人提出问题",
            "cold_group_opening": "群聊安静后有人开场叫人",
            "cold_group_greeting": "群聊安静后有人问候/冒泡",
            "cold_group_help": "群聊安静后有人求助",
            "cold_group": "群聊安静后有人重新开口",
        }
        return labels.get(reason) or {
            "question": "答疑唤醒",
            "cold_group": "冷群唤醒",
            "direct_word": "强唤醒词",
            "context_word": "弱相关唤醒",
            "interest": "兴趣唤醒",
        }.get(wakeup_type, reason or wakeup_type or "唤醒")

    def _group_wakeup_reason_detail(self, wakeup: dict[str, Any], *, score_notes: list[str] | None = None) -> str:
        wakeup_type = _single_line(wakeup.get("type"), 40)
        reason = _single_line(wakeup.get("reason"), 80)
        label = self._group_wakeup_reason_label(wakeup_type, reason)
        score = _safe_int(wakeup.get("score"), 0, 0)
        threshold = _safe_int(wakeup.get("threshold"), 0, 0)
        parts = [label]
        if score or threshold:
            parts.append(f"强度 {score}/{threshold or '-'}")
        help_type = _single_line(wakeup.get("help_type"), 24)
        if help_type:
            parts.append(f"类型 {help_type}")
        idle_seconds = _safe_float(wakeup.get("idle_seconds"), 0.0, 0.0)
        if idle_seconds > 0:
            parts.append(f"冷群 {max(1, int(idle_seconds // 60))} 分钟")
        if score_notes:
            parts.append("；".join(score_notes[:3]))
        return _single_line("，".join(parts), 180)

    def _record_group_wakeup_log(
        self,
        group: dict[str, Any],
        *,
        scene: dict[str, Any],
        sender_id: str,
        sender_name: str,
        text: str,
        wakeup: dict[str, Any],
        result: str = "woke",
        strength: str = "",
        fatigue: dict[str, Any] | None = None,
        note: str = "",
    ) -> None:
        logs = group.setdefault("group_wakeup_logs", [])
        if not isinstance(logs, list):
            logs = []
            group["group_wakeup_logs"] = logs
        fatigue = fatigue if isinstance(fatigue, dict) else self._group_wakeup_fatigue(group)
        wakeup_type = _single_line(wakeup.get("type"), 40)
        strength = _single_line(strength or wakeup.get("strength"), 24)
        reason = _single_line(wakeup.get("reason"), 80)
        reason_label = _single_line(wakeup.get("reason_label"), 80) or self._group_wakeup_reason_label(wakeup_type, reason)
        reason_detail = _single_line(wakeup.get("reason_detail"), 180) or self._group_wakeup_reason_detail(wakeup)
        logs.append(
            {
                "ts": _now_ts(),
                "result": _single_line(result, 32),
                "type": wakeup_type,
                "word": _single_line(wakeup.get("word"), 60),
                "strength": strength,
                "strength_label": self._group_wakeup_strength_label(strength),
                "probability": _safe_float(wakeup.get("probability"), 0.0, 0.0),
                "score": _safe_int(wakeup.get("score"), 0, 0),
                "threshold": _safe_int(wakeup.get("threshold"), 0, 0),
                "intensity": _single_line(wakeup.get("intensity"), 20),
                "help_type": _single_line(wakeup.get("help_type"), 30),
                "reason": reason,
                "reason_label": reason_label,
                "reason_detail": reason_detail,
                "topic_weight": wakeup.get("topic_weight") if isinstance(wakeup.get("topic_weight"), dict) else {},
                "note": _single_line(note or wakeup.get("note"), 180),
                "sender_id": _single_line(sender_id, 40),
                "sender_name": _single_line(sender_name, 40),
                "text": _single_line(text, 160),
                "scene_trigger": _single_line(scene.get("trigger"), 40),
                "fatigue_value": _safe_float(fatigue.get("value"), 0.0, 0.0),
                "fatigue_label": _single_line(fatigue.get("label"), 20),
            }
        )
        limit = max(10, _safe_int(_persona_value(self, "group_wakeup_log_limit", 80), 80, 10))
        del logs[:-limit]

    def _group_recent_wakeup_count(self, group: dict[str, Any], *, now: float | None = None, window_seconds: int | None = None) -> int:
        now = now or _now_ts()
        window = max(15, int(window_seconds or _persona_value(self, "group_high_intensity_wakeup_window_seconds", 60) or 60))
        logs = group.get("group_wakeup_logs") if isinstance(group.get("group_wakeup_logs"), list) else []
        count = 0
        for item in reversed(logs):
            if not isinstance(item, dict):
                continue
            ts = _safe_float(item.get("ts"), 0.0, 0.0)
            if ts <= 0:
                continue
            if now - ts > window:
                break
            if str(item.get("result") or "") == "woke":
                count += 1
        return count

    def _group_high_intensity_state(self, group: dict[str, Any], *, now: float | None = None, mutate: bool = True) -> dict[str, Any]:
        now = now or _now_ts()
        if not _persona_value(self, "enable_group_high_intensity_mode", True):
            return {"active": False, "reason": "disabled", "recent_wakeups": 0, "threshold": 0, "until_ts": 0.0}
        window = max(15, _safe_int(_persona_value(self, "group_high_intensity_wakeup_window_seconds", 60), 60, 15, 600))
        threshold = max(2, _safe_int(_persona_value(self, "group_high_intensity_wakeup_threshold", 3), 3, 2, 20))
        cooldown_getter = getattr(self, "_effective_group_high_intensity_cooldown_seconds", None)
        cooldown = cooldown_getter() if callable(cooldown_getter) else max(30, _safe_int(_persona_value(self, "group_high_intensity_cooldown_seconds", 150), 150, 30, 1800))
        recent_wakeups = self._group_recent_wakeup_count(group, now=now, window_seconds=window)
        fatigue = self._group_wakeup_fatigue(group)
        until_ts = _safe_float(group.get("group_high_intensity_until"), 0.0, 0.0)
        reason = ""
        burst_floor = max(2, min(threshold, threshold - 1 if threshold > 2 else threshold))
        triggered = False
        if recent_wakeups >= threshold:
            triggered = True
            reason = "recent_wakeups"
        elif str(fatigue.get("level") or "") == "high" and recent_wakeups >= burst_floor:
            triggered = True
            reason = "fatigue_high_recent"
        if triggered and mutate:
            next_until = now + cooldown
            if next_until > until_ts:
                group["group_high_intensity_until"] = round(next_until, 3)
                until_ts = next_until
        active = bool(triggered or until_ts > now)
        if active and not reason:
            reason = "cooldown"
        merge_active = bool(active and recent_wakeups >= burst_floor)
        return {
            "active": active,
            "merge_active": merge_active,
            "reason": reason,
            "recent_wakeups": recent_wakeups,
            "threshold": threshold,
            "merge_recent_floor": burst_floor,
            "window_seconds": window,
            "cooldown_seconds": cooldown,
            "until_ts": round(until_ts, 3) if until_ts > 0 else 0.0,
            "remaining_seconds": round(max(0.0, until_ts - now), 1),
            "fatigue": dict(fatigue),
        }
