# -*- coding: utf-8 -*-
"""LlmToolActionsPhotoGeneratePart02Part06Mixin。

由 tmp/refactor/lta2_split.py 从 llm_tool_actions_photo_generate_part02.py 的 _pc_generate_photo_impl 段级拆分而来（payload）。
段体与拆分前逐字节相同；仅段末追加 `return _StageNext(...)` 交还活跃局部名。
所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsPhotoGenerateMixin）。
"""
from __future__ import annotations

import re
from typing import Any

from .helpers import _single_line
from .llm_tool_actions_photo_generate_part02_shared import _StageNext
from .llm_tool_actions_shared import (
    PHOTO_TOOL_SILENT_SENTINEL,
    logger,
)


class LlmToolActionsPhotoGeneratePart02Part06Mixin:
    """_pc_generate_photo_impl 的 payload 段。"""

    async def _pc_generate_photo_impl_payload(
        self,
        actual_reference_path,
        annotator,
        backend_name,
        content,
        delivery,
        delivery_deferred,
        event,
        failure_stage,
        final_presets,
        final_scene_preset,
        generation_completed,
        generation_metadata,
        generation_session_key,
        image_path,
        intent_kind,
        note,
        ok,
        preset_text,
        public_receipt,
        reference_usage_known,
        resolved_reference_paths,
        send_image,
        sent,
        used_reference,
        visible_caption,
        workflow_kind,
    ):
        """_pc_generate_photo_impl 段：结果回执组装与失败分支收口。"""
        if callable(annotator):
            annotator(
                image_path=image_path,
                session_key=generation_session_key,
                trigger="llm_tool",
                intent_kind=intent_kind,
                sent=sent,
                caption=visible_caption,
                preset_hint=preset_text,
                tool_name="pc_generate_photo",
            )
        if ok:
            memory_recorder = getattr(self, "_memory_companion_record_photo_generation", None)
            if callable(memory_recorder):
                await memory_recorder(
                    event,
                    prompt=content,
                    kind=workflow_kind,
                    intent_kind=intent_kind,
                    backend=backend_name,
                    image_path=image_path,
                    note=note,
                    sent=sent,
                    trigger="llm_tool",
                    scene_preset=final_scene_preset,
                    reference_image_path=actual_reference_path,
                    reference_used=used_reference if reference_usage_known else None,
                )
        delivery_uncertain = bool(delivery.get("uncertain"))
        overall_success = bool(ok and (not send_image or sent or delivery_deferred))
        public_reference_plan = [
            {
                key: value
                for key, value in binding.items()
                if key in {"reference_id", "roles", "priority", "preserve", "ignore", "submitted"}
            }
            for binding in (generation_metadata.get("reference_plan") or [])[:8]
            if isinstance(binding, dict)
        ]
        result_payload = {
            "status": (
                "success"
                if overall_success
                else "delivery_uncertain"
                if ok and send_image and delivery_uncertain
                else "delivery_failed"
                if ok
                else "result_retrieval_failed"
                if generation_completed and failure_stage == "result_materialization"
                else "error"
            ),
            "success": overall_success,
            "generated": ok,
            "generation_completed": generation_completed,
            "failure_stage": failure_stage,
            "send_requested": send_image,
            "message": (
                _single_line(delivery.get("message"), 220)
                if ok and send_image and delivery
                else ("图片已生成但按请求未发送" if ok and not send_image else (
                    "上游已完成生图，但图片结果没有成功取回，未发送。"
                    if generation_completed and failure_stage == "result_materialization"
                    else (_single_line(note, 220) or "生图失败")
                ))
            ),
            "backend": _single_line(backend_name, 80),
            "kind": workflow_kind,
            "intent_kind": intent_kind,
            "used_reference": used_reference,
            "reference_id": _single_line(generation_metadata.get("reference_id"), 60),
            "reference_kind": _single_line(generation_metadata.get("reference_kind"), 40),
            "reference_roles": list(generation_metadata.get("reference_roles") or [])[:8],
            "reference_intent": {
                "requested_roles": list(generation_metadata.get("reference_requested_roles") or [])[:8],
                "excluded_roles": list(generation_metadata.get("reference_excluded_roles") or [])[:8],
                "continuity_mode": _single_line(generation_metadata.get("continuity_mode"), 30),
                "confidence": generation_metadata.get("reference_confidence", 0.0),
            },
            "reference_plan": public_reference_plan,
            "reference_fulfilled_roles": list(generation_metadata.get("reference_fulfilled_roles") or [])[:8],
            "reference_missing_roles": list(generation_metadata.get("reference_missing_roles") or [])[:8],
            "reference_fallback_message": _single_line(generation_metadata.get("reference_fallback_message"), 260),
            "wardrobe_mode": _single_line(generation_metadata.get("wardrobe_mode"), 40),
            "wardrobe_category": _single_line(generation_metadata.get("wardrobe_category"), 40),
            "outfit_locked": bool(generation_metadata.get("outfit_locked")),
            "daily_outfit_removed": bool(generation_metadata.get("daily_outfit_removed")),
            "preset_hint": preset_text,
            "preset_source": _single_line(generation_metadata.get("preset_source"), 40),
            "suggestion_status": _single_line(generation_metadata.get("suggestion_status"), 60),
            "final_presets": final_presets,
            "prompt_hash": _single_line(generation_metadata.get("prompt_hash"), 80),
            "sent": sent,
            "delivery_deferred": delivery_deferred,
            "delivery_uncertain": delivery_uncertain,
            "delivery": _single_line(delivery.get("destination"), 30),
            "safety_review": _single_line(delivery.get("review_label"), 30),
            "note": _single_line(note, 220),
            "must_not_claim_sent": not sent,
            "same_turn_retry_allowed": False,
            "final_response_instruction": (
                f"图片及可选的自然 caption 已作为本轮唯一可见回复发送。最终回复不要留空，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                if sent
                else f"图片已生成并交给主动发送链；只有非回执的自然 caption 才会随图发送。不要输出状态回执，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                if delivery_deferred
                else ""
            ),
        }
        if ok and send_image and not sent and not delivery_deferred:
            delivery_error = _single_line(delivery.get("message"), 360) or "图片发送失败"
            result_payload.update(
                {
                    "failure_stage": "delivery",
                    "delivery_error": delivery_error,
                    "actual_error": delivery_error,
                    "actionable_hint": (
                        "图片已经提交给平台，但发送回执未确认。不要断言用户已收到，也不要断言发送失败；"
                        "如需回复，只能简短说明回执未确认并请用户查看，绝对不要立即再次发送。"
                        if delivery_uncertain
                        else "图片文件已经生成，但用户没有收到图片。请明确说发送失败，绝对不能说已经发出。"
                    ),
                    "retryable": not delivery_uncertain,
                }
            )
        elif not ok:
            note_text = _single_line(note, 360) or "生图失败"
            lowered_note = note_text.lower()
            upstream_submission_unconfirmed = bool(
                re.search(r"HTTP\s*(?:500|502|503|504)\b", note_text, flags=re.I)
                or "上游生图服务临时失败" in note_text
                or "网关中断" in note_text
                or ("在线图片 API" in note_text and "超时" in note_text)
            )
            hint = "请按 actual_error 里的真实原因回复用户，不要改写成未出现的超时、排队或权限问题。"
            policy_refusal = self._photo_generation_policy_refusal(note_text)
            if policy_refusal:
                public_error = "图片服务拒绝了这次画面描述，本次没有生成或发送图片。"
                logger.warning(
                    "pc_generate_photo 被图片服务策略拒绝: backend=%s error=%s",
                    _single_line(backend_name, 80),
                    note_text,
                )
                result_payload.update(
                    {
                        "message": public_error,
                        "note": public_error,
                        "error_code": "provider_policy_refusal",
                        "failure_reason": public_error,
                        "actual_error": public_error,
                        "actionable_hint": "请用当前人格简短说明这次没有生成出来，并自然询问用户是否换一种画面描述重试；不要复述 Provider 原文、政策名称、敏感词判断或链接。",
                        "do_not_claim_timeout": True,
                        "must_not_claim_sent": True,
                        "retryable": True,
                        "final_response_instruction": "不要复述或翻译 Provider 的英文原文、政策名称、敏感词判断和链接。只用符合当前人格的一句简短中文说明这次没有生成出来，再自然询问是否换一种画面描述重试。",
                    }
                )
            elif "404" in note_text or "not found" in lowered_note or "未找到" in note_text:
                hint = "在线生图接口返回 404，通常是 API 地址端点不对或缺少 /v1；请让用户检查在线图片 API 地址是否支持 /images/generations。"
            elif "图片模型" in note_text or "image model" in lowered_note:
                hint = "当前模型可能不是生图模型；请让用户把在线图片模型改成对应平台的图片模型。"
            elif "api key" in lowered_note or "unauthorized" in lowered_note or "401" in note_text or "403" in note_text:
                hint = "请让用户检查在线图片 API Key、权限和额度。"
            if not policy_refusal:
                result_payload.update(
                    {
                        "failure_reason": note_text,
                        "actual_error": note_text,
                        "actionable_hint": hint,
                        "do_not_claim_timeout": "超时" not in note_text and "timeout" not in lowered_note,
                        "must_not_claim_sent": True,
                    }
                )
            if upstream_submission_unconfirmed:
                result_payload.update(
                    {
                        "status": "submission_unconfirmed",
                        "failure_stage": "upstream_response",
                        "retryable": False,
                        "same_turn_retry_allowed": False,
                        "possible_upstream_execution": True,
                        "actionable_hint": (
                            "网关失败不代表上游任务没有执行，且可能已经计费。"
                            "本轮绝对不要重新调用任何生图工具；请用户先检查服务端任务或账单，稍后再明确决定是否重试。"
                        ),
                        "final_response_instruction": (
                            "简短说明本次没有取回图片，但上游可能仍在执行；不要声称确定失败，"
                            "不要自动重试，也不要建议用户立刻重复提交。"
                        ),
                    }
                )
            if generation_completed and failure_stage == "result_materialization":
                retrieval_message = (
                    "上游已经完成生图，但返回的图片结果未能取回或保存到本地；"
                    "本轮没有发送图片，也不要再次提交同一生图请求。"
                )
                result_payload.update(
                    {
                        "status": "result_retrieval_failed",
                        "message": retrieval_message,
                        "note": retrieval_message,
                        "failure_reason": retrieval_message,
                        "actual_error": note_text,
                        "failure_stage": "result_materialization",
                        "upstream_generated": True,
                        "retryable": False,
                        "same_turn_retry_allowed": False,
                        "actionable_hint": (
                            "如实说明上游已生成但图片结果取回失败，未发送；"
                            "不要说成上游生图请求失败，也不要在本轮再次调用 pc_generate_photo。"
                        ),
                        "final_response_instruction": (
                            "本轮不要再次调用 pc_generate_photo。简短说明图片结果取回失败、没有发送；"
                            "不要声称用户已经收到图片。用户下一轮明确要求时再重试。"
                        ),
                    }
                )
        known_private_paths: list[Any] = [
            image_path,
            actual_reference_path,
            generation_metadata.get("prompt_path"),
            *resolved_reference_paths,
        ]
        for binding in generation_metadata.get("reference_plan") or []:
            if not isinstance(binding, dict):
                continue
            known_private_paths.extend(
                value
                for key, value in binding.items()
                if "path" in str(key or "").lower()
            )
        return public_receipt(
            result_payload,
            ensure_ascii=False,
            known_paths=tuple(known_private_paths),
        )
        return _StageNext(())
