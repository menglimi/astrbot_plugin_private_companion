# -*- coding: utf-8 -*-
"""CoreStoreUserProfileScopeMixin。

由 tools/split_mixin_domain.py 从 core_store.py 机械抽取（37 个方法 + 0 个模块级名字 + 0 个类级赋值 / 739 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CoreStoreMixin）。
"""
from __future__ import annotations

from .core_store_shared import logger
from .core_store_shared import Any
from .core_store_shared import PHOTO_GENERATION_SCOPES
from .core_store_shared import PHOTO_GENERATION_SCOPE_LABELS
from .core_store_shared import PHOTO_GENERATION_SCOPE_LIMIT_KEYS
from .core_store_shared import _DEFAULT_GROUP_TEMPLATE
from .core_store_shared import _DEFAULT_USER_TEMPLATE
from .core_store_shared import _now_ts
from .core_store_shared import _safe_float
from .core_store_shared import _safe_int
from .core_store_shared import _single_line
from .core_store_shared import _today_key
from .core_store_shared import deepcopy
from .core_store_shared import ensure_legacy_profile_capabilities
from .core_store_shared import ensure_new_profile_capabilities
from .core_store_shared import migrate_legacy_relationship_score
from .core_store_shared import normalize_photo_generation_scope_limit
from .core_store_shared import normalize_photo_generation_scopes
from .core_store_shared import re
from .core_store_shared import runtime_persona_setting



