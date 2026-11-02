"""First-install authority and read-only reopen for a dedicated staging store.

The bootstrap methods are trusted local administration ports, not chat tools or
HTTP endpoints. A parsed file is not evidence that its author may manage a host.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging

from companion_v2.agenda.service import AgendaService
from companion_v2.capabilities.models import (
    CapabilityAuthorizationState, CapabilityDesiredState, CapabilityPackageDescriptor,
)
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.capabilities.screen_access import PACKAGE_ID as SCREEN_PACKAGE_ID
from companion_v2.execution.models import ProviderBinding, TaskContext, TaskError, encode, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef, OperationRef, VersionRef, normalize_utc
from companion_v2.identity_policy.grants import (
    DELIVERY_GRANT_ID,
    STAGING_CAPABILITY_GRANT_PREFIX,
    STAGING_SCREEN_GRANT_PREFIX,
)
from companion_v2.identity_policy.models import (
    AuthenticatedHostPrincipal,
    AuthorizationTier,
    GrantState,
    RoleBinding,
)
from companion_v2.identity_policy.personas import ConversationRoute, PersonaBinding, PersonaDirectory, RuntimeIdentity, SelectorKind
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.model_gateway.models import MAX_MODEL_OUTPUT_TOKENS
from companion_v2.relationships.private_access import REVOCABLE_ABSENCE_CODES, REVOCABLE_CAPABILITIES
from companion_v2.screen import SCREEN_EXTENSION, SCREEN_PROCESS, SCREEN_READ
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository
from companion_v2.storage.runtime import StorageRuntime
from companion_v2.storage.schema import REQUIRED_TABLES

from .native import HostAccount, NativePrivateBridge
from .provider import AstrBotTextProvider, provider_fingerprint
from .staging import AstrBotStagingSession, NativeAdapterTypes, StagingProfile
from .staging_config import (
    DELIVERY_CAPABILITY,
    REBINDING_FIELDS,
    StagingConfiguration,
)
from .staging_delivery import delivery_grant_id, delivery_grants, require_delivery_access


logger = logging.getLogger(__name__)

# The second receipt format: a canonical JSON record carrying the identity sub-hash
# alongside the whole-document hash and one digest per contract field. A store
# provisioned before it stores only the whole-document hash, which cannot say whether a
# mismatch moved the identity or the environment, so those receipts take the
# ``_legacy_identity_differences`` path instead.
RECEIPT_VERSION = "companion-v2.staging-receipt.v2"
RECEIPT_KEYS = frozenset({"version", "identity", "document", "fields"})
# Bounded, value-free reasons: field *names* travel, values never do.
PROVISIONED_DIFFERENTLY = "this store was provisioned for a different target or persona: "


@dataclass(frozen=True)
class BootstrapReceipt:
    operation_id: str
    configuration_fingerprint: str
    identity_binding_id: str
    persona_binding_id: str
    replayed: bool


@dataclass(frozen=True)
class DeliveryAuthorizationReceipt:
    grant_id: str
    capability: str
    expires_at: datetime | None
    state: str
    replayed: bool


@dataclass(frozen=True)
class ScreenPreparationReceipt:
    package_version: str
    artifact_hash: str
    scope: str
    replayed: bool


@dataclass(frozen=True)
class ScreenUpgradeReceipt:
    previous_version: str
    package_version: str
    artifact_hash: str
    revision: int
    scope: str


def _receipt_record(configuration: StagingConfiguration) -> str:
    """The provisioning receipt stored as this operation's parameter hash.

    Three digests and no values: the identity sub-hash answers whether the profile still
    points at the same role, the document hash keeps the byte-exact provenance the
    single-hash receipt had, and the per-field digests let a mismatch name the fields
    that moved.
    """
    return encode({"version": RECEIPT_VERSION, "identity": configuration.identity_fingerprint,
                   "document": configuration.fingerprint, "fields": configuration.field_fingerprints()})


def _recorded_receipt(value: str) -> tuple[str | None, str, dict | None]:
    """Read back a provisioning receipt as ``(identity, document, fields)``.

    A receipt written before this format is one bare 64-character digest, so it has no
    identity sub-hash: it comes back as ``(None, digest, None)`` and is judged by
    ``_legacy_identity_differences``. A value shaped like this format but not readable
    as one is not treated as legacy -- that would let a truncated or tampered receipt
    pass as "old", so it refuses instead.
    """
    if not value.startswith("{"):
        return None, value, None
    try:
        record = json.loads(value)
    except ValueError:
        record = None
    if (type(record) is not dict or set(record) != RECEIPT_KEYS or record.get("version") != RECEIPT_VERSION
            or type(record.get("identity")) is not str or type(record.get("document")) is not str
            or type(record.get("fields")) is not dict
            or any(type(name) is not str or type(item) is not str for name, item in record["fields"].items())):
        raise TaskError("staging_bootstrap_conflict", "the stored provisioning receipt is unreadable")
    return record["identity"], record["document"], record["fields"]


def _recorded_differences(recorded: dict, current: dict) -> tuple[str, ...]:
    """Names of the fields whose digest moved, sorted, never any value."""
    return tuple(sorted(name for name in set(recorded) | set(current)
                        if recorded.get(name) != current.get(name)))


class AstrBotStagingBootstrap:
    OPERATION_ID = "astrbot-staging-bootstrap"
    IDENTITY_ID = "astrbot-staging-participant"
    PERSONA_ID = "astrbot-staging-persona"
    SCREEN_GRANT_PREFIX = STAGING_SCREEN_GRANT_PREFIX
    SCREEN_ACTIONS = (SCREEN_READ, SCREEN_PROCESS, "extension.tool.invoke")

    def __init__(self, runtime: StorageRuntime, context, *, configuration: StagingConfiguration,
                 native_types: NativeAdapterTypes, native_tool_port=None, now=None):
        if runtime.installation_id != configuration.scope.installation_id:
            raise ValueError("staging bootstrap belongs to another installation")
        self.runtime, self.context = runtime, context
        self.configuration, self.native_types = configuration, native_types
        self.native_tool_port = native_tool_port
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.identity = PersistentIdentityDirectory(runtime)
        self.personas = PersonaDirectory(runtime)
        self.operations = OperationRepository(runtime)
        s = configuration.scope
        self.principal = AuthenticatedHostPrincipal(s.installation_id, configuration.host_id, s.platform,
            s.native_scope_id, s.bot_id, s.account_id)

    @staticmethod
    def grant_id(capability: str) -> str:
        if capability == DELIVERY_CAPABILITY:
            return DELIVERY_GRANT_ID
        return STAGING_CAPABILITY_GRANT_PREFIX + capability

    def _time(self) -> datetime:
        at = normalize_utc(self.now(), field="bootstrap time")
        config = self.configuration
        if at < config.valid_from or config.expires_at is not None and at >= config.expires_at:
            raise TaskError("staging_access_inactive", "configured test access is not currently valid")
        return at

    def _receipt(self, *, replayed):
        return BootstrapReceipt(self.OPERATION_ID, self.configuration.fingerprint,
                                self.IDENTITY_ID, self.PERSONA_ID, replayed)

    def _legacy_identity_differences(self) -> tuple[str, ...]:
        """Identity fields a first-format receipt can still be checked against.

        Such a receipt carries one whole-document digest, so it cannot say which field
        moved; the store's own records are the only oracle left. ``scope`` is compared
        by ``_marker`` from the receipt row's own scope columns, the participant comes
        from the identity binding written at provisioning, and the role from the persona
        binding. The persona name and instruction digest are not recorded in the store,
        but assembly checks them against the live host (``host_persona_unmapped`` /
        ``host_persona_changed``). Delivery mode is not a binding field: this host always
        uses its official OneBot sender, while per-person permissions remain in force.
        """
        config = self.configuration
        binding = next((item for item in self.identity.snapshot().bindings
                        if item.binding_id == self.IDENTITY_ID), None)
        persona = self.personas.binding(self.PERSONA_ID)
        found = []
        if binding is not None and binding.subject != config.subject:
            found.append("subject")
        if persona is not None and persona.role != config.persona.role:
            found.append("persona")
        return tuple(found)

    def _marker(self):
        result = self.operations.get(self.OPERATION_ID)
        if result.failure:
            if result.failure.code == "operation_not_found":
                return None
            require(result)
        receipt = result.unwrap()
        config = self.configuration
        if (receipt.operation.owner != "host.staging.bootstrap" or receipt.operation.scope != config.scope
                or receipt.state is not OperationState.COMPLETED):
            raise TaskError("staging_bootstrap_conflict", "this store was provisioned from a different configuration")
        identity, document, fields = _recorded_receipt(receipt.parameter_hash)
        if document == config.fingerprint:
            return receipt
        # The whole-document digest moved. That is either a rebinding or environment
        # drift, and only the receipt can tell them apart: refusing both took an entire
        # installation offline because a budget was re-tuned or AstrBot added a field,
        # while accepting both would let a stray edit quietly repoint the role.
        if identity is None:
            moved = self._legacy_identity_differences()
            if moved:
                raise TaskError("staging_bootstrap_conflict", PROVISIONED_DIFFERENTLY + ", ".join(moved))
            logger.warning(
                "[CompanionV2] 固定 profile 与建库收据（旧版，无逐字段摘要）存在差异，"
                "目标、参与人与角色一致，按当前值继续加载"
            )
            return receipt
        differences = _recorded_differences(fields, config.field_fingerprints())
        # Older structured receipts hashed the persona's instruction text together
        # with its identity. The controller separately verifies the live Bot/persona
        # selection, while the bootstrap's persistent binding verifies the V2 role.
        # A prompt edit therefore remains visible drift, not a rebinding.
        persona_identity_changed = "persona" in self._legacy_identity_differences()
        moved = ", ".join(name for name in differences if name in REBINDING_FIELDS
                           and (name != "persona" or persona_identity_changed))
        if moved:
            raise TaskError("staging_bootstrap_conflict", PROVISIONED_DIFFERENTLY + moved)
        logger.warning("[CompanionV2] 固定 profile 与建库收据存在环境差异，按当前值继续加载：%s",
                       ", ".join(name for name in differences
                                 if name not in REBINDING_FIELDS or name == "persona") or "unknown")
        return receipt

    def _fresh(self):
        # Names come only from the runtime's fixed schema, never from the document.
        exempt = {"runtime_metadata", "schema_migrations", "identity_policy_metadata", "object_policy_metadata"}
        for table in sorted(REQUIRED_TABLES - exempt):
            if self.runtime.read_one(f'SELECT 1 FROM "{table}" LIMIT 1', ()):
                raise TaskError("staging_bootstrap_store_in_use", "initialization requires a fresh dedicated V2 store")
        for table in ("identity_policy_metadata", "object_policy_metadata"):
            if self.runtime.read_one(f'SELECT revision FROM "{table}" WHERE singleton = 1', ())["revision"] != 1:
                raise TaskError("staging_bootstrap_store_in_use", "previous authority history cannot be reinitialized")

    def _target(self, at: datetime) -> TaskContext:
        config = self.configuration
        actor = require(self.identity.resolve_authenticated(self.principal, observed_at=at))
        if actor.subject != config.subject or actor.binding_id != self.IDENTITY_ID or actor.binding_revision != 1:
            raise TaskError("staging_binding_changed", "the original configured participant binding changed")
        route = self.personas.route(config.scope)
        if route != ConversationRoute(config.scope, user=config.subject):
            raise TaskError("staging_route_changed", "the original configured private participant changed")
        persona = require(self.personas.resolve(config.scope, observed_at=at))
        if (persona.selected.binding_id != self.PERSONA_ID or persona.selected.revision != 1
                or persona.binding.role != config.persona.role or persona.binding.revision != 1):
            raise TaskError("staging_persona_changed", "the original configured persona binding changed")
        # Reopen observes each original grant. Another grant cannot silently
        # substitute for a revoked bootstrap grant or reopen a wider scope.
        for capability in config.capabilities:
            access = self.identity.authorize(actor, scope=config.scope, capability=capability, observed_at=at)
            if capability in REVOCABLE_CAPABILITIES and access.failure is not None \
                    and access.failure.code in REVOCABLE_ABSENCE_CODES:
                # 对话/主动权限可以按人收回（见 relationships/private_access.py）。
                # 收回后仍然要能重开这一次会话：收信闸会把它变成"按配置回一句"。
                continue
            current = require(access)
            if capability in REVOCABLE_CAPABILITIES:
                continue
            if current.grant_id != self.grant_id(capability) or current.grant_revision != 1:
                raise TaskError("staging_grant_changed", "the configured original capability grant changed")
        return TaskContext(actor, persona.binding, AudienceRef(config.scope, (actor.subject,)))

    def _delivery_access(self, at: datetime):
        return None

    def require_delivery_authorization(self):
        with self.runtime.read_snapshot():
            return self._delivery_access(self._time())

    def delivery_authorization_status(self) -> dict:
        return {"required": False, "authorized": True, "state": "not_required",
                "grant_id": None, "capability": None, "expires_at": None}

    def authorize_delivery(self, *, expires_at: datetime) -> Outcome[DeliveryAuthorizationReceipt]:
        """Compatibility hook; the official host no longer issues delivery grants."""
        return Outcome.success(DeliveryAuthorizationReceipt(
            "not-required", DELIVERY_CAPABILITY, None, "not_required", True))

    def revoke_delivery(self) -> Outcome[DeliveryAuthorizationReceipt | None]:
        """Compatibility hook; no delivery grant controls the sender anymore."""
        return Outcome.success(DeliveryAuthorizationReceipt(
            "not-required", DELIVERY_CAPABILITY, None, "not_required", True))

    @staticmethod
    def _screen_package(package: CapabilityPackageDescriptor) -> None:
        if (not isinstance(package, CapabilityPackageDescriptor)
                or package.extension_id != SCREEN_EXTENSION
                or package.package_id != SCREEN_PACKAGE_ID
                or set(package.capabilities) != {SCREEN_READ, SCREEN_PROCESS}
                or set(package.dataset_owners) != {"screen"}):
            raise TaskError("staging_screen_package_invalid", "loaded Screen package has an unexpected identity")

    def prepare_screen(self, package: CapabilityPackageDescriptor) -> Outcome[ScreenPreparationReceipt]:
        """Authorize only the pinned private target for the loaded Screen package."""
        try:
            self._screen_package(package)
            with self.runtime.transaction():
                at = self._time()
                if self._marker() is None:
                    raise TaskError("staging_not_initialized", "initialize the staging profile first")
                target = self._target(at)
                config = self.configuration
                capabilities = CapabilityRepository(self.runtime)
                require(capabilities.register_descriptor(package, registered_at=at))
                installed = capabilities.installation(extension_id=SCREEN_EXTENSION)
                if installed.failure is not None:
                    if installed.failure.code != "capability_not_installed":
                        require(installed)
                    require(capabilities.acquire(extension_id=SCREEN_EXTENSION,
                        package_id=package.package_id, package_version=package.package_version,
                        expected_revision=0, observed_at=at))
                    changed = True
                else:
                    current = installed.unwrap()
                    if (current.package_id, current.package_version, current.artifact_hash,
                            current.desired_state) != (package.package_id, package.package_version,
                                                       package.artifact_hash, CapabilityDesiredState.ACTIVE):
                        raise TaskError("staging_screen_package_changed", "Screen installation differs from the loaded package")
                    changed = False

                projection = require(capabilities.projection(extension_id=SCREEN_EXTENSION,
                    role=config.persona.role, scope=config.scope))
                selection = projection.role_selection
                if selection is None:
                    require(capabilities.set_role_enabled(extension_id=SCREEN_EXTENSION,
                        role=config.persona.role, bot_id=config.scope.bot_id, enabled=True,
                        expected_revision=0, observed_at=at))
                    changed = True
                elif not selection.enabled:
                    raise TaskError("staging_screen_role_disabled", "Screen was explicitly disabled for this role")

                for capability in (SCREEN_READ, SCREEN_PROCESS):
                    consent = capabilities.consent(extension_id=SCREEN_EXTENSION,
                        capability_id=capability, subject=config.subject, scope=config.scope)
                    if consent.failure is not None:
                        if consent.failure.code != "capability_consent_missing":
                            require(consent)
                        require(capabilities.set_authorization(extension_id=SCREEN_EXTENSION,
                            capability_id=capability, subject=config.subject, scope=config.scope,
                            state=CapabilityAuthorizationState.ACTIVE, expected_revision=0, observed_at=at))
                        changed = True
                grants = self.identity.snapshot().grants
                for capability in self.SCREEN_ACTIONS:
                    grant_id = self.SCREEN_GRANT_PREFIX + capability
                    same = tuple(grant for grant in grants if grant.subject == config.subject
                        and grant.scope == config.scope and grant.capability == capability)
                    fixed = next((grant for grant in grants if grant.grant_id == grant_id), None)
                    if fixed is None and not same:
                        self.identity.issue_grant(grant_id=grant_id, subject=config.subject,
                            scope=config.scope, capability=capability, tier=AuthorizationTier.STANDARD,
                            valid_from=at, expires_at=config.expires_at,
                            expected_revision=self.identity.revision)
                        changed = True
                    elif (fixed is None or same != (fixed,) or fixed.revision != 1
                            or fixed.state is not GrantState.ACTIVE
                            or fixed.tier is not AuthorizationTier.STANDARD
                            or fixed.expires_at != config.expires_at):
                        raise TaskError("staging_screen_grant_changed", "Screen grant was changed or revoked")
                    require(self.identity.authorize(target.actor, scope=config.scope,
                        capability=capability, observed_at=at))
                for capability in (SCREEN_READ, SCREEN_PROCESS):
                    require(capabilities.authorize(extension_id=SCREEN_EXTENSION,
                        capability_id=capability, subject=config.subject, scope=config.scope))
                require(capabilities.require_role_enabled(extension_id=SCREEN_EXTENSION,
                    role=config.persona.role, bot_id=config.scope.bot_id))
                return Outcome.success(ScreenPreparationReceipt(package.package_version,
                    package.artifact_hash, config.scope.native_scope_id, not changed))
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except Exception:
            return Outcome.failed("staging_screen_preparation_failed", "Screen preparation could not be verified or committed")

    def upgrade_screen(self, package: CapabilityPackageDescriptor, *, previous_version: str,
                       previous_hash: str, expected_revision: int) -> Outcome[ScreenUpgradeReceipt]:
        """Explicitly replace one reviewed Screen package while preserving current authority."""
        try:
            self._screen_package(package)
            if (type(previous_version) is not str or not previous_version
                    or type(previous_hash) is not str or not previous_hash
                    or type(expected_revision) is not int or expected_revision < 1):
                raise TaskError("staging_screen_upgrade_invalid", "provide the exact installed Screen package and revision")
            with self.runtime.transaction():
                at = self._time()
                if self._marker() is None:
                    raise TaskError("staging_not_initialized", "initialize the staging profile first")
                self._target(at)
                capabilities = CapabilityRepository(self.runtime)
                current = require(capabilities.installation(extension_id=SCREEN_EXTENSION))
                if (current.package_id != package.package_id or current.package_version != previous_version
                        or current.artifact_hash != previous_hash or current.revision != expected_revision
                        or current.desired_state is not CapabilityDesiredState.ACTIVE):
                    raise TaskError("staging_screen_upgrade_conflict", "installed Screen package differs from the reviewed revision")
                if package.package_version == previous_version:
                    raise TaskError("staging_screen_upgrade_invalid", "a package change requires a new version")
                require(capabilities.register_descriptor(package, registered_at=at))
                upgraded = require(capabilities.update(extension_id=SCREEN_EXTENSION,
                    package_id=package.package_id, package_version=package.package_version,
                    expected_revision=expected_revision, observed_at=at))
                require(self.prepare_screen(package))
                return Outcome.success(ScreenUpgradeReceipt(previous_version, package.package_version,
                    package.artifact_hash, upgraded.revision, self.configuration.scope.native_scope_id))
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except Exception:
            return Outcome.failed("staging_screen_upgrade_failed", "Screen upgrade could not be verified or committed")

    async def _native_preflight(self):
        config, s, types = self.configuration, self.configuration.scope, self.native_types
        # No identity is manufactured from an event. Read the configured native
        # account/persona through the same adapter used for later dispatch.
        bridge = NativePrivateBridge(self.context, version=types.version, event_type=types.event,
            installation_id=s.installation_id, host_id=config.host_id, identities=self.identity,
            accounts=(HostAccount(s.platform, s.account_id, s.bot_id),), personas=(config.persona,),
            session_persona_override_lookup=types.session_persona_override_lookup,
            persona_snapshot_lookup=types.persona_snapshot_lookup,
            persona_selection_lookup=types.persona_selection_lookup)
        native = self.context.get_provider_by_id(config.provider_id)
        if not types.is_text_provider(native) or provider_fingerprint(native) != config.provider_fingerprint:
            raise TaskError("staging_provider_mismatch", "configured native provider fingerprint does not match")
        provider = AstrBotTextProvider(self.context, bridge=bridge,
            binding=ProviderBinding("model.text", config.provider_id, types.version, config.provider_fingerprint),
            provider_id=config.provider_id, version=types.version, provider_type=types.provider_types,
            function_tool_type=types.function_tool, tool_set_type=types.tool_set, tariff=config.tariff,
            # The probe only needs the provider ceiling; the saved default budget is
            # a task setting and must not be read as a provider capability here.
            maximum_output_tokens=MAX_MODEL_OUTPUT_TOKENS, maximum_input_estimate=config.maximum_input_estimate,
            output_parameter=config.output_parameter, streaming=config.streaming)
        if provider.native is not native:
            raise TaskError("staging_provider_mismatch", "native provider changed during bootstrap assembly")
        self._connected_account(bridge)
        await bridge.verify_role(s, RoleBinding(s, config.persona.role, 1))
        self._connected_account(bridge)
        provider._current()

    def _connected_account(self, bridge):
        scope = self.configuration.scope
        client = bridge.client(scope)
        if not getattr(client, "_wsr_api_clients", {}).get(scope.account_id):
            raise TaskError("staging_account_unavailable", "configured native account is not connected")

    async def initialize(self) -> Outcome[BootstrapReceipt]:
        """Explicit first-install control-plane operation, separate from load/start."""
        try:
            at = self._time()
            with self.runtime.read_snapshot():
                if self._marker() is not None:
                    self._target(at)
                    return Outcome.success(self._receipt(replayed=True))
                self._fresh()
            await self._native_preflight()
            # A competing initializer or management write may commit during await.
            with self.runtime.transaction():
                at = self._time()
                if self._marker() is not None:
                    self._target(at)
                    return Outcome.success(self._receipt(replayed=True))
                self._fresh()
                config = self.configuration
                operation = OperationRef(self.OPERATION_ID, "host.staging.bootstrap", config.scope,
                    VersionRef(self.OPERATION_ID, 1), self.OPERATION_ID)
                require(self.operations.reserve(operation, parameter_hash=_receipt_record(config), observed_at=at))
                # Prepare the built-in owner's migration marker in this explicit
                # initialization transaction; ordinary assembly must stay read-only.
                AgendaService(self.runtime)
                self.identity.bind(binding_id=self.IDENTITY_ID, principal=self.principal, subject=config.subject,
                    valid_from=config.valid_from, expires_at=config.expires_at, expected_revision=self.identity.revision)
                route = self.personas.register_route(ConversationRoute(config.scope, user=config.subject), observed_at=at)
                selector = next(s for s, _ in route.selectors() if s.kind is SelectorKind.SESSION)
                self.personas.set_binding(PersonaBinding(self.PERSONA_ID, selector, config.persona.role, 0,
                    config.valid_from, "astrbot-staging-bootstrap", config.authority_ref,
                    effective_until=config.expires_at), expected_revision=0, observed_at=at)
                for capability in config.capabilities:
                    self.identity.issue_grant(grant_id=self.grant_id(capability), subject=config.subject,
                        scope=config.scope, capability=capability, tier=AuthorizationTier.STANDARD,
                        valid_from=config.valid_from, expires_at=config.expires_at, expected_revision=self.identity.revision)
                self._target(at)
                require(self.operations.transition(self.OPERATION_ID, expected_state=OperationState.ACCEPTED,
                    next_state=OperationState.COMPLETED, observed_at=at))
                return Outcome.success(self._receipt(replayed=False))
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except Exception:
            return Outcome.failed("staging_bootstrap_failed", "bootstrap could not verify or commit the configured installation")

    def load(self, *, runtime_instance_id: str, native_tool_port=None, voice=None,
             voice_policy=None, weather_port=None, weather_alert_port=None, balance_port=None, news_port=None, solar_term_port=None,
             qzone_transport=None,
             additional_providers=(), fallback_provider_id="", replacement_rules="[]",
             fallback_on_refusal=False, review_only=False, default_provider_resolver=None,
             relay_tool_port_factory=None, search_port_factory=None) -> Outcome[AstrBotStagingSession]:
        """Read current authority and assemble; never initialize, grant or start work."""
        try:
            with self.runtime.read_snapshot():
                return Outcome.success(self._load(runtime_instance_id, native_tool_port, voice, voice_policy,
                                                  weather_port, weather_alert_port, balance_port, news_port, solar_term_port, additional_providers,
                                                  fallback_provider_id, replacement_rules, fallback_on_refusal, review_only,
                                                  qzone_transport, default_provider_resolver, relay_tool_port_factory,
                                                  search_port_factory))
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except Exception:
            return Outcome.failed("staging_load_failed", "staging configuration or native host could not be loaded")

    def _load(self, runtime_instance_id: str, native_tool_port=None, voice=None,
              voice_policy=None, weather_port=None, weather_alert_port=None, balance_port=None, news_port=None, solar_term_port=None,
              additional_providers=(), fallback_provider_id="", replacement_rules="[]",
              fallback_on_refusal=False, review_only=False, qzone_transport=None,
              default_provider_resolver=None, relay_tool_port_factory=None,
              search_port_factory=None) -> AstrBotStagingSession:
        if self._marker() is None:
            raise TaskError("staging_not_initialized", "use explicit initialization on a fresh staging store first")
        target = self._target(self._time())
        config = self.configuration
        native = self.context.get_provider_by_id(config.provider_id)
        if not self.native_types.is_text_provider(native) or provider_fingerprint(native) != config.provider_fingerprint:
            raise TaskError("staging_provider_mismatch", "configured native provider fingerprint does not match")
        profile = StagingProfile(target=target,
            runtime_identity=RuntimeIdentity(config.ecosystem_id, runtime_instance_id, "astrbot", config.host_id),
            persona=config.persona, provider_id=config.provider_id, tariff=config.tariff, budget=config.budget,
            budget_soft=config.budget_soft,
            task_prompt=config.task_prompt, delivery_mode=config.delivery_mode, max_output_tokens=config.max_output_tokens,
            maximum_input_estimate=config.maximum_input_estimate, output_parameter=config.output_parameter,
            policy=config.policy, limits=config.limits, streaming=config.streaming, segmentation=config.segmentation,
            forward_intake=config.forward_intake)
        session = AstrBotStagingSession(self.runtime, self.context, profile=profile, native_types=self.native_types,
            native_tool_port=native_tool_port if native_tool_port is not None else self.native_tool_port,
            now=self.now, voice=voice, voice_policy=voice_policy, weather_port=weather_port,
            weather_alert_port=weather_alert_port, balance_port=balance_port, news_port=news_port,
            solar_term_port=solar_term_port,
            qzone_transport=qzone_transport,
            default_provider_resolver=default_provider_resolver,
            additional_providers=additional_providers,
            fallback_provider_id=fallback_provider_id, replacement_rules=replacement_rules,
            fallback_on_refusal=fallback_on_refusal, review_only=review_only,
            relay_tool_port_factory=relay_tool_port_factory,
            search_port_factory=search_port_factory,
            profile_field_shapes={name: tuple(config._fingerprint_document().get(name, {}))
                                  for name in ("policy", "segmentation")},
            bootstrap_receipt_validated=True)
        # The native context may return a different provider on a later lookup.
        if session.model.native is not native or session.model.binding.generation != config.provider_fingerprint:
            raise TaskError("staging_provider_mismatch", "configured native provider fingerprint does not match")
        session.model._current()
        return session
