"""Fish Audio model capabilities at the native Provider boundary."""


MODEL_OPTIONS = [
    {"value": "drama-3", "label": "Drama 3 Preview"},
    {"value": "s2.1-pro-free", "label": "S2.1 Pro Free"},
    {"value": "s2.1-pro", "label": "S2.1 Pro"},
    {"value": "s2-pro", "label": "S2 Pro"},
    {"value": "s1", "label": "S1 旧版"},
]

INDEXTTS_DIRECTION = (
    "本轮朗读引擎支持情绪描述（IndexTTS2）。只在 <pc_tts> 开头写一个 [情绪：<不超过 12 字的情绪与语气>]，"
    "例如 [情绪：压低的温柔]，随后直接写要念的正文；不要加动作旁白、括号说明或其他控制标记。"
    "情绪描述只交给语音合成，中文译文和标签外的正文不写这些控制标记。"
    "保持本轮指定的朗读语种、角色声线和原意，不自行添加其他说话人。"
)

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


def _is_index_tts(provider_type, model):
    folded = str(provider_type or "").replace("_", "").replace("-", "").casefold()
    return "indextts" in folded or "indextts" in str(model or "").replace("-", "").casefold()


def _direction_for(policy, provider_type, model):
    """Pick the direction block for one resolved provider under the configured mode."""
    mode = getattr(policy, "direction_mode", "auto")
    if mode == "off":
        return ""
    if mode == "fishaudio":
        return DRAMA_DIRECTION if str(provider_type) == "fishaudio_tts_api" else ""
    if mode == "indextts2":
        return INDEXTTS_DIRECTION if _is_index_tts(provider_type, model) else ""
    # auto：只对明确支持表演指令的引擎开口，其他保持沉默。
    if str(provider_type) == "fishaudio_tts_api" and str(model).strip().casefold() == "drama-3":
        return DRAMA_DIRECTION
    if _is_index_tts(provider_type, model):
        return INDEXTTS_DIRECTION
    return ""


async def provider_direction(context, policy, umo, channel=""):
    """Describe only the selected live voice; never include endpoint or secrets."""
    if policy is None or context is None or not (
            policy.generation_mode == "model_tag" or policy.trigger_mode == "adaptive"):
        return ""
    if getattr(policy, "direction_mode", "auto") == "off":
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
        if not isinstance(config, dict):
            return ""
        getter = getattr(provider, "get_model", None)
        model = getter() if callable(getter) else config.get("model", "")
        return _direction_for(policy, config.get("type", ""), model)
    except Exception:
        # Optional performance guidance must never interrupt a core reply.
        return ""
