"""Album projection of images accepted through AstrBot's native tool ledger."""

import json
import sqlite3
from dataclasses import replace

from companion_v2.capabilities.native import SCHEMA as NATIVE_SCHEMA
from companion_v2.execution.models import ProviderBinding, SideEffect, TaskError, TaskSpec, digest, encode, require
from companion_v2.conversation.media.models import CapturedImage, ImageDescriptor
from companion_v2.conversation.messages import source_from_document
from companion_v2.conversation.native_tool_results import PREFIX, operation_row
from companion_v2.sources.models import GovernanceState
from companion_v2.storage.image_payloads import load_image


def _operation_or_none(runtime, invocation_id):
    try:
        return operation_row(runtime, invocation_id)
    except (TaskError, TypeError, ValueError, KeyError, UnicodeError, sqlite3.Error):
        return None


def _active(owner, table, *, id_value, lineage_value, revision_value,
            id_column, lineage_column, revision_column):
    row = owner.messages.runtime.read_one(
        f"SELECT state FROM {table} WHERE {id_column}=? AND {lineage_column}=? AND {revision_column}=?",
        (id_value, lineage_value, revision_value))
    return row is not None and row["state"] == "active"


def _payload_images(owner, row):
    """Read the stored original, not the provider invocation that produced it.

    The album must survive a provider upgrade: the image bytes and their
    source/asset receipts do not change when the native image package version
    changes.  The current operator was already authorized by the album manager;
    this read only rechecks that the stored source and asset are still active.
    """
    raw = row.get("result_source_json") if isinstance(row, dict) else None
    if not raw:
        return ()
    try:
        parent = source_from_document(json.loads(raw))
    except (TypeError, ValueError, KeyError):
        return ()
    if not _active(owner, "sources", id_value=parent.source_id,
                   lineage_value=parent.version.lineage_id, revision_value=parent.version.revision,
                   id_column="source_id", lineage_column="lineage_id", revision_column="source_revision"):
        return ()
    images = []
    for document in row.get("images") or ():
        try:
            descriptor = ImageDescriptor(**document)
        except TypeError:
            continue
        record = owner.messages.runtime.read_one(
            """SELECT * FROM conversation_image_payloads
               WHERE parent_id=? AND parent_lineage=? AND parent_revision=? AND position=?""",
            (parent.source_id, parent.version.lineage_id, parent.version.revision, descriptor.position))
        if (record is None or record["descriptor_json"] != encode(descriptor)
                or not _active(owner, "sources", id_value=record["source_id"],
                               lineage_value=record["source_lineage"], revision_value=record["source_revision"],
                               id_column="source_id", lineage_column="lineage_id",
                               revision_column="source_revision")
                or not _active(owner, "assets", id_value=record["asset_id"],
                               lineage_value=record["asset_lineage"], revision_value=record["asset_revision"],
                               id_column="asset_id", lineage_column="lineage_id",
                               revision_column="asset_revision")):
            continue
        images.append(CapturedImage(descriptor, load_image(owner.runtime.data_root, record["content"])))
    return tuple(images)


