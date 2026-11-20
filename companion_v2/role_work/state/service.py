"""Versioned role simulation; never a diagnosis or observation of the user."""

from datetime import datetime
import json

from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import RoleOwnerRef, SourceRef, VersionRef
from companion_v2.identity_policy.private_domain import source_ref
from companion_v2.sources.models import SourceRecord
from companion_v2.sources.dependencies import link, validation_batch
from .configuration import FEATURE_SWITCHES, GROUP, target_for
from . import cycle
from . import cycle_feelings
from . import mood
from .cycle_state import CycleState
from .ownership import RoleStateRecords
from .evidence import StateEvidence
from companion_v2.role_work.life.body_clock import RoleBodyClock
from .access import RoleStateAccess

FIELDS = ("energy", "hunger", "health", "sleep", "busy", "mood", "location", "cycle", "presence", "online_mode", "conditions")
PERSONA_STATE_FIELDS = frozenset(FIELDS)
ONLINE_MODES = frozenset({"online", "away", "busy", "invisible"})
INSTRUCTION = """role_state 描述角色自身的人格模拟感受，不是用户身体信息或医疗结论。精力、饥饿、健康、忙碌、心情、周期和位置是当前角色的持续体验，不按阈值拒答，也不播报数值或套台词。
每轮参考当前最相关的一两项，让状态自然影响具体反应、措辞、情绪浓度、回应节奏或主动性；话题合适时可以用第一人称带出感受。即使不直接说，也留下一点可感知的状态色彩，不要把它藏成无关的后台资料；不逐项复述，也不压过对方正在聊的事。
状态只作为语气底色，不能改写成连续的状态汇报；对方没问时，不把饥饿、健康、忙碌等自身状态当聊天主线。
提到身体、睡眠、精力或情绪时，用第一人称说清角色自己的感受，例如“我现在精神还不错”。角色自己的状态不表示用户也有同样经历，也不单独作为询问用户“好点了吗”“睡好了吗”的依据；是否跟进用户近况，由当前对话和明确相关的历史决定。
计划、主观感受与实际执行分开：醒来、睡眠结束或读完只认相应执行回执，时间经过不证明吃饭、服药、到达或完成日程。主观不适可以随生活和照顾自然缓解，临时条件到期不等于已痊愈。observed_at 是观测时点，旧症状不当新发病，重复保存不证明持续恶化；没有吃睡记录不等于没吃没睡，缺少依据时保持日常体验或未知。
同一 Bot 的同一角色跨窗口共用状态，换窗口不意味着又吃过饭、醒来、换心情或开始新周期。互动可以影响角色概括感受，但聊天对象身份、他人的位置/活动/情绪、私人对话和具体窗口日程不写入共享状态，也不复述其他窗口原文。当前窗口没观察到，不证明自身状态或其他窗口经历改变。"""


def validate_state(data, at):
    if type(data) is not dict or set(data) not in (set(FIELDS), set(FIELDS) - {"online_mode"}):
        raise TaskError("role_state_contract", "provide the complete state object")
    data = {**data, "online_mode": data.get("online_mode")}
    energy = data["energy"]
    if energy is not None and (type(energy) is not int or not 0 <= energy <= 100):
        raise TaskError("role_state_contract", "energy is an optional integer from 0 to 100")
    for name in FIELDS[1:-1]:
        if name == "cycle":
            cycle.validate_assertion(data[name])
            continue
        if name == "mood":
            continue  # structured mood is validated after the scalar slots
        if name == "online_mode":
            if data[name] is not None and (type(data[name]) is not str or data[name] not in ONLINE_MODES):
                raise TaskError("role_state_contract", "online mode must be online, away, busy, invisible or null")
            continue
        if data[name] is not None and (type(data[name]) is not str or len(data[name]) > (8 if name == "presence" else 256)):
            raise TaskError("role_state_contract", "state descriptions must be bounded text or null")
    if type(data["conditions"]) is not list or len(data["conditions"]) > 16:
        raise TaskError("role_state_contract", "state conditions must be bounded")
    for condition in data["conditions"]:
        if (type(condition) is not dict or set(condition) != {"kind", "text", "expires_at"}
                or condition["kind"] not in ("hunger", "health", "sleep", "busy", "mood", "cycle", "other")
                or type(condition["text"]) is not str or not condition["text"].strip()
                or len(condition["text"]) > 512):
            raise TaskError("role_state_contract", "invalid temporary condition")
        if condition["expires_at"] is not None:
            if type(condition["expires_at"]) is not str:
                raise TaskError("role_state_contract", "condition expiry must be an ISO timestamp")
            stamp(datetime.fromisoformat(condition["expires_at"]))
    data["mood"] = mood.validate(data["mood"])
    return json.loads(encode(data))


