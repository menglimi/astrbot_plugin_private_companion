"""OpenAI-compatible visual video relay bypassing image-only text_chat sanitizers."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import re
import time
from typing import Any

from companion_v2.adapters.astrbot.provider_transport import extract_error_status_code
from companion_v2.execution.models import TaskError
from .limits import format_seconds

try:
    from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI
except ImportError:  # pragma: no cover - optional SDK in lightweight test harnesses
    ProviderGoogleGenAI = None  # type: ignore[assignment]

try:
    from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
except ImportError:  # pragma: no cover
    ProviderOpenAIOfficial = None  # type: ignore[assignment]

try:
    from astrbot.core.provider.sources.openai_responses_source import ProviderOpenAIResponses
except ImportError:  # pragma: no cover
    ProviderOpenAIResponses = None  # type: ignore[assignment]


_RESERVED_EXTRA_KEYS = frozenset(
    {"model", "messages", "input", "stream", "tools", "tool_choice", "max_tokens", "max_completion_tokens", "max_output_tokens"}
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}")
_KEY_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z\-_]{16,})\b")
_URL_RE = re.compile(r"https?://[^\s'\"<>]+")


@dataclass(frozen=True)
class RelayRouteInfo:
    protocol: str  # "openai_chat" | "openai_responses" | "gemini"
    endpoint_url: str
    model: str
    api_key: str
    timeout_s: float
    proxy: str | None
    custom_headers: dict[str, str]
    custom_extra_body: dict[str, Any]


def _provider_config(provider: Any) -> dict[str, Any]:
    config = getattr(provider, "provider_config", None)
    return config if isinstance(config, dict) else {}


def _provider_type(provider: Any) -> str:
    return str(_provider_config(provider).get("type") or "").strip().casefold()


def _normalized_modalities(provider: Any) -> set[str] | None:
    declared = _provider_config(provider).get("modalities")
    if not isinstance(declared, list) or not declared:
        return None
    cleaned = {item.strip().casefold() for item in declared if isinstance(item, str) and item.strip()}
    return cleaned or None


def is_gemini_provider(provider: Any) -> bool:
    """Return True if ``provider`` uses the native Google GenAI SDK/transport."""
    if ProviderGoogleGenAI is not None and isinstance(provider, ProviderGoogleGenAI):
        return True
    return _provider_type(provider).startswith("googlegenai")


def genai_lacks_video_modality(provider: Any) -> bool:
    """Detect GoogleGenAI gateways whose declared non-empty modalities omit video and audio."""
    if not is_gemini_provider(provider):
        return False
    modalities = _normalized_modalities(provider)
    if modalities is None:
        return False
    return "video" not in modalities and "audio" not in modalities


def _is_anthropic_like(provider: Any) -> bool:
    kind = _provider_type(provider)
    cls_hint = f"{type(provider).__module__}.{type(provider).__name__}".casefold()
    combined = f"{kind} {cls_hint}"
    return "anthropic" in combined or "claude" in combined


def is_openai_compat_video_provider(provider: Any) -> bool:
    """Return True if ``provider`` is an image-capable OpenAI-compatible chat or responses provider."""
    if provider is None or is_gemini_provider(provider) or _is_anthropic_like(provider):
        return False
    modalities = _normalized_modalities(provider)
    if modalities is not None and "image" not in modalities:
        return False
    kind = _provider_type(provider)
    if ProviderOpenAIResponses is not None and isinstance(provider, ProviderOpenAIResponses):
        return True
    if ProviderOpenAIOfficial is not None and isinstance(provider, ProviderOpenAIOfficial):
        return True
    if kind in ("openai_chat_completion", "openai_responses"):
        return True
    if kind and kind != "openai":
        return False
    client = getattr(provider, "client", None)
    has_chat = callable(getattr(getattr(getattr(client, "chat", None), "completions", None), "create", None))
    has_resp = callable(getattr(getattr(client, "responses", None), "create", None))
    if has_chat or has_resp:
        return True
    config = _provider_config(provider)
    return isinstance(config.get("api_base"), str) and bool(config.get("api_base", "").strip())


def extract_api_key(provider: Any) -> str:
    """Read active API credential cleanly from AstrBot provider or config."""
    getter = getattr(provider, "get_current_key", None)
    if callable(getter):
        try:
            key = getter()
            if isinstance(key, str) and key.strip():
                return key.strip()
        except Exception:
            pass
    config = _provider_config(provider)
    raw = config.get("key")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                return item.strip()
    elif isinstance(raw, str) and raw.strip():
        return raw.strip()
    client = getattr(provider, "client", None)
    client_key = getattr(client, "api_key", None)
    return client_key.strip() if isinstance(client_key, str) else ""


def extract_model_name(provider: Any, explicit_model: str = "") -> str:
    """Resolve effective model identifier for a provider."""
    if isinstance(explicit_model, str) and explicit_model.strip():
        return explicit_model.strip()
    getter = getattr(provider, "get_model", None)
    if callable(getter):
        try:
            name = getter()
            if isinstance(name, str) and name.strip():
                return name.strip()
        except Exception:
            pass
    config = _provider_config(provider)
    return str(config.get("model") or config.get("model_id") or getattr(provider, "model_name", "") or "").strip()


def extract_relay_route_info(provider: Any, model: str = "") -> RelayRouteInfo:
    """Inspect endpoint, protocol, credential and transport metadata cleanly."""
    config = _provider_config(provider)
    kind = _provider_type(provider)
    client = getattr(provider, "client", None)
    if is_gemini_provider(provider):
        protocol = "gemini"
    elif (
        kind == "openai_responses"
        or (ProviderOpenAIResponses is not None and isinstance(provider, ProviderOpenAIResponses))
        or (
            callable(getattr(getattr(client, "responses", None), "create", None))
            and not callable(getattr(getattr(getattr(client, "chat", None), "completions", None), "create", None))
        )
    ):
        protocol = "openai_responses"
    else:
        protocol = "openai_chat"

    base_url = getattr(client, "base_url", None)
    endpoint = str(base_url or config.get("api_base") or "").strip().rstrip("/")
    try:
        timeout_val = float(config.get("timeout") or 60.0)
        if timeout_val <= 0:
            timeout_val = 60.0
    except (TypeError, ValueError):
        timeout_val = 60.0
    proxy = config.get("proxy")
    proxy = proxy.strip() if isinstance(proxy, str) and proxy.strip() else None
    raw_headers = config.get("custom_headers")
    headers = (
        {str(k): str(v) for k, v in raw_headers.items() if isinstance(k, str) and isinstance(v, str)}
        if isinstance(raw_headers, dict)
        else {}
    )
    raw_extra = config.get("custom_extra_body")
    extra = (
        {k: v for k, v in raw_extra.items() if isinstance(k, str) and k not in _RESERVED_EXTRA_KEYS}
        if isinstance(raw_extra, dict)
        else {}
    )
    return RelayRouteInfo(
        protocol=protocol,
        endpoint_url=endpoint,
        model=extract_model_name(provider, model),
        api_key=extract_api_key(provider),
        timeout_s=timeout_val,
        proxy=proxy,
        custom_headers=headers,
        custom_extra_body=extra,
    )


def sanitize_video_error(error: BaseException, *, secrets: tuple[str, ...] = ()) -> str:
    """Return a concise error summary with keys, bearer tokens and URLs redacted."""
    text = f"{type(error).__name__}: {error}".strip()
    status = extract_error_status_code(error)
    if status is not None and str(status) not in text:
        text = f"HTTP {status} {text}"
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 4:
            text = text.replace(secret, "<已隐藏>")
    text = _BEARER_RE.sub("Bearer <已隐藏>", text)
    text = _KEY_RE.sub("<已隐藏>", text)
    text = _URL_RE.sub("<URL已隐藏>", text)
    text = " ".join(text.split())
    return text[:240]


def _read_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def extract_openai_video_text(raw: Any) -> str:
    """Extract visible text from Chat Completions or Responses payloads (dict or SDK objects)."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()

    # Responses API direct output_text
    direct = _read_attr_or_key(raw, "output_text", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    # Responses API output list
    output_items = _read_attr_or_key(raw, "output", None)
    if isinstance(output_items, (list, tuple)):
        chunks = []
        for item in output_items:
            item_type = str(_read_attr_or_key(item, "type", "") or "").casefold()
            if item_type in ("reasoning", "thought"):
                continue
            content = _read_attr_or_key(item, "content", None)
            if isinstance(content, str) and content.strip():
                chunks.append(content.strip())
            elif isinstance(content, (list, tuple)):
                for part in content:
                    part_type = str(_read_attr_or_key(part, "type", "text") or "").casefold()
                    if part_type in ("reasoning", "thought", "thinking"):
                        continue
                    part_text = _read_attr_or_key(part, "text", None)
                    if isinstance(part_text, str) and part_text.strip():
                        chunks.append(part_text.strip())
        if chunks:
            return "\n".join(chunks).strip()

    # Chat Completions choices
    choices = _read_attr_or_key(raw, "choices", None)
    if isinstance(choices, (list, tuple)) and choices:
        first = choices[0]
        message = _read_attr_or_key(first, "message", None)
        if message is not None:
            content = _read_attr_or_key(message, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, (list, tuple)):
                parts = []
                for part in content:
                    if _read_attr_or_key(part, "thought", False):
                        continue
                    part_type = str(_read_attr_or_key(part, "type", "text") or "").casefold()
                    if part_type in ("reasoning", "thought", "thinking"):
                        continue
                    part_text = _read_attr_or_key(part, "text", None)
                    if isinstance(part_text, str) and part_text.strip():
                        parts.append(part_text.strip())
                if parts:
                    return "\n".join(parts).strip()

    # Generic fallback (AstrBot LLMResponse, etc.)
    completion_text = _read_attr_or_key(raw, "completion_text", None)
    if isinstance(completion_text, str) and completion_text.strip():
        return completion_text.strip()
    return ""


def _output_token_field(model: str, protocol: str) -> str:
    if protocol == "openai_responses":
        return "max_output_tokens"
    lowered = str(model or "").casefold()
    if lowered.startswith(("gpt-6", "gpt-5", "o1", "o3", "o4")):
        return "max_completion_tokens"
    return "max_tokens"


def _encode_video_data_uri(content: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"


async def relay_video_with_openai_compat(
    provider: Any,
    model: str,
    content: bytes,
    *,
    mime_type: str = "video/mp4",
    prompt: str,
    wait_seconds: float = 45.0,
    max_tokens: int = 1024,
) -> str:
    """Relay a bounded ``data:video/...;base64,...`` request via an OpenAI-compat provider."""
    info = extract_relay_route_info(provider, model)
    if not info.model:
        raise TaskError("video_narration_unavailable", "OpenAI 兼容视觉模型未配置模型名。")
    budget = float(wait_seconds)
    if budget <= 0:
        raise TaskError("video_narration_timeout", "视觉中转视频理解等待预算已耗尽。")
    deadline = time.monotonic() + budget
    data_uri = await asyncio.to_thread(_encode_video_data_uri, content, mime_type)
    timeout_s = deadline - time.monotonic()
    if timeout_s <= 0:
        raise TaskError("video_narration_timeout", f"视觉中转视频理解超时（{format_seconds(budget)}）。")
    secrets = (info.api_key,) if info.api_key else ()
    client = getattr(provider, "client", None)

    try:
        if info.protocol == "openai_responses":
            input_payload = [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": data_uri},
                    ],
                }
            ]
            if client is not None and callable(getattr(getattr(client, "responses", None), "create", None)):
                target_client = (
                    client.with_options(max_retries=0)
                    if callable(getattr(client, "with_options", None))
                    else client
                )
                kwargs: dict[str, Any] = {
                    "model": info.model,
                    "input": input_payload,
                    "max_output_tokens": int(max_tokens),
                    "timeout": timeout_s,
                }
                if info.custom_extra_body:
                    kwargs["extra_body"] = dict(info.custom_extra_body)
                raw = await asyncio.wait_for(target_client.responses.create(**kwargs), timeout=timeout_s)
            else:
                raw = await _post_openai_json(
                    info,
                    "/responses",
                    {
                        "model": info.model,
                        "input": input_payload,
                        "max_output_tokens": int(max_tokens),
                        **info.custom_extra_body,
                    },
                    timeout_s,
                )
        else:
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ]
            token_key = _output_token_field(info.model, info.protocol)
            if client is not None and callable(
                getattr(getattr(getattr(client, "chat", None), "completions", None), "create", None)
            ):
                target_client = (
                    client.with_options(max_retries=0)
                    if callable(getattr(client, "with_options", None))
                    else client
                )
                kwargs = {
                    "model": info.model,
                    "messages": messages,
                    token_key: int(max_tokens),
                    "timeout": timeout_s,
                }
                if info.custom_extra_body:
                    kwargs["extra_body"] = dict(info.custom_extra_body)
                raw = await asyncio.wait_for(target_client.chat.completions.create(**kwargs), timeout=timeout_s)
            else:
                raw = await _post_openai_json(
                    info,
                    "/chat/completions",
                    {
                        "model": info.model,
                        "messages": messages,
                        token_key: int(max_tokens),
                        **info.custom_extra_body,
                    },
                    timeout_s,
                )
    except TaskError:
        raise
    except asyncio.TimeoutError as error:
        raise TaskError(
            "video_narration_timeout",
            f"视觉中转视频理解超时（{format_seconds(budget)}）。",
        ) from error
    except Exception as error:
        raise TaskError(
            "video_narration_failed",
            f"视觉中转视频理解失败（{sanitize_video_error(error, secrets=secrets)}）。",
        ) from error

    text = extract_openai_video_text(raw)
    if not text:
        raise TaskError("video_narration_empty", "视觉中转视频模型未返回有效描述。")
    return text


