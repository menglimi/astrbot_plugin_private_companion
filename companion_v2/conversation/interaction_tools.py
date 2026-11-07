"""First-party model tool for one explicit, event-scoped poke.

旧版把"摸摸/拍一拍"放在主动动作里，只能由系统按概率发起；这里补的是同一件事的
按需入口：对方明确点名要你戳谁时，模型可以真的戳一下，而不是只用文字假装。
它只作用于当前会话，不跨窗口、不解析到其他群，也不替代文字回复。
"""

from companion_v2.execution.models import encode
from companion_v2.model_gateway.models import ToolSpec


POKE_ACTION = "interaction.poke"

POKE_SPEC = ToolSpec(
    POKE_ACTION,
    "戳一戳（拍一拍）当前会话里被点名的这个人，只在对方明确要求时调用。"
    "群聊必须填 target（对方给出的昵称、群名片或 QQ）；私聊可省略。"
    "只戳明确点名的人，不猜同名、不戳无关成员；它不代替文字回复，"
    "同一轮对同一个人只调用一次，结果未知时不要重试。",
    encode({"type": "object", "additionalProperties": False,
            "properties": {
                "target": {"type": "string",
                           "description": "群聊必填：要戳的人的昵称、群名片或 QQ；私聊可省略。"},
                "reason": {"type": "string",
                           "description": "可选：为什么戳，一句话；只进审计，不发给对方。"}},
            "required": []}),
)


__all__ = ["POKE_ACTION", "POKE_SPEC"]
