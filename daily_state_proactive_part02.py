# -*- coding: utf-8 -*-
"""DailyStateProactivePart02Mixin。

由 tools/split_mixin_domain.py 从 daily_state_proactive.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 392 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateProactiveMixin）。
"""
from __future__ import annotations

from .daily_state_proactive_shared import _now_ts, logger
from .daily_state_proactive_shared import Any
from .daily_state_proactive_shared import Path
from .daily_state_proactive_shared import _safe_float
from .daily_state_proactive_shared import _safe_int
from .daily_state_proactive_shared import _single_line
from .daily_state_proactive_shared import _strip_internal_message_blocks
from .daily_state_proactive_shared import re
from .daily_state_proactive_shared import runtime_persona_setting



class DailyStateProactivePart02Mixin:
    """DailyStateProactivePart02Mixin（从 DailyStateProactiveMixin 拆出）。"""


    def _normalize_event_motive(self, item: dict[str, Any]) -> str:
        direct = _single_line(item.get("motive"), 80)
        if direct:
            return self._normalize_internal_motive_text(direct)
        reason = _single_line(item.get("reason"), 40)
        action = _single_line(item.get("action"), 20)
        topic = _single_line(item.get("topic"), 50)
        why = _single_line(item.get("why"), 80)
        scene = _single_line(item.get("scene"), 60)
        tone = _single_line(item.get("tone"), 24)
        impulse = _single_line(item.get("impulse"), 80)
        if impulse:
            return self._normalize_internal_motive_text(impulse)
        base = {
            "insomnia_night": "夜里还没睡着，想短短留一句",
            "state_share": "当前状态有变化,想让你知道",
            "quiet_care": "想到用户，想确认一下用户那边怎么样",
            "activity_share": "遇到一段可以分享的日常内容",
            "diary_share": "整理今日记录时想到可以分享",
            "important_date_share": "有个重要时间点值得提前提醒",
            "background_schedule": "当前日程有一点可以自然提到",
            "check_in": "刚好停下来,想看看那边有没有空",
            "morning_greeting": "早上这会儿想先把一句招呼放过去",
            "noon_greeting": "中午松下来时想短短说一句",
            "evening_greeting": "晚上慢下来时想先来你这边说一句",
        }.get(reason, "刚好停下来,想到可以短短说一句")
        if action == "screen_peek":
            base = "刚好有点空，想看看那边是不是还在忙"
        elif action == "photo_text":
            base = "刚刚看到的画面想分享一下"
        elif action == "poke":
            base = "想做一次轻量提醒"
        elif action == "voice":
            base = "这会儿更适合用语音表达"
        if topic and any(token in topic for token in ("日记", "笔记", "碎片", "念头", "半句", "想法")):
            base = "整理记录时发现一段适合分享的内容"
        elif topic and any(token in topic for token in self._visual_share_tokens()):
            base = "眼前有个具体小画面适合顺手分享"
        elif topic and any(token in topic for token in ("雨", "天气", "晚霞", "阳光")):
            base = "当前天气内容适合分享"
        elif why and len(why) <= 30:
            base = why
        if scene and tone:
            base = f"{scene}里有个可以自然提到的小切口"
        elif scene:
            base = f"{scene}里有个可以自然提到的小切口"
        elif tone and not topic:
            base = "这会儿适合短短说一句,状态只留在语气里"
        return self._normalize_internal_motive_text(_single_line(base, 80))

    def _dedupe_proactive_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in events:
            if not isinstance(item, dict):
                continue
            key = "|".join(
                [
                    _single_line(item.get("window"), 20),
                    _single_line(item.get("reason"), 40),
                    _single_line(item.get("action"), 20),
                    _single_line(item.get("topic"), 80),
                ]
            )
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _proactive_topic_signature(self, *parts: Any) -> str:
        normalized_parts: list[str] = []
        address_prefix = re.compile(
            r"^(?:[嗯唔哦噢诶欸啊呀哎嘿嗨]+[。！？!?…~～，,\s]*)?"
            r"(?:[\w\u4e00-\u9fffぁ-んァ-ヶー]{1,10}(?:大人|老师|主人|哥哥|姐姐|同学|宝宝|宝贝)"
            r"[，,、：:\s~～…]*|(?!(?:今天|现在|刚才|刚刚|这会儿|早上|中午|晚上|外面|天气|最近|等下|待会)[，,、：:])"
            r"[\w\u4e00-\u9fffぁ-んァ-ヶー]{1,3}[，,、：:]\s*)"
        )
        for part in parts:
            value = _single_line(part, 160)
            if not value:
                continue
            # 收件人称呼不是主题。先去掉句首称呼，避免不同内容仅因反复称呼
            # 同一用户而被误判为重复。
            normalized_parts.append(address_prefix.sub("", value, count=1).strip() or value)
        text = " ".join(normalized_parts)
        if not text:
            return ""
        school_stress_markers = (
            "上课", "课", "物理", "老师", "点名", "叫上去", "做题", "抓到",
            "发呆", "心跳", "紧张", "差点", "讲台",
        )
        if sum(1 for token in school_stress_markers if token in text) >= 2:
            return "school_class_anxiety"
        food_markers = ("食堂", "午饭", "中午", "菜", "咸", "吃")
        if sum(1 for token in food_markers if token in text) >= 2:
            return "noon_food_share"
        weather_markers = (
            "外面下雨", "外面下雪", "天气", "天晴", "晴吗", "晴天", "下雨", "没下雨",
            "雨声", "雨停", "雨雪停", "小雨", "中雨", "大雨", "阵雨", "雷雨", "雷暴", "降雨",
            "阴天", "天阴", "阴阴", "多云", "放晴", "太阳", "阳光", "晚霞", "天色", "气温",
            "降温", "升温", "起风", "风声", "下雪", "雪天", "雾霾",
        )
        if any(token in text for token in weather_markers):
            # 普通天气换一种说法仍是同一个主动话题。结构化预警和实时
            # 环境变化在候选层按事件指纹去重，不依赖这里放行。
            return "ordinary_weather_topic"
        image_markers = ("图", "图片", "照片", "拍", "自拍", "画面")
        if sum(1 for token in image_markers if token in text) >= 2:
            return "photo_share"
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", text)
        stopwords = {
            "刚才", "现在", "今天", "这个", "那个", "一下", "一点", "有点", "还是",
            "没有", "已经", "时候", "用户", "对方", "主动", "消息", "这会儿",
            "内容", "第一", "时间", "看到", "喜欢", "希望", "继续", "话题", "换个",
            "说过", "讲过", "提过", "聊过", "发过", "前面", "之前", "刚刚",
        }
        kept: list[str] = []
        def add_anchor(value: str) -> None:
            anchor = str(value or "").strip()
            if len(anchor) < 2 or anchor in stopwords:
                return
            if re.fullmatch(r"[了啦呀呢嘛吗吧啊哦噢诶嗯]+", anchor):
                return
            if re.fullmatch(r"[年月日点分秒上下左右前后早晚中午今晚昨今明]+", anchor):
                return
            if anchor not in kept:
                kept.append(anchor)

        for token in tokens:
            if re.fullmatch(r"[A-Za-z0-9_]{3,}", token):
                add_anchor(token.lower())
                continue
            cleaned = re.sub(r"(的时候|时候|一下|一点|了|啦|呀|呢|嘛|吗|吧|啊|哦|噢|诶|嗯)$", "", token)
            add_anchor(cleaned)
            if len(cleaned) >= 3:
                for size in (2, 3):
                    for index in range(0, max(0, len(cleaned) - size + 1)):
                        add_anchor(cleaned[index : index + size])
        return "|".join(kept)

    def _cleanup_recent_proactive_topics(self, user: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
        now = now or _now_ts()
        raw = user.get("recent_proactive_topics", [])
        if not isinstance(raw, list):
            raw = []
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        kept: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            signature = str(item.get("signature") or "")
            visible_text = _single_line(item.get("text"), 240)
            derived_signature = self._proactive_topic_signature(visible_text) if visible_text else ""
            if signature == "morning_weather_check" or derived_signature == "ordinary_weather_topic":
                signature = "ordinary_weather_topic"
                item["signature"] = signature
            if signature == "ordinary_weather_topic":
                configured_minutes = self._proactive_dedup_window_minutes("weather", 1080)
                retention = 30 * 24 * 3600 if configured_minutes <= 0 else max(18 * 3600, configured_minutes * 60)
            else:
                configured_minutes = self._proactive_dedup_window_minutes("sent", 240)
                retention = 30 * 24 * 3600 if configured_minutes <= 0 else max(6 * 3600, configured_minutes * 60)
            if now - _safe_float(item.get("ts"), 0) > retention:
                continue
            if callable(meta_leak_checker) and (
                meta_leak_checker(str(item.get("text") or ""))
                or meta_leak_checker(str(item.get("signature") or ""))
            ):
                continue
            kept.append(item)
        user["recent_proactive_topics"] = kept[-12:]
        return user["recent_proactive_topics"]

    def _proactive_dedup_window_minutes(self, kind: str, default: int) -> int:
        key = (
            "proactive_dedup_weather_window_minutes"
            if kind == "weather"
            else "proactive_dedup_last_message_window_minutes"
            if kind == "last_message"
            else "proactive_dedup_sent_window_minutes"
        )
        raw = runtime_persona_setting(self, key, default)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return max(0, int(default))

    @staticmethod
    def _proactive_dedup_age_allowed(age: float, window_minutes: int) -> bool:
        return window_minutes <= 0 or age <= window_minutes * 60

    def _topic_signature_similar(self, left: str, right: str, *, use_proactive_dedup_config: bool = False) -> bool:
        if not left or not right:
            return False
        if left == right:
            return True
        left_set = {part for part in left.split("|") if part}
        right_set = {part for part in right.split("|") if part}
        if not left_set or not right_set:
            return False
        common = left_set & right_set
        smaller_size = min(len(left_set), len(right_set))
        min_shared_tokens = 1
        overlap_floor = 0.0
        if use_proactive_dedup_config:
            try:
                min_shared_tokens = max(1, min(4, int(runtime_persona_setting(self, "proactive_dedup_min_shared_tokens", 1))))
            except (TypeError, ValueError):
                min_shared_tokens = 1
            try:
                overlap_floor = max(0.0, min(1.0, float(runtime_persona_setting(self, "proactive_dedup_min_overlap_ratio", 0.0))))
            except (TypeError, ValueError):
                overlap_floor = 0.0
        if smaller_size <= 2:
            return len(common) >= min_shared_tokens
        overlap = len(common) / smaller_size
        return bool(
            (len(common) >= 2 and overlap >= max(0.5, overlap_floor))
            or (len(common) >= 3 and overlap >= max(0.3, overlap_floor))
            or (len(common) >= 4 and overlap >= max(0.18, overlap_floor))
            or (len(common) >= 6 and overlap >= overlap_floor)
        )

    def _recent_proactive_topic_repeated(self, user: dict[str, Any], signature: str, *, now: float | None = None) -> bool:
        if not signature:
            return False
        check_now = now or _now_ts()
        for item in self._cleanup_recent_proactive_topics(user, now=check_now):
            item_signature = str(item.get("signature") or "")
            is_weather = signature == "ordinary_weather_topic" or item_signature == "ordinary_weather_topic"
            window_minutes = self._proactive_dedup_window_minutes(
                "weather" if is_weather else "sent",
                1080 if is_weather else 240,
            )
            if not self._proactive_dedup_age_allowed(check_now - _safe_float(item.get("ts"), 0), window_minutes):
                continue
            if self._topic_signature_similar(signature, item_signature, use_proactive_dedup_config=True):
                return True
        return False

    def _remember_proactive_topic(self, user: dict[str, Any], *, text: str = "", topic: str = "", motive: str = "") -> None:
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        if callable(meta_leak_checker) and (
            meta_leak_checker(text) or meta_leak_checker(topic) or meta_leak_checker(motive)
        ):
            logger.warning("跳过记录疑似工具循环摘要的主动话题记忆")
            return
        signature = self._proactive_topic_signature(text, topic, motive)
        if not signature:
            return
        recent = self._cleanup_recent_proactive_topics(user)
        recent.append(
            {
                "ts": _now_ts(),
                "signature": signature,
                "text": _single_line(text or topic or motive, 120),
            }
        )
        del recent[:-12]

    def _proactive_dedup_enabled_policies(self) -> frozenset[str]:
        raw_value = runtime_persona_setting(self, "proactive_dedup_policies", None)
        if raw_value is None:
            return frozenset({"semantic", "content_fingerprint", "life_event"})
        raw = str(raw_value).strip().lower()
        return frozenset(part for part in re.split(r"[,，;；\s]+", raw) if part)

    def _recent_proactive_text_duplicate_reason(
        self,
        user: dict[str, Any],
        *,
        text: str = "",
        topic: str = "",
        motive: str = "",
        now: float | None = None,
    ) -> str:
        if not bool(runtime_persona_setting(self, "proactive_dedup_enabled", True)):
            return ""
        signature = self._proactive_topic_signature(text, topic, motive)
        if not signature:
            return ""
        check_now = now or _now_ts()
        for item in self._cleanup_recent_proactive_topics(user, now=check_now):
            old_signature = str(item.get("signature") or "")
            if not self._topic_signature_similar(signature, old_signature, use_proactive_dedup_config=True):
                continue
            age = check_now - _safe_float(item.get("ts"), 0)
            duplicate_window = self._proactive_dedup_window_minutes(
                "weather" if signature == "ordinary_weather_topic" else "sent",
                1080 if signature == "ordinary_weather_topic" else 240,
            )
            if not self._proactive_dedup_age_allowed(age, duplicate_window):
                continue
            old_text = _single_line(item.get("text"), 80)
            if signature == "ordinary_weather_topic":
                return f"近期已经主动聊过天气" + (f"：{old_text}" if old_text else "")
            return f"近 {max(1, int(age // 60))} 分钟已发送相似主动" + (f"：{old_text}" if old_text else "")
        last_message = _single_line(_strip_internal_message_blocks(user.get("last_companion_message"), enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 500)
        # last_reply_at is inbound user activity, so it must never make an old
        # companion message look newly delivered.
        last_at = _safe_float(user.get("last_companion_message_at"), 0)
        sending_started_at = _safe_float(user.get("proactive_sending_started_at"), 0)
        unconfirmed_current_candidate = bool(
            sending_started_at > 0
            and last_at >= sending_started_at
            and user.get("proactive_sending")
        )
        if (
            bool(runtime_persona_setting(self, "proactive_dedup_last_message_enabled", True))
            and not unconfirmed_current_candidate
            and last_message
            and last_at > 0
            and self._proactive_dedup_age_allowed(
                check_now - last_at,
                self._proactive_dedup_window_minutes("last_message", 240),
            )
        ):
            last_signature = self._proactive_topic_signature(last_message)
            if self._topic_signature_similar(signature, last_signature, use_proactive_dedup_config=True):
                age = check_now - last_at
                return f"近 {max(1, int(age // 60))} 分钟聊天里已经说过相似内容：{_single_line(last_message, 80)}"
        return ""

    def _pending_proactive_send_retry(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
        payload = user.get("pending_proactive_send_retry") if isinstance(user, dict) else None
        if not isinstance(payload, dict) or not payload.get("active"):
            return None
        current = _now_ts() if now is None else float(now)
        if _safe_float(payload.get("expires_at"), 0) <= current:
            self._clear_pending_proactive_send_retry(user)
            return None
        delivery_key_getter = getattr(self, "_planned_proactive_delivery_key", None)
        current_delivery_key = delivery_key_getter(user) if callable(delivery_key_getter) else ""
        retry_delivery_key = _single_line(payload.get("delivery_key"), 80)
        retry_freshness = _single_line(payload.get("freshness"), 24)
        retry_profile = _single_line(payload.get("route_retry_profile"), 32) or "normal"
        cancel_if_new_inbound = bool(payload.get("route_cancel_if_new_inbound", True))
        retry_fresh_until = _safe_float(payload.get("fresh_until_at"), 0)
        retry_activity_at = _safe_float(payload.get("private_activity_at"), 0)
        retry_inbound_count = _safe_int(payload.get("private_inbound_count"), 0)
        current_activity_at = self._latest_private_user_activity_ts(user)
        current_inbound_count = _safe_int(user.get("private_inbound_count"), 0)
        if (
            not retry_delivery_key
            or retry_delivery_key != current_delivery_key
            or (retry_profile == "normal" and retry_freshness != "durable")
            or retry_fresh_until <= current
            or (
                cancel_if_new_inbound
                and (current_activity_at > retry_activity_at or current_inbound_count > retry_inbound_count)
            )
        ):
            self._clear_pending_proactive_send_retry(user)
            return None
        image_path = str(payload.get("image_path") or "").strip()
        text = _single_line(payload.get("text"), 1200)
        validator = getattr(self, "_validate_proactive_outbound_candidate", None)
        if callable(validator):
            try:
                validation = validator(
                    text,
                    image_path=image_path,
                    reason=_single_line(payload.get("reason"), 40),
                    action=_single_line(payload.get("action"), 40),
                    source="retry_load",
                )
            except Exception:
                validation = {"decision": "send", "text": text}
            decision = str(validation.get("decision") or "send")
            if decision == "drop":
                self._clear_pending_proactive_send_retry(user)
                return None
            if decision == "rewrite":
                text = _single_line(validation.get("text"), 1200)
                payload["text"] = text
        if image_path and not re.match(r"^(?:https?://|file://|data:)", image_path, flags=re.I):
            try:
                if not Path(image_path).exists():
                    self._clear_pending_proactive_send_retry(user)
                    return None
            except Exception:
                self._clear_pending_proactive_send_retry(user)
                return None
        if not text and not image_path:
            self._clear_pending_proactive_send_retry(user)
            return None
        return payload

    def _clear_pending_proactive_send_retry(self, user: dict[str, Any]) -> None:
        if isinstance(user, dict):
            user["pending_proactive_send_retry"] = {}

    def _abandon_failed_proactive_retry_candidate(
        self,
        user: dict[str, Any],
        *,
        note: str,
        now: float,
        delay_hours: tuple[float, float],
    ) -> None:
        self._clear_pending_proactive_send_retry(user)
        self._mark_planned_candidate_status(user, "dropped", note)
        self._clear_pending_proactive_plan(user)
        self._schedule_next_proactive(user, now=now, delay_hours=delay_hours)
