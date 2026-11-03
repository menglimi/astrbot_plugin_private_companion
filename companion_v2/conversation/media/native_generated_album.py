"""Album projection of images accepted through AstrBot's native tool ledger."""

import json
import sqlite3

from companion_v2.execution.models import TaskContext, TaskError
from companion_v2.conversation.messages import source_from_document
from companion_v2.conversation.native_tool_results import PREFIX, operation_row
from companion_v2.conversation.native_tool_media import read_native_images


def _original_context(row, fallback):
    """Use the image's own conversation snapshot for historical album keys.

    ``_origin_keys`` deliberately includes older keys from the same role and
    window, but the native receipt still checks ``origin_key == context.key``.
    Reading with the current key would silently drop every image created before
    the last profile save or session rotation.
    """
    raw = row.get("context_json") if isinstance(row, dict) else None
    if not raw:
        return fallback
    try:
        return TaskContext.restore(json.loads(raw))
    except (TypeError, ValueError, KeyError):
        return fallback


def images(owner, row, context, at):
    external = owner.messages.tools.external
    result, material = external.snapshot(row, context, at)
    if result["state"] != "succeeded" or material is None:
        return ()
    from companion_v2.conversation.external_tools import _SourceAccess
    descriptor = external._descriptor(row)
    return read_native_images(owner.messages, context, material.source, row["action"], row.get("images", []),
        capability=descriptor.binding.capability, authorizer=_SourceAccess(external, descriptor, context), at=at)


def _origin_keys(owner, context):
    """同一角色/窗口的旧 origin key 也算图库内容。

    ``context.key`` 把身份绑定和角色绑定 revision 都算进去：保存一次角色档案、
    重新确认映射或换一轮会话都会得到新的 key。图库如果只认当前 key，之前
    生成的图片会整体消失。这里按“角色主体 + 窗口作用域”聚合历史 key。
    """
    scope = context.role_binding.scope
    role = context.role_binding.role
    rows = owner.runtime.read_all(
        "SELECT DISTINCT context_key, context_json FROM conversation_native_turns", ())
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
                candidate_scope.get("kind"), candidate_scope.get("native_scope_id")) != (
                scope.platform, scope.account_id, scope.kind.value, scope.native_scope_id):
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
        try:
            captured = images(owner, row, _original_context(row, context), at)
        except TaskError:
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
    for captured in images(owner, row, _original_context(row, context), at):
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
