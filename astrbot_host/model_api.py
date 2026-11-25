"""Authenticated model routing, TTS bindings and native image controls."""

import asyncio
import base64
import inspect
import json
import logging
import re
import sqlite3
from copy import deepcopy
from pathlib import Path
from time import monotonic
from uuid import uuid4

from astrbot.api.web import request
from companion_v2.configuration.models import ConfigLayer
from companion_v2.conversation.media.models import MAX_REFERENCE_IMAGES
from companion_v2.conversation.reply_management import ReplyManagement
from companion_v2.decision import LayaDecisionService, LayaSettingsStore
from companion_v2.decision.client import LayaClientError
from companion_v2.decision.service import safe_laya_client_error_reason
from companion_v2.decision.store import LayaSettingsError
from companion_v2.execution.models import TaskError, digest, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.model_gateway.management import ModelManagement
from companion_v2.model_gateway.pricing import (
    PricingSettingsError,
    PricingSettingsStore,
    effective_usd_cny_rate,
)
from companion_v2.model_gateway.pricing_catalog import PriceCatalogStore
from companion_v2.model_gateway.pricing_expr import validate_tiered_expr_rule
from companion_v2.proactive.search_settings import SearchSettingsError, SearchSettingsStore
from companion_v2.storage.runtime import StorageOpenError

from .management_identity import dashboard_operator
from .core_reply_configuration import CORE_MANAGED_FIELDS, project_core_reply_configuration
from .exchange_rate import ExchangeRateService, read_astrbot_http_proxy
from .pricing_inventory import (
    build_pricing_payload,
    build_provider_metadata_map,
    collect_provider_inventory,
    read_provider_stat_runtime_models,
)
from .pricing_sources import PricingCatalogService
from .memory_diagnostics import process_memory_snapshot
from .memory_linkage import read_external_memory_summary
from .maintenance_checks import read_host_checks
from .native_extensions.image_settings import read_image_settings, write_image_settings
from .settings import HostControlError
from .native_extensions.images import IMAGE_ACTIONS
from .reply_api import ReplyWebAPI
from .quota_api import QuotaWebEndpoints
from .service_balance import configured_balance_port, display_amount
from .token_sources import read_token_sources
from .host_token_usage import read_astrbot_token_usage
from .voice import _read_audio
from .fish_audio import MODEL_OPTIONS as FISH_AUDIO_MODEL_OPTIONS

logger = logging.getLogger(__name__)


def _probe_error_summary(error):
    """Return a bounded, URL/credential-free probe failure summary for logs."""
    text = str(error)
    text = re.sub(r"https?://[^\s]+", "[URL]", text, flags=re.I)
    text = re.sub(r"(?i)\b(?:api[_-]?key|token|secret|password|authorization)\b\s*[:=]\s*\S+",
                  "[REDACTED]", text)
    text = re.sub(r"\bBearer\s+\S+", "Bearer [REDACTED]", text, flags=re.I)
    return (type(error).__name__ + ": " + text.replace("\n", " ").replace("\r", " "))[:320]

PREFIX = "/astrbot_plugin_private_companion/model"
VOICE_FIELDS = {"zh": "voice_zh_provider_id", "ja": "voice_ja_provider_id", "en": "voice_en_provider_id"}
VOICE_CHANNELS = {"private": "私聊", "group": "群聊"}
CHANNEL_VOICE_FIELDS = {
    channel: {lang: f"voice_{channel}_{lang}_provider_id" for lang in VOICE_FIELDS}
    for channel in VOICE_CHANNELS
}
VOICE_CHANGES = frozenset((*VOICE_FIELDS.values(),
    *(field for fields in CHANNEL_VOICE_FIELDS.values() for field in fields.values()),
    "voice_timeout_seconds"))
LANGUAGES = {"zh": "中文", "ja": "日语", "en": "英语"}
IMAGE_SETTINGS = "model_image_routes.json"
TTS_PROVIDER_SYSTEM_KEYS = frozenset({"id", "provider", "type", "provider_type", "enable", "hint", "provider_source_id"})
TTS_PROVIDER_SECRET_KEYS = frozenset({"api_key", "azure_tts_subscription_key", "gemini_tts_api_key", "proxy"})
PROVIDER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}")
VOICE_AUDIO_MIME_TYPES = {
    ".aac": "audio/aac", ".flac": "audio/flac", ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".opus": "audio/ogg",
    ".wav": "audio/wav", ".webm": "audio/webm",
}
# 语音保存只写配置；宿主启停或切换时及时返回未写入，避免页面长时间等待。
VOICE_SAVE_LOCK_WAIT_SECONDS = 2.0
TTS_PROVIDER_LABELS = {
    "openai_tts_api": "OpenAI TTS(API)", "mimo_tts_api": "MiMo TTS(API)",
    "genie_tts": "Genie TTS", "edge_tts": "Edge TTS", "gsv_tts_selfhost": "GSV TTS(Local)",
    "gsvi_tts_api": "GSVI TTS(API)", "fishaudio_tts_api": "FishAudio TTS(API)",
    "dashscope_tts": "通义千问 TTS(API)", "azure_tts": "Azure TTS",
    "minimax_tts_api": "MiniMax TTS(API)", "volcengine_tts": "火山引擎 TTS(API)",
    "gemini_tts": "Gemini TTS", "elevenlabs_tts_api": "ElevenLabs TTS(API)",
}


def _is_tts_provider_config(config):
    return isinstance(config, dict) and str(config.get("provider_type") or "").strip().lower() in {"text_to_speech", "tts"}


def _is_secret_key(key):
    lowered = str(key).lower()
    return _tts_secret_field(lowered)


def _field_type(default, meta):
    declared = str(meta.get("type") or "").lower()
    if declared in {"bool", "int", "float", "string", "object", "list"}:
        return declared
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int) and not isinstance(default, bool):
        return "int"
    if isinstance(default, float):
        return "float"
    return "string"


def _single_line(value, limit=120):
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text[:limit]


def _multi_line(value, limit=600):
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()[:limit]


def _metadata_unresolved(value):
    text = str(value or "").strip()
    lowered = text.lower()
    return (lowered.startswith("provider_group.") or lowered.startswith("config."
            ) or lowered.endswith(".description") or lowered.endswith(".hint") or "�" in text)


def _tts_field_group(key):
    lowered = str(key or "").lower()
    if lowered in {"api_base", "api_key", "proxy", "timeout", "appid", "minimax-group-id"}:
        return "connection"
    if any(marker in lowered for marker in (
        "model", "voice", "character", "reference", "emotion", "language", "text_lang",
        "prompt_text_lang", "style", "role", "format", "dialect", "speed", "pitch", "volume", "rate",
    )):
        return "voice"
    return "advanced"


def _tts_secret_field(key):
    lowered = str(key or "").strip().lower()
    return lowered in TTS_PROVIDER_SECRET_KEYS or any(marker in lowered for marker in (
        "api-key", "api_key", "api_token", "access_token", "access_key", "secret_key",
        "subscription_key", "password",
    )) or lowered in {"key", "token", "secret"}


def _tts_provider_identifier(provider):
    """Read a live TTS provider id without relying on the generic LLM lookup."""
    try:
        metadata = provider.meta() if callable(getattr(provider, "meta", None)) else None
    except Exception:
        metadata = None
    identifier = getattr(metadata, "id", "") or getattr(provider, "id", "")
    return str(identifier).strip() if isinstance(identifier, str) else ""


def _find_tts_provider(context, provider_id):
    """Return the live TTS instance matching *provider_id*.

    AstrBot keeps all provider kinds in ``inst_map`` but its generic context
    helper has changed behavior between releases.  The TTS collection is the
    authoritative list for this route, so use it first and only accept a
    generic fallback when that object is also in the TTS collection.
    """
    providers = list(context.get_all_tts_providers() or [])
    for provider in providers:
        if _tts_provider_identifier(provider) == provider_id:
            return provider
    getter = getattr(context, "get_provider_by_id", None)
    if callable(getter):
        candidate = getter(provider_id)
        if candidate in providers and _tts_provider_identifier(candidate) == provider_id:
            return candidate
    return None


def _voice_error_text(error, provider=None, limit=2000):
    """Expose useful provider diagnostics while removing configured secrets."""
    text = f"{type(error).__name__}: {error}" if error is not None else ""
    secrets = []
    for owner in (provider, getattr(provider, "provider_config", None),
                  getattr(provider, "provider_settings", None)):
        values = owner.items() if isinstance(owner, dict) else getattr(owner, "items", lambda: ())()
        for key, value in values:
            if _tts_secret_field(key) and value not in (None, "", [], {}):
                secrets.append(str(value))
    for secret in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)((?:api[_-]?key|token|secret|password)=)[^&\s]+", r"\1<redacted>", text)
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())[:limit]


def _voice_suggestion(code, stage):
    return {
        "model_voice_provider_unavailable": "确认 AstrBot 中该 TTS Provider 已启用并完成加载，然后刷新页面。",
        "model_voice_probe_timeout": "检查网络、代理和上游服务状态；结果未知时不要重复发送，只在确认后重试。",
        "model_voice_probe_failed": "根据详细错误核对 API 地址、密钥、代理、模型和参考音色参数。",
        "model_voice_audio_invalid": "确认 Provider 返回的是有效音频文件，并检查 AstrBot 临时目录权限。",
        "model_voice_audio_read_failed": "确认音频文件在 AstrBot 临时目录内且仍可读取，然后重新测试。",
        "model_voice_provider_changed": "Provider 在测试期间发生变化，请刷新语音配置后重新测试。",
    }.get(code, {
        "provider_lookup": "刷新语音配置并确认目标 Provider 仍处于已加载状态。",
        "synthesis": "检查该 Provider 的连接、凭据和模型参数后再测试。",
        "audio_read": "确认 AstrBot 临时目录可读，并重新执行合成测试。",
    }.get(stage, "查看详细错误并核对当前语音 Provider 配置后重试。"))


def _tts_fallback_label(key):
    return {
        "api_key": "API Key", "api_base": "API 地址", "proxy": "代理地址", "timeout": "超时时间",
        "model": "模型", "appid": "App ID", "openai-tts-voice": "音色", "mimo-tts-voice": "音色",
        "mimo-tts-format": "输出格式", "mimo-tts-style-prompt": "风格提示词", "mimo-tts-dialect": "方言",
        "mimo-tts-seed-text": "种子文本", "edge-tts-voice": "音色", "rate": "语速", "volume": "音量",
        "pitch": "音调", "fishaudio-tts-character": "角色名称", "fishaudio-tts-reference-id": "参考模型 ID",
        "dashscope_tts_voice": "音色", "azure_tts_voice": "音色", "azure_tts_style": "语气风格",
        "azure_tts_role": "角色", "azure_tts_rate": "语速", "azure_tts_volume": "音量",
        "azure_tts_subscription_key": "订阅密钥", "azure_tts_region": "服务区域", "volcengine_cluster": "集群",
        "volcengine_voice_type": "音色 ID", "gemini_tts_api_key": "API Key", "gemini_tts_api_base": "API 地址",
        "gemini_tts_timeout": "超时时间", "gemini_tts_model": "模型", "gemini_tts_prefix": "朗读前缀",
        "gemini_tts_voice_name": "音色", "elevenlabs-tts-voice-id": "Voice ID",
        "elevenlabs-tts-output-format": "输出格式", "elevenlabs-tts-stability": "稳定度",
        "elevenlabs-tts-similarity-boost": "相似度增强", "elevenlabs-tts-style": "风格强度",
        "elevenlabs-tts-use-speaker-boost": "启用说话人增强",
    }.get(str(key), str(key or "").replace("_", " ").replace("-", " ").strip())


def _tts_fallback_hint(key):
    return {
        "proxy": "可选的代理地址；已配置的代理不会在页面回显。",
        "fishaudio-tts-character": "Fish Audio 的角色名称，可在官方音色库中查询。",
        "fishaudio-tts-reference-id": "可选的 Fish Audio 参考模型 ID。",
    }.get(str(key), "")


def _schema_fields(source, field_metadata, provider_type=""):
    fields = []
    for key, default in source.items():
        if key in TTS_PROVIDER_SYSTEM_KEYS:
            continue
        meta = field_metadata.get(key) if isinstance(field_metadata.get(key), dict) else {}
        if provider_type == "fishaudio_tts_api" and key == "model":
            meta = {**meta, "type": "string", "description": "Fish Audio 模型",
                    "hint": "Drama 3 支持方括号表演指令及 <whisper> 等成对局部语气标签；S2/S2.1 使用方括号，S1 使用圆括号。[singing]/[哼唱] 是社区实测偶发生效的实验性指令，官方标签表未列出。",
                    "options": deepcopy(FISH_AUDIO_MODEL_OPTIONS)}
        raw_options = meta.get("options") if isinstance(meta.get("options"), list) else []
        labels = meta.get("labels") if isinstance(meta.get("labels"), list) else []
        options = []
        for index, option in enumerate(raw_options):
            if isinstance(option, dict):
                value = str(option.get("value", "") or "")
                options.append({"value": value, "label": str(option.get("label") or value)})
            else:
                label = str(labels[index]) if index < len(labels) and labels[index] else str(option or "")
                options.append({"value": str(option or ""), "label": label})
        fallback_label = _tts_fallback_label(key)
        label = _single_line(meta.get("description"), 120)
        if (not label or _metadata_unresolved(label)
                or (any("\u4e00" <= char <= "\u9fff" for char in fallback_label)
                    and not any("\u4e00" <= char <= "\u9fff" for char in label))):
            label = fallback_label
        hint = _multi_line(meta.get("hint"), 600)
        if _metadata_unresolved(hint):
            hint = _tts_fallback_hint(key)
        slider = meta.get("slider") if isinstance(meta.get("slider"), dict) else {}
        fields.append({"key": key, "type": _field_type(default, meta), "label": label,
            "hint": hint, "options": options, "secret": _tts_secret_field(key),
            "default": deepcopy(default), "group": _tts_field_group(key),
            "min": meta.get("min", slider.get("min")), "max": meta.get("max", slider.get("max")),
            "step": meta.get("step", slider.get("step"))})
    return fields


