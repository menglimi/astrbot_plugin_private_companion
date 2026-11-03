"""Convert audited V1 plugin settings into registered V2 configuration fields."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
from importlib.resources import files
import json
import math
import re
from types import MappingProxyType

from companion_v2.configuration.registry import (
    LegacyProbabilityRule,
    LegacyProbabilityUnit,
    ValueKind,
)
from companion_v2.conversation.segmentation import (
    LEGACY_DEFAULT_SPLIT_WORDS,
    LEGACY_REMOVED_SPLIT_WORDS,
    LEGACY_SPLIT_WORD_POOL,
)
from companion_v2.legacy.catalog import V1_SUPPORTED_VERSION


_BOOL_TRUE = {"true", "yes", "1", "on", "enabled"}
_BOOL_FALSE = {"false", "no", "0", "off", "disabled", ""}
_INT = re.compile(r"^[+-]?\d+$")
_UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}
_MISSING = object()
_WEATHER_CONVERTER_SOURCES = frozenset({
    "weather_config.enable_weather_context",
    "weather_config.weather_location",
    "weather_config.weather_city",
    "weather_config.weather_lat",
    "weather_config.weather_lon",
    "weather_config.weather_refresh_minutes",
    "weather_config.enable_weather_alerts",
    "weather_config.weather_alert_refresh_minutes",
    "weather_config.weather_alert_min_severity",
    "weather_config.enable_environment_change_proactive",
    "weather_config.environment_change_check_minutes",
})
_SCHEDULE_INFLUENCE_ENABLED = "skill_growth_config.enable_skill_growth_schedule_influence"
_SCHEDULE_INFLUENCE_STRENGTH = "skill_growth_config.skill_growth_schedule_influence_strength"
_SCHEDULE_INFLUENCE_TARGET = "role.growth.skills.schedule_influence_strength"
_ENVIRONMENT_PERCEPTION_MASTER = "basic_config.enable_environment_perception"
_ENVIRONMENT_PERCEPTION_TARGETS = (
    "conversation.perception.calendar_enabled",
    "conversation.perception.platform_enabled",
    "conversation.perception.model_route_enabled",
    "conversation.perception.configured_modalities_enabled",
)
_MODEL_PERCEPTION_SOURCE = "basic_config.enable_model_perception"
_MODEL_PERCEPTION_MODALITIES_TARGET = "conversation.perception.configured_modalities_enabled"
_SMART_SILENCE_SOURCE = "emotion_relationship_config.enable_smart_silence"
_PARTIAL_MAPPING_NOTES = {
    "message_debounce_config.enable_private_image_self_recognition":
        "这是部分映射：V2 开关控制额外图片转述与缓存流程；原图仍会进入本轮主回复模型。",
    "group_observation_config.enable_group_image_understanding":
        "这是部分映射：V2 群聊权限控制额外视觉转述与历史摘要；原图仍会进入本轮主回复模型。",
    "experimental_motivation_config.enable_emotion_simulation":
        "这是部分映射：V2 开关只控制角色短期情绪维护；V1 相关的关系边界惩罚、QQ 空间发布等能力分别审计，不由此开关迁移。",
    "humanized_state_config.enable_humanized_states":
        "这是部分映射：V2 role.state.enabled 只控制状态维护；V1 同时控制的日记、梦境等生活能力由各自设置迁移。",
}
_PROACTIVE_AUDIT_TARGETS = frozenset({
    "role.state.planning_weather_enabled",
    "role.state.planning_weather_location",
    "role.state.planning_weather_max_age_minutes",
})
_ACCOUNT_CONFIGURATION_SOURCES = frozenset({
    "basic_config.bot_scope_mode",
    "basic_config.bot_scope_ids",
    "basic_config.plugin_specific_persona_id",
    "basic_config.enable_multi_persona_mode",
    "basic_config.multi_persona_ids",
    "basic_config.target_user_ids",
    "group_observation_config.enable_group_companion",
    "group_observation_config.group_whitelist_ids",
    "legacy_compat_config.target_group_ids",
})
ACCOUNT_CONFIGURATION_TARGETS = frozenset({
    "target.platform_id",
    "target.account_id",
    "target.bot_id",
    "target.participant_id",
    "target.role_id",
    "target.persona_id",
    "multi_host.targets",
    "multi_host.personas",
    "multi_host.group_targets",
})


@dataclass(frozen=True)
class ConfigurationDifference:
    field_id: str
    kind: str
    summary: str
    source_key: str = ""
    audit_disposition: str = ""
    audit_scope: str = ""

    def document(self) -> dict:
        result = {
            "field_id": self.field_id,
            "kind": self.kind,
            "summary": self.summary,
            "source_key": self.source_key,
        }
        if self.audit_disposition:
            result.update({
                "audit": True,
                "disposition": self.audit_disposition,
                "audit_scope": self.audit_scope,
            })
        return result


@dataclass(frozen=True)
class ConfigurationMigration:
    changes: dict[str, object]
    confidence: dict[str, str]
    sources: dict[str, tuple[str, ...]]
    differences: tuple[ConfigurationDifference, ...]


@lru_cache(maxsize=1)
def _configuration_rule_document() -> dict:
    return json.loads(files("companion_v2.legacy").joinpath("configuration_rules.json").read_text(encoding="utf-8"))


def configuration_rules() -> tuple[dict, ...]:
    """Load the generated, package-local mapping table once per process."""
    if not hasattr(configuration_rules, "_cached"):
        document = _configuration_rule_document()
        configuration_rules._cached = tuple(MappingProxyType(rule) for rule in document["rules"])
    return configuration_rules._cached


def configuration_exclusions() -> tuple[dict, ...]:
    """Audited V1 settings that must not be mapped to a similar V2 field."""
    if not hasattr(configuration_exclusions, "_cached"):
        document = _configuration_rule_document()
        configuration_exclusions._cached = tuple(
            MappingProxyType(rule) for rule in document.get("exclusions", ()))
    return configuration_exclusions._cached


def plugin_configuration_rules() -> tuple[dict, ...]:
    """Rules whose destination is a direct field in the V2 plugin config."""
    if not hasattr(plugin_configuration_rules, "_cached"):
        document = _configuration_rule_document()
        plugin_configuration_rules._cached = tuple(
            MappingProxyType(rule) for rule in document.get("plugin_rules", ()))
    return plugin_configuration_rules._cached


def semantic_configuration_rules() -> tuple[dict, ...]:
    """Rules whose destination is a direct field in the V2 semantic catalog."""
    if not hasattr(semantic_configuration_rules, "_cached"):
        document = _configuration_rule_document()
        semantic_configuration_rules._cached = tuple(
            MappingProxyType(rule) for rule in document.get("semantic_rules", ()))
    return semantic_configuration_rules._cached


@lru_cache(maxsize=1)
def configuration_audit_sources() -> tuple[dict, ...]:
    """All versioned V1 configuration keys, including keys without an auto-mapper."""
    document = _configuration_rule_document()
    return tuple(MappingProxyType(item) for item in document.get("audit_sources", ()))


def automatically_handled_configuration_sources() -> frozenset[str]:
    """Sources with a converter, a deliberate exclusion, or a specialized handler."""
    sources = {
        rule["source"] for rule in (
            *configuration_rules(), *plugin_configuration_rules(),
            *semantic_configuration_rules(),
        )
    }
    sources.update(_ACCOUNT_CONFIGURATION_SOURCES)
    sources.update(_WEATHER_CONVERTER_SOURCES)
    sources.update((_SCHEDULE_INFLUENCE_ENABLED, _SCHEDULE_INFLUENCE_STRENGTH))
    sources.add(_ENVIRONMENT_PERCEPTION_MASTER)
    from .proactive_configuration import DEFAULT_ENABLED_SOURCE
    sources.add(DEFAULT_ENABLED_SOURCE)
    sources.update(_SEGMENT_SPECIALIZED_SOURCES)
    from .reply_configuration import PRIVATE_DEBOUNCE_SOURCES
    sources.update(PRIVATE_DEBOUNCE_SOURCES)
    from .image_configuration_migration import IMAGE_CONFIGURATION_SOURCES
    sources.update(IMAGE_CONFIGURATION_SOURCES)
    sources.add("voice_action_config.tts_frequency_control_mode")
    # Hand-mapped by behavior_configuration (old per-list capacity ×2); the
    # audit ledger must not also report it as an unmapped setting.
    sources.add("memory_habit_config.max_learned_expression_items")
    sources.update(("model_assignment_config.model_replacement_rules",
        "model_assignment_config.model_replacement_scope",
        "model_assignment_config.enable_sensitive_model_replacement",
        "model_assignment_config.SENSITIVE_REPLACEMENT_PROVIDER_ID",
        "model_assignment_config.sensitive_replacement_keywords", "model_timeout_overrides"))
    return frozenset(sources)


def _audit_disposition(entry: dict) -> str:
    verdict = entry.get("verdict")
    if verdict == "implemented-elsewhere":
        return "implemented_elsewhere"
    if verdict == "not-applicable":
        return "not_applicable"
    if verdict == "owed":
        return "manual_required"
    if verdict == "not-migrated-by-design":
        return "not_migrated"
    if entry.get("resolved_kind") == "external":
        return "manual_mapping"
    return "not_migrated"


def _audit_scope(entry: dict) -> str:
    target = entry.get("target", "")
    source = entry.get("source", "")
    if (target.startswith("proactive.contact.") or target in _PROACTIVE_AUDIT_TARGETS
            or source in _WEATHER_CONVERTER_SOURCES or source == _SMART_SILENCE_SOURCE):
        return "proactive"
    return "all"


def unmapped_configuration_differences(config: dict) -> tuple[ConfigurationDifference, ...]:
    """Explain every present audited source that has no automatic conversion path.

    Only key names and reviewed audit summaries are returned. Source values (which may
    include credentials or private prompts) are never copied into migration diagnostics.
    """
    if not isinstance(config, dict):
        return ()
    handled = automatically_handled_configuration_sources()
    differences = []
    for entry in configuration_audit_sources():
        source = entry["source"]
        if source in handled or not _source_value(config, source)[0]:
            continue
        disposition = _audit_disposition(entry)
        differences.append(ConfigurationDifference(
            entry.get("target") or source,
            disposition,
            entry.get("summary") or "该设置没有经过核实的 V2 自动转换规则。",
            source,
            disposition,
            _audit_scope(entry),
        ))
    return tuple(differences)


def plugin_configuration_defaults() -> dict[str, object]:
    """Schema defaults used to distinguish untouched compatibility values from V2 edits."""
    defaults = {rule["target"]: rule["contract"].get("default")
                for rule in plugin_configuration_rules() if "default" in rule["contract"]}
    defaults.update(_configuration_rule_document().get("plugin_defaults", {}))
    return defaults


def _source_value(config: dict, source: str):
    """Read a legacy setting from its nested path, with flat-key fallback.

    V1 mostly stored settings as ``group -> leaf`` objects, but a few settings
    (notably relationship-stage routes) have another nested object below the
    group.  The old one-dot lookup silently treated those values as absent and
    then reported them as if the user had never configured them.
    """
    parts = tuple(part for part in source.split(".") if part)
    nested = config
    for part in parts:
        if not isinstance(nested, dict) or part not in nested:
            nested = _MISSING
            break
        nested = nested[part]
    if nested is not _MISSING:
        return True, nested
    if source in config:
        return True, config[source]
    if len(parts) == 2 and parts[0] == "weather_config" and parts[1] in config:
        return True, config[parts[1]]
    return False, None


def _source_precedence_conflict(config: dict, source: str) -> bool:
    """Whether a nested value and its flat compatibility alias disagree."""
    parts = tuple(part for part in source.split(".") if part)
    nested = config
    for part in parts:
        if not isinstance(nested, dict) or part not in nested:
            return False
        nested = nested[part]
    return source in config and nested != config[source]


def _task_provider_target(target: str) -> bool:
    """Whether one V2 field *is* a model task's provider selection."""
    return target.startswith("model.") and target.endswith(".provider")


