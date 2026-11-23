"""Request-local capability context for a conversation model attempt."""

from companion_v2.foundation.refs import ScopeKind


def _single_line(value, limit=80):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def capability_instruction(envelope, *, model_name=""):
    request = envelope.original
    if request.output_format != "conversation":
        return ""

    kind = {ScopeKind.PRIVATE: "私聊", ScopeKind.GROUP: "群聊"}.get(request.event.scope.kind)
    lines = ["本轮能力与会话背景仅供判断表达和行动边界，不必主动报告。"]
    lines.append("回复时遵循原人格与世界观自然表达；后台能力名称不代表现实动作已经完成。")
    if kind:
        lines.append(f"当前是{kind}；只面向这次会话回复。")
    platform = _single_line(request.event.scope.platform) if request.perception_options[0] else ""
    if platform:
        lines.append(f"本轮会话的平台标识：{platform}。")
    target_model = _single_line(model_name) if request.perception_options[1] else ""
    if target_model:
        lines.append(f"本次提交的目标对话模型标识：{target_model}；这只是请求路由标识，不代表底层实际型号或能力已验证。")

    capabilities = set(envelope.provider_capabilities) if request.perception_options[2] else set()
    if capabilities:
        modes = [label for key, label in (("text", "文本"), ("image", "图片输入"),
                                         ("audio", "音频输入"), ("tools", "结构化工具调用"))
                 if key in capabilities]
        if modes:
            lines.append("本轮接入配置允许提交：" + "、".join(modes)
                         + "；这不等于底层模型已验证理解，仍以实际返回和原件为准。")

    screen_supplied = any(image.source.owner == "screen" for image in request.images)
    supplied = [label for items, label in (
        (tuple(image for image in request.images if image.source.owner != "screen"), "图片"),
        (request.audios, "音频"), (request.videos, "视频画面帧")) if items]
    if screen_supplied:
        supplied.append("按需采集的屏幕画面")
    if supplied:
        lines.append("本轮实际提供的媒体：" + "、".join(supplied) + "；只描述原件可见或可听的范围。")
    else:
        lines.append("本轮没有提供图片、音频或视频原件；文字描述不等于亲眼看见或亲耳听见。")

    actions = {tool.action for tool in envelope.tools}
    if actions:
        lines.append("本轮仅能调用实际列出的工具；调用结果未返回成功前，不要声称操作已完成。")
    else:
        lines.append("本轮没有可调用工具。")
    if screen_supplied:
        lines.append("已收到按需采集的屏幕画面；只能描述采集时刻、画面覆盖范围内清晰可见的内容，"
                     "不能当作实时屏幕或用户发送的图片。")
        if "screen.capture" in actions:
            lines.append("仅当用户明确要求看屏，或当前问题确实无法在没有桌面画面的情况下回答时，才调用 screen.capture；"
                         "授权本身不代表可以每轮采集，不要为填补沉默或推测屏幕外行为而截图；意图不清时先询问。")
    elif "screen.capture" in actions:
        lines.append("只有用户明确要求看屏，或当前问题确实依赖桌面画面时，才调用 screen.capture；"
                     "授权不等于每轮采集，也不要为填补沉默或推测屏幕外行为截图。成功返回前，不能声称看到了屏幕。")
    else:
        lines.append("本轮未提供识屏工具，不能查看当前屏幕。")
    if "image.generate" in actions:
        lines.append("仅在没有底图且需要从零创作新图时，生成图片需调用 image.generate，以成功结果为准；修改附图或刚生成的图不要使用此工具。")
    if "image.edit" in actions:
        lines.append("需要修改图片时调用 image.edit（包括修改本轮附图、引用图或当前会话刚生成的图片；仅传 prompt 时后端会自动优先选用本轮附图/引用图，无附图时回退当前会话最近生成的图片）。若用户指定了本轮多张附图中的某一张，必须用 reference_indices 精确选择；省略时后端会收到全部本轮附图，不要把未选图片混入结果。reference_roles 可按所选图片顺序指定用途（identity、outfit、pose、scene、style、background、continuity），用途不清时先澄清。需要使用角色参考目录时，只填已明确选择的 reference_ids。若用户明确提供底图与蒙版，才调用 image.mask_edit。以工具回执为准。")
    if "image.mask_edit" in actions:
        lines.append("image.mask_edit 的当前私聊输入图片按 0 起始顺序编号，需指定不同的底图与蒙版序号；不能把普通参考图猜作蒙版。")
    return "\n".join(lines)
