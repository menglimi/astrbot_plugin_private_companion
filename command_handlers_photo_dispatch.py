# -*- coding: utf-8 -*-
"""出图命令派发域。

由 tools/split_mixin_domain.py 从 command_handlers.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 714 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CommandHandlersMixin）。
"""
from __future__ import annotations

import re
from .command_handlers_shared import logger
from .helpers import _missing_optional_model_dependency, _photo_group_request_matches, _single_line
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from pathlib import Path
from typing import Any



class CommandHandlersPhotoDispatchMixin:
    """出图命令派发域（从 CommandHandlersMixin 拆出）。"""


    def _natural_language_photo_ack_text(self, *, kind: str, has_reference: bool) -> str:
        return "等我一下。"

    def _natural_language_photo_done_text(self, *, kind: str, reference_label: str = "") -> str:
        return "好了，你看。"

    def _natural_language_photo_ack_reference(self, *, kind: str, has_reference: bool) -> str:
        if kind == "edit" or has_reference:
            return "参考意图：已经拿到参考图，开始按用户要求改图；让用户稍等，不要描述工具执行过程。"
        if kind == "selfie":
            return "参考意图：用户要角色自拍，先轻轻回应会去准备；让用户稍等，不要承诺额外内容。"
        return "参考意图：用户要生成图片，先轻轻回应会去画；让用户稍等，不要描述工具执行过程。"

    def _natural_language_photo_done_reference(self, *, kind: str, reference_label: str = "") -> str:
        if kind == "edit":
            label = _single_line(reference_label, 24) or "这张图"
            return f"参考意图：图片已按{label}改好，提醒用户看图；语气自然短一点。"
        if kind == "selfie":
            return "参考意图：自拍图片已经完成，提醒用户看图；语气自然短一点。"
        return "参考意图：图片已经完成，提醒用户看图；语气自然短一点。"

    async def _natural_language_photo_ack_reply_text(
        self,
        event: AstrMessageEvent,
        user: dict[str, Any],
        *,
        kind: str,
        has_reference: bool,
    ) -> str:
        rewriter = getattr(self, "_rewrite_reference_reply_with_persona", None)
        if callable(rewriter):
            text = await rewriter(
                self._natural_language_photo_ack_reference(kind=kind, has_reference=has_reference),
                scene="规则快判生图/改图已接单，生成前短回执",
                user=user,
                event=event,
                fallback_text="等我一下。",
                task="natural_photo_ack_rewrite",
                max_chars=60,
                allow_fallback=True,
                preserve_status=True,
            )
            if text:
                return text
        return "等我一下。"

    async def _natural_language_photo_done_reply_text(
        self,
        event: AstrMessageEvent,
        user: dict[str, Any],
        *,
        kind: str,
        reference_label: str = "",
    ) -> str:
        rewriter = getattr(self, "_rewrite_reference_reply_with_persona", None)
        if callable(rewriter):
            text = await rewriter(
                self._natural_language_photo_done_reference(kind=kind, reference_label=reference_label),
                scene="规则快判生图/改图已完成，随图短标题",
                user=user,
                event=event,
                fallback_text="好了，你看。",
                task="natural_photo_done_rewrite",
                max_chars=60,
                allow_fallback=True,
                preserve_status=True,
            )
            if text:
                return text
        return "好了，你看。"

    async def _maybe_handle_natural_language_photo_request(
        self,
        event: AstrMessageEvent,
        user_id: str,
        text: str,
        *,
        directed: bool = False,
    ) -> bool:
        text = _single_line(text, 800)
        if not text or text.startswith(("陪伴", "/陪伴", "私聊陪伴", "主动陪伴")):
            return False
        explicit_plugin_request = self._natural_language_photo_explicit_plugin_request(text)
        if not runtime_persona_setting(self, 'enable_photo_text_action', False):
            if explicit_plugin_request:
                await self._reply(event, self._natural_language_photo_disabled_text("photo_off"))
                event.stop_event()
                return True
            return False
        if not runtime_persona_setting(
            self,
            "enable_user_requested_photo_generation",
            True,
        ):
            if explicit_plugin_request:
                await self._reply(event, "管理员已关闭用户请求生图/改图。")
                event.stop_event()
                return True
            return False
        mode = _single_line(runtime_persona_setting(self, 'natural_language_photo_generation_mode', "tool_first"), 40).lower()
        if mode not in {"tool_first", "rule_fast", "off"}:
            mode = "tool_first"
        if mode in {"tool_first", "off"}:
            if explicit_plugin_request:
                logger.info(
                    "非指令生图交给主链工具处理: mode=%s user=%s text=%s",
                    mode,
                    _single_line(user_id, 40),
                    _single_line(text, 160),
                )
            return False
        if not runtime_persona_setting(self, 'enable_natural_language_photo_generation', False):
            if explicit_plugin_request:
                await self._reply(event, self._natural_language_photo_disabled_text("natural_off"))
                event.stop_event()
                return True
            return False
        try:
            safe_has_image = getattr(self, "_private_event_has_image_safe", None)
            if callable(safe_has_image):
                has_reference = bool(safe_has_image(event, label="natural_photo_intent"))
            else:
                has_reference_checker = getattr(self, "_private_event_has_image", None)
                has_reference = bool(has_reference_checker(event) if callable(has_reference_checker) else False)
            has_reference = has_reference or bool(self._photo_reference_sources_from_reply_cache(event))
            if not has_reference:
                has_reference = bool(await self._photo_reference_sources_from_reply_event(event))
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "自然语言生图参考图检测缺少可选模型依赖，已按无参考图继续: module=%s err=%s",
                missing,
                _single_line(exc, 160),
            )
            has_reference = False
        intent = self._natural_language_photo_intent(text, has_reference=has_reference, directed=directed)
        if not intent:
            if directed:
                logger.info(
                    "定向自然语言生图未命中意图: user=%s has_reference=%s text=%s",
                    _single_line(user_id, 40),
                    has_reference,
                    _single_line(text, 180),
                )
            return False
        group_photo_requested = _photo_group_request_matches(
            intent.get("prompt") or text
        )
        if group_photo_requested and intent.get("kind") != "edit":
            intent["kind"] = "selfie"
        logger.info(
            "自然语言生图命中: user=%s kind=%s has_reference=%s prompt=%s raw=%s",
            _single_line(user_id, 40),
            _single_line(intent.get("kind"), 30),
            has_reference,
            _single_line(intent.get("prompt"), 180),
            _single_line(text, 180),
        )
        if intent.get("needs_prompt"):
            await self._reply(event, "要画成什么样？给我一句具体点的描述就行。")
            event.stop_event()
            return True
        scope_checker = getattr(self, "_photo_generation_scope_allowed", None)
        photo_scope = ""
        async with self._data_lock:
            user = self._get_user(user_id)
            scope_getter = getattr(self, "_photo_generation_scope", None)
            if callable(scope_getter):
                photo_scope = scope_getter(event, user=user, user_id=user_id)
            scope_quota_getter = getattr(self, "_photo_generation_scope_quota_left", None)
            scope_left = (
                scope_quota_getter(
                    event,
                    user=user,
                    user_id=user_id,
                    scope=photo_scope,
                )
                if callable(scope_quota_getter)
                else None
            )
            scope_blocked = scope_left is not None and scope_left <= 0
            if not callable(scope_quota_getter) and callable(scope_checker):
                scope_blocked = not scope_checker(event, user=user, user_id=user_id)
            if scope_blocked:
                message_getter = getattr(self, "_photo_generation_scope_quota_block_message", None)
                message = (
                    message_getter(
                        event,
                        user=user,
                        user_id=user_id,
                        scope=photo_scope,
                    )
                    if callable(message_getter)
                    else "当前会话不允许生图/改图，或今天该范围的生图额度已经用完。"
                )
                await self._reply(event, message)
                event.stop_event()
                return True
            if self._natural_language_photo_quota_left(user) <= 0:
                await self._reply(event, "今天规则快判生图/改图额度用完了。")
                event.stop_event()
                return True
        if not self._photo_text_available():
            await self._reply(event, "现在没有可用的生图后端，先画不了。")
            event.stop_event()
            return True
        reference_path = ""
        reference_label = ""
        reference_kind = str(intent.get("kind") or "")
        reference_required = reference_kind == "edit" or group_photo_requested
        if reference_required or reference_kind == "selfie":
            try:
                reference_path, reference_label, saw_image = await self._photo_reference_image_from_command_context(event, user_id)
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if not missing:
                    raise
                logger.warning(
                    "自然语言生图引用来源解析缺少可选模型依赖: module=%s err=%s",
                    missing,
                    _single_line(exc, 160),
                )
                reference_usage = (
                    "合影"
                    if group_photo_requested
                    else ("改图" if reference_kind == "edit" else "自拍")
                )
                await self._reply(
                    event,
                    f"{reference_usage}参考图解析缺少可选依赖 {missing}，这次先不生成。",
                )
                event.stop_event()
                return True
            logger.info(
                "自然语言生图引用来源解析: user=%s kind=%s saw_image=%s label=%s path=%s exists=%s",
                _single_line(user_id, 40),
                _single_line(reference_kind, 30),
                saw_image,
                _single_line(reference_label, 40),
                _single_line(reference_path, 180),
                bool(reference_path and Path(reference_path).exists()),
            )
            if not reference_path and (reference_required or saw_image):
                await self._reply(
                    event,
                    (
                        "合影需要人物参考图。请把合影原图或包含相关人物的参考图和要求一起发，或引用图片后再说合影要求。"
                        if group_photo_requested
                        else "我没拿到要改的图。可以把图片和要求一起发，或者引用一张近期图片再说“改成……”。"
                    )
                    if not saw_image
                    else "看到了图片，但没能保存成可用参考图，暂时无法生成。",
                )
                event.stop_event()
                return True
        memory_context = ""
        memory_getter = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(memory_getter):
            try:
                memory_context = await memory_getter(
                    kind="natural_photo",
                    query=(
                        f"自然语言生图 {intent.get('kind') or ''} {intent.get('prompt') or ''} "
                        "今日穿搭 当前地点 当前日程 最近自拍 用户偏好 衣服颜色"
                    ),
                    event=event,
                    user_id=user_id,
                    top_k=5,
                    max_chars=760,
                    timeout_seconds=1.5,
                )
            except Exception:
                memory_context = ""
        prompt_sections = self._build_natural_language_photo_prompt_sections(
            prompt=str(intent.get("prompt") or ""),
            kind=str(intent.get("kind") or "text2img"),
            has_reference=bool(reference_path),
            memory_context=memory_context,
        )
        prompt_text = str(intent.get("prompt") or "")
        intent_kind = str(intent.get("kind") or "text2img")
        workflow_kind = self._photo_generation_workflow_kind(intent_kind)
        ack_text = await self._natural_language_photo_ack_reply_text(
            event,
            user,
            kind=intent_kind,
            has_reference=bool(reference_path),
        )
        await self._reply(event, ack_text)
        generation_session_key = f"natural_photo_{user_id}"
        continuity_composer = getattr(self, "_compose_photo_continuity_key", None)
        continuity_key = (
            continuity_composer(getattr(event, "unified_msg_origin", ""), user_id)
            if callable(continuity_composer)
            else ""
        )
        try:
            backend_name, image_path, note = await self._generate_photo_image(
                workflow_kind=workflow_kind,
                prompt_text=prompt_text,
                request_text=str(intent.get("prompt") or ""),
                session_key=generation_session_key,
                continuity_key=continuity_key,
                requester_user_id=user_id,
                requester_is_private=bool(
                    (getattr(event, "is_private_chat", lambda: False)() if callable(getattr(event, "is_private_chat", None)) else getattr(event, "is_private_chat", False))
                ),
                reference_image_path=reference_path,
                prompt_sections=prompt_sections,
            )
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "自然语言生图后端缺少可选模型依赖: module=%s err=%s",
                missing,
                _single_line(exc, 160),
            )
            await self._reply(event, f"生图后端缺少可选依赖 {missing}，这次先不生成。")
            event.stop_event()
            return True
        logger.info(
            "自然语言生图结果: user=%s backend=%s ok=%s note=%s image=%s",
            _single_line(user_id, 40),
            _single_line(backend_name, 80),
            bool(image_path),
            _single_line(note, 180),
            _single_line(image_path, 180),
        )
        counted = bool(image_path)
        if not image_path and callable(getattr(self, "_photo_generation_failure_counts_as_attempt", None)):
            counted = bool(self._photo_generation_failure_counts_as_attempt(note))
        if counted:
            async with self._data_lock:
                user = self._get_user(user_id)
                self._note_natural_language_photo_generation_attempt(user, image_path=image_path)
                scope_notifier = getattr(self, "_note_photo_generation_scope_attempt", None)
                if callable(scope_notifier):
                    scope_notifier(
                        event,
                        user=user,
                        user_id=user_id,
                        scope=photo_scope,
                    )
                self._save_data_sync(sections={"users", "photo_generation_scope_attempts"})
        if not image_path:
            await self._reply(
                event,
                f"这次没生成出来：{_single_line(note, 160) or '后端没有返回图片'}"
                + ("\n这次已经计入规则快判生图额度，避免后端异常时反复请求。" if counted else ""),
            )
            event.stop_event()
            return True
        caption = await self._natural_language_photo_done_reply_text(
            event,
            user,
            kind=intent_kind,
            reference_label=reference_label,
        )
        metadata_getter = getattr(self, "_photo_generation_result_metadata", None)
        generation_metadata = (
            metadata_getter(image_path=image_path, session_key=generation_session_key)
            if callable(metadata_getter)
            else {}
        )
        fallback_payload = (
            generation_metadata.get("reference_fallback")
            if isinstance(generation_metadata, dict)
            else {}
        )
        fallback_message = _single_line(
            fallback_payload.get("message") if isinstance(fallback_payload, dict) else "",
            260,
        )
        if fallback_message:
            caption = f"{caption}\n{fallback_message}".strip()
        delivery = await self._deliver_generated_image_to_event(
            event,
            image_path=image_path,
            caption=caption,
        )
        annotator = getattr(self, "_annotate_recent_photo_generation", None)
        if callable(annotator):
            annotator(
                image_path=image_path,
                session_key=generation_session_key,
                trigger="natural_photo_rule",
                intent_kind=intent_kind,
                sent=bool(delivery.get("sent")),
                caption=caption,
                tool_name="natural_photo_rule",
            )
        if not delivery.get("sent"):
            await self._reply(event, _single_line(delivery.get("message"), 180) or "图片未能发送。")
        elif delivery.get("destination") == "private":
            await self._reply(event, "图片已私聊发送。")
        event.stop_event()
        return True

    async def _handle_companion_photo_command(
        self,
        event: AstrMessageEvent,
        user_id: str,
        action: str,
        value: str,
    ) -> bool:
        """Run the plugin image backend from an explicit /陪伴 command."""
        action_text = _single_line(action, 24)
        prompt = _single_line(value, 800).strip()
        action_kind_map = {
            "自拍": "selfie",
            "拍照": "selfie",
            "拍一张": "selfie",
            "改图": "edit",
            "修图": "edit",
            "重绘": "edit",
            "P图": "edit",
            "p图": "edit",
        }
        forced_kind = action_kind_map.get(action_text, "text2img")
        if not runtime_persona_setting(self, 'enable_photo_text_action', False):
            await self._reply(event, self._natural_language_photo_disabled_text("photo_off"))
            event.stop_event()
            return True
        if not runtime_persona_setting(
            self,
            "enable_user_requested_photo_generation",
            True,
        ):
            await self._reply(event, "管理员已关闭用户请求生图/改图。")
            event.stop_event()
            return True
        scope_checker = getattr(self, "_photo_generation_scope_allowed", None)
        try:
            safe_has_image = getattr(self, "_private_event_has_image_safe", None)
            if callable(safe_has_image):
                has_reference = bool(safe_has_image(event, label="natural_photo_quota"))
            else:
                has_reference_checker = getattr(self, "_private_event_has_image", None)
                has_reference = bool(has_reference_checker(event) if callable(has_reference_checker) else False)
            has_reference = has_reference or bool(self._photo_reference_sources_from_reply_cache(event))
            if not has_reference:
                has_reference = bool(await self._photo_reference_sources_from_reply_event(event))
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "指令生图参考图检测缺少可选模型依赖，已按无参考图继续: module=%s err=%s",
                missing,
                _single_line(exc, 160),
            )
            has_reference = False

        compact = re.sub(r"\s+", "", prompt)
        group_photo_requested = _photo_group_request_matches(prompt)
        if forced_kind == "text2img":
            if group_photo_requested or any(marker in compact for marker in ("自拍", "拍照", "拍张照", "拍一张照", "来张自拍", "发张自拍")):
                forced_kind = "selfie"
            elif has_reference and any(marker in compact for marker in ("改图", "修图", "重绘", "p图", "P图", "改成", "改为", "换成", "变成", "加上", "去掉", "去除")):
                forced_kind = "edit"

        if forced_kind == "selfie" and not prompt:
            prompt = "拍一张自拍"
        if not prompt:
            usage = (
                "请这样使用：\n"
                "陪伴 生图 <画面描述>\n"
                "陪伴 自拍 [画面要求]\n"
                "陪伴 改图 <修改要求>（需要带图或回复图片）"
            )
            await self._reply(event, usage)
            event.stop_event()
            return True

        photo_scope = ""
        async with self._data_lock:
            user = self._get_user(user_id)
            scope_getter = getattr(self, "_photo_generation_scope", None)
            if callable(scope_getter):
                photo_scope = scope_getter(event, user=user, user_id=user_id)
            scope_quota_getter = getattr(self, "_photo_generation_scope_quota_left", None)
            scope_left = (
                scope_quota_getter(
                    event,
                    user=user,
                    user_id=user_id,
                    scope=photo_scope,
                )
                if callable(scope_quota_getter)
                else None
            )
            scope_blocked = scope_left is not None and scope_left <= 0
            if not callable(scope_quota_getter) and callable(scope_checker):
                scope_blocked = not scope_checker(event, user=user, user_id=user_id)
            if scope_blocked:
                message_getter = getattr(self, "_photo_generation_scope_quota_block_message", None)
                message = (
                    message_getter(
                        event,
                        user=user,
                        user_id=user_id,
                        scope=photo_scope,
                    )
                    if callable(message_getter)
                    else "当前会话不允许生图/改图，或今天该范围的生图额度已经用完。"
                )
                await self._reply(event, message)
                event.stop_event()
                return True
            quota_left = self._command_photo_quota_left(user)
            if quota_left is not None and quota_left <= 0:
                await self._reply(event, self._command_photo_quota_block_message())
                event.stop_event()
                return True

        if not self._photo_text_available():
            await self._reply(event, "现在没有可用的生图后端，先画不了。")
            event.stop_event()
            return True

        reference_path = ""
        reference_label = ""
        reference_required = forced_kind == "edit" or group_photo_requested
        if reference_required or forced_kind == "selfie":
            try:
                reference_path, reference_label, saw_image = await self._photo_reference_image_from_command_context(event, user_id)
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if not missing:
                    raise
                logger.warning(
                    "指令生图引用来源解析缺少可选模型依赖: module=%s err=%s",
                    missing,
                    _single_line(exc, 160),
                )
                reference_usage = (
                    "合影"
                    if group_photo_requested
                    else ("改图" if forced_kind == "edit" else "自拍")
                )
                await self._reply(
                    event,
                    f"{reference_usage}参考图解析缺少可选依赖 {missing}，这次先不生成。",
                )
                event.stop_event()
                return True
            logger.info(
                "指令生图引用来源解析: user=%s kind=%s saw_image=%s label=%s path=%s exists=%s",
                _single_line(user_id, 40),
                _single_line(forced_kind, 30),
                saw_image,
                _single_line(reference_label, 40),
                _single_line(reference_path, 180),
                bool(reference_path and Path(reference_path).exists()),
            )
            if not reference_path and (reference_required or saw_image):
                await self._reply(
                    event,
                    (
                        "合影需要人物参考图。请把合影原图或包含相关人物的参考图和指令一起发，或引用图片后再试。"
                        if group_photo_requested
                        else "我没拿到要改的图。可以把图片和“陪伴 改图 <要求>”一起发，或者引用近期图片再用这个指令。"
                    )
                    if not saw_image
                    else "看到了图片，但没能保存成可用参考图，暂时无法生成。",
                )
                event.stop_event()
                return True

        memory_context = ""
        memory_getter = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(memory_getter):
            try:
                memory_context = await memory_getter(
                    kind="command_photo",
                    query=(
                        f"指令生图 {forced_kind} {prompt} "
                        "今日穿搭 当前地点 当前日程 最近自拍 用户偏好 衣服颜色"
                    ),
                    event=event,
                    user_id=user_id,
                    top_k=5,
                    max_chars=760,
                    timeout_seconds=1.5,
                )
            except Exception:
                memory_context = ""

        prompt_sections = self._build_natural_language_photo_prompt_sections(
            prompt=prompt,
            kind=forced_kind,
            has_reference=bool(reference_path),
            memory_context=memory_context,
        )
        prompt_text = prompt
        workflow_kind = self._photo_generation_workflow_kind(forced_kind)
        async with self._data_lock:
            user = self._get_user(user_id)
            user_snapshot = dict(user)
        ack_text = await self._natural_language_photo_ack_reply_text(
            event,
            user_snapshot,
            kind=forced_kind,
            has_reference=bool(reference_path),
        )
        await self._reply(event, ack_text)
        generation_session_key = f"command_photo_{user_id}"
        continuity_composer = getattr(self, "_compose_photo_continuity_key", None)
        continuity_key = (
            continuity_composer(getattr(event, "unified_msg_origin", ""), user_id)
            if callable(continuity_composer)
            else ""
        )
        try:
            backend_name, image_path, note = await self._generate_photo_image(
                workflow_kind=workflow_kind,
                prompt_text=prompt_text,
                request_text=prompt,
                session_key=generation_session_key,
                continuity_key=continuity_key,
                requester_user_id=user_id,
                requester_is_private=bool(
                    (getattr(event, "is_private_chat", lambda: False)() if callable(getattr(event, "is_private_chat", None)) else getattr(event, "is_private_chat", False))
                ),
                reference_image_path=reference_path,
                prompt_sections=prompt_sections,
            )
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "指令生图后端缺少可选模型依赖: module=%s err=%s",
                missing,
                _single_line(exc, 160),
            )
            await self._reply(event, f"生图后端缺少可选依赖 {missing}，这次先不生成。")
            event.stop_event()
            return True
        logger.info(
            "指令生图结果: user=%s action=%s backend=%s ok=%s note=%s image=%s",
            _single_line(user_id, 40),
            action_text,
            _single_line(backend_name, 80),
            bool(image_path),
            _single_line(note, 180),
            _single_line(image_path, 180),
        )
        counted = bool(image_path)
        if not image_path and callable(getattr(self, "_photo_generation_failure_counts_as_attempt", None)):
            counted = bool(self._photo_generation_failure_counts_as_attempt(note))
        if counted:
            async with self._data_lock:
                user = self._get_user(user_id)
                self._note_command_photo_generation_attempt(user, image_path=image_path)
                scope_notifier = getattr(self, "_note_photo_generation_scope_attempt", None)
                if callable(scope_notifier):
                    scope_notifier(
                        event,
                        user=user,
                        user_id=user_id,
                        scope=photo_scope,
                    )
                self._save_data_sync(sections={"users", "photo_generation_scope_attempts"})
        if not image_path:
            await self._reply(
                event,
                f"这次没生成出来：{_single_line(note, 160) or '后端没有返回图片'}"
                + ("\n这次已经计入今日指令生图额度，避免后端异常时反复请求。" if counted else ""),
            )
            event.stop_event()
            return True
        caption = await self._natural_language_photo_done_reply_text(
            event,
            user_snapshot,
            kind=forced_kind,
            reference_label=reference_label,
        )
        metadata_getter = getattr(self, "_photo_generation_result_metadata", None)
        generation_metadata = (
            metadata_getter(image_path=image_path, session_key=generation_session_key)
            if callable(metadata_getter)
            else {}
        )
        fallback_payload = (
            generation_metadata.get("reference_fallback")
            if isinstance(generation_metadata, dict)
            else {}
        )
        fallback_message = _single_line(
            fallback_payload.get("message") if isinstance(fallback_payload, dict) else "",
            260,
        )
        if fallback_message:
            caption = f"{caption}\n{fallback_message}".strip()
        delivery = await self._deliver_generated_image_to_event(
            event,
            image_path=image_path,
            caption=caption,
        )
        annotator = getattr(self, "_annotate_recent_photo_generation", None)
        if callable(annotator):
            annotator(
                image_path=image_path,
                session_key=generation_session_key,
                trigger="command_photo",
                intent_kind=forced_kind,
                sent=bool(delivery.get("sent")),
                caption=caption,
                tool_name="companion_photo_command",
            )
        if not delivery.get("sent"):
            await self._reply(event, _single_line(delivery.get("message"), 180) or "图片未能发送。")
        elif delivery.get("destination") == "private":
            await self._reply(event, "图片已私聊发送。")
        event.stop_event()
        return True
