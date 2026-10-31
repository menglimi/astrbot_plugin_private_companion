"""Natural-language image requests in AstrBot's existing core tool loop."""

from collections.abc import Mapping
import json
import logging
from zoneinfo import ZoneInfo

from companion_v2.conversation.media.generated import IMAGE_ACTIONS
from companion_v2.conversation.media.output_settings import (effective_settings, image_request_denial,
                                                             is_rule_image_request, prompt_instruction)
from companion_v2.references.prompts import IMAGE_REFERENCE_INSTRUCTION

from .native_extensions.request_context import set_system_instruction


def _record_turn_note(session, text):
    """Keep the per-turn image hint out of the system prompt."""
    state = getattr(session, "state", None)
    if not isinstance(state, dict):
        return
    notes = state.setdefault("_turn_notes", {})
    if isinstance(notes, dict):
        notes["image"] = text


logger = logging.getLogger(__name__)
_PURPOSES = {
    "image.generate": "没有可用参考图时按画面描述生成新图；包括全新角色、普通场景与无身份锚点的自拍",
    "image.edit": "依据本轮附图、已保存产物或角色参考素材改图；需要保持角色身份或参考图一致时优先使用",
    "image.mask_edit": "使用明确底图和蒙版做局部编辑",
}
_DELIVERY = (
    "生成工具返回的图片由 AstrBot 缓存供查看，生成成功与已经发给对方是两个步骤。"
    "图片符合请求时，把工具结果实际提供的缓存原路径交给 send_message_to_user，"
    "使用 messages=[{\"type\":\"image\",\"path\":\"工具返回的原路径\"}] 投递当前会话，session 留空；图片投递只包含图片。"
    "不要把 plain.text 或语音放进图片投递；需要说的话由本轮正常回复发送一次。图片投递成功后自然收住，避免重复发送同一张图。"
    "根据工具结果说明实际进展：没有成功结果时不声称已生成或送达，unknown 时保留原操作，不换编号重做。"
    "图片错误码 host_image_limit 表示宿主本地图片处理预算超限（字节数、尺寸或张数），"
    "reference_image_limit 表示已保存参考素材超出原生图像输入上限；两者都不是服务商日额度错误。"
    "没有明确的服务商额度回执时，不要声称今天的生图额度已用完。"
    "短文字可以表达角色语气，但发起图片请求后的核心动作仍是完成图片生成与投递。"
)


def core_image_tool_contract(action, description, parameters, *, reference_limit=None):
    """Annotate only the request-local schema; leave the provider catalog intact."""
    if action not in _PURPOSES:
        return description, parameters
    description = _PURPOSES[action] + "；返回图片原件与操作回执，生成成功本身不代表已发送。"
    properties = parameters.get("properties", {})
    prompt = properties.get("prompt")
    if isinstance(prompt, dict):
        prompt["description"] = ("本次画面或修改要求，按图片调用偏好的格式书写；"
                                  "有身份参考图时只写动作、镜头、场景、光线和用户明确要求的变化，"
                                  "不要重复或改写参考图已有的固定外貌；自拍写明 selfie。")
    return description, parameters


def core_image_tools(session, offered):
    """Keep role-disabled image tools out of this request's private ToolSet."""
    present = [item.spec.action for item in offered if item.spec.action in IMAGE_ACTIONS]
    if not present:
        filtered = []
        diagnostics = getattr(getattr(session.application, "tools", None), "catalog_for_with_diagnostics", None)
        if callable(diagnostics):
            try:
                _available, filtered = diagnostics(session.context)
                filtered = [item for item in filtered if item.get("action") in IMAGE_ACTIONS]
            except Exception:
                filtered = []
        logger.info("[CompanionV2][image] stage=catalog offered=%s image_actions=0 filtered=%s",
                    len(offered), filtered[:4])
        return offered, None
    try:
        values = effective_settings(session.application.runtime, session.context)
        denial = image_request_denial(session.application.runtime, session.context, values)
    except Exception as error:
        logger.warning("[CompanionV2] 核心生图偏好暂不可用 error_type=%s", type(error).__name__)
        return tuple(item for item in offered if item.spec.action not in IMAGE_ACTIONS), None
    if denial is not None:
        logger.info("[CompanionV2][image] stage=policy denial=%s actions=%s kept=0",
                    denial[0], present)
        return tuple(item for item in offered if item.spec.action not in IMAGE_ACTIONS), None
    logger.info("[CompanionV2][image] stage=keep mode=%s actions=%s", values["trigger_mode"], present)
    return offered, values


def _reference_date(session):
    prepared = session.prepared
    original = prepared.get("native_input") if isinstance(prepared, Mapping) else None
    at = getattr(original, "at", None) or session.application.tasks.now()
    timezone_name = getattr(session.application.messages, "timezone_name", "Asia/Shanghai")
    snapshot = prepared.get("perception_context") if isinstance(prepared, Mapping) else None
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except ValueError:
            snapshot = None
    clock = snapshot.get("current_local_time") if isinstance(snapshot, Mapping) else None
    if isinstance(clock, Mapping):
        timezone_name = clock.get("timezone") or timezone_name
    return at.astimezone(ZoneInfo(timezone_name)).date().isoformat()