def _unset_provider(value: object) -> bool:
    """Whether a legacy provider value means "follow the default".

    V1 spelled the default route as nothing at all, an empty string, or blanks.
    """
    return value is None or isinstance(value, str) and not value.strip()


def _boolean(value):
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    if type(value) is str:
        folded = value.strip().lower()
        if folded in _BOOL_TRUE:
            return True
        if folded in _BOOL_FALSE:
            return False
    raise ValueError("旧值不是可识别的开关值")


def _number(value, *, integer: bool):
    if type(value) is bool:
        raise ValueError("布尔值不能迁移为数字")
    if type(value) in (int, float):
        number = value
    elif type(value) is str and (not integer or _INT.fullmatch(value.strip())):
        try:
            number = float(value) if not integer else int(value.strip())
        except ValueError as error:
            raise ValueError("旧值不是有效数字") from error
    else:
        raise ValueError("旧值类型与数字配置不匹配")
    if integer:
        if type(number) is float and not number.is_integer():
            raise ValueError("旧值不是整数")
        return int(number)
    return number


def _convert_duration(rule: dict, value: object, field):
    source_name = rule["source"].rsplit(".", 1)[-1]
    target_name = field.field_id.rsplit(".", 1)[-1]
    source_unit = next((unit for unit in _UNIT_SECONDS if source_name.endswith("_" + unit)), None)
    target_unit = next((unit for unit in _UNIT_SECONDS if target_name.endswith("_" + unit)), None)
    if source_unit is None or target_unit is None or source_unit == target_unit:
        return value
    number = _number(value, integer=False)
    converted = number * _UNIT_SECONDS[source_unit] / _UNIT_SECONDS[target_unit]
    if field.kind is ValueKind.INTEGER:
        if not converted.is_integer():
            raise ValueError("时长单位转换不能得到整数")
        return int(converted)
    return converted


def _convert_value(rule: dict, value: object, field):
    transform = rule.get("transform")
    if transform == "component_order":
        from .behavior_configuration import component_order
        return field.validate(component_order(value))
    if transform == "bool_to_expression_intensity":
        enabled = _boolean(value)
        return field.validate(field.default if enabled else 0)
    if transform == "bool_to_cycle_mode":
        return field.validate("six_phase" if _boolean(value) else "basic")

    source_type = rule["source_type"].lower()
    value = _convert_duration(rule, value, field)
    if field.kind is ValueKind.BOOLEAN:
        converted = _boolean(value)
    elif field.kind is ValueKind.INTEGER:
        converted = _number(value, integer=True)
    elif field.kind is ValueKind.PROBABILITY:
        if source_type in ("int", "integer"):
            old_key = rule["source"]
            probability = LegacyProbabilityRule(
                V1_SUPPORTED_VERSION, old_key, LegacyProbabilityUnit.PERCENT,
            )
            converted = probability.convert(
                source_version=V1_SUPPORTED_VERSION, old_key=old_key,
                value=_number(value, integer=True), field=field,
            )
        else:
            converted = _number(value, integer=False)
    elif field.kind is ValueKind.WEIGHT:
        converted = _number(value, integer=False)
    elif field.kind is ValueKind.TEXT:
        if source_type == "list":
            if not isinstance(value, list):
                raise ValueError("旧值不是列表")
            list_format = rule.get("list_format")
            if list_format == "json":
                converted = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            elif list_format == "newline":
                if any(type(item) is not str for item in value):
                    raise ValueError("列表成员不是文本")
                converted = "\n".join(value)
            else:
                raise ValueError("列表目标没有经核实的序列化格式")
        elif type(value) is str:
            converted = value
        else:
            raise ValueError("旧值类型与文本配置不匹配")
    else:
        raise ValueError("V2 字段类型暂不支持")
    return field.validate(converted)


def _schedule_influence_tier(value: object, field) -> int:
    strength = _number(value, integer=False)
    if not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("旧日程影响强度必须在 0 到 1 之间")
    tier = 0 if strength == 0 else min(3, math.ceil(strength * 3))
    return field.validate(tier)


def _append_environment_perception_master(config, registry, candidates, differences):
    """Honor V1's environment master switch over V2's independently editable parts."""
    present, raw = _source_value(config, _ENVIRONMENT_PERCEPTION_MASTER)
    if not present:
        return
    if _source_precedence_conflict(config, _ENVIRONMENT_PERCEPTION_MASTER):
        differences.append(ConfigurationDifference(
            "conversation.perception", "group_precedence",
            "分组值与平铺兼容值不同；按 V1 宿主实际读取顺序采用分组值。",
            _ENVIRONMENT_PERCEPTION_MASTER,
        ))
    try:
        enabled = _boolean(raw)
    except (TypeError, ValueError) as error:
        differences.append(ConfigurationDifference(
            "conversation.perception", "invalid_value",
            f"环境感知总开关无法识别，相关分项保持各自迁移结果：{error}。",
            _ENVIRONMENT_PERCEPTION_MASTER,
        ))
        return
    if enabled:
        return

    # In V1 the parent switch suppressed all of these projections regardless of
    # their saved child values. V2 has no parent switch, so materialize the same
    # effective behavior while leaving unrelated calendar detail switches alone.
    for target in _ENVIRONMENT_PERCEPTION_TARGETS:
        field = registry.fields.get(target)
        if field is None:
            differences.append(ConfigurationDifference(
                target, "unavailable", "当前 V2 运行档没有注册此环境感知分项，已跳过。",
                _ENVIRONMENT_PERCEPTION_MASTER,
            ))
            continue
        try:
            disabled = field.validate(False)
        except (TypeError, ValueError) as error:
            differences.append(ConfigurationDifference(
                target, "invalid_value", f"环境感知分项关闭值未通过 V2 校验：{error}。",
                _ENVIRONMENT_PERCEPTION_MASTER,
            ))
            continue
        # The V1 parent switch dominates child settings. Replace any child
        # candidates rather than reporting a false conflict between the master
        # switch and the intentionally saved per-feature preferences.
        candidates[target] = [({"source": _ENVIRONMENT_PERCEPTION_MASTER,
                               "confidence": "high"}, disabled)]
        differences.append(ConfigurationDifference(
            target, "inactive_source",
            "V1 环境感知总开关已关闭；为保持升级后的行为，V2 对应分项已关闭。",
            _ENVIRONMENT_PERCEPTION_MASTER,
        ))


def _append_model_perception_modalities(config, registry, candidates, differences):
    """V1's model switch covered both route identity and configured input modes."""
    master_present, master_raw = _source_value(config, _ENVIRONMENT_PERCEPTION_MASTER)
    if master_present:
        try:
            if not _boolean(master_raw):
                return
        except (TypeError, ValueError):
            pass
    present, raw = _source_value(config, _MODEL_PERCEPTION_SOURCE)
    if not present:
        return
    try:
        enabled = _boolean(raw)
    except (TypeError, ValueError):
        # The ordinary rule conversion already records the invalid source value.
        return
    field = registry.fields.get(_MODEL_PERCEPTION_MODALITIES_TARGET)
    if field is None:
        differences.append(ConfigurationDifference(
            _MODEL_PERCEPTION_MODALITIES_TARGET, "unavailable",
            "当前 V2 运行档没有注册模型能力感知分项，已跳过。", _MODEL_PERCEPTION_SOURCE,
        ))
        return
    try:
        converted = field.validate(enabled)
    except (TypeError, ValueError) as error:
        differences.append(ConfigurationDifference(
            _MODEL_PERCEPTION_MODALITIES_TARGET, "invalid_value",
            f"模型能力感知分项未通过 V2 校验：{error}。", _MODEL_PERCEPTION_SOURCE,
        ))
        return
    candidates[_MODEL_PERCEPTION_MODALITIES_TARGET] = [(
        {"source": _MODEL_PERCEPTION_SOURCE, "confidence": "high"}, converted,
    )]


