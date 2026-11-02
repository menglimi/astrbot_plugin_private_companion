"""Adapters for the text providers currently loaded by AstrBot."""

from dataclasses import dataclass, replace
import json
from logging import getLogger

from companion_v2.execution.models import ProviderBinding, TaskError
from .provider import AstrBotTextProvider, UsageTariff, provider_fingerprint

logger = getLogger(__name__)


@dataclass(frozen=True)
class TextProviderSettings:
    provider_id: str
    tariff: UsageTariff
    maximum_output_tokens: int = 2048
    maximum_input_estimate: int = 131072
    output_parameter: str = "max_tokens"
    streaming: bool = False

    def __post_init__(self):
        if (type(self.provider_id) is not str or not self.provider_id.strip()
                or self.provider_id != self.provider_id.strip() or len(self.provider_id) > 512
                or not isinstance(self.tariff, UsageTariff)
                or type(self.maximum_output_tokens) is not int or not 1 <= self.maximum_output_tokens <= 32768
                or type(self.maximum_input_estimate) is not int or not 1024 <= self.maximum_input_estimate <= 1000000
                or self.output_parameter not in ("max_tokens", "max_completion_tokens")
                or type(self.streaming) is not bool):
            raise ValueError("invalid additional model provider profile")


def _provider_id(native):
    meta = native.meta() if callable(getattr(native, "meta", None)) else None
    return str(getattr(meta, "id", "") or getattr(native, "id", "")
               or getattr(native, "provider_config", {}).get("id", "")).strip()


def _text_provider(native_types, native):
    """Registered text class, or any provider exposing the AstrBot text surface."""
    checker = getattr(native_types, "is_text_provider", None)
    if callable(checker):
        if bool(checker(native)):
            return True
    elif isinstance(native, getattr(native_types, "provider_types", ()) or ()):
        return True
    # A provider may expose only the public text_chat method.  The adapter
    # handles it through the compatibility transport instead of dropping it.
    return callable(getattr(native, "text_chat", None))


def loaded_routing(fallback_provider_id, replacement_rules, provider_ids):
    """Keep saved default routes only while their AstrBot providers are loaded."""
    available = frozenset(provider_ids)
    fallback = fallback_provider_id if fallback_provider_id in available else ""
    raw = json.loads(replacement_rules or "[]")
    rules = raw if type(raw) is list else [raw]
    active = [rule for rule in rules if (not isinstance(rule, dict)
              or rule.get("enabled", True) is False or rule.get("provider_id") in available)]
    if fallback != fallback_provider_id or len(active) != len(rules):
        logger.warning("Ignoring saved model routes for AstrBot providers that are not currently loaded")
    encoded = replacement_rules if len(active) == len(rules) else json.dumps(active, ensure_ascii=False, separators=(",", ":"))
    return fallback, encoded


def assemble_providers(context, *, bridge, native_types, primary_id, additional=(), defaults=None):
    """Expose every loaded, verified AstrBot text provider to V2 routing.

    ``additional`` remains a compatibility layer for existing installations that
    saved per-provider limits or tariffs. It no longer controls availability.
    """
    additional = tuple(additional)
    overrides = {item.provider_id: item for item in additional}
    if len(overrides) != len(additional) or primary_id in overrides:
        raise ValueError("model provider profiles must have distinct IDs")
    if defaults is None:
        defaults = TextProviderSettings(primary_id, UsageTariff(0, 0, 0))
    if not isinstance(defaults, TextProviderSettings):
        raise TypeError("default model provider profile is invalid")

    adapters = []
    inventory_reader = getattr(context, "get_all_providers", None)
    inventory = list(inventory_reader() or []) if callable(inventory_reader) else []
    candidates = []
    seen = set()
    for native in inventory:
        if native_types.video_provider_type is not None and isinstance(native, native_types.video_provider_type):
            adapter_kind = "video"
        elif _text_provider(native_types, native):
            adapter_kind = "text"
        else:
            continue
        try:
            provider_id = _provider_id(native)
        except (AttributeError, TypeError, ValueError):
            continue
        if not provider_id or provider_id in seen or provider_id == primary_id:
            continue
        seen.add(provider_id)
        candidates.append((provider_id, native, adapter_kind))

    for provider_id, native, adapter_kind in candidates:
        settings = overrides.get(provider_id) or replace(defaults, provider_id=provider_id)
        try:
            if context.get_provider_by_id(provider_id) is not native:
                raise TaskError("host_provider_changed", "AstrBot provider changed during model assembly")
            if adapter_kind == "video":
                adapter = native_types.video_provider_factory(context, bridge, native_types, native, settings)
            else:
                adapter = AstrBotTextProvider(context, bridge=bridge,
                    binding=ProviderBinding("model.text", provider_id, native_types.version, provider_fingerprint(native)),
                    provider_id=provider_id, version=native_types.version,
                    provider_type=getattr(native_types, "provider_types", native_types.provider),
                    function_tool_type=native_types.function_tool, tool_set_type=native_types.tool_set,
                    tariff=settings.tariff, maximum_output_tokens=settings.maximum_output_tokens,
                    maximum_input_estimate=settings.maximum_input_estimate, output_parameter=settings.output_parameter,
                    streaming=settings.streaming)
            if adapter.native is not native:
                raise TaskError("host_provider_changed", "AstrBot provider changed during model assembly")
            adapter._current()
        except (TaskError, TypeError, ValueError, AttributeError, KeyError) as error:
            if provider_id in overrides:
                raise
            logger.warning("Loaded AstrBot %s provider %s is unavailable to V2 (%s)", adapter_kind,
                           provider_id, type(error).__name__)
            continue
        adapters.append(adapter)
    return tuple(adapters)
