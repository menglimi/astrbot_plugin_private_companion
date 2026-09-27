# -*- coding: utf-8 -*-
"""CoreStoreIdentityMergeMixin。

由 tools/split_mixin_domain.py 从 core_store.py 机械抽取（18 个方法 + 0 个模块级名字 + 0 个类级赋值 / 693 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CoreStoreMixin）。
"""
from __future__ import annotations

from .core_store_shared import logger
from .core_store_shared import Any
from .core_store_shared import _DEFAULT_GROUP_TEMPLATE
from .core_store_shared import _now_ts
from .core_store_shared import _safe_float
from .core_store_shared import _safe_int
from .core_store_shared import _single_line
from .core_store_shared import _today_key
from .core_store_shared import asyncio
from .core_store_shared import deepcopy
from .core_store_shared import hashlib
from .core_store_shared import json
from .core_store_shared import migrate_legacy_relationship_score
from .core_store_shared import re
from .core_store_shared import runtime_persona_setting
from .core_store_shared import story_legacy_operation



class CoreStoreIdentityMergeMixin:
    """CoreStoreIdentityMergeMixin（从 CoreStoreMixin 拆出）。"""


    @story_legacy_operation("store.plugin.reset")
    async def _reset_plugin_store(self) -> None:
        async with self._data_lock:
            self.data = self._new_store()
            if runtime_persona_setting(self, "default_enable_configured_targets", True):
                self._sync_configured_targets()
            self._clear_default_data_save_dirty()
            await asyncio.to_thread(
                self._save_data_now_sync,
                full_scope="explicit_reset",
            )

    async def _rebuild_today_after_reset(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        state = await self._ensure_daily_state(force=True)
        plan = await self._generate_daily_plan()
        async with self._data_lock:
            self.data["daily_plan"] = plan
            self._save_data_sync(
                sections={"daily_state", "daily_plan"},
            )

        diary = None
        if runtime_persona_setting(self, "enable_daily_diary", True):
            diary = await self._generate_daily_diary()
            async with self._data_lock:
                diaries = self.data.setdefault("bot_diaries", [])
                if not isinstance(diaries, list):
                    diaries = []
                    self.data["bot_diaries"] = diaries
                diaries.append(diary)
                max_entries = max(1, _safe_int(runtime_persona_setting(self, "max_diary_entries", 14), 14, 1))
                del diaries[:-max_entries]
                self.data["diary_generated_day"] = _today_key()
                try:
                    self.data["dream_fragments"] = self._merge_dream_fragment_pool(
                        diary.get("dream_fragments", []) if isinstance(diary, dict) else []
                    )
                    self.data["daily_diary_postprocess_error"] = ""
                except Exception as exc:
                    self.data["daily_diary_postprocess_error"] = _single_line(exc, 180)
                    logger.warning(
                        "重建今日日记已保存,但梦境碎片合并失败: %s",
                        _single_line(exc, 180),
                    )
                story_plan = diary.get("story_plan") if isinstance(diary, dict) else None
                if isinstance(story_plan, dict):
                    self.data["daily_story_plan"] = story_plan
                self._save_data_sync(
                    sections={
                        "bot_diaries",
                        "diary_generated_day",
                        "dream_fragments",
                        "daily_diary_postprocess_error",
                        "daily_story_plan",
                    },
                )
            outfit_generator = getattr(self, "_ensure_daily_outfit_photo", None)
            if callable(outfit_generator):
                try:
                    await outfit_generator(diary)
                except Exception as exc:
                    logger.warning(
                        "重建今日日记已保存,但每日穿搭照片生成失败: %s",
                        _single_line(exc, 180),
                    )
        return state, plan, diary

    def _parse_private_user_aliases(self, raw: Any) -> dict[str, str]:
        aliases: dict[str, str] = {}
        if isinstance(raw, dict):
            items = raw.items()
        elif isinstance(raw, list):
            items = []
            for item in raw:
                if isinstance(item, dict):
                    alias = str(item.get("alias") or item.get("from") or item.get("source") or "").strip()
                    canonical = str(item.get("canonical") or item.get("to") or item.get("target") or "").strip()
                    if alias and canonical:
                        aliases[alias] = canonical
                    continue
                text = str(item or "").strip()
                if text:
                    items.append((text, ""))
        else:
            text = str(raw or "").strip()
            items = [(line.strip(), "") for line in text.splitlines() if line.strip()]
        for key, value in items:
            alias = str(key or "").strip()
            canonical = str(value or "").strip()
            if not canonical:
                for sep in ("=>", "=", ":", "：", "->"):
                    if sep in alias:
                        left, right = alias.split(sep, 1)
                        alias = left.strip()
                        canonical = right.strip()
                        break
            if alias and canonical and alias != canonical:
                aliases[alias] = canonical
        return aliases

    def _canonical_private_user_id(self, user_id: str) -> str:
        current = str(user_id or "").strip()
        aliases = getattr(self, "private_user_aliases", {}) or {}
        seen: set[str] = set()
        while current and current in aliases and current not in seen:
            seen.add(current)
            current = str(aliases.get(current) or "").strip()
        return current or str(user_id or "").strip()

    def _private_event_identity_context(self, event: Any, subject_id: Any) -> dict[str, str]:
        """Build a bounded platform/account identity for a user record."""
        subject = self._normalize_private_identity_id(subject_id) or _single_line(subject_id, 128)
        platform_getter = getattr(self, "_platform_kind_for_event", None)
        platform = platform_getter(event) if callable(platform_getter) else "generic"
        platform = _single_line(platform, 40).lower() or "generic"
        adapter = _single_line(getattr(event, "adapter_instance_id", ""), 120)
        if not adapter:
            origin = _single_line(getattr(event, "unified_msg_origin", ""), 240)
            adapter = origin.split(":", 1)[0] if ":" in origin else origin
            adapter = _single_line(adapter, 80)
        self_getter = getattr(self, "_event_self_id", None)
        bot_id = ""
        if callable(self_getter):
            try:
                bot_id = self._normalize_private_identity_id(self_getter(event))
            except Exception:
                bot_id = ""
        return {
            "subject": subject,
            "platform": platform,
            "adapter": adapter,
            "bot_id": bot_id,
        }

    def _private_user_matches_event_identity(
        self,
        user: Any,
        context: dict[str, str],
    ) -> bool:
        if not isinstance(user, dict) or not isinstance(context, dict):
            return False
        subject = _single_line(context.get("subject"), 128)
        stored_subject = _single_line(user.get("identity_subject_id"), 128)
        if not stored_subject:
            stored_umo = _single_line(user.get("umo") or user.get("last_inbound_umo"), 240)
            parser = getattr(self, "_private_umo_session_id", None)
            if callable(parser) and stored_umo:
                try:
                    stored_subject = _single_line(parser(stored_umo), 128)
                except Exception:
                    stored_subject = ""
        if subject and stored_subject and subject != stored_subject:
            return False
        platform = _single_line(context.get("platform"), 40).lower()
        stored_platform = _single_line(user.get("identity_platform_kind"), 40).lower()
        if not stored_platform:
            umo = _single_line(user.get("umo") or user.get("last_inbound_umo"), 240)
            platform_parser = getattr(self, "_platform_kind_for_umo", None)
            if callable(platform_parser) and umo:
                try:
                    stored_platform = _single_line(platform_parser(umo), 40).lower()
                except Exception:
                    stored_platform = ""
        if not stored_platform or not platform or stored_platform != platform:
            return False
        for field, stored_field in (
            ("adapter", "identity_adapter_instance_id"),
            ("bot_id", "identity_bot_id"),
        ):
            expected = _single_line(context.get(field), 120)
            actual = _single_line(user.get(stored_field), 120)
            # A stamped profile with a missing account marker is not safe to
            # reuse for a concrete adapter/bot event.  Treat it as legacy and
            # let the resolver create an isolated scoped record instead of
            # silently sharing data between Bot accounts.
            if expected and (not actual or expected != actual):
                return False
        return True

    def _event_private_user_storage_id(self, event: Any, user_id: Any) -> str:
        """Resolve a private/group event to a platform-isolated users key."""
        raw = _single_line(user_id, 160)
        normalized = self._normalize_private_identity_id(raw)
        canonical = self._canonical_private_user_id(normalized or raw)
        if not canonical:
            return ""
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return canonical
        context = self._private_event_identity_context(event, raw)
        # Reuse a previously isolated record for the same exact platform/account.
        for stored_id, candidate in users.items():
            if self._canonical_private_user_id(str(stored_id or "")) == canonical and self._private_user_matches_event_identity(candidate, context):
                return str(stored_id)
            if isinstance(candidate, dict) and _single_line(candidate.get("identity_subject_id"), 128) == context.get("subject") and self._private_user_matches_event_identity(candidate, context):
                return str(stored_id)
        existing = users.get(canonical)
        if not isinstance(existing, dict) or self._private_user_matches_event_identity(existing, context):
            return canonical
        stored_subject = _single_line(existing.get("identity_subject_id"), 128)
        stored_platform = _single_line(existing.get("identity_platform_kind"), 40).lower()
        if not stored_platform:
            stored_umo = _single_line(existing.get("umo") or existing.get("last_inbound_umo"), 240)
            platform_parser = getattr(self, "_platform_kind_for_umo", None)
            if callable(platform_parser) and stored_umo:
                try:
                    inferred_platform = _single_line(platform_parser(stored_umo), 40).lower()
                except Exception:
                    inferred_platform = ""
                if inferred_platform and inferred_platform != "generic":
                    stored_platform = inferred_platform
        # Claim an unversioned legacy record on its first concrete event. The
        # normal profile path stamps the platform/account immediately, so a
        # later same-ID event from another platform is isolated below.
        if not stored_platform and (not stored_subject or stored_subject == context.get("subject")):
            return canonical
        # Older explicitly managed DM profiles may have a real inbound route
        # but no adapter/bot stamps. Claim them only for the same concrete
        # platform and subject; a different platform or any existing account
        # marker still takes the isolated path below.
        observed_platform = _single_line(context.get("platform"), 40).lower()
        stored_adapter = _single_line(existing.get("identity_adapter_instance_id"), 120)
        stored_bot_id = _single_line(existing.get("identity_bot_id"), 120)
        explicitly_managed = bool(
            existing.get("manual_enabled")
            or existing.get("manual_disabled")
            or existing.get("auto_profile_created")
            or _safe_int(existing.get("private_inbound_count") or 0, 0) > 0
            or _safe_float(existing.get("last_private_seen") or 0, 0.0) > 0
        )
        same_subject = not stored_subject or stored_subject == context.get("subject")
        same_platform = bool(
            stored_platform
            and observed_platform
            and stored_platform == observed_platform
        )
        if explicitly_managed and same_subject and same_platform and not stored_adapter and not stored_bot_id:
            return canonical
        # A configured target is allowed to roll over adapter-instance
        # metadata inside its configured platform. Reuse the canonical record
        # so passive and proactive paths do not split into a disabled shadow.
        # Unconfigured identities still take the isolated digest path below.
        try:
            configured_ids = {
                self._canonical_private_user_id(str(item or "").strip())
                for item in self._configured_target_ids()
                if str(item or "").strip()
            }
        except Exception:
            configured_ids = set()
        if canonical in configured_ids:
            configured_raw = _single_line(getattr(self, "target_platform", ""), 80).lower()
            configured_kind = self._normalize_platform_kind(configured_raw) if configured_raw else "generic"
            observed_kind = _single_line(context.get("platform"), 40).lower()
            compatible = True
            if configured_kind != "generic" and observed_kind not in {"", "generic", configured_kind}:
                compatible = False
            elif configured_kind != "generic" and observed_kind == "generic" and configured_raw:
                adapter = _single_line(context.get("adapter"), 120).lower()
                if adapter and configured_raw not in {adapter, adapter.split(":", 1)[0]}:
                    compatible = False
            elif configured_kind == "generic" and configured_raw:
                adapter = _single_line(context.get("adapter"), 120).lower()
                compatible = not adapter or configured_raw in {adapter, adapter.split(":", 1)[0]}
            if compatible:
                return canonical
        # A conflicting platform/account never inherits the existing record.
        digest = hashlib.sha256(
            f"{context.get('platform','generic')}|{context.get('adapter','')}|{context.get('bot_id','')}|{canonical}".encode("utf-8")
        ).hexdigest()[:16]
        return _single_line(f"{context.get('platform','generic')}:{canonical}:{digest}", 160)

    def _stamp_private_event_identity(self, user: dict[str, Any], event: Any, subject_id: Any) -> None:
        if not isinstance(user, dict):
            return
        context = self._private_event_identity_context(event, subject_id)
        user["identity_subject_id"] = context.get("subject", "")
        user["identity_platform_kind"] = context.get("platform", "generic")
        if context.get("adapter"):
            user["identity_adapter_instance_id"] = context["adapter"]
        if context.get("bot_id"):
            user["identity_bot_id"] = context["bot_id"]

    def _private_user_id_for_event(self, event: Any, user_id: Any = None) -> str:
        """Return the storage key for a raw sender in this event's identity scope."""
        raw = user_id
        if raw is None:
            try:
                raw = event.get_sender_id()
            except Exception:
                raw = ""
        raw_text = _single_line(raw, 160)
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        normalized = normalizer(raw_text) if callable(normalizer) else raw_text
        normalized = normalized or raw_text
        resolver = getattr(self, "_event_private_user_storage_id", None)
        if callable(resolver):
            try:
                resolved = resolver(event, normalized)
            except Exception:
                resolved = ""
            if resolved:
                return _single_line(resolved, 160)
            # A resolver failure on a concrete platform/account must not fall
            # back to a bare sender ID, which would re-open the cross-adapter
            # collision this scoped resolver is meant to prevent.  Preserve a
            # deterministic namespace so the event can still be handled and
            # diagnosed without inheriting another profile.
            try:
                context = self._private_event_identity_context(event, normalized)
            except Exception:
                context = {}
            platform = _single_line(context.get("platform"), 40).lower() if isinstance(context, dict) else ""
            adapter = _single_line(context.get("adapter"), 120) if isinstance(context, dict) else ""
            bot_id = _single_line(context.get("bot_id"), 120) if isinstance(context, dict) else ""
            if platform and platform != "generic" and (adapter or bot_id):
                canonical = _single_line(self._canonical_private_user_id(normalized), 128)
                digest = hashlib.sha256(
                    f"{platform}|{adapter}|{bot_id}|{canonical}".encode("utf-8")
                ).hexdigest()[:16]
                return _single_line(f"{platform}:{canonical}:{digest}", 160)
        return _single_line(self._canonical_private_user_id(normalized), 160)

    @staticmethod
    def _normalize_private_identity_id(value: Any, limit: int = 128) -> str:
        if isinstance(value, (dict, list, tuple, set)):
            return ""
        # Parse the transport wrapper before applying the identity length
        # limit. Otherwise a long adapter/platform prefix can truncate the
        # opaque session ID and create a second, colliding user record.
        text = _single_line(value, max(512, limit + 256))
        if not text:
            return ""
        invalid_exact = {
            "default",
            "aiocqhttp",
            "qq_official",
            "weixin_official_account",
            "dingtalk",
            "friendmessage",
            "groupmessage",
            "friend_message",
            "group_message",
            "umo",
            "uid",
            "none",
            "null",
        }
        umo_match = re.search(r":friendmessage:", text, re.IGNORECASE)
        if umo_match:
            session_id = _single_line(text[umo_match.end():], limit)
            if not session_id or ":" in session_id:
                return ""
            session_lower = session_id.lower()
            if session_lower in invalid_exact or re.search(r"(friendmessage|groupmessage|unified_msg_origin)", session_lower):
                return ""
            return session_id
        text = _single_line(text, limit)
        lower = text.lower()
        if lower in invalid_exact:
            return ""
        if ":" in text:
            return ""
        if re.search(r"(friendmessage|groupmessage|unified_msg_origin)", lower):
            return ""
        return text

    @staticmethod
    def _normalize_group_identity_id(value: Any, limit: int = 160) -> str:
        """Normalize a numeric group ID, opaque platform ID, or GroupMessage UMO."""
        if isinstance(value, (dict, list, tuple, set)):
            return ""
        # The platform prefix is not part of the group identity and must not
        # consume the opaque session ID's length budget.
        text = _single_line(value, max(512, limit + 256))
        if not text:
            return ""
        invalid_exact = {
            "default",
            "aiocqhttp",
            "qq_official",
            "groupmessage",
            "group_message",
            "group",
            "friendmessage",
            "friend_message",
            "umo",
            "uid",
            "none",
            "null",
        }
        umo_match = re.search(r":groupmessage:", text, re.IGNORECASE)
        if umo_match:
            text = _single_line(text[umo_match.end():], limit)
            if not text or ":" in text:
                return ""
        else:
            text = _single_line(text, limit)
        lower = text.lower()
        if lower in invalid_exact or ":" in text:
            return ""
        if re.search(r"(friendmessage|groupmessage|unified_msg_origin)", lower):
            return ""
        return text

    @staticmethod
    def _group_merge_list_identity(field: str, item: Any) -> tuple[Any, ...] | None:
        """Return a stable identity for group history entries when one exists."""
        if not isinstance(item, dict):
            try:
                return ("value", json.dumps(item, ensure_ascii=False, sort_keys=True))
            except (TypeError, ValueError):
                return ("value", repr(item))

        if field == "group_episodes":
            episode_id = item.get("id")
            if episode_id not in (None, ""):
                return ("id", str(episode_id))
            return (
                "episode",
                str(item.get("created_ts") or ""),
                str(item.get("date") or ""),
                str(item.get("summary") or ""),
            )

        identity_fields = {
            "topic_threads": ("signature", "topic_id", "id"),
            "slang_terms": ("term", "text", "id"),
            "recent_messages": ("message_id", "id"),
            "recent_bot_replies": ("message_id", "delivery_id", "id"),
            "pending_atrelay_tasks": ("task_id", "id"),
            "group_wakeup_logs": ("trace_id", "id"),
        }.get(field, ("id", "event_id", "trace_id"))
        for key in identity_fields:
            value = item.get(key)
            if value not in (None, ""):
                return (key, str(value))

        if field == "recent_messages":
            return (
                "message",
                str(item.get("ts") or ""),
                str(item.get("sender_id") or ""),
                str(item.get("text") or ""),
            )
        if field == "recent_bot_replies":
            return (
                "bot_message",
                str(item.get("ts") or ""),
                str(item.get("reply_to_id") or item.get("sender_id") or ""),
                str(item.get("kind") or ""),
                str(item.get("text") or ""),
            )
        try:
            return ("value", json.dumps(item, ensure_ascii=False, sort_keys=True))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _group_merge_records_equal(target: dict[str, Any], source: dict[str, Any]) -> bool:
        """Detect copied alias records so counters are not added twice."""
        ignored = {"group_id", "umo", "alias_group_ids", "umo_aliases"}
        target_body = {key: value for key, value in target.items() if key not in ignored}
        source_body = {key: value for key, value in source.items() if key not in ignored}
        return target_body == source_body

    def _merge_group_list_values(self, target: list[Any], source: list[Any], field: str) -> None:
        identities: dict[tuple[Any, ...], int] = {}
        for index, item in enumerate(target):
            identity = self._group_merge_list_identity(field, item)
            if identity is not None:
                identities.setdefault(identity, index)

        for item in source:
            identity = self._group_merge_list_identity(field, item)
            matched_index = identities.get(identity) if identity is not None else None
            if matched_index is None:
                target.append(deepcopy(item))
                if identity is not None:
                    identities[identity] = len(target) - 1
                continue
            existing = target[matched_index]
            if isinstance(existing, dict) and isinstance(item, dict) and existing != item:
                self._merge_group_mapping_values(existing, item)

    def _merge_group_mapping_values(
        self,
        target: dict[str, Any],
        source: dict[str, Any],
        *,
        numeric_values_are_counts: bool = False,
    ) -> None:
        """Recursively merge independently accumulated group observations."""
        for key, value in source.items():
            if key == "group_id":
                continue
            existing = target.get(key)
            if isinstance(value, dict):
                if not isinstance(existing, dict):
                    if existing in (None, "", [], {}):
                        target[key] = deepcopy(value)
                    continue
                if existing != value:
                    self._merge_group_mapping_values(
                        existing,
                        value,
                        numeric_values_are_counts=(
                            key in {"tone", "counts", "counters", "feedback_counts", "reaction_counts"}
                            or key.endswith("_counts")
                        ),
                    )
                continue
            if isinstance(value, list):
                if not isinstance(existing, list):
                    if existing in (None, "", [], {}):
                        target[key] = deepcopy(value)
                    continue
                self._merge_group_list_values(existing, value, key)
                continue
            if isinstance(value, bool):
                if key not in target:
                    target[key] = value
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if numeric_values_are_counts or key == "count" or key.endswith("_count") or key.endswith("_today"):
                    target[key] = _safe_float(existing, 0.0, 0.0) + _safe_float(value, 0.0, 0.0)
                    if isinstance(existing, int) and isinstance(value, int):
                        target[key] = int(target[key])
                elif key == "last_seen" or key.endswith("_at") or key.endswith("_ts"):
                    target[key] = max(_safe_float(existing, 0.0, 0.0), _safe_float(value, 0.0, 0.0))
                elif existing in (None, "", 0):
                    target[key] = deepcopy(value)
                continue
            if existing in (None, "", [], {}):
                target[key] = deepcopy(value)

    def _merge_group_record_values(
        self,
        target: dict[str, Any],
        source: dict[str, Any],
        alias_id: Any,
    ) -> None:
        if target is source:
            return

        copied_alias = self._group_merge_records_equal(target, source)
        target_manual = _single_line(target.get("manual_group_name"), 80)
        source_manual = _single_line(source.get("manual_group_name"), 80)
        target_manual_updated = _safe_float(target.get("manual_group_name_updated_at"), 0.0, 0.0)
        source_manual_updated = _safe_float(source.get("manual_group_name_updated_at"), 0.0, 0.0)
        known_names: list[str] = []
        for candidate in (
            *(target.get("group_name_aliases") if isinstance(target.get("group_name_aliases"), list) else []),
            target_manual,
            source_manual,
            source.get("name"),
            source.get("group_name"),
        ):
            name = _single_line(candidate, 80)
            if name and name not in known_names:
                known_names.append(name)

        if not copied_alias:
            self._merge_group_mapping_values(target, source)

        if source_manual and (not target_manual or source_manual_updated > target_manual_updated):
            target_manual = source_manual
            target["manual_group_name"] = source_manual
            if source_manual_updated:
                target["manual_group_name_updated_at"] = source_manual_updated
        if known_names:
            target["group_name_aliases"] = known_names
        if target_manual:
            target["manual_group_name"] = target_manual
            target["name"] = target_manual
            target["group_name"] = target_manual
            target["group_name_source"] = "manual"

        aliases = target.setdefault("alias_group_ids", [])
        if not isinstance(aliases, list):
            aliases = []
            target["alias_group_ids"] = aliases
        source_aliases = source.get("alias_group_ids") if isinstance(source.get("alias_group_ids"), list) else []
        for candidate in (alias_id, *source_aliases):
            alias = _single_line(candidate, 512)
            if alias and alias not in aliases:
                aliases.append(alias)

        umo_aliases = target.setdefault("umo_aliases", [])
        if not isinstance(umo_aliases, list):
            umo_aliases = []
            target["umo_aliases"] = umo_aliases
        for candidate in (source.get("umo"), alias_id):
            umo = _single_line(candidate, 512)
            if ":groupmessage:" in umo.lower() and umo not in umo_aliases:
                umo_aliases.append(umo)

    def _canonicalize_group_records(self, canonical_id: str) -> dict[str, Any]:
        """Re-key and merge equivalent records in the active persona store only."""
        groups = self.data.setdefault("groups", {})
        if not isinstance(groups, dict):
            groups = {}
            self.data["groups"] = groups

        matches: list[tuple[Any, dict[str, Any]]] = []
        for raw_key, raw_group in list(groups.items()):
            if not isinstance(raw_group, dict):
                continue
            identities = {
                self._normalize_group_identity_id(raw_key),
                self._normalize_group_identity_id(raw_group.get("group_id")),
                self._normalize_group_identity_id(raw_group.get("umo")),
            }
            if canonical_id in identities:
                matches.append((raw_key, raw_group))

        canonical_group = groups.get(canonical_id)
        if not isinstance(canonical_group, dict):
            canonical_group = matches[0][1] if matches else deepcopy(_DEFAULT_GROUP_TEMPLATE)
            groups[canonical_id] = canonical_group

        for raw_key, source in matches:
            if source is not canonical_group:
                self._merge_group_record_values(canonical_group, source, raw_key)
            elif raw_key != canonical_id:
                aliases = canonical_group.setdefault("alias_group_ids", [])
                if not isinstance(aliases, list):
                    aliases = []
                    canonical_group["alias_group_ids"] = aliases
                alias = _single_line(raw_key, 512)
                if alias and alias not in aliases:
                    aliases.append(alias)
            if raw_key != canonical_id:
                groups.pop(raw_key, None)

        canonical_group["group_id"] = canonical_id
        return canonical_group

    def _merge_user_record_values(self, target: dict[str, Any], source: dict[str, Any], alias_id: str) -> None:
        # Alias records can span the v1/v2 score boundary. Normalize both sides
        # before additive fields are combined so their units never mix.
        migration_now = _now_ts()
        migrate_legacy_relationship_score(
            target,
            created=False,
            now=migration_now,
            record_id=target.get("user_id"),
        )
        migrate_legacy_relationship_score(
            source,
            created=False,
            now=migration_now,
            record_id=source.get("user_id") or alias_id,
        )
        additive_keys = {
            "inbound_count",
            "private_inbound_count",
            "reply_count",
            "proactive_sent_count",
            "relationship_score",
            "sent_today",
            "ignored_streak",
            "unanswered_proactive_count",
            "poke_count",
        }
        max_keys = {
            "last_seen",
            "last_sent",
            "last_active_at",
            "last_user_message_at",
            "last_memory_refresh_at",
            "last_episode_refresh_at",
        }
        for key, value in source.items():
            if key == "user_id":
                continue
            if key in additive_keys:
                target[key] = _safe_int(target.get(key), 0) + _safe_int(value, 0)
            elif key == "req041_relationship_source_revision":
                target[key] = max(_safe_int(target.get(key), 0), _safe_int(value, 0))
            elif key in max_keys or key.endswith("_at") or key.endswith("_ts"):
                target[key] = max(_safe_float(target.get(key), 0), _safe_float(value, 0))
            elif isinstance(value, list):
                existing = target.get(key)
                if not isinstance(existing, list):
                    existing = []
                    target[key] = existing
                for item in value:
                    if item not in existing:
                        existing.append(deepcopy(item))
            elif isinstance(value, dict):
                existing = target.get(key)
                if not isinstance(existing, dict):
                    existing = {}
                    target[key] = existing
                for sub_key, sub_value in value.items():
                    if sub_key not in existing or existing.get(sub_key) in (None, "", [], {}):
                        existing[sub_key] = deepcopy(sub_value)
            elif target.get(key) in (None, "", [], {}):
                target[key] = deepcopy(value)
        aliases = target.setdefault("alias_user_ids", [])
        if not isinstance(aliases, list):
            aliases = []
            target["alias_user_ids"] = aliases
        for alias in [alias_id, *(source.get("alias_user_ids") if isinstance(source.get("alias_user_ids"), list) else [])]:
            alias_text = str(alias or "").strip()
            if alias_text and alias_text not in aliases:
                aliases.append(alias_text)
