"""Role image triggers and prompt preferences used by the native image path."""

import json
import re

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind

GROUP = OWNER = "conversation.image_output"
LAYERS = (ConfigLayer.INSTALLATION, ConfigLayer.ROLE)
CHOICES = {
    "trigger_mode": {"tool", "rules", "off"},
    "prompt_format": {"traditional", "natural", "nai"},
    "style": {"realistic", "anime", "custom"},
    "negative_mode": {"safe_default", "merge", "replace"},
    "request_permission": {"core_only", "everyone"},
}
DEFAULT_SCENE_PRESETS = (
    {"id": "home-night", "name": "睡前居家", "prompt": "cozy bedroom, warm bedside light, relaxed home clothing"},
    {"id": "campus-day", "name": "校园日常", "prompt": "natural campus daytime, candid everyday atmosphere"},
    {"id": "formal-event", "name": "正式场合", "prompt": "formal event setting, composed posture, refined clothing"},
)
SCENE_MARKER = re.compile(r"\[场景预设[:：]([^\]\r\n]{1,40})\]")

# The "rules" trigger adds a direct-call hint to clear image requests while
# keeping the enabled image tools stable in the request's tool catalog.
_IMAGE_RULE_VERBS = r"(?:画|绘制|生成|做|出|来|拍|发|给|整|搞|修|改|换|去掉|去除|添加|加上|调整|重画|再画)"
_IMAGE_RULE_NOUNS = r"(?:图|图片|照片|相片|自拍|头像|壁纸|插画|立绘|写真|画面|全身照|穿搭照|合照)"
_IMAGE_RULE_PATTERN = re.compile(
    rf"(?:{_IMAGE_RULE_VERBS}.{{0,8}}{_IMAGE_RULE_NOUNS}|{_IMAGE_RULE_NOUNS}.{{0,8}}{_IMAGE_RULE_VERBS})",
    re.IGNORECASE,
)
_IMAGE_RULE_ENGLISH = re.compile(
    r"\b(?:draw|paint|generate|render|make)\b.{0,24}\b(?:image|picture|photo|portrait|selfie|avatar|illustration)\b"
    r"|\b(?:image|picture|photo|portrait|selfie|avatar|illustration)\b.{0,24}\b(?:edit|change|remove|add|redraw|regenerate)\b",
    re.IGNORECASE,
)
_IMAGE_RULE_DIRECT = re.compile(
    r"(?:^|[，。！？!?；;\s])(?:我)?(?:给我|帮我|请|麻烦|可以|能不能|能|想|要)?\s*"
    r"(?:画|绘制|拍|生成|做|来|发|整|搞)(?:一|个|张|只|幅|下|些|点)?"
    r"|来(?:张|一张|幅)|生成(?:一|个|张|只|幅)"
    r"|(?:生图|画图|改图|修图|出图|做图|合照)"
    r"|(?:想看|看看|让我看|给我看|看下|看一下).{0,12}"
    r"(?:自拍|照片|图片|头像|穿搭|样子|你)"
    r"|(?:自拍|照片|头像|穿搭)(?:呢|吗|看看|发来|给我)",
    re.IGNORECASE,
)
_IMAGE_RULE_FOLLOWUP = re.compile(
    r"(?:再来一张|再画|继续画|重画|重新生成|再试试|再试一下|重试|"
    r"换个(?:姿势|背景|风格|衣服)|换一套衣服|"
    r"刚才那张|上一张|改成|修一下|去掉|加上|"
    r"(?:^|[，。！？!?；;\s])(?:继续|接着|再来|还要|想看|看看|当然|好啊|好呀|可以)"
    r"(?:$|[，。！？!?；;\s]))",
    re.IGNORECASE,
)


def is_rule_image_request(value, *, has_images=False):
    """Return whether a rule-mode turn should get the direct-call hint.

    This is a coarse, bounded prefilter, not a model substitute. False
    positives leave the final decision with the model; false negatives omit
    the hint but do not remove the enabled image tools.
    """

    text = " ".join(str(value or "").split())
    if not text:
        return False
    if (_IMAGE_RULE_PATTERN.search(text) or _IMAGE_RULE_DIRECT.search(text)
            or _IMAGE_RULE_ENGLISH.search(text)):
        return True
    if _IMAGE_RULE_FOLLOWUP.search(text):
        return True
    return bool(has_images and re.search(r"(?:改|修|换|去掉|去除|添加|加上|调整)", text))


