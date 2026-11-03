"""Model task profile for Reality's short text generation."""

from companion_v2.model_gateway.models import Lane, ModelTaskProfile

KIND = "reality.text"


def reality_text_profile(provider, *, max_output_tokens=1024):
    return ModelTaskProfile(
        KIND, Lane.BACKGROUND, provider,
        "为现实提醒、唤醒回执和短回复生成自然文本；保持角色语气，不虚构设备读数。",
        max_output_tokens=max_output_tokens,
    )
