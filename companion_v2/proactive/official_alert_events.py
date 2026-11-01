"""Opt-in official warnings for the configured role location."""

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta
import json

from companion_v2.agenda.planning_config import configured_weather_location
from companion_v2.conversation.messages import source_from_document
from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import ScopeKind, SourceRef, VersionRef
from companion_v2.sources.governance import bind
from companion_v2.sources.models import SourceRecord

from .contact_models import ContactIntent
from .models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind
from .weather_alerts import MAX_OBSERVATION_AGE, SEVERITY_RANK, active_official_alerts


SOURCE_OWNER = "proactive.official_weather_alert"
RETRY = timedelta(minutes=5)
SOURCE_URL = "https://www.qweather.com/"
MEANING = ("这是和风天气对管理员配置的角色所在地提供的官方预警，"
           "但查询坐标不证明通告覆盖目标镇街；不代表对方所在地。"
           "仅当原文明确覆盖整个配置城市或明确包含目标镇街，且没有排除目标镇街时才发送。")


class _NoCurrentAlert:
    """本轮轮询成功，但当前没有生效中的预警（与获取失败区分）。"""


NO_CURRENT_ALERT = _NoCurrentAlert()


def attribution_suffix(attributions):
    return "\n\n来源：和风天气 " + SOURCE_URL + "\n" + "\n".join(attributions)


