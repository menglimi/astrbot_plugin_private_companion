# -*- coding: utf-8 -*-
"""DailyStateProactivePart03Mixin。

由 tools/split_mixin_domain.py 从 daily_state_proactive.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 419 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateProactiveMixin）。
"""
from __future__ import annotations

from .daily_state_proactive_shared import _now_ts
from .daily_state_proactive_shared import Any
from .daily_state_proactive_shared import _normalize_photo_subject_owner
from .daily_state_proactive_shared import _path_text
from .daily_state_proactive_shared import _safe_float
from .daily_state_proactive_shared import _safe_int
from .daily_state_proactive_shared import _single_line
from .daily_state_proactive_shared import hashlib
from .daily_state_proactive_shared import random
from .daily_state_proactive_shared import re



class DailyStateProactivePart03Mixin:
    """DailyStateProactivePart03Mixin（从 DailyStateProactiveMixin 拆出）。"""


    def _store_or_advance_proactive_send_retry(
        self,
        user: dict[str, Any],
        *,
        text: str,
        image_path: str,
        extra_components: list[Any],
        reason: str,
        action: str,
        action_summary: str,
        error_text: str,
        photo_subject_owner: str = "",
        now: float | None = None,
    ) -> str:
        if not isinstance(user, dict):
            return "无法保存待重发内容"
        current = _now_ts() if now is None else float(now)
        delivery_snapshot_getter = getattr(self, "_ensure_planned_proactive_delivery_state", None)
        delivery_snapshot = delivery_snapshot_getter(user, now=current) if callable(delivery_snapshot_getter) else {}
        freshness = _single_line(delivery_snapshot.get("freshness"), 24) if isinstance(delivery_snapshot, dict) else ""
        delivery_key = _single_line(delivery_snapshot.get("key"), 80) if isinstance(delivery_snapshot, dict) else ""
        existing = user.get("pending_proactive_send_retry")
        previous_count = _safe_int(existing.get("retry_count"), 0, 0, 10) if isinstance(existing, dict) else 0
        retry_count = previous_count + 1
        retry_profile = _single_line(user.get("planned_proactive_route_retry_profile"), 32) or "normal"
        retry_limit = 4 if retry_profile == "until_expiry" else 2
        clean_error = _single_line(error_text, 180)
        error_hint = ""
        if clean_error:
            compact_error = clean_error.lower()
            if "retcode=1200" in compact_error and "eventchecker" in compact_error:
                error_hint = "QQ/NTQQ 拒绝发送（目标当前不可私聊或客户端临时异常）"
            elif "timeout" in compact_error:
                error_hint = "平台发送超时"
            elif "actionfailed" in compact_error or "failed" in compact_error:
                error_hint = "平台发送失败"
            else:
                error_hint = clean_error
        if retry_count > retry_limit:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，待重发内容连续失败，已放弃复用并重新排程",
                now=current,
                delay_hours=(12, 24),
            )
            return "发送失败，待重发内容连续失败，已放弃复用并重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        if (freshness != "durable" and retry_profile == "normal") or not delivery_key:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，当前候选依赖即时语境，已放弃复用并重新编排",
                now=current,
                delay_hours=(1.5, 4.0),
            )
            return "发送失败，当前候选依赖即时语境，已放弃复用并重新编排" + (f"；原因：{error_hint}" if error_hint else "")
        if extra_components:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，包含复杂组件，已放弃复用并重新排程",
                now=current,
                delay_hours=(6, 12),
            )
            return "发送失败，包含复杂组件，未缓存待重发内容，已延后重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        clean_text = _single_line(text, 1200)
        clean_image = _path_text(image_path, 1000)
        if not clean_text and not clean_image:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，无可复用内容，已放弃复用并重新排程",
                now=current,
                delay_hours=(6, 12),
            )
            return "发送失败，无可复用内容，已延后重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        validator = getattr(self, "_validate_proactive_outbound_candidate", None)
        unsafe_retry_text = False
        if callable(validator):
            try:
                validation = validator(
                    clean_text,
                    image_path=clean_image,
                    extra_components=extra_components,
                    reason=reason,
                    action=action,
                    source="retry_store",
                )
            except Exception:
                validation = {"decision": "send", "text": clean_text}
            decision = str(validation.get("decision") or "send")
            if decision == "drop":
                unsafe_retry_text = True
            elif decision == "rewrite":
                clean_text = _single_line(validation.get("text"), 1200)
        else:
            meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
            instruction_leak_checker = getattr(self, "_is_proactive_instruction_leak_text", None)
            try:
                unsafe_retry_text = bool(clean_text) and (
                    (callable(meta_leak_checker) and meta_leak_checker(clean_text))
                    or (callable(instruction_leak_checker) and instruction_leak_checker(clean_text))
                    or self._is_proactive_delivery_receipt_text(clean_text)
                )
            except Exception:
                unsafe_retry_text = False
        if unsafe_retry_text:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，候选正文疑似内部提示词/执行指令泄漏，已放弃复用并重新排程",
                now=current,
                delay_hours=(2, 6),
            )
            return "发送失败，候选正文疑似内部提示词/执行指令泄漏，已放弃复用并重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        if not clean_text and not clean_image:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，清理后无可复用内容，已放弃复用并重新排程",
                now=current,
                delay_hours=(6, 12),
            )
            return "发送失败，清理后无可复用内容，已延后重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        if retry_profile == "until_expiry":
            retry_delay_seconds = 3 * 60 if retry_count <= 1 else 8 * 60
        elif retry_profile == "short_lived":
            retry_delay_seconds = 2 * 60 if retry_count <= 1 else 5 * 60
        elif retry_profile == "while_anchor_live":
            retry_delay_seconds = 5 * 60 if retry_count <= 1 else 12 * 60
        else:
            retry_delay_seconds = 8 * 60 if retry_count <= 1 else 20 * 60
        planned_expire_at = _safe_float(delivery_snapshot.get("expire_at"), 0) if isinstance(delivery_snapshot, dict) else 0
        fresh_until_at = min(current + 72 * 3600, planned_expire_at) if planned_expire_at > current else current
        if fresh_until_at <= current + retry_delay_seconds:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，候选在下一次重试前会失效，已放弃复用并重新编排",
                now=current,
                delay_hours=(1.5, 4.0),
            )
            return "发送失败，候选在下一次重试前会失效，已重新编排" + (f"；原因：{error_hint}" if error_hint else "")
        user["pending_proactive_send_retry"] = {
            "active": True,
            "created_at": _safe_float(existing.get("created_at"), current) if isinstance(existing, dict) else current,
            "updated_at": current,
            "expires_at": current + 72 * 3600,
            "fresh_until_at": fresh_until_at,
            "retry_count": retry_count,
            "text": clean_text,
            "image_path": clean_image,
            "reason": _single_line(reason, 40) or "check_in",
            "action": _single_line(action, 40) or "message",
            "action_summary": _single_line(action_summary, 500),
            "photo_subject_owner": _normalize_photo_subject_owner(photo_subject_owner),
            "last_error": clean_error,
            "delivery_key": delivery_key,
            "freshness": freshness,
            "route_retry_profile": retry_profile,
            "route_cancel_if_new_inbound": bool(
                user.get("planned_proactive_route_cancel_if_new_inbound", True)
            ),
            "private_activity_at": self._latest_private_user_activity_ts(user),
            "private_inbound_count": _safe_int(user.get("private_inbound_count"), 0),
        }
        user["next_proactive_at"] = current + retry_delay_seconds
        user["planned_proactive_window_start_at"] = user["next_proactive_at"]
        user["planned_proactive_delivery_state"] = "retrying"
        return f"发送失败，已保留待重发内容，约 {max(1, int(retry_delay_seconds // 60))} 分钟后第 {retry_count} 次重试" + (f"；原因：{error_hint}" if error_hint else "")

    def _activity_share_global_signature(self, user: dict[str, Any], *, text: str = "", action_summary: str = "") -> str:
        state = self.data.get("daily_state", {})
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        parts: list[Any] = [
            user.get("planned_proactive_topic"),
            user.get("planned_proactive_motive"),
            action_summary,
        ]
        if isinstance(current_item, dict):
            parts.extend(
                [
                    current_item.get("time"),
                    current_item.get("activity"),
                    current_item.get("message_seed"),
                ]
            )
        if isinstance(state, dict):
            parts.extend(
                [
                    state.get("activity"),
                    state.get("current_activity"),
                    state.get("message_seed"),
                    state.get("mood_bias"),
                ]
            )
        parts.append(text)
        signature = self._proactive_topic_signature(*parts)
        if signature:
            return signature
        raw = " ".join(_single_line(part, 120) for part in parts if _single_line(part, 120))
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16] if raw else ""

    def _cleanup_global_activity_share_topics(self, *, now: float | None = None) -> list[dict[str, Any]]:
        check_now = now or _now_ts()
        runtime = self.data.setdefault("proactive_runtime", {})
        if not isinstance(runtime, dict):
            runtime = {}
            self.data["proactive_runtime"] = runtime
        raw = runtime.get("recent_activity_shares")
        if not isinstance(raw, list):
            raw = []
        kept = [
            item for item in raw
            if isinstance(item, dict) and check_now - _safe_float(item.get("ts"), 0) <= 90 * 60
        ]
        runtime["recent_activity_shares"] = kept[-12:]
        return runtime["recent_activity_shares"]

    def _activity_share_recently_sent_elsewhere(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        text: str = "",
        action_summary: str = "",
        now: float | None = None,
    ) -> str:
        signature = self._activity_share_global_signature(user, text=text, action_summary=action_summary)
        if not signature:
            return ""
        for item in self._cleanup_global_activity_share_topics(now=now):
            if str(item.get("user_id") or "") == str(user_id):
                continue
            if self._topic_signature_similar(signature, str(item.get("signature") or "")):
                return _single_line(item.get("text"), 80) or "同一日常碎片刚刚已分享给其他私聊对象"
        return ""

    def _remember_global_activity_share(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        text: str = "",
        action_summary: str = "",
    ) -> None:
        signature = self._activity_share_global_signature(user, text=text, action_summary=action_summary)
        if not signature:
            return
        recent = self._cleanup_global_activity_share_topics()
        recent.append(
            {
                "ts": _now_ts(),
                "user_id": str(user_id),
                "signature": signature,
                "text": _single_line(text or user.get("planned_proactive_topic") or user.get("planned_proactive_motive"), 120),
            }
        )
        del recent[:-12]

    def _activity_share_duplicate_block_remaining(self, user: dict[str, Any], *, now: float | None = None) -> float:
        check_now = now or _now_ts()
        until = _safe_float(user.get("activity_share_duplicate_block_until"), 0)
        return max(0.0, until - check_now)

    def _block_duplicate_activity_share_for_user(
        self,
        user: dict[str, Any],
        *,
        duplicate_note: str = "",
        now: float | None = None,
        seconds: float = 90 * 60,
    ) -> None:
        check_now = now or _now_ts()
        user["activity_share_duplicate_block_until"] = check_now + max(60.0, float(seconds or 0))
        user["activity_share_duplicate_block_note"] = _single_line(duplicate_note, 120)
        user["last_activity_share_duplicate_block_at"] = check_now

    def _format_recent_proactive_topics_hint(self, user: dict[str, Any]) -> str:
        recent = self._cleanup_recent_proactive_topics(user)
        if not recent:
            return ""
        lines: list[str] = []
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        for item in recent[-4:]:
            text = _single_line(item.get("text"), 80)
            if not text:
                continue
            if callable(meta_leak_checker) and meta_leak_checker(text):
                continue
            when = self._format_timestamp_elapsed(item.get("ts"))
            lines.append(f"- {when}说过：{text}")
        if any(str(item.get("signature") or "") == "ordinary_weather_topic" for item in recent):
            lines.append("- 最近已经用天气开过话题；除非本轮原因是刚发生的环境突变或官方预警，否则这次不要再写天气、气温、下雨、天色，也不要追问对方那边的天气。")
        return "\n".join(lines)

    def _generate_weather_linked_proactive_events(self) -> list[dict[str, Any]]:
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        if weather == "暂无天气信息":
            return []
        events: list[dict[str, Any]] = []
        if any(token in weather for token in ("雨", "阵雨", "雷", "小雨", "中雨", "大雨")) and random.random() < 0.24:
            events.append(
                {
                    "source": "weather_context",
                    "weather_linked": True,
                    "window": self._pick_weather_window("rain"),
                    "reason": "activity_share",
                    "action": "message",
                    "why": f"外面在下雨，想短短提一句。{weather}",
                    "topic": "外面下雨了",
                    "motive": "听见外面下雨，想短短提一声",
                    "mood": "安静",
                }
            )
        if any(token in weather for token in ("晴", "阳光", "多云", "晚霞")) and random.random() < 0.12:
            events.append(
                {
                    "source": "weather_context",
                    "weather_linked": True,
                    "window": self._pick_weather_window("clear"),
                    "reason": "activity_share",
                    "action": "message",
                    "why": f"外面的天色有点好看，想短短提一句。{weather}",
                    "topic": "天色有点好看",
                    "motive": "外面天色不错",
                    "mood": "松弛",
                }
            )
        return events[:1]

    @staticmethod
    def _daily_proactive_archive_context_text(text: str) -> bool:
        if not text:
            return False
        raw = str(text)
        compact = re.sub(r"\s+", "", raw).lower()
        lowered = raw.lower()
        if "主动承接占位" in raw and ("用户还没发来新消息" in raw or "bot主动" in compact):
            return True
        if "这不是用户消息" in raw and "private companion" in lowered and "主动消息" in raw:
            return True
        if "[主动消息]" in raw or "【主动消息】" in raw:
            legacy_markers = ("触发原因", "行为结果", "内部动机", "动作摘要")
            if sum(1 for marker in legacy_markers if marker in raw) >= 2:
                return True
        return False

    def _sanitize_proactive_social_fact_fields_inplace(self, item: dict[str, Any], *, field: str) -> bool:
        if not isinstance(item, dict):
            return False
        changed = False
        for key in ("topic", "motive", "why", "scene", "impulse"):
            original = _single_line(item.get(key), 180)
            if not original:
                continue
            cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"{field}.{key}")
            if cleaned != original:
                item[key] = cleaned
                changed = True
        if changed and "signature" in item:
            item["signature"] = self._proactive_topic_signature(
                item.get("reason"),
                item.get("source"),
                item.get("topic"),
                item.get("motive"),
            )
        return changed

    def _sanitize_user_proactive_social_facts_inplace(self, user: dict[str, Any], *, field: str) -> bool:
        if not isinstance(user, dict):
            return False
        changed = False
        for source_key in ("planned_proactive_topic", "planned_proactive_motive"):
            original = _single_line(user.get(source_key), 180)
            if not original:
                continue
            cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"{field}.{source_key}")
            if cleaned != original:
                user[source_key] = cleaned
                changed = True
        if changed:
            user["planned_proactive_model_judge_signature"] = ""
            user["planned_proactive_model_judge_result"] = {}
        impulses = user.get("proactive_impulses")
        if isinstance(impulses, list):
            for index, item in enumerate(impulses):
                if self._sanitize_proactive_social_fact_fields_inplace(
                    item,
                    field=f"{field}.proactive_impulses.{index}",
                ):
                    changed = True
        recent_topics = user.get("recent_proactive_topics")
        if isinstance(recent_topics, list):
            kept_topics: list[Any] = []
            topics_changed = False
            meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
            for index, topic in enumerate(recent_topics):
                if isinstance(topic, dict):
                    if callable(meta_leak_checker) and (
                        meta_leak_checker(str(topic.get("text") or ""))
                        or meta_leak_checker(str(topic.get("signature") or ""))
                    ):
                        topics_changed = True
                        continue
                    item_changed = False
                    cleaned_topic = dict(topic)
                    for key in ("text", "topic", "motive"):
                        original_value = _single_line(cleaned_topic.get(key), 180)
                        if not original_value:
                            continue
                        cleaned_value = self._sanitize_daily_plan_social_fact_text(
                            original_value,
                            field=f"{field}.recent_proactive_topics.{index}.{key}",
                        )
                        if cleaned_value != original_value:
                            cleaned_topic[key] = cleaned_value
                            item_changed = True
                    if item_changed:
                        topics_changed = True
                    kept_topics.append(cleaned_topic)
                    continue
                original = _single_line(topic, 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(
                    original,
                    field=f"{field}.recent_proactive_topics.{index}",
                )
                if cleaned != original:
                    topics_changed = True
                if cleaned and cleaned != "放慢节奏处理手边的小事，把这段时间过得轻一点":
                    kept_topics.append(cleaned)
            if topics_changed:
                user["recent_proactive_topics"] = kept_topics[-20:]
                changed = True
        return changed
