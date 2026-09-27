# -*- coding: utf-8 -*-
"""世界书 / 外部成员绑定 域页面 API。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（17 个方法 / 931 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。

"""
from __future__ import annotations

import asyncio
import time
import re
import sqlite3
from copy import copy, deepcopy
from typing import Any, Mapping
from quart import send_file
from .page_api_shared import _page_api_host, _page_api_host_request as request
from .constants import (
    DEFAULT_DAILY_PLAN_ITEMS,
    PAGE_FONT_NAMES,
    PAGE_THEME_NAMES,
    WORLDBOOK_IMPORTANT_MEMORY_CAPACITY,
    WORLDBOOK_PENDING_OBSERVATION_CAPACITY,
    _REASON_TEXT,
)
from .helpers import _MISSING, _flat_get, _normalize_timezone_name, _normalize_timezone_setting, _path_text, _redact_outbound_secrets, _safe_int, _set_into_config, _strip_internal_message_blocks, _text_looks_garbled, _text_similarity, _today_key, normalize_bot_relationship_cards
from .persona_config import runtime_persona_setting
from .reference_assets import (
    REFERENCE_ASSET_MAX_BYTES,
    REFERENCE_ASSET_MAX_PER_OWNER,
    REFERENCE_ASSET_MAX_TOTAL,
    REFERENCE_ASSET_ROLES,
    normalize_reference_asset,
    normalize_reference_asset_scope,
    normalize_reference_owner_id,
)
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


