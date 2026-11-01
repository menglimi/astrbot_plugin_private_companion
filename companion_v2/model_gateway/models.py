"""Trusted model task contracts. Usage and reservations have different meanings."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import inspect
import json
from typing import Awaitable, Callable

from companion_v2.conversation.foreground import ModelDraft, PromptRequest
from companion_v2.execution.models import Lane, ProviderBinding, digest, encode
from companion_v2.identity_policy.models import _opaque

from .failures import ModelFailure
from .refusal import DEFAULT_REFUSAL_TERMS_TEXT, parse_refusal_terms
from .response_diagnostics import ResponseDiagnostics


MAX_TOOL_CATALOG = 64
MAX_MODEL_OUTPUT_TOKENS = 65_536

# A model call whose provider never returned a usage receipt would otherwise keep
# its ceiling in ``held_tokens`` for the lifetime of the installation. The
# reservation is returned to the account by writing this marker into the existing
# ``receipt_ref`` column, so no schema change is needed and a late trusted usage
# receipt can still replace it. Callers must never guess usage from the reserved
# amount: this marker means "the hold ended, the tokens are still unconfirmed".
RELEASE_RECEIPT_PREFIX = "local:released:"
RELEASE_WITHOUT_USAGE = "no-usage-receipt"
RELEASE_EXPIRED = "expired-reservation"
RELEASE_REASONS = (RELEASE_WITHOUT_USAGE, RELEASE_EXPIRED)


def is_released(receipt_ref) -> bool:
    """Return whether this call's reservation was already returned to its account."""
    return type(receipt_ref) is str and receipt_ref.startswith(RELEASE_RECEIPT_PREFIX)


def released_receipt(reason: str, receipt_ref: str | None = None) -> str:
    """Build the durable release marker, keeping any real provider receipt beside it."""
    if reason not in RELEASE_REASONS:
        raise ValueError("a reservation release needs a fixed reason")
    marker = RELEASE_RECEIPT_PREFIX + reason
    if receipt_ref is None:
        return marker
    if type(receipt_ref) is not str or not receipt_ref or is_released(receipt_ref):
        raise ValueError("a release marker wraps one real provider receipt")
    return marker + ":" + receipt_ref


@dataclass(frozen=True)
class Amount:
    tokens: int
    cost_microusd: int
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self):
        for value in (self.tokens, self.cost_microusd):
            if type(value) is not int or not 0 <= value <= 10**12:
                raise ValueError("usage must be bounded integer tokens and millionths of USD")
        for value in (self.input_tokens, self.cached_input_tokens, self.cache_creation_tokens, self.output_tokens):
            if value is not None and (type(value) is not int or not 0 <= value <= 10**12):
                raise ValueError("usage detail must be a bounded nonnegative token count")
        if (self.input_tokens is not None and self.cached_input_tokens is not None
                and self.cached_input_tokens > self.input_tokens):
            raise ValueError("cached input cannot exceed reported input")
        if (self.input_tokens is not None and self.cache_creation_tokens is not None
                and self.cached_input_tokens is not None
                and self.cached_input_tokens + self.cache_creation_tokens > self.input_tokens):
            raise ValueError("cache usage details cannot exceed reported input")


@dataclass(frozen=True)
class BudgetPolicy:
    installation: Amount
    bot: Amount
    role: Amount
    window: Amount
    background: Amount
    enabled: bool = True

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("hard budget admission needs an explicit switch")
        if not all(isinstance(value, Amount) for value in (
                self.installation, self.bot, self.role, self.window, self.background)):
            raise ValueError("all daily budget ceilings must be explicit")
        if (self.background.tokens > self.installation.tokens
                or self.background.cost_microusd > self.installation.cost_microusd):
            raise ValueError("background ceiling cannot exceed the installation ceiling")

    def document(self) -> dict:
        # Existing explicit policies keep their original serialized shape and hash.
        # Host defaults opt out explicitly; their amounts remain useful for settings.
        result = asdict(self)
        if self.enabled:
            result.pop("enabled")
        return result


@dataclass(frozen=True)
class PromptSection:
    key: str
    label: str
    default_prompt: str
    condition: str
    config_group: str = ""
    field: str = ""
    variables: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("key", "label", "condition"):
            _opaque(getattr(self, name), field=name)
        if type(self.default_prompt) is not str or bool(self.config_group) != bool(self.field):
            raise ValueError("prompt sections require text and a complete optional configuration binding")
        for name in ("config_group", "field"):
            if getattr(self, name):
                _opaque(getattr(self, name), field=name)
        object.__setattr__(self, "variables", tuple(self.variables))

    @property
    def editable(self):
        return bool(self.field)


