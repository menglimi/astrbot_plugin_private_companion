# -*- coding: utf-8 -*-
"""QzoneSchedulePart03Mixin。

由 tools/split_mixin_domain.py 从 qzone_schedule.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 214 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 QzoneScheduleMixin）。
"""
from __future__ import annotations

from .qzone_schedule_shared import _persona_provider_id, logger
from .qzone_schedule_shared import Any
from .qzone_schedule_shared import PromptRenderMode
from .qzone_schedule_shared import _now_ts
from .qzone_schedule_shared import _safe_float
from .qzone_schedule_shared import _safe_int
from .qzone_schedule_shared import _single_line
from .qzone_schedule_shared import prompt_section
from .qzone_schedule_shared import random
from .qzone_schedule_shared import render_prompt_sections
from .qzone_schedule_shared import runtime_persona_setting



class QzoneSchedulePart03Mixin:
    """QzoneSchedulePart03Mixin（从 QzoneScheduleMixin 拆出）。"""


    async def _maybe_publish_qzone_emotional_vent(
        self,
        *,
        user_snapshot: dict[str, Any] | None = None,
        interaction_state: dict[str, Any] | None = None,
        relationship_state: dict[str, Any] | None = None,
        intent: dict[str, Any] | None = None,
    ) -> None:
        if not (
            self._qzone_available()
            and runtime_persona_setting(self, "enable_emotion_simulation", True)
            and runtime_persona_setting(self, "enable_qzone_emotional_vent_publish", False)
        ):
            return
        # The short-lived interaction projection is the source of truth for
        # public expression. Keep the legacy relationship_state argument as a
        # compatibility bridge, but never use its mood score as the new gate.
        interaction = interaction_state if isinstance(interaction_state, dict) else {}
        if not interaction and isinstance(relationship_state, dict):
            legacy_projection = relationship_state.get("current_interaction")
            if isinstance(legacy_projection, dict):
                interaction = legacy_projection
        threshold = _safe_int(
            runtime_persona_setting(self, "qzone_emotional_vent_threshold", 90),
            90,
            40,
            100,
        )
        event_intensity = _safe_int((intent or {}).get("emotion_intensity"), 0, 0, 100)
        if event_intensity < threshold or interaction.get("expression_band") not in {"avoidant", "hurt"}:
            return
        if isinstance(user_snapshot, dict):
            role_getter = getattr(self, "_private_user_role", None)
            try:
                role = role_getter(user_snapshot, str(user_snapshot.get("user_id") or "")) if callable(role_getter) else ""
            except Exception:
                role = ""
            if role != "owner":
                logger.info(
                    "公开心情动态跳过: user_role=%s intensity=%s",
                    role or "friend",
                    event_intensity,
                )
                return
        now = _now_ts()
        state = self.data.setdefault("qzone_integration", {})
        if not isinstance(state, dict):
            self.data["qzone_integration"] = {}
            state = self.data["qzone_integration"]
        cooldown = max(
            4,
            _safe_int(
                runtime_persona_setting(self, "qzone_emotional_vent_cooldown_hours", 72),
                72,
                4,
                336,
            ),
        ) * 3600
        if now - _safe_float(state.get("last_emotional_vent_at"), 0) < cooldown:
            logger.info("公开心情动态跳过: cooldown intensity=%s", event_intensity)
            return
        block_reason = self._qzone_auto_publish_block_reason(state, now=now)
        if block_reason:
            state["last_emotional_vent_status"] = f"paused:auth:{_single_line(block_reason, 80)}"
            state["last_emotional_vent_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            return
        if now - _safe_float(state.get("last_emotional_vent_failed_at"), 0) < 15 * 60:
            return
        reusable_text = self._qzone_reusable_draft(state, "emotional_vent", now=now)
        probability = max(
            0.0,
            min(
                1.0,
                _safe_float(
                    runtime_persona_setting(self, "qzone_emotional_vent_probability", 0.35),
                    0.35,
                ),
            ),
        )
        if not reusable_text and random.random() > probability:
            state["last_emotional_vent_status"] = "skipped:probability_miss"
            state["last_emotional_vent_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            return
        preflight_error = await self._qzone_preflight_auto_publish(None, state=state, source="emotional_vent")
        if preflight_error:
            state["last_emotional_vent_failed_at"] = now
            state["last_emotional_vent_status"] = f"paused:auth:{_single_line(preflight_error, 80)}"
            state["last_emotional_vent_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            return
        daily_state = self.data.get("daily_state", {})
        current_item = self._qzone_current_agenda_item()
        public_state_hint = self._qzone_relationship_safe_source(
            self._qzone_public_state_hint(daily_state if isinstance(daily_state, dict) else {}),
            source="qzone.emotional_vent.current_state",
        )
        current_schedule_hint = self._qzone_relationship_safe_source(
            self._format_plan_item_for_prompt(current_item),
            source="qzone.emotional_vent.current_schedule",
        )
        reason = _single_line(
            self._qzone_relationship_safe_source(
                interaction.get("reason") or (intent or {}).get("emotion_reason"),
                source="qzone.emotional_vent.reason",
            ),
            80,
        )
        relationship_authority_guard = self._qzone_relationship_authority_guard()
        instruction = """
请以当前 Bot 人格写一条 QQ 空间说说,表达一种模糊的低落、委屈或想透气的心情。
只输出说说正文,不要解释,不要加标题。

要求：
- 20 到 80 字。
- 像自然生活动态,不要像控诉、公告、任务汇报。
- 只留一个画面、一个动作,不写排比和感想堆叠。
- 不要 @ 用户,不要提到任何具体用户、私聊内容、聊天截图或“刚才谁说了什么”。
- 不要出现“受伤分”“情绪分”“阈值”“插件”“模型”“Bot”“机器人”“/100”等内部词。
- 可以写天气、夜色、窗边、散步、想安静一会儿这类公开可见的余味。
""".strip()

        prompt = "\n\n".join(
            part for part in (
                render_prompt_sections([prompt_section(key="qzone.emotional_vent.instruction", title="QQ 空间心情动态", source="qzone_schedule", content=instruction)], mode=PromptRenderMode.BODY_ONLY),
                render_prompt_sections(
                    [
                        prompt_section(key="qzone.emotional_vent.style", title="说说风格提示", source="qzone_schedule", content=self._qzone_publish_style_prompt(mood="emotional_vent")),
                        prompt_section(key="qzone.emotional_vent.state", title="公开可写的状态余味", source="qzone_schedule", content=public_state_hint),
                        prompt_section(key="qzone.emotional_vent.schedule", title="当前/附近日程", source="qzone_schedule", content=current_schedule_hint or "无明确日程"),
                        prompt_section(key="qzone.emotional_vent.reason", title="内部触发原因，只能作为情绪方向，禁止复述", source="qzone_schedule", content=reason or "情绪有点低落"),
                    ],
                    mode=PromptRenderMode.LABELED_BLOCK,
                ),
                relationship_authority_guard,
                self._format_worldview_adaptation_prompt(),
            ) if part
        )
        try:
            if reusable_text:
                text = reusable_text
                logger.info(
                    "QQ 空间复用待发布心情动态草稿: age=%ds",
                    int(now - _safe_float(state.get("last_emotional_vent_draft_at"), now)),
                )
            else:
                text = await self._llm_call(
                    prompt,
                    max_tokens=140,
                    provider_id=self._task_provider(
                        _persona_provider_id(
                            self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                        ),
                        _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
                    ),
                    task="qzone_emotional_vent",
                )
                text = await self._sanitize_qzone_life_post_text(text, prompt=prompt)
                if not text:
                    state["last_emotional_vent_failed_at"] = now
                    state["last_emotional_vent_status"] = "cancelled:empty_or_unsafe_draft"
                    state["last_emotional_vent_checked_at"] = now
                    self._save_data_sync(sections={"qzone_integration"})
                    logger.warning("公开心情动态草稿为空或不安全,已跳过发布")
                    return
                state["last_emotional_vent_draft"] = _single_line(text, 240)
                state["last_emotional_vent_draft_at"] = now
            if reusable_text:
                image_sources = self._qzone_reusable_generated_image(state, "emotional_vent", text, now=now)
            else:
                image_sources = await self._maybe_generate_qzone_publish_image(
                    post_text=text,
                    reason="emotional_vent",
                    daily_state=daily_state if isinstance(daily_state, dict) else {},
                    current_item=current_item,
                    diary_context="",
                    state=state,
                )
            result = await self._publish_qzone_text(text, images=image_sources, publish_reason="emotional_vent")
            if result.get("success"):
                state["last_emotional_vent_at"] = now
                state.pop("last_emotional_vent_failed_at", None)
                state["last_emotional_vent_status"] = "published"
                if result.get("image_fallback"):
                    self._qzone_note_publish_image_status(
                        state,
                        "emotional_vent",
                        "failed:upload_fallback",
                        result.get("image_fallback_message") or "配图发布失败，已降级纯文字发布",
                    )
                    state["last_emotional_vent_image_fallback"] = {
                        "stage": _single_line(result.get("image_fallback_stage"), 40),
                        "message": _single_line(result.get("image_fallback_message"), 180),
                        "at": now,
                    }
                else:
                    state.pop("last_emotional_vent_image_fallback", None)
                self._qzone_clear_pending_publish_assets(state, "emotional_vent")
                logger.info("公开心情动态已发布: intensity=%s text=%s", event_intensity, _single_line(result.get("text") or text, 120))
            else:
                state["last_emotional_vent_failed_at"] = now
                state["last_emotional_vent_status"] = f"failed:{_single_line(result.get('message'), 80)}"
                logger.warning("公开心情动态发布失败: %s", _single_line(result.get("message"), 120))
            state["last_emotional_vent_checked_at"] = now
            state["last_emotional_vent_text"] = _single_line(result.get("text") or text, 180)
            state["last_emotional_vent_images"] = _safe_int(result.get("image_count"), len(result.get("images") or []), 0, 99) if result.get("success") else 0
            self._save_data_sync(sections={"qzone_integration"})
        except Exception as exc:
            state["last_emotional_vent_failed_at"] = now
            state["last_emotional_vent_status"] = f"failed:{_single_line(exc, 80)}"
            state["last_emotional_vent_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            logger.warning("公开心情动态异常: %s", _single_line(exc, 160), exc_info=True)
