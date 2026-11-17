"""Bounded failure decisions shared by stored and native tool results."""


_INPUT_CODES = frozenset({
    "qzone_topic_required", "qzone_topic_invalid", "qzone_scene_required",
    "qzone_scene_invalid", "qzone_topic_confidence_invalid", "qzone_request_invalid",
    "qzone_image_invalid",
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
    if state == "succeeded" or not action.startswith("qzone."):
        return {}
    disposition = retry_disposition_for(error_code, state)
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
