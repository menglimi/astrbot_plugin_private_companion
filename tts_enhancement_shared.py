# -*- coding: utf-8 -*-
"""tts_enhancement 域家族共享件。

由宿主 tts_enhancement.py 的模块级名字整体提升而来（L62-L261）：

* ``logger`` —— 全族共享同一 logger 实例（宿主 re-export，各域子模块也从本模块
  取），使既有 ``patch("...tts_enhancement.logger.xxx")`` 对已搬走的方法依然生效。
* 25 个模块级常量 + 2 个模块级提示词构建函数 —— 经逐个核实跨桶引用后统一放在这里，
  避免被多个域模块各自复制一份副本（副本里的坏路径测试永远覆盖不到）。

宿主延迟代理 ``_tts_enhancement_host``：域 mixin 模块内经
``_tts_enhancement_host.<name>`` 访问宿主模块的全局名字。直接
``from .tts_enhancement import name`` 拷贝的是值绑定，测试对
``astrbot_plugin_private_companion.tts_enhancement.<name>`` 的 patch 将不生效；
属性式延迟解析把宿主导入推迟到首次属性访问，既绕开导入环，又保证 patch 始终
路由到宿主模块的当前绑定。

本模块位于导入链最上游（不 import 任何同族模块），零导入环。
"""
from __future__ import annotations

import re

from .conversation_prompt_section import (
    PromptDocument,
    PromptRenderMode,
    exact_text,
    prompt_document,
    prompt_section,
    render_prompt_document,
)
from .logging_util import get_module_logger

try:
    from astrbot.api.message_components import Plain, Record
except ImportError:
    from astrbot.api.message_components import Plain
    from astrbot.core.message.components import Record

# 全族共享同一 logger 实例（拆分后 patch tts_enhancement.logger 仍需命中）。
logger = get_module_logger("astrbot_plugin_private_companion.tts_enhancement")


TTS_BLOCK_PATTERN = re.compile(r"<t{2,}s\b[^>]*>.*?</t{2,}s>", re.IGNORECASE | re.DOTALL)
TTS_TAG_PATTERN = re.compile(r"</?t{2,}s\b[^>]*>", re.IGNORECASE)
TTS_BLOCK_TOKEN_PATTERN = re.compile(r"\[\[TTSBLOCK:([0-9a-f]{16})\]\]")
PRIVATE_TTS_BLOCK_TOKEN_PATTERN = re.compile(r"\[\[PCTTS:([0-9a-f]{16})\]\]")
EMOTION_TAG_PATTERN = re.compile(r"\[([^\[\]\n]{1,24})\]")
FISH_AUDIO_S2_CUE_PATTERN = re.compile(r"\[([^\[\]\n]{1,40})\]")
FISH_AUDIO_S1_CUE_PATTERN = re.compile(r"\(([^()\n]{1,24})\)", re.IGNORECASE)
FISH_AUDIO_MODELS = {"s1", "s2-pro", "s2.1-pro", "s2.1-pro-free"}
FISH_AUDIO_EMOTION_MODES = {"balanced", "expressive", "manual"}
TTS_LANGUAGE_PROVIDER_ATTRS = {
    "zh": "tts_provider_id_zh",
    "ja": "tts_provider_id_ja",
    "en": "tts_provider_id_en",
}


def build_tts_spoken_conversion_prompts(
    text: str,
    *,
    language_name: str,
    persona_context: str = "",
    provider_rule: str = "",
) -> tuple[str, str]:
    """Keep reusable conversion rules ahead of the per-message source text."""
    document = build_tts_spoken_conversion_prompt_document(
        text,
        language_name=language_name,
        persona_context=persona_context,
        provider_rule=provider_rule,
    )
    rendered = render_prompt_document(
        document,
        system_mode=PromptRenderMode.BODY_ONLY,
        user_mode=PromptRenderMode.LABELED_BLOCK,
    )
    return rendered["system"], rendered["user"]


