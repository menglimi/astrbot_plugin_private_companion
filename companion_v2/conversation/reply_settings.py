"""Reply preferences captured when inputs seal, independent of the host profile."""

from dataclasses import asdict, replace
import json
import math

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError, EffectiveValue
from companion_v2.configuration.role_settings import RoleSharedSettingsOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import require
from companion_v2.foundation.refs import ScopeKind, ScopeRef

from .segmentation import SegmentationPolicy
from .segmentation import PART_GAP_MODES
from .outbound_plan import component_order
from .private_closing import DEFAULTS as PRIVATE_CLOSING_DEFAULTS
from .private_closing import LIMITS as PRIVATE_CLOSING_LIMITS
from .private_closing import Settings as PrivateClosingSettings
from .private_debounce import DEFAULTS as PRIVATE_DEBOUNCE_DEFAULTS
from .private_debounce import LIMITS as PRIVATE_DEBOUNCE_LIMITS
from .private_debounce import Policy as PrivateDebounceSettings
from .text_processing import TEXT_DEFAULTS, text_policy
from .voice_reply import VoiceReplyPolicy
from .media.understanding import (
    DEFAULTS as UNDERSTANDING_DEFAULTS,
    LIMITS as UNDERSTANDING_LIMITS,
    format_groups_for_legacy,
    settings as understanding_settings,
)


GROUP = "conversation.presentation"
OWNER = "conversation.presentation"
CONFIGURATION_LAYERS = (ConfigLayer.ROLE, ConfigLayer.WINDOW)
ROLE_PROMPT_FIELDS = frozenset(("style_prompt", "humanized_expression_prompt"))
RULES_FIELD = GROUP + ".rules_enabled"
IMAGE_POSITIONS = frozenset(("after_text", "before_text"))
VOICE_FIELDS = tuple(key for key in asdict(VoiceReplyPolicy())
                     if key not in ("private_probability", "group_probability",
                                    "private_cooldown_seconds", "group_cooldown_seconds"))
GROUP_AIR_DEFAULTS = {"group_air_guard_enabled": True, "group_air_guard_window_seconds": 60,
                      "group_air_guard_max_bot_replies": 3, "group_air_guard_cooldown_seconds": 150,
                      "group_air_guard_polite_loop_limit": 2, "group_high_intensity_enabled": False}
GROUP_AIR_LIMITS = {"group_air_guard_window_seconds": (30, 1800),
                    "group_air_guard_max_bot_replies": (1, 20),
                    "group_air_guard_cooldown_seconds": (0, 1800),
                    "group_air_guard_polite_loop_limit": (1, 10)}
GROUP_CONTINUATION_DEFAULTS = {"group_conversation_followup_enabled": True,
                               "group_conversation_followup_seconds": 120,
                               "group_conversation_followup_max_turns": 3}
GROUP_CONTINUATION_LIMITS = {"group_conversation_followup_seconds": (15, 1800),
                             "group_conversation_followup_max_turns": (1, 12)}
GROUP_WAKEUP_LEGACY_FIELDS = frozenset(("group_wakeup_enabled", "group_wakeup_direct_words",
                                        "group_wakeup_context_words", "group_wakeup_interest_keywords",
                                        "group_wakeup_question_enabled"))
GROUP_WAKEUP_DEFAULTS = {"group_wakeup_enabled": True, "group_wakeup_direct_words": "[]",
                         "group_wakeup_context_words": "[]",
                         "group_wakeup_interest_keywords": "[]", "group_wakeup_question_enabled": False,
                         "group_wakeup_bot_name": "", "group_wakeup_primary_member_ids": "[]",
                         "group_wakeup_owner_direct_words": "[]",
                         "group_wakeup_interest_probability": 0.18, "group_wakeup_question_threshold": 65,
                         "group_wakeup_cold_group_enabled": False, "group_wakeup_cold_group_threshold": 65,
                         "group_wakeup_cold_group_idle_minutes": 25, "group_wakeup_cooldown_seconds": 90,
                         "group_wakeup_fatigue_limit": 5, "group_wakeup_fatigue_decay_minutes": 90,
                         "group_wakeup_bot_name_enabled": True,
                         "group_wakeup_generated_keyword_limit": 24,
                         "group_wakeup_topic_interest_max_boost": 45,
                         "group_wakeup_debounce_pending_penalty": 65,
                         "group_wakeup_image_enabled": False,
                         "group_wakeup_image_weight": 0.2,
                         "group_wakeup_image_default_guidance_enabled": True,
                         "group_wakeup_short_text_wait_ms": 15000, "group_wakeup_log_limit": 80}
