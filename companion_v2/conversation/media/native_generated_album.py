"""Album projection of images accepted through AstrBot's native tool ledger."""

import json
import sqlite3

from companion_v2.execution.models import TaskError, encode
from companion_v2.conversation.media.models import CapturedImage, ImageDescriptor
from companion_v2.conversation.messages import source_from_document
from companion_v2.conversation.native_tool_results import PREFIX, operation_row


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
        images.append(CapturedImage(descriptor, record["content"]))
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
        binding = data.get("role_binding") or {}
        candidate_role = binding.get("role") or {}
        candidate_scope = binding.get("scope") or {}
        if candidate_role.get("subject_id") != role.subject_id:
            continue
        if (candidate_scope.get("platform"), candidate_scope.get("account_id"),
                candidate_scope.get("bot_id")) != (scope.platform, scope.account_id, scope.bot_id):
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
    placeholders = ",".join("?" for _ in keys)
    return ("""FROM runtime_metadata m JOIN execution_tasks t ON t.task_id = json_extract(m.value, '$.task_id')
        WHERE substr(m.key, 1, ?) = ? AND json_extract(m.value, '$.origin_key') IN (""" + placeholders + """)
        AND json_extract(m.value, '$.action') IN (?,?,?)""",
            (len(PREFIX), PREFIX, *keys, *sorted(IMAGE_ACTIONS)))


def page(owner, context, at, *, before=None, limit=24):
    from .generated import GeneratedAlbumImage
    selection, values = _selection(owner, context)
    selection += " AND json_extract(m.value, '$.state') = 'succeeded'"
    if before is not None:
        selection += " AND (t.created_at < ? OR (t.created_at = ? AND json_extract(m.value, '$.invocation_id') <= ?))"
        values += (before["created_at"], before["created_at"], before["invocation_id"])
    rows = owner.runtime.read_all("SELECT json_extract(m.value, '$.invocation_id') AS invocation_id " + selection
        + " ORDER BY t.created_at DESC, invocation_id DESC LIMIT 128", values)
    items = []
    for selected in rows:
        row = operation_row(owner.runtime, selected["invocation_id"])
        captured = _payload_images(owner, row)
        if not captured:
            continue
        parent = source_from_document(json.loads(row["result_source_json"]))
        for image in captured:
            if before is not None and (row["created_at"], row["invocation_id"], image.descriptor.position) >= (
                    before["created_at"], before["invocation_id"], before["position"]):
                continue
            items.append(GeneratedAlbumImage("generated-image:" + row["invocation_id"] + ":" + str(image.descriptor.position),
                row["created_at"], row["action"], image.descriptor, image.content, parent=parent))
        if len(items) > limit:
            break
    return items, len(rows) == 128


def image(owner, context, at, invocation_id, position):
    from .generated import GeneratedAlbumImage, IMAGE_ACTIONS
    row = operation_row(owner.runtime, invocation_id)
    if row is None or row["origin_key"] not in _origin_keys(owner, context) \
            or row["action"] not in IMAGE_ACTIONS:
        raise TaskError("generated_image_missing", "the native album image is unavailable")
    for captured in _payload_images(owner, row):
        if captured.descriptor.position == position:
            parent = source_from_document(json.loads(row["result_source_json"]))
            return GeneratedAlbumImage("generated-image:" + invocation_id + ":" + str(position),
                row["created_at"], row["action"], captured.descriptor, captured.content, parent=parent)
    raise TaskError("generated_image_missing", "the native album image is unavailable")


def diagnostics(owner, context, *, limit):
    selection, values = _selection(owner, context)
    count = owner.runtime.read_one("SELECT COUNT(*) AS total " + selection, values)
    rows = owner.runtime.read_all("SELECT json_extract(m.value, '$.invocation_id') AS invocation_id " + selection
        + " ORDER BY t.created_at DESC, invocation_id DESC LIMIT ?", (*values, limit))
    return int(count["total"]), [operation_row(owner.runtime, row["invocation_id"]) for row in rows]
