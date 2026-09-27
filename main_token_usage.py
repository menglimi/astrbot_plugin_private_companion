# -*- coding: utf-8 -*-
"""token_usage 域。

由 tools/split_main_domain_v2.py 从 main.py 机械抽取（2 个方法 / 97 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import hashlib
import time
from .helpers import _safe_float, _safe_int, _single_line
from .main_shared import _multi_persona_event_context
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.provider.entities import LLMResponse

class PrivateCompanionPluginTokenUsageMixin:
    """token_usage 域（从 PrivateCompanionPlugin 拆出）。"""

    @filter.on_llm_response()
    @_multi_persona_event_context
    async def record_external_llm_token_usage(self, event: AstrMessageEvent, resp: LLMResponse, *args, **kwargs):
        """统计非插件内部调用的 AstrBot 主回复 Token，单独展示且不计入插件限额。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "llm_request"):
            return
        if bool(getattr(event, "private_companion_skip_external_token_stats", False)):
            return
        prompt = str(getattr(event, "private_companion_external_token_prompt", "") or "")
        started = _safe_float(getattr(event, "private_companion_external_token_start", 0), 0)
        completion = self._completion_text_for_token_stats(resp)
        response_tool_names = getattr(resp, "tools_call_name", None) if resp is not None else None
        if isinstance(response_tool_names, str):
            has_tool_call = bool(response_tool_names.strip())
        elif isinstance(response_tool_names, (list, tuple, set)):
            has_tool_call = any(str(item or "").strip() for item in response_tool_names)
        else:
            has_tool_call = False
        if not prompt and not completion and resp is None:
            return
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        resp_id = _single_line(getattr(resp, "id", ""), 120) if resp is not None else ""
        usage = getattr(resp, "usage", None) if resp is not None else None
        usage_total = _safe_int(getattr(usage, "total", 0), 0)
        trigger_message_id = self._event_message_id(event)
        completion_sig = hashlib.sha1(
            completion[:4000].encode("utf-8", errors="ignore")
        ).hexdigest()[:16] if completion else ""
        record_key = "|".join(
            (
                resp_id,
                umo,
                sender_id,
                trigger_message_id,
                str(usage_total),
                str(len(prompt)),
                str(len(completion)),
                completion_sig,
            )
        )
        try:
            recorded_keys = getattr(event, "private_companion_external_token_recorded_keys", None)
            if not isinstance(recorded_keys, set):
                recorded_keys = set()
                setattr(event, "private_companion_external_token_recorded_keys", recorded_keys)
            if record_key in recorded_keys:
                return
            recorded_keys.add(record_key)
        except Exception:
            pass
        try:
            is_private_chat = bool(getattr(event, "is_private_chat", lambda: False)())
            task = "astrbot_private_reply" if is_private_chat else "astrbot_group_reply"
        except Exception:
            is_private_chat = False
            task = "astrbot_reply"
        provider_id = self._provider_id_from_llm_response(resp) or self._default_chat_provider_id(umo)
        self._record_external_llm_usage(
            provider_id=provider_id,
            task=task,
            prompt=prompt,
            completion=completion,
            elapsed_ms=int(max(0.0, time.time() - started) * 1000) if started > 0 else 0,
            success=bool(completion or has_tool_call),
            error="" if completion or has_tool_call else "empty_response",
            resp=resp,
            session_id=umo,
            sender_id=sender_id,
            message_type="private" if is_private_chat else "group",
        )

    @filter.on_llm_response()
    @_multi_persona_event_context
    async def record_group_expression_rule_usage(self, event: AstrMessageEvent, resp: LLMResponse, *args, **kwargs):
        """记录群聊中实际进入主回复链的已审核语义表达规则。"""
        if self is None or not self.enabled or bool(getattr(event, "is_private_chat", lambda: False)()):
            return
        semantic_rules = getattr(event, "private_companion_semantic_expression_rules", None)
        if not isinstance(semantic_rules, list) or not semantic_rules:
            return
        if bool(getattr(event, "private_companion_group_semantic_usage_recorded", False)):
            return
        completion = _single_line(getattr(resp, "completion_text", ""), 500)
        if not completion:
            return
        # Rule usage is committed by the confirmed-delivery finalizer. Keep this
        # hook read-only so an LLM response that is later dropped cannot mutate
        # durable expression-learning state.
        try:
            setattr(event, "private_companion_group_semantic_usage_pending", True)
        except Exception:
            pass