@dataclass(frozen=True)
class ModelTaskProfile:
    kind: str
    lane: Lane
    default_provider: str
    default_prompt: str
    default_fallback: str = ""
    max_output_tokens: int = 1024
    capabilities: tuple[str, ...] = ("text",)
    version: str = "1"
    replacement_rules: str = "[]"
    fallback_on_refusal: bool = False
    timeout_seconds: int = 60
    replacement_scope: str = "all"
    prompt_sections: tuple[PromptSection, ...] = ()
    refusal_terms: str = DEFAULT_REFUSAL_TERMS_TEXT

    def __post_init__(self):
        for name in ("kind", "default_provider", "version"):
            _opaque(getattr(self, name), field=name)
        if not isinstance(self.lane, Lane) or not isinstance(self.default_prompt, str):
            raise ValueError("model tasks require an explicit lane and task prompt")
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= MAX_MODEL_OUTPUT_TOKENS:
            raise ValueError("output ceiling is outside the local model profile")
        if self.default_fallback:
            _opaque(self.default_fallback, field="default_fallback")
        if not isinstance(self.default_fallback, str):
            raise ValueError("fallback must be an identifier or explicit empty string")
        if not self.capabilities or len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("model capabilities must be nonempty and unique")
        for capability in self.capabilities:
            _opaque(capability, field="model capability")
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        from .replacement_rules import checked_rules
        checked_rules(self.replacement_rules)
        if type(self.fallback_on_refusal) is not bool:
            raise ValueError("refusal replacement must be an explicit boolean")
        parse_refusal_terms(self.refusal_terms)
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 600:
            raise ValueError("model timeout must be between 1 and 600 seconds")
        if self.replacement_scope not in ("all", "plugin", "conversation"):
            raise ValueError("replacement scope must name a supported task class")
        if (any(not isinstance(section, PromptSection) for section in self.prompt_sections)
                or len({section.key for section in self.prompt_sections}) != len(self.prompt_sections)
                or any(section.key == "task_prompt" for section in self.prompt_sections)):
            raise ValueError("business prompt sections must have distinct registered keys")
        object.__setattr__(self, "prompt_sections", tuple(self.prompt_sections))

    @property
    def group(self) -> str:
        return "model." + self.kind


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    action: str
    arguments_json: str

    def __post_init__(self):
        _opaque(self.call_id, field="tool call ID")
        _opaque(self.action, field="tool action")
        if not isinstance(self.arguments_json, str) or len(self.arguments_json) > 16384:
            raise ValueError("tool arguments must be bounded JSON")
        arguments = json.loads(self.arguments_json)
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        canonical = encode(arguments)
        if len(canonical) > 16384:
            raise ValueError("canonical tool arguments exceed the local limit")
        object.__setattr__(self, "arguments_json", canonical)


@dataclass(frozen=True)
class ToolSpec:
    action: str
    description: str
    parameters_json: str


@dataclass(frozen=True)
class ToolExchange:
    call: ToolCall
    result_json: str


