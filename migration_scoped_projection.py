"""REQ-041 profile, memory and learning scoped projection synchronizer."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import threading
import time
from typing import Any, Callable

try:
    from .identity_namespace import NamespaceContext
    from .expression_scope_ownership import (
        ExpressionScopeError,
        bind_expression_item,
        bind_expression_profile,
    )
    from .scoped_domain_contract import build_scoped_domain_payload
    from .unified_person_registry import UnifiedPersonRegistry
    from .authoritative_private_memory import (
        AuthoritativePrivateMemoryError,
        AuthoritativePrivateMemoryStore,
    )
except ImportError:  # pragma: no cover - direct-module test compatibility
    from identity_namespace import NamespaceContext
    from expression_scope_ownership import (
        ExpressionScopeError,
        bind_expression_item,
        bind_expression_profile,
    )
    from scoped_domain_contract import build_scoped_domain_payload
    from unified_person_registry import UnifiedPersonRegistry
    from authoritative_private_memory import (
        AuthoritativePrivateMemoryError,
        AuthoritativePrivateMemoryStore,
    )


_PRIVATE_MEMORY_FIELDS = (
    "companion_memory", "intent_profile", "dialogue_episodes", "open_loops",
    "behavior_habits", "action_preferences", "action_consequences", "state_continuity",
    "recent_reply_topics", "birthday_profile", "birthday_curiosity_opt_out",
    "birthday_curiosity_asked_at", "birthday_curiosity_answered_at",
)
_GROUP_MEMORY_FIELDS = (
    "recent_messages", "recent_bot_replies", "slang_terms", "slang_meanings", "topic_signatures", "topic_threads",
    "group_episodes", "relationship_edges", "atmosphere", "interjection_feedback",
)
_MEMBER_PROFILE_FIELDS = (
    "name", "identity_name", "group_role", "group_role_label", "count", "last_seen",
    "display_name_events",
)
_RULE_EVIDENCE_FIELDS = (
    "samples", "pending_samples", "scene_profiles", "recent_phrases", "endings", "expression_rules",
)
_RECORD_PREFIX = "req041-"


class ScopedProjectionError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _persona_ref(source_scope: str) -> str:
    value = str(source_scope or "").strip()
    if value == "default":
        return "default"
    return "persona-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def scoped_persona_ref(active_persona: Any = "") -> str:
    value = str(active_persona or "").strip()
    source_scope = "default" if not value else "persona:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return _persona_ref(source_scope)


def _group_ref(persona_id: str, group_id: Any) -> str:
    raw = str(group_id or "").strip()
    if not raw:
        return ""
    return "group-" + hashlib.sha256(f"{persona_id}:{raw}".encode("utf-8")).hexdigest()[:32]


def scoped_group_ref(persona_id: str, group_id: Any) -> str:
    return _group_ref(persona_id, group_id)


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth > 7:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        return value[:8000]
    if isinstance(value, list):
        return [_bounded(item, depth=depth + 1) for item in value[-256:]]
    if isinstance(value, dict):
        return {
            str(key)[:96]: _bounded(item, depth=depth + 1)
            for key, item in list(value.items())[:256]
            if isinstance(key, str)
        }
    return None


@dataclass(frozen=True, slots=True)
class ScopedProjectionRecord:
    context: NamespaceContext
    record_kind: str
    record_id: str
    payload: dict[str, Any]


class ScopedProjectionSynchronizer:
    """Build and synchronize bounded legacy projections without changing legacy data."""

    def __init__(
        self,
        *,
        read: Callable[..., dict[str, Any]],
        list_records: Callable[..., dict[str, Any]],
        upsert: Callable[..., dict[str, Any]],
        tombstone: Callable[..., dict[str, Any]],
        migration_epoch: str,
        policy_version: str,
        tombstone_identity_scopes: Callable[..., dict[str, Any]] | None = None,
        erase_group_scopes: Callable[..., dict[str, Any]] | None = None,
        erase_persona_scopes: Callable[..., dict[str, Any]] | None = None,
        observability: Any = None,
    ) -> None:
        self._read = read
        self._list = list_records
        self._upsert = upsert
        self._tombstone = tombstone
        self._tombstone_identity_scopes = (
            tombstone_identity_scopes if callable(tombstone_identity_scopes) else None
        )
        self._erase_group_scopes = erase_group_scopes if callable(erase_group_scopes) else None
        self._erase_persona_scopes = erase_persona_scopes if callable(erase_persona_scopes) else None
        self.migration_epoch = str(migration_epoch or "").strip()
        self.policy_version = str(policy_version or "").strip()
        if not self.migration_epoch or not self.policy_version:
            raise ScopedProjectionError("scoped_projection_contract_invalid")
        self._state_lock = threading.RLock()
        self._ready_scopes: set[str] = set()
        self._projection_cache: dict[str, dict[str, Any]] = {}
        self._observability = observability

    def mark_dirty(self) -> None:
        with self._state_lock:
            evicted = len(self._projection_cache)
            self._ready_scopes.clear()
            self._projection_cache.clear()
        if evicted and self._observability is not None:
            self._observability.cache_event(
                "scoped_projection", "eviction", size=0,
            )

    def is_ready(self, context: NamespaceContext) -> bool:
        with self._state_lock:
            return context.cache_scope() in self._ready_scopes

    @staticmethod
    def _projection_fields(records: list[ScopedProjectionRecord]) -> dict[str, dict[str, Any]]:
        projected: dict[str, dict[str, Any]] = {}
        for record in records:
            scope = record.context.cache_scope()
            fields = projected.setdefault(scope, {})
            payload = record.payload
            content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
            domain = str(payload.get("domain") or "")
            if domain in {"profile", "memory"}:
                fields.update(deepcopy(content))
                continue
            expression_profile = fields.setdefault("expression_profile", {})
            if record.record_kind == "rule":
                rules = content.get("rules") if isinstance(content.get("rules"), list) else []
                if payload.get("approval_state") == "approved":
                    expression_profile["learned_rules"] = deepcopy(rules)
                elif payload.get("approval_state") == "pending":
                    expression_profile["pending_rules"] = deepcopy(rules)
            elif record.record_kind == "evidence":
                expression_profile.update(deepcopy(content))
        return projected

    def _context(
        self,
        *,
        kind: str,
        persona_id: str,
        identity_id: str = "",
        group_id: str = "",
        assurance: str = "verified",
    ) -> NamespaceContext:
        context = NamespaceContext(
            kind=kind, persona_id=persona_id, identity_id=identity_id, group_id=group_id,
            assurance=assurance, profile_status="active", policy_version=self.policy_version,
            migration_epoch=self.migration_epoch,
        )
        if context.errors():
            raise ScopedProjectionError(context.errors()[0])
        return context

    @staticmethod
    def _record(
        context: NamespaceContext,
        *,
        record_kind: str,
        record_id: str,
        domain: str,
        content: Any,
        approval_state: str = "not_applicable",
        source_revision: int = 0,
        approved_by: str = "",
    ) -> ScopedProjectionRecord:
        return ScopedProjectionRecord(
            context=context,
            record_kind=record_kind,
            record_id=record_id,
            payload=build_scoped_domain_payload(
                domain=domain, source_kind=context.kind, content=_bounded(content),
                approval_state=approval_state, source_revision=source_revision,
                approved_by=approved_by,
            ),
        )

    @staticmethod
    def _bound_expression_items(
        items: Any,
        context: NamespaceContext,
        *,
        approval_state: str,
        default_approved_by: str = "",
    ) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        result: list[dict[str, Any]] = []
        for raw in items:
            item = raw if isinstance(raw, dict) else {"legacy_value": deepcopy(raw)}
            existing = item.get("scope_binding") if isinstance(item.get("scope_binding"), dict) else {}
            approved_by = str(existing.get("approved_by") or default_approved_by)
            try:
                result.append(bind_expression_item(
                    item, context, approval_state=approval_state, approved_by=approved_by,
                ))
            except (ExpressionScopeError, TypeError, ValueError):
                # A pre-existing cross-scope or malformed binding is never repaired by guessing.
                continue
        return result

    def _learning_records(
        self, context: NamespaceContext, profile: Any, *, prefix: str
    ) -> list[ScopedProjectionRecord]:
        if not isinstance(profile, dict):
            return []
        try:
            bound_profile = bind_expression_profile(profile, context)
        except (ExpressionScopeError, TypeError, ValueError):
            return []
        source_revision = int(bound_profile.get("scope_revision") or 1)
        result: list[ScopedProjectionRecord] = []
        approved = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
        pending = profile.get("pending_rules") if isinstance(profile.get("pending_rules"), list) else []
        rejected = profile.get("rejected_rules") if isinstance(profile.get("rejected_rules"), list) else []
        revoked = profile.get("revoked_rules") if isinstance(profile.get("revoked_rules"), list) else []
        approved = self._bound_expression_items(
            approved, context, approval_state="approved",
            default_approved_by="administrator" if context.kind == "persona_global" else "legacy_migration",
        )
        if context.kind == "persona_global":
            approved = [
                item for item in approved
                if str(item.get("scope_binding", {}).get("approved_by") or "") == "administrator"
            ]
        approved_actors = {
            str(item.get("scope_binding", {}).get("approved_by") or "")
            for item in approved if isinstance(item.get("scope_binding"), dict)
        }
        envelope_approved_by = next(iter(approved_actors)) if len(approved_actors) == 1 else "multiple_approvers"
        pending = [] if context.kind == "persona_global" else self._bound_expression_items(
            pending, context, approval_state="pending"
        )
        rejected = self._bound_expression_items(
            rejected, context, approval_state="rejected", default_approved_by="administrator",
        )
        revoked = self._bound_expression_items(
            revoked, context, approval_state="revoked", default_approved_by="administrator",
        )
        evidence: dict[str, Any] = {} if context.kind == "persona_global" else {
            "scope_revision": source_revision,
            "scope_ownership": deepcopy(bound_profile["scope_ownership"]),
        }
        for key in (() if context.kind == "persona_global" else _RULE_EVIDENCE_FIELDS):
            value = profile.get(key)
            if not _present(value):
                continue
            if key in {"samples", "pending_samples", "expression_rules"} and isinstance(value, list):
                state = "approved" if key == "samples" else "pending"
                value = self._bound_expression_items(
                    value, context, approval_state=state,
                    default_approved_by="legacy_migration" if state == "approved" else "",
                )
            if _present(value):
                evidence[key] = deepcopy(value)
        if approved:
            result.append(self._record(
                context, record_kind="rule", record_id=f"{prefix}-rule-approved", domain="learning",
                content={"rules": approved}, approval_state="approved",
                source_revision=source_revision, approved_by=envelope_approved_by,
            ))
        if pending:
            result.append(self._record(
                context, record_kind="rule", record_id=f"{prefix}-rule-pending", domain="learning",
                content={"rules": pending}, approval_state="pending",
                source_revision=source_revision,
            ))
        if rejected:
            result.append(self._record(
                context, record_kind="rule", record_id=f"{prefix}-rule-rejected", domain="learning",
                content={"rules": rejected}, approval_state="rejected",
                source_revision=source_revision, approved_by="administrator",
            ))
        if revoked:
            result.append(self._record(
                context, record_kind="rule", record_id=f"{prefix}-rule-revoked", domain="learning",
                content={"rules": revoked}, approval_state="revoked",
                source_revision=source_revision, approved_by="administrator",
            ))
        if evidence:
            result.append(self._record(
                context, record_kind="evidence", record_id=f"{prefix}-rule-evidence", domain="learning",
                content=evidence, approval_state="pending", source_revision=source_revision,
            ))
        return result

    @staticmethod
    def _formal_people(snapshot: dict[str, Any]) -> list[str]:
        root = snapshot.get("unified_person") if isinstance(snapshot.get("unified_person"), dict) else {}
        profiles = root.get("profiles") if isinstance(root.get("profiles"), dict) else {}
        return sorted(
            str(person_id) for person_id, profile in profiles.items()
            if isinstance(profile, dict) and profile.get("profile_status", "active") == "active"
        )

    def build_records(
        self, snapshot: dict[str, Any], *, source_scope: str = "default"
    ) -> tuple[list[ScopedProjectionRecord], list[NamespaceContext]]:
        if not isinstance(snapshot, dict):
            raise ScopedProjectionError("scoped_projection_snapshot_invalid")
        persona_id = _persona_ref(source_scope)
        reset_saga = snapshot.get("_req041_persona_reset_saga")
        if (
            isinstance(reset_saga, dict)
            and reset_saga.get("state") == "confirmed"
            and str(reset_saga.get("persona_id") or "").strip() == persona_id
        ):
            return [], []
        registry = UnifiedPersonRegistry(snapshot)
        people = self._formal_people(snapshot)
        records: list[ScopedProjectionRecord] = []
        contexts: dict[str, NamespaceContext] = {}

        def remember(context: NamespaceContext) -> None:
            contexts[context.cache_scope()] = context

        persona_context = self._context(
            kind="persona_global", persona_id=persona_id, identity_id="", group_id="",
        )
        remember(persona_context)
        records.extend(self._learning_records(
            persona_context,
            snapshot.get("_req041_persona_expression_profile"),
            prefix="req041-persona",
        ))

        users = snapshot.get("users") if isinstance(snapshot.get("users"), dict) else {}
        by_person: dict[str, list[dict[str, Any]]] = {}
        for legacy_key, raw_user in users.items():
            if not isinstance(raw_user, dict):
                continue
            person_id = str(raw_user.get("unified_person_id") or "").strip()
            subject = str(raw_user.get("identity_subject_id") or raw_user.get("user_id") or legacy_key or "").strip()
            if person_id in people and subject and registry.matches_person_subject(person_id, subject):
                by_person.setdefault(person_id, []).append(raw_user)

        for person_id in people:
            matched = by_person.get(person_id, [])
            resolution = registry.formal_namespace_for_person(
                person_id, kind="private", policy_version=self.policy_version,
                migration_epoch=self.migration_epoch, purpose="profile_read",
            )
            raw_context = resolution.get("context") if isinstance(resolution, dict) else None
            if not resolution.get("ok") or not isinstance(raw_context, dict):
                continue
            context = self._context(
                kind="private", persona_id=persona_id, identity_id=person_id,
                assurance=str(raw_context.get("assurance") or "verified"),
            )
            remember(context)
            facts_result = registry.identity_profile_facts(person_id)
            facts = facts_result.get("facts") if facts_result.get("ok") else {}
            user = matched[0] if len(matched) == 1 else None
            authoritative_content: dict[str, Any] | None = None
            authoritative_revision = 0
            try:
                authoritative = AuthoritativePrivateMemoryStore(snapshot).read(person_id)
            except AuthoritativePrivateMemoryError as exc:
                raise ScopedProjectionError(str(exc)) from exc
            authoritative_record = (
                authoritative.get("record") if isinstance(authoritative, dict) else None
            )
            if isinstance(authoritative_record, dict):
                raw_content = authoritative_record.get("content")
                if not isinstance(raw_content, dict):
                    raise ScopedProjectionError("private_memory_record_invalid")
                authoritative_content = raw_content
                authoritative_revision = int(authoritative_record.get("revision") or 0)
            preferred = str((facts or {}).get("preferred_address") or "").strip()
            display_name = str((facts or {}).get("display_name") or "").strip()
            canonical_name = preferred or (display_name if display_name != "unknown_person" else "")
            profile_content: dict[str, Any] = {}
            if canonical_name:
                profile_content["nickname"] = canonical_name
            elif isinstance(user, dict) and _present(user.get("nickname")):
                profile_content["nickname"] = deepcopy(user["nickname"])
            for source_key in ("style", "profile_origin", "auto_profile_created"):
                if _present((facts or {}).get(source_key)):
                    profile_content[source_key] = deepcopy(facts[source_key])
                elif isinstance(user, dict) and _present(user.get(source_key)):
                    profile_content[source_key] = deepcopy(user[source_key])
            if facts_result.get("ok"):
                profile_content["profile_fact_revision"] = int(
                    facts_result.get("profile_fact_revision") or 1
                )
            if profile_content:
                records.append(self._record(
                    context, record_kind="profile_fact", record_id="req041-private-profile",
                    domain="profile", content=profile_content,
                ))
            for field in _PRIVATE_MEMORY_FIELDS:
                memory_source = authoritative_content if authoritative_content is not None else user
                if isinstance(memory_source, dict) and _present(memory_source.get(field)):
                    records.append(self._record(
                        context, record_kind="memory", record_id=f"req041-private-memory-{field.replace('_', '-')}",
                        domain="memory", content={field: deepcopy(memory_source[field])},
                        source_revision=authoritative_revision,
                    ))
            if isinstance(user, dict):
                records.extend(self._learning_records(
                    context, user.get("expression_profile"), prefix="req041-private"
                ))

        root = snapshot.get("unified_person") if isinstance(snapshot.get("unified_person"), dict) else {}
        links = root.get("identity_links") if isinstance(root.get("identity_links"), dict) else {}
        subject_people: dict[str, set[str]] = {}
        for link in links.values():
            if not isinstance(link, dict) or link.get("status") != "active":
                continue
            identity = link.get("identity") if isinstance(link.get("identity"), dict) else {}
            subject = str(identity.get("platform_subject_id") or "").strip()
            person_id = str(link.get("person_id") or "").strip()
            if subject and person_id in people and registry.matches_person_subject(person_id, subject):
                subject_people.setdefault(subject, set()).add(person_id)

        groups = snapshot.get("groups") if isinstance(snapshot.get("groups"), dict) else {}
        reset_sagas = (
            snapshot.get("_req041_group_reset_sagas")
            if isinstance(snapshot.get("_req041_group_reset_sagas"), dict)
            else {}
        )
        resetting_groups = {
            str(saga.get("group_id") or "").strip()
            for saga in reset_sagas.values()
            if isinstance(saga, dict)
            and saga.get("state") in {"confirmed", "config_pending"}
            and str(saga.get("persona_id") or "").strip() == persona_id
        }
        for legacy_group_key, raw_group in groups.items():
            if not isinstance(raw_group, dict):
                continue
            raw_group_id = str(raw_group.get("group_id") or legacy_group_key or "").strip()
            if raw_group_id in resetting_groups:
                continue
            group_id = _group_ref(persona_id, raw_group_id)
            if not group_id:
                continue
            shared = self._context(kind="group_shared", persona_id=persona_id, group_id=group_id)
            remember(shared)
            for field in _GROUP_MEMORY_FIELDS:
                if _present(raw_group.get(field)):
                    records.append(self._record(
                        shared, record_kind="memory", record_id=f"req041-group-memory-{field.replace('_', '-')}",
                        domain="memory", content={field: deepcopy(raw_group[field])},
                    ))
            records.extend(self._learning_records(shared, raw_group.get("expression_profile"), prefix="req041-group"))
            members = raw_group.get("members") if isinstance(raw_group.get("members"), dict) else {}
            for subject, member in members.items():
                candidates = subject_people.get(str(subject), set())
                if len(candidates) != 1 or not isinstance(member, dict):
                    continue
                person_id = next(iter(candidates))
                assurance = "verified"
                resolution = registry.formal_namespace_for_person(
                    person_id, kind="group_member", group_id=group_id,
                    policy_version=self.policy_version, migration_epoch=self.migration_epoch,
                    purpose="profile_read",
                )
                raw_context = resolution.get("context") if isinstance(resolution, dict) else None
                if not resolution.get("ok") or not isinstance(raw_context, dict):
                    continue
                assurance = str(raw_context.get("assurance") or assurance)
                member_context = self._context(
                    kind="group_member", persona_id=persona_id, identity_id=person_id,
                    group_id=group_id, assurance=assurance,
                )
                remember(member_context)
                member_profile = {
                    key: deepcopy(member[key]) for key in _MEMBER_PROFILE_FIELDS if _present(member.get(key))
                }
                if member_profile:
                    records.append(self._record(
                        member_context, record_kind="profile_fact", record_id="req041-group-member-profile",
                        domain="profile", content=member_profile,
                    ))
                member_memory = {
                    key: deepcopy(member[key]) for key in ("recent_phrases", "display_name_events")
                    if _present(member.get(key))
                }
                if member_memory:
                    records.append(self._record(
                        member_context, record_kind="memory", record_id="req041-group-member-observation",
                        domain="memory", content=member_memory,
                    ))
        return records, list(contexts.values())

    def sync_snapshot(self, snapshot: dict[str, Any], *, source_scope: str = "default") -> dict[str, Any]:
        started = time.perf_counter()
        records, contexts = self.build_records(snapshot, source_scope=source_scope)
        desired: dict[tuple[str, str], set[str]] = {}
        counts = {"created": 0, "updated": 0, "unchanged": 0, "cleared": 0, "tombstoned": 0, "errors": 0}
        errors: list[str] = []
        for record in records:
            scope = record.context.cache_scope()
            desired.setdefault((scope, record.record_kind), set()).add(record.record_id)
            current = self._read(record.context, record_kind=record.record_kind, record_id=record.record_id)
            if not isinstance(current, dict) or current.get("ok") is not True:
                counts["errors"] += 1
                errors.append(str((current or {}).get("code") or "scoped_read_failed")[:80])
                continue
            existing = current.get("record") if current.get("code") == "found" else None
            if isinstance(existing, dict) and _canonical(existing.get("payload")) == _canonical(record.payload):
                counts["unchanged"] += 1
                continue
            revision = int(existing.get("revision") or 0) + 1 if isinstance(existing, dict) else 1
            event_id = "req041-sync-" + hashlib.sha256(
                f"{scope}:{record.record_kind}:{record.record_id}:{revision}:{_hash(record.payload)}".encode("utf-8")
            ).hexdigest()[:48]
            result = self._upsert(
                record.context, record_kind=record.record_kind, record_id=record.record_id,
                revision=revision, payload=record.payload, event_id=event_id,
            )
            if not isinstance(result, dict) or result.get("ok") is not True:
                counts["errors"] += 1
                errors.append(str((result or {}).get("code") or "scoped_upsert_failed")[:80])
                continue
            counts["updated" if isinstance(existing, dict) else "created"] += 1

        context_map = {context.cache_scope(): context for context in contexts}
        for scope, context in context_map.items():
            for record_kind in ("profile_fact", "memory", "rule", "evidence"):
                listed = self._list(context, record_kind=record_kind, limit=1000)
                if not isinstance(listed, dict) or listed.get("ok") is not True:
                    counts["errors"] += 1
                    errors.append(str((listed or {}).get("code") or "scoped_list_failed")[:80])
                    continue
                keep = desired.get((scope, record_kind), set())
                for existing in listed.get("records") if isinstance(listed.get("records"), list) else []:
                    record_id = str(existing.get("record_id") or "") if isinstance(existing, dict) else ""
                    if not record_id.startswith(_RECORD_PREFIX) or record_id in keep:
                        continue
                    existing_payload = existing.get("payload") if isinstance(existing.get("payload"), dict) else {}
                    if existing_payload.get("content") in ({}, [], "", None):
                        continue
                    domain = str(existing_payload.get("domain") or "")
                    source_kind = str(existing_payload.get("source_kind") or context.kind)
                    approval_state = str(existing_payload.get("approval_state") or "not_applicable")
                    approved_by = str(existing_payload.get("approved_by") or "")
                    try:
                        cleared_payload = build_scoped_domain_payload(
                            domain=domain, source_kind=source_kind, content={},
                            approval_state=approval_state, approved_by=approved_by,
                        )
                    except Exception:
                        counts["errors"] += 1
                        errors.append("scoped_clear_payload_invalid")
                        continue
                    revision = int(existing.get("revision") or 0) + 1
                    event_id = "req041-clear-" + hashlib.sha256(
                        f"{scope}:{record_kind}:{record_id}:{revision}".encode("utf-8")
                    ).hexdigest()[:48]
                    result = self._upsert(
                        context, record_kind=record_kind, record_id=record_id,
                        revision=revision, payload=cleared_payload, event_id=event_id,
                    )
                    if isinstance(result, dict) and result.get("ok") is True:
                        counts["cleared"] += 1
                    else:
                        counts["errors"] += 1
                        errors.append(str((result or {}).get("code") or "scoped_clear_failed")[:80])
        involved_scopes = {context.cache_scope() for context in contexts}
        projected = self._projection_fields(records)
        with self._state_lock:
            if counts["errors"] == 0:
                self._ready_scopes.update(involved_scopes)
                for scope in involved_scopes:
                    self._projection_cache[scope] = deepcopy(projected.get(scope, {}))
            else:
                self._ready_scopes.difference_update(involved_scopes)
                for scope in involved_scopes:
                    self._projection_cache.pop(scope, None)
            cache_size = len(self._projection_cache)
        if self._observability is not None:
            self._observability.observe(
                "scoped_sync", (time.perf_counter() - started) * 1000.0, external=True,
            )
            self._observability.cache_event(
                "scoped_projection", "cold_start" if counts["errors"] == 0 else "stale_reject",
                size=cache_size,
            )
        return {
            "ok": counts["errors"] == 0,
            "code": "scoped_projection_synced" if counts["errors"] == 0 else "scoped_projection_degraded",
            "source_scope": source_scope,
            "records": len(records),
            **counts,
            "error_codes": sorted(set(errors))[:16],
        }

    def read_projection(self, context: NamespaceContext) -> dict[str, Any]:
        started = time.perf_counter()
        scope = context.cache_scope()
        with self._state_lock:
            if scope not in self._ready_scopes:
                if self._observability is not None:
                    self._observability.cache_event(
                        "scoped_projection", "stale_reject", namespace_kind=context.kind,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                        size=len(self._projection_cache),
                    )
                return {"ok": False, "code": "scoped_projection_not_reconciled", "fields": {}}
            fields = deepcopy(self._projection_cache.get(scope, {}))
            cache_size = len(self._projection_cache)
        if self._observability is not None:
            self._observability.cache_event(
                "scoped_projection", "hit", namespace_kind=context.kind,
                latency_ms=(time.perf_counter() - started) * 1000.0, size=cache_size,
            )
        return {"ok": True, "code": "scoped_projection_read", "fields": fields}

    def archive_identity_scopes(
        self,
        context: NamespaceContext,
        *,
        operation_id: str,
        reason_code: str = "person_archive",
    ) -> dict[str, Any]:
        """Invalidate hot views before invoking Memory's atomic person archive."""
        self.mark_dirty()
        if (
            context.kind != "private"
            or context.group_id
            or not context.identity_id
            or context.migration_epoch != self.migration_epoch
            or context.policy_version != self.policy_version
            or context.errors()
        ):
            return {"ok": False, "state": "rejected", "code": "scoped_identity_archive_context_invalid"}
        if self._tombstone_identity_scopes is None:
            return {"ok": False, "state": "degraded", "code": "scoped_identity_archive_unavailable"}
        result = self._tombstone_identity_scopes(
            context, operation_id=operation_id, reason_code=reason_code,
        )
        if not isinstance(result, dict):
            return {"ok": False, "state": "degraded", "code": "scoped_identity_archive_response_invalid"}
        return result

    def erase_group_scopes(
        self,
        context: NamespaceContext,
        *,
        operation_id: str,
        reason_code: str = "group_reset",
    ) -> dict[str, Any]:
        self.mark_dirty()
        if (
            context.kind != "group_shared" or context.identity_id or not context.group_id
            or context.migration_epoch != self.migration_epoch
            or context.policy_version != self.policy_version or context.errors()
        ):
            return {"ok": False, "state": "rejected", "code": "scoped_group_erase_context_invalid"}
        if self._erase_group_scopes is None:
            return {"ok": False, "state": "degraded", "code": "scoped_group_erase_unavailable"}
        result = self._erase_group_scopes(
            context, operation_id=operation_id, reason_code=reason_code,
        )
        if not isinstance(result, dict):
            return {"ok": False, "state": "degraded", "code": "scoped_group_erase_response_invalid"}
        return result

    def erase_persona_scopes(
        self,
        context: NamespaceContext,
        *,
        operation_id: str,
        reason_code: str = "persona_reset",
    ) -> dict[str, Any]:
        self.mark_dirty()
        if (
            context.kind != "persona_global" or context.identity_id or context.group_id
            or context.migration_epoch != self.migration_epoch
            or context.policy_version != self.policy_version or context.errors()
        ):
            return {"ok": False, "state": "rejected", "code": "scoped_persona_erase_context_invalid"}
        if self._erase_persona_scopes is None:
            return {"ok": False, "state": "degraded", "code": "scoped_persona_erase_unavailable"}
        result = self._erase_persona_scopes(
            context, operation_id=operation_id, reason_code=reason_code,
        )
        if not isinstance(result, dict):
            return {"ok": False, "state": "degraded", "code": "scoped_persona_erase_response_invalid"}
        return result


__all__ = [
    "ScopedProjectionError", "ScopedProjectionRecord", "ScopedProjectionSynchronizer",
    "scoped_group_ref", "scoped_persona_ref",
]