GROUP_WAKEUP_ADDED_FIELDS = frozenset(("group_wakeup_bot_name_enabled",
    "group_wakeup_generated_keyword_limit", "group_wakeup_topic_interest_max_boost",
    "group_wakeup_debounce_pending_penalty"))
GROUP_WAKEUP_TIMING_FIELDS = frozenset(("group_wakeup_short_text_wait_ms", "group_wakeup_log_limit"))
GROUP_WAKEUP_PROMPT_FIELDS = frozenset(GROUP_WAKEUP_DEFAULTS) - GROUP_WAKEUP_TIMING_FIELDS
GROUP_WAKEUP_PREVIOUS_FIELDS = GROUP_WAKEUP_PROMPT_FIELDS - GROUP_WAKEUP_ADDED_FIELDS
GROUP_WAKEUP_WORD_FIELDS = ("group_wakeup_direct_words", "group_wakeup_context_words",
                            "group_wakeup_interest_keywords", "group_wakeup_owner_direct_words")
GROUP_WAKEUP_GROUP_FIELDS = frozenset(("group_wakeup_primary_member_ids", "group_wakeup_owner_direct_words"))
GROUP_WAKEUP_LIMITS = {"group_wakeup_question_threshold": (0, 100),
                       "group_wakeup_cold_group_threshold": (0, 100),
                       "group_wakeup_cold_group_idle_minutes": (3, 720),
                       "group_wakeup_cooldown_seconds": (0, 3600),
                       "group_wakeup_fatigue_limit": (1, 20),
                       "group_wakeup_fatigue_decay_minutes": (5, 720),
                       "group_wakeup_generated_keyword_limit": (4, 80),
                       "group_wakeup_topic_interest_max_boost": (0, 150),
                       "group_wakeup_debounce_pending_penalty": (0, 100),
                       "group_wakeup_short_text_wait_ms": (0, 30000), "group_wakeup_log_limit": (10, 300)}

# One window-level tool manifest, resolved once per admitted turn.  Keeping the
# list static is what preserves the provider's cached tool prefix: the plugin
# never adds or removes a tool because one message happens to carry an image,
# mention or follow-up.
GROUP_TOOL_PROFILES = frozenset(("lean", "standard", "full"))
GROUP_TOOL_DEFAULTS = {"group_tool_profile": "full"}

# These switches are deliberately part of the sealed reply snapshot.  The
# prompt and the outbound policy must agree about the group audience that was
# authorized when the turn started; reading live persona settings here would
# make a reply change meaning halfway through generation.
GROUP_SAFETY_DEFAULTS = {"enable_group_injection_guard": True,
                         "enable_group_privacy_guard": True,
                         "enable_group_reality_promise_guard": True}

# Master switches for the decision page's silence policy. Detail windows remain
# in their own sections; this is the one place that says whether that source may
# cancel a reply at all.
SILENCE_DEFAULTS = {"recall_silence_enabled": True}

# What one reply may carry beyond its text and originals. The component order
# replaces the old four-value position switches only when it is non-empty, so an
# installation that never edits it keeps its current placement; the quote
# switches are off by default for the same reason V1's master switch was off.
OUTBOUND_DEFAULTS = {"component_order": "[]", "reply_quote_enabled": False,
                     "reply_quote_group_enabled": True, "reply_quote_first_part_only": True,
                     "reply_quote_skip_chars": 0, "reply_mention_speaker_enabled": False}
OUTBOUND_LIMITS = {"reply_quote_skip_chars": (0, 120)}
OUTBOUND_BOOLEAN_FIELDS = ("reply_quote_enabled", "reply_quote_group_enabled",
                           "reply_quote_first_part_only", "reply_mention_speaker_enabled")
FORWARD_DEFAULTS = {"forward_message_adaptation_enabled": True}
SPACING_DEFAULTS = {"part_gap_mode": "log", "part_gap_min_ms": 1500,
                    "part_gap_max_ms": 3500, "part_gap_log_base": 2.1}
SPACING_FIELDS = frozenset(SPACING_DEFAULTS)
SEGMENTATION_SHARED_FIELDS = frozenset(asdict(SegmentationPolicy())) | SPACING_FIELDS | frozenset(("part_gap_ms",))
TEXT_CLEANUP_SHARED_FIELDS = frozenset(
    key for key in TEXT_DEFAULTS if key.startswith(("content_cleanup_", "content_replacement")))
CONTEXT_IMAGE_DEFAULTS = {"enable_context_image_captioning": False,
                          "context_image_caption_max_items": 2}
