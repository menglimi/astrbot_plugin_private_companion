# -*- coding: utf-8 -*-
"""WorldbookPart01Mixin。

由 tools/split_mixin_domain.py 从 worldbook.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 486 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 WorldbookMixin）。
"""
from __future__ import annotations

from .worldbook_shared import logger
from .worldbook_shared import Any
from .worldbook_shared import Path
from .worldbook_shared import _now_ts
from .worldbook_shared import _safe_int
from .worldbook_shared import _single_line
from .worldbook_shared import get_astrbot_data_path
from .worldbook_shared import json
from .worldbook_shared import re
from .worldbook_shared import runtime_persona_setting
from .worldbook_shared import unicodedata



class WorldbookPart01Mixin:
    """WorldbookPart01Mixin（从 WorldbookMixin 拆出）。"""


    def _worldbook_config_path_candidates(self) -> list[Path]:
        data_root = Path(get_astrbot_data_path())
        configured = [
            Path(part.strip())
            for part in re.split(
                r"[\n,;；]+",
                str(runtime_persona_setting(self, "worldbook_config_paths", "") or ""),
            )
            if part.strip()
        ]
        defaults = [
            data_root / "config" / "plugin_upload_astrbot_plugin_worldbook_config.json",
            data_root / "config" / "astrbot_plugin_worldbook_config.json",
        ]
        paths: list[Path] = []
        for path in [*configured, *defaults]:
            resolved = path if path.is_absolute() else data_root / path
            if resolved not in paths:
                paths.append(resolved)
        return paths

    @staticmethod
    def _worldbook_entry_template(raw: dict[str, Any]) -> str:
        return str(raw.get("__template_key") or raw.get("template") or "").strip().lower()

    def _normalize_worldbook_entry(self, raw: dict[str, Any], *, source: str) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        name = _single_line(raw.get("name"), 80)
        content = str(raw.get("content") or "").strip()
        if not name and not content:
            return None
        template = self._worldbook_entry_template(raw)
        scope_limit = 160 if template == "group" else 40
        scope = (
            [_single_line(item, scope_limit).strip() for item in raw.get("scope", []) if _single_line(item, scope_limit).strip()]
            if isinstance(raw.get("scope"), list)
            else []
        )
        aliases = (
            [_single_line(item, 40).strip() for item in raw.get("aliases", []) if _single_line(item, 40).strip()]
            if isinstance(raw.get("aliases"), list)
            else []
        )
        gender = _single_line(raw.get("gender") or raw.get("性别"), 40)
        return {
            "template": template,
            "name": name,
            "gender": gender,
            "enabled": bool(raw.get("enabled", True)),
            "priority": _safe_int(raw.get("priority"), 100, -1000, 10000),
            "scope": scope,
            "keywords": [_single_line(item, 80) for item in raw.get("keywords", []) if _single_line(item, 80)]
            if isinstance(raw.get("keywords"), list)
            else [],
            "aliases": aliases,
            "content": content,
            "source": source,
            "raw": raw,
        }

    def _import_worldbook_entries_from_sources(self) -> bool:
        entries: list[dict[str, Any]] = []
        source_files: list[str] = []
        deleted_member_ids = {
            str(item).strip()
            for item in self.data.get("worldbook_deleted_member_ids", [])
            if str(item).strip()
        } if isinstance(self.data.get("worldbook_deleted_member_ids"), list) else set()
        deleted_group_ids = {
            str(item).strip()
            for item in self.data.get("worldbook_deleted_group_ids", [])
            if str(item).strip()
        } if isinstance(self.data.get("worldbook_deleted_group_ids"), list) else set()
        for path in self._worldbook_config_path_candidates():
            if not path.exists() or not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception as e:
                logger.warning(f"读取关系网配置失败: {path} ({e})")
                continue
            raw_entries = payload.get("entry_storage") if isinstance(payload, dict) else None
            if not isinstance(raw_entries, list):
                continue
            source_files.append(str(path))
            for raw in raw_entries:
                item = self._normalize_worldbook_entry(raw, source=str(path))
                if item:
                    entries.append(item)
        if not entries:
            return False

        profiles: dict[str, dict[str, Any]] = {}
        groups: dict[str, dict[str, Any]] = {}
        for item in entries:
            template = item.get("template")
            if template == "user":
                digit_scopes = [str(scope).strip() for scope in item.get("scope", []) if str(scope).strip().isdigit()]
                for user_id in digit_scopes:
                    if user_id in deleted_member_ids:
                        continue
                    profile = profiles.setdefault(
                        user_id,
                        {
                            "user_id": user_id,
                            "name": item.get("name") or user_id,
                            "gender": "",
                            "aliases": [],
                            "content": "",
                            "identity_note": "",
                            "boundary_note": "",
                            "important_memories": [],
                            "enabled": bool(item.get("enabled", True)),
                            "priority": item.get("priority", 120),
                            "source_entries": [],
                            "observed_names": [],
                        },
                    )
                    for alias in [item.get("name"), *(item.get("aliases") or [])]:
                        alias = _single_line(alias, 40)
                        if alias and alias not in profile["aliases"] and alias != user_id:
                            profile["aliases"].append(alias)
                    content = _single_line(item.get("content"), 1200)
                    if content and content not in profile.get("content", ""):
                        profile["content"] = "\n".join(part for part in (profile.get("content"), content) if part).strip()
                    if content and not profile.get("note"):
                        profile["note"] = content
                    if content and not profile.get("identity_note"):
                        profile["identity_note"] = content
                    if item.get("gender") and not profile.get("gender"):
                        profile["gender"] = item.get("gender")
                    profile["enabled"] = bool(profile.get("enabled", True) and item.get("enabled", True))
                    profile["source_entries"].append(item.get("name") or user_id)
            elif template == "group":
                group_scopes = []
                for scope in item.get("scope", []):
                    group_id = self._normalize_group_identity_id(scope)
                    if group_id and group_id not in group_scopes:
                        group_scopes.append(group_id)
                for group_id in group_scopes:
                    if group_id in deleted_group_ids:
                        continue
                    groups[group_id] = {
                        "group_id": group_id,
                        "name": item.get("name") or group_id,
                        "content": item.get("content") or "",
                        "enabled": bool(item.get("enabled", True)),
                        "priority": item.get("priority", 110),
                        "aliases": item.get("aliases") or [],
                        "source_entries": [item.get("name") or group_id],
                    }

        old_profiles = self.data.get("worldbook_member_profiles") if isinstance(self.data.get("worldbook_member_profiles"), dict) else {}
        for user_id, profile in profiles.items():
            old = old_profiles.get(user_id) if isinstance(old_profiles.get(user_id), dict) else {}
            if old.get("manual_edit_ts"):
                for key in ("name", "gender", "aliases", "content", "identity_note", "boundary_note", "important_memories", "enabled", "priority", "note", "manual_edit_ts"):
                    if key in old:
                        profile[key] = old[key]
            profile["gender"] = _single_line(profile.get("gender"), 40)
            observed = old.get("observed_names") if isinstance(old.get("observed_names"), list) else []
            profile["observed_names"] = [_single_line(item, 40) for item in observed if _single_line(item, 40)]
            old_note = str(old.get("note") or "").strip()
            if old_note:
                profile["note"] = old_note
            elif not profile.get("note"):
                profile["note"] = profile.get("content", "")
            if not profile.get("identity_note"):
                profile["identity_note"] = profile.get("note") or profile.get("content", "")
            if not isinstance(profile.get("important_memories"), list):
                profile["important_memories"] = []
        for user_id, old in old_profiles.items():
            if user_id in profiles or not isinstance(old, dict):
                continue
            if old.get("manual_edit_ts") or "手动维护" in (old.get("source_entries") or []):
                profiles[user_id] = old

        old_groups = self.data.get("worldbook_group_profiles") if isinstance(self.data.get("worldbook_group_profiles"), dict) else {}
        for group_id, group in list(groups.items()):
            old = old_groups.get(group_id) if isinstance(old_groups.get(group_id), dict) else {}
            if old.get("manual_edit_ts"):
                for key in ("name", "content", "enabled", "priority", "aliases", "manual_edit_ts"):
                    if key in old:
                        group[key] = old[key]
        for group_id, old in old_groups.items():
            if group_id in groups or not isinstance(old, dict):
                continue
            if old.get("manual_edit_ts") or "手动维护" in (old.get("source_entries") or []):
                groups[group_id] = old

        changed = (
            self.data.get("worldbook_entries") != entries
            or self.data.get("worldbook_member_profiles") != profiles
            or self.data.get("worldbook_group_profiles") != groups
        )
        self.data["worldbook_entries"] = entries
        self.data["worldbook_member_profiles"] = profiles
        self.data["worldbook_group_profiles"] = groups
        self.data["worldbook_import_state"] = {
            "last_import_at": _now_ts(),
            "source_files": source_files,
            "entry_count": len(entries),
            "member_count": len(profiles),
            "group_count": len(groups),
        }
        return changed

    def _remember_worldbook_observed_name(self, user_id: str, name: str) -> None:
        if not runtime_persona_setting(self, "enable_worldbook_member_recognition", True):
            return
        user_id = str(user_id or "").strip()
        name = _single_line(name, 40)
        if not user_id or not name or name == user_id:
            return
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return
        profile = profiles.get(user_id)
        if not isinstance(profile, dict):
            return
        if bool(profile.get("observation_only")):
            # Group cards belong to their group scope and must not become a
            # global alias that can be injected into another conversation.
            return
        observed = profile.setdefault("observed_names", [])
        if not isinstance(observed, list):
            observed = []
            profile["observed_names"] = observed
        if name not in observed and name not in profile.get("aliases", []):
            observed.append(name)
            del observed[:-8]

    def _ensure_worldbook_group_observation_profile(
        self,
        *,
        group_id: str,
        sender_id: str,
        qq_nickname: str = "",
        group_card: str = "",
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Create the non-authoritative role card for an observed group speaker."""
        sender_id = _single_line(sender_id, 40)
        group_id = _single_line(group_id, 80)
        if not sender_id or not group_id:
            return None
        qq_nickname = _single_line(qq_nickname, 40)
        group_card = _single_line(group_card, 40)
        display_name = qq_nickname or group_card or sender_id
        now = float(now or _now_ts())
        profiles = self.data.setdefault("worldbook_member_profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            self.data["worldbook_member_profiles"] = profiles
        profile = profiles.get(sender_id)
        if not isinstance(profile, dict):
            profile = {
                "user_id": sender_id,
                "identity_type": "qq",
                "name": display_name,
                "gender": "",
                "aliases": [],
                "observed_names": [],
                "content": "白名单群内实际发言自动建立的仅观察角色档案。",
                "identity_note": f"QQ {sender_id}，仅观察角色档案。",
                "boundary_note": "仅用于当前群观察和关系展示；不用于私聊、主动触达、跨群记忆、P4 计分或黑屋。",
                "important_memories": [],
                "enabled": True,
                "priority": 0,
                "source_entries": ["白名单群观察"],
                "profile_origin": "group_observation",
                "projection_kind": "group_observation",
                "observation_only": True,
                "proactive_contact_enabled": False,
                "private_memory_enabled": False,
                "cross_group_memory_enabled": False,
                "p4_eligible": False,
                "auto_registered_ts": now,
            }
            profiles[sender_id] = profile
        if not bool(profile.get("observation_only")):
            # An explicit profile keeps its own identity and permissions. We
            # only retain the group-scoped alias below.
            return profile
        profile["user_id"] = sender_id
        profile["proactive_contact_enabled"] = False
        profile["private_memory_enabled"] = False
        profile["cross_group_memory_enabled"] = False
        profile["p4_eligible"] = False
        # New group observation records retain only group-local aliases and
        # observations.  They never become a second personal relationship
        # ledger; old compatibility fields may remain in existing records.
        profile.setdefault("profile_origin", "group_observation")
        profile.setdefault("projection_kind", "group_observation")
        profile.setdefault("identity_type", "qq")
        profile.setdefault("source_entries", ["白名单群观察"])
        profile.setdefault("observation_only", True)
        aliases_by_group = profile.setdefault("group_aliases", {})
        if not isinstance(aliases_by_group, dict):
            aliases_by_group = {}
            profile["group_aliases"] = aliases_by_group
        aliases = aliases_by_group.setdefault(group_id, [])
        if not isinstance(aliases, list):
            aliases = []
            aliases_by_group[group_id] = aliases
        if group_card and group_card not in aliases and group_card != profile.get("name"):
            aliases.append(group_card)
            del aliases[:-8]
        scopes = profile.setdefault("group_observation_scope_ids", [])
        if not isinstance(scopes, list):
            scopes = []
            profile["group_observation_scope_ids"] = scopes
        if group_id not in scopes:
            scopes.append(group_id)
            del scopes[:-32]
        profile["last_observed_at"] = now
        return profile

    def _worldbook_group_observation_profile_by_identity(self, user_id: str) -> tuple[str, dict[str, Any]] | None:
        """Return the exact, observation-only card for a future private identity merge.

        This is deliberately an exact identity lookup rather than a nickname or
        alias match.  A group nickname is scoped evidence only and must never
        turn into an implicit private-chat identity link.
        """
        identity = _single_line(user_id, 40)
        if not identity:
            return None
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return None
        direct = profiles.get(identity)
        if isinstance(direct, dict) and bool(direct.get("observation_only")):
            return identity, direct
        for profile_id, profile in profiles.items():
            if not isinstance(profile, dict) or not bool(profile.get("observation_only")):
                continue
            profile_identity = _single_line(profile.get("user_id") or profile_id, 40)
            if profile_identity == identity:
                return str(profile_id), profile
        return None

    def _confirm_worldbook_observation_profile_name(
        self,
        profile: dict[str, Any],
        *,
        sender_id: str,
        name: str,
        aliases: list[str],
    ) -> bool:
        """Apply an explicit @Bot name confirmation without granting authority."""
        if not isinstance(profile, dict) or not bool(profile.get("observation_only")):
            return False
        sender_id = _single_line(sender_id, 40)
        name = _single_line(name, 40)
        if not sender_id or not name:
            return False
        previous_name = _single_line(profile.get("name"), 40)
        existing_aliases = profile.get("aliases") if isinstance(profile.get("aliases"), list) else []
        merged_aliases = [
            _single_line(item, 40)
            for item in [*existing_aliases, previous_name, *aliases]
            if _single_line(item, 40) and _single_line(item, 40) != name and _single_line(item, 40) != sender_id
        ]
        profile["name"] = name
        profile["aliases"] = list(dict.fromkeys(merged_aliases))[:8]
        profile["name_source"] = "self_registration"
        profile["name_confirmed_at"] = _now_ts()
        profile["proactive_contact_enabled"] = False
        profile["private_memory_enabled"] = False
        profile["cross_group_memory_enabled"] = False
        profile["p4_eligible"] = False
        return True

    def _worldbook_profile_by_user_id(
        self,
        user_id: str,
        *,
        include_observation: bool = False,
    ) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_worldbook_member_recognition", True):
            return None
        user_id = str(user_id or "").strip()
        if not user_id:
            return None
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return None
        profile = profiles.get(user_id)
        if isinstance(profile, dict) and profile.get("enabled", True) and (
            include_observation or not profile.get("observation_only")
        ):
            view = dict(profile)
            view["user_id"] = _single_line(profile.get("linked_qq_user_id") or profile.get("user_id") or user_id, 40)
            return view
        for profile_key, item in profiles.items():
            if (
                not isinstance(item, dict)
                or not item.get("enabled", True)
                or (item.get("observation_only") and not include_observation)
            ):
                continue
            linked_id = _single_line(item.get("linked_qq_user_id") or item.get("user_id") or profile_key, 40)
            if linked_id == user_id:
                view = dict(item)
                view["user_id"] = linked_id
                return view
        return None

    @staticmethod
    def _group_address_token_key(value: Any) -> str:
        text = unicodedata.normalize("NFKC", _single_line(value, 40)).casefold()
        return re.sub(r"[\s「」『』“”\"'`\[\]()（）<>《》:：,，.。!！?？_-]+", "", text)

    def _group_display_name_address_conflict(
        self,
        user_id: str,
        display_name: str,
    ) -> bool:
        """Whether a non-target member's display name looks like a protected relationship address."""
        uid = _single_line(user_id, 40)
        display_key = self._group_address_token_key(display_name)
        if not uid or not display_key:
            return False
        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
        current_user = users.get(uid) if isinstance(users, dict) else None
        target_checker = getattr(self, "_is_target_private_user", None)
        if callable(target_checker):
            try:
                if target_checker(uid, current_user if isinstance(current_user, dict) else None):
                    return False
            except Exception:
                pass

        protected = {
            "主人", "主用户", "主要用户", "目标用户", "次要用户",
            "老公", "老婆", "男友", "女友", "男朋友", "女朋友", "恋人", "对象", "宝贝",
            "爸", "爸爸", "父亲", "妈", "妈妈", "母亲", "爹", "爷", "爷爷", "奶奶", "祖宗",
            "群主", "管理员", "管理", "号主", "官方", "客服", "系统", "开发者", "作者",
            "插件作者", "超级用户", "root", "admin",
        }
        protected_getter = getattr(self, "_protected_owner_nickname_tokens", None)
        if callable(protected_getter):
            try:
                protected.update(protected_getter() or set())
            except Exception:
                pass
        protected_keys = {
            self._group_address_token_key(item)
            for item in protected
            if self._group_address_token_key(item)
        }
        return display_key in protected_keys

    def _group_member_identity_name(self, user_id: str, fallback: str = "", *, limit: int = 30) -> str:
        profile = self._worldbook_profile_by_user_id(user_id, include_observation=True)
        if isinstance(profile, dict):
            name = _single_line(profile.get("name"), limit)
            if (
                name
                and name != str(user_id or "")
                and not self._group_display_name_address_conflict(user_id, name)
            ):
                return name
        if self._group_display_name_address_conflict(user_id, fallback):
            return "群成员"
        return _single_line(fallback, limit) or str(user_id or "") or "群友"

    def _group_member_identity_label(self, user_id: str, fallback: str = "", *, limit: int = 24) -> str:
        uid = _single_line(user_id, 40)
        name = self._group_member_identity_name(uid, fallback, limit=limit)
        if not uid:
            return name
        if not name or name == uid:
            return f"QQ:{uid}"
        return f"{name}[QQ:{uid}]"

    def _group_member_identity_anchor_note(self, user_id: str, display_name: str = "", *, limit: int = 120) -> str:
        uid = _single_line(user_id, 40)
        if not uid:
            return ""
        display = _single_line(display_name, 40)
        stable_name = self._group_member_identity_name(uid, display, limit=30)
        if not display or display == uid or display == stable_name:
            return ""
        return f"{stable_name}[QQ:{uid}] 现在显示成“{display}”，这是群名片/临时显示名。"

    def _format_display_name_rename_events(self, events: Any, *, limit: int = 3) -> str:
        if not isinstance(events, list):
            return ""
        lines = []
        for item in events[-max(1, limit):]:
            if not isinstance(item, dict):
                continue
            old = _single_line(item.get("old"), 24)
            new = _single_line(item.get("new"), 24)
            if old and new and old != new:
                lines.append(f"{old} -> {new}")
        return "；".join(lines)
