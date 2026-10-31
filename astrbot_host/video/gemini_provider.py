"""Google GenAI conversation adapter with captured MP4 bytes as native Parts."""

from __future__ import annotations

import base64
from collections import OrderedDict
from collections.abc import Mapping
from copy import deepcopy
from uuid import uuid4

from google.genai import types

from companion_v2.adapters.astrbot.model_wire import build_wire, decode_text_response, strict_json
from companion_v2.adapters.astrbot.provider import (UsageTariff, provider_class_matches,
                                                    provider_config_drift, provider_fingerprint)
from companion_v2.adapters.astrbot.provider_catalog import TextProviderSettings
from companion_v2.execution.models import ProviderBinding, TaskError, digest, encode
from companion_v2.model_gateway.models import Amount, LocalProvider, ProviderResponse, ToolCall


class GeminiNativeVideoProvider:
    def __init__(self, context, bridge, native_types, native, *, settings):
        if not isinstance(native, native_types.video_provider_type):
            raise ValueError("native video requires the verified Google GenAI provider type")
        self.context, self.bridge, self.native = context, bridge, native
        self.native_types = native_types
        if not isinstance(settings, TextProviderSettings):
            raise TypeError("native video route needs the loaded provider's checked limits and tariff")
        self.tariff = settings.tariff
        if not isinstance(self.tariff, UsageTariff):
            raise TypeError("native video route needs a checked usage tariff")
        self.maximum_input_estimate = settings.maximum_input_estimate
        self.maximum_output_tokens = settings.maximum_output_tokens
        self.provider_id = str(native.meta().id)
        self.model = native.get_model()
        self.version = native_types.version
        self.fingerprint = provider_fingerprint(native)
        # 装配时捕获的配置键：宿主刷新注册表时重新合并配置不算换了 Provider，
        # 改动或删除这些键才是。值本身只留在内存，不进错误与日志。
        self.config = deepcopy(native.provider_config)
        self.parameters = frozenset(getattr(native, "default_params", {}) or ())
        self.binding = ProviderBinding("model.native_video", self.provider_id, self.version,
                                       self.fingerprint)
        # Native continuation signatures stay inside this adapter. They never
        # become prompt material, tool arguments, persisted history or logs.
        self._continuation_signatures = OrderedDict()
        self._current()

    def _current(self):
        """跟随一次宿主注册表刷新，仍然对真实的 Provider 变化报错。

        AstrBot 刷新 Provider 注册表或重载引擎时会重建 Provider 实例（也可能
        重新导入适配器模块，类对象随之更换）。只要模型、已捕获的配置键和可用
        参数都没变，这就不是换了 Provider：适配器改挂到新实例上，并刷新自己
        的指纹与 binding，避免注册表刷新后每个后台任务都永久失配。

        真正变化仍然拒绝：能力类型、模型、已捕获的配置键或参数任一不同，都会
        抛出 ``host_provider_changed``，并在消息里带上诊断字段。整次重绑定在
        一个同步步骤内完成（中间没有 await），并发任务不会读到更新到一半的
        引用。
        """
        current = self.context.get_provider_by_id(self.provider_id)
        if current is None:
            raise TaskError("host_provider_changed",
                            "native video provider is unavailable (provider_id: "
                            + self.provider_id + ")")
        if not provider_class_matches(current, (self.native_types.video_provider_type,)):
            raise TaskError("host_provider_changed",
                            "native video provider type changed during the request")
        model = current.get_model()
        fingerprint = provider_fingerprint(current)
        if current is self.native and model == self.model and fingerprint == self.fingerprint:
            return
        drifted = provider_config_drift(self.config, current)
        parameters = frozenset(getattr(current, "default_params", {}) or ())
        if model != self.model or drifted or parameters != self.parameters:
            problems = []
            if model != self.model:
                problems.append("model_changed")
            if drifted:
                problems.append("changed_keys: " + ", ".join(drifted))
            if parameters != self.parameters:
                problems.append("parameters_changed")
            raise TaskError("host_provider_changed",
                            "native video provider was replaced (" + "; ".join(problems) + ")")
        self.native, self.fingerprint = current, fingerprint
        self.binding = ProviderBinding("model.native_video", self.provider_id, self.version, fingerprint)

    def local_provider(self):
        return LocalProvider(self.binding, ("text", "tools", "image", "video"), self.maximum_output_tokens,
            self.reserve, self.generate, transport="astrbot_gemini_native_video",
            supports_model_override=True, model=self.model)

    def reserve(self, envelope):
        if (envelope.original.native_video_input and not envelope.original.videos
                or envelope.original.audios):
            raise TaskError("model_capability_mismatch", "Gemini route received unsupported or incomplete media")
        estimated = build_wire(envelope, model_name=envelope.model_override or self.model).input_estimate()
        if estimated > self.maximum_input_estimate or envelope.max_output_tokens > self.maximum_output_tokens:
            raise TaskError("host_model_limit", "native video request exceeds its declared input or output profile")
        return self.tariff.amount(estimated, 0, envelope.max_output_tokens)

    async def probe(self):
        """Use the dashboard's context-free probe through this native transport."""
        self._current()
        payloads = {"model": self.model, "max_tokens": min(self.maximum_output_tokens, 64)}
        config = await self.native._prepare_query_config(payloads, None, "auto", None, ["TEXT"], 0)
        self._current()
        result = await self.native.client.models.generate_content(
            model=self.model,
            contents=[types.UserContent(parts=[types.Part.from_text(text="Reply OK.")])],
            config=config,
        )
        self._current()
        if not getattr(result, "candidates", None):
            raise TaskError("model_response_invalid", "Gemini probe returned no candidate")

    @staticmethod
    def _parts(content):
        result = []
        videos = 0
        for item in content:
            if item.get("type") == "text":
                result.append(types.Part.from_text(text=item.get("text") or " "))
            elif item.get("type") == "video_data":
                payload = item["video_data"]
                result.append(types.Part.from_bytes(
                    data=base64.b64decode(payload["data"], validate=True),
                    mime_type=payload["mime_type"],
                ))
                videos += 1
            elif item.get("type") == "image_url":
                url = item["image_url"].get("url", "")
                header, separator, data = url.partition(",")
                if not separator or not header.startswith("data:image/") or ";base64" not in header:
                    raise TaskError("model_wire_invalid", "native image input must be a checked data URL")
                mime_type = header[5:].split(";", 1)[0]
                result.append(types.Part.from_bytes(data=base64.b64decode(data, validate=True),
                                                    mime_type=mime_type))
            else:
                raise TaskError("model_wire_invalid", "Gemini route received unsupported media")
        if videos > 2:
            raise TaskError("model_wire_invalid", "native video request exceeds its two-video limit")
        return result

    @classmethod
    def _contents(cls, messages, wire, *, owner=None, signatures=None):
        contents = []
        tool_names = {}
        for message in messages:
            role, content = message["role"], message.get("content")
            if role == "system":
                continue
            if role == "user":
                items = content if isinstance(content, list) else [{"type": "text", "text": content or " "}]
                contents.append(types.UserContent(parts=cls._parts(items)))
            elif role == "assistant" and message.get("tool_calls"):
                parts = []
                for call in message["tool_calls"]:
                    function = call.get("function", {})
                    name = function.get("name", "")
                    arguments = strict_json(function.get("arguments", "{}"))
                    if name not in wire.aliases or not isinstance(arguments, dict):
                        raise TaskError("model_wire_invalid", "Gemini tool history differs from the declared catalog")
                    call_id = call.get("id", "")
                    tool_names[call_id] = name
                    part = types.Part.from_function_call(name=name, args=arguments)
                    captured = (signatures or {}).get(call_id)
                    if (captured is not None and captured[0] == owner
                            and captured[1] == digest([name, arguments])):
                        part.thought_signature = captured[2]
                    parts.append(part)
                contents.append(types.Content(role="model", parts=parts))
            elif role == "assistant":
                if content:
                    contents.append(types.Content(role="model", parts=cls._parts([
                        {"type": "text", "text": content}])))
            elif role == "tool":
                name = tool_names.get(message.get("tool_call_id", ""))
                if not name:
                    raise TaskError("model_tool_results_missing", "Gemini tool result has no matching declared call")
                response = strict_json(message.get("content", "{}"))
                contents.append(types.UserContent(parts=[types.Part.from_function_response(
                    name=name, response={"name": name, "content": encode(response)})]))
            else:
                raise TaskError("model_wire_invalid", "Gemini conversation contains an unsupported role")
        if not contents or not isinstance(contents[-1], types.UserContent):
            raise TaskError("model_wire_invalid", "Gemini request must end with a user or tool result")
        return contents

    def _continuation_owner(self, envelope):
        return digest([self.binding, envelope.original.event, envelope.original.role_binding,
                       envelope.original.role_instruction, envelope.task_prompt,
                       envelope.configuration_token, envelope.model_override])

    def _release_continuations(self, owner):
        for identifier, captured in tuple(self._continuation_signatures.items()):
            if captured[0] == owner:
                del self._continuation_signatures[identifier]

    async def generate(self, envelope):
        self._current()
        request = envelope.original
        if (request.native_video_input and not request.videos or len(request.videos) > 2
                or request.audios or envelope.tool_history and not envelope.tools):
            raise TaskError("model_capability_mismatch", "Gemini route received unsupported or incomplete media history")
        wire = build_wire(envelope, model_name=envelope.model_override or self.model)
        self.reserve(envelope)
        # Background tasks may replace the model-facing role with the self page.
        # The native guard must still compare the host persona captured behind
        # that substitution, otherwise Gemini rejects a legitimate task role.
        await self.bridge.verify_role(request.event.scope, request.role_binding, request.host_role_instruction)
        self._current()
        system_instruction = "\n".join(message["content"] for message in wire.messages
                                        if message["role"] == "system")
        user = next((message for message in wire.messages if message["role"] == "user"), None)
        if user is None:
            raise TaskError("model_wire_invalid", "Gemini request has no bounded user content")
        owner = self._continuation_owner(envelope)
        contents = self._contents(wire.messages, wire, owner=owner,
                                  signatures=self._continuation_signatures)
        temperature = 0.7
        payloads = {"model": envelope.model_override or self.model,
                    "max_tokens": envelope.max_output_tokens}
        tools = (self.native_types.tool_set(tools=[self.native_types.function_tool(**item, handler=None)
                  for item in wire.tools]) if wire.tools else None)
        config = await self.native._prepare_query_config(
            payloads, tools, "auto", system_instruction, ["TEXT"], temperature)
        self._current()
        if envelope.before_dispatch is None:
            raise TaskError("model_request_fence_missing", "native video dispatch requires its live owner fence")
        envelope.before_dispatch()
        result = await self.native.client.models.generate_content(
            model=envelope.model_override or self.model,
            contents=contents,
            config=config,
        )
        self._current()
        candidates = getattr(result, "candidates", None) or ()
        if len(candidates) != 1:
            raise TaskError("model_response_invalid", "native video provider returned no unique candidate")
        candidate = candidates[0]
        finish = getattr(candidate, "finish_reason", None)
        finish = getattr(finish, "name", str(finish)).upper()
        if finish != "STOP":
            raise TaskError("model_response_incomplete", "native video response did not finish normally")
        response_content = getattr(candidate, "content", None)
        response_parts = getattr(response_content, "parts", None) or ()
        raw_text = "".join(part.text for part in response_parts
                           if isinstance(getattr(part, "text", None), str)
                           and not getattr(part, "thought", False))
        function_parts = [part for part in response_parts
                          if getattr(part, "function_call", None) is not None]
        if function_parts:
            if raw_text.strip() or not envelope.tools or len(function_parts) > 8:
                raise TaskError("model_response_invalid", "Gemini returned a mixed or unbounded tool response")
            calls = []
            response_id = getattr(result, "response_id", None)
            for index, part in enumerate(function_parts):
                function_call = part.function_call
                alias = getattr(function_call, "name", None)
                arguments = getattr(function_call, "args", None)
                if (alias not in wire.aliases or not isinstance(arguments, Mapping)
                        or len(encode(dict(arguments))) > 16384):
                    raise TaskError("model_response_invalid", "Gemini requested an undeclared or invalid tool")
                signature = getattr(part, "thought_signature", None)
                if signature is not None and (type(signature) is not bytes or len(signature) > 65536):
                    raise TaskError("model_response_invalid", "Gemini returned an invalid continuation signature")
                call_id = "gemini-tool:" + digest([owner, response_id or uuid4().hex, index])[:48]
                calls.append(ToolCall(call_id, wire.aliases[alias], encode(dict(arguments))))
                if signature:
                    self._continuation_signatures[call_id] = (owner, digest([alias, dict(arguments)]), signature)
                    while len(self._continuation_signatures) > 256:
                        self._continuation_signatures.popitem(last=False)
            usage = self._usage(result)
            receipt = "astrbot-gemini-video:" + digest([self.binding, response_id or uuid4().hex])[:48]
            return ProviderResponse("generated", tool_calls=tuple(calls), usage=usage,
                                    receipt_ref=receipt if usage is not None else None)
        draft, tools = decode_text_response(raw_text, wire)
        if draft is None or tools:
            raise TaskError("model_response_invalid", "native video response did not satisfy the reply contract")
        self._release_continuations(owner)
        usage = self._usage(result)
        response_id = getattr(result, "response_id", None)
        receipt = "astrbot-gemini-video:" + digest([self.binding, response_id or uuid4().hex])[:48]
        return ProviderResponse("generated", draft=draft, usage=usage,
                                receipt_ref=receipt if usage is not None else None)

    def _usage(self, result):
        metadata = getattr(result, "usage_metadata", None)
        if metadata is None:
            return None
        # A counter the upstream did not report arrives as an explicit ``None``:
        # the attribute exists, so ``getattr``'s default never applies. Relays
        # omit the cached count on ordinary calls, and that alone used to discard
        # the whole receipt. Read every counter the way the host's own
        # ``_extract_usage`` does, so a missing counter cannot cost the receipt.
        prompt_tokens = getattr(metadata, "prompt_token_count", None) or 0
        output_tokens = getattr(metadata, "candidates_token_count", None) or 0
        cached_tokens = getattr(metadata, "cached_content_token_count", None) or 0
        if (type(prompt_tokens) is not int or type(output_tokens) is not int
                or type(cached_tokens) is not int or not 0 <= cached_tokens <= prompt_tokens
                or not 0 <= prompt_tokens + output_tokens <= 10**12):
            return None
        return self.tariff.amount(prompt_tokens - cached_tokens, cached_tokens, output_tokens,
            input_tokens=prompt_tokens, cached_input_tokens=cached_tokens)