def _contract_value(rule: dict, value: object):
    contract = rule["contract"]
    transform = rule["transform"]
    source_type = rule["source_type"].lower()

    if transform == "voice_generation_mode":
        aliases = {name: "model_tag" for name in (
            "fast_tag", "hybrid", "direct", "tag", "tags", "fast",
            "postprocess", "convert", "post", "llm")}
        if type(value) is not str or value.strip().lower() not in aliases:
            raise ValueError("旧语音生成方式不可识别")
        converted = aliases[value.strip().lower()]
    elif transform == "bool_to_voice_scope":
        converted = "full" if _boolean(value) else "partial"
    elif transform in ("bool_to_voice_trigger", "bool_to_voice_delivery"):
        enabled = _boolean(value)
        converted = (("always" if enabled else "request") if transform == "bool_to_voice_trigger"
                     else ("voice_only" if enabled else "voice_and_text"))
    elif transform == "weather_provider":
        aliases = {
            "auto": "auto", "qweather": "qweather", "open-meteo": "open-meteo",
            "open_meteo": "open-meteo", "openmeteo": "open-meteo",
        }
        if type(value) is not str or value.strip().lower() not in aliases:
            raise ValueError("V2 没有与此旧天气来源等价的选项")
        converted = aliases[value.strip().lower()]
    elif transform == "voice_delivery_scope":
        aliases = {"full": "voice_only", "partial": "voice_and_text"}
        if type(value) is not str or value.strip().lower() not in aliases:
            raise ValueError("旧语音转换范围不是可识别的全量/混合模式")
        converted = aliases[value.strip().lower()]
    elif transform == "percent":
        number = _number(value, integer=False)
        inherited_voice_probability = (rule["target"] in {
            "voice_reply.private_probability", "voice_reply.group_probability"} and number == -1)
        if rule["target"] == "voice_reply.primary_mention_probability" and not 0 <= number <= 100:
            raise ValueError("旧核心用户提及概率必须在 0 到 100 之间")
        converted = -1.0 if inherited_voice_probability else number / 100
    elif transform == "one_minus_probability":
        # V1 stored the chance of using a preset poke reply.  V2 stores the
        # complementary chance of handing the interaction to its model lane.
        number = _number(value, integer=False)
        if not math.isfinite(number) or not 0 <= number <= 1:
            raise ValueError("旧戳一戳预设回复概率必须在 0 到 1 之间")
        converted = 1 - number
    elif transform == "integer":
        number = _number(value, integer=False)
        if not float(number).is_integer():
            raise ValueError("秒数不能无损转换为整数")
        converted = int(number)
    elif transform == "minutes_to_seconds":
        converted = _number(value, integer=True) * 60
    elif transform == "milliseconds_to_seconds":
        milliseconds = _number(value, integer=True)
        if milliseconds % 1000:
            raise ValueError("毫秒数不能无损转换为整数秒")
        converted = milliseconds // 1000
    elif transform == "numeric_string_integer":
        converted = _number(value, integer=True)
    elif transform == "route_rules":
        from companion_v2.model_gateway.replacement_rules import checked_rules
        converted = json.dumps(value, ensure_ascii=False, separators=(",", ":")) if isinstance(value, list) else value
        checked_rules(converted)
    elif transform == "json_list":
        if not isinstance(value, list) or any(type(item) is not str for item in value):
            raise ValueError("旧值不是列表")
        if rule["target"] == "segmentation.split_words":
            value = list(dict.fromkeys(value))
            if (set(LEGACY_DEFAULT_SPLIT_WORDS) <= set(value)
                    and set(value) <= LEGACY_SPLIT_WORD_POOL):
                value = [word for word in value if word not in LEGACY_REMOVED_SPLIT_WORDS]
        converted = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    else:
        target_type = contract["type"]
        if target_type == "bool":
            converted = _boolean(value)
        elif target_type == "int":
            converted = _number(value, integer=True)
        elif target_type == "float":
            converted = _number(value, integer=False)
        elif target_type in ("string", "text"):
            if type(value) is not str:
                raise ValueError("旧值不是文本")
            converted = value
        elif target_type == "list":
            if type(value) is not list:
                raise ValueError("旧值不是列表")
            converted = list(value)
        else:
            raise ValueError("V2 设置类型暂不支持")

    target_type = contract["type"]
    valid_type = {
        "bool": type(converted) is bool,
        "int": type(converted) is int,
        "float": type(converted) in (int, float) and type(converted) is not bool,
        "string": type(converted) is str,
        "text": type(converted) is str,
        "list": type(converted) is list,
    }.get(target_type, False)
    if not valid_type:
        raise ValueError("转换结果与 V2 设置类型不匹配")
    if target_type in ("int", "float"):
        if not math.isfinite(converted):
            raise ValueError("旧值不是有限数字")
        lower = contract.get("minimum", contract.get("min"))
        upper = contract.get("maximum", contract.get("max"))
        if lower is not None and converted < lower or upper is not None and converted > upper:
            raise ValueError("旧值超出 V2 设置范围")
    maximum_chars = contract.get("maximum_chars")
    if type(converted) is str and maximum_chars is not None and len(converted) > maximum_chars:
        raise ValueError("旧值超出 V2 文本长度")
    if "options" in contract and converted not in contract["options"]:
        raise ValueError("旧值不是 V2 支持的选项")
    if target_type == "list":
        item_schema = contract.get("items")
        item_type = item_schema.get("type") if isinstance(item_schema, dict) else None
        if item_type == "string" and any(type(item) is not str for item in converted):
            raise ValueError("旧列表包含非文本成员")
    return converted


def _voice_trigger_value(config, raw, *, window_kind=None):
    """V1's global 100% frequency also enabled conversion with auto voice off."""
    if _boolean(raw):
        return "always"
    mode_present, mode = _source_value(config, "voice_action_config.tts_frequency_control_mode")
    probability_present, probability = _source_value(config, "voice_action_config.tts_trigger_probability")
    if not mode_present and not probability_present:
        return "request"
    if mode_present and mode not in {"global", "legacy"}:
        raise ValueError("旧语音频控模式不可识别")
    if mode == "legacy":
        return "request"
    chance = _number(probability, integer=False) if probability_present else 100
    kinds = (window_kind,) if window_kind else ("private", "group")
    chances = []
    for kind in kinds:
        present, override = _source_value(config, f"voice_action_config.tts_{kind}_trigger_probability")
        override = _number(override, integer=False) if present else -1
        chances.append(override if override >= 0 else chance)
    return "always" if any(value > 0 for value in chances) else "request"


def _convert_contract_configuration(config, rules, *, label: str, window_kind=None) -> ConfigurationMigration:
    if not isinstance(config, dict):
        return ConfigurationMigration({}, {}, {}, (
            ConfigurationDifference("", "invalid_source", "V1 配置不是键值映射。"),
        ))

    differences = []
    candidates: dict[str, list[tuple[dict, object]]] = {}
    for rule in rules:
        source = rule["source"]
        present, raw = _source_value(config, source)
        if not present:
            continue
        if _source_precedence_conflict(config, source):
            differences.append(ConfigurationDifference(
                rule["target"], "group_precedence",
                "分组值与平铺兼容值不同；按 V1 宿主实际读取顺序采用分组值。", source,
            ))
        try:
            converted = _contract_value(rule, raw)
            if rule.get("transform") == "bool_to_voice_trigger":
                converted = _voice_trigger_value(config, raw, window_kind=window_kind)
        except (TypeError, ValueError, OverflowError) as error:
            differences.append(ConfigurationDifference(
                rule["target"], "invalid_value", f"值未迁移：{error}。", source,
            ))
            continue
        candidates.setdefault(rule["target"], []).append((rule, converted))

    changes = {}
    confidence = {}
    sources = {}
    for target, values in sorted(candidates.items()):
        distinct = {json.dumps(value, ensure_ascii=False, sort_keys=True) for _, value in values}
        source_keys = tuple(rule["source"] for rule, _ in values)
        if len(distinct) != 1:
            differences.append(ConfigurationDifference(
                target, "conflict",
                f"多个 V1 配置项指向同一 V2{label}字段且值不同；为避免静默覆盖，该字段未迁移。来源："
                + "、".join(source_keys),
            ))
            continue
        changes[target] = values[0][1]
        confidence[target] = "medium" if any(rule["confidence"] == "medium" for rule, _ in values) else "high"
        sources[target] = source_keys
        if target == "voice_reply.trigger_mode" and values[0][1] == "always":
            frequency_sources = ("voice_action_config.tts_frequency_control_mode",
                "voice_action_config.tts_trigger_probability",
                "voice_action_config.tts_private_trigger_probability",
                "voice_action_config.tts_group_trigger_probability")
            sources[target] += tuple(source for source in frequency_sources
                                     if _source_value(config, source)[0] and source not in source_keys)

    return ConfigurationMigration(changes, confidence, sources, tuple(differences))


