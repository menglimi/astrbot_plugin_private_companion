# -*- coding: utf-8 -*-
"""CoreStorePrivateUserStateMixin。

由 tools/split_mixin_domain.py 从 core_store.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 707 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CoreStoreMixin）。
"""
from __future__ import annotations

from .core_store_shared import logger
from .core_store_shared import Any
from .core_store_shared import _DEFAULT_USER_TEMPLATE
from .core_store_shared import _now_ts
from .core_store_shared import _safe_float
from .core_store_shared import _single_line
from .core_store_shared import apply_natural_relationship_decay
from .core_store_shared import apply_relationship_event
from .core_store_shared import clamp_relationship_positive_stage_cap
from .core_store_shared import current_interaction_projection
from .core_store_shared import deepcopy
from .core_store_shared import migrate_legacy_relationship_score
from .core_store_shared import normalize_normal_interaction_band_cap
from .core_store_shared import normalize_relationship_mode
from .core_store_shared import normalize_relationship_positive_stage_cap_key
from .core_store_shared import re
from .core_store_shared import runtime_persona_setting



class CoreStorePrivateUserStateMixin:
    """CoreStorePrivateUserStateMixin（从 CoreStoreMixin 拆出）。"""


    def _merge_private_user_alias_records(self) -> bool:
        aliases = getattr(self, "private_user_aliases", {}) or {}
        users = self.data.setdefault("users", {})
        changed = False
        migration_now = _now_ts()

        def merge_transport_identity_records() -> bool:
            """Fold legacy full-UMO user keys into their stable private identity."""
            normalizer = getattr(self, "_normalize_private_identity_id", None)
            if not callable(normalizer):
                return False
            transport_changed = False
            for raw_user_id, source in list(users.items()):
                raw_id = str(raw_user_id or "").strip()
                if ":FriendMessage:" not in raw_id or not isinstance(source, dict):
                    continue
                normalized_id = normalizer(raw_id)
                canonical_id = self._canonical_private_user_id(normalized_id) if normalized_id else ""
                if not canonical_id or canonical_id == raw_id:
                    continue
                target = users.get(canonical_id)
                if isinstance(target, dict):
                    self._merge_user_record_values(target, source, raw_id)
                else:
                    target = source
                    users[canonical_id] = target
                target["user_id"] = canonical_id
                raw_aliases = target.get("alias_user_ids")
                if isinstance(raw_aliases, list):
                    target["alias_user_ids"] = [
                        item for item in raw_aliases if str(item or "").strip() != raw_id
                    ]
                users.pop(raw_user_id, None)
                transport_changed = True
                logger.info(
                    "已归一旧私聊 UMO 用户键: old=%s user=%s",
                    _single_line(raw_id, 120),
                    _single_line(canonical_id, 80),
                )
            return transport_changed

        backups = self.data.setdefault("private_user_alias_merge_backups", {})
        if not isinstance(backups, dict):
            backups = {}
            self.data["private_user_alias_merge_backups"] = backups
            changed = True

        # Keep alias records recoverable when an operator removes a mapping later.
        # The merged canonical record is intentionally retained; only the pre-merge
        # alias snapshot is restored, so activity recorded after the merge is not lost.
        active_aliases = {str(alias or "").strip() for alias in aliases}
        for raw_alias_id, raw_backup in list(backups.items()):
            alias_id = str(raw_alias_id or "").strip()
            if not alias_id or alias_id in active_aliases or not isinstance(raw_backup, dict):
                continue
            source = raw_backup.get("source")
            if not isinstance(source, dict):
                source = deepcopy(_DEFAULT_USER_TEMPLATE)
            source = deepcopy(source)
            source["user_id"] = alias_id
            if alias_id not in users:
                users[alias_id] = source
                changed = True
            canonical_id = str(raw_backup.get("canonical_id") or "").strip()
            target = users.get(canonical_id)
            if isinstance(target, dict):
                target_aliases = target.get("alias_user_ids")
                if isinstance(target_aliases, list) and alias_id in target_aliases:
                    target["alias_user_ids"] = [
                        item for item in target_aliases if str(item or "").strip() != alias_id
                    ]
                    changed = True
            backups.pop(raw_alias_id, None)
            changed = True

        # Startup maintenance must cover every persisted user, even when no
        # alias mapping is configured for this installation.
        for raw_user_id, raw_user in list(users.items()):
            if not isinstance(raw_user, dict):
                continue
            migration = migrate_legacy_relationship_score(
                raw_user,
                created=False,
                now=migration_now,
                record_id=raw_user_id,
            )
            changed = changed or bool(migration.get("changed"))
        # Older versions removed alias records without retaining a snapshot.
        # Recreate a clean identity from the canonical record's alias list when
        # that alias is no longer configured, even if other mappings remain.
        for canonical_id, user in list(users.items()):
            if not isinstance(user, dict):
                continue
            raw_alias_ids = user.get("alias_user_ids")
            if not isinstance(raw_alias_ids, list):
                continue
            kept_alias_ids: list[Any] = []
            for raw_alias_id in raw_alias_ids:
                alias_id = str(raw_alias_id or "").strip()
                if not alias_id or alias_id == str(canonical_id or "").strip():
                    continue
                if alias_id in active_aliases:
                    kept_alias_ids.append(raw_alias_id)
                    continue
                if alias_id not in users:
                    restored = deepcopy(_DEFAULT_USER_TEMPLATE)
                    restored["user_id"] = alias_id
                    users[alias_id] = restored
                changed = True
            if kept_alias_ids != raw_alias_ids:
                user["alias_user_ids"] = kept_alias_ids
                changed = True
        if not aliases:
            return merge_transport_identity_records() or changed
        for alias_id, canonical_id in list(aliases.items()):
            alias_id = str(alias_id or "").strip()
            canonical_id = self._canonical_private_user_id(canonical_id)
            if not alias_id or not canonical_id or alias_id == canonical_id:
                continue
            backup = backups.get(alias_id)
            source = users.get(alias_id)
            previous_canonical = (
                str(backup.get("canonical_id") or "").strip()
                if isinstance(backup, dict)
                else ""
            )
            if (
                not isinstance(source, dict)
                and isinstance(backup, dict)
                and previous_canonical
                and previous_canonical != canonical_id
            ):
                restored_source = backup.get("source")
                if isinstance(restored_source, dict):
                    source = deepcopy(restored_source)
                    source["user_id"] = alias_id
                    users[alias_id] = source
                    changed = True
            if not isinstance(source, dict):
                continue
            if isinstance(backup, dict):
                if previous_canonical and previous_canonical != canonical_id:
                    previous_target = users.get(previous_canonical)
                    previous_target_aliases = (
                        previous_target.get("alias_user_ids")
                        if isinstance(previous_target, dict)
                        else None
                    )
                    if isinstance(previous_target_aliases, list) and alias_id in previous_target_aliases:
                        previous_target["alias_user_ids"] = [
                            item
                            for item in previous_target_aliases
                            if str(item or "").strip() != alias_id
                        ]
                    changed = True
                backup["canonical_id"] = canonical_id
            else:
                backups[alias_id] = {
                    "canonical_id": canonical_id,
                    "source": deepcopy(source),
                }
                changed = True
            target_created = canonical_id not in users
            target = users.setdefault(canonical_id, deepcopy(_DEFAULT_USER_TEMPLATE))
            target["user_id"] = canonical_id
            target_migration = migrate_legacy_relationship_score(
                target,
                created=target_created,
                now=migration_now,
                record_id=canonical_id,
            )
            source_migration = migrate_legacy_relationship_score(
                source,
                created=False,
                now=migration_now,
                record_id=alias_id,
            )
            changed = changed or bool(target_migration.get("changed")) or bool(source_migration.get("changed"))
            self._merge_user_record_values(target, source, alias_id)
            users.pop(alias_id, None)
            changed = True
        return merge_transport_identity_records() or changed

    def _private_user_has_group_observation_evidence(self, user_id: str, user: dict[str, Any]) -> bool:
        """Whether a disabled users row was materialized only from group observation."""
        if not user_id or not isinstance(user, dict):
            return False
        if bool(user.get("observation_only")):
            return True
        if _single_line(user.get("profile_origin"), 40).lower() == "group_observation":
            return True

        subject_id = _single_line(user.get("identity_subject_id"), 160)
        if not subject_id:
            subject_id = self._canonical_private_user_id(str(user_id or "").strip())
        profiles = self.data.get("worldbook_member_profiles") if isinstance(getattr(self, "data", None), dict) else {}
        observation = profiles.get(subject_id) if isinstance(profiles, dict) else None
        if isinstance(observation, dict) and bool(observation.get("observation_only")):
            return True

        person_id = _single_line(user.get("unified_person_id"), 80)
        root = self.data.get("unified_person") if isinstance(getattr(self, "data", None), dict) else {}
        if not person_id or not isinstance(root, dict):
            return False
        links = root.get("identity_links")
        checkpoints = root.get("binding_checkpoints")
        has_group_creation = any(
            isinstance(item, dict)
            and _single_line(item.get("person_id"), 80) == person_id
            and _single_line(item.get("last_operation_id"), 160).startswith("req036.group_observation:")
            for item in (links.values() if isinstance(links, dict) else [])
        )
        person_checkpoints = [
            item
            for item in (checkpoints.values() if isinstance(checkpoints, dict) else [])
            if isinstance(item, dict) and _single_line(item.get("person_id"), 80) == person_id
        ]
        has_private_source = any(
            _single_line(item.get("last_source_scope"), 160).lower() in {"private", "dm"}
            or _single_line(item.get("last_source_scope"), 160).lower().startswith(("private:", "dm:"))
            for item in person_checkpoints
        )
        return bool(has_group_creation and not has_private_source)

    def _private_user_is_reaction_only_shadow(self, user_id: str, user: dict[str, Any]) -> bool:
        """Whether a scoped row only mirrors a canonical user's reaction cache."""
        match = re.fullmatch(r"([a-z0-9_]+):([^:]+):([0-9a-f]{16})", str(user_id or "").strip().lower())
        if not match or not isinstance(user, dict):
            return False
        platform_kind, canonical_id, _digest = match.groups()
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        canonical = users.get(canonical_id) if isinstance(users, dict) else None
        if not isinstance(canonical, dict) or canonical is user:
            return False
        if _single_line(user.get("identity_platform_kind"), 40):
            return False
        if _single_line(user.get("last_inbound_umo"), 240):
            return False
        reaction = user.get("reaction_expression")
        scopes = reaction.get("scopes") if isinstance(reaction, dict) else None
        if not isinstance(scopes, dict) or not scopes:
            return False
        expected_marker = f":FriendMessage:{canonical_id}"
        if any(expected_marker not in _single_line(scope, 240) for scope in scopes):
            return False
        canonical_umo = _single_line(canonical.get("last_inbound_umo") or canonical.get("umo"), 240)
        if expected_marker not in canonical_umo:
            return False
        platform_parser = getattr(self, "_platform_kind_for_umo", None)
        if callable(platform_parser):
            try:
                canonical_platform = _single_line(platform_parser(canonical_umo), 40).lower()
            except Exception:
                canonical_platform = ""
            if canonical_platform not in {"", "generic", platform_kind}:
                return False
        return True

    def _private_user_has_private_footprint(self, user_id: str, user: dict[str, Any]) -> bool:
        """Whether a stored user has evidence that it belongs in private chat."""
        if not user_id or not isinstance(user, dict):
            return False
        try:
            configured_targets = {
                str(item).strip()
                for item in self._configured_target_ids()
                if str(item).strip()
            }
        except Exception:
            configured_targets = set()
        if user_id in configured_targets:
            return True
        if bool(user.get("enabled")) or bool(user.get("manual_enabled")) or bool(user.get("manual_disabled")):
            return True
        if self._normalize_private_user_role(user.get("relationship_role")) == "owner":
            return True
        profile_origin = _single_line(user.get("profile_origin"), 40).lower()
        if profile_origin in {"manual", "administrator", "private", "private_auto"}:
            return True
        if bool(user.get("auto_profile_created")):
            return True

        # ``umo`` alone is not proof of a DM. Legacy group observation rows
        # were assigned a synthetic ``default:FriendMessage:<id>`` fallback
        # before any private event was received. Inbound and bound routes are
        # only written by real private delivery paths.
        for key in ("last_inbound_umo", "bound_delivery_umo", "preferred_delivery_umo"):
            route = _single_line(user.get(key), 300)
            if route and ":GroupMessage:" not in route:
                return True
        routes = user.get("private_delivery_routes")
        if isinstance(routes, (dict, list)) and routes:
            return True

        numeric_activity_keys = (
            "last_sent",
            "last_user_message_at",
            "last_companion_message_at",
            "last_reply_at",
            "last_private_seen",
            "last_private_activity_at",
            "last_private_reply_at",
            "private_inbound_count",
            "reply_count",
            "proactive_sent_count",
        )
        if any(_safe_float(user.get(key), 0.0, 0.0) > 0 for key in numeric_activity_keys):
            return True

        text_activity_keys = (
            "last_user_message",
            "last_companion_message",
            "last_proactive_reason",
            "last_proactive_action",
            "last_proactive_behavior_summary",
            "last_proactive_motive",
        )
        if any(bool(_single_line(user.get(key), 240)) for key in text_activity_keys):
            return True

        structured_activity_keys = (
            "companion_memory",
            "expression_profile",
            "intent_profile",
            "relationship_state",
            "persona_relationship",
            "dialogue_episodes",
            "open_loops",
            "action_preferences",
            "action_consequences",
            "state_continuity",
            "pending_followup_event",
            "suspended_proactive",
            "simulation_mode",
            "llm_timer_event",
            "planned_event_chain",
            "greetings_sent",
            "behavior_habits",
        )

        def has_structured_activity(value: Any) -> bool:
            if isinstance(value, dict):
                return any(has_structured_activity(item) for item in value.values())
            if isinstance(value, list):
                return any(has_structured_activity(item) for item in value)
            if isinstance(value, str):
                return bool(value.strip())
            return value not in (None, False, 0)

        if any(has_structured_activity(user.get(key)) for key in structured_activity_keys):
            return True
        ledger = user.get("relationship_ledger")
        if isinstance(ledger, list) and any(
            isinstance(item, dict)
            and _single_line(item.get("reason_code"), 80).lower() not in {"", "group_inbound"}
            for item in ledger
        ):
            return True
        aliases = user.get("alias_user_ids")
        if isinstance(aliases, list) and any(
            ":FriendMessage:" in _single_line(item, 240)
            for item in aliases
        ):
            return True
        group_only_ledger = bool(ledger) and all(
            isinstance(item, dict)
            and _single_line(item.get("reason_code"), 80).lower() == "group_inbound"
            for item in ledger
        )
        if _safe_float(user.get("relationship_score"), 0.0) != 0 and not group_only_ledger:
            return True

        default_nickname = _single_line(runtime_persona_setting(self, "default_nickname", ""), 40)
        nickname = _single_line(user.get("nickname"), 40)
        if nickname and nickname != default_nickname:
            return True
        default_style = _single_line(runtime_persona_setting(self, "default_style", ""), 120)
        style = _single_line(user.get("style"), 120)
        return bool(style and style != default_style)

    def _cleanup_orphan_reaction_expression_users(self) -> bool:
        """Remove group-only placeholders from the private-user table.

        Old group observation and reaction paths could create a user record and
        then attach transient group caches to it.  Those caches are not private
        chat evidence; explicitly managed users and records with real private
        activity remain untouched.
        """
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else None
        if not isinstance(users, dict) or not users:
            return False
        removed: list[str] = []

        for raw_user_id, user in list(users.items()):
            user_id = self._canonical_private_user_id(str(raw_user_id or "").strip())
            if not user_id or not isinstance(user, dict):
                continue
            if self._is_bot_self_user_id(user_id):
                continue
            cleanup_evidence = self._private_user_has_group_observation_evidence(user_id, user)
            cleanup_evidence = cleanup_evidence or self._private_user_is_reaction_only_shadow(user_id, user)
            if not cleanup_evidence:
                continue
            if self._private_user_has_private_footprint(user_id, user):
                continue
            users.pop(raw_user_id, None)
            removed.append(user_id)

        if removed:
            logger.info(
                "已清理群聊链路遗留的私聊占位记录: count=%s ids=%s",
                len(removed),
                ",".join(removed[:12]),
            )
            return True
        return False

    @staticmethod
    def _normalize_private_user_role(value: Any) -> str:
        text = str(value or "").strip().lower()
        mapping = {
            "owner": "owner",
            "master": "owner",
            "main": "owner",
            "target": "owner",
            "主人": "owner",
            "主用户": "owner",
            "主要用户": "owner",
            "目标用户": "owner",
            "friend": "friend",
            "social": "friend",
            "guest": "friend",
            "朋友": "friend",
            "好友": "friend",
            "普通朋友": "friend",
            "次要用户": "friend",
        }
        return mapping.get(text, "")

    @staticmethod
    def _private_user_role_label(role: str) -> str:
        return "主要用户" if role == "owner" else "次要用户"

    def _protected_owner_nickname_tokens(self) -> set[str]:
        tokens: set[str] = set()
        generic = {
            "你",
            "妳",
            "您",
            "我",
            "他",
            "她",
            "它",
            "大家",
            "群友",
            "朋友",
            "主人",
            "主用户",
            "主要用户",
            "次要用户",
            "目标用户",
        }

        def add(value: Any) -> None:
            text = _single_line(value, 24)
            text = text.strip("「」『』“”\"'`[]()（）<>《》:：,，.。!！?？")
            if not text or text.isdigit() or text in generic:
                return
            if len(text) < 2 or len(text) > 12:
                return
            tokens.add(text)
            compact = re.sub(r"\s+", "", text)
            if compact and compact != text and 2 <= len(compact) <= 12 and compact not in generic:
                tokens.add(compact)

        add(runtime_persona_setting(self, "default_nickname", ""))
        target_ids = set()
        try:
            target_ids = set(self._configured_target_ids())
        except Exception:
            target_ids = set()
        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
        if isinstance(users, dict):
            for user_id, user in users.items():
                if not isinstance(user, dict):
                    continue
                role = self._private_user_role(user, str(user_id or ""))
                if role != "owner" and str(user_id or "") not in target_ids:
                    continue
                add(user.get("nickname"))
                add(user.get("name"))
        profiles = self.data.get("worldbook_member_profiles", {}) if isinstance(getattr(self, "data", None), dict) else {}
        if isinstance(profiles, dict):
            for user_id in target_ids:
                profile = profiles.get(str(user_id))
                if not isinstance(profile, dict):
                    continue
                add(profile.get("name"))
                for key in ("aliases", "observed_names"):
                    raw = profile.get(key)
                    if isinstance(raw, list):
                        for item in raw:
                            add(item)
        return tokens

    def _private_user_default_role(self, user_id: str, user: dict[str, Any] | None = None) -> str:
        clean_id = self._canonical_private_user_id(str(user_id or "").strip())
        if clean_id and clean_id in set(self._configured_target_ids()):
            return "owner"
        return "friend"

    def _ensure_private_user_role(self, user_id: str, user: dict[str, Any]) -> str:
        role = self._normalize_private_user_role(user.get("relationship_role"))
        if not role:
            role = self._private_user_default_role(user_id, user)
            user["relationship_role"] = role
        return role

    def _ensure_relationship_user_state(self, user: dict[str, Any], *, created: bool = False) -> bool:
        """Lazily normalize additive relationship fields without migrating user identity or data paths."""
        setting_getter = getattr(self, "persona_setting", None)
        setting = setting_getter if callable(setting_getter) else lambda key, default=None: getattr(self, key, default)
        # The user-visible affinity master switch is intentionally a hard
        # runtime boundary: archived relationship data remains readable, but
        # it must not be normalized, decayed or otherwise changed while off.
        if not bool(setting("enable_custom_relationship_stage_policy", False)):
            return False
        before = {
            "relationship_mode": user.get("relationship_mode"),
            "relationship_score": user.get("relationship_score"),
            "relationship_score_schema_version": user.get("relationship_score_schema_version"),
            "relationship_positive_stage_cap_key": user.get("relationship_positive_stage_cap_key"),
            "normal_interaction_band_cap": user.get("normal_interaction_band_cap"),
            "current_interaction": deepcopy(user.get("current_interaction")),
            "relationship_decay_settled_day": user.get("relationship_decay_settled_day"),
            "relationship_last_decay_stage_drop_at": user.get("relationship_last_decay_stage_drop_at"),
        }
        score_migration = migrate_legacy_relationship_score(
            user,
            created=created,
            now=_now_ts(),
            record_id=user.get("user_id"),
        )
        user["relationship_mode"] = normalize_relationship_mode(
            user.get("relationship_mode"),
            user.get("relationship_role"),
        )
        positive_cap = normalize_relationship_positive_stage_cap_key(
            setting("relationship_positive_stage_cap_key", "close")
        )
        interaction_cap = normalize_normal_interaction_band_cap(
            setting("normal_interaction_band_cap", "warm")
        )
        user["relationship_positive_stage_cap_key"] = positive_cap
        user["normal_interaction_band_cap"] = interaction_cap
        clamp_relationship_positive_stage_cap(user, cap_key=positive_cap)
        raw_interaction = user.get("current_interaction")
        if created and not raw_interaction:
            raw_interaction = {
                "expression_band": str(setting("default_interaction_band", "relaxed") or "relaxed"),
                "source": "default_profile",
                "reason": "profile_created",
                "updated_at": _now_ts(),
                "expires_at": 0,
                "manual_override": False,
            }
        user["current_interaction"] = current_interaction_projection(
            raw_interaction,
            relationship_role=user.get("relationship_role"),
            relationship_mode=user.get("relationship_mode"),
            relationship_score=user.get("relationship_score"),
            normal_interaction_band_cap=interaction_cap,
            now=_now_ts(),
        )
        apply_natural_relationship_decay(
            user,
            grace_days=int(getattr(self, "relationship_decay_grace_days", 3)),
            early_rate=int(getattr(self, "relationship_decay_early_per_day", 2)),
            middle_rate=int(getattr(self, "relationship_decay_middle_per_day", 5)),
            late_rate=int(getattr(self, "relationship_decay_late_per_day", 8)),
            policy=(
                setting("relationship_stage_policy", None)
                if bool(setting("enable_custom_relationship_stage_policy", False))
                else None
            ),
            timezone_name=getattr(self, "environment_perception_timezone", None),
        )
        after = {
            "relationship_mode": user.get("relationship_mode"),
            "relationship_score": user.get("relationship_score"),
            "relationship_score_schema_version": user.get("relationship_score_schema_version"),
            "relationship_positive_stage_cap_key": user.get("relationship_positive_stage_cap_key"),
            "normal_interaction_band_cap": user.get("normal_interaction_band_cap"),
            "current_interaction": user.get("current_interaction"),
            "relationship_decay_settled_day": user.get("relationship_decay_settled_day"),
            "relationship_last_decay_stage_drop_at": user.get("relationship_last_decay_stage_drop_at"),
        }
        changed = before != after or bool(score_migration.get("changed"))
        if changed:
            snapshot_emitter = getattr(self, "_req041_emit_relationship_snapshot", None)
            if callable(snapshot_emitter):
                snapshot_emitter(user, reason_code="relationship_state_normalized")
        return changed

    def _apply_relationship_event(
        self,
        user: dict[str, Any],
        delta: int,
        *,
        reason_code: str,
        event_id: str = "",
        now: float | None = None,
        req041_group_admission_event_id: str = "",
    ) -> dict[str, Any]:
        setting_getter = getattr(self, "persona_setting", None)
        setting = setting_getter if callable(setting_getter) else lambda key, default=None: getattr(self, key, default)
        if bool(getattr(self, "enable_p4_b_legacy_score_isolation", False)):
            return {
                "changed": False,
                "code": "p4_legacy_score_isolated",
                "score": user.get("relationship_score"),
            }
        if not bool(setting("enable_custom_relationship_stage_policy", False)):
            return {
                "changed": False,
                "code": "relationship_system_disabled",
                "score": user.get("relationship_score"),
            }
        score_migration = migrate_legacy_relationship_score(
            user,
            created=False,
            now=now,
            record_id=user.get("user_id"),
        )
        # A secondary user's ordinary positive events are paused while a
        # verified boundary violation is still unrecovered. Explicit apology
        # recovery remains available through its dedicated reason code.
        if delta > 0 and str(reason_code) not in {"relationship_violation_recovery"}:
            violation = user.get("relationship_violation")
            recovery_settler = getattr(self, "_settle_relationship_violation_recovery", None)
            if isinstance(violation, dict) and callable(recovery_settler):
                recovery_settler(user, now=_now_ts() if now is None else _safe_float(now, _now_ts(), 0))
                violation = user.get("relationship_violation")
            try:
                role = self._private_user_role(user, str(user.get("user_id") or ""))
            except Exception:
                role = str(user.get("relationship_role") or "friend")
            try:
                pending_points = int(violation.get("unrecovered_points") or 0) if isinstance(violation, dict) else 0
            except (TypeError, ValueError):
                pending_points = 0
            if str(role).strip().lower() != "owner" and pending_points > 0:
                if score_migration.get("changed"):
                    self._schedule_data_save(sections={"users"})
                return {
                    "changed": False,
                    "code": "relationship_violation_recovery_pending",
                    "score": user.get("relationship_score"),
                    "delta": 0,
                }
        result = apply_relationship_event(
            user,
            delta,
            reason_code=reason_code,
            event_id=event_id or None,
            now=now,
            positive_daily_cap=int(getattr(self, "relationship_positive_daily_cap", 12)),
            event_window_seconds=int(getattr(self, "relationship_event_window_minutes", 30)) * 60,
            positive_event_cap=int(getattr(self, "relationship_positive_event_cap", 4)),
            negative_event_cap=int(getattr(self, "relationship_negative_event_cap", 12)),
            positive_stage_cap_key=setting("relationship_positive_stage_cap_key", "close"),
            timezone_name=getattr(self, "environment_perception_timezone", None),
        )
        producer = getattr(self, "req041_dual_write_producer", None)
        if result.get("changed") and producer is not None:
            try:
                try:
                    source_revision = max(0, int(user.get("req041_relationship_source_revision") or 0)) + 1
                except (TypeError, ValueError, OverflowError):
                    source_revision = 1
                registry_getter = getattr(self, "_active_unified_person_registry", None)
                registry = registry_getter() if callable(registry_getter) else None
                if registry is None:
                    raise RuntimeError("dual_write_registry_unavailable")
                scope_getter = getattr(self, "_unified_persona_domain", None)
                source_scope = scope_getter() if callable(scope_getter) else ""
                dual_write = producer.emit_relationship(
                    registry=registry,
                    user=user,
                    requested_delta=delta,
                    reason_code=str(reason_code or ""),
                    result=result,
                    source_scope=source_scope or "default",
                    source_revision=source_revision,
                    group_admission_event_id=req041_group_admission_event_id,
                )
                if int(dual_write.get("source_revision") or 0) > 0:
                    user["req041_relationship_source_revision"] = int(dual_write["source_revision"])
                result["req041_dual_write"] = str(dual_write.get("status") or "unknown")
                result["req041_dual_write_code"] = str(dual_write.get("code") or "")
            except Exception as exc:
                producer.fail_closed("relationship_dual_write_failed")
                result["req041_dual_write"] = "failed"
                result["req041_dual_write_code"] = "relationship_dual_write_failed"
                migration_status = getattr(self, "req041_migration_status", None)
                if isinstance(migration_status, dict):
                    migration_status.update({
                        "state": "paused",
                        "code": "relationship_dual_write_failed",
                        "dual_write": "failed",
                    })
                logger.warning(
                    "REQ-041 关系双写失败，已暂停新读切换并保留 legacy 写入: %s",
                    _single_line(exc, 160),
                )
        if result.get("changed") or score_migration.get("changed"):
            self._schedule_data_save(sections={"users"})
        return result
