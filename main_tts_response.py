# -*- coding: utf-8 -*-
"""TTS 回复规范化域。

由 tools/split_main_domain_v2.py 从 main.py 机械抽取（4 个方法 / 263 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

from .helpers import _single_line
from .llm_tool_actions import PHOTO_TOOL_SILENT_SENTINEL
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginTtsResponseMixin:
    """TTS 回复规范化域（从 PrivateCompanionPlugin 拆出）。"""

    async def _tts_recover_visible_text(self, event: Any, resp: Any) -> Any:
        """恢复被降级为正文的生图调用，返回 (原始正文, 恢复后正文)。"""
        original_text = str(getattr(resp, "completion_text", "") or "")
        same_session_tool = getattr(self, "_prepare_same_session_send_tool_response", None)
        same_session_tool_call = False
        if callable(same_session_tool):
            try:
                same_session_tool_call, _ = same_session_tool(event, resp)
            except Exception as exc:
                logger.debug(
                    "同会话工具回复去重准备失败: %s",
                    _single_line(exc, 120),
                )
        if same_session_tool_call:
            # AstrBot yields completion_text even when the same response also has
            # a tool call. The tool/final-response path is authoritative here.
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            original_text = ""
        tool_names = getattr(resp, "tools_call_name", None)
        if isinstance(tool_names, str):
            normalized_tool_names = {tool_names.strip()}
        elif isinstance(tool_names, (list, tuple, set)):
            normalized_tool_names = {
                str(item or "").strip() for item in tool_names if str(item or "").strip()
            }
        else:
            normalized_tool_names = set()
        media_delivery_tool_call = bool(
            normalized_tool_names
            & {"pc_find_reaction_image", "pc_generate_photo", "pc_send_current_media"}
        )
        if media_delivery_tool_call:
            # These tools own their visible caption/media delivery. AstrBot also
            # yields assistant content attached to a tool call as an llm_result;
            # exposing that intermediate text produces a duplicate before the
            # tool result is known and can falsely claim that an image was sent.
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            original_text = ""
            logger.info(
                "已隐藏媒体工具调用前的中间正文: tools=%s session=%s",
                ",".join(sorted(normalized_tool_names)),
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
        recovered_text, _ = await self._recover_plaintext_photo_tool_call(event, resp, original_text)
        if recovered_text != original_text:
            resp.completion_text = recovered_text
        return original_text, recovered_text

    async def _tts_drop_photo_tool_trailing_text(self, event: Any, resp: Any, recovered_text: Any) -> bool:
        """图片工具已发送时丢弃同轮尾随正文。返回 True 表示宿主应立即收口。"""
        if bool(getattr(event, "_private_companion_photo_tool_sent", False)):
            # pc_generate_photo 已经把 caption 与图片作为唯一可见回复发出。
            # 不论模型是否输出静默标记，都丢弃同一轮尾随正文，避免再次分段、TTS 或触发表情附件。
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            for attr in (
                "_private_companion_reaction_expression_intent",
                "_private_companion_deferred_reaction_tts",
                "_private_companion_reaction_expression_expected_primary_chunks",
                "_private_companion_reaction_expression_segmented_remainder",
            ):
                try:
                    delattr(event, attr)
                except (AttributeError, TypeError):
                    pass
            logger.info(
                "图片已发送，已丢弃同轮尾随模型正文: session=%s chars=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                len(recovered_text or ""),
            )
            return True
        return False

    async def _tts_apply_reaction_expression_pass(self, event: Any, resp: Any, recovered_text: Any) -> Any:
        """表情意图抽取与授权记账，返回清洗后的正文。"""
        reaction_extractor = getattr(
            self, "_extract_reaction_expression_hidden_intent", None
        )
        if callable(reaction_extractor):
            cleaned_reaction_text, reaction_intent = reaction_extractor(
                recovered_text
            )
        else:
            cleaned_reaction_text, reaction_intent = recovered_text, {}
        response_has_tool_call = bool(getattr(resp, "tools_call_name", None))
        if response_has_tool_call:
            try:
                delattr(event, "_private_companion_reaction_expression_intent")
            except (AttributeError, TypeError):
                pass
        reaction_authorization_getter = getattr(
            self, "_reaction_expression_authorization", None
        )
        authorization = (
            reaction_authorization_getter(event)
            if callable(reaction_authorization_getter)
            else {}
        )
        reaction_visible_checker = getattr(
            self, "_reaction_expression_has_visible_text", None
        )
        reaction_visible_text = (
            reaction_visible_checker(cleaned_reaction_text)
            if callable(reaction_visible_checker)
            else bool(str(cleaned_reaction_text or "").strip())
        )
        reaction_runtime_logger = getattr(
            self, "_log_reaction_expression_event", None
        )
        reaction_scope_getter = getattr(self, "_reaction_expression_scope", None)
        reaction_scope = (
            reaction_scope_getter(event)
            if callable(reaction_scope_getter)
            else "unknown"
        )
        if cleaned_reaction_text != recovered_text:
            resp.completion_text = cleaned_reaction_text
            recovered_text = cleaned_reaction_text
            if (
                reaction_intent
                and authorization.get("authorized")
                and not authorization.get("consumed")
                and reaction_visible_text
                and not response_has_tool_call
            ):
                try:
                    setattr(
                        event,
                        "_private_companion_reaction_expression_intent",
                        reaction_intent,
                    )
                except Exception:
                    pass
                if callable(reaction_runtime_logger):
                    reaction_runtime_logger(
                        event,
                        stage="intent",
                        decision="accepted",
                        reason="intent_extracted",
                        scope=reaction_scope,
                    )
            elif reaction_intent and callable(reaction_runtime_logger):
                reaction_runtime_logger(
                    event,
                    stage="intent",
                    decision="discarded",
                        reason=(
                            "tool_call_intermediate"
                            if response_has_tool_call
                            else "intent_discarded"
                        ),
                        scope=reaction_scope,
                    )
        existing_reaction_intent = getattr(
            event, "_private_companion_reaction_expression_intent", None
        )
        if (
            authorization.get("authorized")
            and not authorization.get("consumed")
            and reaction_visible_text
            and not reaction_intent
            and not (
                isinstance(existing_reaction_intent, dict)
                and bool(existing_reaction_intent)
            )
            and not response_has_tool_call
            and not authorization.get("model_omission_recorded")
        ):
            authorization["model_omission_recorded"] = True
            authorization_setter = getattr(
                self, "_set_reaction_expression_authorization", None
            )
            if callable(authorization_setter):
                authorization_setter(event, authorization)
            runtime_notifier = getattr(self, "_note_reaction_expression_runtime", None)
            if callable(runtime_notifier):
                runtime_notifier(
                    model_omissions=1,
                    last_reason="model_omitted_intent",
                )
            if callable(reaction_runtime_logger):
                reaction_runtime_logger(
                    event,
                    stage="intent",
                    decision="omit",
                    reason="model_omitted_intent",
                    scope=authorization.get("scope") or reaction_scope,
                )
            fallback_builder = getattr(self, "_reaction_expression_local_fallback_intent", None)
            fallback_intent = (
                fallback_builder(event, cleaned_reaction_text, authorization)
                if callable(fallback_builder)
                else {}
            )
            if fallback_intent:
                try:
                    setattr(
                        event,
                        "_private_companion_reaction_expression_intent",
                        fallback_intent,
                    )
                except Exception:
                    pass
                if callable(runtime_notifier):
                    runtime_notifier(
                        local_fallbacks=1,
                        last_reason="local_fallback_intent",
                    )
                if callable(reaction_runtime_logger):
                    reaction_runtime_logger(
                        event,
                        stage="intent",
                        decision="accepted",
                        reason="local_fallback_intent",
                        scope=authorization.get("scope") or reaction_scope,
                    )
        return recovered_text

    def _tts_apply_photo_sentinel_guards(self, event: Any, resp: Any, original_text: Any, recovered_text: Any) -> Any:
        """清除生图成功后残留的静默标记与重复承接正文，返回 (原始正文, 恢复后正文)。"""
        sent_photo_caption = str(
            getattr(event, "_private_companion_photo_tool_sent_caption", "") or ""
        ).strip()
        if (
            bool(getattr(event, "_private_companion_photo_tool_sent", False))
            and PHOTO_TOOL_SILENT_SENTINEL in recovered_text
        ):
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            original_text = ""
            recovered_text = ""
            logger.info(
                "已清除图片工具成功发送后的内部静默标记: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
        if (
            bool(getattr(event, "_private_companion_photo_tool_sent", False))
            and self._photo_tool_followup_is_redundant(sent_photo_caption, recovered_text)
        ):
            try:
                resp.result_chain = None
            except Exception:
                pass
            resp.completion_text = ""
            original_text = ""
            recovered_text = ""
            logger.info(
                "已移除生图工具成功发送后的重复承接正文: session=%s caption=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(sent_photo_caption, 120),
            )
        return original_text, recovered_text
