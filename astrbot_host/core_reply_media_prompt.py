"""Finalize media presentation after native persona and other request hooks."""

from collections.abc import Mapping
import logging

from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from companion_v2.expression.reply_intent import INSTRUCTION as REACTION_INSTRUCTION
from .core_reply import CORE_REPLY_CONTEXT_EXTRA
from .core_reply_voice import voice_prompt_instruction


logger = logging.getLogger(__name__)


def _value(item, name, default=None):
    return item.get(name, default) if isinstance(item, Mapping) else getattr(item, name, default)


def _set_content(message, content):
    if isinstance(message, dict):
        message["content"] = content
    else:
        message.content = content


def _text(content):
    if isinstance(content, str):
        return content
    return "\n".join(_value(part, "text", "") for part in content or ()
                     if _value(part, "type") == "text")


def finalize_core_reply_media_prompt(event, run_context):
    """Apply the selected presentation to the messages the SDK actually sends.

    The agent boundary is after request hooks and persona construction. Keep
    the persona, history, nontext parts and tools; replace only our own guidance.
    Provider fallbacks and tool continuations share these same system messages.
    """
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    messages = getattr(run_context, "messages", None)
    if not isinstance(core, dict) or not isinstance(messages, list):
        return
    prepared = core.get("prepared", {})
    if not isinstance(prepared, Mapping):
        return
    voice = voice_prompt_instruction(prepared.get("voice_reply_policy"),
        prepared.get("voice_provider_instruction", ""),
        prepared.get("voice_rules_instruction", ""))
    reaction = core.get("_reaction_reply", {}).get("enabled", False)
    instructions = [part for part in (voice, REACTION_INSTRUCTION if reaction else "") if part]
    if not instructions:
        return
    block = ("<companion_reply_presentation>\n"
             "以下是本轮最终回复的呈现设置，适用于普通接话及工具执行后的最终回应。"
             "角色身份、性格与措辞保持当前人格；语音和表情的呈现方式采用本轮设置。"
             "内部标签交给程序消费，与不向用户展示内部格式的要求兼容。\n"
             "历史消息通常只保存最终可见文字；历史里没有语音标签或表情意图，不表示本轮关闭了媒体，也不应沿用历史的纯文字格式。\n"
             + "\n\n".join(instructions) + "\n</companion_reply_presentation>")
    policy = prepared.get("voice_reply_policy")
    if voice and "send_message_to_user" in core.get("_native_tool_names", ()):
        block += ("\n通过 send_message_to_user 投递本轮正文时，plain.text 字段也使用上述语音标签与译文格式。"
                  "朗读原文由程序合成音频，不要编造语音文件地址；工具正文和普通最终正文采用同一呈现设置。")
    if (reaction and isinstance(policy, VoiceReplyPolicy) and policy.language == "ja"
            and policy.foreign_text_mode == "translation"
            and (policy.generation_mode == "model_tag" or policy.trigger_mode == "adaptive")):
        block += ('\n本轮同时生成语音和表情判定，彼此独立。完整格式示例：'
                  '<pc_tts>おやすみ。ゆっくり休んでね。</pc_tts>晚安，好好休息呀。'
                  '<pc_reaction_expression>{"intent":"温柔道晚安，关心对方休息"}</pc_reaction_expression>。'
                  '实际回复按本轮语境和角色语气生成，不照抄示例。')
    previous = core.get("_media_prompt_instruction", "")
    remove = tuple(part for part in (previous, core.get("_voice_prompt_instruction", ""),
                                    REACTION_INSTRUCTION if reaction else "") if part)
    systems = [message for message in messages if _value(message, "role") == "system"]
    if not systems:
        from astrbot.core.agent.message import Message
        messages.insert(0, Message(role="system", content=block))
    else:
        for message in systems:
            content = _value(message, "content", "")
            if isinstance(content, str):
                for part in remove:
                    content = content.replace(part, "")
                _set_content(message, content.rstrip())
            elif isinstance(content, list):
                updated = []
                for item in content:
                    if _value(item, "type") == "text":
                        text = _value(item, "text", "")
                        original = text
                        for part in remove:
                            text = text.replace(part, "")
                        if original != text and not text.strip():
                            continue
                        if isinstance(item, dict):
                            item = {**item, "text": text.rstrip()}
                        else:
                            item = item.model_copy(update={"text": text.rstrip()})
                    updated.append(item)
                _set_content(message, updated)
        last = systems[-1]
        content = _value(last, "content", "")
        if isinstance(content, list):
            from astrbot.core.agent.message import TextPart
            _set_content(last, [*content, TextPart(text=block)])
        else:
            _set_content(last, ((content or "").rstrip() + "\n\n" + block).lstrip())
    core["_media_prompt_instruction"] = block


def core_media_request_diagnostics(event, run_context):
    """Observe actual system messages without retaining prompts or chat text."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict):
        return None
    prepared = core.get("prepared", {})
    if not isinstance(prepared, Mapping):
        return None
    policy = prepared.get("voice_reply_policy")
    reaction = bool(core.get("_reaction_reply", {}).get("enabled"))
    if not isinstance(policy, VoiceReplyPolicy) and not reaction:
        return None
    systems = "\n".join(_text(_value(message, "content")) for message in run_context.messages
                        if _value(message, "role") == "system")
    block = core.get("_media_prompt_instruction")
    note = {"voice_mode": policy.trigger_mode if isinstance(policy, VoiceReplyPolicy) else "disabled",
            "voice_prompt_present": bool(policy and voice_prompt_instruction(
                policy, prepared.get("voice_provider_instruction", ""),
                prepared.get("voice_rules_instruction", "")) in systems),
            "reaction_enabled": reaction, "reaction_prompt_present": reaction and REACTION_INSTRUCTION in systems,
            "presentation_present": bool(block and block in systems)}
    core.setdefault("_media_request_diagnostics", []).append(note)
    logger.info("[CompanionV2][media] stage=request event_ref=%s voice_mode=%s voice_prompt=%s reaction_enabled=%s reaction_prompt=%s presentation=%s",
                core.get("prepared", {}).get("event_ref", "unknown"), note["voice_mode"],
                note["voice_prompt_present"], reaction, note["reaction_prompt_present"], note["presentation_present"])
    return note
