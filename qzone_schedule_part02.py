# -*- coding: utf-8 -*-
"""QzoneSchedulePart02Mixin。

由 tools/split_mixin_domain.py 从 qzone_schedule.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 494 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 QzoneScheduleMixin）。
"""
from __future__ import annotations

from .qzone_schedule_shared import _persona_provider_id, _qzone_compat_constant, logger
from .qzone_schedule_shared import Any
from .qzone_schedule_shared import PromptRenderMode
from .qzone_schedule_shared import _day_start_ts
from .qzone_schedule_shared import _now_ts
from .qzone_schedule_shared import _safe_float
from .qzone_schedule_shared import _safe_int
from .qzone_schedule_shared import _single_line
from .qzone_schedule_shared import _today_key
from .qzone_schedule_shared import ngram_shared_count
from .qzone_schedule_shared import prompt_section
from .qzone_schedule_shared import re
from .qzone_schedule_shared import render_prompt_sections
from .qzone_schedule_shared import runtime_persona_setting
from .qzone_schedule_shared import text_length_ok
from .qzone_schedule_shared import time



class QzoneSchedulePart02Mixin:
    """QzoneSchedulePart02Mixin（从 QzoneScheduleMixin 拆出）。"""


    @staticmethod
    def _qzone_life_publish_next_planned_at(plan: dict[str, Any]) -> float:
        """Earliest still-pending moment, for status display."""
        items = plan.get("items") if isinstance(plan, dict) else None
        if not isinstance(items, list):
            return 0.0
        pending = [
            _safe_float(item.get("planned_at"), 0)
            for item in items
            if isinstance(item, dict) and item.get("status") == "planned"
        ]
        pending = [value for value in pending if value > 0]
        return min(pending) if pending else 0.0

    def _qzone_plan_item_finish(
        self,
        plan: dict[str, Any] | None,
        item: dict[str, Any] | None,
        status: str,
        *,
        now: float,
    ) -> None:
        """Move a plan item to a terminal state so the slot never fires twice.

        Consuming the item is what fixes the old single-planned_at bug, where a
        finished slot stayed eligible and fired again once the cooldown lapsed.
        """
        if not isinstance(item, dict):
            return
        item["status"] = status
        item["finished_at"] = now
        if not isinstance(plan, dict):
            return
        if status == "published":
            plan["published_count"] = _safe_int(plan.get("published_count"), 0, 0) + 1
            key = _single_line(item.get("schedule_key"), 80)
            if key:
                used = plan.get("used_schedule_keys")
                if not isinstance(used, list):
                    used = []
                if key not in used:
                    used.append(key)
                plan["used_schedule_keys"] = used

    @classmethod
    def _qzone_text_length_ok(cls, text: Any, profile: Any) -> bool:
        return text_length_ok(text, profile_range=cls._qzone_length_profile_range(profile), hard_limit=int(_qzone_compat_constant("QZONE_LENGTH_HARD_LIMIT")))

    async def _qzone_life_publish_rewrite_to_length(
        self,
        text: str,
        profile: Any,
        *,
        prompt: str = "",
    ) -> str:
        """Ask once for a length-corrected rewrite, then re-run safety checks."""
        low, high = self._qzone_length_profile_range(profile)
        hard_limit = int(_qzone_compat_constant("QZONE_LENGTH_HARD_LIMIT"))
        instruction = f"""
下面这条 QQ 空间说说草稿字数不合要求。请在保留原意、语气和具体生活细节的前提下改写到 {low} 到 {high} 字。
只输出正文，不要解释，不要加标题，绝对不要超过 {hard_limit} 字。
""".strip()

        rewrite_prompt = "\n\n".join(
            (
                render_prompt_sections([prompt_section(key="qzone.length.instruction", title="说说字数改写", source="qzone_schedule", content=instruction)], mode=PromptRenderMode.BODY_ONLY),
                render_prompt_sections([prompt_section(key="qzone.length.draft", title="原草稿", source="qzone_schedule", content=text)], mode=PromptRenderMode.LABELED_BLOCK),
            )
        )
        try:
            rewritten = await self._llm_call(
                rewrite_prompt,
                max_tokens=180,
                provider_id=self._task_provider(
                    _persona_provider_id(
                        self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                    ),
                    _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
                ),
                task="qzone_publish_length",
            )
        except Exception as exc:
            logger.warning("QQ 空间说说字数重写失败: %s", _single_line(exc, 120))
            return ""
        # A rewrite bypasses the original sanitizer, so re-run it here.
        return await self._sanitize_qzone_life_post_text(rewritten, prompt=rewrite_prompt)

    @staticmethod
    def _qzone_ngram_shared_count(left: Any, right: Any, *, n: int = 3) -> int:
        return ngram_shared_count(left, right, n=n)

    def _qzone_life_publish_similar_recent(self, state: dict[str, Any], draft: Any) -> list[dict[str, Any]]:
        """Return recent posts whose shared 3-gram count with the draft meets the threshold."""
        items = state.get("recent_life_publish_texts") if isinstance(state, dict) else []
        if not isinstance(items, list):
            return []
        threshold = max(
            1,
            _safe_int(
                runtime_persona_setting(self, "qzone_life_publish_similarity_threshold", 2),
                2,
                1,
                20,
            ),
        )
        matches: list[dict[str, Any]] = []
        for item in items[-8:]:
            old = _single_line(item.get("text") if isinstance(item, dict) else item, 180)
            if not old:
                continue
            shared = self._qzone_ngram_shared_count(draft, old)
            if shared >= threshold:
                matches.append(
                    {
                        "text": old,
                        "shared": shared,
                        "at": _safe_float(item.get("at"), 0) if isinstance(item, dict) else 0,
                    }
                )
        return matches

    async def _qzone_life_publish_rewrite_deduplicated(
        self,
        text: str,
        similar: list[dict[str, Any]],
        *,
        prompt: str = "",
    ) -> str:
        recent_lines = "\n".join(f"- {_single_line(item['text'], 120)}" for item in similar[:3])
        instruction = """
下面是一条 QQ 空间说说草稿，和最近发过的说说太像（同一场景、同一叙事套路）。
请改写成一条内容上明显不同的生活说说：换一个场景、换一个观察角度、换一种情绪，不要沿用原来的骨架和用词。
只输出正文，30 到 120 字，不要解释，不要加标题。
""".strip()

        rewrite_prompt = "\n\n".join(
            (
                render_prompt_sections([prompt_section(key="qzone.deduplicate.instruction", title="说说去重改写", source="qzone_schedule", content=instruction)], mode=PromptRenderMode.BODY_ONLY),
                render_prompt_sections(
                    [
                        prompt_section(key="qzone.deduplicate.recent", title="最近已发的说说（避免重复）", source="qzone_schedule", content=recent_lines),
                        prompt_section(key="qzone.deduplicate.draft", title="原草稿", source="qzone_schedule", content=text),
                        prompt_section(key="qzone.deduplicate.context", title="原任务背景", source="qzone_schedule", content=_single_line(prompt, 600)),
                    ],
                    mode=PromptRenderMode.LABELED_BLOCK,
                ),
            )
        )
        try:
            rewritten = await self._llm_call(
                rewrite_prompt,
                max_tokens=180,
                provider_id=self._task_provider(
                    _persona_provider_id(
                        self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                    ),
                    _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
                ),
                task="qzone_publish_deduplicate",
            )
        except Exception as exc:
            logger.warning("QQ 空间说说去重重写失败: %s", _single_line(exc, 120))
            return ""
        # A rewrite bypasses the original sanitizer, so re-run it here.
        return await self._sanitize_qzone_life_post_text(rewritten, prompt=rewrite_prompt)

    async def _maybe_publish_qzone_life_post(self) -> None:
        if not self._qzone_automatic_persona_active():
            return
        async with self._qzone_operation_lock("life_publish"):
            await self._maybe_publish_qzone_life_post_locked()

    async def _maybe_publish_qzone_life_post_locked(self) -> None:
        if not (
            self._qzone_available()
            and runtime_persona_setting(self, "enable_qzone_life_publish", False)
        ):
            return
        now = _now_ts()
        state = self.data.setdefault("qzone_integration", {})
        if not isinstance(state, dict):
            self.data["qzone_integration"] = {}
            state = self.data["qzone_integration"]
        existing_plan = state.get("life_publish_daily_plan")
        signature = self._qzone_life_publish_plan_signature()
        plan_is_new = not (
            isinstance(existing_plan, dict)
            and _single_line(existing_plan.get("date"), 24) == _today_key()
            and _single_line(existing_plan.get("config_signature"), 40) == signature
        )
        daily_plan = self.data.get("daily_plan") if isinstance(self.data, dict) else None
        if plan_is_new and not (
            isinstance(daily_plan, dict) and _single_line(daily_plan.get("date"), 24) == _today_key()
        ):
            ensure_daily_plan = getattr(self, "_ensure_daily_plan", None)
            if callable(ensure_daily_plan):
                try:
                    await ensure_daily_plan()
                except Exception as exc:
                    logger.warning("QQ 空间建计划前确保今日日程失败，继续使用当下状态: %s", _single_line(exc, 120))
        plan = self._qzone_life_publish_daily_plan(state, now=now)
        plan_changed = self._qzone_backfill_plan_schedule_labels(plan)
        # The cross-day gap is applied while building the first slot. Once that
        # plan exists, same-day posts use their own explicit spacing instead of
        # accidentally inheriting a 24-hour cross-day cooldown.
        last_publish_at = _safe_float(state.get("last_life_publish_at"), 0)
        if (
            last_publish_at >= _day_start_ts(now)
            and now - last_publish_at < self._qzone_intra_day_gap_seconds()
        ):
            return
        # A new plan writes an initial status once; existing plans are stable
        # across ticks and process restarts.
        if plan.get("skip_reason"):
            if plan_is_new:
                state["last_life_publish_status"] = f"skipped:{_single_line(plan.get('skip_reason'), 40)}"
                state["last_life_publish_checked_at"] = now
                self._save_data_sync(sections={"qzone_integration"})
            return
        plan_item = self._qzone_life_publish_due_item(plan, now=now)
        if plan_item is None:
            if plan_is_new or plan_changed:
                next_at = self._qzone_life_publish_next_planned_at(plan)
                target = _safe_int(plan.get("target_count"), 0, 0)
                state["last_life_publish_status"] = (
                    f"ready:planned@{time.strftime('%m-%d %H:%M', time.localtime(next_at))}x{target}"
                    if next_at > 0
                    else "ready:planned"
                )
                state["last_life_publish_checked_at"] = now
                self._save_data_sync(sections={"qzone_integration"})
            return
        if plan_item.get("night") and not self._qzone_night_publish_allowed():
            self._qzone_plan_item_finish(plan, plan_item, "cancelled", now=now)
            plan_item["failed_reason"] = "night_state_inactive"
            state["last_life_publish_status"] = "cancelled:night_state_inactive"
            state["last_life_publish_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            return
        reusable_text = self._qzone_reusable_draft(state, "life_publish", now=now)
        block_reason = self._qzone_auto_publish_block_reason(state, now=now)
        if block_reason:
            state["last_life_publish_status"] = f"paused:auth:{_single_line(block_reason, 80)}"
            state["last_life_publish_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            return
        if now - _safe_float(state.get("last_life_publish_failed_at"), 0) < 15 * 60:
            return
        length_profile = "medium"
        schedule_label = ""
        if isinstance(plan_item, dict):
            length_profile = _single_line(plan_item.get("length_profile"), 16) or "medium"
            candidate_eligibility = _single_line(plan_item.get("schedule_fact_eligibility"), 40).lower()
            candidate_valid_until = _safe_float(plan_item.get("schedule_valid_until"), 0.0)
            schedule_label = (
                _single_line(plan_item.get("schedule_label"), 160)
                if candidate_eligibility in {"current_internal", "current_observed"}
                and candidate_valid_until > now
                else ""
            )
            # The item stays "planned" until it reaches a terminal state, so an
            # early return below simply retries on a later tick instead of
            # stranding it. The attempt counter is what stops an endless loop.
            attempts = _safe_int(plan_item.get("attempts"), 0, 0, 99) + 1
            plan_item["attempts"] = attempts
            if attempts > int(
                _qzone_compat_constant("QZONE_PLAN_ITEM_MAX_ATTEMPTS")
            ):
                plan_item["status"] = "failed"
                plan_item["failed_reason"] = "max_attempts"
                self._qzone_clear_pending_publish_assets(state, "life_publish")
                state["last_life_publish_status"] = "cancelled:max_attempts"
                state["last_life_publish_checked_at"] = now
                self._save_data_sync(sections={"qzone_integration"})
                return
        preflight_error = await self._qzone_preflight_auto_publish(None, state=state, source="life_publish")
        if preflight_error:
            state["last_life_publish_failed_at"] = now
            state["last_life_publish_status"] = f"paused:auth:{_single_line(preflight_error, 80)}"
            state["last_life_publish_checked_at"] = now
            self._save_data_sync(sections={"qzone_integration"})
            return
        daily_state = self.data.get("daily_state", {})
        current_item = self._qzone_current_agenda_item()
        diary_context = self._recent_diary_context(count=2)
        theme_hint = self._qzone_publish_theme_hint()
        temporal_context = self._qzone_temporal_context()
        recent_publish_context = self._qzone_recent_publish_context(state)
        memory_context = await self._qzone_memory_companion_context(
            purpose="publish",
            query="QQ空间生活说说 今日公开可写生活 当前日程 今日穿搭 最近吃饭 日记余味 自我时间线",
        )
        public_state_hint = self._qzone_relationship_safe_source(
            self._qzone_public_state_hint(daily_state if isinstance(daily_state, dict) else {}),
            source="qzone.publish.current_state",
        )
        current_schedule_hint = self._qzone_relationship_safe_source(
            self._format_plan_item_for_prompt(current_item),
            source="qzone.publish.current_schedule",
        )
        diary_context = self._qzone_relationship_safe_source(
            diary_context,
            source="qzone.publish.recent_diary",
        )
        memory_context = self._qzone_relationship_safe_source(
            memory_context,
            source="qzone.publish.memory",
        )
        relationship_authority_guard = self._qzone_relationship_authority_guard()
        if reusable_text:
            text = reusable_text
            logger.info(
                "QQ 空间复用待发布生活说说草稿: age=%ds",
                int(now - _safe_float(state.get("last_life_publish_draft_at"), now)),
            )
        else:
            length_min, length_max = self._qzone_length_profile_range(length_profile)
            hard_limit = int(_qzone_compat_constant("QZONE_LENGTH_HARD_LIMIT"))
            length_rule = f"- {length_min} 到 {length_max} 字，最多不超过 {hard_limit} 字。"
            special_sections = []
            if schedule_label:
                special_sections.append(
                    prompt_section(
                        key="qzone.publish.schedule_anchor",
                        title="本条要写的生活片段",
                        source="qzone_schedule",
                        content=f"{schedule_label}\n只围绕这一个片段写，不要复述整天日程，也不要写成行程汇报。",
                    )
                )
            if isinstance(plan_item, dict) and plan_item.get("night"):
                special_sections.append(
                    prompt_section(
                        key="qzone.publish.night_state",
                        title="夜间状态",
                        source="qzone_schedule",
                        content="现在是失眠或浅睡的深夜，只写一句很短、低刺激的碎碎念，不要显得精神饱满。",
                    )
                )
            instruction = f"""
请以当前 Bot 人格写一条 QQ 空间说说。
只输出说说正文,不要解释,不要加标题。

要求：
{length_rule}
- 像自然生活动态,不是公告、不是任务汇报。
- 开头直接进入一个具体画面或动作,不要总结式开场。
- 可以带一点公开可见的心情、天气或日记余味,但不要暴露插件、模型、内部状态数值。
- 禁止出现“能量”“心理能量”“/100”“状态变量”“当前状态”等内部汇报词。
- 不要 @ 用户,不要泄露私聊内容,不要写得像营销文。
- 写作角度：{theme_hint}""".strip()
            instruction_with_special = instruction
            if special_sections:
                instruction_with_special += "\n" + render_prompt_sections(
                    special_sections,
                    mode=PromptRenderMode.LABELED_BLOCK,
                )
            prompt = "\n\n".join(
                part for part in (
                    render_prompt_sections([prompt_section(key="qzone.publish.instruction", title="QQ 空间说说生成", source="qzone_schedule", content=instruction_with_special)], mode=PromptRenderMode.BODY_ONLY),
                    render_prompt_sections(
                        [
                            prompt_section(key="qzone.publish.style", title="说说风格提示", source="qzone_schedule", content=self._qzone_publish_style_prompt()),
                            prompt_section(key="qzone.publish.time", title="当前时间与季节", source="qzone_schedule", content=temporal_context),
                            prompt_section(key="qzone.publish.state", title="公开可写的状态余味", source="qzone_schedule", content=public_state_hint),
                            prompt_section(key="qzone.publish.schedule", title="当前/附近日程", source="qzone_schedule", content=current_schedule_hint or "无明确日程"),
                            prompt_section(key="qzone.publish.diary", title="近日私密日记余味", source="qzone_schedule", content=diary_context or "暂无"),
                            prompt_section(key="qzone.publish.memory", title="我会牢牢记住你 公开可写生活参考", source="qzone_schedule", content=(memory_context or "暂无") + "\n使用方式：只选公开可写、不会泄露私聊或内部记忆来源的生活连续性。"),
                            prompt_section(key="qzone.publish.recent", title="最近说说去重", source="qzone_schedule", content=recent_publish_context or "暂无最近记录。"),
                        ],
                        mode=PromptRenderMode.LABELED_BLOCK,
                    ),
                    relationship_authority_guard,
                    self._format_worldview_adaptation_prompt(),
                ) if part
            )
            text = await self._llm_call(
                prompt,
                max_tokens=180,
                provider_id=self._task_provider(
                    _persona_provider_id(
                        self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                    ),
                    _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
                ),
                task="qzone_publish",
            )
            text = await self._sanitize_qzone_life_post_text(text, prompt=prompt)
            if not text:
                state["last_life_publish_failed_at"] = now
                state["last_life_publish_status"] = "cancelled:empty_or_unsafe_draft"
                state["last_life_publish_checked_at"] = now
                self._qzone_plan_item_finish(plan, plan_item, "cancelled", now=now)
                self._save_data_sync(sections={"qzone_integration"})
                logger.warning("QQ 空间生活动态草稿为空或不安全,已跳过发布")
                return
            if not self._qzone_text_length_ok(text, length_profile):
                relengthed = await self._qzone_life_publish_rewrite_to_length(
                    text,
                    length_profile,
                    prompt=prompt,
                )
                if relengthed and self._qzone_text_length_ok(relengthed, length_profile):
                    text = relengthed
                else:
                    state["last_life_publish_failed_at"] = now
                    state["last_life_publish_status"] = "cancelled:length"
                    state["last_life_publish_checked_at"] = now
                    self._qzone_plan_item_finish(plan, plan_item, "cancelled", now=now)
                    self._save_data_sync(sections={"qzone_integration"})
                    logger.info(
                        "QQ 空间说说字数不合要求且重写失败,已取消: profile=%s len=%s",
                        length_profile,
                        len(re.sub(r"\s+", "", text or "")),
                    )
                    return
            state["last_life_publish_draft"] = _single_line(text, 300)
            state["last_life_publish_draft_at"] = now
        similar = self._qzone_life_publish_similar_recent(state, text)
        if similar:
            if reusable_text:
                state["last_life_publish_failed_at"] = now
                state["last_life_publish_status"] = "cancelled:duplicate"
                state["last_life_publish_checked_at"] = now
                self._qzone_clear_pending_publish_assets(state, "life_publish")
                self._qzone_plan_item_finish(plan, plan_item, "cancelled", now=now)
                self._save_data_sync(sections={"qzone_integration"})
                logger.info("QQ 空间复用草稿与近期说说重复,已取消发布")
                return
            rewritten = await self._qzone_life_publish_rewrite_deduplicated(
                text,
                similar,
                prompt=prompt,
            )
            if (
                rewritten
                and self._qzone_text_length_ok(rewritten, length_profile)
                and not self._qzone_life_publish_similar_recent(state, rewritten)
            ):
                text = rewritten
                state["last_life_publish_draft"] = _single_line(text, 300)
                state["last_life_publish_draft_at"] = now
                logger.info("QQ 空间草稿与近期说说重复,已重写避开: %s", _single_line(text, 120))
            else:
                state["last_life_publish_failed_at"] = now
                state["last_life_publish_status"] = "cancelled:duplicate_after_retry"
                state["last_life_publish_checked_at"] = now
                self._qzone_plan_item_finish(plan, plan_item, "cancelled", now=now)
                self._save_data_sync(sections={"qzone_integration"})
                logger.info("QQ 空间草稿重写后仍与近期说说重复,已取消发布")
                return
        if reusable_text:
            image_sources = self._qzone_reusable_generated_image(state, "life_publish", text, now=now)
        else:
            image_sources = await self._maybe_generate_qzone_publish_image(
                post_text=text,
                reason="life_publish",
                daily_state=daily_state if isinstance(daily_state, dict) else {},
                current_item=current_item,
                diary_context=diary_context,
                state=state,
            )
        result = await self._publish_qzone_text(text, images=image_sources, publish_reason="life_publish")
        if result.get("success"):
            state["last_life_publish_at"] = now
            state.pop("last_life_publish_failed_at", None)
            state["last_life_publish_status"] = "published"
            if result.get("image_fallback"):
                self._qzone_note_publish_image_status(
                    state,
                    "life_publish",
                    "failed:upload_fallback",
                    result.get("image_fallback_message") or "配图发布失败，已降级纯文字发布",
                )
                state["last_life_publish_image_fallback"] = {
                    "stage": _single_line(result.get("image_fallback_stage"), 40),
                    "message": _single_line(result.get("image_fallback_message"), 180),
                    "at": now,
                }
            else:
                state.pop("last_life_publish_image_fallback", None)
            self._qzone_clear_pending_publish_assets(state, "life_publish")
            self._qzone_plan_item_finish(plan, plan_item, "published", now=now)
        else:
            state["last_life_publish_failed_at"] = now
            if result.get("delivery_unknown"):
                state["last_life_publish_status"] = f"delivery_unknown:{_single_line(result.get('message'), 80)}"
                self._qzone_plan_item_finish(plan, plan_item, "delivery_unknown", now=now)
                self._qzone_clear_pending_publish_assets(state, "life_publish")
            else:
                state["last_life_publish_status"] = f"failed:{_single_line(result.get('message'), 80)}"
            # Keep confirmed failures retryable until the attempt budget is exhausted.
            if not result.get("delivery_unknown") and (
                isinstance(plan_item, dict)
                and _safe_int(plan_item.get("attempts"), 0, 0, 99)
                >= int(_qzone_compat_constant("QZONE_PLAN_ITEM_MAX_ATTEMPTS"))
            ):
                self._qzone_plan_item_finish(plan, plan_item, "failed", now=now)
                self._qzone_clear_pending_publish_assets(state, "life_publish")
        state["last_life_publish_checked_at"] = now
        state["last_life_publish_text"] = _single_line(result.get("text") or text, 180)
        state["last_life_publish_images"] = _safe_int(result.get("image_count"), len(result.get("images") or []), 0, 99) if result.get("success") else 0
        self._save_data_sync(sections={"qzone_integration"})