def _identity_reference_guidance(material):
    """Tell the model when an identity anchor is available without copying IDs."""
    if not isinstance(material, Mapping):
        return ""
    references = material.get("references")
    if not isinstance(references, (list, tuple)):
        return ""
    has_identity = False
    for item in references:
        if not isinstance(item, Mapping):
            continue
        roles = item.get("roles", ())
        roles = {getattr(role, "value", role) for role in roles} if isinstance(roles, (list, tuple, set)) else set()
        if "identity" not in roles:
            continue
        has_identity = True
    if not has_identity:
        return ""
    return (
        "当前参考材料有可用的 identity 身份参考；其中标注 base_identity 的素材应作为主身份锚点。"
        "角色本人自拍、穿搭照或需要保持人物一致时，"
        "优先调用 image.edit，使用 companion_v2_context 中对应的 reference_ids，"
        "reference_indices=[]，并按所选顺序填写 reference_roles=[\"identity\"]。"
        "只有没有可用身份参考，或用户明确要求全新角色时才使用 image.generate。"
        "选用身份参考后，prompt 只写动作、镜头、场景、光线和用户明确要求的变化；"
        "不要重复发色、眼睛、脸型、发饰等已由参考图提供的固定外貌，也不要无故换脸。"
    )


def inject_core_image_prompt(session, request, offered, values):
    """Keep configuration in system and per-turn hints in the turn suffix."""
    session.state.pop("_image_reference_material", None)
    actions = tuple(item.spec.action for item in offered if item.spec.action in IMAGE_ACTIONS)
    if not actions or values is None:
        set_system_instruction(request, "image_output")
        _record_turn_note(session, "")
        return
    from .core_reply_tools import native_tool_name

    reference_limit = None
    if "image.edit" in actions:
        try:
            from .model_route_material import image_edit_reference_limit
            reference_limit = image_edit_reference_limit(
                getattr(session.application, "native_tool_port", None))
        except Exception:
            reference_limit = None

    descriptions = "\n".join(native_tool_name(action) + "：" + _PURPOSES[action] for action in actions)
    rule_hint = ""
    if values["trigger_mode"] == "rules":
        state = getattr(session, "state", None)
        command = state.get("functional_command") if isinstance(state, Mapping) else None
        command_intent = getattr(command, "intent", None)
        prepared = getattr(session, "prepared", None)
        original = prepared.get("native_input") if isinstance(prepared, Mapping) else None
        text = getattr(original, "text", "")
        matched = command_intent in {"image_generate", "image_edit"} or is_rule_image_request(
            text, has_images=bool(getattr(original, "images", ())))
        if matched:
            rule_hint = (
                "本轮用户文本已命中图片请求规则。若这句话确实是在要新图、自拍或改图，"
                "直接调用对应图片工具，不要只回一段描述或反复确认。"
            )
    # The rule match is a per-turn signal. Keeping it in system would flip the
    # whole cached prefix on every image request, so it rides the turn suffix
    # applied after the host clears turn context.
    _record_turn_note(session, rule_hint)
    instruction = "\n".join(part for part in (
        prompt_instruction(values),
        "本轮图片工具：\n" + descriptions, _DELIVERY) if part)
    if reference_limit is not None:
        instruction += (f"\n当前生图改图路线最多接收 {reference_limit} 张参考图。"
                        "需要保持角色身份时优先选择 identity；如果上限不足，不要自动同时提交身份图和穿搭图，"
                        "将其余服装、姿态或场景要求写进 prompt。")
    if "image.edit" in actions:
        instruction += (
            "\n本轮参考素材位于 companion_v2_context.image_reference_material。"
            "角色自拍有可用身份锚点时，可通过改图工具的 reference_ids 与 reference_roles=[\"identity\"] 保持人物一致；"
            "仅采用已存素材时用 reference_indices=[]，避免带入本轮无关附图。"
            "没有可用身份图时，依照已知角色外观使用生成工具；只有改图工具可用且没有任何参考图时，"
            "简短说明需要图片参考，不猜素材 ID。\n" + IMAGE_REFERENCE_INSTRUCTION
        )
        owner = getattr(session.application.messages, "reference_materials", None)
        if owner is not None:
            try:
                material = owner.prompt_material(session.context, local_date=_reference_date(session))
                if isinstance(material, dict) and (material.get("references") or material.get("wardrobe")):
                    session.state["_image_reference_material"] = material
                    route_guidance = _identity_reference_guidance(material)
                    if route_guidance:
                        instruction += "\n" + route_guidance
            except Exception as error:
                logger.warning("[CompanionV2] 核心生图参考资料暂不可用 error_type=%s", type(error).__name__)
    set_system_instruction(request, "image_output", instruction)
