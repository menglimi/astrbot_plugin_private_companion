"""Strict, secret-free configuration for an explicitly administered test host.

Parsing is pure and provides no authority. Only a trusted local control-plane
caller may use this document to provision a fresh staging installation.
"""

from __future__ import annotations

import dataclasses
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import re

from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.conversation.forwards.models import ForwardIntakePolicy
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.messages import REPLY_KIND
from companion_v2.execution.models import Lane, SupervisorLimits, digest
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef, normalize_utc
# Re-exported for the callers that only need the staging vocabulary; the literal
# lives in ``identity_policy.grants`` so the classifier cannot drift from it.
from companion_v2.identity_policy.grants import DELIVERY_GRANT_ID
from companion_v2.identity_policy.models import _opaque
from companion_v2.identity_policy.object_access import DEFAULT_ACTIONS
from companion_v2.model_gateway.budget import BudgetSoftPolicy
from companion_v2.model_gateway.models import Amount, BudgetPolicy, ModelTaskProfile

from .native import HostAccount, HostPersona, route_id
from .provider import UsageTariff


CONFIG_VERSION = "companion-v2.astrbot-staging.v1"
MAX_CONFIG_BYTES = 131072
REQUIRED_CAPABILITIES = frozenset({"text", "conversation.reply", "configuration.read"})
ALLOWED_CAPABILITIES = frozenset({"text", *(a.action for a in DEFAULT_ACTIONS if not a.core_only)})
DELIVERY_CAPABILITY = "message.delivery"
# Policy fields added after the v1 fingerprint document. A pinned profile
# reopens only while its stored bootstrap hash matches; a field the author
# never expressed (still at its schema default) must not change that hash,
# so late fields are omitted from the document until configured.
SEGMENTATION_LATE_FIELDS = frozenset({"split_words", "match_width_variants", "rule_regex"})
# Same rule for the conversation timing block: the busy-hour/rest deferral was added
# to ``ConversationPolicy`` after the v1 document, so a profile pinned before it must
# still read (as "no deferral") instead of failing the whole configuration.
POLICY_LATE_FIELDS = frozenset({"defer_seconds"})
# Blocks whose emitted shape has changed since the v1 document. A profile pinned by an
# older writer stores the older shape, and the fingerprint has to reproduce exactly that
# shape — carrying today's normalized values — or reopening it fails against the receipt
# recorded at initialization. Add a block here when its emission rules change.
SHAPE_SENSITIVE_BLOCKS = ("budget", "budget_soft", "segmentation", "policy")

# The fields whose change means "this profile now points somewhere else". Only these
# refuse to load; every other difference is environment drift and loads with a warning.
#
# Why the split exists: pinning is about not silently *rebinding* a role -- a stray
# command or a config edit must not quietly repoint who the role is, who it talks to, or
# which account it speaks through. A provider that expired upstream, a re-tuned budget, a
# limit, or a field AstrBot added in an upgrade rebinds nothing, yet under a single
# all-fields rule each of those took the whole installation offline, and the operator saw
# only "connection failed".
#
# ``scope`` carries platform/account/bot/participant/conversation; ``subject`` is the
# person; ``persona`` is the Bot/persona/role identity. Its instruction hash is content,
# not identity: editing the same AstrBot persona must not require rebinding the chat.
# Delivery is fixed by the official host and is not a user-selectable binding dimension.
# Model/provider parameters remain environment drift and do not block reconnecting the
# same binding.
REBINDING_FIELDS = frozenset({"scope", "subject", "persona"})

# The keys of ``scope`` that identify *where* this profile points, as opposed to which
# conversation the platform currently has open.
#
# ``conversation_id`` and ``session_id`` are deliberately excluded. They are read live
# from AstrBot on every probe (``astrbot_host/native.py`` asks
# ``get_curr_conversation_id`` for the target's ``umo``), so they are conversation-scoped
# runtime values, not provisioning conditions. Hashing them into the identity meant that
# the platform opening a different conversation -- an ordinary thing a person does, and
# something V1 never reacted to -- made the whole role refuse to load, and the operator
# saw only "connection failed".
#
# They stay in the stored document and are still crossed against the live probe when a
# message lands, so talking to the wrong conversation is still refused. What they no
# longer have is the power to take the entire installation offline.
SCOPE_IDENTITY_KEYS = ("installation_id", "platform", "kind", "native_scope_id",
                       "bot_id", "account_id")


class StagingConfigurationError(ValueError):
    """A bounded error that never echoes configuration values or native secrets."""