def convert_plugin_configuration(config, *, rules=None, window_kind=None) -> ConfigurationMigration:
    """Convert direct V2 plugin settings without guessing compound identities."""
    if window_kind not in (None, "private", "group"):
        raise ValueError("window kind must be private or group")
    source_rules = plugin_configuration_rules() if rules is None else rules
    selection_differences = ()
    if rules is None:
        from .reply_configuration import select_plugin_rules
        source_rules, selection_differences = select_plugin_rules(config, source_rules)
    if window_kind is not None:
        source_rules = tuple(rule for rule in source_rules
                             if rule["source"] != "legacy_compat_config.segmented_proactive_interval_max")
    migration = _convert_contract_configuration(
        config, source_rules, label="插件", window_kind=window_kind,
    )
    if selection_differences:
        migration = ConfigurationMigration(migration.changes, migration.confidence, migration.sources,
                                           migration.differences + selection_differences)
    # The V1 reactive-poke setting controlled the split between a preset reply
    # and the model branch.  V2 exposes both the probability and its explicit
    # enable switch, so carry the switch along with the converted probability
    # instead of leaving a non-zero probability inert after migration.
    source = "reactive_poke_config.reactive_poke_normal_reply_probability"
    probability_target = "poke.model_reply_probability"
    enabled_target = "poke.model_reply_enabled"
    if (probability_target in migration.changes
            and any(source in source_keys for source_keys in migration.sources.values())):
        changes = dict(migration.changes)
        confidence = dict(migration.confidence)
        sources = dict(migration.sources)
        changes[enabled_target] = changes[probability_target] > 0
        confidence[enabled_target] = confidence.get(probability_target, "medium")
        sources[enabled_target] = (source,)
        migration = ConfigurationMigration(changes, confidence, sources, migration.differences)
    return migration


def convert_semantic_configuration(config, *, rules=None) -> ConfigurationMigration:
    """Convert V1 settings into the V2 semantic catalog, reporting alias conflicts."""
    source_rules = semantic_configuration_rules() if rules is None else rules
    differences = ()
    if rules is None:
        from .configuration_precedence import select_semantic_rules
        source_rules, differences = select_semantic_rules(config, source_rules)
    migration = _convert_contract_configuration(config, source_rules, label="语义")
    return ConfigurationMigration(migration.changes, migration.confidence, migration.sources,
                                  migration.differences + differences)


_WINDOWED_SEGMENT_TARGETS = frozenset({
    "conversation.presentation.enabled",
    "conversation.presentation.threshold",
    "conversation.presentation.min_segment_chars",
    "conversation.presentation.max_segments",
    "conversation.presentation.part_gap_ms",
})
_SEGMENT_PROFILE_FIELDS = frozenset({
    "enabled", "threshold", "min_segment_chars", "max_segments",
    "interval_method", "interval_min", "interval_max", "log_base",
})
_SEGMENT_GLOBAL_FIELDS = frozenset({
    "segmented_proactive_threshold", "segmented_proactive_min_segment_chars",
    "segmented_proactive_interval_min", "segmented_proactive_interval_max",
    "segmented_proactive_interval_method", "segmented_proactive_log_base",
})
_SEGMENT_MASTER = "legacy_compat_config.enable_segmented_proactive_reply"
_SEGMENT_PROFILES = "legacy_compat_config.enable_segmented_proactive_chat_profiles"
_SEGMENT_CHAT_SCOPE = "legacy_compat_config.segmented_proactive_chat_scope"
_COMPONENT_STRATEGIES = {
    "legacy_compat_config.segmented_proactive_image_strategy":
        ("conversation.presentation.image_position", "image"),
    "legacy_compat_config.segmented_proactive_voice_strategy":
        ("conversation.presentation.voice_position", "voice"),
    "legacy_compat_config.segmented_proactive_at_strategy": ("", "at"),
}
_SEGMENT_SPECIALIZED_SOURCES = frozenset({
    _SEGMENT_MASTER, _SEGMENT_PROFILES, _SEGMENT_CHAT_SCOPE, *_COMPONENT_STRATEGIES,
})


def _windowed_segment_rule(rule):
    source = rule["source"]
    if source in _COMPONENT_STRATEGIES:
        return True
    if not source.startswith("legacy_compat_config."):
        return False
    name = source.partition(".")[2]
    if name in _SEGMENT_GLOBAL_FIELDS:
        return True
    if name == "enable_segmented_proactive_reply":
        return True
    for profile in ("private", "group"):
        prefix = f"segmented_proactive_{profile}_"
        if name.startswith(prefix) and name[len(prefix):] in _SEGMENT_PROFILE_FIELDS:
            return True
    return rule["target"] in _WINDOWED_SEGMENT_TARGETS and name.startswith("segmented_proactive_")


def _append_component_strategies(config, registry, candidates, differences, rules):
    rules_by_source = {rule["source"]: rule for rule in rules}
    for source, (target, component) in _COMPONENT_STRATEGIES.items():
        present, raw = _source_value(config, source)
        if not present:
            continue
        if _source_precedence_conflict(config, source):
            differences.append(ConfigurationDifference(
                target or "conversation.presentation.component_order", "group_precedence",
                "分组值与平铺兼容值不同；按 V1 宿主实际读取顺序采用分组值。", source,
            ))
        if type(raw) is not str:
            differences.append(ConfigurationDifference(
                target or "conversation.presentation.component_order", "invalid_value",
                "分段组件位置不是文本，已跳过。", source,
            ))
            continue
        strategy = raw.strip().lower()
        if strategy == "inline" and component == "at":
            differences.append(ConfigurationDifference(
                "conversation.presentation.component_order", "already_represented",
                "V2 默认将 @ 组件与正文放在同一条消息中；旧版 inline 策略已由默认投递顺序承接。",
                source,
            ))
            continue
        if strategy == "inline":
            field = registry.fields.get(target)
            if field is None:
                differences.append(ConfigurationDifference(
                    target, "unavailable", "当前 V2 运行档没有注册此组件位置配置，已跳过。", source,
                ))
                continue
            rule = rules_by_source.get(source, {"source": source, "confidence": "medium"})
            candidates.setdefault(target, []).append((rule, field.validate("after_text")))
            continue
        if strategy not in {"separate", "previous", "next"}:
            differences.append(ConfigurationDifference(
                target or "conversation.presentation.component_order", "invalid_value",
                "旧版分段组件位置不是受支持的选项，已跳过。", source,
            ))
            continue
        differences.append(ConfigurationDifference(
            target or "conversation.presentation.component_order", "strategy_not_representable",
            f"V1 的 {strategy} 策略涉及独立消息或跨段组件归属；V2 的单条回复组件顺序无法等价表示，未自动迁移。",
            source,
        ))


def _legacy_segment_source(config, field, window_kind, profiles_enabled, *, profile_only=False):
    if profiles_enabled:
        profile_source = f"legacy_compat_config.segmented_proactive_{window_kind}_{field}"
        present, _ = _source_value(config, profile_source)
        if present:
            return profile_source
    if profile_only:
        return None
    global_source = f"legacy_compat_config.segmented_proactive_{field}"
    present, _ = _source_value(config, global_source)
    return global_source if present else None


def _append_window_segment_value(config, registry, candidates, differences, rules_by_source,
                                 source, target, *, source_type=None, convert=None):
    present, raw = _source_value(config, source)
    if not present:
        return False
    if _source_precedence_conflict(config, source):
        differences.append(ConfigurationDifference(
            target, "group_precedence",
            "分组值与平铺兼容值不同；按 V1 宿主实际读取顺序采用分组值。", source,
        ))
    field = registry.fields.get(target)
    if field is None:
        differences.append(ConfigurationDifference(
            target, "unavailable", "当前 V2 运行档没有注册此分段配置项，已跳过。", source,
        ))
        return True
    rule = rules_by_source.get(source, {"source": source, "source_type": source_type or "text",
                                        "target": target, "confidence": "high"})
    try:
        value = convert(raw, field) if convert is not None else _convert_value(rule, raw, field)
    except (TypeError, ValueError, OverflowError) as error:
        differences.append(ConfigurationDifference(
            target, "invalid_value", f"分段设置未迁移：{error}。", source,
        ))
        return True
    candidates.setdefault(target, []).append((
        {"source": source, "confidence": rule.get("confidence", "high")}, value,
    ))
    return True


