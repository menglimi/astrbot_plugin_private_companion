"""Atomic daily reservations, receipt-based settlement and bounded reclamation.

This is a trusted owner API. Public reads go through the gateway's original-task
authorization; model tools cannot release reservations or submit usage receipts.
A call whose provider never returns usage keeps its ceiling only until the owning
task clearly cannot still be running; then the hold returns to the original accounts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
from logging import getLogger
import math

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import Lane, ProviderBinding, TaskDeferred, TaskError, digest, encode, stamp
from companion_v2.execution.runner import TaskRun
from companion_v2.execution.service import TaskSupervisor
from companion_v2.foundation.refs import normalize_utc
from companion_v2.storage.runtime import StorageRuntime

from .models import (Amount, BudgetPolicy, CacheSignature, ProviderResponse, RELEASE_EXPIRED,
                     RELEASE_RECEIPT_PREFIX, RELEASE_WITHOUT_USAGE, is_released, released_receipt)
from .failures import ModelFailure
from .usage import MAIN_CHAIN_KIND


logger = getLogger(__name__)

# A reservation is only reclaimed once the task that owns it could not still be
# running: twice its execution timeout, never less than five minutes. A call that
# finishes without a usage receipt stays counted in ``held_tokens`` for that grace
# window (admission stays conservative), then its ceiling is returned to the
# account with a durable marker instead of being held forever.
RECLAIM_MIN_SECONDS = 300.0
RECLAIM_TIMEOUT_MULTIPLIER = 2.0
RECLAIM_BATCH = 64
RECLAIM_AUDIT_KEY = "model.budget.reclaim.v1"


# Optional background work that may be deferred once the day's soft ceiling is reached.
# Simulated life, journal writing, reflection and state maintenance ignore the soft
# gate but still use the plugin's hard accounts. Native dialogue is observed separately.
DEFAULT_SOFT_DEFER_KINDS = frozenset({
    "calendar.plan",
    "role.growth.progress",
    "conversation.group.review",
})

# The daily soft ceiling is an installation/role setting, matching V1's
# `enable_daily_token_soft_limit` + `daily_token_soft_limit`. It deliberately has no
# window layer: a single conversation must not be able to raise or lower the day's
# optional-work budget for everyone.
GROUP = "model.budget"
OWNER = "configuration"
SOFT_FIELDS = (
    FieldDefinition(GROUP + ".enable_daily_token_soft_limit", GROUP, GROUP, ValueKind.BOOLEAN, False,
                    layers=(ConfigLayer.INSTALLATION, ConfigLayer.ROLE),
                    apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER),
    FieldDefinition(GROUP + ".daily_token_soft_limit", GROUP, GROUP, ValueKind.INTEGER, 800_000,
                    layers=(ConfigLayer.INSTALLATION, ConfigLayer.ROLE),
                    apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER, minimum=0, maximum=10**12),
)


def configuration_fields():
    return SOFT_FIELDS


def effective_soft(runtime, context, fallback=None):
    """Resolve each field independently; the installation switch is the master gate.

    A legacy role switch can narrow an enabled installation, but cannot turn its
    budget back on. Missing stored fields inherit the host default independently.
    """
    role = context.role_binding.role
    target = ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE, role,
                          bot_id=context.scope.bot_id)
    owner = SparseConfigurationOwner(runtime)
    snapshot = owner.read(target, SOFT_FIELDS)
    installation = owner.read(ConfigTarget(context.scope.installation_id, ConfigLayer.INSTALLATION), SOFT_FIELDS)
    enabled = snapshot.fields[GROUP + ".enable_daily_token_soft_limit"]
    tokens = snapshot.fields[GROUP + ".daily_token_soft_limit"]
    master = installation.fields[GROUP + ".enable_daily_token_soft_limit"]
    defaults = fallback if fallback is not None else BudgetSoftPolicy(tokens=SOFT_FIELDS[1].default)
    global_enabled = master.value if master.source is not None else defaults.enabled
    role_enabled = enabled.value if enabled.source is not None else global_enabled
    token_limit = tokens.value if tokens.source is not None else defaults.tokens
    if type(global_enabled) is not bool or type(role_enabled) is not bool or type(token_limit) is not int:
        raise TaskError("model_budget_configuration", "the daily soft ceiling settings are invalid")
    return BudgetSoftPolicy(enabled=global_enabled and role_enabled, tokens=token_limit, kinds=defaults.kinds)


@dataclass(frozen=True)
class BudgetSoftPolicy:
    """One installation-wide daily soft ceiling for optional background model work."""

    enabled: bool = False
    tokens: int = 0
    kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("a soft budget policy needs an explicit switch")
        if type(self.tokens) is not int or not 0 <= self.tokens <= 10**12:
            raise ValueError("the soft ceiling must be bounded integer tokens")
        if type(self.kinds) is not tuple or any(type(kind) is not str or not kind.strip() for kind in self.kinds):
            raise ValueError("deferred task kinds must be explicit identifiers")

    def is_deferrable(self, kind: str) -> bool:
        """Background work only: the caller decides the lane, this decides the kind."""
        selected = self.kinds or tuple(sorted(DEFAULT_SOFT_DEFER_KINDS))
        return kind in selected


class BudgetDeferral(TaskDeferred):
    """An original reservation could not start within its daily account."""

    def __init__(self, code, *, account_key, period, amount):
        retry_at = datetime.fromisoformat(period + "T00:00:00+00:00") + timedelta(days=1)
        super().__init__(code, "plugin model admission is waiting for its configured local daily budget", retry_at=retry_at)
        self.account_key, self.period, self.amount = account_key, period, amount


DEFERRAL_PREFIX = "model.budget.deferral.v1:"


class BudgetLedger:
    def __init__(self, runtime: StorageRuntime, policy: BudgetPolicy, soft: BudgetSoftPolicy | None = None):
        self.runtime = runtime
        self.policy = policy
        # Toggling admission never changes account identity or drops usage history.
        self.policy_hash = digest({key: value for key, value in policy.document().items() if key != "enabled"})
        self.soft = soft if soft is not None else BudgetSoftPolicy()

    def _accounts(self, run: TaskRun, period: str):
        background = run.supervisor.registrations[run.task.spec.kind].definition.lane is Lane.BACKGROUND
        return self._context_accounts(run.task.spec.context, period, background=background)

    def _context_accounts(self, context, period, *, background):
        scope = context.scope
        installation = [scope.installation_id]
        bot = installation + [scope.bot_id]
        role = bot + [asdict(context.role_binding.role)]
        layers = [("installation", installation), ("bot", bot), ("role", role),
                  ("window", [scope.document()])]
        if background:
            layers.append(("background", installation))
        return tuple((digest([layer, address, period]), getattr(self.policy, layer)) for layer, address in layers)

    @staticmethod
    def _is_main_chain(run: TaskRun) -> bool:
        """Identify the exact foreground reply task, rather than its action."""
        return run.task.spec.kind == MAIN_CHAIN_KIND

    def plugin_account_usage(self, account_key: str):
        """Read charged work directly, including accounts from before reply exemption.

        Released reservations keep their call row (and its unknown usage) but no
        longer count as held quota.
        """
        return self.runtime.read_one("""SELECT
                COALESCE(SUM(CASE WHEN c.actual_tokens IS NOT NULL THEN c.actual_tokens ELSE 0 END), 0) AS spent_tokens,
                COALESCE(SUM(CASE WHEN c.actual_cost IS NOT NULL THEN c.actual_cost ELSE 0 END), 0) AS spent_cost,
                COALESCE(SUM(CASE WHEN c.actual_tokens IS NULL AND COALESCE(c.receipt_ref, '') NOT LIKE ?
                    THEN c.reserved_tokens ELSE 0 END), 0) AS held_tokens,
                COALESCE(SUM(CASE WHEN c.actual_tokens IS NULL AND COALESCE(c.receipt_ref, '') NOT LIKE ?
                    THEN c.reserved_cost ELSE 0 END), 0) AS held_cost
            FROM model_calls c
            JOIN model_call_accounts ca ON ca.call_id = c.call_id
            JOIN execution_tasks t ON t.task_id = c.task_id
            WHERE ca.account_key = ? AND t.kind != ?""",
            (RELEASE_RECEIPT_PREFIX + "%", RELEASE_RECEIPT_PREFIX + "%", account_key, MAIN_CHAIN_KIND))

    def released_usage(self, account_key: str):
        """Report holds that were returned without ever receiving a usage receipt."""
        return self.runtime.read_one("""SELECT COUNT(*) AS calls,
                COALESCE(SUM(c.reserved_tokens), 0) AS tokens,
                COALESCE(SUM(c.reserved_cost), 0) AS cost_microusd
            FROM model_calls c JOIN model_call_accounts ca ON ca.call_id = c.call_id
            JOIN execution_tasks t ON t.task_id = c.task_id
            WHERE ca.account_key = ? AND t.kind != ? AND c.actual_tokens IS NULL
                AND COALESCE(c.receipt_ref, '') LIKE ?""",
            (account_key, MAIN_CHAIN_KIND, RELEASE_RECEIPT_PREFIX + "%"))

    def reservation_sources(self, account_key):
        """Explain held quota separately from provider-confirmed consumption."""
        return [dict(row) for row in self.runtime.read_all("""SELECT t.kind, c.state,
                f.code AS failure, COUNT(*) AS calls,
                SUM(c.reserved_tokens) AS held_tokens
            FROM model_calls c JOIN model_call_accounts ca USING(call_id)
            JOIN execution_tasks t USING(task_id)
            LEFT JOIN model_call_failures f USING(call_id)
            WHERE ca.account_key = ? AND t.kind != ? AND c.actual_tokens IS NULL
                AND COALESCE(c.receipt_ref, '') NOT LIKE ?
            GROUP BY t.kind, c.state, f.code ORDER BY held_tokens DESC, t.kind, c.state""",
            (account_key, MAIN_CHAIN_KIND, RELEASE_RECEIPT_PREFIX + "%"))]


    def _soft_refusal(self, run: TaskRun, period: str, amount: Amount):
        """The configured account that defers this call, or None.

        Only the installation and role accounts of the reservation's local day are
        read, so no window can widen the ceiling, and a deferred call writes no
        reservation at all. A ceiling below what one call would reserve defers
        immediately, which is how a mixed batch keeps its optional share bounded.

        The effective settings are read here rather than pinned, because deferral
        only decides whether optional work starts: it never mutates a pinned daily
        account, so changing it mid-day cannot invalidate an active ceiling.
        """
        settings = effective_soft(self.runtime, run.task.spec.context, self.soft)
        if settings is None or not settings.enabled or settings.tokens <= 0:
            return None
        definition = run.supervisor.registrations[run.task.spec.kind].definition
        kind = run.task.spec.kind
        if definition.lane is not Lane.BACKGROUND or not settings.is_deferrable(kind):
            return None
        context = run.task.spec.context
        scope = context.scope
        keys = (digest(["installation", [scope.installation_id], period]),
                digest(["role", [scope.installation_id, scope.bot_id, asdict(context.role_binding.role)], period]))
        # The installation account already includes every role's calls. Adding
        # the role account again charged the current role twice.
        usage = self.plugin_account_usage(keys[0])
        used = usage["spent_tokens"] + usage["held_tokens"]
        if used + amount.tokens > settings.tokens:
            return keys[1]
        return None

    def record_deferral(self, run, error: BudgetDeferral):
        """Persist after the rejected reservation transaction has rolled back."""
        with self.runtime.transaction() as connection:
            run.validate()
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (self._deferral_key(run.task.spec.context, run.task.spec.kind), encode({
                    "account_key": error.account_key,
                    "code": error.code, "period": error.period, "policy_hash": self.policy_hash,
                    "retry_at": stamp(error.retry_at), "tokens": error.amount.tokens,
                    "cost": error.amount.cost_microusd})))

    @staticmethod
    def _deferral_key(context, kind):
        scope = context.scope
        return DEFERRAL_PREFIX + digest([scope.installation_id, scope.bot_id,
                                        context.role_binding.role, kind])

    def background_deferral(self, context, kind, *, at):
        """Recheck saved admission refusals without creating a task or reservation.

        A late usage receipt, a soft-setting change or a new budget day can clear
        the wait. This never releases unknown calls or replays an original task.
        """
        period = stamp(at)[:10]
        accounts = self._context_accounts(context, period, background=True)
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                    (self._deferral_key(context, kind),))
        if row is None:
            return None
        saved = json.loads(row["value"])
        if saved["period"] != period or saved["policy_hash"] != self.policy_hash:
            return None
        if saved["code"] == "model_budget_exhausted" and self.policy.enabled:
            ceiling = dict(accounts).get(saved["account_key"])
            if ceiling is None:
                return None
            key = saved["account_key"]
            usage = self.plugin_account_usage(key)
            if (usage["spent_tokens"] + usage["held_tokens"] + saved["tokens"] > ceiling.tokens
                    or usage["spent_cost"] + usage["held_cost"] + saved["cost"] > ceiling.cost_microusd):
                return {"state": "deferred", "code": saved["code"], "retry_at": saved["retry_at"]}
        soft = effective_soft(self.runtime, context, self.soft)
        if (saved["code"] == "model_budget_soft_deferred" and soft.enabled
                and soft.tokens > 0 and soft.is_deferrable(kind)):
            usage = self.plugin_account_usage(accounts[0][0])
            if usage["spent_tokens"] + usage["held_tokens"] + saved["tokens"] > soft.tokens:
                return {"state": "deferred", "code": saved["code"], "retry_at": saved["retry_at"]}
        return None

    def reserve(self, run: TaskRun, *, call_id: str, position: int,
                provider: ProviderBinding, amount: Amount):
        if self.runtime is not run.supervisor.runtime or not isinstance(amount, Amount):
            raise ValueError("model reservation must share the task runtime and use explicit amounts")
        with self.runtime.transaction() as connection:
            run.validate()
            plan = connection.execute("SELECT created_at FROM model_requests WHERE task_id = ?", (run.task.task_id,)).fetchone()
            if plan is None:
                raise TaskError("model_plan_missing", "reserve only against the original adopted plan")
            if connection.execute("SELECT 1 FROM model_calls WHERE task_id = ? AND position = ?",
                                  (run.task.task_id, position)).fetchone():
                raise TaskError("model_call_already_reserved", "an original call cannot be repeated")
            period = datetime.fromisoformat(plan["created_at"]).date().isoformat()
            main_chain = self._is_main_chain(run)
            background = run.supervisor.registrations[run.task.spec.kind].definition.lane is Lane.BACKGROUND
            # Keep role account links as a reporting/ownership index. Main-chain
            # calls use those links for maintenance views, but never charge them.
            accounts = self._accounts(run, period)
            # Every call gets a scope index so maintenance can enforce role
            # visibility. Only plugin calls enter the budget checks below.
            for key, ceiling in accounts:
                connection.execute("INSERT OR IGNORE INTO model_budget_accounts VALUES (?, ?, ?, ?, 0, 0, 0, 0)",
                                   (key, self.policy_hash, ceiling.tokens, ceiling.cost_microusd))
                if not main_chain and self.policy.enabled:
                    # Hard ceilings are checked first: software deferral decides
                    # whether optional work should run, never whether the body may answer.
                    account = connection.execute("SELECT * FROM model_budget_accounts WHERE account_key = ?", (key,)).fetchone()
                    if account["policy_hash"] != self.policy_hash:
                        raise TaskError("model_budget_policy_changed", "active daily budget policy requires an explicit migration")
                    usage = self.plugin_account_usage(key)
                    spent_tokens, held_tokens = usage["spent_tokens"], usage["held_tokens"]
                    spent_cost, held_cost = usage["spent_cost"], usage["held_cost"]
                    if (spent_tokens + held_tokens + amount.tokens > account["token_limit"]
                            or spent_cost + held_cost + amount.cost_microusd > account["cost_limit"]):
                        if background:
                            raise BudgetDeferral("model_budget_exhausted", account_key=key,
                                                 period=period, amount=amount)
                        raise TaskError("model_budget_exhausted", "model reservation exceeds an original scope budget")
            soft_key = None if main_chain else self._soft_refusal(run, period, amount)
            if soft_key is not None:
                raise BudgetDeferral("model_budget_soft_deferred", account_key=soft_key,
                                     period=period, amount=amount)
            connection.execute("""INSERT INTO model_calls
                (call_id, task_id, attempt_id, position, state, provider_json, reserved_tokens, reserved_cost)
                VALUES (?, ?, ?, ?, 'reserved', ?, ?, ?)""",
                (call_id, run.task.task_id, run.claim.attempt_id, position, encode(provider), amount.tokens, amount.cost_microusd))
            for key, _ in accounts:
                if not main_chain:
                    connection.execute("UPDATE model_budget_accounts SET held_tokens = held_tokens + ?, held_cost = held_cost + ? WHERE account_key = ?",
                                       (amount.tokens, amount.cost_microusd, key))
                connection.execute("INSERT INTO model_call_accounts VALUES (?, ?)", (call_id, key))

    def dispatch(self, run: TaskRun, call_id: str):
        with self.runtime.transaction() as connection:
            run.validate()
            changed = connection.execute("""UPDATE model_calls SET state = 'dispatched', started_at = ?
                WHERE call_id = ? AND attempt_id = ? AND state = 'reserved'""",
                (stamp(run.supervisor.now()), call_id, run.claim.attempt_id))
            if changed.rowcount != 1:
                raise TaskError("model_dispatch_conflict", "only an unstarted original reservation may dispatch")

    def record_cache_signature(self, run: TaskRun, call_id: str, provider: ProviderBinding,
                               signature: CacheSignature):
        if (self.runtime is not run.supervisor.runtime or not isinstance(signature, CacheSignature)):
            raise ValueError("cache signature must belong to the current model task")
        signature_json = encode(signature.to_record())
        with self.runtime.transaction() as connection:
            run.validate()
            row = connection.execute("""SELECT state, provider_json FROM model_calls
                WHERE call_id = ? AND task_id = ? AND attempt_id = ?""",
                (call_id, run.task.task_id, run.claim.attempt_id)).fetchone()
            if row is None or row["state"] != "dispatched" or row["provider_json"] != encode(provider):
                raise TaskError("model_cache_signature_mismatch", "cache signature belongs to no active provider call")
            existing = connection.execute("SELECT signature_json FROM model_call_cache_signatures WHERE call_id = ?",
                                          (call_id,)).fetchone()
            if existing is not None:
                if existing["signature_json"] != signature_json:
                    raise TaskError("model_cache_signature_conflict", "cache signature is immutable for an original call")
                return
            connection.execute("INSERT INTO model_call_cache_signatures(call_id, signature_json) VALUES (?, ?)",
                               (call_id, signature_json))

    def settle(self, call_id: str, provider: ProviderBinding, response: ProviderResponse, *,
               observed_at: datetime, latency_ms: float | None = None):
        if latency_ms is not None and (type(latency_ms) not in (int, float)
                                      or not math.isfinite(latency_ms) or latency_ms < 0):
            raise ValueError("provider latency must be measured and finite")
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT * FROM model_calls WHERE call_id = ?", (call_id,)).fetchone()
            if row is None or row["provider_json"] != encode(provider):
                raise TaskError("model_receipt_mismatch", "usage receipt belongs to another provider binding")
            if row["state"] == "reserved" and response.status != "not_started":
                raise TaskError("model_receipt_mismatch", "undispatched work cannot report provider execution")
            if ((row["state"] == "not_started" and response.status == "generated")
                    or (row["state"] in ("generated", "discarded") and response.status == "not_started")):
                raise TaskError("model_processing_conflict", "provider processing receipts contradict each other")
            usage = response.usage
            released = is_released(row["receipt_ref"])
            if row["actual_tokens"] is not None:
                if (usage is not None and (row["actual_tokens"], row["actual_cost"], row["receipt_ref"]) !=
                        (usage.tokens, usage.cost_microusd, response.receipt_ref)):
                    raise TaskError("model_usage_conflict", "settled usage is immutable")
                if usage is not None and any(
                        row[name] is not None and getattr(usage, name) is not None
                        and row[name] != getattr(usage, name)
                        for name in ("input_tokens", "cached_input_tokens", "cache_creation_tokens", "output_tokens")):
                    raise TaskError("model_usage_conflict", "settled usage details are immutable")
            elif usage is not None:
                task = connection.execute("SELECT kind FROM execution_tasks WHERE task_id = ?", (row["task_id"],)).fetchone()
                accounts = connection.execute("SELECT account_key FROM model_call_accounts WHERE call_id = ?", (call_id,)).fetchall()
                if task is None or task["kind"] != MAIN_CHAIN_KIND:
                    for account in accounts:
                        if released:
                            # The expired-reservation sweep already returned this
                            # hold; a late receipt may still charge confirmed usage.
                            connection.execute("""UPDATE model_budget_accounts SET spent_tokens = spent_tokens + ?,
                                spent_cost = spent_cost + ? WHERE account_key = ?""",
                                (usage.tokens, usage.cost_microusd, account["account_key"]))
                        else:
                            connection.execute("""UPDATE model_budget_accounts SET held_tokens = held_tokens - ?,
                                held_cost = held_cost - ?, spent_tokens = spent_tokens + ?, spent_cost = spent_cost + ? WHERE account_key = ?""",
                                (row["reserved_tokens"], row["reserved_cost"], usage.tokens, usage.cost_microusd, account["account_key"]))
            # A late accounting receipt cannot resurrect a discarded draft or replay a call.
            state = row["state"] if row["state"] in ("generated", "discarded", "not_started") else response.status
            connection.execute("""UPDATE model_calls SET state = ?, actual_tokens = ?, actual_cost = ?,
                input_tokens = COALESCE(input_tokens, ?), cached_input_tokens = COALESCE(cached_input_tokens, ?),
                cache_creation_tokens = COALESCE(cache_creation_tokens, ?), output_tokens = COALESCE(output_tokens, ?),
                receipt_ref = ?, finished_at = COALESCE(finished_at, ?), latency_ms = COALESCE(latency_ms, ?)
                WHERE call_id = ?""", (state, usage.tokens if usage else row["actual_tokens"],
                usage.cost_microusd if usage else row["actual_cost"],
                usage.input_tokens if usage else None, usage.cached_input_tokens if usage else None,
                usage.cache_creation_tokens if usage else None, usage.output_tokens if usage else None,
                (row["receipt_ref"] if row["actual_tokens"] is not None
                 else response.receipt_ref if response.receipt_ref is not None else row["receipt_ref"]),
                stamp(observed_at), latency_ms, call_id))
            if response.failure is not None:
                self._record_failure(connection, call_id, response.failure, observed_at)
            if response.diagnostics is not None:
                # Preserve the first adapter observation; accounting reconciliation
                # must not replace it or make an unknown response replayable.
                connection.execute("""INSERT INTO model_call_diagnostics(call_id, summary_json, observed_at)
                    VALUES (?, ?, ?) ON CONFLICT(call_id) DO NOTHING""",
                    (call_id, encode(response.diagnostics.to_record()), stamp(observed_at)))
            return row["actual_tokens"] is not None or usage is not None

    @staticmethod
    def _record_failure(connection, call_id: str, failure: ModelFailure, observed_at: datetime):
        # Keep the first observed classification, including across late accounting receipts.
        connection.execute("""INSERT INTO model_call_failures(call_id, code, observed_at) VALUES (?, ?, ?)
            ON CONFLICT(call_id) DO NOTHING""", (call_id, failure.value, stamp(observed_at)))

    def discard(self, call_id: str, *, failure: ModelFailure | None = None, observed_at: datetime | None = None):
        if failure is not None and (not isinstance(failure, ModelFailure) or observed_at is None):
            raise ValueError("discard diagnostics require a fixed classification and observation time")
        with self.runtime.transaction() as connection:
            changed = connection.execute("UPDATE model_calls SET state = 'discarded' WHERE call_id = ? AND state = 'generated'", (call_id,))
            if changed.rowcount and failure is not None:
                self._record_failure(connection, call_id, failure, observed_at)

    def reconcile_usage(self, call_id: str, provider: ProviderBinding, usage: Amount, *, receipt_ref: str, observed_at: datetime):
        """Trusted adapter accounting only, including after revocation or restart."""
        self.settle(call_id, provider, ProviderResponse("unknown", usage=usage, receipt_ref=receipt_ref), observed_at=observed_at)

    def release(self, call_id: str, *, reason: str = RELEASE_WITHOUT_USAGE,
                observed_at: datetime) -> Amount:
        """Return one unconfirmed reservation to its accounts, exactly once.

        This is a trusted owner API. It never guesses usage and never resurrects a
        discarded draft: the call row keeps its state and unknown usage, only the
        held quota goes back. A later trusted receipt may still charge the account.
        """
        if reason not in (RELEASE_WITHOUT_USAGE, RELEASE_EXPIRED):
            raise ValueError("a reservation release needs a fixed reason")
        with self.runtime.transaction() as connection:
            released = self._release_locked(connection, call_id, reason, observed_at)
        if released.tokens or released.cost_microusd:
            logger.warning("[CompanionV2] 模型预留已释放 call=%s reason=%s tokens=%d cost_microusd=%d",
                           call_id, reason, released.tokens, released.cost_microusd)
        return released

    def _release_locked(self, connection, call_id: str, reason: str, observed_at: datetime) -> Amount:
        """Apply one release inside the caller's write transaction; idempotent."""
        row = connection.execute("SELECT * FROM model_calls WHERE call_id = ?", (call_id,)).fetchone()
        if row is None:
            raise TaskError("model_release_missing", "only an existing original reservation can be released")
        if row["actual_tokens"] is not None or is_released(row["receipt_ref"]):
            return Amount(0, 0)
        changed = connection.execute("""UPDATE model_calls SET receipt_ref = ?, finished_at = COALESCE(finished_at, ?)
            WHERE call_id = ? AND actual_tokens IS NULL AND COALESCE(receipt_ref, '') NOT LIKE ?""",
            (released_receipt(reason, row["receipt_ref"]), stamp(observed_at), call_id,
             RELEASE_RECEIPT_PREFIX + "%"))
        if changed.rowcount != 1:
            return Amount(0, 0)
        released = Amount(row["reserved_tokens"], row["reserved_cost"])
        task = connection.execute("SELECT kind FROM execution_tasks WHERE task_id = ?", (row["task_id"],)).fetchone()
        if task is not None and task["kind"] == MAIN_CHAIN_KIND:
            # The main chain keeps its receipt links but never added to held quota.
            return Amount(0, 0)
        for account in connection.execute("SELECT account_key FROM model_call_accounts WHERE call_id = ?",
                                          (call_id,)).fetchall():
            held = connection.execute("SELECT held_tokens, held_cost FROM model_budget_accounts WHERE account_key = ?",
                                      (account["account_key"],)).fetchone()
            if held is None:
                continue
            remaining_tokens = max(0, held["held_tokens"] - row["reserved_tokens"])
            remaining_cost = max(0, held["held_cost"] - row["reserved_cost"])
            if (remaining_tokens, remaining_cost) != (held["held_tokens"], held["held_cost"]):
                connection.execute("UPDATE model_budget_accounts SET held_tokens = ?, held_cost = ? WHERE account_key = ?",
                                   (remaining_tokens, remaining_cost, account["account_key"]))
        return released

    def reclaim_expired(self, supervisor: TaskSupervisor, *, limit: int = RECLAIM_BATCH):
        """Return reservations that clearly outlived any task that could still use them.

        Runs at startup and from host maintenance. Only holds older than twice the
        owning task's timeout (never less than five minutes) are reclaimed, so a
        live call is never touched. Every reclaimed hold leaves a durable marker in
        ``receipt_ref`` plus a bounded audit record and a log line.
        """
        if supervisor.runtime is not self.runtime:
            raise ValueError("expired reservation reclamation requires the current task runtime")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("reservation reclamation is bounded")
        now = normalize_utc(supervisor.now(), field="model budget clock")
        cutoff = stamp(now - timedelta(seconds=RECLAIM_MIN_SECONDS))
        released_calls = released_tokens = released_cost = 0
        with self.runtime.transaction() as connection:
            controller = connection.execute("SELECT generation FROM task_supervisor WHERE singleton = 1").fetchone()
            if controller is None or controller["generation"] != supervisor.generation:
                raise TaskError("supervisor_fenced", "only the current controller can reclaim model reservations")
            rows = connection.execute("""SELECT c.call_id, c.state, c.started_at, c.finished_at,
                    t.kind AS kind, r.created_at AS requested_at
                FROM model_calls c
                JOIN execution_tasks t ON t.task_id = c.task_id
                JOIN model_requests r ON r.task_id = c.task_id
                WHERE c.actual_tokens IS NULL
                    AND COALESCE(c.receipt_ref, '') NOT LIKE ?
                    AND COALESCE(c.finished_at, c.started_at, r.created_at) <= ?
                ORDER BY COALESCE(c.finished_at, c.started_at, r.created_at)
                LIMIT ?""", (RELEASE_RECEIPT_PREFIX + "%", cutoff, limit)).fetchall()
            for row in rows:
                registration = supervisor.registrations.get(row["kind"])
                timeout_seconds = (registration.definition.timeout_seconds
                                   if registration is not None else 0)
                threshold = max(RECLAIM_MIN_SECONDS, timeout_seconds * RECLAIM_TIMEOUT_MULTIPLIER)
                anchor = datetime.fromisoformat(row["finished_at"] or row["started_at"] or row["requested_at"])
                if (now - anchor).total_seconds() < threshold:
                    continue
                amount = self._release_locked(connection, row["call_id"], RELEASE_EXPIRED, now)
                if not (amount.tokens or amount.cost_microusd):
                    continue
                released_calls += 1
                released_tokens += amount.tokens
                released_cost += amount.cost_microusd
                logger.debug("[CompanionV2] 过期模型预留已回收 call=%s kind=%s state=%s tokens=%d",
                             row["call_id"], row["kind"], row["state"], amount.tokens)
            report = {"at": stamp(now), "released_calls": released_calls,
                      "released_tokens": released_tokens, "released_cost_microusd": released_cost}
            if released_calls:
                connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                    (RECLAIM_AUDIT_KEY, encode(report)))
        if released_calls:
            logger.warning("[CompanionV2] 过期模型预留已回收 calls=%d tokens=%d cost_microusd=%d",
                           released_calls, released_tokens, released_cost)
        return report

    def reclaim_audit(self):
        """Read the last non-empty reclamation record without exposing call details."""
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (RECLAIM_AUDIT_KEY,))
        if row is None:
            return None
        try:
            value = json.loads(row["value"])
        except (TypeError, ValueError):
            return None
        return value if type(value) is dict else None

    def recover(self, supervisor: TaskSupervisor) -> int:
        if supervisor.runtime is not self.runtime:
            raise ValueError("recovery requires the current task runtime")
        with self.runtime.transaction() as connection:
            controller = connection.execute("SELECT generation FROM task_supervisor WHERE singleton = 1").fetchone()
            if controller is None or controller["generation"] != supervisor.generation:
                raise TaskError("supervisor_fenced", "only the current controller can recover model reservations")
            rows = connection.execute("""SELECT c.* FROM model_calls c JOIN execution_attempts a USING(attempt_id)
                WHERE a.generation < ? AND c.state IN ('reserved', 'dispatched')""", (supervisor.generation,)).fetchall()
            for row in rows:
                response = (ProviderResponse("not_started", usage=Amount(0, 0), receipt_ref="local:never-dispatched")
                            if row["state"] == "reserved" else ProviderResponse("unknown", failure=ModelFailure.RECOVERY))
                self.settle(row["call_id"], ProviderBinding(**json.loads(row["provider_json"])), response,
                            observed_at=supervisor.now())
            return len(rows)
