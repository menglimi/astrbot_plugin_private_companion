# -*- coding: utf-8 -*-
"""EventDispatchResultProactivePart01Mixin。

由 tools/split_mixin_domain.py 从 event_dispatch_result_proactive.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 304 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 EventDispatchResultProactiveMixin）。
"""
from __future__ import annotations

from .event_dispatch_result_proactive_shared import _SEGMENTED_PROTECTED_LITERAL_PATTERN
from .event_dispatch_result_proactive_shared import Any
from .event_dispatch_result_proactive_shared import AstrMessageEvent
from .event_dispatch_result_proactive_shared import Plain
from .event_dispatch_result_proactive_shared import TextPart
from .event_dispatch_result_proactive_shared import _persona_value
from .event_dispatch_result_proactive_shared import _redact_outbound_secrets
from .event_dispatch_result_proactive_shared import _single_line
from .event_dispatch_result_proactive_shared import re
from .event_dispatch_result_proactive_shared import runtime_persona_setting



class EventDispatchResultProactivePart01Mixin:
    """EventDispatchResultProactivePart01Mixin（从 EventDispatchResultProactiveMixin 拆出）。"""


    def _build_result_from_chain(
        self,
        chain: list[Any],
        *,
        source_result: Any | None = None,
    ) -> Any:
        """Build a result while retaining producer metadata when transforming it.

        Decorating hooks may replace only the chain (for example, the
        segmented-reply hook).  AstrBot stores the LLM/non-LLM distinction on
        ``result_content_type`` rather than on the components themselves.  A
        replacement must therefore copy that metadata from the result it is
        transforming; callers that create an independent plugin reply keep
        the framework default.
        """
        try:
            from astrbot.api.event import MessageEventResult
        except ImportError:
            from astrbot.core.message.message_event_result import MessageEventResult
        prefer_chain_result = self._chain_has_media_component(chain)
        if prefer_chain_result:
            try:
                result = MessageEventResult().chain_result(chain)
            except Exception:
                try:
                    result = MessageEventResult(chain=chain)
                except TypeError:
                    result = MessageEventResult().chain_result(chain)
        else:
            try:
                result = MessageEventResult(chain=chain)
            except TypeError:
                result = MessageEventResult().chain_result(chain)
        if source_result is not None:
            for attr in ("result_content_type", "use_t2i_", "use_markdown_"):
                if not hasattr(source_result, attr) or not hasattr(result, attr):
                    continue
                try:
                    setattr(result, attr, getattr(source_result, attr))
                except Exception:
                    pass
        result = self._disable_result_t2i(result)
        # Decorating hooks are global in AstrBot. Keep an explicit ownership
        # marker on results built by this plugin so its optional segmentation
        # stage cannot rewrite another plugin's plain-text result.
        try:
            setattr(result, "_private_companion_owned_result", True)
        except Exception:
            pass
        return result

    def _suppress_outbound_reply(
        self,
        event: AstrMessageEvent,
        *,
        source: str,
        reason: str,
        replacement_text: str | None = None,
        history_note: str | None = None,
        detail: str = "",
        level: str = "info",
    ) -> None:
        """Suppress one outbound result without stopping the enclosing turn."""
        result = event.get_result()
        original = ""
        if result is not None:
            original = "\n".join(
                component.text
                for component in result.chain
                if isinstance(component, Plain)
            ).strip()
        if replacement_text is not None:
            event.set_result(self._build_result_from_chain([Plain(str(replacement_text))]))
        elif result is not None:
            result.chain = []
        else:
            event.set_result(self._build_result_from_chain([]))
        assistant = getattr(event, "_private_companion_official_assistant_message", None)
        if replacement_text is None and assistant is not None:
            assistant.content = [TextPart(text=history_note or f"[本轮未发送：{reason}]")]
            assistant.tool_calls = None
            assistant.tool_call_id = None
            assistant._no_save = False
        setattr(event, "_private_companion_outbound_suppressed", True)
        self._record_passive_no_reply(
            event,
            source=source,
            reason=reason,
            detail=detail,
            reply_preview=_redact_outbound_secrets(original)[:160],
            level=level,
        )

    def _build_segmented_result_from_chain(
        self,
        chain: list[Any],
        source_result: Any,
    ) -> Any:
        """Rebuild a segmented result while tolerating legacy overrides.

        Some integrations and tests replace ``_build_result_from_chain`` with
        the historical one-argument callable. Keep that extension point
        working while still preserving the source result's LLM metadata when
        the native builder supports it.
        """
        builder = self._build_result_from_chain
        try:
            return builder(chain, source_result=source_result)
        except TypeError as exc:
            if "source_result" not in str(exc):
                raise
            rebuilt = builder(chain)
            for attr in ("result_content_type", "use_t2i_", "use_markdown_"):
                if not hasattr(source_result, attr) or not hasattr(rebuilt, attr):
                    continue
                try:
                    setattr(rebuilt, attr, getattr(source_result, attr))
                except Exception:
                    pass
            return rebuilt

    def _disable_result_t2i(self, result: Any) -> Any:
        if result is None:
            return result
        try:
            if hasattr(result, "use_t2i"):
                result = result.use_t2i(False)
            elif hasattr(result, "use_t2i_"):
                result.use_t2i_ = False
        except Exception:
            pass
        return result

    def _is_silent_control_reply_text(self, text: str) -> bool:
        cleaned = _single_line(text, 160)
        if not cleaned:
            return False
        stripped = re.sub(r"^[\s\(\（\[\【]+|[\s\)\）\]\】。.!！?？]+$", "", cleaned).strip()
        compact = re.sub(r"\s+", "", stripped)
        if not compact:
            return False
        no_reply_markers = ("不回复", "无需回复", "不要回复", "不用回复", "别回复", "静默", "忽略")
        empty_reply_markers = {"空字符串", "空内容", "留空", "null", "none", "nil", "n/a"}
        context_markers = ("群友之间", "群友互动", "群聊背景", "不是对你", "不需要接话", "无需接话", "不要接话")
        if compact in empty_reply_markers:
            return True
        if "空字符串" in compact and len(compact) <= 16:
            return True
        if any(marker in compact for marker in no_reply_markers) and any(marker in compact for marker in context_markers):
            return True
        if compact in {"不回复", "无需回复", "不要回复", "不用回复", "别回复", "静默", "忽略"}:
            return True
        if compact.startswith("不回复") and len(compact) <= 28:
            return True
        return False

    def _legacy_event_dispatch_proactive_delivery_receipt_text(self, text: str) -> bool:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return True
        compact = re.sub(r"\s+", "", cleaned)
        if compact in {
            "我主动开口了。",
            "我主动开口了",
            "我主动发了一段语音。",
            "我主动发了一段语音",
            "我主动分享了一点东西。",
            "我主动分享了一点东西",
            "我主动做了一次小互动。",
            "我主动做了一次小互动",
        }:
            return True
        if re.fullmatch(r"(?:图|图片|照片)(?:好|好了|生成好了|出来了|完成了)[啦了~～。!！]*", cleaned):
            return True
        if re.fullmatch(r"(?:生图|出图|图片生成)(?:完成|好了|成功)[啦了~～。!！]*", cleaned):
            return True
        if re.search(r"(?:还在|正在|继续)?(?:排队|队列|等待生成|等图|等图片|等它出图)", cleaned):
            return True
        if re.match(r"^(?:已经|已)(?:发|发送)过去[啦了]?[，,。!！~～\s]*(?:等(?:着|他|你|对方)|等回复|等回我)?.*$", cleaned):
            return True
        if re.match(r"^等(?:着)?(?:他|你|对方)?回(?:我|复)?[啦了~～。!！]*$", cleaned):
            return True
        if re.match(r"^消息已送达[，,。]?", cleaned):
            return True
        if re.match(r"^这是[^。！？\n]{0,80}(?:发的|发送的|收到的)[^。！？\n]{0,80}(?:消息|打招呼|问候|回复)", cleaned):
            return True
        if re.match(r"^这(?:条|是)[^。！？\n]{0,80}(?:语气|内容|消息)[^。！？\n]{0,80}$", cleaned):
            return True
        if "消息已送达" in cleaned and "收到了" in cleaned:
            return True
        return False

    @staticmethod
    def _decode_segmented_replacement_token(value: Any, *, replacement: bool = False) -> str:
        raw = str(value if value is not None else "")
        trimmed = raw.strip()
        lowered = trimmed.lower()
        aliases = {
            "<space>": " ",
            "{space}": " ",
            "[space]": " ",
            "空格": " ",
            "<newline>": "\n",
            "{newline}": "\n",
            "[newline]": "\n",
            "换行": "\n",
            "<tab>": "\t",
            "{tab}": "\t",
            "[tab]": "\t",
            "tab": "\t",
            "<empty>": "",
            "{empty}": "",
            "[empty]": "",
            "空内容": "",
            "删除": "",
        }
        if lowered in aliases and (replacement or lowered not in {"<empty>", "{empty}", "[empty]", "空内容", "删除"}):
            return aliases[lowered]
        return trimmed.replace("\\n", "\n").replace("\\t", "\t")

    def _segmented_content_replacement_pairs(
        self,
        *,
        event: Any | None = None,
        umo: str = "",
        chat_type: str = "",
    ) -> list[tuple[str, str]]:
        setting_getter = getattr(self, "_segmented_setting", None)
        if callable(setting_getter):
            raw_rules = setting_getter(
                "content_replacements",
                event=event,
                umo=umo,
                chat_type=chat_type,
                default=[],
            )
        else:
            raw_rules = _persona_value(self, 'segmented_proactive_content_replacements', [])
        if isinstance(raw_rules, str):
            rules: list[Any] = [line for line in raw_rules.splitlines() if line.strip()]
        elif isinstance(raw_rules, list):
            rules = list(raw_rules)
        else:
            rules = []
        pairs: list[tuple[str, str]] = []
        for raw_rule in rules[:80]:
            old_value: Any = ""
            new_value: Any = ""
            if isinstance(raw_rule, dict):
                old_value = raw_rule.get("from", raw_rule.get("old", raw_rule.get("source", "")))
                new_value = raw_rule.get("to", raw_rule.get("new", raw_rule.get("replacement", "")))
            else:
                rule_text = str(raw_rule or "")
                separator = next(
                    (item for item in ("=>", "＝>", "→", "->") if item in rule_text),
                    "",
                )
                if not separator:
                    continue
                old_value, new_value = rule_text.split(separator, 1)
            old_text = self._decode_segmented_replacement_token(old_value)
            new_text = self._decode_segmented_replacement_token(new_value, replacement=True)
            if not old_text or len(old_text) > 200 or len(new_text) > 500:
                continue
            pairs.append((old_text, new_text))
        return pairs

    def _apply_segmented_content_replacements(
        self,
        text: Any,
        *,
        event: Any | None = None,
        umo: str = "",
        chat_type: str = "",
    ) -> tuple[str, int]:
        original = str(text or "")
        enabled = bool(
            runtime_persona_setting(
                self,
                "enable_segmented_proactive_content_replacement",
                False,
            )
        )
        if not original or not enabled:
            return original, 0
        pairs = self._segmented_content_replacement_pairs(
            event=event,
            umo=umo,
            chat_type=chat_type,
        )
        if not pairs:
            return original, 0
        parts: list[str] = []
        cursor = 0
        replacements = 0
        for match in _SEGMENTED_PROTECTED_LITERAL_PATTERN.finditer(original):
            plain = original[cursor:match.start()]
            for old_text, new_text in pairs:
                count = plain.count(old_text)
                if count:
                    plain = plain.replace(old_text, new_text)
                    replacements += count
            parts.extend((plain, match.group(0)))
            cursor = match.end()
        plain = original[cursor:]
        for old_text, new_text in pairs:
            count = plain.count(old_text)
            if count:
                plain = plain.replace(old_text, new_text)
                replacements += count
        parts.append(plain)
        return "".join(parts), replacements
