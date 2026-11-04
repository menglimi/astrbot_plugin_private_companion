"""Parse native provider profiles and reply routing without loading credentials."""

from companion_v2.adapters.astrbot.provider import UsageTariff
from companion_v2.adapters.astrbot.provider_catalog import TextProviderSettings
from companion_v2.execution.models import encode
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind
from companion_v2.model_gateway.models import MAX_MODEL_OUTPUT_TOKENS
from companion_v2.model_gateway.replacement_rules import checked_rules


# 结构任务（表达学习、审阅、书写等）一次至少要这么多 token；插件全局
# 「最大输出 Token」低于它时，插件按这个下限上报 provider 能力，否则这些任务
# 根本存不下来。管理面据此解释「为什么全局写 2048、界面显示 8192」。
PROVIDER_OUTPUT_FLOOR = 8192


def provider_output_limit_rows(providers, *, plugin_global, primary_id, floor=PROVIDER_OUTPUT_FLOOR):
    """Tell the management page where a reported provider ceiling came from.

    The reply provider's ceiling is ``max(插件全局「最大输出 Token」, 地板)``,
    not a capability the AstrBot provider declared: raising the global setting is
    what actually moves it. Without that fact the unsaved-limit hint can only
    say "超过所选模型提供方的 N", and the operator goes looking in the provider
    settings, where the number does not exist. Every other provider keeps
    ``host_provider`` so its wording is unchanged.
    """
    rows = []
    for row in providers:
        if not isinstance(row, dict):
            rows.append(row)
            continue
        item = dict(row)
        from_plugin = bool(primary_id) and item.get("id") == primary_id
        item["max_output_tokens_basis"] = "plugin_global" if from_plugin else "host_provider"
        item["plugin_global_max_output_tokens"] = plugin_global
        if from_plugin and plugin_global < floor:
            item["floor_applied"] = floor
        rows.append(item)
    return rows


def conversation_provider_resolver(native_context):
    """Follow AstrBot's per-session model for replies and proactive dialogue."""
    def resolve(context, profile):
        if profile.kind not in ("conversation.reply", "conversation.poke_reply",
                                "proactive.plan", "proactive.compose"):
            return None
        scope = context.scope
        message_type = {ScopeKind.PRIVATE: "FriendMessage", ScopeKind.GROUP: "GroupMessage"}.get(scope.kind)
        if message_type is None:
            raise TaskError("model_provider_unavailable", "conversation models require a native conversation scope")
        getter = getattr(native_context, "get_using_provider", None)
        # Older host ports can retain their configured route. The native SDK
        # supplies this method and resolves both session and config-file choices.
        if not callable(getter):
            return None
        identifier = f"{scope.platform}:{message_type}:{scope.native_scope_id}"
        provider = getter(identifier)
        if provider is None:
            raise TaskError("model_provider_unavailable", "AstrBot has no model selected for this conversation")
        meta = provider.meta() if callable(getattr(provider, "meta", None)) else None
        provider_id = getattr(meta, "id", None) or getattr(provider, "provider_config", {}).get("id")
        if (not isinstance(provider_id, str) or not provider_id.strip()
                or native_context.get_provider_by_id(provider_id) is not provider):
            raise TaskError("model_provider_unavailable", "the selected AstrBot conversation provider was replaced")
        return provider_id
    return resolve


def parse_routing(model):
    primary = model.get("provider_id", "")
    if type(primary) is not str:
        raise ValueError("primary provider requires an ID")
    primary = primary.strip()
    raw_providers = model.get("additional_providers", [])
    if type(raw_providers) is not list or len(raw_providers) > 8:
        raise ValueError("saved per-provider overrides are limited to eight entries")
    providers = []
    fields = {"provider_id", "max_output_tokens", "maximum_input_estimate", "output_parameter", "streaming",
              "input_microusd_per_million", "cached_input_microusd_per_million",
              "output_microusd_per_million", "__template_key"}
    for raw in raw_providers:
        if type(raw) is not dict or set(raw) - fields:
            raise ValueError("additional provider has invalid fields")
        provider_id = raw.get("provider_id", "")
        if type(provider_id) is not str:
            raise ValueError("additional provider requires an ID")
        profile = TextProviderSettings(provider_id=provider_id.strip(),
            tariff=UsageTariff(raw.get("input_microusd_per_million", 0),
                              raw.get("cached_input_microusd_per_million", 0),
                              raw.get("output_microusd_per_million", 0)),
            # An undeclared provider carries the product ceiling: the global
            # ``model.max_output_tokens`` is the task default budget, so copying it
            # here would cap every task at the default and blame the provider.
            maximum_output_tokens=raw.get("max_output_tokens", MAX_MODEL_OUTPUT_TOKENS),
            maximum_input_estimate=raw.get("maximum_input_estimate", model.get("maximum_input_estimate", 131072)),
            output_parameter=raw.get("output_parameter", model.get("output_parameter", "max_tokens")),
            streaming=raw.get("streaming", model.get("streaming", False)))
        if profile.provider_id != primary:
            providers.append(profile)
    ids = [provider.provider_id for provider in providers]
    if len(ids) != len(set(ids)):
        raise ValueError("saved provider overrides must have distinct IDs")
    fallback = model.get("fallback_provider_id", "")
    if type(fallback) is not str:
        raise ValueError("fallback requires a provider ID")
    fallback = fallback.strip()
    if fallback == primary:
        fallback = ""
    raw_rules = model.get("replacement_rules", "[]")
    rules_json = raw_rules if type(raw_rules) is str else encode(raw_rules)
    rules = checked_rules(rules_json)
    # Provider IDs are checked against the live AstrBot catalog when adapters are
    # assembled. The saved settings parser has no host catalog to consult.
    output_limit = model.get("max_output_tokens", 2048)
    selected_ids = {rule.provider_id for rule in rules} | ({fallback} if fallback else set())
    if any(provider.provider_id in selected_ids and output_limit > provider.maximum_output_tokens
           for provider in providers):
        raise ValueError("reply output limit exceeds a fallback or replacement provider")
    return tuple(providers), fallback, rules_json
