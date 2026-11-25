"""Transactional store for role reference material.

One role's registrations are identified by a role-owned key, so a window looking at
the role sees the same wardrobe. The 24-entry ceiling is enforced when a
registration is *written*: an over-long list is refused here rather than being
silently truncated when something finally wants to use it.
"""

from __future__ import annotations

import json

from companion_v2.execution.models import TaskError, digest
from companion_v2.storage.reference_asset_schema import reference_limit
from companion_v2.storage.runtime import StorageRuntime

from .models import (MAX_ROLE_REFERENCE_BYTES, ContentDigest, ReferenceAsset,
                     ReferenceKind, ReferenceRole, ReferenceSource, ReferenceState,
                     WardrobeItem)
from .usage import default_usage, validate_usage


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _parse(value):
    from datetime import datetime
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _asset_from_row(row):
    return ReferenceAsset(
        asset_id=row["asset_id"], kind=ReferenceKind(row["kind"]), role=ReferenceRole(row["role"]),
        content_hash=ContentDigest(row["content_hash"]), size_bytes=row["size_bytes"],
        mime_type=row["mime_type"], media_ref=row["media_ref"],
        source=ReferenceSource(row["source"]), state=ReferenceState(row["state"]),
        label=row["label"], revision=row["revision"],
        created_at=_parse(row["created_at"]), updated_at=_parse(row["updated_at"]),
    )


def _item_from_row(row):
    tags = json.loads(row["tags_json"])
    return WardrobeItem(
        item_id=row["item_id"], name=row["name"], description=row["description"],
        tags=tuple(sorted(tags)), asset_id=row["asset_id"],
        source=ReferenceSource(row["source"]), state=ReferenceState(row["state"]),
        revision=row["revision"], created_at=_parse(row["created_at"]), updated_at=_parse(row["updated_at"]),
    )


