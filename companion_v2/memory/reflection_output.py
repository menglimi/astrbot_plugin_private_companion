"""Small model-facing reflection format and independent candidate validation."""

import json
import re

from companion_v2.execution.models import TaskError, digest, encode
from companion_v2.relationships import anniversaries
from companion_v2.sources.privacy import redact_fields, redact_text

from . import codec
from .models import text
from .tools import PROPOSAL_PROPERTIES, decode_proposal


DETAIL_FIELDS = tuple(k for k in PROPOSAL_PROPERTIES if k not in (
    "operation", "evidence_handles", "predicate", "object", "representation",
    "target_atom_id", "expected_revision", "namespace", "visibility", "purpose", "defer_until_boundary",
))


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


# Optional semantic qualifications are supplied only when relevant. The host
# supplies identities, operation keys, namespaces and the default policy values.
REFLECTION_SCHEMA = object_schema({
    "summary": {"type": "string"},
    "proposals": {"type": "array", "items": object_schema({
        "item_id": {"type": ["string", "null"]},
        "operation": {"type": "string", "enum": ["add", "correct", "retract", "no_op"]},
        "evidence_handles": {"type": "array", "items": {"type": "string"}},
        "predicate": {"type": ["string", "null"]},
        "object": {"type": ["string", "null"]},
        "representation": {"type": ["string", "null"]},
        "target": {"type": ["string", "null"]},
        "details": {"type": "array", "items": object_schema({
            "field": {"type": "string", "enum": list(DETAIL_FIELDS)},
            "value": {"anyOf": [{"type": "string"}, {"type": "boolean"}, {"type": "number"}, {"type": "null"},
                {"type": "array", "items": object_schema({"key": {"type": "string"}, "value": {"type": "string"}})}]},
        })},
    })},
    "pending_decisions": {"type": "array", "items": object_schema({
        "operation_id": {"type": "string"}, "decision": {"type": "string", "enum": ["accept", "discard"]},
    })},
    "anniversaries": {"type": "array", "maxItems": 1, "items": object_schema({
        "label": {"type": "string"},
        "reason": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_handle": {"type": "string"},
    })},
})


class OutputProblem(ValueError):
    def __init__(self, code, path="$"):
        super().__init__(code)
        self.code, self.path = code, path


def redact_output(body):
    """Redact escaped JSON strings without repairing structure or duplicate keys."""
    def string(match):
        try:
            safe = redact_text(json.loads(match[0]))
            return json.dumps(safe, ensure_ascii=any(0xD800 <= ord(char) <= 0xDFFF for char in safe))
        except (ValueError, UnicodeError):
            return match[0]
    body = body.encode("utf-8", errors="backslashreplace").decode("utf-8")
    body = re.sub(r'"(?:[^"\\\r\n]|\\.)*"', string, body)
    body = redact_text(body)
    try:
        value = parse_output({"text": body, "finish_reason": "stop"})
    except OutputProblem:
        return body
    changed = False
    def walk(item):
        nonlocal changed
        if isinstance(item, dict):
            for key, val in tuple(item.items()):
                walk(val)
            for label, content in (("predicate", "object"), ("key", "value")):
                if isinstance(item.get(label), str) and isinstance(item.get(content), str):
                    safe = redact_fields({item[label]: item[content]}).get(redact_text(item[label]), "[REDACTED]")
                    if safe != item[content]:
                        item[content], changed = safe, True
        elif isinstance(item, list):
            for val in item:
                walk(val)
    walk(value)
    return encode(value) if changed else body


def parse_output(raw):
    if raw["finish_reason"] != "stop":
        raise OutputProblem("output_" + raw["finish_reason"])
    body = raw["text"].strip()
    # Only remove a single unambiguous outer Markdown wrapper. Never find the
    # first brace, close a truncated object, choose a duplicate key or invent a value.
    lines = body.splitlines()
    if len(lines) >= 3 and lines[0].lower() in ("```json", "```") and lines[-1] == "```":
        body = "\n".join(lines[1:-1]).strip()

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise OutputProblem("duplicate_json_field")
            result[key] = value
        return result

    def constant(_):
        raise OutputProblem("nonfinite_json_number")

    try:
        result = json.loads(body, object_pairs_hook=unique, parse_constant=constant)
        json.dumps(result, ensure_ascii=False).encode("utf-8")
    except OutputProblem:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise OutputProblem("invalid_json") from None
    if (type(result) is not dict or not {"summary", "proposals"}.issubset(result)
            or set(result) - {"summary", "proposals", "pending_decisions", "anniversaries"}):
        raise OutputProblem("invalid_shape")
    try:
        text(result["summary"], "summary", 8192, empty=True)
    except ValueError:
        raise OutputProblem("invalid_summary", "$.summary") from None
    if type(result["proposals"]) is not list or len(result["proposals"]) > 32:
        raise OutputProblem("invalid_proposals", "$.proposals")
    decisions = result.get("pending_decisions", [])
    if type(decisions) is not list or len(decisions) > 32:
        raise OutputProblem("invalid_decisions", "$.pending_decisions")
    anniversary_items = result.get("anniversaries", [])
    if type(anniversary_items) is not list or len(anniversary_items) > 1:
        raise OutputProblem("invalid_anniversaries", "$.anniversaries")
    return result