def _append_window_segmentation(config, registry, candidates, differences, rules, window_kind):
    rules_by_source = {rule["source"]: rule for rule in rules}
    _, profiles_raw = _source_value(config, _SEGMENT_PROFILES)
    profiles_present, _ = _source_value(config, _SEGMENT_PROFILES)
    try:
        profiles_enabled = _boolean(profiles_raw) if profiles_present else False
    except ValueError as error:
        profiles_enabled = False
        differences.append(ConfigurationDifference(
            "conversation.presentation.enabled", "invalid_value",
            f"私聊/群聊独立分段开关无法识别，按 V1 默认关闭处理：{error}。", _SEGMENT_PROFILES,
        ))

    def selected(field, *, profile_only=False):
        return _legacy_segment_source(config, field, window_kind, profiles_enabled, profile_only=profile_only)

    # V1 had one module switch plus an optional per-window switch. V2 stores only
    # the effective switch on the selected private/group window.
    master_present, master_raw = _source_value(config, _SEGMENT_MASTER)
    profile_switch = selected("enabled", profile_only=True)
    profile_present, profile_raw = _source_value(config, profile_switch) if profile_switch else (False, None)
    scope_present, scope_raw = _source_value(config, _SEGMENT_CHAT_SCOPE)
    if master_present or profile_present or scope_present or profiles_present:
        try:
            master_enabled = _boolean(master_raw) if master_present else False
        except ValueError as error:
            master_enabled = False
            differences.append(ConfigurationDifference(
                "conversation.presentation.enabled", "invalid_value",
                f"V1 分段总开关无法识别，按关闭处理：{error}。", _SEGMENT_MASTER,
            ))
        sources = [_SEGMENT_MASTER] if master_present else []
        if profiles_present:
            sources.append(_SEGMENT_PROFILES)
        if profiles_enabled:
            try:
                profile_enabled = _boolean(profile_raw) if profile_present else True
            except ValueError as error:
                profile_enabled = False
                differences.append(ConfigurationDifference(
                    "conversation.presentation.enabled", "invalid_value",
                    f"当前窗口的 V1 分段开关无法识别，按关闭处理：{error}。", profile_switch,
                ))
            effective_enabled = master_enabled and profile_enabled
            if profile_present:
                sources.append(profile_switch)
        else:
            scope = str(scope_raw or "all").strip().lower() if scope_present else "all"
            if scope not in {"all", "private", "group"}:
                scope = "all"
            effective_enabled = master_enabled and (scope == "all" or scope == window_kind)
            if scope_present:
                sources.append(_SEGMENT_CHAT_SCOPE)
        if sources:
            for source in sources:
                if _source_precedence_conflict(config, source):
                    differences.append(ConfigurationDifference(
                        "conversation.presentation.enabled", "group_precedence",
                        "分组值与平铺兼容值不同；按 V1 宿主实际读取顺序采用分组值。", source,
                    ))
            field = registry.fields.get("conversation.presentation.enabled")
            if field is None:
                differences.append(ConfigurationDifference(
                    "conversation.presentation.enabled", "unavailable",
                    "当前 V2 运行档没有注册分段开关，已跳过。", "、".join(sources),
                ))
            else:
                try:
                    value = field.validate(effective_enabled)
                except (TypeError, ValueError) as error:
                    differences.append(ConfigurationDifference(
                        "conversation.presentation.enabled", "invalid_value",
                        f"有效分段开关未通过 V2 校验：{error}。", "、".join(sources),
                    ))
                else:
                    for source in sources:
                        candidates.setdefault("conversation.presentation.enabled", []).append((
                            {"source": source, "confidence": "high"}, value,
                        ))

    for name, target in (("threshold", "conversation.presentation.threshold"),
                         ("min_segment_chars", "conversation.presentation.min_segment_chars")):
        source = selected(name)
        if source:
            _append_window_segment_value(config, registry, candidates, differences,
                rules_by_source, source, target, source_type="int")

    # A profile-specific max overrides the V1 global role default. When it is
    # absent, the existing segmentation profile migration supplies that default.
    source = selected("max_segments", profile_only=True)
    if source:
        _append_window_segment_value(config, registry, candidates, differences,
            rules_by_source, source, "conversation.presentation.max_segments", source_type="int")

    timing_fields = ("interval_method", "interval_min", "interval_max", "log_base")
    timing_sources = {name: selected(name) for name in timing_fields}
    timing_present = any(source and _source_value(config, source)[0] for source in timing_sources.values())
    if timing_present:
        method_source = timing_sources["interval_method"]
        method_present, method_raw = _source_value(config, method_source) if method_source else (False, None)
        method = str(method_raw or "").strip().lower() if method_present else "log"
        if method not in {"random", "log"}:
            differences.append(ConfigurationDifference(
                "conversation.presentation.part_gap_mode", "invalid_value",
                "V1 分段间隔方式无法识别，按 V1 默认的按长度调节迁移。", method_source or "",
            ))
            method = "log"
        mode_field = registry.fields.get("conversation.presentation.part_gap_mode")
        if mode_field is None:
            differences.append(ConfigurationDifference(
                "conversation.presentation.part_gap_mode", "unavailable",
                "当前 V2 运行档没有注册分段间隔方式，已跳过。", method_source or "",
            ))
        else:
            try:
                mode = mode_field.validate(method)
            except (TypeError, ValueError) as error:
                differences.append(ConfigurationDifference(
                    "conversation.presentation.part_gap_mode", "invalid_value",
                    f"V1 分段间隔方式未通过 V2 校验：{error}。", method_source or "",
                ))
            else:
                source = method_source or next((item for item in timing_sources.values() if item),
                                                "legacy_compat_config.segmented_proactive_interval_method")
                candidates.setdefault("conversation.presentation.part_gap_mode", []).append((
                    {"source": source, "confidence": "high"}, mode,
                ))

        for name, target in (("interval_min", "conversation.presentation.part_gap_min_ms"),
                             ("interval_max", "conversation.presentation.part_gap_max_ms")):
            source = timing_sources[name]
            if source:
                _append_window_segment_value(config, registry, candidates, differences,
                    rules_by_source, source, target, source_type="float",
                    convert=lambda raw, field: field.validate(round(_number(raw, integer=False) * 1000)))
        source = timing_sources["log_base"]
        if source:
            _append_window_segment_value(config, registry, candidates, differences,
                rules_by_source, source, "conversation.presentation.part_gap_log_base", source_type="float")

    scope_source = selected("scope")
    scope_present, scope_value = _source_value(config, scope_source) if scope_source else (False, None)
    if scope_present and str(scope_value).strip().lower() == "proactive_only":
        differences.append(ConfigurationDifference(
            "conversation.presentation.enabled", "scope_not_representable",
            "V1 将此分段设置限制为插件主动消息；V2 当前窗口的分段设置也作用于普通回复，已迁移参数但作用范围需在预览中核对。",
            scope_source,
        ))

    forward_source = selected("send_as_forward")
    forward_present, forward_value = _source_value(config, forward_source) if forward_source else (False, None)
    if forward_present:
        try:
            forward_enabled = _boolean(forward_value)
        except ValueError as error:
            differences.append(ConfigurationDifference(
                "conversation.presentation.enabled", "invalid_value",
                f"V1 的分段合并转发开关无法识别：{error}。", forward_source,
            ))
        else:
            if forward_enabled:
                differences.append(ConfigurationDifference(
                    "conversation.presentation.enabled", "not_migrated",
                    "V1 的分段合并转发在 V2 当前回复链没有对应配置；V2 仍按正常逐段消息投递。",
                    forward_source,
                ))