@dataclass(frozen=True)
class CacheSignature:
    """Bounded fingerprints of cache-relevant request structure; never prompt text."""

    model_hash: str
    system_hash: str
    system_parts: tuple[str, ...]
    tools_hash: str
    tool_parts: tuple[str, ...]
    context_hashes: tuple[str, ...]
    context_bytes: int
    context_observed: bool
    estimates_observed: bool = False
    context_est_tokens: int = 0
    system_est_tokens: int = 0
    tool_est_tokens: int = 0
    message_estimates_observed: bool = False
    message_est_tokens: int = 0
    message_chars: int = 0
    message_bytes: int = 0
    request_estimate_observed: bool = False
    request_est_tokens: int = 0
    context_availability_observed: bool = False
    context_availability_hash: str = ""
    deferred_material_count: int = 0
    unavailable_material_count: int = 0
    context_sequence_hash: str = ""
    context_multiset_hash: str = ""
    context_material_count: int = -1
    context_chars: int = 0
    request_stats_observed: bool = False
    message_count: int = 0
    system_message_count: int = 0
    system_chars: int = 0
    system_bytes: int = 0
    tool_count: int = 0
    tool_bytes: int = 0
    image_count: int = 0
    audio_count: int = 0
    video_count: int = 0

    def __post_init__(self):
        if not self.context_sequence_hash:
            object.__setattr__(self, "context_sequence_hash", digest(list(self.context_hashes)))
        if not self.context_multiset_hash:
            object.__setattr__(self, "context_multiset_hash", digest(sorted(self.context_hashes)))
        if not self.context_availability_hash:
            object.__setattr__(self, "context_availability_hash", digest({"deferred": [], "unavailable": []}))
        if self.context_material_count == -1:
            object.__setattr__(self, "context_material_count", len(self.context_hashes))
        if (type(self.system_parts) is not tuple or len(self.system_parts) > 32
                or type(self.tool_parts) is not tuple or len(self.tool_parts) > 64
                or type(self.context_hashes) is not tuple or len(self.context_hashes) > 256):
            raise ValueError("cache signature must contain bounded hash lists")
        hashes = (self.model_hash, self.system_hash, self.tools_hash, *self.system_parts,
                  *self.tool_parts, self.context_sequence_hash, self.context_multiset_hash,
                  self.context_availability_hash, *self.context_hashes)
        counts = (self.context_material_count, self.context_chars, self.context_bytes,
                  self.context_est_tokens, self.system_est_tokens, self.tool_est_tokens,
                  self.message_est_tokens, self.message_chars, self.message_bytes, self.request_est_tokens,
                  self.deferred_material_count, self.unavailable_material_count,
                  self.message_count, self.system_message_count, self.system_chars,
                  self.system_bytes, self.tool_count, self.tool_bytes, self.image_count,
                  self.audio_count, self.video_count)
        if (any(type(value) is not str or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value) for value in hashes)
                or any(type(value) is not int or not 0 <= value <= 2**31 - 1 for value in counts)
                or type(self.context_observed) is not bool
                or type(self.estimates_observed) is not bool
                or type(self.message_estimates_observed) is not bool
                or type(self.request_estimate_observed) is not bool
                or type(self.context_availability_observed) is not bool
                or type(self.request_stats_observed) is not bool):
            raise ValueError("cache signature must contain bounded hashes and context metadata")

    def to_record(self):
        return {"version": 5, "model_hash": self.model_hash, "system_hash": self.system_hash,
                "system_parts": list(self.system_parts), "tools_hash": self.tools_hash,
                "tool_parts": list(self.tool_parts), "context_sequence_hash": self.context_sequence_hash,
                "context_multiset_hash": self.context_multiset_hash, "context_hashes": list(self.context_hashes),
                "context_material_count": self.context_material_count, "context_chars": self.context_chars,
                "context_bytes": self.context_bytes, "context_observed": self.context_observed,
                "estimates_observed": self.estimates_observed,
                "context_est_tokens": self.context_est_tokens, "system_est_tokens": self.system_est_tokens,
                "tool_est_tokens": self.tool_est_tokens,
                "message_estimates_observed": self.message_estimates_observed,
                "message_est_tokens": self.message_est_tokens, "message_chars": self.message_chars,
                "message_bytes": self.message_bytes, "request_estimate_observed": self.request_estimate_observed,
                "request_est_tokens": self.request_est_tokens,
                "context_availability_observed": self.context_availability_observed,
                "context_availability_hash": self.context_availability_hash,
                "deferred_material_count": self.deferred_material_count,
                "unavailable_material_count": self.unavailable_material_count,
                "request_stats_observed": self.request_stats_observed, "message_count": self.message_count,
                "system_message_count": self.system_message_count, "system_chars": self.system_chars,
                "system_bytes": self.system_bytes, "tool_count": self.tool_count, "tool_bytes": self.tool_bytes,
                "image_count": self.image_count, "audio_count": self.audio_count, "video_count": self.video_count}


@dataclass(frozen=True)
class ModelEnvelope:
    original: PromptRequest
    task_prompt: str
    max_output_tokens: int
    configuration_token: str
    tools: tuple[ToolSpec, ...] = ()
    tool_history: tuple[ToolExchange, ...] = ()
    # Ephemeral owner fence, never serialized or sent to the model. Native
    # adapters call it after asynchronous host preparation, just before I/O.
    before_dispatch: Callable[[], object] | None = field(default=None, compare=False, repr=False)
    record_cache_signature: Callable[[CacheSignature], object] | None = field(default=None, compare=False, repr=False)
    record_prompt_snapshot: Callable[[list[dict]], object] | None = field(default=None, compare=False, repr=False)
    model_override: str = ""
    provider_capabilities: tuple[str, ...] = ()
    on_reply_prefix: Callable[[ModelDraft], Awaitable[bool]] | None = field(default=None, compare=False, repr=False)
    reply_prefix_ready: Callable[[ModelDraft], bool] | None = field(default=None, compare=False, repr=False)
    refusal_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapturedModelOutput:
    """Unvalidated private output; receiving it does not authorize a fact write."""

    text: str = field(repr=False)
    finish_reason: str = "stop"

    def __post_init__(self):
        if not isinstance(self.text, str) or len(self.text) > 65536:
            raise ValueError("captured model output must be bounded text")
        if self.finish_reason not in ("stop", "length", "refusal", "invalid", "oversized"):
            raise ValueError("captured output requires a known completion classification")