DEFAULTS = {
    # 默认走规则引导：明确的图片请求会追加直接调用提示，工具目录保持稳定。
    "allow_user_requests": True, "trigger_mode": "rules",
    "group_enabled": True, "request_permission": "everyone",
    "proactive_probability": 0.35, "proactive_max_daily": 1,
    "prompt_format": "traditional", "style": "realistic", "style_prompt": "",
    "negative_mode": "safe_default", "negative_prompt": "",
    "text2img_fixed_prompt": "clean composition, environmental storytelling",
    "selfie_fixed_prompt": "stable facial features, natural selfie framing",
    "edit_fixed_prompt": "preserve unedited pixels, no unrelated redesign",
    "text2img_negative_prompt": "lowres, text, watermark",
    "selfie_negative_prompt": "inconsistent facial features",
    "edit_negative_prompt": "unrequested redesign, identity change",
    "scene_presets_json": json.dumps(DEFAULT_SCENE_PRESETS, ensure_ascii=False, separators=(",", ":")),
}


NEGATIVE_PROMPT_KINDS = ("text2img", "selfie", "edit")


def negative_prompt_default(values, kind):
    """Resolve one negative prompt, treating a blank override as unset.

    Editors and migrations can store an explicit empty string.  In
    ``safe_default`` mode such a blank value must not replace the built-in
    safety prompt, so only a non-blank configured value keeps precedence.
    """
    value = values.get(kind + "_negative_prompt")
    return value if type(value) is str and value.strip() else DEFAULTS[kind + "_negative_prompt"]


def negative_prompt_defaults(values):
    return {kind + "_negative_prompt": negative_prompt_default(values, kind)
            for kind in NEGATIVE_PROMPT_KINDS}


def configuration_fields():
    return tuple(FieldDefinition(GROUP + "." + key, GROUP, GROUP,
        ValueKind.BOOLEAN if type(value) is bool else ValueKind.INTEGER if type(value) is int
        else ValueKind.PROBABILITY if type(value) is float else ValueKind.TEXT, value,
        layers=LAYERS, owner_id=OWNER,
        **({"minimum": 0, "maximum": 1} if type(value) is float else
           {"minimum": -1, "maximum": 100} if type(value) is int else {}))
        for key, value in DEFAULTS.items())


class ImageOutputConfigurationOwner(SparseConfigurationOwner):
    owner_id = OWNER

    def write(self, target, fields, **kwargs):
        current = self.read(target, fields)
        values = {key.rsplit(".", 1)[-1]: item.value for key, item in current.fields.items()}
        values.update({key.rsplit(".", 1)[-1]: value for key, value in kwargs["changes"].items()})
        for key, choices in CHOICES.items():
            if values.get(key) not in choices:
                raise TaskError("image_output_configuration_invalid", "invalid image preference")
        if any(type(value) is str and len(value) > 4000 for value in values.values()):
            raise TaskError("image_output_configuration_invalid", "image prompt exceeds 4000 characters")
        parse_scene_presets(values.get("scene_presets_json", DEFAULTS["scene_presets_json"]))
        return super().write(target, fields, **kwargs)


def effective_settings(runtime, context):
    target = ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                          context.role_binding.role, bot_id=context.scope.bot_id)
    snapshot = ImageOutputConfigurationOwner(runtime).read(target, configuration_fields())
    values = {key.rsplit(".", 1)[-1]: item.value for key, item in snapshot.fields.items()}
    values = {**DEFAULTS, **values}
    values.update(negative_prompt_defaults(values))
    return values


def _is_core_actor(runtime, actor):
    """Check the installation tier for an authenticated actor.

    The image-output group is readable by standard users, but the optional
    ``core_only`` request policy must use the same durable tier lookup as the
    other permission gates.  An unavailable runtime or unknown subject is not
    core, so the policy fails closed instead of treating a lookup error as
    consent.
    """
    subject = getattr(actor, "subject", None)
    kind = getattr(subject, "kind", None)
    subject_id = getattr(subject, "subject_id", None)
    kind_value = getattr(kind, "value", kind)
    if runtime is None or not kind_value or not subject_id:
        return False
    row = runtime.read_one(
        "SELECT tier FROM subject_access_tiers WHERE subject_kind = ? AND subject_id = ?",
        (kind_value, subject_id))
    return row is not None and row["tier"] == "core"


def image_request_denial(runtime, context, values):
    """Return a stable error code and reason when a user image request is denied.

    Proactive generation is intentionally checked by the caller before this
    helper: the policy below governs requests made in a conversation, not the
    role's own proactive image workflow.
    """
    if not values.get("allow_user_requests") or values.get("trigger_mode") == "off":
        return ("image_requests_disabled",
                "image requests are disabled for this role")
    scope = getattr(context, "scope", None)
    if (getattr(scope, "kind", None) is ScopeKind.GROUP
            and values.get("group_enabled", True) is False):
        return ("group_image_requests_disabled",
                "image requests are disabled in group chats for this role")
    if values.get("request_permission", "everyone") == "core_only":
        if not _is_core_actor(runtime, getattr(context, "actor", None)):
            return ("image_requests_core_only",
                    "only the installation core administrator may request image generation")
    return None