def convert_configuration(config, *, registry, rules=None, window_kind=None) -> ConfigurationMigration:
    """Convert present source keys, validating against the live V2 field contract.

    Ambiguous aliases are accepted only when their converted values agree. Unknown,
    invalid, or unregistered values are reported and left out of the write intents.

    A blank V1 ``model.*.provider`` means "follow the default" and stays unset, so the
    host conversation or task profile default still applies downstream; blank values of
    every other field migrate as written because those may be deliberate.
    """
    if not isinstance(config, dict):
        return ConfigurationMigration({}, {}, {}, (
            ConfigurationDifference("", "invalid_source", "V1 配置不是键值映射。"),
        ))

    if window_kind not in (None, "private", "group"):
        raise ValueError("window kind must be private or group")

    differences = []
    candidates: dict[str, list[tuple[dict, object]]] = {}
    source_rules = configuration_rules() if rules is None else rules
    if rules is None:
        from .configuration_precedence import select_registry_rules
        source_rules, selection_differences = select_registry_rules(config, source_rules)
        differences.extend(selection_differences)
    for rule in source_rules:
        if rules is None:
            from .behavior_configuration import SPECIAL_SOURCES
            if rule["source"] in SPECIAL_SOURCES:
                continue
        if rule["source"].startswith("legacy_compat_config.") and rule["source"].rsplit(".", 1)[-1] in (
                "enable_maintenance_token_saver", "maintenance_token_soft_limit"):
            from .reply_configuration import soft_alias_is_inactive
            if soft_alias_is_inactive(config, rule["source"]):
                continue
        if window_kind is not None and _windowed_segment_rule(rule):
            continue
        source = rule["source"]
        if source == _ENVIRONMENT_PERCEPTION_MASTER:
            # The V1 parent switch is converted together with its child fields
            # below; it has no standalone V2 registry destination.
            continue
        if source in (_SCHEDULE_INFLUENCE_ENABLED, _SCHEDULE_INFLUENCE_STRENGTH):
            continue
        if source in _WEATHER_CONVERTER_SOURCES:
            continue
        present, raw = _source_value(config, source)
        if not present:
            continue
        if _task_provider_target(rule["target"]) and _unset_provider(raw):
            # V1 wrote "follow the default" as a blank provider.  V2 separates unset
            # (no source → the host conversation or task profile default applies) from
            # an explicit empty value (the provider lookup fails with
            # model_provider_unavailable), so a blank must not become an explicit
            # change here.  Only ``model.*.provider`` carries that meaning: a blank in
            # any other field may be deliberate and must still migrate.
            continue
        if _source_precedence_conflict(config, source):
            differences.append(ConfigurationDifference(
                rule["target"], "group_precedence",
                "分组值与平铺兼容值不同；按 V1 宿主实际读取顺序采用分组值。", source,
            ))
        field = registry.fields.get(rule["target"])
        if field is None:
            differences.append(ConfigurationDifference(
                rule["target"], "unavailable", "当前 V2 运行档没有注册此配置项，已跳过。", source,
            ))
            continue
        try:
            converted = _convert_value(rule, raw, field)
        except (TypeError, ValueError) as error:
            differences.append(ConfigurationDifference(
                rule["target"], "invalid_value", f"值未迁移：{error}。", source,
            ))
            continue
        candidates.setdefault(rule["target"], []).append((rule, converted))
        partial_note = _PARTIAL_MAPPING_NOTES.get(source)
        if partial_note:
            differences.append(ConfigurationDifference(
                rule["target"], "partial_mapping", partial_note, source,
            ))

    _append_environment_perception_master(config, registry, candidates, differences)
    if rules is None:
        _append_model_perception_modalities(config, registry, candidates, differences)

    enabled_present, enabled_raw = _source_value(config, _SCHEDULE_INFLUENCE_ENABLED)
    strength_present, strength_raw = _source_value(config, _SCHEDULE_INFLUENCE_STRENGTH)
    if enabled_present or strength_present:
        source_keys = tuple(source for source, present in (
            (_SCHEDULE_INFLUENCE_ENABLED, enabled_present),
            (_SCHEDULE_INFLUENCE_STRENGTH, strength_present),
        ) if present)
        for source in source_keys:
            if _source_precedence_conflict(config, source):
                differences.append(ConfigurationDifference(
                    _SCHEDULE_INFLUENCE_TARGET, "group_precedence",
                    "分组值与平铺兼容值不同；按 V1 宿主实际读取顺序采用分组值。", source,
                ))

        target_field = registry.fields.get(_SCHEDULE_INFLUENCE_TARGET)
        if target_field is None:
            differences.append(ConfigurationDifference(
                _SCHEDULE_INFLUENCE_TARGET, "unavailable", "当前 V2 运行档没有注册此配置项，已跳过。",
                "、".join(source_keys),
            ))
        else:
            try:
                enabled = _boolean(enabled_raw) if enabled_present else True
                if not enabled:
                    converted = target_field.validate(0)
                    if strength_present:
                        differences.append(ConfigurationDifference(
                            _SCHEDULE_INFLUENCE_TARGET, "inactive_source",
                            "V1 日程影响开关已关闭；V2 档位迁移为 0，忽略保存的强度值。",
                            _SCHEDULE_INFLUENCE_ENABLED,
                        ))
                    confidence = "high"
                else:
                    raw_strength = strength_raw if strength_present else 0.35
                    converted = _schedule_influence_tier(raw_strength, target_field)
                    confidence = "medium"
                    if not strength_present:
                        differences.append(ConfigurationDifference(
                            _SCHEDULE_INFLUENCE_TARGET, "derived_default",
                            "V1 日程影响已开启但未保存强度；按 V1 默认 0.35 折算为 V2 的 2 档。",
                            _SCHEDULE_INFLUENCE_ENABLED,
                        ))
                    elif _number(raw_strength, integer=False) not in (0, 1):
                        differences.append(ConfigurationDifference(
                            _SCHEDULE_INFLUENCE_TARGET, "quantized",
                            f"V1 强度 {raw_strength} 按三档等宽折算为 V2 的 {converted} 档。",
                            _SCHEDULE_INFLUENCE_STRENGTH,
                        ))
            except (TypeError, ValueError, OverflowError) as error:
                differences.append(ConfigurationDifference(
                    _SCHEDULE_INFLUENCE_TARGET, "invalid_value", f"日程影响设置未迁移：{error}。",
                    "、".join(source_keys),
                ))
            else:
                for source in source_keys:
                    candidates.setdefault(_SCHEDULE_INFLUENCE_TARGET, []).append((
                        {"source": source, "confidence": confidence}, converted,
                    ))

    if rules is None:
        from .behavior_configuration import append_behavior_configuration
        append_behavior_configuration(config, registry, candidates, differences)
        from .configuration_precedence import append_compound_configuration
        append_compound_configuration(config, registry, candidates, differences)
        from .proactive_configuration import append_contact_defaults
        append_contact_defaults(config, registry, candidates, differences)
        from .model_configuration import append_model_configuration
        append_model_configuration(config, registry, candidates, differences)
        from .reply_configuration import append_private_debounce
        append_private_debounce(config, registry, candidates, differences, window_kind=window_kind)
        _append_component_strategies(config, registry, candidates, differences, source_rules)
        if window_kind is not None:
            _append_window_segmentation(config, registry, candidates, differences, source_rules, window_kind)

    changes = {}
    confidence = {}
    sources = {}
    for target, values in sorted(candidates.items()):
        distinct = {json.dumps(value, ensure_ascii=False, sort_keys=True) for _, value in values}
        source_keys = tuple(rule["source"] for rule, _ in values)
        if len(distinct) != 1:
            differences.append(ConfigurationDifference(
                target, "conflict",
                "多个 V1 配置项指向同一 V2 字段且值不同；为避免静默覆盖，该字段未迁移。来源："
                + "、".join(source_keys),
            ))
            continue
        _, converted = values[0]
        changes[target] = converted
        confidence[target] = "medium" if any(rule["confidence"] == "medium" for rule, _ in values) else "high"
        sources[target] = source_keys

    if rules is None:
        differences.extend(unmapped_configuration_differences(config))
    return ConfigurationMigration(changes, confidence, sources, tuple(differences))


def _legacy_account_ids(config, source, differences):
    present, raw = _source_value(config, source)
    if not present:
        return False, []
    if _source_precedence_conflict(config, source):
        differences.append(ConfigurationDifference(
            source, "group_precedence",
            "分组值与平铺兼容值不同；按 V1 宿主实际读取顺序采用分组值。", source,
        ))
    if type(raw) is not list:
        differences.append(ConfigurationDifference(
            source, "invalid_value", "旧值不是 ID 列表，已跳过。", source,
        ))
        return True, []
    values, invalid = [], 0
    for item in raw:
        if type(item) is int and item > 0 and item.bit_length() <= 107:
            text = str(item)
        elif type(item) is int:
            invalid += 1
            continue
        elif type(item) is str and item.strip() and len(item.strip()) <= 32:
            text = item.strip()
        else:
            invalid += 1
            continue
        if not text.isascii() or not text.isdecimal() or int(text) <= 0:
            invalid += 1
            continue
        canonical = str(int(text))
        if canonical not in values:
            values.append(canonical)
    if invalid:
        differences.append(ConfigurationDifference(
            source, "invalid_value", f"旧列表含 {invalid} 个非正整数 ID，已跳过这些成员。", source,
        ))
    return True, values


def _legacy_string(config, source, differences):
    present, raw = _source_value(config, source)
    if not present:
        return False, ""
    if _source_precedence_conflict(config, source):
        differences.append(ConfigurationDifference(
            source, "group_precedence",
            "分组值与平铺兼容值不同；按 V1 宿主实际读取顺序采用分组值。", source,
        ))
    if type(raw) is not str:
        differences.append(ConfigurationDifference(
            source, "invalid_value", "旧值不是文本，已跳过。", source,
        ))
        return True, ""
    return True, raw.strip()


def _legacy_boolean(config, source, differences):
    present, raw = _source_value(config, source)
    if not present:
        return False, False
    if _source_precedence_conflict(config, source):
        differences.append(ConfigurationDifference(
            source, "group_precedence",
            "分组值与平铺兼容值不同；按 V1 宿主实际读取顺序采用分组值。", source,
        ))
    try:
        return True, _boolean(raw)
    except ValueError:
        differences.append(ConfigurationDifference(
            source, "invalid_value", "旧值不是可识别的开关，已跳过。", source,
        ))
        return True, False


def _legacy_persona_ids(config, source, differences):
    present, raw = _source_value(config, source)
    if not present:
        return False, []
    if _source_precedence_conflict(config, source):
        differences.append(ConfigurationDifference(
            source, "group_precedence",
            "分组值与平铺兼容值不同；按 V1 宿主实际读取顺序采用分组值。", source,
        ))
    if type(raw) is not list:
        differences.append(ConfigurationDifference(
            source, "invalid_value", "旧值不是人格 ID 列表，已跳过。", source,
        ))
        return True, []
    values, invalid = [], 0
    for item in raw:
        if type(item) is not str or not item.strip():
            invalid += 1
            continue
        value = item.strip()
        if len(value) > 120 or any(ord(char) < 32 for char in value):
            invalid += 1
            continue
        if value not in values:
            values.append(value)
    if invalid:
        differences.append(ConfigurationDifference(
            source, "invalid_value", f"旧人格列表含 {invalid} 个无效成员，已跳过。", source,
        ))
    return True, values


def _legacy_account_inventory(accounts):
    """Accept only the host's connected OneBot accounts and their role mappings."""
    result = []
    seen = set()
    for item in accounts or ():
        if not isinstance(item, dict):
            continue
        platform_id = item.get("platform_id")
        account_id = item.get("account_id")
        bot_id = item.get("bot_id")
        if (type(platform_id) is not str or not platform_id.strip()
                or type(account_id) is not str or not account_id.isascii()
                or not account_id.isdecimal() or len(account_id) > 32 or int(account_id) <= 0
                or account_id != str(int(account_id))
                or type(bot_id) is not str or not bot_id.strip()):
            continue
        key = platform_id, account_id
        if key in seen:
            continue
        seen.add(key)
        raw_roles = item.get("persona_roles", {})
        roles = {}
        if isinstance(raw_roles, dict):
            for persona_id, role_id in raw_roles.items():
                if (type(persona_id) is str and persona_id.strip()
                        and type(role_id) is str and role_id.strip()):
                    roles[persona_id.strip()] = role_id.strip()
        default_role_id = item.get("default_role_id")
        if type(default_role_id) is str and default_role_id.strip():
            roles.setdefault("", default_role_id.strip())
        account = {"platform_id": platform_id.strip(), "account_id": account_id,
                   "bot_id": bot_id.strip(), "persona_roles": roles}
        default_persona_id = item.get("default_persona_id")
        if (type(default_persona_id) is str and len(default_persona_id.strip()) <= 120
                and not any(ord(char) < 32 for char in default_persona_id)):
            account["default_persona_id"] = default_persona_id.strip() or "[%None]"
        if "native_windows" in item:
            account["native_windows"] = []
            for window in item["native_windows"] if isinstance(item["native_windows"], list) else ():
                if not isinstance(window, dict):
                    continue
                kind, peer, persona = (window.get(field) for field in
                                       ("scope_kind", "native_scope_id", "persona_id"))
                if (kind not in ("private", "group") or type(peer) is not str
                        or len(peer) > 32 or not peer.isascii() or not peer.isdecimal() or int(peer) <= 0
                        or peer != str(int(peer)) or type(persona) is not str
                        or not persona.strip() or len(persona) > 120
                        or any(ord(char) < 32 for char in persona)):
                    continue
                record = {"scope_kind": kind, "native_scope_id": peer, "persona_id": persona.strip()}
                origin = window.get("unified_msg_origin")
                if origin is not None:
                    prefix = platform_id.strip() + (":FriendMessage:" if kind == "private" else ":GroupMessage:")
                    if type(origin) is not str or not origin.startswith(prefix):
                        continue
                    ids = origin[len(prefix):].split("_")
                    if (len(ids) not in ((1,) if kind == "private" else (1, 2)) or ids[-1] != peer
                            or any(len(value) > 32 or not value.isascii() or not value.isdecimal()
                                   or int(value) <= 0 or value != str(int(value)) for value in ids)):
                        continue
                    record["unified_msg_origin"] = origin
                account["native_windows"].append(record)
        result.append(account)
    return sorted(result, key=lambda row: (row["platform_id"], row["account_id"]))


