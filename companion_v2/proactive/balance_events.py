"""Opt-in, source-backed contact about the Bot service balance."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json

from companion_v2.conversation.messages import source_from_document
from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import ScopeKind, SourceRef, VersionRef, normalize_utc
from companion_v2.sources.governance import bind
from companion_v2.sources.models import SourceRecord

from .contact_models import ContactIntent
from .models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind
SOURCE_OWNER = "proactive.service_balance"
DISPLAY_AMOUNT_QUANTUM = Decimal("0.01")
RETRY = timedelta(minutes=5)
MAX_OBSERVATION_AGE = timedelta(minutes=5)
EVENT_WINDOW = timedelta(minutes=30)
MEANING = ("这是 Bot 所用服务在观测时间的经费状态，不是用户的欠款、付款义务或用户账户余额。"
           "可以自然地略过；不要催促充值、索取付款或凭状态推断服务即将中断。")


def display_amount(value):
    """Return a validated balance amount in the dashboard/message display form."""
    try:
        amount = value if type(value) is Decimal else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("balance display value is invalid") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("balance display value is invalid")
    return format(amount.quantize(DISPLAY_AMOUNT_QUANTUM, rounding=ROUND_HALF_UP), "f")


@dataclass(frozen=True)
class BalanceObservation:
    balance: Decimal
    total: Decimal | None
    observed_at: datetime
    unit: str | None = None
    used: Decimal | None = None
    plan_name: str | None = None
    extra: str | None = None

    def __post_init__(self):
        if (type(self.balance) is not Decimal or not self.balance.is_finite() or self.balance < 0
                or (self.total is not None and
                    (type(self.total) is not Decimal or not self.total.is_finite() or self.total < 0))):
            raise ValueError("balance observation requires finite non-negative values")
        if self.used is not None and (type(self.used) is not Decimal or not self.used.is_finite() or self.used < 0):
            raise ValueError("balance used amount must be finite and non-negative")
        for name, limit in (("unit", 24), ("plan_name", 256), ("extra", 256)):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or len(value) > limit
                                      or any(ord(char) < 32 for char in value)):
                raise ValueError("balance observation metadata is invalid")
        object.__setattr__(self, "observed_at", normalize_utc(self.observed_at, field="balance observation"))


def balance_level(observation, values):
    remaining = observation.balance
    percent = (remaining * 100 / observation.total) if observation.total else None
    critical = min(values["balance_critical_threshold"], values["balance_low_threshold"])
    critical_percent = min(values["balance_critical_percent_threshold"],
                           values["balance_low_percent_threshold"])
    if remaining <= critical or percent is not None and percent <= critical_percent:
        return "critical"
    if remaining <= values["balance_low_threshold"] or percent is not None and percent <= values["balance_low_percent_threshold"]:
        return "low"
    return None


class BalanceContactEvents:
    def __init__(self, contacts, port):
        self.contacts, self.port = contacts, port
        self.runtime = contacts.runtime
        self.retry_at = {}
        self.guard = bind(self.runtime, SOURCE_OWNER, self._validate_source)

    def close(self):
        self.guard.close()
        self.retry_at.clear()

    def update_port(self, port):
        self.port = port
        self.retry_at.clear()

    def _settings(self, context, preferences):
        values = preferences.values
        if (self.port is None or context.scope.kind is not ScopeKind.PRIVATE or not values["automatic_enabled"]
                or not values["balance_awareness_enabled"] or preferences.reason("environment")):
            return None
        names = ("enabled", "automatic_enabled", "balance_awareness_enabled",
                 "balance_include_amount_in_message", "balance_currency_label",
                 "balance_check_interval_minutes", "balance_message_cooldown_hours",
                 "balance_low_threshold", "balance_critical_threshold",
                 "balance_low_percent_threshold", "balance_critical_percent_threshold")
        return digest({**{name: values[name] for name in names},
                       "source": getattr(self.port, "source_fingerprint", None)})

    def _row(self, context):
        return self.runtime.read_one("SELECT * FROM proactive_balance_polls WHERE origin_key = ?", (context.key,))

    @staticmethod
    def _opportunity(context, event):
        return Opportunity(event["id"], context.scope,
            datetime.fromisoformat(event["opened_at"]), datetime.fromisoformat(event["expires_at"]))

    def _candidate(self, context, row, settings_hash, at):
        if row is None or row["settings_hash"] != settings_hash or row["event_json"] is None:
            return None
        event = json.loads(row["event_json"])
        opportunity = self._opportunity(context, event)
        if not opportunity.opened_at <= at < opportunity.expires_at:
            return None
        task_id = self.contacts.task_id(context, opportunity)
        if (self.runtime.read_one("SELECT 1 FROM execution_tasks WHERE task_id = ?", (task_id,))
                or self.runtime.read_one("SELECT 1 FROM proactive_contacts WHERE task_id = ?", (task_id,))):
            return None
        return opportunity, event

    def select(self, context, preferences, at):
        if self.port is None or self.retry_at.get(context.key, at) > at:
            return None
        settings_hash = self._settings(context, preferences)
        if settings_hash is None:
            return None
        row = self._row(context)
        candidate = self._candidate(context, row, settings_hash, at)
        if candidate is not None:
            return candidate[0], {"settings_hash": settings_hash}
        interval = timedelta(minutes=preferences.values["balance_check_interval_minutes"])
        if (row is not None and row["settings_hash"] == settings_hash
                and at < datetime.fromisoformat(row["checked_at"]) + interval):
            return None
        opportunity = Opportunity("balance-poll-" + digest([context.key, stamp(at)]),
                                  context.scope, at, at + EVENT_WINDOW)
        return opportunity, {"settings_hash": settings_hash}

    def _save(self, context, settings_hash, observation, preferences, at):
        level = balance_level(observation, preferences.values)
        previous = self._row(context)
        if previous is not None and previous["settings_hash"] != settings_hash:
            previous = None
        last_notice = previous["last_notice_at"] if previous is not None else None
        last_level = previous["last_notice_level"] if previous is not None else None
        event = None
        if level is None:
            last_notice, last_level = None, None
        else:
            cooldown = timedelta(hours=preferences.values["balance_message_cooldown_hours"])
            escalated = level == "critical" and last_level == "low"
            if last_notice is None or at >= datetime.fromisoformat(last_notice) + cooldown or escalated:
                event = {"id": "balance-" + digest([context.key, settings_hash, stamp(at)]),
                         "opened_at": stamp(at), "expires_at": stamp(at + EVENT_WINDOW),
                         "level": level, "settings_hash": settings_hash}
                if preferences.values["balance_include_amount_in_message"]:
                    event["amount"] = str(observation.balance)
                    event["currency"] = observation.unit or preferences.values["balance_currency_label"]
                last_notice, last_level = stamp(at), level
            elif previous is not None and previous["event_json"]:
                prior_event = json.loads(previous["event_json"])
                if (prior_event["level"] == level
                        and at < datetime.fromisoformat(prior_event["expires_at"])
                        and ("amount" not in prior_event or prior_event["amount"] == str(observation.balance))
                        and ("currency" not in prior_event or prior_event["currency"] ==
                             (observation.unit or preferences.values["balance_currency_label"]))):
                    event = prior_event
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO proactive_balance_polls
                (origin_key, settings_hash, checked_at, level, last_notice_at, last_notice_level, event_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(origin_key) DO UPDATE SET settings_hash=excluded.settings_hash,
                checked_at=excluded.checked_at, level=excluded.level,
                last_notice_at=excluded.last_notice_at, last_notice_level=excluded.last_notice_level,
                event_json=excluded.event_json""",
                (context.key, settings_hash, stamp(at), level or "normal", last_notice,
                 last_level, encode(event) if event is not None else None))

    def _record(self, context, event):
        opportunity = self._opportunity(context, event)
        body = {"event": "bot_service_balance_low", "level": event["level"],
                "observed_at": event["opened_at"], "meaning": MEANING}
        if "amount" in event:
            body.update(amount=display_amount(event["amount"]), currency=event["currency"])
        payload = encode({**body, "_receipt": {"context": json.loads(encode(context)),
                                             "event_id": event["id"], "settings_hash": event["settings_hash"]}})
        identifier = "contact-balance-source-" + digest([context.key, event["id"]])
        source = SourceRef(identifier, SOURCE_OWNER, context.scope, VersionRef(identifier, 1),
                           opportunity.opened_at, opportunity.expires_at)
        return SourceRecord(source, context.audience, digest(payload), payload)

    async def prepare(self, context, marker):
        if self.port is None or type(marker) is not dict or set(marker) != {"settings_hash"}:
            return None
        at = self.contacts.tasks.now()
        preferences = require(self.contacts.preferences.read(context, observed_at=at))
        settings_hash = self._settings(context, preferences)
        if settings_hash is None or settings_hash != marker["settings_hash"]:
            return None
        candidate = self._candidate(context, self._row(context), settings_hash, at)
        if candidate is None:
            port = self.port
            try:
                observation = await asyncio.wait_for(port.fetch(), timeout=12)
            except Exception:
                if self.port is port:
                    self.retry_at[context.key] = self.contacts.tasks.now() + RETRY
                return None
            at = self.contacts.tasks.now()
            preferences = require(self.contacts.preferences.read(context, observed_at=at))
            if self.port is not port or self._settings(context, preferences) != settings_hash:
                return None
            if (type(observation) is not BalanceObservation
                    or not timedelta(0) <= at - observation.observed_at <= MAX_OBSERVATION_AGE):
                self.retry_at[context.key] = at + RETRY
                return None
            self._save(context, settings_hash, observation, preferences, at)
            self.retry_at.pop(context.key, None)
            candidate = self._candidate(context, self._row(context), settings_hash, at)
            if candidate is None:
                return None
        opportunity, event = candidate
        record = self._record(context, event)
        require(self.contacts.sources.publish(record, observed_at=at))
        require(self.contacts.sources.validate_reference_for_audience(
            record.reference, recipient=context.actor.subject, observed_at=at))
        marker = {"event_id": event["id"], "settings_hash": settings_hash,
                  "source": json.loads(encode(record.reference))}
        thought = Thought("balance-thought-" + digest(marker), opportunity, ThoughtKind.CONVERSATION,
            "Bot 服务经费偏低；只有适合当前人格与关系时才自然提及，也可以跳过。",
            EvidenceSnapshot(context.scope, at, (record.reference,)), 1.0)
        return opportunity, (ContactIntent("environment", thought),), marker

    def _validate_source(self, record, at):
        try:
            receipt = json.loads(record.payload)["_receipt"]
            context = TaskContext.restore(receipt["context"])
            preferences = require(self.contacts.preferences.read(context, observed_at=at))
            row = self._row(context)
            if (self._settings(context, preferences) != receipt["settings_hash"] or row is None
                    or row["settings_hash"] != receipt["settings_hash"] or row["event_json"] is None):
                raise ValueError("balance observation is no longer current")
            event = json.loads(row["event_json"])
            opportunity = self._opportunity(context, event)
            if (event["id"] != receipt["event_id"] or not opportunity.opened_at <= at < opportunity.expires_at
                    or record != self._record(context, event) or record.content_hash != digest(record.payload)):
                raise ValueError("balance observation changed")
        except TaskError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise TaskError("contact_balance_changed", "service balance source is no longer current") from error

    def validate(self, context, marker, at):
        if type(marker) is not dict or set(marker) != {"event_id", "settings_hash", "source"}:
            raise TaskError("contact_balance_invalid", "balance marker is invalid")
        try:
            source = source_from_document(marker["source"])
            row = self._row(context)
            event = json.loads(row["event_json"]) if row is not None and row["event_json"] else None
            if (source.owner != SOURCE_OWNER or source.scope != context.scope or event is None
                    or event["id"] != marker["event_id"] or event["settings_hash"] != marker["settings_hash"]
                    or source != self._record(context, event).reference):
                raise ValueError("balance marker changed")
            require(self.contacts.sources.validate_reference_for_audience(
                source, recipient=context.actor.subject, observed_at=at))
        except TaskError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise TaskError("contact_balance_changed", "service balance marker is no longer current") from error
