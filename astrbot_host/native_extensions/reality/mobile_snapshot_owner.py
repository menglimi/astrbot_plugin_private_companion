"""Reality-owned RV-1 governance for existing mobile projections.

No collection, model calls, delivery or durable body store. Native mutations
invalidate subscribers synchronously; readers use the same gateway state lock.
The host authority is pinned to the currently published Private API instance.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import threading
import time
import weakref
from typing import Any
from uuid import uuid4


KINDS = ("location", "device", "activity", "telemetry")
STORES = {"location": "_mobile_locations", "device": "_mobile_device_status",
          "activity": "_mobile_activity", "telemetry": "_mobile_telemetry"}
REFERENCE_SECONDS = 60


def _encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _authority(request: dict) -> bytes:
    return _encode({key: request[key] for key in ("scope", "context", "binding_ref", "provider_id",
                                                 "provider_generation", "capability_id", "capability_version")})


def _disclosure(request: dict) -> bytes:
    return _encode({"scope": request["scope"], "context": {key: request["context"][key]
        for key in ("caller_ref", "subject", "audience_ref", "purpose", "sharing")}})


class OwnerFailure(Exception):
    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status, self.reason = status, reason


@dataclass
class _User:
    binding_revision: int = 0
    authorization_revision: int = 0
    subject_ref: str = ""
    authority_state: bytes | None = None
    policy: tuple | None = None
    epochs: dict[str, int] = field(default_factory=lambda: dict.fromkeys(KINDS, 0))


@dataclass(frozen=True)
class _Ticket:
    seal: object
    invocation: object
    user_id: str
    authority: bytes
    disclosure: bytes
    epochs: tuple
    sources: bytes
    authorized_until: datetime


@dataclass
class _V2Grant:
    event: object
    claims: bytes
    identity: bytes
    monotonic_expiry: float


class _V2RealityAccess:
    """Small V2 authority adapter for the Reality mobile owner.

    Mobile snapshots are admitted against the current V2 private context and
    native tool catalog owned by the same V2 controller and internal Reality
    service. No separately installed companion participates in admission.
    """

    def __init__(self, gateway: Any, host_api: Any) -> None:
        self.gateway = gateway
        self.host_api = host_api
        self.generation = "authorization:" + uuid4().hex
        self.revision = 0
        self._grants: dict[object, _V2Grant] = {}
        self._listeners: list[Any] = []
        self._lock = threading.RLock()
        self._thread_id = threading.get_ident()
        self._pending_invalidation = False

    def subscribe(self, listener: Any) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Any) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _api(self):
        api = self.gateway.v2_host_api()
        if api is None or api is not self.host_api:
            raise OwnerFailure("rejected", "access_denied")
        return api

    @staticmethod
    def _scope_document(context: Any) -> dict:
        scope = getattr(context, "scope", None)
        document = getattr(scope, "document", None)
        value = document() if callable(document) else None
        if (not isinstance(value, dict) or value.get("kind") != "private"
                or not value.get("native_scope_id")):
            raise OwnerFailure("rejected", "access_denied")
        return value

    def _claims_for(self, invocation: Any) -> tuple[dict, bytes]:
        context = getattr(invocation, "context", None)
        if context is None:
            raise OwnerFailure("rejected", "access_denied")
        scope = self._scope_document(context)
        actor = getattr(context, "actor", None)
        principal = getattr(actor, "principal", None)
        subject = getattr(actor, "subject", None)
        user_id = getattr(principal, "native_account_id", "")
        if not isinstance(user_id, str) or not user_id or user_id != scope.get("native_scope_id"):
            raise OwnerFailure("rejected", "access_denied")
        descriptor = getattr(invocation, "descriptor", None)
        tool = getattr(descriptor, "tool", None)
        if getattr(tool, "action", "") != "reality.snapshot.read":
            raise OwnerFailure("rejected", "access_denied")
        attestation = self._api().reality_mobile_context(user_id)
        if (not isinstance(attestation, dict)
                or attestation.get("schema") != "companion.reality-mobile-context.v1"
                or attestation.get("user_id") != user_id
                or attestation.get("context_key") != getattr(context, "key", "")
                or attestation.get("snapshot_authorized") is not True):
            raise OwnerFailure("rejected", "access_denied")
        subject_ref = str(attestation.get("subject_ref") or "")
        if not subject_ref or not getattr(subject, "subject_id", ""):
            raise OwnerFailure("rejected", "access_denied")
        scope_key = hashlib.sha256(_encode(scope)).hexdigest()[:24]
        caller_ref = "caller:" + hashlib.sha256(_encode([scope_key, subject_ref])).hexdigest()[:24]
        audience_ref = "audience:" + hashlib.sha256(_encode([getattr(context, "key", ""), subject_ref])).hexdigest()[:24]
        now = datetime.now(timezone.utc)
        deadline = getattr(invocation, "deadline_at", None)
        if not isinstance(deadline, datetime) or deadline.tzinfo is None or deadline <= now:
            raise OwnerFailure("rejected", "access_denied")
        claims = {
            "scope": scope,
            "user_id": user_id,
            "subject_ref": subject_ref,
            "caller_ref": caller_ref,
            "audience_ref": audience_ref,
            "authorization_ref": self.generation,
            "authorization_revision": self.revision,
            "purpose": "conversation_context",
            "sharing": {"sharing_mode": "session", "sharing_anchor": "sharing:" + audience_ref,
                        "sharing_policy_revision": 1},
            "execution": {"parent_task_id": getattr(invocation, "parent_task_id", ""),
                           "tool_call_id": getattr(invocation, "tool_call_id", "")},
            "budget": {"max_input_bytes": 131072, "max_output_bytes": 131072, "max_model_calls": 0},
            "trace_id": "trace:" + hashlib.sha256(_encode(getattr(context, "key", ""))).hexdigest()[:24],
            "deadline_at": deadline.isoformat(),
        }
        return claims, _encode({key: value for key, value in claims.items()
                                if key not in {"execution", "budget", "trace_id", "deadline_at"}})

    def resolve(self, invocation: Any) -> dict:
        with self._lock:
            self.refresh()
            existing = self._grants.get(invocation)
            if existing is not None:
                if time.monotonic() >= existing.monotonic_expiry:
                    self._grants.pop(invocation, None)
                else:
                    return json.loads(existing.claims)
            claims, identity = self._claims_for(invocation)
            self._grants[invocation] = _V2Grant(
                getattr(invocation, "context", None), _encode(claims), identity,
                time.monotonic() + max(0.0, (datetime.fromisoformat(
                    claims["deadline_at"]) - datetime.now(timezone.utc)).total_seconds()))
            return dict(claims)

    def refresh(self) -> None:
        with self._lock:
            for invocation, grant in tuple(self._grants.items()):
                if time.monotonic() >= grant.monotonic_expiry:
                    self._grants.pop(invocation, None)
                    continue
                try:
                    _claims, identity = self._claims_for(invocation)
                except Exception:
                    self.invalidate()
                    return
                if identity != grant.identity:
                    self.invalidate()
                    return

    def dependency_identity(self, invocation: Any) -> tuple[str, dict]:
        claims = self.resolve(invocation)
        return ("scope:" + hashlib.sha256(_encode(claims["scope"])).hexdigest()[:32],
                {key: value for key, value in claims.items()
                 if key in {"scope", "subject_ref", "caller_ref", "audience_ref", "sharing"}})

    def binding_state(self, pin: dict) -> str:
        try:
            user_id = pin["scope"]["native_scope_id"]
            attestation = self._api().reality_mobile_context(user_id)
            return "current" if isinstance(attestation, dict) and attestation.get("context_key") else "revoked"
        except Exception:
            return "unavailable"

    def invalidate(self, *, transient: bool = False) -> None:
        with self._lock:
            self._pending_invalidation = False
            self.revision += 1
            self._grants.clear()
            for listener in tuple(self._listeners):
                listener(transient=transient)


class MobileSnapshotOwner:
    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway
        self.generation = "generation:" + uuid4().hex
        self._seal = object()
        self._users: dict[str, _User] = {}
        self._host = None
        self._access = None
        self.provider_current = None
        self._listeners: list[weakref.WeakMethod] = []
        self._notices: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self._loop = None
        self._thread_id = None
        from .snapshot_dependencies import SnapshotDependencies
        directory = getattr(gateway, "data_dir", None)
        self.dependencies = SnapshotDependencies(directory, self.generation) if directory else None

    def bind(self, host_api: Any) -> "MobileSnapshotOwner":
        with self.gateway._mobile_state_lock:
            if host_api is not self.gateway.v2_host_api():
                raise OwnerFailure("rejected", "access_denied")
            access = self._access if self._host is host_api and isinstance(self._access, _V2RealityAccess) \
                else _V2RealityAccess(self.gateway, host_api)
            if self._host is not host_api or self._access is not access:
                if self._access is not None:
                    self._access.unsubscribe(self.invalidate_host)
                    self.rotate()
                self._host, self._access = host_api, access
                access.subscribe(self.invalidate_host)
            return self

    def subscribe(self, callback: Any) -> Any:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop and not self._loop.is_closed():
            raise OwnerFailure("unavailable", "provider_unavailable")
        self._loop, self._thread_id = loop, threading.get_ident()
        self._listeners = [ref for ref in self._listeners if ref() is not None]
        if len(self._listeners) >= 64:
            raise OwnerFailure("unavailable", "resource_limit")
        reference = weakref.WeakMethod(callback)
        self._listeners.append(reference)

        def unsubscribe() -> None:
            if reference in self._listeners:
                self._listeners.remove(reference)

        return unsubscribe

    def _user(self, user_id: str) -> _User:
        if user_id not in self._users:
            if len(self._users) >= 64:
                raise OwnerFailure("unavailable", "resource_limit")
            self._users[user_id] = _User()
        return self._users[user_id]

    @staticmethod
    def source_ref(user_id: str, kind: str) -> str:
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]
        return "source:mobile:" + digest + ":" + kind

    def invalidate(self, user_id: str, kinds: tuple = KINDS, *, withdraw: bool = True) -> None:
        with self.gateway._mobile_state_lock:
            state = self._user(user_id)
            for kind in kinds:
                state.epochs[kind] += 1
                if withdraw and self.dependencies is not None:
                    self.dependencies.revoke(source=self.source_ref(user_id, kind))
                for listener in tuple(self._listeners):
                    callback = listener()
                    if callback is not None:
                        if threading.get_ident() == self._thread_id:
                            callback(self.source_ref(user_id, kind))
                        elif self._loop is not None and not self._loop.is_closed():
                            self._loop.call_soon_threadsafe(callback, self.source_ref(user_id, kind))

    def invalidate_host(self, *, transient: bool = False) -> None:
        with self.gateway._mobile_state_lock:
            for user_id, state in self._users.items():
                state.authorization_revision += 1
                if transient:
                    state.authority_state = None
                self.invalidate(user_id, withdraw=not transient)

    def rotate(self) -> None:
        """Stop/unload changes generation before any asynchronous server cleanup."""
        with self.gateway._mobile_state_lock:
            self.generation = "generation:" + uuid4().hex
            for user_id in self._users:
                self.invalidate(user_id)

    def restore_sessions(self, user_id: str) -> None:
        """Bind persisted sessions to the current owner generation without dropping restored uploads."""
        with self.gateway._mobile_state_lock:
            state = self._user(user_id)
            state.binding_revision += 1
            now = time.time()
            for item in self.gateway._mobile_sessions.values():
                if item.get("user_id") != user_id or item.get("expires_at", 0) <= now:
                    continue
                subject = item.get("snapshot_subject_ref") or ""
                if subject and not state.subject_ref:
                    state.subject_ref = subject
                item["snapshot_binding_revision"] = state.binding_revision
                item["snapshot_provider_generation"] = self.generation

    def session_changed(self, user_id: str, session: dict | None = None) -> None:
        with self.gateway._mobile_state_lock:
            state = self._user(user_id)
            previous_binding = state.binding_revision
            previous_epochs = dict(state.epochs)
            state.binding_revision += 1
            self.invalidate(user_id)
            if session is not None:
                api = getattr(self.gateway, "v2_host_api", lambda: None)()
                try:
                    identity = api.reality_mobile_context(user_id) if api is not None else {}
                except Exception:
                    identity = {}
                subject = identity.get("subject_ref") if identity.get("schema") == "companion.reality-mobile-context.v1" else ""
                state.subject_ref = subject or ""
                state.authority_state = None
                session["snapshot_subject_ref"] = state.subject_ref
                session["snapshot_context_key"] = identity.get("context_key", "") if isinstance(identity, dict) else ""
                session["snapshot_session_ref"] = "session:" + uuid4().hex
            now = time.time()
            active_refs = set()
            for item in self.gateway._mobile_sessions.values():
                if item.get("user_id") == user_id:
                    item["snapshot_binding_revision"] = state.binding_revision
                    item["snapshot_provider_generation"] = self.generation
                    if (item.get("expires_at", 0) > now and self.session_authorized(item)
                            and item.get("snapshot_subject_ref") == state.subject_ref):
                        active_refs.add(item.get("snapshot_session_ref"))
            active_refs.discard(None)
            # Invalidate old read tickets, but keep samples from an unchanged
            # uploading session. Pairing/closing an observer must not erase a
            # phone's uploads or shorten their original retention deadline.
            for kind, store_name in STORES.items():
                store = getattr(self.gateway, store_name)
                item = store.get(user_id)
                stamp = item.get("_rv1") if isinstance(item, dict) else None
                refs = stamp.get("session_refs") if isinstance(stamp, dict) else None
                if (not refs or not set(refs) <= active_refs
                        or stamp.get("subject_ref") != state.subject_ref
                        or stamp.get("generation") != self.generation
                        or stamp.get("binding_revision") != previous_binding
                        or stamp.get("epoch") != previous_epochs[kind]
                        or stamp.get("retain_until", 0) <= now):
                    store.pop(user_id, None)
                    continue
                item["_rv1"] = dict(stamp, binding_revision=state.binding_revision, epoch=state.epochs[kind])
            screen = self.gateway._mobile_screen_clients.get(user_id)
            if isinstance(screen, dict) and screen.get("session_ref") not in active_refs:
                self.gateway._mobile_screen_clients.pop(user_id, None)

    def session_authorized(self, session: dict) -> bool:
        """Recheck formal sessions without changing legacy unattested sessions."""
        subject = session.get("snapshot_subject_ref")
        if not subject:
            return False
        user_id = session.get("user_id")
        if user_id != self.gateway._mobile_allowed_user_id():
            return False
        api = getattr(self.gateway, "v2_host_api", lambda: None)()
        try:
            context = api.reality_mobile_context(user_id) if api is not None else {}
        except Exception:
            return False
        return bool(isinstance(context, dict)
                    and context.get("schema") == "companion.reality-mobile-context.v1"
                    and context.get("subject_ref") == subject
                    and context.get("context_key") == session.get("snapshot_context_key"))

    def stamp(self, user_id: str, kind: str, item: dict, *, session: dict | None = None,
              previous: dict | None = None) -> None:
        """Absolute owner retention is set at ingestion, never inferred on read."""
        state = self._user(user_id)
        sessions = [entry for entry in self.gateway._mobile_sessions.values()
                    if entry.get("user_id") == user_id and entry.get("snapshot_subject_ref") == state.subject_ref
                    and entry.get("snapshot_session_ref") and entry.get("expires_at", 0) > time.time()]
        if (not state.subject_ref or not sessions or not session or not session.get("snapshot_session_ref")
                or not any(entry.get("snapshot_session_ref") == session["snapshot_session_ref"] for entry in sessions)):
            item.pop("_rv1", None)
            return  # Legacy / unattested inputs remain snapshot-only.
        received = float(item["received_at"])
        ttl = {"location": self.gateway._mobile_location_ttl(), "device": 86400,
               "activity": self.gateway._mobile_activity_ttl(), "telemetry": self.gateway._mobile_telemetry_ttl()}[kind]
        origin = next(entry for entry in sessions
                      if entry["snapshot_session_ref"] == session["snapshot_session_ref"])
        retain_until = min(received + ttl, float(origin["expires_at"]))
        session_refs = {origin["snapshot_session_ref"]}
        self.gateway._mobile_record_upload(origin, kind, received)
        if kind == "telemetry" and previous:
            # This legacy merge carries prior measurements/events. Without
            # per-component provenance its oldest bound governs the aggregate.
            old = previous.get("_rv1")
            if not isinstance(old, dict) or not old.get("session_refs") or old["retain_until"] <= time.time():
                item.pop("_rv1", None)
                return
            retain_until = min(retain_until, old["retain_until"])
            session_refs.update(old["session_refs"])
        item["_rv1"] = {"subject_ref": state.subject_ref, "binding_revision": state.binding_revision,
                        "generation": self.generation, "epoch": state.epochs[kind],
                        "retain_until": retain_until, "session_refs": sorted(session_refs)}

    def _claims(self, invocation: object) -> dict:
        if not callable(self.provider_current) or not self.provider_current():
            self.invalidate_host()
            raise OwnerFailure("unavailable", "provider_unavailable")
        if self._host is None or self.gateway.v2_host_api() is not self._host:
            self.invalidate_host()
            raise OwnerFailure("rejected", "access_denied")
        try:
            claims = self._access.resolve(invocation)
        except Exception:
            raise OwnerFailure("rejected", "access_denied") from None
        user_id = claims["user_id"]
        if user_id != self.gateway._mobile_allowed_user_id():
            self.invalidate(user_id)
            raise OwnerFailure("rejected", "access_denied")
        return claims

    def _sync(self, claims: dict) -> _User:
        user_id = claims["user_id"]
        state = self._user(user_id)
        authority_state = _encode([claims["subject_ref"], claims["authorization_ref"], claims["authorization_revision"]])
        if state.authority_state != authority_state:
            if state.authority_state is not None:
                state.authorization_revision += 1
                self.invalidate(user_id)
            if state.subject_ref and state.subject_ref != claims["subject_ref"]:
                self.session_changed(user_id)
                for key, entry in list(self.gateway._mobile_sessions.items()):
                    if entry.get("user_id") == user_id:
                        del self.gateway._mobile_sessions[key]
            state.subject_ref = claims["subject_ref"]
            state.authority_state = authority_state
        policy = self._policy()
        if state.policy is not None and state.policy != policy:
            self.invalidate(user_id)
        state.policy = policy
        return state

    def _policy(self):
        return (self.gateway._mobile_enabled() and not self.gateway._mobile_runtime_stopped,
                self.gateway._mobile_activity_enabled(), self.gateway._mobile_telemetry_enabled(),
                self.gateway._mobile_location_ttl(), self.gateway._mobile_activity_ttl(), self.gateway._mobile_telemetry_ttl())

    def envelope(self, invocation: object) -> dict:
        with self.gateway._mobile_state_lock:
            self.gateway._mobile_cleanup_sessions()
            claims = self._claims(invocation)
            state = self._sync(claims)
            return {
                "schema_version": "companion.reality.view.request.v1", "capability_id": "reality.view.query",
                "capability_version": "1.0", "provider_id": "astrbot_plugin_private_companion.reality",
                "provider_generation": self.generation, "scope": claims["scope"],
                "binding_ref": "binding:mobile:" + hashlib.sha256(claims["user_id"].encode("utf-8")).hexdigest()[:24],
                "context": {"caller_ref": claims["caller_ref"], "subject": {"kind": "user", "ref": claims["subject_ref"]},
                            "audience_ref": claims["audience_ref"], "authorization_ref": claims["authorization_ref"],
                            "authorization_revision": state.authorization_revision, "binding_revision": state.binding_revision,
                            "purpose": claims["purpose"], "sharing": claims["sharing"]},
                "execution": claims["execution"], "budget": claims["budget"], "trace_id": claims["trace_id"],
                "deadline_at": claims["deadline_at"], "required_features": ["companion.reality-view@1", "companion.sharing@1"],
            }

    def _sources(self, user_id: str, state: _User) -> list[dict]:
        sources = []
        for kind in KINDS:
            enabled = state.policy[0] and (kind != "activity" or state.policy[1]) and (kind != "telemetry" or state.policy[2])
            raw = getattr(self.gateway, STORES[kind]).get(user_id, {})
            stamp = raw.get("_rv1") if isinstance(raw, dict) else None
            supported = bool(isinstance(stamp, dict) and stamp["subject_ref"] == state.subject_ref
                             and stamp["binding_revision"] == state.binding_revision
                             and stamp["generation"] == self.generation and stamp["epoch"] == state.epochs[kind])
            sources.append({"source_ref": self.source_ref(user_id, kind), "kind": kind,
                            "owner_ref": "owner:reality-mobile", "descriptor_revision": state.epochs[kind] + 1,
                            "availability": "readable" if enabled else "disabled", "policy_ref": "policy:mobile-ingest-retention:v1",
                            "max_reference_age_seconds": REFERENCE_SECONDS if supported else 0,
                            "governance_epoch": str(state.epochs[kind]) if supported else None})
        return sources

    def authorize(self, invocation: object, request: dict) -> dict:
        with self.gateway._mobile_state_lock:
            expected = self.envelope(invocation)
            if _disclosure(request) != _disclosure(expected):
                raise OwnerFailure("rejected", "access_denied")
            if request["context"]["authorization_revision"] != expected["context"]["authorization_revision"]:
                raise OwnerFailure("rejected", "access_denied")
            if _authority(request) != _authority(expected):
                raise OwnerFailure("rejected", "binding_invalid")
            if any(request[key] != expected[key] for key in ("execution", "trace_id", "deadline_at")):
                raise OwnerFailure("rejected", "access_denied")
            if any(value > expected["budget"].get(key, 0) for key, value in request["budget"].items()):
                raise OwnerFailure("unavailable", "resource_limit")
            # Omitting a restrictive parent field must not restore a default.
            if set(request["budget"]) != set(expected["budget"]):
                raise OwnerFailure("unavailable", "resource_limit")
            disclosed = None
            self._purge_notices()
            if request["payload"]["method"] == "read":
                disclosed = request["payload"]["snapshot_ref"]
                notice = self._notices.get(disclosed)
                if notice is None or notice[0]["disclosure"] != _disclosure(request):
                    raise OwnerFailure("rejected", "access_denied")
            claims = self._claims(invocation)
            state = self._sync(claims)
            user_id = claims["user_id"]
            sources = self._sources(user_id, state)
            until = datetime.fromisoformat(claims["deadline_at"])
            ticket = _Ticket(self._seal, invocation, user_id, _authority(request), _disclosure(request),
                             tuple(state.epochs.items()), _encode(sources), until)
            return {"user_id": user_id, "authorized_until": until,
                    "catalog_revision": "catalog:" + hashlib.sha256(_encode(sources)).hexdigest()[:24],
                    "sources": sources, "disclosed_reference": disclosed, "ticket": ticket}

    def check_current(self, ticket: object) -> None:
        with self.gateway._mobile_state_lock:
            if type(ticket) is not _Ticket or ticket.seal is not self._seal:
                raise OwnerFailure("rejected", "access_denied")
            expected = self.envelope(ticket.invocation)
            if ticket.authority != _authority(expected):
                raise OwnerFailure("rejected", "binding_invalid")
            if ticket.epochs != tuple(self._users[ticket.user_id].epochs.items()):
                raise OwnerFailure("invalidated", "snapshot_invalidated")
            if datetime.now(timezone.utc) >= ticket.authorized_until:
                raise OwnerFailure("rejected", "access_denied")

    def read_source(self, ticket: object, source_ref: str, max_bytes: int) -> dict:
        with self.gateway._mobile_state_lock:
            self.check_current(ticket)
            source = next((source for source in json.loads(ticket.sources) if source["source_ref"] == source_ref), None)
            if source is None:
                raise OwnerFailure("rejected", "access_denied")
            kind = source["kind"]
            reader = getattr(self.gateway, "_mobile_" + kind + "_snapshot")
            projection = reader(ticket.user_id) if kind == "device" else reader(ticket.user_id, prompt_safe=True)
            # Incremental encoding enforces the byte cap before building a full
            # encoded copy; gateway projections are already structurally bounded.
            chunks, used = [], 0
            encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            for part in encoder.iterencode(projection):
                encoded = part.encode("utf-8")
                used += len(encoded)
                if used > max_bytes:
                    raise OwnerFailure("unavailable", "resource_limit")
                chunks.append(encoded)
            self.check_current(ticket)
            raw = getattr(self.gateway, STORES[kind]).get(ticket.user_id, {})
            stamp = raw.get("_rv1")
            until = datetime.fromtimestamp(stamp["retain_until"], timezone.utc) if stamp else None
            return {"body": b"".join(chunks), "user_id": ticket.user_id,
                    "governance_epoch": source["governance_epoch"], "retain_until": until}

    def _purge_notices(self) -> None:
        for ref, (_, expiry) in list(self._notices.items()):
            if time.monotonic() >= expiry:
                del self._notices[ref]

    def record_references(self, ticket: object, notices: tuple[dict, ...]) -> None:
        with self.gateway._mobile_state_lock:
            self.check_current(ticket)
            sources = {source["source_ref"] for source in json.loads(ticket.sources)}
            if any(notice["authority"] != ticket.authority or notice["disclosure"] != ticket.disclosure
                   or notice["source_ref"] not in sources or notice["expires_at"] > ticket.authorized_until
                   for notice in notices):
                raise OwnerFailure("rejected", "access_denied")
            self._purge_notices()
            for notice in notices:
                descriptor = next(source for source in json.loads(ticket.sources)
                                  if source["source_ref"] == notice["source_ref"])
                owned = dict(notice, role_epoch=descriptor["governance_epoch"], role_generation=self.generation)
                self._notices[notice["snapshot_ref"]] = (owned, time.monotonic() + 600)
            while len(self._notices) > 256:
                self._notices.popitem(last=False)

    def bind_dependencies(self, invocation: object, references: list[dict], binding: str) -> list[dict]:
        """A host-only provenance port. A reference string alone grants nothing."""
        with self.gateway._mobile_state_lock:
            if self.dependencies is None:
                raise OwnerFailure("unavailable", "provider_unavailable")
            expected = self.envelope(invocation)
            claims = self._claims(invocation)
            # The host derives this anchor; model input never supplies it.
            original_binding, pin = self._access.dependency_identity(invocation)
            if binding != original_binding:
                raise OwnerFailure("rejected", "access_denied")
            result = []
            for ref in references:
                saved = self._notices.get(ref["snapshot_ref"])
                notice = saved[0] if saved else None
                if (not notice or notice["source_ref"] != ref["source_ref"]
                        or notice["authority"] != _authority(expected)
                        or notice["disclosure"] != _disclosure(expected)
                        or not notice["reference_supported"] or notice["expires_at"] <= datetime.now(timezone.utc)):
                    raise OwnerFailure("rejected", "access_denied")
                ticket = self.dependencies.bind(binding, notice["source_ref"], self.generation,
                                                notice["expires_at"].timestamp(), pin)
                result.append({"kind": "reality", "id": ref["snapshot_ref"], "ticket": ticket,
                               "binding": binding, "source_ref": notice["source_ref"],
                               "expires_at": notice["expires_at"].isoformat()})
            return result

    def check_dependencies(self, host_api: Any, *, binding: str, tickets: list[str]) -> dict:
        with self.gateway._mobile_state_lock:
            if (host_api is not self._host or self.gateway.v2_host_api() is not host_api
                    or not callable(self.provider_current) or not self.provider_current()
                    or self.dependencies is None or len(tickets) > 128):
                return {"items": []}
            self.gateway._mobile_cleanup_sessions()
            self._access.refresh()
            policy = self._policy()
            for user_id, state in self._users.items():
                if user_id != self.gateway._mobile_allowed_user_id() or (state.policy is not None and state.policy != policy):
                    self.invalidate(user_id)
                state.policy = policy
            return self.dependencies.check(binding, tickets, self._access.binding_state)

    def acknowledge_dependencies(self, host_api: Any, *, binding: str, tickets: list[str]) -> None:
        with self.gateway._mobile_state_lock:
            if (host_api is not self._host or self.gateway.v2_host_api() is not host_api
                    or self.dependencies is None or len(tickets) > 128):
                raise OwnerFailure('rejected', 'access_denied')
            self.dependencies.acknowledge(binding, tickets)

    def role_source_port(self, host_api: Any):
        """Explicit optional assembly; never enabled by ordinary snapshot reads."""
        from .role_snapshot_sources import RoleSnapshotSources
        with self.gateway._mobile_state_lock:
            if host_api is not self._host or self.gateway.v2_host_api() is not host_api:
                raise OwnerFailure("rejected", "access_denied")
            port = getattr(self, "_role_source_port", None)
            if port is None or port.host_api is not host_api:
                port = self._role_source_port = RoleSnapshotSources(self, host_api)
            return port
