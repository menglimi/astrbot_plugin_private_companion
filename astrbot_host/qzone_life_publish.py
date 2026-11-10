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
import logging
import random

from companion_v2.execution.models import stamp
from companion_v2.foundation.refs import RoleOwnerRef
from companion_v2.qzone import life_publish
from companion_v2.qzone.service import local_day, owner_key

logger = logging.getLogger(__name__)

CHECK_SECONDS = 900
DRAFT_TIMEOUT_SECONDS = 120


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
        if result.get("reason") not in ("host_not_running", "qzone_unavailable",
                                        "life_publish_disabled", "publish_disabled",
                                        "qzone_disabled", "interval", "probability_miss"):
            logger.info("[CompanionV2] 生活说说检查 reason=%s", result.get("reason"))
        return result

    async def _tick(self, now):
        controller = self.controller
        session = getattr(controller, "session", None)
        if session is None or getattr(getattr(session, "application", None), "state", "") != "running":
            return {"reason": "host_not_running"}
        application = session.application
        service = getattr(application, "qzone", None)
        context = getattr(getattr(session, "profile", None), "target", None)
        if service is None or context is None:
            return {"reason": "qzone_unavailable"}
        at = now or controller.now()
        try:
            settings = application.qzone_settings.read(context, at)
        except Exception:  # noqa: BLE001 - an unreadable Zone config is a no-op, not a crash
            return {"reason": "qzone_settings_unavailable"}
        state = service.state(context)
        decision = life_publish.decide(settings, state, now=at)
        if not decision.due:
            return {"reason": decision.reason}
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
            return {"reason": "draft_failed"}
        if not text:
            return {"reason": "draft_unavailable"}
        try:
            result = await service.publish(
                context, text=text, topic=life_publish.TOPIC.value,
                scene=life_publish.SCENE.value, confidence=0.82, topic_confirmed=True,
                operation_key=life_publish.operation_key(owner_key(context), decision.day, at))
        except Exception as error:  # noqa: BLE001 - publish reports its own codes; log and space the retry
            logger.warning("[CompanionV2] 生活说说发布未完成 code=%s error_type=%s",
                           getattr(error, "code", "qzone_publish_failed"), type(error).__name__)
            return {"reason": "publish_failed", "code": getattr(error, "code", "")}
        logger.info("[CompanionV2] 生活说说已发布 status=%s", result.get("status"))
        return {"reason": "published", "status": result.get("status"),
                "operation_id": result.get("operation_id")}

    # ---- drafting ------------------------------------------------------

    async def _draft(self, session, context, settings, at):
        from companion_v2.role_work.journal.models import JournalKind

        controller = self.controller
        common = getattr(getattr(controller, "settings", None), "common", None)
        provider_id = str(getattr(common, "provider_id", "") or "").strip()
        provider = controller.context.get_provider_by_id(provider_id) if provider_id else None
        if provider is None or not callable(getattr(provider, "text_chat", None)):
            raise RuntimeError("text provider unavailable")
        profile = self._profile(session)
        diary = self._latest_diary(session, context, at, JournalKind)
        request = life_publish.build_request(
            profile, diary, now=at, style=settings.get("life_publish_style_prompt", ""))
        response = await asyncio.wait_for(
            provider.text_chat(prompt=request["prompt"], system_prompt=request["system"],
                               contexts=[], max_tokens=400),
            timeout=DRAFT_TIMEOUT_SECONDS)
        return life_publish.parse_draft(getattr(response, "completion_text", response))

    @staticmethod
    def _profile(session):
        try:
            from astrbot_host.persona_workshop_api import current_role_profile
            return current_role_profile(session.runtime, session)
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