def _fields(data, names, location, *, optional=()):
    names, optional = set(names.split()), set(optional)
    if not isinstance(data, dict) or not names <= set(data) or set(data) - names - optional:
        raise StagingConfigurationError(location + ": missing, unknown or inapplicable fields")
    return data


def _hash(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise StagingConfigurationError(field + ": expected a SHA-256 fingerprint")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StagingConfigurationError("configuration contains duplicate fields")
        result[key] = value
    return result


def _nonfinite(_):
    raise StagingConfigurationError("configuration numbers must be finite")


@dataclass(frozen=True)
class StagingConfiguration:
    scope: ScopeRef
    host_id: str
    ecosystem_id: str
    subject: SubjectRef
    persona: HostPersona
    authority_ref: str
    valid_from: datetime
    expires_at: datetime | None
    capabilities: tuple[str, ...]
    provider_id: str
    provider_fingerprint: str
    tariff: UsageTariff
    budget: BudgetPolicy
    task_prompt: str
    budget_soft: BudgetSoftPolicy = BudgetSoftPolicy()
    delivery_mode: str = "onebot_private"
    max_output_tokens: int = 1024
    maximum_input_estimate: int = 131072
    output_parameter: str = "max_tokens"
    policy: ConversationPolicy = ConversationPolicy()
    limits: SupervisorLimits = SupervisorLimits()
    streaming: bool = False
    segmentation: SegmentationPolicy = SegmentationPolicy()
    forward_intake: ForwardIntakePolicy = ForwardIntakePolicy()
    # A storage artefact rather than a contract value, so it stays out of equality:
    # ``same_contract`` compares settings, ``fingerprint`` identifies the pinned shape.
    _pinned_document: dict | None = field(default=None, compare=False, repr=False)

    def same_contract(self, other: "StagingConfiguration") -> bool:
        """Whether both records describe the same pinned settings.

        Compared by value, never by hash: the *shape* of a stored document depends
        on the writer that pinned it (late fields omitted, blocks present or not),
        while the contract is the values those shapes carry. A fresh probe and a
        profile pinned last month therefore still match when nothing was changed.

        Scoped exactly like ``contract_differences``, so a conversation the platform
        opened since the pin reads as "same contract" rather than as drift with no
        nameable field.
        """
        return not self.contract_differences(other)

    def contract_differences(self, other: "StagingConfiguration") -> tuple[str, ...]:
        """Which fields differ, by name only.

        A pinned profile that no longer matches refuses to load, and the operator is
        then told only that *something* changed. That is not actionable: they cannot
        tell whether the target, the persona, the provider or a limit moved. Field
        names are reported and values never are — this document carries an
        installation id, a persona hash and a provider fingerprint, and an error
        surface is not the place for them.
        """
        def differs(name):
            if name == "scope":
                # Same key set as ``identity_document``: a conversation the platform
                # opened since the pin is not a settings difference and must not even be
                # reported as environment drift, or every new conversation would warn.
                mine, theirs = self.scope.document(), other.scope.document()
                return any(mine.get(key) != theirs.get(key) for key in SCOPE_IDENTITY_KEYS)
            return getattr(self, name) != getattr(other, name)

        return tuple(field.name for field in dataclasses.fields(self)
                     if not field.name.startswith("_") and differs(field.name))

    def rebinding_differences(self, other: "StagingConfiguration") -> tuple[str, ...]:
        """The differing fields that mean the profile is being pointed somewhere else.

        Only these refuse to load. A provider renamed or expired upstream, a budget or
        a limit re-tuned, or a field AstrBot added in an upgrade are environment drift:
        the role is still the same role, talking to the same person, on the same
        account. Treating those as a conflict took an entire installation offline for a
        change nobody made in this profile, which is the opposite of what pinning is
        for — pinning is about not silently *rebinding*.
        """
        differences = self.contract_differences(other)
        current_persona = (self.persona.bot_id, self.persona.persona_id, self.persona.role.subject_id)
        pinned_persona = (other.persona.bot_id, other.persona.persona_id, other.persona.role.subject_id)
        return tuple(name for name in differences if name in REBINDING_FIELDS
                     and (name != "persona" or current_persona != pinned_persona))

    def identity_document(self) -> dict:
        """The part of the record that identifies *which* role this profile is.

        A store records this digest in its provisioning receipt, so a reopen can tell
        "the profile now points somewhere else" from "only the environment moved"
        without ever recomputing the whole document. Delivery is not part of identity.

        The shape is deliberately *not* ``document()``'s: the writer that pinned a
        profile may omit ``delivery_mode`` or store its old ``preview`` value, and blocks
        added later may be absent. Reproducing that shape keeps old receipts readable.
        """
        return {"scope": {key: value for key, value in self.scope.document().items()
                          if key in SCOPE_IDENTITY_KEYS},
                "subject_id": self.subject.subject_id,
                "persona": {"persona_id": self.persona.persona_id, "role_id": self.persona.role.subject_id}}

    @property
    def identity_fingerprint(self) -> str:
        """Digest of ``identity_document``: moves for a rebinding only."""
        return digest(self.identity_document())

    def field_fingerprints(self) -> dict[str, str]:
        """One digest per contract field, so drift can be *named* without values.

        The whole-document fingerprint answers "did anything move" but never "what
        moved", and the pinned document is never stored next to the receipt, so a
        mismatch there would otherwise be unactionable. Field names are safe to report;
        the values are not — this document carries an installation id, a persona hash
        and a provider fingerprint.

        Digests come from the normalized *values*, not from the document's blocks, for
        the same reason ``identity_document`` does: a writer upgrade changing which keys
        a block spells out is not a settings change.
        """
        return {item.name: digest(getattr(self, item.name))
                for item in dataclasses.fields(self) if not item.name.startswith("_")}

    def __post_init__(self):
        scope = self.scope
        if (scope.kind is not ScopeKind.PRIVATE or not scope.is_resolved
                or scope.session_id != scope.native_scope_id
                or self.subject.kind is not SubjectKind.HUMAN or self.subject.installation_id != scope.installation_id
                or self.persona.bot_id != scope.bot_id or self.persona.role.installation_id != scope.installation_id):
            raise StagingConfigurationError("configuration requires one complete private target and matching subjects")
        HostAccount(scope.platform, scope.account_id, scope.bot_id)
        route_id(scope.native_scope_id)
        for name in ("host_id", "ecosystem_id", "authority_ref", "provider_id"):
            _opaque(getattr(self, name), field=name)
        _hash(self.persona.instruction_hash, "persona.instruction_hash")
        _hash(self.provider_fingerprint, "model.provider_fingerprint")
        object.__setattr__(self, "valid_from", normalize_utc(self.valid_from, field="valid_from"))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", normalize_utc(self.expires_at, field="expires_at"))
            if self.expires_at <= self.valid_from:
                raise StagingConfigurationError("access expiration must follow valid_from")
        if (not isinstance(self.capabilities, tuple) or any(type(c) is not str for c in self.capabilities)
                or len(set(self.capabilities)) != len(self.capabilities)
                or not REQUIRED_CAPABILITIES.issubset(self.capabilities)
                or not set(self.capabilities).issubset(ALLOWED_CAPABILITIES)):
            raise StagingConfigurationError("access requires unique registered non-core actions and the reply prerequisites")
        object.__setattr__(self, "capabilities", tuple(sorted(self.capabilities)))
        if self.delivery_mode == "preview":
            object.__setattr__(self, "delivery_mode", "onebot_private")
        elif self.delivery_mode != "onebot_private":
            raise StagingConfigurationError("delivery_mode is fixed to onebot_private")
        if (type(self.maximum_input_estimate) is not int or not 1024 <= self.maximum_input_estimate <= 1000000
                or self.output_parameter not in ("max_tokens", "max_completion_tokens")):
            raise StagingConfigurationError("invalid model input/output profile")
        if not isinstance(self.task_prompt, str) or len(self.task_prompt) > 65536:
            raise StagingConfigurationError("task prompt must be bounded text")
        if type(self.streaming) is not bool or not isinstance(self.segmentation, SegmentationPolicy):
            raise StagingConfigurationError("invalid streaming or segmentation policy")
        if not isinstance(self.forward_intake, ForwardIntakePolicy):
            raise StagingConfigurationError("invalid forward intake policy")
        ModelTaskProfile(REPLY_KIND, Lane.FOREGROUND, self.provider_id, self.task_prompt,
                         max_output_tokens=self.max_output_tokens)
        for value, expected in ((self.tariff, UsageTariff), (self.budget, BudgetPolicy),
                                (self.policy, ConversationPolicy), (self.limits, SupervisorLimits)):
            if not isinstance(value, expected):
                raise StagingConfigurationError("configuration requires validated policy records")

    def document(self) -> dict:
        return {"schema_version": CONFIG_VERSION, "scope": self.scope.document(),
            "host": {"host_id": self.host_id, "ecosystem_id": self.ecosystem_id},
            "subject_id": self.subject.subject_id,
            "persona": {"persona_id": self.persona.persona_id, "role_id": self.persona.role.subject_id,
                        "instruction_hash": self.persona.instruction_hash},
            "access": {"authority_ref": self.authority_ref, "valid_from": self.valid_from.isoformat(),
                "expires_at": self.expires_at.isoformat() if self.expires_at else None, "capabilities": list(self.capabilities)},
            "model": {"provider_id": self.provider_id, "provider_fingerprint": self.provider_fingerprint,
                "tariff": self.tariff.document(), "task_prompt": self.task_prompt, "max_output_tokens": self.max_output_tokens,
                "maximum_input_estimate": self.maximum_input_estimate, "output_parameter": self.output_parameter,
                **({"streaming": True} if self.streaming else {})},
            "budget": self.budget.document(),
            "budget_soft": {"enabled": self.budget_soft.enabled, "tokens": self.budget_soft.tokens,
                            "kinds": list(self.budget_soft.kinds)},
            "policy": self._policy_document(), "limits": asdict(self.limits),
            **self._delivery_document(),
            **self._segmentation_document(),
            **self._forward_intake_document()}

    def _delivery_document(self) -> dict:
        return {"delivery_mode": "onebot_private"}

    def _forward_intake_document(self) -> dict:
        # Same late-field rule as segmentation: an operator who kept the defaults
        # preserves the original hash; explicit bounds or nested parsing decisions
        # enter the profile fingerprint so the active bridge applies them.
        values = asdict(self.forward_intake)
        defaults = asdict(ForwardIntakePolicy())
        serialized = {name: value for name, value in values.items() if value != defaults[name]}
        return {"forward_intake": serialized} if serialized else {}

    def _policy_document(self) -> dict:
        # Timing is always expressed, but the late deferral is not: a profile pinned
        # before it existed must keep its original hash, while an operator who really
        # set a deferral still pins that decision.
        values = asdict(self.policy)
        defaults = asdict(ConversationPolicy())
        for name in POLICY_LATE_FIELDS:
            if values[name] == defaults[name]:
                values.pop(name)
        return values

    def _segmentation_document(self) -> dict:
        # Only expressed decisions belong to the fingerprint document, including the
        # late fields: a profile that never changed segmentation must not change hash.
        values = asdict(self.segmentation)
        defaults = asdict(SegmentationPolicy())
        serialized = {name: value for name, value in values.items() if value != defaults[name]}
        return {"segmentation": serialized} if serialized else {}

    @property
    def fingerprint(self) -> str:
        return digest(self._fingerprint_document())

    def _fingerprint_document(self) -> dict:
        """The document this record is identified by.

        Values are always the normalized ones, but the *shape* is whatever was pinned:
        a profile written before a block existed has no key for it, and one written
        before a field was added spells out the older key set. Reproducing that shape is
        what keeps the receipt from ``initialize`` valid after the writer evolves;
        a record built fresh simply hashes the document it would write.
        """
        document = self.document()
        stored = self._pinned_document
        if stored is None:
            return document
        if "delivery_mode" in stored:
            document["delivery_mode"] = stored["delivery_mode"]
        else:
            document.pop("delivery_mode", None)
        for name in SHAPE_SENSITIVE_BLOCKS:
            stored_block = stored.get(name)
            if not isinstance(stored_block, dict):
                # The pinning writer had no such block at all.
                document.pop(name, None)
                continue
            current = document.get(name)
            current = current if isinstance(current, dict) else {}
            if name == "budget":
                document[name] = {
                    scope: (current.get(scope, True) if scope == "enabled" else
                            {key: current.get(scope, {}).get(key) for key in amount})
                    for scope, amount in stored_block.items()
                }
                continue
            defaults = asdict(type(getattr(self, name))())
            document[name] = {key: current.get(key, defaults.get(key)) for key in stored_block}
        return document

    @classmethod
    def parse(cls, text: str) -> StagingConfiguration:
        try:
            if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
                raise StagingConfigurationError("configuration exceeds the UTF-8 document limit")
            data = json.loads(text, object_pairs_hook=_unique, parse_constant=_nonfinite)
            # Escaped lone surrogates are accepted by json.loads but cannot be
            # stored or displayed as UTF-8; reject them at the document boundary.
            json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            # The stored document *is* the pinned contract's identity: keep its exact
            # shape, because a profile pinned by an older writer may express schema
            # defaults (or omit blocks added later) that today's writer would not emit.
            # Hashing the rebuilt view instead is how a pinned profile silently stops
            # matching the receipt recorded at initialization.
            pinned_document = json.loads(text, object_pairs_hook=_unique, parse_constant=_nonfinite)
            segmentation = data.pop("segmentation", {}) if isinstance(data, dict) else {}
            forward_intake = data.pop("forward_intake", {}) if isinstance(data, dict) else {}
            # Older documents may carry either legacy delivery value. The runtime
            # normalizes both to the official sender; the exact stored value remains in
            # ``pinned_document`` so its original fingerprint can still be reproduced.
            has_pinned_delivery = isinstance(data, dict) and "delivery_mode" in data
            pinned_delivery = data.pop("delivery_mode", None) if isinstance(data, dict) else None
            if has_pinned_delivery and pinned_delivery not in ("preview", "onebot_private"):
                raise StagingConfigurationError("delivery_mode is not a supported legacy value")
            _fields(data, "schema_version scope host subject_id persona access model budget policy limits", "root",
                    optional=("budget_soft",))
            if data["schema_version"] != CONFIG_VERSION:
                raise StagingConfigurationError("unsupported staging configuration version")
            scope = ScopeRef.restore(_fields(data["scope"],
                "installation_id platform kind native_scope_id bot_id account_id conversation_id session_id", "scope"))
            host = _fields(data["host"], "host_id ecosystem_id", "host")
            persona = _fields(data["persona"], "persona_id role_id instruction_hash", "persona")
            access = _fields(data["access"], "authority_ref valid_from expires_at capabilities", "access")
            if not isinstance(access["capabilities"], list):
                raise StagingConfigurationError("access.capabilities must be an array")
            streaming = data["model"].pop("streaming", False) if isinstance(data["model"], dict) else False
            model = _fields(data["model"],
                "provider_id provider_fingerprint tariff task_prompt max_output_tokens maximum_input_estimate output_parameter", "model")
            tariff = UsageTariff(**_fields(model["tariff"], "input cached_input output", "model.tariff",
                                           optional=("cache_creation", "configured")))
            budget = _fields(data["budget"], "installation bot role window background", "budget", optional=("enabled",))
            budget_enabled = budget.pop("enabled", True)
            amounts = {name: Amount(**_fields(
                value, "tokens cost_microusd", "budget.amount",
                optional=("input_tokens", "cached_input_tokens", "cache_creation_tokens", "output_tokens"),
            )) for name, value in budget.items()}
            # Added after the v1 fingerprint document: a profile pinned by an older host
            # block simply keeps the soft ceiling disabled.
            soft = (_fields(data["budget_soft"], "enabled tokens kinds", "budget_soft")
                    if "budget_soft" in data else
                    {"enabled": False, "tokens": 0, "kinds": []})
            if (type(soft["enabled"]) is not bool or type(soft["tokens"]) is not int
                    or type(soft["kinds"]) is not list or any(type(kind) is not str for kind in soft["kinds"])):
                raise StagingConfigurationError("budget_soft must declare a switch, a token ceiling and a kind list")
            # The deferral is optional on the wire: a profile pinned before the field
            # existed omits it, and the dataclass default reads that as "no deferral".
            policy = _fields(data["policy"],
                "quiet_seconds maximum_wait_seconds turn_timeout_seconds part_gap_seconds", "policy",
                optional=POLICY_LATE_FIELDS)
            configuration = cls(scope=scope, **host, subject=SubjectRef(scope.installation_id, SubjectKind.HUMAN, data["subject_id"]),
                persona=HostPersona(scope.bot_id, persona["persona_id"],
                    SubjectRef(scope.installation_id, SubjectKind.BOT, persona["role_id"]), persona["instruction_hash"]),
                authority_ref=access["authority_ref"], valid_from=datetime.fromisoformat(access["valid_from"]),
                expires_at=datetime.fromisoformat(access["expires_at"]) if access["expires_at"] is not None else None,
                capabilities=tuple(access["capabilities"]), tariff=tariff, budget=BudgetPolicy(**amounts, enabled=budget_enabled),
                budget_soft=BudgetSoftPolicy(enabled=soft["enabled"], tokens=soft["tokens"], kinds=tuple(soft["kinds"])),
                streaming=streaming, segmentation=SegmentationPolicy(**segmentation),
                forward_intake=ForwardIntakePolicy(**forward_intake),
                policy=ConversationPolicy(**policy),
                limits=SupervisorLimits(**_fields(data["limits"],
                    "foreground_slots background_slots queued_per_lane retained_tasks", "limits")),
                delivery_mode="onebot_private",
                **{k: v for k, v in model.items() if k != "tariff"})
            # Frozen dataclass: attach the stored shape through the documented escape
            # hatch, since ``fingerprint`` hashes it instead of the rebuilt view.
            object.__setattr__(configuration, "_pinned_document", pinned_document)
            return configuration
        except StagingConfigurationError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            raise StagingConfigurationError("invalid staging configuration types, references or values") from None