IMAGE_NARRATION_DEFAULTS = {"enable_private_image_self_recognition": False,
    "enable_private_image_vision_cache": True, "private_image_vision_cache_max_items": 300,
    "private_image_vision_custom_prompt": "", "private_image_vision_max_chars": 1200,
    "private_image_vision_wait_seconds": 8,
    "private_image_provider_failure_cooldown_seconds": 60,
    "private_image_self_recognition_hint": ""}
DIAGNOSTIC_DEFAULTS = {"reply_diagnostic_limit": 80}
ROLE_SHARED_FIELDS = (frozenset(asdict(SegmentationPolicy())) | SPACING_FIELDS
    | frozenset(("streaming", "part_gap_ms", "image_position", "rules_enabled"))
    | frozenset(("voice_enabled", *("voice_" + key for key in VOICE_FIELDS)))
    | frozenset(GROUP_AIR_DEFAULTS)
    | frozenset(GROUP_CONTINUATION_DEFAULTS) | frozenset(PRIVATE_CLOSING_DEFAULTS)
    | frozenset(PRIVATE_DEBOUNCE_DEFAULTS)
    | (frozenset(GROUP_WAKEUP_DEFAULTS) - GROUP_WAKEUP_GROUP_FIELDS)
    | frozenset(GROUP_SAFETY_DEFAULTS) | frozenset(SILENCE_DEFAULTS) | frozenset(OUTBOUND_DEFAULTS) | frozenset(TEXT_DEFAULTS)
    | frozenset(FORWARD_DEFAULTS) | frozenset(CONTEXT_IMAGE_DEFAULTS)
    | frozenset(IMAGE_NARRATION_DEFAULTS) | frozenset(UNDERSTANDING_DEFAULTS)
    | frozenset(DIAGNOSTIC_DEFAULTS) | frozenset(GROUP_TOOL_DEFAULTS) | ROLE_PROMPT_FIELDS)


def outbound_settings(values):
    settings = {key: values.get(key, default) for key, default in OUTBOUND_DEFAULTS.items()}
    if any(type(settings[key]) is not bool for key in OUTBOUND_BOOLEAN_FIELDS):
        raise ConfigurationError("reply_configuration_invalid", "outbound switches must be boolean")
    for key, (minimum, maximum) in OUTBOUND_LIMITS.items():
        if type(settings[key]) is not int or not minimum <= settings[key] <= maximum:
            raise ConfigurationError("reply_configuration_invalid", "invalid outbound quote setting")
    try:
        component_order(settings)
    except ValueError as error:
        raise ConfigurationError("reply_configuration_invalid", str(error)) from error
    return settings


def group_safety_settings(values):
    settings = {key: values.get(key, default) for key, default in GROUP_SAFETY_DEFAULTS.items()}
    if any(type(value) is not bool for value in settings.values()):
        raise ConfigurationError("reply_configuration_invalid", "group safety switches must be boolean")
    return settings


def silence_settings(values):
    settings = {key: values.get(key, default) for key, default in SILENCE_DEFAULTS.items()}
    if any(type(value) is not bool for value in settings.values()):
        raise ConfigurationError("reply_configuration_invalid", "silence switches must be boolean")
    return settings


def group_air_settings(values):
    settings = {key: values.get(key, default) for key, default in GROUP_AIR_DEFAULTS.items()}
    if (type(settings["group_air_guard_enabled"]) is not bool
            or type(settings["group_high_intensity_enabled"]) is not bool):
        raise ConfigurationError("reply_configuration_invalid", "group air guard enabled must be boolean")
    for key, (minimum, maximum) in GROUP_AIR_LIMITS.items():
        if type(settings[key]) is not int or not minimum <= settings[key] <= maximum:
            raise ConfigurationError("reply_configuration_invalid", "invalid group air guard setting")
    return settings


def group_continuation_settings(values):
    settings = {key: values.get(key, default) for key, default in GROUP_CONTINUATION_DEFAULTS.items()}
    if type(settings["group_conversation_followup_enabled"]) is not bool:
        raise ConfigurationError("reply_configuration_invalid", "group conversation follow-up enabled must be boolean")
    for key, (minimum, maximum) in GROUP_CONTINUATION_LIMITS.items():
        if type(settings[key]) is not int or not minimum <= settings[key] <= maximum:
            raise ConfigurationError("reply_configuration_invalid", "invalid group conversation follow-up setting")
    return settings


