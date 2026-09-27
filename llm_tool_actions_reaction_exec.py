# -*- coding: utf-8 -*-
"""表情表达执行域。

由 tools/split_mixin_domain.py 从 llm_tool_actions.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 918 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsMixin）。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from .helpers import _now_ts, _path_text, _safe_float, _safe_int, _single_line
from .llm_tool_actions_shared import PHOTO_TOOL_SILENT_SENTINEL, logger
from .persona_config import runtime_persona_setting
from .reaction_expression import (
    append_reaction_expression_outcome,
    ensure_reaction_expression_state,
    evaluate_reaction_expression_gate,
    normalize_reaction_expression_intent,
    reaction_expression_image_key,
    reaction_expression_image_keys,
    reaction_expression_reservation_owned,
    reaction_expression_scope_state,
    reaction_expression_selection_preferences,
    record_reaction_expression_sent,
    release_reaction_expression_image,
    release_reaction_expression_reservation,
    reserve_reaction_expression_image,
    reserve_reaction_expression_intent,
)
from astrbot.api.event import AstrMessageEvent
from typing import Any



class LlmToolActionsReactionExecMixin:
    """表情表达执行域（从 LlmToolActionsMixin 拆出）。"""


    async def _pc_reaction_expression_impl(
        self,
        event: AstrMessageEvent,
        *,
        query: str = "",
        context: str = "",
        meme_only: bool = True,
        send: bool = True,
        caption: str = "",
        purpose: str = "",
        emotion: str = "",
        intensity: int = 0,
        candidate_queries: Any = "",
        attach_only: bool = False,
    ) -> str:
        self._note_reaction_expression_runtime(attempts=1)
        if not bool(runtime_persona_setting(self, 'enable_reaction_expression_experiment', False)):
            return json.dumps(
                self._reaction_expression_skip_result(
                    "experiment_disabled",
                    event=event,
                ),
                ensure_ascii=False,
            )
        if not attach_only and not self._reaction_expression_bool_arg(send, True):
            return json.dumps(
                self._reaction_expression_skip_result(
                    "send_disabled",
                    event=event,
                ),
                ensure_ascii=False,
            )

        scope = self._reaction_expression_scope(event)
        scope_enabled = (
            bool(runtime_persona_setting(self, 'reaction_expression_private_enabled', True))
            if scope == "private"
            else bool(runtime_persona_setting(self, 'reaction_expression_group_enabled', False))
            if scope == "group"
            else False
        )
        if not scope_enabled:
            return json.dumps(
                self._reaction_expression_skip_result(
                    f"{scope}_disabled",
                    event=event,
                    scope=scope,
                ),
                ensure_ascii=False,
            )

        try:
            user_id = _single_line(event.get_sender_id(), 160)
        except Exception:
            user_id = ""
        if scope == "private" and user_id:
            user_id = self._reaction_expression_event_storage_id(event, user_id)
        if not user_id:
            return json.dumps(
                self._reaction_expression_skip_result(
                    "missing_user",
                    event=event,
                    scope=scope,
                ),
                ensure_ascii=False,
            )
        scope_key = self._reaction_expression_scope_key(event, user_id)
        authorization = self._reaction_expression_authorization(event)
        profile_snapshot = (
            dict(authorization.get("profile_snapshot"))
            if isinstance(authorization.get("profile_snapshot"), dict)
            else None
        )
        authorized, authorization_reason = self._consume_reaction_expression_authorization(
            event,
            user_id=user_id,
            scope_key=scope_key,
        )
        if not authorized:
            return json.dumps(
                self._reaction_expression_skip_result(
                    authorization_reason,
                    event=event,
                    scope=scope,
                ),
                ensure_ascii=False,
            )

        candidate_limit = _safe_int(
            runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6), 6, 1, 16
        )
        intent = normalize_reaction_expression_intent(
            query=query,
            context=context,
            purpose=purpose,
            emotion=emotion,
            intensity=intensity,
            candidate_queries=candidate_queries,
            candidate_limit=candidate_limit,
        )
        signature = _single_line(intent.get("signature"), 40)
        reservation_token = uuid.uuid4().hex
        now = _now_ts()
        async with self._data_lock:
            user = self._reaction_expression_state_owner(event, user_id)
            if not isinstance(user, dict):
                return json.dumps(
                    self._reaction_expression_skip_result(
                        "state_unavailable",
                        event=event,
                        scope=scope,
                    ),
                    ensure_ascii=False,
                )
            state = ensure_reaction_expression_state(user)
            scoped_state = reaction_expression_scope_state(state, scope_key)
            gate = evaluate_reaction_expression_gate(
                scoped_state,
                intent,
                now=now,
                probability=1.0,
                cooldown_seconds=_safe_float(
                    runtime_persona_setting(self, 'reaction_expression_cooldown_seconds', 180),
                    180.0,
                    0.0,
                    86400.0,
                ),
                random_value=0.0,
            )
            if not gate.get("allowed"):
                reason = _single_line(gate.get("reason"), 80) or "gate"
                append_reaction_expression_outcome(
                    state,
                    status="skipped",
                    reason=reason,
                    intent_signature=signature,
                    now=now,
                    candidate_limit=candidate_limit,
                )
                self._persist_reaction_expression_state(
                    sections={"reaction_expression_group_states"}
                    if scope == "group"
                    else {"users"}
                )
                return json.dumps(
                    self._reaction_expression_skip_result(
                        reason,
                        event=event,
                        scope=scope,
                        intent=intent,
                    ),
                    ensure_ascii=False,
                )
            selection_preferences = reaction_expression_selection_preferences(
                state,
                intent_signature=signature,
            )
            reserve_reaction_expression_intent(
                scoped_state,
                intent,
                now=now,
                reservation_token=reservation_token,
            )
            lookup_context = self._reaction_expression_lookup_context(
                user,
                intent,
                profile_snapshot=profile_snapshot,
            )

        # Keep alternate model-provided search phrases explicit in the local
        # lookup context. The owned catalog can score these phrases directly
        # without another provider/model request.
        candidate_queries = intent.get("candidate_queries")
        if isinstance(candidate_queries, list) and candidate_queries:
            candidate_text = "；".join(
                _single_line(item, 100) for item in candidate_queries if _single_line(item, 100)
            )
            if candidate_text and "候选检索表达：" not in lookup_context:
                lookup_context = _single_line(
                    "；".join(part for part in (lookup_context, f"候选检索表达：{candidate_text}") if part),
                    1000,
                )

        low_latency = bool(runtime_persona_setting(self, 'reaction_expression_low_latency_mode', True))
        raw_lookup = await self._pc_find_reaction_image_impl(
            event,
            query=_single_line(intent.get("provider_query"), 500),
            search_context=lookup_context,
            meme_only=meme_only,
            send=False,
            caption="",
            low_latency=low_latency,
            internal_attachment=True,
            selection_preferences=selection_preferences,
            selection_signature=signature,
        )
        try:
            lookup = json.loads(raw_lookup)
        except (TypeError, ValueError, json.JSONDecodeError):
            lookup = {}
        lookup_cache_hit = bool(lookup.get("cache_hit")) if isinstance(lookup, dict) else False
        lookup_latency_ms = (
            _safe_float(lookup.get("lookup_latency_ms"), 0.0, 0.0, 3_600_000.0)
            if isinstance(lookup, dict)
            else 0.0
        )
        if not isinstance(lookup, dict) or not lookup.get("success") or not lookup.get("found"):
            reason = _single_line(lookup.get("status") if isinstance(lookup, dict) else "", 80) or "not_found"
            async with self._data_lock:
                state_owner = self._reaction_expression_state_owner(event, user_id)
                if not isinstance(state_owner, dict):
                    return json.dumps(
                        self._reaction_expression_skip_result(
                            "state_unavailable",
                            event=event,
                            scope=scope,
                            intent=intent,
                        ),
                        ensure_ascii=False,
                    )
                state = ensure_reaction_expression_state(state_owner)
                scoped_state = reaction_expression_scope_state(state, scope_key)
                release_reaction_expression_reservation(
                    scoped_state,
                    intent_signature=signature,
                    reservation_token=reservation_token,
                )
                append_reaction_expression_outcome(
                    state,
                    status="skipped",
                    reason=reason,
                    intent_signature=signature,
                    now=_now_ts(),
                    candidate_limit=candidate_limit,
                    cache_hit=lookup_cache_hit,
                    latency_ms=lookup_latency_ms,
                )
                self._persist_reaction_expression_state(
                    sections={"reaction_expression_group_states"}
                    if scope == "group"
                    else {"users"}
                )
            return json.dumps(
                self._reaction_expression_skip_result(
                    reason,
                    event=event,
                    scope=scope,
                    intent=intent,
                    provider_status=reason,
                    cache_hit=lookup_cache_hit,
                    lookup_latency_ms=lookup_latency_ms,
                ),
                ensure_ascii=False,
            )

        image_path = _path_text(lookup.get("path"), 1000)
        image_id = _single_line(lookup.get("image_id"), 160)
        image_key = reaction_expression_image_key(image_id, image_path)
        image_keys = reaction_expression_image_keys(image_id, image_path)
        duplicate_window = max(
            600.0,
            _safe_float(
                runtime_persona_setting(self, 'reaction_expression_cooldown_seconds', 180),
                180.0,
                0.0,
                86400.0,
            )
            * 3,
        )
        async with self._data_lock:
            state_owner = self._reaction_expression_state_owner(event, user_id)
            if not isinstance(state_owner, dict):
                return json.dumps(
                    self._reaction_expression_skip_result(
                        "state_unavailable",
                        event=event,
                        scope=scope,
                        intent=intent,
                    ),
                    ensure_ascii=False,
                )
            state = ensure_reaction_expression_state(state_owner)
            scoped_state = reaction_expression_scope_state(state, scope_key)
            final_reason = ""
            if not reaction_expression_reservation_owned(
                scoped_state,
                reservation_token,
            ):
                final_reason = "reservation_lost"
            else:
                last_sent_at = _safe_float(scoped_state.get("last_sent_at"), 0.0)
                cooldown_seconds = _safe_float(
                    runtime_persona_setting(self, 'reaction_expression_cooldown_seconds', 180),
                    180.0,
                    0.0,
                    86400.0,
                )
                current_time = _now_ts()
                if (
                    last_sent_at > 0
                    and cooldown_seconds > 0
                    and current_time - last_sent_at < cooldown_seconds
                ):
                    final_reason = "cooldown"
                else:
                    scoped_state["reservation"]["at"] = current_time
            if final_reason:
                release_reaction_expression_reservation(
                    scoped_state,
                    intent_signature=signature,
                    reservation_token=reservation_token,
                )
                append_reaction_expression_outcome(
                    state,
                    status="skipped",
                    reason=final_reason,
                    intent_signature=signature,
                    now=_now_ts(),
                    candidate_limit=candidate_limit,
                    image_key=image_key,
                    cache_hit=lookup_cache_hit,
                    latency_ms=lookup_latency_ms,
                )
                self._persist_reaction_expression_state(
                    sections={"reaction_expression_group_states"}
                    if scope == "group"
                    else {"users"}
                )
                return json.dumps(
                    self._reaction_expression_skip_result(
                        final_reason,
                        event=event,
                        scope=scope,
                        intent=intent,
                        found=True,
                        image_id=image_id,
                        cache_hit=lookup_cache_hit,
                        lookup_latency_ms=lookup_latency_ms,
                    ),
                    ensure_ascii=False,
                )
            image_reserved = reserve_reaction_expression_image(
                state,
                image_key=image_key,
                image_keys=image_keys,
                now=_now_ts(),
                duplicate_window_seconds=duplicate_window,
                reservation_token=reservation_token,
            )
            if not image_reserved:
                release_reaction_expression_reservation(
                    scoped_state,
                    intent_signature=signature,
                    reservation_token=reservation_token,
                )
                append_reaction_expression_outcome(
                    state,
                    status="skipped",
                    reason="duplicate_image",
                    intent_signature=signature,
                    now=_now_ts(),
                    candidate_limit=candidate_limit,
                    image_key=image_key,
                    cache_hit=lookup_cache_hit,
                    latency_ms=lookup_latency_ms,
                )
                self._persist_reaction_expression_state(
                    sections={"reaction_expression_group_states"}
                    if scope == "group"
                    else {"users"}
                )
                return json.dumps(
                    self._reaction_expression_skip_result(
                        "duplicate_image",
                        event=event,
                        scope=scope,
                        intent=intent,
                        found=True,
                        image_id=image_id,
                        cache_hit=lookup_cache_hit,
                        lookup_latency_ms=lookup_latency_ms,
                    ),
                    ensure_ascii=False,
                )

        tags = [
            _single_line(item, 60)
            for item in lookup.get("tags", [])
            if _single_line(item, 60)
        ]
        need = _single_line(lookup.get("need"), 220) or _single_line(
            intent.get("provider_query"), 220
        )
        match_reason = _single_line(lookup.get("reason"), 220)
        snapshot_caption = "；".join(
            part
            for part in (
                f"图片画面：{_single_line(lookup.get('description') or lookup.get('image_description'), 200)}"
                if lookup.get("description") or lookup.get("image_description")
                else "",
                f"图库标签：{'、'.join(tags[:8])}" if tags else "",
                f"表达需求：{need}" if need else "",
                f"选图依据：{match_reason}" if match_reason else "",
            )
            if part
        )
        if attach_only:
            pending_attachment = {
                "trace_id": self._reaction_expression_trace_id(event),
                "user_id": user_id,
                "scope": scope,
                "scope_key": scope_key,
                "intent": intent,
                "intent_signature": signature,
                "reservation_token": reservation_token,
                "candidate_limit": candidate_limit,
                "duplicate_window_seconds": duplicate_window,
                "image_path": image_path,
                "image_id": image_id,
                "image_key": image_key,
                "image_keys": image_keys,
                "tags": tags,
                "need": need,
                "match_reason": match_reason,
                "match_basis": self._reaction_expression_match_basis(lookup),
                "snapshot_caption": snapshot_caption,
                "cache_hit": lookup_cache_hit,
                "lookup_latency_ms": lookup_latency_ms,
                "confidence": _safe_float(
                    lookup.get("confidence"), 0.0, 0.0, 1.0
                ),
                "attached": False,
                "settled": False,
            }
            try:
                setattr(
                    event,
                    "_private_companion_reaction_expression_pending_attachment",
                    pending_attachment,
                )
            except Exception:
                await self._settle_reaction_expression_attachment_data(
                    pending_attachment,
                    sent=False,
                    reason="attachment_state_failed",
                )
                return json.dumps(
                    {
                        "status": "skipped",
                        "success": True,
                        "found": True,
                        "sent": False,
                        "experimental": True,
                        "decision": "skip",
                        "skip_reason": "attachment_state_failed",
                        "intent": intent,
                        "image_id": image_id,
                        "must_not_claim_sent": True,
                    },
                    ensure_ascii=False,
                )
            self._log_reaction_expression_event(
                event,
                stage="attachment",
                decision="prepared",
                reason="awaiting_platform_send",
                scope=scope,
                status="prepared",
                found=True,
                sent=False,
                image_id=image_id,
                confidence=lookup.get("confidence"),
                cache_hit=lookup_cache_hit,
                latency_ms=lookup_latency_ms,
                match_basis=self._reaction_expression_match_basis(lookup),
            )
            return json.dumps(
                {
                    "status": "prepared",
                    "success": True,
                    "found": True,
                    "sent": False,
                    "experimental": True,
                    "decision": "attach",
                    "path": image_path,
                    "image_id": image_id,
                    "tags": tags,
                    "need": need,
                    "reason": match_reason,
                    "confidence": _safe_float(
                        lookup.get("confidence"), 0.0, 0.0, 1.0
                    ),
                    "cache_hit": lookup_cache_hit,
                    "lookup_latency_ms": lookup_latency_ms,
                    "intent": intent,
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        visible_caption = self._sanitize_photo_tool_caption(caption, limit=120)
        try:
            delivery = await self._deliver_generated_image_to_event(
                event,
                image_path=image_path,
                caption=visible_caption,
            )
        except Exception as exc:
            delivery = {
                "sent": False,
                "destination": "error",
                "message": f"图片发送失败：{_single_line(exc, 180) or '未知错误'}",
            }
        if not isinstance(delivery, dict):
            delivery = {"sent": False, "destination": "error", "message": "图片发送失败"}
        sent = bool(delivery.get("sent"))
        if not sent:
            delivery_reason = (
                "delivery_uncertain"
                if bool(delivery.get("uncertain"))
                else "delivery_failed"
            )
            async with self._data_lock:
                state_owner = self._reaction_expression_state_owner(event, user_id)
                if not isinstance(state_owner, dict):
                    return json.dumps(
                        self._reaction_expression_skip_result(
                            "state_unavailable",
                            event=event,
                            scope=scope,
                            intent=intent,
                        ),
                        ensure_ascii=False,
                    )
                state = ensure_reaction_expression_state(state_owner)
                scoped_state = reaction_expression_scope_state(state, scope_key)
                release_reaction_expression_image(
                    state,
                    image_key,
                    image_keys=image_keys,
                    reservation_token=reservation_token,
                )
                release_reaction_expression_reservation(
                    scoped_state,
                    intent_signature=signature,
                    reservation_token=reservation_token,
                )
                append_reaction_expression_outcome(
                    state,
                    status="skipped",
                    reason=delivery_reason,
                    intent_signature=signature,
                    now=_now_ts(),
                    candidate_limit=candidate_limit,
                    image_key=image_key,
                    cache_hit=lookup_cache_hit,
                    latency_ms=lookup_latency_ms,
                )
                self._persist_reaction_expression_state(
                    sections={"reaction_expression_group_states"}
                    if scope == "group"
                    else {"users"}
                )
            return json.dumps(
                self._reaction_expression_skip_result(
                    delivery_reason,
                    event=event,
                    stage="delivery",
                    scope=scope,
                    intent=intent,
                    found=True,
                    image_id=image_id,
                    cache_hit=lookup_cache_hit,
                    lookup_latency_ms=lookup_latency_ms,
                    delivery=_single_line(delivery.get("destination"), 40),
                ),
                ensure_ascii=False,
            )

        try:
            setattr(event, "_private_companion_photo_tool_sent", True)
            setattr(event, "_private_companion_photo_tool_sent_caption", visible_caption)
        except Exception:
            pass
        settled_at = _now_ts()
        async with self._data_lock:
            user = self._reaction_expression_state_owner(event, user_id)
            if not isinstance(user, dict):
                return json.dumps(
                    self._reaction_expression_skip_result(
                        "state_unavailable",
                        event=event,
                        scope=scope,
                        intent=intent,
                    ),
                    ensure_ascii=False,
                )
            state = ensure_reaction_expression_state(user)
            record_reaction_expression_sent(
                state,
                intent,
                image_id=image_id,
                image_path=image_path,
                image_key=image_key,
                image_keys=image_keys,
                now=settled_at,
                candidate_limit=candidate_limit,
                duplicate_window_seconds=duplicate_window,
                scope_key=scope_key,
                reservation_token=reservation_token,
                cache_hit=lookup_cache_hit,
                latency_ms=lookup_latency_ms,
            )
            if snapshot_caption:
                self._remember_recent_photo_share_snapshot(
                    user,
                    caption=snapshot_caption,
                    topic=need,
                    motive=match_reason,
                    reason="reaction_expression_experiment",
                    subject_owner="unknown",
                )
            self._persist_reaction_expression_state(
                sections={"reaction_expression_group_states"}
                if scope == "group"
                else {"users"}
            )

        self._note_reaction_expression_runtime(sent=1, last_reason="delivered")
        self._mark_reaction_asset_used(image_id, event=event)
        self._log_reaction_expression_event(
            event,
            stage="delivery",
            decision="sent",
            reason="delivered",
            scope=scope,
            status="success",
            found=True,
            sent=True,
            image_id=image_id,
            confidence=lookup.get("confidence"),
            cache_hit=lookup_cache_hit,
            latency_ms=lookup_latency_ms,
            delivery=delivery.get("destination"),
            match_basis=self._reaction_expression_match_basis(lookup),
        )

        return json.dumps(
            {
                "status": "success",
                "success": True,
                "found": True,
                "sent": True,
                "experimental": True,
                "decision": "send",
                "message": _single_line(delivery.get("message"), 220),
                "path": image_path,
                "image_id": image_id,
                "tags": tags,
                "need": need,
                "reason": match_reason,
                "confidence": _safe_float(lookup.get("confidence"), 0.0, 0.0, 1.0),
                "delivery": _single_line(delivery.get("destination"), 40),
                "cache_hit": lookup_cache_hit,
                "lookup_latency_ms": lookup_latency_ms,
                "intent": intent,
                "must_not_claim_sent": False,
                "final_response_instruction": (
                    "图片和文字 caption 已作为本轮组合回复发送。"
                    f"最终回复不要留空，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                ),
            },
            ensure_ascii=False,
        )

    async def _settle_reaction_expression_attachment_data(
        self,
        pending: dict[str, Any],
        *,
        sent: bool,
        reason: str,
    ) -> bool:
        """Commit an attached image only after the platform send phase."""
        if not isinstance(pending, dict) or pending.get("settled"):
            return False
        pending["settled"] = True
        pending["sent"] = bool(sent)
        pending["settled_reason"] = _single_line(reason, 80)

        user_id = _single_line(pending.get("user_id"), 160)
        trace_id = _single_line(pending.get("trace_id"), 12)
        scope = _single_line(pending.get("scope"), 16)
        scope_key = _single_line(pending.get("scope_key"), 240)
        intent = pending.get("intent") if isinstance(pending.get("intent"), dict) else {}
        signature = _single_line(pending.get("intent_signature"), 40)
        reservation_token = _single_line(pending.get("reservation_token"), 80)
        candidate_limit = _safe_int(pending.get("candidate_limit"), 6, 1, 16)
        image_path = _path_text(pending.get("image_path"), 1000)
        image_id = _single_line(pending.get("image_id"), 160)
        image_key = _single_line(pending.get("image_key"), 1000)
        image_keys = pending.get("image_keys")
        cache_hit = bool(pending.get("cache_hit"))
        latency_ms = _safe_float(
            pending.get("lookup_latency_ms"), 0.0, 0.0, 3_600_000.0
        )
        confidence = _safe_float(pending.get("confidence"), 0.0, 0.0, 1.0)
        settled_at = _now_ts()

        if not user_id:
            self._note_reaction_expression_runtime(
                skipped=1,
                last_reason=reason or "missing_user",
            )
            self._log_reaction_expression_event(
                None,
                trace_id=trace_id,
                stage="delivery",
                decision="failed",
                reason=reason or "missing_user",
                scope=scope,
                status="missing_user",
                found=bool(image_id),
                sent=False,
                image_id=image_id,
                confidence=confidence,
                cache_hit=cache_hit,
                latency_ms=latency_ms,
            )
            return False

        async with self._data_lock:
            user = self._reaction_expression_state_owner(
                None,
                user_id,
                scope=scope,
                scope_key=scope_key,
            )
            if not isinstance(user, dict):
                self._note_reaction_expression_runtime(
                    skipped=1,
                    last_reason=reason or "state_unavailable",
                )
                return False
            state = ensure_reaction_expression_state(user)
            scoped_state = reaction_expression_scope_state(state, scope_key)
            if sent:
                record_reaction_expression_sent(
                    state,
                    intent,
                    image_id=image_id,
                    image_path=image_path,
                    image_key=image_key,
                    image_keys=image_keys,
                    now=settled_at,
                    candidate_limit=candidate_limit,
                    duplicate_window_seconds=_safe_float(
                        pending.get("duplicate_window_seconds"),
                        600.0,
                        60.0,
                        86400.0 * 7,
                    ),
                    scope_key=scope_key,
                    reservation_token=reservation_token,
                    cache_hit=cache_hit,
                    latency_ms=latency_ms,
                )
                snapshot_caption = _single_line(
                    pending.get("snapshot_caption"), 700
                )
                remember_snapshot = getattr(
                    self, "_remember_recent_photo_share_snapshot", None
                )
                if snapshot_caption and callable(remember_snapshot):
                    remember_snapshot(
                        user,
                        caption=snapshot_caption,
                        topic=_single_line(pending.get("need"), 220),
                        motive=_single_line(pending.get("match_reason"), 220),
                        reason="reaction_expression_experiment",
                        subject_owner="unknown",
                    )
            else:
                release_reaction_expression_image(
                    state,
                    image_key,
                    image_keys=image_keys,
                    reservation_token=reservation_token,
                )
                release_reaction_expression_reservation(
                    scoped_state,
                    intent_signature=signature,
                    reservation_token=reservation_token,
                )
                append_reaction_expression_outcome(
                    state,
                    status="skipped",
                    reason=_single_line(reason, 80) or "not_sent",
                    intent_signature=signature,
                    now=settled_at,
                    candidate_limit=candidate_limit,
                    image_key=image_key,
                    cache_hit=cache_hit,
                    latency_ms=latency_ms,
                )
            self._persist_reaction_expression_state(
                sections={"reaction_expression_group_states"}
                if scope == "group"
                else {"users"}
            )

        if sent:
            self._note_reaction_expression_runtime(sent=1, last_reason="delivered")
            self._mark_reaction_asset_used(image_id, trace_id=trace_id)
        else:
            self._note_reaction_expression_runtime(
                skipped=1,
                last_reason=_single_line(reason, 80) or "not_sent",
            )
        self._log_reaction_expression_event(
            None,
            trace_id=trace_id,
            stage="delivery",
            decision="sent" if sent else "failed",
            reason="delivered" if sent else (_single_line(reason, 80) or "not_sent"),
            scope=scope,
            status="success" if sent else "not_sent",
            found=bool(image_id),
            sent=sent,
            image_id=image_id,
            confidence=confidence,
            cache_hit=cache_hit,
            latency_ms=latency_ms,
            delivery=_single_line(reason, 80),
            match_basis=pending.get("match_basis"),
        )
        return True

    async def _reaction_expression_vision_verify(
        self,
        event: AstrMessageEvent,
        library: Any,
        lookup: dict[str, Any],
        query_text: str,
        lookup_context: str,
    ) -> dict[str, Any] | None:
        """Let a vision-capable model look at the chosen meme before it is sent.

        Returns ``{"fit": bool, "description": str}`` or ``None`` when no
        vision provider is usable (the caller then trusts the metadata match).
        """
        asset_id = _single_line(lookup.get("asset_id"), 64)
        if not asset_id or library is None:
            return None
        # ponytail: in-memory per-asset description cache; persist into the catalog if restarts matter
        cache = getattr(self, "_reaction_vision_description_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_reaction_vision_description_cache", cache)
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 160)
        provider_id, provider_source, _prompt, provider = self._select_private_image_visual_provider(umo)
        if provider is None or not self._can_run_llm_task(provider_id, task="reaction_vision_verify"):
            return None
        image = await asyncio.to_thread(library.get_analysis_image_data, asset_id, max_edge=512)
        if not image or not image.get("data_url"):
            return None
        known = _single_line(cache.get(asset_id), 300)
        prompt = (
            "你是聊天机器人的眼睛。我准备用这张表情包回应下面的对话，请先看图再判断它是否贴合。\n"
            f"检索需求：{query_text}\n"
            + (f"对话语境：{lookup_context}\n" if lookup_context else "")
            + (f"已知描述：{known}\n" if known else "")
            + "只输出 JSON：{\"fit\": true/false, \"description\": \"一句话描述图里的内容和情绪（30字内）\", \"reason\": \"贴合或不贴合的原因（20字内）\"}"
        )
        prompt_applier = getattr(self, "_apply_task_prompt_override_for_call", None)
        if callable(prompt_applier):
            prompt, _unused_system_prompt = prompt_applier(
                "reaction_vision_verify",
                prompt,
                None,
                flatten_system_prompt=True,
            )
        started = time.time()
        try:
            result = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, image_urls=[image["data_url"]], max_tokens=120),
                timeout=8.0,
            )
            raw_text = str(getattr(result, "completion_text", result) or "").strip()
            payload = self._extract_json_payload(raw_text)
            if not isinstance(payload, dict) or "fit" not in payload:
                raise ValueError("vision verify returned no fit field")
            self._record_llm_usage(
                provider_id=provider_id, task="reaction_vision_verify", prompt=prompt,
                completion=raw_text, resp=result, elapsed_ms=int((time.time() - started) * 1000),
                success=True, budget_exempt=True,
            )
            self._clear_private_image_provider_failure(provider_id, provider_source)
        except Exception as exc:
            self._mark_private_image_provider_failure(provider_id, provider_source, exc, task="reaction_vision_verify")
            logger.debug("表情包视觉复核失败: provider=%s error_type=%s", provider_id, type(exc).__name__)
            return None
        description = _single_line(payload.get("description"), 300)
        if description:
            cache[asset_id] = description
        fit_value = payload.get("fit")
        fit = fit_value if isinstance(fit_value, bool) else str(fit_value).strip().lower() in {"true", "1", "yes", "是"}
        logger.info(
            "表情包视觉复核: fit=%s asset=%s need=%s 描述=%s 原因=%s",
            fit,
            asset_id[:12],
            _single_line(query_text, 80),
            description,
            _single_line(payload.get("reason"), 120),
        )
        return {
            "fit": fit,
            "description": description or known,
            "reason": _single_line(payload.get("reason"), 120),
            "provider_id": provider_id,
        }
