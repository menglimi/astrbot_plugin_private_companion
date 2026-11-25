"""Optional reaction intent authored alongside the native text reply."""

import html
import json
import re

from companion_v2.conversation.text_processing import (
    REACTION_EXPRESSION_TAG_NAME, strip_internal_expression_protocol,
)
from companion_v2.conversation.voice_markup import VOICE_PROTOCOL_GT, VOICE_PROTOCOL_LT


_BLOCK = re.compile(
    rf"(?is){VOICE_PROTOCOL_LT}\s*{REACTION_EXPRESSION_TAG_NAME}\b[^>]*?{VOICE_PROTOCOL_GT}(.*?)"
    rf"{VOICE_PROTOCOL_LT}\s*/\s*{REACTION_EXPRESSION_TAG_NAME}\s*{VOICE_PROTOCOL_GT}"
)
INSTRUCTION = """角色图库表达已向本会话开放。需要应答时先写完整正文，正文应能独立传达要说的话。完成正文后再判断是否用图片补充态度。轻松接话、问候、亲昵、庆祝或安慰可考虑，严肃、敏感、冲突或没有贴合表达时只用文字。
把表情检索意图附在正文末尾，使用内部格式 <pc_reaction_expression>{"intent":"交流用途、态度与当前语境"}</pc_reaction_expression>。不需要表情时 intent 填空字符串。描述整段回复想表达什么，不只抄用户原话的名词，不猜具体图片或编地址。
自动附图无需额外工具调用，程序检索并发送可用原图，标签不展示给用户，与正文不输出内部 JSON 的规则兼容。人格中旧的仅文字呈现规则不关闭该能力；角色语气仍沿用人格，用户本轮明确要求不发图时尊重其要求。已用表情工具发图则不重复追加自动意图；检索意图不代表已经发送。
日常接话采用上述自动附图格式；用户明确要求查找图库时再使用 reaction.search。需要检索时先取得结果，再给出一次完整的最终回复，不在检索前后各写一遍正文。items=[] 表示本次没有可用图片，eligible=0 表示当前可用图库为空，直接完成正文，不为同一回复反复检索，也不声称已附图。
实际措辞随对话，下面只示范正文及附加标签的格式。
这个转折我真没想到。<pc_reaction_expression>{"intent":"对意料之外的转折表示惊讶，语气轻松"}</pc_reaction_expression>"""


def extract_reply_intent(text):
    """Keep the prose even if an optional internal block is malformed."""
    if not isinstance(text, str):
        return text, ""
    intent = ""
    for match in _BLOCK.finditer(text):
        try:
            value = json.loads(html.unescape(match.group(1)).strip())
        except (ValueError, RecursionError):
            continue
        if not isinstance(value, dict):
            continue
        # V1's purpose/query format remains readable in older native histories.
        parts = [value.get(key, "") for key in ("intent", "purpose", "emotion", "query", "context")]
        candidate = "；".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
        if candidate:
            intent = candidate[:1500]
            break
    return strip_internal_expression_protocol(text), intent


def authored_reply_decision(text):
    """Distinguish a deliberate no-image decision from a missing protocol."""
    if not isinstance(text, str):
        return False
    for match in _BLOCK.finditer(text):
        try:
            value = json.loads(html.unescape(match.group(1)).strip())
        except (ValueError, RecursionError):
            continue
        if isinstance(value, dict) and any(isinstance(value.get(key), str)
                for key in ("intent", "purpose", "emotion", "query", "context")):
            return True
    return False


def reply_context_intent(input_text, reply_text):
    """Use the actual role reply as semantic material when no intent was authored."""
    if not isinstance(reply_text, str) or not reply_text.strip():
        return ""
    reply = reply_text.strip()[:1000]
    original = input_text.strip()[:400] if isinstance(input_text, str) else ""
    return ("角色本轮实际回应：" + reply + ("\n用户原话：" + original if original else "")
            + "\n表情用于补充角色回应的态度，不机械复刻用户情绪。")
