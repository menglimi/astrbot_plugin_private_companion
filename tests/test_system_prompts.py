"""Fixed system prompt overrides: catalog, config contract and TTS wiring."""

from types import SimpleNamespace

import pytest

from companion_v2.foundation.refs import SubjectKind
from companion_v2.system_prompts import catalog, configuration
from companion_v2.system_prompts.context import SystemPromptContext


def test_catalog_has_bounded_defaults_for_every_editable_block():
    keys = {entry.key for entry in catalog.ENTRIES}
    assert keys == {"voice_rules", "voice_drama", "voice_indextts", "background",
                    "relationship", "knowledge", "memory"}
    assert set(catalog.FIELD_DEFAULTS) == keys
    assert all(isinstance(value, str) and value.strip() for value in catalog.FIELD_DEFAULTS.values())
    assert "<pc_tts>" in catalog.VOICE_RULES
    assert "[singing]" in catalog.DRAMA_DIRECTION
    assert "官方标签表" in catalog.DRAMA_DIRECTION


def test_configuration_contract_requires_every_field_and_bounds_text():
    configuration.validate_values(dict(catalog.FIELD_DEFAULTS))
    with pytest.raises(configuration.ConfigurationError):
        configuration.validate_values({"voice_drama": "only one"})
    with pytest.raises(configuration.ConfigurationError):
        configuration.validate_values({**catalog.FIELD_DEFAULTS, "voice_drama": "x" * (configuration.MAX_PROMPT_CHARS + 1)})
    fields = configuration.configuration_fields()
    assert {field.field_id for field in fields} == {
        f"system.prompts.{key}" for key in catalog.FIELD_DEFAULTS}
    assert all(field.default == catalog.FIELD_DEFAULTS[field.field_id.split(".", 2)[-1]] for field in fields)


def test_context_returns_only_non_default_overrides(monkeypatch):
    context = SimpleNamespace(
        scope=SimpleNamespace(installation_id="install", bot_id="bot"),
        role_binding=SimpleNamespace(role=SimpleNamespace(kind=SubjectKind.BOT, subject_id="role-a")),
    )
    values = dict(catalog.FIELD_DEFAULTS)
    values["voice_drama"] = "自定义 Drama 规则"
    values["knowledge"] = ""
    snapshot = SimpleNamespace(fields={
        f"system.prompts.{key}": SimpleNamespace(value=value) for key, value in values.items()
    })
    prompts = SystemPromptContext(runtime=object())
    monkeypatch.setattr("companion_v2.system_prompts.context.target_for", lambda context: object())
    monkeypatch.setattr(prompts.owner, "read", lambda target, fields: snapshot)
    assert prompts.overrides(context) == {"voice_drama": "自定义 Drama 规则"}


def test_voice_rules_override_keeps_the_runtime_limits():
    from companion_v2.conversation.voice_reply import VoiceReplyPolicy
    from astrbot_host.core_reply_voice import voice_prompt_instruction

    policy = VoiceReplyPolicy(generation_mode="model_tag", trigger_mode="always",
                              max_speech_chars=240)
    text = policy.instruction_with_rules("自定义标签规则")
    assert text.startswith("本轮采用语音回复")
    assert "自定义标签规则" in text
    assert "每段朗读控制在 240 字以内" in text
    assert "<pc_tts>" not in text
    assert "<pc_tts>" in policy.instruction
    integrated = voice_prompt_instruction(policy, "供应商指令", "自定义标签规则")
    assert "自定义标签规则" in integrated
    assert "供应商指令" in integrated
    assert "每段朗读控制在 240 字以内" in integrated


def test_tts_provider_direction_uses_the_role_override():
    from astrbot_host.fish_audio import _direction_for

    policy = SimpleNamespace(direction_mode="fishaudio")
    assert _direction_for(policy, "fishaudio_tts_api", "drama-3",
                          {"voice_drama": "只读自定义 Drama"}) == "只读自定义 Drama"
    assert _direction_for(policy, "fishaudio_tts_api", "drama-3",
                          {"voice_indextts": "不应生效"}) == catalog.DRAMA_DIRECTION
    policy = SimpleNamespace(direction_mode="indextts2")
    assert _direction_for(policy, "t2s_index", "indextts2",
                          {"voice_indextts": "自定义情绪"}) == "自定义情绪"
