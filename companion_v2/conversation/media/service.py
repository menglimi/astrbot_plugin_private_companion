"""Publish image originals atomically with intake; re-read at every use boundary."""

import base64

from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.foundation.refs import AssetRef, SourceRef, VersionRef
from companion_v2.sources.dependencies import key, link
from companion_v2.sources.models import AssetRecord, SourceRecord
from companion_v2.sources.repositories import AssetRepository
from companion_v2.conversation.models import InboundConversationEvent
from companion_v2.storage.image_payloads import load_image

from .models import CapturedImage, OWNER, PromptImage, fits_budget
from .animation import AnimationDescriptor, AnimationFrameEvent, CapturedAnimation, PromptAnimation


class ConversationImages:
    def __init__(self, messages):
        self.messages, self.runtime = messages, messages.runtime
        self.assets = AssetRepository(self.runtime)

    @staticmethod
    def references(event, descriptor):
        identifier = "input-image-" + digest([event.source, descriptor.position])
        version = VersionRef(identifier, 1)
        return (SourceRef(identifier, OWNER, event.scope, version, event.source.issued_at, event.source.expires_at),
                AssetRef(identifier, OWNER, event.scope, version, descriptor.content_hash))

    @staticmethod
    def metadata(event, descriptor):
        metadata = {"image": descriptor, "parent_source_id": event.source.source_id,
                       "sender": event.principal.native_account_id,
                       "attribution": "sender_only", "original_author": "unknown", "capture_time": "unknown",
                       "coverage": "one_static_image", **getattr(event, "image_attribution", {})}
        if isinstance(descriptor, AnimationDescriptor):
            metadata.update(coverage=descriptor.coverage, timing="container_elapsed_ms",
                            playback="one_encoded_cycle", default_image_included=False)
        if callable(getattr(event, "frame_metadata", None)):
            metadata.update(event.frame_metadata(descriptor))
        return encode(metadata)

    def frame_event(self, event, descriptor):
        reference, _ = self.references(event, descriptor)
        return AnimationFrameEvent(event.event_id, event.principal, event.scope, reference,
            event.capability, digest(self.metadata(event, descriptor)), event.received_at, event.runtime_scope,
            tuple(f.image for f in descriptor.frames), descriptor, tuple(getattr(event, "image_attribution", {}).items()))

    def contributions(self, event, image, *, purpose):
        from companion_v2.conversation.foreground import ContextContribution, ContributionState
        result = [ContextContribution(image.source.source_id, image.source.owner,
            purpose + image.parent.source_id + ":" + str(image.descriptor.position), ContributionState.READY,
            self.metadata(event, image.descriptor), image.source, True)]
        if isinstance(image, PromptAnimation):
            parent = self.frame_event(event, image.descriptor)
            for frame in image.frames:
                result.extend(self.contributions(parent, frame, purpose=purpose + "frame:"))
        return tuple(result)

    def publish(self, context, event, images):
        if (not isinstance(event, InboundConversationEvent) or not getattr(event, "images", ()) or not isinstance(images, tuple)
                or any(not isinstance(i, CapturedImage) for i in images)
                or tuple(i.descriptor for i in images) != event.images):
            raise TaskError("image_manifest_invalid", "publish only the original host-captured image manifest")
        with self.runtime.transaction() as connection:
            self.messages.authorize(context)
            parent = self.messages._read_source(event.source, context, event.capability, self.messages.tasks.now())
            if event.principal != context.actor.principal or parent.content_hash != event.input_hash:
                raise TaskError("image_owner_mismatch", "images must belong to the admitted original message")
            for image in images:
                if isinstance(image.descriptor, AnimationDescriptor) and not isinstance(image, CapturedAnimation):
                    raise TaskError("animation_content_changed", "animation capture is missing its rendered frames")
                reference, asset = self.references(event, image.descriptor)
                payload = self.metadata(event, image.descriptor)
                require(self.messages.sources.publish(SourceRecord(reference, context.audience, digest(payload), payload),
                                                      observed_at=event.received_at))
                link(self.runtime, reference, (event.source,), event.received_at)
                require(self.assets.publish(AssetRecord(asset, context.audience), observed_at=event.received_at))
                values = (*key(event.source), image.descriptor.position, *key(reference),
                          asset.asset_id, asset.version.lineage_id, asset.version.revision,
                          encode(image.descriptor), image.content)
                old = connection.execute("""SELECT * FROM conversation_image_payloads
                    WHERE parent_id = ? AND parent_lineage = ? AND parent_revision = ? AND position = ?""",
                    values[:4]).fetchone()
                if old is not None and tuple(old) != values:
                    raise TaskError("image_version_conflict", "the original attachment cannot be replaced")
                connection.execute("INSERT OR IGNORE INTO conversation_image_payloads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
                if isinstance(image, CapturedAnimation):
                    self.publish(context, self.frame_event(event, image.descriptor), image.frames)

    def read(self, context, events, at):
        manifest = [(event, descriptor) for event in events for descriptor in getattr(event, "images", ())]
        if not fits_budget(tuple(d for _, d in manifest)):
            raise TaskError("image_turn_limit", "this turn exceeds the declared image budget")
        result = []
        with self.runtime.read_snapshot():
            self.messages.authorize(context, at)
            for event, descriptor in manifest:
                parent = self.messages._read_source(event.source, context, event.capability, at)
                if event.principal != context.actor.principal or parent.content_hash != event.input_hash:
                    raise TaskError("image_owner_mismatch", "images must retain their original sender and source")
                reference, asset = self.references(event, descriptor)
                record = self.messages._read_source(reference, context, event.capability, at)
                payload = self.metadata(event, descriptor)
                if record.payload != payload or record.content_hash != digest(payload):
                    raise TaskError("image_content_changed", "image metadata differs from the original manifest")
                asset_record = require(self.assets.read(asset, resolved=context.actor, capability=event.capability,
                                                       observed_at=at, authorizer=self.messages.tasks.identity))
                if not set(context.audience.recipients).issubset(asset_record.audience.recipients):
                    raise TaskError("image_audience_denied", "image asset is unavailable to the original audience")
                row = self.runtime.read_one("""SELECT * FROM conversation_image_payloads
                    WHERE parent_id = ? AND parent_lineage = ? AND parent_revision = ? AND position = ?""",
                    (*key(event.source), descriptor.position))
                if row is None:
                    raise TaskError("image_original_unavailable", "original image bytes are unavailable")
                if ((row["source_id"], row["source_lineage"], row["source_revision"]) != key(reference)
                        or (row["asset_id"], row["asset_lineage"], row["asset_revision"]) != (
                            asset.asset_id, asset.version.lineage_id, asset.version.revision)
                        or row["descriptor_json"] != encode(descriptor)):
                    raise TaskError("image_content_changed", "image body is bound to another original")
                captured = CapturedImage(descriptor, load_image(self.runtime.data_root, row["content"]))
                data = base64.b64encode(captured.content).decode("ascii")
                if isinstance(descriptor, AnimationDescriptor):
                    frames = self.read(context, (self.frame_event(event, descriptor),), at)
                    result.append(PromptAnimation(reference, event.source, descriptor, data, frames))
                else:
                    result.append(PromptImage(reference, event.source, descriptor, data))
        return tuple(result)
