"""Review the last durable proactive capture and its exact interpretation."""

import base64
import json

from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.execution.models import TaskError, digest, require
from companion_v2.foundation.refs import ScopeKind
from companion_v2.identity_policy.object_access import ObjectAccessPolicy

from .models import SCREEN_EXTENSION, ScreenMediaKind
from .repositories import ScreenRepository


# 装配/启用状态回答的是「现在能不能采」，不是「这份记录归谁看」。它没就绪
# 时给出空记录加原因，让页面引导去装配；授权与核心权限错误仍然照常抛出，
# 否则会把真实的授权问题伪装成「还没有记录」。
_DEFERRED_ACCESS = frozenset((
    "capability_not_installed",
    "capability_not_admissible",
    "capability_role_not_enabled",
))


class ScreenObservationReview:
    def __init__(self, application, context):
        self.app, self.context = application, context
        self.runtime = application.runtime
        self.screens = ScreenRepository(self.runtime, authorizer=application.tasks.identity)

    def _access(self, operator, at):
        if self.context.scope.kind is not ScopeKind.PRIVATE:
            raise TaskError("screen_private_required", "select the original private window")
        require(ObjectAccessPolicy(self.runtime).scope_access(operator, scope=self.context.scope,
            action="capability.view", observed_at=at, core_only=True))
        require(self.app.tasks.authorize_context(self.context, action="conversation.reply", observed_at=at))
        require(CapabilityRepository(self.runtime).require_role_enabled(extension_id=SCREEN_EXTENSION,
            role=self.context.role_binding.role, bot_id=self.context.scope.bot_id))

    def _last_capture(self):
        # These task and original receipts already survive restart. A failed
        # device attempt must not replace the last actual retained original.
        offset = 0
        latest_attempt = None
        while True:
            rows = self.runtime.read_all("""SELECT task_id, state, created_at, record_json FROM execution_tasks
                WHERE origin_key = ? AND kind = 'screen.peek'
                ORDER BY created_at DESC, task_id DESC LIMIT 64 OFFSET ?""", (self.context.key, offset))
            if not rows:
                return None, latest_attempt
            for row in rows:
                if latest_attempt is None:
                    latest_attempt = {"task_id": row["task_id"], "state": row["state"],
                                      "created_at": row["created_at"]}
                invocation_id = "screen-peek-" + digest([row["task_id"], self.context.key])
                capture_id = "screen-capture-" + digest(invocation_id)
                reference = self.screens.published_reference(capture_id, scope=self.context.scope)
                if reference.is_success:
                    return (row, reference.unwrap()), latest_attempt
                if reference.failure.code != "screen_capture_not_found":
                    require(reference)
            offset += len(rows)

    def latest(self, operator, at):
        try:
            self._access(operator, at)
        except TaskError as error:
            if error.code not in _DEFERRED_ACCESS:
                raise
            with self.runtime.read_snapshot():
                _, latest_attempt = self._last_capture()
            return {"observation": None, "reason": error.code,
                    "latest_attempt": latest_attempt}
        with self.runtime.read_snapshot():
            last, latest_attempt = self._last_capture()
            if last is None:
                return {"observation": None, "latest_attempt": latest_attempt}
            peek, reference = last
            capture, _ = require(self.screens.read_retained_capture(reference,
                resolved=self.context.actor, observed_at=at))
            task_id = "screen-interpretation:" + digest([self.context.key, reference.capture_id, "glance"])
            row = self.runtime.read_one("""SELECT state, record_json FROM execution_tasks
                WHERE task_id = ? AND origin_key = ? AND kind = 'screen.interpretation'""",
                (task_id, self.context.key))
            state = row["state"] if row is not None else "not_started"
            result = {"state": state, "task_id": task_id, "text": None, "analysis_id": None}
            if row is not None:
                record = json.loads(row["record_json"])
                result["reason"] = record.get("reason")
                if state == "succeeded" and record.get("result_ref"):
                    analysis = require(self.screens.read_retained_analysis(reference, record["result_ref"],
                        resolved=self.context.actor, observed_at=at))
                    if analysis.purpose != "glance":
                        raise TaskError("screen_analysis_changed", "this result is not a proactive observation")
                    result.update(text=analysis.interpretation, analysis_id=analysis.analysis_id,
                                  analyzed_at=analysis.created_at.isoformat())
                call = self.runtime.read_one("""SELECT provider_json FROM model_calls
                    WHERE task_id = ? ORDER BY position DESC LIMIT 1""", (task_id,))
                if call is not None:
                    result["provider"] = json.loads(call["provider_json"]).get("provider")
            return {"observation": {"capture_id": reference.capture_id,
                "media_kind": capture.media_kind.value, "device_id": capture.device_id,
                "captured_at": capture.captured_at.isoformat(), "content_hash": reference.asset.content_hash,
                "peek_task_id": peek["task_id"],
                "mode": "diagnostic" if peek["task_id"].startswith("screen-peek-diagnostic:") else "automatic",
                "analysis": result}, "latest_attempt": latest_attempt}

    def original(self, operator, at, capture_id):
        self._access(operator, at)
        with self.runtime.read_snapshot():
            last, _ = self._last_capture()
            if last is None:
                raise TaskError("screen_capture_not_found", "there is no saved proactive capture")
            _, reference = last
            if reference.capture_id != capture_id:
                raise TaskError("screen_observation_changed", "a newer original is available; refresh the review")
            capture, _ = require(self.screens.read_retained_capture(reference,
                resolved=self.context.actor, observed_at=at))
            if capture.media_kind is not ScreenMediaKind.IMAGE:
                raise TaskError("screen_original_unavailable", "this provider has no retained recording body")
            image = require(self.screens.read_retained_image(reference,
                resolved=self.context.actor, observed_at=at))
            return {"capture_id": capture_id, "media_kind": capture.media_kind.value,
                "mime_type": image.mime_type,
                "data_url": "data:" + image.mime_type + ";base64," + base64.b64encode(image.content).decode("ascii")}
