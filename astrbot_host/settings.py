"""Validated settings for one explicitly selected staging profile."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import timedelta
import re
from typing import Any

from companion_v2.adapters.astrbot.native import route_id
from companion_v2.adapters.astrbot.provider import UsageTariff
from companion_v2.adapters.astrbot.provider_catalog import TextProviderSettings
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.conversation.forwards.models import (DEFAULT_MAX_NODES, MAX_DEPTH, MAX_FORWARDS, MAX_NODES,
                                                        ForwardIntakePolicy)
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from companion_v2.execution.models import SupervisorLimits
from companion_v2.model_gateway.budget import BudgetSoftPolicy
from companion_v2.model_gateway.models import Amount, BudgetPolicy, MAX_MODEL_OUTPUT_TOKENS
from .model_settings import parse_routing


PROFILE_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
INSTALLATION_PREFIX = "astrbot-private-companion-v2:"
# Dashboard authorization: short, because it exists to watch one exchange go out.
DELIVERY_AUTHORIZATION_TTL = timedelta(minutes=15)


class HostControlError(ValueError):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code


def _section(config: object, name: str) -> dict[str, Any]:
    value = config.get(name, {}) if hasattr(config, "get") else {}
    if not isinstance(value, dict):
        raise HostControlError("staging_settings_invalid",
                               f"staging settings contain an invalid section: {name}")
    return value


def _text(value: object, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise HostControlError("staging_settings_invalid", "staging settings contain an invalid text field")
    result = str(value).strip()
    if len(result) > 512 or required and not result:
        raise HostControlError("staging_settings_incomplete", "complete the staging target and model settings first")
    return result


def _integer(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise HostControlError("staging_settings_invalid", "staging settings contain an out-of-range integer")
    return value


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise HostControlError("staging_settings_invalid", "staging settings contain an invalid switch")
    return value


def _policy_values(policy_type, section: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields this policy understands.

    Legacy configuration sections may still carry renamed/removed keys after an
    upgrade.  Passing them as ``**kwargs`` made every mapping save fail with
    ``staging_settings_invalid`` even though the known fields were valid.
    """
    names = {item.name for item in fields(policy_type)}
    return {name: value for name, value in section.items() if name in names}


def _policy_parses(policy_type, section: dict[str, Any]) -> bool:
    try:
        policy_type(**_policy_values(policy_type, section))
    except (TypeError, ValueError):
        return False
    return True


def repair_host_policy_sections(config: dict[str, Any]) -> dict[str, list[str]]:
    """Drop known-but-invalid legacy policy values before a mapping save.

    A 6.x -> 7.x upgrade can leave a single stale enum/boolean in
    ``segmentation`` or ``voice_reply``.  That one value used to block the
    entire account/role save with a generic ``staging_settings_invalid``.  Keep
    every valid field, remove only the fields that prevent the policy from
    parsing, and fall back to the section default if the combination is still
    invalid.  Returns the removed field names for the save receipt.
    """
    if not isinstance(config, dict):
        return {}
    repaired: dict[str, list[str]] = {}
    for name, policy_type in (("segmentation", SegmentationPolicy),
                              ("voice_reply", VoiceReplyPolicy)):
        if name not in config:
            continue
        section = config.get(name)
        if not isinstance(section, dict):
            config[name] = {}
            repaired[name] = ["<section>"]
            continue
        candidate = _policy_values(policy_type, section)
        if _policy_parses(policy_type, candidate):
            continue
        removed: list[str] = []
        while candidate and not _policy_parses(policy_type, candidate):
            fixed = False
            for key in tuple(candidate):
                reduced = {item: value for item, value in candidate.items() if item != key}
                if _policy_parses(policy_type, reduced):
                    candidate = reduced
                    removed.append(key)
                    fixed = True
                    break
            if not fixed:
                # Several fields may be invalid at once. Drop one and retry
                # rather than discarding every valid sibling at the first miss.
                key = next(iter(candidate))
                candidate.pop(key)
                removed.append(key)
        if removed:
            config[name] = candidate
            repaired[name] = removed
    return repaired