def _document(raw, keys, *, limit=32000):
    if len(raw) > limit:
        raise ValueError("model request is too large")

    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("duplicate field")
            data[key] = value
        return data

    def finite(_value):
        raise ValueError("nonfinite value")

    data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=finite)
    if type(data) is not dict or set(data) != keys:
        raise ValueError("unexpected or missing model fields")
    return data


def _document_optional(raw, required, optional=(), *, limit=32000):
    """Decode a strict object while allowing explicitly versioned optional keys."""
    if len(raw) > limit:
        raise ValueError("model request is too large")

    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("duplicate field")
            data[key] = value
        return data

    data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite value")))
    required, optional = set(required), set(optional)
    if type(data) is not dict or not required.issubset(data) or set(data) - required - optional:
        raise ValueError("unexpected or missing model fields")
    return data


# 生图 provider 的字段声明。AstrBot 的 CONFIG_METADATA_2.provider_group 只覆盖
# TTS/ASR，生图没有对应元数据，所以这里自己声明一组，供页面按 provider 类型渲染，
# 不再让用户填自由文本。
# kind: text | integer | boolean | select | textarea
# 注意：超时不在这里声明——它由后端自己的配置（NativeClient.config）读取，
# 写进生图参数只会被服务端忽略，宁可不在页面上给假控件。
IMAGE_PROVIDER_GROUP = {
    "comfyui": {
        "label": "ComfyUI",
        "fields": {
            "endpoint": {"kind": "text", "group": "connection", "label": "服务地址",
                         "help": "ComfyUI 的 HTTP 地址，例如 http://127.0.0.1:8188"},
            "workflow": {"kind": "textarea", "group": "workflow", "label": "工作流 JSON",
                         "help": "导出 API 格式的工作流；留空则使用内置模板"},
            "steps": {"kind": "integer", "group": "sampling", "label": "采样步数",
                      "default": 24, "min": 1, "max": 150},
        },
    },
    "sd_webui": {
        "label": "SD WebUI",
        "fields": {
            # Connection fields are edited from the same saved connection object above;
            # only declare values that NativeTransportBackend accepts as request parameters.
            "sampler_name": {"kind": "text", "group": "sampling", "storage": "parameters", "label": "采样器"},
            "steps": {"kind": "integer", "group": "sampling", "storage": "parameters", "label": "采样步数",
                      "default": 28, "min": 1, "max": 150},
            "cfg_scale": {"kind": "number", "group": "sampling", "storage": "parameters", "label": "CFG",
                          "default": 7, "min": 1, "max": 30, "step": 0.1},
            "width": {"kind": "integer", "group": "sampling", "storage": "parameters", "label": "宽度",
                      "default": 768, "min": 64, "max": 8192, "step": 64},
            "height": {"kind": "integer", "group": "sampling", "storage": "parameters", "label": "高度",
                       "default": 768, "min": 64, "max": 8192, "step": 64},
            "batch_size": {"kind": "integer", "group": "sampling", "storage": "parameters", "label": "批量数量",
                           "default": 1, "min": 1, "max": 4},
            "seed": {"kind": "integer", "group": "sampling", "storage": "parameters", "label": "随机种子",
                     "default": -1, "min": -1, "max": 4294967295},
            "denoising_strength": {"kind": "number", "group": "sampling", "storage": "parameters", "label": "重绘幅度",
                                   "default": 0.75, "min": 0, "max": 1, "step": 0.01},
        },
    },
    "openai_images": {
        "label": "OpenAI 兼容生图",
        "fields": {
            "api_base": {"kind": "text", "group": "connection", "label": "接口地址"},
            "api_key": {"kind": "text", "group": "connection", "label": "密钥引用",
                        "help": "填写环境变量名，不写明文密钥"},
            "model": {"kind": "text", "group": "sampling", "label": "模型"},
            "size": {"kind": "text", "group": "sampling", "label": "尺寸", "default": "1024x1024"},
            "quality": {"kind": "select", "group": "sampling", "label": "质量",
                        "choices": ["standard", "hd"], "default": "standard"},
            "max_reference_images": {"kind": "integer", "group": "sampling",
                                     "label": "最多参考图张数", "default": MAX_REFERENCE_IMAGES,
                                     "min": 0, "max": MAX_REFERENCE_IMAGES},
        },
    },
}



def text_provider_catalog(controller):
    """Return the live AstrBot text providers shared by model pages and drafts."""
    context = controller.context
    from .bootstrap_provider import supported_text_provider, text_provider_predicate
    expected_type = text_provider_predicate(getattr(controller, "native_types", None))
    if expected_type is None:
        raise TaskError("model_catalog_unavailable", "AstrBot text provider type is unavailable")
    providers = []
    for provider in list(context.get_all_providers() or []):
        if not supported_text_provider(provider, expected_type):
            continue
        meta = provider.meta() if callable(getattr(provider, "meta", None)) else None
        identifier = str(getattr(meta, "id", "") or getattr(provider, "id", "") or "").strip()
        if not identifier:
            continue
        name = str(getattr(meta, "name", "") or getattr(meta, "label", "") or identifier)
        providers.append({"id": identifier, "name": name[:120],
                          "capabilities": sorted({"text", "conversation.reply"}),
                          "loaded": True, "source": "astrbot"})
    configured = getattr(getattr(context, "provider_manager", None), "providers_config", None) or []
    configured_ids = {str(item.get("id")) for item in configured if isinstance(item, dict)}
    for item in providers:
        item["configured"] = item["id"] in configured_ids
    providers.sort(key=lambda item: (not item["configured"], item["name"].lower(), item["id"]))
    return providers


def read_global_model_defaults(controller):
    """Read plugin-wide default primary/fallback text providers (model.provider_id / fallback_provider_id)."""
    base = getattr(controller, "controller", controller)
    config = getattr(base, "config", None)
    model_cfg = config.get("model", {}) if isinstance(config, dict) else {}
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    configured_primary = str(model_cfg.get("provider_id") or "").strip()
    configured_fallback = str(model_cfg.get("fallback_provider_id") or "").strip()
    settings = getattr(base, "settings", None)
    common = getattr(settings, "common", settings)
    effective_primary = configured_primary or str(getattr(common, "provider_id", "") or "").strip()
    effective_fallback = configured_fallback or str(getattr(common, "fallback_provider_id", "") or "").strip()
    if effective_fallback == effective_primary:
        effective_fallback = ""
    editable = isinstance(config, dict) and callable(getattr(base, "save_plugin_config", None))
    return {
        "provider_id": configured_primary,
        "fallback_provider_id": configured_fallback,
        "effective_provider_id": effective_primary,
        "effective_fallback_provider_id": effective_fallback,
        "editable": editable,
    }


def _validate_global_text_provider(base, provider_id, allowed_provider_ids=None):
    from .bootstrap_provider import supported_text_provider, text_provider_predicate
    context = getattr(base, "context", None)
    expected_type = text_provider_predicate(getattr(base, "native_types", None))
    if context is not None and callable(getattr(context, "get_provider_by_id", None)) and expected_type is not None:
        provider = context.get_provider_by_id(provider_id)
        if provider is None or not supported_text_provider(provider, expected_type):
            raise TaskError("host_provider_unavailable", "choose a loaded AstrBot text provider supported by V2")
        return
    if allowed_provider_ids is not None and provider_id not in allowed_provider_ids:
        raise TaskError("host_provider_unavailable", "choose a loaded AstrBot text provider supported by V2")


async def save_global_model_defaults(controller, data, *, allowed_provider_ids=None):
    """Persist plugin-wide default primary and fallback providers shared with account/setup settings."""
    base = getattr(controller, "controller", controller)
    raw_primary = data.get("provider_id")
    if type(raw_primary) is not str:
        raise TaskError("model_defaults_invalid", "global default primary provider must be a string")
    provider_id = raw_primary.strip()
    if len(provider_id) > 512:
        raise TaskError("model_defaults_invalid", "global default primary provider id is too long")
    raw_fallback = data.get("fallback_provider_id", "")
    if type(raw_fallback) is not str:
        raise TaskError("model_defaults_invalid", "fallback global default provider must be a string")
    fallback_provider_id = raw_fallback.strip()
    if len(fallback_provider_id) > 512:
        raise TaskError("model_defaults_invalid", "fallback global default provider id is too long")
    if provider_id:
        _validate_global_text_provider(base, provider_id, allowed_provider_ids=allowed_provider_ids)
    fallback_cleared_duplicate = False
    if provider_id and fallback_provider_id == provider_id:
        fallback_provider_id = ""
        fallback_cleared_duplicate = True
    elif fallback_provider_id:
        _validate_global_text_provider(base, fallback_provider_id, allowed_provider_ids=allowed_provider_ids)
    config = getattr(base, "config", None)
    save = getattr(base, "save_plugin_config", None)
    if not isinstance(config, dict) or not callable(save):
        raise TaskError("model_defaults_unavailable", "plugin configuration cannot be edited here")
    candidate = deepcopy(dict(config))
    model_settings = candidate.setdefault("model", {})
    if type(model_settings) is not dict:
        raise TaskError("model_defaults_invalid", "plugin model settings have an invalid shape")
    model_settings["provider_id"] = provider_id
    model_settings["fallback_provider_id"] = fallback_provider_id
    profiles = model_settings.get("additional_providers")
    if provider_id and type(profiles) is list:
        model_settings["additional_providers"] = [
            profile for profile in profiles
            if not isinstance(profile, dict) or profile.get("provider_id") != provider_id
        ]
    save(candidate)
    refresh = getattr(base, "refresh_settings", None)
    if callable(refresh):
        refresh()
    restart_required = False
    if getattr(base, "settings_error", None) is None:
        initializer = getattr(base, "initialize_profile", None)
        if callable(initializer):
            try:
                initialized = await initializer()
                failure = getattr(initialized, "failure", None)
                if failure is not None and getattr(failure, "code", "") == "host_must_stop":
                    restart_required = True
            except Exception:
                restart_required = getattr(base, "session", None) is not None
        else:
            restart_required = getattr(base, "session", None) is not None
    return {
        "saved": True,
        "global_defaults": read_global_model_defaults(base),
        "restart_required": restart_required,
        "fallback_cleared_duplicate": fallback_cleared_duplicate,
    }


