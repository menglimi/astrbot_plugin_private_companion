from __future__ import annotations

import asyncio
import contextvars
import functools
import time
import uuid
from types import MethodType
from typing import Any, Awaitable, Callable

try:
    from astrbot.core.message.message_event_result import MessageChain
except ImportError:
    from astrbot.api.event import MessageChain
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform import PlatformStatus
from .logging_util import get_module_logger

logger = get_module_logger(__name__)
try:
    from astrbot.core.platform.astr_message_event import MessageSession
except ImportError:
    from astrbot.core.platform.message_session import MessageSession


def _short(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").split())[:limit]


class ProactiveChatRuntimeBridge:
    """Reversible, instance-local bridge for Proactive Chat's complete send lifecycle."""

    REQUIRED_METHODS = (
        "check_and_chat",
        "_prepare_llm_request",
        "_generate_llm_response",
        "_send_proactive_message",
        "_send_chain_with_hooks",
        "_finalize_and_reschedule",
    )

    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.instance: Any | None = None
        self.version = ""
        self._originals: dict[str, Callable[..., Any]] = {}
        self._wrappers: dict[str, Callable[..., Any]] = {}
        self._attempt_var: contextvars.ContextVar[dict[str, Any] | None] = (
            contextvars.ContextVar(
                f"private_companion_proactive_chat_attempt_{id(self):x}",
                default=None,
            )
        )
        self._open_attempts: dict[str, dict[str, Any]] = {}
        self._watch_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._stopping = False
        self._last_error = ""
        self._last_event = "等待发现 Proactive Chat 运行实例"
        self._last_event_at = 0.0
        self._attached_at = 0.0
        self._missing_methods: list[str] = []
        self._counters = {
            "preflight_allowed": 0,
            "preflight_blocked": 0,
            "review_dropped": 0,
            "delivery_succeeded": 0,
            "delivery_failed": 0,
        }

    async def start(self) -> None:
        if isinstance(self._watch_task, asyncio.Task) and not self._watch_task.done():
            return
        self._stopping = False
        self._stop_event.clear()
        await self.refresh()
        self._watch_task = asyncio.create_task(
            self._watch_loop(),
            name="private-companion-proactive-chat-bridge",
        )

    async def stop(self) -> None:
        self._stopping = True
        self._stop_event.set()
        task = self._watch_task
        self._watch_task = None
        if isinstance(task, asyncio.Task) and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        attempts = list(self._open_attempts.values())
        for attempt in attempts:
            await self._cancel_attempt(attempt, reason="bridge_stopped")
        self.detach(reason="Private Companion 已停止")

    async def _watch_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=12.0)
                except asyncio.TimeoutError:
                    await self.refresh()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._note_error("运行实例监视失败", exc)

    async def refresh(self) -> bool:
        if self._stopping:
            return False
        if not bool(getattr(self.owner, "enable_proactive_chat_integration", True)):
            self.detach(reason="联动开关已关闭")
            return False
        candidate = self._discover_instance()
        if candidate is None:
            if self.instance is not None:
                self.detach(reason="Proactive Chat 运行实例已卸载")
            return False
        if candidate is self.instance and self._is_intact():
            return True
        if self.instance is not None:
            self.detach(reason="Proactive Chat 运行实例已更换")
        return self.attach(candidate)

    def _discover_instance(self) -> Any | None:
        context = getattr(self.owner, "context", None)
        getter = getattr(context, "get_all_stars", None)
        if not callable(getter):
            self._last_event = "当前 AstrBot 未提供运行插件实例查询接口"
            return None
        try:
            stars = list(getter() or [])
        except Exception as exc:
            self._note_error("读取 AstrBot 插件实例失败", exc)
            return None
        for metadata in stars:
            candidate = getattr(metadata, "star_cls", None)
            if candidate is None or candidate is self.owner:
                continue
            identifiers = (
                getattr(metadata, "module_path", ""),
                getattr(metadata, "name", ""),
                getattr(metadata, "root_dir_name", ""),
                getattr(type(candidate), "__module__", ""),
            )
            if any("astrbot_plugin_proactive_chat" in str(item or "").lower() for item in identifiers):
                return candidate
        return None

    def attach(self, instance: Any) -> bool:
        missing = [name for name in self.REQUIRED_METHODS if not callable(getattr(instance, name, None))]
        if missing:
            self._missing_methods = list(missing)
            self._last_error = "缺少兼容方法: " + ", ".join(missing)
            self._last_event = "当前 Proactive Chat 版本仅能使用发送装饰降级联动"
            self._last_event_at = time.time()
            return False
        existing = getattr(instance, "_private_companion_runtime_bridge", None)
        if existing is not None and existing is not self:
            self._last_error = "Proactive Chat 实例已被另一份 Private Companion 桥接占用"
            self._last_event = "深度联动未接管"
            self._last_event_at = time.time()
            return False
        originals = {name: getattr(instance, name) for name in self.REQUIRED_METHODS}
        wrappers = self._build_wrappers(originals)
        try:
            for name, wrapper in wrappers.items():
                setattr(instance, name, MethodType(wrapper, instance))
            setattr(instance, "_private_companion_runtime_bridge", self)
        except Exception as exc:
            for name, original in originals.items():
                try:
                    setattr(instance, name, original)
                except Exception:
                    pass
            self._note_error("安装深度联动方法失败", exc)
            return False
        self.instance = instance
        self._originals = originals
        self._wrappers = wrappers
        self.version = _short(getattr(instance, "version", ""), 40) or "未知"
        self._attached_at = time.time()
        self._last_error = ""
        self._missing_methods = []
        self._last_event = "生成前、终审、平台发送与结算链路已接管"
        self._last_event_at = self._attached_at
        logger.info(
            "Proactive Chat 深度联动已接入: version=%s methods=%s",
            self.version,
            ",".join(self.REQUIRED_METHODS),
        )
        return True

    def detach(self, *, reason: str = "") -> None:
        instance = self.instance
        if instance is not None:
            for name, original in self._originals.items():
                current = getattr(instance, name, None)
                wrapper = self._wrappers.get(name)
                current_func = getattr(current, "__func__", None)
                if wrapper is not None and current_func is wrapper:
                    try:
                        setattr(instance, name, original)
                    except Exception as exc:
                        logger.debug(
                            "恢复 Proactive Chat 方法失败: method=%s error=%s",
                            name,
                            _short(exc),
                        )
            if getattr(instance, "_private_companion_runtime_bridge", None) is self:
                try:
                    delattr(instance, "_private_companion_runtime_bridge")
                except Exception:
                    pass
        was_attached = instance is not None
        self.instance = None
        self.version = ""
        self._originals = {}
        self._wrappers = {}
        self._missing_methods = []
        if reason:
            self._last_event = reason
            self._last_event_at = time.time()
        if was_attached:
            logger.info("Proactive Chat 深度联动已卸载: %s", reason or "正常释放")

    def _is_intact(self) -> bool:
        instance = self.instance
        if instance is None:
            return False
        return all(
            getattr(getattr(instance, name, None), "__func__", None) is wrapper
            for name, wrapper in self._wrappers.items()
        )

    def _build_wrappers(
        self,
        originals: dict[str, Callable[..., Any]],
    ) -> dict[str, Callable[..., Awaitable[Any]]]:
        manager = self

        @functools.wraps(originals["check_and_chat"])
        async def check_and_chat(_instance: Any, session_id: str, *args: Any, **kwargs: Any) -> Any:
            return await manager._run_check(originals["check_and_chat"], session_id, *args, **kwargs)

        @functools.wraps(originals["_prepare_llm_request"])
        async def prepare_llm_request(_instance: Any, session_id: str, *args: Any, **kwargs: Any) -> Any:
            return await manager._run_prepare(originals["_prepare_llm_request"], session_id, *args, **kwargs)

        @functools.wraps(originals["_generate_llm_response"])
        async def generate_llm_response(_instance: Any, session_id: str, *args: Any, **kwargs: Any) -> Any:
            return await manager._run_generate(originals["_generate_llm_response"], session_id, *args, **kwargs)

        @functools.wraps(originals["_send_proactive_message"])
        async def send_proactive_message(_instance: Any, session_id: str, text: str, *args: Any, **kwargs: Any) -> Any:
            return await manager._run_send(originals["_send_proactive_message"], session_id, text, *args, **kwargs)

        @functools.wraps(originals["_send_chain_with_hooks"])
        async def send_chain_with_hooks(_instance: Any, session_id: str, components: list[Any], *args: Any, **kwargs: Any) -> Any:
            return await manager._run_send_chain(
                originals["_send_chain_with_hooks"],
                session_id,
                components,
                *args,
                **kwargs,
            )

        @functools.wraps(originals["_finalize_and_reschedule"])
        async def finalize_and_reschedule(_instance: Any, session_id: str, *args: Any, **kwargs: Any) -> Any:
            return await manager._run_finalize(
                originals["_finalize_and_reschedule"],
                session_id,
                *args,
                **kwargs,
            )

        return {
            "check_and_chat": check_and_chat,
            "_prepare_llm_request": prepare_llm_request,
            "_generate_llm_response": generate_llm_response,
            "_send_proactive_message": send_proactive_message,
            "_send_chain_with_hooks": send_chain_with_hooks,
            "_finalize_and_reschedule": finalize_and_reschedule,
        }

    async def _run_check(
        self,
        original: Callable[..., Awaitable[Any]],
        session_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        context_token = self._attempt_var.set(None)
        try:
            return await original(session_id, *args, **kwargs)
        finally:
            attempt = self._attempt_var.get()
            if isinstance(attempt, dict) and not attempt.get("closed"):
                await self._cancel_attempt(attempt, reason="flow_finished_without_delivery")
            if isinstance(attempt, dict):
                self._open_attempts.pop(str(attempt.get("attempt_id") or ""), None)
            self._attempt_var.reset(context_token)

    async def _run_prepare(
        self,
        original: Callable[..., Awaitable[Any]],
        session_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        instance = self.instance
        unanswered = 0
        if instance is not None:
            try:
                normalized = instance._normalize_session_id(session_id)
            except Exception:
                normalized = session_id
            state = getattr(instance, "session_data", {}).get(normalized, {})
            if isinstance(state, dict):
                try:
                    unanswered = max(0, int(state.get("unanswered_count", 0) or 0))
                except (TypeError, ValueError):
                    unanswered = 0
        try:
            prepared = await self.owner._prepare_proactive_chat_bridge(
                session_id,
                unanswered_count=unanswered,
            )
        except Exception as exc:
            self._note_error("生成前预检失败，已回退 Proactive Chat 原链路", exc)
            return await original(session_id, *args, **kwargs)
        if not bool(prepared.get("enabled")):
            return await original(session_id, *args, **kwargs)
        if not bool(prepared.get("allowed")):
            self._counters["preflight_blocked"] += 1
            self._last_event = "生成前已阻断: " + _short(prepared.get("reason"), 120)
            self._last_event_at = time.time()
            logger.info(
                "Proactive Chat 生成前已阻断: session=%s reason=%s",
                _short(session_id, 120),
                _short(prepared.get("reason"), 160),
            )
            return None
        attempt_id = "pc-deep-" + uuid.uuid4().hex
        attempt = {
            "attempt_id": attempt_id,
            "session_id": str(session_id or ""),
            "token": _short(prepared.get("token"), 80),
            "prompt_fragment": str(prepared.get("prompt_fragment") or "").strip(),
            "started_at": time.time(),
            "phase": "preparing",
            "text": "",
            "delivery_results": [],
            "delivery_success": False,
            "closed": False,
        }
        self._attempt_var.set(attempt)
        self._open_attempts[attempt_id] = attempt
        self._counters["preflight_allowed"] += 1
        try:
            package = await original(session_id, *args, **kwargs)
        except Exception:
            await self._cancel_attempt(attempt, reason="context_prepare_failed")
            raise
        if not isinstance(package, dict):
            await self._cancel_attempt(attempt, reason="context_prepare_empty")
            return package
        effective_session = str(package.get("session_id") or session_id or "")
        attempt["session_id"] = effective_session
        fragment = attempt["prompt_fragment"]
        marker = "<!-- private_companion_proactive_chat_deep_bridge_v1 -->"
        system_prompt = str(package.get("system_prompt") or "")
        if fragment and marker not in system_prompt:
            package["system_prompt"] = f"{system_prompt.rstrip()}\n\n{marker}\n{fragment}".strip()
        attempt["phase"] = "prepared"
        self._last_event = "已在模型调用前注入关系、状态、表达与收件人边界"
        self._last_event_at = time.time()
        return package

    async def _run_generate(
        self,
        original: Callable[..., Awaitable[Any]],
        session_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = await original(session_id, *args, **kwargs)
        attempt = self._matching_attempt(session_id)
        if attempt is None or not isinstance(result, tuple) or len(result) < 2:
            return result
        response_text = str(result[0] or "").strip()
        if not response_text:
            await self._cancel_attempt(attempt, reason="generation_empty")
            return result
        attempt["phase"] = "reviewing"
        try:
            review = await self.owner._review_proactive_chat_bridge_message(
                str(attempt.get("session_id") or session_id),
                response_text,
                token=str(attempt.get("token") or ""),
                attempt_id=str(attempt.get("attempt_id") or ""),
            )
        except Exception as exc:
            self._note_error("生成后终审失败，已取消本轮发送", exc)
            await self._cancel_attempt(attempt, reason="review_failed")
            return (None, result[1], *result[2:])
        reviewed_text = str(review.get("text") or "").strip()
        if not bool(review.get("ok")) or not reviewed_text:
            self._counters["review_dropped"] += 1
            self._last_event = "终审已阻断: " + _short(review.get("reason"), 120)
            self._last_event_at = time.time()
            await self._cancel_attempt(attempt, reason="review_dropped")
            return (None, result[1], *result[2:])
        attempt["phase"] = "reviewed"
        attempt["text"] = reviewed_text
        return (reviewed_text, result[1], *result[2:])

    async def _run_send(
        self,
        original: Callable[..., Awaitable[Any]],
        session_id: str,
        text: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        attempt = self._matching_attempt(session_id)
        if attempt is None:
            return await original(session_id, text, *args, **kwargs)
        attempt["phase"] = "sending"
        attempt["text"] = str(text or attempt.get("text") or "").strip()
        attempt["delivery_results"] = []
        try:
            result = await original(session_id, text, *args, **kwargs)
        except Exception:
            self._counters["delivery_failed"] += 1
            await self._cancel_attempt(attempt, reason="send_pipeline_exception")
            raise
        deliveries = list(attempt.get("delivery_results") or [])
        succeeded = [item for item in deliveries if isinstance(item, dict) and item.get("sent")]
        attempt["delivery_success"] = bool(succeeded)
        if not succeeded:
            self._counters["delivery_failed"] += 1
            reasons = [
                _short(item.get("reason"), 100)
                for item in deliveries
                if isinstance(item, dict) and item.get("reason")
            ]
            self._last_event = "平台发送未成功" + (": " + "；".join(reasons[:3]) if reasons else "")
            self._last_event_at = time.time()
            await self._cancel_attempt(attempt, reason="delivery_not_confirmed")
            return result
        try:
            recorded = await self.owner._record_proactive_chat_bridge_sent(
                str(attempt.get("session_id") or session_id),
                str(attempt.get("text") or text),
                token=str(attempt.get("token") or ""),
                attempt_id=str(attempt.get("attempt_id") or ""),
            )
        except Exception as exc:
            recorded = {"recorded": False, "reason": _short(exc)}
            self._note_error("平台已发送，但 Private Companion 状态结算失败", exc)
        attempt["recorded"] = bool(recorded.get("recorded"))
        attempt["closed"] = True
        attempt["phase"] = "delivered"
        self._counters["delivery_succeeded"] += 1
        physical_count = len(succeeded)
        total_count = len(deliveries)
        self._last_event = f"平台发送已确认，逻辑消息 1 条，物理发送 {physical_count}/{total_count} 条成功"
        self._last_event_at = time.time()
        logger.info(
            "Proactive Chat 深度联动发送结算: session=%s success=%s/%s recorded=%s",
            _short(session_id, 120),
            physical_count,
            total_count,
            bool(recorded.get("recorded")),
        )
        return result

    async def _run_send_chain(
        self,
        original: Callable[..., Awaitable[Any]],
        session_id: str,
        components: list[Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        attempt = self._matching_attempt(session_id)
        if attempt is None:
            return await original(session_id, components, *args, **kwargs)
        delivery = await self._send_chain_confirmed(session_id, components)
        attempt.setdefault("delivery_results", []).append(delivery)
        return delivery

    async def _send_chain_confirmed(self, session_id: str, components: list[Any]) -> dict[str, Any]:
        instance = self.instance
        if instance is None:
            return {"sent": False, "reason": "bridge_instance_missing"}
        try:
            processed = await instance._trigger_decorating_hooks(session_id, components)
            processed = list(processed or [])
            if not processed:
                return {"sent": False, "reason": "empty_after_decorating", "component_count": 0}
            chain = MessageChain(processed)
            parsed = instance._parse_session_id(session_id)
            if not parsed:
                await instance.context.send_message(session_id, chain)
                await instance._persist_proactive_message_to_platform_history(session_id, chain)
                return {"sent": True, "path": "context", "component_count": len(processed)}
            platform_id, message_type_text, target_id = parsed
            message_type = (
                MessageType.GROUP_MESSAGE
                if "Group" in str(message_type_text)
                else MessageType.FRIEND_MESSAGE
            )
            manager = instance.context.platform_manager
            getter = getattr(manager, "get_insts", None)
            platforms = list(getter() or []) if callable(getter) else list(getattr(manager, "platform_insts", []) or [])
            target_platform = next(
                (
                    platform
                    for platform in platforms
                    if str(getattr(platform.meta(), "id", "") or "") == str(platform_id)
                ),
                None,
            )
            if target_platform is None:
                await instance.context.send_message(session_id, chain)
                await instance._persist_proactive_message_to_platform_history(session_id, chain)
                return {"sent": True, "path": "context_fallback", "component_count": len(processed)}
            if getattr(target_platform, "status", PlatformStatus.RUNNING) != PlatformStatus.RUNNING:
                return {"sent": False, "reason": "platform_not_running", "platform": str(platform_id)}
            session = MessageSession(
                platform_name=str(platform_id),
                message_type=message_type,
                session_id=str(target_id),
            )
            await target_platform.send_by_session(session, chain)
            if str(platform_id) != "webchat":
                await instance._persist_proactive_message_to_platform_history(session_id, chain)
            return {
                "sent": True,
                "path": "platform",
                "platform": str(platform_id),
                "component_count": len(processed),
            }
        except Exception as exc:
            logger.error(
                "Proactive Chat 平台发送失败: session=%s error=%s",
                _short(session_id, 120),
                _short(exc, 200),
            )
            return {"sent": False, "reason": "platform_send_exception", "error": _short(exc, 180)}

    async def _run_finalize(
        self,
        original: Callable[..., Awaitable[Any]],
        session_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        attempt = self._matching_attempt(session_id)
        if attempt is None or bool(attempt.get("delivery_success")):
            return await original(session_id, *args, **kwargs)
        scheduler = getattr(self.instance, "_schedule_next_chat_and_save", None)
        if callable(scheduler):
            await scheduler(session_id)
        logger.info(
            "Proactive Chat 本轮无已确认发送，已跳过成功历史与未回复计数: session=%s",
            _short(session_id, 120),
        )
        return None

    def _matching_attempt(self, session_id: str) -> dict[str, Any] | None:
        attempt = self._attempt_var.get()
        if not isinstance(attempt, dict):
            return None
        current = str(attempt.get("session_id") or "")
        requested = str(session_id or "")
        if current == requested:
            return attempt
        instance = self.instance
        normalizer = getattr(instance, "_normalize_session_id", None)
        if callable(normalizer):
            try:
                if str(normalizer(current)) == str(normalizer(requested)):
                    return attempt
            except Exception:
                pass
        return None

    async def _cancel_attempt(self, attempt: dict[str, Any], *, reason: str) -> None:
        if attempt.get("closed"):
            return
        attempt["closed"] = True
        attempt["phase"] = "cancelled"
        attempt["cancel_reason"] = reason
        token = str(attempt.get("token") or "")
        session_id = str(attempt.get("session_id") or "")
        if token and session_id:
            try:
                await self.owner._cancel_proactive_chat_bridge(session_id, token=token)
            except Exception as exc:
                self._note_error("释放联动发送占用失败", exc)

    def outbound_context(self) -> dict[str, Any]:
        attempt = self._attempt_var.get()
        if not isinstance(attempt, dict) or attempt.get("phase") != "sending":
            return {}
        return {
            "detected": True,
            "deep_bridge": True,
            "attempt_id": str(attempt.get("attempt_id") or ""),
            "token": str(attempt.get("token") or ""),
            "full_text": str(attempt.get("text") or ""),
            "session_id": str(attempt.get("session_id") or ""),
        }

    def owns_outbound(self, session_id: str = "", attempt_id: str = "") -> bool:
        context = self.outbound_context()
        if not context:
            return False
        if attempt_id and str(context.get("attempt_id") or "") != str(attempt_id):
            return False
        if session_id and self._matching_attempt(session_id) is None:
            return False
        return True

    def status(self) -> dict[str, Any]:
        enabled = bool(getattr(self.owner, "enable_proactive_chat_integration", True))
        attached = bool(self.instance is not None and self._is_intact())
        if not enabled:
            mode = "disabled"
            label = "联动已关闭"
        elif attached:
            mode = "deep"
            label = "深度联动"
        elif self._last_error:
            mode = "degraded"
            label = "深度联动降级"
        elif self._discover_instance() is not None:
            mode = "fallback"
            label = "发送前兼容"
        else:
            mode = "waiting"
            label = "等待运行实例"
        return {
            "mode": mode,
            "mode_label": label,
            "attached": attached,
            "version": self.version,
            "methods": list(self.REQUIRED_METHODS) if attached else [],
            "method_count": len(self.REQUIRED_METHODS) if attached else 0,
            "last_error": self._last_error,
            "degraded": mode == "degraded",
            "required_methods": list(self.REQUIRED_METHODS),
            "missing_methods": list(self._missing_methods),
            "last_event": self._last_event,
            "last_event_at": self._last_event_at,
            "attached_at": self._attached_at,
            "open_attempt_count": len(self._open_attempts),
            "counters": dict(self._counters),
        }

    def _note_error(self, label: str, exc: Exception) -> None:
        self._last_error = f"{label}: {_short(exc, 180)}"
        self._last_event = label
        self._last_event_at = time.time()
        logger.warning("%s: %s", label, _short(exc, 180))
