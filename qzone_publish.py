# -*- coding: utf-8 -*-
"""QQ Zone post composition, validation, history, and image preparation."""
from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .helpers import _now_ts, _path_text, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting

__all__ = ("QzonePublishMixin",)

class QzonePublishMixin:
    """Public post composition, sanitization, records, and image preparation."""
    def _qzone_public_state_hint(self, state: dict[str, Any]) -> str:
        """Return a public-safe mood hint for Qzone posts without internal state fields."""
        if not isinstance(state, dict):
            return "心情平稳,适合写一小段生活感。"
        mood = _single_line(state.get("mood_bias"), 24) or "平稳"
        weather = _single_line(state.get("weather"), 80)
        sleep = _single_line(state.get("sleep"), 40)
        hints: list[str] = []
        if mood:
            hints.append(f"心情底色偏{mood}")
        if weather and weather != "暂无天气信息":
            hints.append(f"天气余味：{weather}")
        if sleep and sleep not in {"睡眠平稳", "正常"}:
            hints.append(f"节奏偏{sleep}")
        if not hints:
            hints.append("生活节奏平稳")
        hints.append("只能写成自然感受,不要写状态标签、数值或内部变量。")
        return "；".join(hints)

    @staticmethod
    def _qzone_temporal_context() -> str:
        now = time.localtime()
        weekday_names = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
        hour = now.tm_hour
        if 0 <= hour < 6:
            period = "凌晨"
        elif hour < 9:
            period = "早晨"
        elif hour < 12:
            period = "上午"
        elif hour < 16:
            period = "下午"
        elif hour < 19:
            period = "傍晚"
        elif hour < 22:
            period = "晚上"
        else:
            period = "深夜"
        if now.tm_mon in (12, 1, 2):
            season = "冬天"
        elif now.tm_mon in (3, 4, 5):
            season = "春天"
        elif now.tm_mon in (6, 7, 8, 9):
            season = "夏天"
        else:
            season = "秋天"
        weekday = weekday_names[min(6, max(0, now.tm_wday))]
        day_type = "周末" if now.tm_wday >= 5 else "工作日"
        return f"{time.strftime('%Y年%m月%d日 %H:%M', now)}，{weekday}，{day_type}，{season}，{period}。"

    @staticmethod
    def _qzone_publish_theme_hint() -> str:
        themes = (
            "记录当前时段里一件具体的小事",
            "写一个自然冒出来的心情余味",
            "轻轻吐槽一个生活里的小麻烦",
            "记录眼前看到、听到或碰到的具体画面",
            "写一段短短的碎碎念，不要总结成道理",
            "记录一个让人稍微开心或安心的小瞬间",
            "写写天气、光线、食物、衣物、路上或桌边的生活细节",
            "从当前日程里挑一个最不像任务汇报的切面",
        )
        return random.choice(themes)

    def _qzone_relationship_safe_source(self, value: Any, *, source: str) -> str:
        sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)
        if callable(sanitizer):
            try:
                return sanitizer(value, source=source)
            except Exception:
                pass
        return str(value or "").strip()

    def _qzone_relationship_authority_guard(self) -> str:
        formatter = getattr(self, "_format_generation_relationship_authority_guard", None)
        if callable(formatter):
            try:
                return str(formatter() or "").strip()
            except Exception:
                pass
        return ""

    def _qzone_recent_publish_context(self, state: dict[str, Any], *, limit: int = 5) -> str:
        items = state.get("recent_life_publish_texts") if isinstance(state, dict) else []
        if not isinstance(items, list):
            return ""
        lines: list[str] = []
        for item in items[-max(1, int(limit or 5)) :]:
            text = _single_line(
                self._qzone_relationship_safe_source(
                    item.get("text") if isinstance(item, dict) else item,
                    source="qzone.recent_publish",
                ),
                120,
            )
            if text:
                lines.append(f"- {text}")
        if not lines:
            return ""
        return "最近已发说说：\n" + "\n".join(lines) + "\n本次请换一个场景、情绪或观察角度，不要重复同一类表达。"

    def _qzone_recent_self_publish_chat_context(self, *, limit: int = 3) -> str:
        """Expose recent successful Bot posts to Qzone-related chat turns."""
        state = self.data.get("qzone_integration") if isinstance(getattr(self, "data", None), dict) else {}
        items = state.get("recent_life_publish_texts") if isinstance(state, dict) else []
        if not isinstance(items, list):
            return ""
        records: list[str] = []
        labels = ("最新一条", "上一条", "更早一条")
        for item in reversed(items):
            if len(records) >= max(1, min(3, int(limit or 3))):
                break
            text = _single_line(
                self._qzone_relationship_safe_source(
                    item.get("text") if isinstance(item, dict) else item,
                    source="qzone.recent_self_publish_chat",
                ),
                180,
            )
            if not text:
                continue
            image_count = _safe_int(item.get("image_count"), 0, 0, 99) if isinstance(item, dict) else 0
            image_note = f"；配图 {image_count} 张" if image_count else ""
            label = labels[len(records)] if len(records) < len(labels) else f"较早第 {len(records) + 1} 条"
            records.append(f"- {label}：{text}{image_note}")
        if not records:
            return ""
        return (
            "【Bot 自己最近成功发布的 QQ 空间记录】\n"
            + "\n".join(records)
            + "\n这些正文是 Bot 自己发出的公开动态，不是当前用户发的内容。"
        )

    def _qzone_note_recent_publish(
        self,
        state: dict[str, Any],
        text: Any,
        *,
        reason: str,
        now: float | None = None,
        tid: str = "",
        image_count: int = 0,
        verified: bool | None = None,
        source: str = "",
    ) -> None:
        if not isinstance(state, dict):
            return
        clean = _single_line(text, 180)
        if not clean:
            return
        current = _now_ts() if now is None else float(now)
        items = state.get("recent_life_publish_texts")
        if not isinstance(items, list):
            items = []
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            raw = item.get("text") if isinstance(item, dict) else item
            item_text = _single_line(raw, 180)
            key = re.sub(r"\s+", "", item_text)
            if not item_text or key in seen or key == re.sub(r"\s+", "", clean):
                continue
            seen.add(key)
            if isinstance(item, dict):
                deduped.append(dict(item))
            else:
                deduped.append({"text": item_text, "at": 0, "reason": ""})
        entry = {
            "text": clean,
            "at": current,
            "reason": _single_line(reason, 40),
            "tid": _single_line(tid, 80),
            "image_count": _safe_int(image_count, 0, 0, 99),
            "source": _single_line(source, 40),
        }
        if verified is not None:
            entry["verified"] = bool(verified)
        deduped.append(entry)
        state["recent_life_publish_texts"] = deduped[-8:]

    async def _qzone_record_published_post(
        self,
        text: Any,
        *,
        reason: str = "manual_publish",
        tid: str = "",
        image_count: int = 0,
        verified: bool | None = None,
        event: AstrMessageEvent | None = None,
    ) -> None:
        state = self._qzone_state_dict()
        now = _now_ts()
        clean = _single_line(text, 300)
        if not clean:
            return
        self._qzone_note_recent_publish(
            state,
            clean,
            reason=reason,
            now=now,
            tid=tid,
            image_count=image_count,
            verified=verified,
            source="publish_success",
        )
        state["last_publish_recorded_at"] = now
        state["last_publish_recorded_text"] = _single_line(clean, 180)
        state["last_publish_recorded_reason"] = _single_line(reason, 40)
        state["last_publish_recorded_tid"] = _single_line(tid, 80)
        state["last_publish_recorded_images"] = _safe_int(image_count, 0, 0, 99)
        recorder = getattr(self, "_memory_companion_record_qzone_publish", None)
        if callable(recorder):
            await recorder(
                text=clean,
                reason=reason,
                tid=tid,
                image_count=image_count,
                verified=verified,
                event=event,
            )
        self._qzone_append_publish_to_current_detail(
            clean,
            reason=reason,
            tid=tid,
            image_count=image_count,
            verified=verified,
        )
        invalidator = getattr(self, "_invalidate_detail_after_interaction", None)
        if callable(invalidator):
            try:
                invalidator(now=now)
            except Exception:
                pass
        try:
            self._save_data_sync(sections={"qzone_integration"})
        except Exception as exc:
            logger.debug("[PrivateCompanion] QQ 空间发布记录保存失败: %s", _single_line(exc, 120))

    def _qzone_append_publish_to_current_detail(
        self,
        text: Any,
        *,
        reason: str = "",
        tid: str = "",
        image_count: int = 0,
        verified: bool | None = None,
    ) -> bool:
        segment_getter = getattr(self, "_current_detail_segment_for_update", None)
        if not callable(segment_getter):
            return False
        try:
            segment = segment_getter()
        except Exception:
            return False
        if not isinstance(segment, dict):
            return False
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return False
        key = str(segment.get("key") or "")
        snapshot = enhanced.get(key)
        if not isinstance(snapshot, dict):
            return False
        clean = _single_line(text, 180)
        if not clean:
            return False
        safe_image_count = _safe_int(image_count, 0, 0, 99)
        image_part = f"；配图 {safe_image_count} 张" if safe_image_count > 0 else ""
        verify_part = "；已反查确认" if verified else ""
        event_text = _single_line(f"刚发布了一条 QQ 空间说说：{clean}{image_part}{verify_part}。", 220)
        events = snapshot.setdefault("today_events", [])
        if not isinstance(events, list):
            events = []
            snapshot["today_events"] = events
        tid_text = _single_line(tid, 80)
        for item in events:
            if not isinstance(item, dict):
                continue
            if tid_text and _single_line(item.get("tid"), 80) == tid_text:
                return False
            if clean and clean in _single_line(item.get("event") or item.get("text"), 260):
                return False
        try:
            at = self._environment_now().strftime("%H:%M")
        except Exception:
            at = ""
        events.append(
            {
                "window": at,
                "event": event_text,
                "mood": "公开动态已发布",
                "source": "qzone_publish",
                "reason": _single_line(reason, 40),
                "tid": tid_text,
            }
        )
        del events[:-8]
        summary = _single_line(snapshot.get("summary"), 140)
        summary_tail = _single_line(f"刚发了一条 QQ 空间说说：{clean}", 80)
        if summary_tail and summary_tail not in summary:
            snapshot["summary"] = _single_line(f"{summary}；{summary_tail}" if summary else summary_tail, 160)
        snapshot["updated_at"] = at or _single_line(snapshot.get("updated_at"), 20)
        return True

    def _qzone_text_leaks_internal_state(self, text: str) -> bool:
        compact = str(text or "")
        if not compact.strip():
            return False
        patterns = (
            r"能量\s*[：:=]?\s*\d{1,3}\s*/\s*100",
            r"心理能量",
            r"\d{1,3}\s*/\s*100",
            r"状态变量",
            r"当前状态",
            r"拟人状态",
            r"内部状态",
            r"插件",
            r"模型",
            r"系统提示",
        )
        return any(re.search(pattern, compact, flags=re.IGNORECASE) for pattern in patterns)

    def _strip_qzone_internal_state_fragments(self, text: str) -> str:
        cleaned = _single_line(text, 180)
        if not cleaned:
            return ""
        cleaned = re.sub(r"(?:心理)?能量\s*[：:=]?\s*\d{1,3}\s*/\s*100[，,。；;\s]*", "", cleaned)
        cleaned = re.sub(r"\d{1,3}\s*/\s*100[，,。；;\s]*", "", cleaned)
        cleaned = re.sub(r"(?:当前状态|拟人状态|状态变量|内部状态)[：:，,。；;\s]*", "", cleaned)
        cleaned = re.sub(r"(?:插件|模型|系统提示)[^。！？!?；;]{0,40}[。！？!?；;]?", "", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ，,。；;")
        return _single_line(cleaned, 180)

    def _qzone_publish_style_prompt(self, *, mood: str = "life") -> str:
        base = (
            "默认风格：像随手发的一条 QQ 空间生活碎片，贴着眼前具体事物、动作或天气写；"
            "口语、轻一点、短一点，可以有小情绪但不要上价值。"
            "避免哲理总结、人生感悟、诗化独白、宏大比喻、老成说教、文案腔和谜语感。"
            "只写一个具体画面加一个动作，开头直接进入画面，不要用“今天也是……的一天”这类模板句式开场；"
            "发之前读一遍，删掉不像这个人格会说的话。"
        )
        if mood == "emotional_vent":
            base += " 心情动态也要克制，只写公开可见的余味，不要写成控诉或伤感散文。"
        voice = ""
        voice_formatter = getattr(self, "_format_persona_voice_channel_prompt", None)
        if callable(voice_formatter):
            voice = voice_formatter("creative")
        expression_voice = ""
        expression_formatter = getattr(self, "_format_expression_voice_for_prompt", None)
        if callable(expression_formatter):
            expression_voice = expression_formatter(
                scope="qzone",
                inbound_text="低落情绪" if mood == "emotional_vent" else "生活闲聊",
            )
        custom = _single_line(getattr(self, "qzone_publish_style_prompt", ""), 500)
        parts = [base]
        if voice:
            parts.append(voice)
        if expression_voice:
            parts.append(expression_voice)
        if custom:
            parts.append(f"自定义风格：{custom}")
        return "\n".join(parts)

    def _qzone_publish_image_style_prompt(self) -> str:
        base = (
            "默认配图策略：像 QQ 空间随手生活图，先贴合说说正文和当前日程选择画面。"
            "人物可以自然入镜，但不要每次都做自拍；在生活物件、食物饮品、路上光影、桌面一角、窗边、背影、侧脸、第一视角手部之间轮换。"
            "避免过度使用镜前自拍、手机挡脸自拍、固定半身自拍模板；只有正文或日程明确在整理穿搭、出门前照镜子、换衣服时才考虑镜前/镜中构图。"
        )
        custom = _single_line(getattr(self, "qzone_publish_image_style_prompt", ""), 600)
        if custom:
            return f"{base}\n自定义配图提示：{custom}"
        return base

    async def _sanitize_qzone_life_post_text(self, text: str, *, prompt: str = "") -> str:
        cleaned = _single_line(text, 180)
        relationship_cleaned = _single_line(
            self._qzone_relationship_safe_source(
                cleaned,
                source="qzone.generated_post",
            ),
            180,
        )
        if relationship_cleaned != cleaned:
            logger.warning(
                "[PrivateCompanion] QQ 空间说说草稿含未声明关系,已移除污染片段: %s",
                _single_line(cleaned, 160),
            )
            cleaned = relationship_cleaned
        if len(cleaned) < 12:
            return ""
        if not self._qzone_text_leaks_internal_state(cleaned):
            return cleaned
        stripped = self._strip_qzone_internal_state_fragments(cleaned)
        if stripped and not self._qzone_text_leaks_internal_state(stripped) and len(stripped) >= 12:
            logger.warning("[PrivateCompanion] QQ 空间说说草稿含内部状态,已净化: %s", _single_line(cleaned, 160))
            return stripped
        rewrite_prompt = f"""
下面是一条 QQ 空间说说草稿,里面泄露了内部状态/数值。请重写成自然生活动态。
只输出正文,30 到 120 字,不要解释。
禁止出现：能量、心理能量、/100、当前状态、状态变量、插件、模型、系统提示。

【原草稿】
{cleaned}

【原任务背景】
{_single_line(prompt, 600)}
""".strip()
        try:
            rewritten = await self._llm_call(
                rewrite_prompt,
                max_tokens=160,
                provider_id=self._task_provider(
                    runtime_persona_setting(self, "MAI_STYLE_PROVIDER_ID", ""),
                    runtime_persona_setting(self, "LLM_PROVIDER_ID", ""),
                ),
                task="qzone_publish_sanitize",
            )
            rewritten = _single_line(
                self._qzone_relationship_safe_source(
                    rewritten,
                    source="qzone.sanitizer_rewrite",
                ),
                180,
            )
            if rewritten and not self._qzone_text_leaks_internal_state(rewritten):
                logger.warning("[PrivateCompanion] QQ 空间说说草稿含内部状态,已重写: %s", _single_line(cleaned, 160))
                return rewritten
        except Exception as exc:
            logger.warning("[PrivateCompanion] QQ 空间说说内部状态重写失败: %s", _single_line(exc, 120))
        logger.warning("[PrivateCompanion] QQ 空间说说草稿含内部状态且重写失败,已取消本次发布")
        return ""

    async def _test_qzone_publish_tool_chain(self, event: AstrMessageEvent | None = None) -> str:
        lines = ["QQ 空间发布链路模拟："]
        lines.append(f"- 整合开关：{'开启' if self.enable_qzone_integration else '关闭'}")
        lines.append("- 真实发布：否，本指令只模拟工具链路")
        if not self._qzone_platform_supported(event):
            lines.append(f"结果：{self._qzone_platform_unavailable_message()}")
            return "\n".join(lines)

        try:
            empty_result_raw = await self._pc_qzone_publish_feed_impl(event, "")
            empty_result = json.loads(empty_result_raw)
        except Exception as exc:
            empty_result = {"status": "exception", "message": _single_line(exc, 160)}
        lines.append(
            "- 空参数工具调用："
            + (
                "通过，返回 need_text"
                if empty_result.get("status") == "need_text"
                else f"异常，返回 {empty_result.get('status') or empty_result.get('message') or empty_result}"
            )
        )

        qzone_state = self._qzone_state_dict()
        daily_state = self.data.get("daily_state", {})
        current_item = self._qzone_current_agenda_item()
        diary_context = self._recent_diary_context(count=2)
        theme_hint = self._qzone_publish_theme_hint()
        temporal_context = self._qzone_temporal_context()
        recent_publish_context = self._qzone_recent_publish_context(qzone_state)
        memory_context = await self._qzone_memory_companion_context(
            purpose="publish_test",
            query="QQ空间生活说说 今日公开可写生活 当前日程 今日穿搭 最近吃饭 日记余味 自我时间线",
        )
        public_state_hint = self._qzone_relationship_safe_source(
            self._qzone_public_state_hint(daily_state if isinstance(daily_state, dict) else {}),
            source="qzone.publish_test.current_state",
        )
        current_schedule_hint = self._qzone_relationship_safe_source(
            self._format_plan_item_for_prompt(current_item),
            source="qzone.publish_test.current_schedule",
        )
        diary_context = self._qzone_relationship_safe_source(
            diary_context,
            source="qzone.publish_test.recent_diary",
        )
        memory_context = self._qzone_relationship_safe_source(
            memory_context,
            source="qzone.publish_test.memory",
        )
        relationship_authority_guard = self._qzone_relationship_authority_guard()
        prompt = f"""
请以当前 Bot 人格写一条 QQ 空间说说。
只输出说说正文,不要解释,不要加标题。

要求：
- 30 到 120 字。
- 像自然生活动态,不是公告、不是任务汇报。
- 可以带一点公开可见的心情、天气或日记余味,但不要暴露插件、模型、内部状态数值。
- 禁止出现“能量”“心理能量”“/100”“状态变量”“当前状态”等内部汇报词。
- 不要 @ 用户,不要泄露私聊内容,不要写得像营销文。
- 写作角度：{theme_hint}

【说说风格提示】
{self._qzone_publish_style_prompt()}

【当前时间与季节】
{temporal_context}

【公开可写的状态余味】
{public_state_hint}

【当前/附近日程】
{current_schedule_hint or "无明确日程"}

【近日私密日记余味】
{diary_context or "暂无"}

【我会牢牢记住你 公开可写生活参考】
{memory_context or "暂无"}
使用方式：只选公开可写、不会泄露私聊或内部记忆来源的生活连续性。

【最近说说去重】
{recent_publish_context or "暂无最近记录。"}

{relationship_authority_guard}

{self._format_worldview_adaptation_prompt()}
""".strip()
        try:
            draft = await self._llm_call(
                prompt,
                max_tokens=180,
                provider_id=self._task_provider(
                    runtime_persona_setting(self, "MAI_STYLE_PROVIDER_ID", ""),
                    runtime_persona_setting(self, "LLM_PROVIDER_ID", ""),
                ),
                task="qzone_publish_test",
            )
            draft = await self._sanitize_qzone_life_post_text(draft, prompt=prompt)
        except Exception as exc:
            draft = ""
            lines.append(f"- 草稿生成：失败，{_single_line(exc, 160)}")
        if draft:
            lines.append("- 草稿生成：成功")
            lines.append(f"- 将传入工具参数：{{\"text\":\"{draft}\"}}")
            lines.append(f"- 草稿正文：{draft}")
        else:
            lines.append("- 草稿生成：失败或为空")
        image_enabled = bool(getattr(self, "enable_qzone_generated_image_publish", False))
        image_probability = max(0.0, min(1.0, _safe_float(getattr(self, "qzone_generated_image_probability", 0.25), 0.25)))
        generator_available = callable(getattr(self, "_generate_photo_image", None))
        backend_summary = ""
        summary_getter = getattr(self, "_photo_generation_backend_config_summary", None)
        if callable(summary_getter):
            try:
                backend_summary = _single_line(summary_getter(), 180)
            except Exception:
                backend_summary = ""
        prefix = self._qzone_reason_prefix("life_publish")
        last_image_status = _single_line(qzone_state.get(f"last_{prefix}_generated_image_status"), 80)
        last_image_note = _single_line(qzone_state.get(f"last_{prefix}_generated_image_note"), 160)
        lines.append(
            "- 配图预检："
            f"开关={'开启' if image_enabled else '关闭'}，"
            f"概率={image_probability:.0%}，"
            f"生图入口={'可用' if generator_available else '不可用'}"
        )
        if backend_summary:
            lines.append(f"- 生图后端：{backend_summary}")
        if last_image_status or last_image_note:
            lines.append(f"- 上次配图状态：{last_image_status or '-'} {last_image_note or ''}".rstrip())
        if image_enabled and image_probability <= 0:
            lines.append("- 配图结论：概率为 0，不会自动带图。")
        elif not image_enabled:
            lines.append("- 配图结论：说说配图开关未开启，不会自动带图。")
        elif not generator_available:
            lines.append("- 配图结论：缺少生图入口，不会自动带图。")
        else:
            lines.append("- 配图结论：满足发布条件时会按概率尝试生成 1 张配图；生成失败会回退纯文字。")
        lines.append("结果：模拟完成。若要真实发布,请使用 `陪伴 发说说 <正文>` 或让模型调用带 text 的 `pc_qzone_publish_feed`。")
        return "\n".join(lines)

    async def _test_qzone_publish_image_chain(self, event: AstrMessageEvent | None = None) -> str:
        lines = ["QQ 空间配图链路测试："]
        lines.append("- 真实发布：否，本指令只生成草稿和配图，不发 QQ 空间")
        image_enabled = bool(getattr(self, "enable_qzone_generated_image_publish", False))
        image_probability = max(0.0, min(1.0, _safe_float(getattr(self, "qzone_generated_image_probability", 0.25), 0.25)))
        generator_available = callable(getattr(self, "_generate_photo_image", None))
        lines.append(f"- 配图开关：{'开启' if image_enabled else '关闭'}")
        lines.append(f"- 自动配图概率：{image_probability:.0%}（本测试会绕过概率，只检查生图链路）")
        lines.append(f"- 生图入口：{'可用' if generator_available else '不可用'}")
        if not self._qzone_platform_supported(event):
            lines.append(f"结果：{self._qzone_platform_unavailable_message()}")
            return "\n".join(lines)
        summary_getter = getattr(self, "_photo_generation_backend_config_summary", None)
        if callable(summary_getter):
            try:
                backend_summary = _single_line(summary_getter(), 180)
            except Exception:
                backend_summary = ""
            if backend_summary:
                lines.append(f"- 生图后端：{backend_summary}")
        if not self.enable_qzone_integration:
            lines.append("结果：QQ 空间动态层未启用，配图测试取消。")
            return "\n".join(lines)
        if not image_enabled:
            lines.append("结果：说说配图开关未开启，配图测试取消。")
            return "\n".join(lines)
        if not generator_available:
            lines.append("结果：缺少主动生图入口，配图测试取消。")
            return "\n".join(lines)

        state = self._qzone_state_dict()
        daily_state = self.data.get("daily_state", {})
        current_item = self._qzone_current_agenda_item()
        diary_context = self._recent_diary_context(count=2)
        public_state_hint = self._qzone_relationship_safe_source(
            self._qzone_public_state_hint(daily_state if isinstance(daily_state, dict) else {}),
            source="qzone.publish_image_test.current_state",
        )
        current_schedule_hint = self._qzone_relationship_safe_source(
            self._format_plan_item_for_prompt(current_item),
            source="qzone.publish_image_test.current_schedule",
        )
        diary_context = self._qzone_relationship_safe_source(
            diary_context,
            source="qzone.publish_image_test.recent_diary",
        )
        relationship_authority_guard = self._qzone_relationship_authority_guard()
        prompt = f"""
请以当前 Bot 人格写一条 QQ 空间说说，用来测试配图生成。
只输出说说正文,不要解释,不要加标题。

要求：
- 30 到 100 字。
- 像自然生活动态,最好包含一个能被画出来的具体场景或物件。
- 不要 @ 用户,不要泄露私聊内容,不要出现插件、模型、系统提示、内部状态数值。
- 写作角度：{self._qzone_publish_theme_hint()}

【说说风格提示】
{self._qzone_publish_style_prompt()}

【当前时间与季节】
{self._qzone_temporal_context()}

【公开可写的状态余味】
{public_state_hint}

【当前/附近日程】
{current_schedule_hint or "无明确日程"}

【近日私密日记余味】
{diary_context or "暂无"}

【最近说说去重】
{self._qzone_recent_publish_context(state) or "暂无最近记录。"}

{relationship_authority_guard}

{self._format_worldview_adaptation_prompt()}
""".strip()
        try:
            draft = await self._llm_call(
                prompt,
                max_tokens=160,
                provider_id=self._task_provider(
                    runtime_persona_setting(self, "MAI_STYLE_PROVIDER_ID", ""),
                    runtime_persona_setting(self, "LLM_PROVIDER_ID", ""),
                ),
                task="qzone_publish_image_test_draft",
            )
            draft = await self._sanitize_qzone_life_post_text(draft, prompt=prompt)
        except Exception as exc:
            lines.append(f"结果：草稿生成失败，{_single_line(exc, 160)}")
            return "\n".join(lines)
        if not draft:
            lines.append("结果：草稿为空或不安全，配图测试取消。")
            return "\n".join(lines)
        lines.append(f"- 草稿正文：{draft}")
        images = await self._maybe_generate_qzone_publish_image(
            post_text=draft,
            reason="life_publish",
            daily_state=daily_state if isinstance(daily_state, dict) else {},
            current_item=current_item,
            diary_context=diary_context,
            state=state,
            force=True,
        )
        prefix = self._qzone_reason_prefix("life_publish")
        status = _single_line(state.get(f"last_{prefix}_generated_image_status"), 80)
        note = _single_line(state.get(f"last_{prefix}_generated_image_note"), 160)
        backend = _single_line(state.get(f"last_{prefix}_generated_image_backend"), 60)
        caption = _single_line(state.get(f"last_{prefix}_generated_image_caption"), 160)
        visual_anchor = _single_line(state.get(f"last_{prefix}_generated_image_anchor"), 120)
        composition = _single_line(state.get(f"last_{prefix}_generated_image_composition"), 120)
        reference_image = _single_line(state.get(f"last_{prefix}_generated_image_reference"), 220)
        reference_exists = bool(state.get(f"last_{prefix}_generated_image_reference_exists", False))
        if callable(getattr(self, "_save_data_sync", None)):
            try:
                self._save_data_sync(sections={"qzone_integration"})
            except Exception:
                pass
        if images:
            lines.append("- 生图结果：成功")
            if backend:
                lines.append(f"- 后端：{backend}")
            if caption:
                lines.append(f"- 画面说明：{caption}")
            if visual_anchor:
                lines.append(f"- 视觉锚点：{visual_anchor}")
            if composition:
                lines.append(f"- 构图：{composition}")
            if reference_image:
                lines.append(f"- 自拍参考图：{'可用' if reference_exists else '不可用'} {_single_line(reference_image, 160)}")
            lines.append(f"- 图片路径：{_single_line(images[0], 220)}")
            lines.append("结果：配图生成链路可用。下一步可用 `陪伴 发说说 <正文>` 或等待自动说说验证上传。")
        else:
            lines.append(f"- 生图结果：{status or '失败'}")
            if note:
                lines.append(f"- 原因：{note}")
            if visual_anchor:
                lines.append(f"- 视觉锚点：{visual_anchor}")
            if composition:
                lines.append(f"- 构图：{composition}")
            if reference_image:
                lines.append(f"- 自拍参考图：{'可用' if reference_exists else '不可用'} {_single_line(reference_image, 160)}")
            lines.append("结果：没有生成可用于说说的图片。")
        return "\n".join(lines)

    async def _test_qzone_integration(self, event: AstrMessageEvent | None, target_id: str = "") -> str:
        lines = ["QQ 空间测试："]

        lines.append(f"- 整合开关：{'开启' if self.enable_qzone_integration else '关闭'}")
        lines.append("- 内置服务：可用")
        lines.append("- 外部插件依赖：无")

        if not self._qzone_platform_supported(event):
            lines.append(f"结果：{self._qzone_platform_unavailable_message()}")
            return "\n".join(lines)

        if not self.enable_qzone_integration:
            lines.append("结果：整合开关关闭。")
            return "\n".join(lines)

        target = _single_line(target_id, 40)
        try:
            cookie_header = await self._qzone_get_cookies(event)
            ctx = self._qzone_context_from_cookies(cookie_header)
            target = target or str(ctx.get("uin") or "")
            lines.append(f"- Cookie：已获取，登录 QQ {ctx.get('uin')}")
            lines.append("- 读取动态：可用")
            lines.append("- 发布说说：可用")
            lines.append("- 点赞/评论：可用")
            posts = await self._qzone_query_feeds(
                event,
                target_id=target or None,
                pos=0,
                num=1,
                with_detail=True,
                cookie_header=cookie_header,
            )
            if not posts:
                lines.append(f"- 查询目标：{target or '默认'}")
                lines.append("- 查询结果：空")
                lines.append("结果：读取链路可调用，但没有拿到动态。")
                return "\n".join(lines)
            post = posts[0]
            text = _single_line(getattr(post, "text", "") or getattr(post, "rt_con", ""), 120)
            images = list(getattr(post, "images", []) or [])
            lines.append(f"- 查询目标：{target or '默认'}")
            lines.append("- 查询结果：成功")
            lines.append(f"- 作者：{_single_line(getattr(post, 'name', ''), 40) or '未知'}")
            lines.append(f"- QQ：{str(getattr(post, 'uin', '') or '') or '未知'}")
            lines.append(f"- 内容：{text or '无文本'}")
            lines.append(f"- 图片数：{len(images)}")
            lines.append("结果：QQ 空间读取链路正常。")
            return "\n".join(lines)
        except Exception as exc:
            lines.append(f"- 查询目标：{target or '默认'}")
            error_text = _single_line(exc, 160)
            if "空响应" in error_text:
                error_text = "接口返回空响应，通常表示目标空间不可见、无权限访问，或当前 Cookie 对该目标无访问权"
            lines.append(f"- 查询结果：失败：{error_text}")
            lines.append("结果：内置服务已加载，但 QQ 空间访问失败。")
            return "\n".join(lines)

    @staticmethod
    def _qzone_reason_prefix(reason: str) -> str:
        if reason == "emotional_vent":
            return "emotional_vent"
        if reason == "manual_publish":
            return "manual_publish"
        return "life_publish"

    def _qzone_reusable_draft(self, state: dict[str, Any], reason: str, *, now: float | None = None, max_age_hours: float = 72.0) -> str:
        if not isinstance(state, dict):
            return ""
        prefix = self._qzone_reason_prefix(reason)
        status = str(state.get(f"last_{prefix}_status") or "").strip()
        if not (status.startswith("failed:") or status.startswith("paused:") or status.startswith("retrying:")):
            return ""
        current = _now_ts() if now is None else float(now)
        draft_at = _safe_float(state.get(f"last_{prefix}_draft_at"), 0)
        if not draft_at or current - draft_at > max(1.0, float(max_age_hours)) * 3600:
            return ""
        draft_key = f"last_{prefix}_draft"
        draft = _single_line(state.get(draft_key), 300)
        cleaned = _single_line(
            self._qzone_relationship_safe_source(
                draft,
                source=f"qzone.reusable_draft.{prefix}",
            ),
            300,
        )
        if cleaned != draft:
            state[draft_key] = cleaned
            if len(cleaned) < 12:
                state.pop(draft_key, None)
                state.pop(f"last_{prefix}_draft_at", None)
                return ""
        return cleaned

    def _qzone_reusable_generated_image(self, state: dict[str, Any], reason: str, post_text: str, *, now: float | None = None) -> list[str]:
        if not isinstance(state, dict):
            return []
        prefix = self._qzone_reason_prefix(reason)
        current = _now_ts() if now is None else float(now)
        image_at = _safe_float(state.get(f"last_{prefix}_generated_image_at"), 0)
        if not image_at or current - image_at > 72 * 3600:
            return []
        stored_text = _single_line(state.get(f"last_{prefix}_generated_image_text"), 300)
        if stored_text and stored_text != _single_line(post_text, 300):
            return []
        image_path = str(state.get(f"last_{prefix}_generated_image_path") or "").strip()
        if not image_path:
            return []
        if not re.match(r"^(?:https?://|file://|data:)", image_path, flags=re.I) and not Path(image_path).exists():
            return []
        logger.info("[PrivateCompanion] QQ 空间复用待发布配图: reason=%s path=%s", reason, _single_line(image_path, 160))
        return [image_path]

    def _qzone_note_publish_image_status(
        self,
        state: dict[str, Any] | None,
        reason: str,
        status: str,
        note: Any = "",
        *,
        path: Any = "",
        backend: Any = "",
        caption: Any = "",
        reference_image: Any = "",
        reference_exists: bool | None = None,
        visual_anchor: Any = "",
        composition: Any = "",
    ) -> None:
        if not isinstance(state, dict):
            return
        prefix = self._qzone_reason_prefix(reason)
        state[f"last_{prefix}_generated_image_status"] = _single_line(status, 60)
        state[f"last_{prefix}_generated_image_note"] = _single_line(note, 180)
        state[f"last_{prefix}_generated_image_checked_at"] = _now_ts()
        if path:
            state[f"last_{prefix}_generated_image_path"] = _path_text(path, 1000)
        if backend:
            state[f"last_{prefix}_generated_image_backend"] = _single_line(backend, 40)
        if caption:
            state[f"last_{prefix}_generated_image_caption"] = _single_line(caption, 180)
        if reference_image:
            state[f"last_{prefix}_generated_image_reference"] = _path_text(reference_image, 1000)
        if reference_exists is not None:
            state[f"last_{prefix}_generated_image_reference_exists"] = bool(reference_exists)
        if visual_anchor:
            state[f"last_{prefix}_generated_image_anchor"] = _single_line(visual_anchor, 120)
        if composition:
            state[f"last_{prefix}_generated_image_composition"] = _single_line(composition, 120)

    def _qzone_clear_pending_publish_assets(self, state: dict[str, Any], reason: str) -> None:
        if not isinstance(state, dict):
            return
        prefix = self._qzone_reason_prefix(reason)
        for key in (
            f"last_{prefix}_draft",
            f"last_{prefix}_draft_at",
            f"last_{prefix}_generated_image_path",
            f"last_{prefix}_generated_image_at",
            f"last_{prefix}_generated_image_text",
            f"last_{prefix}_generated_image_reference",
            f"last_{prefix}_generated_image_reference_exists",
            f"last_{prefix}_generated_image_anchor",
            f"last_{prefix}_generated_image_composition",
        ):
            state.pop(key, None)

    async def _maybe_generate_qzone_publish_image(
        self,
        *,
        post_text: str,
        reason: str,
        daily_state: dict[str, Any] | None = None,
        current_item: Any = None,
        diary_context: str = "",
        state: dict[str, Any] | None = None,
        force: bool = False,
    ) -> list[str]:
        reusable = [] if force else self._qzone_reusable_generated_image(state if isinstance(state, dict) else {}, reason, post_text)
        if reusable:
            self._qzone_note_publish_image_status(state, reason, "reused", "复用上次待发布配图", path=reusable[0])
            return reusable
        if not (
            getattr(self, "enable_qzone_generated_image_publish", False)
            and getattr(self, "enable_qzone_integration", False)
        ):
            self._qzone_note_publish_image_status(state, reason, "skipped:disabled", "QQ 空间配图开关未开启")
            return []
        probability = max(0.0, min(1.0, _safe_float(getattr(self, "qzone_generated_image_probability", 0.25), 0.25)))
        if not force and (probability <= 0 or random.random() > probability):
            self._qzone_note_publish_image_status(state, reason, "skipped:probability", f"未命中配图概率 {probability:.0%}")
            return []
        if callable(getattr(self, "_daily_token_soft_limit_should_defer", None)) and self._daily_token_soft_limit_should_defer("photo_prompt"):
            logger.info("[PrivateCompanion] QQ 空间主动配图跳过: token_soft_limit")
            self._qzone_note_publish_image_status(state, reason, "skipped:token_budget", "token 软上限保护")
            return []
        generator = getattr(self, "_generate_photo_image", None)
        if not callable(generator):
            logger.info("[PrivateCompanion] QQ 空间主动配图跳过: image_generator_unavailable")
            self._qzone_note_publish_image_status(state, reason, "skipped:no_generator", "缺少 _generate_photo_image 生图入口")
            return []

        style_name, style_instruction = self._get_photo_style_instruction()
        post_text = self._qzone_relationship_safe_source(post_text, source="qzone.image.post_text")
        current_desc = self._qzone_relationship_safe_source(
            self._format_plan_item_for_prompt(current_item),
            source="qzone.image.current_schedule",
        ) or "无明确日程"
        state_desc = self._qzone_relationship_safe_source(
            self._qzone_public_state_hint(daily_state if isinstance(daily_state, dict) else {}),
            source="qzone.image.current_state",
        )
        diary_context = self._qzone_relationship_safe_source(
            diary_context,
            source="qzone.image.recent_diary",
        )
        content_options = ""
        try:
            content_options = self._format_content_choice_options_for_prompt()
        except Exception:
            content_options = "生活小物、窗边光影、路上风景、桌面一角、随手自拍、偶遇小动物。"
        content_options = self._qzone_relationship_safe_source(
            content_options,
            source="qzone.image.content_options",
        )
        relationship_authority_guard = self._qzone_relationship_authority_guard()
        qzone_selfie_reference_path = ""
        qzone_selfie_reference_exists = False
        reference_getter = getattr(self, "_photo_persona_reference_image_for_kind_async", None)
        if callable(reference_getter):
            try:
                qzone_selfie_reference_path = await reference_getter(
                    "selfie",
                    allow_daily_outfit=True,
                    request_text=f"说说：{post_text}",
                    ambient_context=f"当前日程：{current_desc}\n当前状态：{state_desc}",
                )
            except Exception as ref_exc:
                logger.info(
                    "[PrivateCompanion] QQ 空间自拍参考图预检失败: reason=%s error=%s",
                    _single_line(reason, 40),
                    _single_line(ref_exc, 120),
                )
                qzone_selfie_reference_path = ""
        try:
            qzone_selfie_reference_exists = bool(
                qzone_selfie_reference_path and Path(str(qzone_selfie_reference_path)).exists()
            )
        except (OSError, ValueError):
            qzone_selfie_reference_exists = False
        reference_text = (
            "有可用参考图。可以选择 selfie 让人物自然入镜，但不要默认镜前自拍；只有正文或日程明确需要穿搭/照镜子时才用镜前构图。选择 selfie 时 prompt 必须写明保持参考图中的人物身份、脸部、发色、瞳色、穿搭连续性。"
            if qzone_selfie_reference_path
            else "当前没有可用自拍参考图。可以让人物自然入镜，人物外貌参考人格描述和公开状态；优先使用第一视角手部、侧脸、背影、肩颈半身、影子、随身小物等不强依赖精确脸部的方式，避免凭空追加人格里没有的脸部细节，也不要默认镜前自拍。"
        )
        prompt = f"""
请为一条即将公开发布到 QQ 空间的说说生成一张配图提示词。
只输出 JSON，不要解释。

【说说正文】
{_single_line(post_text, 300)}

【人格】
{self._get_default_persona_prompt()}

【公开可写的状态余味】
{state_desc}

【当前/附近日程】
{current_desc}

【近日日记余味】
{_single_line(diary_context, 500) or "暂无"}

{self._format_worldview_adaptation_prompt()}

{relationship_authority_guard}

【可选画面方向】
{content_options}

【自拍参考图状态】
{reference_text}

【空间配图风格提示】
{self._qzone_publish_image_style_prompt()}

【生图风格】
{style_name}
风格要求：{style_instruction}

输出 JSON：
{{
  "kind": "selfie 或 text2img；按说说正文选择，不要固定优先镜前自拍",
  "visual_anchor": "本图唯一视觉锚点，例如第一视角手部与饮品/桌面小物/路上夕光/侧脸看窗边光影/背影走在路上/餐盘与衣袖；必须具体",
  "composition": "构图一句话，例如第一视角手部近景/桌面俯拍/侧脸三分构图/背影环境中景/路边半身随拍/窗边剪影；镜前自拍只能偶尔使用",
  "prompt": "给生图后端的中文提示词，包含唯一主体、场景、光线、构图、情绪和风格；不要写聊天口吻",
  "caption": "一句画面说明"
}}

要求：
1. 图片必须像公开动态配图，不要包含私聊、系统、插件、模型、内部状态数值。
2. 先确定一个“唯一视觉锚点”，不要把多个主体拼在一张图里；画面要贴合说说正文和当前日程，不要为了配图硬画无关内容。
3. 人物可以入镜，但不要每次都自拍；在第一视角手部、桌面小物、食物饮品、路上光影、窗边侧脸、背影、影子、随身小物和半身随拍之间轮换。
4. 镜前自拍、镜中自拍、手机挡脸自拍不是默认模板；只有正文/日程明确涉及穿搭、整理仪容、出门前照镜子或房间镜子时才使用，且不要连续复用。
5. 如果有自拍参考图，选择 selfie 时必须写清“保留参考图人物身份和外观”“脸部完整清晰”“不要裁脸/遮脸/只拍身体局部”，并让场景来自当前日程；但仍要优先考虑非镜前构图。
6. 如果没有自拍参考图，仍可选择人物入镜；人物外貌以人格描述、公开状态和风格设定为准，不要追加人格里没有的脸部细节。优先使用不强依赖精确脸部的自然入镜方式，比如侧脸、背影、第一视角手部、肩颈半身、窗边剪影。
7. 如果选择 text2img：也可以保留人的存在感，如手边物件、脚步、背影、影子或随身小物；只有画面确实不适合人物入镜时才纯物件/纯风景。
8. 不要包含 NSFW、真实用户隐私、聊天截图或电脑屏幕内容；避免文字、水印、UI、二维码、聊天气泡。
9. prompt 必须体现上面的生图风格要求，且不能是泛泛的“好看的照片/生活记录/天气图”。
""".strip()
        try:
            text = await self._llm_call(
                prompt,
                max_tokens=360,
                provider_id=self._task_provider(
                    runtime_persona_setting(self, "PHOTO_PROMPT_PROVIDER_ID", ""),
                    runtime_persona_setting(self, "MAI_STYLE_PROVIDER_ID", ""),
                ),
                task=f"qzone_{reason}_photo_prompt",
            )
            payload = self._extract_json_payload(text or "")
            if isinstance(payload, dict):
                workflow_kind = _single_line(payload.get("kind"), 60).lower()
                visual_anchor = _single_line(payload.get("visual_anchor"), 120)
                composition = _single_line(payload.get("composition"), 120)
                image_prompt = _single_line(payload.get("prompt"), 600)
                caption = _single_line(payload.get("caption"), 180)
            else:
                workflow_kind = "text2img"
                visual_anchor = ""
                composition = ""
                image_prompt = _single_line(text, 600)
                caption = image_prompt
            if any(token in workflow_kind for token in ("selfie", "portrait", "自拍", "人像", "人物", "出镜")):
                workflow_kind = "selfie"
            elif any(token in workflow_kind for token in ("text2img", "scene", "photo", "风景", "静物", "物件")):
                workflow_kind = "text2img"
            else:
                workflow_kind = "text2img"
            if not image_prompt:
                image_prompt = f"QQ 空间公开动态配图，{_single_line(post_text, 160)}，{style_instruction}"
            if visual_anchor and visual_anchor not in image_prompt:
                image_prompt = f"唯一视觉锚点：{visual_anchor}。{image_prompt}"
            if composition and composition not in image_prompt:
                image_prompt = f"{image_prompt}。构图：{composition}"
            if workflow_kind == "selfie":
                if qzone_selfie_reference_path:
                    image_prompt = (
                        f"{image_prompt}。保留参考图中的人物身份、脸部、发色、瞳色和穿搭连续性；"
                        "脸部完整清晰，头发、肩颈和上半身自然入镜；不要裁脸、遮脸、背影、只拍身体局部。"
                    )
                else:
                    image_prompt = (
                        f"{image_prompt}。人物是画面主角，外貌参考人格描述、公开状态和风格设定；没有可用自拍参考图时不要追加人格里没有的脸部细节；"
                        "优先使用第一视角手部、侧脸、背影、肩颈半身、窗边剪影、随身小物等自然入镜方式；不要默认镜前自拍或手机挡脸自拍，保持公开动态随手拍质感。"
                    )
            else:
                image_prompt = (
                    f"{image_prompt}。画面像 QQ 空间公开生活配图，单一主体清楚，不出现聊天截图、UI、二维码、水印或虚构人物脸部。"
                )
            reference_image_path = qzone_selfie_reference_path if workflow_kind == "selfie" else ""
            reference_exists = qzone_selfie_reference_exists if workflow_kind == "selfie" else False
            logger.info(
                "[PrivateCompanion] QQ 空间配图生图开始: reason=%s kind=%s anchor=%s composition=%s reference=%s reference_exists=%s post=%s prompt=%s",
                _single_line(reason, 40),
                _single_line(workflow_kind, 30),
                _single_line(visual_anchor, 80) or "-",
                _single_line(composition, 80) or "-",
                bool(reference_image_path),
                reference_exists,
                _single_line(post_text, 120),
                _single_line(image_prompt, 180),
            )
            backend_name, image_path, workflow_note = await generator(
                workflow_kind=workflow_kind,
                prompt_text=image_prompt,
                session_key=f"qzone_{reason}",
                reference_image_path=reference_image_path,
            )
        except Exception as exc:
            logger.info("[PrivateCompanion] QQ 空间主动配图失败: %s", _single_line(exc, 120))
            self._qzone_note_publish_image_status(
                state,
                reason,
                "failed:prompt_or_generate",
                exc,
                reference_image=qzone_selfie_reference_path,
                reference_exists=qzone_selfie_reference_exists,
            )
            return []
        if not image_path:
            logger.info("[PrivateCompanion] QQ 空间主动配图跳过: %s", _single_line(workflow_note, 160))
            self._qzone_note_publish_image_status(
                state,
                reason,
                "failed:no_image",
                workflow_note,
                backend=backend_name,
                reference_image=reference_image_path,
                reference_exists=reference_exists,
                visual_anchor=visual_anchor,
                composition=composition,
            )
            return []
        if not re.match(r"^(?:https?://|file://|data:)", str(image_path), flags=re.I) and not Path(str(image_path)).exists():
            logger.info("[PrivateCompanion] QQ 空间主动配图跳过: image_path_missing path=%s", _single_line(image_path, 160))
            self._qzone_note_publish_image_status(
                state,
                reason,
                "failed:path_missing",
                "生图返回路径不存在",
                path=image_path,
                backend=backend_name,
                reference_image=reference_image_path,
                reference_exists=reference_exists,
                visual_anchor=visual_anchor,
                composition=composition,
            )
            return []
        if isinstance(state, dict):
            prefix = self._qzone_reason_prefix(reason)
            state["last_generated_image_path"] = _path_text(image_path, 1000)
            state["last_generated_image_at"] = _now_ts()
            state["last_generated_image_reason"] = reason
            state["last_generated_image_caption"] = _single_line(caption, 180)
            state["last_generated_image_backend"] = _single_line(backend_name, 40)
            if visual_anchor:
                state["last_generated_image_anchor"] = _single_line(visual_anchor, 120)
            if composition:
                state["last_generated_image_composition"] = _single_line(composition, 120)
            if reference_image_path:
                state["last_generated_image_reference"] = _path_text(reference_image_path, 1000)
            state["last_generated_image_reference_exists"] = bool(reference_exists)
            state[f"last_{prefix}_generated_image_path"] = _path_text(image_path, 1000)
            state[f"last_{prefix}_generated_image_at"] = _now_ts()
            state[f"last_{prefix}_generated_image_text"] = _single_line(post_text, 300)
            state[f"last_{prefix}_generated_image_caption"] = _single_line(caption, 180)
            state[f"last_{prefix}_generated_image_backend"] = _single_line(backend_name, 40)
            if visual_anchor:
                state[f"last_{prefix}_generated_image_anchor"] = _single_line(visual_anchor, 120)
            if composition:
                state[f"last_{prefix}_generated_image_composition"] = _single_line(composition, 120)
            self._qzone_note_publish_image_status(
                state,
                reason,
                "generated",
                workflow_note or "ok",
                path=image_path,
                backend=backend_name,
                caption=caption,
                reference_image=reference_image_path,
                reference_exists=reference_exists,
                visual_anchor=visual_anchor,
                composition=composition,
            )
        logger.info(
            "[PrivateCompanion] QQ 空间主动配图完成: reason=%s backend=%s reference=%s reference_exists=%s path=%s",
            reason,
            _single_line(backend_name, 40),
            bool(reference_image_path),
            reference_exists,
            _single_line(image_path, 160),
        )
        return [image_path]
