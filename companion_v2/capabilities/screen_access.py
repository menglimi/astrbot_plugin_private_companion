"""Explicit, per-private-window access to the loaded native Screen provider."""

from companion_v2.capabilities.models import CapabilityAuthorizationState, CapabilityDesiredState
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.foundation.refs import OperationRef, ScopeKind, SubjectKind, VersionRef
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.storage.codec import scope_values
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository

from .privacy import is_core


EXTENSION = "companion.screen.native"
PACKAGE_ID = "astrbot_plugin_private_companion.screen"
PROVIDER = "screen.native"
ACTION = "screen.capture"
CAPABILITIES = ("screen.read", "screen.process")
OWNER = "capability.screen-access"


class ScreenAccess:
    def __init__(self, application, context, descriptor):
        self.application, self.context, self.descriptor = application, context, descriptor
        self.package = descriptor.package
        self.runtime = application.runtime
        self.repository = CapabilityRepository(self.runtime)
        self.identities = PersistentIdentityDirectory(self.runtime)
        self.policy = ObjectAccessPolicy(self.runtime)
        if (descriptor.binding.provider != PROVIDER
                or descriptor.tool.action != ACTION
                or self.package.extension_id != EXTENSION or self.package.package_id != PACKAGE_ID
                or set(self.package.capabilities) != set(CAPABILITIES)
                or set(self.package.dataset_owners) != {"screen"}):
            raise TaskError("screen_provider_changed", "the loaded Screen package changed")

    def _access(self, operator, at, *, action="capability.view", require_active_context=True):
        # 核心管理员默认可用：任何会话范围都不再是门槛。
        if is_core(self.runtime, operator.subject):
            return
        if require_active_context:
            require(self.application.tasks.authorize_context(
                self.context, action="conversation.reply", observed_at=at))
        require(self.policy.authorize(operator, scope=self.context.scope,
            action=action, owner=self.context.actor.subject, observed_at=at))

    def _consents(self):
        rows = self.runtime.read_all("""SELECT capability_id, state, revision
            FROM capability_authorizations
            WHERE extension_id = ? AND subject_kind = ? AND subject_id = ?
              AND installation_id = ? AND platform = ? AND scope_kind = ? AND native_scope_id = ?
              AND bot_id = ? AND account_id = ? AND conversation_id = ? AND session_id = ?""",
            (EXTENSION, self.context.actor.subject.kind.value, self.context.actor.subject.subject_id,
             *scope_values(self.context.scope)))
        return {row["capability_id"]: (row["state"], row["revision"]) for row in rows
                if row["capability_id"] in CAPABILITIES}

    def _state(self):
        installation_result = self.repository.installation(extension_id=EXTENSION)
        if not installation_result.is_success and installation_result.failure.code != "capability_not_installed":
            require(installation_result)
        installed = installation_result.unwrap() if installation_result.is_success else None
        selection = None
        if installed is not None:
            projection = self.repository.projection(extension_id=EXTENSION,
                role=self.context.role_binding.role, scope=self.context.scope)
            if projection.is_success:
                selection = projection.unwrap().role_selection
            else:
                require(projection)
        return installed, selection, self._consents()

    def _token(self, state):
        return digest([self.context.key, encode(self.descriptor),
            [encode(item) if item is not None else None for item in state[:2]],
            state[2], self.identities.revision, self.policy.revision])

    def status(self, operator, at):
        self._access(operator, at, require_active_context=False)
        with self.runtime.read_snapshot():
            state = self._state()
            installed, selection, consents = state
            same_package = installed is not None and (
                installed.package_id, installed.package_version, installed.artifact_hash
            ) == (self.package.package_id, self.package.package_version, self.package.artifact_hash)
            upgrade_available = bool(installed is not None
                and installed.package_id == self.package.package_id
                and installed.package_version != self.package.package_version
                and installed.desired_state is CapabilityDesiredState.ACTIVE)
            active = bool(same_package and installed.desired_state is CapabilityDesiredState.ACTIVE)
            role_enabled = bool(selection and selection.enabled)
            # 默认可用看的是这个窗口实际发起的操作者；管理权限仍看 Dashboard 身份。
            core = is_core(self.runtime, self.context.actor.subject)
            manage_core = is_core(self.runtime, operator.subject)
            read_consented = core or consents.get("screen.read", (None, 0))[0] == CapabilityAuthorizationState.ACTIVE.value
            process_consented = core or consents.get("screen.process", (None, 0))[0] == CapabilityAuthorizationState.ACTIVE.value
            can_manage = manage_core or self.policy.scope_access(operator, scope=self.context.scope,
                action="capability.role.set", observed_at=at, core_only=True).is_success
            catalog = getattr(self.application, "external", None)
            invokable = bool(catalog and any(tool.action == ACTION for tool in catalog.catalog_for(self.context)))
            reason = ("capability_not_installed" if installed is None else
                "screen_package_changed" if not same_package else
                "capability_not_active" if not active else
                "capability_role_disabled" if not role_enabled else
                "screen_read_consent_missing" if not read_consented else
                "screen_process_consent_missing" if not process_consented else
                "screen_tool_unavailable" if not invokable else "")
            return {"action": ACTION, "package_version": self.package.package_version,
                "upgrade_available": upgrade_available,
                "installed_package_version": installed.package_version if installed else None,
                "installed_artifact_hash": installed.artifact_hash if installed else None,
                "installed_revision": installed.revision if installed else None,
                "installed": installed is not None, "active": active,
                "role_enabled": role_enabled, "role_revision": selection.revision if selection else 0,
                "read_consented": read_consented, "process_consented": process_consented,
                "consented": read_consented and process_consented, "auto_consent": core,
                "invokable": invokable, "can_manage": can_manage,
                "reason": reason, "edit_token": self._token(state)}

    def configure(self, operator, command, at):
        self._access(operator, at, action="capability.consent.set")
        require(self.policy.scope_access(operator, scope=self.context.scope,
            action="capability.role.set", observed_at=at, core_only=True))
        if (type(command) is not dict or set(command) != {"operation_key", "edit_token", "enabled"}
                or type(command["enabled"]) is not bool
                or any(type(command[key]) is not str or not command[key].strip() or len(command[key]) > 160
                       for key in ("operation_key", "edit_token"))):
            raise TaskError("screen_access_request_invalid", "an explicit scoped Screen access command is required")
        context = self.context
        key = digest([context.key, operator.subject.subject_id, command["operation_key"]])
        operation = OperationRef("screen-access:" + key, OWNER, context.scope,
            VersionRef(key, 1), key)
        operations = OperationRepository(self.runtime)
        with self.runtime.transaction():
            reservation = require(operations.reserve(operation,
                parameter_hash=digest([encode(operator.principal), encode(operator.subject),
                    operator.binding_id, operator.binding_revision, context.key, command]), observed_at=at))
            if reservation.replayed:
                if reservation.state is not OperationState.COMPLETED:
                    raise TaskError("capability_operation_unresolved", "the original Screen access command is unresolved")
                return {"replayed": True, "status": self.status(operator, at)}
            state = self._state()
            if command["edit_token"] != self._token(state):
                raise TaskError("screen_access_changed", "refresh Screen access before saving")
            installed, selection, consents = state
            if not command["enabled"] and (
                    installed is None or installed.desired_state is CapabilityDesiredState.DISCARDED):
                raise TaskError("capability_not_installed", "the Screen extension has not been assembled")
            if command["enabled"]:
                require(self.repository.register_descriptor(self.package, registered_at=at))
                if installed is None:
                    installed = require(self.repository.acquire(extension_id=EXTENSION,
                        package_id=self.package.package_id, package_version=self.package.package_version,
                        expected_revision=0, observed_at=at))
                elif (installed.package_id, installed.package_version, installed.artifact_hash) != (
                        self.package.package_id, self.package.package_version, self.package.artifact_hash):
                    raise TaskError("screen_package_changed", "the selected package differs from the loaded provider")
                if installed.desired_state is not CapabilityDesiredState.ACTIVE:
                    raise TaskError("capability_not_active", "the selected Screen package is paused or discarded")
                if not selection or not selection.enabled:
                    require(self.repository.set_role_enabled(extension_id=EXTENSION,
                        role=context.role_binding.role, bot_id=context.scope.bot_id, enabled=True,
                        expected_revision=selection.revision if selection else 0, observed_at=at))
                for capability in (*CAPABILITIES, "extension.tool.invoke"):
                    if self.identities.authorize(context.actor, scope=context.scope,
                            capability=capability, observed_at=at).is_success:
                        continue
                    binding = next(item for item in self.identities.snapshot().bindings
                                   if item.binding_id == context.actor.binding_id)
                    self.identities.issue_grant(grant_id="screen-access:" + key + ":" + capability,
                        subject=context.actor.subject, scope=context.scope, capability=capability,
                        tier=AuthorizationTier.STANDARD, valid_from=at, expires_at=binding.expires_at,
                        expected_revision=self.identities.revision)
            for capability in CAPABILITIES:
                current = consents.get(capability)
                next_state = CapabilityAuthorizationState.ACTIVE if command["enabled"] else CapabilityAuthorizationState.REVOKED
                if command["enabled"] or (current and current[0] != next_state.value):
                    require(self.repository.set_authorization(extension_id=EXTENSION,
                        capability_id=capability, subject=context.actor.subject, scope=context.scope,
                        state=next_state, expected_revision=current[1] if current else 0, observed_at=at))
            if not command["enabled"]:
                for grant in self.identities.snapshot().grants:
                    if (grant.grant_id.startswith("screen-access:") and grant.subject == context.actor.subject
                            and grant.scope == context.scope and grant.capability in (*CAPABILITIES, "extension.tool.invoke")
                            and grant.state is GrantState.ACTIVE):
                        self.identities.revoke_grant(grant.grant_id, revoked_at=at,
                            expected_revision=self.identities.revision)
            require(operations.transition(operation.operation_id,
                expected_state=OperationState.ACCEPTED, next_state=OperationState.COMPLETED, observed_at=at))
            status = self.status(operator, at)
            if command["enabled"] and not status["invokable"]:
                raise TaskError("screen_tool_unavailable", "Screen did not enter this private conversation's tool catalog")
            return {"replayed": False, "status": status}


__all__ = ["ACTION", "CAPABILITIES", "EXTENSION", "OWNER", "PROVIDER", "PACKAGE_ID", "ScreenAccess"]
