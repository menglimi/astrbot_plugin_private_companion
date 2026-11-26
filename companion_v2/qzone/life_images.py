"""Supervised scene image generation for autonomous QQ Zone life posts."""

from __future__ import annotations

from datetime import timedelta
import logging

from companion_v2.execution.models import (
    Lane,
    ProviderBinding,
    SideEffect,
    TaskDefinition,
    TaskError,
    TaskRegistration,
    TaskResult,
    TaskSpec,
    digest,
    require,
)
from companion_v2.execution.runner import ExactTaskRunner
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import ScopeKind
from companion_v2.qzone.images import PublishImage
from companion_v2.references.models import ReferenceRole


KIND = "qzone.life_image"
CALL_ID = "qzone-life-image"
TIMEOUT = 300
MAX_PROMPT_CHARS = 1500
logger = logging.getLogger(__name__)


class QzoneLifeImageGenerator:
    """Generate one scene image under the role's proactive image quota."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.port = None
        self.quota = None
        self.app = None
        self.runner = None
        self._pending = {}
        self._results = {}
        self.closed = False
        self.registrations = (
            TaskRegistration(
                TaskDefinition(
                    KIND,
                    KIND,
                    "extension.tool.invoke",
                    Lane.BACKGROUND,
                    side_effect=SideEffect.EXTERNAL_NETWORK,
                    binding=ProviderBinding(KIND, "host.image", "1", "qzone-life-image-v1"),
                    timeout_seconds=TIMEOUT,
                    max_attempts=1,
                    concurrency=1,
                ),
                self.validate,
            ),
        )

    def attach(self, app):
        self.app = app
        self.quota = getattr(getattr(app, "messages", None), "image_quota", None)
        self.runner = ExactTaskRunner(
            app.tasks,
            frozenset({KIND}),
            self._generate,
            waiting=app.background_waiting,
        )

    def _base_identity_id(self, context, at):
        if self.app is None:
            return None
        references = getattr(self.app, "references", None)
        ref_settings = getattr(self.app, "reference_settings", None)
        if references is None or ref_settings is None:
            return None
        try:
            if not ref_settings.read(context, at).get("enabled"):
                return None
            assets = references.list_assets(context, role=ReferenceRole.IDENTITY)
            owner = references.owner(context)
            for asset in assets:
                if references.assets.usage_of(owner, asset.asset_id).get("base_identity"):
                    return asset.asset_id
            return assets[0].asset_id if assets else None
        except (TaskError, KeyError, TypeError, ValueError):
            return None

    def _route(self, context, at, *, include_character=False):
        if self.closed or self.port is None or self.app is None or self.runner is None:
            return None, None
        if getattr(self.app, "state", "") != "running":
            return None, None
        if getattr(getattr(context, "scope", None), "kind", None) is not ScopeKind.PRIVATE:
            return None, None
        identity = self._base_identity_id(context, at) if include_character else None
        if identity is not None:
            declaration = self.port.role_image_route(context, identity)
            if declaration is not None:
                return declaration, identity
        declaration = self.port.role_image_route(context, None)
        return (declaration, None) if declaration is not None else (None, None)

    def available(self, context, at, *, include_character=False):
        declaration, _ = self._route(context, at, include_character=include_character)
        if declaration is None:
            return False
        if self.quota is not None:
            try:
                self.quota.admit_proactive(context, at)
            except TaskError:
                return False
        return True

    @outcome
    def validate(self, spec, at):
        self.app.require_running()
        if self.closed:
            raise TaskError("qzone_life_image_closed", "life image generator is closed")
        if spec.task_id not in self._pending:
            raise TaskError("qzone_life_image_unavailable", "life image request is no longer active")
        return True

    async def generate(self, context, prompt, *, operation_key, include_character=False):
        cleaned = " ".join(str(prompt or "").split())[:MAX_PROMPT_CHARS]
        if not cleaned:
            return None
        at = self.app.now() if callable(getattr(self.app, "now", None)) else self.app.tasks.now()
        declaration, identity = self._route(context, at, include_character=include_character)
        if declaration is None:
            return None
        if self.quota is not None:
            try:
                self.quota.admit_proactive(context, at)
            except TaskError:
                return None
        task_id = "qzone-life-image-" + digest([context.key, operation_key])
        invocation_id = "qzone-life-img-" + digest([context.key, operation_key])
        if self.runtime.read_one("SELECT 1 FROM execution_tasks WHERE task_id=?", (task_id,)) is not None:
            return self._results.get(task_id)
        self._pending[task_id] = (cleaned, identity)
        try:
            with self.runtime.transaction():
                require(
                    self.app.tasks.submit(
                        TaskSpec(
                            task_id,
                            context,
                            KIND,
                            invocation_id,
                            operation_key,
                            at,
                            at + timedelta(seconds=TIMEOUT),
                        )
                    )
                )
            run_outcome = await self.runner.run(context, task_id)
            if not run_outcome.is_success:
                return None
            return self._results.pop(task_id, None)
        except Exception as error:  # noqa: BLE001 - image generation degrades cleanly to text-only
            logger.warning("[CompanionV2] 生活说说配图生成失败 error_type=%s", type(error).__name__)
            return None
        finally:
            self._pending.pop(task_id, None)
            self._results.pop(task_id, None)

    async def _generate(self, run):
        spec = run.task.spec
        context = spec.context
        at = self.app.now() if callable(getattr(self.app, "now", None)) else self.app.tasks.now()
        prompt, identity = self._pending[spec.task_id]
        if self.quota is not None:
            self.quota.admit_proactive(context, at)
        result = await self.port.generate_role_image(
            run,
            prompt,
            identity,
            invocation_id=spec.request_ref,
            call_id=CALL_ID,
        )
        if result.state != "succeeded" or not result.images:
            if result.effect == "not_committed" and result.receipt_ref:
                return TaskResult("failed", effect="not_committed", receipt_ref=result.receipt_ref)
            return TaskResult("uncertain", effect="unknown", receipt_ref=result.receipt_ref)
        captured = result.images[0]
        image = PublishImage.read(captured.content, mime_type=captured.descriptor.mime_type)
        with self.runtime.transaction():
            run.validate()
            run.record_operation(result.operation_id)
            self._results[spec.task_id] = image
        return TaskResult(
            "succeeded",
            effect="committed",
            result_ref=spec.request_ref,
            operation_id=result.operation_id,
            receipt_ref=result.receipt_ref,
        )

    @property
    def active_handles(self):
        return self.runner.active_handles if self.runner is not None else ()

    def cancel_running(self):
        self.closed = True

    async def close(self):
        self.cancel_running()
        if self.runner is not None:
            await self.runner.close()


__all__ = ["CALL_ID", "KIND", "QzoneLifeImageGenerator"]
