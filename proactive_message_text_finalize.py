# -*- coding: utf-8 -*-
"""text_finalize 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（21 个方法 + 1 个模块级名字 + 0 个类级赋值 / 661 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import random
import re
from .helpers import (
    _normalize_outbound_punctuation_flow,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
)
from .persona_config import runtime_persona_setting
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



# 直接问用户的措辞。复用处：
#   _trim_performative_self_state_tail 的 asks_user 判据（有问句就不删状态尾巴）
#   _is_conversational_proactive_text 的对话式守卫（有问句就不收束状态清单）
_ASK_USER_PROACTIVE_PATTERN = re.compile(
    r"(你那边|你呢|你那儿|你那里|你现在|你今天|你还|你有没有|你要不要|你是不是)"
)


class ProactiveMessageTextFinalizeMixin:
    """text_finalize 域（从 ProactiveMessageMixin 拆出）。"""


    def _story_item_relevant_to_now(
        self,
        item: dict[str, Any],
        now_minutes: int,
        *,
        past_minutes: int = 90,
        future_minutes: int = 180,
    ) -> bool:
        start, end = self._parse_window_minutes(str(item.get("window") or ""))
        if start is None or end is None:
            return False
        candidates = [(start, end)]
        if end < start:
            candidates = [(start, end + 24 * 60), (start - 24 * 60, end)]
        for item_start, item_end in candidates:
            if item_end >= now_minutes - past_minutes and item_start <= now_minutes + future_minutes:
                return True
        return False

    def _format_plan_item_for_prompt(self, item: dict[str, Any] | None) -> str:
        if not isinstance(item, dict):
            return "（暂无）"
        phase = _single_line(item.get("temporal_phase"), 16).lower()
        eligibility = _single_line(item.get("fact_eligibility"), 48).lower()
        status = _single_line(item.get("status"), 24).lower()
        commitment = _single_line(item.get("commitment_level"), 24).lower()
        is_unverified_future = phase == "future" or (status in {"planned", "unknown", ""} and eligibility in {"", "none"})
        if is_unverified_future and commitment not in {"confirmed", "routine"}:
            return f"{_single_line(item.get('time'), 12)}｜临近时段可能有安排"
        if phase == "future" and status in {"planned", ""} and commitment in {"confirmed", "routine"}:
            label = _single_line(item.get("title") or item.get("activity"), 80)
            label = re.split(r"[，,。；;：:]", label, maxsplit=1)[0].strip()[:50]
            prefix = "按安排" if commitment == "confirmed" else "照平常"
            return "｜".join(part for part in (_single_line(item.get("time"), 12), f"{prefix}{label}") if part)
        parts = [
            str(item.get("time", "")).strip(),
            str(item.get("activity", "")).strip(),
            f"情绪：{item.get('mood', '')}".strip(),
        ]
        seed = _single_line(item.get("message_seed"), 120)
        if seed:
            parts.append(f"可分享碎片：{seed}")
        return "｜".join(part for part in parts if part)

    @staticmethod
    def _truncate_proactive_context(text: str, limit: int = 2600) -> str:
        source = str(text or "").strip()
        if len(source) <= limit:
            return source
        units = [item.strip() for item in re.split(r"(?<=[。！？!?])\s*|\n+", source) if item.strip()]
        kept: list[str] = []
        size = 0
        for unit in units:
            extra = len(unit) + (1 if kept else 0)
            if size + extra > limit:
                break
            kept.append(unit)
            size += extra
        if kept:
            return "\n".join(kept)
        return source[:limit].rstrip() + "…"

    @staticmethod
    def _truncate_proactive_text(text: str, limit: int = 260) -> str:
        source = str(text or "").strip()
        if len(source) <= limit:
            return source
        cut = source[:limit]
        boundaries = [cut.rfind(mark) for mark in "。！？!?…"]
        boundary = max(boundaries, default=-1)
        if boundary >= max(20, int(limit * 0.55)):
            return cut[: boundary + 1].rstrip()
        return cut.rstrip() + "…"

    def _sanitize_proactive_text(self, text: str) -> str:
        cleaned = str(text or "").strip()
        cleaned = _strip_internal_message_blocks(
            cleaned,
            enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)),
            tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
        )
        cleaned = cleaned.replace("[图片]", "").replace("【图片】", "")
        cleaned = cleaned.replace("（图片已送达）", "").replace("(图片已送达)", "")
        emotion_cleaner = getattr(self, "_strip_visible_tts_emotion_cues", None)
        if (
            bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))
            and bool(runtime_persona_setting(self, "enable_tts_enhancement", False))
            and callable(emotion_cleaner)
        ):
            cleaned = emotion_cleaner(cleaned)
        identity_cleaner = getattr(self, "_strip_internal_identity_anchors", None)
        if callable(identity_cleaner):
            cleaned = identity_cleaner(cleaned)
        cleaned = re.sub(r"^```(?:text)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip().strip('"').strip("'")
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        lines = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if re.fullmatch(r"[（(].{0,40}语音消息.{0,20}[)）]", line):
                continue
            if re.match(r"^(?:图片发过去了|希望他看到的时候|然后过了好一会儿)", line):
                continue
            if self._is_proactive_instruction_leak_text(line):
                continue
            if re.match(r"^[（(].{0,80}(?:翻了个身|裹紧了些|眼睛微微眯起来).*[）)]$", line):
                continue
            line = self._strip_parenthetical_stage_directions(line)
            if not line:
                continue
            lines.append(line)
        if not lines:
            return ""
        return self._truncate_proactive_text("\n".join(lines[:3]), 260)

    def _strip_parenthetical_stage_directions(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        stage_tokens = (
            "搅", "夹", "咬", "嚼", "喝", "抿", "吞", "放下", "拿起",
            "叹", "笑", "眨", "盯", "看", "望", "低头", "抬头", "偏头",
            "小声", "轻轻", "慢慢", "默默", "皱眉", "挑眉", "眯眼",
            "伸手", "缩", "靠", "蹭", "戳", "敲", "揉", "摸", "抱",
            "翻身", "裹", "坐", "站", "躺", "走", "晃", "顿了顿",
        )

        def _replace(match: re.Match[str]) -> str:
            inner = (match.group(1) or "").strip()
            if not inner:
                return ""
            if any(token in inner for token in stage_tokens):
                return ""
            return match.group(0)

        cleaned = re.sub(r"^[（(]\s*[^()（）\n]{1,50}\s*[）)]\s*", "", cleaned)
        cleaned = re.sub(r"[（(]\s*([^()（）\n]{1,50})\s*[）)]", _replace, cleaned)
        return self._strip_leading_sentence_boundary_artifacts(re.sub(r"\s+", " ", cleaned).strip())

    def _normalize_proactive_sentence_flow(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        # Protect URLs from sentence-flow tokenization: a URL is one atomic
        # unit and must never be split on ASCII dots or receive an appended
        # sentence punctuation. Swap each URL out for a NUL placeholder and
        # restore it right before returning.
        url_placeholders: list[str] = []

        def _protect_url(match: re.Match[str]) -> str:
            url_placeholders.append(match.group(0))
            return f"\x00URL{len(url_placeholders) - 1}\x00"

        cleaned = re.sub(r"https?://[^\s，。！？!?；;、]+", _protect_url, cleaned)
        cleaned = self._strip_unsupported_proactive_agreement(cleaned)
        cleaned = self._trim_abrupt_closing_topic_shift(cleaned)
        cleaned = _normalize_outbound_punctuation_flow(cleaned)
        cleaned = cleaned.replace("！?", "！？").replace("？!", "？！")
        cleaned = re.sub(
            r"([A-Za-z0-9_\-]{1,40})[。！？!?]\s+(呢|呀|啊|嘛|吧|哦|喔|诶)(?=[，,。！？!?~～\s]|$)",
            r"\1\2",
            cleaned,
        )
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        raw_units: list[str] = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            raw_units.extend(self._split_proactive_sentence_units(line))

        if not raw_units:
            return ""

        merged: list[str] = []
        continuation_prefixes = (
            "又", "还", "也", "就", "才", "只是", "但", "但是", "不过", "然后", "所以",
            "有点", "有一点", "不想", "没想", "想着", "顺手",
        )
        for unit in raw_units:
            unit = unit.strip(" ,，、")
            if not unit:
                continue
            is_continuation = unit.startswith(continuation_prefixes)
            if merged and is_continuation:
                merged[-1] = merged[-1].rstrip("。！？!?；;，,") + "，" + unit
            else:
                merged.append(unit)

        normalized = [self._ensure_chat_sentence_punctuation(item) for item in merged]
        normalized = [item for item in normalized if item]
        if len(normalized) <= 3:
            result = self._truncate_proactive_text("\n".join(normalized), 260)
        else:
            head = normalized[:2]
            tail_sentences = normalized[2:4]
            result = self._truncate_proactive_text("\n".join(head + tail_sentences), 260)
        for idx, url in enumerate(url_placeholders):
            result = result.replace(f"\x00URL{idx}\x00", url)
        return result

    def _group_share_text_has_life_sidecar(self, text: str) -> bool:
        cleaned = _single_line(text, 500)
        if "群" not in cleaned:
            return False
        life_tokens = (
            "课", "老师", "同学", "作业", "草稿纸", "书", "笔", "桌", "窗", "路上",
            "小猫", "猫", "饭", "吃", "喝", "杯", "天气", "雨", "太阳", "云", "风",
            "困", "饿", "刚刚", "刚才", "这会儿",
        )
        return any(token in cleaned for token in life_tokens)

    def _strip_unsupported_proactive_agreement(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        patterns = (
            r"^(?:哈哈|哈|嘿|嗯嗯|嗯|唔|诶|欸)[，,。.\s]*(?:我也觉得|确实|对吧|是吧|真的)[，,。.\s]*",
            r"^(?:我也觉得|确实|对吧|是吧|真的)[，,。.\s]*",
            r"^(?:哈哈|哈)[，,。.\s]*(?=(?:今天|刚刚|刚才|现在|窗外|路上|云|天气|太阳|雨|风))",
        )
        for pattern in patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
        return cleaned or str(text or "").strip()

    def _trim_performative_self_state_tail(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        tail_clause_match = re.search(
            r"([，,；;。！？!?]\s*)"
            r"((?:我)?(?:刚刚|刚才|刚|这会儿|现在)?"
            r"(?:在|还在|正|正在)?"
            r"[^。！？!?；;\n，,]{0,14}"
            r"(?:发呆|发怔|晃神|躺着|趴着|盯着|望着|看着天花板|看天花板|刷手机|摸鱼|犯困|醒着|睡不着|放空|走神|缓神)"
            r"[^。！？!?；;\n，,]{0,18}"
            r"(?:来着|而已|呢|啦|。|！|？|~|～)?$)",
            cleaned,
        )
        if tail_clause_match:
            kept = cleaned[: tail_clause_match.start()].strip(" ,，、；;")
            if kept:
                result = self._finish_trimmed_proactive_text(kept)
                logger.info(
                    "主动消息已去除刻意状态尾巴: before=%s after=%s",
                    _single_line(cleaned, 160),
                    _single_line(result, 160),
                )
                return result
        units: list[str] = []
        for line in cleaned.splitlines():
            line = line.strip()
            if line:
                units.extend(self._split_proactive_sentence_units(line))
        units = [unit.strip(" ,，、") for unit in units if unit.strip(" ,，、")]
        if len(units) <= 1:
            return cleaned
        tail = units[-1].strip()
        if not tail:
            return cleaned
        asks_user = bool(_ASK_USER_PROACTIVE_PATTERN.search(tail))
        if asks_user:
            return cleaned
        performative_tail = bool(
            re.search(
                r"^(?:我)?(?:刚刚|刚才|刚|这会儿|现在)?"
                r"(?:在|还在|正|正在)?"
                r"[^。！？!?；;\n]{0,12}"
                r"(?:发呆|躺着|趴着|盯着|望着|看着天花板|看天花板|刷手机|摸鱼|犯困|醒着|睡不着|放空|走神|缓神)"
                r"[^。！？!?；;\n]{0,18}"
                r"(?:来着|而已|呢|啦|。|！|？|~|～)?$",
                tail,
            )
        )
        if not performative_tail:
            return cleaned
        kept = units[:-1]
        if not kept:
            return cleaned
        result = "\n".join(self._finish_trimmed_proactive_text(unit) for unit in kept if unit)
        result = result.strip()
        if result:
            logger.info(
                "主动消息已去除刻意状态尾巴: before=%s after=%s",
                _single_line(cleaned, 160),
                _single_line(result, 160),
            )
            return result
        return cleaned

    def _proactive_status_inventory_kind(self, unit: str) -> str:
        cleaned = _single_line(unit, 140).strip(" ，,。！？!?~～")
        if not cleaned:
            return ""
        opener_match = re.match(r"^([\w\u4e00-\u9fffぁ-んァ-ヶー]{1,8})[，,]\s*", cleaned)
        if opener_match:
            opener_text = opener_match.group(1)
            if len(opener_text) <= 4 and not re.search(r"(窗外|外面|雨声|风声|天气|今天|现在|刚刚|刚才)", opener_text):
                cleaned = cleaned[opener_match.end():].strip()
        if re.search(r"(你|主人).{0,12}(吗|呢|呀|要不要|有没有)", cleaned):
            return ""
        if re.search(r"(窗外|外面|雨声|风声|雨|下雨|小雨|大雨|风|云|天色|阳光|太阳|月亮|路灯|杯沿|书页|桌边)", cleaned):
            return "scene"
        if re.search(r"^(?:我)?(?:刚刚|刚才|刚|才|已经|这会儿)?(?:洗漱|洗完|洗澡|刷牙|起床|醒|到家|出门|回家|吃完|喝完|写完|收拾完|换好|换完)", cleaned):
            return "routine"
        if re.search(r"^(?:我)?(?:今天|现在|刚刚|刚才|刚|才)?(?:穿了|穿着|换了|换成|披了|套了|戴了|拿了)", cleaned):
            return "clothing"
        if re.search(r"(舒服|安静|困|累|清醒|迷糊|开心|烦|平稳|舒服)", cleaned) and len(cleaned) <= 22:
            return "state"
        return ""

    def _is_conversational_proactive_text(self, text: str) -> bool:
        """判断主动消息是否为对话式消息，而非状态清单。

        状态清单收束（_trim_proactive_status_inventory）只应作用于「纯陈述、多段自报」的
        消息；含问句、颜文字/表情或直接问用户的消息是对话，收束会丢掉称呼、问句与情绪，
        破坏一句话的完整性（例如一条「场景铺垫＋颜文字＋问句」的完整问候被削成只剩一句
        场景描述）。
        """
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        if _ASK_USER_PROACTIVE_PATTERN.search(cleaned):
            return True
        for unit in self._split_proactive_sentence_units(cleaned):
            unit = unit.strip(" ，,、")
            if not unit:
                continue
            # 问句：以问号结尾，或结尾带疑问语气词（覆盖省略主语的问句）
            if re.search(r"[？?]$", unit) or re.search(r"[吗呢呀么]$", unit):
                return True
            # 颜文字/表情：括号内含非中文散文字符的短表达式，如 (≧▽≦) (◍•ᴗ•◍)
            if re.search(r"[（(][^（）()\n]*[^一-鿿，。！？、\s][^（）()\n]*[)）]", unit):
                return True
        return False

    def _trim_proactive_status_inventory(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        units: list[str] = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if line:
                units.extend(self._split_proactive_sentence_units(line))
        units = [unit.strip(" ，,、") for unit in units if unit.strip(" ，,、")]
        if len(units) < 2:
            return cleaned
        kinds = [self._proactive_status_inventory_kind(unit) for unit in units]
        inventory_count = sum(1 for kind in kinds if kind)
        if inventory_count < 2:
            return cleaned
        if len(units) == 2 and inventory_count < len(units):
            return cleaned
        # 对话式消息（含问句/颜文字/直接问用户）不是状态清单，收束会破坏其完整性，跳过
        if self._is_conversational_proactive_text(cleaned):
            return cleaned
        opener = ""
        opener_match = re.match(r"^([\w\u4e00-\u9fffぁ-んァ-ヶー]{1,4}[，,])", units[0])
        if opener_match:
            opener = opener_match.group(1)
        priority = {"scene": 4, "clothing": 3, "routine": 2, "state": 1}
        best_index = max(
            range(len(units)),
            key=lambda index: (priority.get(kinds[index], 0), index),
        )
        chosen = units[best_index].strip()
        if opener:
            chosen = re.sub(r"^[\w\u4e00-\u9fffぁ-んァ-ヶー]{1,4}[，,]\s*", "", chosen).strip()
            if chosen:
                chosen = f"{opener}{chosen}"
        chosen = self._ensure_chat_sentence_punctuation(chosen)
        if chosen and chosen != cleaned:
            logger.info(
                "主动消息已收束状态清单: before=%s after=%s",
                _single_line(cleaned, 180),
                _single_line(chosen, 160),
            )
            return chosen
        return cleaned

    def _finish_trimmed_proactive_text(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        lines = [line.strip(" ,，、；;") for line in cleaned.splitlines() if line.strip(" ,，、；;")]
        if not lines:
            return ""
        return "\n".join(self._ensure_chat_sentence_punctuation(line) for line in lines)

    def _has_abrupt_closing_topic_shift(self, text: str, *, inbound_text: str = "") -> bool:
        original = str(text or "").strip()
        if not original:
            return False
        trimmed = self._trim_abrupt_closing_topic_shift(original, inbound_text=inbound_text)
        return bool(trimmed and trimmed != original)

    def _trim_abrupt_closing_topic_shift(self, text: str, *, inbound_text: str = "") -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        units = []
        for line in cleaned.splitlines():
            line = line.strip()
            if line:
                units.extend(self._split_proactive_sentence_units(line))
        units = [unit.strip(" ,，、") for unit in units if unit.strip(" ,，、")]
        if len(units) <= 1:
            return cleaned
        inbound = _single_line(inbound_text, 260)
        inbound_is_sleep_context = bool(re.search(r"(晚安|睡了|睡觉|做梦|好梦|困了|休息|先睡|去睡|早点睡)", inbound))
        closing_index = -1
        for index, unit in enumerate(units):
            if re.search(r"(晚安|好梦|做个梦|做梦|睡吧|睡觉|去睡|早点睡|休息吧|明天见|(?:先)?(?:别|不)(?:吵|打扰|烦))", unit):
                closing_index = index
                break
        if closing_index < 0 or closing_index >= len(units) - 1:
            return cleaned
        tail = "".join(units[closing_index + 1 :])
        if not tail:
            return cleaned
        tail_continues_closing = bool(re.search(r"(梦|睡|晚安|明天|醒来|休息|被窝|枕头|月亮|星星)", tail))
        if tail_continues_closing and inbound_is_sleep_context:
            return cleaned
        abrupt_markers = (
            "今天", "刚刚", "刚才", "现在", "天气", "云", "太阳", "雨", "风", "作业", "阅读",
            "视频", "新闻", "群里", "资料柜", "日程", "吃", "喝", "路上", "窗外", "看到", "觉得",
        )
        looks_abrupt = any(marker in tail for marker in abrupt_markers) or len(tail) >= 6
        if not looks_abrupt:
            return cleaned
        kept = units[: closing_index + 1]
        result = "\n".join(self._ensure_chat_sentence_punctuation(unit) for unit in kept if unit)
        return result.strip() or cleaned

    def _ensure_chat_sentence_punctuation(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        if re.search(r"[。！？!?…~～]$", cleaned):
            return cleaned
        question_tokens = (
            "吗", "嘛", "么", "什么", "怎么", "咋", "有没有", "是不是", "要不要",
            "忙什么", "吃东西了吗", "睡了吗", "醒了吗", "你呢", "你那边呢", "你那里呢", "你那儿呢",
        )
        if any(token in cleaned for token in question_tokens):
            return cleaned + "？"
        soft_endings = ("呀", "啦", "嘛", "呢", "吧", "哦", "喔", "诶", "啊")
        if cleaned.endswith(soft_endings):
            return cleaned + "。"
        return cleaned + "。"

    def _split_proactive_sentence_units(self, text: str) -> list[str]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return []
        # 句子边界 = 中文/全角结束标点后的空白（含换行）。英文单词之间的空格
        # 不是句子边界——新闻标题/品牌名（如 "Koei Tecmo"、"TGS 2026"）里的空格
        # 必须保留，否则外来文本会被按单词切碎
        units: list[str] = []
        for part in [item.strip() for item in re.split(r"(?<=[。！？!?；;…~～])\s+", cleaned) if item.strip()]:
            if re.search(r"[。！？!?；;…~～]", part):
                matches = re.findall(r"[^。！？!?；;…~～]+[。！？!?；;…~～]+|[^。！？!?；;…~～]+$", part)
                units.extend(match.strip() for match in matches if match.strip())
            else:
                units.append(part)
        return units

    def _soften_social_proactive_text(self, text: str, *, action: str = "message") -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        cleaned = self._strip_parenthetical_stage_directions(cleaned)
        if not cleaned:
            return ""
        cleaned = self._trim_proactive_status_inventory(cleaned)
        cleaned = self._trim_performative_self_state_tail(cleaned)
        cleaned = re.sub(r"^(?:早上好|早安|上午好|中午好|午安|下午好|晚上好)[,,\s]*", "", cleaned)

        _SOCIAL_REPLACEMENTS = [
            ("刷一下存在感", ""),
            ("冒个泡", ""),
            ("冒个头", ""),
            ("顺手冒了个头", ""),
            ("没什么大不了的,就是", ""),
            ("没什么大道理,就是", ""),
            ("免得你又忘了我", ""),
            ("最近忙不忙？", ""),
            ("最近忙不忙", ""),
            ("数据有意思吗？", ""),
            ("数据有意思吗", ""),
            ("发现你好像在忙。", ""),
            ("发现你好像在忙", ""),
            ("请注意休息", "记得歇会儿"),
        ]
        for old, new in _SOCIAL_REPLACEMENTS:
            cleaned = cleaned.replace(old, new)

        cleaned = re.sub(r"你在忙(.{0,24})吗？感觉你[^。！？\n]*专注[^。！？\n]*[。！？]?", r"还在忙\1啊。", cleaned)
        cleaned = re.sub(r"你在忙(.{0,24})吗？", r"还在忙\1啊。", cleaned)
        cleaned = re.sub(r"感觉你[^。！？\n]*专注[^。！？\n]*[。！？]?", "", cleaned)
        cleaned = re.sub(r"感觉你[^。！？\n]{0,28}呢[。！？]?", "", cleaned)
        cleaned = re.sub(r"(?:我看你|看你)又?在忙", "还在忙", cleaned)

        _ACTION_SPECIFIC_REPLACEMENTS = {
            "screen_peek": [
                ("逻辑分支的工作", "那个逻辑分支"),
                ("工作吗？", "啊。"),
                ("工作啊。", "啊。"),
                ("感觉你投入的样子很专注呢。", ""),
                ("感觉你很投入呢。", ""),
                ("还在忙啊。", "还没从那边抬头啊。"),
            ],
            "poke": [
                ("我就戳一下", "就戳你一下"),
                ("所以来戳你一下", "所以来碰你一下"),
            ],
            "voice": [
                ("给你留了句语音。", "给你留了句语音。"),
                ("刚给你留了句语音,", "刚给你留了句语音,"),
            ],
            "photo_text": [
                ("路边的植物看着很有生机,给你拍了张照片。", "路边那点绿刚好有点顺眼。"),
                ("给你拍了张照片。", ""),
                ("给你拍了张照片", ""),
                ("给你拍了照片", ""),
                ("发给你啦。", ""),
            ],
        }
        if action in _ACTION_SPECIFIC_REPLACEMENTS:
            for old, new in _ACTION_SPECIFIC_REPLACEMENTS[action]:
                cleaned = cleaned.replace(old, new)
        if "photo_text" in action:
            cleaned = re.sub(r"^(?:今天天气[^。！？\n]{0,30}[,,])", "", cleaned)
            cleaned = cleaned.replace("（图片已送达）", "").replace("(图片已送达)", "")

        cleaned = re.sub(r"(?:来找你一下[,,、\s]*){2,}", "来找你一下,", cleaned)
        cleaned = re.sub(r"^[嗨哈喂欸诶]{1,2}[,,\s]+", "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = re.sub(r"([。！？])\1+", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"^[,。！？、\s]+", "", cleaned)
        cleaned = self._strip_parenthetical_stage_directions(cleaned)
        return cleaned

    def _deemphasize_state_report_preamble(self, text: str, *, reason: str = "") -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        if reason == "important_date_share":
            return cleaned

        date_report_patterns = (
            r"^(?:今天|现在)(?:是)?[^。！？\n]{0,18}(?:五一|劳动节|周末|休息日|假期|放假)[^。！？\n]{0,24}[。！？,\s]*",
            r"^(?:今天|现在)[^。！？\n]{0,16}(?:不用|不用去|不需要)(?:上学|上班|工作|补课)[^。！？\n]{0,18}[。！？,\s]*",
        )
        for pattern in date_report_patterns:
            cleaned = re.sub(pattern, "", cleaned)

        cleaned = re.sub(r"(?:所以|因此)[,，、\s]*(?=我|先|就|你)", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"^[,，。！？、\s]+", "", cleaned)
        return cleaned

    def _choose_proactive_message(
        self,
        user: dict[str, Any],
        name: str,
        planned_reason: str = "",
    ) -> tuple[str, str]:
        """Pick the proactive reason and return an internal intent note.

        The second value is deliberately not outbound copy. The actual message
        must still be generated by the framework chain and pass send review.
        """
        state = self.data.get("daily_state", {})
        current_item = self._proactive_current_plan_item(self.data.get("daily_plan", {}))
        can_do = self.data.get("can_do", [])
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        mood = _single_line(state.get("mood_bias") if isinstance(state, dict) else "平稳", 20)
        active_conditions = state.get("conditions", []) if isinstance(state, dict) else []

        reasons = [planned_reason] if planned_reason else []
        if self._is_quiet_time() and self._has_active_insomnia_state():
            reasons.append("insomnia_night")
        if active_conditions and random.random() < 0.45:
            reasons.append("quiet_care")
        if energy < 45 and random.random() < 0.55:
            reasons.append("quiet_care")
        share_probability = runtime_persona_setting(self, "proactive_share_probability", 45)
        activity_share_blocked = False
        block_checker = getattr(self, "_activity_share_duplicate_block_remaining", None)
        if callable(block_checker):
            try:
                activity_share_blocked = block_checker(user) > 0
            except Exception:
                activity_share_blocked = False
        if can_do and not activity_share_blocked and random.random() < max(0.05, min(0.85, share_probability)):
            reasons.append("activity_share")
        if self.data.get("bot_diaries") and random.random() < max(0.08, share_probability * 0.55):
            reasons.append("diary_share")
        upcoming_dates = self._get_relevant_important_dates()
        if upcoming_dates and random.random() < 0.35:
            reasons.append("important_date_share")
        if (
            current_item
            and runtime_persona_setting(self, "include_schedule_in_messages", True)
            and random.random() < 0.22
        ):
            reasons.append("background_schedule")
        if not reasons:
            reasons.append("check_in")
        elif _safe_int(user.get("ignored_streak"), 0, 0) <= 0 and random.random() < 0.12:
            reasons.append("check_in")
        reason = planned_reason if planned_reason and self._is_reason_allowed_now(planned_reason, user) else random.choice(reasons)

        if reason == "insomnia_night":
            return reason, "夜间清醒时的一句短开场；根据人格决定是安静自述、轻轻靠近还是不打扰地留一句，不报时、不追问、不拉长。"

        if reason == "special_day_greeting":
            return reason, "特别日子的低压力关系问候；只留一个真诚而具体的祝愿，不堆砌节日话术。"

        if reason == "quiet_care":
            return reason, "低能量或状态余波下的轻量问候；只给一个具体切口，不写成关心清单。"

        if reason == "morning_greeting":
            return reason, "当前时段的首次早间开口；贴近早晨片段，只做普通问候，不问早餐、吃了吗或吃什么，等用户回应后再关心。"

        if reason == "noon_greeting":
            return reason, "午间短开口；可以围绕吃饭、午休或短暂放松，不催促。"

        if reason == "evening_greeting":
            return reason, "傍晚或夜间收尾时的一句轻开口；不汇报日程，不追问。"


        if reason == "activity_share":
            activity = _single_line(random.choice(can_do), 40) if isinstance(can_do, list) and can_do else "刚才那点小事"
            return reason, f"围绕可做事项“{activity}”分享一个很小的进展或片段；不要写成自证或汇报。"

        if reason == "diary_share":
            fragment = self._pick_diary_fragment()
            if fragment:
                return reason, f"可引用日记碎片“{_single_line(fragment, 80)}”；只取一句自然分享，不写成报告。"

        if reason == "important_date_share" and upcoming_dates:
            entry = upcoming_dates[0]
            days = _safe_int(entry.get("_days_until"), 0)
            title = _single_line(entry.get("title"), 40)
            note = _single_line(entry.get("note"), 80)
            if days == 0:
                detail = f"今天是「{title}」"
            else:
                detail = f"「{title}」还有 {days} 天"
            if note:
                detail = f"{detail}；备注：{note}"
            return reason, f"重要日期提醒：{detail}；一句说清，不责备用户。"

        if reason == "background_schedule" and current_item:
            activity = _single_line(current_item.get("activity"), 40)
            seed = self._deemphasize_state_report_preamble(
                _single_line(current_item.get("message_seed"), 60),
                reason=reason,
            )
            detail = "；".join(part for part in (activity, seed) if part)
            return reason, f"当前日程片段：{detail or '没有明确片段'}；只取一个生活切口，不逐项汇报。"

        style = _single_line(
            user.get("style") or runtime_persona_setting(self, "default_style", "温柔"),
            24,
        )
        style_hint = f"；参考语气偏好：{style}" if style else ""
        return "check_in", f"无明确来源时的轻量开场{style_hint}；优先贴近关系事实、当前状态或当前日程，不使用固定模板。"
