# -*- coding: utf-8 -*-
"""EventDispatchSmartDebounceMixin。

由 tools/split_mixin_domain.py 从 event_dispatch.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 741 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 EventDispatchMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .event_dispatch_shared import _persona_value, logger
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from astrbot.api.event import AstrMessageEvent
from astrbot.core.provider.entities import LLMResponse
from typing import Any



class EventDispatchSmartDebounceMixin:
    """EventDispatchSmartDebounceMixin（从 EventDispatchMixin 拆出）。"""


    async def guard_pending_message_debounce(self, event: AstrMessageEvent, *args: Any, **kwargs: Any) -> None:
        """在会话锁前收口补话，避免旧回复与新消息并发出站。"""
        if bool(getattr(event, "private_companion_debounce_pending_merged", False)):
            return
        try:
            if not bool(event.is_private_chat()):
                scene = getattr(event, "private_companion_group_scene", None)
                high_intensity = getattr(event, "private_companion_group_high_intensity", None)
                if (
                    not isinstance(scene, dict)
                    or str(scene.get("talking_to") or "") != "bot"
                    or (isinstance(high_intensity, dict) and high_intensity.get("merge_active"))
                ):
                    return
        except Exception:
            return
        text = _single_line(getattr(event, "message_str", ""), 260)
        if text:
            self._message_debounce_absorb_pending_message(event, text)

    async def settle_pending_message_debounce(self, event: AstrMessageEvent, resp: LLMResponse, *args: Any, **kwargs: Any) -> None:
        key = self._message_debounce_pending_key(event)
        if not key:
            return
        store = self._message_debounce_pending_store()
        pending = store.get(key)
        if not isinstance(pending, dict):
            return
        message_id = self._message_debounce_event_id(event)
        stale_ids = pending.get("stale_event_ids") if isinstance(pending.get("stale_event_ids"), list) else []
        if message_id and message_id in stale_ids:
            stale_ids.remove(message_id)
            try:
                resp.completion_text = ""
            except Exception:
                pass
            try:
                # Some AstrBot versions expose a parallel result chain; clear
                # it as well so an expired response cannot leak a second path.
                resp.result_chain = None
            except Exception:
                pass
            pending["updated_ts"] = _now_ts()
            logger.info(
                "已丢弃消息收口中过期 LLM 回复: key=%s event=%s",
                key,
                message_id,
            )
            return
        active_id = _single_line(pending.get("event_id"), 120)
        if not active_id or active_id == message_id:
            store.pop(key, None)

    def _smart_message_debounce_store(self) -> dict[str, Any]:
        store = self.data.setdefault("smart_message_debounce", {})
        if not isinstance(store, dict):
            store = {}
            self.data["smart_message_debounce"] = store
        store.setdefault("last_decisions", {})
        store.setdefault("examples", [])
        store.setdefault("recent_logs", [])
        return store

    def _record_smart_message_debounce_log(
        self,
        *,
        scope: str,
        sender_id: str,
        text: str = "",
        decision: str = "",
        confidence: float = 0.0,
        reason: str = "",
        wait_seconds: float = 0.0,
        outcome: str = "",
        note: str = "",
        source: str = "model",
        raw: str = "",
        message_count: int = 0,
        private_chat: bool | None = None,
    ) -> None:
        store = self._smart_message_debounce_store()
        logs = store.setdefault("recent_logs", [])
        if not isinstance(logs, list):
            logs = []
            store["recent_logs"] = logs
        logs.append(
            {
                "ts": _now_ts(),
                "scope": _single_line(scope, 80),
                "sender_id": _single_line(sender_id, 40),
                "text": _single_line(text, 180),
                "decision": _single_line(decision, 40),
                "confidence": max(0.0, min(1.0, float(confidence or 0.0))),
                "reason": _single_line(reason, 120),
                "wait_seconds": max(0.0, float(wait_seconds or 0.0)),
                "outcome": _single_line(outcome, 40),
                "note": _single_line(note, 160),
                "source": _single_line(source, 40),
                "raw": _single_line(raw, 180),
                "message_count": max(0, _safe_int(message_count, 0, 0)),
                "chat": "private" if private_chat is True else "group" if private_chat is False else "",
            }
        )
        del logs[:-80]

    def _smart_message_debounce_examples(self) -> list[dict[str, Any]]:
        if not self._smart_message_debounce_enabled():
            return []
        limit = max(0, _safe_int(_persona_value(self, 'smart_message_debounce_examples_limit', 8), 8, 0))
        if limit <= 0:
            return []
        store = self._smart_message_debounce_store()
        examples = store.get("examples") if isinstance(store.get("examples"), list) else []
        return [item for item in examples[-limit:] if isinstance(item, dict)]

    def _record_smart_message_debounce_example(
        self,
        *,
        kind: str,
        scope: str,
        sender_id: str,
        messages: list[str],
        previous_decision: str = "",
        note: str = "",
    ) -> bool:
        if not self._smart_message_debounce_enabled():
            return False
        cleaned = [_single_line(item, 160) for item in messages if _single_line(item, 160)]
        if not cleaned:
            return False
        store = self._smart_message_debounce_store()
        examples = store.setdefault("examples", [])
        if not isinstance(examples, list):
            examples = []
            store["examples"] = examples
        signature = hashlib.sha1("\n".join([kind, scope, sender_id, *cleaned]).encode("utf-8", errors="ignore")).hexdigest()
        if any(isinstance(item, dict) and item.get("sig") == signature for item in examples[-20:]):
            return False
        examples.append(
            {
                "sig": signature,
                "ts": _now_ts(),
                "kind": _single_line(kind, 40),
                "scope": _single_line(scope, 80),
                "sender_id": _single_line(sender_id, 40),
                "messages": cleaned[:4],
                "previous_decision": _single_line(previous_decision, 40),
                "note": _single_line(note, 120),
            }
        )
        del examples[:- max(20, _safe_int(_persona_value(self, 'smart_message_debounce_examples_limit', 8), 8, 0) * 4 or 20)]
        logger.info(
            "智能防抖学习样本已记录: kind=%s scope=%s messages=%s note=%s",
            kind,
            scope,
            len(cleaned),
            _single_line(note, 80),
        )
        self._record_smart_message_debounce_log(
            scope=scope,
            sender_id=sender_id,
            text=" / ".join(cleaned[:3]),
            decision=previous_decision,
            outcome="learned",
            note=note,
            source=kind,
            message_count=len(cleaned),
        )
        return True

    def _remember_smart_message_debounce_decision(
        self,
        *,
        scope: str,
        sender_id: str,
        text: str,
        decision: str,
        confidence: float = 0.0,
        reason: str = "",
    ) -> None:
        if not self._smart_message_debounce_enabled():
            return
        data = getattr(self, "data", None)
        store = data.get("smart_message_debounce") if isinstance(data, dict) else None
        if not isinstance(store, dict):
            return
        last = store.setdefault("last_decisions", {})
        if not isinstance(last, dict):
            last = {}
            store["last_decisions"] = last
        key = self._semantic_buffer_key(scope, sender_id)
        last[key] = {
            "ts": _now_ts(),
            "scope": _single_line(scope, 80),
            "sender_id": _single_line(sender_id, 40),
            "text": _single_line(text, 180),
            "decision": _single_line(decision, 40),
            "confidence": max(0.0, min(1.0, float(confidence or 0.0))),
            "reason": _single_line(reason, 120),
        }
        if len(last) > 200:
            for item_key, _ in sorted(last.items(), key=lambda item: _safe_float(item[1].get("ts"), 0) if isinstance(item[1], dict) else 0)[:40]:
                last.pop(item_key, None)

    def _maybe_record_smart_message_debounce_followup(
        self,
        *,
        scope: str,
        sender_id: str,
        text: str,
        now: float | None = None,
    ) -> bool:
        if not self._smart_message_debounce_enabled():
            return False
        data = getattr(self, "data", None)
        store = data.get("smart_message_debounce") if isinstance(data, dict) else None
        if not isinstance(store, dict):
            return False
        last = store.get("last_decisions") if isinstance(store.get("last_decisions"), dict) else {}
        key = self._semantic_buffer_key(scope, sender_id)
        previous = last.get(key) if isinstance(last, dict) else None
        if not isinstance(previous, dict):
            return False
        if str(previous.get("decision") or "") != "complete":
            return False
        now = now or _now_ts()
        window = max(1.0, _safe_float(_persona_value(self, "smart_message_debounce_learning_window_seconds", 8.0), 8.0, 1.0))
        if now - _safe_float(previous.get("ts"), 0) > window:
            return False
        return self._record_smart_message_debounce_example(
            kind="false_complete",
            scope=scope,
            sender_id=sender_id,
            messages=[str(previous.get("text") or ""), text],
            previous_decision="complete",
            note="模型判断已说完后,用户很快继续补充。",
        )

    def _smart_message_debounce_heuristic_incomplete(self, text: str) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        if self._smart_message_debounce_suspense_intro_reason(cleaned):
            return True
        if re.search(r"(等下|等一下|稍等|我想想|我组织下|先别回|等等|还有|另外|然后|接着|顺便|就是)$", cleaned):
            return True
        if re.search(r"[,，、:：;；]$", cleaned):
            return True
        return False

    def _smart_message_debounce_suspense_intro_reason(self, text: str) -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        compact = re.sub(r"\s+", "", cleaned)
        compact = re.sub(r"[?？。.!！~～…]+$", "", compact)
        setting_getter = getattr(self, "persona_setting", None)
        bot_name = re.sub(r"\s+", "", str(setting_getter("bot_name", "") if callable(setting_getter) else _persona_value(self, 'bot_name', "") or ""))
        if bot_name and compact.startswith(bot_name) and len(compact) > len(bot_name):
            addressed_tail = compact[len(bot_name):].lstrip("，,、:： ")
            if re.fullmatch(r"(你)?知道(吗|嘛|么|不|吧)?", addressed_tail):
                return ""
            if re.fullmatch(r"(你)?(懂|明白|晓得|听懂)(吗|嘛|么|不)?", addressed_tail):
                return ""
            compact = addressed_tail
        if not compact or len(compact) > 10:
            return ""
        if re.fullmatch(r"(你)?知道(吗|嘛|么|不|吧)?", compact):
            return "悬念式问句"
        if re.fullmatch(r"(你)?(懂|明白|晓得|听懂)(吗|嘛|么|不)?", compact):
            return "确认式铺垫"
        if re.fullmatch(r"(你)?猜(猜)?(看|呢|嘛|吗|么)?", compact):
            return "猜测式铺垫"
        if re.fullmatch(r"(我)?(跟|和)?你说(个事|一下|哦|嗷|哈)?", compact):
            return "说话起手式"
        if re.fullmatch(r"(问|说)(你)?个事", compact):
            return "说话起手式"
        return ""

    def _smart_message_debounce_fast_complete_reason(self, text: str) -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return "空文本"
        compact = re.sub(r"\s+", "", cleaned)
        if self._smart_message_debounce_suspense_intro_reason(compact):
            return ""
        if re.search(r"[?？。.!！]$", compact):
            return "句末完整标点"
        if re.search(r"^(其实)?我(是|叫|就是).{1,24}$", compact):
            return "完整身份陈述"
        if re.search(r"(吗|么|嘛|呢|呀|啊|谁|什么|怎么|咋|为何|为什么|哪[个里儿]?|多少|几|是不是|能不能|可不可以|行不行)[?？]?$", compact):
            return "完整疑问句"
        if re.search(r"(摸摸|贴贴|抱抱|亲亲|捏捏|早安|晚安|早上好|晚上好|你好|在吗|谢谢|好呀|好的|嗯嗯|哈哈|草|笑死)[~～。.!！]?$", compact):
            return "短互动完整"
        if len(compact) <= 8 and not self._smart_message_debounce_heuristic_incomplete(compact):
            return "短句完整"
        if not self._smart_message_debounce_heuristic_incomplete(compact):
            return "未命中补话特征"
        return ""

    def _group_short_wakeup_wait_seconds(
        self,
        event: AstrMessageEvent,
        text: str,
        *,
        smart_result: dict[str, Any] | None = None,
    ) -> float:
        if not bool(_persona_value(self, "enable_group_wakeup_enhancement", True)):
            return 0.0
        wait = max(0.0, min(30.0, _safe_float(_persona_value(self, "group_wakeup_short_text_wait_seconds", 0.0), 0.0, 0.0)))
        if wait <= 0:
            return 0.0
        cleaned = _single_line(text, 80)
        compact = re.sub(r"\s+", "", cleaned)
        if not compact or len(compact) > 2:
            return 0.0
        if re.search(r"[?？。.!！~～…]$", compact):
            return 0.0
        if re.fullmatch(r"(好+|嗯+|哦+|啊+|哈+|草+|在|早|晚|是|不|行|可|对|谢谢|谢了)", compact):
            return 0.0
        scene = getattr(event, "private_companion_group_scene", None)
        if not isinstance(scene, dict) or str(scene.get("talking_to") or "") != "bot":
            return 0.0
        trigger = str(scene.get("trigger") or "")
        if trigger not in {
            "at_bot",
            "reply_bot",
            "mention_bot_name",
            "group_wakeup_direct_word",
            "group_wakeup_context_word",
            "group_wakeup_interest",
            "group_wakeup_question",
            "group_wakeup_cold_group",
            "bot_conversation_followup",
        }:
            return 0.0
        decision = str((smart_result or {}).get("decision") or "")
        reason = str((smart_result or {}).get("reason") or "")
        if decision and decision != "complete":
            return 0.0
        if reason and reason not in {"短句完整", "未命中补话特征"}:
            return 0.0
        try:
            setattr(
                event,
                "private_companion_smart_message_debounce_result",
                {
                    "decision": "incomplete",
                    "wait_seconds": wait,
                    "original_wait_seconds": _safe_float((smart_result or {}).get("original_wait_seconds"), 0.0, 0.0),
                    "elapsed_ms": _safe_int((smart_result or {}).get("elapsed_ms"), 0, 0),
                    "source": "group_short_wakeup",
                    "reason": "群聊短唤醒等待补话",
                },
            )
        except Exception:
            pass
        logger.info(
            "群聊短唤醒进入补话等待: wait=%.1fs trigger=%s text=%s",
            wait,
            trigger,
            _single_line(cleaned, 40),
        )
        return wait

    def _parse_smart_message_debounce_decision(self, raw: str) -> tuple[str, float, str]:
        text = str(raw or "").strip()
        if not text:
            return "complete", 0.0, "empty"
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                decision = str(data.get("decision") or data.get("status") or "").strip().lower()
                confidence = float(data.get("confidence") or 0)
                reason = _single_line(data.get("reason"), 120)
                if decision in {"incomplete", "wait", "continue", "unfinished", "未说完", "等待"}:
                    return "incomplete", max(0.0, min(1.0, confidence)), reason
                if decision in {"complete", "done", "reply", "finished", "已说完", "回复"}:
                    return "complete", max(0.0, min(1.0, confidence)), reason
            except Exception:
                pass
        upper = text.upper()
        if upper.startswith("INCOMPLETE") or text.startswith(("未说完", "等待", "继续")):
            return "incomplete", 0.7, text[:80]
        return "complete", 0.6, text[:80]

    @staticmethod
    def _smart_message_debounce_prompt_section(
        *,
        private_chat: bool,
        sender_name: str,
        sender_id: str,
        cleaned: str,
        recent: list[Any],
        example_lines: list[str],
    ) -> PromptSection:
        return prompt_section(
            key="background.smart_message_debounce",
            title="消息完整性判断",
            source="event_dispatch",
            template=(
                "判断用户当前这句话是否明显还没说完，需要 Bot 等一小会儿再回复。\n\n"
                '只输出 JSON：{{"decision":"complete|incomplete","confidence":0-1,"reason":"不超过20字"}}\n\n'
                "会话类型：{conversation_type}\n"
                "用户：{sender}\n"
                "当前消息：{message}\n"
                "缓冲中的前文：{recent}\n\n"
                "已学习的误判样本：\n{examples}\n\n"
                "判断规则：\n"
                "- “知道吗/你知道吗/懂吗/明白吗/猜猜/问你个事/跟你说”这类短引子通常是在铺垫下一句，倾向 incomplete。\n"
                "- 如果用户像是在起手、列举、转折、说“等下/还有/然后/我想想”、句子停在逗号冒号分号，倾向 incomplete。\n"
                "- 如果是完整问题、完整请求、完整情绪表达、问候、贴贴、摸摸、表情或短回复，倾向 complete。\n"
                "- 不要因为消息短就等待；只有真的像还会补一句才 incomplete。\n"
                "- 宁可少等，也不要让正常对话变慢。"
            ),
            variables={
                "conversation_type": "私聊" if private_chat else "群聊",
                "sender": _single_line(sender_name, 40) or _single_line(sender_id, 40),
                "message": cleaned,
                "recent": " / ".join(recent[-3:]) if recent else "无",
                "examples": "\n".join(example_lines) or "无",
            },
        )

    async def _smart_message_debounce_wait_seconds_for_event(
        self,
        event: AstrMessageEvent,
        *,
        key: str,
        text: str,
        sender_id: str,
        sender_name: str = "",
        private_chat: bool = True,
    ) -> float:
        def _set_smart_result(
            decision: str,
            *,
            wait_seconds: float = 0.0,
            original_wait_seconds: float = 0.0,
            elapsed_ms: int = 0,
            source: str = "",
            reason: str = "",
        ) -> None:
            try:
                setattr(
                    event,
                    "private_companion_smart_message_debounce_result",
                    {
                        "decision": _single_line(decision, 40),
                        "wait_seconds": max(0.0, float(wait_seconds or 0.0)),
                        "original_wait_seconds": max(0.0, float(original_wait_seconds or 0.0)),
                        "elapsed_ms": max(0, int(elapsed_ms or 0)),
                        "source": _single_line(source, 40),
                        "reason": _single_line(reason, 120),
                    },
                )
            except Exception:
                pass

        if not self._smart_message_debounce_enabled():
            return 0.0
        started_at = time.perf_counter()
        scope = key.rsplit(":", 1)[0]
        cleaned = _single_line(text, 260)
        if not cleaned:
            return 0.0
        wait = max(0.0, min(15.0, _safe_float(_persona_value(self, "smart_message_debounce_wait_seconds", 3.0), 3.0, 0.0)))
        if wait <= 0:
            return 0.0
        # Fast-rule decisions also need a durable section before they record
        # ``last_decisions``; otherwise the first decision after startup can
        # be lost because the log path initializes the store only afterward.
        self._smart_message_debounce_store()
        buffers = getattr(self, "_semantic_message_buffers", None)
        existing = buffers.get(key) if isinstance(buffers, dict) else None
        if isinstance(existing, dict):
            self._record_smart_message_debounce_log(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="incomplete",
                confidence=1.0,
                reason="已有收口缓冲",
                wait_seconds=wait,
                outcome="extend_wait",
                note="同一用户已有等待中的消息,继续合并补话。",
                source="buffer",
                private_chat=private_chat,
            )
            logger.info(
                "智能防抖沿用等待缓冲: scope=%s sender=%s wait=%.1fs text=%s",
                scope,
                sender_id,
                wait,
                _single_line(cleaned, 80),
            )
            _set_smart_result("incomplete", wait_seconds=wait, original_wait_seconds=wait, source="buffer", reason="已有收口缓冲")
            return wait
        suspense_reason = self._smart_message_debounce_suspense_intro_reason(cleaned)
        if suspense_reason:
            self._remember_smart_message_debounce_decision(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="incomplete",
                confidence=0.9,
                reason=suspense_reason,
            )
            self._record_smart_message_debounce_log(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="incomplete",
                confidence=0.9,
                reason=suspense_reason,
                wait_seconds=wait,
                outcome="wait",
                note="短引子常用于铺垫下一句,直接等待补话。",
                source="fast_rule",
                private_chat=private_chat,
            )
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            _set_smart_result(
                "incomplete",
                wait_seconds=wait,
                original_wait_seconds=wait,
                elapsed_ms=elapsed_ms,
                source="fast_rule",
                reason=suspense_reason,
            )
            logger.info(
                "智能防抖本地判定等待补话: scope=%s sender=%s elapsed=%sms reason=%s wait=%.1fs text=%s",
                scope,
                sender_id,
                elapsed_ms,
                _single_line(suspense_reason, 80),
                wait,
                _single_line(cleaned, 80),
            )
            return wait
        fast_complete_reason = self._smart_message_debounce_fast_complete_reason(cleaned)
        if fast_complete_reason:
            self._remember_smart_message_debounce_decision(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="complete",
                confidence=1.0,
                reason=fast_complete_reason,
            )
            self._record_smart_message_debounce_log(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="complete",
                confidence=1.0,
                reason=fast_complete_reason,
                wait_seconds=0.0,
                outcome="reply_now",
                note="本地快判已确认完整,不调用小模型。",
                source="fast_rule",
                private_chat=private_chat,
            )
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            _set_smart_result(
                "complete",
                wait_seconds=0.0,
                original_wait_seconds=wait,
                elapsed_ms=elapsed_ms,
                source="fast_rule",
                reason=fast_complete_reason,
            )
            logger.info(
                "智能防抖本地快判放行: scope=%s sender=%s elapsed=%sms reason=%s text=%s",
                scope,
                sender_id,
                elapsed_ms,
                _single_line(fast_complete_reason, 80),
                _single_line(cleaned, 80),
            )
            return 0.0
        examples = self._smart_message_debounce_examples()
        example_lines = []
        for item in examples[-8:]:
            messages = item.get("messages") if isinstance(item.get("messages"), list) else []
            if not messages:
                continue
            example_lines.append(
                f"- {item.get('kind')}: {' / '.join(_single_line(msg, 80) for msg in messages[:3])} => {item.get('note') or ''}"
            )
        recent = []
        try:
            snapshot = self._semantic_buffer_active_snapshot(key, force=True)
            recent = snapshot.get("texts", []) if isinstance(snapshot, dict) else []
        except Exception:
            recent = []
        prompt = render_prompt_sections(
            [self._smart_message_debounce_prompt_section(
                private_chat=private_chat,
                sender_name=sender_name,
                sender_id=sender_id,
                cleaned=cleaned,
                recent=recent,
                example_lines=example_lines,
            )],
            mode=PromptRenderMode.BODY_ONLY,
        )
        raw = ""
        model_error = ""
        timeout_seconds = max(0.2, min(5.0, _safe_float(_persona_value(self, 'smart_message_debounce_model_timeout_seconds', 0.8), 0.8, 0.2)))
        provider_selector = getattr(self, "_task_provider", None)
        configured_debounce_provider = _persona_value(
            self,
            "smart_message_debounce_provider_id",
            "",
        )
        configured_default_provider = _persona_value(self, "llm_provider_id", "")
        if callable(provider_selector):
            debounce_provider_id = provider_selector(
                configured_debounce_provider,
                configured_default_provider,
            )
        else:
            debounce_provider_id = str(
                configured_debounce_provider or configured_default_provider or ""
            )
        timeout_getter = getattr(self, "_model_timeout_seconds_for_call", None)
        timeout_override = (
            timeout_getter(
                task="smart_message_debounce",
                provider_id=debounce_provider_id,
                timeout_key="SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            )
            if callable(timeout_getter)
            else None
        )
        if timeout_override is not None:
            timeout_seconds = float(timeout_override)
        try:
            raw = await asyncio.wait_for(
                self._llm_call(
                    prompt,
                    max_tokens=80,
                    provider_id=debounce_provider_id or None,
                    task="smart_message_debounce",
                ),
                timeout=timeout_seconds,
            ) or ""
        except asyncio.TimeoutError:
            logger.warning(
                "智能防抖模型判断超时,使用启发式: scope=%s sender=%s timeout=%.1fs text=%s",
                scope,
                sender_id,
                timeout_seconds,
                _single_line(cleaned, 80),
            )
            model_error = f"timeout>{timeout_seconds:.1f}s"
        except Exception as exc:
            logger.warning("智能防抖模型判断失败,使用启发式: %s", _single_line(exc, 120))
            model_error = _single_line(exc, 120)
        decision, confidence, reason = self._parse_smart_message_debounce_decision(raw)
        source = "model" if raw else "heuristic"
        if not raw and self._smart_message_debounce_heuristic_incomplete(cleaned):
            decision, confidence, reason = "incomplete", 0.55, "启发式未说完"
            source = "heuristic"
        if decision != "incomplete":
            self._remember_smart_message_debounce_decision(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="complete",
                confidence=confidence,
                reason=reason,
            )
            self._record_smart_message_debounce_log(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="complete",
                confidence=confidence,
                reason=reason,
                wait_seconds=0.0,
                outcome="reply_now",
                note="判定用户已说完,不额外等待。",
                source=source,
                raw=raw or model_error,
                private_chat=private_chat,
            )
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            _set_smart_result(
                "complete",
                wait_seconds=0.0,
                original_wait_seconds=wait,
                elapsed_ms=elapsed_ms,
                source=source,
                reason=reason,
            )
            logger.info(
                "智能防抖判定放行: scope=%s sender=%s elapsed=%sms source=%s confidence=%.2f reason=%s text=%s",
                scope,
                sender_id,
                elapsed_ms,
                source,
                confidence,
                _single_line(reason, 80),
                _single_line(cleaned, 80),
            )
            return 0.0
        self._remember_smart_message_debounce_decision(
            scope=scope,
            sender_id=sender_id,
            text=cleaned,
            decision="incomplete",
            confidence=confidence,
            reason=reason,
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        elapsed_seconds = max(0.0, elapsed_ms / 1000.0)
        remaining_wait = max(0.0, wait - elapsed_seconds)
        self._record_smart_message_debounce_log(
            scope=scope,
            sender_id=sender_id,
            text=cleaned,
            decision="incomplete",
            confidence=confidence,
            reason=reason,
            wait_seconds=remaining_wait,
            outcome="wait",
            note=f"判定用户可能没说完,判断耗时计入防抖,剩余等待 {remaining_wait:.1f}s。",
            source=source,
            raw=raw or model_error,
            private_chat=private_chat,
        )
        _set_smart_result(
            "incomplete",
            wait_seconds=remaining_wait,
            original_wait_seconds=wait,
            elapsed_ms=elapsed_ms,
            source=source,
            reason=reason,
        )
        logger.info(
            "智能防抖判定等待补话: scope=%s sender=%s elapsed=%sms source=%s wait=%.1fs remaining=%.1fs confidence=%.2f reason=%s text=%s",
            scope,
            sender_id,
            elapsed_ms,
            source,
            wait,
            remaining_wait,
            confidence,
            _single_line(reason, 80),
            _single_line(cleaned, 80),
        )
        return remaining_wait
