# -*- coding: utf-8 -*-
"""sensitive_reply 域。

由 tools/split_main_domain_v2.py 从 main.py 机械抽取（1 个方法 / 95 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

from .helpers import _single_line
from .main_shared import _multi_persona_event_context
from .model_routing import contains_sensitive_refusal, scope_allows
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.provider.entities import LLMResponse
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginSensitiveReplyMixin:
    """sensitive_reply 域（从 PrivateCompanionPlugin 拆出）。"""

    @filter.on_llm_response(priority=120000)
    @_multi_persona_event_context
    async def replace_sensitive_conversation_response(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
        *args,
        **kwargs,
    ):
        """Retry common model refusals on the configured conversation Provider."""
        if self is None or not bool(getattr(self, "enabled", False)):
            return
        if not bool(getattr(self, "enable_sensitive_model_replacement", False)):
            return
        if not scope_allows(getattr(self, "model_replacement_scope", "plugin"), "conversation"):
            return
        if bool(getattr(event, "private_companion_sensitive_model_retry", False)):
            return
        text = str(getattr(resp, "completion_text", "") or "").strip()
        keyword = contains_sensitive_refusal(text, getattr(self, "sensitive_replacement_keywords", ""))
        if not keyword:
            return
        target_provider = _single_line(getattr(self, "sensitive_replacement_provider_id", ""), 160)
        if not target_provider:
            return
        current_provider = self._provider_id_from_llm_response(resp)
        if target_provider == current_provider:
            return
        getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
        if not callable(getter):
            return
        try:
            if getter(target_provider) is None:
                logger.warning("敏感拒答替换模型不存在：%s", target_provider)
                return
        except Exception:
            return
        request = self._model_replacement_event_extra(event, "provider_request", None)
        if request is None:
            request = getattr(event, "private_companion_model_replacement_request", None)
        try:
            setattr(event, "private_companion_sensitive_model_retry", True)
        except Exception:
            pass
        try:
            retry_kwargs: dict[str, Any] = {
                "chat_provider_id": target_provider,
                "prompt": getattr(request, "prompt", None) if request is not None else getattr(event, "message_str", ""),
                "contexts": getattr(request, "contexts", None) if request is not None else None,
                "system_prompt": getattr(request, "system_prompt", None) if request is not None else None,
                "image_urls": list(getattr(request, "image_urls", None) or []) if request is not None else None,
                "audio_urls": list(getattr(request, "audio_urls", None) or []) if request is not None else None,
            }
            fallback = await self.context.llm_generate(**retry_kwargs)
            fallback_text = str(getattr(fallback, "completion_text", "") or "").strip()
            fallback_keyword = contains_sensitive_refusal(
                fallback_text,
                getattr(self, "sensitive_replacement_keywords", ""),
            )
            if not fallback_text or fallback_keyword:
                try:
                    resp.result_chain = None
                    resp.completion_text = ""
                except Exception:
                    pass
                logger.warning(
                    "敏感拒答替换模型仍拒答，已阻断原回复: original=%s target=%s keyword=%s",
                    _single_line(current_provider, 120) or "unknown",
                    target_provider,
                    _single_line(fallback_keyword or keyword, 80),
                )
                return
            resp.completion_text = fallback_text
            resp.result_chain = getattr(fallback, "result_chain", None)
            try:
                resp.role = getattr(fallback, "role", None) or resp.role
            except Exception:
                pass
            logger.info(
                "检测到模型敏感拒答，已改用指定对话模型: original=%s target=%s keyword=%s",
                _single_line(current_provider, 120) or "unknown",
                target_provider,
                _single_line(keyword, 80),
            )
        except Exception as exc:
            logger.warning(
                "敏感拒答替换模型调用失败，已阻断原回复: target=%s error=%s",
                target_provider,
                _single_line(exc, 180),
            )
            try:
                resp.result_chain = None
                resp.completion_text = ""
            except Exception:
                pass