class ManifestResolver:
    """Durable aliases point to captured spans, never to a renewed query session."""

    def __init__(self, reflection, manifest):
        self.reflection, self.manifest = reflection, manifest

    def resolve(self, context, _session, handle):
        raw = self.manifest["handles"].get(handle)
        if raw is None:
            raise OutputProblem("unknown_evidence", "evidence_handles")
        evidence = codec.evidence(raw)
        self.reflection.memory.writer.access.evidence(context, evidence, action="memory.reflect")
        return evidence


_CONTEXT_MODES = {"relevant": "relevant", "相关": "relevant", "pinned": "pinned", "置顶": "pinned"}
_POLARITIES = {"positive": "positive", "正面": "positive", "negative": "negative", "负面": "negative",
               "uncertain": "uncertain", "不明确": "uncertain", "unknown": "uncertain"}
_RETENTIONS = {"session": "session", "会话": "session", "short": "short", "短期": "short",
               "durable": "durable", "长期": "durable", "protected": "protected", "保护": "protected"}
# 归因是自由说明里的高频词。模型会写 self / self_report / direct_observation（现场 2026-11-04
# 的三次拒绝都是这一类），而内部合同只认 user / third_party / bot / unknown / observed。
# 别名并进合同值，认不出的直接丢掉走默认值——不能让一个措辞把整条事实判成 invalid_proposal。
_ATTRIBUTIONS = {"user": "user", "self": "user", "用户": "user", "自述": "user", "用户自述": "user",
                 "self_report": "user", "self-reported": "user", "first_person": "user",
                 "observed": "observed", "observation": "observed", "direct_observation": "observed",
                 "直接观察": "observed", "观察": "observed",
                 "third_party": "third_party", "third-party": "third_party", "转述": "third_party", "他人转述": "third_party",
                 "bot": "bot", "角色": "bot", "角色自述": "bot",
                 "unknown": "unknown", "不确定": "unknown"}


def _coerce_soft_details(data):
    """Keep a model's soft qualifier from discarding the whole proposal.

    ``details`` is free-form for the model, but the written proposal validates a
    few enums, a number and two timestamps. One stray word such as
    ``context_mode="轻松私聊…"`` used to raise and turn an otherwise valid fact into
    ``invalid_proposal``. Unknown values are dropped here (the dataclass default
    applies), while known aliases and numeric strings are folded into the value
    the internal profile accepts.
    """
    value = data.get("context_mode")
    if value is not None:
        mapped = _CONTEXT_MODES.get(str(value).strip().lower()) if isinstance(value, str) else None
        if mapped is None:
            data.pop("context_mode", None)
        else:
            data["context_mode"] = mapped
    value = data.get("polarity")
    if value is not None:
        mapped = _POLARITIES.get(str(value).strip().lower()) if isinstance(value, str) else None
        if mapped is None:
            data.pop("polarity", None)
        else:
            data["polarity"] = mapped
    value = data.get("retention")
    if value is not None:
        mapped = _RETENTIONS.get(str(value).strip().lower()) if isinstance(value, str) else None
        if mapped is None:
            data.pop("retention", None)
        else:
            data["retention"] = mapped
    value = data.get("attribution")
    if value is not None:
        mapped = _ATTRIBUTIONS.get(str(value).strip().lower()) if isinstance(value, str) else None
        if mapped is None:
            data.pop("attribution", None)
        else:
            data["attribution"] = mapped
    value = data.get("single_value")
    if value is not None and type(value) is not bool:
        folded = {"true": True, "false": False, "是": True, "否": False}.get(str(value).strip().lower())
        if folded is None:
            data.pop("single_value", None)
        else:
            data["single_value"] = folded
    for name in ("valid_from", "valid_to"):
        value = data.get(name)
        if value is None:
            continue
        if isinstance(value, str):
            try:
                from datetime import datetime
                datetime.fromisoformat(value)
            except ValueError:
                data.pop(name, None)
    return data


