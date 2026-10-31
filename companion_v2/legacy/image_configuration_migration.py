"""Migrate the V1 image backend queue and role-owned prompt preferences."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


IMAGE_CONFIGURATION_SOURCES = frozenset({
    "photo_action_config.photo_generation_backend",
    "photo_action_config.external_image_api_endpoints",
    "photo_action_config.external_image_api_platform",
    "photo_action_config.EXTERNAL_IMAGE_API_BASE_URL",
    "photo_action_config.EXTERNAL_IMAGE_API_KEY",
    "photo_action_config.EXTERNAL_IMAGE_API_MODEL",
    "photo_action_config.external_image_api_size",
    "photo_action_config.external_image_api_timeout_seconds",
    "photo_action_config.external_image_api_custom_headers",
    "photo_action_config.external_image_download_proxy",
    "photo_action_config.external_image_download_use_environment_proxy",
    "photo_action_config.enable_backup_external_image_api",
    "photo_action_config.backup_external_image_api_platform",
    "photo_action_config.BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
    "photo_action_config.BACKUP_EXTERNAL_IMAGE_API_KEY",
    "photo_action_config.BACKUP_EXTERNAL_IMAGE_API_MODEL",
    "photo_action_config.backup_external_image_api_size",
    "photo_action_config.backup_external_image_api_timeout_seconds",
    "photo_action_config.backup_external_image_api_custom_headers",
    "photo_action_config.custom_photo_tool_name",
    "photo_action_config.custom_photo_tool_prompt_param",
    "photo_action_config.custom_photo_tool_kind_param",
    "photo_action_config.custom_photo_tool_reference_param",
    "photo_action_config.custom_photo_tool_extra_params",
    "photo_action_config.COMFYUI_TEXT2IMG_WORKFLOW_NAME",
    "photo_action_config.COMFYUI_SELFIE_WORKFLOW_NAME",
    "photo_action_config.enable_photo_reference_image",
    "photo_action_config.photo_generation_prompt_format",
    "photo_action_config.photo_generation_style",
    "photo_action_config.photo_generation_style_custom_prompt",
    "photo_action_config.photo_generation_negative_prompt_mode",
    "photo_action_config.photo_generation_negative_prompt",
    "photo_action_config.photo_generation_text2img_negative_prompt",
    "photo_action_config.photo_generation_selfie_negative_prompt",
    "photo_action_config.photo_generation_edit_negative_prompt",
    "photo_action_config.photo_generation_text2img_fixed_prompt",
    "photo_action_config.photo_generation_selfie_fixed_prompt",
    "photo_action_config.photo_generation_edit_fixed_prompt",
    "photo_action_config.photo_generation_fixed_prompt",
    "photo_action_config.photo_generation_scene_presets",
})

_PREFIX = "photo_action_config."
_MISSING = object()
_PLATFORMS = frozenset({
    "auto", "openai", "openrouter", "agnes", "sensenova", "minimax",
    "bailian", "modelscope", "doubao", "gemini",
})
_PLATFORM_ALIASES = {
    "openai-compatible": "openai", "openai_compatible": "openai",
    "openai兼容": "openai", "兼容": "openai", "external": "openai",
    "open-router": "openrouter", "open_router": "openrouter",
    "openrouter.ai": "openrouter", "dashscope": "bailian", "aliyun": "bailian",
    "modelstudio": "bailian", "model_studio": "bailian", "百炼": "bailian",
    "阿里云百炼": "bailian", "通义万相": "bailian", "model_scope": "modelscope",
    "魔搭": "modelscope", "魔搭社区": "modelscope", "api-inference": "modelscope",
    "volcengine": "doubao", "volces": "doubao", "ark": "doubao", "豆包": "doubao",
    "火山": "doubao", "火山引擎": "doubao", "seedream": "doubao", "seed": "doubao",
    "google": "gemini", "google-ai": "gemini", "google_ai": "gemini",
    "generativelanguage": "gemini", "nano-banana": "gemini",
    "sense-nova": "sensenova", "日日新": "sensenova", "商汤日日新": "sensenova",
    "minimaxi": "minimax", "minimax-ai": "minimax", "minimax_ai": "minimax", "海螺": "minimax",
    "agnes-ai": "agnes", "agnes_ai": "agnes", "agnes-image": "agnes", "sapiens": "agnes",
}
# V1 never exposed an image quality knob.  A migrated endpoint therefore starts
# on the service default instead of claiming ``standard``, a value only older
# dall-e models accept; a quality the operator did write is kept as is.
#
# The save path owns the same vocabulary (``IMAGE_QUALITIES`` in
# ``astrbot_host/native_extensions/image_native/output_configuration.py``) and this
# package may not import ``astrbot_host`` (``companion_v2.foundation.isolation``),
# so the two sets stay separate.
# ``test_quality_vocabularies_match_the_save_path_exactly`` compares the sets in
# both directions (the save side also accepts "" for "use the service default"),
# so a value added to only one side fails there: a migration-only value would make
# the save path reject the whole ``image.output`` write, and a save-only value
# would be downgraded here into a spurious ``invalid_value`` difference.
_IMAGE_QUALITIES = frozenset({"auto", "low", "medium", "high", "standard", "hd"})


@dataclass(frozen=True)
class ImageConfigurationMigration:
    preferences: dict
    preference_sources: dict[str, tuple[str, ...]]
    output_changes: dict
    output_sources: dict[str, tuple[str, ...]]
    differences: tuple[dict, ...]


def _source(config: dict, source: str):
    parts = source.split(".")
    current = config
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            current = _MISSING
            break
        current = current[part]
    if current is not _MISSING:
        return True, current
    if source in config:
        return True, config[source]
    return False, None


def _boolean(value) -> bool:
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on", "enabled", "开启", "启用", "是"}:
            return True
        if normalized in {"false", "0", "no", "off", "disabled", "关闭", "停用", "否", ""}:
            return False
    raise ValueError("旧开关值无法识别")


def _string(value, field, *, limit=4000):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError(f"{field} 不是有效文本")
    return value.strip()


def _platform(value, base_url="", model=""):
    text = str(value or "auto").strip().lower()
    platform = _PLATFORM_ALIASES.get(text, text)
    if platform not in _PLATFORMS:
        raise ValueError("旧在线平台没有 V2 对应协议")
    if platform == "auto":
        host = re.sub(r"^https?://", "", str(base_url).lower()).split("/", 1)[0]
        model = str(model).lower()
        if "minimax" in host or model in {"image-01", "image-01-live"}:
            return "minimax"
        if "openrouter.ai" in host:
            return "openrouter"
        if "agnes-ai.com" in host or model.startswith("agnes-image-"):
            return "agnes"
        if "sensenova.cn" in host or model.startswith("senova-"):
            return "sensenova"
        if "volces.com" in host or "volcengine" in host:
            return "doubao"
        if "dashscope.aliyuncs.com" in host:
            return "bailian"
        if "modelscope.cn" in host:
            return "modelscope"
        if "generativelanguage.googleapis.com" in host:
            return "gemini"
    return platform


def _size(value, item):
    """Return executable dimensions and retain V1's service-specific label.

    V1 accepted labels such as ``1K`` and ``auto`` in addition to WxH.  Those
    labels are meaningful to some providers, so a failed integer conversion
    must not discard the whole endpoint.  V2 executes with dimensions where a
    provider does not accept the label and keeps the original label for the
    provider-specific request/UI.
    """
    label = str(value or item.get("size_label", "") or "").strip()
    width, height = item.get("width"), item.get("height")
    if width is None or height is None:
        match = re.fullmatch(r"\s*(\d{2,5})\s*[xX×]\s*(\d{2,5})\s*", label)
        if match:
            width, height = map(int, match.groups())
        elif isinstance(value, dict):
            width, height = value.get("width"), value.get("height")
        else:
            tier = re.fullmatch(r"(?i)([1-4])\s*K", label)
            if tier:
                width = height = int(tier.group(1)) * 1024
                label = f"{tier.group(1)}K"
            elif label.lower() in {"", "auto", "default", "original"}:
                width = height = 1024
    if type(width) is not int or type(height) is not int or not (64 <= width <= 8192 and 64 <= height <= 8192):
        raise ValueError("旧尺寸不能无损映射为 V2 的宽高范围")
    return width, height, label or f"{width}x{height}"


def _headers(value):
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{"):
            try:
                value = json.loads(text)
            except ValueError as error:
                raise ValueError("自定义请求头 JSON 无法解析") from error
        else:
            headers = {}
            for line in text.splitlines():
                if not line.strip():
                    continue
                name, separator, header_value = line.partition(":")
                if not separator:
                    raise ValueError("自定义请求头应为每行一个 Header: Value")
                headers[name.strip()] = header_value.strip()
            value = headers
    if type(value) is not dict or len(value) > 32:
        raise ValueError("自定义请求头格式无效")
    result = {}
    for name, header_value in value.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,80}", name)
                or name.lower() in {"host", "content-length", "transfer-encoding"}
                or not isinstance(header_value, str) or len(header_value) > 2000
                or "\r" in header_value or "\n" in header_value or "\x00" in header_value):
            raise ValueError("自定义请求头含有无效名称或值")
        result[name] = header_value
    return result


def _endpoints(raw, *, reference_images, source, differences):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except ValueError:
            raw = [{"base_url": line.strip()} for line in raw.splitlines() if line.strip()]
    if isinstance(raw, dict):
        raw = raw.get("items", raw.get("endpoints", raw.get("apis", [])))
    if not isinstance(raw, list):
        differences.append({"field": "image.output.endpoints", "kind": "invalid_value",
                            "explain": "V1 在线生图队列不是列表；未自动写入端点。"})
        return []
    result = []
    for index, item in enumerate(raw[:24]):
        if not isinstance(item, dict):
            differences.append({"field": f"image.output.endpoints.{index + 1}", "kind": "invalid_value",
                                "explain": "此 V1 端点不是对象，已跳过。"})
            continue
        name = str(item.get("name", item.get("label", item.get("title", f"在线 API {index + 1}"))) or "").strip()
        base_url = str(item.get("base_url", item.get("api_base", item.get("api_base_url", item.get("url", "")))) or "").strip()
        model = str(item.get("model", item.get("model_name", "")) or "").strip()
        if not name or len(name) > 240 or len(base_url) > 2048 or len(model) > 240:
            differences.append({"field": f"image.output.endpoints.{index + 1}", "kind": "invalid_value",
                                "explain": "端点名称、地址或模型标识无效，已跳过。"})
            continue
        try:
            platform = _platform(item.get("platform", item.get("external_image_api_platform", "auto")), base_url, model)
            width, height, size_label = _size(item.get("size", item.get("image_size", "1024x1024")), item)
            timeout = item.get("timeout_seconds", item.get("timeout", 180))
            if type(timeout) is not int or not 1 <= timeout <= 1800:
                raise ValueError("旧端点超时不在 V2 支持范围内")
            enabled = _boolean(item.get("enabled", item.get("enable", item.get("active", True))))
            api_key = item.get("api_key", item.get("key", item.get("token", "")))
            if not isinstance(api_key, str) or len(api_key) > 4096:
                raise ValueError("旧端点密钥格式无效")
            raw_headers = item.get("custom_headers", item.get("headers", ""))
            headers = _headers(raw_headers)
            ratio = str(item.get("ratio", item.get("aspect_ratio", "")) or "").strip()
            if ratio and not re.fullmatch(r"\d{1,5}:\d{1,5}", ratio):
                raise ValueError("画面比例格式无效")
            quality = str(item.get("quality", item.get("image_quality", "")) or "").strip().lower()
            if quality and quality not in _IMAGE_QUALITIES:
                differences.append({"field": f"image.output.endpoints.{index + 1}.quality",
                                    "kind": "invalid_value",
                                    "explain": "旧配置中的图片质量不属于 V2 支持的取值，已改为服务端默认。"})
                quality = ""
        except (TypeError, ValueError, OverflowError) as error:
            differences.append({"field": f"image.output.endpoints.{index + 1}", "kind": "invalid_value",
                                "explain": f"端点已保留为待核对项，未迁入执行队列：{error}。"})
            continue
        result.append({
            "id": f"legacy-image-{index + 1:02d}", "name": name, "enabled": enabled,
            "platform": platform, "model": model, "width": width, "height": height,
            "quality": quality, "response_format": "",
            "max_reference_images": 1 if reference_images else 0,
            "base_url": base_url, "api_key": api_key, "timeout_seconds": timeout,
            "custom_headers": headers, "ratio": ratio, "size_label": size_label,
        })
    if len(raw) > 24:
        differences.append({"field": "image.output.endpoints", "kind": "limit_changed",
                            "explain": "V1 队列超过 V2 的 24 个端点上限，超出部分没有自动写入。"})
    return result


def _single_endpoint(config, prefix, *, index, reference_images, differences):
    def read(name):
        return _source(config, _PREFIX + name)

    api_prefix = "" if index == 0 else "backup_"
    enabled = True
    if index:
        present, raw_enabled = read("enable_backup_external_image_api")
        if present:
            try:
                enabled = _boolean(raw_enabled)
            except ValueError as error:
                differences.append({"field": "image.output.endpoints", "kind": "invalid_value",
                                    "explain": f"备用 API 开关无效：{error}。"})
                enabled = False
        if not enabled:
            return None
    name = "主在线 API" if index == 0 else f"备用在线 API {index + 1}"
    keys = {
        "platform": f"{api_prefix}external_image_api_platform",
        "base_url": "BACKUP_EXTERNAL_IMAGE_API_BASE_URL" if index else "EXTERNAL_IMAGE_API_BASE_URL",
        "api_key": "BACKUP_EXTERNAL_IMAGE_API_KEY" if index else "EXTERNAL_IMAGE_API_KEY",
        "model": "BACKUP_EXTERNAL_IMAGE_API_MODEL" if index else "EXTERNAL_IMAGE_API_MODEL",
        "size": f"{api_prefix}external_image_api_size",
        "timeout_seconds": f"{api_prefix}external_image_api_timeout_seconds",
        "custom_headers": f"{api_prefix}external_image_api_custom_headers",
    }
    endpoint = {"name": name, "enabled": enabled}
    found = False
    for field, source_name in keys.items():
        present, value = read(source_name)
        if present:
            endpoint[field] = value
            found = found or value not in (None, "")
    if not found:
        return None
    converted = _endpoints([endpoint], reference_images=reference_images,
                           source=_PREFIX + "external_image_api_endpoints", differences=differences)
    if not converted:
        return None
    converted[0]["id"] = f"legacy-image-{index + 1:02d}"
    return converted[0]


def _scene_presets(raw):
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except ValueError:
            values = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                name, separator, prompt = line.partition(":" if ":" in line else "：")
                if not separator or not name.strip() or not prompt.strip():
                    raise ValueError("场景预设应为 JSON 列表或每行“名称:提示词”")
                values.append({"name": name.strip(), "prompt": prompt.strip()})
            raw = values
    if isinstance(raw, dict):
        raw = raw.get("presets", raw.get("items", raw))
        if isinstance(raw, dict):
            raw = [{"name": name, "prompt": prompt} for name, prompt in raw.items()]
    if not isinstance(raw, list) or len(raw) > 12:
        raise ValueError("场景预设数量或格式不符合 V2 的 12 项限制")
    result, names = [], set()
    for index, item in enumerate(raw):
        if isinstance(item, str):
            name, separator, prompt = item.partition(":" if ":" in item else "：")
            item = {"name": name.strip(), "prompt": prompt.strip()} if separator else None
        if not isinstance(item, dict):
            raise ValueError("场景预设需要名称和画面提示词")
        name = str(item.get("name", item.get("title", "")) or "").strip()
        prompt = str(item.get("prompt", item.get("value", "")) or "").strip()
        if not name or len(name) > 24 or not prompt or len(prompt) > 800 or name in names:
            raise ValueError("场景名称或画面提示词无效，或存在重名")
        identifier = str(item.get("id", "") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,31}", identifier):
            identifier = f"legacy-scene-{index + 1:02d}"
        result.append({"id": identifier, "name": name, "prompt": prompt})
        names.add(name)
    return result


def convert_image_configuration(config: dict) -> ImageConfigurationMigration:
    """Convert image preferences and API endpoints without clipping source data."""
    if not isinstance(config, dict):
        return ImageConfigurationMigration({}, {}, {}, {}, ())
    differences = []
    preferences, preference_sources = {}, {}
    output_changes, output_sources = {}, {}

    def read(name):
        return _source(config, _PREFIX + name)

    mappings = {
        "photo_generation_prompt_format": ("prompt_format", {
            "traditional": "traditional", "natural_language": "natural", "natural": "natural", "nai": "nai",
        }),
        "photo_generation_negative_prompt_mode": ("negative_mode", {
            "safe_default": "safe_default", "merge": "merge", "replace": "replace",
        }),
        "photo_generation_negative_prompt": ("negative_prompt", None),
        "photo_generation_text2img_negative_prompt": ("text2img_negative_prompt", None),
        "photo_generation_selfie_negative_prompt": ("selfie_negative_prompt", None),
        "photo_generation_edit_negative_prompt": ("edit_negative_prompt", None),
        "photo_generation_style_custom_prompt": ("style_prompt", None),
    }
    for source_name, (target, aliases) in mappings.items():
        present, raw = read(source_name)
        if not present:
            continue
        source_id = _PREFIX + source_name
        try:
            if aliases is not None:
                if not isinstance(raw, str) or raw.strip().lower() not in aliases:
                    raise ValueError("旧选项在 V2 中没有对应值")
                value = aliases[raw.strip().lower()]
            else:
                value = _string(raw, source_name)
        except (TypeError, ValueError) as error:
            differences.append({"field": "conversation.image_output." + target, "kind": "invalid_value",
                                "explain": f"旧图片偏好未迁移：{error}。"})
            continue
        preferences[target] = value
        preference_sources[target] = (source_id,)

    present, style = read("photo_generation_style")
    if present:
        text = str(style or "").strip().lower()
        normalized = {"真实": "realistic", "写实": "realistic", "realistic": "realistic",
                      "二次元": "anime", "动漫": "anime", "anime": "anime",
                      "其他": "custom", "自定义": "custom", "custom": "custom"}.get(text)
        if normalized is None:
            normalized = "custom"
            custom = str(style or "").strip()
            if custom and "style_prompt" not in preferences:
                preferences["style_prompt"] = custom[:4000]
                preference_sources["style_prompt"] = (_PREFIX + "photo_generation_style",)
            differences.append({"field": "conversation.image_output.style", "kind": "mapped_to_custom",
                                "explain": "旧风格标签不属于 V2 内置选项，已保留为自定义风格提示词。"})
        preferences["style"] = normalized
        preference_sources["style"] = (_PREFIX + "photo_generation_style",)

    present, global_fixed = read("photo_generation_fixed_prompt")
    global_fixed = str(global_fixed or "").strip() if present else ""
    for source_name, target in (
        ("photo_generation_text2img_fixed_prompt", "text2img_fixed_prompt"),
        ("photo_generation_selfie_fixed_prompt", "selfie_fixed_prompt"),
        ("photo_generation_edit_fixed_prompt", "edit_fixed_prompt"),
    ):
        field_present, raw = read(source_name)
        specific = str(raw or "").strip() if field_present else ""
        value = "\n".join(item for item in (global_fixed, specific) if item)
        if value:
            if len(value) > 4000:
                differences.append({"field": "conversation.image_output." + target, "kind": "too_long",
                                    "explain": "固定提示词合并后超过 V2 长度限制，未自动迁移。"})
                continue
            preferences[target] = value
            preference_sources[target] = tuple(source for source, exists in (
                (_PREFIX + "photo_generation_fixed_prompt", bool(global_fixed)),
                (_PREFIX + source_name, field_present and bool(specific)),
            ) if exists)

    present, scenes = read("photo_generation_scene_presets")
    if present and scenes not in (None, "", [], {}):
        try:
            preferences["scene_presets_json"] = json.dumps(
                _scene_presets(scenes), ensure_ascii=False, separators=(",", ":"))
            preference_sources["scene_presets_json"] = (_PREFIX + "photo_generation_scene_presets",)
        except (TypeError, ValueError) as error:
            differences.append({"field": "conversation.image_output.scene_presets_json", "kind": "invalid_value",
                                "explain": f"旧场景预设未迁移：{error}。"})

    reference_images = True
    present, raw_reference = read("enable_photo_reference_image")
    if present:
        try:
            reference_images = _boolean(raw_reference)
        except ValueError as error:
            differences.append({"field": "image.output.endpoints[].max_reference_images", "kind": "invalid_value",
                                "explain": f"参考图开关无法识别，端点按允许一张参考图迁移：{error}。"})

    backend_present, raw_backend = read("photo_generation_backend")
    backend = None
    if backend_present:
        raw_text = str(raw_backend or "").strip().lower()
        backend_aliases = {"auto": "auto", "external": "online_api", "online_api": "online_api",
                           "api": "online_api", "comfyui": "comfyui",
                           "sd_webui": "sd_webui"}
        backend = backend_aliases.get(raw_text)
        if backend is None:
            differences.append({"field": "image.output.backend", "kind": "manual_mapping",
                                "explain": f"V1 生图方式“{raw_text or '空'}”没有可确认的 V2 等价后端，已保留原设置并等待手动选择。"})
        else:
            output_changes["backend"] = backend
            output_sources["backend"] = (_PREFIX + "photo_generation_backend",)
        # Keep the original selector for the configuration page and upgrade
        # diagnostics.  ``backend`` remains the executable V2 route.
        output_changes["legacy_backend"] = raw_text
        output_sources["legacy_backend"] = (_PREFIX + "photo_generation_backend",)

    legacy_options = {}
    tool_names = {
        "name": "custom_photo_tool_name",
        "prompt_param": "custom_photo_tool_prompt_param",
        "kind_param": "custom_photo_tool_kind_param",
        "reference_param": "custom_photo_tool_reference_param",
        "extra_params": "custom_photo_tool_extra_params",
    }
    tool_values = {}
    for target, source_name in tool_names.items():
        field_present, raw = read(source_name)
        if not field_present:
            continue
        try:
            value = _string(raw, source_name, limit=8000)
        except ValueError as error:
            differences.append({"field": "image.output.legacy_options.tool_call." + target,
                                "kind": "invalid_value", "explain": f"舊工具调用参数未保存：{error}。"})
            continue
        if value:
            tool_values[target] = value
    if tool_values:
        legacy_options["tool_call"] = tool_values

    workflow_names = {}
    for target, source_name in (("generate", "COMFYUI_TEXT2IMG_WORKFLOW_NAME"),
                                ("selfie", "COMFYUI_SELFIE_WORKFLOW_NAME")):
        field_present, raw = read(source_name)
        if field_present and raw not in (None, ""):
            try:
                workflow_names[target] = _string(raw, source_name, limit=240)
            except ValueError as error:
                differences.append({"field": "image.output.legacy_options.comfyui_workflow_names." + target,
                                    "kind": "invalid_value", "explain": f"旧工作流名称未保存：{error}。"})
    if workflow_names:
        legacy_options["comfyui_workflow_names"] = workflow_names

    if legacy_options:
        output_changes["legacy_options"] = legacy_options
        output_sources["legacy_options"] = tuple(sorted(
            _PREFIX + source_name for source_name in (
                *tool_names.values(), "COMFYUI_TEXT2IMG_WORKFLOW_NAME", "COMFYUI_SELFIE_WORKFLOW_NAME")
            if _source(config, _PREFIX + source_name)[0]))
        if tool_values:
            differences.append({"field": "image.output.legacy_options.tool_call", "kind": "preserved_not_executable",
                                "explain": "V1 自定义函数工具的名称和参数已保存在旧版迁移配置中；V2 需由已注册的 Image 能力提供方执行，不能直接按旧参数名调用。"})
        if workflow_names:
            differences.append({"field": "image.output.legacy_options.comfyui_workflow_names", "kind": "preserved_not_executable",
                                "explain": "旧 ComfyUI 工作流名称已保留；V2 需要导入并绑定实际工作流图，名称本身不能替代工作流配置。"})

    proxy_present, raw_proxy = read("external_image_download_proxy")
    env_present, raw_env_proxy = read("external_image_download_use_environment_proxy")
    if proxy_present or env_present:
        try:
            proxy = _string(raw_proxy, "external_image_download_proxy", limit=2048) if proxy_present else ""
            use_environment_proxy = _boolean(raw_env_proxy) if env_present else False
            output_changes["network"] = {"download_proxy": proxy,
                                         "use_environment_proxy": use_environment_proxy}
            output_sources["network"] = tuple(_PREFIX + name for name, present in (
                ("external_image_download_proxy", proxy_present),
                ("external_image_download_use_environment_proxy", env_present)) if present)
        except (TypeError, ValueError) as error:
            differences.append({"field": "image.output.network", "kind": "invalid_value",
                                "explain": f"在线图片下载代理配置未迁移：{error}。"})

    present, raw_endpoints = read("external_image_api_endpoints")
    if present:
        endpoints = _endpoints(raw_endpoints, reference_images=reference_images,
                               source=_PREFIX + "external_image_api_endpoints", differences=differences)
        endpoint_sources = [_PREFIX + "external_image_api_endpoints"]
    else:
        endpoints = []
        for index in range(2):
            item = _single_endpoint(config, "", index=index,
                                    reference_images=reference_images, differences=differences)
            if item:
                endpoints.append(item)
        endpoint_sources = [source for source in IMAGE_CONFIGURATION_SOURCES
                            if source.endswith(("EXTERNAL_IMAGE_API_BASE_URL", "EXTERNAL_IMAGE_API_KEY",
                                                "EXTERNAL_IMAGE_API_MODEL", "external_image_api_size",
                                                "external_image_api_timeout_seconds", "external_image_api_platform",
                                                "external_image_api_custom_headers", "enable_backup_external_image_api",
                                                "backup_external_image_api_size", "backup_external_image_api_timeout_seconds",
                                                "backup_external_image_api_platform", "backup_external_image_api_custom_headers"))
                            and _source(config, source)[0]]
    if endpoints:
        output_changes["endpoints"] = endpoints
        output_sources["endpoints"] = tuple(sorted(endpoint_sources))

    if backend is not None:
        online = backend in {"auto", "online_api"}
        if online and endpoints:
            online_routes = ["online:" + item["id"] for item in endpoints]
            routes = online_routes
        elif backend == "comfyui":
            routes = ["comfyui"]
        elif backend == "sd_webui":
            routes = ["sd_webui"]
        else:
            routes = []
        output_changes["operation_routes"] = {"generate": list(routes), "edit": list(routes)}
        output_sources["operation_routes"] = tuple(sorted(set(output_sources.get("backend", ()))
            | set(output_sources.get("endpoints", ()))))
        executable_online = any(item["enabled"] and item["base_url"] and item["model"]
            and item["platform"] in {"auto", "openai", "openrouter", "agnes", "sensenova", "minimax", "doubao"}
            for item in endpoints)
        output_changes["enabled"] = bool(online and executable_online)
        output_sources["enabled"] = output_sources["operation_routes"]
        if backend in {"comfyui", "sd_webui"}:
            differences.append({"field": "image.output.enabled", "kind": "incomplete_backend",
                                "explain": "旧本地生图由独立服务提供；V2 已保留所选方式，但需绑定实际连接和工作流后再启用。"})
        elif online and endpoints and not executable_online:
            differences.append({"field": "image.output.enabled", "kind": "incomplete_endpoint",
                                "explain": "旧在线端点已保留，但没有当前 V2 可执行的完整协议与连接；确认端点后再启用。"})
        if online and not endpoints:
            differences.append({"field": "image.output.enabled", "kind": "incomplete_endpoint",
                                "explain": "旧设置选择了在线生图，但没有可迁移端点；V2 执行器保持关闭，补齐端点后再启用。"})
    elif backend_present:
        # An unrelated V2 default route must not impersonate an unsupported V1
        # provider (notably the separately installed SDGen plugin).
        output_changes.update(backend="auto", enabled=False,
                              operation_routes={"generate": [], "edit": []})
        for field in ("backend", "enabled", "operation_routes"):
            output_sources[field] = (_PREFIX + "photo_generation_backend",)

    return ImageConfigurationMigration(preferences, preference_sources, output_changes,
                                       output_sources, tuple(differences))


def _owns_output_backend(current) -> bool:
    """Whether the saved V2 image output already owns an executable configuration."""
    if not isinstance(current, dict):
        return True
    if current.get("enabled") is True or current.get("backend", "auto") != "auto":
        return True
    if current.get("legacy_backend"):
        return True
    if current.get("endpoints"):
        return True
    for name in ("sd_webui", "comfyui"):
        value = current.get(name)
        if not isinstance(value, dict):
            continue
        if any(value.get(key) for key in ("base_url", "api_key", "api_key_set", "parameters", "workflows")):
            return True
    return False


def _owns_download_proxy(current) -> bool:
    """Whether the operator already saved the V2 image download group.

    ``current`` is the V2 page projection.  A never-touched network group is
    projected as ``{"download_proxy_set": False}``, while every saved group also
    carries ``use_environment_proxy`` (the save path always persists both keys).
    Presence of that key -- not its value -- is what tells an explicit clear
    (``{"download_proxy_set": False, "use_environment_proxy": False}``) apart from
    "no V2 save yet", so a cleared proxy is preserved instead of being restored
    from V1.  A non-empty ``download_proxy`` is accepted as well so a raw saved
    configuration is recognized too.
    """
    network = current.get("network") if isinstance(current, dict) else None
    if not isinstance(network, dict):
        return False
    return ("use_environment_proxy" in network
            or bool(network.get("download_proxy") or network.get("download_proxy_set")))


def plan_image_output_changes(current, changes):
    """Decide which migrated V1 image groups may still be written into V2.

    The migration can be re-run, so it runs against an image output configuration
    the operator may already have edited in the new page.  Two mistakes are
    possible and must be avoided in opposite directions:

    * rewriting a group V2 already owns silently discards that edit -- the
      download proxy is the one group a V2-only save can set on its own, without
      any endpoint queue, so it cannot be inferred from the endpoint fields;
    * skipping the whole intent because one group is owned would strand an
      operator who has only touched that group: the endpoints of a not yet
      migrated V1 configuration would never arrive.

    A configuration V2 owns as a whole (a backend, a queue or a local connection)
    still preserves the complete intent, exactly as before; only a proxy saved on
    its own preserves that single group and lets the rest migrate.
    """
    if not isinstance(changes, dict) or not changes:
        return {}, ()
    if _owns_output_backend(current):
        return {}, ({"field": "image.output", "kind": "preserved_v2",
                     "before": "V1 图片模型与队列设置", "after": "V2 已有图片模型设置",
                     "explain": "V2 已保存图片模型、端点队列或后端连接，为避免覆盖，本次保留 V2 设置。"},)
    if "network" not in changes or not _owns_download_proxy(current):
        return dict(changes), ()
    writable = {field: value for field, value in changes.items() if field != "network"}
    explain = "V2 已保存在线图片下载代理设置（含显式清空），为避免覆盖，本次保留 V2 设置。"
    if writable:
        explain += "其余 V1 图片设置照常迁移。"
    return writable, ({"field": "image.output.network", "kind": "preserved_v2",
                       "before": "V1 在线图片下载代理", "after": "V2 已保存的在线图片下载代理设置",
                       "explain": explain},)
