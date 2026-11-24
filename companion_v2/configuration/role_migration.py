"""Idempotent, non-destructive migration for split role-layer configuration rows.

When an installation renames a role, collapses duplicate bot personas into a
canonical role id, or retains legacy display-name/bot-less role targets in
``configuration_targets``, settings saved under the older role layer can become
invisible to readers anchored on the canonical ``(installation_id, role_id,
bot_id)`` target.

This module copies missing field values from alias role targets onto the
canonical role target inside a single transaction without deleting historical
rows or overwriting explicit values already written on the canonical target.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Iterable, Mapping

from companion_v2.foundation.refs import SubjectKind, SubjectRef
from companion_v2.storage.codec import encode_timestamp
from companion_v2.storage.runtime import StorageRuntime, table_exists

from .models import ConfigLayer, ConfigTarget, encode


def _normalize_aliases(
    role_aliases: Mapping[str, str] | Iterable[tuple[str, ...]],
    persona_bindings: Iterable[tuple[str, str, str]],
) -> list[tuple[str, str, str | None]]:
    """Expand explicit aliases and safe persona-id fallbacks into (old, new, bot_id)."""
    bindings = [
        (str(bot_id or "").strip(), str(persona_id or "").strip(), str(role_id or "").strip())
        for bot_id, persona_id, role_id in persona_bindings
        if str(bot_id or "").strip() and str(role_id or "").strip()
    ]
    active_roles_by_bot: dict[str, set[str]] = {}
    bots_for_role: dict[str, set[str]] = {}
    for bot_id, _persona_id, role_id in bindings:
        active_roles_by_bot.setdefault(bot_id, set()).add(role_id)
        bots_for_role.setdefault(role_id, set()).add(bot_id)

    pairs: list[tuple[str, str, str | None]] = []
    seen: set[tuple[str, str, str | None]] = set()

    def add(old_role: str, new_role: str, bot_id: str | None) -> None:
        old_clean = str(old_role or "").strip()
        new_clean = str(new_role or "").strip()
        bot_clean = str(bot_id).strip() if bot_id else None
        if not old_clean or not new_clean or old_clean == new_clean:
            return
        entry = (old_clean, new_clean, bot_clean)
        if entry not in seen:
            seen.add(entry)
            pairs.append(entry)

    alias_items = role_aliases.items() if isinstance(role_aliases, Mapping) else role_aliases
    for item in alias_items:
        if len(item) == 2:
            old_role, new_role = item
            target_bots = sorted(bots_for_role.get(str(new_role or "").strip(), ()))
            if target_bots:
                for bot_id in target_bots:
                    add(old_role, new_role, bot_id)
            else:
                add(old_role, new_role, None)
        elif len(item) == 3:
            old_role, new_role, bot_id = item
            add(old_role, new_role, bot_id)

    # When a persona uses an auto-generated role id (e.g. role-<bot>-<hash>),
    # historical saves may have landed under the display persona_id (e.g. "时音").
    # Only treat persona_id as an alias if it is not itself an active canonical
    # role id on the same bot.
    for bot_id, persona_id, role_id in bindings:
        if persona_id and persona_id != role_id and persona_id not in active_roles_by_bot.get(bot_id, ()):
            add(persona_id, role_id, bot_id)

    return pairs


def migrate_role_configuration_aliases(
    runtime: StorageRuntime,
    *,
    role_aliases: Mapping[str, str] | Iterable[tuple[str, ...]] = (),
    persona_bindings: Iterable[tuple[str, str, str]] = (),
    observed_at: datetime | None = None,
) -> dict[str, int]:
    """Copy missing role-layer fields from legacy/alias role targets onto canonical targets.

    Safe to call repeatedly at startup and after role binding updates:
    - Never overwrites fields already explicitly configured on the canonical target.
    - Never deletes legacy rows (preserving rollback safety).
    - Leaves target revisions untouched when no missing fields are copied.
    """
    store = getattr(runtime, "configuration_store", runtime)
    if store is None or not table_exists(store, "configuration_targets") or not table_exists(store, "configuration_values"):
        return {"targets_migrated": 0, "fields_copied": 0}

    installation_id = runtime.installation_id
    bindings = [
        (str(bot_id or "").strip(), str(persona_id or "").strip(), str(role_id or "").strip())
        for bot_id, persona_id, role_id in persona_bindings
        if str(bot_id or "").strip() and str(role_id or "").strip()
    ]
    aliases = _normalize_aliases(role_aliases, bindings)
    bots_by_canonical: dict[str, set[str]] = {}
    for bot_id, _persona_id, role_id in bindings:
        bots_by_canonical.setdefault(role_id, set()).add(bot_id)

    if not aliases and not bots_by_canonical:
        return {"targets_migrated": 0, "fields_copied": 0}

    timestamp = encode_timestamp(observed_at or datetime.now(timezone.utc))
    targets_migrated = 0
    fields_copied = 0

    with store.transaction() as connection:
        rows = connection.execute(
            "SELECT target_key, target_json FROM configuration_targets"
        ).fetchall()
        role_rows: list[tuple[str, str, str | None]] = []
        for row in rows:
            try:
                doc = json.loads(row["target_json"])
            except (TypeError, ValueError):
                continue
            if (type(doc) is not dict or doc.get("installation_id") != installation_id
                    or doc.get("layer") != ConfigLayer.ROLE.value):
                continue
            role_id = doc.get("role_id")
            if not isinstance(role_id, str) or not role_id:
                continue
            bot_id = doc.get("bot_id")
            bot_clean = bot_id if isinstance(bot_id, str) and bot_id else None
            role_rows.append((row["target_key"], role_id, bot_clean))

        if not role_rows:
            return {"targets_migrated": 0, "fields_copied": 0}

        # Map each source role target_key -> list of canonical ConfigTargets to seed.
        copy_plan: list[tuple[str, ConfigTarget]] = []
        planned_pairs: set[tuple[str, str]] = set()

        def enqueue(source_key: str, canonical_role: str, bot_id: str | None) -> None:
            canonical = ConfigTarget(
                installation_id,
                ConfigLayer.ROLE,
                SubjectRef(installation_id, SubjectKind.BOT, canonical_role),
                bot_id=bot_id,
            )
            if source_key == canonical.key:
                return
            pair = (source_key, canonical.key)
            if pair not in planned_pairs:
                planned_pairs.add(pair)
                copy_plan.append((source_key, canonical))

        for source_key, source_role, source_bot in role_rows:
            for old_role, new_role, alias_bot in aliases:
                if source_role != old_role:
                    continue
                if source_bot is not None:
                    if alias_bot is None or alias_bot == source_bot:
                        enqueue(source_key, new_role, source_bot)
                elif alias_bot is not None:
                    enqueue(source_key, new_role, alias_bot)
                else:
                    enqueue(source_key, new_role, None)
            # Legacy role targets stored before bot_id scoping: backfill into
            # active bot-scoped canonical targets for the same role id.
            if source_bot is None and source_role in bots_by_canonical:
                for bot_id in sorted(bots_by_canonical[source_role]):
                    enqueue(source_key, source_role, bot_id)

        for source_key, canonical in copy_plan:
            source_values = connection.execute(
                "SELECT field_id, value_json FROM configuration_values WHERE target_key = ? ORDER BY field_id",
                (source_key,),
            ).fetchall()
            if not source_values:
                continue
            existing_fields = {
                row["field_id"]
                for row in connection.execute(
                    "SELECT field_id FROM configuration_values WHERE target_key = ?",
                    (canonical.key,),
                ).fetchall()
            }
            missing = [row for row in source_values if row["field_id"] not in existing_fields]
            if not missing:
                continue
            connection.execute(
                "INSERT OR IGNORE INTO configuration_targets VALUES (?, ?, 0, ?)",
                (canonical.key, encode(canonical.document()), timestamp),
            )
            target_row = connection.execute(
                "SELECT revision FROM configuration_targets WHERE target_key = ?",
                (canonical.key,),
            ).fetchone()
            next_revision = (target_row["revision"] if target_row else 0) + 1
            for item in missing:
                connection.execute(
                    """INSERT OR IGNORE INTO configuration_values (target_key, field_id, value_json, revision)
                       VALUES (?, ?, ?, ?)""",
                    (canonical.key, item["field_id"], item["value_json"], next_revision),
                )
            connection.execute(
                "UPDATE configuration_targets SET revision = ?, updated_at = ? WHERE target_key = ?",
                (next_revision, timestamp, canonical.key),
            )
            targets_migrated += 1
            fields_copied += len(missing)

    return {"targets_migrated": targets_migrated, "fields_copied": fields_copied}


__all__ = ["migrate_role_configuration_aliases"]
