# -*- coding: utf-8 -*-
"""UserMemoryInboundIntentPart04Mixin。

由 tools/split_mixin_domain.py 从 user_memory_inbound_intent.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 209 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryInboundIntentMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from .conversation_prompt_section import prompt_section
from .helpers import _now_ts, _safe_float, _single_line
from .persona_config import runtime_persona_setting
from .user_memory_render_shared import _render_user_memory_background_prompt, _render_user_memory_labeled_section, logger
from typing import Any



class UserMemoryInboundIntentPart04Mixin:
    """UserMemoryInboundIntentPart04Mixin（从 UserMemoryInboundIntentMixin 拆出）。"""


    async def _decide_smart_silence(
        self,
        *,
        inbound_text: str,
        response_text: str,
        user: dict[str, Any] | None = None,
        session_kind: str = "",
        recent_context: list[str] | None = None,
    ) -> dict[str, Any]:
        if not bool(runtime_persona_setting(self, "enable_smart_silence", True)):
            return {"decision": "send", "reason": "disabled", "confidence": 0.0, "source": "disabled"}
        inbound = _single_line(inbound_text, 320)
        response = _single_line(response_text, 600)
        trigger = self._smart_silence_contextual_trigger_reason(
            inbound,
            response,
            session_kind=session_kind,
        )
        if not trigger:
            return {"decision": "send", "reason": "no_boundary_trigger", "confidence": 0.0, "source": "prefilter"}
        if not response:
            return {"decision": "send", "reason": "empty_response", "confidence": 0.0, "source": "prefilter"}
        cache_key = hashlib.sha1(
            f"{session_kind}\n{inbound}\n{response[:240]}".encode("utf-8", errors="ignore")
        ).hexdigest()
        cache = getattr(self, "_smart_silence_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_smart_silence_cache", cache)
        now = _now_ts()
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and now - _safe_float(cached.get("ts"), 0) <= 120:
            result = dict(cached.get("result") or {})
            result["source"] = "cache"
            return result
        if len(cache) > 256:
            for key, item in list(cache.items())[:64]:
                if not isinstance(item, dict) or now - _safe_float(item.get("ts"), 0) > 120:
                    cache.pop(key, None)

        provider_id = self._task_provider(
            runtime_persona_setting(self, "smart_silence_provider_id", ""),
            runtime_persona_setting(self, "response_review_provider_id", ""),
            runtime_persona_setting(self, "smart_message_debounce_provider_id", ""),
            runtime_persona_setting(self, "mai_style_provider_id", ""),
            runtime_persona_setting(self, "llm_provider_id", ""),
        )
        if not provider_id:
            return {"decision": "send", "reason": "no_provider", "confidence": 0.0, "source": "prefilter"}

        last_companion = _single_line((user or {}).get("last_companion_message"), 260) if isinstance(user, dict) else ""
        recent_lines = []
        for item in (recent_context or [])[-6:]:
            line = _single_line(item, 120)
            if line:
                recent_lines.append(f"- {line}")
        recent_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.smart_silence.recent_context",
                title="最近上下文",
                source="user_memory",
                content="\n".join(recent_lines) or "（无）",
            )
        )
        last_bot_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.smart_silence.last_bot_message",
                title="Bot 上次发出的话",
                source="user_memory",
                content=last_companion or "（无）",
            )
        )
        inbound_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.smart_silence.inbound",
                title="用户刚才说",
                source="user_memory",
                content=inbound,
            )
        )
        response_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.smart_silence.response",
                title="待发送回复",
                source="user_memory",
                content=response,
            )
        )
        prompt = prompt_section(
            key="background.memory.smart_silence",
            title="智能沉默判定",
            source="user_memory",
            content=f"""
你是聊天回复发送前的智能沉默判定器。判断用户是否在表达“不要继续这个话题/不要再追问/先别回复/换掉当前话题”，或上下文已经明显适合安静收住，从而应该直接不发这条待发送回复。

只输出 JSON：{{"decision":"send|silent","confidence":0-1,"reason":"不超过20字"}}

