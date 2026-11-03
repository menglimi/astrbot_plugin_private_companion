"""Adopt one native desktop capture into Screen-owned V2 originals."""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import logging

from companion_v2.capabilities.privacy import is_core
from companion_v2.conversation.external_tools import ExternalToolResponse
from companion_v2.conversation.native_tool_sources import native_turn
from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.foundation.refs import AssetRef, ScopeKind, SourceRef, VersionRef
from companion_v2.screen.models import (
    ClockQuality, ScreenCapture, ScreenCaptureRef, ScreenCoverage, ScreenMediaKind,
)
from companion_v2.screen.originals import ScreenImage
from companion_v2.screen.repositories import ScreenRepository
from companion_v2.sources.models import SourceRecord


ACTION = "screen.capture"
CAPTURE_TTL = timedelta(minutes=30)
logger = logging.getLogger(__name__)


class ScreenAdopter:
    def __init__(self, registry, application):
        self.registry = registry
        self.application = application
        self.screens = ScreenRepository(application.runtime, authorizer=application.tasks.identity)

    @staticmethod
    def reference(invocation, observed_at):
        token = digest(invocation.invocation_id)
        scope = invocation.context.scope
        source_id = "screen-source-" + token
        asset_id = "screen-asset-" + token
        return ScreenCaptureRef("screen-capture-" + token,
            SourceRef(source_id, "screen", scope, VersionRef(source_id, 1),
                      observed_at, observed_at + CAPTURE_TTL),
            AssetRef(asset_id, "screen", scope, VersionRef(asset_id, 1), "pending"))

    def preflight(self, invocation):
        context = invocation.context
        if context.scope.kind is not ScopeKind.PRIVATE:
            if (context.scope.kind is not ScopeKind.GROUP
                    or not is_core(self.application.runtime, context.actor.subject)):
                raise TaskError("screen_scope_denied", "screen capture requires the original private conversation")
            # Core administrators already bypass per-window screen consent in
            # the privacy policy; keep the capture observation-only and audited.
            logger.info("[CompanionV2] 核心管理员群聊屏幕采集已授权 actor=%s group=%s",
                        context.actor.subject.subject_id, context.scope.native_scope_id)
        reference = self.reference(invocation, self.registry.now())
        return reference, require(self.screens.admit_capture(reference,
            role=invocation.context.role_binding.role, resolved=invocation.context.actor,
            observed_at=self.registry.now()))

    def recheck(self, invocation, reference, stamp):
        current = require(self.screens.admit_capture(reference,
            role=invocation.context.role_binding.role, resolved=invocation.context.actor,
            observed_at=self.registry.now()))
        if current != stamp:
            raise TaskError("screen_admission_changed", "screen access changed during the original device call")

    def adopt(self, invocation, native, reference, stamp):
        if native.state != "succeeded" or len(native.artifacts) != 1:
            raise TaskError("screen_artifact_unavailable", "one original screenshot is required")
        artifact = native.artifacts[0]
        result = native.result
        if (type(result) is not dict or set(result) != {
                "device_id", "surface", "captured_at", "clock_quality", "window_title", "gaps"}
                or type(result["gaps"]) is not list or len(result["gaps"]) > 16
                or any(type(gap) is not str for gap in result["gaps"])
                or result["window_title"] is not None and type(result["window_title"]) is not str):
            raise TaskError("screen_artifact_invalid", "screenshot coverage differs from the native receipt")
        try:
            image = ScreenImage(artifact.mime_type, artifact.content)
            if image.content_hash != artifact.content_hash or len(image.content) != artifact.size_bytes:
                raise ValueError("artifact content changed")
            captured_at = datetime.fromisoformat(result["captured_at"])
            coverage = ScreenCoverage(result["surface"],
                ("image.viewport", "window.title") if result["window_title"] else ("image.viewport",),
                tuple(result["gaps"]), ClockQuality(result["clock_quality"]))
            now = self.registry.now()
            bound = ScreenCaptureRef(reference.capture_id, reference.source,
                AssetRef(reference.asset.asset_id, "screen", reference.asset.scope,
                         reference.asset.version, image.content_hash))
            capture = ScreenCapture(bound, result["device_id"], ScreenMediaKind.IMAGE,
                coverage, captured_at, now, now)
        except (ValueError, TypeError, KeyError) as error:
            raise TaskError("screen_artifact_invalid", "screenshot cannot be decoded from its native receipt") from error
        self.recheck(invocation, reference, stamp)
        require(self.screens.publish_image(capture, image, audience=invocation.context.audience,
            window_title=result["window_title"], role=invocation.context.role_binding.role,
            resolved=invocation.context.actor, observed_at=now, expected_admission_stamp=stamp))
        # Core vision receives the original immediately. Interpretation is an
        # optional background consumer and cannot invalidate a captured image.
        interpretation = getattr(self.application, "screen_interpretation", None)
        parent = self.application.runtime.read_one("SELECT kind FROM execution_tasks WHERE task_id = ? AND origin_key = ?",
            (invocation.parent_task_id, invocation.context.key))
        # A supervised peek hands this capture to its own glance interpretation.
        # Ordinary tool calls still keep their optional reply interpretation.
        if interpretation is not None and not (parent is not None and parent["kind"] == "screen.peek"):
            try:
                interpretation.submit(invocation.context, bound, purpose="reply",
                                      parent_task_id=invocation.parent_task_id)
            except Exception as error:
                logger.warning("[CompanionV2] 屏幕后台解读调度降级 error_type=%s", type(error).__name__)
        payload = encode({"native": native.metadata(invocation.descriptor.binding),
                          "screen_capture": {"reference": asdict(bound),
                                             "content_hash": image.content_hash}})
        source_id = "native:" + invocation.invocation_id
        source = SourceRecord(SourceRef(source_id, "screen", invocation.context.scope,
            VersionRef(invocation.invocation_id, 1), now), invocation.context.audience,
            digest(payload), payload)
        from ..media import inspect_image
        return ExternalToolResponse(invocation.invocation_id, invocation.context.key,
            invocation.arguments_hash, invocation.descriptor.binding, native.state, native.effect,
            source=source, operation_id=native.operation_id, receipt_ref=native.receipt_ref,
            images=(inspect_image(image.content, 0),) if native_turn(invocation.parent_task_id) else (),
            dependencies=(bound.source,))
