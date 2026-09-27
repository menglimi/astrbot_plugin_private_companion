# -*- coding: utf-8 -*-
"""PrivateCompanionPluginReq041Part03Mixin。

由 tools/split_mixin_domain.py 从 main_req041.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 315 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginReq041Mixin）。
"""
from __future__ import annotations
from .main_req041_shared import Any
from .main_req041_shared import AstrMessageEvent
from .main_req041_shared import NamespaceContext
from .main_req041_shared import RelationshipAccountStore
from .main_req041_shared import _now_ts
from .main_req041_shared import _single_line
from .main_req041_shared import admit_confirmed_group_affinity
from .main_req041_shared import asyncio
from .main_req041_shared import deepcopy
from .main_req041_shared import normalize_group_allowlist
from .main_req041_shared import overlay_group_runtime_view
from .main_req041_shared import prepare_group_affinity_candidate
from .main_req041_shared import runtime_persona_setting
from .main_req041_shared import scoped_group_ref
from .main_req041_shared import scoped_persona_ref
from .main_req041_shared import uuid



class PrivateCompanionPluginReq041Part03Mixin:
    """PrivateCompanionPluginReq041Part03Mixin（从 PrivateCompanionPluginReq041Mixin 拆出）。"""


    def _req041_scoped_group_read_view(
        self,
        event: Any,
        *,
        group_id: str,
        group: dict[str, Any],
        sender_id: str,
        relationship_user: dict[str, Any] | None,
    ) -> dict[str, Any]:
        existing = getattr(event, "req041_scoped_group_read_view", None)
        if isinstance(existing, dict):
            return existing
        view = deepcopy(group) if isinstance(group, dict) else group
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None or not isinstance(view, dict):
            return view
        persona_id = scoped_persona_ref(self._active_persona_scope())
        safe_group = scoped_group_ref(persona_id, group_id)
        shared = NamespaceContext(
            kind="group_shared", persona_id=persona_id, identity_id="", group_id=safe_group,
            assurance="verified", profile_status="active", policy_version=synchronizer.policy_version,
            migration_epoch=synchronizer.migration_epoch,
        )
        shared_projection = synchronizer.read_projection(shared)
        persona_context = self._req041_persona_global_context(purpose="rule_read")
        persona_projection = (
            synchronizer.read_projection(persona_context) if persona_context is not None else None
        )
        member_projection = None
        member_context = self._req041_scoped_context_for_user(
            relationship_user or {}, kind="group_member", group_id=group_id, purpose="profile_read"
        )
        if member_context is not None:
            member_projection = synchronizer.read_projection(member_context)
        if not isinstance(shared_projection, dict) or shared_projection.get("ok") is not True:
            view["req041_scoped_read_generation"] = "new_unavailable"
            try:
                setattr(event, "req041_scoped_group_read_view", view)
            except Exception:
                pass
            return view
        view = overlay_group_runtime_view(
            view, shared_projection, sender_id=sender_id, member_projection=member_projection,
            persona_projection=persona_projection,
        )
        if not isinstance(view, dict) or view.get("req041_scoped_read_generation") != "new":
            return view
        view["req041_scoped_read_generation"] = "new"
        try:
            setattr(event, "req041_scoped_group_read_view", view)
        except Exception:
            pass
        return view

    def _req041_replay_finished(self, _task: Any) -> None:
        self._req041_replay_task = None
        if bool(getattr(self, "_req041_replay_requested", False)):
            try:
                self._req041_schedule_replay()
            except RuntimeError:
                status = getattr(self, "req041_migration_status", None)
                if isinstance(status, dict):
                    status.update({"state": "paused", "code": "migration_replay_loop_unavailable"})

    def _req041_relationship_read_view(
        self,
        event: Any,
        user: dict[str, Any],
        *,
        kind: str = "private",
        group_id: str = "",
    ) -> dict[str, Any]:
        existing = getattr(event, "req041_relationship_read_view", None)
        if isinstance(existing, dict):
            return existing
        router = getattr(self, "req041_relationship_read_router", None)
        if not isinstance(user, dict):
            return user
        result: dict[str, Any] = {}
        view = user
        if router is not None:
            event_ref = self._event_message_id(event)
            if not event_ref:
                event_ref = f"{getattr(event, 'unified_msg_origin', '')}:{uuid.uuid4().hex}"
            result = router.begin(user, event_ref=event_ref, kind=kind, group_id=group_id)
            view = result.get("user") if isinstance(result.get("user"), dict) else user
        production_view = view
        view = self._lab_fixture_relationship_view(event, view)
        if router is None and view is production_view:
            return user
        try:
            setattr(event, "req041_relationship_read_view", view)
            if router is not None:
                setattr(event, "req041_read_chain_id", str(result.get("chain_id") or ""))
                setattr(event, "req041_read_generation", str(result.get("generation") or "legacy"))
                setattr(event, "req041_read_identity_id", str(result.get("identity_id") or ""))
        except Exception:
            pass
        return view

    def _req041_relationship_snapshot_view(
        self,
        user: dict[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        """Take one short-lived private relationship view for background decisions."""
        if not isinstance(user, dict):
            return user
        if user.get("_req041_relationship_snapshot_resolved") is True:
            return user
        relationship_view = user
        router = getattr(self, "req041_relationship_read_router", None)
        if router is not None and user.get("req041_read_generation") != "new":
            result = router.begin(
                user,
                event_ref=f"snapshot:{_single_line(source, 60) or 'relationship'}:{uuid.uuid4().hex}",
                kind="private",
            )
            chain_id = str(result.get("chain_id") or "")
            try:
                relationship_view = (
                    result.get("user") if isinstance(result.get("user"), dict) else user
                )
            finally:
                if chain_id:
                    try:
                        router.finish(chain_id)
                    except Exception:
                        pass
        scoped_getter = getattr(self, "_req041_scoped_private_read_view", None)
        return (
            scoped_getter(None, relationship_view)
            if callable(scoped_getter) else relationship_view
        )

    def _req041_group_sender_is_human(self, event: AstrMessageEvent) -> bool:
        if not self._event_is_inbound_chat_message(event):
            return False
        sender_id = self._event_sender_id(event)
        self_id = self._event_self_id(event)
        if not sender_id or (self_id and sender_id == self_id):
            return False
        raw = self._event_raw_payload(event)
        sender = raw.get("sender") if isinstance(raw.get("sender"), dict) else {}
        message_obj = getattr(event, "message_obj", None)
        message_sender = getattr(message_obj, "sender", None) if message_obj is not None else None

        def field(owner: Any, name: str) -> Any:
            if isinstance(owner, dict):
                return owner.get(name)
            try:
                return getattr(owner, name, None)
            except Exception:
                return None

        for owner in (raw, sender, message_sender):
            if owner is None:
                continue
            for key in ("is_bot", "bot", "is_system", "system"):
                value = field(owner, key)
                if value is True or str(value or "").strip().lower() in {"1", "true", "yes", "bot", "system"}:
                    return False
            role = str(field(owner, "role") or field(owner, "sender_type") or "").strip().lower()
            if role in {"assistant", "bot", "system", "service"}:
                return False
        return True

    def _req041_prepare_group_affinity_candidate(
        self,
        event: AstrMessageEvent,
        *,
        group_id: str,
        relationship_user: dict[str, Any] | None,
        scene_trigger: str,
        forwarded: bool,
    ) -> dict[str, Any] | None:
        if (
            not isinstance(relationship_user, dict)
            or str(getattr(event, "req041_read_generation", "") or "") != "new"
            or getattr(self, "req041_dual_write_producer", None) is None
            or getattr(self, "req041_migration_replay", None) is None
            or getattr(self, "req041_relationship_store", None) is None
            or not bool(runtime_persona_setting(self, 'enable_custom_relationship_stage_policy', False))
        ):
            return None
        direction = "at_bot" if scene_trigger == "at_bot" else "reply_bot" if scene_trigger == "reply_bot" else ""
        context = self._req041_scoped_context_for_user(
            relationship_user,
            kind="group_member",
            group_id=group_id,
            purpose="relationship_write",
        )
        if context is None:
            return None
        candidate = prepare_group_affinity_candidate(
            context,
            raw_group_id=group_id,
            allowlist=getattr(self, "group_relationship_affinity_allowlist", ()),
            enabled=bool(getattr(self, "enable_group_relationship_affinity", False)),
            inbound_event_id=self._event_message_id(event),
            directed_by=direction,
            legacy_user_key=str(relationship_user.get("user_id") or ""),
            inbound=self._event_is_inbound_chat_message(event),
            human_sender=self._req041_group_sender_is_human(event),
            forwarded=bool(forwarded),
            echo=False,
            historical=False,
        )
        if isinstance(candidate, dict):
            setattr(event, "req041_group_affinity_candidate", candidate)
        return candidate

    async def _req041_settle_confirmed_group_affinity(self, event: AstrMessageEvent) -> None:
        candidate = getattr(event, "req041_group_affinity_candidate", None)
        if not isinstance(candidate, dict) or bool(candidate.get("settled")):
            return
        if not self._reaction_expression_primary_reply_confirmed(
            event, require_segmented_complete=True,
        ):
            return
        live_allowlist = normalize_group_allowlist(
            getattr(self, "group_relationship_affinity_allowlist", ())
        )
        if (
            not bool(runtime_persona_setting(self, 'enable_custom_relationship_stage_policy', False))
            or not bool(getattr(self, "enable_group_relationship_affinity", False))
            or str(candidate.get("raw_group_id") or "") not in live_allowlist
        ):
            candidate["settled"] = True
            candidate["result_code"] = "group_affinity_config_revoked"
            return
        store = getattr(self, "req041_relationship_store", None)
        if not isinstance(store, RelationshipAccountStore):
            return
        admission = await asyncio.to_thread(
            admit_confirmed_group_affinity,
            candidate,
            store,
            reply_succeeded=True,
            requested_delta=4,
            group_daily_net_cap=int(getattr(self, "group_relationship_daily_net_cap", 2)),
            group_window_seconds=int(getattr(self, "group_relationship_window_minutes", 30)) * 60,
            group_window_absolute_cap=int(getattr(self, "group_relationship_window_absolute_cap", 1)),
            group_person_daily_absolute_cap=int(
                getattr(self, "group_relationship_person_daily_absolute_cap", 4)
            ),
            group_scope_daily_absolute_cap=int(
                getattr(self, "group_relationship_scope_daily_absolute_cap", 20)
            ),
            group_event_cap=4,
        )
        if admission is None:
            return
        candidate["settled"] = True
        candidate["result_code"] = admission.code
        if admission.admitted_delta == 0:
            return
        context = NamespaceContext(**candidate.get("context", {}))
        user_key = str(candidate.get("legacy_user_key") or "")
        async with self._data_lock:
            users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
            user = users.get(user_key) if isinstance(users, dict) else None
            if (
                not isinstance(user, dict)
                or str(user.get("unified_person_id") or "") != context.identity_id
            ):
                candidate["settled"] = False
                raise RuntimeError("group_affinity_legacy_subject_mismatch")
            result = self._apply_relationship_event(
                user,
                admission.admitted_delta,
                reason_code="direct_group_interaction",
                event_id=admission.event_id,
                now=_now_ts(),
                req041_group_admission_event_id=admission.event_id,
            )
            candidate["legacy_result_code"] = str(result.get("code") or "")
            if result.get("changed"):
                self._schedule_data_save(sections={"users"})

    async def _req041_run_replay_batch(self) -> None:
        worker = getattr(self, "req041_migration_replay", None)
        if worker is None:
            return
        while bool(getattr(self, "_req041_replay_requested", False)):
            self._req041_replay_requested = False
            result = await asyncio.to_thread(worker.run_batch)
            if result.get("status") == "paused":
                status = getattr(self, "req041_migration_status", None)
                if isinstance(status, dict):
                    status.update({
                        "state": "paused",
                        "code": str(result.get("error_code") or "migration_replay_failed")[:120],
                        "s5": result,
                    })
                return
            runtime = getattr(self, "req041_migration_status", None)
            coordinator = getattr(self, "req041_migration_coordinator", None)
            outbox = getattr(self, "req041_migration_outbox", None)
            if isinstance(runtime, dict) and coordinator is not None and outbox is not None:
                try:
                    stability_fn = advance_migration_stability
                except NameError:
                    from migration_stability import advance_migration_stability as stability_fn
                control = coordinator.status()
                scoped = runtime.get("scoped") if isinstance(runtime.get("scoped"), dict) else {}
                stability = await asyncio.to_thread(
                    stability_fn,
                    coordinator=coordinator, outbox=outbox,
                    migration_epoch=str(control.get("migration_epoch") or ""),
                    replay_ok=True, scoped_ok=bool(scoped.get("ok")),
                    memory_bound=bool(runtime.get("memory_bound")),
                    observability=self.req041_observability,
                    boot_ref=str(getattr(self, "_req041_runtime_boot_ref", f"boot-{id(self)}")),
                )
                control = coordinator.status()
                runtime.update({"phase": control.get("phase", runtime.get("phase")),
                                "checkpoint": control.get("checkpoint", runtime.get("checkpoint")),
                                "stability": stability})
            if int(result.get("count") or 0) > 0 or int(result.get("recovered") or 0) > 0:
                self._req041_replay_requested = True