def _origin_keys(owner, context):
    """同一角色的旧 origin key 也算图库内容。

    ``context.key`` 把身份绑定和角色绑定 revision 都算进去：保存一次角色档案、
    重新确认映射或换一轮会话都会得到新的 key。图库跟随角色，私聊/群聊窗口
    只作为分类，不再用当前窗口过滤历史图。
    """
    scope = context.role_binding.scope
    role = context.role_binding.role
    rows = ()
    try:
        rows = owner.runtime.read_all(
            "SELECT DISTINCT context_key, context_json FROM conversation_native_turns", ())
    except sqlite3.OperationalError:
        # Older fixtures/databases may not have the native-turn ledger yet.
        # The current key is still a valid album projection in that case.
        rows = ()
    keys = []
    for row in rows:
        try:
            data = json.loads(row["context_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if type(data) is not dict:
            continue
        binding = data.get("role_binding")
        if type(binding) is not dict:
            continue
        candidate_role = binding.get("role")
        candidate_scope = binding.get("scope")
        if type(candidate_role) is not dict or type(candidate_scope) is not dict:
            continue
        if candidate_role.get("subject_id") != role.subject_id:
            continue
        if (candidate_scope.get("installation_id"), candidate_scope.get("platform"),
                candidate_scope.get("kind"), candidate_scope.get("account_id"),
                candidate_scope.get("bot_id")) != (
                    scope.installation_id, scope.platform, scope.kind.value,
                    scope.account_id, scope.bot_id):
            continue
        key = row["context_key"]
        if key and key not in keys:
            keys.append(key)
    if context.key not in keys:
        keys.append(context.key)
    return tuple(keys)


def _selection(owner, context):
    from .generated import IMAGE_ACTIONS
    keys = _origin_keys(owner, context)
    return ("""FROM runtime_metadata m JOIN execution_tasks t ON t.task_id = json_extract(m.value, '$.task_id')
        WHERE substr(m.key, 1, ?) = ? AND json_extract(m.value, '$.origin_key') IN
            (SELECT value FROM json_each(?))
        AND json_extract(m.value, '$.action') IN (?,?,?)""",
            (len(PREFIX), PREFIX, encode(keys), *sorted(IMAGE_ACTIONS)))


def _album_descriptors(external, row):
    """Restore the original declaration while authorizing the current provider."""
    from companion_v2.conversation.external_tools import ExtensionToolDescriptor
    from companion_v2.model_gateway.models import ToolSpec

    provider = external.providers.get(row["action"])
    if provider is None:
        raise TaskError("tool_owner_unavailable", "the image provider is unavailable")
    current = provider.descriptor
    saved = json.loads(row["descriptor_json"])
    package_data = saved["package"]
    package = require(external.capabilities.descriptor(
        extension_id=package_data["extension_id"], package_id=package_data["package_id"],
        package_version=package_data["package_version"]))
    if json.loads(encode(package)) != package_data:
        raise TaskError("tool_owner_unavailable", "the original image package differs from its catalog")
    original = ExtensionToolDescriptor(
        tool=ToolSpec(**saved["tool"]), package=package, binding=ProviderBinding(**saved["binding"]),
        source_owner=saved["source_owner"], side_effect=SideEffect(saved["side_effect"]),
        timeout_seconds=saved["timeout_seconds"])
    if (encode(original) != row["descriptor_json"] or encode(original.binding) != row["provider_json"]
            or original.tool.action != row["action"]
            or (original.package.extension_id, original.package.package_id, original.package.publisher_id,
                original.binding.provider, original.binding.capability, original.source_owner) != (
                current.package.extension_id, current.package.package_id, current.package.publisher_id,
                current.binding.provider, current.binding.capability, current.source_owner)):
        raise TaskError("tool_owner_unavailable", "the original image provider identity changed")
    return original, current


def _trusted_native_receipt(owner, context, at, row):
    """Return the governed source receipt for one settled native operation.

    The provider's ``imgart_*`` identifier is only a transport handle.  The
    source published by the native result handler is the durable receipt that
    binds that handle to the operation and to the adopted image manifest.
    """
    raw = row.get("result_source_json") if isinstance(row, dict) else None
    if not raw:
        return None
    try:
        parent = source_from_document(json.loads(raw))
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None
    try:
        # Keep the album projection behind the same execution-ledger checks as
        # ``native_tool_results.snapshot``.  A source receipt alone is not
        # sufficient: the task, invocation, and accepted result must still be
        # the exact operation that produced it.
        current = _operation_or_none(owner.runtime, row["invocation_id"])
        if current is None or current != {key: value for key, value in row.items() if key != "scan_id"}:
            return None
        if current.get("state") != "succeeded" or current.get("action") not in {
                "image.generate", "image.edit", "image.mask_edit"}:
            return None
        if current["origin_key"] not in _origin_keys(owner, context):
            return None
        external = owner._external()
        original_descriptor, current_descriptor = _album_descriptors(external, current)
        external.admit(current_descriptor, context, at)
        require(external.tasks.check_context_access(context, action="conversation.reply", observed_at=at))
        task = owner.runtime.read_one(
            "SELECT * FROM execution_tasks WHERE task_id = ? AND origin_key = ?",
            (current["task_id"], current["origin_key"]),
        )
        if task is None or task["origin_key"] != current["origin_key"]:
            return None
        data = json.loads(task["record_json"])
        definition = replace(original_descriptor.definition(), kind=PREFIX + digest(original_descriptor),
                             owner="astrbot.core-tool")
        if data.get("definition") != json.loads(encode(definition)):
            return None
        spec = TaskSpec.restore(data["spec"])
        if (spec.task_id, spec.kind, spec.request_ref, spec.context.key) != (
                current["task_id"], definition.kind, current["invocation_id"], current["origin_key"]):
            return None
        if task["state"] != "succeeded" or (
                data.get("effect"), data.get("operation_id"), data.get("result_ref"),
                data.get("receipt_ref")) != (
                    current["effect"], current["operation_id"], current["invocation_id"],
                    current["receipt_ref"]):
            return None
        from companion_v2.conversation.external_tools import _SourceAccess
        source = require(owner.messages.sources.read(
            parent, resolved=context.actor, capability=current_descriptor.binding.capability,
            observed_at=at, authorizer=_SourceAccess(external, current_descriptor, context)))
        if (source.reference != parent or source.reference.owner != original_descriptor.source_owner
                or source.reference.scope != context.scope
                or source.audience != context.audience
                or source.state is not GovernanceState.ACTIVE
                or digest(source) != current["result_hash"]
                or source.content_hash != digest(source.payload)):
            return None
        payload = source.payload
        document = json.loads(payload)
        native = document.get("native") if type(document) is dict else None
        if type(native) is not dict:
            return None
        if (native.get("schema") != NATIVE_SCHEMA
                or native.get("operation_id") != row.get("operation_id")
                or native.get("receipt_ref") != row.get("receipt_ref")
                or encode(native.get("binding")) != row.get("provider_json")):
            return None
        result = native.get("result")
        artifacts = native.get("artifacts")
        if type(result) is not dict or type(artifacts) is not list:
            return None
        required = {"artifact_id", "operation_id", "content_hash", "mime_type", "size_bytes"}
        if any(type(item) is not dict or set(item) != required for item in artifacts):
            return None
        if (result.get("operation_id") != row.get("operation_id")
                or result.get("original_invocation_id") != row.get("invocation_id")
                or result.get("operation") != row.get("action", "").removeprefix("image.")
                or result.get("artifact_ids") != [item["artifact_id"] for item in artifacts]):
            return None
        if (len({item["artifact_id"] for item in artifacts}) != len(artifacts)
                or any(item["operation_id"] != row.get("operation_id") for item in artifacts)):
            return None
        return source.reference, artifacts
    except (OSError, TaskError, TypeError, ValueError, KeyError, UnicodeError, sqlite3.Error):
        return None


def _validated_native_images(owner, context, at, row):
    receipt = _trusted_native_receipt(owner, context, at, row)
    if receipt is None:
        return None
    parent, artifacts = receipt
    try:
        captured = _payload_images(owner, row)
    except (OSError, TypeError, ValueError, KeyError, AttributeError, sqlite3.Error, TaskError):
        return None
    if (len(captured) != len(artifacts)
            or [item.descriptor.position for item in captured] != list(range(len(artifacts)))):
        return None
    for image, metadata in zip(captured, artifacts):
        descriptor = image.descriptor
        if (metadata["operation_id"] != row.get("operation_id")
                or metadata["content_hash"] != descriptor.content_hash
                or metadata["mime_type"] != descriptor.mime_type
                or metadata["size_bytes"] != descriptor.byte_count):
            return None
    return parent, captured, artifacts


def artifact(owner, context, at, artifact_id):
    """Resolve a native ``imgart_*`` handle through its governed album entry."""
    from .generated import GeneratedAlbumImage, IMAGE_ACTIONS

    if (type(artifact_id) is not str or not artifact_id.startswith("imgart_")
            or not 8 <= len(artifact_id) <= 180):
        raise TaskError("generated_image_missing", "the selected album image is unavailable")
    keys = _origin_keys(owner, context)
    actions = tuple(sorted(IMAGE_ACTIONS))
    query = (
        """WITH scoped_sources AS (
            SELECT json_extract(m.value, '$.invocation_id') AS invocation_id,
                CASE WHEN json_valid(s.payload_json) THEN json_extract(s.payload_json, '$') ELSE '' END AS payload
            FROM runtime_metadata m
            JOIN execution_tasks t ON t.task_id = json_extract(m.value, '$.task_id')
            JOIN sources s
              ON s.source_id = json_extract(m.value, '$.source.source_id')
             AND s.lineage_id = json_extract(m.value, '$.source.version.lineage_id')
             AND s.source_revision = json_extract(m.value, '$.source.version.revision')
            WHERE substr(m.key, 1, ?) = ?
              AND json_extract(m.value, '$.origin_key') IN (SELECT value FROM json_each(?))
        """
        + " AND json_extract(m.value, '$.action') IN (" + ",".join("?" for _ in actions) + ")"
        + """ AND json_extract(m.value, '$.state') = 'succeeded'
              AND json_extract(m.value, '$.task_id') IS NOT NULL
              AND s.state = 'active'
        ), candidate_artifacts AS (
            SELECT invocation_id,
                CASE WHEN json_valid(payload) THEN
                    CASE WHEN json_type(payload, '$.native.artifacts') = 'array'
                         THEN json_extract(payload, '$.native.artifacts') ELSE '[]' END
                    ELSE '[]' END AS artifacts
            FROM scoped_sources
        )
        SELECT DISTINCT candidate_artifacts.invocation_id
        FROM candidate_artifacts, json_each(candidate_artifacts.artifacts) AS artifact
        WHERE CASE WHEN artifact.type = 'object'
                   THEN json_extract(artifact.value, '$.artifact_id') = ?
                   ELSE 0 END
        ORDER BY candidate_artifacts.invocation_id""")
    rows = owner.runtime.read_all(query, (len(PREFIX), PREFIX, encode(keys), *actions, artifact_id))
    resolved = None
    for selected in rows:
        row = _operation_or_none(owner.runtime, selected["invocation_id"])
        if (row is None or row.get("state") != "succeeded"
                or row.get("action") not in IMAGE_ACTIONS):
            continue
        validated = _validated_native_images(owner, context, at, row)
        if validated is None:
            continue
        parent, captured, artifacts = validated
        matching = [index for index, item in enumerate(artifacts)
                    if item["artifact_id"] == artifact_id]
        if len(matching) != 1:
            continue
        index = matching[0]
        if resolved is not None:
            raise TaskError("generated_image_missing", "the selected album image is unavailable")
        resolved = GeneratedAlbumImage(
            "generated-image:" + row["invocation_id"] + ":" + str(index),
            row["created_at"], row["action"], captured[index].descriptor,
            captured[index].content, parent=parent)
    if resolved is not None:
        return resolved
    raise TaskError("generated_image_missing", "the selected album image is unavailable")


def page(owner, context, at, *, before=None, limit=24):
    from .generated import GeneratedAlbumImage
    items = []
    scan_before = before
    first_scan = True
    while True:
        selection, values = _selection(owner, context)
        selection += " AND json_extract(m.value, '$.state') = 'succeeded'"
        if scan_before is not None:
            operator = "<=" if first_scan else "<"
            selection += (" AND (t.created_at < ? OR (t.created_at = ? AND "
                          "json_extract(m.value, '$.invocation_id') " + operator + " ?))")
            values += (scan_before["created_at"], scan_before["created_at"], scan_before["invocation_id"])
        rows = owner.runtime.read_all("SELECT json_extract(m.value, '$.invocation_id') AS invocation_id, t.created_at " + selection
            + " ORDER BY t.created_at DESC, invocation_id DESC LIMIT 128", values)
        if not rows:
            break
        for selected in rows:
            row = _operation_or_none(owner.runtime, selected["invocation_id"])
            validated = _validated_native_images(owner, context, at, row)
            if validated is None:
                continue
            parent, captured, _ = validated
            for image in captured:
                if before is not None and (row["created_at"], row["invocation_id"], image.descriptor.position) >= (
                        before["created_at"], before["invocation_id"], before["position"]):
                    continue
                items.append(GeneratedAlbumImage(
                    "generated-image:" + row["invocation_id"] + ":" + str(image.descriptor.position),
                    row["created_at"], row["action"], image.descriptor, image.content, parent=parent))
            if len(items) > limit:
                return items, True
        if len(rows) < 128:
            break
        scan_before = {"created_at": rows[-1]["created_at"],
                       "invocation_id": rows[-1]["invocation_id"]}
        first_scan = False
    return items, False


def image(owner, context, at, invocation_id, position):
    from .generated import GeneratedAlbumImage, IMAGE_ACTIONS
    row = _operation_or_none(owner.runtime, invocation_id)
    if (row is None or row.get("state") != "succeeded"
            or row["origin_key"] not in _origin_keys(owner, context)
            or row["action"] not in IMAGE_ACTIONS):
        raise TaskError("generated_image_missing", "the selected album image is unavailable")
    validated = _validated_native_images(owner, context, at, row)
    if validated is None:
        raise TaskError("generated_image_missing", "the selected album image is unavailable")
    parent, images, _ = validated
    for image in images:
        if image.descriptor.position == position:
            return GeneratedAlbumImage("generated-image:" + invocation_id + ":" + str(position),
                row["created_at"], row["action"], image.descriptor, image.content, parent=parent)
    raise TaskError("generated_image_missing", "the selected album image is unavailable")


def diagnostics(owner, context, *, limit):
    selection, values = _selection(owner, context)
    count = owner.runtime.read_one("SELECT COUNT(*) AS total " + selection, values)
    rows = owner.runtime.read_all("SELECT json_extract(m.value, '$.invocation_id') AS invocation_id " + selection
        + " ORDER BY t.created_at DESC, invocation_id DESC LIMIT ?", (*values, limit))
    return int(count["total"]), [operation for row in rows
        if (operation := _operation_or_none(owner.runtime, row["invocation_id"])) is not None]
