"""Fish Audio model capabilities at the native Provider boundary."""

from companion_v2.system_prompts.catalog import DRAMA_DIRECTION, INDEXTTS_DIRECTION


MODEL_OPTIONS = [
    {"value": "drama-3", "label": "Drama 3 Preview"},
    {"value": "s2.1-pro-free", "label": "S2.1 Pro Free"},
    {"value": "s2.1-pro", "label": "S2.1 Pro"},
    {"value": "s2-pro", "label": "S2 Pro"},
    {"value": "s1", "label": "S1 旧版"},
]


def _is_index_tts(provider_type, model):
    folded = str(provider_type or "").replace("_", "").replace("-", "").casefold()
    return "indextts" in folded or "indextts" in str(model or "").replace("-", "").casefold()


def _direction_for(policy, provider_type, model, overrides=None):
    """Pick the direction block for one resolved provider under the configured mode."""
    overrides = overrides if isinstance(overrides, dict) else {}
    drama = str(overrides.get("voice_drama") or "").strip() or DRAMA_DIRECTION
    index = str(overrides.get("voice_indextts") or "").strip() or INDEXTTS_DIRECTION
    mode = getattr(policy, "direction_mode", "auto")
    if mode == "off":
        return ""
    if mode == "fishaudio":
        return drama if str(provider_type) == "fishaudio_tts_api" else ""
    if mode == "indextts2":
        return index if _is_index_tts(provider_type, model) else ""
    # auto：只对明确支持表演指令的引擎开口，其他保持沉默。
    if str(provider_type) == "fishaudio_tts_api" and str(model).strip().casefold() == "drama-3":
        return drama
    if _is_index_tts(provider_type, model):
        return index
    return ""


async def provider_direction(context, policy, umo, channel="", overrides=None):
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
        return _direction_for(policy, config.get("type", ""), model, overrides)
    except Exception:
        # Optional performance guidance must never interrupt a core reply.
        return ""