def private_debounce_settings(values):
    settings = {key: values.get(key, default) for key, default in PRIVATE_DEBOUNCE_DEFAULTS.items()}
    if type(settings["private_debounce_enabled"]) is not bool:
        raise ConfigurationError("reply_configuration_invalid",
                                 "private debounce enabled must be boolean")
    for key, (minimum, maximum) in PRIVATE_DEBOUNCE_LIMITS.items():
        if type(settings[key]) is not int or not minimum <= settings[key] <= maximum:
            raise ConfigurationError("reply_configuration_invalid",
                                     "private debounce timing is out of range")
    try:
        return PrivateDebounceSettings.from_milliseconds(
            settings["private_debounce_enabled"], settings["private_debounce_min_ms"],
            settings["private_debounce_max_ms"], settings["private_debounce_total_ms"])
    except ValueError as error:
        raise ConfigurationError("reply_configuration_invalid",
                                 "private debounce timing is invalid") from error


def group_wakeup_settings(values):
    settings = {key: values.get(key, default) for key, default in GROUP_WAKEUP_DEFAULTS.items()}
    if any(type(settings[key]) is not bool for key in ("group_wakeup_enabled", "group_wakeup_question_enabled",
                                                    "group_wakeup_cold_group_enabled", "group_wakeup_bot_name_enabled",
                                                    "group_wakeup_image_enabled",
                                                    "group_wakeup_image_default_guidance_enabled")):
        raise ConfigurationError("reply_configuration_invalid", "group wakeup switches must be boolean")
    for key in GROUP_WAKEUP_WORD_FIELDS:
        raw = settings[key]
        try:
            words = json.loads(raw) if type(raw) is str and len(raw.encode("utf-8")) <= 2048 else None
        except (ValueError, TypeError):
            words = None
        if (type(words) is not list or len(words) > 24
                or any(type(word) is not str or word != word.strip() or not 1 <= len(word) <= 40
                       or any(ord(char) < 32 for char in word) for word in words)
                or len({word.casefold() for word in words}) != len(words)):
            raise ConfigurationError("reply_configuration_invalid", "group wakeup words require a short JSON string list")
        settings[key] = tuple(words)
    name = settings["group_wakeup_bot_name"]
    if (type(name) is not str or len(name) > 40 or name != name.strip()
            or any(ord(char) < 32 for char in name)):
        raise ConfigurationError("reply_configuration_invalid", "group bot name must be a short display name")
    raw = settings["group_wakeup_primary_member_ids"]
    try:
        members = json.loads(raw) if type(raw) is str and len(raw.encode("utf-8")) <= 512 else None
    except (ValueError, TypeError):
        members = None
    if (type(members) is not list or len(members) > 16
            or any(type(member) is not str or not member.isascii() or not member.isdecimal()
                   or not 1 <= len(member) <= 20 or member.startswith("0") for member in members)
            or len(set(members)) != len(members)):
        raise ConfigurationError("reply_configuration_invalid", "primary group members require account IDs")
    settings["group_wakeup_primary_member_ids"] = tuple(members)
    chance = settings["group_wakeup_interest_probability"]
    if type(chance) not in (int, float) or not 0 <= chance <= 1 or not math.isfinite(chance):
        raise ConfigurationError("reply_configuration_invalid", "invalid group interest probability")
    image_weight = settings["group_wakeup_image_weight"]
    if (type(image_weight) not in (int, float) or not 0 <= image_weight <= 1
            or not math.isfinite(image_weight)):
        raise ConfigurationError("reply_configuration_invalid", "invalid group image wakeup weight")
    for key, (minimum, maximum) in GROUP_WAKEUP_LIMITS.items():
        if type(settings[key]) is not int or not minimum <= settings[key] <= maximum:
            raise ConfigurationError("reply_configuration_invalid", "invalid group wakeup setting")
    return settings


def voice_settings(policy=None):
    return {"voice_enabled": policy is not None,
            **{"voice_" + key: value for key, value in asdict(policy or VoiceReplyPolicy()).items()
               if key in VOICE_FIELDS}}


def voice_policy(values):
    defaults = VoiceReplyPolicy()
    policy = VoiceReplyPolicy(**{key: values.get("voice_" + key, getattr(defaults, key)) for key in VOICE_FIELDS})
    if type(values["voice_enabled"]) is not bool:
        raise ValueError("voice enabled must be boolean")
    return policy if values["voice_enabled"] else None


def target_for(context, layer=ConfigLayer.WINDOW):
    if layer is ConfigLayer.ROLE:
        return ConfigTarget(context.scope.installation_id, layer, context.role_binding.role,
                            bot_id=context.scope.bot_id)
    if layer is not ConfigLayer.WINDOW:
        raise ValueError("reply configuration supports role defaults or one window")
    return ConfigTarget(context.scope.installation_id, layer, context.role_binding.role, context.scope)


