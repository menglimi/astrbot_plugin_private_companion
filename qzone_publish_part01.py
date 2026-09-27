# -*- coding: utf-8 -*-
"""QzonePublishPart01Mixin。

由 tools/split_mixin_domain.py 从 qzone_publish.py 机械抽取（17 个方法 + 0 个模块级名字 + 0 个类级赋值 / 442 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 QzonePublishMixin）。
"""
from __future__ import annotations

from .qzone_publish_shared import logger
from .qzone_publish_shared import Any
from .qzone_publish_shared import AstrMessageEvent
from .qzone_publish_shared import PromptRenderMode
from .qzone_publish_shared import PromptSection
from .qzone_publish_shared import _now_ts
from .qzone_publish_shared import _safe_int
from .qzone_publish_shared import _single_line
from .qzone_publish_shared import prompt_section
from .qzone_publish_shared import random
from .qzone_publish_shared import re
from .qzone_publish_shared import render_prompt_sections
from .qzone_publish_shared import runtime_persona_setting
from .qzone_publish_shared import time



class QzonePublishPart01Mixin:
    """QzonePublishPart01Mixin（从 QzonePublishMixin 拆出）。"""


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

    def _qzone_recent_self_publish_chat_context_body(self, *, limit: int = 3) -> str:
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
            "\n".join(records)
            + "\n这些正文是 Bot 自己发出的公开动态，不是当前用户发的内容。"
        )

    def _qzone_recent_self_publish_chat_context(self, *, limit: int = 3) -> str:
        section = self._qzone_recent_self_publish_chat_prompt_section(limit=limit)
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _qzone_recent_self_publish_chat_prompt_section(
        self,
        *,
        limit: int = 3,
    ) -> PromptSection:
        return prompt_section(
            key="tools.qzone.recent_self_publish",
            title="Bot 自己最近成功发布的 QQ 空间记录",
            source="qzone_publish",
            content=self._qzone_recent_self_publish_chat_context_body(limit=limit),
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
            logger.debug("QQ 空间发布记录保存失败: %s", _single_line(exc, 120))

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
                "QQ 空间说说草稿含未声明关系,已移除污染片段: %s",
                _single_line(cleaned, 160),
            )
            cleaned = relationship_cleaned
        if len(cleaned) < 12:
            return ""
        if not self._qzone_text_leaks_internal_state(cleaned):
            return cleaned
        stripped = self._strip_qzone_internal_state_fragments(cleaned)
        if stripped and not self._qzone_text_leaks_internal_state(stripped) and len(stripped) >= 12:
            logger.warning("QQ 空间说说草稿含内部状态,已净化: %s", _single_line(cleaned, 160))
            return stripped
        instruction = """
下面是一条 QQ 空间说说草稿,里面泄露了内部状态/数值。请重写成自然生活动态。
只输出正文,30 到 120 字,不要解释。
禁止出现：能量、心理能量、/100、当前状态、状态变量、插件、模型、系统提示。
""".strip()

        rewrite_prompt = "\n\n".join(
            (
                render_prompt_sections([prompt_section(key="qzone.sanitize.instruction", title="说说净化任务", source="qzone_publish", content=instruction)], mode=PromptRenderMode.BODY_ONLY),
                render_prompt_sections(
                    [
                        prompt_section(key="qzone.sanitize.draft", title="原草稿", source="qzone_publish", content=cleaned),
                        prompt_section(key="qzone.sanitize.context", title="原任务背景", source="qzone_publish", content=_single_line(prompt, 600)),
                    ],
                    mode=PromptRenderMode.LABELED_BLOCK,
                ),
            )
        )
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
                logger.warning("QQ 空间说说草稿含内部状态,已重写: %s", _single_line(cleaned, 160))
                return rewritten
        except Exception as exc:
            logger.warning("QQ 空间说说内部状态重写失败: %s", _single_line(exc, 120))
        logger.warning("QQ 空间说说草稿含内部状态且重写失败,已取消本次发布")
        return ""