@dataclass(frozen=True)
class ProviderResponse:
    status: str
    draft: ModelDraft | None = None
    usage: Amount | None = None
    receipt_ref: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    failure: ModelFailure | None = None
    captured_output: CapturedModelOutput | None = None
    diagnostics: ResponseDiagnostics | None = None

    def __post_init__(self):
        if self.diagnostics is not None and not isinstance(self.diagnostics, ResponseDiagnostics):
            raise ValueError("provider diagnostics must contain only trusted response metadata")
        if self.status not in ("generated", "not_started", "unknown"):
            raise ValueError("provider result requires an explicit processing outcome")
        if self.failure is not None and (
                not isinstance(self.failure, ModelFailure) or self.status == "generated"):
            raise ValueError("only unsuccessful responses may carry a fixed failure classification")
        if (not isinstance(self.tool_calls, tuple) or len(self.tool_calls) > 8 or
                any(not isinstance(call, ToolCall) for call in self.tool_calls) or
                len({call.call_id for call in self.tool_calls}) != len(self.tool_calls)):
            raise ValueError("a model round may request at most eight distinct structured tools")
        if self.status == "generated":
            if (self.draft is not None and not isinstance(self.draft, ModelDraft)) or (
                    self.captured_output is not None and not isinstance(self.captured_output, CapturedModelOutput)) or (
                    sum((self.draft is not None, bool(self.tool_calls), self.captured_output is not None)) != 1):
                raise ValueError("a generated response contains one draft, tool round or private captured output")
        elif self.draft is not None or self.tool_calls or self.captured_output is not None:
            raise ValueError("unprocessed responses cannot contain a draft or tools")
        if self.usage is not None and (not isinstance(self.usage, Amount) or self.receipt_ref is None):
            raise ValueError("actual usage requires an adapter receipt")
        if self.receipt_ref is not None:
            _opaque(self.receipt_ref, field="provider receipt")
        if self.status == "not_started" and self.usage != Amount(0, 0):
            raise ValueError("fallback requires explicit zero usage and no processing")


@dataclass(frozen=True)
class LocalProvider:
    binding: ProviderBinding
    capabilities: tuple[str, ...]
    maximum_output_tokens: int
    reserve: Callable[[ModelEnvelope], Amount]
    generate: Callable[[ModelEnvelope], Awaitable[ProviderResponse]]
    # 适配器自己声明的输入预估上限。owner 侧要在发请求前判断材料装不装得下，
    # 只能从这里读；`reserve` 只回答"装不下"，说不出被拒的那条线在哪。
    # 默认值与宿主配置的默认值一致，本地测试适配器可以不管这个字段。
    maximum_input_estimate: int = 131072
    transport: str = "local_test_adapter"
    default_streaming: bool = False
    supports_model_override: bool = False
    # 这一层实际会用的模型标识（适配器读宿主 get_model() 得来）。
    # 管理面把 provider 列成一个 id 时，多个同类型 provider 看起来完全一样，
    # 选错只能靠试。带上模型名才区分得开，这也是 V1 面板一直在显示的字段。
    #
    # 允许空串而不是必填：本地测试适配器和部分第三方传输没有"模型"这个概念，
    # 让它们编一个名字出来，比让它们留空更糟。
    model: str = ""

    def __post_init__(self):
        if type(self.default_streaming) is not bool:
            raise ValueError("provider stream default must be explicit")
        _opaque(self.transport, field="model transport")
        if type(self.model) is not str:
            raise ValueError("provider model identifier must be a string")
        if self.model:
            _opaque(self.model, field="provider model")
        if not isinstance(self.binding, ProviderBinding) or not inspect.iscoroutinefunction(self.generate):
            raise ValueError("local provider requires a binding and a cooperative async handler")
        if not callable(self.reserve) or inspect.iscoroutinefunction(self.reserve):
            raise ValueError("reservation estimator must be a bounded synchronous adapter function")
        if type(self.maximum_output_tokens) is not int or not 1 <= self.maximum_output_tokens <= MAX_MODEL_OUTPUT_TOKENS:
            raise ValueError("provider output limit must be explicit")
        if type(self.maximum_input_estimate) is not int or not 1024 <= self.maximum_input_estimate <= 1_000_000:
            raise ValueError("provider input limit must be explicit")
        if not self.capabilities or len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("provider capabilities must be explicit and unique")
        for capability in self.capabilities:
            _opaque(capability, field="provider capability")
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        if type(self.supports_model_override) is not bool:
            raise ValueError("model override support must be explicit")


@dataclass(frozen=True)
class ModelCompletion:
    call_id: str
    draft: ModelDraft | None
    usage_known: bool
    tool_calls: tuple[ToolCall, ...] = ()
    captured_output: CapturedModelOutput | None = None
