"""Durable native tool images for the host's own image cache and send tools."""

import base64
import json
from types import SimpleNamespace

from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.sources.dependencies import key, link
from companion_v2.sources.models import AssetRecord, SourceRecord
from companion_v2.storage.image_payloads import load_image, store_image

from .media.models import CapturedImage, ImageDescriptor


def _view(context, source, action):
    return SimpleNamespace(source=source, scope=context.scope, principal=context.actor.principal,
        image_attribution={"attribution": "provider_output", "action": action,
                           "original_author": "provider", "capture_time": "unknown"})


def publish_native_images(messages, context, source, action, images, *, at):
    """Reuse governed image/asset storage without inventing a conversation batch."""
    view = _view(context, source, action)
    descriptors = []
    with messages.runtime.transaction() as connection:
        for image in images:
            if type(image) is not CapturedImage or type(image.descriptor) is not ImageDescriptor:
                raise TaskError("native_image_unsupported", "native tools return static image originals")
            reference, asset = messages.images.references(view, image.descriptor)
            payload = messages.images.metadata(view, image.descriptor)
            require(messages.sources.publish(SourceRecord(reference, context.audience, digest(payload), payload),
                                             observed_at=at))
            link(messages.runtime, reference, (source,), at)
            require(messages.images.assets.publish(AssetRecord(asset, context.audience), observed_at=at))
            stored_content = store_image(messages.runtime.data_root, image.content,
                                         mime_type=image.descriptor.mime_type)
            values = (*key(source), image.descriptor.position, *key(reference), asset.asset_id,
                      asset.version.lineage_id, asset.version.revision, encode(image.descriptor), stored_content)
            prior = connection.execute("""SELECT * FROM conversation_image_payloads
                WHERE parent_id=? AND parent_lineage=? AND parent_revision=? AND position=?""", values[:4]).fetchone()
            if prior is not None:
                prior_values = tuple(prior)
                try:
                    same_content = load_image(messages.runtime.data_root, prior_values[-1]) == image.content
                except (OSError, TypeError, ValueError):
                    same_content = False
                if prior_values[:-1] != values[:-1] or not same_content:
                    raise TaskError("native_image_changed", "the original native tool image cannot be replaced")
            connection.execute("INSERT OR IGNORE INTO conversation_image_payloads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               values)
            descriptors.append(json.loads(encode(image.descriptor)))
    return descriptors


def read_native_images(messages, context, source, action, descriptors, *, capability, authorizer, at):
    view = _view(context, source, action)
    images = []
    for document in descriptors:
        descriptor = ImageDescriptor(**document)
        reference, asset = messages.images.references(view, descriptor)
        record = require(messages.sources.read(reference, resolved=context.actor, capability=capability,
            observed_at=at, authorizer=authorizer))
        if record.payload != messages.images.metadata(view, descriptor):
            raise TaskError("native_image_changed", "the native image manifest changed")
        owned = require(messages.images.assets.read(asset, resolved=context.actor, capability=capability,
            observed_at=at, authorizer=authorizer))
        if not set(context.audience.recipients).issubset(owned.audience.recipients):
            raise TaskError("native_image_audience", "the native image no longer belongs to this audience")
        row = messages.runtime.read_one("""SELECT * FROM conversation_image_payloads
            WHERE parent_id=? AND parent_lineage=? AND parent_revision=? AND position=?""",
            (*key(source), descriptor.position))
        if (row is None or (row["source_id"], row["source_lineage"], row["source_revision"]) != key(reference)
                or row["descriptor_json"] != encode(descriptor)
                or (row["asset_id"], row["asset_lineage"], row["asset_revision"]) !=
                (asset.asset_id, asset.version.lineage_id, asset.version.revision)):
            raise TaskError("native_image_missing", "the original native tool image is unavailable")
        try:
            content = load_image(messages.runtime.data_root, row["content"])
        except (OSError, TypeError, ValueError):
            raise TaskError("native_image_missing", "the original native tool image is unavailable") from None
        images.append(CapturedImage(descriptor, content))
    return tuple(images)


def native_image_data(image):
    return base64.b64encode(image.content).decode("ascii")
