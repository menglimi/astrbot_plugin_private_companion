# -*- coding: utf-8 -*-
"""PrivateCompanionPluginSegmentedReplyPart02Mixin。

由 tools/split_mixin_domain.py 从 main_segmented_reply.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 172 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginSegmentedReplyMixin）。
"""
from __future__ import annotations
from .main_segmented_reply_shared import Any
from .main_segmented_reply_shared import AstrMessageEvent
from .main_segmented_reply_shared import Plain
from .main_segmented_reply_shared import _single_line
from .main_segmented_reply_shared import _strip_chain_plain_thinking
from .main_segmented_reply_shared import component_kind
from .main_segmented_reply_shared import component_order_from_owner
from .main_segmented_reply_shared import component_strategies_from_owner
from .main_segmented_reply_shared import plan_component_chunks
from .main_segmented_reply_shared import runtime_persona_setting
from .main_segmented_reply_shared import sanitize_llm_segment_control_tokens



class PrivateCompanionPluginSegmentedReplyPart02Mixin:
    """PrivateCompanionPluginSegmentedReplyPart02Mixin（从 PrivateCompanionPluginSegmentedReplyMixin 拆出）。"""


    def _segment_llm_reply_chain(self, event: AstrMessageEvent, chain: list[Any]) -> tuple[list[list[Any]], bool, str]:
        working_chain = list(chain or [])
        # Apply the same configured cleanup before segmenting joined text.
        _strip_chain_plain_thinking(self, working_chain)
        reply_prefix = [comp for comp in working_chain if self._is_reply_component(comp)]
        content_chain = [comp for comp in working_chain if not self._is_reply_component(comp)]
        if (
            bool(runtime_persona_setting(self, 'enable_proactive_quote_trigger_message', False))
            and bool(runtime_persona_setting(self, 'enable_quote_group_reply', True))
            and not reply_prefix
            and not self._chain_has_reply_component(working_chain)
        ):
            quote_message_id = self._group_current_reply_quote_message_id(
                event,
                text_or_chain=content_chain,
            )
            reply = self._make_reply_component(quote_message_id, event=event)
            if reply is not None:
                working_chain = [reply, *working_chain]

        llm_controlled = bool(
            runtime_persona_setting(self, "enable_llm_controlled_segmenting", False)
        )
        prepared_buffers: list[list[str]] = []
        if llm_controlled:
            raw_buffers: list[str] = []
            plain_buffer: list[str] = []

            def flush_plain_buffer() -> None:
                if not plain_buffer:
                    return
                raw_text = "".join(plain_buffer).strip()
                plain_buffer.clear()
                if raw_text:
                    raw_buffers.append(raw_text)

            for component in working_chain:
                if self._is_reply_component(component):
                    continue
                if isinstance(component, Plain):
                    plain_buffer.append(str(getattr(component, "text", "") or ""))
                    continue
                flush_plain_buffer()
            flush_plain_buffer()
            prepared_buffers = self._split_llm_controlled_text_buffers_for_event(
                event,
                raw_buffers,
            )
        prepared_iter = iter(prepared_buffers)

        def split_text_buffer(text: str) -> list[str]:
            if llm_controlled:
                try:
                    return next(prepared_iter)
                except StopIteration:
                    return [str(text or "").strip()]
            return self._split_proactive_text(text, event=event)

        chunks, changed, _split_changed, full_text = plan_component_chunks(
            working_chain,
            plain_type=Plain,
            split_text=split_text_buffer,
            strategies=component_strategies_from_owner(self),
            component_order=component_order_from_owner(self),
            classify=component_kind,
        )
        if not full_text:
            return [], False, ""
        full_text = sanitize_llm_segment_control_tokens(full_text)
        final_chunks = self._clean_segmented_reply_chunks(event, chunks) if changed else [chain]
        history_segments = getattr(
            event,
            "_private_companion_llm_history_segments",
            (),
        )
        if isinstance(history_segments, tuple) and len(history_segments) >= 2:
            planned_texts = [
                self._plain_result_body_text(chunk)
                for chunk in final_chunks
            ]
            planned_ids = getattr(
                event,
                "_private_companion_llm_planned_segment_ids",
                (),
            )
            if (
                planned_texts
                and all(planned_texts)
                and isinstance(planned_ids, tuple)
                and len(planned_ids) == len(planned_texts)
            ):
                setattr(
                    event,
                    "_private_companion_llm_planned_chunk_texts",
                    tuple(planned_texts),
                )
            else:
                for attr_name in (
                    "_private_companion_llm_history_segments",
                    "_private_companion_llm_planned_segment_ids",
                ):
                    try:
                        delattr(event, attr_name)
                    except AttributeError:
                        pass
        return final_chunks, changed, full_text

    @staticmethod
    def _event_can_deliver_directly(event: AstrMessageEvent) -> bool:
        """判断 ``event.send()`` 是否真的会把消息投递到平台。

        AstrBot 基类 ``AstrMessageEvent.send()`` 是空实现：只上传一次埋点、
        设置 ``_has_send_oper`` 标志位，既不发送也不抛异常；只有平台适配器子类
        才重写它。外部插件（例如屏幕伴侣）会自行构造基类合成事件来触发
        ``OnDecoratingResultEvent``，这类事件调用 ``send()`` 会静默丢弃消息，
        必须改走平台直发。

        返回 True 表示可以安全使用 ``event.send()``。
        """
        try:
            # 本项目导入的 AstrMessageEvent 就是基类本体
            # （astrbot.api.event → astrbot.core.platform → astr_message_event）。
            return type(event).send is not AstrMessageEvent.send
        except Exception:
            # 判定失败时保持原行为，避免误伤正常链路
            return True

    async def _send_segmented_remainder_chain(
        self,
        event: AstrMessageEvent,
        chain: list[Any],
    ) -> str:
        """Send delayed chunks through a live platform route when the source event is proactive."""
        external_proactive = (
            str(getattr(event, "_private_companion_external_proactive_source", "") or "")
            == "proactive_chat"
        )
        proactive_delivery_umo = _single_line(
            getattr(event, "_private_companion_proactive_delivery_umo", ""),
            240,
        )
        if (
            external_proactive
            or proactive_delivery_umo
            or not self._event_can_deliver_directly(event)
        ):
            umo = proactive_delivery_umo or _single_line(
                getattr(event, "unified_msg_origin", ""),
                240,
            )
            sender = getattr(self, "_send_chain_components", None)
            if not umo or not callable(sender):
                raise RuntimeError("主动分段补发缺少可用的平台发送入口")
            accepted = await sender(
                umo,
                list(chain),
                apply_decorating_hooks=False,
            )
            if not accepted:
                raise RuntimeError("主动分段补发未被平台接受")
            return "platform"
        markdown_mode = getattr(
            event,
            "_private_companion_segmented_markdown_mode",
            None,
        )
        if markdown_mode is not None:
            await event.send(self._segmented_result_from_chain(event, chain))
        else:
            try:
                await event.send(event.chain_result(chain))
            except Exception:
                await event.send(self._build_result_from_chain(chain))
        return "event"