class ReferenceAssetRepository:
    def __init__(self, runtime: StorageRuntime):
        self._runtime = runtime

    # --- reference assets --------------------------------------------------------

    def get(self, owner, asset_id):
        row = self._runtime.read_one(
            "SELECT * FROM reference_assets WHERE owner_key = ? AND asset_id = ?", (owner, asset_id))
        return _asset_from_row(row) if row else None

    def list(self, owner, *, kind=None, role=None, state=ReferenceState.ACTIVE, limit=None):
        """Registrations for one role, gallery order first and stable within ties.

        A role filter consults the declared-role rows, not just the primary column:
        a material that was granted a second role must be found by it.
        """
        ceiling = reference_limit() if limit is None else max(1, min(int(limit), reference_limit()))
        clauses = ["a.owner_key = ?"]
        parameters = [owner]
        if state is not None:
            clauses.append("a.state = ?")
            parameters.append(ReferenceState(state).value)
        if kind is not None:
            clauses.append("a.kind = ?")
            parameters.append(ReferenceKind(kind).value)
        if role is not None:
            clauses.append("EXISTS (SELECT 1 FROM reference_asset_roles r "
                           "WHERE r.owner_key = a.owner_key AND r.asset_id = a.asset_id AND r.role = ?)")
            parameters.append(ReferenceRole(role).value)
        rows = self._runtime.read_all(
            f"SELECT a.* FROM reference_assets a WHERE {' AND '.join(clauses)} "
            "ORDER BY a.position, a.created_at, a.asset_id LIMIT ?", (*parameters, ceiling))
        return tuple(_asset_from_row(row) for row in rows)

    def reorder_active(self, owner, *, expected_asset_ids, asset_ids):
        with self._runtime.transaction() as connection:
            rows = connection.execute(
                "SELECT asset_id FROM reference_assets WHERE owner_key = ? AND state = ? "
                "ORDER BY position, created_at, asset_id",
                (owner, ReferenceState.ACTIVE.value),
            ).fetchall()
            current = [row["asset_id"] for row in rows]
            if current != expected_asset_ids:
                raise TaskError("reference_order_conflict", "the reference gallery changed; reload before saving its order")
            if (len(asset_ids) != len(current) or len(set(asset_ids)) != len(asset_ids)
                    or set(asset_ids) != set(current)):
                raise TaskError("reference_order_invalid", "the saved order must contain every active reference exactly once")
            connection.executemany(
                "UPDATE reference_assets SET position = ? WHERE owner_key = ? AND asset_id = ? AND state = ?",
                [(position, owner, asset_id, ReferenceState.ACTIVE.value)
                 for position, asset_id in enumerate(asset_ids)],
            )
        return tuple(asset_ids)

    def count_active(self, owner):
        row = self._runtime.read_one(
            "SELECT COUNT(*) AS n FROM reference_assets WHERE owner_key = ? AND state = ?",
            (owner, ReferenceState.ACTIVE.value))
        return row["n"] if row else 0

    def put(self, owner, asset, *, created_at, payload=None, ignore_limit=False):
        """Register one material, refusing to grow past the declared ceiling."""
        if payload is not None and (not isinstance(payload, bytes) or not asset.verify(payload)):
            raise TaskError("reference_payload_mismatch", "stored reference bytes must match their registration")
        if not ignore_limit:
            existing = self.get(owner, asset.asset_id)
            if existing is None and self.count_active(owner) >= reference_limit():
                raise TaskError("reference_limit_reached",
                                f"this role already holds {reference_limit()} active registrations")
        roles = {asset.role}
        with self._runtime.transaction() as connection:
            existing = connection.execute(
                "SELECT position FROM reference_assets WHERE owner_key = ? AND asset_id = ?",
                (owner, asset.asset_id),
            ).fetchone()
            position = existing["position"] if existing else connection.execute(
                "SELECT COALESCE(MAX(position) + 1, 0) AS next_position FROM reference_assets WHERE owner_key = ?",
                (owner,),
            ).fetchone()["next_position"]
            connection.execute(
                """INSERT OR REPLACE INTO reference_assets
                   (owner_key, asset_id, kind, role, label, content_hash, size_bytes, mime_type,
                    media_ref, source, state, revision, created_at, updated_at, position)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (owner, asset.asset_id, asset.kind.value, asset.role.value, asset.label,
                 asset.content_hash.sha256, asset.size_bytes, asset.mime_type, asset.media_ref,
                 asset.source.value, asset.state.value, asset.revision,
                 (asset.created_at or created_at).isoformat(), created_at.isoformat(), position))
            # The declared role is also a row, so "which materials serve this slot"
            # is a query rather than a scan with an implicit assumption.
            connection.execute(
                "INSERT OR IGNORE INTO reference_asset_roles VALUES (?,?,?,?)",
                (owner, asset.asset_id, sorted(roles, key=lambda role: role.value)[0].value,
                 created_at.isoformat()))
            if payload is not None:
                previous = connection.execute(
                    "SELECT size_bytes FROM reference_asset_payloads WHERE owner_key = ? AND asset_id = ?",
                    (owner, asset.asset_id)).fetchone()
                stored = connection.execute(
                    "SELECT COALESCE(SUM(size_bytes), 0) AS n FROM reference_asset_payloads WHERE owner_key = ?",
                    (owner,)).fetchone()["n"]
                total = stored - (previous["size_bytes"] if previous else 0) + len(payload)
                if total > MAX_ROLE_REFERENCE_BYTES:
                    raise TaskError("reference_storage_limit", "this role's stored reference images are at capacity")
                connection.execute(
                    "INSERT OR REPLACE INTO reference_asset_payloads"
                    " (owner_key, asset_id, content_hash, size_bytes, mime_type, content, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (owner, asset.asset_id, asset.content_hash.sha256, len(payload), asset.mime_type,
                     payload, created_at.isoformat()))
        return self.get(owner, asset.asset_id)

    def get_payload(self, owner, asset_id):
        row = self._runtime.read_one(
            "SELECT content_hash, size_bytes, mime_type, content FROM reference_asset_payloads "
            "WHERE owner_key = ? AND asset_id = ?", (owner, asset_id))
        if row is None:
            return None
        payload = row["content"]
        if (not isinstance(payload, bytes) or len(payload) != row["size_bytes"]
                or ContentDigest(row["content_hash"]).matches(payload) is False):
            raise TaskError("reference_payload_changed", "stored reference bytes differ from their immutable receipt")
        return payload

    def usage_of(self, owner, asset_id):
        row = self._runtime.read_one(
            "SELECT usage_json FROM reference_asset_usage WHERE owner_key = ? AND asset_id = ?",
            (owner, asset_id))
        return validate_usage(json.loads(row["usage_json"])) if row else default_usage()

    def update_registration(self, owner, asset_id, *, label, roles, expected_revision, updated_at, usage=None):
        selected_roles = tuple(ReferenceRole(role) for role in roles)
        if not selected_roles or len(set(selected_roles)) != len(selected_roles):
            raise TaskError("reference_roles_invalid", "one or more distinct reference roles are required")
        selected_usage = validate_usage(usage) if usage is not None else None
        if (selected_usage and selected_usage["base_identity"]
                and ReferenceRole.IDENTITY not in selected_roles):
            raise TaskError("reference_usage_invalid", "a base identity reference requires the identity role")
        with self._runtime.transaction() as connection:
            row = connection.execute(
                "SELECT state, revision FROM reference_assets WHERE owner_key = ? AND asset_id = ?",
                (owner, asset_id)).fetchone()
            if row is None:
                raise TaskError("reference_asset_missing", "the reference material is not registered")
            if row["state"] != ReferenceState.ACTIVE.value:
                raise TaskError("reference_asset_withdrawn", "the reference material was withdrawn")
            if row["revision"] != expected_revision:
                raise TaskError("reference_revision_conflict", "the reference material changed; reload before editing")
            if selected_usage is None:
                previous = connection.execute(
                    "SELECT usage_json FROM reference_asset_usage WHERE owner_key = ? AND asset_id = ?",
                    (owner, asset_id)).fetchone()
                selected_usage = validate_usage(json.loads(previous["usage_json"])) if previous else default_usage()
                if ReferenceRole.IDENTITY not in selected_roles:
                    selected_usage["base_identity"] = False
            changed = connection.execute(
                "UPDATE reference_assets SET label = ?, role = ?, revision = revision + 1, updated_at = ? "
                "WHERE owner_key = ? AND asset_id = ? AND revision = ? AND state = ?",
                (label, selected_roles[0].value, updated_at.isoformat(), owner, asset_id,
                 expected_revision, ReferenceState.ACTIVE.value))
            if changed.rowcount != 1:
                raise TaskError("reference_revision_conflict", "the reference material changed; reload before editing")
            connection.execute("DELETE FROM reference_asset_roles WHERE owner_key = ? AND asset_id = ?",
                               (owner, asset_id))
            connection.executemany(
                "INSERT INTO reference_asset_roles VALUES (?,?,?,?)",
                [(owner, asset_id, role.value, updated_at.isoformat()) for role in selected_roles])
            if selected_usage is not None:
                if selected_usage["base_identity"]:
                    others = connection.execute(
                        "SELECT asset_id, usage_json FROM reference_asset_usage WHERE owner_key = ? AND asset_id != ?",
                        (owner, asset_id)).fetchall()
                    for other in others:
                        previous = validate_usage(json.loads(other["usage_json"]))
                        if not previous["base_identity"]:
                            continue
                        previous["base_identity"] = False
                        connection.execute("UPDATE reference_asset_usage SET usage_json = ? WHERE owner_key = ? AND asset_id = ?",
                                           (_json(previous), owner, other["asset_id"]))
                        connection.execute("UPDATE reference_assets SET revision = revision + 1, updated_at = ? "
                                           "WHERE owner_key = ? AND asset_id = ?",
                                           (updated_at.isoformat(), owner, other["asset_id"]))
                connection.execute("INSERT OR REPLACE INTO reference_asset_usage VALUES (?, ?, ?)",
                                   (owner, asset_id, _json(selected_usage)))
        return self.get(owner, asset_id)

    def withdraw(self, owner, asset_id, *, updated_at, expected_revision=None):
        with self._runtime.transaction() as connection:
            row = connection.execute(
                "SELECT state, revision FROM reference_assets WHERE owner_key = ? AND asset_id = ?",
                (owner, asset_id)).fetchone()
            if row is None or row["state"] == ReferenceState.WITHDRAWN.value:
                return 0
            if expected_revision is not None and row["revision"] != expected_revision:
                raise TaskError("reference_revision_conflict", "the reference material changed; reload before removing it")
            changed = connection.execute(
                "UPDATE reference_assets SET state = ?, revision = revision + 1, updated_at = ? "
                "WHERE owner_key = ? AND asset_id = ? AND revision = ? AND state = ?",
                (ReferenceState.WITHDRAWN.value, updated_at.isoformat(), owner, asset_id,
                 row["revision"], ReferenceState.ACTIVE.value))
            if changed.rowcount:
                connection.execute("DELETE FROM reference_asset_payloads WHERE owner_key = ? AND asset_id = ?",
                                   (owner, asset_id))
            return changed.rowcount

    def add_role(self, owner, asset_id, role, *, created_at):
        role = ReferenceRole(role)
        with self._runtime.transaction() as connection:
            connection.execute("INSERT OR IGNORE INTO reference_asset_roles VALUES (?,?,?,?)",
                               (owner, asset_id, role.value, created_at.isoformat()))
        return tuple(sorted(row["role"] for row in self._runtime.read_all(
            "SELECT role FROM reference_asset_roles WHERE owner_key = ? AND asset_id = ?",
            (owner, asset_id))))

    def roles_of(self, owner, asset_id):
        return tuple(sorted(row["role"] for row in self._runtime.read_all(
            "SELECT role FROM reference_asset_roles WHERE owner_key = ? AND asset_id = ?",
            (owner, asset_id))))

    def withdraw_source(self, owner, source, *, updated_at):
        """Withdraw one provider's material; keeps the catalog honest."""
        with self._runtime.transaction() as connection:
            changed = connection.execute(
                "UPDATE reference_assets SET state = ?, revision = revision + 1, updated_at = ? "
                "WHERE owner_key = ? AND source = ? AND state != ?",
                (ReferenceState.WITHDRAWN.value, updated_at.isoformat(), owner,
                 ReferenceSource(source).value, ReferenceState.WITHDRAWN.value))
            connection.execute(
                "DELETE FROM reference_asset_payloads WHERE owner_key = ? AND asset_id IN "
                "(SELECT asset_id FROM reference_assets WHERE owner_key = ? AND source = ? AND state = ?)",
                (owner, owner, ReferenceSource(source).value, ReferenceState.WITHDRAWN.value))
            return changed.rowcount

    # --- wardrobe items ----------------------------------------------------------

    def get_item(self, owner, item_id):
        row = self._runtime.read_one(
            "SELECT * FROM wardrobe_items WHERE owner_key = ? AND item_id = ?", (owner, item_id))
        return _item_from_row(row) if row else None

    def list_items(self, owner, *, state=ReferenceState.ACTIVE, limit=None, order_by_updated=False):
        ceiling = reference_limit() if limit is None else max(1, min(int(limit), reference_limit()))
        clauses = ["owner_key = ?"]
        parameters = [owner]
        if state is not None:
            clauses.append("state = ?")
            parameters.append(ReferenceState(state).value)
        order_clause = "updated_at DESC, item_id DESC" if order_by_updated else "created_at, item_id"
        rows = self._runtime.read_all(
            f"SELECT * FROM wardrobe_items WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order_clause} LIMIT ?", (*parameters, ceiling))
        return tuple(_item_from_row(row) for row in rows)

    def count_items(self, owner, *, state=ReferenceState.ACTIVE):
        row = self._runtime.read_one(
            "SELECT COUNT(*) AS n FROM wardrobe_items WHERE owner_key = ? AND state = ?",
            (owner, ReferenceState(state).value))
        return row["n"] if row else 0

    def count_active_items(self, owner):
        return self.count_items(owner, state=ReferenceState.ACTIVE)

    def put_item(self, owner, item, *, created_at, ignore_limit=False):
        if not ignore_limit:
            existing = self.get_item(owner, item.item_id)
            if existing is None and self.count_active_items(owner) >= reference_limit():
                raise TaskError("wardrobe_limit_reached",
                                f"this role already holds {reference_limit()} active wardrobe items")
        if item.asset_id is not None and self.get(owner, item.asset_id) is None:
            raise TaskError("reference_asset_missing", "the wardrobe picture is not registered")
        with self._runtime.transaction() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO wardrobe_items
                   (owner_key, item_id, asset_id, name, description, tags_json, source, state,
                    revision, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (owner, item.item_id, item.asset_id, item.name, item.description,
                 _json(list(item.tags)), item.source.value, item.state.value, item.revision,
                 (item.created_at or created_at).isoformat(), created_at.isoformat()))
        return self.get_item(owner, item.item_id)

    def withdraw_item(self, owner, item_id, *, updated_at):
        with self._runtime.transaction() as connection:
            changed = connection.execute(
                "UPDATE wardrobe_items SET state = ?, revision = revision + 1, updated_at = ? "
                "WHERE owner_key = ? AND item_id = ? AND state != ?",
                (ReferenceState.WITHDRAWN.value, updated_at.isoformat(), owner, item_id,
                 ReferenceState.WITHDRAWN.value))
            return changed.rowcount

    def choose_daily_item(self, owner, local_date, *, rotation_days, candidates, created_at):
        """Persist one role-level choice per local day and avoid recent repeats."""
        from datetime import date, timedelta

        today = date.fromisoformat(local_date)
        start = (today - timedelta(days=max(0, rotation_days - 1))).isoformat()
        by_id = {item.item_id: item for item in candidates}
        if not by_id:
            return None
        with self._runtime.transaction() as connection:
            existing = connection.execute(
                """SELECT w.*, d.item_revision AS chosen_revision FROM wardrobe_daily_outfits d
                   JOIN wardrobe_items w ON w.owner_key=d.owner_key AND w.item_id=d.item_id
                   WHERE d.owner_key=? AND d.local_date=?""", (owner, local_date)).fetchone()
            if (existing is not None and existing["state"] == ReferenceState.ACTIVE.value
                    and existing["revision"] == existing["chosen_revision"]):
                return _item_from_row(existing)
            recent = connection.execute(
                """SELECT item_id, MAX(local_date) AS last_date FROM wardrobe_daily_outfits
                   WHERE owner_key=? AND local_date>=? AND local_date<? GROUP BY item_id""",
                (owner, start, local_date)).fetchall()
            last_used = {row["item_id"]: row["last_date"] for row in recent}
            available = [item for item in candidates if item.item_id not in last_used]
            pool = available or list(candidates)
            chosen = min(pool, key=lambda item: (last_used.get(item.item_id, ""),
                                                  digest([owner, local_date, item.item_id])))
            connection.execute(
                """INSERT INTO wardrobe_daily_outfits(owner_key, local_date, item_id, item_revision, created_at)
                   VALUES (?,?,?,?,?) ON CONFLICT(owner_key, local_date) DO UPDATE SET
                   item_id=excluded.item_id, item_revision=excluded.item_revision, created_at=excluded.created_at""",
                (owner, local_date, chosen.item_id, chosen.revision, created_at.isoformat()))
            return chosen

    def daily_outfit_item(self, owner, local_date):
        """Read the committed outfit choice without opening a write transaction."""
        row = self._runtime.read_one(
            """SELECT w.*, d.item_revision AS chosen_revision FROM wardrobe_daily_outfits d
               JOIN wardrobe_items w ON w.owner_key=d.owner_key AND w.item_id=d.item_id
               WHERE d.owner_key=? AND d.local_date=?""", (owner, local_date))
        if (row is None or row["state"] != ReferenceState.ACTIVE.value
                or row["revision"] != row["chosen_revision"]):
            return None
        return _item_from_row(row)

    def select_daily_item(self, owner, local_date, item_id, *, created_at):
        """Persist an explicit role-level choice for one local calendar day."""
        item = self.get_item(owner, item_id)
        if item is None or item.state is not ReferenceState.ACTIVE:
            raise TaskError("wardrobe_item_missing", "the selected wardrobe item is unavailable")
        with self._runtime.transaction() as connection:
            connection.execute(
                """INSERT INTO wardrobe_daily_outfits(owner_key, local_date, item_id, item_revision, created_at)
                   VALUES (?,?,?,?,?) ON CONFLICT(owner_key, local_date) DO UPDATE SET
                   item_id=excluded.item_id, item_revision=excluded.item_revision, created_at=excluded.created_at""",
                (owner, local_date, item.item_id, item.revision, created_at.isoformat()))
        return item
