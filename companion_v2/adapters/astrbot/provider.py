"""One-attempt text transport for the verified AstrBot OpenAI-compatible class.

No host import or registration occurs here. This adapter bypasses text_chat's
outer ten-attempt loop and disables the SDK retry layer on a private client view.
The V2 gateway alone owns tool execution, fallback and durable accounting.
"""

from copy import copy, deepcopy
from dataclasses import dataclass, replace
from logging import getLogger
import re
from types import SimpleNamespace
from uuid import uuid4

from companion_v2.execution.models import TaskError, digest, encode
from companion_v2.model_gateway.cache_diagnostics import signature_for_payload
from companion_v2.model_gateway.failures import ModelFailure
from companion_v2.model_gateway.models import Amount, CapturedModelOutput, LocalProvider, ProviderResponse
from companion_v2.model_gateway.response_diagnostics import ResponseDiagnostics, ResponseTextSummary

from .model_wire import build_wire, decode_response
from .native import check_version, native_id
from .memory_output import capture, explicit_refusal, response_format, responses_text_format
from .streaming import StreamTranscript
from .response_diagnostics import observe_completion
from .provider_transport import definitely_not_sent, rejected_before_generation


# Reasoning models count hidden thinking tokens against the same completion budget.
# A tiny probe cap (previously 8) can return only reasoning and no visible text.
PROBE_OUTPUT_TOKENS = 256


RESERVED_EXTRA = frozenset({"model", "messages", "tools", "functions", "function_call", "tool_choice",
    "stream", "stream_options", "max_tokens", "max_completion_tokens", "max_output_tokens", "n",
    "response_format", "extra_body"})
logger = getLogger(__name__)
_MEDIA_KEYS = frozenset(("url", "image_url", "data", "data_base64", "video_data", "input_audio"))
_SECRET_KEYS = frozenset(("api_key", "access_token", "authorization", "password", "secret", "token"))


