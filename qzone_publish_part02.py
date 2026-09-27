# -*- coding: utf-8 -*-
"""QzonePublishPart02Mixin。

由 tools/split_mixin_domain.py 从 qzone_publish.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 437 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 QzonePublishMixin）。
"""
from __future__ import annotations

from .qzone_publish_shared import logger
from .qzone_publish_shared import Any
from .qzone_publish_shared import AstrMessageEvent
from .qzone_publish_shared import Path
from .qzone_publish_shared import PromptRenderMode
from .qzone_publish_shared import _now_ts
from .qzone_publish_shared import _path_text
from .qzone_publish_shared import _safe_float
from .qzone_publish_shared import _single_line
from .qzone_publish_shared import json
from .qzone_publish_shared import prompt_section
from .qzone_publish_shared import re
from .qzone_publish_shared import render_prompt_sections
from .qzone_publish_shared import runtime_persona_setting



class QzonePublishPart02Mixin:
    """QzonePublishPart02Mixin（从 QzonePublishMixin 拆出）。"""


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
        instruction = f"""
请以当前 Bot 人格写一条 QQ 空间说说。
只输出说说正文,不要解释,不要加标题。

要求：
- 30 到 120 字。
- 像自然生活动态,不是公告、不是任务汇报。
- 可以带一点公开可见的心情、天气或日记余味,但不要暴露插件、模型、内部状态数值。
- 禁止出现“能量”“心理能量”“/100”“状态变量”“当前状态”等内部汇报词。
- 不要 @ 用户,不要泄露私聊内容,不要写得像营销文。
- 写作角度：{theme_hint}
""".strip()

        prompt = "\n\n".join(
            part for part in (
                render_prompt_sections([prompt_section(key="qzone.publish_test.instruction", title="QQ 空间说说测试", source="qzone_publish", content=instruction)], mode=PromptRenderMode.BODY_ONLY),
                render_prompt_sections(
                    [
                        prompt_section(key="qzone.publish_test.style", title="说说风格提示", source="qzone_publish", content=self._qzone_publish_style_prompt()),
                        prompt_section(key="qzone.publish_test.time", title="当前时间与季节", source="qzone_publish", content=temporal_context),
                        prompt_section(key="qzone.publish_test.state", title="公开可写的状态余味", source="qzone_publish", content=public_state_hint),
                        prompt_section(key="qzone.publish_test.schedule", title="当前/附近日程", source="qzone_publish", content=current_schedule_hint or "无明确日程"),
                        prompt_section(key="qzone.publish_test.diary", title="近日私密日记余味", source="qzone_publish", content=diary_context or "暂无"),
                        prompt_section(key="qzone.publish_test.memory", title="我会牢牢记住你 公开可写生活参考", source="qzone_publish", content=(memory_context or "暂无") + "\n使用方式：只选公开可写、不会泄露私聊或内部记忆来源的生活连续性。"),
                        prompt_section(key="qzone.publish_test.recent", title="最近说说去重", source="qzone_publish", content=recent_publish_context or "暂无最近记录。"),
                    ],
                    mode=PromptRenderMode.LABELED_BLOCK,
                ),
                relationship_authority_guard,
                self._format_worldview_adaptation_prompt(),
            ) if part
        )
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
        instruction = f"""
请以当前 Bot 人格写一条 QQ 空间说说，用来测试配图生成。
只输出说说正文,不要解释,不要加标题。

要求：
- 30 到 100 字。
- 像自然生活动态,最好包含一个能被画出来的具体场景或物件。
- 不要 @ 用户,不要泄露私聊内容,不要出现插件、模型、系统提示、内部状态数值。
- 写作角度：{self._qzone_publish_theme_hint()}
""".strip()

        prompt = "\n\n".join(
            part for part in (
                render_prompt_sections([prompt_section(key="qzone.image_test.instruction", title="QQ 空间配图测试", source="qzone_publish", content=instruction)], mode=PromptRenderMode.BODY_ONLY),
                render_prompt_sections(
                    [
                        prompt_section(key="qzone.image_test.style", title="说说风格提示", source="qzone_publish", content=self._qzone_publish_style_prompt()),
                        prompt_section(key="qzone.image_test.time", title="当前时间与季节", source="qzone_publish", content=self._qzone_temporal_context()),
                        prompt_section(key="qzone.image_test.state", title="公开可写的状态余味", source="qzone_publish", content=public_state_hint),
                        prompt_section(key="qzone.image_test.schedule", title="当前/附近日程", source="qzone_publish", content=current_schedule_hint or "无明确日程"),
                        prompt_section(key="qzone.image_test.diary", title="近日私密日记余味", source="qzone_publish", content=diary_context or "暂无"),
                        prompt_section(key="qzone.image_test.recent", title="最近说说去重", source="qzone_publish", content=self._qzone_recent_publish_context(state) or "暂无最近记录。"),
                    ],
                    mode=PromptRenderMode.LABELED_BLOCK,
                ),
                relationship_authority_guard,
                self._format_worldview_adaptation_prompt(),
            ) if part
        )
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
        logger.info("QQ 空间复用待发布配图: reason=%s path=%s", reason, _single_line(image_path, 160))
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