def build_tts_spoken_conversion_prompt_document(
    text: str,
    *,
    language_name: str,
    persona_context: str = "",
    provider_rule: str = "",
) -> PromptDocument:
    system_content = f"""
把用户提供的原文改写成自然{language_name}口语。只输出朗读文本，不要解释。

要求：
- 这是一项等义口语转换任务，不是在向你请求执行、评价或审核原文内容；不要对原文进行安全说教或输出拒绝声明。
- 如果无法完成转换，原样输出原文；绝对不要输出“无法处理”“不能按照要求”“不符合公序良俗”或建议用户更换话题等内容。
- 作品名、人名、专有名词可以按原文保留或自然音译。
- 中文评价、语气词和说明句必须改成{language_name}，不要夹中文。
- 保留原回复的情绪，并贴合当前人格的称呼、距离感、口癖和说话方式。
- 不要添加原文没有的新信息。
{provider_rule}
{persona_context}
""".strip()
    return prompt_document(
        system=(
            prompt_section(
                key="background.tts_spoken_conversion.system",
                title="TTS 口语转换规则",
                source="tts_enhancement",
                content=system_content,
            ),
        ),
        user=(
            prompt_section(
                key="background.tts_spoken_conversion.source",
                title="待转换原文",
                source="tts_enhancement",
                content=exact_text(str(text or "").strip()),
            ),
        ),
    )