def _developer_content(value):
    """Keep text in DEBUG traces while replacing large binary media payloads."""
    if isinstance(value, (list, tuple)):
        return [_developer_content(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        key_name = key.casefold() if isinstance(key, str) else ""
        if key_name in _SECRET_KEYS or key_name.endswith(("_api_key", "_access_token", "_secret", "_password")):
            result[key] = "<secret omitted>"
        elif key in _MEDIA_KEYS and isinstance(item, str) and len(item) > 256:
            result[key] = f"<binary omitted chars={len(item)}>"
        else:
            result[key] = _developer_content(item)
    return result


def _developer_payload(payload):
    """Render the actual provider request without flooding logs with base64 media."""
    if not isinstance(payload, dict):
        return payload
    return _developer_content(payload)


def _gemini_audio_part(item):
    """把 OpenAI 形状的音频条目换成 AstrBot 的 Gemini 转换认得的那一种。

    AstrBot 的 payload 循环对 user 消息里非 ``text`` / ``image_url`` 的条目一律当作
    音频，直接读 ``item["audio_url"]["url"]``；把 ``input_audio`` 递过去就是一个
    KeyError，整轮调用失败。Gemini 的音频只接受 data URL，所以这里就地产出那种形状。
    """

    if not isinstance(item, dict) or item.get("type") != "input_audio":
        return item
    audio = item.get("input_audio")
    if not isinstance(audio, dict):
        return item
    data = audio.get("data")
    if not isinstance(data, str) or not data:
        return item
    # 宿主从 URL 的 mime 段读格式，任何分隔符都会把解析切错。
    fmt = "".join(char for char in str(audio.get("format") or "wav").lower() if char.isalnum()) or "wav"
    return {"type": "audio_url", "audio_url": {"url": "data:audio/" + fmt + ";base64," + data}}


def _gemini_content(content):
    if not isinstance(content, list):
        return content
    converted, changed = [], False
    for item in content:
        part = _gemini_audio_part(item)
        changed = changed or part is not item
        converted.append(part)
    return converted if changed else content


def _gemini_messages(messages):
    """AstrBot 的 Gemini 转换用 ``message["name"]`` 回填 function_response。

    OpenAI 的工具结果只带 ``tool_call_id``，所以这里按同一次对话里的
    tool_calls 把函数名补回去；音频条目同时换成宿主认得的形状。没有可补的内容时
    原样返回。
    """
    names = {}
    for message in messages:
        for call in message.get("tool_calls") or ():
            function = call.get("function") or {}
            if isinstance(call.get("id"), str) and isinstance(function.get("name"), str):
                names[call["id"]] = function["name"]
    enriched, changed = [], False
    for message in messages:
        current = message
        if message.get("role") == "tool" and message.get("tool_call_id") in names:
            current = {**current, "name": names[message["tool_call_id"]]}
        content = _gemini_content(current.get("content"))
        if content is not current.get("content"):
            current = {**current, "content": content}
        changed = changed or current is not message
        enriched.append(current)
    return enriched if changed else messages


def _chat_image_urls(messages):
    """Collect image URLs from OpenAI-shaped chat messages before host conversion."""
    urls = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        for part in content if isinstance(content, list) else ():
            if not isinstance(part, dict) or part.get("type") not in ("image_url", "input_image"):
                continue
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if isinstance(url, str) and url:
                urls.append(url)
    return tuple(urls)


def _responses_image_urls(items):
    """Collect image URLs from a prepared Responses ``input`` array."""
    urls = []
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        for part in content if isinstance(content, list) else ():
            if isinstance(part, dict) and part.get("type") == "input_image":
                url = part.get("image_url")
                if isinstance(url, str) and url:
                    urls.append(url)
    return tuple(urls)


def _unsupported_media(messages):
    """Return the media kinds the Responses converter would drop or degrade."""
    found = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        for part in content if isinstance(content, list) else ():
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("input_audio", "audio_url"):
                found.add("audio")
            elif part.get("type") == "video_data":
                found.add("video")
    return tuple(sorted(found))


def _responses_failure(error):
    """Classify the host Responses parser's fixed exceptions without importing AstrBot.

    ``ProviderOpenAIResponses._parse_response`` raises ``EmptyModelOutputError``
    for unusable output and ``RuntimeError`` for a provider-reported failure or a
    content-filter rejection. These are response-stage failures, never transport
    failures; a content filter is a refusal with its own fallback semantics.
    """
    if "EmptyModelOutputError" in {item.__name__ for item in type(error).__mro__}:
        return ModelFailure.NATIVE_PARSE
    if type(error).__name__ == "RuntimeError":
        message = str(error)
        if message.startswith("Responses API output was rejected by the provider content filter"):
            return ModelFailure.REFUSAL
        if message.startswith("Responses API request failed"):
            return ModelFailure.NATIVE_PARSE
    return None


def _developer_completion(completion):
    """Return the response body in a log-friendly shape before host parsing."""
    if completion is None:
        return None
    if isinstance(completion, (dict, list, tuple)):
        return _developer_content(completion)
    for method in ("model_dump", "to_dict"):
        convert = getattr(completion, method, None)
        if callable(convert):
            try:
                dumped = convert()
                if isinstance(dumped, (dict, list, tuple)):
                    return _developer_content(dumped)
            except Exception:
                pass

    def read(value, name, default=None):
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    # AstrBot's native transport returns LLMResponse rather than the raw
    # OpenAI ``choices`` object.  Keep its plain completion text and tool
    # fields explicit so the trace remains useful after host parsing.
    if hasattr(completion, "completion_text"):
        raw = getattr(completion, "raw_completion", None)
        raw_dump = None
        raw_choice = None
        raw_choices = read(raw, "choices", ())
        if isinstance(raw_choices, (list, tuple)) and raw_choices:
            raw_choice = raw_choices[0]
        raw_message = read(raw_choice, "message")
        if raw is not None:
            for method in ("model_dump", "to_dict"):
                convert = getattr(raw, method, None)
                if callable(convert):
                    try:
                        raw_dump = _developer_content(convert())
                        break
                    except Exception:
                        pass
            if raw_dump is None:
                raw_dump = _developer_completion(raw) if raw is not completion else None
        return {"role": getattr(completion, "role", None),
                "completion_text": _developer_content(getattr(completion, "completion_text", None)),
                "reasoning_content": _developer_content(getattr(completion, "reasoning_content", None)),
                "tools_call_name": _developer_content(getattr(completion, "tools_call_name", None)),
                "tools_call_ids": _developer_content(getattr(completion, "tools_call_ids", None)),
                "tools_call_args": _developer_content(getattr(completion, "tools_call_args", None)),
                "raw_content": _developer_content(read(raw_message, "content")),
                "raw_finish_reason": read(raw_choice, "finish_reason"),
                "raw_completion": raw_dump,
                "usage": repr(getattr(completion, "usage", None)),
                "raw_type": type(completion).__name__}

    choices = getattr(completion, "choices", ())
    choice = choices[0] if isinstance(choices, (list, tuple)) and choices else None
    message = read(choice, "message")
    return {"finish_reason": read(choice, "finish_reason"),
            "message": {"role": read(message, "role"),
                        "content": _developer_content(read(message, "content")),
                        "tool_calls": _developer_content(read(message, "tool_calls"))},
            "usage": _developer_content(read(completion, "usage")),
            "raw_type": type(completion).__name__}


class _DeveloperRequestTrace:
    """Observe SDK parameters after all native request transformations."""

    def __init__(self, client, provider_id, event_id):
        self.client, self.provider_id, self.event_id = client, provider_id, event_id

    def _log(self, kwargs):
        logger.debug("[CompanionV2][开发者追踪] 最终模型请求 event_id=%s provider=%s payload=%s",
                     self.event_id, self.provider_id, _developer_payload(kwargs))

    async def create(self, **kwargs):
        self._log(kwargs)
        return await self.client.chat.completions.create(**kwargs)

    async def create_response(self, **kwargs):
        self._log(kwargs)
        return await self.client.responses.create(**kwargs)

    def client_view(self):
        trace = self

        class ClientView:
            chat = SimpleNamespace(completions=SimpleNamespace(create=trace.create))
            responses = SimpleNamespace(create=trace.create_response)

            def __getattr__(self, name):
                return getattr(trace.client, name)

        return ClientView()


def provider_fingerprint(native):
    """Read-only identity of the selected configuration, without returning secrets."""
    return digest([native.provider_config, native.get_model(),
                   sorted(getattr(native, "default_params", {}))])


@dataclass(frozen=True)
class UsageTariff:
    """Explicit rated cost in microUSD per million tokens; not a billing receipt."""
    input: int
    cached_input: int
    output: int

    def __post_init__(self):
        if any(type(v) is not int or not 0 <= v <= 10**12 for v in (self.input, self.cached_input, self.output)):
            raise ValueError("tariff requires nonnegative integer microUSD rates")

    def amount(self, other, cached, output, *, input_tokens=None, cached_input_tokens=None,
               cache_creation_tokens=None):
        cost = (other * self.input + cached * self.cached_input + output * self.output + 999999) // 1000000
        return Amount(other + cached + output, cost, input_tokens=input_tokens,
                      cached_input_tokens=cached_input_tokens, cache_creation_tokens=cache_creation_tokens,
                      output_tokens=output if input_tokens is not None else None)


def looks_like_text_provider(native):
    """AstrBot 文本 provider 的公共合同：_query(payloads, tools) + get_model + provider_config。

    原生 Google GenAI、Anthropic 等 provider 与 OpenAI 共享这套表面；是否走
    OpenAI 专用钩子由适配器按能力判断，而不是按类名白名单。
    """
    return (callable(getattr(native, "_query", None))
            and callable(getattr(native, "get_model", None))
            and isinstance(getattr(native, "provider_config", None), dict))


def _same_config_value(left, right):
    """比较一份配置值，且不把值本身写进错误或日志。"""
    try:
        return digest(left) == digest(right)
    except (TypeError, ValueError):
        return left == right


#: AstrBot 会把 0（自动）的上下文长度在运行时补成具体数值。
#:
#: 装配时捕获到的 ``max_context_tokens`` 可能还是 0；普通聊天之后宿主把它写成
#: 供应商实际的窗口大小。这不是“模型配置被替换”，只是自动补全，不能据此拦住
#: 主动联系等后台任务。超出这个集合的字段仍按原规则比较。
AUTO_FILLED_CONFIG_KEYS = frozenset({"max_context_tokens"})


def _is_auto_value(value):
    return value in (0, None, "", "auto")


def _is_concrete_limit(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _auto_filled_equivalent(key, expected, actual):
    """同一个字段的“自动（0）”与“已补全的具体值”视为等价。"""
    return (key in AUTO_FILLED_CONFIG_KEYS
            and ((_is_auto_value(expected) and _is_concrete_limit(actual))
                 or (_is_concrete_limit(expected) and _is_auto_value(actual))))


def provider_class_matches(provider, expected_types):
    """宿主刷新注册表时会重新导入适配器模块，类对象随之更换。

    模块与限定名都相同的类仍视为同一个宿主类；一旦换到别的类（例如同一
    ID 下替换了 Provider 类型）则拒绝。
    """
    if provider is None:
        return False
    if isinstance(provider, expected_types):
        return True
    actual = type(provider)
    return any(actual.__module__ == expected.__module__
               and actual.__qualname__ == expected.__qualname__
               for expected in expected_types)


def provider_config_drift(expected, provider, auto_resolved=None):
    """列出被宿主改动或删除的已捕获配置键，绝不返回值本身。

    宿主刷新注册表时会重新合并 Provider 配置，并可能补写 ``modalities``
    这类派生字段。只比较装配时捕获的键：新增字段不构成替换，改动或删除
    已捕获的字段才是。``auto_resolved`` 记录宿主把“自动（0）”补成具体值
    之后的结果；同一个字段的自动值与补全值互为等价，不算替换。
    """
    config = getattr(provider, "provider_config", None)
    if not isinstance(config, dict):
        return ("provider_config",)
    snapshot = {**expected, **(auto_resolved or {})}
    drifted = []
    for key, value in snapshot.items():
        if key not in config:
            drifted.append(key)
        elif (not _same_config_value(config[key], value)
              and not _auto_filled_equivalent(key, value, config[key])):
            drifted.append(key)
    return tuple(sorted(drifted))


#: 主回复任务的模型能力要求：只需要文本。
#:
#: “能不能用工具”是**每一轮**的运行时决定（``model_gateway/service.py`` 在真的要
#: 提供工具目录时单独检查），不是整轮任务的能力合同。把它写进任务 profile 会让
#: AstrBot 里完全合法的“视觉模型但没勾工具使用”“纯文本模型”直接变成
#: “这个模型不能回复”。
REPLY_TASK_CAPABILITIES = ("text",)


def host_capabilities(provider_config):
    """由 AstrBot Provider 的 ``modalities`` 推出 V2 的本地能力集。

    宿主契约（4.28.1 与 4.29.1 逐行一致，见 ``astr_main_agent`` /
    ``tool_loop_agent_runner`` / ``utils/migra_helper``）：

    * 文本是本底能力。宿主全仓没有 ``"text" in modalities`` 这类判定，
      ``text`` 只是面板上的惰性复选框，因此这里恒定带上 ``"text"``；
    * ``[]`` 是宿主迁移无条件写入的“未配置、不限制”，键缺失同义，两者都给全能力；
    * 只有 ``tool_use`` / ``image`` / ``audio`` 是 ``modalities`` 授予的可选能力；
    * 其它值（手改配置、宿主未来新增模态）一律忽略：宁可诚实少一项可选能力，
      也不构造空集合让 ``LocalProvider`` 在装配期抛 ``ValueError``。
    """
    declared = provider_config.get("modalities")
    if not isinstance(declared, list) or not declared:
        return ("text", "tools", "image", "audio")
    declared = {value for value in declared if isinstance(value, str)}
    return ("text",) + tuple(capability for modality, capability in (
        ("tool_use", "tools"), ("image", "image"), ("audio", "audio")) if modality in declared)


class AstrBotTextProvider:
    def __init__(self, context, *, bridge, binding, provider_id, version, provider_type,
                 function_tool_type, tool_set_type, tariff: UsageTariff, maximum_output_tokens=4096,
                 maximum_input_estimate=131072, output_parameter="max_tokens", streaming=False):
        check_version(version)
        if output_parameter not in ("max_tokens", "max_completion_tokens"):
            raise ValueError("output token parameter must be selected explicitly")
        if type(maximum_input_estimate) is not int or not 1024 <= maximum_input_estimate <= 1000000:
            raise ValueError("input estimate ceiling is outside this adapter profile")
        classes = provider_type if isinstance(provider_type, tuple) else (provider_type,)
        if not classes or any(not isinstance(item, type) for item in classes):
            raise ValueError("provider needs the verified native provider class")
        self.context, self.bridge, self.binding, self.provider_id = context, bridge, binding, provider_id
        self.native = context.get_provider_by_id(provider_id)
        self.verified_type = isinstance(self.native, classes)
        if not self.verified_type and not looks_like_text_provider(self.native):
            raise ValueError("provider needs the verified native provider class")
        self.provider_types, self.provider_type = classes, classes[0]
        native_config = getattr(self.native, "provider_config", {})
        native_kind = str(native_config.get("type") or "") if isinstance(native_config, dict) else ""
        client = getattr(self.native, "client", None)
        responses_create = getattr(getattr(client, "responses", None), "create", None)
        responses_hooks = (native_kind == "openai_responses"
                           and callable(getattr(self.native, "_parse_response", None))
                           and callable(responses_create))
        if native_kind == "openai_responses" and not responses_hooks:
            raise ValueError("openai_responses provider lacks the verified Responses API surface")
        openai_hooks = (callable(getattr(self.native, "_prepare_chat_payload", None))
                        and callable(getattr(self.native, "_parse_openai_completion", None))
                        and callable(getattr(client, "with_options", None)))
        # Responses 与 Chat Completions 共用多数钩子，必须按宿主注册类型和真实
        # Responses 表面先分流；其余才按 OpenAI 专用钩子判断。
        self.kind = ("openai_responses" if responses_hooks else
                     "openai" if openai_hooks
                     else "gemini" if native_kind.startswith("googlegenai") else "native")
        self.config = deepcopy(self.native.provider_config)
        self.config_hash = digest(self.config)
        # 自动补全后的上下文长度快照：只用于后续漂移比较，不写回宿主配置。
        self._auto_resolved = {}
        self.model = self.native.get_model()
        self.client = self.native.client
        self.parameters = frozenset(getattr(self.native, "default_params", {}))
        self.function_tool_type, self.tool_set_type = function_tool_type, tool_set_type
        self.tariff, self.output_parameter = tariff, output_parameter
        if type(streaming) is not bool:
            raise ValueError("streaming must be an explicit boolean")
        # 只有 Chat Completions 的 chunk 协议与 StreamTranscript 兼容。Responses
        # 的 _query_stream 产出 LLMResponse 事件，Gemini 同样是另一套协议：没有
        # 完整验证过的 transcript 前一律降级为一次性返回，不能假装支持流式。
        self.streaming_supported = self.kind == "openai"
        if streaming and not self.streaming_supported:
            streaming = False
        if streaming and not callable(getattr(self.native, "_query_stream", None)):
            raise ValueError("the verified native provider has no streaming transport")
        self.streaming = streaming
        self.maximum_output_tokens, self.maximum_input_estimate = maximum_output_tokens, maximum_input_estimate
        extra = self.config.get("custom_extra_body", {})
        if not isinstance(extra, dict) or RESERVED_EXTRA & extra.keys():
            raise ValueError("custom provider parameters cannot override V2 routing, tools or output limits")
        # Validate the public limits without making a request.
        self.local_provider()

    def local_provider(self):
        capabilities = host_capabilities(self.config)
        transport = ("astrbot_openai_responses" if self.kind == "openai_responses" else
                     "astrbot_openai" if self.kind == "openai" else
                     "astrbot_gemini" if self.kind == "gemini" else "astrbot_native")
        return LocalProvider(self.binding, capabilities, self.maximum_output_tokens,
                             self.reserve, self.generate, transport=transport, default_streaming=self.streaming,
                             supports_model_override=True, model=self.model)

    def _same_provider_kind(self, provider):
        """未登记或重新导入的宿主类，至少要是同一种 Provider 类型。"""
        expected = str(self.config.get("type") or "")
        actual = str(getattr(provider, "provider_config", {}).get("type") or "")
        return bool(expected) and actual == expected

    def _current(self):
        current = self.context.get_provider_by_id(self.provider_id)
        if current is None:
            raise TaskError("host_provider_changed", "original host provider is unavailable")
        if not provider_class_matches(current, self.provider_types) and not (
                looks_like_text_provider(current) and self._same_provider_kind(current)):
            raise TaskError("host_provider_changed", "original host provider is unavailable")
        # AstrBot may rebuild provider instances when its provider registry is
        # refreshed. That is not a provider change if the model, configuration
        # and accepted parameters are still the same. Rebind the live instance
        # instead of failing every background task admitted before the refresh.
        drifted = provider_config_drift(self.config, current, self._auto_resolved)
        parameters = frozenset(getattr(current, "default_params", {}) or {})
        if current.get_model() != self.model or drifted or parameters != self.parameters:
            if drifted:
                logger.warning("[CompanionV2] 宿主 Provider 配置已变化 provider=%s keys=%s",
                               self.provider_id, ",".join(drifted))
            detail = "original host provider/configuration was replaced"
            if drifted:
                detail += " (changed_keys: " + ", ".join(drifted) + ")"
            raise TaskError("host_provider_changed", detail)
        self.native, self.client = current, current.client
        # 宿主把“自动”补成具体值之后，把具体值记进快照；以后再变才继续判为漂移。
        for key in AUTO_FILLED_CONFIG_KEYS & set(self.config):
            resolved = current.provider_config.get(key)
            if _is_concrete_limit(resolved) and _is_auto_value(self.config[key]):
                self._auto_resolved[key] = resolved

    def _estimate(self, envelope, wire):
        estimated = wire.input_estimate()
        structured = response_format(envelope.original)
        if structured is not None:
            estimated += len(encode(structured).encode("utf-8"))
        # 输入越界与输出预算越界是两种不同的处置动作（调输入上限 / 调输出上限），
        # 因此分成两个 code；reason 只带配置数值，不带提示词正文。
        if estimated > self.maximum_input_estimate:
            raise TaskError("host_model_limit_input",
                            f"input limit exceeded: estimated={estimated} maximum={self.maximum_input_estimate}")
        if (type(envelope.max_output_tokens) is not int
                or not 1 <= envelope.max_output_tokens <= self.maximum_output_tokens):
            raise TaskError("host_model_limit_output",
                            f"output limit exceeded: budget={envelope.max_output_tokens} "
                            f"allowed=1..{self.maximum_output_tokens}")
        return estimated

    def reserve(self, envelope):
        estimate = self._estimate(envelope, build_wire(envelope, model_name=envelope.model_override or self.model))
        # No assumed cache hit; reserve using the larger of the two input rates.
        rate = max(self.tariff.input, self.tariff.cached_input)
        return UsageTariff(rate, rate, self.tariff.output).amount(estimate, 0, envelope.max_output_tokens)

    def _usage(self, raw):
        try:
            def read(value, name):
                return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

            usage = read(raw, "usage")
            if hasattr(usage, "input_other"):
                # AstrBot 的原生 Gemini 用量是 TokenUsage，而不是 OpenAI 的 usage dict。
                other, cached, output = usage.input_other, usage.input_cached, usage.output
                if any(type(v) is not int or v < 0 for v in (other, cached, output)):
                    return None
                return self.tariff.amount(other, cached, output,
                                          input_tokens=other + cached, cached_input_tokens=cached)
            prompt = read(usage, "prompt_tokens")
            completion = read(usage, "completion_tokens")
            total = read(usage, "total_tokens")
            if total is None and type(prompt) is int and type(completion) is int:
                total = prompt + completion
            values = (prompt, completion, total)
            if any(type(v) is not int or not 0 <= v <= 10**12 for v in values) or prompt + completion != total:
                return None
            details = read(usage, "prompt_tokens_details") or read(usage, "input_tokens_details")
            cached = read(details, "cached_tokens") if details else None
            if cached is None:
                cached = read(usage, "cache_read_input_tokens")
            if cached is None:
                cached = read(usage, "prompt_cache_hit_tokens")
            cache_creation = read(usage, "cache_creation_input_tokens")
            if cache_creation is None:
                cache_creation = read(usage, "cache_creation_tokens")
            if cache_creation is None and details:
                cache_creation = read(details, "cache_creation_tokens")
            if cache_creation is None:
                creation = read(usage, "cache_creation")
                if creation:
                    creation_values = [read(creation, name) for name in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")]
                    if all(value is None or type(value) is int for value in creation_values):
                        cache_creation = sum(value or 0 for value in creation_values)
            # Absent cache detail is rated conservatively at the input rate.
            cache_read = 0 if cached is None else cached
            if (type(cache_read) is not int or not 0 <= cache_read <= prompt
                    or cache_creation is not None and (type(cache_creation) is not int
                                                       or not 0 <= cache_creation <= prompt
                                                       or cache_read + cache_creation > prompt)):
                return None
            return self.tariff.amount(prompt - cache_read, cache_read, completion, input_tokens=prompt,
                                      cached_input_tokens=cache_read if cached is not None else None,
                                      cache_creation_tokens=cache_creation)
        except (AttributeError, TypeError, ValueError):
            return None

    def _receipt(self, raw, fallback):
        try:
            identifier = native_id(raw.id)
            if len(identifier) <= 256:
                return "astrbot-response:" + digest(self.binding)[:24] + ":" + identifier
        except (AttributeError, TypeError, ValueError):
            pass
        return fallback

    async def probe(self):
        """Send one fixed, context-free request through the pinned native transport."""
        self._current()
        native = copy(self.native)
        native.provider_config = deepcopy(self.config)
        if self.kind == "openai_responses":
            native.client = self.client.with_options(max_retries=0)
            if native.client.max_retries != 0:
                raise ValueError("native probe retries could not be disabled")
            # Responses 的合法请求只有 input；messages / max_tokens 会进入
            # extra_body 并被服务端拒绝。这里直接复用宿主的真实转换器。
            payload, _ = await native._prepare_chat_payload(
                prompt="Reply OK.", contexts=[], model=self.model)
            payload["max_output_tokens"] = PROBE_OUTPUT_TOKENS
            result = await native._query(payload, None, request_max_retries=1)
        elif self.kind != "openai":
            # Gemini 客户端没有 OpenAI 的 with_options；重试次数由 request_max_retries 控制。
            payload = {"model": self.model, "messages": [{"role": "user", "content": "Reply OK."}],
                       "max_tokens": PROBE_OUTPUT_TOKENS}
            result = await native._query(payload, None, request_max_retries=1)
        else:
            native.client = self.client.with_options(max_retries=0)
            if native.client.max_retries != 0:
                raise ValueError("native probe retries could not be disabled")
            # AstrBot 的 `_query` 固定以 stream=False 调用原生 client；payload 里
            # 再带 stream 会触发 `multiple values for keyword argument 'stream'`。
            payload = {"model": self.model, "messages": [{"role": "user", "content": "Reply OK."}],
                       self.output_parameter: PROBE_OUTPUT_TOKENS}
            result = await native._query(payload, None, request_max_retries=1)
        self._current()
        if result is None:
            raise ValueError("native probe returned no result")

    async def generate(self, envelope):
        receipt = "astrbot-model-" + uuid4().hex
        observed = []
        parse_failed = False
        verified_refusal = False
        transcript = None
        response = None
        diagnostics = ResponseDiagnostics()
        try:
            wire = build_wire(envelope, model_name=envelope.model_override or self.model)
            logger.debug("[CompanionV2][开发者追踪] 模型提示词组装 provider=%s output_format=%s messages=%s tools=%s",
                         self.provider_id, wire.output_format, _developer_content(wire.messages),
                         _developer_content(wire.tools))
            self._estimate(envelope, wire)
            await self.bridge.verify_role(envelope.original.event.scope, envelope.original.role_binding,
                                          envelope.original.host_role_instruction)
            self._current()
            native = copy(self.native)
            native.provider_config = deepcopy(self.config)
            if self.kind in ("openai", "openai_responses"):
                native.client = self.client.with_options(max_retries=0)
                if native.client.max_retries != 0:
                    raise ValueError("client retry layer could not be disabled")
                native.client = _DeveloperRequestTrace(native.client, self.provider_id,
                    getattr(envelope.original.event, "event_id",
                            getattr(envelope.original.event, "request_id", "unknown"))).client_view()
            streaming = self.streaming if envelope.original.streaming is None else envelope.original.streaming
            if self.kind != "openai":
                streaming = False
            if streaming and envelope.original.output_format == "conversation":
                if not callable(getattr(native, "_query_stream", None)):
                    raise ValueError("native streaming transport is unavailable")
                on_content = None
                if envelope.on_reply_prefix is not None and envelope.original.streaming_mode == "incremental":
                    from .reply_stream import OrderedReplyStream
                    reply_stream = OrderedReplyStream(wire)

                    async def on_content(fragment):
                        draft = reply_stream.feed(fragment)
                        if draft is not None and (envelope.reply_prefix_ready is None or envelope.reply_prefix_ready(draft)):
                            self._current()
                            await self.bridge.verify_role(envelope.original.event.scope, envelope.original.role_binding,
                                                          envelope.original.host_role_instruction)
                            await envelope.on_reply_prefix(draft)
                transcript = StreamTranscript(native.client, on_content)
            tools = self.tool_set_type(tools=[self.function_tool_type(**t, handler=None) for t in wire.tools]) if wire.tools else None

            async def parse(completion, offered):
                # Retain actual usage even if the host parser rejects an empty/filtered result.
                nonlocal parse_failed, verified_refusal, diagnostics
                observed.append(completion)
                logger.debug("[CompanionV2][开发者追踪] 原生解析输入 provider=%s response=%s",
                             self.provider_id, _developer_completion(completion))
                diagnostics = observe_completion(completion)
                if transcript is not None:
                    try:
                        transcript.verify(completion)
                    except Exception:
                        diagnostics = replace(diagnostics, parse_state="stream_rejected")
                        raise
                verified_refusal = explicit_refusal(completion) or bool(transcript and transcript.refusal)
                if wire.output_format == "memory_reflection":
                    # Memory owns parsing and a durable recovery queue. Keep the
                    # provider's original text and completion reason, even when
                    # a native text parser would reject an empty/refused reply.
                    return completion
                try:
                    parsed = await self.provider_type._parse_openai_completion(native, completion, offered)
                    diagnostics = replace(diagnostics, parse_state="native_parsed",
                        parsed=ResponseTextSummary.from_text(getattr(parsed, "completion_text", None)))
                    logger.debug("[CompanionV2][开发者追踪] 原生解析结果 provider=%s completion_text=%r diagnostics=%s",
                                 self.provider_id, getattr(parsed, "completion_text", None), diagnostics.to_record())
                    return parsed
                except Exception as error:
                    parse_failed = True
                    diagnostics = replace(diagnostics, parse_state="native_rejected")
                    logger.debug("[CompanionV2][开发者追踪] 原生解析失败 provider=%s error_type=%s",
                                 self.provider_id, type(error).__name__, exc_info=True)
                    raise
            if self.kind in ("openai", "openai_responses"):
                if self.kind == "openai":
                    native._parse_openai_completion = parse
                if envelope.original.images or envelope.original.audios or envelope.original.videos:
                    # V2 has already materialized and validated exact data URLs. The
                    # host's general image resolver may download, re-encode or log
                    # unresolved locations; none of those are needed for these bytes.
                    async def keep_original_images(contexts):
                        return deepcopy(contexts)
                    native._materialize_context_image_parts = keep_original_images
                payload, _ = await native._prepare_chat_payload(prompt=None, contexts=deepcopy(wire.messages),
                                                              model=envelope.model_override or self.model)
                if self.kind == "openai_responses":
                    lossy = _unsupported_media(wire.messages)
                    if lossy:
                        # 宿主的 Responses 转换器把音频变成 "[Audio]" 文本、直接丢弃
                        # 视频原始数据；宁可显式失败，也不能把附件静默当成文字。
                        raise TaskError("host_media_unsupported",
                                        "Responses provider cannot carry " + "/".join(lossy) + " input without data loss")
                    input_items = payload.get("input")
                    if not isinstance(input_items, list):
                        raise ValueError("native Responses preparation returned no input")
                    if _responses_image_urls(input_items) != _chat_image_urls(wire.messages):
                        raise ValueError("native preparation changed original images or their coverage and attribution")
                    payload["max_output_tokens"] = envelope.max_output_tokens
                else:
                    if envelope.original.images or envelope.original.audios or envelope.original.videos:
                        def image_materials(messages):
                            return [message for message in messages if isinstance(message.get("content"), list)]
                        if image_materials(payload["messages"]) != image_materials(wire.messages):
                            raise ValueError("native preparation changed original images or their coverage and attribution")
                    payload[self.output_parameter] = envelope.max_output_tokens
            else:
                # 原生 Gemini 接受 OpenAI 形状的 messages，由宿主转换为 generate_content；
                # 输出上限使用 Gemini 认得的 max_tokens。
                payload = {"model": envelope.model_override or self.model,
                           "messages": deepcopy(_gemini_messages(wire.messages)),
                           "max_tokens": envelope.max_output_tokens}
            if self.kind == "openai_responses":
                text_format = responses_text_format(envelope.original)
                if text_format is not None:
                    payload["text"] = text_format
            else:
                structured = response_format(envelope.original)
                if structured is not None:
                    payload["response_format"] = structured
            logger.debug("[CompanionV2][开发者追踪] 实际模型请求 provider=%s model=%s payload=%s",
                         self.provider_id, payload.get("model", self.model), _developer_payload(payload))
            self._current()
            if envelope.before_dispatch is None:
                raise ValueError("native model dispatch requires the live owner's fence")
            envelope.before_dispatch()
            if envelope.record_cache_signature is not None:
                try:
                    signature_messages = (payload.get("input", ()) if self.kind == "openai_responses"
                                          else payload.get("messages", ()))
                    envelope.record_cache_signature(signature_for_payload(
                        payload.get("model", envelope.model_override or self.model),
                        signature_messages, wire.tools))
                except Exception:
                    logger.debug("cache signature collection failed", exc_info=True)
        except Exception as error:
            task_error_code = error.code if isinstance(error, TaskError) else None
            if (not isinstance(task_error_code, str)
                    or re.fullmatch(r"[a-z0-9_.-]{1,80}", task_error_code) is None):
                task_error_code = None
            status_code = getattr(error, "status_code", None)
            if type(status_code) is not int or not 100 <= status_code <= 599:
                status_code = None
            error_type = type(error).__name__
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error_type) is None:
                error_type = None
            diagnostics = replace(diagnostics, failure=ModelFailure.PREPARATION,
                error_type=error_type, error_code=task_error_code, status_code=status_code)
            logger.warning("[CompanionV2] 模型请求准备失败 provider=%s failure=%s error_type=%s "
                           "task_error_code=%s status_code=%s",
                           self.provider_id, ModelFailure.PREPARATION.value,
                           error_type or "unavailable", task_error_code or "unavailable",
                           status_code if status_code is not None else "unavailable")
            return ProviderResponse("not_started", usage=Amount(0, 0), receipt_ref=receipt,
                                    failure=ModelFailure.PREPARATION,
                                    diagnostics=diagnostics)
        failure = ModelFailure.TRANSPORT
        try:
            response = (await transcript.query(native, payload, tools) if transcript is not None
                        else await native._query(payload, tools, request_max_retries=1))
            if self.kind != "openai":
                # Gemini 与 Responses 的 _query 都返回宿主归一化的 LLMResponse；
                # 没有 ChatCompletion 解析钩子，直接观察该结果。
                observed.append(response)
                diagnostics = observe_completion(response)
                verified_refusal = explicit_refusal(response)
            logger.debug("[CompanionV2][开发者追踪] 模型原始回复 provider=%s response=%s",
                         self.provider_id, _developer_completion(response))
            raw = transcript.usage_receipt if transcript is not None and transcript.usage_receipt else (observed[-1] if observed else None)
            usage = self._usage(raw)
            receipt = self._receipt(raw, receipt)
            failure = ModelFailure.PROVIDER_RECHECK
            self._current()
            failure = ModelFailure.ROLE_RECHECK
            await self.bridge.verify_role(envelope.original.event.scope, envelope.original.role_binding,
                                          envelope.original.host_role_instruction)
            failure = ModelFailure.WIRE_DECODE
            if wire.output_format == "memory_reflection":
                logger.debug("[CompanionV2][开发者追踪] 模型正文处理 provider=%s stage=captured body=%s",
                             self.provider_id, _developer_completion(observed[-1]))
                return ProviderResponse("generated", captured_output=capture(observed[-1]), usage=usage, receipt_ref=receipt,
                                        diagnostics=replace(diagnostics, parse_state="captured"))
            if verified_refusal:
                logger.debug("[CompanionV2][开发者追踪] 模型正文处理 provider=%s stage=refusal",
                             self.provider_id)
                return ProviderResponse("generated", captured_output=CapturedModelOutput("", "refusal"),
                                        usage=usage, receipt_ref=receipt, diagnostics=replace(diagnostics, parse_state="refusal"))
            # A background body is decoded by its own owner, so one the output
            # ceiling cut is handed up with ``length`` semantics instead of being
            # dropped as an unparsable answer — the same classification the private
            # memory owner receives. A reply is never captured: half a delivered
            # message is not a message, so it stays an incomplete result.
            if wire.output_format != "conversation" and observed:
                ceiling = capture(observed[-1])
                if ceiling.finish_reason == "length":
                    logger.debug("[CompanionV2][开发者追踪] 模型正文被输出上限截断 provider=%s output_format=%s",
                                 self.provider_id, wire.output_format)
                    return ProviderResponse("generated", captured_output=ceiling, usage=usage, receipt_ref=receipt,
                                            diagnostics=replace(diagnostics, parse_state="captured"))
            draft, calls = decode_response(response, wire)
            logger.debug("[CompanionV2][开发者追踪] 模型正文处理 provider=%s stage=decoded body=%r claims=%r tool_calls=%r",
                         self.provider_id, draft.text if draft is not None else None,
                         draft.claims if draft is not None else None, calls)
            return ProviderResponse("generated", draft=draft, tool_calls=calls, usage=usage, receipt_ref=receipt,
                diagnostics=replace(diagnostics, parse_state="tools" if calls else "validated",
                                    visible=ResponseTextSummary.from_text(draft.text) if draft is not None else None))
        except Exception as error:
            logger.debug("[CompanionV2][开发者追踪] 模型回复处理失败 provider=%s stage=%s error_type=%s",
                         self.provider_id, failure.value, type(error).__name__, exc_info=True)
            raw = transcript.usage_receipt if transcript is not None and transcript.usage_receipt else (observed[-1] if observed else None)
            native_failure = _responses_failure(error) if self.kind == "openai_responses" else None
            if native_failure is ModelFailure.REFUSAL:
                try:
                    self._current()
                    await self.bridge.verify_role(envelope.original.event.scope, envelope.original.role_binding,
                                                  envelope.original.host_role_instruction)
                except Exception:
                    failure = ModelFailure.ROLE_RECHECK
                    native_failure = None
                else:
                    return ProviderResponse("generated", captured_output=CapturedModelOutput("", "refusal"),
                                            usage=self._usage(raw), receipt_ref=self._receipt(raw, receipt),
                                            diagnostics=replace(diagnostics, finish_reason="content_filter",
                                                                parse_state="refusal"))
            if transcript is not None and transcript.ended and transcript.finish == "content_filter":
                try:
                    self._current()
                    await self.bridge.verify_role(envelope.original.event.scope, envelope.original.role_binding,
                                                  envelope.original.host_role_instruction)
                except Exception:
                    failure = ModelFailure.ROLE_RECHECK
                else:
                    return ProviderResponse("generated", captured_output=CapturedModelOutput("", "refusal"),
                                            usage=self._usage(raw), receipt_ref=self._receipt(raw, receipt),
                                            diagnostics=replace(diagnostics, parse_state="refusal"))
            if parse_failed and verified_refusal:
                try:
                    self._current()
                    await self.bridge.verify_role(envelope.original.event.scope, envelope.original.role_binding,
                                                  envelope.original.host_role_instruction)
                except Exception:
                    failure = ModelFailure.ROLE_RECHECK
                else:
                    return ProviderResponse("generated", captured_output=CapturedModelOutput("", "refusal"),
                                            usage=self._usage(raw), receipt_ref=self._receipt(raw, receipt),
                                            diagnostics=replace(diagnostics, parse_state="refusal"))
            final_failure = ModelFailure.NATIVE_PARSE if parse_failed else (native_failure or failure)
            if native_failure is not None:
                diagnostics = replace(diagnostics, parse_state="native_rejected")
            diagnostics = replace(diagnostics, failure=final_failure,
                parse_state="contract_rejected" if final_failure is ModelFailure.WIRE_DECODE else diagnostics.parse_state)
            task_error_code = error.code if isinstance(error, TaskError) else "unavailable"
            if (not isinstance(task_error_code, str)
                    or re.fullmatch(r"[a-z0-9_.-]{1,80}", task_error_code) is None):
                task_error_code = "unavailable"
            status_code = getattr(error, "status_code", None)
            if type(status_code) is not int or not 100 <= status_code <= 599:
                status_code = "unavailable"
            error_type = type(error).__name__
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error_type) is None:
                error_type = None
            diagnostics = replace(diagnostics, error_type=error_type,
                error_code=task_error_code if task_error_code != "unavailable" else None,
                status_code=status_code if type(status_code) is int else None)
            if (raw is None and transcript is None
                    and (definitely_not_sent(error) or rejected_before_generation(error))):
                logger.warning("[CompanionV2] 模型请求确认未生成，标记为未开始 provider=%s error_type=%s",
                               self.provider_id, error_type)
                return ProviderResponse("not_started", usage=Amount(0, 0), receipt_ref=receipt,
                                        failure=final_failure, diagnostics=diagnostics)
            logger.warning(
                "[CompanionV2] 模型适配器结果未知 provider=%s phase=%s failure=%s "
                "error_type=%s task_error_code=%s status_code=%s completion_present=%s "
                "finish_reason=%s parse_state=%s raw_chars=%s parsed_chars=%s",
                self.provider_id, final_failure.document()["phase"], final_failure.value,
                type(error).__name__, task_error_code, status_code, diagnostics.completion_present,
                diagnostics.finish_reason, diagnostics.parse_state,
                diagnostics.raw.chars if diagnostics.raw is not None else "unavailable",
                diagnostics.parsed.chars if diagnostics.parsed is not None else "unavailable")
            return ProviderResponse("unknown", usage=self._usage(raw), receipt_ref=self._receipt(raw, receipt),
                                    failure=final_failure, diagnostics=diagnostics)