def validate(values):
    if type(values) is not dict:
        raise ConfigurationError("reply_configuration_invalid", "provide the declared reply settings")
    segmentation_defaults = asdict(SegmentationPolicy())
    expected = set(segmentation_defaults) | {"streaming", "part_gap_ms"}
    legacy_required = expected - {"split_words", "match_width_variants", "rule_regex", "streaming_mode"}
    voice = set(voice_settings())
    air = set(GROUP_AIR_DEFAULTS)
    continuation = set(GROUP_CONTINUATION_DEFAULTS)
    private_closing = set(PRIVATE_CLOSING_DEFAULTS)
    private_debounce = set(PRIVATE_DEBOUNCE_DEFAULTS)
    wakeup = set(GROUP_WAKEUP_DEFAULTS)
    safety = set(GROUP_SAFETY_DEFAULTS)
    silence = set(SILENCE_DEFAULTS)
    outbound = set(OUTBOUND_DEFAULTS)
    forward = set(FORWARD_DEFAULTS)
    presentation = set(TEXT_DEFAULTS)
    captions = set(CONTEXT_IMAGE_DEFAULTS)
    image_narration = set(IMAGE_NARRATION_DEFAULTS)
    understanding = set(UNDERSTANDING_DEFAULTS)
    spacing = set(SPACING_DEFAULTS)
    diagnostics = set(DIAGNOSTIC_DEFAULTS)
    tool = set(GROUP_TOOL_DEFAULTS)
    optional = set(values) - expected - {"image_position"}
    provided = set(values)
    if (not legacy_required <= provided
            or optional - voice - air - continuation - private_closing - private_debounce - wakeup - safety - silence - presentation - outbound - forward - captions - image_narration - understanding - spacing - diagnostics - tool
            or (optional & voice and not (voice - {"voice_conversion_scope"}) <= optional)
            or any(optional & block and not block <= optional for block in (air, continuation, private_closing, private_debounce, presentation))
            or optional & spacing not in (set(), spacing)
            or optional & forward not in (set(), forward)
            or optional & wakeup not in (set(), set(GROUP_WAKEUP_LEGACY_FIELDS),
                                        set(GROUP_WAKEUP_PREVIOUS_FIELDS), set(GROUP_WAKEUP_PROMPT_FIELDS), wakeup)):
        raise ConfigurationError("reply_configuration_invalid", "provide the declared reply settings")
    try:
        if values.get("group_tool_profile", "full") not in GROUP_TOOL_PROFILES:
            raise ValueError("invalid group tool profile")
        diagnostic_limit = values.get("reply_diagnostic_limit", DIAGNOSTIC_DEFAULTS["reply_diagnostic_limit"])
        if type(diagnostic_limit) is not int or not 10 <= diagnostic_limit <= 300:
            raise ValueError("invalid reply diagnostic limit")
        policy = SegmentationPolicy(**{key: values.get(key, default)
                                       for key, default in segmentation_defaults.items()})
        if type(values["streaming"]) is not bool or type(values["part_gap_ms"]) is not int or not 0 <= values["part_gap_ms"] <= 60000:
            raise ValueError("invalid transport or spacing")
        if values.get("image_position", "after_text") not in IMAGE_POSITIONS:
            raise ValueError("invalid image position")
        spacing_values = {**SPACING_DEFAULTS, **{name: values[name] for name in spacing if name in values}}
        if (spacing_values["part_gap_mode"] not in PART_GAP_MODES
                or type(spacing_values["part_gap_min_ms"]) is not int
                or type(spacing_values["part_gap_max_ms"]) is not int
                or not 100 <= spacing_values["part_gap_min_ms"] <= 60000
                or not 100 <= spacing_values["part_gap_max_ms"] <= 60000
                or spacing_values["part_gap_min_ms"] > spacing_values["part_gap_max_ms"]
                or type(spacing_values["part_gap_log_base"]) not in (int, float)
                or not 1.1 <= spacing_values["part_gap_log_base"] <= 5.0):
            raise ValueError("invalid part spacing settings")
        if "voice_enabled" in values:
            voice_policy(values)
        group_air_settings(values)
        group_continuation_settings(values)
        PrivateClosingSettings.from_values(values)
        private_debounce_settings(values)
        group_wakeup_settings(values)
        group_safety_settings(values)
        silence_settings(values)
        outbound_settings(values)
        forward_values = {**FORWARD_DEFAULTS, **{name: values[name] for name in forward if name in values}}
        if type(forward_values["forward_message_adaptation_enabled"]) is not bool:
            raise ValueError("invalid forward message setting")
        text_policy(values)
        understanding_settings(values)
        if (type(values.get("enable_context_image_captioning", False)) is not bool
                or type(values.get("context_image_caption_max_items", 2)) is not int
                or not 0 <= values.get("context_image_caption_max_items", 2) <= 8):
            raise ValueError("invalid historical image caption setting")
        narration = {**IMAGE_NARRATION_DEFAULTS,
                     **{name: values[name] for name in image_narration if name in values}}
        if (type(narration["enable_private_image_self_recognition"]) is not bool
                or type(narration["enable_private_image_vision_cache"]) is not bool
                or type(narration["private_image_vision_cache_max_items"]) is not int
                or not 0 <= narration["private_image_vision_cache_max_items"] <= 2000
                or type(narration["private_image_vision_custom_prompt"]) is not str
                or len(narration["private_image_vision_custom_prompt"]) > 4000
                or type(narration["private_image_vision_max_chars"]) is not int
                or not 120 <= narration["private_image_vision_max_chars"] <= 4000
                or type(narration["private_image_vision_wait_seconds"]) is not int
                or not 1 <= narration["private_image_vision_wait_seconds"] <= 30
                or type(narration["private_image_provider_failure_cooldown_seconds"]) is not int
                or not 0 <= narration["private_image_provider_failure_cooldown_seconds"] <= 3600
                or type(narration["private_image_self_recognition_hint"]) is not str
                or len(narration["private_image_self_recognition_hint"]) > 1200):
            raise ValueError("invalid image narration setting")
    except (ValueError, TypeError) as error:
        raise ConfigurationError("reply_configuration_invalid", "invalid reply settings or prompt length") from error
    return policy


