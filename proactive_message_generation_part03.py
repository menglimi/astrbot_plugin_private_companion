# -*- coding: utf-8 -*-
"""ProactiveMessageGenerationPart03Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_generation.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 364 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageGenerationMixin）。
"""
from __future__ import annotations

from .proactive_message_generation_shared import logger
from .proactive_message_generation_shared import Any
from .proactive_message_generation_shared import _safe_int
from .proactive_message_generation_shared import _single_line
from .proactive_message_generation_shared import _split_address_terms
from .proactive_message_generation_shared import re
from .proactive_message_generation_shared import runtime_persona_setting



class ProactiveMessageGenerationPart03Mixin:
    """ProactiveMessageGenerationPart03Mixin（从 ProactiveMessageGenerationMixin 拆出）。"""


    def _collapse_multi_candidate_proactive_text(self, text: str, *, user: dict[str, Any], name: str = "") -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if len(lines) >= 2:
            collapsed_lines = self._collapse_near_duplicate_proactive_lines(lines)
            if len(collapsed_lines) < len(lines):
                result = "\n".join(collapsed_lines).strip()
                logger.info(
                    "主动消息已合并同轮近似候选: before=%s after=%s",
                    _single_line(cleaned, 180),
                    _single_line(result, 160),
                )
                return result or cleaned
        units: list[str] = []
        for line in lines or [cleaned]:
            units.extend(self._split_proactive_sentence_units(line))
        units = [unit.strip() for unit in units if unit and unit.strip()]
        if len(units) <= 2:
            return cleaned

        opener_tokens: list[str] = []
        for value in (
            name,
            user.get("nickname") if isinstance(user, dict) else "",
            runtime_persona_setting(self, "default_nickname", ""),
        ):
            for token in _split_address_terms(value, 8):
                opener_tokens.append(_single_line(token, 16))
        first_opener = ""
        match = re.match(r"^([\w\u4e00-\u9fffぁ-んァ-ヶー]{1,8})[，,、\s]", units[0])
        if match:
            first_opener = match.group(1)
            opener_tokens.append(first_opener)
        opener_tokens = [token for token in dict.fromkeys(opener_tokens) if token]

        repeated_opener_index = 0
        for index, unit in enumerate(units[1:], start=1):
            if any(unit.startswith(token) and index >= 2 for token in opener_tokens):
                repeated_opener_index = index
                break
        if repeated_opener_index:
            units = units[:repeated_opener_index]

        if self._private_user_role(user) == "friend" and len(units) > 2:
            units = units[:2]
        return "\n".join(units).strip() or cleaned

    def _proactive_candidate_core_text(self, text: str) -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        cleaned = re.sub(r"^[\w\u4e00-\u9fffぁ-んァ-ヶー]{1,8}[，,、\s]+", "", cleaned)
        cleaned = re.sub(r"^(?:早上好|早安|上午好|中午好|午安|下午好|晚上好)[。！？!?…~～,，\s]*", "", cleaned)
        cleaned = re.sub(r"^(?:唔|嗯|诶|欸|啊|嗨|嘿)[。！？!?…~～,，\s]*", "", cleaned)
        cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", cleaned)
        filler_tokens = ("刚刚", "刚才", "现在", "今天", "这会儿", "好像", "感觉", "一点", "有点")
        for token in filler_tokens:
            cleaned = cleaned.replace(token, "")
        return cleaned

    def _proactive_candidate_bigrams(self, text: str) -> set[str]:
        cleaned = self._proactive_candidate_core_text(text)
        if len(cleaned) < 2:
            return set()
        return {cleaned[index : index + 2] for index in range(len(cleaned) - 1)}

    def _collapse_near_duplicate_proactive_lines(self, lines: list[str]) -> list[str]:
        kept: list[str] = []
        for line in lines:
            current = line.strip()
            if not current:
                continue
            current_core = self._proactive_candidate_core_text(current)
            duplicate_index = -1
            for index, old in enumerate(kept):
                old_core = self._proactive_candidate_core_text(old)
                if not current_core or not old_core:
                    continue
                shorter = min(len(current_core), len(old_core))
                if shorter < 8:
                    continue
                same_core = current_core == old_core
                contained = current_core in old_core or old_core in current_core
                current_bigrams = self._proactive_candidate_bigrams(current)
                old_bigrams = self._proactive_candidate_bigrams(old)
                bigram_overlap = 0.0
                if current_bigrams and old_bigrams:
                    bigram_overlap = len(current_bigrams & old_bigrams) / max(1, min(len(current_bigrams), len(old_bigrams)))
                if same_core or contained or bigram_overlap >= 0.86:
                    duplicate_index = index
                    break
            if duplicate_index < 0:
                kept.append(current)
                continue
            old = kept[duplicate_index]
            old_core = self._proactive_candidate_core_text(old)
            prefer_current = (
                len(current_core) < len(old_core)
                or (len(current) + 6 < len(old) and not re.search(r"^(?:早上好|早安|上午好|中午好|午安|下午好|晚上好)", current))
            )
            if prefer_current:
                kept[duplicate_index] = current
        return kept

    def _should_drop_vague_generic_proactive(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        action_context: str = "",
        text: str = "",
    ) -> bool:
        if reason != "check_in" or action != "message":
            return False
        if _safe_int(user.get("ignored_streak"), 0, 0) < 2:
            return False
        context = _single_line(action_context, 180)
        if context and not context.startswith("message") and "普通私聊文本" not in context:
            return False
        cleaned = _single_line(text, 160)
        if not cleaned:
            return True
        vague_tokens = ("想找你", "来看看你", "刷存在感", "最近忙不忙", "辛苦了", "在吗", "有点想你", "没什么事", "就是想")
        concrete_markers = ("刚", "路上", "窗", "雨", "书", "饭", "水", "图", "群", "视频", "作业", "游戏", "梦")
        return any(token in cleaned for token in vague_tokens) and not any(token in cleaned for token in concrete_markers)

    def _apply_proactive_style_variation(self, text: str, user: dict[str, Any]) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        items = user.get("action_consequences")
        if not isinstance(items, list):
            return cleaned
        recent_texts = [
            _single_line(item.get("text"), 80)
            for item in items[-5:]
            if isinstance(item, dict) and _single_line(item.get("text"), 80)
        ]
        if not recent_texts:
            return cleaned
        current_opening = re.split(r"[，,。！？!?…\s]", _single_line(cleaned, 80), maxsplit=1)[0][:6]
        repeated_opening = current_opening and any(
            re.split(r"[，,。！？!?…\s]", text, maxsplit=1)[0][:6] == current_opening
            for text in recent_texts
        )
        proactive_voice = str(
            runtime_persona_setting(self, "persona_proactive_voice_prompt", "") or ""
        )
        # Repetition control must not erase an opening explicitly defined by the persona.
        if repeated_opening and current_opening not in proactive_voice:
            cleaned = re.sub(r"^(唔|嗯|诶|啊|欸)[…\.。!！?？~～\s，,]*", "", cleaned).strip()
            cleaned = re.sub(r"^(刚好|突然|我就是|我来|来找你)[^，,。！？!?…\n]{0,16}[，,。！？!?…\s]*", "", cleaned).strip()
        if sum(cleaned.count(token) for token in ("唔", "嗯", "诶", "呀", "啦", "嘛", "哦", "呢")) >= 5:
            cleaned = re.sub(r"(呀|啦|嘛|哦|呢)(?=.*\1)", "", cleaned)
        return cleaned or str(text or "").strip()

    def _format_action_prompt_context(self, action: str, action_context: str) -> str:
        context = str(action_context or "").strip()
        if not context:
            return "普通文字"
        return _single_line(self._sanitize_action_context_text(action, context), 420)

    def _sanitize_action_boundaries(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
        has_real_image: bool = False,
    ) -> str:
        cleaned = self._soften_social_proactive_text(text, action=action)
        if not cleaned:
            return ""
        if not has_real_image and "photo_text" not in action:
            cleaned = self._remove_unbacked_media_claims(cleaned)
        if "screen_peek" in action:
            photo_patterns = (
                "拍了张照片",
                "拍了照片",
                "拍了自拍",
                "自拍",
                "风景照",
                "窗外阳光",
                "要看看吗",
                "给你看照片",
                "发你照片",
                "看图",
            )
            if any(pattern in cleaned for pattern in photo_patterns):
                return ""
        if "poke" in action and "photo_text" not in action and "voice" not in action:
            cleaned = cleaned.replace("戳一戳", "戳你一下")
            cleaned = cleaned.replace("我刚刚戳了你", "我刚戳你了")
            cleaned = cleaned.replace("我刚刚戳了你一下", "我刚戳你了")
        if action == "voice":
            cleaned = cleaned.replace("我给你发了一条语音", "刚给你发了条语音")
            cleaned = cleaned.replace("我发了一条语音", "刚给你发了条语音")
            cleaned = cleaned.replace("我生成了一条语音", "刚给你发了条语音")
            cleaned = cleaned.replace("我合成了一条语音", "刚给你发了条语音")
            cleaned = cleaned.replace("要不要听", "你有空再听嘛")
            cleaned = cleaned.replace("要听吗", "你有空再听嘛")
        if action == "photo_text":
            if has_real_image:
                if reason not in {"bili_video_share", "news_share", "web_exploration_share"}:
                    cleaned = self._repair_non_external_title_share_text(
                        cleaned,
                        reason=reason,
                        action_context=action_context,
                    )
                replacements = {
                    "我画了一张图": "这个画面",
                    "我刚画了张图": "这个画面",
                    "我生成了一张图": "这个画面",
                    "我做了张图": "这个画面",
                    "我生了一张图": "这个画面",
                    "我渲染了一张图": "这个画面",
                    "画面是": "画面里是",
                }
                for old, new in replacements.items():
                    cleaned = cleaned.replace(old, new)
                queue_replacements = {
                    "图好了": "",
                    "图片好了": "",
                    "照片好了": "",
                    "图生成好了": "",
                    "图片生成好了": "",
                    "还在队列里": "",
                    "还在排队": "",
                    "等图出来": "",
                    "等图片出来": "",
                    "已经发过去啦": "",
                    "已经发过去了": "",
                }
                for old, new in queue_replacements.items():
                    cleaned = cleaned.replace(old, new)
                for old in ("要看看吗", "要看吗", "想看吗"):
                    cleaned = cleaned.replace(old, "")
                cleaned = self._deemphasize_state_report_preamble(cleaned, reason=reason)
                return self._soften_social_proactive_text(cleaned, action=action)
            replacements = {
                "拍了张照片": "想到一个画面",
                "拍了照片": "想到一个画面",
                "拍了美美的照片": "想到一个挺想拍下来的画面",
                "发你照片": "想跟你说说刚才那个画面",
                "给你看照片": "想跟你说说刚才那个画面",
                "要看看吗": "先跟你说一下",
                "要看吗": "先跟你说一下",
            }
            for old, new in replacements.items():
                cleaned = cleaned.replace(old, new)
        cleaned = self._deemphasize_state_report_preamble(cleaned, reason=reason)
        return self._soften_social_proactive_text(cleaned, action=action)

    def _repair_non_external_title_share_text(
        self,
        text: str,
        *,
        reason: str = "",
        action_context: str = "",
    ) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        if reason in {"bili_video_share", "news_share", "web_exploration_share"}:
            return cleaned
        title_leak_pattern = r"刚看到[，,、\s]*[“\"『「].{2,60}[”\"』」](?:这个)?标题"
        if not re.search(title_leak_pattern, cleaned):
            return cleaned
        context = _single_line(action_context, 520)
        if reason == "group_share" or "群" in context:
            repaired = re.sub(rf"{title_leak_pattern}[，,。！？!?\s]*", "", cleaned, count=1).strip()
            return repaired if len(repaired) >= 2 else ""
        if "图片路径：" in context or "真实图片文件：" in context or "photo_text" in context:
            repaired = re.sub(rf"{title_leak_pattern}[，,。！？!?\s]*", "", cleaned, count=1).strip()
            return repaired if len(repaired) >= 2 else ""
        repaired = re.sub(
            r"刚看到[，,、\s]*[“\"『「]([^”\"』」]{2,60})[”\"』」](?:这个)?标题[，,。！？!?\s]*",
            "",
            cleaned,
            count=1,
        )
        return repaired.strip()

    def _remove_unbacked_media_claims(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        replacements = {
            "我拍了张照片": "我看到一个画面",
            "我拍了照片": "我看到一个画面",
            "拍了张照片": "看到一个画面",
            "拍了照片": "看到一个画面",
            "拍了张照": "看到一个画面",
            "拍了照": "看到一个画面",
            "给你拍了张照片": "看到一个画面就想到你",
            "给你拍了照片": "看到一个画面就想到你",
            "给你拍了张照": "看到一个画面就想到你",
            "给你拍了照": "看到一个画面就想到你",
            "发你看看": "跟你说一下",
            "发给你看看": "跟你说一下",
            "发你看": "跟你说一下",
            "发给你看": "跟你说一下",
            "给你看照片": "跟你说说这个画面",
            "给你看图": "跟你说说这个画面",
            "看图": "听我说",
            "你看看喜不喜欢": "你应该会喜欢",
            "你看看喜欢吗": "你应该会喜欢",
            "你看看": "跟你说一下",
        }
        for old, new in replacements.items():
            cleaned = cleaned.replace(old, new)
        cleaned = re.sub(r"[，,、\s]*(?:照片|图片|图)(?:里|上)?[，,、\s]*(?=被|看着|颜色|特别|挺)", "画面", cleaned)
        cleaned = re.sub(r"(?:这张|那张|这幅|那幅)(?:照片|图片|图)", "这个画面", cleaned)
        cleaned = cleaned.replace("[图片]", "").replace("【图片】", "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。")
        return cleaned

    def _is_overabstract_proactive_text(self, text: str, *, action: str) -> bool:
        cleaned = _single_line(text, 220)
        if not cleaned:
            return False
        weak_patterns = (
            "最近忙不忙",
            "发现你好像在忙",
            "数据有意思吗",
            "刚好想到你",
            "来找你一下",
            "碰你一下",
            "我就是来一下",
            "顺手来一下",
        )
        if any(token in cleaned for token in weak_patterns):
            return True
        if "screen_peek" in action and any(token in cleaned for token in ("还在忙啊", "看你在忙", "你好像在忙")):
            return True
        return False

    def _ground_proactive_text(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str,
    ) -> str:
        context = str(action_context or "")
        if reason == "goodnight_screen_check":
            return "还没睡的话，忙完就早点休息，不用回我。"
        if "screen_peek" in action:
            if "逻辑分支" in context:
                return "你还在跟那个逻辑分支较劲啊。先别急,慢慢捋嘛。"
            if any(token in context for token in ("测试", "进度", "插件")):
                return "你还在盯那个进度啊。眼睛先歇一下啦。"
            return "你半天都没抬头了诶。先缓一口气。"
        if "poke" in action:
            return "我刚戳你了。怎么又不出声啦。"
        if "photo_text" in action:
            return text
        if "voice" in action:
            return "刚给你发了条语音。你有空再听嘛。"
        if reason == "quiet_care":
            return "感觉你这阵子都没怎么松下来。歇一小会儿嘛,又不会怎样。"
        if reason == "evening_greeting":
            return "都这个点了,你还没收工吗。别一直绷着啦。"
        if reason == "noon_greeting":
            return "中午了诶。你吃东西没有,别又随便糊弄过去。"
        if reason in {"meal_care", "meal_care_followup"}:
            return "到饭点了。你吃东西没有呀？"
        if reason in {"activity_share", "diary_share", "background_schedule"}:
            return "有件小事想跟你说一下。"
        return "刚好到能休息一小会儿的时候,想问你一句。"
