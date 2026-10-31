"""Adapters for the text providers currently loaded by AstrBot.

Two different limits live here and must not be derived from each other:

* ``model.max_output_tokens`` is the default output budget of a task: how long
  the next request should be unless the task configuration says otherwise;
* a provider's ``maximum_output_tokens`` is an assertion about what that
  provider accepts in one call, so it decides whether a task configuration is
  valid at all.

Sharing one number used to mean a task could never ask for more than the global
default, and raising it was refused in the provider's name even though nobody
had ever declared a ceiling for that provider. Undeclared providers therefore
carry the product ceiling; an explicit per-provider declaration still wins and
may be smaller.
"""

from dataclasses import dataclass, replace
import json
from logging import getLogger

from companion_v2.execution.models import ProviderBinding, TaskError
from companion_v2.model_gateway.models import MAX_MODEL_OUTPUT_TOKENS
from .provider import AstrBotTextProvider, UsageTariff, provider_fingerprint

logger = getLogger(__name__)


@dataclass(frozen=True)
class TextProviderSettings:
    provider_id: str
    tariff: UsageTariff
    maximum_output_tokens: int = MAX_MODEL_OUTPUT_TOKENS
    maximum_input_estimate: int = 131072
    output_parameter: str = "max_tokens"
    streaming: bool = False

    def __post_init__(self):
        if (type(self.provider_id) is not str or not self.provider_id.strip()
                or self.provider_id != self.provider_id.strip() or len(self.provider_id) > 512):
            raise ValueError("model provider ID is invalid")
        if not isinstance(self.tariff, UsageTariff):
            raise ValueError("model provider tariff is invalid")
        if (type(self.maximum_output_tokens) is not int
                or not 1 <= self.maximum_output_tokens <= MAX_MODEL_OUTPUT_TOKENS):
            raise ValueError("model provider output limit is invalid")
        if (type(self.maximum_input_estimate) is not int
                or not 1024 <= self.maximum_input_estimate <= 1000000):
            raise ValueError("model provider input estimate is invalid")
        if self.output_parameter not in ("max_tokens", "max_completion_tokens"):
            raise ValueError("model provider output parameter is invalid")
        if type(self.streaming) is not bool:
            raise ValueError("model provider streaming switch is invalid")


def _provider_id(native):
    meta = native.meta() if callable(getattr(native, "meta", None)) else None
    return str(getattr(meta, "id", "") or getattr(native, "id", "")
               or getattr(native, "provider_config", {}).get("id", "")).strip()


def _text_provider(native_types, native):
    """Registered text class, or any provider exposing the AstrBot text surface."""
    checker = getattr(native_types, "is_text_provider", None)
    if callable(checker):
        return bool(checker(native))
    return isinstance(native, getattr(native_types, "provider_types", ()) or ())


UNAVAILABLE_REASON_CHARS = 180


def _unavailable_detail(error):
    """Bounded single-line reason for one provider the catalog cannot wrap.

    The type name alone cannot separate a configuration refusal from a defect:
    ``AstrBotTextProvider`` raises ``ValueError`` for a wrong class, an
    unusable streaming request and an overriding ``custom_extra_body``, while
    this module raises ``TaskError`` when the host swaps the provider during
    assembly. Every one of those messages is a fixed contract string written by
    this plugin, never user text, so quoting it is safe; anything unrecognized
    keeps the historical type name only.
    """
    if isinstance(error, TaskError):
        return f"{type(error).__name__} code={error.code}"
    if isinstance(error, (TypeError, ValueError)):
        reason = " ".join(str(error).split())[:UNAVAILABLE_REASON_CHARS]
        if reason:
            return f"{type(error).__name__}: {reason}"
    return type(error).__name__


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

    ``defaults`` supplies the shared tariff, input estimate, output parameter and
    streaming choice. Its ``maximum_output_tokens`` is deliberately not copied to
    providers that declare no limit of their own: that field is a task budget,
    while the provider ceiling is the product ceiling ``MAX_MODEL_OUTPUT_TOKENS``.
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
        # This catalog builds fallback text adapters; a provider registered for
        # both surfaces (such as native Gemini) belongs to the text lane here.
        if _text_provider(native_types, native):
            adapter_kind = "text"
        elif native_types.video_provider_type is not None and isinstance(native, native_types.video_provider_type):
            adapter_kind = "video"
        else:
            continue
        try:
            provider_id = _provider_id(native)
        except (AttributeError, TypeError, ValueError) as error:
            logger.warning("Loaded AstrBot provider has no readable ID and stays unavailable to V2 (%s)",
                           _unavailable_detail(error))
            continue
        if not provider_id or provider_id in seen or provider_id == primary_id:
            continue
        seen.add(provider_id)
        candidates.append((provider_id, native, adapter_kind))

    for provider_id, native, adapter_kind in candidates:
        settings = overrides.get(provider_id) or replace(defaults, provider_id=provider_id,
                                                        maximum_output_tokens=MAX_MODEL_OUTPUT_TOKENS)
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
                           provider_id, _unavailable_detail(error))
            continue
        adapters.append(adapter)
    return tuple(adapters)