class PrivateCompanionPageApiWorldbookMixin:
    """世界书 / 外部成员绑定 域（从 PrivateCompanionPageApi 拆出）。"""

    def _auto_import_worldbook_if_needed_locked(self) -> None:
        if not bool(runtime_persona_setting(self.plugin, "worldbook_auto_import", False)):
            return
        if not bool(runtime_persona_setting(self.plugin, "enable_worldbook_member_recognition", False)):
            return
        data = getattr(self.plugin, "data", {})
        if not isinstance(data, dict):
            return
        has_imported = bool(data.get("worldbook_entries")) or bool(data.get("worldbook_member_profiles")) or bool(data.get("worldbook_group_profiles"))
        if has_imported:
            return
        importer = getattr(self.plugin, "_import_worldbook_entries_from_sources", None)
        if not callable(importer):
            return
        if importer():
            self.plugin._save_data_sync(
                sections={
                    "worldbook_entries",
                    "worldbook_member_profiles",
                    "worldbook_group_profiles",
                    "worldbook_import_state",
                    "worldbook_deleted_member_ids",
                    "worldbook_deleted_group_ids",
                }
            )
    async def import_worldbook(self) -> dict[str, Any]:
        try:
            async with self.plugin._data_lock:
                changed = bool(self.plugin._import_worldbook_entries_from_sources())
                self.plugin._save_data_sync(
                    sections={
                        "worldbook_entries",
                        "worldbook_member_profiles",
                        "worldbook_group_profiles",
                        "worldbook_import_state",
                        "worldbook_deleted_member_ids",
                        "worldbook_deleted_group_ids",
                    }
                )
                data = deepcopy(self.plugin.data)
            return self._ok({"changed": changed, "worldbook": self._worldbook_summary(data)})
        except Exception as exc:
            logger.error(f"导入世界书失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def update_worldbook_member(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        raw_user_id = self._single_line(payload.get("user_id"), 80)
        user_id = self._normalize_worldbook_member_id(raw_user_id)
        if not user_id:
            return self._error("缺少 user_id")
        try:
            async with self.plugin._data_lock:
                profiles = self.plugin.data.setdefault("worldbook_member_profiles", {})
                if not isinstance(profiles, dict):
                    profiles = {}
                    self.plugin.data["worldbook_member_profiles"] = profiles
                if payload.get("delete"):
                    deleted = self.plugin.data.setdefault("worldbook_deleted_member_ids", [])
                    if not isinstance(deleted, list):
                        deleted = []
                        self.plugin.data["worldbook_deleted_member_ids"] = deleted
                    if user_id not in deleted:
                        deleted.append(user_id)
                    changed = profiles.pop(user_id, None) is not None
                    assets = self.plugin.data.get("photo_reference_assets")
                    if isinstance(assets, list):
                        self.plugin.data["photo_reference_assets"] = [
                            asset for asset in assets
                            if not (
                                isinstance(asset, dict)
                                and asset.get("scope") == "relation_user"
                                and str(asset.get("owner_id") or "") == user_id
                            )
                        ]
                    self.plugin._save_data_sync(
                        sections={
                            "worldbook_member_profiles",
                            "worldbook_deleted_member_ids",
                            "photo_reference_assets",
                        }
                    )
                    data = deepcopy(self.plugin.data)
                    return self._ok({"changed": changed, "message": "已删除关系节点", "worldbook": self._worldbook_summary(data)})
                if not self._worldbook_member_id_valid(
                    user_id,
                    allow_opaque=self._worldbook_known_opaque_member_id(user_id),
                ):
                    return self._error("关系节点必须使用有效 QQ 号、平台身份 ID 或 B 站外部身份键")
                linked_qq_user_id = self._single_line(
                    payload.get("linked_qq_user_id") or payload.get("bound_qq_user_id") or payload.get("qq_user_id"),
                    40,
                )
                if linked_qq_user_id and (not linked_qq_user_id.isdigit() or len(linked_qq_user_id) < 5):
                    return self._error("绑定目标必须是有效 QQ 号")
                deleted = self.plugin.data.setdefault("worldbook_deleted_member_ids", [])
                if isinstance(deleted, list) and user_id in deleted:
                    self.plugin.data["worldbook_deleted_member_ids"] = [item for item in deleted if str(item) != user_id]
                profile = profiles.get(user_id)
                if not isinstance(profile, dict):
                    profile = {
                        "user_id": user_id,
                        "name": user_id,
                        "gender": "",
                        "aliases": [],
                        "content": "",
                        "identity_note": "",
                        "boundary_note": "",
                        "important_memories": [],
                        "enabled": True,
                        "priority": 120,
                        "source_entries": ["手动维护"],
                        "observed_names": [],
                    }
                    profiles[user_id] = profile
                profile["user_id"] = user_id
                profile["identity_type"] = "qq" if user_id.isdigit() else "external"
                if "enabled" in payload:
                    profile["enabled"] = bool(payload.get("enabled"))
                if "name" in payload:
                    profile["name"] = self._single_line(payload.get("name"), 80) or user_id
                if "gender" in payload:
                    profile["gender"] = self._single_line(payload.get("gender"), 40)
                if "aliases" in payload and isinstance(payload.get("aliases"), list):
                    profile["aliases"] = [self._single_line(item, 40) for item in payload.get("aliases", []) if self._single_line(item, 40)]
                if "content" in payload:
                    profile["content"] = str(payload.get("content") or "").strip()[:2000]
                if "note" in payload:
                    profile["note"] = str(payload.get("note") or "").strip()[:2000]
                if "identity_note" in payload:
                    profile["identity_note"] = str(payload.get("identity_note") or "").strip()[:2000]
                if "boundary_note" in payload:
                    profile["boundary_note"] = str(payload.get("boundary_note") or "").strip()[:1200]
                if "important_memories" in payload:
                    profile["important_memories"] = self._normalize_important_memories(payload.get("important_memories"))
                if "accept_pending_observation_id" in payload or "reject_pending_observation_id" in payload:
                    pending = profile.get("pending_observations") if isinstance(profile.get("pending_observations"), list) else []
                    target_id = self._single_line(
                        payload.get("accept_pending_observation_id") or payload.get("reject_pending_observation_id"),
                        40,
                    )
                    kept = []
                    accepted = None
                    for item in pending:
                        if not isinstance(item, dict):
                            continue
                        if self._single_line(item.get("id"), 40) == target_id:
                            accepted = item
                            continue
                        kept.append(item)
                    profile["pending_observations"] = kept[
                        :WORLDBOOK_PENDING_OBSERVATION_CAPACITY
                    ]
                    if accepted and payload.get("accept_pending_observation_id"):
                        memories = self._normalize_important_memories(profile.get("important_memories"))
                        memories.insert(
                            0,
                            {
                                "title": self._single_line(accepted.get("title"), 60) or "群聊观察",
                                "content": str(accepted.get("content") or accepted.get("evidence") or "").strip()[:500],
                                "weight": self._clamp_int(accepted.get("weight"), 35, 0, 100),
                                 "privacy": "internal",
                                 "source": self._single_line(accepted.get("source"), 40) or "group_observation",
                                 "import_batch_id": self._single_line(accepted.get("import_batch_id"), 120),
                                 "source_observation_id": self._single_line(accepted.get("id"), 120),
                                 "enabled": True,
                                "updated_at": time.time(),
                            },
                        )
                        profile["important_memories"] = self._normalize_important_memories(memories)
                if "priority" in payload:
                    profile["priority"] = self._clamp_int(payload.get("priority"), 120, -1000, 10000)
                profile["manual_edit_ts"] = time.time()
                bind_result: dict[str, Any] | None = None
                if linked_qq_user_id and linked_qq_user_id != user_id:
                    bind_result = self._bind_worldbook_external_member_locked(profiles, user_id, linked_qq_user_id, profile)
                self.plugin._save_data_sync(
                    sections={
                        "worldbook_member_profiles",
                        "worldbook_deleted_member_ids",
                    }
                )
                data = deepcopy(self.plugin.data)
            if payload.keys() <= {"user_id", "enabled"}:
                message = "已更新关系节点状态"
            elif "important_memories" in payload and len(payload) <= 2:
                message = "已更新重要记忆"
            elif bind_result:
                message = "已绑定到 QQ 关系节点"
            else:
                message = "已保存关系节点"
            response = {"message": message, "worldbook": self._worldbook_summary(data)}
            if bind_result:
                response["bind"] = bind_result
            return self._ok(response)
        except Exception as exc:
            logger.error(f"更新关系节点失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def get_worldbook_member_livingmemory(self) -> dict[str, Any]:
        user_id = self._normalize_worldbook_member_id(self._single_line(request.args.get("user_id"), 80))
        if not user_id:
            return self._error("缺少 user_id")
        limit = self._query_int("limit", 20, 1, 60)
        try:
            async with self.plugin._data_lock:
                profiles = self.plugin.data.get("worldbook_member_profiles") if isinstance(self.plugin.data.get("worldbook_member_profiles"), dict) else {}
                profile = profiles.get(user_id)
                if not isinstance(profile, dict):
                    return self._error("没有找到对应关系节点")
                profile_copy = deepcopy(profile)
            token_bundle = self._worldbook_member_livingmemory_tokens(user_id, profile_copy)
            tokens = token_bundle.get("tokens", [])
            memory_status = self._livingmemory_summary()
            if memory_status.get("memory_companion_active"):
                source_label = self._single_line(memory_status.get("memory_companion_display_name"), 80) or "我会牢牢记住你"
                summary_reader = getattr(self.plugin, "_memory_companion_read_user_memory_summary", None)
                if not callable(summary_reader):
                    return self._ok(
                        {
                            "available": False,
                            "source_type": "memory_companion",
                            "source_label": source_label,
                            "user_id": user_id,
                            "items": [],
                            "total": 0,
                            "message": f"{source_label} 当前版本不支持关系页摘要",
                        }
                    )
                summary_result: dict[str, Any] = {}
                matched_identity = ""
                for identity in token_bundle.get("primary_tokens", []):
                    candidate = summary_reader(identity, limit=min(5, limit))
                    if asyncio.iscoroutine(candidate) or hasattr(candidate, "__await__"):
                        candidate = await candidate
                    if isinstance(candidate, dict):
                        summary_result = candidate
                    if summary_result.get("available") is True:
                        matched_identity = identity
                        break
                if summary_result.get("available") is not True:
                    reason_messages = {
                        "private_identity_untrusted": "该关系节点尚未绑定可信私聊身份",
                        "group_observation_forbidden": "群聊观察节点不能读取私聊长期记忆",
                        "private_memory_disabled": "该用户已关闭私聊长期记忆",
                        "private_session_mismatch": "关系节点与私聊会话身份不一致",
                        "requester_context_required": "该用户缺少可验证的 Bot 与私聊会话身份",
                        "requester_context_method_unavailable": f"{source_label} 当前版本不支持受控摘要上下文",
                        "requester_platform_missing": "该关系节点缺少可验证的平台身份",
                        "requester_bot_id_missing": "该关系节点缺少可验证的 Bot 身份",
                        "requester_capability_unavailable": f"{source_label} 未能验证当前陪伴插件实例",
                        "requester_context_unavailable": "该关系节点未能建立受控的私聊记忆上下文",
                        "requester_identity_mismatch": "关系节点与记忆中的用户身份不一致",
                        "requester_session_mismatch": "关系节点与记忆中的私聊会话不一致",
                        "summary_method_unavailable": f"{source_label} 当前版本不支持用户记忆摘要",
                        "bridge_unavailable": f"{source_label} 桥接当前不可用",
                    }
                    reason_code = self._single_line(summary_result.get("reason_code"), 80)
                    return self._ok(
                        {
                            "available": False,
                            "source_type": "memory_companion",
                            "source_label": source_label,
                            "user_id": user_id,
                            "items": [],
                            "total": 0,
                            "reason_code": reason_code,
                            "message": f"{reason_messages.get(reason_code, f'暂时无法从 {source_label} 读取该成员的记忆摘要')}（原因：{reason_code or 'unknown'}）",
                        }
                    )
                counts = summary_result.get("counts") if isinstance(summary_result.get("counts"), dict) else {}
                summaries = summary_result.get("summaries") if isinstance(summary_result.get("summaries"), dict) else {}
                category_labels = {
                    "profile": "用户画像",
                    "preference": "偏好",
                    "relationship": "关系",
                    "private_chat": "私聊连续性",
                }
                items: list[dict[str, Any]] = []
                total = 0
                for category, category_label in category_labels.items():
                    count = self._clamp_int(counts.get(category), 0, 0, 1_000_000)
                    total += count
                    summary = self._single_line(summaries.get(category), 220)
                    if count <= 0 and not summary:
                        continue
                    preview = summary or f"已记录 {count} 条；详细内容由 {source_label} 按权限保护。"
                    items.append(
                        {
                            "source": "memory_companion",
                            "source_label": source_label,
                            "id": f"summary:{matched_identity}:{category}",
                            "category": category,
                            "category_label": category_label,
                            "count": count,
                            "preview": preview,
                            "content": preview,
                        }
                    )
                return self._ok(
                    {
                        "available": True,
                        "source_type": "memory_companion",
                        "source_label": source_label,
                        "user_id": user_id,
                        "matched_identity": matched_identity,
                        "items": items,
                        "total": total,
                        "filter_note": f"按绑定的私聊身份读取 {source_label} 提供的脱敏分类摘要；详细记忆仍由记忆插件管理。",
                        "message": f"已从 {source_label} 读取 {total} 条相关记忆摘要",
                    }
                )
            db_path = self._livingmemory_db_path()
            if not db_path:
                return self._ok(
                    {
                        "available": False,
                        "source_type": "livingmemory",
                        "source_label": "LivingMemory",
                        "user_id": user_id,
                        "tokens": tokens,
                        "items": [],
                        "total": 0,
                        "message": "未找到 LivingMemory 数据库",
                    }
                )
            items = await asyncio.to_thread(self._query_livingmemory_for_tokens, db_path, token_bundle, limit)
            return self._ok(
                {
                    "available": True,
                    "source_type": "livingmemory",
                    "source_label": "LivingMemory",
                    "user_id": user_id,
                    "tokens": tokens,
                    "primary_tokens": token_bundle.get("primary_tokens", []),
                    "support_tokens": token_bundle.get("support_tokens", []),
                    "items": items,
                    "total": len(items),
                    "filter_note": "默认仅召回命中 QQ 或绑定身份键的记忆；名称、别名和群名片不单独作为召回依据。",
                    "message": f"已找到 {len(items)} 条 LivingMemory 相关记忆",
                }
            )
        except sqlite3.OperationalError as exc:
            logger.warning(f"查询 LivingMemory 失败: {exc}")
            return self._exception_error("LivingMemory 数据库暂时不可读")
        except Exception as exc:
            logger.error(f"查询关系节点 LivingMemory 失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
    async def clear_worldbook_pending_observations(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        user_id = self._single_line(payload.get("user_id"), 40)
        try:
            async with self.plugin._data_lock:
                profiles = self.plugin.data.setdefault("worldbook_member_profiles", {})
                if not isinstance(profiles, dict):
                    profiles = {}
                    self.plugin.data["worldbook_member_profiles"] = profiles
                cleared = 0
                touched = 0
                for profile_id, profile in profiles.items():
                    if user_id and str(profile_id) != user_id:
                        continue
                    if not isinstance(profile, dict):
                        continue
                    pending = profile.get("pending_observations")
                    if not isinstance(pending, list) or not pending:
                        continue
                    cleared += len([item for item in pending if isinstance(item, dict)])
                    profile["pending_observations"] = []
                    profile["pending_observations_cleared_at"] = time.time()
                    touched += 1
                if user_id and not touched:
                    return self._error("没有找到可清理的待确认观察")
                if touched:
                    self.plugin._save_data_sync(sections={"worldbook_member_profiles"})
                data = deepcopy(self.plugin.data)
            message = f"已清理 {cleared} 条待确认观察" if cleared else "没有待确认观察需要清理"
            return self._ok({"message": message, "cleared": cleared, "worldbook": self._worldbook_summary(data)})
        except Exception as exc:
            logger.error(f"清理待确认观察失败: {exc}", exc_info=True)
            return self._exception_error("清理待确认观察失败")
    async def update_worldbook_group(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        group_id = self._single_line(payload.get("group_id"), 40)
        if not group_id:
            return self._error("缺少 group_id")
        try:
            async with self.plugin._data_lock:
                groups = self.plugin.data.setdefault("worldbook_group_profiles", {})
                if not isinstance(groups, dict):
                    groups = {}
                    self.plugin.data["worldbook_group_profiles"] = groups
                if payload.get("delete"):
                    deleted = self.plugin.data.setdefault("worldbook_deleted_group_ids", [])
                    if not isinstance(deleted, list):
                        deleted = []
                        self.plugin.data["worldbook_deleted_group_ids"] = deleted
                    if group_id not in deleted:
                        deleted.append(group_id)
                    changed = groups.pop(group_id, None) is not None
                    self.plugin._save_data_sync(
                        sections={"worldbook_group_profiles", "worldbook_deleted_group_ids"}
                    )
                    data = deepcopy(self.plugin.data)
                    return self._ok({"changed": changed, "message": "已删除群资料", "worldbook": self._worldbook_summary(data)})
                deleted = self.plugin.data.setdefault("worldbook_deleted_group_ids", [])
                if isinstance(deleted, list) and group_id in deleted:
                    self.plugin.data["worldbook_deleted_group_ids"] = [item for item in deleted if str(item) != group_id]
                group = groups.get(group_id)
                if not isinstance(group, dict):
                    group = {
                        "group_id": group_id,
                        "name": group_id,
                        "content": "",
                        "enabled": True,
                        "priority": 110,
                        "aliases": [],
                        "source_entries": ["手动维护"],
                    }
                    groups[group_id] = group
                group["group_id"] = group_id
                if "enabled" in payload:
                    group["enabled"] = bool(payload.get("enabled"))
                if "name" in payload:
                    group["name"] = self._single_line(payload.get("name"), 80) or group_id
                if "content" in payload:
                    group["content"] = str(payload.get("content") or "").strip()[:2000]
                if "priority" in payload:
                    group["priority"] = self._clamp_int(payload.get("priority"), 110, -1000, 10000)
                group["manual_edit_ts"] = time.time()
                self.plugin._save_data_sync(sections={"worldbook_group_profiles"})
                data = deepcopy(self.plugin.data)
            return self._ok({"message": "已保存群资料", "worldbook": self._worldbook_summary(data)})
        except Exception as exc:
            logger.error(f"更新群资料失败: {exc}", exc_info=True)
            return self._exception_error("更新群资料失败")
    def _worldbook_member_for_private_user_locked(
        self,
        data: dict[str, Any],
        user_id: str,
        user: dict[str, Any],
    ) -> dict[str, Any] | None:
        profiles = data.get("worldbook_member_profiles") if isinstance(data.get("worldbook_member_profiles"), dict) else {}
        if not profiles:
            return None
        candidate_ids = [str(user_id)]
        if isinstance(user, dict):
            candidate_ids.extend(
                self._single_line(item, 80)
                for item in (user.get("alias_user_ids") if isinstance(user.get("alias_user_ids"), list) else [])
                if self._single_line(item, 80)
            )
        for candidate_id in candidate_ids:
            profile = profiles.get(candidate_id)
            if isinstance(profile, dict):
                return self._worldbook_member_profile_summary(candidate_id, profile)
        for profile_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            linked_id = self._single_line(profile.get("linked_qq_user_id") or profile.get("merged_into_user_id"), 80)
            external_ids = profile.get("external_ids") if isinstance(profile.get("external_ids"), list) else []
            external_id_set = {self._single_line(item, 80) for item in external_ids if self._single_line(item, 80)}
            if linked_id in candidate_ids or any(candidate_id in external_id_set for candidate_id in candidate_ids):
                return self._worldbook_member_profile_summary(str(profile_id), profile)
        return None
    def _worldbook_member_profile_summary(self, user_id: str, item: dict[str, Any]) -> dict[str, Any]:
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        observed = item.get("observed_names") if isinstance(item.get("observed_names"), list) else []
        external_ids = item.get("external_ids") if isinstance(item.get("external_ids"), list) else []
        memories = self._normalize_important_memories(item.get("important_memories"))
        pending = item.get("pending_observations") if isinstance(item.get("pending_observations"), list) else []
        return {
            "user_id": self._single_line(user_id, 40),
            "identity_type": self._single_line(item.get("identity_type") or ("qq" if str(user_id).isdigit() else "external"), 20),
            "name": self._single_line(item.get("name"), 60),
            "gender": self._single_line(item.get("gender"), 40),
            "enabled": bool(item.get("enabled", True)),
            "priority": item.get("priority", 120),
            "aliases": [self._single_line(alias, 40) for alias in aliases if self._single_line(alias, 40)],
            "observed_names": [self._single_line(name, 40) for name in observed if self._single_line(name, 40)],
            "external_ids": [self._single_line(ext, 80) for ext in external_ids if self._single_line(ext, 80)],
            "linked_qq_user_id": self._single_line(item.get("linked_qq_user_id") or item.get("merged_into_user_id"), 40),
            "linked_bili_profile_id": self._single_line(item.get("linked_bili_profile_id"), 80),
            "content": self._single_line(item.get("content"), 260),
            "identity_note": self._single_line(item.get("identity_note") or item.get("note") or item.get("content"), 500),
            "boundary_note": self._single_line(item.get("boundary_note"), 500),
            "important_memories": memories[:6],
            "pending_observation_count": len(pending),
            "source_entries": item.get("source_entries") if isinstance(item.get("source_entries"), list) else [],
            "note": self._single_line(item.get("note"), 500),
        }
    @classmethod
    def _normalize_worldbook_member_id(cls, value: Any) -> str:
        text = cls._single_line(value, 80)
        if not text:
            return ""
        lowered = text.lower()
        if lowered.startswith("bili:") or lowered.startswith("bilibili:"):
            digits = re.sub(r"\D+", "", lowered.split(":", 1)[1])
            return f"bili:{digits}" if digits else ""
        if lowered.startswith("bili_live_"):
            return re.sub(r"[^A-Za-z0-9_:-]+", "_", text)[:80]
        if text.isdigit():
            return text
        return re.sub(r"[^A-Za-z0-9_:-]+", "_", text)[:80]
    def _worldbook_member_id_valid(self, user_id: str, *, allow_opaque: bool = False) -> bool:
        if user_id.isdigit():
            return len(user_id) >= 5
        lowered = user_id.lower()
        if lowered.startswith("bili:"):
            return bool(re.fullmatch(r"bili:\d{2,}", lowered))
        if lowered.startswith("bili_live_"):
            return bool(re.fullmatch(r"bili_live_[A-Za-z0-9_-]{6,64}", user_id))
        # QQ 官方等平台使用稳定但不可枚举的 openid/平台用户 ID。只在
        # 调用方已确认其来自受支持平台时放行，避免把任意文本当成节点键。
        return bool(allow_opaque and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{4,79}", user_id))
    def _worldbook_known_opaque_member_id(self, user_id: str) -> bool:
        """Return whether an opaque node key is already tied to QQ Official."""
        candidate = self._single_line(user_id, 80)
        if not candidate:
            return False
        users = self.plugin.data.get("users") if isinstance(getattr(self.plugin, "data", None), dict) else {}
        if isinstance(users, dict):
            user = users.get(candidate)
            if isinstance(user, dict):
                umo = self._single_line(user.get("umo"), 240)
                profile_getter = getattr(self.plugin, "_platform_profile", None)
                if callable(profile_getter):
                    try:
                        profile = profile_getter(umo=umo)
                    except Exception:
                        profile = {}
                    if isinstance(profile, dict) and profile.get("kind") == "qq_official":
                        return True
        profiles = self.plugin.data.get("worldbook_member_profiles") if isinstance(getattr(self.plugin, "data", None), dict) else {}
        if isinstance(profiles, dict):
            profile = profiles.get(candidate)
            if isinstance(profile, dict) and str(profile.get("identity_type") or "").lower() == "external":
                return True
        return False
    def _worldbook_setup_member_id_valid(
        self,
        user_id: str,
        *,
        target_ids: list[str],
        target_platform: str,
    ) -> bool:
        platform_getter = getattr(self.plugin, "_platform_profile", None)
        platform_profile = {}
        if callable(platform_getter):
            try:
                platform_profile = platform_getter(kind=target_platform)
            except Exception:
                platform_profile = {}
        allow_opaque = bool(
            user_id
            and user_id in target_ids
            and isinstance(platform_profile, dict)
            and platform_profile.get("kind") == "qq_official"
        )
        return self._worldbook_member_id_valid(
            user_id,
            allow_opaque=allow_opaque or self._worldbook_known_opaque_member_id(user_id),
        )
    def _bind_worldbook_external_member_locked(
        self,
        profiles: dict[str, Any],
        source_id: str,
        target_id: str,
        source_profile: dict[str, Any],
    ) -> dict[str, Any]:
        target = profiles.get(target_id)
        if not isinstance(target, dict):
            target = {
                "user_id": target_id,
                "name": self._single_line(source_profile.get("name"), 80) or target_id,
                "gender": self._single_line(source_profile.get("gender"), 40),
                "aliases": [],
                "content": "",
                "identity_note": f"QQ {target_id}，由外部身份绑定创建。",
                "boundary_note": "",
                "important_memories": [],
                "enabled": True,
                "priority": 120,
                "source_entries": [],
                "observed_names": [],
            }
            profiles[target_id] = target
        target["user_id"] = target_id
        target["identity_type"] = "qq"
        source_gender = self._single_line(source_profile.get("gender"), 40)
        if source_gender and not self._single_line(target.get("gender"), 40):
            target["gender"] = source_gender
        live_names = self._worldbook_string_list(source_profile.get("observed_names"), limit=12, item_limit=40)
        live_name = self._single_line(source_profile.get("name"), 40)
        if live_name and live_name != target_id:
            live_names.insert(0, live_name)
        aliases = self._worldbook_string_list(target.get("aliases"), limit=30, item_limit=40)
        for alias in live_names:
            if alias and alias != target_id and alias not in aliases:
                aliases.append(alias)
        target["aliases"] = aliases[:30]

        external_ids = self._worldbook_string_list(target.get("external_ids"), limit=20, item_limit=80)
        if source_id not in external_ids:
            external_ids.insert(0, source_id)
        target["external_ids"] = external_ids[:20]
        target["linked_bili_profile_id"] = source_id
        target["manual_edit_ts"] = time.time()

        for field, limit in (("content", 2000), ("identity_note", 2000), ("boundary_note", 1200)):
            source_text = str(source_profile.get(field) or "").strip()
            target_text = str(target.get(field) or "").strip()
            if source_text and source_text not in target_text:
                glue = "\n" if target_text else ""
                target[field] = (target_text + glue + source_text)[:limit]

        source_memories = self._normalize_important_memories(source_profile.get("important_memories"))
        target_memories = self._normalize_important_memories(target.get("important_memories"))
        seen = {self._single_line(item.get("content"), 160) for item in target_memories if isinstance(item, dict)}
        for memory in source_memories:
            content = self._single_line(memory.get("content"), 160)
            if content and content not in seen:
                target_memories.append(memory)
                seen.add(content)
        target["important_memories"] = target_memories[:30]

        source_entries = self._worldbook_string_list(target.get("source_entries"), limit=30, item_limit=80)
        for entry in self._worldbook_string_list(source_profile.get("source_entries"), limit=12, item_limit=80):
            if entry not in source_entries:
                source_entries.append(entry)
        if "live_stream_companion" not in source_entries:
            source_entries.append("live_stream_companion")
        target["source_entries"] = source_entries[:30]

        # Keep visual references attached to the surviving QQ node when an
        # external identity is merged into it.
        visual_assets = self.plugin.data.get("photo_reference_assets")
        if isinstance(visual_assets, list):
            target_asset_count = sum(
                1 for raw in visual_assets
                if isinstance(raw, dict)
                and raw.get("scope") == "relation_user"
                and str(raw.get("owner_id") or "") == target_id
            )
            for raw in visual_assets:
                if not isinstance(raw, dict) or raw.get("scope") != "relation_user" or str(raw.get("owner_id") or "") != source_id:
                    continue
                if target_asset_count >= REFERENCE_ASSET_MAX_PER_OWNER:
                    break
                raw["owner_id"] = target_id
                raw["updated_at"] = time.time()
                target_asset_count += 1

        source_profile["enabled"] = False
        source_profile["linked_qq_user_id"] = target_id
        source_profile["merged_into_user_id"] = target_id
        source_profile["manual_edit_ts"] = time.time()
        self._merge_live_viewer_activity_for_worldbook_bind(source_id, target_id, live_names)
        return {"source_user_id": source_id, "target_user_id": target_id}
    def _worldbook_string_list(self, value: Any, *, limit: int = 20, item_limit: int = 60) -> list[str]:
        raw_items: list[Any]
        if isinstance(value, list):
            raw_items = value
        elif isinstance(value, str):
            raw_items = re.split(r"[\n,，;；]+", value)
        else:
            raw_items = []
        result: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = self._single_line(item, item_limit)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
            if len(result) >= limit:
                break
        return result
    def _merge_live_viewer_activity_for_worldbook_bind(self, source_id: str, target_id: str, live_names: list[str]) -> None:
        store = self.plugin.data.get("live_stream_companion")
        if not isinstance(store, dict):
            return
        activity = store.get("viewer_activity")
        if not isinstance(activity, dict):
            return
        target_key = f"user:{target_id}"
        source_keys = [f"user:{source_id}"]
        source_keys.extend(f"live:{name}" for name in live_names if name)
        target = activity.setdefault(target_key, {"viewer_key": target_key, "user_id": target_id, "recent_events": [], "recent_danmaku": [], "event_counts": {}})
        if not isinstance(target, dict):
            target = {"viewer_key": target_key, "user_id": target_id, "recent_events": [], "recent_danmaku": [], "event_counts": {}}
            activity[target_key] = target
        target["viewer_key"] = target_key
        target["user_id"] = target_id
        aliases = target.setdefault("live_usernames", [])
        if not isinstance(aliases, list):
            aliases = []
            target["live_usernames"] = aliases
        for name in live_names:
            if name and name not in aliases:
                aliases.insert(0, name)
        del aliases[8:]
        for key in source_keys:
            item = activity.get(key)
            if not isinstance(item, dict) or item is target:
                continue
            self._merge_activity_item(target, item)
            activity.pop(key, None)
    def _worldbook_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        profiles = data.get("worldbook_member_profiles") if isinstance(data.get("worldbook_member_profiles"), dict) else {}
        groups = data.get("worldbook_group_profiles") if isinstance(data.get("worldbook_group_profiles"), dict) else {}
        visual_assets = [
            normalized
            for raw in (data.get("photo_reference_assets") if isinstance(data.get("photo_reference_assets"), list) else [])
            if (normalized := normalize_reference_asset(raw)) is not None
        ]
        visual_assets_by_owner: dict[str, list[dict[str, Any]]] = {}
        for asset in visual_assets:
            if asset.get("scope") == "relation_user":
                visual_assets_by_owner.setdefault(str(asset.get("owner_id") or ""), []).append(asset)
        role_assets_by_owner: dict[str, list[dict[str, Any]]] = {}
        for asset in visual_assets:
            if asset.get("scope") == "relation_role":
                role_assets_by_owner.setdefault(str(asset.get("owner_id") or ""), []).append(asset)
        role_cards: list[dict[str, Any]] = []
        for raw_card in normalize_bot_relationship_cards(
            runtime_persona_setting(
                self.plugin,
                "bot_relationship_cards",
                getattr(self.plugin, "bot_relationship_cards", []),
            )
        ):
            parts = [self._single_line(part, 200) for part in raw_card.split(" || ", 2)]
            role_name = parts[0] if parts else ""
            if not role_name:
                continue
            role_owner = normalize_reference_owner_id("relation_role", role_name)
            role_assets = role_assets_by_owner.get(role_owner, [])
            role_cards.append(
                {
                    "name": role_name,
                    "relation": parts[1] if len(parts) > 1 else "",
                    "appearance": parts[2] if len(parts) > 2 else "",
                    "owner_id": role_owner,
                    "reference_asset_count": len(role_assets),
                    "reference_assets": [
                        self._reference_asset_page_item(asset)
                        for asset in role_assets[:REFERENCE_ASSET_MAX_PER_OWNER]
                    ],
                }
            )
        entries = data.get("worldbook_entries") if isinstance(data.get("worldbook_entries"), list) else []
        state = data.get("worldbook_import_state") if isinstance(data.get("worldbook_import_state"), dict) else {}
        member_count = self._int(data.get("worldbook_member_profile_count")) if "worldbook_member_profile_count" in data else 0
        enabled_member_count = self._int(data.get("worldbook_enabled_member_profile_count")) if "worldbook_enabled_member_profile_count" in data else 0
        pending_observation_total = self._int(data.get("worldbook_pending_observation_total")) if "worldbook_pending_observation_total" in data else 0
        group_count = self._int(data.get("worldbook_group_profile_count")) if "worldbook_group_profile_count" in data else 0
        entry_count = self._int(data.get("worldbook_entry_count")) if "worldbook_entry_count" in data else len(entries)
        profile_items = []
        for user_id, item in profiles.items():
            if not isinstance(item, dict):
                continue
            aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
            observed = item.get("observed_names") if isinstance(item.get("observed_names"), list) else []
            external_ids = item.get("external_ids") if isinstance(item.get("external_ids"), list) else []
            memories = self._normalize_important_memories(item.get("important_memories"))
            pending = item.get("pending_observations") if isinstance(item.get("pending_observations"), list) else []
            pending_items = []
            for raw in pending[:8]:
                if not isinstance(raw, dict):
                    continue
                pending_items.append(
                    {
                        "id": self._single_line(raw.get("id"), 40),
                        "title": self._single_line(raw.get("title"), 60) or "群聊观察",
                        "content": self._single_line(raw.get("content"), 260),
                        "evidence": self._single_line(raw.get("evidence"), 160),
                        "group_id": self._single_line(raw.get("group_id"), 40),
                        "weight": self._clamp_int(raw.get("weight"), 35, 0, 100),
                        "count": self._clamp_int(raw.get("count"), 1, 1, 999),
                        "created_at": self.plugin._format_timestamp_elapsed(raw.get("created_at", 0)),
                    }
                )
            profile_items.append(
                {
                    "user_id": self._single_line(user_id, 40),
                    "identity_type": self._single_line(item.get("identity_type") or ("qq" if str(user_id).isdigit() else "external"), 20),
                    "name": self._single_line(item.get("name"), 60),
                    "gender": self._single_line(item.get("gender"), 40),
                    "enabled": bool(item.get("enabled", True)),
                    "priority": item.get("priority", 120),
                    "aliases": [self._single_line(alias, 40) for alias in aliases if self._single_line(alias, 40)],
                    "observed_names": [self._single_line(name, 40) for name in observed if self._single_line(name, 40)],
                    "external_ids": [self._single_line(ext, 80) for ext in external_ids if self._single_line(ext, 80)],
                    "linked_qq_user_id": self._single_line(item.get("linked_qq_user_id") or item.get("merged_into_user_id"), 40),
                    "linked_bili_profile_id": self._single_line(item.get("linked_bili_profile_id"), 80),
                    "auto_registration_pending": bool(item.get("auto_registration_pending", False)),
                    "profile_origin": self._single_line(item.get("profile_origin"), 40),
                    "observation_only": bool(item.get("observation_only", False)),
                    "proactive_contact_enabled": bool(item.get("proactive_contact_enabled", False)),
                    "relationship_state": self._single_line(item.get("relationship_state"), 24) or "neutral",
                    "affinity_score": self._int(item.get("affinity_score")),
                    "observed_group_count": len(
                        item.get("group_observation_scope_ids")
                        if isinstance(item.get("group_observation_scope_ids"), list)
                        else []
                    ),
                    "content": self._single_line(item.get("content"), 260),
                    "identity_note": self._single_line(item.get("identity_note") or item.get("note") or item.get("content"), 500),
                    "boundary_note": self._single_line(item.get("boundary_note"), 500),
                    "important_memories": memories,
                    "pending_observations": pending_items,
                    "pending_observation_count": len(pending_items),
                    "source_entries": item.get("source_entries") if isinstance(item.get("source_entries"), list) else [],
                    "note": self._single_line(item.get("note"), 500),
                    "reference_asset_count": len(visual_assets_by_owner.get(str(user_id), [])),
                    "reference_assets": [
                        self._reference_asset_page_item(asset)
                        for asset in visual_assets_by_owner.get(str(user_id), [])[:REFERENCE_ASSET_MAX_PER_OWNER]
                    ],
                }
            )
        profile_items.sort(key=lambda item: (not item.get("enabled", True), item.get("name") or item.get("user_id")))
        if "worldbook_member_profile_count" not in data:
            member_count = len(profile_items)
            enabled_member_count = sum(1 for item in profile_items if item.get("enabled", True))
            pending_observation_total = sum(self._clamp_int(item.get("pending_observation_count"), 0, 0, 999) for item in profile_items)
        group_items = [
            {
                "group_id": self._single_line(group_id, 40),
                "name": self._single_line(item.get("name"), 60),
                "enabled": bool(item.get("enabled", True)),
                "priority": item.get("priority", 110),
                "content": self._single_line(item.get("content"), 220),
            }
            for group_id, item in groups.items()
            if isinstance(item, dict)
        ]
        if "worldbook_group_profile_count" not in data:
            group_count = len(group_items)
        return {
            "enabled": bool(runtime_persona_setting(self.plugin, "enable_worldbook_member_recognition", False)),
            "auto_import": bool(runtime_persona_setting(self.plugin, "worldbook_auto_import", False)),
            "match_aliases": bool(runtime_persona_setting(self.plugin, "worldbook_member_match_aliases", False)),
            "self_registration": bool(runtime_persona_setting(self.plugin, "worldbook_self_registration", False)),
            "self_registration_block_word_count": len(
                runtime_persona_setting(
                    self.plugin,
                    "worldbook_self_registration_block_words",
                    [],
                )
                if isinstance(
                    runtime_persona_setting(
                        self.plugin,
                        "worldbook_self_registration_block_words",
                        [],
                    ),
                    list,
                )
                else []
            ),
            "auto_pending_observations": bool(runtime_persona_setting(self.plugin, "worldbook_auto_pending_observations", False)),
            "inject_limit": runtime_persona_setting(
                self.plugin,
                "worldbook_member_inject_limit",
                0,
            ),
            "entry_count": entry_count,
            "member_count": member_count,
            "enabled_member_count": enabled_member_count,
            "pending_observation_total": pending_observation_total,
            "group_count": group_count,
            "last_import": self.plugin._format_timestamp_elapsed(state.get("last_import_at", 0)),
            "source_files": state.get("source_files") if isinstance(state.get("source_files"), list) else [],
            "members": profile_items[:120],
            "groups": group_items[:80],
            "relationship_roles": role_cards[:32],
            "relationship_role_reference_count": sum(
                len(items) for items in role_assets_by_owner.values()
            ),
        }
    def _worldbook_member_livingmemory_tokens(self, user_id: str, profile: dict[str, Any]) -> dict[str, list[str]]:
        primary_raw: list[Any] = [
            user_id,
            profile.get("linked_qq_user_id"),
            profile.get("merged_into_user_id"),
            profile.get("linked_bili_profile_id"),
        ]
        external_ids = profile.get("external_ids")
        if isinstance(external_ids, list):
            primary_raw.extend(external_ids)
        support_raw: list[Any] = []
        for key in ("name", "aliases", "observed_names"):
            value = profile.get(key)
            if isinstance(value, list):
                support_raw.extend(value)

        def normalize(raw_items: list[Any], seen: set[str]) -> list[str]:
            tokens: list[str] = []
            for raw in raw_items:
                text = self._single_line(raw, 80)
                if not text or text in seen:
                    continue
                if text.isdigit():
                    if len(text) < 5:
                        continue
                elif len(text) < 2:
                    continue
                seen.add(text)
                tokens.append(text)
            return tokens

        def stable_support(raw_items: list[Any]) -> list[Any]:
            stable: list[Any] = []
            for raw in raw_items:
                text = self._single_line(raw, 40)
                if not text or len(text) > 16:
                    continue
                if any(mark in text for mark in ("，", ",", "。", "！", "？", " ", "：", ":", "|", "\n")):
                    continue
                stable.append(text)
            return stable

        seen_tokens: set[str] = set()
        primary_tokens = normalize(primary_raw, seen_tokens)
        support_tokens = normalize(stable_support(support_raw), seen_tokens)
        tokens = [*primary_tokens, *support_tokens]
        return {
            "tokens": tokens[:18],
            "primary_tokens": primary_tokens[:8],
            "support_tokens": support_tokens[:12],
        }
