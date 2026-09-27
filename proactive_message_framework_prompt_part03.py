# -*- coding: utf-8 -*-
"""ProactiveMessageFrameworkPromptPart03Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_framework_prompt.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 572 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageFrameworkPromptMixin）。
"""
from __future__ import annotations

from .proactive_message_framework_prompt_shared import _host_build_main_agent, logger
from .proactive_message_framework_prompt_shared import Any
from .proactive_message_framework_prompt_shared import AstrMessageEvent
from .proactive_message_framework_prompt_shared import Context
from .proactive_message_framework_prompt_shared import LLMResponse
from .proactive_message_framework_prompt_shared import MainAgentBuildConfig
from .proactive_message_framework_prompt_shared import Plain
from .proactive_message_framework_prompt_shared import ProviderRequest
from .proactive_message_framework_prompt_shared import SyntheticPrivateWakeEvent
from .proactive_message_framework_prompt_shared import _looks_like_upstream_llm_error_response
from .proactive_message_framework_prompt_shared import _path_text
from .proactive_message_framework_prompt_shared import _safe_int
from .proactive_message_framework_prompt_shared import _single_line
from .proactive_message_framework_prompt_shared import asyncio
from .proactive_message_framework_prompt_shared import deepcopy
from .proactive_message_framework_prompt_shared import hashlib
from .proactive_message_framework_prompt_shared import os
from .proactive_message_framework_prompt_shared import re
from .proactive_message_framework_prompt_shared import runtime_persona_setting
from .proactive_message_framework_prompt_shared import time
from .proactive_message_framework_prompt_shared import uuid



