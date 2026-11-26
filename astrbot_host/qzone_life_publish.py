"""Background driver for the QQ Zone life post (V1 ``enable_qzone_life_publish``).

The decision and text rules live in ``companion_v2/qzone/life_publish.py``; this
module only supplies the host pieces the pure module must not own: the running
session, the selected provider, the role's latest diary, and the Qzone service.

Three failure rules:

* **The attempt is recorded before any model spend.** A failed draft or a
  publish error therefore spaces the next try by ``life_publish_min_interval_hours``
  instead of retrying every check tick.
* **An unknown publish result is never retried.** The attempt marker stays, and
  the operation key of that attempt is never reused.
* **Every publish still passes the normal Qzone gates** (installation, role
  enablement, window consent, credentials, policy interval/caps). This driver
  does not grant anything; a disabled or unauthorized Zone is a logged no-op.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import logging
import random

from companion_v2.execution.models import TaskError, stamp
from companion_v2.foundation.refs import RoleOwnerRef, ScopeRef
from companion_v2.qzone import life_publish
from companion_v2.qzone.capability import PUBLISH_CONVERSATION_CHANGED
from companion_v2.qzone.service import owner_key

logger = logging.getLogger(__name__)

CHECK_SECONDS = 900
DRAFT_TIMEOUT_SECONDS = 120
_QUIET_REASONS = frozenset({
    "host_not_running", "qzone_unavailable", "life_publish_disabled",
    "publish_disabled", "qzone_disabled", "interval", "probability_miss",
})
_UNKNOWN_DELIVERY_CODES = frozenset({
    "qzone_delivery_unknown", "qzone_operation_unresolved",
})
_SHORT_RETRY_PUBLISH_CODES = frozenset({
    "qzone_operation_failed", "qzone_credential_missing", "qzone_publish_failed",
})
_DUPLICATE_PUBLISH_REASONS = frozenset({"这条说说和刚发过的重复", "这条说说和刚发过的太像"})
_REASON_PRIORITY = {
    "published": 0,
    "publish_failed": 1,
    "draft_failed": 2,
    "draft_unavailable": 2,
    "qzone_unauthorized": 3,
    PUBLISH_CONVERSATION_CHANGED: 3,
    "qzone_settings_unavailable": 3,
    "daily_limit": 4,
    "retry_wait": 4,
    "probability_miss": 5,
    "interval": 5,
    "life_publish_disabled": 6,
    "publish_disabled": 6,
    "qzone_disabled": 6,
    "qzone_unavailable": 7,
    "host_not_running": 7,
}


class QzoneLifePublisher:
    def __init__(self, controller, *, interval_seconds=CHECK_SECONDS, draw=random.random):
        self.controller = controller
        self.interval_seconds = max(60, int(interval_seconds))
        self.draw = draw
        self._task = None
        self._closed = False

    # ---- lifecycle -----------------------------------------------------

    def start(self):
        if self._closed or (self._task is not None and not self._task.done()):
            return self
        self._task = asyncio.create_task(self._run(), name="companion:qzone-life-publish")
        logger.info("[CompanionV2] 生活说说检查已启动 interval_seconds=%s", self.interval_seconds)
        return self

    async def close(self):
        self._closed = True
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown is best effort
                pass

    async def _run(self):
        while not self._closed:
            try:
                await asyncio.sleep(self.interval_seconds)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - one bad tick must not stop the loop
                logger.warning("[CompanionV2] 生活说说检查失败 error_type=%s", type(error).__name__)

    # ---- one check -----------------------------------------------------

    async def tick(self, now=None):
        result = await self._tick(now)
        if result.get("reason") not in _QUIET_REASONS:
            logger.info("[CompanionV2] 生活说说检查 reason=%s", result.get("reason"))
        return result

    async def _tick(self, now):
        controller = self.controller
        session = getattr(controller, "session", None)
        if session is None or getattr(getattr(session, "application", None), "state", "") != "running":
            return {"reason": "host_not_running"}
        application = session.application
        service = getattr(application, "qzone", None)
        if service is None:
            return {"reason": "qzone_unavailable"}
        at = now or controller.now()
        contexts = self._target_contexts(session, at)
        candidates = self._select_contexts_by_owner(service, contexts)
        if not candidates:
            return {"reason": "qzone_unavailable"}
        results = []
        for context, _authorized in candidates:
            results.append(await self._tick_context(
                session, application, service, context, at))
        if len(results) == 1:
            return results[0]
        return min(results, key=lambda item: _REASON_PRIORITY.get(item.get("reason"), 99))

    async def _tick_context(self, session, application, service, context, at, *, authorized=None):
        if self._closed or getattr(application, "state", "") != "running":
            return {"reason": "host_not_running"}
        # Re-verify active target/role binding and publish authorization right
        # before drafting so revocations or persona switches that occur while a
        # previous role awaits model drafting never send stale materials to LLM.
        owner = owner_key(context)
        live_for_owner = [item for item in self._target_contexts(session, at) if owner_key(item) == owner]
        if not live_for_owner:
            return {"reason": "qzone_unavailable"}
        context, authorization_reason = self._select_contexts_by_owner(service, live_for_owner)[0]
        try:
            settings = application.qzone_settings.read(context, at)
        except Exception:  # noqa: BLE001 - an unreadable Zone config is a no-op, not a crash
            return {"reason": "qzone_settings_unavailable"}
        state = service.state(context)
        decision = life_publish.decide(settings, state, now=at)
        if not decision.due:
            return {"reason": decision.reason}
        if authorization_reason:
            return {"reason": authorization_reason}
        if not life_publish.should_attempt(settings, self.draw()):
            return {"reason": "probability_miss"}
        # Record the attempt before the model call and clear any expired short
        # retry marker so a crash or deterministic block respects the normal
        # interval instead of looping on a stale retry timestamp.
        attempt_actions = {**state.last_action_at, life_publish.ATTEMPT_KEY: stamp(at)}
        attempt_actions.pop(life_publish.RETRY_KEY, None)
        attempt = replace(state, last_action_at=attempt_actions)
        service.store.write(owner, attempt, at=at)
        try:
            plan = await self._draft_plan(
                session, application, context, settings, at,
                recent_publish_texts=state.recent_publish_texts,
            )
        except Exception as error:  # noqa: BLE001 - no draft means no public post
            logger.warning("[CompanionV2] 生活说说草稿生成失败 error_type=%s", type(error).__name__)
            self._schedule_retry(service, context, attempt, at, settings)
            return {"reason": "draft_failed"}
        if not plan or not plan.text:
            self._schedule_retry(service, context, attempt, at, settings)
            return {"reason": "draft_unavailable"}
        # Re-verify the exact drafted context is still active and authorized
        # right before resolving/generating images or publishing so mid-draft
        # revocations or persona switches never act with stale consent.
        if self._closed or getattr(application, "state", "") != "running":
            return {"reason": "host_not_running"}
        if not self._is_context_active(context, self._target_contexts(session, at)):
            return {"reason": "qzone_unavailable"}
        authorization_reason = self._publish_authorization_reason(service, context)
        if authorization_reason:
            return {"reason": authorization_reason}
        op_key = life_publish.operation_key(owner, decision.day, at)
        images, source_refs = await self._prepare_images(application, context, plan, at=at, operation_key=op_key)
        if self._closed or getattr(application, "state", "") != "running":
            return {"reason": "host_not_running"}
        if not self._is_context_active(context, self._target_contexts(session, at)):
            return {"reason": "qzone_unavailable"}
        authorization_reason = self._publish_authorization_reason(service, context)
        if authorization_reason:
            return {"reason": authorization_reason}
        try:
            result = await service.publish(
                context, text=plan.text, topic=life_publish.TOPIC.value,
                scene=life_publish.SCENE.value, confidence=0.82, topic_confirmed=True,
                source_refs=source_refs, images=images, operation_key=op_key)
        except Exception as error:  # noqa: BLE001 - publish reports its own codes; log and space the retry
            code = getattr(error, "code", "qzone_publish_failed")
            reason_text = str(error) if isinstance(error, TaskError) and code == "qzone_action_blocked" else ""
            if reason_text in _DUPLICATE_PUBLISH_REASONS:
                logger.warning("[CompanionV2] 生活说说发布未完成 code=%s error_type=%s reason=%s",
                               code, type(error).__name__, reason_text)
            else:
                logger.warning("[CompanionV2] 生活说说发布未完成 code=%s error_type=%s",
                               code, type(error).__name__)
            self._schedule_retry(
                service, context, attempt, at, settings,
                unknown=code in _UNKNOWN_DELIVERY_CODES,
                defer_interval=code not in _SHORT_RETRY_PUBLISH_CODES,
            )
            return {"reason": "publish_failed", "code": code}
        logger.info("[CompanionV2] 生活说说已发布 status=%s image_count=%s", result.get("status"), len(images))
        reply = {"reason": "published", "status": result.get("status"),
                 "operation_id": result.get("operation_id")}
        if images:
            reply["image_count"] = len(images)
        return reply

    # ---- target resolution ---------------------------------------------

    @classmethod
    def _target_contexts(cls, session, at):
        """Resolve active task contexts from single-host or multi-host sessions."""
        profile_target = getattr(getattr(session, "profile", None), "target", None)
        if profile_target is not None:
            return (profile_target,)
        authority = getattr(session, "authority", None)
        settings = getattr(session, "settings", None)
        if authority is None or settings is None:
            return ()
        return cls._resolve_multi_contexts(session, authority, settings, at)

    @classmethod
    def _resolve_multi_contexts(cls, session, authority, settings, at):
        targets = []
        seen_targets = set()
        candidates = [
            *(getattr(settings, "primary_targets", ()) or ()),
            *(getattr(settings, "targets", ()) or ()),
        ]
        prepared = getattr(session, "_prepared_wildcard_private_targets", None)
        if callable(prepared):
            candidates.extend(prepared() or ())
        for target in candidates:
            target_id = getattr(target, "target_id", "")
            participant_id = getattr(target, "participant_id", "*")
            if (not target_id or getattr(target, "group_id", None) is not None
                    or participant_id == "*" or target_id in seen_targets):
                continue
            seen_targets.add(target_id)
            targets.append(target)

        canonical = getattr(settings, "canonical_role_id", None)
        personas = getattr(settings, "personas", ()) or ()
        contexts = []
        for target in targets:
            bot_roles = cls._bot_role_ids(personas, target.bot_id, canonical)
            active_role = cls._active_role_id(authority, target.target_id, at, canonical)
            if active_role and (not bot_roles or active_role in bot_roles):
                role_ids = (active_role,)
            else:
                role_ids = bot_roles
            for role_id in role_ids:
                context = cls._authority_context(authority, target.target_id, role_id)
                if context is not None:
                    contexts.append(context)
        return tuple(contexts)

    @staticmethod
    def _bot_role_ids(personas, bot_id, canonical):
        roles = []
        seen = set()
        for persona in personas:
            if getattr(persona, "bot_id", None) != bot_id:
                continue
            raw_role = getattr(persona, "role_id", "")
            role_id = canonical(raw_role) if callable(canonical) and raw_role else raw_role
            if role_id and role_id not in seen:
                seen.add(role_id)
                roles.append(role_id)
        return tuple(roles)

    @staticmethod
    def _active_role_id(authority, target_id, at, canonical):
        try:
            stored = authority.read("host.multi.active:" + target_id)
            if not stored:
                return ""
            document = json.loads(stored) if isinstance(stored, str) else stored
            scope = ScopeRef.restore(document)
            resolution = authority.personas.resolve(scope, observed_at=at)
            if resolution.failure is not None:
                return ""
            role_id = resolution.unwrap().binding.role.subject_id
            return canonical(role_id) if callable(canonical) else role_id
        except (TaskError, ValueError, KeyError, TypeError):
            return ""

    @staticmethod
    def _authority_context(authority, target_id, role_id):
        try:
            return authority.context(target_id, role_id)
        except (TaskError, ValueError, KeyError, TypeError):
            return None

    @classmethod
    def _select_contexts_by_owner(cls, service, contexts):
        """Deduplicate candidate windows per role owner, preferring authorized ones."""
        selected = {}
        for context in contexts:
            key = owner_key(context)
            authorization_reason = cls._publish_authorization_reason(service, context)
            previous = selected.get(key)
            if previous is None or (previous[1] and not authorization_reason):
                selected[key] = (context, authorization_reason)
        return tuple(selected.values())

    @staticmethod
    def _is_context_active(context, live_contexts):
        same_window = getattr(context, "same_window", None)
        if callable(same_window):
            return any(same_window(candidate) for candidate in live_contexts)
        return any(candidate == context for candidate in live_contexts)

    @staticmethod
    def _publish_authorization_reason(service, context):
        try:
            service.require_publish_authorization(context)
        except TaskError as error:
            if error.code == PUBLISH_CONVERSATION_CHANGED:
                return error.code
            return "qzone_unauthorized"
        return ""

    @staticmethod
    def _schedule_retry(service, context, state, at, settings, *, unknown=False, defer_interval=False):
        current = service.state(context) if callable(getattr(service, "state", None)) else state
        actions = {**current.last_action_at, life_publish.ATTEMPT_KEY: stamp(at)}
        if unknown or defer_interval:
            # Unknown deliveries and deterministic policy/auth blocks must not
            # spin every 30 minutes; clearing RETRY_KEY defers to the normal
            # life_publish_min_interval_hours window via ATTEMPT_KEY.
            actions.pop(life_publish.RETRY_KEY, None)
            service.store.write(owner_key(context), replace(current, last_action_at=actions), at=at)
            return
        try:
            minutes = int(settings.get("life_publish_retry_minutes") or 30)
        except (TypeError, ValueError):
            minutes = 30
        minutes = min(24 * 60, max(5, minutes))
        actions[life_publish.RETRY_KEY] = stamp(at + timedelta(minutes=minutes))
        service.store.write(owner_key(context), replace(current, last_action_at=actions), at=at)

    # ---- drafting & images ---------------------------------------------

    async def _draft(self, session, context, settings, at, *, recent_publish_texts=()):
        plan = await self._draft_plan(
            session, session.application, context, settings, at,
            recent_publish_texts=recent_publish_texts,
        )
        return plan.text

    async def _draft_plan(self, session, application, context, settings, at, *, recent_publish_texts=()):
        from companion_v2.role_work.journal.models import JournalKind

        controller = self.controller
        provider_id = self._provider_id(controller, session)
        provider = controller.context.get_provider_by_id(provider_id) if provider_id else None
        if provider is None or not callable(getattr(provider, "text_chat", None)):
            raise RuntimeError("text provider unavailable")
        profile = self._profile(session, context)
        diaries = self._recent_diaries(session, context, at, JournalKind)
        activities = self._recent_activities(session, context, at)
        schedules = self._recent_schedules(session, context, at)
        album_candidates = self._album_candidates(session, context, at)
        life_images = getattr(application, "qzone_life_images", None)
        can_generate = bool(life_images is not None and life_images.available(context, at))
        request = life_publish.build_request(
            profile, diaries, now=at,
            style=settings.get("life_publish_style_prompt", ""),
            recent_publish_texts=recent_publish_texts,
            activities=activities,
            schedules=schedules,
            album_candidates=album_candidates,
            can_generate_image=can_generate,
        )
        max_tokens = 700 if (can_generate or album_candidates) else 400
        response = await asyncio.wait_for(
            provider.text_chat(prompt=request["prompt"], system_prompt=request["system"],
                               contexts=[], max_tokens=max_tokens),
            timeout=DRAFT_TIMEOUT_SECONDS)
        return life_publish.parse_plan(
            getattr(response, "completion_text", response),
            allowed_album_ids=[item["image_id"] for item in album_candidates],
            allow_generate=can_generate,
        )

    @staticmethod
    async def _prepare_images(application, context, plan, *, at, operation_key):
        life_images = getattr(application, "qzone_life_images", None)
        if plan.image_source == "album" and plan.album_image_id:
            try:
                from companion_v2.qzone.images import album_images
                images, _ = album_images(application, context, at, [plan.album_image_id])
                refs = tuple(image.source_id for image in images if image.source_id)
                return images, refs
            except Exception as error:  # noqa: BLE001 - fall back to generation or text-only
                logger.warning("[CompanionV2] 生活说说相册配图读取失败 error_type=%s", type(error).__name__)
                if not (plan.image_prompt and life_images is not None
                        and life_images.available(context, at, include_character=plan.include_character)):
                    return (), ()
        if plan.image_source in ("generate", "album") and plan.image_prompt and life_images is not None:
            try:
                generated = await life_images.generate(
                    context,
                    plan.image_prompt,
                    operation_key=operation_key,
                    include_character=plan.include_character,
                )
                if generated is not None:
                    return (generated,), ()
            except Exception as error:  # noqa: BLE001 - image failure never aborts text post
                logger.warning("[CompanionV2] 生活说说生图失败 error_type=%s", type(error).__name__)
        return (), ()

    @staticmethod
    def _provider_id(controller, session):
        from astrbot_host.persona_workshop_api import _session_provider_id

        for source in (session, controller):
            if source is None:
                continue
            found = _session_provider_id(source)
            if found:
                return found
        return ""

    @staticmethod
    def _profile(session, context=None):
        try:
            from astrbot_host.persona_workshop_api import current_role_profile
            return current_role_profile(session.runtime, session, context=context)
        except Exception:  # noqa: BLE001 - drafting can proceed on the diary alone
            return {}

    @staticmethod
    def _recent_diaries(session, context, at, journal_kind, *, limit=3):
        entries = []
        try:
            owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                                 context.role_binding.role.subject_id)
            journal = session.application.role_journal
            heads = journal.context_days(owner, context.scope, observed_at=at, limit=16)
            for head in heads:
                if head.key.kind is not journal_kind.DIARY:
                    continue
                try:
                    entry = journal.get_for_audience(
                        head.key, scope=context.scope,
                        recipient=context.actor.subject, observed_at=at,
                    )
                except TaskError:
                    continue
                body = entry.draft.body.strip()
                if body:
                    entries.append({"local_date": head.key.local_date, "body": body})
                    if len(entries) >= limit:
                        break
        except Exception:  # noqa: BLE001 - no diary is a valid drafting context
            return ()
        return tuple(entries)

    @staticmethod
    def _recent_activities(session, context, at, *, limit=6):
        try:
            role_life = getattr(session.application, "role_life", None)
            if role_life is None:
                return ()
            owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                                 context.role_binding.role.subject_id)
            activities = role_life.context_activities(owner, context.scope, observed_at=at, limit=limit)
            items = []
            for activity in activities:
                if activity.owner != owner or activity.scope != context.scope:
                    continue
                observed = activity.updated_at or activity.started_at
                if observed is not None and observed > at:
                    continue
                if activity.work is not None and activity.work.context_key != context.key:
                    continue
                entry = {
                    "state": activity.state.value,
                    "description": activity.description,
                    "checkpoint": activity.checkpoint,
                    "updated_at": observed.isoformat() if observed is not None else "",
                }
                if activity.reading is not None and activity.reading.reflection:
                    entry["reflection"] = activity.reading.reflection
                items.append(entry)
            return tuple(items)
        except Exception:  # noqa: BLE001 - optional life context
            return ()

    @staticmethod
    def _recent_schedules(session, context, at, *, limit=6):
        try:
            calendar = getattr(session.application, "calendar", None)
            if calendar is None:
                return ()
            page = calendar.occurrences(
                context,
                start=at - timedelta(hours=24),
                end=at + timedelta(hours=24),
                kinds=("role_plan",),
            )
            items = []
            for item in page.get("items") or ():
                if item.get("kind") != "role_plan" or item.get("state") == "cancelled":
                    continue
                items.append({
                    "state": item.get("state", ""),
                    "title": item.get("title", ""),
                    "detail": item.get("detail", ""),
                    "start": item.get("start", ""),
                    "end": item.get("end", ""),
                })
                if len(items) >= limit:
                    break
            return tuple(items)
        except Exception:  # noqa: BLE001 - missing calendar access degrades gracefully
            return ()

    @staticmethod
    def _album_candidates(session, context, at, *, limit=8):
        try:
            from companion_v2.qzone.images import list_album_candidates
            return tuple(list_album_candidates(session.application, context, at, limit=limit))
        except Exception:  # noqa: BLE001 - optional album listing
            return ()

    @staticmethod
    def _latest_diary(session, context, at, journal_kind):
        entries = QzoneLifePublisher._recent_diaries(session, context, at, journal_kind, limit=1)
        return entries[0]["body"] if entries else ""


__all__ = ["CHECK_SECONDS", "QzoneLifePublisher"]
