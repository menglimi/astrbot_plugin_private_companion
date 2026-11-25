"""Core-managed contact preferences for the original private participant."""

from companion_v2.execution.models import TaskError, digest, require
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, VersionRef
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.grants import PERSON_PERMISSION_GRANT_PREFIX

from .configuration import GROUP, PURPOSES, target_for
from .preferences import ContactPreferences
from .service import CONTACT_KIND


GRANT_PREFIX = "proactive-managed:"


class ContactManagement:
    def __init__(self, app, context):
        self.app, self.context, self.runtime = app, context, app.runtime
        self.policy = ObjectAccessPolicy(self.runtime)

    def _authorize(self, operator):
        now, context = self.app.now(), self.context
        # Authorize before reading configuration or exposing the target's state.
        require(self.policy.scope_access(operator, scope=context.scope, action="configuration.write",
                                          observed_at=now, core_only=True))
        if context.scope.kind is not ScopeKind.PRIVATE:
            raise TaskError("contact_private_required", "contact settings require one original private participant")
        # This checks the real route, account, audience and current role binding;
        # it grants no target permission and never substitutes the operator.
        self.app.tasks._context(context, now)

    def _status(self, operator):
        self._authorize(operator)
        now = self.app.now()
        snapshot = require(self.app.configuration.read(operator, target=target_for(self.context),
                                                        group_id=GROUP, observed_at=now))
        settings = {key.removeprefix(GROUP + "."): value for key, value in snapshot.values.items()}
        preferences = ContactPreferences(snapshot, settings, now)
        access = self.app.proactive.preferences.read(self.context, observed_at=now)
        text_access = self.app.tasks.identity.authorize(self.context.actor, scope=self.context.scope,
                                                         capability="text", observed_at=now)
        registered = CONTACT_KIND in self.app.gateway.router.profiles
        authority_reason = (access.failure.code if access.failure else
                            text_access.failure.code if text_access.failure else None)
        runtime_reason = "host_not_running" if self.app.state != "running" else None if registered else "model_task_unregistered"
        purposes = {}
        for purpose, label in PURPOSES.items():
            reason = authority_reason or preferences.reason(purpose) or runtime_reason
            purposes[purpose] = {"label": label, "allowed": reason is None, "reason": reason}
        allowed = any(item["allowed"] for item in purposes.values())
        reason = None if allowed else authority_reason or next(iter(purposes.values()))["reason"]
        automatic_reason = reason or access.unwrap().automatic_reason()
        return {"settings": settings, "edit_token": snapshot.edit_token,
            "effective": {"allowed": allowed, "reason": reason,
                "automatic_allowed": automatic_reason is None, "automatic_reason": automatic_reason,
                "model_registered": registered, "readonly_fields": []},
            "purposes": purposes, "host_state": self.app.state}

    @outcome
    def status(self, operator):
        with self.runtime.read_snapshot():
            return self._status(operator)

    def _grants(self, enabled, operation_id):
        identity, context = self.app.tasks.identity, self.context
        if any(grant.grant_id.startswith(PERSON_PERMISSION_GRANT_PREFIX)
               and grant.capability == GROUP and grant.scope == context.scope
               and grant.subject == context.actor.subject for grant in identity.snapshot().grants):
            return
        for grant in identity.snapshot().grants:
            if (grant.grant_id.startswith(GRANT_PREFIX) and grant.capability == GROUP
                    and grant.scope == context.scope and grant.subject == context.actor.subject
                    and grant.state is GrantState.ACTIVE):
                identity.revoke_grant(grant.grant_id, revoked_at=self.app.now(), expected_revision=identity.revision)
        if enabled:
            identity.issue_grant(grant_id=GRANT_PREFIX + digest([context.key, operation_id]),
                subject=context.actor.subject, scope=context.scope, capability=GROUP,
                tier=AuthorizationTier.STANDARD, valid_from=self.app.now(), expected_revision=identity.revision)

    def sync_configuration_grant(self, operator, enabled, receipt):
        """Keep the generic settings editor's enabled switch on the same grant path.

        A replay may be recovering a save made by older code that stored the
        preference without issuing its grant. Recover only a missing lineage while
        the preference is still enabled; an existing revoked lineage stays revoked.
        """
        with self.runtime.transaction():
            self._authorize(operator)
            current = require(self.app.configuration.read(operator, target=target_for(self.context),
                group_id=GROUP, observed_at=self.app.now()))
            if current.values[GROUP + ".enabled"] is not enabled:
                return

            identity, context = self.app.tasks.identity, self.context
            grants = identity.snapshot().grants
            if any(grant.grant_id.startswith(PERSON_PERMISSION_GRANT_PREFIX)
                   and grant.capability == GROUP and grant.scope == context.scope
                   and grant.subject == context.actor.subject for grant in grants):
                return

            operation_id = receipt["operation_id"]
            grant_id = GRANT_PREFIX + digest([context.key, operation_id])
            lineage = next((grant for grant in grants if grant.grant_id == grant_id), None)
            if receipt["replayed"]:
                if lineage is not None:
                    return
                if enabled and any(grant.grant_id.startswith(GRANT_PREFIX)
                    and grant.capability == GROUP and grant.scope == context.scope
                    and grant.subject == context.actor.subject and grant.state is GrantState.ACTIVE
                    for grant in grants):
                    return
                if enabled:
                    identity.issue_grant(grant_id=grant_id, subject=context.actor.subject,
                        scope=context.scope, capability=GROUP, tier=AuthorizationTier.STANDARD,
                        valid_from=self.app.now(), expected_revision=identity.revision)
                else:
                    for grant in grants:
                        if (grant.grant_id.startswith(GRANT_PREFIX) and grant.capability == GROUP
                                and grant.scope == context.scope and grant.subject == context.actor.subject
                                and grant.state is GrantState.ACTIVE):
                            identity.revoke_grant(grant.grant_id, revoked_at=self.app.now(),
                                                  expected_revision=identity.revision)
                return

            self._grants(enabled, operation_id)

    def _configure(self, operator, data, *, require_running, sync_grants=True):
        if (type(data) is not dict or set(data) != {"operation_key", "edit_token", "changes"}
                or type(data["operation_key"]) is not str or not data["operation_key"].strip()
                or len(data["operation_key"]) > 128 or type(data["edit_token"]) is not str
                or not data["edit_token"] or len(data["edit_token"]) > 256
                or type(data["changes"]) is not dict or not data["changes"]
                or any(type(key) is not str for key in data["changes"])):
            raise TaskError("contact_configuration_invalid", "provide original settings, edit token and stable operation key")
        if require_running:
            self.app.require_running()
        elif (set(data["changes"]) != {"enabled"}
                or type(data["changes"]["enabled"]) is not bool or data["changes"]["enabled"]):
            raise TaskError("contact_configuration_invalid", "stopped contact recovery may only disable contact settings")
        with self.runtime.transaction():
            self._authorize(operator)
            if require_running:
                require(self.app.tasks.check_generation())
            fields = {field.field_id.removeprefix(GROUP + ".") for field in self.app.configuration.registry.group(GROUP)}
            if set(data["changes"]) - fields:
                raise TaskError("contact_configuration_invalid", "contact settings contain unknown fields")
            operation_id = "contact-config:" + digest([operator.subject, self.context.key, data["operation_key"]])
            receipt = require(self.app.configuration.patch(operator, target=target_for(self.context), group_id=GROUP,
                operation=OperationRef(operation_id, GROUP, self.context.scope, VersionRef(operation_id, 1), operation_id),
                expected_edit_token=data["edit_token"], changes={GROUP + "." + key: value for key, value in data["changes"].items()},
                observed_at=self.app.now()))
            if sync_grants and not receipt.replayed and "enabled" in data["changes"]:
                self._grants(data["changes"]["enabled"], operation_id)
            result = {"operation_id": operation_id, "replayed": receipt.replayed, "status": self._status(operator)}
        self.app.notify()
        return result

    @outcome
    def configure(self, operator, data):
        return self._configure(operator, data, require_running=True)

    @outcome
    def restore_temporary_disable(self, operator, data):
        """Restore the setup switch without revoking the administrator's test grant."""
        if (type(data) is not dict or type(data.get("changes")) is not dict
                or set(data["changes"]) != {"enabled"}
                or type(data["changes"].get("enabled")) is not bool
                or data["changes"]["enabled"]):
            raise TaskError("contact_configuration_invalid", "stopped contact recovery may only revoke contact permission")
        return self._configure(operator, data, require_running=False, sync_grants=False)
