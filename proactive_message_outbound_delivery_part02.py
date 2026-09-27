# -*- coding: utf-8 -*-
"""ProactiveMessageOutboundDeliveryPart02Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_outbound_delivery.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 530 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageOutboundDeliveryMixin）。
"""
from __future__ import annotations

from .proactive_message_outbound_delivery_shared import logger
from .proactive_message_outbound_delivery_shared import Any
from .proactive_message_outbound_delivery_shared import AstrMessageEvent
from .proactive_message_outbound_delivery_shared import MessageChain
from .proactive_message_outbound_delivery_shared import MessageSession
from .proactive_message_outbound_delivery_shared import MessageType
from .proactive_message_outbound_delivery_shared import Plain
from .proactive_message_outbound_delivery_shared import PlatformStatus
from .proactive_message_outbound_delivery_shared import _redact_outbound_secrets
from .proactive_message_outbound_delivery_shared import _single_line
from .proactive_message_outbound_delivery_shared import re
from .proactive_message_outbound_delivery_shared import runtime_persona_setting



class ProactiveMessageOutboundDeliveryPart02Mixin:
    """ProactiveMessageOutboundDeliveryPart02Mixin（从 ProactiveMessageOutboundDeliveryMixin 拆出）。"""


    async def _call_onebot_forward_action(self, client: Any, action: str, **params: Any) -> bool:
        for attr in ("call_action", "call_api", "api"):
            func = getattr(client, attr, None)
            if not callable(func):
                continue
            try:
                result = func(action, **params)
            except TypeError:
                try:
                    result = func(action, params)
                except Exception as exc:
                    if self._delivery_outcome_is_uncertain(exc):
                        self._log_uncertain_onebot_submission(action, exc)
                        return True
                    continue
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                continue
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                continue
            if self._onebot_forward_action_result_ok(result):
                return True
        func = getattr(client, action, None)
        if callable(func):
            try:
                result = func(**params)
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                return False
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                return False
            return self._onebot_forward_action_result_ok(result)
        return False

    async def _send_segmented_forward_message(
        self,
        *,
        target_type: str,
        target_id: str,
        segments: list[str],
        event: Any | None = None,
        source: str = "",
    ) -> bool:
        send_as_forward = self._segmented_setting(
            "send_as_forward",
            chat_type=target_type,
            default=False,
        )
        if not bool(send_as_forward):
            return False
        target_type = str(target_type or "").strip().lower()
        target_id = _single_line(target_id, 80)
        if target_type not in {"private", "group"} or not target_id:
            return False
        raw_segments = [_redact_outbound_secrets(item, self).strip() for item in segments if str(item or "").strip()]
        if len(raw_segments) <= 1:
            return False
        if runtime_persona_setting(self, "enable_tts_enhancement", False) and any(
            re.search(r"</?t{2,}s\b", item, flags=re.IGNORECASE) for item in raw_segments
        ):
            logger.info("分段合并消息跳过 TTS 内容: source=%s target=%s:%s", source or "unknown", target_type, target_id)
            return False
        cleaned_segments = self._clean_forward_segment_texts(raw_segments)
        if len(cleaned_segments) <= 1:
            return False
        hit = self._forbidden_recall_hit("\n".join(cleaned_segments))
        if hit:
            logger.warning(
                "分段合并消息命中违禁词，已拦截发送: source=%s target=%s:%s word=%s",
                source or "unknown",
                target_type,
                target_id,
                _single_line(hit, 40),
            )
            return False
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False
        nodes = self._forward_nodes_for_segments(cleaned_segments, event=event)
        if len(nodes) <= 1:
            return False
        target_value: Any = target_id
        try:
            target_value = int(target_id)
        except Exception:
            pass
        if target_type == "group":
            attempts = [
                ("send_group_forward_msg", {"group_id": target_value, "messages": nodes}),
                ("send_group_forward_msg", {"group_id": target_value, "nodes": nodes}),
                ("send_forward_msg", {"group_id": target_value, "messages": nodes}),
                ("send_forward_msg", {"group_id": target_value, "nodes": nodes}),
            ]
        else:
            attempts = [
                ("send_private_forward_msg", {"user_id": target_value, "messages": nodes}),
                ("send_private_forward_msg", {"user_id": target_value, "nodes": nodes}),
                ("send_forward_msg", {"user_id": target_value, "messages": nodes}),
                ("send_forward_msg", {"user_id": target_value, "nodes": nodes}),
            ]
        for action, params in attempts:
            if await self._call_onebot_forward_action(client, action, **params):
                self._confirm_outbound_delivery(
                    "",
                    [Plain(segment) for segment in cleaned_segments],
                )
                logger.info(
                    "分段消息已合并转发发送: source=%s target=%s:%s segments=%s",
                    source or "unknown",
                    target_type,
                    target_id,
                    len(cleaned_segments),
                )
                return True
        logger.info(
            "分段合并转发发送不可用，回退普通分段: source=%s target=%s:%s segments=%s",
            source or "unknown",
            target_type,
            target_id,
            len(cleaned_segments),
        )
        return False

    async def _send_segmented_proactive_forward_message(self, umo: str, segments: list[str], *, source: str = "proactive") -> bool:
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("merged_forward", umo=umo):
            return False
        session = self._parse_message_session(umo)
        if not session:
            return False
        target_id = _single_line(getattr(session, "session_id", ""), 80)
        if not target_id:
            return False
        target_type = "group" if self._message_type_for_session(session) == MessageType.GROUP_MESSAGE else "private"
        return await self._send_segmented_forward_message(
            target_type=target_type,
            target_id=target_id,
            segments=segments,
            source=source,
        )

    async def _send_segmented_event_forward_message(self, event: AstrMessageEvent, segments: list[str], *, source: str = "decorating_result") -> bool:
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("merged_forward", event=event):
            return False
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                user_id = _single_line(event.get_sender_id(), 80)
                if user_id:
                    return await self._send_segmented_forward_message(
                        target_type="private",
                        target_id=user_id,
                        segments=segments,
                        event=event,
                        source=source,
                    )
        except Exception:
            pass
        group_id = self._extract_group_id_from_event(event)
        if group_id:
            return await self._send_segmented_forward_message(
                target_type="group",
                target_id=group_id,
                segments=segments,
                event=event,
                source=source,
            )
        return False

    def _segmented_chat_scope_allows(self, chat_type: str) -> bool:
        chat_type = str(chat_type or "").strip().lower()
        if chat_type not in {"private", "group"}:
            chat_type = "private"
        if bool(
            runtime_persona_setting(self, "enable_segmented_proactive_chat_profiles", False)
        ):
            return bool(
                runtime_persona_setting(
                    self,
                    f"segmented_proactive_{chat_type}_enabled",
                    True,
                )
            )
        scope = str(
            runtime_persona_setting(self, "segmented_proactive_chat_scope", "all") or "all"
        ).strip().lower()
        if scope not in {"all", "private", "group"}:
            scope = "all"
        return scope == "all" or scope == chat_type

    def _segmented_chat_type_for_umo(self, umo: str) -> str:
        session = self._parse_message_session(umo)
        if session and self._message_type_for_session(session) == MessageType.GROUP_MESSAGE:
            return "group"
        return "private"

    def _segmented_chat_type_for_event(self, event: AstrMessageEvent) -> str:
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return "private"
        except Exception:
            pass
        return "group" if self._extract_group_id_from_event(event) else "private"

    def _segmented_setting(
        self,
        name: str,
        *,
        event: AstrMessageEvent | None = None,
        umo: str = "",
        chat_type: str = "",
        default: Any = None,
    ) -> Any:
        normalized_name = str(name or "").strip()
        fallback = runtime_persona_setting(
            self,
            f"segmented_proactive_{normalized_name}",
            default,
        )
        if not bool(
            runtime_persona_setting(self, "enable_segmented_proactive_chat_profiles", False)
        ):
            return fallback
        resolved_chat_type = str(chat_type or "").strip().lower()
        if resolved_chat_type not in {"private", "group"}:
            resolved_chat_type = (
                self._segmented_chat_type_for_event(event)
                if event is not None
                else self._segmented_chat_type_for_umo(umo)
            )
        return runtime_persona_setting(
            self,
            f"segmented_proactive_{resolved_chat_type}_{normalized_name}",
            fallback,
        )

    def _segmented_scope_allows_umo(self, umo: str) -> bool:
        return self._segmented_chat_scope_allows(self._segmented_chat_type_for_umo(umo))

    def _segmented_scope_allows_event(self, event: AstrMessageEvent) -> bool:
        return self._segmented_chat_scope_allows(self._segmented_chat_type_for_event(event))

    def _segmented_platform_allows(
        self,
        *,
        event: AstrMessageEvent | None = None,
        umo: str = "",
    ) -> bool:
        platform_supports = getattr(self, "_platform_supports", None)
        return not callable(platform_supports) or bool(
            platform_supports("segmented_reply", event=event, umo=umo)
        )

    async def _onebot_messages_from_chain(self, chain: list[Any]) -> tuple[list[dict[str, Any]], str]:
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

            messages = await AiocqhttpMessageEvent._parse_onebot_json(MessageChain(chain))
            return list(messages or []), ""
        except Exception as exc:
            return [], self._format_send_exception(exc)

    async def _send_chain_components_via_onebot_direct(
        self,
        umo: str,
        session: MessageSession | None,
        chain: list[Any],
    ) -> tuple[bool, str]:
        if session is None:
            return False, "UMO 无法解析，不能使用 OneBot 原生兜底"
        target_id = _single_line(getattr(session, "session_id", ""), 80)
        if not target_id or not target_id.isdigit():
            return False, f"session_id 不是纯数字，不能使用 OneBot 原生兜底: {target_id or '-'}"
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False, "没有找到可用的 aiocqhttp/OneBot 客户端"
        messages, parse_error = await self._onebot_messages_from_chain(chain)
        if not messages:
            return False, parse_error or "消息链无法转换为 OneBot 消息段"
        target_value: Any = target_id
        try:
            target_value = int(target_id)
        except Exception:
            pass
        is_group = self._message_type_for_session(session) == MessageType.GROUP_MESSAGE
        action = "send_group_msg" if is_group else "send_private_msg"
        params = {"group_id": target_value, "message": messages} if is_group else {"user_id": target_value, "message": messages}
        ok, error = await self._call_onebot_action_with_error(
            client,
            action,
            at_most_once=True,
            **params,
        )
        if ok:
            logger.info(
                "主动消息已通过 OneBot 原生兜底发送: action=%s target=%s segments=%s umo=%s",
                action,
                target_id,
                len(messages),
                _single_line(umo, 140),
            )
            return True, ""
        return False, error or f"OneBot 原生动作 {action} 返回失败"

    async def _send_chain_components(
        self,
        umo: str,
        chain: list[Any],
        *,
        apply_decorating_hooks: bool = True,
    ) -> bool:
        bot_scope_checker = getattr(self, "_bot_scope_allows_umo", None)
        if callable(bot_scope_checker) and not bot_scope_checker(umo):
            logger.info(
                "Bot 作用域已跳过后台投递: umo=%s",
                _single_line(umo, 140),
            )
            return False
        marker_cleaner = getattr(self, "_strip_llm_segment_marker_lines", None)
        if callable(marker_cleaner):
            cleaned_chain: list[Any] = []
            for component in chain or []:
                if not isinstance(component, Plain):
                    cleaned_chain.append(component)
                    continue
                original = str(getattr(component, "text", "") or "")
                cleaned = marker_cleaner(original)
                if cleaned:
                    cleaned_chain.append(Plain(cleaned) if cleaned != original else component)
            chain = cleaned_chain
        chain_redactor = getattr(self, "_redact_outbound_chain_secrets", None)
        if callable(chain_redactor):
            chain, redacted = chain_redactor(chain)
            if redacted:
                logger.error("主动发送前检测到敏感凭据并已脱敏: umo=%s stage=before_hooks", _single_line(umo, 120))
        hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(chain))
        if hit:
            logger.warning(
                "主动待发送消息命中违禁词，已拦截发送: umo=%s word=%s",
                umo,
                _single_line(hit, 40),
            )
            notifier = getattr(self, "_schedule_reply_interception_forward", None)
            if callable(notifier):
                notifier(
                    "proactive_block",
                    source="主动发送组件校验",
                    reason=f"命中违禁词：{_single_line(hit, 40)}",
                    source_session=umo,
                    before=self._chain_text_for_forbidden_recall(chain),
                )
            return False
        processed_chain = (
            await self._trigger_proactive_decorating_hooks(umo, chain)
            if apply_decorating_hooks
            else list(chain)
        )
        if not processed_chain:
            notifier = getattr(self, "_schedule_reply_interception_forward", None)
            if callable(notifier):
                notifier(
                    "proactive_block",
                    source="主动发送装饰钩子",
                    reason="装饰钩子清空了待发送消息",
                    source_session=umo,
                    before=self._chain_text_for_forbidden_recall(chain),
                )
            return False
        if callable(chain_redactor):
            processed_chain, redacted = chain_redactor(processed_chain)
            if redacted:
                logger.error("主动装饰后检测到敏感凭据并已脱敏: umo=%s stage=after_hooks", _single_line(umo, 120))
        tts_chain_guard = getattr(self, "_sanitize_outbound_tts_chain_without_event", None)
        if callable(tts_chain_guard):
            processed_chain = await tts_chain_guard(processed_chain, umo=umo)
            if not processed_chain:
                notifier = getattr(self, "_schedule_reply_interception_forward", None)
                if callable(notifier):
                    notifier("proactive_block", source="主动发送 TTS 校验", reason="TTS 校验清空了待发送消息", source_session=umo)
                return False
        hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(processed_chain))
        if hit:
            logger.warning(
                "主动装饰后消息命中违禁词，已拦截发送: umo=%s word=%s",
                umo,
                _single_line(hit, 40),
            )
            notifier = getattr(self, "_schedule_reply_interception_forward", None)
            if callable(notifier):
                notifier(
                    "proactive_block",
                    source="主动装饰后校验",
                    reason=f"装饰后命中违禁词：{_single_line(hit, 40)}",
                    source_session=umo,
                    before=self._chain_text_for_forbidden_recall(processed_chain),
                )
            return False
        session = self._parse_message_session(umo)
        platform = self._get_platform_for_session(session) if session else None
        precise_error: Exception | None = None
        if runtime_persona_setting(self, "enable_precise_platform_send", True) and session and platform:
            status = getattr(platform, "status", None)
            if status is not None and status != PlatformStatus.RUNNING:
                logger.warning("目标平台未运行,跳过主动发送: %s", umo)
                notifier = getattr(self, "_schedule_reply_interception_forward", None)
                if callable(notifier):
                    notifier(
                        "proactive_block",
                        source="主动发送平台校验",
                        reason="目标平台未运行",
                        source_session=umo,
                        before=self._chain_text_for_forbidden_recall(processed_chain),
                    )
                raise RuntimeError(f"目标平台未运行，无法发送主动消息: {_single_line(umo, 140)}")
            try:
                session_obj = self._session_for_platform(session, platform)
                precise_result = await platform.send_by_session(session_obj, MessageChain(processed_chain))
                if precise_result is not False:
                    self._confirm_outbound_delivery(umo, processed_chain)
                    return True
                precise_error = RuntimeError("精确平台发送返回 False（平台未接受消息）")
                logger.warning(
                    "精确平台发送未被目标平台接受,回退核心发送: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
            except Exception as e:
                precise_error = e
                if self._is_onebot_event_checker_send_rejection(e):
                    summary = self._onebot_event_checker_rejection_summary()
                    logger.info(
                        "主动发送被 QQ/NTQQ 底层拒绝，停止对同一 sendMsg 链路的立即重复尝试: target=%s",
                        self._describe_send_target(umo, session, platform),
                    )
                    raise RuntimeError(summary) from e
                if self._delivery_outcome_is_uncertain(e):
                    logger.warning(
                        "精确平台发送回执不确定，为避免同一主动消息立即重复发送，本次按已提交处理: target=%s error=%s",
                        self._describe_send_target(umo, session, platform),
                        self._format_send_exception(e),
                    )
                    self._confirm_outbound_delivery(umo, processed_chain)
                    return True
                logger.warning(
                    "精确平台发送失败,回退核心发送: target=%s error=%s",
                    self._describe_send_target(umo, session, platform),
                    self._format_send_exception(e),
                )
        core_error: Exception | None = None
        core_result: Any = None
        core_session: str | MessageSession = umo
        if session and platform:
            core_session = self._session_for_platform(session, platform)
        try:
            core_result = await self.context.send_message(core_session, self._build_result_from_chain(processed_chain))
            if core_result is not False:
                self._confirm_outbound_delivery(umo, processed_chain)
                return True
            platform_supports = getattr(self, "_platform_supports", None)
            if not callable(platform_supports) or platform_supports("onebot_actions", umo=umo):
                logger.warning(
                    "主动核心发送未找到匹配平台,尝试 OneBot 原生兜底: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
            else:
                logger.warning(
                    "主动核心发送未被官方平台接受,不使用 OneBot 原生兜底: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
        except Exception as e:
            core_error = e
            if self._is_onebot_event_checker_send_rejection(e):
                logger.info(
                    "主动核心发送被 QQ/NTQQ 底层拒绝，停止同链立即重试: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
                raise RuntimeError(self._onebot_event_checker_rejection_summary()) from e
            if self._delivery_outcome_is_uncertain(e):
                logger.warning(
                    "主动核心发送回执不确定，为避免 OneBot 兜底重复发送，本次按已提交处理: target=%s error=%s",
                    self._describe_send_target(umo, session, platform),
                    self._format_send_exception(e),
                )
                self._confirm_outbound_delivery(umo, processed_chain)
                return True
            target = self._describe_send_target(umo, session, platform)
            precise_text = self._format_send_exception(precise_error) or "未尝试或未失败"
            fallback_text = self._format_send_exception(e)
            logger.warning(
                "主动核心发送失败: target=%s precise_error=%s fallback_error=%s",
                target,
                precise_text,
                fallback_text,
            )
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("onebot_actions", umo=umo):
            target = self._describe_send_target(umo, session, platform)
            precise_text = self._format_send_exception(precise_error) or "未尝试或未失败"
            fallback_text = self._format_send_exception(core_error) if core_error is not None else (
                "AstrBot 核心发送返回 False（平台未找到或官方通道拒绝）"
            )
            raise RuntimeError(
                f"主动消息发送失败: {target}; precise={precise_text}; fallback={fallback_text}; 当前平台不使用 OneBot 原生兜底"
            ) from core_error
        direct_ok, direct_error = await self._send_chain_components_via_onebot_direct(umo, session, processed_chain)
        if direct_ok:
            self._confirm_outbound_delivery(umo, processed_chain)
            return True
        if self._is_onebot_event_checker_send_rejection(direct_error):
            raise RuntimeError(self._onebot_event_checker_rejection_summary())
        target = self._describe_send_target(umo, session, platform)
        precise_text = self._format_send_exception(precise_error) or "未尝试或未失败"
        if core_error is not None:
            fallback_text = self._format_send_exception(core_error)
        elif core_result is False:
            fallback_text = "AstrBot 核心发送返回 False（未找到匹配平台或平台拒绝发送）"
        else:
            fallback_text = "未尝试或未失败"
        logger.warning(
            "主动发送兜底也失败: target=%s precise_error=%s fallback_error=%s direct_error=%s",
            target,
            precise_text,
            fallback_text,
            direct_error,
        )
        raise RuntimeError(
            f"主动消息发送失败: {target}; precise={precise_text}; fallback={fallback_text}; direct={direct_error}"
        ) from core_error