def parse_scene_presets(value):
    if type(value) is not str or len(value) > 4000:
        raise TaskError("image_output_configuration_invalid", "scene presets must be bounded JSON text")
    try:
        presets = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise TaskError("image_output_configuration_invalid", "scene presets must be a JSON list") from error
    if type(presets) is not list or len(presets) > 12:
        raise TaskError("image_output_configuration_invalid", "scene presets allow at most 12 entries")
    ids, names = set(), set()
    for item in presets:
        if type(item) is not dict or set(item) != {"id", "name", "prompt"}:
            raise TaskError("image_output_configuration_invalid", "each scene preset needs an id, name and prompt")
        identifier, name, prompt = item["id"], item["name"], item["prompt"]
        if (type(identifier) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,31}", identifier)
                or type(name) is not str or not name.strip() or name != name.strip() or len(name) > 24
                or type(prompt) is not str or not prompt.strip() or prompt != prompt.strip() or len(prompt) > 800
                or identifier in ids or name in names):
            raise TaskError("image_output_configuration_invalid", "scene preset names and ids must be unique and bounded")
        ids.add(identifier)
        names.add(name)
    return tuple(presets)


def prompt_instruction(values):
    if not values["allow_user_requests"] or values["trigger_mode"] == "off":
        return "当前角色已关闭用户请求生图和改图；按本轮话题自然回应，图片请求可简短说明暂不能执行。"
    formats = {"traditional": "使用简洁的英文画面标签，以逗号分隔",
               "natural": "使用连贯的自然语言描述构图、人物与场景",
               "nai": "使用 NAI 标签格式，可用花括号表达强调，避免聊天句式"}
    trigger = (
        "用户用自然语言要新图片、画图、自拍、头像或改图时，使用本轮开放的图片工具完成，无需特定指令。"
        "“来张自拍看看”“想看你现在的穿搭”已经表达了看图需求；承接尚未完成的同一请求时，"
        "结合最近对话理解“看看”“当然”“继续”“再来一张”，不要求对方重复整句。"
        "需求清楚时可自然接话并执行；确实缺少影响画面的关键信息时再问一句。"
        "玩笑、讨论摄影或泛泛问近况仍按普通聊天理解，已完成的请求不因后续应声重复生图。"
        "上一轮图片工具明确失败且本轮用户要求重试时，重新调用工具；unknown 表示结果未确认，不能换调用编号重放同一操作。"
        "角色本人出镜时，按当前人设与已知对话展开外观、穿搭、动作和场景；"
        "近期明确换装或换景优先，缺少的细节可自然创作，不把生成画面说成现实观测。"
        "自拍画面在 prompt 中写明 selfie，以便应用角色的自拍提示词。"
        "图片请求以实际图片工具结果接续，不用反复确认、只说稍等或改邀其他活动代替。"
    )
    presets = parse_scene_presets(values.get("scene_presets_json", DEFAULTS["scene_presets_json"]))
    scene = ("可用场景预设：" + "、".join(item["name"] + "（ID:" + item["id"] + "）" for item in presets)
             + "。场景与用户要求吻合时，可在图片工具 prompt 中标记 [场景预设:名称]；"
               "宿主会替换为对应画面提示词，不要把标记写入解释或回复。" if presets else "")
    return "图片调用偏好：" + trigger + "\n图片 prompt 应" + formats[values["prompt_format"]] + "。" + scene


def apply_prompts(arguments, action, values):
    result = dict(arguments)
    original = result.get("prompt", "")
    presets = parse_scene_presets(values.get("scene_presets_json", DEFAULTS["scene_presets_json"]))
    selected_names = []
    for match in SCENE_MARKER.finditer(original):
        name = match.group(1).strip()
        if name not in selected_names:
            selected_names.append(name)
    original = SCENE_MARKER.sub("", original).strip()
    scene_prompts = [item["prompt"] for name in selected_names for item in presets if item["name"] == name]
    kind = "edit" if action != "image.generate" else (
        "selfie" if re.search(r"自拍|selfie|self.portrait", original, re.I) else "text2img")
    style = {"realistic": "photorealistic", "anime": "anime illustration", "custom": ""}[values["style"]]
    blocks = [values[kind + "_fixed_prompt"], style, values["style_prompt"], *scene_prompts, original]
    delimiter = "\n" if values["prompt_format"] == "natural" else ", "
    result["prompt"] = delimiter.join(block.strip() for block in blocks if block.strip())
    defaults = negative_prompt_default(values, kind)
    negative = defaults if values["negative_mode"] == "safe_default" else (
        delimiter.join(block for block in (defaults, values["negative_prompt"]) if block)
        if values["negative_mode"] == "merge" else values["negative_prompt"])
    # User exclusions remain attached even when replacing the system negative text.
    result["negative_prompt"] = delimiter.join(block for block in (negative, result.get("negative_prompt", "")) if block)
    return result