def expression_prompt_sections():
    from companion_v2.model_gateway.models import PromptSection
    return (
        PromptSection("style", "回复风格补充", TEXT_DEFAULTS["style_prompt"],
                      "填写后追加到 AstrBot 核心回复提示", GROUP, "style_prompt"),
        PromptSection("humanized", "日常表达", TEXT_DEFAULTS["humanized_expression_prompt"],
                      "开启日常表达时生效；开关在表达设置中调整", GROUP, "humanized_expression_prompt"),
    )


def configuration_fields(segmentation, streaming, gap, voice=None):
    defaults = {**asdict(segmentation), "streaming": streaming, "part_gap_ms": round(gap * 1000),
              **SPACING_DEFAULTS, "image_position": "after_text", **voice_settings(voice), **GROUP_AIR_DEFAULTS,
              **GROUP_CONTINUATION_DEFAULTS, **PRIVATE_CLOSING_DEFAULTS,
              **PRIVATE_DEBOUNCE_DEFAULTS, **GROUP_WAKEUP_DEFAULTS,
              **GROUP_SAFETY_DEFAULTS, **SILENCE_DEFAULTS, **OUTBOUND_DEFAULTS, **TEXT_DEFAULTS}
    # The field remains in the backend contract so older snapshots can still
    # be read, but it is no longer a choice: deterministic rule completion is
    # always active whenever segmentation itself is enabled.
    defaults["rules_enabled"] = True
    defaults.update(FORWARD_DEFAULTS)
    defaults.update(CONTEXT_IMAGE_DEFAULTS)
    defaults.update(IMAGE_NARRATION_DEFAULTS)
    defaults.update(UNDERSTANDING_DEFAULTS)
    defaults.update(DIAGNOSTIC_DEFAULTS)
    defaults.update(GROUP_TOOL_DEFAULTS)
    validate(defaults)
    limits = {"threshold": (1, 65536), "min_segment_chars": (1, 65536), "max_segments": (1, 64),
              "part_gap_ms": (0, 60000), "voice_cooldown_seconds": (0, 86400),
              "part_gap_min_ms": (100, 60000), "part_gap_max_ms": (100, 60000),
              "part_gap_log_base": (1.1, 5.0),
              "voice_max_speech_chars": (1, 10000), "voice_timeout_seconds": (1, 120),
              "reply_diagnostic_limit": (10, 300),
              **GROUP_AIR_LIMITS, **GROUP_CONTINUATION_LIMITS, **PRIVATE_CLOSING_LIMITS,
              **PRIVATE_DEBOUNCE_LIMITS, **GROUP_WAKEUP_LIMITS, **OUTBOUND_LIMITS}
    limits["context_image_caption_max_items"] = (0, 8)
    limits.update(UNDERSTANDING_LIMITS)
    limits.update({"private_image_vision_cache_max_items": (0, 2000),
                   "private_image_vision_max_chars": (120, 4000),
                   "private_image_vision_wait_seconds": (1, 30),
                   "private_image_provider_failure_cooldown_seconds": (0, 3600)})
    return tuple(FieldDefinition(GROUP + "." + name, GROUP, GROUP,
        ValueKind.WEIGHT if name == "part_gap_log_base" else
        ValueKind.PROBABILITY if name in ("voice_probability", "voice_primary_mention_probability",
                                          "group_wakeup_interest_probability") else
        {bool: ValueKind.BOOLEAN, int: ValueKind.INTEGER, float: ValueKind.PROBABILITY, str: ValueKind.TEXT}[type(value)], value,
        layers=CONFIGURATION_LAYERS, owner_id=OWNER,
        **(dict(zip(("minimum", "maximum"), limits[name])) if name in limits else {}),
        **({"decimal_places": 1} if name == "part_gap_log_base" else {}))
        for name, value in defaults.items())