@dataclass(frozen=True)
class HostSettings:
    enabled: bool
    profile_id: str
    delivery_mode: str
    platform_id: str
    account_id: str
    participant_id: str
    bot_id: str
    role_id: str
    persona_id: str
    provider_id: str
    task_prompt: str
    max_output_tokens: int
    maximum_input_estimate: int
    output_parameter: str
    tariff: UsageTariff
    budget: BudgetPolicy
    policy: ConversationPolicy
    limits: SupervisorLimits
    budget_soft: BudgetSoftPolicy = BudgetSoftPolicy()
    streaming: bool = False
    segmentation: SegmentationPolicy = SegmentationPolicy()
    voice_reply_enabled: bool = False
    voice_reply_policy: VoiceReplyPolicy = VoiceReplyPolicy()
    additional_providers: tuple[TextProviderSettings, ...] = ()
    fallback_provider_id: str = ""
    replacement_rules: str = "[]"
    fallback_on_refusal: bool = False
    forward_intake: ForwardIntakePolicy = ForwardIntakePolicy()

    @property
    def installation_id(self) -> str:
        return INSTALLATION_PREFIX + self.profile_id

    @classmethod
    def parse(cls, config: object) -> "HostSettings":
        staging = _section(config, "staging")
        target = _section(config, "target")
        model = _section(config, "model")
        runtime = _section(config, "runtime")
        try:
            additional_providers, fallback_provider_id, replacement_rules = parse_routing(model)
        except (TypeError, ValueError, AttributeError) as error:
            raise HostControlError("host_model_routes_invalid", "check additional providers, fallback and replacement rules") from error
        try:
            segmentation = SegmentationPolicy(**_policy_values(SegmentationPolicy,
                                                                _section(config, "segmentation")))
        except (TypeError, ValueError) as error:
            raise HostControlError("staging_settings_invalid",
                                   f"reply segmentation settings are invalid: {error}") from error
        voice_section = _section(config, "voice_reply")
        try:
            voice_policy = VoiceReplyPolicy(**_policy_values(VoiceReplyPolicy, voice_section))
        except (TypeError, ValueError) as error:
            raise HostControlError("staging_settings_invalid",
                                   f"voice reply settings are invalid: {error}") from error
        # 与 _conf_schema.json 的默认 profile_id 对齐（同为 "main"）。
        # 原先这里是 "preview-1"：缺键时安装标识会变成
        # 「astrbot-private-companion-v2:preview-1」，与界面显示、文档和她自己的数据根对不上。
        profile_id = _text(staging.get("profile_id", "main"), required=True)
        if PROFILE_PATTERN.fullmatch(profile_id) is None:
            raise HostControlError("staging_profile_invalid", "profile_id must use lowercase letters, digits, underscores or hyphens")
        legacy_delivery_mode = _text(staging.get("delivery_mode", "onebot_private"), required=True)
        if legacy_delivery_mode not in ("preview", "onebot_private"):
            raise HostControlError("staging_delivery_invalid", "the legacy delivery field is invalid")
        # Older installations may still carry "preview". The formal host always
        # uses the native OneBot sender; the legacy value only remains readable.
        delivery_mode = "onebot_private"
        output_parameter = _text(model.get("output_parameter", "max_tokens"), required=True)
        if output_parameter not in ("max_tokens", "max_completion_tokens"):
            raise HostControlError("staging_settings_invalid", "select a supported output token parameter")

        budget_tokens = _integer(runtime.get("budget_tokens", 1_000_000), minimum=1, maximum=10**12)
        budget_cost = _integer(runtime.get("budget_cost_microusd", 100_000_000), minimum=0, maximum=10**15)
        amount = Amount(budget_tokens, budget_cost)
        soft_kinds = tuple(kind.strip() for kind in
                           _text(runtime.get("daily_token_soft_deferred_kinds", "")).replace(",", "\n").split("\n")
                           if kind.strip())
        soft = BudgetSoftPolicy(enabled=_boolean(runtime.get("enable_daily_token_soft_limit", False)),
            # 缺键时的默认值必须与 _conf_schema.json 的 800000 一致。
            # 原先写 0，而 0 在软限额里的含义是「不设限额」：一份没有这个键的
            # 旧数据根，一旦把开关打开，看上去开了限额、实际什么都没限。
            tokens=_integer(runtime.get("daily_token_soft_limit", 800_000), minimum=0, maximum=10**12),
            kinds=soft_kinds)
        return cls(
            enabled=_boolean(staging.get("enabled", False)),
            profile_id=profile_id,
            delivery_mode=delivery_mode,
            platform_id=_text(target.get("platform_id", "")),
            account_id=_text(target.get("account_id", "")),
            participant_id=_text(target.get("participant_id", "")),
            bot_id=_text(target.get("bot_id", "staging-bot"), required=True),
            role_id=_text(target.get("role_id", "staging-role"), required=True),
            persona_id=_text(target.get("persona_id", "")),
            provider_id=_text(model.get("provider_id", "")),
            additional_providers=additional_providers,
            fallback_provider_id=fallback_provider_id,
            replacement_rules=replacement_rules,
            fallback_on_refusal=_boolean(model.get("fallback_on_refusal", False)),
            task_prompt=_text(model.get("task_prompt", "沿用当前 AstrBot 人格，先回应本轮真正的问题或话题。自然、具体地说清意思；只使用本轮输入和有依据的材料，不补造事实。按任务需要调整长短与格式，闲聊不复述、不写客服式收尾。")),
            max_output_tokens=_integer(model.get("max_output_tokens", 2048), minimum=1, maximum=MAX_MODEL_OUTPUT_TOKENS),
            maximum_input_estimate=_integer(
                model.get("maximum_input_estimate", 131_072), minimum=1_024, maximum=1_000_000
            ),
            output_parameter=output_parameter,
            streaming=_boolean(model.get("streaming", False)),
            segmentation=segmentation,
            voice_reply_enabled=_boolean(voice_section.get("enabled", False)),
            voice_reply_policy=voice_policy,
            tariff=UsageTariff(
                _integer(model.get("input_microusd_per_million", 0), minimum=0, maximum=10**12),
                _integer(model.get("cached_input_microusd_per_million", 0), minimum=0, maximum=10**12),
                _integer(model.get("output_microusd_per_million", 0), minimum=0, maximum=10**12),
            ),
            budget=BudgetPolicy(amount, amount, amount, amount, amount,
                                enabled=_boolean(runtime.get("enable_daily_model_hard_limit", False))),
            budget_soft=soft,
            policy=ConversationPolicy(
                _integer(runtime.get("quiet_seconds", 0), minimum=0, maximum=300),
                _integer(runtime.get("maximum_wait_seconds", 0), minimum=0, maximum=600),
                _integer(runtime.get("turn_timeout_seconds", 300), minimum=1, maximum=600),
                _integer(runtime.get("part_gap_seconds", 0), minimum=0, maximum=60),
            ),
            forward_intake=ForwardIntakePolicy(
                _integer(runtime.get("forward_max_forwards", MAX_FORWARDS), minimum=1, maximum=MAX_FORWARDS),
                _integer(runtime.get("forward_max_nodes", DEFAULT_MAX_NODES), minimum=1, maximum=MAX_NODES),
                _integer(runtime.get("forward_max_depth", MAX_DEPTH), minimum=1, maximum=MAX_DEPTH),
                _boolean(runtime.get("forward_parse_nested", True)),
            ),
            limits=SupervisorLimits(
                _integer(runtime.get("foreground_slots", 2), minimum=1, maximum=32),
                _integer(runtime.get("background_slots", 3), minimum=1, maximum=32),
                _integer(runtime.get("queued_per_lane", 64), minimum=1, maximum=10_000),
                _integer(runtime.get("retained_tasks", 10_000), minimum=1, maximum=100_000),
            ),
        )

    def require_ready(self, *, require_enabled: bool = True) -> None:
        if require_enabled and not self.enabled:
            raise HostControlError("staging_disabled", "enable the staging profile before using the control plane")
        for value in (self.platform_id, self.account_id, self.participant_id, self.provider_id):
            if not value:
                raise HostControlError("staging_settings_incomplete", "complete the staging target and model settings first")
        route_id(self.account_id)
        route_id(self.participant_id)

    def with_identity(self, identity: dict[str, str]) -> "HostSettings":
        """The same settings carrying a conversation target derived at binding time.

        Single-target mode no longer reads platform/account/target/bot/role out of the
        plugin config: the operator binds them from the chat instead, and the values
        arrive here afterwards. Everything else -- model, budget, policy, limits --
        stays exactly as configured, so this only ever fills the identity fields.

        Returning a new instance rather than mutating keeps ``HostSettings`` frozen and
        makes the substitution explicit at the one call site that does it.
        """
        from dataclasses import replace

        known = ("platform_id", "account_id", "participant_id", "bot_id", "role_id", "persona_id")
        unknown = set(identity) - set(known)
        if unknown:
            raise HostControlError("host_identity_invalid", "the derived identity carries unknown fields")
        return replace(self, **{name: identity[name] for name in known if name in identity})

    @property
    def has_target(self) -> bool:
        """Whether a conversation target is known yet.

        Separated from ``require_ready`` because the two answer different questions:
        this one is "is there something to talk to", which is false during first-run
        binding and which the setup progress reports as a step. Trying to run with no
        target still fails loudly through ``require_ready``.
        """
        return bool(self.platform_id and self.account_id and self.participant_id)
