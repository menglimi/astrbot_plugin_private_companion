"""Generated image originals for a single authorized proactive contact."""

import asyncio
import base64

from companion_v2.conversation.media.models import ImageDescriptor, PromptImage
from companion_v2.conversation.media.output_settings import effective_settings
from companion_v2.conversation.messages import source_from_document
from companion_v2.delivery.media import image_manifest
from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.refs import SourceRef, VersionRef
from companion_v2.storage.image_payloads import load_image, store_image


DIAGNOSTIC_PROMPT = "一张轻松的日常静物插画，靠窗的木桌上有一杯热茶和一本打开的书，柔和的自然光，画面中不出现文字。"


class ProactiveImages:
    def __init__(self, runtime):
        self.runtime = runtime
        self.port = self.quota = None

    def check(self, context, at, request_hash, *, diagnostic=False):
        settings = effective_settings(self.runtime, context)
        probability, limit = settings["proactive_probability"], settings["proactive_max_daily"]
        if not diagnostic:
            if not settings["proactive_enabled"]:
                raise TaskError("proactive_image_disabled", "automatic message images are disabled")
            sample = int(digest([request_hash, "proactive-generated-image"])[:16], 16) / 2**64
            if sample >= probability:
                raise TaskError("proactive_image_probability", "this contact did not pass the image probability")
        if self.port is None or self.quota is None or not self.port.role_image_route(context):
            raise TaskError("proactive_image_route_unavailable", "no authorized image generation route")
        self.quota.admit_proactive_message(context, at, limit)
        return limit

    def available(self, context, at, request_hash, *, diagnostic=False):
        try:
            self.check(context, at, request_hash, diagnostic=diagnostic)
            return True
        except TaskError:
            if diagnostic:
                raise
            return False

    async def generate(self, run, contact_id, prompt, request_hash, *, diagnostic=False):
        context, at = run.task.spec.context, run.supervisor.now()
        reservation = None
        try:
            limit = self.check(context, at, request_hash, diagnostic=diagnostic)

            def guard():
                nonlocal reservation
                run.validate()
                if reservation is None:
                    with self.runtime.transaction():
                        slot = self.quota.reserve_proactive_message(context, run.supervisor.now(), limit)
                        run.begin_effect()
                    reservation = slot

            result = await self.port.generate_role_image(
                run, prompt, None, invocation_id=contact_id, call_id="proactive-image", on_dispatch=guard)
            if result.state != "succeeded" or not result.images:
                if result.effect == "not_committed" and reservation is not None:
                    self.quota.release_proactive_message(reservation)
                code = ("proactive_image_result_unknown" if result.state == "unknown"
                        else result.error_code or "proactive_image_generation_failed")
                return {"state": "unknown" if result.state == "unknown" else "failed", "code": code}, None
            captured = result.images[0]
            marker = await asyncio.to_thread(store_image, self.runtime.data_root, captured.content,
                                             mime_type=captured.descriptor.mime_type)
            identifier = contact_id + "-generated-image"
            source = SourceRef(identifier, "conversation.image", context.scope, VersionRef(identifier, 1), at)
            image = PromptImage(source, result.source.reference, captured.descriptor,
                                base64.b64encode(captured.content).decode("ascii"))
            return {"state": "passed"}, {"manifest": image_manifest(image), "marker": marker.decode("ascii")}
        except TaskError as error:
            return {"state": "failed", "code": error.code}, None
        except (ValueError, OSError):
            return {"state": "failed", "code": "proactive_image_generation_failed"}, None

    def read(self, context, original):
        if self.port is None or not self.port.role_image_route(context):
            raise TaskError("proactive_image_route_unavailable", "the authorized generation route is unavailable")
        manifest = original["manifest"]
        try:
            content = load_image(self.runtime.data_root, original["marker"].encode("ascii"))
            return PromptImage(source_from_document(manifest["source"]), source_from_document(manifest["parent"]),
                               ImageDescriptor(**manifest["descriptor"]), base64.b64encode(content).decode("ascii"))
        except (ValueError, OSError, TypeError, KeyError) as error:
            raise TaskError("proactive_image_original_unavailable", "the sealed generated original changed") from error
