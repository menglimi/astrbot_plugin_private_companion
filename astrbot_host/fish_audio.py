"""Fish Audio model capabilities at the native Provider boundary."""


MODEL_OPTIONS = [
    {"value": "drama-3", "label": "Drama 3 Preview"},
    {"value": "s2.1-pro-free", "label": "S2.1 Pro Free"},
    {"value": "s2.1-pro", "label": "S2.1 Pro"},
    {"value": "s2-pro", "label": "S2 Pro"},
    {"value": "s1", "label": "S1 旧版"},
]

DRAMA_DIRECTION = (
    "本轮朗读引擎支持 Drama 3 表演指令。只在 <pc_tts> 内根据角色此刻的情绪和对话，"
    "自然地添加少量方括号指令，例如 [温柔地，像贴近对方说话]、[轻笑]、[pause]；"
    "停顿、呼吸这类整句指令只写方括号，不要写成 <pause> 或 <breath>；"
    "指令可用中文或朗读语种，不必每句话都加，也不要把动作描写当成要念的台词。"
    "局部语气可用成对的 <whisper>、<emphasis>、<soft>、<fast>、<slow>、<stress>、<forceful> "
    "包住对应词句，例如 <pc_tts>[轻声安慰]没关系，<soft>慢慢来。</soft></pc_tts>。"
    "这些表演指令仅交给语音合成，中文译文和标签外的正文不写这些控制标记。"
    "保持本轮指定的朗读语种、角色声线和原意，不自行添加其他说话人。"
)


async def provider_direction(context, policy, umo, channel=""):
    """Describe only the selected live voice; never include endpoint or secrets."""
    if policy is None or context is None or not (
            policy.generation_mode == "model_tag" or policy.trigger_mode == "adaptive"):
        return ""
    try:
        provider_id = policy.provider_for_language(channel)
        if provider_id:
            provider = context.get_provider_by_id(provider_id)
            if provider is None or provider not in context.get_all_tts_providers():
                return ""
        else:
            getter = getattr(context, "get_using_tts_provider_async", None)
            provider = await getter(umo) if callable(getter) else context.get_using_tts_provider(umo)
        config = getattr(provider, "provider_config", {})
        if not isinstance(config, dict) or config.get("type") != "fishaudio_tts_api":
            return ""
        getter = getattr(provider, "get_model", None)
        model = getter() if callable(getter) else config.get("model", "")
        return DRAMA_DIRECTION if str(model).strip().lower() == "drama-3" else ""
    except Exception:
        # Optional performance guidance must never interrupt a core reply.
        return ""
