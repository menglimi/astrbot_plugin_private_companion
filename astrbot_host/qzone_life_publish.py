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
from companion_v2.qzone.capability import (EXTENSION as QZONE_EXTENSION,
                                          PUBLISH_ACTION as QZONE_PUBLISH_ACTION)
from companion_v2.qzone.service import local_day, owner_key

logger = logging.getLogger(__name__)

CHECK_SECONDS = 900
DRAFT_TIMEOUT_SECONDS = 120
_QUIET_REASONS = frozenset({
    "host_not_running", "qzone_unavailable", "life_publish_disabled",
    "publish_disabled", "qzone_disabled", "interval", "probability_miss",
})
_REASON_PRIORITY = {
    "published": 0,
    "publish_failed": 1,
    "draft_failed": 2,
    "draft_unavailable": 2,
    "qzone_unauthorized": 3,
    "qzone_settings_unavailable": 3,
    "daily_limit": 4,
    "retry_wait": 4,
    "probability_miss": 5,
    "interval": 5,
    "life_publish_disabled": 6,
    "publish_disabled": 6,
    "qzone_disabled": 6,
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
        for context, authorized in candidates:
            results.append(await self._tick_context(
                session, application, service, context, at, authorized=authorized))
        if len(results) == 1:
            return results[0]
        return min(results, key=lambda item: _REASON_PRIORITY.get(item.get("reason"), 99))

    async def _tick_context(self, session, application, service, context, at, *, authorized=None):
        try:
            settings = application.qzone_settings.read(context, at)
        except Exception:  # noqa: BLE001 - an unreadable Zone config is a no-op, not a crash
            return {"reason": "qzone_settings_unavailable"}
        state = service.state(context)
        decision = life_publish.decide(settings, state, now=at)
        if not decision.due:
            return {"reason": decision.reason}
        if authorized is False:
            return {"reason": "qzone_unauthorized"}
        if not life_publish.should_attempt(settings, self.draw()):
            return {"reason": "probability_miss"}
        # Record the attempt before the model call: a crash or a failed publish
        # must not turn every check tick into another model spend.
        attempt = replace(state, last_action_at={
            **state.last_action_at, life_publish.ATTEMPT_KEY: stamp(at)})
        service.store.write(owner_key(context), attempt, at=at)
        try:
            text = await self._draft(session, context, settings, at)
        except Exception as error:  # noqa: BLE001 - no draft means no public post
            logger.warning("[CompanionV2] 生活说说草稿生成失败 error_type=%s", type(error).__name__)
            self._schedule_retry(service, context, state, at, settings)
            return {"reason": "draft_failed"}
        if not text:
            self._schedule_retry(service, context, state, at, settings)
            return {"reason": "draft_unavailable"}
        try:
            result = await service.publish(
                context, text=text, topic=life_publish.TOPIC.value,
                scene=life_publish.SCENE.value, confidence=0.82, topic_confirmed=True,
                operation_key=life_publish.operation_key(owner_key(context), decision.day, at))
        except Exception as error:  # noqa: BLE001 - publish reports its own codes; log and space the retry
            code = getattr(error, "code", "qzone_publish_failed")
            logger.warning("[CompanionV2] 生活说说发布未完成 code=%s error_type=%s",
                           code, type(error).__name__)
            self._schedule_retry(service, context, state, at, settings, unknown=code in {
                "qzone_delivery_unknown", "qzone_operation_unresolved"})
            return {"reason": "publish_failed", "code": code}
        logger.info("[CompanionV2] 生活说说已发布 status=%s", result.get("status"))
        return {"reason": "published", "status": result.get("status"),
                "operation_id": result.get("operation_id")}

    # ---- target resolution ---------------------------------------------

    @classmethod
    def _target_contexts(cls, session, at):
        """Resolve active task contexts from single-host or multi-host sessions."""
        profile_target = getattr(getattr(session, "profile", None), "target", None)
        if profile_target is not None:
            return (profile_target,)
        # Fork: no autonomous life posts from multi-host sessions (7.2.6 behaviour).
        # The Qzone transport credential is not bound to the publishing account, and
        # the diary material is not scoped to the conversation, so a post could go out
        # under the wrong QQ account or carry another conversation's diary. Re-enable
        # once upstream binds both; the multi-host resolution below stays for merges.
        return ()
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
            authorized = cls._is_publish_authorized(service, context)
            previous = selected.get(key)
            if previous is None or (previous[1] is False and authorized is not False):
                selected[key] = (context, authorized)
        return tuple(selected.values())

    @staticmethod
    def _is_publish_authorized(service, context):
        capabilities = getattr(service, "capabilities", None)
        if capabilities is None:
            return None
        require_role = getattr(capabilities, "require_role_enabled", None)
        authorize = getattr(capabilities, "authorize", None)
        if not callable(require_role) or not callable(authorize):
            return None
        role_ok = require_role(
            extension_id=QZONE_EXTENSION,
            role=context.role_binding.role,
            bot_id=context.scope.bot_id,
        )
        if not getattr(role_ok, "is_success", False):
            return False
        auth_ok = authorize(
            extension_id=QZONE_EXTENSION,
            capability_id=QZONE_PUBLISH_ACTION,
            subject=context.actor.subject,
            scope=context.scope,
        )
        return bool(getattr(auth_ok, "is_success", False))

    @staticmethod
    def _schedule_retry(service, context, state, at, settings, *, unknown=False):
        try:
            minutes = int(settings.get("life_publish_retry_minutes") or 30)
        except (TypeError, ValueError):
            minutes = 30
        if unknown:
            try:
                minutes = max(60, int(settings.get("life_publish_min_interval_hours") or 6) * 60)
            except (TypeError, ValueError):
                minutes = 360
        minutes = min(24 * 60, max(5, minutes))
        retry = replace(state, last_action_at={
            **state.last_action_at,
            life_publish.RETRY_KEY: stamp(at + timedelta(minutes=minutes)),
        })
        service.store.write(owner_key(context), retry, at=at)

    # ---- drafting ------------------------------------------------------

    async def _draft(self, session, context, settings, at):
        from companion_v2.role_work.journal.models import JournalKind

        controller = self.controller
        provider_id = self._provider_id(controller, session)
        provider = controller.context.get_provider_by_id(provider_id) if provider_id else None
        if provider is None or not callable(getattr(provider, "text_chat", None)):
            raise RuntimeError("text provider unavailable")
        profile = self._profile(session, context)
        diary = self._latest_diary(session, context, at, JournalKind)
        request = life_publish.build_request(
            profile, diary, now=at, style=settings.get("life_publish_style_prompt", ""))
        response = await asyncio.wait_for(
            provider.text_chat(prompt=request["prompt"], system_prompt=request["system"],
                               contexts=[], max_tokens=400),
            timeout=DRAFT_TIMEOUT_SECONDS)
        return life_publish.parse_draft(getattr(response, "completion_text", response))

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
    def _latest_diary(session, context, at, journal_kind):
        try:
            owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                                 context.role_binding.role.subject_id)
            application = session.application
            for head in application.role_journal.recent_days(owner, through_date=local_day(at), limit=8):
                if head.key.kind is not journal_kind.DIARY:
                    continue
                entry = application.role_journal.get(head.key, observed_at=at)
                body = entry.draft.body.strip()
                if body:
                    return body
        except Exception:  # noqa: BLE001 - no diary is a valid drafting context
            return ""
        return ""


__all__ = ["CHECK_SECONDS", "QzoneLifePublisher"]
