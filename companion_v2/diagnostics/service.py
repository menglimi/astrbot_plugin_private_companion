"""On-demand local checks. No source scanning, polling, model or network calls.

Only built-in bounded readers run here. Their timeout is a local result budget,
not a claim that arbitrary synchronous plugin code can be forcibly cancelled.
External L3 probes require the future supervised task runner.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from time import perf_counter
from typing import Callable
from uuid import uuid4

from companion_v2.capabilities.management import CapabilityManagementService
from companion_v2.configuration.models import ConfigurationError, encode, fingerprint
from companion_v2.configuration.service import ConfigurationService
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef, normalize_utc
from companion_v2.identity_policy.personas import PersonaDirectory
from companion_v2.storage.models import OperationState
from companion_v2.storage.backup import InstallationBackup
from companion_v2.storage.repositories import OperationRepository
from companion_v2.storage.schema import SCHEMA_VERSION
from companion_v2.execution.models import TaskContext
from companion_v2.execution.service import TaskSupervisor
from companion_v2.model_gateway.service import LocalModelGateway
from companion_v2.conversation.messages import ConversationMessages

from .models import (
    CheckResult, CheckStatus, DiagnosticCheck, DiagnosticContext, DiagnosticError,
    DiagnosticEvidence, DiagnosticMode, DiagnosticQuery, DiagnosticReport, Finding, require,
)


CHECKS = (
    DiagnosticCheck("configuration", "配置来源与采用", "1", "L1", "explicit_target_and_group", "configuration.read"),
    DiagnosticCheck("capability", "能力本地选择与本人同意", "1", "L0", "original_conversation_and_role", "capability.view"),
    DiagnosticCheck("storage", "本地数据库可读性", "1", "L1", "installation", "diagnostics.storage.read"),
    DiagnosticCheck("operation", "原操作与本地投递记录", "1", "L2", "exact_operation_and_time_window", "diagnostics.operation.read"),
    DiagnosticCheck("execution", "执行链接入与原任务记录", "4", "L1", "original_conversation_and_exact_task", "current_identity_role_and_task_action"),
)

EXPLANATIONS = {
    "configuration": "配置按安装、角色和窗口继承，显式值与继承分别保存。保存回执与消费者采用回执分别查证。概率存储为 0..1，页面可显示百分比；权重仅参与多个可行念头的竞争。",
    "capability": "获取家具对应选择能力包。获取、角色启用、范围同意与实际装载是不同状态，需要分别查证。保留同意不意味着当前设备在线，也不替代原件读取权限。",
    "operation": "操作身份用于查回同一次操作。accepted、completed 和 unknown 记录本地阶段；外部发送与收件人收到需要各自的实际证据。缺少记录不证明操作从未开始。",
    "repair": "修复建议保存字段差异、依据、影响范围和原版本。确认后重新核对当前身份、角色、授权及版本，再由公共配置服务提交；取消不修改配置。",
}


def report_document(report: DiagnosticReport) -> dict:
    """Private diagnostic material; clocks are observations, never model estimates."""
    def convert(value):
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError("unsupported diagnostic report value")
    return json.loads(json.dumps(asdict(report), ensure_ascii=False, default=convert))


class DiagnosticService:
    def __init__(self, configuration: ConfigurationService, *, now: Callable[[], datetime] | None = None,
                 monotonic: Callable[[], float] = perf_counter, tasks: TaskSupervisor | None = None,
                 models: LocalModelGateway | None = None, messages: ConversationMessages | None = None):
        self.configuration = configuration
        self.runtime = configuration.runtime
        self.identity = configuration.identity
        self.policy = configuration.policy
        self.capabilities = CapabilityManagementService(self.runtime)
        self.operations = OperationRepository(self.runtime)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic
        if tasks is not None and tasks.runtime is not self.runtime:
            raise ValueError("diagnostics must use the same task runtime and read snapshot")
        self.tasks = tasks
        if models is not None and (models.tasks is not tasks or models.router.configuration is not configuration):
            raise ValueError("model diagnostics must share the original configuration consumer and supervisor")
        self.models = models
        if messages is not None and messages.tasks is not tasks:
            raise ValueError("message diagnostics must share the original supervisor")
        self.messages = messages
        # Short-lived original reports, capped in memory; never a background cache.
        self._reports: OrderedDict[str, DiagnosticReport] = OrderedDict()

    def current(self, context: DiagnosticContext, *, private: bool = True) -> datetime:
        now = normalize_utc(self.now(), field="diagnostic clock")
        actor = require(self.identity.resolve_authenticated(context.actor.principal, observed_at=now))
        if (actor.subject, actor.binding_id, actor.binding_revision) != (
                context.actor.subject, context.actor.binding_id, context.actor.binding_revision):
            raise DiagnosticError("binding_rebound", "original diagnostic caller changed")
        principal = actor.principal
        if (principal.installation_id != self.runtime.installation_id or
                (principal.platform, principal.bot_id, principal.account_id) !=
                (context.scope.platform, context.scope.bot_id, context.scope.account_id)):
            raise DiagnosticError("diagnostic_context_mismatch", "caller belongs to another bot/account route")
        binding = require(self.identity.resolve_role(context.scope, observed_at=now))
        if binding != context.role_binding:
            raise DiagnosticError("role_binding_changed", "diagnostic answer must retain the original persona revision")
        if private:
            route = PersonaDirectory(self.runtime).route(context.scope)
            if (context.scope.kind is not ScopeKind.PRIVATE or context.audience.recipients != (actor.subject,)
                    or route is None or route.user != actor.subject):
                raise DiagnosticError("private_result_required", "runtime diagnostics require this caller's private audience")
        return now

    def _configuration(self, context: DiagnosticContext, query: DiagnosticQuery, now: datetime):
        snapshot = require(self.configuration.read(context.actor, target=query.target, group_id=query.group_id, observed_at=now))
        adopted = require(self.configuration.application_status(context.actor, target=query.target, group_id=query.group_id, observed_at=now))
        status = {"applied": CheckStatus.PASS, "failed": CheckStatus.FAIL,
                  "consumer_not_started": CheckStatus.UNKNOWN}.get(adopted.state, CheckStatus.WARN)
        details = {
            "schema_version": snapshot.schema_version, "configuration_revision": snapshot.revision,
            "change_sequence": snapshot.change_sequence, "consumer_id": snapshot.consumer_id,
            "apply_mode": snapshot.apply_mode.value, "application_state": adopted.state,
            "consumer_generation": adopted.generation,
            "fields": {key: {"source": value.source.layer.value if value.source else "default",
                             "explicit": value.explicit, "source_revision": value.source_revision}
                       for key, value in snapshot.fields.items()},
            "values_redacted": True,
        }
        token = fingerprint([snapshot.edit_token, adopted.state, adopted.generation, dict(adopted.adopted_values)])
        return status, adopted.state, details, query.target.authorization_scope, token

    def _capability(self, context: DiagnosticContext, query: DiagnosticQuery, now: datetime):
        result = self.capabilities.projection(context.actor, extension_id=query.extension_id,
                        role=context.role_binding.role, scope=context.scope, observed_at=now)
        if result.failure:
            if result.failure.code == "capability_not_installed":
                return CheckStatus.NOT_APPLICABLE, result.failure.code, {"package_selection": "absent"}, context.scope, "absent"
            require(result)
        projection = result.unwrap()
        installation, role, settings = projection.installation, projection.role_selection, projection.settings
        details = {"package_version": installation.package_version, "desired_state": installation.desired_state.value,
                   "installation_revision": installation.revision, "role_enabled": role.enabled if role else None,
                   "role_revision": role.revision if role else 0, "settings_revision": settings.revision if settings else 0,
                   "runtime_ready": "unknown", "device_online": "unknown", "consent": "not_requested"}
        if query.capability_id:
            consent = self.capabilities.own_consent(context.actor, extension_id=query.extension_id,
                        capability_id=query.capability_id, scope=context.scope, observed_at=now)
            if consent.failure and consent.failure.code not in ("capability_consent_missing", "capability_consent_revoked"):
                require(consent)
            details["consent"] = consent.failure.code if consent.failure else "active"
            details["consent_revision"] = consent.value.revision if consent.value else None
        return CheckStatus.UNKNOWN, "capability_runtime_unobserved", details, context.scope, fingerprint(details)

    def _storage(self, context: DiagnosticContext, query: DiagnosticQuery, now: datetime):
        scope = ScopeRef(self.runtime.installation_id, "companion", ScopeKind.SYSTEM, "diagnostics.storage")
        require(self.policy.scope_access(context.actor, scope=scope, action="diagnostics.storage.read", observed_at=now))
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = 'schema_version'", ())
        version = row["value"] if row else None
        # Report the backup state this installation actually has. An unverified or
        # absent snapshot is a real finding here, not a placeholder: the earlier
        # hard-coded "not_run" hid the difference between "no backups" and
        # "backups exist but were never checked".
        snapshots, verification = self._backups()
        details = {"schema_version": version, "connection": "readable", "integrity_scan": "not_run",
                   "backup_verification": verification, "snapshot_count": snapshots,
                   "v1_migration": "not_run"}
        return (CheckStatus.PASS if version == SCHEMA_VERSION else CheckStatus.FAIL,
                "local_schema_readable" if version == SCHEMA_VERSION else "local_schema_mismatch", details, scope, fingerprint(details))

    def _backups(self) -> tuple[int, str]:
        """Count complete snapshots and verify the newest one, without restoring it."""
        try:
            listing = InstallationBackup(self.runtime).listing()
            if not listing.is_success:
                return 0, "unreadable"
            documents = listing.unwrap()["snapshots"]
        except (OSError, ValueError):
            return 0, "unreadable"
        if not documents:
            return 0, "no_snapshot"
        newest = documents[0]
        try:
            verified = InstallationBackup(self.runtime).inspect(snapshot_id=newest["snapshot_id"])
        except (OSError, ValueError):
            return len(documents), "unverified"
        if not verified.is_success:
            return len(documents), "failed"
        return len(documents), "verified"

    def _operation(self, context: DiagnosticContext, query: DiagnosticQuery, now: datetime):
        # The generic ledger lacks object-owner metadata. Only installation core may
        # inspect it. Non-core object lookups continue through their domain service.
        require(self.policy.scope_access(context.actor, scope=query.operation_scope,
                                        action="diagnostics.operation.read", observed_at=now))
        if query.window.end > now:
            raise DiagnosticError("incident_window_in_future", "incident window must end no later than the actual check")
        result = self.operations.get(query.operation_id)
        if result.failure or result.value.operation.scope != query.operation_scope:
            return CheckStatus.UNKNOWN, "operation_not_observed", {"external_effect": "unknown"}, query.operation_scope, "absent"
        record = result.unwrap()
        if not any(query.window.start <= time <= query.window.end for time in (record.created_at, record.updated_at)):
            return CheckStatus.UNKNOWN, "operation_outside_window", {"external_effect": "unknown"}, query.operation_scope, "outside"
        if record.updated_at > query.window.end:
            return (CheckStatus.UNKNOWN, "operation_changed_after_window", {"state_in_window": "unknown", "external_effect": "unknown"},
                    query.operation_scope, fingerprint([record.state.value, record.updated_at.isoformat()]))
        # Exact indexed operation lookup; grouping returns at most the four known states.
        rows = self.runtime.read_all("SELECT state, COUNT(*) AS n FROM outbox WHERE operation_id = ? GROUP BY state",
                                     (record.operation.operation_id,))
        outbox = {row["state"]: row["n"] for row in rows}
        details = {"local_operation_state": record.state.value, "local_commit_state": record.commit_state.value,
                   "created_at": record.created_at.isoformat(), "updated_at": record.updated_at.isoformat(),
                   "outbox": outbox, "external_effect": "unknown", "recipient_received": "unknown",
                   "outbox_coverage": "current_snapshot_without_transition_history",
                   "timeline_coverage": "creation_and_latest_local_state_only", "queue_and_provider_latency": "unknown"}
        code = "local_operation_" + record.state.value
        status = CheckStatus.UNKNOWN if record.state is OperationState.UNKNOWN or "unknown" in outbox else CheckStatus.WARN
        return status, code, details, query.operation_scope, fingerprint(details)

    def _execution(self, context: DiagnosticContext, query: DiagnosticQuery, now: datetime):
        details = {"worker": "not_connected", "provider": "not_connected", "sender": "not_connected",
                   "runtime_latency": "unknown", "scope": "this_isolated_service"}
        if self.tasks is not None:
            details["worker"] = "local_supervisor_registered"
            if query.execution_task_id is not None:
                original = TaskContext(context.actor, context.role_binding, context.audience)
                details["task"] = require(self.tasks.observe(original, query.execution_task_id))
                if self.models is not None:
                    details["model"] = require(self.models.observe(original, query.execution_task_id))
                    details["provider"] = details["model"]["transport"]
                status = CheckStatus.UNKNOWN if details["task"]["state"] == "uncertain" else CheckStatus.WARN
                if self.models is not None and any(call["usage_state"] == "unknown" for call in details["model"]["calls"]):
                    status = CheckStatus.UNKNOWN
                if self.messages is not None:
                    details["conversation"] = require(self.messages.observe_task(original, query.execution_task_id))
                    details["sender"] = details["conversation"]["sender"]
                    if details["conversation"]["unresolved_parts"]:
                        status = CheckStatus.UNKNOWN
                return status, "local_task_observed", details, context.scope, fingerprint(details)
            return CheckStatus.UNKNOWN, "execution_partially_connected", details, context.scope, fingerprint(details)
        return CheckStatus.UNKNOWN, "execution_unobserved", details, context.scope, fingerprint(details)

    def collect(self, context: DiagnosticContext, query: DiagnosticQuery) -> tuple[tuple[CheckResult, ...], str]:
        """Trusted local collection, also used to revalidate persisted proposal evidence."""
        results = []
        with self.runtime.read_snapshot():
            now = self.current(context)
            selected = (query.target is not None, query.extension_id is not None, query.storage,
                        query.operation_id is not None, query.execution)
            for check, enabled in zip(CHECKS, selected):
                if not enabled:
                    continue
                started = self.now()
                monotonic_start = self.monotonic()
                status, code, details, scope, token = getattr(self, "_" + check.check_id)(context, query, now)
                elapsed = max(0.0, (self.monotonic() - monotonic_start) * 1000)
                finished = self.now()
                if elapsed > check.timeout_ms:
                    status, code = CheckStatus.UNKNOWN, "local_check_budget_exceeded"
                    details = {"completed_within_budget": False}
                    token = fingerprint(details)
                reference = "diagnostic:" + check.check_id + ":" + fingerprint([query.document(), scope.document()])
                results.append(CheckResult(check, status, code, started, finished, elapsed, encode(details),
                                           (DiagnosticEvidence(reference, scope, token),)))
            # A state digest excludes observation clocks and global counters that
            # advance for unrelated users, group bindings, memories or reminders.
            # Anchor it on this caller's exact binding, role revision, tier and
            # grants on the checked scopes plus every check's own evidence tokens.
            tier_row = self.runtime.read_one(
                "SELECT tier, revision FROM subject_access_tiers WHERE subject_kind = ? AND subject_id = ?",
                (context.actor.subject.kind.value, context.actor.subject.subject_id),
            )
            actor_tier = (tier_row["tier"], tier_row["revision"]) if tier_row else ("standard", 0)
            checked_scopes = {e.scope for r in results for e in r.evidence}
            actor_grants = sorted(
                (grant.grant_id, grant.scope.key, grant.capability, grant.state.value,
                 grant.valid_from.isoformat(), grant.expires_at.isoformat() if grant.expires_at else None)
                for grant in self.identity.grants_for_subject(context.actor.subject)
                if grant.scope in checked_scopes
            )
            token = fingerprint([
                context.key,
                context.actor.binding_id,
                context.actor.binding_revision,
                context.role_binding.revision,
                actor_tier,
                actor_grants,
                [(r.check.check_id, r.check.version, r.status.value, r.code, r.details_json,
                  [asdict(e) for e in r.evidence]) for r in results],
            ])
        return tuple(results), token

    def _remember(self, report: DiagnosticReport) -> DiagnosticReport:
        for key in tuple(self._reports):
            if self._reports[key].expires_at <= self.now():
                del self._reports[key]
        self._reports[report.report_id] = report
        while len(self._reports) > 32:
            self._reports.popitem(last=False)
        return report

    def inspect(self, context: DiagnosticContext, query: DiagnosticQuery) -> Outcome[DiagnosticReport]:
        try:
            created = self.now()
            results, token = self.collect(context, query)
            findings = tuple(Finding(result.code, tuple(e.reference for e in result.evidence), (),
                                    "unknown" if result.status is CheckStatus.UNKNOWN else "high",
                                    "仅对记录中的本地状态作出判断，不推断未观测的运行效果。") for result in results)
            report = DiagnosticReport(uuid4().hex, query.mode, context.key, query, results, findings, "",
                        tuple(r.code for r in results if r.status is CheckStatus.UNKNOWN),
                        ("根据缺失的证据选择下一项检查；本次没有启动网络、设备或模型探测。",),
                        created, created + timedelta(seconds=min(r.check.freshness_seconds for r in results)), token)
            return Outcome.success(self._remember(report))
        except (DiagnosticError, ConfigurationError) as error:
            return Outcome.failed(error.code, str(error))

    def explain(self, context: DiagnosticContext, topic: str) -> Outcome[DiagnosticReport]:
        try:
            created = self.current(context, private=False)
            if topic not in EXPLANATIONS:
                raise DiagnosticError("unknown_explanation_topic", "no registered explanation for this topic")
            report = DiagnosticReport(uuid4().hex, DiagnosticMode.EXPLAIN, context.key, None, (), (),
                        EXPLANATIONS[topic], ("这是设计说明，本次未检查配置或运行状态。",), (), created,
                        created + timedelta(minutes=1), fingerprint([topic, EXPLANATIONS[topic]]))
            return Outcome.success(self._remember(report))
        except DiagnosticError as error:
            return Outcome.failed(error.code, str(error))

    def validated(self, context: DiagnosticContext, report_id: str) -> Outcome[DiagnosticReport]:
        try:
            report = self._reports.get(report_id)
            if report is None or report.origin_key != context.key:
                raise DiagnosticError("diagnostic_report_unavailable", "original report is unavailable in this context")
            now = self.current(context, private=report.mode is not DiagnosticMode.EXPLAIN)
            if not report.created_at <= now < report.expires_at:
                raise DiagnosticError("diagnostic_report_expired", "run a new check instead of reviving old evidence")
            if report.query is not None and self.collect(context, report.query)[1] != report.basis_token:
                raise DiagnosticError("diagnostic_evidence_changed", "the original report's evidence or permissions changed")
            return Outcome.success(report)
        except (DiagnosticError, ConfigurationError) as error:
            return Outcome.failed(error.code, str(error))

    def validate_snapshot(self, context: DiagnosticContext, document: dict, *,
                          query: DiagnosticQuery | None = None, topic: str | None = None):
        """Recheck an owner-stored report after restart; never replace its observed contents."""
        try:
            now = self.current(context, private=document["mode"] != DiagnosticMode.EXPLAIN.value)
            if document["origin_key"] != context.key:
                raise DiagnosticError("diagnostic_context_mismatch", "report belongs to another original context")
            if not datetime.fromisoformat(document["created_at"]) <= now < datetime.fromisoformat(document["expires_at"]):
                raise DiagnosticError("diagnostic_report_expired", "the original report expired")
            if query is not None:
                token = self.collect(context, query)[1]
            elif topic in EXPLANATIONS:
                token = fingerprint([topic, EXPLANATIONS[topic]])
            else:
                raise DiagnosticError("diagnostic_report_unavailable", "original report basis is unavailable")
            if token != document["basis_token"]:
                raise DiagnosticError("diagnostic_evidence_changed", "the original report basis changed")
            return Outcome.success(True)
        except (DiagnosticError, ConfigurationError) as error:
            return Outcome.failed(error.code, str(error))