class ReplyConfigurationOwner(RoleSharedSettingsOwner):
    owner_id = OWNER

    def is_shared(self, field):
        name = field.field_id.rsplit(".", 1)[-1]
        return name in ROLE_SHARED_FIELDS

    def role_value_overrides_window(self, field):
        return False

    def role_explicit_value_overrides_window(self, field):
        return False

    def adopt_window_value_on_write(self, field, changed_fields):
        return False

    def window_write_role_default(self, field):
        return False

    def window_reset_role_default(self, field):
        return False

    @staticmethod
    def _stable_group_target(target):
        scope = target.scope
        if (target.layer is not ConfigLayer.WINDOW or scope is None
                or scope.kind is not ScopeKind.GROUP):
            return target
        stable_session = scope.session_id or scope.native_scope_id
        stable_scope = replace(scope, conversation_id=stable_session, session_id=stable_session)
        if stable_scope == scope:
            return target
        return ConfigTarget(target.installation_id, ConfigLayer.WINDOW, target.role, stable_scope,
                            bot_id=target.bot_id)

    def _legacy_group_values(self, target, fields):
        scope = target.scope
        if (target.layer is not ConfigLayer.WINDOW or scope is None
                or scope.kind is not ScopeKind.GROUP or target.role is None):
            return {}
        identifiers = tuple(field.field_id for field in fields)
        if not identifiers:
            return {}
        placeholders = ",".join("?" for _ in identifiers)
        rows = self.runtime.read_all(f"""SELECT field_id, value_json, revision, target_json FROM (
                SELECT v.field_id, v.value_json, v.revision, t.target_json,
                    ROW_NUMBER() OVER (PARTITION BY v.field_id ORDER BY t.updated_at DESC, t.target_key DESC) AS rank
                FROM configuration_values v JOIN configuration_targets t USING (target_key)
                WHERE v.field_id IN ({placeholders})
                  AND json_extract(t.target_json, '$.layer') = 'window'
                  AND json_extract(t.target_json, '$.role_id') = ?
                  AND json_extract(t.target_json, '$.scope.platform') = ?
                  AND json_extract(t.target_json, '$.scope.kind') = 'group'
                  AND json_extract(t.target_json, '$.scope.native_scope_id') = ?
                  AND json_extract(t.target_json, '$.scope.bot_id') = ?
                  AND json_extract(t.target_json, '$.scope.account_id') = ?
                  AND json_extract(t.target_json, '$.scope.session_id') = ?
                  AND json_extract(t.target_json, '$.scope.conversation_id') <> ?
            ) WHERE rank = 1""",
            (*identifiers, target.role.subject_id, scope.platform, scope.native_scope_id,
             scope.bot_id, scope.account_id, scope.session_id, scope.conversation_id))
        definitions = {field.field_id: field for field in fields}
        result = {}
        for row in rows:
            definition = definitions[row["field_id"]]
            try:
                value = definition.validate(json.loads(row["value_json"]))
                document = json.loads(row["target_json"])
                legacy_scope = ScopeRef.restore(document["scope"])
                source = ConfigTarget(document["installation_id"], ConfigLayer.WINDOW,
                    target.role, legacy_scope, bot_id=document.get("bot_id"))
            except (ConfigurationError, KeyError, TypeError, ValueError):
                continue
            result[row["field_id"]] = EffectiveValue(value, source, True, row["revision"])
        return result

    def _clear_legacy_group_values(self, target, field_ids):
        scope = target.scope
        if (not field_ids or target.layer is not ConfigLayer.WINDOW or scope is None
                or scope.kind is not ScopeKind.GROUP or target.role is None):
            return
        rows = self.runtime.read_all("""SELECT target_key FROM configuration_targets
            WHERE json_extract(target_json, '$.layer') = 'window'
              AND json_extract(target_json, '$.role_id') = ?
              AND json_extract(target_json, '$.scope.platform') = ?
              AND json_extract(target_json, '$.scope.kind') = 'group'
              AND json_extract(target_json, '$.scope.native_scope_id') = ?
              AND json_extract(target_json, '$.scope.bot_id') = ?
              AND json_extract(target_json, '$.scope.account_id') = ?
              AND json_extract(target_json, '$.scope.session_id') = ?
              AND json_extract(target_json, '$.scope.conversation_id') <> ?""",
            (target.role.subject_id, scope.platform, scope.native_scope_id, scope.bot_id,
             scope.account_id, scope.session_id, scope.conversation_id))
        placeholders = ",".join("?" for _ in field_ids)
        with self.runtime.transaction() as connection:
            for row in rows:
                cursor = connection.execute(
                    f"DELETE FROM configuration_values WHERE target_key = ? AND field_id IN ({placeholders})",
                    (row["target_key"], *field_ids))
                if cursor.rowcount:
                    connection.execute(
                        "UPDATE configuration_targets SET revision = revision + 1 WHERE target_key = ?",
                        (row["target_key"],))

    def __init__(self, runtime, voice=None):
        super().__init__(runtime)
        self.voice = voice or VoiceReplyPolicy()

    def read(self, target, fields):
        storage_target = self._stable_group_target(target)
        snapshot = super().read(storage_target, fields)
        legacy = self._legacy_group_values(storage_target, fields)
        if legacy:
            effective = dict(snapshot.fields)
            for field_id, value in legacy.items():
                current = effective[field_id]
                if current.source is None or current.source.layer is ConfigLayer.ROLE:
                    effective[field_id] = value
            snapshot = replace(snapshot, fields=effective)
        rules = snapshot.fields.get(RULES_FIELD)
        if rules is not None and rules.value is not True:
            snapshot = replace(snapshot, fields={**snapshot.fields,
                RULES_FIELD: replace(rules, value=True)})
        key = GROUP + ".voice_probability"
        current = snapshot.fields.get(key)
        if current is not None and current.source is None and target.scope is not None:
            snapshot = replace(snapshot, fields={**snapshot.fields,
                key: replace(current, value=self.voice.chance(target.scope.kind.value))})
        key = GROUP + ".voice_cooldown_seconds"
        current = snapshot.fields.get(key)
        if current is not None and current.source is None and target.scope is not None:
            snapshot = replace(snapshot, fields={**snapshot.fields,
                key: replace(current, value=self.voice.cooldown(target.scope.kind.value))})
        formats_key = GROUP + ".file_understanding_formats"
        groups_key = GROUP + ".file_understanding_format_groups"
        legacy, groups = snapshot.fields.get(formats_key), snapshot.fields.get(groups_key)
        if legacy is not None and groups is not None and not groups.explicit:
            selected = format_groups_for_legacy(legacy.value)
            if selected:
                snapshot = replace(snapshot, fields={**snapshot.fields,
                    groups_key: replace(groups, value="\n".join(selected),
                        source=legacy.source, explicit=legacy.explicit,
                        source_revision=legacy.source_revision)})
        return snapshot

    def preview_set(self, target, fields, *, changes):
        target = self._stable_group_target(target)
        normalized = dict(changes)
        if RULES_FIELD in normalized:
            normalized[RULES_FIELD] = True
        result = super().preview_set(target, fields, changes=normalized)
        validate({key.removeprefix(GROUP + "."): item.value for key, item in result.items()})
        return result

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        target = self._stable_group_target(target)
        normalized = dict(changes)
        if RULES_FIELD in normalized:
            normalized[RULES_FIELD] = True
        with self.runtime.transaction():
            result = super().write(target, fields, changes=normalized, reset=reset,
                expected_revision=expected_revision, observed_at=observed_at)
            self._clear_legacy_group_values(target, reset)
            validate({key.removeprefix(GROUP + "."): item.value for key, item in self.read(target, fields).fields.items()})
            return result


class ReplySettings:
    def __init__(self, configuration):
        self.configuration = configuration

    def snapshot(self, actor, context, at):
        return require(self.configuration.read(actor, target=target_for(context), group_id=GROUP, observed_at=at))

    def capture(self, context, at):
        snapshot = self.snapshot(context.actor, context, at)
        settings = {key.removeprefix(GROUP + "."): value for key, value in snapshot.values.items()}
        validate(settings)
        return {"settings": settings, "configuration_token": snapshot.resolution_token,
                "streaming_explicit": snapshot.fields[GROUP + ".streaming"].explicit}