async def _post_openai_json(
    info: RelayRouteInfo,
    path: str,
    body: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    if not info.endpoint_url:
        raise TaskError("video_narration_unavailable", "OpenAI 兼容视觉模型缺少接口地址。")
    try:
        import httpx
    except ImportError as error:  # pragma: no cover
        raise TaskError("video_narration_unavailable", "缺少 httpx 客户端，无法调用视觉中转接口。") from error

    timeout_val = float(timeout_s)
    if timeout_val <= 0:
        raise asyncio.TimeoutError()

    url = f"{info.endpoint_url}{path}"
    headers = {"Content-Type": "application/json", **info.custom_headers}
    if info.api_key:
        headers["Authorization"] = f"Bearer {info.api_key}"
    client_kwargs: dict[str, Any] = {"timeout": timeout_val, "headers": headers}
    if info.proxy:
        client_kwargs["proxy"] = info.proxy

    async def _send_and_read() -> dict[str, Any]:
        async with httpx.AsyncClient(**client_kwargs) as http_client:
            response = await http_client.post(url, json=body)
            if response.status_code >= 400:
                snippet = response.text.strip()[:200] if isinstance(response.text, str) else ""
                safe_detail = sanitize_video_error(
                    RuntimeError(f"HTTP {response.status_code}: {snippet}"),
                    secrets=(info.api_key,) if info.api_key else (),
                )
                raise TaskError("video_narration_failed", f"视觉中转接口返回错误（{safe_detail}）。")
            data = response.json()
            if not isinstance(data, dict):
                raise TaskError("video_narration_empty", "视觉中转接口返回了非 JSON 对象响应。")
            return data

    return await asyncio.wait_for(_send_and_read(), timeout=timeout_val)