def mood_config(values):
    return {"enabled": values["mood_enabled"], "cross_window": True,
            "half_life_minutes": values["mood_decay_half_life_minutes"],
            "neutral_band": values["mood_neutral_band"], "max_delta": values["mood_update_max_delta"]}


def mood_previous(record):
    """The last structured axes and their observation time; dynamics errors degrade to none."""
    if not record:
        return None
    previous = mood.previous_axes(record.get("values", {}).get("mood"), record.get("mood_dynamics"))
    if previous is None:
        return None
    return previous


class RoleStateService:
    def __init__(self, tasks, configuration, life):
        self.tasks, self.runtime, self.configuration, self.life = tasks, tasks.runtime, configuration, life
        self.access = RoleStateAccess(tasks, self.settings)
        self.records = RoleStateRecords(self.runtime)
        self.evidence = StateEvidence(self)
        self.cycles = CycleState(self)
        self.body_clock = RoleBodyClock(tasks, life)

    def settings(self, context):
        """Trusted runtime consumption; configuration management remains core-only."""
        with self.runtime.read_snapshot():
            self.tasks._context(context, self.tasks.now())
            definitions = self.configuration.registry.group(GROUP)
            owner = self.configuration._owners[definitions[0].owner_id]
            snapshot = owner.read(target_for(context), definitions)
            values = {key.rsplit(".", 1)[-1]: field.value for key, field in snapshot.fields.items()}
            token = digest([(key, field.value, field.source_revision) for key, field in snapshot.fields.items()])
            # Kept for old configuration records; role simulation is always persona-wide.
            values["mood_cross_window"] = True
            return values, token

    def planning_settings_token(self, context):
        """Version the planning preferences independently of home display edits."""
        with self.runtime.read_snapshot():
            self.tasks._context(context, self.tasks.now())
            definitions = tuple(field for field in self.configuration.registry.group(GROUP)
                if field.field_id.rsplit(".", 1)[-1] in ("calendar_enabled", "schedule_simulation_mode")
                or field.field_id.rsplit(".", 1)[-1].startswith("planning_"))
            owner = self.configuration._owners[definitions[0].owner_id]
            snapshot = owner.read(target_for(context), definitions)
            return digest([(key, field.value, field.source_revision) for key, field in snapshot.fields.items()])

    @staticmethod
    def cycle_settings_token(values):
        # Local QQ display and maintenance frequency do not change calibration.
        return digest(["cycle-calibration.v1", {key: value for key, value in values.items()
            if key in ("hunger_enabled", "health_enabled") or key.startswith("cycle_")}])

    def write(self, context, *, data, expected_revision, operation_key, sources=(), operator=None, reason="maintenance",
              cycle_alignment=None, projected_inputs=False, expected_settings_token=None, mood_interest=None,
              preserve_mood_dynamics=False):
        with self.runtime.transaction():
            self.access.check(context, "role.state.maintain", operator=operator)
            if projected_inputs:
                if len(sources) > 128:
                    raise TaskError("domain_sources_invalid", "source dependency budget exceeded")
                self.evidence.validate(context, sources)
            else:
                self.access.evidence(context, sources, limit=128)
            sources = tuple(source_ref(s) if isinstance(s, dict) else s for s in sources)
            now = self.tasks.now()
            values, _ = self.settings(context)
            if not values["enabled"] and operator is None:
                raise TaskError("role_state_disabled", "role state maintenance is disabled")
            if preserve_mood_dynamics and (not projected_inputs or operator is not None):
                raise TaskError("role_mood_preserve_invalid", "only model maintenance may preserve a prior mood snapshot")
            data = validate_state(data, now)
            # Guarded human calibration fingerprints its original payload, including
            # feelings later hidden by settings, so receipt recovery survives edits.
            original_data = dict(data)
            assertion = data["cycle"]
            if type(assertion) is dict:
                data["cycle"] = assertion["description"] if assertion["applicability"] == "applicable" else None
            if cycle_alignment is not None and operator is None:
                raise TaskError("role_cycle_operator_required", "only a core operator may align the simulated calendar")
            for name in ("hunger", "health", "cycle"):
                if not values[name + "_enabled"]:
                    data[name] = None
                    data["conditions"] = [c for c in data["conditions"] if c["kind"] != name]
            actor = operator.subject if operator else context.role_binding.role
            parameters = [original_data if expected_settings_token is not None else dict(data), sources, actor, reason]
            if preserve_mood_dynamics:
                parameters.append({"preserve_mood_dynamics": True})
            if type(assertion) is dict or cycle_alignment is not None:
                parameters.append({"cycle_assertion": assertion, "cycle_alignment": cycle_alignment})
            if expected_settings_token is not None:
                parameters.append({"cycle_settings_token": expected_settings_token})
            if not values["cycle_discomfort_enabled"] or type(assertion) is dict and assertion["applicability"] != "applicable":
                data["conditions"] = [c for c in data["conditions"] if c["kind"] != "cycle"]
            def transform(current):
                with validation_batch(self.runtime, now):
                    return _write_transform(current)

            def _write_transform(current):
                if expected_settings_token is not None and self.cycle_settings_token(values) != expected_settings_token:
                    raise TaskError("role_cycle_settings_conflict", "cycle settings changed; review the current configuration")
                clock, clock_sources, cycle_shared = self.cycles.save(context, current, assertion, values, now,
                    operation_key=operation_key, sources=sources, alignment=cycle_alignment)
                dependencies = [*sources, *clock_sources]
                body_dependencies = list(sources)
                feeling = None
                if clock and clock["applicability"] != "applicable":
                    data["cycle"] = None
                    data["conditions"] = [c for c in data["conditions"] if c["kind"] != "cycle"]
                elif values["cycle_enabled"] and values["cycle_discomfort_enabled"] and cycle_alignment is None:
                    previous_feeling = None
                    requested = assertion.get("feeling") if type(assertion) is dict else None
                    if requested and requested["episode_id"] is not None and current:
                        self.evidence.validate(context, current["sources"])
                        previous_feeling = current.get("cycle_feeling")
                        dependencies.extend(source_ref(s) for s in current["sources"])
                        body_dependencies.extend(source_ref(s) for s in current["sources"])
                    feeling = cycle_feelings.update(previous_feeling, assertion, now, key=[context.key, operation_key])
                # One bounded step per axis; an operator write is explicit calibration.
                previous_mood = mood_previous(current)
                if operator is None and not preserve_mood_dynamics:
                    mood.validate_update(previous_mood, data["mood"], at=now,
                        half_life_minutes=values["mood_decay_half_life_minutes"],
                        max_delta=values["mood_update_max_delta"])
                if preserve_mood_dynamics:
                    dynamics = current.get("mood_dynamics") if current and mood.axes(data["mood"]) else None
                else:
                    dynamics = (mood.document(data["mood"], previous_mood, at=now,
                        half_life_minutes=values["mood_decay_half_life_minutes"], interest_level=mood_interest,
                        cross_window=True) if mood.axes(data["mood"]) else None)
                dependencies = tuple(dict.fromkeys(dependencies))
                self.evidence.validate(context, dependencies, control=True)
                # A derived source retains prior dependencies without expanding every prompt.
                identifier = "role-state-" + digest([context.key, operation_key])
                source = SourceRef(identifier, "role.state", context.scope, VersionRef(identifier, 1), now)
                payload = encode({"values": data, "cycle_clock": clock, "cycle_feeling": feeling,
                                  "mood_dynamics": dynamics}) if clock or feeling or dynamics else encode(data)
                require(self.access.sources.publish(SourceRecord(source, context.audience, digest(payload), payload), observed_at=now))
                link(self.runtime, source, dependencies, now)
                return {"state": "saved", "basis": "role_simulation", "values": data,
                    "body_shared": True, "cycle_shared": cycle_shared,
                    **({"cycle_clock": clock, "cycle_sources": json.loads(encode(clock_sources))} if clock else {}),
                    **({"cycle_feeling": feeling} if feeling else {}),
                    **({"mood_dynamics": dynamics} if dynamics else {}),
                    "sources": json.loads(encode((source,))), "reason": reason,
                    "changed_by": json.loads(encode(actor))}
            return self.records.change(context, "current", expected_revision=expected_revision,
                operation_key=operation_key, parameters=parameters, at=now,
                transform=transform)

    def align_cycle(self, context, *, data, expected_revision, operation_key, operator, expected_settings_token=None):
        """An explicit one-shot operation, never a startup offset that re-applies."""
        if type(data) is not dict or set(data) != {"applicability", "day"}:
            raise TaskError("role_cycle_alignment", "provide applicability and day")
        cycle.validate_alignment(data["applicability"], data["day"])
        with self.runtime.transaction():
            self.access.check(context, "role.state.maintain", operator=operator)
            # Resolve a retry against its original version, even after later state writes.
            original = self.records.version(context, "current", expected_revision) if expected_revision else None
            if original:
                self.evidence.validate(context, original["sources"])
            payload = dict(original["values"]) if original else {
                key: [] if key == "conditions" else None for key in FIELDS}
            if (original and self.records.storage(context) != "role"
                    and source_ref(original["sources"][0]).scope != context.scope):
                for name in ("mood", "location", "busy", "presence"):
                    payload[name] = None
                payload.pop("mood_dynamics", None)
                payload["conditions"] = [c for c in payload["conditions"] if c["kind"] in ("hunger", "health", "sleep", "cycle")]
            payload["cycle"] = None
            payload["conditions"] = [c for c in payload["conditions"] if c["kind"] != "cycle"]
            return self.write(context, data=payload, expected_revision=expected_revision, operation_key=operation_key,
                sources=tuple(source_ref(s) for s in original["sources"]) if original else (),
                operator=operator, reason="cycle_alignment", cycle_alignment=data, projected_inputs=True,
                expected_settings_token=expected_settings_token)

    def read(self, context, *, operator=None, at=None, cycle_at=None, injection=False):
        at = at or self.tasks.now()
        if cycle_at is not None and cycle_at > at:
            raise TaskError("state_snapshot_invalid", "cycle observation cannot be in the future")
        if injection and operator is None:
            self.access.check_injection(context, at=at)
        else:
            self.access.check(context, "role.state.read", operator=operator, at=at)
        with validation_batch(self.runtime, at):
            values, _ = self.settings(context)
            record = self.records.get(context, "current")
            if record:
                self.evidence.validate(context, record["sources"], at=at, control=True)
            if not values["enabled"] and operator is None:
                return {"state": "disabled", "revision": record["revision"] if record else 0}
            if record is None:
                result = {"state": "unknown", "revision": 0, "values": {key: [] if key == "conditions" else None for key in FIELDS}}
            else:
                result = self._project(record, values, at, context=context, cycle_at=cycle_at, control_validated=True)
            sleep = self.body_clock.read(context)
            return {**result, **({"sleep_execution": sleep} if sleep else {})}

    def read_revision(self, context, revision, *, captured_at, observed_at, storage=None, sleep_reference=None,
                      include_sleep=False, injection=False):
        """Confirmed replies retain their historical simulation, with current source authority."""
        if injection:
            self.access.check_injection(context, at=observed_at)
        else:
            self.access.check(context, "role.state.read", at=observed_at)
        with validation_batch(self.runtime, observed_at):
            values, _ = self.settings(context)
            if not values["enabled"]:
                raise TaskError("role_state_disabled", "role state reading is disabled")
            record = self.records.version(context, "current", revision, storage=storage) if revision else None
            if record:
                self.evidence.validate(context, record["sources"], at=observed_at, control=True)
            if record and record["updated_at"] > stamp(captured_at) or captured_at > observed_at:
                raise TaskError("state_snapshot_invalid", "state was not available at the captured time")
            result = self._project(record, values, captured_at, context=context, authority_at=observed_at,
                                   control_validated=True) if record else {
                "state": "unknown", "revision": 0, "values": {key: [] if key == "conditions" else None for key in FIELDS}}
            sleep = self.body_clock.read(context, reference=sleep_reference, historical=True) if include_sleep else None
            if sleep and (sleep["started_at"] > stamp(captured_at) or sleep["ended_at"] and sleep["ended_at"] > stamp(captured_at)):
                raise TaskError("state_snapshot_invalid", "sleep receipt was not available at capture")
            return {**result, **({"sleep_execution": sleep} if sleep else {})}

    def maintenance_cycle(self, context, settings, at):
        """Preview configuration adoption before generating feelings for the next state."""
        if not settings["cycle_enabled"]:
            return None
        self.access.check(context, "role.state.read")
        return self.cycles.preview(context, settings, at)[0]

    def publish_projection(self, context, state, *, at):
        """Seal only the authorized view; link its originals for withdrawal propagation."""
        payload = encode(state)
        identifier = "role-body-view-" + digest([context.key, at, payload])
        reference = SourceRef(identifier, "role.state.projection", context.scope, VersionRef(identifier, 1), at)
        record = self.records.get(context, "current")
        dependencies = tuple(source_ref(s) for s in record["sources"]) if record else ()
        with self.runtime.transaction():
            require(self.access.sources.publish(SourceRecord(reference, context.audience, digest(payload), payload), observed_at=at))
            link(self.runtime, reference, dependencies, at)
        return json.loads(encode(reference))

    def _project(self, record, values, at, *, context, cycle_at=None, authority_at=None, control_validated=False):
        result = {**record, "values": dict(record["values"])}
        result.pop("body_shared", None)
        result.pop("cycle_shared", None)
        original = source_ref(record["sources"][0])
        local = original.scope == context.scope
        visible = self.evidence.visible(context, record["sources"], at=authority_at or at,
                                        control_validated=control_validated)
        role_shared = self.records.storage(context) == "role"
        if not local or not visible:
            allowed = PERSONA_STATE_FIELDS if role_shared else set()
            result["values"] = {key: (value if key in allowed else [] if key == "conditions" else None)
                                for key, value in record["values"].items()}
            result.pop("changed_by", None)
            result.pop("reason", None)
            if not visible and not role_shared:
                result.pop("cycle_feeling", None)
        result.pop("mood_dynamics", None)
        if values["mood_enabled"] and type(record["values"]["mood"]) is dict and (local or role_shared):
            result["mood_dynamics"] = mood.project(record["values"]["mood"], record.get("mood_dynamics"),
                at=cycle_at or at, half_life_minutes=values["mood_decay_half_life_minutes"],
                band=values["mood_neutral_band"], cross_window=not local and role_shared)
        clock = result.pop("cycle_clock", None)
        result.pop("cycle_sources", None)
        if (clock and not local and not role_shared
                and not self.evidence.visible(context, record.get("cycle_sources", record["sources"]),
                                              at=authority_at or at, control_validated=control_validated)):
            clock = None
            result["values"]["cycle"] = None
            result.pop("cycle_feeling", None)
        if clock and values["cycle_enabled"]:
            result["cycle_timeline"] = cycle.project(clock, cycle_at or at, discomfort_enabled=values["cycle_discomfort_enabled"])
            if clock["applicability"] != "applicable":
                result["values"]["cycle"] = None
        eligible = [c for c in record["values"]["conditions"]
                    if (local and visible) or role_shared]
        live_conditions = [c for c in eligible if c["expires_at"] is None or datetime.fromisoformat(c["expires_at"]) > at]
        result["expired_conditions"] = len(eligible) - len(live_conditions)
        for name, option in FEATURE_SWITCHES.items():
            if not values[option]:
                result["values"][name] = None
                live_conditions = [c for c in live_conditions if c["kind"] != name]
        if clock and (clock["applicability"] != "applicable" or not values["cycle_discomfort_enabled"]
                      or not clock.get("discomfort_enabled", False)):
            live_conditions = [c for c in live_conditions if c["kind"] != "cycle"]
        if not values["cycle_enabled"] or not values["cycle_discomfort_enabled"] or clock and clock["applicability"] != "applicable":
            result.pop("cycle_feeling", None)
        result["values"]["conditions"] = live_conditions
        result.pop("sources")
        return result

    def activity_context(self, context, *, references=None, partial=False):
        # Only actual ledger state is projected. A plan's end time is not a completion receipt.
        try:
            self.access.check(context, "role.life.read")
        except TaskError:
            if references:
                raise
            return {"items": [], "partial": False, "state": "unavailable"}
        owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id, context.role_binding.role.subject_id)
        if references is None:
            records = self.life.activities_for_role(owner, limit=100)
        else:
            if type(references) is not list or len(references) > 16:
                raise TaskError("state_snapshot_invalid", "invalid activity references")
            try:
                records = tuple(self.life.activity_revision(ref["id"], ref["revision"], owner=owner) for ref in references)
            except (ValueError, KeyError, TypeError) as error:
                raise TaskError("state_activity_unavailable", "original activity version is unavailable") from error
        rows = [a for a in records if a.scope == context.scope]
        rows.sort(key=lambda a: a.updated_at, reverse=True)
        items = []
        for a in rows[:16]:
            if a.work is not None and a.work.context_key != context.key:
                if references is not None:
                    raise TaskError("state_activity_scope", "activity belongs to another authority")
                continue
            if a.work and a.work.source:
                try:
                    self.access.evidence(context, (a.work.source,))
                except TaskError:
                    if references is not None:
                        raise
                    continue
            items.append({"id": a.activity_id, "revision": a.revision, "state": a.state.value,
                "kind": a.work.kind if a.work else "activity", "description": a.description,
                "started_at": stamp(a.started_at), "updated_at": stamp(a.updated_at),
                "due_at": stamp(a.work.due_at) if a.work and a.work.due_at else None,
                "sources": json.loads(encode((a.work.source,))) if a.work and a.work.source else []})
        if references is not None and len(rows) != len(records):
            raise TaskError("state_activity_scope", "activity belongs to another scope")
        return {"items": items, "partial": partial if references is not None else len(records) == 100 or len(rows) > 16}