def normalize_proposal(raw, manifest):
    if type(raw) is not dict:
        raise OutputProblem("invalid_proposal")
    data = dict(raw)
    if "details" in data:
        details = data.pop("details")
        if type(details) is not list or len(details) > len(DETAIL_FIELDS):
            raise OutputProblem("invalid_details", "details")
        for detail in details:
            if type(detail) is not dict or set(detail) != {"field", "value"}:
                raise OutputProblem("invalid_details", "details")
            field = detail["field"]
            if type(field) is not str or field not in DETAIL_FIELDS or field in data:
                raise OutputProblem("invalid_details", "details")
            value = detail["value"]
            if field == "qualifiers":
                if (type(value) is not list or len(value) > 32 or any(type(v) is not dict or set(v) != {"key", "value"}
                        or type(v["key"]) is not str or type(v["value"]) is not str for v in value)
                        or len({v["key"] for v in value}) != len(value)):
                    raise OutputProblem("invalid_qualifiers", "details.qualifiers")
                value = {v["key"]: v["value"] for v in value}
            data[field] = value
    if "target" in data:
        alias = data.pop("target")
        if alias is not None:
            if type(alias) is not str or alias not in manifest.get("fact_handles", {}):
                raise OutputProblem("unknown_target", "target")
            if "target_atom_id" in data or "expected_revision" in data:
                raise OutputProblem("ambiguous_target", "target")
            reference = manifest["fact_handles"][alias]
            data.update(target_atom_id=reference["atom_id"], expected_revision=reference["revision"])
    for key in ("predicate", "object", "representation"):
        if data.get(key, "") is None:
            data.pop(key)
    return _coerce_soft_details(data)


def _groups(data, manifest):
    groups = set()
    if type(data) is not dict or "details" in data or "target" in data:
        return groups
    target = data.get("target_atom_id")
    if isinstance(target, str):
        groups.add("target:" + target)
        for fact in manifest.get("fact_records", {}).values():
            if fact["atom_id"] == target:
                groups.add("fact:" + fact["fact_key"])
    if isinstance(data.get("predicate"), str) and data["predicate"]:
        groups.add("fact:" + digest([data.get("namespace", "personal"), data.get("subject", "self"),
                                     data["predicate"], data.get("qualifiers", {})]))
    if data.get("operation") == "no_op":
        groups.add("no_op")
    return groups


def proposal_for_repair(proposal, manifest):
    data = {k: v for k, v in proposal.items() if k in PROPOSAL_PROPERTIES}
    handles = manifest.get("handles") or {"s" + str(index + 1): evidence for index, evidence in enumerate(manifest["evidence"])}
    data["evidence_handles"] = [alias for alias, evidence in handles.items() if evidence in proposal["evidence"]]
    if proposal.get("target"):
        data.update(target_atom_id=proposal["target"]["atom_id"], expected_revision=proposal["target"]["revision"])
    return data