判定原则：
- 用户明确说别聊、别问、别继续、到此为止、算了别说了、换个话题，且待发送回复仍在确认、安慰、解释、追问或继续这个话题，decision=silent。
- 当触发词是 short_disengage、soft_disengage、leaving_or_busy 或 group_reaction_not_request 时，要结合上下文判断：用户只是短促收尾、要离开、忙了、敷衍回应，且待发送回复还在追问、解释、建议、延长话题，才 silent。
- 如果用户同一句已经开启了新请求或新问题，例如“算了，帮我看这个”“换个话题，今天吃什么”，且待发送回复是在处理新请求，decision=send。
- 如果待发送回复只是“好，那不聊这个了”“嗯我闭嘴了”这类对边界的重复确认，通常 silent；真实聊天里安静退开更自然。
- 如果待发送回复是必要的信息回答、用户明确提问的答案、工具结果、约定确认或安全提醒，decision=send。
- 不要因为用户说“算了”两个字就一定沉默，要看它是不是结束当前话题，而不是普通口头禅。
- 不确定时 send。

会话类型：{_single_line(session_kind, 40) or "未知"}
触发词：{trigger}

{recent_block}

{last_bot_block}

{inbound_block}

{response_block}
""".strip(),
        )
        timeout_seconds = max(
            0.2,
            min(
                5.0,
                _safe_float(
                    runtime_persona_setting(self, "smart_silence_model_timeout_seconds", 1.2),
                    1.2,
                    0.2,
                ),
            ),
        )
        started = time.perf_counter()
        raw = ""
        timeout_getter = getattr(self, "_model_timeout_seconds_for_call", None)
        timeout_override = (
            timeout_getter(
                task="smart_silence",
                provider_id=provider_id,
                timeout_key="SMART_SILENCE_PROVIDER_ID",
            )
            if callable(timeout_getter)
            else None
        )
        if timeout_override is not None:
            timeout_seconds = float(timeout_override)
        try:
            raw = await asyncio.wait_for(
                self._llm_call(
                    _render_user_memory_background_prompt(prompt),
                    max_tokens=100,
                    provider_id=provider_id,
                    task="smart_silence",
                ),
                timeout=timeout_seconds,
            ) or ""
        except asyncio.TimeoutError:
            result = {"decision": "send", "reason": f"timeout>{timeout_seconds:.1f}s", "confidence": 0.0, "source": "timeout"}
            cache[cache_key] = {"ts": now, "result": result}
            logger.warning(
                "智能沉默判定超时,默认放行: trigger=%s timeout=%.1fs text=%s",
                trigger,
                timeout_seconds,
                _single_line(inbound, 100),
            )
            return result
        except Exception as exc:
            result = {"decision": "send", "reason": _single_line(exc, 80), "confidence": 0.0, "source": "error"}
            cache[cache_key] = {"ts": now, "result": result}
            logger.warning("智能沉默判定失败,默认放行: %s", _single_line(exc, 120))
            return result

        payload = self._extract_json_payload(raw or "")
        if not isinstance(payload, dict):
            result = {"decision": "send", "reason": "invalid_json", "confidence": 0.0, "source": "model"}
            cache[cache_key] = {"ts": now, "result": result}
            return result
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {"send", "silent"}:
            decision = "send"
        confidence = max(0.0, min(1.0, _safe_float(payload.get("confidence"), 0.0, 0.0)))
        reason = _single_line(payload.get("reason"), 80) or "模型判定"
        threshold = max(
            0.0,
            min(
                1.0,
                _safe_float(runtime_persona_setting(self, "smart_silence_min_confidence", 0.66), 0.66, 0.0),
            ),
        )
        if decision == "silent" and confidence < threshold:
            decision = "send"
            reason = f"低置信度:{reason}"
        result = {
            "decision": decision,
            "reason": reason,
            "confidence": confidence,
            "source": "model",
            "trigger": trigger,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }
        cache[cache_key] = {"ts": now, "result": result}
        logger.info(
            "智能沉默判定: decision=%s confidence=%.2f trigger=%s elapsed=%dms reason=%s user=%s reply=%s",
            decision,
            confidence,
            trigger,
            result["elapsed_ms"],
            reason,
            _single_line(inbound, 120),
            _single_line(response, 140),
        )
        return result
