"""Bounded failure decisions shared by stored and native tool results."""


_INPUT_CODES = frozenset({
    "qzone_topic_required", "qzone_topic_invalid", "qzone_scene_required",
    "qzone_scene_invalid", "qzone_topic_confidence_invalid", "qzone_request_invalid",
    "qzone_image_invalid",
    "missing_edit_source", "native_reference_missing", "invalid_image_arguments",
    "invalid_prompt", "invalid_negative_prompt", "invalid_reference_ids",
    "invalid_reference_indices", "invalid_reference_roles", "invalid_mask_reference_indices",
    "native_reference_index_unavailable", "native_reference_id_unavailable",
    "native_reference_roles_mismatch", "native_reference_role_required",
    "native_reference_role_unavailable", "image_artifact_not_found",
    "reference_ids_invalid", "backend_request_invalid", "native_reference_limit",
    "edit_reference_limit", "mask_edit_requires_source_and_mask",
    "edit_mask_invalid", "mask_dimension_mismatch", "edit_mask_empty",
})

_IMAGE_UNCONFIGURED_CODES = frozenset({
    "native_backend_unconfigured", "unsupported_image_operation", "native_workflow_unconfigured",
})
_IMAGE_WORKFLOW_CODES = frozenset({
    "native_workflow_bindings_invalid", "native_workflow_reference_role_mismatch",
    "native_workflow_unbound_reference", "native_workflow_mapping_invalid",
    "workflow_nodes_missing", "native_mask_bindings_invalid",
    "native_workflow_mask_binding_invalid", "native_workflow_mask_unconnected",
})
_IMAGE_BACKEND_REJECTED_CODES = frozenset({
    "comfyui_rejected", "sd_webui_rejected", "native_endpoint_rejected", "native_endpoints_rejected",
})


def retry_disposition_for(error_code, state):
    """Recover classifications for older rows without a stored owner decision."""
    if state in ("unknown", "running", "queued"):
        return "external_effect_unknown"
    if state != "failed":
        return None
    if error_code == "qzone_topic_confirmation_required":
        return "human_confirmation_required"
    return "input_invalid" if error_code in _INPUT_CODES else "stop"


def blocks_repeat(*, action, state, disposition, effectful, same_arguments, same_turn):
    if state in ("queued", "running", "unknown"):
        # A changed post body is not evidence that an earlier write failed.
        return same_arguments and (same_turn or effectful) or action == "qzone.post.publish"
    if state != "failed" or not same_turn:
        return False
    if disposition == "human_confirmation_required":
        return True
    if action == "qzone.post.publish" and disposition == "stop":
        return True
    # Each external child has a one-attempt budget. A fresh model call ID
    # cannot replenish that budget, even for an otherwise safe failure.
    return same_arguments


def failure_guidance(error_code, state, *, action):
    """Only static messages reach the model; provider exception text stays private."""
    if state == "succeeded":
        return {}
    disposition = retry_disposition_for(error_code, state)
    if action.startswith("image."):
        if error_code in ("missing_edit_source", "native_reference_missing"):
            return {
                "reason": "当前没有可用于改图的源图片（本轮未附图或引用图片，当前会话也没有可编辑的最近生成图，且未指定角色参考素材），尚未发起远端生图。",
                "next_step": "请让用户发送或引用要修改的图片，或确认改为从零生成新图；不要在缺少底图时盲目重试改图，也不要编造额度或尺寸错误。",
            }
        if disposition == "external_effect_unknown":
            return {
                "reason": "本次图片请求结果尚未确认。",
                "next_step": "请保留原操作状态，不要换参数盲目重复提交。",
            }
        if error_code in _IMAGE_UNCONFIGURED_CODES:
            return {
                "reason": "当前生图或改图路线尚未配置可用的后端或对应工作流，未发起远端生图。",
                "next_step": "请如实告知用户当前未配置可用的生图/改图路线或工作流，请在陪伴小屋的生图设置中检查。",
            }
        if error_code in _IMAGE_WORKFLOW_CODES:
            return {
                "reason": "ComfyUI 工作流节点映射或节点依赖校验未通过，未提交远端生图。",
                "next_step": "请如实告知用户工作流映射或节点配置有误，请在陪伴小屋重新识别并保存工作流后再试。",
            }
        if error_code == "backend_rate_limited":
            return {
                "reason": "生图服务当前触发限流，本次请求未执行。",
                "next_step": "请稍后再试，或提示用户检查生图服务限流状态。",
            }
        if error_code in _IMAGE_BACKEND_REJECTED_CODES:
            return {
                "reason": "生图后端拒绝了本次请求，未生成图片。",
                "next_step": "请结合当前参数调整后再试，必要时在陪伴小屋检查生图端点配置与回执诊断。",
            }
        if disposition == "input_invalid":
            return {
                "reason": "图片调用参数（参考图序号、素材 ID、产物 ID 或数量限制）无效，未发起远端生图。",
                "next_step": "请核对参考图数量、序号、用途或改为只传 prompt 使用当前附图/最近生成图，修正后再调用。",
            }
        return {
            "reason": "本次图片工具调用失败，未生成图片。",
            "next_step": "请如实向用户说明当前无法完成生图或改图，不要声称已生成或已发送。",
        }
    if not action.startswith("qzone."):
        return {}
    if disposition == "external_effect_unknown":
        return {"reason": "QQ 空间操作结果未知。",
                "next_step": "请先到 QQ 空间核对实际结果；核对前不要自动重发。"}
    if disposition == "human_confirmation_required":
        return {"reason": "发布主题和场景需要人工确认，尚未发布。",
                "next_step": "请打开“陪伴小屋 → QQ 空间”发布区，查看具体正文、主题和场景后手动发布。"}
    if disposition == "input_invalid":
        return {"reason": "QQ 空间发布内容或参数需要修正，尚未发布。",
                "next_step": "请检查正文、主题、场景、置信度和图片，修正后再提交。"}
    if error_code == "qzone_auth_blocked":
        return {"reason": "QQ 空间凭据不可用，本次操作已停止。",
                "next_step": "请在“陪伴小屋 → QQ 空间”检查登录状态和凭据。"}
    return {"reason": "QQ 空间操作被当前策略或服务拒绝，本次操作已停止。",
            "next_step": "请在“陪伴小屋 → QQ 空间”检查开关、主题规则和额度后再操作。"}
