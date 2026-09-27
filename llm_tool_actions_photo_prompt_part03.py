# -*- coding: utf-8 -*-
"""LlmToolActionsPhotoPromptPart03Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_photo_prompt.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 247 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsPhotoPromptMixin）。
"""
from __future__ import annotations
from .llm_tool_actions_photo_prompt_shared import Any
from .llm_tool_actions_photo_prompt_shared import AstrMessageEvent
from .llm_tool_actions_photo_prompt_shared import PHOTO_TOOL_SILENT_SENTINEL
from .llm_tool_actions_photo_prompt_shared import _redact_outbound_secrets
from .llm_tool_actions_photo_prompt_shared import _single_line
from .llm_tool_actions_photo_prompt_shared import asyncio
from .llm_tool_actions_photo_prompt_shared import json
from .llm_tool_actions_photo_prompt_shared import logger



class LlmToolActionsPhotoPromptPart03Mixin:
    """LlmToolActionsPhotoPromptPart03Mixin（从 LlmToolActionsPhotoPromptMixin 拆出）。"""


    async def _pc_send_current_media_impl(
        self,
        event: AstrMessageEvent,
        *,
        media_path: str = "",
        caption: str = "",
        destination: str = "current",
        **kwargs: Any,
    ) -> str:
        if self._current_turn_has_delivered_media(event):
            setattr(event, "_private_companion_photo_tool_sent", True)
            setattr(event, "_private_companion_photo_tool_sent_caption", "")
            return json.dumps(
                {
                    "status": "already_sent",
                    "success": True,
                    "sent": True,
                    "message": "本轮已经发送过媒体，不再重复投递。",
                    "same_turn_retry_allowed": False,
                    "final_response_instruction": f"不要追加回执或正文，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。",
                },
                ensure_ascii=False,
            )
        raw_path = media_path or kwargs.get("image_path") or kwargs.get("path")
        path, rejection = self._resolve_current_media_image(raw_path)
        if path is None:
            return json.dumps(
                {
                    "status": "invalid_media",
                    "success": False,
                    "sent": False,
                    "message": rejection or "图片不可用",
                    "must_not_claim_sent": True,
                    "same_turn_retry_allowed": False,
                    "final_response_instruction": "不要再次猜测或改写本地路径；如实说明这次图片没有发送。",
                },
                ensure_ascii=False,
            )
        destination_raw = _single_line(
            destination or kwargs.get("target_scope") or kwargs.get("scope") or "current",
            40,
        ).casefold()
        requester_private = destination_raw in {
            "requester_private",
            "requester-private",
            "private",
            "private_requester",
            "dm",
            "私聊",
            "私信",
        }
        if requester_private and not self._current_media_private_delivery_instruction_matches(
            getattr(event, "message_str", "")
        ):
            return json.dumps(
                {
                    "status": "destination_not_confirmed",
                    "success": False,
                    "sent": False,
                    "message": "当前消息没有明确要求把图片私聊发给请求者",
                    "must_not_claim_sent": True,
                    "same_turn_retry_allowed": False,
                    "final_response_instruction": "不要私聊发送，也不要声称已经发送；按当前会话自然回复。",
                },
                ensure_ascii=False,
            )
        sent_paths = getattr(event, "_private_companion_current_media_sent_paths", None)
        if not isinstance(sent_paths, set):
            sent_paths = set()
            setattr(event, "_private_companion_current_media_sent_paths", sent_paths)
        destination_key = "requester_private" if requester_private else "current"
        path_key = f"{destination_key}:{path}".casefold()
        if path_key in sent_paths:
            setattr(event, "_private_companion_photo_tool_sent", True)
            return json.dumps(
                {
                    "status": "already_sent",
                    "success": True,
                    "sent": True,
                    "message": "这张图片本轮已经投递，不再重复发送。",
                    "same_turn_retry_allowed": False,
                    "final_response_instruction": f"不要追加回执或正文，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。",
                },
                ensure_ascii=False,
            )
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=120)
        try:
            if requester_private:
                try:
                    target_user = _single_line(event.get_sender_id(), 128)
                except Exception:
                    target_user = ""
                sender = getattr(self, "_send_atrelay_chain_to_target", None)
                chain_builder = getattr(self, "_build_outbound_chain", None)
                if not target_user:
                    delivery = {
                        "sent": False,
                        "destination": "requester_private",
                        "message": "无法识别当前请求者，图片没有私聊发送",
                    }
                elif not callable(sender) or not callable(chain_builder):
                    delivery = {
                        "sent": False,
                        "destination": "requester_private",
                        "message": "当前平台没有可用的私聊图片投递链路",
                    }
                else:
                    chain = chain_builder(visible_caption, str(path))
                    ok, error, used_umo = await sender(
                        event,
                        message_type="private",
                        target_id=target_user,
                        chain=chain,
                    )
                    delivery = {
                        "sent": bool(ok),
                        "destination": "requester_private",
                        "message": (
                            "图片已私聊发送给当前请求者"
                            if ok
                            else f"图片私聊发送失败：{_single_line(error, 180) or '没有可用私聊会话'}"
                        ),
                        "target_umo": _single_line(used_umo, 160),
                    }
            else:
                delivery = await self._deliver_generated_image_to_event(
                    event,
                    image_path=str(path),
                    caption=visible_caption,
                )
        except Exception as exc:
            delivery = {
                "sent": False,
                "uncertain": isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)),
                "destination": destination_key,
                "message": f"图片发送失败：{_single_line(exc, 180) or '未知错误'}",
            }
        sent = bool(delivery.get("sent"))
        uncertain = bool(delivery.get("uncertain"))
        if sent:
            sent_paths.add(path_key)
            setattr(event, "_private_companion_photo_tool_sent", True)
            setattr(event, "_private_companion_photo_tool_sent_caption", visible_caption)
        payload = {
            "status": "success" if sent else "delivery_uncertain" if uncertain else "delivery_failed",
            "success": sent,
            "sent": sent,
            "delivery_uncertain": uncertain,
            "delivery": _single_line(delivery.get("destination"), 30),
            "message": _single_line(delivery.get("message"), 220) or ("图片已发送" if sent else "图片发送失败"),
            "must_not_claim_sent": not sent,
            "same_turn_retry_allowed": False,
            "final_response_instruction": (
                f"图片及可选 caption 已作为本轮唯一可见回复发送，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                if sent
                else "不要再次发送或重新生成；按 message 如实说明当前投递结果。"
            ),
        }
        return json.dumps(payload, ensure_ascii=False)

    async def _recover_plaintext_photo_tool_call(
        self,
        event: AstrMessageEvent,
        resp: Any,
        text: Any,
    ) -> tuple[str, dict[str, Any] | None]:
        raw = str(text or "")
        if bool(getattr(event, "_private_companion_plaintext_tool_checked", False)):
            previous = getattr(event, "_private_companion_plaintext_tool_recovery", None)
            return raw, previous if isinstance(previous, dict) else None
        cleaned, calls = self._strip_plaintext_tool_call_envelopes(raw)
        if not calls:
            return raw, None
        setattr(event, "_private_companion_plaintext_tool_checked", True)
        logger.warning(
            "检测到模型将工具调用写入普通正文，已阻止外发: session=%s tools=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            ",".join(call.get("name", "") for call in calls),
        )
        recovery: dict[str, Any] = {
            "status": "sanitized_only",
            "sent": False,
            "tools": [call.get("name", "") for call in calls],
        }
        setattr(event, "_private_companion_plaintext_tool_recovery", recovery)
        photo_calls = [call for call in calls if call.get("name") == "pc_generate_photo"]
        if len(calls) != 1 or len(photo_calls) != 1:
            return cleaned, recovery
        try:
            called_names = getattr(resp, "tools_call_name", None)
            if isinstance(called_names, str) and called_names.strip() == "pc_generate_photo":
                recovery["status"] = "already_called"
                return cleaned, recovery
            if isinstance(called_names, (list, tuple, set)) and "pc_generate_photo" in {str(item) for item in called_names}:
                recovery["status"] = "already_called"
                return cleaned, recovery
            if self._proactive_only_blocks_passive_event(event, "pc_generate_photo"):
                recovery["status"] = "blocked"
                return cleaned, recovery
        except Exception:
            pass
        inbound_text = str(getattr(event, "message_str", "") or "")
        if not self._plaintext_photo_recovery_intent_matches(inbound_text):
            recovery["status"] = "intent_mismatch"
            return cleaned, recovery

        raw_parameters = photo_calls[0].get("parameters")
        parameters = dict(raw_parameters) if isinstance(raw_parameters, dict) else {}
        allowed_keys = {
            "prompt",
            "kind",
            "reference_image_path",
            "reference_image_paths",
            "image_size",
            "caption",
            "scene_preset",
        }
        parameters = {key: value for key, value in parameters.items() if key in allowed_keys}
        parameters["send"] = True
        try:
            result_raw = await self._pc_generate_photo_impl(event, **parameters)
            try:
                result = json.loads(result_raw) if isinstance(result_raw, str) else dict(result_raw or {})
            except Exception:
                result = {"status": "error", "sent": False, "message": "生图工具返回无法解析"}
        except Exception as exc:
            logger.error(
                "明文生图工具调用恢复失败: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(exc, 160),
                exc_info=True,
            )
            result = {"status": "error", "sent": False, "message": "图片生成调用失败"}
        sent = bool(result.get("sent"))
        recovery.update({"status": "recovered" if sent else "failed", "sent": sent, "result": result})
        setattr(event, "_private_companion_plaintext_tool_recovery", recovery)
        if sent:
            setattr(event, "_private_companion_plaintext_photo_sent", True)
            logger.info(
                "已恢复并执行明文生图工具调用: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return cleaned, recovery
        failure = _single_line(result.get("message") or result.get("actual_error") or "图片没有生成成功", 180)
        failure = _redact_outbound_secrets(failure, self)
        failure_text = f"这次图片没能发出来：{failure}" if failure else "这次图片没能发出来。"
        cleaned = "\n".join(part for part in (cleaned, failure_text) if str(part or "").strip()).strip()
        return cleaned, recovery
