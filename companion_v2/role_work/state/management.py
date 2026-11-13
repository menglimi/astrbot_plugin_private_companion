"""Explicit core operations for state, schedules and presentation of one private role."""

from datetime import timedelta, timezone
import json
from zoneinfo import ZoneInfo

from companion_v2.execution.models import TaskError, digest, require, stamp
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, VersionRef
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository
from companion_v2.delivery.presence_diagnostics import describe_presentation
from companion_v2.role_work.life.management import RoleLifeManagement
from .ownership import role_schedule_key
from .configuration import (DEFAULTS, GROUP, OWNER, PERMISSIONS, role_target_for,
    sync_window_grants, target_for)
from .settings_scope import role_preference


class StateManagement:
    def __init__(self, app, context):
        self.app, self.context = app, context
        self.life = RoleLifeManagement(app, context)

    def token(self, operator, access, identifier, revision):
        basis = ["state-management.v1", operator.subject, operator.binding_id, operator.binding_revision,
                 self.context.key, access.policy_revision, identifier, revision]
        if identifier == "current":
            records = self.app.role_state.records
            try:
                original = records.version(self.context, "current", revision) if revision else None
            except TaskError:
                original = None
            basis.append(digest(original))
        return digest(basis)

    @staticmethod
    def key(value):
        if type(value) is not str or not value.strip() or len(value) > 128:
            raise TaskError("state_command_invalid", "provide a stable operation key")

    @outcome
    def status(self, operator, *, after_id="", limit=30):
        now = self.app.now()
        self.life.authorize(operator)
        try:
            from .configuration import ensure_window_presentation_grants
            ensure_window_presentation_grants(self.app.runtime, self.context, now)
        except Exception:
            pass
        try:
            from .configuration import ensure_role_state_grants
            ensure_role_state_grants(self.app.runtime, self.context, now)
        except Exception:
            pass
        with self.app.runtime.read_snapshot():
            now = self.app.now()
            access = self.life.authorize(operator)
            role_snapshot = require(self.app.configuration.read(operator, target=role_target_for(self.context),
                                    group_id=GROUP, observed_at=now))
            window_snapshot = require(self.app.configuration.read(operator, target=target_for(self.context),
                                      group_id=GROUP, observed_at=now))
            settings = {name: window_snapshot.values[GROUP + "." + name] for name in DEFAULTS}
            effective = {}
            for option, actions in PERMISSIONS.items():
                try:
                    if option == "enabled":
                        self.app.role_state.access.check_injection(self.context)
                    else:
                        for action in actions:
                            self.app.role_state.access.check(self.context, action)
                    effective[option] = {"allowed": bool(settings[option]), "reason": None}
                except TaskError as error:
                    effective[option] = {"allowed": False, "reason": error.code}
            try:
                state = self.app.role_state.read(self.context, operator=operator)
            except TaskError as error:
                original = self.app.role_state.records.get(self.context, "current")
                state = {"state": "unavailable", "revision": original["revision"] if original else 0, "code": error.code}
            state["view_token"] = self.token(operator, access, "current", state["revision"])
            calendar = self.app.calendar.inventory(self.context, after_id=after_id, limit=limit,
                                                   operator=operator)
            for item in calendar["items"]:
                item["view_token"] = self.token(operator, access, item["id"], item["revision"])
            schedule = self.app.runtime.read_one("SELECT next_at, enabled, result_json FROM role_state_schedules WHERE context_key = ?",
                                                 (role_schedule_key(self.context),))
            sleep = self.life.sleep_status(operator, access=access)
            settings["mood_cross_window"] = True
            local_day = now.astimezone(ZoneInfo(settings["planning_timezone"]))
            day_start = local_day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            return {"settings": settings,
                "body_scope": {"installation_id": self.context.scope.installation_id,
                    "bot_id": self.context.scope.bot_id, "role_id": self.context.role_binding.role.subject_id,
                    "settings": [name for name in DEFAULTS if name in ("hunger_enabled", "health_enabled") or name.startswith("cycle_")]},
                "edit_token": window_snapshot.edit_token, "role_edit_token": role_snapshot.edit_token,
                "cycle_settings_token": self.app.role_state.cycle_settings_token(settings),
                "effective": effective, "state": state, "calendar": calendar,
                "planning": self.app.calendar_planning.status(self.context),
                "create_token": self.token(operator, access, "calendar:new", 0),
                "occurrences": self.app.calendar.occurrences(self.context,
                    start=day_start.astimezone(timezone.utc), end=day_end.astimezone(timezone.utc),
                    operator=operator, diagnostics=True),
                **sleep,
                "maintenance": {"next_at": schedule["next_at"], "enabled": bool(schedule["enabled"]),
                    "result": json.loads(schedule["result_json"]) if schedule["result_json"] else None} if schedule else None,
                "presentation": self.app.presence.results_for(self.context) if self.app.presence else {"state": "preview"},
                "presentation_diagnostics": describe_presentation(self.app, self.context, settings=settings, effective=effective),
                "host_state": self.app.state}

    def _configure(self, operator, data):
        changes = data["changes"]
        if (type(changes) is not dict or not changes or set(changes) - set(DEFAULTS)
                or any(type(value) is not type(DEFAULTS[name]) for name, value in changes.items())
                or type(data.get("edit_token", "")) is not str
                or type(data.get("role_edit_token", "")) is not str):
            raise TaskError("state_configuration_invalid", "use explicit state settings with the declared types")
        observed_at = self.app.now()
        role_changes = {key: value for key, value in changes.items() if role_preference(key)}
        window_changes = {key: value for key, value in changes.items() if not role_preference(key)}
        if data["action"] == "configure_window":
            role_changes, window_changes = {}, dict(changes)
        if data["action"] == "configure_role" and (window_changes or not data.get("role_edit_token")):
            raise TaskError("state_configuration_invalid", "role defaults cannot include window-only settings")
        if data["action"] in ("configure", "configure_window") and (
                (data["action"] == "configure" and role_changes and not data.get("role_edit_token"))
                or (window_changes and not data.get("edit_token"))):
            raise TaskError("state_configuration_invalid", "provide the edit token for each configuration layer")

        role_target = role_target_for(self.context)
        window_target = target_for(self.context)
        role_token = data.get("role_edit_token", "")
        window_token = data.get("edit_token", "")
        op_id = "state-config:" + digest([operator.subject, self.context.key, data["operation_key"]])
        operation = OperationRef(op_id, OWNER, self.context.scope, VersionRef(op_id, 1), op_id)
        reserved = require(OperationRepository(self.app.configuration.store).reserve(operation,
            parameter_hash=digest({"action": data["action"], "changes": changes,
                "role_edit_token": role_token if role_changes else None,
                "edit_token": window_token if window_changes else None}), observed_at=observed_at))
        if reserved.replayed:
            if reserved.state is not OperationState.COMPLETED:
                raise TaskError("configuration_operation_incomplete", "the original settings save is not complete")
            return {"operation_id": op_id, "replayed": True}

        if role_changes:
            role_before = require(self.app.configuration.read(operator, target=role_target,
                group_id=GROUP, observed_at=observed_at))
            if role_before.edit_token != role_token:
                raise TaskError("configuration_revision_conflict", "role settings changed before this save")
        if window_changes:
            window_before = require(self.app.configuration.read(operator, target=window_target,
                group_id=GROUP, observed_at=observed_at))
            if window_before.edit_token != window_token:
                raise TaskError("configuration_revision_conflict", "window settings changed before this save")

        if role_changes:
            layer_id = "state-role-config:" + digest([operator.subject, role_target.key, data["operation_key"]])
            require(self.app.configuration.patch(operator, target=role_target, group_id=GROUP,
                operation=OperationRef(layer_id, OWNER, role_target.authorization_scope,
                    VersionRef(layer_id, 1), layer_id), expected_edit_token=role_token,
                changes={GROUP + "." + key: value for key, value in role_changes.items()},
                observed_at=observed_at))
        if window_changes:
            # The role write changes this target's inherited values and edit token.
            # The aggregate operation above already checked the caller's original token.
            window_current = require(self.app.configuration.read(operator, target=window_target,
                group_id=GROUP, observed_at=observed_at))
            layer_id = "state-window-config:" + digest([operator.subject, window_target.key, data["operation_key"]])
            require(self.app.configuration.patch(operator, target=window_target, group_id=GROUP,
                operation=OperationRef(layer_id, OWNER, window_target.authorization_scope,
                    VersionRef(layer_id, 1), layer_id), expected_edit_token=window_current.edit_token,
                changes={GROUP + "." + key: value for key, value in window_changes.items()},
                observed_at=observed_at))
        from .ownership import role_owner
        grant_changes = frozenset(set(role_changes).intersection(PERMISSIONS))
        if "enabled" in role_changes or "maintenance_grant_scope" in role_changes:
            grant_changes |= frozenset({"enabled"})
        if grant_changes:
            effective = require(self.app.configuration.read(operator, target=window_target,
                group_id=GROUP, observed_at=observed_at))
            current_settings = {name: effective.values[GROUP + "." + name] for name in DEFAULTS}
            sync_window_grants(self.app.runtime, window_target, self.context.actor.subject,
                current_settings, grant_changes, observed_at)
            if current_settings["maintenance_grant_scope"] == "role":
                owner = role_owner(self.context)
                for bound in tuple(self.app.state_maintenance.bindings.values()):
                    candidate = bound[0]
                    if (candidate.key == self.context.key
                            or role_owner(candidate) != owner
                            or candidate.scope.kind is not ScopeKind.PRIVATE):
                        continue
                    sync_window_grants(self.app.runtime, target_for(candidate),
                        candidate.actor.subject, current_settings, frozenset({"enabled"}),
                        observed_at)
        for bound in tuple(self.app.state_maintenance.bindings.values()):
            if role_owner(bound[0]) == role_owner(self.context):
                try:
                    self.app.state_maintenance.bind(*bound[:2], guard=bound[2])
                except TaskError:
                    continue  # A retired window cannot invalidate the current configuration.
        self.app.calendar_planning.invalidate(self.context)
        if self.app.presence is not None:
            # A dashboard write may target a role that is not currently selected
            # in this native window. Keep it available for conflict diagnostics,
            # but do not replace the verified active route until native intake
            # confirms the persona switch.
            self.app.presence.bind(self.context, active=False)
        require(OperationRepository(self.app.configuration.store).transition(op_id,
            expected_state=OperationState.ACCEPTED, next_state=OperationState.COMPLETED, observed_at=observed_at))
        return {"operation_id": op_id, "replayed": False}

    def _maintain(self, operator, data):
        self.app.require_running()
        service = self.app.state_maintenance
        settings, _ = self.app.role_state.settings(self.context)
        if not settings["enabled"] or self.context.key not in service.bindings:
            raise TaskError("state_not_enabled", "enable state and bind the current host persona first")
        self.app.role_state.access.check(self.context, "role.state.maintain")
        op_id = "state-request:" + digest([operator.subject, self.context.key, data["operation_key"]])
        operations = OperationRepository(self.app.runtime)
        reserved = require(operations.reserve(OperationRef(op_id, GROUP, self.context.scope, VersionRef(op_id, 1), op_id),
            parameter_hash=digest(data), observed_at=self.app.now()))
        if not reserved.replayed:
            current = self.app.role_state.records.get(self.context, "current")
            if (current["revision"] if current else 0) != data["expected_revision"]:
                raise TaskError("record_revision_conflict", "read the current state first")
            with self.app.runtime.transaction() as connection:
                connection.execute("UPDATE role_state_schedules SET next_at = ?, revision = revision + 1 WHERE context_key = ?",
                                   (stamp(self.app.now()), role_schedule_key(self.context)))
            require(operations.transition(op_id, expected_state=OperationState.ACCEPTED,
                next_state=OperationState.COMPLETED, observed_at=self.app.now()))
        return {"operation_id": op_id, "replayed": reserved.replayed, "state": "scheduled"}

    def _calendar_request(self, operator, data):
        # Registering a reminder changes the object policy revision and therefore
        # the view token. Persist the exact authorized management request in the
        # same transaction, so receipt recovery can survive its own policy change.
        # command() rechecks current operator/target access before this lookup.
        op_id = "state-calendar-request:" + digest([operator.subject, self.context.key, data["operation_key"]])
        receipt = require(OperationRepository(self.app.runtime).reserve(
            OperationRef(op_id, GROUP, self.context.scope, VersionRef(op_id, 1), op_id),
            parameter_hash=digest([data, operator.binding_id, operator.binding_revision]),
            observed_at=self.app.now()))
        if receipt.replayed and receipt.state is not OperationState.COMPLETED:
            raise TaskError("state_receipt_incomplete", "original calendar request has no completed receipt")
        return receipt

    @outcome
    def command(self, operator, data):
        self.key(data["operation_key"])
        with self.app.runtime.transaction():
            access = self.life.authorize(operator)
            action = data["action"]
            if action in ("configure", "configure_role", "configure_window"):
                result = self._configure(operator, data)
            else:
                revision = data["expected_revision"]
                if type(revision) is not int or revision < 0:
                    raise TaskError("state_command_invalid", "provide the original record revision")
                identifier = "current" if action in ("state.write", "cycle.align", "maintain") else data["id"]
                token_id = "calendar:new" if action == "calendar.create" else identifier
                calendar_request = self._calendar_request(operator, data) if action.startswith("calendar.") else None
                if not (calendar_request and calendar_request.replayed) and data["view_token"] != self.token(operator, access, token_id, revision):
                    raise TaskError("state_view_stale", "refresh the view before applying this change")
                key = digest([operator.subject, data["operation_key"]])
                if action == "state.write":
                    result = self.app.role_state.write(self.context, data=data["data"], expected_revision=revision,
                        operation_key=key, sources=data.get("sources", ()), operator=operator, reason="human_edit")
                elif action == "cycle.align":
                    if "settings_token" in data and type(data["settings_token"]) is not str:
                        raise TaskError("state_command_invalid", "provide the original settings token")
                    result = self.app.role_state.align_cycle(self.context, data=data["data"], expected_revision=revision,
                        operation_key=key, operator=operator, expected_settings_token=data.get("settings_token"))
                elif action == "maintain":
                    result = self._maintain(operator, data)
                elif action == "calendar.remind":
                    result = self.app.calendar.remind(self.context, identifier=identifier, expected_revision=revision,
                        operation_key=key, sequence=data["sequence"], operator=operator)
                elif action.startswith("calendar."):
                    result = self.app.calendar.command(self.context, identifier=identifier, action=action.split(".")[1],
                        expected_revision=revision, operation_key=key, data=data.get("data"),
                        sources=data.get("sources", ()), operator=operator)
                else:
                    raise TaskError("state_command_invalid", "unknown state command")
                if calendar_request and not calendar_request.replayed:
                    require(OperationRepository(self.app.runtime).transition(calendar_request.operation.operation_id,
                        expected_state=OperationState.ACCEPTED, next_state=OperationState.COMPLETED,
                        observed_at=self.app.now()))
        self.app.notify()
        return result
