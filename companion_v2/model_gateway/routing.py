"""Per-task configuration adoption with explicit provider and prompt provenance."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from types import MappingProxyType

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationSnapshot
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.configuration.service import ConfigurationService
from companion_v2.conversation.foreground import PromptRequest
from companion_v2.execution.models import TaskError, digest, require
from companion_v2.execution.runner import TaskRun

from .models import LocalProvider, MAX_MODEL_OUTPUT_TOKENS, ModelEnvelope, ModelTaskProfile
from .refusal import parse_refusal_terms
from .replacement_rules import checked_rules, match_route, output_capabilities, validate_targets


CONSUMER = "model_gateway"


def configuration_fields(profiles: tuple[ModelTaskProfile, ...]) -> tuple[FieldDefinition, ...]:
    fields = []
    for profile in profiles:
        for suffix, kind, default in (
            ("provider", ValueKind.TEXT, profile.default_provider),
            ("fallback", ValueKind.TEXT, profile.default_fallback or profile.default_provider),
            ("prompt", ValueKind.TEXT, profile.default_prompt),
            ("max_output_tokens", ValueKind.INTEGER, profile.max_output_tokens),
            ("replacement_rules", ValueKind.TEXT, profile.replacement_rules),
            ("fallback_on_refusal", ValueKind.BOOLEAN, profile.fallback_on_refusal),
            ("refusal_terms", ValueKind.TEXT, profile.refusal_terms),
            ("timeout_seconds", ValueKind.INTEGER, profile.timeout_seconds),
            ("replacement_scope", ValueKind.TEXT, profile.replacement_scope),
        ):
            boundaries = ({"minimum": 1, "maximum": 600} if suffix == "timeout_seconds" else
                          {"minimum": 1, "maximum": MAX_MODEL_OUTPUT_TOKENS} if kind is ValueKind.INTEGER else {})
            if suffix == "refusal_terms":
                boundaries = {"maximum_chars": 4096}
            fields.append(FieldDefinition(profile.group + "." + suffix, profile.group, CONSUMER,
                                          kind, default, **boundaries))
    return tuple(fields)


@dataclass(frozen=True)
class ResolvedModelPlan:
    envelope: ModelEnvelope
    snapshot: ConfigurationSnapshot
    providers: tuple[LocalProvider, ...]
    consumer_generation: int | None
    audit: dict


class ModelRouter:
    def __init__(self, configuration: ConfigurationService, profiles: tuple[ModelTaskProfile, ...],
                 providers: tuple[LocalProvider, ...], *, default_provider_resolver=None):
        self.configuration = configuration
        self.default_provider_resolver = default_provider_resolver
        self.profiles = MappingProxyType({p.kind: p for p in profiles})
        self.providers = MappingProxyType({p.binding.provider: p for p in providers})
        if not profiles or len(self.profiles) != len(profiles) or len(self.providers) != len(providers):
            raise ValueError("model profiles and provider IDs must be unique")
        for field in configuration_fields(profiles):
            if configuration.registry.fields.get(field.field_id) != field:
                raise ValueError("model fields must use the shared configuration registry")
        self.generation: int | None = None
        for profile in profiles:
            if profile.default_fallback and profile.default_fallback not in self.providers:
                raise ValueError("model task fallback must select a loaded provider")
            validate_targets(checked_rules(profile.replacement_rules), self.providers,
                             capabilities=profile.capabilities)

    def provider_default(self, context, profile, snapshot=None):
        """Read the host's current conversation route before sealing a model plan."""
        if self.default_provider_resolver is None:
            return None
        if snapshot is not None:
            primary = snapshot.fields[profile.group + ".provider"]
            fallback = snapshot.fields[profile.group + ".fallback"]
            if primary.source is not None and (fallback.source is not None or profile.default_fallback):
                return None
        provider_id = self.default_provider_resolver(context, profile)
        if provider_id is not None and provider_id not in self.providers:
            raise TaskError("model_provider_unavailable", "the current host conversation model has no local adapter")
        return provider_id

    @staticmethod
    def apply_provider_default(profile, snapshot, provider_id):
        if provider_id is None:
            return snapshot
        fields = dict(snapshot.fields)
        for suffix in ("provider", "fallback"):
            key = profile.group + "." + suffix
            if fields[key].source is None and (suffix == "provider" or not profile.default_fallback):
                fields[key] = replace(fields[key], value=provider_id)
        # This projection only supplies routing values. Adoption must retain
        # the original saved snapshot; the host selection belongs in the audit.
        return replace(snapshot, fields=fields)

    def start(self, *, expected_generation: int) -> int:
        self.generation = self.configuration.start_consumer(CONSUMER, expected_generation=expected_generation)
        return self.generation

    @staticmethod
    def _output_ceiling(profile, snapshot, values, providers):
        """Return the effective output ceiling and the basis it was chosen on.

        A value the user saved at any configuration layer is the user's
        contract: it is returned untouched here and refused later when it does
        not fit a provider the request can reach. Only a registry default (no
        configuration row at all) is a product-level budget, so only a default
        is clamped down to the strictest reachable provider.
        """
        declared = values["max_output_tokens"]
        if snapshot.fields[profile.group + ".max_output_tokens"].source is not None:
            return declared, "explicit"
        capacity = min(output_capabilities(values, providers), default=MAX_MODEL_OUTPUT_TOKENS)
        ceiling = min(declared, MAX_MODEL_OUTPUT_TOKENS, capacity)
        return ceiling, "registry_default_clamped" if ceiling < declared else "registry_default"

    def _validate_route_targets(self, profile, values, *, max_output_tokens=None):
        try:
            validate_targets(checked_rules(values["replacement_rules"]), self.providers,
                             capabilities=profile.capabilities, max_output_tokens=max_output_tokens)
        except ValueError as error:
            raise TaskError("model_route_invalid", str(error)) from error

    def _route_selection(self, profile, values, context, request, at):
        rules = checked_rules(values["replacement_rules"])
        texts = [material.payload for material in request.context.materials
                 if material.source is not None and material.source.scope == context.scope
                 and (material.purpose == "current_source" and material.source == request.event.source
                      or material.purpose.startswith("merged_input:"))] if hasattr(request.event, "source") else []
        in_scope = (values["replacement_scope"] == "all" or
                    values["replacement_scope"] == "conversation" and profile.kind == "conversation.reply" or
                    values["replacement_scope"] == "plugin" and profile.kind != "conversation.reply")
        match = match_route(rules, "\n".join(texts), at) if in_scope else None
        primary = match.provider_id if match else values["provider"]
        provider_ids = (primary,) + ((values["fallback"],)
            if values["fallback"] and values["fallback"] != primary else ())
        selected = tuple(self.providers.get(provider_id) for provider_id in provider_ids)
        if any(provider is None for provider in selected):
            raise TaskError("model_provider_unavailable", "configured provider has no local adapter")
        return match, selected, in_scope

    def selected_route(self, context, kind, request, *, at):
        """Resolve the configured primary model and provider chain without dispatch."""
        profile = self.profiles.get(kind)
        if profile is None:
            raise TaskError("model_task_unregistered", "model task type has no registered profile")
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                              context.role_binding.role, context.scope)
        snapshot = require(self.configuration.read(context.actor, target=target,
            group_id=profile.group, observed_at=at))
        snapshot = self.apply_provider_default(profile, snapshot, self.provider_default(context, profile, snapshot))
        values = {name.rsplit(".", 1)[1]: item.value for name, item in snapshot.fields.items()}
        self._validate_route_targets(profile, values)
        match, providers, _ = self._route_selection(profile, values, context, request, at)
        ceiling, basis = self._output_ceiling(profile, snapshot, values, self.providers)
        if basis == "explicit":
            self._validate_route_targets(profile, values, max_output_tokens=ceiling)
        if any(not set(profile.capabilities).issubset(provider.capabilities)
               or ceiling > provider.maximum_output_tokens for provider in providers):
            raise TaskError("model_capability_mismatch", "selected provider cannot fulfill this task profile")
        primary = providers[0]
        return (primary.binding.provider, match.model if match and match.model else primary.model,
                providers)

    def resolve(self, run: TaskRun, request: PromptRequest, *, provider_id=None) -> ResolvedModelPlan:
        task = run.validate()
        context = task.spec.context
        profile = self.profiles.get(task.spec.kind)
        if profile is None or run.supervisor.registrations[task.spec.kind].definition.lane != profile.lane:
            raise TaskError("model_task_unregistered", "model task type and lane must be registered")
        if (request.role_binding != context.role_binding or request.event.scope != context.scope
                or request.event.principal != context.actor.principal):
            raise TaskError("model_context_mismatch", "model request must retain its original identity and role")
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                              context.role_binding.role, context.scope)
        snapshot = require(self.configuration.read(context.actor, target=target,
                           group_id=profile.group, observed_at=run.supervisor.now()))
        provider_default = self.provider_default(context, profile, snapshot) if provider_id is None else None
        routed_snapshot = self.apply_provider_default(profile, snapshot, provider_default)
        values = {name.rsplit(".", 1)[1]: item.value for name, item in routed_snapshot.fields.items()}
        # The saved value, before ``values`` is overwritten with the ceiling this
        # route adopted: a refusal has to name both sides of the comparison, and
        # only this one is what the operator can edit.
        saved_max_output_tokens = values["max_output_tokens"]
        fallback_field = routed_snapshot.fields[profile.group + ".fallback"]
        if provider_id is None and values["fallback"] and values["fallback"] == values["provider"] and fallback_field.explicit:
            raise TaskError("model_fallback_duplicate", "fallback must select a different provider")
        evaluated_at = run.supervisor.now()
        try:
            if provider_id is None:
                self._validate_route_targets(profile, values)
                match, selected, in_scope = self._route_selection(profile, values, context, request, evaluated_at)
                ceiling, basis = self._output_ceiling(profile, routed_snapshot, values, self.providers)
                if basis == "explicit":
                    self._validate_route_targets(profile, values, max_output_tokens=ceiling)
            else:
                if provider_id not in self.providers:
                    raise TaskError("model_provider_unavailable", "the selected review provider has no local adapter")
                match, selected, in_scope = None, (self.providers[provider_id],), False
                ceiling, basis = self._output_ceiling(profile, routed_snapshot, values, self.providers)
        except TaskError:
            # TaskError is itself a ValueError, but its stable code is the
            # product diagnostic we need to preserve (for example, an
            # unregistered provider). Only malformed route definitions should
            # be normalized to the generic route-invalid code below.
            raise
        except ValueError as error:
            raise TaskError("model_route_invalid", str(error)) from error
        values["max_output_tokens"] = ceiling
        for provider in selected:
            if not set(profile.capabilities).issubset(provider.capabilities):
                raise TaskError("model_capability_mismatch", "provider lacks required model task capabilities")
            if request.native_video_input and "video" not in provider.capabilities:
                raise TaskError("model_capability_mismatch", "original video bytes require a native video provider")
            if (request.images or request.videos and not request.native_video_input) and "image" not in provider.capabilities:
                raise TaskError("model_capability_mismatch", "the original images require an image-capable provider")
            if request.audios and "audio" not in provider.capabilities:
                raise TaskError("model_capability_mismatch", "the original voice requires an audio-capable provider")
            if values["max_output_tokens"] > provider.maximum_output_tokens:
                # A ceiling the route cannot pay for is refused here, before any
                # request leaves the process: nothing about the prompt is wrong,
                # so the operator needs the three numbers that produced the
                # refusal — the saved value, the ceiling this route adopted and
                # the capability the provider declares — plus which basis was
                # used. Without them the code reads as "the prompt was too
                # long", which points the operator at the opposite fix.
                raise TaskError("model_output_limit", "configured output exceeds provider capability",
                                details={"stage": "preflight",
                                         "saved_max_output_tokens": saved_max_output_tokens,
                                         "adopted_max_output_tokens": ceiling,
                                         "adopted_basis": basis,
                                         "provider_id": provider.binding.provider,
                                         "provider_max_output_tokens": provider.maximum_output_tokens})
        audit = {
            "profile": {"kind": profile.kind, "version": profile.version, "lane": profile.lane.value},
            "configuration_token": snapshot.resolution_token,
            "fields": {key: {"source": item.source.document() if item.source else
                             "host_conversation" if provider_default is not None and
                             (key == profile.group + ".provider" or
                              key == profile.group + ".fallback" and not profile.default_fallback)
                             else "registry_default",
                             "explicit": item.explicit, "revision": item.source_revision}
                       for key, item in routed_snapshot.fields.items()},
            "provider_default": provider_default or profile.default_provider,
            "provider_override": provider_id,
            "providers": [asdict(p.binding) for p in selected],
            "role_revision": context.role_binding.revision,
            "role_prompt_hash": digest(request.role_instruction),
            "task_prompt_hash": digest(values["prompt"]),
            "request_hash": digest(request),
            "max_output_tokens": values["max_output_tokens"],
            "max_output_tokens_basis": basis,
            "timeout_seconds": values["timeout_seconds"],
            "replacement_scope": values["replacement_scope"],
            "transport": selected[0].transport,
            "provider_transports": {p.binding.provider: p.transport for p in selected},
            "consumer_generation": self.generation,
            "fallback_on_refusal": values["fallback_on_refusal"] if in_scope else False,
            "replacement": None if match is None else {
                "rule_id": match.rule_id, "reason": match.reason, "provider_id": match.provider_id,
                "model": match.model, "matched_keyword_hash": digest(match.matched_keyword) if match.matched_keyword else None,
                "evaluated_at": evaluated_at.isoformat(), "input_basis": "sealed_current_inputs"},
        }
        return ResolvedModelPlan(ModelEnvelope(request, values["prompt"], values["max_output_tokens"],
                                              snapshot.resolution_token, model_override=match.model if match else "",
                                              refusal_terms=parse_refusal_terms(values["refusal_terms"])),
                                 snapshot, tuple(selected), self.generation, audit)

    def validate_plan(self, plan: ResolvedModelPlan):
        consumer = self.configuration.store.read_one(
            "SELECT generation FROM configuration_consumers WHERE consumer_id = ?", (CONSUMER,))
        if (consumer is None or plan.consumer_generation is None or self.generation != plan.consumer_generation
                or consumer["generation"] != plan.consumer_generation):
            raise TaskError("consumer_generation_conflict", "model plan belongs to a replaced consumer")

    def adopt(self, run: TaskRun, plan: ResolvedModelPlan):
        require(self.configuration.report_application(run.task.spec.context.actor, snapshot=plan.snapshot,
                generation=self.generation, succeeded=True, observed_at=run.supervisor.now()))