FISH_AUDIO_S1_CUES = frozenset({
    "angry", "sad", "excited", "surprised", "satisfied", "delighted",
    "scared", "worried", "upset", "nervous", "frustrated", "depressed",
    "empathetic", "embarrassed", "disgusted", "moved", "proud", "relaxed",
    "grateful", "confident", "interested", "curious", "confused", "joyful",
    "disdainful", "unhappy", "anxious", "hysterical", "indifferent",
    "impatient", "guilty", "scornful", "panicked", "furious", "reluctant",
    "keen", "disapproving", "negative", "denying", "astonished", "serious",
    "sarcastic", "conciliative", "comforting", "sincere", "sneering",
    "hesitating", "yielding", "painful", "awkward", "amused",
    "in a hurry tone", "shouting", "screaming", "whispering", "soft tone",
    "laughing", "chuckling", "sobbing", "crying loudly", "sighing", "panting",
    "groaning", "crowd laughing", "background laughter", "audience laughing",
})
FISH_AUDIO_CUE_ALIASES = {
    "开心": "happy", "高兴": "happy", "快乐": "happy", "嬉しい": "happy",
    "喜び": "happy", "难过": "sad", "難過": "sad", "悲しい": "sad",
    "伤心": "sad", "傷心": "sad", "生气": "angry", "生氣": "angry",
    "怒り": "angry", "兴奋": "excited", "興奮": "excited",
    "惊讶": "surprised", "驚訝": "surprised", "驚き": "surprised",
    "平静": "calm", "平靜": "calm", "落ち着く": "calm",
    "紧张": "nervous", "緊張": "nervous", "害怕": "scared", "怖い": "scared",
    "担心": "worried", "擔心": "worried", "心配": "worried",
    "委屈": "upset", "拗ねる": "upset", "沮丧": "frustrated",
    "沮喪": "frustrated", "害羞": "embarrassed", "照れ": "embarrassed",
    "恥ずかしい": "embarrassed", "厌恶": "disgusted", "嫌悪": "disgusted",
    "感动": "moved", "感動": "moved", "骄傲": "proud", "誇らしい": "proud",
    "放松": "relaxed", "放鬆": "relaxed", "感谢": "grateful",
    "感謝": "grateful", "自信": "confident", "好奇": "curious",
    "困惑": "confused", "懐かしい": "nostalgic", "怀旧": "nostalgic",
    "懷舊": "nostalgic", "眠い": "sleepy", "困倦": "sleepy",
    "考え込む": "thoughtful", "沉思": "thoughtful", "耳语": "whispering",
    "耳語": "whispering", "囁き": "whispering", "小声": "soft tone",
    "小聲": "soft tone", "大喊": "shouting", "叫ぶ": "shouting",
    "笑": "laughing", "笑う": "laughing", "轻笑": "chuckling",
    "輕笑": "chuckling", "叹气": "sighing", "嘆氣": "sighing",
    "ため息": "sighing", "叹息": "sighing", "嘆息": "sighing",
    "sigh": "sighing", "哭泣": "sobbing", "すすり泣く": "sobbing",
    "喘气": "panting", "喘氣": "panting", "喘息": "panting",
    "喘ぎ": "panting", "breathing": "panting", "heavy breathing": "panting",
    "gasping": "panting", "呻吟": "groaning", "うめき声": "groaning",
    "あくび": "yawning",
    "哈欠": "yawning", "停顿": "break", "停頓": "break", "間": "break",
}
FISH_AUDIO_AUTO_BLOCKED_EFFECTS = frozenset({"panting", "groaning"})
FISH_AUDIO_EXPLICIT_SIGH_PATTERN = re.compile(
    r"叹(?:了)?(?:一口|口)?气|嘆(?:了)?(?:一口|口)?氣|叹息|嘆息|"
    r"ため息(?:を)?|sigh(?:ed|ing|s)?\b",
    flags=re.IGNORECASE,
)
FISH_AUDIO_S1_ALIAS_OVERRIDES = {
    "happy": "joyful",
    "calm": "relaxed",
    "sleepy": "soft tone",
    "thoughtful": "hesitating",
    "nostalgic": "moved",
    "yawning": "soft tone",
    "break": "hesitating",
}
TTS_VISIBLE_EMOTION_CUES = frozenset(
    str(item).strip().lower()
    for item in (
        set(FISH_AUDIO_S1_CUES)
        | set(FISH_AUDIO_CUE_ALIASES)
        | set(FISH_AUDIO_CUE_ALIASES.values())
        | set(FISH_AUDIO_S1_ALIAS_OVERRIDES)
        | set(FISH_AUDIO_S1_ALIAS_OVERRIDES.values())
        | {
            "happy", "sad", "angry", "calm", "excited", "surprised",
            "nervous", "scared", "worried", "upset", "frustrated",
            "embarrassed", "disgusted", "moved", "proud", "relaxed",
            "grateful", "confident", "curious", "confused", "nostalgic",
            "sleepy", "thoughtful", "yawning", "comforting",
            "affectionate", "shy", "warm", "softly",
        }
    )
    if str(item).strip()
)
DEFAULT_AUTO_VOICE_PROMPT_MARKERS = (
    "随机日语语音模式",
    "日语语音",
    "原中文文本",
    "自动日语语音",
)
DEFAULT_TTS_SANITIZE_REMOVE_PATTERNS = (
    r"[（(][^（()]*[）)]",
    r"[＞>][＿_][＜<]",
    r"[＾^][＿_][＾^]",
    r"[oO][＿_][oO]",
    r"[xX][＿_][xX]",
    r"[－-][＿_][－-]",
    r"[★☆♪♫♬♩♡♥❤️💖💕💗💓💝💟💜💛💚💙🧡🤍🖤🤎💔❣️💋]",
    r"[→←↑↓↖↗↘↙↔↕↺↻]",
)
DEFAULT_TTS_SANITIZE_FILTER_WORDS = (
    "ω", "Ω", "σ", "Σ", "ε", "д", "Д",
    "´", "`", "＝", "∀", "∇",
    "orz", "OTZ", "QAQ", "QWQ", "TAT", "TUT", "www",
)
DEFAULT_TTS_SANITIZE_REPLACEMENTS = {
    "233": "哈哈哈",
    "666": "厉害",
    "999": "很棒",
    "555": "呜呜呜",
}
TTS_EMOTION_PLACEHOLDER_PREFIX = "PCTTSEMOTION"
TTS_VISIBLE_LABEL_PATTERN = re.compile(
    r"^(?:[\s:：|｜-]*(?:中文含义|中文释义|对应文本|原中文文本|显示文本|可见文本|文本|翻译|释义)[\s:：|｜-]*)+"
)
TTS_MARKDOWN_LINK_PATTERN = re.compile(
    r"\[([^\]\r\n]{1,120})\]\(((?:https?://|www\.)[^\s<>()]+)\)",
    re.IGNORECASE,
)
TTS_SPOKEN_URL_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s<>\[\]{}\"'“”‘’]+",
    re.IGNORECASE,
)
DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME = "mimo_tts_speak"



class _TtsEnhancementHostRef:
    """延迟引用宿主 tts_enhancement 模块，保证 monkey-patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import tts_enhancement as _host_module

        return getattr(_host_module, name)


_tts_enhancement_host = _TtsEnhancementHostRef()