def _selected_legacy_accounts(config, accounts, differences):
    mode_present, raw_mode = _legacy_string(config, "basic_config.bot_scope_mode", differences)
    scope_present, scope_ids = _legacy_account_ids(config, "basic_config.bot_scope_ids", differences)
    by_selector = {}
    by_platform = {}
    for account in accounts:
        for selector in (account["account_id"], account["bot_id"]):
            by_selector.setdefault(selector, []).append(account)
        by_platform.setdefault(account["platform_id"], []).append(account)

    def matches_for(selector):
        return by_selector.get(selector) or by_platform.get(selector, [])

    if not mode_present:
        scope_invalid = any(item.field_id == "basic_config.bot_scope_ids"
                            and item.kind == "invalid_value" for item in differences)
        if len(accounts) == 1 and not scope_ids and not scope_invalid:
            return accounts
        differences.append(ConfigurationDifference(
            "multi_host.targets", "needs_input",
            "V1 未记录 Bot 作用域，当前连接有多个账号，无法判断旧目标属于哪个账号。",
        ))
        return []

    mode = raw_mode.strip().lower()
    if mode == "all":
        selected = list(accounts)
        if selected:
            differences.append(ConfigurationDifference(
                "multi_host.targets", "account_scope_snapshot",
                "V1 的全账号范围按本次预览时已连接的 OneBot 账号展开；未连接账号需上线后重新试算。",
                "basic_config.bot_scope_mode",
            ))
    elif mode in ("allowlist", "whitelist"):
        selected, matched, unknown, ambiguous = [], set(), [], []
        for selector in scope_ids:
            matches = matches_for(selector)
            if not matches:
                unknown.append(selector)
            elif len(matches) > 1:
                ambiguous.append(selector)
                continue
            for account in matches:
                key = account["platform_id"], account["account_id"]
                if key not in matched:
                    matched.add(key)
                    selected.append(account)
        if not scope_present or not scope_ids:
            differences.append(ConfigurationDifference(
                "multi_host.targets", "needs_input",
                "V1 Bot 白名单为空，没有可安全映射的账号。", "basic_config.bot_scope_ids",
            ))
        if unknown:
            differences.append(ConfigurationDifference(
                "multi_host.targets", "unmatched_account",
                f"V1 Bot 作用域中有 {len(unknown)} 个 ID 未匹配到当前连接账号，已跳过。",
                "basic_config.bot_scope_ids",
            ))
        if ambiguous:
            differences.append(ConfigurationDifference(
                "multi_host.targets", "ambiguous_account",
                f"V1 Bot 白名单中有 {len(ambiguous)} 个 ID 对应多个平台账号；V1 没有平台标识，已跳过这些映射。",
                "basic_config.bot_scope_ids",
            ))
    elif mode in ("denylist", "blacklist"):
        excluded, unknown, ambiguous = set(), [], []
        for selector in scope_ids:
            matches = matches_for(selector)
            if not matches:
                unknown.append(selector)
            elif len(matches) > 1:
                ambiguous.append(selector)
            excluded.update((account["platform_id"], account["account_id"]) for account in matches)
        selected = [account for account in accounts
                    if (account["platform_id"], account["account_id"]) not in excluded]
        if unknown:
            differences.append(ConfigurationDifference(
                "multi_host.targets", "unmatched_account",
                f"V1 Bot 排除列表中有 {len(unknown)} 个 ID 未匹配到当前连接账号。",
                "basic_config.bot_scope_ids",
            ))
        if ambiguous:
            differences.append(ConfigurationDifference(
                "multi_host.targets", "ambiguous_account",
                f"V1 Bot 排除列表中有 {len(ambiguous)} 个 ID 对应多个平台账号；为避免扩大范围，已排除所有同号账号。",
                "basic_config.bot_scope_ids",
            ))
    else:
        differences.append(ConfigurationDifference(
            "multi_host.targets", "invalid_value",
            "V1 Bot 作用域模式无法识别，账号目标未迁移。", "basic_config.bot_scope_mode",
        ))
        return []

    if not accounts and (scope_present or mode_present):
        differences.append(ConfigurationDifference(
            "multi_host.targets", "unavailable",
            "当前没有可核对的 OneBot 登录账号，旧目标 ID 暂不能迁移。",
            "basic_config.bot_scope_ids",
        ))
    return selected


def _legacy_group_ids(config, differences):
    primary_present, primary = _legacy_account_ids(
        config, "group_observation_config.group_whitelist_ids", differences,
    )
    alias_present, alias = _legacy_account_ids(
        config, "legacy_compat_config.target_group_ids", differences,
    )
    if primary_present and primary:
        if alias_present and alias and primary != alias:
            differences.append(ConfigurationDifference(
                "multi_host.group_targets", "group_precedence",
                "新群白名单与旧群目标列表不同；按 V1 兼容规则采用非空的新白名单。",
                "group_observation_config.group_whitelist_ids",
            ))
        return primary, "group_observation_config.group_whitelist_ids"
    if alias_present:
        return alias, "legacy_compat_config.target_group_ids"
    return [], "group_observation_config.group_whitelist_ids"


def _legacy_target_scope(config, accounts):
    """Resolve old authorization before considering persona availability."""
    differences = []
    inventory = _legacy_account_inventory(accounts)
    selected_accounts = _selected_legacy_accounts(config, inventory, differences)
    _private_present, private_ids = _legacy_account_ids(config, "basic_config.target_user_ids", differences)

    group_enabled_present, group_enabled = _legacy_boolean(
        config, "group_observation_config.enable_group_companion", differences,
    )
    group_ids, group_source = _legacy_group_ids(config, differences)
    if group_ids and not group_enabled:
        differences.append(ConfigurationDifference(
            "multi_host.group_targets", "disabled_source",
            ("V1 群陪伴未启用；群列表保留在旧配置中，本次不创建可生效的 V2 群目标。"
             if group_enabled_present else
             "V1 未记录群陪伴启用状态；为避免扩大接管范围，群列表保留在旧配置中。"),
            "group_observation_config.enable_group_companion" if group_enabled_present else group_source,
        ))
        group_ids = []

    access_present, access_mode = _legacy_string(
        config, "group_observation_config.group_access_mode", differences,
    )
    if not access_present:
        require_group, require_value = _legacy_boolean(
            config, "legacy_compat_config.require_target_group", differences,
        )
        if require_group:
            access_present = True
            access_mode = "whitelist" if require_value else "blacklist"
    if group_ids and access_present and access_mode.lower() not in ("whitelist", "allowlist"):
        differences.append(ConfigurationDifference(
            "multi_host.group_targets", "unsupported_mode",
            "V1 群准入不是白名单模式，V2 无法把它等价转换为明确群目标；群列表已跳过。",
            "group_observation_config.group_access_mode",
        ))
        group_ids = []

    return selected_accounts, private_ids, group_ids, group_source, differences


def legacy_authorized_windows(config, *, accounts=()):
    """Return exact old targets for read-only native persona discovery."""
    if not isinstance(config, dict):
        return []
    selected, private_ids, group_ids, _, _ = _legacy_target_scope(config, accounts)
    return [{**account, "scope_kind": kind, "native_scope_id": peer}
            for account in selected
            for kind, peers in (("private", private_ids), ("group", group_ids)) for peer in peers]


def legacy_persona_storage_shared(config):
    """Use the same recognized switch values as the configuration converter."""
    if not isinstance(config, dict):
        return False
    present, raw = _source_value(config, "basic_config.enable_multi_persona_mode")
    if not present:
        return True
    try:
        return not _boolean(raw)
    except ValueError:
        return False