class OfficialAlertContactEvents:
    def __init__(self, contacts, role_state, port):
        if contacts.runtime is not role_state.runtime:
            raise ValueError("official alerts must share the original role runtime")
        self.contacts, self.role_state, self.port = contacts, role_state, port
        self.runtime = contacts.runtime
        self.retry_at = {}
        self.guard = bind(self.runtime, SOURCE_OWNER, self._validate_source)

    def close(self):
        self.guard.close()
        self.retry_at.clear()

    def _location(self, context, preferences):
        if (context.scope.kind is not ScopeKind.PRIVATE or not preferences.values["official_weather_alert_enabled"]
                or preferences.reason("environment")):
            return None
        settings, _ = self.role_state.settings(context)
        location = configured_weather_location(settings["planning_weather_location"])
        return (location, digest(location)) if location is not None and location.alert_area else None

    def select(self, context, preferences, at):
        if self.port is None or self.retry_at.get(context.key, at) > at:
            return None
        configured = self._location(context, preferences)
        if configured is None:
            return None
        row = self.runtime.read_one("SELECT * FROM proactive_official_alert_polls WHERE origin_key = ?", (context.key,))
        poll_interval = timedelta(minutes=preferences.values["official_alert_poll_minutes"])
        if (row is not None and row["location_hash"] == configured[1]
                and at < datetime.fromisoformat(row["checked_at"]) + poll_interval):
            return None
        opportunity = Opportunity("official-alert-poll-" + digest([context.key, stamp(at)]),
                                  context.scope, at, at + MAX_OBSERVATION_AGE)
        return opportunity, {"location_hash": configured[1]}

    def _save_observation(self, context, location_hash, observation, active, at):
        with self.runtime.transaction() as connection:
            previous = connection.execute("""SELECT alert_id, chain_id FROM proactive_official_alerts
                WHERE origin_key = ? AND location_hash = ?""", (context.key, location_hash)).fetchall()
            chains = {row["alert_id"]: row["chain_id"] for row in previous}
            connection.execute("""UPDATE proactive_official_alerts SET state = 'retired'
                WHERE origin_key = ? AND location_hash = ?""", (context.key, location_hash))
            for alert in active:
                chain = chains.get(alert.identifier)
                if chain is None:
                    chain = next((chains[item] for item in alert.supersedes if item in chains), None)
                if chain is None:
                    chain = min(alert.supersedes) if alert.supersedes else alert.identifier
                chains[alert.identifier] = chain
                connection.execute("""INSERT INTO proactive_official_alerts
                    (origin_key, location_hash, alert_id, chain_id, state, alert_json, attributions_json, queried_at)
                    VALUES (?, ?, ?, ?, 'current', ?, ?, ?)
                    ON CONFLICT(origin_key, location_hash, alert_id) DO UPDATE SET
                    chain_id=excluded.chain_id, state=excluded.state, alert_json=excluded.alert_json,
                    attributions_json=excluded.attributions_json, queried_at=excluded.queried_at""",
                    (context.key, location_hash, alert.identifier, chain,
                     encode({**asdict(alert), "role_location": observation.location.name,
                             "alert_city": observation.location.alert_city,
                             "alert_area": observation.location.alert_area}),
                     encode(observation.attributions), stamp(observation.queried_at)))
            connection.execute("""INSERT INTO proactive_official_alert_polls VALUES (?, ?, ?)
                ON CONFLICT(origin_key) DO UPDATE SET location_hash=excluded.location_hash,
                checked_at=excluded.checked_at""", (context.key, location_hash, stamp(at)))

    def _contacted_chains(self, context, location_hash):
        """Chains that already produced a sendable contact in this window.

        The chain identity alone is the wrong dedupe key: a contact that was
        skipped (model skip, coverage guard, quota) never reached the user, so a
        later version of the same chain - an upgrade with a new id, a new issue
        time or a higher severity - must still be able to contact them.  Only
        records that carried a body (``send`` or its pending persona judgement)
        count as contacted; a skipped record does not.
        """
        rows = self.runtime.read_all("""SELECT json_extract(record_json,
            '$.automatic_origin.official_alert_event.chain_id') AS chain_id
            FROM proactive_contacts WHERE origin_key = ?
            AND json_extract(record_json, '$.decision') IN ('send', 'judge_pending')
            AND json_extract(record_json,
                '$.automatic_origin.official_alert_event.location_hash') = ?""",
            (context.key, location_hash))
        return {row["chain_id"] for row in rows if row["chain_id"]}

    @staticmethod
    def _alert_opportunity(context, location_hash, row, alert):
        """One opportunity per alert version, not per polling pass.

        The version keeps an unchanged warning from being retried on every poll
        after a skip, while a new version in the same chain gets a fresh task id
        and can therefore be reconsidered.
        """
        version = digest([row["alert_id"], alert.get("issued_at"), alert.get("severity")])
        queried = datetime.fromisoformat(row["queried_at"])
        return Opportunity("official-alert-" + digest(
            [context.key, location_hash, row["chain_id"], version]), context.scope, queried,
            min(queried + MAX_OBSERVATION_AGE, datetime.fromisoformat(alert["expires_at"])))

    def _candidate(self, context, location_hash, at):
        rows = self.runtime.read_all("""SELECT * FROM proactive_official_alerts WHERE origin_key = ?
            AND location_hash = ? AND state = 'current' ORDER BY queried_at DESC, alert_id""",
            (context.key, location_hash))
        contacted = self._contacted_chains(context, location_hash)
        for row in rows:
            if row["chain_id"] in contacted:
                continue
            queried = datetime.fromisoformat(row["queried_at"])
            alert = json.loads(row["alert_json"])
            if at - queried > MAX_OBSERVATION_AGE or at >= datetime.fromisoformat(alert["expires_at"]):
                continue
            opportunity = self._alert_opportunity(context, location_hash, row, alert)
            task_id = self.contacts.task_id(context, opportunity)
            if (self.runtime.read_one("SELECT 1 FROM execution_tasks WHERE task_id = ?", (task_id,))
                    or self.runtime.read_one("SELECT 1 FROM proactive_contacts WHERE task_id = ?", (task_id,))):
                continue
            if opportunity.opened_at <= at < opportunity.expires_at:
                return row, opportunity
        return None

    def _record(self, context, row, opportunity):
        alert = json.loads(row["alert_json"])
        attributions = json.loads(row["attributions_json"])
        body = {"event": "role_location_official_weather_alert", "provider": "qweather",
                "role_location": alert.get("role_location"),
                "alert_city": alert.get("alert_city"), "alert_area": alert.get("alert_area"), "alert": alert,
                "attributions": attributions, "source_url": SOURCE_URL, "meaning": MEANING}
        receipt = {"context": json.loads(encode(context)), "location_hash": row["location_hash"],
                   "alert_id": row["alert_id"], "chain_id": row["chain_id"]}
        payload = encode({**body, "_receipt": receipt})
        identifier = "contact-official-alert-source-" + digest([context.key, row["location_hash"],
            row["alert_id"], row["queried_at"]])
        source = SourceRef(identifier, SOURCE_OWNER, context.scope, VersionRef(identifier, 1),
                           datetime.fromisoformat(row["queried_at"]), opportunity.expires_at)
        return SourceRecord(source, context.audience, digest(payload), payload)

    async def prepare(self, context, marker):
        if self.port is None or type(marker) is not dict or set(marker) != {"location_hash"}:
            return None
        at = self.contacts.tasks.now()
        preferences = require(self.contacts.preferences.read(context, observed_at=at))
        configured = self._location(context, preferences)
        if configured is None or configured[1] != marker["location_hash"]:
            return None
        try:
            observation = await asyncio.wait_for(self.port.fetch(configured[0]), timeout=6)
        except Exception:
            self.retry_at[context.key] = self.contacts.tasks.now() + RETRY
            return None
        at = self.contacts.tasks.now()
        preferences = require(self.contacts.preferences.read(context, observed_at=at))
        if self._location(context, preferences) != configured:
            return None
        if observation.location != configured[0] or not timedelta(0) <= at - observation.queried_at <= MAX_OBSERVATION_AGE:
            self.retry_at[context.key] = at + RETRY
            return None
        active = active_official_alerts(observation, configured[0], at)
        minimum = preferences.values["official_alert_min_severity"]
        if minimum:
            floor = SEVERITY_RANK[minimum]
            # Unclassified official alerts are kept: a safety notice must not be
            # silently dropped just because its severity could not be ranked.
            active = [alert for alert in active
                      if alert.severity == "unknown" or SEVERITY_RANK.get(alert.severity, 0) >= floor]
        self._save_observation(context, configured[1], observation, active, at)
        self.retry_at.pop(context.key, None)
        candidate = self._candidate(context, configured[1], at)
        if candidate is None:
            # 没有生效中的预警是常态（接口 zeroResult 或刚被取消），调用方要按
            # 「本轮无候选」处理，不能当成获取失败去写来源不可用的诊断。
            return NO_CURRENT_ALERT
        row, opportunity = candidate
        record = self._record(context, row, opportunity)
        require(self.contacts.sources.publish(record, observed_at=at))
        require(self.contacts.sources.validate_reference_for_audience(
            record.reference, recipient=context.actor.subject, observed_at=at))
        receipt = {"alert_id": row["alert_id"], "chain_id": row["chain_id"],
                   "location_hash": row["location_hash"], "source": json.loads(encode(record.reference))}
        thought = Thought("official-alert-" + digest(receipt), opportunity, ThoughtKind.CONVERSATION,
            "角色所在地有一条有效的官方天气预警；只基于预警原文自然地提示风险和建议，也可以跳过。",
            EvidenceSnapshot(context.scope, at, (record.reference,)), 1.0)
        return opportunity, (ContactIntent("environment", thought),), receipt

    def _validate_source(self, record, at):
        try:
            body = json.loads(record.payload)
            receipt = body.pop("_receipt")
            context = TaskContext.restore(receipt["context"])
            preferences = require(self.contacts.preferences.read(context, observed_at=at))
            configured = self._location(context, preferences)
            row = self.runtime.read_one("""SELECT * FROM proactive_official_alerts WHERE origin_key = ?
                AND location_hash = ? AND alert_id = ?""",
                (context.key, receipt["location_hash"], receipt["alert_id"]))
            if (configured is None or configured[1] != receipt["location_hash"] or row is None
                    or row["state"] != "current" or row["chain_id"] != receipt["chain_id"]):
                raise ValueError("official alert is no longer current")
            opportunity = self._candidate_opportunity(context, row)
            if not opportunity.opened_at <= at < opportunity.expires_at:
                raise ValueError("official alert observation expired")
            expected = self._record(context, row, opportunity)
            expected_body = json.loads(expected.payload)
            expected_body.pop("_receipt")
            if (record != expected or body != expected_body or record.content_hash != digest(record.payload)):
                raise ValueError("official alert source changed")
        except TaskError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise TaskError("contact_official_alert_changed", "official weather alert is no longer current") from error

    @staticmethod
    def _candidate_opportunity(context, row):
        return OfficialAlertContactEvents._alert_opportunity(
            context, row["location_hash"], row, json.loads(row["alert_json"]))

    def validate(self, context, marker, at):
        if type(marker) is not dict or set(marker) != {"alert_id", "chain_id", "location_hash", "source"}:
            raise TaskError("contact_official_alert_invalid", "official alert marker is invalid")
        if any(type(marker[key]) is not str or not marker[key]
               for key in ("alert_id", "chain_id", "location_hash")):
            raise TaskError("contact_official_alert_invalid", "official alert marker is invalid")
        try:
            source = source_from_document(marker["source"])
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise TaskError("contact_official_alert_invalid", "official alert source is invalid") from error
        if source.scope != context.scope or source.owner != SOURCE_OWNER:
            raise TaskError("contact_official_alert_invalid", "official alert belongs to another window")
        row = self.runtime.read_one("""SELECT * FROM proactive_official_alerts WHERE origin_key = ?
            AND location_hash = ? AND alert_id = ?""",
            (context.key, marker["location_hash"], marker["alert_id"]))
        if (row is None or row["state"] != "current" or row["chain_id"] != marker["chain_id"]
                or source != self._record(context, row, self._candidate_opportunity(context, row)).reference):
            raise TaskError("contact_official_alert_changed", "official alert marker no longer matches its source")
        require(self.contacts.sources.validate_reference_for_audience(
            source, recipient=context.actor.subject, observed_at=at))