class CoreStoreUserProfileScopeMixin:
    """CoreStoreUserProfileScopeMixin（从 CoreStoreMixin 拆出）。"""


    def _get_user(self, user_id: str) -> dict[str, Any]:
        original_user_id = str(user_id or "").strip()
        # Some platform adapters expose the complete private UMO as sender_id.
        # Keep the conversation route in ``umo`` but use its stable session ID
        # as the private-user record key, so the identity page never treats a
        # transport origin as a QQ user.
        normalized_identity = self._normalize_private_identity_id(original_user_id)
        if normalized_identity:
            original_user_id = normalized_identity
        user_id = self._canonical_private_user_id(original_user_id)
        users = self.data.setdefault("users", {})
        alias_migration_changed = False
        if original_user_id and original_user_id != user_id and original_user_id in users:
            target_created = user_id not in users
            target = users.setdefault(user_id, {})
            target["user_id"] = user_id
            source = users.pop(original_user_id)
            if isinstance(source, dict):
                migration_now = _now_ts()
                target_migration = migrate_legacy_relationship_score(
                    target,
                    created=target_created,
                    now=migration_now,
                    record_id=user_id,
                )
                source_migration = migrate_legacy_relationship_score(
                    source,
                    created=False,
                    now=migration_now,
                    record_id=original_user_id,
                )
                alias_migration_changed = bool(
                    target_migration.get("changed") or source_migration.get("changed")
                )
                self._merge_user_record_values(target, source, original_user_id)
        created = user_id not in users
        user = users.setdefault(user_id, deepcopy(_DEFAULT_USER_TEMPLATE))
        user["user_id"] = user_id
        if "unanswered_proactive_count" not in user:
            user["unanswered_proactive_count"] = _safe_int(
                user.get("ignored_streak"),
                0,
                0,
                1000,
            )
        if original_user_id and original_user_id != user_id:
            aliases = user.setdefault("alias_user_ids", [])
            if isinstance(aliases, list) and original_user_id not in aliases:
                aliases.append(original_user_id)
        if not created:
            # Capture legacy permission while the record still contains only
            # persisted evidence. The default template intentionally supports
            # old installs and must not manufacture an enabled signal here.
            # This also repairs a late-imported default-closed document when
            # its manual/automatic legacy grant remains present.
            ensure_legacy_profile_capabilities(user)
        for key, default_value in _DEFAULT_USER_TEMPLATE.items():
            if key not in user:
                user[key] = deepcopy(default_value)
        self._ensure_private_user_role(user_id, user)
        relationship_changed = self._ensure_relationship_user_state(user, created=created)
        user.setdefault("manual_enabled", False)
        user.setdefault("manual_disabled", False)
        # Compatibility mirror only: passive private chat is always available.
        user["enabled"] = True
        if not user.get("nickname"):
            user["nickname"] = runtime_persona_setting(self, "default_nickname", "你")
        if not user.get("style"):
            user["style"] = runtime_persona_setting(self, "default_style", "温柔")
        if relationship_changed or alias_migration_changed:
            self._schedule_data_save(sections={"users"})
        return user

    def _auto_profile_platform_set(self) -> set[str]:
        raw = runtime_persona_setting(self, "auto_profile_platforms", None)
        if isinstance(raw, str):
            items = re.split(r"[\s,，、;；]+", raw)
        elif isinstance(raw, (list, tuple, set)):
            items = list(raw)
        else:
            items = []
        normalized = {
            self._normalize_platform_kind(item)
            for item in items
            if str(item or "").strip()
        }
        return normalized or {"onebot", "qq_official", "telegram", "webchat", "generic"}

    def _auto_profile_nickname(self, user_id: str, sender_display_name: str) -> str:
        strategy = str(runtime_persona_setting(self, "default_nickname_strategy", "platform_display_name") or "").strip()
        fixed = _single_line(runtime_persona_setting(self, "default_nickname", "你"), 24) or "你"
        observed = _single_line(sender_display_name, 24)
        generic = {"用户", "主人", "主要用户", "默认用户", "unknown", "未知"}
        if strategy == "fixed":
            return fixed
        if strategy == "user_id":
            return _single_line(user_id, 24) or fixed
        if observed and observed.lower() not in generic:
            return observed
        return fixed or _single_line(user_id, 24)

    def _ensure_auto_private_user_profile(
        self,
        event: Any,
        *,
        user_id: str,
        sender_display_name: str = "",
        now: float | None = None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Create a minimal private profile using the configured permission defaults."""
        raw_user_id = str(user_id or "").strip()
        identity_normalizer = getattr(self, "_normalize_private_identity_id", None)
        normalized_user_id = identity_normalizer(raw_user_id) if callable(identity_normalizer) else ""
        resolver = getattr(self, "_event_private_user_storage_id", None)
        canonical_user_id = (
            resolver(event, normalized_user_id or raw_user_id)
            if callable(resolver)
            else self._canonical_private_user_id(normalized_user_id or raw_user_id)
        )
        if not canonical_user_id or self._is_bot_self_user_id(canonical_user_id):
            return None, False
        platform_kind = self._platform_kind_for_event(event)
        if platform_kind not in self._auto_profile_platform_set():
            return None, False
        users = self.data.setdefault("users", {})
        existing = users.get(canonical_user_id) if isinstance(users, dict) else None
        if isinstance(existing, dict):
            stamper = getattr(self, "_stamp_private_event_identity", None)
            if callable(stamper):
                stamper(existing, event, normalized_user_id or raw_user_id)
            # A legacy/migrated profile remains addressable even when automatic
            # creation is disabled.  Its REQ-036 capability state, not a DM,
            # decides whether the conversation may proceed.
            ensure_legacy_profile_capabilities(existing)
            return existing, False
        # Configured targets are an administrator-owned permission source.  A
        # platform/adapter identity rollover may produce a new scoped storage
        # key, but it must still materialize that target record even when
        # automatic profiles for ordinary users are disabled.  The capability
        # migrator below decides whether this scoped record is actually open;
        # this branch only makes the exact target addressable.
        target_checker = getattr(self, "_is_target_private_user", None)
        is_configured_target = False
        if callable(target_checker):
            for candidate in (normalized_user_id, raw_user_id, canonical_user_id):
                try:
                    if candidate and bool(target_checker(candidate, None)):
                        is_configured_target = True
                        break
                except Exception:
                    continue
        if is_configured_target:
            user = self._get_user(canonical_user_id)
            stamper = getattr(self, "_stamp_private_event_identity", None)
            if callable(stamper):
                stamper(user, event, normalized_user_id or raw_user_id)
            return user, False
        if not bool(runtime_persona_setting(self, "enable_auto_user_profile_creation", False)):
            return None, False

        user = self._get_user(canonical_user_id)
        stamper = getattr(self, "_stamp_private_event_identity", None)
        if callable(stamper):
            stamper(user, event, normalized_user_id or raw_user_id)
        created_at = float(now if now is not None else _now_ts())
        user["auto_profile_created"] = True
        user["auto_profile_created_at"] = created_at
        user["profile_origin"] = "private_auto"
        # `_get_user()` owns relationship initialization.  An automatic profile
        # must not bypass the ledger or overwrite an explicitly configured role.
        user["nickname"] = self._auto_profile_nickname(canonical_user_id, sender_display_name)
        user["style"] = _single_line(runtime_persona_setting(self, "default_style", "温柔"), 24) or "温柔"
        default_proactive_enabled = bool(runtime_persona_setting(self, "default_proactive_enabled", False))
        user["auto_enabled"] = True
        user["manual_enabled"] = False
        user["manual_disabled"] = False
        user["enabled"] = True
        user["private_memory_enabled"] = False
        user["cross_group_memory_enabled"] = False
        user["proactive_daily_limit"] = (
            max(0, min(30, _safe_int(runtime_persona_setting(self, "default_proactive_daily_limit", 0), 0)))
            if default_proactive_enabled
            else 0
        )
        user["proactive_boundary_note"] = (
            "自动建档按配置允许主动触达"
            if default_proactive_enabled
            else "自动建档默认不主动触达"
        )
        ensure_new_profile_capabilities(
            user,
            proactive_private_enabled=default_proactive_enabled,
            grant_source="private_auto_default",
        )
        user["last_seen"] = max(_safe_float(user.get("last_seen"), 0), created_at)
        user["last_activity_at"] = max(_safe_float(user.get("last_activity_at"), 0), created_at)
        self._note_private_user_umo(canonical_user_id, user, getattr(event, "unified_msg_origin", ""))
        self._schedule_data_save(sections={"users"})
        return user, True

    def _latest_user_activity_ts(self, user: dict[str, Any] | None) -> float:
        if not isinstance(user, dict):
            return 0.0
        return max(
            _safe_float(user.get("last_activity_at"), 0),
            _safe_float(user.get("last_seen"), 0),
            _safe_float(user.get("last_user_message_at"), 0),
            _safe_float(user.get("last_reply_at"), 0),
        )

    def _latest_private_user_activity_ts(self, user: dict[str, Any] | None) -> float:
        if not isinstance(user, dict):
            return 0.0
        private_seen = _safe_float(user.get("last_private_seen"), 0)
        return max(
            private_seen,
            _safe_float(user.get("last_private_activity_at"), private_seen),
            _safe_float(user.get("last_private_reply_at"), 0),
        )

    def _note_private_inbound_activity(self, user: dict[str, Any], ts: float, *, text: str = "") -> None:
        if not isinstance(user, dict):
            return
        previous_message_at = _safe_float(user.get("last_user_message_at"), 0)
        if text and previous_message_at > 0 and ts > previous_message_at:
            user["last_inbound_gap_seconds"] = min(
                365 * 24 * 3600,
                max(0.0, ts - previous_message_at),
            )
            user["last_inbound_gap_observed_at"] = ts
        user["last_private_seen"] = ts
        user["last_private_activity_at"] = ts
        if text:
            user["private_inbound_count"] = _safe_int(user.get("private_inbound_count"), 0) + 1

    def _is_target_private_user(self, user_id: str, user: dict[str, Any] | None = None) -> bool:
        user_id = self._canonical_private_user_id(str(user_id or "").strip())
        if self._is_bot_self_user_id(user_id):
            return False
        if isinstance(user, dict):
            if user.get("manual_enabled") or user.get("auto_enabled"):
                return True
            capabilities = user.get("unified_profile_capabilities")
            if (
                user.get("proactive_private_enabled") is True
                or (
                    isinstance(capabilities, dict)
                    and capabilities.get("proactive_private_enabled") is True
                )
            ):
                return True
        if not user_id:
            return False
        if user_id in set(self._configured_target_ids()):
            return True
        return False

    def _private_passive_profile_available(
        self,
        user_id: str,
        user: dict[str, Any] | None = None,
    ) -> bool:
        """Return whether a real private profile may use passive chat enhancements.

        Passive private chat is intentionally independent from the historical
        target/``enabled`` permission flags.  Those flags remain meaningful to
        proactive delivery and relationship policy, but must not suppress normal
        per-turn enhancements for an existing private profile.
        """
        canonical = self._canonical_private_user_id(str(user_id or "").strip())
        if not canonical or self._is_bot_self_user_id(canonical):
            return False
        return isinstance(user, dict)

    def _photo_generation_scope(self, event: Any = None, *, proactive: bool = False, user: dict[str, Any] | None = None, user_id: str = "") -> str:
        """Return the configured permission bucket for a photo request."""
        proactive = proactive or bool(getattr(event, "private_companion_proactive_framework", False))
        if proactive:
            return "proactive"
        group_getter = getattr(self, "_extract_group_id_from_event", None)
        if event is not None and callable(group_getter):
            try:
                if str(group_getter(event) or "").strip():
                    return "group"
            except Exception:
                pass
        resolved_id = str(user_id or "").strip()
        if not resolved_id and event is not None:
            try:
                resolved_id = str(event.get_sender_id() or "").strip()
            except Exception:
                resolved_id = ""
        resolver = getattr(self, "_private_user_id_for_event", None)
        if event is not None and resolved_id and callable(resolver):
            try:
                resolved_id = str(resolver(event, resolved_id) or resolved_id)
            except Exception:
                pass
        if user is None and resolved_id:
            getter = getattr(self, "_get_user", None)
            if callable(getter):
                try:
                    user = getter(resolved_id)
                except Exception:
                    user = None
        role_getter = getattr(self, "_private_user_role", None)
        role = role_getter(user, resolved_id) if callable(role_getter) else str((user or {}).get("relationship_role") or "friend")
        return "private_owner" if role == "owner" else "private_friend"

    def _user_requested_photo_generation_allowed(
        self,
        event: Any = None,
        *,
        proactive: bool = False,
    ) -> bool:
        """Keep user-request permissions separate from Bot-initiated photos."""
        scope = self._photo_generation_scope(event, proactive=proactive)
        if scope == "proactive":
            return True
        return bool(
            runtime_persona_setting(
                self,
                "enable_user_requested_photo_generation",
                True,
            )
        )

    def _photo_generation_scope_daily_limit(self, scope: str) -> int:
        scope = str(scope or "").strip().lower()
        key = PHOTO_GENERATION_SCOPE_LIMIT_KEYS.get(scope)
        if key and hasattr(self, key):
            return normalize_photo_generation_scope_limit(runtime_persona_setting(self, key, -1))

        legacy = runtime_persona_setting(self, "photo_generation_allowed_scopes", None)
        if isinstance(legacy, dict):
            return normalize_photo_generation_scope_limit(legacy.get(scope, -1))
        allowed = normalize_photo_generation_scopes(
            legacy,
            default_if_missing=True,
        )
        return -1 if scope in allowed else 0

    def _photo_generation_scope_requester_id(
        self,
        event: Any = None,
        *,
        user: dict[str, Any] | None = None,
        user_id: str = "",
    ) -> str:
        resolved_id = str(user_id or (user or {}).get("user_id") or "").strip()
        if not resolved_id and event is not None:
            try:
                resolved_id = str(event.get_sender_id() or "").strip()
            except Exception:
                resolved_id = ""
        resolver = getattr(self, "_private_user_id_for_event", None)
        if event is not None and resolved_id and callable(resolver):
            try:
                resolved_id = str(resolver(event, resolved_id) or resolved_id).strip()
            except Exception:
                pass
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        if resolved_id and callable(canonicalizer):
            try:
                resolved_id = str(canonicalizer(resolved_id) or resolved_id).strip()
            except Exception:
                pass
        return resolved_id

    def _photo_generation_scope_today_key(self) -> str:
        today_getter = getattr(self, "_environment_today_key", None)
        if callable(today_getter):
            try:
                today = str(today_getter() or "").strip()
                if today:
                    return today
            except Exception:
                pass
        return _today_key()

    def _photo_generation_scope_quota_left(
        self,
        event: Any = None,
        *,
        proactive: bool = False,
        user: dict[str, Any] | None = None,
        user_id: str = "",
        scope: str = "",
    ) -> int | None:
        resolved_scope = str(scope or "").strip().lower() or self._photo_generation_scope(
            event,
            proactive=proactive,
            user=user,
            user_id=user_id,
        )
        limit = self._photo_generation_scope_daily_limit(resolved_scope)
        if limit < 0:
            return None
        if limit == 0:
            return 0
        requester_id = self._photo_generation_scope_requester_id(
            event,
            user=user,
            user_id=user_id,
        )
        # Shared jobs such as the cached daily outfit have no requester and keep
        # their existing independent quota; a zero scope limit still blocks them.
        if not requester_id:
            return limit
        today = self._photo_generation_scope_today_key()
        data = getattr(self, "data", None)
        usage = data.get("photo_generation_scope_attempts") if isinstance(data, dict) else None
        if not isinstance(usage, dict) or str(usage.get("day") or "") != today:
            return limit
        counts = usage.get("counts")
        scope_counts = counts.get(resolved_scope) if isinstance(counts, dict) else None
        used = _safe_int(scope_counts.get(requester_id), 0, 0) if isinstance(scope_counts, dict) else 0
        return max(0, limit - used)

    def _note_photo_generation_scope_attempt(
        self,
        event: Any = None,
        *,
        proactive: bool = False,
        user: dict[str, Any] | None = None,
        user_id: str = "",
        scope: str = "",
    ) -> None:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return
        resolved_scope = str(scope or "").strip().lower() or self._photo_generation_scope(
            event,
            proactive=proactive,
            user=user,
            user_id=user_id,
        )
        if resolved_scope not in PHOTO_GENERATION_SCOPES:
            return
        requester_id = self._photo_generation_scope_requester_id(
            event,
            user=user,
            user_id=user_id,
        )
        if not requester_id:
            return
        today = self._photo_generation_scope_today_key()
        usage = data.get("photo_generation_scope_attempts")
        if not isinstance(usage, dict) or str(usage.get("day") or "") != today:
            usage = {"day": today, "counts": {}}
            data["photo_generation_scope_attempts"] = usage
        counts = usage.setdefault("counts", {})
        if not isinstance(counts, dict):
            counts = {}
            usage["counts"] = counts
        scope_counts = counts.setdefault(resolved_scope, {})
        if not isinstance(scope_counts, dict):
            scope_counts = {}
            counts[resolved_scope] = scope_counts
        scope_counts[requester_id] = _safe_int(scope_counts.get(requester_id), 0, 0) + 1

    def _photo_generation_scope_quota_block_message(
        self,
        event: Any = None,
        *,
        proactive: bool = False,
        user: dict[str, Any] | None = None,
        user_id: str = "",
        scope: str = "",
    ) -> str:
        resolved_scope = str(scope or "").strip().lower() or self._photo_generation_scope(
            event,
            proactive=proactive,
            user=user,
            user_id=user_id,
        )
        label = PHOTO_GENERATION_SCOPE_LABELS.get(resolved_scope, "当前范围")
        if self._photo_generation_scope_daily_limit(resolved_scope) == 0:
            return f"管理员已关闭{label}生图/改图（对应每日上限为 0）。"
        return f"今天{label}生图/改图额度用完了；管理员可调高对应每日上限，或设为 -1 取消限制。"

    def _photo_generation_scope_allowed(self, event: Any = None, *, proactive: bool = False, user: dict[str, Any] | None = None, user_id: str = "") -> bool:
        quota_left = self._photo_generation_scope_quota_left(
            event,
            proactive=proactive,
            user=user,
            user_id=user_id,
        )
        return quota_left is None or quota_left > 0

    def _is_bot_self_user_id(self, user_id: str) -> bool:
        user_id = str(user_id or "").strip()
        return bool(user_id and user_id in self._known_bot_self_ids())

    def _known_bot_self_ids(self) -> set[str]:
        ids: set[str] = set()
        for attr in ("bot_self_id", "bot_user_id", "self_id"):
            value = self._normalize_private_identity_id(getattr(self, attr, ""))
            if value:
                ids.add(value)
        raw_ids = getattr(self, "bot_self_ids", None)
        if isinstance(raw_ids, (list, tuple, set)):
            for item in raw_ids:
                value = self._normalize_private_identity_id(item)
                if value:
                    ids.add(value)
        platform_manager = getattr(getattr(self, "context", None), "platform_manager", None)
        for inst in list(getattr(platform_manager, "platform_insts", []) or []):
            for attr in ("self_id", "bot_self_id", "bot_user_id"):
                value = self._normalize_private_identity_id(getattr(inst, attr, ""))
                if value:
                    ids.add(value)
            bot = getattr(inst, "bot", None)
            api_clients = getattr(bot, "_wsr_api_clients", None)
            if isinstance(api_clients, dict):
                for item in api_clients:
                    value = self._normalize_private_identity_id(item)
                    if value and re.fullmatch(r"[1-9]\d{4,14}", value):
                        ids.add(value)
        return ids

    def _configured_bot_scope_ids(self) -> set[str]:
        """Return normalized Bot self IDs or adapter instance IDs."""
        raw = getattr(self, "bot_scope_ids", [])
        if isinstance(raw, str):
            values = re.split(r"[,\s,、;；]+", raw)
        elif isinstance(raw, (list, tuple, set)):
            values = list(raw)
        else:
            values = []
        return {
            _single_line(value, 160).casefold()
            for value in values
            if _single_line(value, 160)
        }

    def _bot_scope_allows_candidates(self, candidates: set[str]) -> bool:
        mode = _single_line(getattr(self, "bot_scope_mode", "all"), 20).casefold() or "all"
        if mode not in {"all", "allowlist", "denylist"}:
            mode = "all"
        if mode == "all":
            return True
        configured = self._configured_bot_scope_ids()
        if not configured:
            return mode != "allowlist"
        normalized = {
            _single_line(value, 160).casefold()
            for value in candidates
            if _single_line(value, 160)
        }
        matched = bool(normalized.intersection(configured))
        return matched if mode == "allowlist" else not matched

    def _bot_scope_platform_instance_candidates(self, instance_id: str) -> set[str]:
        prefix = _single_line(instance_id, 160).casefold()
        candidates = {prefix} if prefix else set()
        manager = getattr(getattr(self, "context", None), "platform_manager", None)
        if manager is None or not prefix:
            return candidates
        try:
            platforms = list(manager.get_insts())
        except Exception:
            platforms = list(getattr(manager, "platform_insts", []) or [])
        for platform in platforms:
            try:
                meta = platform.meta()
            except Exception:
                meta = None
            instance_ids = {
                _single_line(getattr(meta, attr, ""), 160).casefold()
                for attr in ("id", "name")
                if _single_line(getattr(meta, attr, ""), 160)
            }
            if prefix not in instance_ids:
                continue
            candidates.update(instance_ids)
            for owner in (platform, getattr(platform, "bot", None)):
                if owner is None:
                    continue
                for attr in ("self_id", "bot_self_id", "bot_user_id"):
                    value = _single_line(getattr(owner, attr, ""), 160).casefold()
                    if value:
                        candidates.add(value)
            api_clients = getattr(getattr(platform, "bot", None), "_wsr_api_clients", None)
            if isinstance(api_clients, dict):
                candidates.update(
                    _single_line(value, 160).casefold()
                    for value in api_clients
                    if _single_line(value, 160)
                )
            break
        return candidates

    def _bot_scope_allows_umo(self, umo: Any) -> bool:
        """Apply Bot scope to an eventless background delivery route."""
        origin = _single_line(umo, 240)
        prefix = origin.split(":", 1)[0] if ":" in origin else origin
        return self._bot_scope_allows_candidates(
            self._bot_scope_platform_instance_candidates(prefix)
        )

    def _bot_scope_allows_event(self, event: Any | None) -> bool:
        """Check whether the configured Bot scope accepts this event."""
        candidates: set[str] = set()
        if event is not None:
            self_getter = getattr(self, "_event_self_id", None)
            if callable(self_getter):
                try:
                    value = _single_line(self_getter(event), 160)
                except Exception:
                    value = ""
                if value:
                    candidates.add(value.casefold())
            for owner in (event, getattr(event, "message_obj", None)):
                if owner is None:
                    continue
                for attr in ("adapter_instance_id", "platform_instance_id", "platform_id"):
                    value = _single_line(getattr(owner, attr, ""), 160)
                    if value:
                        candidates.add(value.casefold())
            origin = _single_line(getattr(event, "unified_msg_origin", ""), 240)
            if ":" in origin:
                candidates.update(
                    self._bot_scope_platform_instance_candidates(origin.split(":", 1)[0])
                )
        return self._bot_scope_allows_candidates(candidates)

    def _get_group(self, group_id: str) -> dict[str, Any]:
        canonical_id = self._normalize_group_identity_id(group_id)
        if not canonical_id:
            canonical_id = _single_line(group_id, 160)
        group = self._canonicalize_group_records(canonical_id)
        for key, default_value in _DEFAULT_GROUP_TEMPLATE.items():
            if key not in group:
                group[key] = deepcopy(default_value)
        group["enabled"] = bool(group.get("enabled", True))
        return group

    def _parse_group_id_list(self, raw: Any) -> list[str]:
        if isinstance(raw, str):
            parts = re.split(r"[,\s,、;；]+", raw)
        elif isinstance(raw, list):
            parts = raw
        else:
            parts = []
        ids = []
        for part in parts:
            group_id = self._normalize_group_identity_id(part)
            if group_id and group_id not in ids:
                ids.append(group_id)
        return ids

    @staticmethod
    def _parse_text_list_config(raw: Any, *, limit: int = 120) -> list[str]:
        if isinstance(raw, str):
            parts = re.split(r"[\n,，、;；]+", raw)
        elif isinstance(raw, list):
            parts = raw
        else:
            parts = []
        values: list[str] = []
        seen: set[str] = set()
        for part in parts:
            value = _single_line(part, 60)
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            values.append(value)
            if len(values) >= limit:
                break
        return values

    def _configured_group_ids(self) -> list[str]:
        # Backward compatibility: old target_group_ids is now treated as whitelist.
        whitelist = self._parse_group_id_list(runtime_persona_setting(self, "group_whitelist_ids", []))
        legacy = self._parse_group_id_list(runtime_persona_setting(self, "target_group_ids", []))
        for group_id in legacy:
            if group_id not in whitelist:
                whitelist.append(group_id)
        return whitelist

    def _configured_group_blacklist_ids(self) -> list[str]:
        return self._parse_group_id_list(runtime_persona_setting(self, "group_blacklist_ids", []))

    def _group_enabled_for_event(self, group_id: str) -> bool:
        if not runtime_persona_setting(self, "enable_group_companion", True):
            return False
        if not self._group_allowed_by_access_mode(group_id):
            return False
        group = self._get_group(group_id)
        return bool(group.get("enabled", True))

    def _group_allowed_by_access_mode(self, group_id: str) -> bool:
        if runtime_persona_setting(self, "group_access_mode", "whitelist") == "blacklist":
            if group_id in self._configured_group_blacklist_ids():
                return False
        else:
            configured = self._configured_group_ids()
            if not configured:
                if not bool(getattr(self, "_empty_group_whitelist_warning_logged", False)):
                    self._empty_group_whitelist_warning_logged = True
                    logger.warning(
                        "群聊观察已开启但白名单为空,当前不会观察任何群；"
                        "请在群聊观测页把目标群加入白名单,或改用黑名单模式: first_group=%s",
                        _single_line(group_id, 80) or "-",
                    )
                return False
            self._empty_group_whitelist_warning_logged = False
            if group_id not in configured:
                return False
        return True

    def _group_llm_reply_block_store(self) -> dict[str, Any]:
        store = self.data.setdefault("group_llm_reply_blocks", {})
        if not isinstance(store, dict):
            store = {}
            self.data["group_llm_reply_blocks"] = store
        return store

    def _group_llm_reply_block_item(self, group_id: str) -> dict[str, Any]:
        group_id = _single_line(group_id, 80)
        if not group_id:
            return {}
        item = self._group_llm_reply_block_store().get(group_id)
        return item if isinstance(item, dict) else {}

    def _group_llm_reply_blocked(self, group_id: str) -> bool:
        item = self._group_llm_reply_block_item(group_id)
        return bool(item.get("enabled"))

    def _set_group_llm_reply_block(
        self,
        group_id: str,
        enabled: bool,
        *,
        operator_id: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        group_id = _single_line(group_id, 80)
        if not group_id:
            return {}
        store = self._group_llm_reply_block_store()
        if enabled:
            item = {
                "group_id": group_id,
                "enabled": True,
                "updated_at": _now_ts(),
                "operator_id": _single_line(operator_id, 80),
                "reason": _single_line(reason, 160),
            }
            store[group_id] = item
            return item
        previous = store.get(group_id)
        if isinstance(previous, dict):
            previous["enabled"] = False
            previous["cleared_at"] = _now_ts()
            previous["cleared_by"] = _single_line(operator_id, 80)
            previous["clear_reason"] = _single_line(reason, 160)
            store.pop(group_id, None)
            return previous
        return {"group_id": group_id, "enabled": False}

    def _active_group_llm_reply_blocks(self) -> list[dict[str, Any]]:
        store = self._group_llm_reply_block_store()
        items: list[dict[str, Any]] = []
        for group_id, item in list(store.items()):
            if not isinstance(item, dict) or not bool(item.get("enabled")):
                continue
            normalized = dict(item)
            normalized["group_id"] = _single_line(normalized.get("group_id") or group_id, 80)
            items.append(normalized)
        return sorted(items, key=lambda item: _safe_float(item.get("updated_at"), 0.0), reverse=True)