def validate_output(reflection, context, manifest, output):
    candidates, identifiers = [], {}
    resolver = ManifestResolver(reflection, manifest)
    allowed = manifest.get("repair_item_ids")
    for index, entry in enumerate(output["proposals"]):
        identifier, raw, normalized = "item-" + str(index + 1).zfill(4), entry, entry
        problem, proposal = None, None
        try:
            if type(entry) is not dict:
                raise OutputProblem("invalid_proposal")
            if "proposal" in entry:
                if set(entry) != {"item_id", "proposal"}:
                    raise OutputProblem("invalid_proposal")
                identifier, raw = entry["item_id"], entry["proposal"]
            else:
                raw = {k: v for k, v in entry.items() if k != "item_id"}
                identifier = entry.get("item_id") or identifier
            text(identifier, "item_id", 128)
            if identifier.startswith("pending:"):
                raise OutputProblem("invalid_item_id")
            if allowed is not None and identifier not in allowed:
                raise OutputProblem("unexpected_repair_item")
            normalized = normalize_proposal(raw, manifest)
            proposal = decode_proposal(resolver, context, "", normalized)
            proposal = reflection.memory.writer.prepare_proposal(proposal)
            action = {"correct": "memory.correct", "retract": "memory.retract"}.get(proposal.operation, "memory.propose")
            reflection.memory.writer._validate_proposal(context, proposal, action)
            if proposal.target:
                # Validate the exact version before deciding independent groups.
                reflection.memory.writer._head(context, proposal.target)
        except OutputProblem as error:
            problem = {"code": error.code, "path": error.path}
        except TaskError as error:
            problem = {"code": error.code, "path": "proposal"}
        except (ValueError, TypeError, KeyError):
            problem = {"code": "invalid_proposal", "path": "proposal"}
        if type(identifier) is not str or not identifier.strip() or len(identifier) > 128:
            identifier = "item-" + str(index + 1).zfill(4)
        candidate = {"item_id": identifier, "raw": raw, "groups": _groups(normalized, manifest),
                     "proposal": json.loads(encode(proposal)) if proposal else None, "problem": problem, "kind": "proposal"}
        candidates.append(candidate)
        identifiers.setdefault(identifier, []).append(candidate)
    rejected = []
    timezone_name = "Asia/Shanghai"
    try:
        settings = reflection.memory.settings.read(context)[0] if reflection.memory.settings else {}
        timezone_name = settings.get("memory.reflection_timezone", timezone_name)
    except Exception:
        pass
    anniversary_candidates, anniversary_rejected = anniversaries.validate_candidates(
        output.get("anniversaries", []), context=context,
        resolve_evidence=lambda handle: resolver.resolve(context, None, handle),
        timezone_name=timezone_name, at=reflection.tasks.now())
    rejected.extend(anniversary_rejected)
    pending = {v["operation_id"]: v for v in manifest.get("pending", [])}
    for index, decision in enumerate(output.get("pending_decisions", [])):
        identifier, groups, problem = "decision-" + str(index + 1), set(), None
        try:
            if type(decision) is not dict or set(decision) != {"operation_id", "decision"}:
                raise OutputProblem("invalid_decision")
            operation_id = decision["operation_id"]
            if type(operation_id) is not str or operation_id not in pending or decision["decision"] not in ("accept", "discard"):
                raise OutputProblem("invalid_decision")
            identifier = "pending:" + operation_id
            if allowed is not None and identifier not in allowed:
                raise OutputProblem("unexpected_repair_item")
            proposal = pending[operation_id]["proposal"]
            groups = {"fact:" + codec.proposal(proposal).fact_key}
            if proposal.get("target"):
                groups.add("target:" + proposal["target"]["atom_id"])
        except (ValueError, TypeError, KeyError):
            problem = {"code": "invalid_decision", "path": "pending_decisions"}
        candidate = {"item_id": identifier, "raw": decision, "groups": groups,
                     "problem": problem, "kind": "decision"}
        candidates.append(candidate)
        identifiers.setdefault(identifier, []).append(candidate)
    for shared in identifiers.values():
        if len(shared) > 1:
            # There is no trustworthy per-item receipt identity to salvage here.
            raise OutputProblem("duplicate_item_id", "proposals")
    targets = {}
    for candidate in candidates:
        for group in candidate["groups"]:
            if group.startswith("target:"):
                targets.setdefault(group, []).append(candidate)
    for shared in targets.values():
        if len(shared) > 1:
            for candidate in shared:
                candidate["problem"] = {"code": "conflicting_target", "path": "target"}
    decision_groups = set().union(*(c["groups"] for c in candidates if c["kind"] == "decision"))
    for candidate in candidates:
        if candidate["kind"] == "proposal" and candidate["groups"] & decision_groups:
            candidate["problem"] = {"code": "overlapping_pending_decision", "path": "proposal"}
    # Do not split a change with an unresolved dependent/conflicting member.
    # An unidentifiable bad item may affect any fact, so keep that batch together.
    blocked = set().union(*(c["groups"] for c in candidates if c["problem"]))
    unknown = any(c["problem"] and not c["groups"] for c in candidates)
    changed = True
    while changed:
        changed = False
        for candidate in candidates:
            if not candidate["problem"] and (unknown or blocked & candidate["groups"]):
                candidate["problem"] = {"code": "related_item_unresolved", "path": "proposal"}
                blocked.update(candidate["groups"])
                changed = True
    proposals, decisions = [], []
    for candidate in candidates:
        if candidate["problem"]:
            rejected.append({k: candidate[k] for k in ("item_id", "raw", "kind", "problem")})
        elif candidate["kind"] == "proposal":
            proposals.append({"item_id": candidate["item_id"], "proposal": candidate["proposal"]})
        else:
            decisions.append(candidate["raw"])
    # A repair must account for every unresolved item; omission is not success.
    if allowed is not None:
        for identifier in sorted(set(allowed) - set(identifiers)):
            rejected.append({"item_id": identifier, "raw": None, "kind": "missing",
                             "problem": {"code": "missing_repair_item", "path": "proposals"}})
    return {"summary": redact_text(output["summary"]), "proposals": proposals,
            "pending_decisions": decisions, "anniversaries": anniversary_candidates,
            "rejected": rejected}
