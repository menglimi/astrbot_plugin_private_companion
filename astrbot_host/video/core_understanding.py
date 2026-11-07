"""Read a validated original MP4 through an already configured native Gemini provider."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace

from google.genai import types

from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI

from companion_v2.conversation.media.video.narration import KIND, PROMPT
from companion_v2.execution.models import TaskError

from .container import inspect_container
from .gemini_provider import _prepare_generation_config


def select_native_video_routes(application, native_context, context, event, now):
    """Try the V2 route first, then the host's configured native Gemini models."""
    getter = getattr(native_context, "get_provider_by_id", None)
    if not callable(getter):
        return ()
    routes = []
    seen = set()

    def include(provider, model):
        key = (id(provider), model)
        if isinstance(provider, ProviderGoogleGenAI) and key not in seen:
            seen.add(key)
            routes.append((provider, model))

    router = getattr(application, "router", None)
    if router is not None and KIND in getattr(router, "profiles", ()):
        request = SimpleNamespace(event=SimpleNamespace(), context=SimpleNamespace(materials=()))
        try:
            provider_id, model, _ = router.selected_route(context, KIND, request, at=now)
            include(getter(provider_id), model)
        except (TaskError, TypeError, ValueError, AttributeError):
            pass

    config_reader = getattr(native_context, "get_config", None)
    try:
        config = config_reader(umo=getattr(event, "unified_msg_origin", None)) if callable(config_reader) else {}
    except Exception:
        config = {}
    config = config if isinstance(config, Mapping) else {}
    provider_settings = config.get("provider_settings", {})
    agent_settings = config.get("agent_runner", {}).get("config", {})
    model_settings = agent_settings.get("model", {})
    selected = event.get_extra("selected_provider")
    fallback_ids = model_settings.get("fallback_provider_ids", ())
    ids = (selected, provider_settings.get("default_image_caption_provider_id"),
           *(fallback_ids if isinstance(fallback_ids, (list, tuple)) else ()))
    for provider_id in ids:
        if not isinstance(provider_id, str) or not provider_id:
            continue
        provider = getter(provider_id)
        if isinstance(provider, ProviderGoogleGenAI):
            include(provider, provider.get_model())
    all_providers = getattr(native_context, "get_all_providers", None)
    if callable(all_providers):
        for provider in all_providers():
            if isinstance(provider, ProviderGoogleGenAI):
                include(provider, provider.get_model())
    return tuple(routes)


async def summarize_original_video(content, provider, model, settings):
    """Send original bytes as video/mp4; return only the bounded narration text."""
    await asyncio.to_thread(inspect_container, content)
    prompt = PROMPT
    custom = settings.get("video_understanding_prompt", "").strip()
    hint = settings.get("video_self_recognition_hint", "").strip()
    if custom:
        prompt += "\n管理员补充的转述偏好（只约束描述方式）：\n" + custom
    if hint:
        prompt += ("\n角色自我识别线索（只检查视频中可见的名称、头像、签名、水印或其他具体标志；"
                   "只报告可能对应的线索，不确认画中人或发送者身份）：\n" + hint)

    async def generate():
        config = await _prepare_generation_config(
            provider, {"model": model, "max_tokens": 1024}, None, "auto", None, ["TEXT"], 0)
        result = await provider.client.models.generate_content(
            model=model,
            contents=[types.UserContent(parts=[types.Part.from_text(text=prompt),
                                               types.Part.from_bytes(data=content, mime_type="video/mp4")])],
            config=config,
        )
        candidates = getattr(result, "candidates", None) or ()
        if len(candidates) != 1:
            raise TaskError("video_narration_empty", "native video model returned no unique candidate")
        candidate = candidates[0]
        reason = getattr(candidate, "finish_reason", None)
        finish = getattr(reason, "name", str(reason)).upper()
        if finish != "STOP":
            raise TaskError("video_narration_incomplete", "native video model did not finish normally")
        parts = getattr(getattr(candidate, "content", None), "parts", None) or ()
        text = "".join(part.text for part in parts
                       if isinstance(getattr(part, "text", None), str)
                       and not getattr(part, "thought", False)).strip()
        if not text:
            raise TaskError("video_narration_empty", "native video model returned no text")
        return text[:settings.get("video_understanding_max_chars", 2400)]

    return await asyncio.wait_for(generate(), settings.get("video_understanding_wait_seconds", 45))