class ProactiveMessageFrameworkPromptPart03Mixin:
    """ProactiveMessageFrameworkPromptPart03Mixin（从 ProactiveMessageFrameworkPromptMixin 拆出）。"""


    def _install_proactive_semantic_provider_fallback(
        self,
        build_result: Any,
        *,
        label: str,
    ) -> bool:
        """Let AstrBot's native fallback chain handle successful error responses."""
        runner = getattr(build_result, "agent_runner", None)
        if runner is None:
            return False
        installed_marker = "_private_companion_semantic_provider_fallback_installed"
        if bool(getattr(runner, installed_marker, False)):
            return True
        original_iter = getattr(runner, "_iter_llm_responses", None)
        if not callable(original_iter):
            return False

        async def _guarded_iter(*args: Any, **kwargs: Any):
            buffered_chunks: list[LLMResponse] = []
            async for response in original_iter(*args, **kwargs):
                if isinstance(response, LLMResponse) and bool(response.is_chunk):
                    buffered_chunks.append(response)
                    continue

                result_chain = getattr(response, "result_chain", None)
                chain = list(getattr(result_chain, "chain", []) or [])
                has_non_plain_component = any(
                    not isinstance(component, Plain) for component in chain
                )
                completion_text = str(
                    getattr(response, "completion_text", "") or ""
                ).strip()
                response_role = str(
                    getattr(response, "role", "") or ""
                ).strip().lower()
                is_native_provider_error = (
                    isinstance(response, LLMResponse)
                    and response_role == "err"
                    and not bool(getattr(response, "is_chunk", False))
                )
                is_semantic_provider_error = (
                    isinstance(response, LLMResponse)
                    and response_role == "assistant"
                    and not bool(getattr(response, "is_chunk", False))
                    and not list(getattr(response, "tools_call_name", []) or [])
                    and not has_non_plain_component
                    and bool(completion_text)
                    and _looks_like_upstream_llm_error_response(completion_text)
                )
                if is_native_provider_error or is_semantic_provider_error:
                    provider = getattr(runner, "provider", None)
                    provider_config = getattr(provider, "provider_config", {})
                    provider_id = (
                        _single_line(provider_config.get("id"), 80)
                        if isinstance(provider_config, dict)
                        else ""
                    )
                    response_ref = hashlib.sha256(
                        completion_text.encode("utf-8", errors="replace")
                    ).hexdigest()[:12]
                    logger.warning(
                        "主动主链识别到 Provider 错误响应,已交给 AstrBot 原生回退链: label=%s provider=%s kind=%s response_ref=%s",
                        _single_line(label, 80),
                        provider_id or type(provider).__name__,
                        "native_error" if is_native_provider_error else "semantic_error",
                        response_ref,
                    )
                    sanitized_response = LLMResponse(
                        role="err",
                        completion_text=(
                            "Provider API error: upstream returned an internal "
                            "failure message."
                        ),
                    )
                    for attr_name in ("id", "usage"):
                        attr_value = getattr(response, attr_name, None)
                        if attr_value is not None:
                            try:
                                setattr(sanitized_response, attr_name, attr_value)
                            except Exception:
                                pass
                    yield sanitized_response
                    return

                for chunk in buffered_chunks:
                    yield chunk
                buffered_chunks.clear()
                yield response
                return

            for chunk in buffered_chunks:
                yield chunk

        try:
            setattr(runner, "_iter_llm_responses", _guarded_iter)
            setattr(runner, installed_marker, True)
        except Exception as exc:
            logger.warning(
                "主动主链无法安装 Provider 语义错误回退适配器: label=%s error_type=%s",
                _single_line(label, 80),
                type(exc).__name__,
            )
            return False
        return True

    def _framework_agent_meta_summary_leak(self, text: str) -> bool:
        cleaned = _single_line(text, 500).lower()
        if not cleaned:
            return False
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", " ", cleaned).strip()
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", "", cleaned)
        if self._is_proactive_delivery_receipt_text(text):
            return True
        if self._is_proactive_instruction_leak_text(text):
            return True
        if (
            ("差不多20条" in cleaned or "差不多 20 条" in cleaned or "20条不同" in cleaned)
            and any(token in cleaned for token in ("没收到回复", "发消息", "消息主要是", "工具调用"))
        ):
            return True
        if (
            ("二十次" in cleaned or "20次" in cleaned or "多次" in cleaned)
            and any(token in cleaned for token in ("试着给", "发私信", "发消息"))
            and any(token in cleaned for token in ("有没有成功", "成功发出去", "没收到回复", "不确定这些消息"))
        ):
            return True
        if (
            ("读取图片文件" in cleaned or "图片文件有问题" in cleaned)
            and any(token in cleaned for token in ("占位", "工具调用", "没法继续", "多次发消息"))
        ):
            return True
        if "工具调用限制" in cleaned and any(token in cleaned for token in ("没法继续", "多次发消息", "发消息")):
            return True
        markers = (
            "trying to send messages",
            "trying to send various messages",
            "sent 20",
            "no response yet",
            "shared parts",
            "asked for her thoughts",
            "message captured",
            "executed the same tool",
            "repetition is now very high",
            "agent reached max steps",
            "forcing a final response",
            "一直试着给",
            "发了差不多20条",
            "还没收到回复",
            "读取图片文件有问题",
            "工具调用限制",
        )
        compact_markers = (
            "tryingtosendmessages",
            "tryingtosendvariousmessages",
            "sent20",
            "noresponseyet",
            "sharedparts",
            "askedforherthoughts",
            "messagecaptured",
            "executedthesametool",
            "repetitionisnowveryhigh",
            "agentreachedmaxsteps",
            "forcingafinalresponse",
            "一直试着给",
            "发了差不多20条",
            "还没收到回复",
            "读取图片文件有问题",
            "工具调用限制",
        )
        return any(marker in cleaned or marker in normalized for marker in markers) or any(
            marker in compact for marker in compact_markers
        )

    async def _conversation_db_operation(self, label: str, operation: Any) -> Any:
        lock = getattr(self, "_conversation_db_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._conversation_db_lock = lock
        for attempt in range(5):
            try:
                async with lock:
                    return await operation()
            except Exception as exc:
                text = str(exc or "").lower()
                locked = "database is locked" in text or "sqlite3.operationalerror" in text
                if locked and attempt < 4:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                logger.debug("会话数据库操作失败: %s error=%s", label, exc)
                raise

    def _is_sqlite_locked_error(self, exc: Exception) -> bool:
        text = str(exc or "").lower()
        return "database is locked" in text or "sqlite3.operationalerror" in text or "sqlalche.me/e/20/e3q8" in text

    async def _get_current_conversation_safely(self, umo: str, *, label: str = "conversation") -> Any:
        async def _read():
            conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
            if not conv_id:
                return None
            return await self.context.conversation_manager.get_conversation(umo, conv_id)

        return await self._conversation_db_operation(label, _read)

    async def _ensure_conversation_id_for_umo(self, umo: str, *, title: str = "Private Companion 主动消息") -> str:
        conv_mgr = getattr(getattr(self, "context", None), "conversation_manager", None)
        if conv_mgr is None:
            return ""
        conv_id = await conv_mgr.get_curr_conversation_id(umo)
        if conv_id:
            return str(conv_id)
        session = self._parse_message_session(umo)
        platform_id = _single_line(getattr(session, "platform_id", ""), 80) if session is not None else ""
        try:
            if platform_id:
                conv_id = await conv_mgr.new_conversation(umo, platform_id)
            else:
                conv_id = await conv_mgr.new_conversation(umo, title=title)
        except TypeError:
            try:
                conv_id = await conv_mgr.new_conversation(umo, title=title)
            except TypeError:
                conv_id = await conv_mgr.new_conversation(umo)
        if conv_id:
            logger.info(
                "已为主动消息存档创建 AstrBot 会话: umo=%s cid=%s",
                _single_line(umo, 140),
                _single_line(conv_id, 80),
            )
        return str(conv_id or "")

    def _proactive_synthetic_event(self, umo: str, *, prompt: str, name: str) -> AstrMessageEvent | None:
        framework_context = self._proactive_framework_context()
        if framework_context is None:
            return None
        session = self._parse_message_session(umo)
        if not session:
            return None
        return SyntheticPrivateWakeEvent(
            context=framework_context,
            session=session,
            message=prompt,
            sender_name=name or "PrivateCompanion",
        )

    def _proactive_framework_context(self) -> Context | None:
        """Resolve only a native AstrBot Context from current or legacy wrappers."""
        candidate = getattr(self, "context", None)
        pending = [candidate]
        visited: set[int] = set()
        wrapper_attrs = (
            "context_obj",
            "plugin_context",
            "wrapped_context",
            "raw_context",
            "_context",
            "_context_obj",
            "_plugin_context",
            "_wrapped_context",
            "_raw_context",
            "__wrapped__",
        )
        while pending:
            current = pending.pop(0)
            if isinstance(current, Context):
                return current
            if current is None or id(current) in visited:
                continue
            visited.add(id(current))
            for attr in wrapper_attrs:
                try:
                    nested = getattr(current, attr, None)
                except Exception:
                    continue
                if nested is not None and id(nested) not in visited:
                    pending.append(nested)
        return None

    def _proactive_conversation_with_configured_persona(self, conversation: Any) -> Any:
        specific_id = str(
            getattr(
                self,
                "_effective_plugin_persona_id",
                lambda: runtime_persona_setting(self, "plugin_specific_persona_id", ""),
            )()
            or ""
        ).strip()
        if conversation is None or not specific_id:
            return conversation
        if str(getattr(conversation, "persona_id", "") or "").strip() == specific_id:
            return conversation
        try:
            scoped = deepcopy(conversation)
            scoped.persona_id = specific_id
            return scoped
        except Exception as exc:
            logger.warning(
                "无法为主动主链应用插件指定人格,继续使用会话人格: persona=%s error=%s",
                _single_line(specific_id, 80),
                _single_line(exc, 120),
            )
            return conversation

    async def _run_framework_agent_text(
        self,
        *,
        umo: str,
        prompt: str,
        name: str,
        label: str,
        task: str | None = None,
        user: dict[str, Any] | None = None,
        max_steps: int = 20,
    ) -> str:
        self._cleanup_framework_delivery_caches()
        cache_key = str(umo or "")
        self._framework_captured_send_cache.pop(cache_key, None)
        getattr(self, "_framework_captured_send_cache_at", {}).pop(cache_key, None)
        deferred_photo_cache = getattr(self, "_framework_deferred_photo_cache", None)
        if isinstance(deferred_photo_cache, dict):
            deferred_photo_cache.pop(cache_key, None)
        getattr(self, "_framework_deferred_photo_cache_at", {}).pop(cache_key, None)
        framework_context = self._proactive_framework_context()
        if framework_context is None:
            context_value = getattr(self, "context", None)
            context_type = type(context_value).__name__ if context_value is not None else "None"
            warning_key = f"{type(context_value).__module__}.{context_type}" if context_value is not None else context_type
            if getattr(self, "_proactive_framework_context_warning_key", "") != warning_key:
                self._proactive_framework_context_warning_key = warning_key
                logger.warning(
                    "主动主链未取得 AstrBot 原生 Context,已直接转入人格化兜底: input_type=%s；请重载插件或重启 AstrBot",
                    context_type,
                )
            return ""
        camera_state: dict[str, Any] = {}
        if label == "proactive_message" and isinstance(user, dict):
            camera_prompt_getter = getattr(self, "_reality_touch_camera_proactive_prompt", None)
            if callable(camera_prompt_getter):
                camera_prompt = camera_prompt_getter(
                    user,
                    user_id=str(user.get("user_id") or ""),
                )
                if camera_prompt:
                    prompt = f"{prompt.rstrip()}\n\n{camera_prompt}"
            camera_state_getter = getattr(self, "_reality_touch_camera_proactive_state", None)
            if callable(camera_state_getter):
                value = camera_state_getter(
                    user,
                    user_id=str(user.get("user_id") or ""),
                )
                if isinstance(value, dict):
                    camera_state = value
        task_key = _single_line(task or label, 120)
        prompt_applier = getattr(self, "_apply_task_prompt_override_for_call", None)
        if callable(prompt_applier):
            prompt, _unused_system_prompt = prompt_applier(
                task_key,
                prompt,
                None,
                flatten_system_prompt=True,
            )
        event = self._proactive_synthetic_event(umo, prompt=prompt, name=name)
        if event is None:
            return ""
        try:
            setattr(event, "private_companion_skip_external_token_stats", True)
            setattr(event, "private_companion_proactive_framework", True)
            setattr(event, "private_companion_skip_passive_input_status", True)
        except Exception:
            pass
        cfg = framework_context.get_config(umo=umo) if umo else framework_context.get_config()
        provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        build_cfg = MainAgentBuildConfig(
            tool_call_timeout=int(provider_settings.get("tool_call_timeout", 120) or 120),
            llm_safety_mode=False,
            streaming_response=False,
        )
        req = ProviderRequest(
            prompt=prompt,
            conversation=None,
            session_id=getattr(event, "session_id", None) or umo,
        )

        captured_tool_sends: list[Any] = []
        result = None
        async def _run_with_retries() -> None:
            nonlocal result, captured_tool_sends
            for attempt in range(3):
                try:
                    conv = await self._get_current_conversation_safely(umo, label=f"{label}_framework_read")
                    req.conversation = self._proactive_conversation_with_configured_persona(conv)

                    async def _runner_factory():
                        build_result = await _host_build_main_agent()(
                            event=event,
                            # AstrBot 4.26.2+ validates this as the concrete Context type.
                            plugin_context=framework_context,
                            config=build_cfg,
                            req=req,
                        )
                        excluded_tools = {"AIsearch"}
                        if not camera_state.get("direct_allowed"):
                            excluded_tools.add("pc_reality_touch_camera_snapshot")
                        self._filter_incompatible_proactive_framework_tools(req, excluded_tools)
                        self._install_proactive_semantic_provider_fallback(
                            build_result,
                            label=label,
                        )
                        return build_result

                    result, captured_tool_sends = await self._capture_framework_send_message_calls(
                        target_session=umo,
                        runner_factory=_runner_factory,
                        max_steps=max_steps,
                    )
                    break
                except Exception as exc:
                    if self._is_sqlite_locked_error(exc) and attempt < 2:
                        wait_seconds = 0.35 * (attempt + 1)
                        logger.info(
                            "主动主链遇到会话库锁,稍后重试: label=%s session=%s retry=%s",
                            label,
                            _single_line(umo, 120),
                            attempt + 1,
                        )
                        await asyncio.sleep(wait_seconds)
                        continue
                    raise
        await _run_with_retries()
        if captured_tool_sends:
            self._framework_captured_send_cache[cache_key] = list(captured_tool_sends)
            captured_at = getattr(self, "_framework_captured_send_cache_at", None)
            if isinstance(captured_at, dict):
                captured_at[cache_key] = time.time()
        if bool(getattr(event, "_private_companion_photo_tool_deferred", False)):
            deferred_path = _path_text(
                getattr(event, "_private_companion_photo_tool_deferred_path", ""),
                1000,
            )
            if deferred_path and os.path.exists(deferred_path):
                cache = getattr(self, "_framework_deferred_photo_cache", None)
                if not isinstance(cache, dict):
                    cache = {}
                    self._framework_deferred_photo_cache = cache
                deferred_caption = self._sanitize_captured_plain_text(
                    getattr(event, "_private_companion_photo_tool_deferred_caption", "")
                )
                cache[cache_key] = {
                    "path": deferred_path,
                    "caption": deferred_caption,
                    "intent_kind": _single_line(
                        getattr(event, "_private_companion_photo_tool_deferred_intent_kind", ""),
                        40,
                    ),
                }
                deferred_at = getattr(self, "_framework_deferred_photo_cache_at", None)
                if isinstance(deferred_at, dict):
                    deferred_at[cache_key] = time.time()
                self._framework_captured_send_cache.pop(cache_key, None)
                getattr(self, "_framework_captured_send_cache_at", {}).pop(cache_key, None)
                logger.info(
                    "主动主链已接收 pc_generate_photo 成图，等待统一发送: label=%s session=%s",
                    label,
                    _single_line(cache_key, 120),
                )
                return deferred_caption
        runner = getattr(result, "agent_runner", None) if result else None
        llm_resp = runner.get_final_llm_resp() if runner else None
        text = str(getattr(llm_resp, "completion_text", "") or "").strip()
        response_role = str(getattr(llm_resp, "role", "") or "").strip().lower()
        captured_text = self._captured_send_plain_text(captured_tool_sends)
        if captured_text:
            if text and self._framework_agent_meta_summary_leak(text):
                logger.warning(
                    "主动主链 final 疑似工具循环摘要或 Provider 失败,改用已捕获发送文本: label=%s final=%s captured=%s",
                    label,
                    _single_line(text, 160),
                    _single_line(captured_text, 160),
                )
            text = captured_text
        elif response_role == "err" or (
            text and self._framework_agent_meta_summary_leak(text)
        ):
            logger.warning(
                "主动主链 final 疑似工具循环摘要或 Provider 失败且无可用捕获文本,已丢弃: label=%s text=%s",
                label,
                _single_line(text, 180),
            )
            return ""
        return text

    async def _generate_proactive_message_via_framework(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        action_context: str = "",
        action: str = "message",
        motive: str = "",
    ) -> str:
        umo = str(user.get("umo") or "").strip()
        if not umo:
            return ""
        prompt = await self._build_framework_proactive_prompt(
            user=user,
            name=name,
            reason=reason,
            action=action,
            action_context=action_context,
            motive=motive,
        )
        recorder = getattr(self, "_record_prompt_injection_snapshot", None)
        if callable(recorder):
            trace_id = f"pro-{uuid.uuid4().hex[:16]}"
            message_preview = _single_line(
                " / ".join(
                    part
                    for part in (
                        name,
                        _single_line(user.get("planned_proactive_topic"), 60),
                        motive,
                        reason,
                        action,
                    )
                    if _single_line(part, 60)
                ),
                220,
            )
            await recorder(
                kind="proactive",
                session=umo,
                title="主动消息提示词",
                text=prompt,
                mode=reason,
                trace_id=trace_id,
                message_preview=message_preview,
                sender_label=_single_line(f"{name}/{user.get('user_id')}", 80),
                metadata={
                    "用户": _single_line(user.get("user_id"), 80),
                    "称呼": name,
                    "原因": reason,
                    "动作": action,
                    "动机": motive,
                    "话题": _single_line(user.get("planned_proactive_topic"), 80),
                },
            )
        try:
            raw_text = await self._run_framework_agent_text(
                umo=umo,
                prompt=prompt,
                name=name,
                label="proactive_message",
                task="proactive_message",
                user=user,
                max_steps=20,
            )
            raw_text = str(raw_text or "")
            if not raw_text:
                return ""
            cleaned_text, payloads = self._extract_timer_directives(raw_text)
            if payloads:
                logger.info(
                    "主动消息中清理到对话临时预约标签,不再由主动链路登记: user=%s",
                    _single_line(user.get("user_id"), 40),
                )
            return cleaned_text
        except Exception as exc:
            if self._is_sqlite_locked_error(exc):
                logger.warning("主动消息主链被会话数据库锁住,本轮跳过并等待下次调度: %s", _single_line(umo, 120))
            else:
                logger.warning("主动消息主链生成失败: %s", exc)
            return ""

    def _proactive_history_limit(self, stage: str) -> int:
        review_stage = str(stage or "").strip().lower() == "review"
        attr = "proactive_review_history_limit" if review_stage else "proactive_generation_history_limit"
        default = 30 if review_stage else 20
        return _safe_int(
            runtime_persona_setting(self, attr, default),
            default,
            1,
            200,
        )