class ModelWebAPI:
    def __init__(self, controller):
        self.controller = controller
        self._balance_lock = asyncio.Lock()
        self._laya_lock = asyncio.Lock()
        self._quota_api = QuotaWebEndpoints(self)

    def routes(self):
        return [
            (PREFIX + "/catalog", self.catalog, ["GET"], "Read AstrBot text provider catalog"),
            (PREFIX, self.status, ["GET"], "Read model task configuration"),
            (PREFIX + "/maintenance", self.maintenance, ["GET"], "Read role-level token and cache diagnostics"),
            (PREFIX + "/memory", self.memory_diagnostic, ["GET"], "Read current AstrBot process memory"),
            (PREFIX + "/balance", self.balance, ["GET"], "Read the configured upstream account balance"),
            (PREFIX + "/test/storage", self.storage_test, ["POST"], "Run read-only SQLite integrity diagnostics"),
            (PREFIX + "/image-schema", self.image_schema, ["GET"],
             "Read the declared image provider field groups"),
            (PREFIX + "/configure", self.configure, ["POST"], "Save one model task configuration"),
            (PREFIX + "/defaults", self.save_defaults, ["POST"], "Save plugin global default primary and fallback providers"),
            (PREFIX + "/test", self.test, ["POST"], "Probe one registered text provider"),
            (PREFIX + "/laya", self.laya_status, ["GET"], "Read optional Laya decision model configuration"),
            (PREFIX + "/laya/save", self.laya_save, ["POST"], "Save optional Laya decision model configuration"),
            (PREFIX + "/search", self.search_status, ["GET"], "Read the DeepSeek search Provider binding"),
            (PREFIX + "/search/save", self.search_save, ["POST"], "Save the DeepSeek search Provider binding"),
            (PREFIX + "/pricing", self.pricing_status, ["GET"], "Read Pricing v2 rules, catalog sources, clusters, and FX"),
            (PREFIX + "/pricing", self.pricing_save, ["POST"], "Save Pricing v2 rules, schedules, multipliers, and FX"),
            (PREFIX + "/pricing/sync-rates", self.pricing_sync_rates, ["POST"], "Refresh live USD to CNY exchange rate"),
            (PREFIX + "/pricing/sync", self.pricing_sync_catalog, ["POST"], "Sync enabled online and New API price catalog sources"),
            (PREFIX + "/pricing/sources/toggle", self.pricing_source_toggle, ["POST"], "Toggle a price catalog source"),
            (PREFIX + "/pricing/sources/currency", self.pricing_source_currency, ["POST"], "Override New API price source currency and resync"),
            (PREFIX + "/pricing/sources/detect", self.pricing_source_detect, ["POST"], "Probe a provider for New API /api/pricing"),
            (PREFIX + "/pricing/select", self.pricing_select, ["POST"], "Persist a catalog candidate selection for a provider model"),
            (PREFIX + "/pricing/select/reset", self.pricing_select_reset, ["POST"], "Clear persisted catalog candidate selection"),
            (PREFIX + "/pricing/expr/validate", self.pricing_expr_validate, ["POST"], "Validate a tiered_expr formula with USD/CNY preview"),
            (PREFIX + "/pricing/reset", self.pricing_reset, ["POST"], "Reset custom pricing rules, schedules, multipliers, or selections"),
            (PREFIX + "/pricing/residue/clear", self.pricing_residue_clear, ["POST"], "Clear orphaned pricing overrides for deleted providers"),
            (PREFIX + "/laya/logs", self.laya_logs, ["GET"], "Read recent Laya decision events"),
            (PREFIX + "/laya/test", self.laya_test, ["POST"], "Test the Laya decision model connection"),
            (PREFIX + "/laya/decide", self.laya_decide, ["POST"], "Call Laya for one semantic decision"),
            (PREFIX + "/voice", self.voice, ["GET"], "Read live TTS bindings"),
            (PREFIX + "/voice/save", self.voice_save, ["POST"], "Save live TTS bindings"),
            (PREFIX + "/voice/test", self.voice_test, ["POST"], "Probe one loaded TTS provider"),
            (PREFIX + "/tts/providers", self.tts_providers, ["GET"], "Read manageable TTS provider configs"),
            (PREFIX + "/tts/providers/create", self.tts_provider_create, ["POST"], "Create one TTS provider from a template"),
            (PREFIX + "/tts/providers/update", self.tts_provider_update, ["POST"], "Update one TTS provider config"),
            (PREFIX + "/tts/providers/clone", self.tts_provider_clone, ["POST"], "Clone one TTS provider for a language"),
            (PREFIX + "/tts/providers/test", self.tts_provider_test, ["POST"], "Test one loaded TTS provider connection"),
            (PREFIX + "/image", self.image, ["GET"], "Read native image actions"),
            (PREFIX + "/image/save", self.image_save, ["POST"], "Save native image switches"),
            (PREFIX + "/image/test", self.image_test, ["POST"], "Check native image registration"),
            (PREFIX + "/image/output", self.image_output, ["GET"], "Read the executing V2 image backend configuration"),
            (PREFIX + "/image/output/save", self.image_output_save, ["POST"], "Save the executing V2 image backend configuration"),
            (PREFIX + "/image/output/test", self.image_output_test, ["POST"], "Test one saved image API endpoint"),
            (PREFIX + "/image/output/reconnect", self.image_output_reconnect, ["POST"], "Reload the active V2 session's native providers"),
            (PREFIX + "/image/observability", self.image_observability, ["GET"], "Read image invocation outcomes and Content dashboard counts"),
            (PREFIX + "/image/review/providers", self.image_review_providers, ["GET"], "List loaded image-capable V2 providers"),
            *self._quota_api.routes(PREFIX),
        ]

    async def catalog(self):
        """Read the live AstrBot text provider list without requiring V2 runtime."""
        from .web_api import outcome_response
        try:
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            if request.query:
                raise TaskError("model_request_invalid", "model catalog does not accept query overrides")
            providers = text_provider_catalog(self.controller)
            return {"status": "ok", "data": {"providers": providers, "source": "astrbot.provider_manager"}}
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except Exception:
            return outcome_response(Outcome.failed("model_catalog_unavailable", "AstrBot provider list is unavailable"))

    @staticmethod
    def _validate_auth_inputs(*, layer="role", allow_query_keys=(), username=None, query=None):
        if username is None:
            username = request.username
        if query is None:
            query = request.query
        if type(username) is not str or not username.strip() or username.startswith("api_key:"):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        if layer not in ("role", "window"):
            raise TaskError("model_request_invalid", "select role defaults or this conversation window")
        if query:
            if allow_query_keys:
                allowed = set(allow_query_keys)
                if (set(query.keys()) - allowed
                        or (callable(getattr(query, "getlist", None))
                            and any(len(query.getlist(key)) != 1 for key in query.keys()))):
                    raise TaskError("model_request_invalid", "model routes do not accept target overrides")
            elif (set(query.keys()) != {"layer"} or query.get("layer") != layer
                  or callable(getattr(query, "getlist", None)) and len(query.getlist("layer")) != 1):
                raise TaskError("model_request_invalid", "model routes do not accept target overrides")
        return username, query

    def _authorized(self, *, layer="role", include_status=False, authorize=True,
                    session=None, runtime=None, allow_query_keys=(),
                    username=None, query=None, observed_at=None):
        controller = self.controller
        username, _query = self._validate_auth_inputs(
            layer=layer, allow_query_keys=allow_query_keys, username=username, query=query)
        if controller._terminated:
            raise TaskError("staging_host_terminated", "plugin is stopped")
        session = session if session is not None else (controller.session or controller.last_session)
        runtime = runtime if runtime is not None else controller.runtime
        if session is None or runtime is None:
            raise TaskError("staging_not_initialized", "load the V2 profile first")
        at = observed_at if observed_at is not None else controller.now()
        operator = dashboard_operator(runtime, username, at=at)
        manager = ModelManagement(session.application, session.profile.target)
        status = require(manager.status(operator, layer=layer)) if include_status else None
        if authorize and not include_status:
            require(manager.authorize(operator))
        return session, operator, manager, status

    async def _run_authorized_read(self, work=None, *, layer="role", include_status=False,
                                   authorize=True, allow_query_keys=()):
        username, query = self._validate_auth_inputs(layer=layer, allow_query_keys=allow_query_keys)
        controller = self.controller
        async with controller._control_lock:
            if controller._terminated:
                raise TaskError("staging_host_terminated", "plugin is stopped")
            session = controller.session or controller.last_session
            runtime = controller.runtime
            if session is None or runtime is None:
                raise TaskError("staging_not_initialized", "load the V2 profile first")
            observed_at = controller.now()

        def execute():
            auth = self._authorized(
                layer=layer,
                include_status=include_status,
                authorize=authorize,
                session=session,
                runtime=runtime,
                allow_query_keys=allow_query_keys,
                username=username,
                query=query,
                observed_at=observed_at,
            )
            return work(*auth, observed_at) if work is not None else auth

        return await asyncio.to_thread(execute)

    @staticmethod
    def _response(result):
        from .web_api import outcome_response
        return outcome_response(result)

    def _host_route(self, session):
        """The exact AstrBot config and model the session's own UMO resolves to."""
        try:
            scope = session.profile.target.scope
            kind = getattr(scope.kind, "value", scope.kind)
            message_type = "FriendMessage" if str(kind) == "private" else "GroupMessage"
            umo = f"{scope.platform}:{message_type}:{scope.native_scope_id}"
            context = self.controller.context
            config = {}
            manager = getattr(context, "astrbot_config_mgr", None)
            getter = getattr(manager, "get_conf_info", None)
            if callable(getter):
                info = getter(umo) or {}
                name = str(info.get("name") or "")
                if str(info.get("id") or "") == "default" or not name:
                    name = "AstrBot 默认配置"
                config = {"id": str(info.get("id") or ""), "name": name}
            provider_id, model = "", ""
            using = getattr(context, "get_using_provider", None)
            if not callable(using):
                return {}
            if callable(using):
                provider = using(umo)
                meta = provider.meta() if callable(getattr(provider, "meta", None)) else None
                provider_id = str(getattr(meta, "id", "") or "")
                model = str(getattr(meta, "model", "") or "")
            return {"umo": umo, "config": config, "provider": provider_id, "model": model}
        except Exception:
            return {}

    async def _run(self, action):
        try:
            data = None
            if action == "configure":
                data = _document_optional(await request.body(),
                    {"kind", "operation_key", "edit_token", "changes", "reset"},
                    optional=("section", "layer"))
                if (data.get("kind") == "conversation.reply" and not data.get("section")
                        and ((set(data.get("changes") or {}) | set(data.get("reset") or []))
                             & set(CORE_MANAGED_FIELDS))):
                    raise TaskError("model_configuration_host_managed",
                                    "普通回复模型由 AstrBot 当前会话管理；这里只保存对话附加提示和时段替换规则。")
            layer = data.get("layer", "role") if data is not None else request.query.get("layer", "role")
            # Capture the lifecycle-bound references under the control lock, but
            # never run synchronous SQLite/projection work on the event loop.
            # StorageRuntime already serialises its own read/write transactions.
            controller = self.controller
            async with controller._control_lock:
                session = controller.session or controller.last_session
                runtime = controller.runtime
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                if session is None or runtime is None:
                    raise TaskError("staging_not_initialized", "load the V2 profile first")
            if action == "status":
                _session, operator, manager, _status = await asyncio.to_thread(
                    self._authorized, layer=layer, authorize=False, session=session, runtime=runtime)
                result = await asyncio.to_thread(manager.status, operator, layer=layer)
            elif action == "maintenance":
                def _run_maintenance_core():
                    sess, op, mgr, st = self._authorized(
                        layer=layer, authorize=False, session=session, runtime=runtime
                    )
                    settings = self._pricing_store(runtime).read_safe()
                    provider_metadata = build_provider_metadata_map(controller, sess, settings)
                    res = mgr.maintenance(op, provider_metadata=provider_metadata)
                    return sess, op, mgr, st, res

                _session, operator, manager, _status, result = await asyncio.to_thread(_run_maintenance_core)
            else:
                _session, operator, manager, _status = await asyncio.to_thread(
                    self._authorized, layer=layer, authorize=False, session=session, runtime=runtime)
                result = await asyncio.to_thread(manager.configure, operator, {**data, "layer": layer})
            if action in {"status", "configure"} and result.is_success:
                if action == "status":
                    payload = dict(result.unwrap())
                    payload["host_route"] = self._host_route(_session)
                    payload["global_defaults"] = read_global_model_defaults(self.controller)
                    result = Outcome.success(payload)
                result = Outcome.success(project_core_reply_configuration(result.unwrap()))
            if action == "maintenance" and result.is_success:
                payload = dict(result.unwrap())
                try:
                    self._exchange_rate_service(runtime.data_root).maybe_schedule_background_refresh()
                except Exception:
                    logger.debug("[CompanionV2] 后台汇率刷新调度跳过", exc_info=True)
                try:
                    self._pricing_catalog_service(runtime.data_root, _session).maybe_schedule_daily_sync()
                except Exception:
                    logger.debug("[CompanionV2] 后台价格源自动同步调度跳过", exc_info=True)

                async def _safe_read_host_checks():
                    try:
                        return await asyncio.to_thread(read_host_checks, self.controller)
                    except Exception:
                        logger.exception("[CompanionV2] 排障主机检查读取失败")
                        return None

                ext_sources, main_usage, host_checks = await asyncio.gather(
                    asyncio.to_thread(read_token_sources, self.controller),
                    read_astrbot_token_usage(self.controller),
                    _safe_read_host_checks(),
                )
                payload["external_token_sources"] = ext_sources
                payload["external_token_sources"]["main"] = main_usage
                memory_source = payload["external_token_sources"].get("memory")
                if isinstance(memory_source, dict):
                    memory_source["linkage"] = await read_external_memory_summary(
                        self.controller, _session.profile.target)
                # Additive, read-only host facts.  They ride the existing
                # maintenance read (the page already refreshes it) so no new
                # polling path is introduced; a failure here must not hide the
                # role-level accounting that already succeeded.
                if host_checks is not None:
                    payload["host_checks"] = host_checks
                # External reads can take time; revalidate before returning the
                # diagnostics rather than trusting the earlier authorization.
                await asyncio.to_thread(manager.authorize, operator)
                result = Outcome.success(payload)
            return self._response(result)
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (StorageOpenError, sqlite3.Error) as error:
            logger.warning("[CompanionV2] 模型存储暂不可用 action=%s error_type=%s",
                           action, type(error).__name__)
            return self._response(Outcome.failed(
                "model_storage_unavailable", "model storage is temporarily unavailable"))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("model_request_invalid", "invalid UTF-8 model request"))

    async def status(self):
        return await self._run("status")

    async def maintenance(self):
        from .http_etag import conditional_response
        result = await self._run("maintenance")
        return conditional_response(result, req=request, ignore_keys=("generated_at", "observed_at", "sampled_at"))

    async def memory_diagnostic(self):
        try:
            payload = await self._run_authorized_read(lambda *_: process_memory_snapshot())
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, UnicodeError):
            return self._response(Outcome.failed(
                "model_memory_diagnostic_unavailable", "process memory could not be sampled"))

    async def balance(self):
        try:
            session, _op, _mgr, _status = await self._run_authorized_read()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or getattr(self.controller, "runtime", None)
            runtime_root = getattr(runtime, "data_root", None)
            async with self._balance_lock:
                try:
                    port = configured_balance_port(
                        self.controller.config,
                        context=getattr(self.controller, "context", None),
                        runtime_root=runtime_root,
                        controller=self.controller,
                    )
                except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
                    return self._response(Outcome.success({
                        "configured": True, "available": False, "provider": "unknown",
                        "provider_label": "余额查询", "currency_label": "额度",
                        "observed_at": None, "amount": None, "state": "configuration_invalid",
                    }))
                if port is None:
                    return self._response(Outcome.success({
                        "configured": False, "available": False, "provider": "newapi",
                        "provider_label": "NewAPI", "currency_label": "额度",
                        "observed_at": None, "amount": None, "state": "not_configured",
                    }))
                try:
                    observation = await asyncio.wait_for(port.fetch(), timeout=12)
                except Exception as error:
                    from .service_balance import balance_failure_data
                    meta_fn = getattr(port, "balance_metadata", None)
                    extra_meta = meta_fn(None) if callable(meta_fn) else {"source_kind": "legacy_balance"}
                    data = {"configured": True, "available": False, "provider": port.provider,
                            "provider_label": port.provider_label, "currency_label": port.currency_label,
                            "observed_at": None, "amount": None, **extra_meta, **balance_failure_data(error)}
                else:
                    from .service_balance import balance_observation_data
                    meta_fn = getattr(port, "balance_metadata", None)
                    extra_meta = meta_fn(observation) if callable(meta_fn) else {"source_kind": "legacy_balance"}
                    data = {"configured": True, "available": True, "provider": port.provider,
                            "provider_label": port.provider_label, "state": "available",
                            **extra_meta, **balance_observation_data(port, observation)}
                return self._response(Outcome.success(data))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("model_request_invalid", "invalid balance query request"))

    async def storage_test(self):
        try:
            data = _document(await request.body(), {"check"}, limit=1024)
            if data["check"] != "sqlite":
                raise ValueError("unsupported storage check")
            started = monotonic()
            result = await self._run_authorized_read(
                lambda _session, operator, manager, _status, _at: manager.check_storage(operator)
            )
            if result.is_success:
                result = Outcome.success({**result.unwrap(),
                    "elapsed_ms": round((monotonic() - started) * 1000)})
            return self._response(result)
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (StorageOpenError, sqlite3.Error) as error:
            logger.warning("[CompanionV2] 模型存储检查暂不可用 error_type=%s", type(error).__name__)
            return self._response(Outcome.failed(
                "model_storage_unavailable", "model storage is temporarily unavailable"))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("model_request_invalid", "invalid storage check request"))

    async def configure(self):
        return await self._run("configure")

    async def save_defaults(self):
        try:
            data = _document_optional(await request.body(), {"provider_id"},
                optional=("fallback_provider_id",), limit=4096)
            async with self.controller._control_lock:
                _session, _operator, _manager, status = self._authorized(include_status=True)
                allowed_ids = {item["id"] for item in status.get("providers", [])
                               if "text" in (item.get("capabilities") or ())}
            saved = await save_global_model_defaults(self.controller, data, allowed_provider_ids=allowed_ids)
            return self._response(Outcome.success(saved))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except HostControlError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("model_defaults_invalid", "invalid global model defaults request"))
        except OSError:
            return self._response(Outcome.failed("account_config_save_failed", "global model defaults could not be saved"))

    async def test(self):
        try:
            data = _document(await request.body(), {"provider_id"}, limit=1024)
            provider_id = data["provider_id"]
            if type(provider_id) is not str or not provider_id or len(provider_id) > 128:
                raise ValueError("invalid provider id")
            async with self.controller._control_lock:
                _session, _operator, _manager, status = self._authorized(include_status=True)
                if provider_id not in {item["id"] for item in status["providers"]}:
                    raise TaskError("model_provider_unavailable", "provider has no V2 adapter")
                base = getattr(self.controller, "controller", self.controller)
                model = next((item for item in (base.session or base.last_session).models
                              if item.provider_id == provider_id), None)
                if model is None:
                    raise TaskError("model_provider_unavailable", "provider has no live V2 adapter")
            started = monotonic()
            try:
                await asyncio.wait_for(model.probe(), timeout=30)
                result, code = "ok", ""
            except asyncio.TimeoutError:
                result, code = "unknown", "model_probe_timeout"
            except Exception as error:
                logger.warning("[CompanionV2] 模型探测失败 provider=%s error=%s",
                               provider_id, _probe_error_summary(error))
                result, code = "unknown", "model_probe_failed"
            async with self.controller._control_lock:
                self._authorized()
                base = getattr(self.controller, "controller", self.controller)
                if model not in (base.session or base.last_session).models:
                    raise TaskError("model_provider_changed", "provider changed during probe")
            return self._response(Outcome.success({"provider_id": provider_id, "status": result,
                "latency_ms": round((monotonic() - started) * 1000), "code": code}))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("model_request_invalid", "invalid probe request"))

    def _laya_service(self):
        """Use the same global configuration as every live conversation."""
        from .laya_client import LayaDecisionClient
        root = Path(getattr(self.controller, "data_root", Path.cwd())).resolve()
        session = getattr(self.controller, "session", None) or getattr(self.controller, "last_session", None)
        application = getattr(session, "application", None)
        target = getattr(getattr(session, "profile", None), "target", None)
        runtime = getattr(application, "runtime", None) or getattr(self.controller, "runtime", None)
        origin_key = getattr(target, "key", None)
        scope = getattr(getattr(target, "audience", None), "scope", None) or getattr(target, "scope", None)
        return LayaDecisionService(LayaSettingsStore(root),
                                   runtime=runtime, origin_key=origin_key, scope=scope,
                                   client_factory=LayaDecisionClient)

    def _search_settings_store(self):
        return SearchSettingsStore(Path(getattr(self.controller, "data_root", Path.cwd())))

    def _search_status(self):
        from .news import deepseek_provider_catalog
        values = self._search_settings_store().read()
        providers = deepseek_provider_catalog(self.controller.context)
        selected = values["deepseek_provider_id"]
        return {**values, "providers": providers,
                "provider_available": not selected or any(item["id"] == selected for item in providers),
                "configuration_scope": "global"}

    async def search_status(self):
        try:
            status = await self._run_authorized_read(lambda *_: self._search_status())
            return self._response(Outcome.success(status))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except SearchSettingsError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (StorageOpenError, OSError, ValueError):
            return self._response(Outcome.failed("search_config_unavailable", "无法读取搜索服务配置"))

    async def search_save(self):
        try:
            data = _document(await request.body(), {"revision", "changes"}, limit=8192)
            if type(data["changes"]) is not dict:
                raise ValueError("invalid search changes")
            async with self.controller._control_lock:
                self._authorized()
                self._search_settings_store().update(data["revision"], data["changes"])
                status = self._search_status()
            return self._response(Outcome.success(status))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except SearchSettingsError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("search_config_invalid", "搜索服务配置字段无效"))

    def _pricing_store(self, runtime) -> PricingSettingsStore:
        return PricingSettingsStore(Path(runtime.data_root))

    def _pricing_catalog_store(self, runtime) -> PriceCatalogStore:
        return PriceCatalogStore(Path(runtime.data_root))

    def _exchange_rate_service(self, runtime_root) -> ExchangeRateService:
        base = getattr(self.controller, "controller", self.controller)
        services = getattr(base, "_exchange_rate_services", None)
        if not isinstance(services, dict):
            services = {}
            try:
                setattr(base, "_exchange_rate_services", services)
            except Exception:
                pass
        key = str(Path(runtime_root).resolve())
        service = services.get(key)
        if service is None:
            service = ExchangeRateService(
                PricingSettingsStore(Path(runtime_root)),
                now=getattr(base, "now", None),
                proxy_resolver=lambda: read_astrbot_http_proxy(self.controller),
            )
            services[key] = service
        return service

    def _pricing_catalog_service(self, runtime_root, session=None) -> PricingCatalogService:
        base = getattr(self.controller, "controller", self.controller)
        services = getattr(base, "_pricing_catalog_services", None)
        if not isinstance(services, dict):
            services = {}
            try:
                setattr(base, "_pricing_catalog_services", services)
            except Exception:
                pass
        key = str(Path(runtime_root).resolve())
        service = services.get(key)
        if service is None:
            import inspect
            from .quota_service import get_quota_watch_service

            def _connections():
                _p, _s, conns = collect_provider_inventory(self.controller, session)
                return conns

            def _quota_station_resolver(origin: str):
                try:
                    qsvc = get_quota_watch_service(self.controller, Path(runtime_root))
                    return qsvc.resolve_station_for_origin(origin, session=session)
                except Exception:
                    return None

            init_kwargs: dict[str, Any] = {
                "now": getattr(base, "now", None),
                "proxy_resolver": lambda: read_astrbot_http_proxy(self.controller),
                "connections_resolver": _connections,
            }
            try:
                if "quota_station_resolver" in inspect.signature(PricingCatalogService.__init__).parameters:
                    init_kwargs["quota_station_resolver"] = _quota_station_resolver
            except (TypeError, ValueError):
                pass

            service = PricingCatalogService(
                PricingSettingsStore(Path(runtime_root)),
                PriceCatalogStore(Path(runtime_root)),
                **init_kwargs,
            )
            setter = getattr(service, "set_quota_station_resolver", None)
            if callable(setter):
                setter(_quota_station_resolver)
            elif hasattr(service, "_quota_station_resolver"):
                try:
                    service._quota_station_resolver = _quota_station_resolver
                except Exception:
                    pass
            services[key] = service
        return service

    async def _authorize_pricing(self):
        async with self.controller._control_lock:
            session = self.controller.session or self.controller.last_session
            runtime = self.controller.runtime
            if self.controller._terminated:
                raise TaskError("staging_host_terminated", "plugin is stopped")
            if session is None or runtime is None:
                raise TaskError("staging_not_initialized", "load the V2 profile first")
        return await asyncio.to_thread(self._authorized, session=session, runtime=runtime)

    async def _build_pricing_response(self, session, operator, manager, runtime, settings) -> dict:
        catalog = await asyncio.to_thread(self._pricing_catalog_store(runtime).read_safe)
        host_route = self._host_route(session)
        try:
            status_outcome = await asyncio.to_thread(manager.status, operator)
            model_status = status_outcome.unwrap() if status_outcome.is_success else None
        except Exception:
            model_status = None
        runtime_models = await read_provider_stat_runtime_models(self.controller)
        return await asyncio.to_thread(
            build_pricing_payload,
            controller=self.controller,
            session=session,
            runtime=runtime,
            settings=settings,
            catalog=catalog,
            host_route=host_route,
            model_status=model_status,
            runtime_models=runtime_models,
        )

    async def pricing_status(self):
        try:
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            settings = await asyncio.to_thread(self._pricing_store(runtime).read)
            try:
                self._exchange_rate_service(runtime.data_root).maybe_schedule_background_refresh()
            except Exception:
                logger.debug("[CompanionV2] 汇率后台刷新调度跳过", exc_info=True)
            try:
                self._pricing_catalog_service(runtime.data_root, session).maybe_schedule_daily_sync()
            except Exception:
                logger.debug("[CompanionV2] 价格源后台同步调度跳过", exc_info=True)
            payload = await self._build_pricing_response(session, operator, manager, runtime, settings)
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except PricingSettingsError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (OSError, ValueError):
            return self._response(Outcome.failed("pricing_config_unavailable", "无法读取模型计价配置"))

    async def pricing_save(self):
        try:
            data = _document(await request.body(), {"revision", "changes"}, limit=524_288)
            if type(data["changes"]) is not dict:
                raise ValueError("invalid pricing changes")
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            updated = await asyncio.to_thread(
                self._pricing_store(runtime).update, data["revision"], data["changes"]
            )
            payload = await self._build_pricing_response(session, operator, manager, runtime, updated)
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except PricingSettingsError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("pricing_config_invalid", "模型计价配置字段无效"))

    async def pricing_sync_rates(self):
        try:
            raw = await request.body()
            if raw and raw.strip():
                _document_optional(raw, set(), optional=(), limit=1024)
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            fx_service = self._exchange_rate_service(runtime.data_root)
            sync_result = await fx_service.sync_now()
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            settings = await asyncio.to_thread(self._pricing_store(runtime).read)
            payload = await self._build_pricing_response(session, operator, manager, runtime, settings)
            return self._response(Outcome.success({**payload, "sync": sync_result}))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except PricingSettingsError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("pricing_sync_invalid", "汇率刷新请求无效"))

    async def pricing_sync_catalog(self):
        try:
            raw = await request.body()
            source_ids = None
            if raw and raw.strip():
                data = _document_optional(raw, set(), optional=("source_ids",), limit=4096)
                if "source_ids" in data and data["source_ids"] is not None:
                    if (
                        type(data["source_ids"]) is not list
                        or len(data["source_ids"]) > 64
                        or any(type(s) is not str or not s.strip() for s in data["source_ids"])
                    ):
                        raise ValueError("invalid source_ids")
                    source_ids = [s.strip() for s in data["source_ids"]]
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            _p, _s, connections = await asyncio.to_thread(collect_provider_inventory, self.controller, session)
            catalog_service = self._pricing_catalog_service(runtime.data_root, session)
            sync_result = await catalog_service.sync_sources(
                source_ids=source_ids, connections=connections, record_daily_timestamp=True
            )
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            settings = await asyncio.to_thread(self._pricing_store(runtime).read)
            payload = await self._build_pricing_response(session, operator, manager, runtime, settings)
            return self._response(Outcome.success({**payload, "sync": sync_result}))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except PricingSettingsError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("pricing_sync_invalid", "价格源同步请求无效"))

    async def pricing_source_toggle(self):
        try:
            data = _document_optional(
                await request.body(),
                {"source_id", "enabled"},
                optional=("sync", "revision"),
                limit=4096,
            )
            source_id = data["source_id"]
            enabled = data["enabled"]
            do_sync = data.get("sync", False)
            revision = data.get("revision")
            if type(source_id) is not str or not source_id.strip() or type(enabled) is not bool or type(do_sync) is not bool:
                raise ValueError("invalid source toggle")
            if revision is not None and type(revision) is not str:
                raise ValueError("invalid revision")
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            updated = await asyncio.to_thread(
                self._pricing_store(runtime).set_source_enabled,
                source_id.strip(),
                enabled,
                revision=revision,
            )
            sync_result = None
            if enabled and do_sync:
                _p, _s, connections = await asyncio.to_thread(collect_provider_inventory, self.controller, session)
                catalog_service = self._pricing_catalog_service(runtime.data_root, session)
                sync_result = await catalog_service.sync_sources(
                    source_ids=[source_id.strip()], connections=connections
                )
                updated = await asyncio.to_thread(self._pricing_store(runtime).read)
            payload = await self._build_pricing_response(session, operator, manager, runtime, updated)
            return self._response(Outcome.success({
                **payload,
                "source_id": source_id.strip(),
                "enabled": enabled,
                "sync": sync_result,
            }))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except PricingSettingsError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("pricing_source_invalid", "价格源切换请求无效"))

    async def pricing_source_currency(self):
        try:
            data = _document_optional(
                await request.body(),
                {"source_id", "currency_override"},
                optional=("sync", "revision", "fiat_per_base_unit"),
                limit=4096,
            )
            source_id = data["source_id"]
            currency_override = data["currency_override"]
            do_sync = data.get("sync", True)
            revision = data.get("revision")
            has_fiat_rate = "fiat_per_base_unit" in data
            raw_fiat_rate = data.get("fiat_per_base_unit") if has_fiat_rate else ...
            if (
                type(source_id) is not str or not source_id.strip()
                or type(currency_override) is not str
                or currency_override not in ("auto", "CNY", "USD")
                or type(do_sync) is not bool
            ):
                raise ValueError("invalid source currency override")
            if revision is not None and type(revision) is not str:
                raise ValueError("invalid revision")
            if has_fiat_rate and raw_fiat_rate is not None:
                if isinstance(raw_fiat_rate, bool) or type(raw_fiat_rate) not in (int, float):
                    raise ValueError("invalid fiat_per_base_unit")
            sid = source_id.strip()
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            updated = await asyncio.to_thread(
                self._pricing_store(runtime).set_source_currency_override,
                sid,
                currency_override,
                fiat_per_base_unit=raw_fiat_rate,
                revision=revision,
            )
            sync_result = None
            if do_sync:
                _p, _s, connections = await asyncio.to_thread(collect_provider_inventory, self.controller, session)
                catalog_service = self._pricing_catalog_service(runtime.data_root, session)
                sync_result = await catalog_service.sync_sources(
                    source_ids=[sid], connections=connections
                )
                updated = await asyncio.to_thread(self._pricing_store(runtime).read)
            payload = await self._build_pricing_response(session, operator, manager, runtime, updated)
            return self._response(Outcome.success({
                **payload,
                "source_id": sid,
                "currency_override": currency_override,
                "sync": sync_result,
            }))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except PricingSettingsError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("pricing_source_invalid", "价格源币种设置请求无效"))

    async def pricing_source_detect(self):
        try:
            data = _document_optional(
                await request.body(),
                {"provider_id"},
                optional=("enable",),
                limit=4096,
            )
            provider_id = data["provider_id"]
            enable = data.get("enable", True)
            if type(provider_id) is not str or not provider_id.strip() or len(provider_id) > 128 or type(enable) is not bool:
                raise ValueError("invalid detect request")
            pid = provider_id.strip()
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            providers_by_id, _suppliers, connections = await asyncio.to_thread(
                collect_provider_inventory, self.controller, session
            )
            conn = connections.get(pid)
            if not conn or not conn.get("api_base"):
                return self._response(Outcome.failed("pricing_detect_unavailable", "该供应商未配置可探测的接口地址"))
            catalog_service = self._pricing_catalog_service(runtime.data_root, session)
            prov_info = providers_by_id.get(pid) or {}
            detection = await catalog_service.detect_newapi_source(
                provider_id=pid,
                api_base=conn["api_base"],
                api_key=conn.get("api_key", ""),
                proxy=conn.get("proxy", ""),
                supplier_label=conn.get("supplier_name") or prov_info.get("supplier_name") or pid,
                cluster_id=conn.get("cluster_id") or prov_info.get("cluster_id") or pid,
                enable=enable,
            )
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            settings = await asyncio.to_thread(self._pricing_store(runtime).read)
            payload = await self._build_pricing_response(session, operator, manager, runtime, settings)
            return self._response(Outcome.success({**payload, "detection": detection}))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except PricingSettingsError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("pricing_detect_invalid", "New API 探测请求无效"))

    async def pricing_select(self):
        try:
            data = _document_optional(
                await request.body(),
                {"provider_id", "model", "price_key"},
                optional=("revision",),
                limit=4096,
            )
            pid = data["provider_id"]
            model = data["model"]
            price_key = data["price_key"]
            revision = data.get("revision")
            if (
                type(pid) is not str or not pid.strip()
                or type(model) is not str or not model.strip()
                or type(price_key) is not str or not price_key.strip()
            ):
                raise ValueError("invalid price selection")
            if revision is not None and type(revision) is not str:
                raise ValueError("invalid revision")
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            existing = await asyncio.to_thread(self._pricing_catalog_store(runtime).get_price, price_key.strip())
            if existing is None:
                return self._response(Outcome.failed("pricing_candidate_not_found", "未在当前价格目录中找到该候选价格"))
            updated = await asyncio.to_thread(
                self._pricing_store(runtime).set_price_selection,
                pid.strip(),
                model.strip(),
                price_key.strip(),
                revision=revision,
            )
            payload = await self._build_pricing_response(session, operator, manager, runtime, updated)
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except PricingSettingsError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("pricing_select_invalid", "价格源选择请求无效"))

    async def pricing_select_reset(self):
        try:
            data = _document_optional(
                await request.body(),
                {"provider_id"},
                optional=("model", "revision"),
                limit=4096,
            )
            pid = data["provider_id"]
            model = data.get("model")
            revision = data.get("revision")
            if type(pid) is not str or not pid.strip():
                raise ValueError("invalid provider_id")
            if model is not None and type(model) is not str:
                raise ValueError("invalid model")
            if revision is not None and type(revision) is not str:
                raise ValueError("invalid revision")
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            updated = await asyncio.to_thread(
                self._pricing_store(runtime).clear_price_selection,
                pid.strip(),
                model=model,
                revision=revision,
            )
            payload = await self._build_pricing_response(session, operator, manager, runtime, updated)
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except PricingSettingsError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("pricing_select_invalid", "清除价格源选择请求无效"))

    async def pricing_expr_validate(self):
        try:
            data = _document_optional(
                await request.body(),
                set(),
                optional=("expr", "tiers", "default_expr", "cluster_multiplier", "currency", "sample"),
                limit=32_768,
            )
            session, _operator, _manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            settings = await asyncio.to_thread(self._pricing_store(runtime).read_safe)
            usd_cny_rate = effective_usd_cny_rate(settings)
            sample = data.get("sample")
            if sample is not None and type(sample) is not dict:
                raise ValueError("invalid sample")
            if "currency" in data and data["currency"] is not None:
                if type(data["currency"]) is not str or data["currency"] not in ("USD", "CNY"):
                    raise ValueError("invalid currency")
            rule_payload = {k: v for k, v in data.items() if k != "sample"}
            result = await asyncio.to_thread(
                validate_tiered_expr_rule, rule_payload, usd_cny_rate=usd_cny_rate, sample=sample
            )
            return self._response(Outcome.success(result))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("pricing_expr_invalid", "表达式验证请求无效"))

    async def pricing_reset(self):
        try:
            raw = await request.body()
            data = (
                _document_optional(raw, set(), optional=("scope", "provider_id", "revision"), limit=4096)
                if raw and raw.strip()
                else {}
            )
            scope = data.get("scope", "all")
            provider_id = data.get("provider_id")
            revision = data.get("revision")
            if type(scope) is not str:
                raise ValueError("invalid scope")
            if provider_id is not None and type(provider_id) is not str:
                raise ValueError("invalid provider_id")
            if revision is not None and type(revision) is not str:
                raise ValueError("invalid revision")
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            updated = await asyncio.to_thread(
                self._pricing_store(runtime).reset_pricing,
                scope=scope,
                provider_id=provider_id,
                revision=revision,
            )
            payload = await self._build_pricing_response(session, operator, manager, runtime, updated)
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except PricingSettingsError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("pricing_reset_invalid", "重置计价配置请求无效"))

    async def pricing_residue_clear(self):
        try:
            raw = await request.body()
            data = (
                _document_optional(raw, set(), optional=("provider_ids", "revision"), limit=8192)
                if raw and raw.strip()
                else {}
            )
            raw_ids = data.get("provider_ids")
            revision = data.get("revision")
            target_ids = None
            if raw_ids is not None:
                if (
                    type(raw_ids) is not list
                    or len(raw_ids) > 256
                    or any(type(p) is not str or not p.strip() for p in raw_ids)
                ):
                    raise ValueError("invalid provider_ids")
                target_ids = {p.strip() for p in raw_ids}
            if revision is not None and type(revision) is not str:
                raise ValueError("invalid revision")
            session, operator, manager, _status = await self._authorize_pricing()
            runtime = getattr(getattr(session, "application", None), "runtime", None) or self.controller.runtime
            providers_by_id, _suppliers, _conns = await asyncio.to_thread(
                collect_provider_inventory, self.controller, session
            )
            configured_pids = set(providers_by_id)
            configured_cids = {info["cluster_id"] for info in providers_by_id.values()}
            updated, cleared = await asyncio.to_thread(
                self._pricing_store(runtime).clear_residue,
                configured_provider_ids=configured_pids,
                configured_cluster_ids=configured_cids,
                target_provider_ids=target_ids,
                revision=revision,
            )
            payload = await self._build_pricing_response(session, operator, manager, runtime, updated)
            return self._response(Outcome.success({**payload, "cleared_provider_ids": cleared}))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except PricingSettingsError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("pricing_residue_invalid", "清理已删除供应商残留请求无效"))

    @staticmethod
    def _laya_error(error):
        return getattr(error, "code", "laya_unavailable"), str(getattr(error, "reason", error))

    @staticmethod
    def _laya_client_failure(error, service=None):
        """Build an ok-enveloped failure payload so the host bridge keeps safe reasons."""
        api_key = ""
        if service is not None:
            try:
                api_key = str(service.store.raw().get("api_key") or "")
            except Exception:
                api_key = ""
        return {
            "status": "failed",
            "code": getattr(error, "code", "laya_unavailable"),
            "reason": safe_laya_client_error_reason(error, api_key=api_key),
        }

    async def laya_status(self):
        try:
            payload = await self._run_authorized_read(lambda *_: self._laya_service().status())
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (LayaSettingsError, OSError, ValueError) as error:
            code, reason = self._laya_error(error)
            return self._response(Outcome.failed(code, reason))

    async def laya_save(self):
        try:
            data = _document(await request.body(), {"revision", "changes"}, limit=16000)
            if type(data["changes"]) is not dict:
                raise ValueError("invalid Laya changes")
            async with self.controller._control_lock:
                self._authorized()
                service = self._laya_service()
                service.save(data["revision"], data["changes"])
                return self._response(Outcome.success(service.status()))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (LayaSettingsError, ValueError, TypeError, KeyError, UnicodeError,
                RecursionError, OverflowError) as error:
            code, reason = self._laya_error(error)
            return self._response(Outcome.failed(code, reason))

    async def laya_logs(self):
        try:
            query = request.query or {}
            scope = query.get("scope", "all")

            def load_logs(*_args):
                if scope not in ("all", "current"):
                    raise TaskError("model_request_invalid", "invalid Laya log scope")
                service = self._laya_service()
                data = service.recent() if scope == "current" else service.recent(all_origins=True)
                data["scope"] = scope
                return data

            data = await self._run_authorized_read(load_logs, allow_query_keys={"scope"})
            return self._response(Outcome.success(data))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (LayaSettingsError, OSError, ValueError) as error:
            code, reason = self._laya_error(error)
            return self._response(Outcome.failed(code, reason))

    async def laya_test(self):
        service = None
        try:
            _document(await request.body(), set(), limit=1024)
            async with self.controller._control_lock:
                self._authorized()
                service = self._laya_service()
                service.set_trace(source="probe")
            async with self._laya_lock:
                result = await service.test()
            return self._response(Outcome.success(result))
        except LayaClientError as error:
            return self._response(Outcome.success(self._laya_client_failure(error, service)))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (LayaSettingsError, OSError, ValueError, TypeError,
                KeyError, UnicodeError, RecursionError, OverflowError) as error:
            code, reason = self._laya_error(error)
            return self._response(Outcome.failed(code, reason))

    async def laya_decide(self):
        service = None
        try:
            data = _document(await request.body(), {"facts"}, limit=32000)
            if type(data["facts"]) is not dict:
                raise ValueError("facts must be an object")
            async with self.controller._control_lock:
                self._authorized()
                service = self._laya_service()
                service.set_trace(source="dashboard_preview")
            async with self._laya_lock:
                result = await service.decide(data["facts"])
            return self._response(Outcome.success(result))
        except LayaClientError as error:
            return self._response(Outcome.success(self._laya_client_failure(error, service)))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (LayaSettingsError, OSError, ValueError, TypeError,
                KeyError, UnicodeError, RecursionError, OverflowError) as error:
            code, reason = self._laya_error(error)
            return self._response(Outcome.failed(code, reason))

    def _voice_projection(self, session, operator):
        status = require(ReplyManagement(session.application, session.profile.target,
            layer=ConfigLayer.ROLE).status(operator))
        settings = status["settings"]
        catalog = ReplyWebAPI(self.controller)._tts_catalog()
        return {"languages": [{"k": lang, "n": label, "bind": settings[field]}
                               for lang, label in LANGUAGES.items() for field in (VOICE_FIELDS[lang],)],
                "channels": [{"k": channel, "n": label,
                              "bindings": {lang: settings[CHANNEL_VOICE_FIELDS[channel][lang]]
                                           for lang in LANGUAGES}}
                             for channel, label in VOICE_CHANNELS.items()],
                "providers": catalog["providers"], "catalog_state": catalog["state"],
                "timeout_seconds": settings["voice_timeout_seconds"],
                "voice_enabled": settings["voice_enabled"], "edit_token": status["edit_token"]}

    async def image_schema(self):
        """按 provider 类型返回生图字段声明（只读，不涉及凭证）。

        页面用它在生图配置旁渲染“该服务支持哪些字段、默认值是什么”，
        让用户不必靠猜键名；真正的保存仍走既有配置接口。
        """
        try:
            await self._run_authorized_read()
            return self._response(Outcome.success({
                "image_provider_group": IMAGE_PROVIDER_GROUP}))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except StorageOpenError:
            return self._response(Outcome.failed(
                "model_storage_unavailable", "model storage is temporarily unavailable"))

    async def voice(self):
        try:
            payload = await self._run_authorized_read(
                lambda session, operator, _manager, _status, _at: self._voice_projection(session, operator)
            )
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except StorageOpenError:
            return self._response(Outcome.failed(
                "model_storage_unavailable", "model storage is temporarily unavailable"))

    async def voice_save(self):
        try:
            data = _document(await request.body(), {"operation_key", "edit_token", "changes"})
            changes = data["changes"]
            if type(changes) is not dict or not changes or set(changes) - VOICE_CHANGES:
                raise ValueError("invalid voice changes")
            for name, value in changes.items():
                if name == "voice_timeout_seconds":
                    if type(value) is not int or not 1 <= value <= 120:
                        raise ValueError("invalid voice timeout")
                elif type(value) is not str or len(value) > 128:
                    raise ValueError("invalid voice binding")
            lock = self.controller._control_lock
            try:
                await asyncio.wait_for(lock.acquire(), timeout=VOICE_SAVE_LOCK_WAIT_SECONDS)
            except TimeoutError:
                return self._response(Outcome.failed(
                    "host_busy", "the companion host is starting or switching; nothing was written"))
            try:
                session, operator, _manager, _status = self._authorized()
                saved = require(ReplyManagement(session.application, session.profile.target,
                    layer=ConfigLayer.ROLE).configure(operator, data))
                return self._response(Outcome.success({**self._voice_projection(session, operator),
                    "replayed": saved["replayed"]}))
            finally:
                lock.release()
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("model_request_invalid", "invalid voice settings request"))

    async def voice_test(self):
        request_id = uuid4().hex[:12]
        started = monotonic()
        try:
            data = _document(await request.body(), {"provider_id", "language"}, limit=1024)
            provider_id = data["provider_id"]
            language = data["language"]
            if (type(provider_id) is not str or not provider_id or len(provider_id) > 128
                    or type(language) is not str or language not in LANGUAGES):
                raise ValueError("invalid TTS provider id")
            async with self.controller._control_lock:
                session, operator, _manager, _status = self._authorized()
                projection = self._voice_projection(session, operator)
                if provider_id not in {p["id"] for p in projection["providers"]}:
                    return self._response(Outcome.success({
                        "provider_id": provider_id, "status": "unavailable",
                        "latency_ms": round((monotonic() - started) * 1000),
                        "code": "model_voice_provider_unavailable", "stage": "provider_lookup",
                        "exception_type": "ProviderUnavailable",
                        "error": "TTS Provider is not present in the loaded provider catalog.",
                        "suggestion": _voice_suggestion("model_voice_provider_unavailable", "provider_lookup"),
                        "request_id": request_id,
                    }))
                try:
                    provider = _find_tts_provider(self.controller.context, provider_id)
                except Exception as error:
                    return self._response(Outcome.success({
                        "provider_id": provider_id, "status": "unknown",
                        "latency_ms": round((monotonic() - started) * 1000),
                        "code": "model_voice_provider_unavailable", "stage": "provider_lookup",
                        "exception_type": type(error).__name__, "error": _voice_error_text(error),
                        "suggestion": _voice_suggestion("model_voice_provider_unavailable", "provider_lookup"),
                        "request_id": request_id,
                    }))
                if provider is None or not callable(getattr(provider, "get_audio", None)):
                    return self._response(Outcome.success({
                        "provider_id": provider_id, "status": "unavailable",
                        "latency_ms": round((monotonic() - started) * 1000),
                        "code": "model_voice_provider_unavailable", "stage": "provider_lookup",
                        "exception_type": "ProviderUnavailable",
                        "error": "The loaded TTS provider has no callable get_audio method.",
                        "suggestion": _voice_suggestion("model_voice_provider_unavailable", "provider_lookup"),
                        "request_id": request_id,
                    }))
                timeout = projection["timeout_seconds"]

            async def synthesize():
                method = provider.get_audio
                sample = {"zh": "你好。", "ja": "こんにちは。", "en": "Hello."}[language]
                result = method(sample) if inspect.iscoroutinefunction(method) else await asyncio.to_thread(method, sample)
                path = await result if inspect.isawaitable(result) else result
                if isinstance(path, (list, tuple)):
                    path = path[0] if path else None
                if path is None or not isinstance(path, (str, Path)) or not str(path).strip():
                    return path, None, None
                try:
                    audio = await asyncio.to_thread(_read_audio, path)
                except Exception as error:
                    return path, None, error
                return path, audio, None

            response = None
            try:
                audio_path, audio, read_error = await asyncio.wait_for(synthesize(), timeout=timeout)
                if read_error is not None:
                    response = {"status": "unknown", "code": "model_voice_audio_read_failed",
                                "stage": "audio_read", "exception_type": type(read_error).__name__,
                                "error": _voice_error_text(read_error, provider),
                                "suggestion": _voice_suggestion("model_voice_audio_read_failed", "audio_read")}
                elif audio is None:
                    response = {"status": "unavailable", "code": "model_voice_audio_invalid",
                                "stage": "audio_read", "exception_type": "AudioValidationError",
                                "error": "The TTS provider returned no readable audio bytes.",
                                "suggestion": _voice_suggestion("model_voice_audio_invalid", "audio_read")}
                else:
                    response = {"status": "ok", "code": "", "stage": "audio_read",
                                "exception_type": "", "error": "", "suggestion": "",
                                "audio_base64": base64.b64encode(audio).decode("ascii"),
                                "audio_mime_type": VOICE_AUDIO_MIME_TYPES.get(
                                    Path(audio_path).suffix.lower(), "application/octet-stream")}
            except asyncio.TimeoutError:
                response = {"status": "unknown", "code": "model_voice_probe_timeout",
                            "stage": "synthesis", "exception_type": "TimeoutError",
                            "error": f"TTS synthesis exceeded the configured timeout of {timeout} seconds.",
                            "suggestion": _voice_suggestion("model_voice_probe_timeout", "synthesis")}
            except Exception as error:
                response = {"status": "unknown", "code": "model_voice_probe_failed",
                            "stage": "synthesis", "exception_type": type(error).__name__,
                            "error": _voice_error_text(error, provider),
                            "suggestion": _voice_suggestion("model_voice_probe_failed", "synthesis")}
            async with self.controller._control_lock:
                self._authorized()
                try:
                    current_provider = _find_tts_provider(self.controller.context, provider_id)
                except Exception as error:
                    current_provider = None
                    response = {"status": "unknown", "code": "model_voice_provider_changed",
                                "stage": "provider_recheck", "exception_type": type(error).__name__,
                                "error": _voice_error_text(error),
                                "suggestion": _voice_suggestion("model_voice_provider_changed", "provider_recheck")}
                if current_provider is not provider:
                    response = {"status": "unknown", "code": "model_voice_provider_changed",
                                "stage": "provider_recheck", "exception_type": "ProviderChanged",
                                "error": "The TTS provider instance changed while the synthesis probe was running.",
                                "suggestion": _voice_suggestion("model_voice_provider_changed", "provider_recheck")}
            response.update({"provider_id": provider_id,
                             "latency_ms": round((monotonic() - started) * 1000),
                             "request_id": request_id})
            return self._response(Outcome.success(response))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("model_request_invalid", "invalid voice probe request"))

    def _preferred_image_projection(self, registry):
        """The first named model in the configured generate queue, not the native owner id."""
        try:
            getter = getattr(registry, "image_extension_api", None)
            api = getter() if callable(getter) else None
            reader = getattr(api, "output_configuration", None)
            if not callable(reader):
                return {}
            projected = reader()
            settings = projected.get("settings") if isinstance(projected, dict) else None
            if not isinstance(settings, dict):
                return {}
            endpoints = {
                item.get("id"): item for item in settings.get("endpoints", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            routes = settings.get("operation_routes", {}).get("generate", [])
            if not isinstance(routes, list):
                return {}
            first_configured = None
            for route in routes:
                if route == "nai":
                    nai = projected.get("nai") or {}
                    model = nai.get("default_model", "")
                    candidate = {
                        "model": model.strip() if isinstance(model, str) else "",
                        "name": "NAI 生图插件",
                        "route": route,
                        "enabled": settings.get("enabled") is True and nai.get("available") is True
                                   and "generate" in (nai.get("operations") or ()),
                    }
                    if first_configured is None:
                        first_configured = candidate
                    if candidate["enabled"]:
                        return candidate
                    continue
                if not isinstance(route, str) or not route.startswith("online:"):
                    continue
                endpoint = endpoints.get(route[7:])
                if not isinstance(endpoint, dict):
                    continue
                model = endpoint.get("model")
                if not isinstance(model, str) or not model.strip():
                    continue
                name = endpoint.get("name")
                name = name.strip() if isinstance(name, str) else ""
                candidate = {
                    "model": model,
                    "name": name or model,
                    "route": route,
                    "enabled": settings.get("enabled") is True and endpoint.get("enabled") is True,
                }
                if first_configured is None:
                    first_configured = candidate
                if candidate["enabled"]:
                    return candidate
            return first_configured or {}
        except Exception:
            # The status endpoint must still expose action readiness even when
            # the image configuration cannot be projected.
            return {}
        return {}

    def _image_projection(self):
        registry = self.controller.native_extensions
        path = Path(self.controller.data_root) / IMAGE_SETTINGS
        enabled = registry.image_enabled if registry is not None else read_image_settings(path)
        declarations = registry.declarations() if registry is not None else ()
        module = (registry.status("image") if registry is not None
                  else {"state": "unavailable", "error_code": "native_registry_unavailable"})
        issue_code = next((code for package_id, code in getattr(registry, "issues", ())
                           if isinstance(package_id, str) and package_id.endswith(".image")), "")
        base = getattr(self.controller, "controller", self.controller)
        session = base.session or base.last_session
        external = getattr(getattr(session, "application", None), "external", None)
        rows = []
        for action in sorted(IMAGE_ACTIONS):
            owners = [item for item in declarations if item.descriptor.tool.action == action]
            owner = owners[0] if len(owners) == 1 else None
            current = bool(owner and registry.current_declaration(owner.host_tool_id) == owner)
            readiness = (registry.declaration_readiness(owner.host_tool_id)
                         if owner and callable(getattr(registry, "declaration_readiness", None))
                         else "ready" if current else "stale")
            registered = bool(owner and external and external.providers.get(action)
                              and external.providers[action].descriptor == owner.descriptor)
            if not enabled[action]:
                diagnostic = "image_action_disabled"
            elif not owner:
                diagnostic = issue_code or module.get("error_code") or "native_image_declaration_missing"
            elif readiness == "unsupported":
                diagnostic = "native_image_operation_unavailable"
            elif not current:
                diagnostic = "native_declaration_stale"
            elif not registered:
                diagnostic = "native_host_registration_missing"
            else:
                diagnostic = ""
            rows.append({"action": action, "enabled": enabled[action],
                         "owner": owner.descriptor.package.extension_id if owner else "",
                         "provider_id": owner.descriptor.binding.provider if owner else "",
                         "loaded": bool(owner), "current": current, "registered": registered,
                         "diagnostic_code": diagnostic,
                         "state": "disabled" if not enabled[action] else
                                  "ready" if current and registered else
                                  "unsupported" if readiness == "unsupported" else "unavailable"})
        module = {**module, "issue_code": issue_code,
                  "declared_actions": sum(item.descriptor.package.extension_id == "image"
                                           for item in declarations)}
        return {"actions": rows, "enabled": dict(enabled), "module": module,
                "authorization": "checked_for_each_invocation",
                "preferred_image": self._preferred_image_projection(registry)}

    async def image(self):
        try:
            payload = await self._run_authorized_read(lambda *_: self._image_projection())
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except StorageOpenError:
            return self._response(Outcome.failed(
                "model_storage_unavailable", "model storage is temporarily unavailable"))

    async def image_save(self):
        try:
            data = _document(await request.body(), {"enabled"}, limit=1024)
            enabled = data["enabled"]
            if type(enabled) is not dict or set(enabled) != IMAGE_ACTIONS or any(
                    type(value) is not bool for value in enabled.values()):
                raise ValueError("invalid native image switches")
            async with self.controller._control_lock:
                self._authorized()
                registry = self.controller.native_extensions
                write_image_settings(Path(self.controller.data_root) / IMAGE_SETTINGS, enabled)
                if registry is not None:
                    registry.set_image_enabled(enabled)
                return self._response(Outcome.success(self._image_projection()))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("model_request_invalid", "invalid image settings request"))

    async def image_test(self):
        try:
            data = _document(await request.body(), {"action"}, limit=1024)
            if type(data["action"]) is not str or data["action"] not in IMAGE_ACTIONS:
                raise ValueError("invalid native image action")
            async with self.controller._control_lock:
                self._authorized()
                row = next(item for item in self._image_projection()["actions"] if item["action"] == data["action"])
                return self._response(Outcome.success({**row, "check": "registration_only"}))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("model_request_invalid", "invalid image check request"))

    # ---------- TTS provider management (AstrBot ProviderManager is the owner) ----------
    def _provider_manager(self):
        manager = getattr(self.controller.context, "provider_manager", None)
        if manager is None:
            raise TaskError("tts_provider_unsupported", "this AstrBot build has no provider manager")
        return manager

    def _tts_schema_bundle(self):
        """Build a stable, V1-compatible projection of AstrBot's TTS schema.

        AstrBot's metadata is the source of truth for defaults, options and
        limits, but older builds sometimes expose unresolved i18n keys (and
        some builds do not expose the metadata at all).  The page must still
        provide a usable editor in those cases, so labels, secrets and the
        Fish Audio model list have explicit fallbacks here.
        """
        config_templates, field_metadata = {}, {}
        try:
            from astrbot.core.config.default import CONFIG_METADATA_2
            provider_metadata = (CONFIG_METADATA_2.get("provider_group", {})
                                 .get("metadata", {}).get("provider", {}))
            config_templates = deepcopy(provider_metadata.get("config_template", {}) or {})
            field_metadata = deepcopy(provider_metadata.get("items", {}) or {})
        except Exception:
            # Derive a minimal schema from the live configs when the metadata
            # index is unavailable.  This keeps existing providers editable;
            # creation simply shows no unsupported templates in that case.
            try:
                manager = self._provider_manager()
                for item in list(getattr(manager, "providers_config", None) or []):
                    if isinstance(item, dict) and _is_tts_provider_config(item):
                        config_templates.setdefault(str(item.get("type") or ""), deepcopy(item))
            except Exception:
                config_templates = {}

        templates, by_type = [], {}
        for display_name, source in config_templates.items():
            if not _is_tts_provider_config(source):
                continue
            defaults = deepcopy(source)
            provider_type = _single_line(defaults.get("type"), 80)
            if not provider_type:
                continue
            if provider_type == "fishaudio_tts_api":
                defaults.setdefault("model", "s2.1-pro-free")
            fields = _schema_fields(defaults, field_metadata, provider_type)
            name = _single_line(display_name, 120)
            if not name or _metadata_unresolved(name) or "�" in name:
                name = TTS_PROVIDER_LABELS.get(provider_type, provider_type)
            template = {
                "name": name,
                "label": name,
                "type": provider_type,
                "provider": _single_line(defaults.get("provider"), 80),
                "hint": _multi_line(defaults.get("hint"), 600),
                "default_id": _single_line(defaults.get("id"), 80),
                "defaults": {key: deepcopy(value) for key, value in defaults.items() if key != "hint"},
                "fields": fields,
            }
            templates.append(template)
            by_type[provider_type] = template
        templates.sort(key=lambda item: (item["name"].lower(), item["type"]))
        return {"templates": templates, "by_type": by_type,
                "image_provider_group": IMAGE_PROVIDER_GROUP}

    def _tts_templates(self):
        bundle = self._tts_schema_bundle()
        return {item["type"]: item for item in bundle.get("templates", [])}

    # Keep the V1 helper name available for diagnostics and parity tests while
    # the V2 route continues to expose its compact template map internally.
    def _tts_provider_schema_bundle(self):
        return self._tts_schema_bundle()

    def _tts_provider_payload(self):
        manager = self._provider_manager()
        bundle = self._tts_schema_bundle()
        templates = bundle.get("by_type", {})
        loaded = set()
        try:
            for provider in self.controller.context.get_all_tts_providers():
                meta = provider.meta() if callable(getattr(provider, "meta", None)) else None
                identifier = getattr(meta, "id", "") or getattr(provider, "id", "")
                if type(identifier) is str and identifier:
                    loaded.add(identifier)
        except Exception:
            loaded = set()
        using_id = ""
        try:
            getter = getattr(self.controller.context, "get_using_tts_provider", None)
            if callable(getter):
                using = getter()
                meta = using.meta() if callable(getattr(using, "meta", None)) else None
                using_id = str(getattr(meta, "id", "") or getattr(using, "id", "") or "")
        except Exception:
            using_id = ""
        items = []
        for config in list(getattr(manager, "providers_config", None) or []):
            if not _is_tts_provider_config(config):
                continue
            provider_type = _single_line(config.get("type"), 80)
            template = templates.get(provider_type) or {"name": provider_type, "label": provider_type, "fields": []}
            merged = deepcopy(config)
            merger = getattr(manager, "get_merged_provider_config", None)
            if callable(merger):
                try:
                    candidate = merger(config)
                    merged = deepcopy(candidate if isinstance(candidate, dict) else config)
                except Exception:
                    merged = deepcopy(config)
            fields = deepcopy(template.get("fields", []) or [])
            known = {field["key"] for field in fields}
            fields = []
            for field in template.get("fields", []) or []:
                key = field["key"]
                value = merged.get(key, field.get("default"))
                entry = dict(field)
                if field["secret"]:
                    entry.update({"value": "", "set": value not in (None, "", [], {})})
                else:
                    entry.update({"value": value, "set": value not in (None, "", [], {})})
                fields.append(entry)
            for key, value in sorted(merged.items()):
                if key in known or key in TTS_PROVIDER_SYSTEM_KEYS:
                    continue
                secret = _tts_secret_field(key)
                fields.append({"key": key, "type": _field_type(value, {}), "label": _tts_fallback_label(key),
                    "hint": _tts_fallback_hint(key), "options": [], "secret": secret, "default": None,
                    "group": _tts_field_group(key), "value": "" if secret else value,
                    "set": value not in (None, "", [], {})})
            values, secret_configured = {}, {}
            for field in fields:
                key = field["key"]
                value = deepcopy(merged.get(key, field.get("default")))
                if field.get("secret"):
                    secret_configured[key] = value not in (None, "", [], {})
                    value = ""
                values[key] = value
            provider_id = _single_line(config.get("id"), 128)
            items.append({"id": provider_id, "type": provider_type,
                "label": _single_line(template.get("name") or provider_type, 120),
                "name": _single_line(template.get("name") or provider_type, 120),
                "provider": _single_line(config.get("provider"), 80),
                "provider_source_id": _single_line(config.get("provider_source_id"), 160),
                "enable": config.get("enable") is True, "loaded": provider_id in loaded,
                "is_default": provider_id == using_id, "model": _single_line(merged.get("model") or merged.get("gemini_tts_model"), 160),
                "fields": fields, "values": values, "secret_configured": secret_configured})
        items.sort(key=lambda item: (not item["enable"], not item["loaded"], item["name"].lower(), item["id"].lower()))
        return {"items": items, "templates": list(bundle.get("templates", [])),
                "fish_audio_models": deepcopy(FISH_AUDIO_MODEL_OPTIONS),
                "total": len(items), "enabled": sum(1 for item in items if item["enable"]),
                "loaded": sum(1 for item in items if item["loaded"])}

    def _coerce_tts_value(self, field, value):
        ftype = field["type"]
        if ftype == "bool":
            if type(value) is bool:
                return value
            return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
        if ftype in {"int", "float"}:
            text = str(value if value is not None else "").strip()
            if not text:
                raise TaskError("tts_provider_invalid", field["label"] + " is required")
            try:
                return int(text) if ftype == "int" else float(text)
            except ValueError:
                raise TaskError("tts_provider_invalid", field["label"] + " must be a number")
        if ftype in {"object", "list"}:
            if isinstance(value, dict) or isinstance(value, list):
                parsed = value
                if ftype == "object" and not isinstance(parsed, dict):
                    raise TaskError("tts_provider_invalid", field["label"] + " must be a JSON object")
                if ftype == "list" and not isinstance(parsed, list):
                    raise TaskError("tts_provider_invalid", field["label"] + " must be a JSON list")
                return parsed
            text = str(value or "").strip()
            if not text:
                return {} if ftype == "object" else []
            try:
                parsed = json.loads(text)
            except ValueError:
                raise TaskError("tts_provider_invalid", field["label"] + " must be valid JSON")
            if ftype == "object" and not isinstance(parsed, dict):
                raise TaskError("tts_provider_invalid", field["label"] + " must be a JSON object")
            if ftype == "list" and not isinstance(parsed, list):
                raise TaskError("tts_provider_invalid", field["label"] + " must be a JSON list")
            return parsed
        return str(value if value is not None else "")[:12000]

    def _tts_provider_by_id(self, manager, provider_id):
        getter = getattr(manager, "get_provider_config_by_id", None)
        current = deepcopy(getter(provider_id)) if callable(getter) else None
        if not _is_tts_provider_config(current):
            current = next((deepcopy(item) for item in getattr(manager, "providers_config", None) or []
                            if isinstance(item, dict) and str(item.get("id") or "") == provider_id), None)
        if not _is_tts_provider_config(current):
            raise TaskError("tts_provider_missing", "TTS provider not found")
        return current

    def _normalized_tts_provider_update(self, current, incoming, schema_bundle=None):
        """Normalize the V1 ``{enable, values}`` editor contract for V2 routes."""
        schema_bundle = schema_bundle or self._tts_schema_bundle()
        provider_type = _single_line(current.get("type"), 80)
        template = schema_bundle.get("by_type", {}).get(provider_type, {})
        field_map = {str(field.get("key")): field for field in template.get("fields", []) or []}
        field_map.setdefault("enable", {"key": "enable", "type": "bool", "label": "启用", "secret": False})
        for key, value in current.items():
            if key in TTS_PROVIDER_SYSTEM_KEYS or key in field_map:
                continue
            field_map[key] = {"key": key, "type": _field_type(value, {}), "label": _tts_fallback_label(key),
                              "hint": _tts_fallback_hint(key), "secret": _tts_secret_field(key)}

        normalized = deepcopy(current)
        if isinstance(incoming, dict) and "enable" in incoming:
            normalized["enable"] = self._coerce_tts_value(field_map["enable"], incoming.get("enable"))
        values = incoming.get("values") if isinstance(incoming, dict) and isinstance(incoming.get("values"), dict) else {}
        for key, value in values.items():
            field = field_map.get(str(key))
            if field is None or (field.get("secret") and value in (None, "")):
                continue
            normalized[str(key)] = self._coerce_tts_value(field, value)
        if provider_type == "fishaudio_tts_api" and "model" in values:
            model = str(normalized.get("model") or "").strip().lower()
            old_model = str(current.get("model") or "").strip().lower()
            allowed = {item["value"] for item in FISH_AUDIO_MODEL_OPTIONS}
            if model not in allowed and model != old_model:
                raise TaskError("tts_provider_invalid", "Fish Audio 模型不在支持列表中")
            normalized["model"] = model
        normalized["id"] = _single_line(current.get("id"), 128)
        normalized["type"] = provider_type
        normalized["provider_type"] = "text_to_speech"
        return normalized

    async def tts_providers(self):
        try:
            payload = await self._run_authorized_read(lambda *_: self._tts_provider_payload())
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except Exception:
            return self._response(Outcome.failed("tts_provider_failed", "the TTS provider catalog could not be read"))

    async def tts_provider_create(self):
        try:
            data = _document(await request.body(), {"type", "id"}, limit=2048)
            provider_type = str(data["type"] or "").strip()[:80]
            provider_id = str(data["id"] or "").strip()[:80]
            if not PROVIDER_ID_PATTERN.fullmatch(provider_id):
                raise TaskError("tts_provider_invalid", "Provider ID only allows letters, digits, dot, underscore, colon and dash")
            async with self.controller._control_lock:
                self._authorized()
                manager = self._provider_manager()
                template = self._tts_templates().get(provider_type)
                if template is None:
                    raise TaskError("tts_provider_invalid", "unsupported TTS provider type")
                if provider_id in {str(item.get("id") or "") for item in getattr(manager, "providers_config", None) or []}:
                    raise TaskError("tts_provider_exists", "Provider ID already exists")
                config = {field["key"]: field["default"] for field in template["fields"]}
                config.update({"id": provider_id, "type": provider_type, "provider": template["provider"],
                               "provider_type": "text_to_speech", "enable": False})
                creator = getattr(manager, "create_provider", None)
                if not callable(creator):
                    raise TaskError("tts_provider_unsupported", "dynamic provider creation is unavailable")
                await creator(config)
                payload = self._tts_provider_payload()
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except Exception:
            return self._response(Outcome.failed("tts_provider_failed", "the TTS provider could not be created"))

    async def tts_provider_update(self):
        try:
            data = _document(await request.body(), {"provider_id", "values"}, limit=32768)
            provider_id = str(data["provider_id"] or "").strip()[:128]
            values = data["values"]
            if type(values) is not dict:
                raise ValueError("empty provider update")
            async with self.controller._control_lock:
                self._authorized()
                manager = self._provider_manager()
                current = self._tts_provider_by_id(manager, provider_id)
                normalized = self._normalized_tts_provider_update(current, {"values": values}, self._tts_schema_bundle())
                # The V2 page keeps enable inside values; accept both shapes so
                # V1-compatible clients can post it at the top level too.
                if "enable" in values:
                    normalized["enable"] = self._coerce_tts_value(
                        {"key": "enable", "type": "bool", "label": "启用"}, values["enable"])
                if normalized != current:
                    updater = getattr(manager, "update_provider", None)
                    if not callable(updater):
                        raise TaskError("tts_provider_unsupported", "dynamic provider updates are unavailable")
                    await updater(provider_id, normalized)
                payload = self._tts_provider_payload()
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("model_request_invalid", "invalid provider update"))
        except Exception:
            return self._response(Outcome.failed("tts_provider_failed", "the TTS provider could not be saved"))

    async def tts_provider_clone(self):
        try:
            data = _document_optional(await request.body(), {"provider_id", "language"}, {"config"}, limit=32768)
            provider_id = str(data["provider_id"] or "").strip()[:128]
            language = str(data["language"] or "").strip()
            if language not in {"zh", "ja", "en"}:
                raise ValueError("invalid clone language")
            async with self.controller._control_lock:
                self._authorized()
                manager = self._provider_manager()
                source = self._tts_provider_by_id(manager, provider_id)
                ids = {str(item.get("id") or "") for item in getattr(manager, "providers_config", None) or []}
                base = re.sub(r"[^A-Za-z0-9._:-]+", "-", provider_id).strip("._:-") or "tts"
                existing_lower = {item.lower() for item in ids}
                for index in range(1, 1000):
                    suffix = f"-{language}" if index == 1 else f"-{language}-{index}"
                    candidate = f"{base[:80 - len(suffix)]}{suffix}"
                    if candidate.lower() not in existing_lower:
                        break
                else:
                    raise TaskError("tts_provider_invalid", "无法生成不重复的语种专用 Provider ID")
                incoming = data.get("config") if isinstance(data.get("config"), dict) else {}
                config = self._normalized_tts_provider_update(source, incoming, self._tts_schema_bundle()) if incoming else deepcopy(source)
                config["id"] = candidate
                if "enable" not in incoming:
                    config["enable"] = False
                creator = getattr(manager, "create_provider", None)
                if not callable(creator):
                    raise TaskError("tts_provider_unsupported", "dynamic provider creation is unavailable")
                await creator(config)
                payload = {**self._tts_provider_payload(), "created_id": candidate,
                           "provider_id": candidate, "source_provider_id": provider_id, "language": language}
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("model_request_invalid", "invalid provider clone"))
        except Exception:
            return self._response(Outcome.failed("tts_provider_failed", "the TTS provider could not be cloned"))

    async def tts_provider_test(self):
        """Run the native Provider self-test without exposing its credentials."""
        try:
            data = _document(await request.body(), {"provider_id"}, limit=2048)
            provider_id = _single_line(data["provider_id"], 128)
            if not provider_id:
                raise ValueError("invalid provider id")
            started = monotonic()
            async with self.controller._control_lock:
                self._authorized()
                manager = self._provider_manager()
                current = self._tts_provider_by_id(manager, provider_id)
                instance = (getattr(manager, "inst_map", {}) or {}).get(provider_id)
                if instance is None:
                    return self._response(Outcome.success({
                        "provider_id": provider_id, "status": "unavailable", "code": "tts_provider_unloaded",
                        "error": "Provider 尚未启用或加载失败，请先保存并启用。",
                        "suggestion": "确认连接参数和凭据后启用 Provider；若已启用，请刷新宿主状态。",
                        "steps": [{"name": "配置检查", "status": "ok", "detail": "已找到 Provider 配置"},
                                  {"name": "实例加载", "status": "error", "detail": "当前 Provider 没有已加载实例"}],
                        "elapsed_ms": round((monotonic() - started) * 1000),
                    }))
                tester = getattr(instance, "test", None)
                if not callable(tester):
                    return self._response(Outcome.success({
                        "provider_id": provider_id, "status": "unsupported", "code": "tts_provider_test_unsupported",
                        "error": "该 Provider 没有提供连接自检入口。",
                        "suggestion": "可以直接使用“测试合成”验证实际语音路径。",
                        "steps": [{"name": "配置检查", "status": "ok", "detail": "已找到并加载 Provider"},
                                  {"name": "Provider 自检", "status": "error", "detail": "未实现 test()"}],
                        "elapsed_ms": round((monotonic() - started) * 1000),
                    }))
            try:
                await asyncio.wait_for(tester(), timeout=90.0)
            except asyncio.TimeoutError:
                status, code, error, suggestion = "unknown", "tts_provider_test_timeout", "Provider 自检超过 90 秒仍未完成。", "检查网络、代理和上游服务状态后重试。"
            except Exception as exc:
                # Provider exceptions can contain request URLs or credentials;
                # keep the response actionable without echoing raw secrets.
                detail = str(exc)[:360]
                for key, value in current.items():
                    if _tts_secret_field(key) and isinstance(value, str) and value:
                        detail = detail.replace(value, "<已隐藏>")
                status, code, error, suggestion = "unknown", "tts_provider_test_failed", f"{exc.__class__.__name__}: {detail}", "核对 API 地址、密钥、代理和模型参数，再查看 AstrBot 后端日志。"
            else:
                status, code, error, suggestion = "ok", "", "", ""
            async with self.controller._control_lock:
                self._authorized()
                if (getattr(manager, "inst_map", {}) or {}).get(provider_id) is not instance:
                    raise TaskError("tts_provider_changed", "Provider 在测试期间发生变化，请刷新后重试")
            result = {"provider_id": provider_id, "status": status, "code": code,
                      "elapsed_ms": round((monotonic() - started) * 1000), "error": error,
                      "suggestion": suggestion,
                      "steps": [{"name": "配置检查", "status": "ok", "detail": "已找到并加载 Provider"},
                                {"name": "Provider 自检", "status": "ok" if status == "ok" else "error",
                                 "detail": "test() 调用成功完成" if status == "ok" else error}]}
            return self._response(Outcome.success(result))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return self._response(Outcome.failed("model_request_invalid", "invalid TTS provider test request"))

    # ---------- image extension route configuration (owned by the extension) ----------
    def _image_extension_api(self):
        registry = self.controller.native_extensions
        api = registry.image_extension_api() if registry is not None else None
        if api is None:
            raise TaskError("model_image_output_unavailable", "the image extension is not loaded")
        return api

    def _image_host_registered(self):
        registry = self.controller.native_extensions
        session = self.controller.session or self.controller.last_session
        if registry is None or session is None:
            return False
        declarations = [item for item in registry.declarations()
                        if item.descriptor.package.extension_id == "image"]
        application = getattr(session, "application", None)
        external = getattr(application, "external", None)
        if not declarations or external is None:
            return False

        readiness = getattr(registry, "declaration_readiness", None)
        current_declaration = getattr(registry, "current_declaration", None)
        registered_actions = 0
        for item in declarations:
            action = item.descriptor.tool.action
            if action not in IMAGE_ACTIONS:
                continue
            state = readiness(item.host_tool_id) if callable(readiness) else "ready"
            if state in {"disabled", "unsupported"}:
                continue
            if state != "ready":
                return False
            if callable(current_declaration) and current_declaration(item.host_tool_id) != item:
                return False
            provider = external.providers.get(action)
            if provider is None or provider.descriptor != item.descriptor:
                return False
            registered_actions += 1
        return registered_actions > 0

    async def image_output(self):
        try:
            def load_output(*_args):
                api = self._image_extension_api()
                reader = getattr(api, "output_configuration", None)
                if not callable(reader):
                    raise TaskError("model_image_output_unavailable", "update the image extension to enable the V2 editor")
                result = reader()
                result["host_registered"] = self._image_host_registered()
                return result

            result = await self._run_authorized_read(load_output)
            return self._response(Outcome.success(result))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except Exception:
            return self._response(Outcome.failed("model_image_output_failed", "image backend settings could not be read"))

    async def image_output_reconnect(self):
        try:
            async with self.controller._control_lock:
                self._authorized()
            result = await self.controller.reconnect_native()
            if result.failure is not None:
                return self._response(result)
            data = result.unwrap()
            async with self.controller._control_lock:
                self._authorized()
                data["host_registered"] = self._image_host_registered()
            return self._response(Outcome.success(data))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except Exception:
            return self._response(Outcome.failed("staging_native_reconnect_failed", "the V2 session could not reconnect"))

    async def image_review_providers(self):
        try:
            def load_providers(session, *_args):
                return [{"id": identifier, "name": identifier}
                        for identifier, provider in session.application.router.providers.items()
                        if "image" in provider.capabilities]

            providers = await self._run_authorized_read(load_providers)
            return self._response(Outcome.success({"providers": providers}))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except Exception:
            return self._response(Outcome.failed("model_catalog_unavailable", "image review model catalog is unavailable"))

    async def image_observability(self):
        try:
            def load_observability(session, _operator, _manager, _status, observed_at):
                context = session.profile.target
                images = session.application.messages.generated_images.observability_page(
                    context, observed_at)
                content = {"available": False, "works_count": None,
                           "candidate_count": None, "updated_at": None}
                registry = self.controller.native_extensions
                consumer = getattr(registry, "content", None) if registry is not None else None
                if consumer is not None and consumer.application is session.application:
                    try:
                        content = consumer.dashboard_summary(context, observed_at)
                    except TaskError:
                        pass
                return {"images": images, "content": content}

            payload = await self._run_authorized_read(load_observability)
            return self._response(Outcome.success(payload))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except Exception:
            return self._response(Outcome.failed("model_image_observability_unavailable", "output diagnostics are unavailable"))

    async def image_output_save(self):
        try:
            data = _document(await request.body(), {"revision", "changes"}, limit=1048576)
            if (type(data["revision"]) is not str or len(data["revision"]) != 64
                    or type(data["changes"]) is not dict or not data["changes"]):
                raise ValueError("invalid image backend changes")
            async with self.controller._control_lock:
                self._authorized()
                api = self._image_extension_api()
                writer = getattr(api, "save_output_configuration", None)
                if not callable(writer):
                    raise TaskError("model_image_output_unavailable", "update the image extension")
                try:
                    result = writer(data["revision"], data["changes"])
                    result["host_registered"] = self._image_host_registered()
                except Exception as error:
                    code = getattr(error, "code", "")
                    if code == "native_endpoint_unconfigured":
                        raise TaskError(
                            "model_image_output_unconfigured",
                            "no enabled image backend can run the requested operations",
                        ) from error
                    if code in {"configuration_store_unavailable", "configuration_save_failed"}:
                        raise TaskError(
                            "model_image_output_store_failed",
                            "image backend settings could not be written to the data directory",
                        ) from error
                    if code not in {"configuration_conflict", "configuration_busy", "invalid_configuration"}:
                        logger.exception("image output configuration save failed (code=%s)", code or "unexpected")
                    suffix = "conflict" if code == "configuration_conflict" else (
                        "busy" if code == "configuration_busy" else "invalid" if code == "invalid_configuration" else "failed")
                    raise TaskError("model_image_output_" + suffix, "image backend settings were not saved") from error
            return self._response(Outcome.success(result))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError):
            return self._response(Outcome.failed("model_request_invalid", "invalid image backend request"))

    async def image_output_test(self):
        try:
            data = _document(await request.body(), {"endpoint_id", "operation_key"}, limit=4096)
            if any(type(data[key]) is not str or not data[key] or len(data[key]) > 128 for key in data):
                raise ValueError("invalid endpoint test")
            async with self.controller._control_lock:
                self._authorized()
                api = self._image_extension_api()
                tester = getattr(api, "test_output_endpoint", None)
                if not callable(tester):
                    raise TaskError("model_image_output_unavailable", "update the image extension")

            def authorize():
                self._authorized()
                if api is not self._image_extension_api():
                    raise TaskError("native_provider_changed", "the image provider changed during testing")

            result = await tester(data["endpoint_id"], data["operation_key"], before_dispatch=authorize)
            return self._response(Outcome.success(result))
        except TaskError as error:
            return self._response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError):
            return self._response(Outcome.failed("model_request_invalid", "invalid endpoint test request"))
        except Exception:
            return self._response(Outcome.failed("model_image_output_failed", "the endpoint test did not complete"))