def convert_account_configuration(config, *, accounts=()) -> ConfigurationMigration:
    """Map authorized targets to loaded native personas without changing runtime mode."""
    if not isinstance(config, dict):
        return ConfigurationMigration({}, {}, {}, (
            ConfigurationDifference("", "invalid_source", "V1 配置不是键值映射。"),
        ))
    selected_accounts, private_ids, group_ids, group_source, differences = _legacy_target_scope(config, accounts)
    specific_present, specific_persona = _legacy_string(
        config, "basic_config.plugin_specific_persona_id", differences,
    )
    multi_present, multi_enabled = _legacy_boolean(
        config, "basic_config.enable_multi_persona_mode", differences,
    )
    multi_valid = not any(item.field_id == "basic_config.enable_multi_persona_mode"
                          and item.kind == "invalid_value" for item in differences)
    persona_list_present, persona_ids = _legacy_persona_ids(
        config, "basic_config.multi_persona_ids", differences,
    )
    if persona_list_present and persona_ids and not multi_enabled:
        differences.append(ConfigurationDifference(
            "multi_host.personas", "inactive_source",
            "V1 多人格开关关闭；未启用的人格列表不参与迁移。",
            "basic_config.enable_multi_persona_mode",
        ))
    if not multi_valid:
        effective_personas = []
    else:
        effective_personas = [specific_persona] if specific_persona else []
        if multi_enabled:
            effective_personas.extend(persona_id for persona_id in persona_ids
                                      if persona_id not in effective_personas)
    invalid_specific = any(item.field_id == "basic_config.plugin_specific_persona_id"
                           and item.kind == "invalid_value" for item in differences)
    invalid_persona_list = any(item.field_id == "basic_config.multi_persona_ids"
                               and item.kind == "invalid_value" for item in differences)
    persona_selection_blocked = (not multi_valid or
        (not (multi_enabled and persona_ids) and invalid_specific) or
        (multi_enabled and persona_list_present and invalid_persona_list
         and not persona_ids and not specific_persona))

    if not private_ids and not group_ids:
        if specific_persona or multi_enabled or persona_ids:
            differences.append(ConfigurationDifference(
                "multi_host.personas", "needs_target",
                "V1 人格设置没有明确私聊或群聊目标可绑定；旧设置保留在 V1，本次不创建孤立人格映射。",
                "basic_config.plugin_specific_persona_id",
            ))
        return ConfigurationMigration({}, {}, {}, tuple(differences))
    if persona_selection_blocked:
        differences.append(ConfigurationDifference(
            "multi_host.personas", "invalid_persona_selection",
            "V1 人格选择无法可靠解析，相关目标未迁移。",
            "basic_config.enable_multi_persona_mode",
        ))
        return ConfigurationMigration({}, {}, {}, tuple(differences))
    if not selected_accounts and (private_ids or group_ids):
        differences.append(ConfigurationDifference(
            "multi_host.targets", "unavailable",
            "没有匹配到可用账号，旧私聊和群聊目标均未迁移。",
            "basic_config.bot_scope_mode",
        ))
        return ConfigurationMigration({}, {}, {}, tuple(differences))

    routes_by_account = {}
    private_targets = []
    group_targets = []
    for account in selected_accounts:
        account_key = account["platform_id"], account["account_id"]
        role_map = account["persona_roles"]
        native_windows = account.get("native_windows")
        authorized_windows = [row for row in native_windows or ()
            if row["native_scope_id"] in (private_ids if row["scope_kind"] == "private" else group_ids)]
        requested_personas = list(effective_personas)
        if native_windows is not None and not multi_enabled:
            # V1 single-role storage followed each window's native persona. A
            # plugin-specific persona was not a forced choice for every window.
            requested_personas.extend(row["persona_id"] for row in authorized_windows
                                      if row["persona_id"] not in requested_personas)
        if not requested_personas:
            default_persona = account.get("default_persona_id")
            if default_persona is None:
                differences.append(ConfigurationDifference(
                    "multi_host.personas", "needs_input",
                    f"账号 {account['account_id']} 的 V1 人格未明确选择；需要确认原会话人格后再迁移相关目标。",
                    "basic_config.plugin_specific_persona_id",
                ))
                continue
            requested_personas = [default_persona]
        persona_pairs = []
        missing = 0
        for persona_id in requested_personas:
            normalized = "" if persona_id == "[%None]" else persona_id
            role_id = role_map.get(persona_id, role_map.get(normalized))
            if not role_id:
                missing += 1
                continue
            pair = (persona_id, role_id)
            if pair not in persona_pairs:
                persona_pairs.append(pair)
        if missing:
            differences.append(ConfigurationDifference(
                "multi_host.personas", "unavailable_persona",
                f"V1 有 {missing} 个人格未在当前 AstrBot 加载，已跳过这些映射。",
                "basic_config.multi_persona_ids" if multi_enabled else "basic_config.plugin_specific_persona_id",
            ))
        if not persona_pairs:
            if private_ids or group_ids:
                differences.append(ConfigurationDifference(
                    "multi_host.targets", "unavailable_persona",
                    f"账号 {account['account_id']} 没有可用的人格映射，其目标未迁移。",
                    "basic_config.plugin_specific_persona_id",
                ))
            continue
        routes_by_account[account_key] = (account, persona_pairs)
        mapped_personas = {pair[0] for pair in persona_pairs}

        def window_available(kind, peer):
            if native_windows is None:
                return True
            windows = [row for row in authorized_windows
                       if row["scope_kind"] == kind and row["native_scope_id"] == peer]
            if windows and all(row["persona_id"] in mapped_personas for row in windows):
                return True
            differences.append(ConfigurationDifference(
                "multi_host.targets" if kind == "private" else "multi_host.group_targets",
                "unavailable_persona",
                f"账号 {account['account_id']} 的{'私聊' if kind == 'private' else '群聊'} {peer} 人格尚未核验或未登记，保留现有配置。",
                "basic_config.plugin_specific_persona_id",
            ))
            return False

        if private_ids:
            for participant_id in private_ids:
                if not window_available("private", participant_id):
                    continue
                private_targets.append({
                    "target_id": "v1t-" + hashlib.sha256(
                        json.dumps([account["platform_id"], account["account_id"], participant_id],
                                   ensure_ascii=True, separators=(",", ":")).encode("utf-8")
                    ).hexdigest()[:20],
                    "platform_id": account["platform_id"],
                    "account_id": account["account_id"],
                    "bot_id": account["bot_id"],
                    "participant_id": participant_id,
                })
        if group_ids:
            for group_id in group_ids:
                if not window_available("group", group_id):
                    continue
                group_targets.append({
                    "target_id": "v1g-" + hashlib.sha256(
                        json.dumps([account["platform_id"], account["account_id"], group_id],
                                   ensure_ascii=True, separators=(",", ":")).encode("utf-8")
                    ).hexdigest()[:20],
                    "platform_id": account["platform_id"],
                    "account_id": account["account_id"],
                    "bot_id": account["bot_id"],
                    "group_id": group_id,
                })

    if private_ids and not private_targets:
        differences.append(ConfigurationDifference(
            "multi_host.targets", "unavailable",
            "旧私聊目标未能与当前账号及已加载人格组成 V2 绑定。",
            "basic_config.target_user_ids",
        ))
    if group_ids and not group_targets:
        differences.append(ConfigurationDifference(
            "multi_host.group_targets", "unavailable",
            "旧群目标未能与当前账号及已加载人格组成 V2 绑定。",
            group_source,
        ))

    persona_mappings = []
    for account, pairs in routes_by_account.values():
        for persona_id, role_id in pairs:
            persona_mappings.append({"bot_id": account["bot_id"],
                                     "persona_id": persona_id or "[%None]",
                                     "role_id": role_id})

    changes = {}
    confidence = {}
    sources = {}
    account_sources = ["basic_config.bot_scope_mode", "basic_config.bot_scope_ids"]
    if private_targets:
        changes["multi_host.targets"] = private_targets
        confidence["multi_host.targets"] = "high"
        sources["multi_host.targets"] = tuple(account_sources + ["basic_config.target_user_ids"])
    if group_targets:
        changes["multi_host.group_targets"] = group_targets
        confidence["multi_host.group_targets"] = "high"
        sources["multi_host.group_targets"] = tuple(account_sources + [group_source,
            "group_observation_config.enable_group_companion"])
    if persona_mappings:
        changes["multi_host.personas"] = persona_mappings
        confidence["multi_host.personas"] = "high"
        persona_sources = account_sources + ["basic_config.plugin_specific_persona_id"]
        if multi_enabled:
            persona_sources.extend(["basic_config.multi_persona_ids", "basic_config.enable_multi_persona_mode"])
        sources["multi_host.personas"] = tuple(persona_sources)

    if len(private_targets) == 1:
        target = private_targets[0]
        account = next((row for row in selected_accounts
                        if row["platform_id"] == target["platform_id"]
                        and row["account_id"] == target["account_id"]), None)
        pairs = routes_by_account.get((target["platform_id"], target["account_id"]), (None, []))[1]
        if effective_personas and len(effective_personas) == 1 and pairs:
            single_persona = pairs[0]
        elif not effective_personas and len(pairs) == 1:
            single_persona = pairs[0]
        else:
            single_persona = None
        if account and "native_windows" in account:
            selected = {row["persona_id"] for row in account["native_windows"]
                        if row["scope_kind"] == "private"
                        and row["native_scope_id"] == target["participant_id"]}
            single_persona = next((pair for pair in pairs if pair[0] in selected), None) if len(selected) == 1 else None
        if account and single_persona:
            persona_id, role_id = single_persona
            single = {
                "target.platform_id": target["platform_id"],
                "target.account_id": target["account_id"],
                "target.bot_id": target["bot_id"],
                "target.participant_id": target["participant_id"],
                "target.role_id": role_id,
                "target.persona_id": "" if persona_id == "[%None]" else persona_id,
            }
            for field_id, value in single.items():
                changes[field_id] = value
                confidence[field_id] = "high"
                sources[field_id] = tuple(account_sources + ["basic_config.target_user_ids",
                    "basic_config.plugin_specific_persona_id"])

    return ConfigurationMigration(changes, confidence, sources, tuple(differences))
