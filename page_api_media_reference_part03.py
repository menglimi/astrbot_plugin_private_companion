# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiMediaReferencePart03Mixin。

由 tools/split_mixin_domain.py 从 page_api_media_reference.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 466 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMediaReferenceMixin）。
"""
from __future__ import annotations

from .page_api_media_reference_shared import PHOTO_REFERENCE_PREVIEW_MAX_BYTES, logger
from .page_api_media_reference_shared import Any
from .page_api_media_reference_shared import MAX_LIBRARY_REFERENCES
from .page_api_media_reference_shared import Mapping
from .page_api_media_reference_shared import Path
from .page_api_media_reference_shared import REFERENCE_ASSET_MAX_BYTES
from .page_api_media_reference_shared import REFERENCE_ASSET_MAX_PER_OWNER
from .page_api_media_reference_shared import REFERENCE_ASSET_MAX_TOTAL
from .page_api_media_reference_shared import REFERENCE_ASSET_ROLES
from .page_api_media_reference_shared import _path_text
from .page_api_media_reference_shared import _safe_int
from .page_api_media_reference_shared import base64
from .page_api_media_reference_shared import binascii
from .page_api_media_reference_shared import deepcopy
from .page_api_media_reference_shared import mimetypes
from .page_api_media_reference_shared import normalize_reference_asset
from .page_api_media_reference_shared import normalize_reference_asset_scope
from .page_api_media_reference_shared import normalize_reference_owner_id
from .page_api_media_reference_shared import quote
from .page_api_media_reference_shared import re
from .page_api_media_reference_shared import request
from .page_api_media_reference_shared import _page_api_media_reference_host
from .page_api_media_reference_shared import time



class PrivateCompanionPageApiMediaReferencePart03Mixin:
    """PrivateCompanionPageApiMediaReferencePart03Mixin（从 PrivateCompanionPageApiMediaReferenceMixin 拆出）。"""


    async def run_photo_reference_selection_trial(self) -> dict[str, Any]:
        """Run a bounded selection trial; never invoke the production photo tool."""
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须是对象")
        request_text = self._multi_line(
            payload.get("request_text") or payload.get("text"),
            1200,
        )
        if not request_text:
            return self._error("必须提供真实对话用户原话 request_text")
        candidates = payload.get("candidates")
        if isinstance(candidates, list):
            normalized_candidates: list[dict[str, Any]] = []

            def clean_values(value: Any, *, limit: int = 12) -> list[str]:
                raw_values = list(value) if isinstance(value, (list, tuple, set)) else [value]
                return [
                    clean
                    for clean in (self._single_line(item, 40) for item in raw_values[:limit])
                    if clean
                ]

            for index, item in enumerate(candidates[: MAX_LIBRARY_REFERENCES + 1], start=1):
                if not isinstance(item, Mapping):
                    continue
                source = self._single_line(item.get("source") or item.get("path"), 1000)
                candidate = {
                    "id": self._single_line(item.get("id"), 80) or f"trial-candidate-{index}",
                    "kind": self._single_line(item.get("kind"), 40) or "library",
                    "source": source,
                    "path": self._single_line(item.get("path") or source, 1000),
                    "note": self._single_line(item.get("note"), 500),
                    "role_name": self._single_line(item.get("role_name"), 80),
                    "relationship": self._single_line(item.get("relationship"), 80),
                    "reference_roles": clean_values(item.get("reference_roles"), limit=8),
                    "outfit_category": self._single_line(item.get("outfit_category"), 40),
                    "outfit_lock_default": bool(item.get("outfit_lock_default")),
                    "scene_categories": clean_values(item.get("scene_categories")),
                    "time_categories": clean_values(item.get("time_categories")),
                    "excluded_scene_categories": clean_values(item.get("excluded_scene_categories")),
                    "excluded_time_categories": clean_values(item.get("excluded_time_categories")),
                    "preferred_preset": self._single_line(item.get("preferred_preset"), 80),
                    "metadata_source": self._single_line(item.get("metadata_source"), 30),
                    "selection_eligibility": self._single_line(
                        item.get("selection_eligibility") or "matching_only",
                        40,
                    ),
                    "priority": self._clamp_int(item.get("priority"), 0, -1000, 10000),
                }
                if isinstance(item.get("editor_intent"), Mapping) and item.get("editor_intent"):
                    candidate["editor_intent"] = {"present": True}
                normalized_candidates.append(candidate)
            candidates = normalized_candidates
        else:
            candidates = [
                item
                for item in self._photo_reference_page_items()
                if item.get("available")
            ][: MAX_LIBRARY_REFERENCES + 1]
        request_payload = dict(payload)
        request_payload["request_text"] = request_text
        request_payload["candidates"] = candidates
        context_snapshot = await self._photo_reference_trial_context_snapshot(request_payload)
        request_payload["_trial_context_snapshot"] = context_snapshot
        request_payload["ambient_context"] = context_snapshot
        runner = getattr(self.plugin, "photo_selection_trial_runner", None)
        if not callable(runner):
            runner = self._photo_reference_selection_trial_model_runner
        try:
            report = await _page_api_media_reference_host.run_photo_selection_trial(
                request_payload,
                candidates=candidates,
                tool_runner=runner if callable(runner) else None,
                selection_runner=self._photo_reference_selection_trial_selector,
                runs=max(1, min(3, _safe_int(payload.get("runs"), 1, 1))),
            )
            return self._ok(report.to_dict())
        except Exception as exc:
            logger.error(f"参考图选图试跑失败: {exc}", exc_info=True)
            return self._exception_error("参考图选图试跑失败")

    def _reference_asset_records(self) -> list[dict[str, Any]]:
        data = getattr(self.plugin, "data", None)
        if not isinstance(data, dict):
            return []
        raw = data.get("photo_reference_assets")
        if not isinstance(raw, list):
            raw = []
            data["photo_reference_assets"] = raw
        normalized: list[dict[str, Any]] = []
        changed = False
        seen: set[str] = set()
        per_owner: dict[tuple[str, str], int] = {}
        for item in raw:
            normalized_item = normalize_reference_asset(item)
            if not normalized_item:
                changed = True
                continue
            key = (normalized_item["scope"], normalized_item["owner_id"])
            if normalized_item["id"] in seen or len(normalized) >= REFERENCE_ASSET_MAX_TOTAL or per_owner.get(key, 0) >= REFERENCE_ASSET_MAX_PER_OWNER:
                changed = True
                continue
            seen.add(normalized_item["id"])
            per_owner[key] = per_owner.get(key, 0) + 1
            normalized.append(normalized_item)
            if normalized_item != item:
                changed = True
        if changed:
            data["photo_reference_assets"] = normalized
        return normalized

    def _reference_asset_local_path(self, asset: dict[str, Any]) -> Path | None:
        source = _path_text(asset.get("path") or asset.get("source"), 1200)
        if not source:
            return None
        resolver = getattr(self.plugin, "_photo_reference_local_path", None)
        local_source = ""
        if callable(resolver):
            try:
                local_source = str(resolver(source) or "")
            except Exception:
                local_source = ""
        path = Path(local_source or source).expanduser()
        try:
            if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                return None
            resolved = path.resolve()
            data_root = Path(str(getattr(self.plugin, "data_dir", "") or ".")).expanduser().resolve()
            allowed_roots = (
                data_root / "photo_reference_images",
                data_root / "photo_reference_assets",
            )
            if not any(resolved == root or root in resolved.parents for root in allowed_roots):
                return None
            return resolved
        except (OSError, ValueError):
            return None

    def _reference_asset_page_item(self, asset: dict[str, Any]) -> dict[str, Any]:
        path = self._reference_asset_local_path(asset)
        available = bool(path)
        file_size = 0
        if path is not None:
            try:
                file_size = path.stat().st_size
            except OSError:
                available = False
        asset_id = self._single_line(asset.get("id"), 80)
        return {
            "id": asset_id,
            "scope": asset.get("scope"),
            "owner_id": asset.get("owner_id"),
            "role_name": self._single_line(
                asset.get("role_name")
                or (
                    str(asset.get("owner_id") or "")[5:]
                    if str(asset.get("owner_id") or "").startswith("role:")
                    else ""
                ),
                80,
            ),
            "title": self._single_line(asset.get("title"), 120),
            "note": self._single_line(asset.get("note"), 500),
            "tags": [self._single_line(tag, 40) for tag in (asset.get("tags") or []) if self._single_line(tag, 40)],
            "reference_roles": [role for role in (asset.get("reference_roles") or []) if role in REFERENCE_ASSET_ROLES],
            "enabled": bool(asset.get("enabled", True)),
            "priority": self._clamp_int(asset.get("priority"), 0, -1000, 10000),
            "created_at": float(asset.get("created_at") or 0),
            "updated_at": float(asset.get("updated_at") or 0),
            "available": available,
            "file_size": file_size,
            "preview_endpoint": f"/reference_asset/image_data?id={quote(asset_id, safe='')}" if available else "",
        }

    def _reference_asset_find(self, asset_id: str) -> dict[str, Any] | None:
        clean_id = self._single_line(asset_id, 80)
        if not clean_id:
            return None
        return next((item for item in self._reference_asset_records() if item.get("id") == clean_id), None)

    @staticmethod
    def _reference_asset_data_size(source: str) -> int:
        text = str(source or "").strip()
        if text.startswith("base64://"):
            encoded = text[len("base64://"):]
        elif text.lower().startswith("data:") and "," in text:
            meta, encoded = text.split(",", 1)
            if ";base64" not in meta.lower():
                return 0
            mime = meta[5:].split(";", 1)[0].strip().lower()
            if mime not in {"image/png", "image/jpeg", "image/webp"}:
                return -1
        else:
            return 0
        try:
            return len(base64.b64decode(encoded, validate=False))
        except (ValueError, binascii.Error):
            return -1

    async def _reference_asset_stable_path(self, source: str, *, stem: str) -> str:
        resolver = getattr(self.plugin, "_photo_reference_source_to_stable_path", None)
        if callable(resolver):
            try:
                result = resolver(source, stem=stem)
                if hasattr(result, "__await__"):
                    result = await result
                return _path_text(result, 1200)
            except Exception as exc:
                logger.info("参考资产稳定落盘失败: type=%s", type(exc).__name__)
                return ""
        writer = getattr(self.plugin, "_photo_reference_write_data_image", None)
        if callable(writer) and (str(source).startswith("data:") or str(source).startswith("base64://")):
            try:
                return _path_text(writer(source, stem=stem), 1200)
            except Exception:
                return ""
        return ""

    def _reference_asset_owner_error(self, scope: str, owner_id: str) -> str:
        if scope == "relation_user":
            if not self._worldbook_member_id_valid(
                owner_id,
                allow_opaque=self._worldbook_known_opaque_member_id(owner_id),
            ):
                return "关系网参考图归属必须是有效 QQ 号、平台身份 ID 或受支持的外部身份键"
            return ""
        if scope == "relation_role":
            if not normalize_reference_owner_id(scope, owner_id):
                return "关系角色参考图归属必须使用 role:<角色名>"
            return ""
        if scope == "knowledge":
            if not normalize_reference_owner_id(scope, owner_id):
                return "知识参考图归属必须使用 kb:<id> 或 doc:<kb_id>:<doc_id>"
            return ""
        return "参考资产范围只能是 relation_user、relation_role 或 knowledge"

    def _reference_asset_payload_fields(self, payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
        base = dict(existing or {})
        for key in ("title", "note"):
            if key in payload:
                base[key] = self._single_line(payload.get(key), 500 if key == "note" else 120)
        if "tags" in payload:
            base["tags"] = [self._single_line(item, 40) for item in (payload.get("tags") if isinstance(payload.get("tags"), list) else re.split(r"[,，、/|\s]+", str(payload.get("tags") or ""))) if self._single_line(item, 40)][:12]
        if "reference_roles" in payload or "roles" in payload:
            raw_roles = payload.get("reference_roles", payload.get("roles"))
            base["reference_roles"] = [str(item or "").strip().lower() for item in (raw_roles if isinstance(raw_roles, list) else re.split(r"[,，、/|\s]+", str(raw_roles or ""))) if str(item or "").strip().lower() in REFERENCE_ASSET_ROLES]
        if "enabled" in payload:
            base["enabled"] = bool(payload.get("enabled"))
        if "priority" in payload:
            base["priority"] = self._clamp_int(payload.get("priority"), 0, -1000, 10000)
        return base

    async def list_reference_assets(self) -> dict[str, Any]:
        raw_scope = request.args.get("scope")
        owner_id = self._single_line(
            request.args.get("owner_id")
            or request.args.get("user_id")
            or request.args.get("knowledge_id")
            or request.args.get("role_name")
            or request.args.get("relationship_role"),
            120,
        )
        scope = normalize_reference_asset_scope(raw_scope)
        if not scope and "/relationship/role/reference/" in str(request.path or ""):
            scope = "relation_role"
        if not scope and request.args.get("user_id"):
            scope = "relation_user"
        if not scope and request.args.get("knowledge_id"):
            scope = "knowledge"
        if not scope and (request.args.get("role_name") or request.args.get("relationship_role")):
            scope = "relation_role"
        if scope and owner_id:
            owner_id = normalize_reference_owner_id(scope, owner_id)
        items = []
        for asset in self._reference_asset_records():
            if scope and asset.get("scope") != scope:
                continue
            if owner_id and asset.get("owner_id") != owner_id:
                continue
            items.append(self._reference_asset_page_item(asset))
        items.sort(key=lambda item: (not item.get("enabled", True), -float(item.get("priority") or 0), -float(item.get("updated_at") or 0)))
        response = {
            "version": 1,
            "assets": items,
            "items": items,
            "total": len(items),
            "available": sum(1 for item in items if item.get("available")),
            "limit": REFERENCE_ASSET_MAX_TOTAL,
            "per_owner_limit": REFERENCE_ASSET_MAX_PER_OWNER,
            "options": {
                "scopes": [
                    {"value": "relation_user", "label": "关系网用户"},
                    {"value": "relation_role", "label": "关系网角色卡"},
                    {"value": "knowledge", "label": "知识库/文档"},
                ],
                "reference_roles": [{"value": role, "label": role} for role in REFERENCE_ASSET_ROLES],
            },
        }
        return self._ok(response)

    async def get_reference_asset_image_data(self) -> dict[str, Any]:
        asset = self._reference_asset_find(request.args.get("id"))
        if not asset:
            return self._error("参考资产不存在或已删除")
        path = self._reference_asset_local_path(asset)
        if path is None:
            return self._error("参考资产文件不存在")
        try:
            file_size = path.stat().st_size
        except OSError:
            return self._error("无法读取参考资产文件")
        if file_size > PHOTO_REFERENCE_PREVIEW_MAX_BYTES:
            return self._error("参考资产预览文件过大")
        mime = mimetypes.guess_type(str(path))[0] or ""
        if not mime.startswith("image/"):
            return self._error("参考资产文件类型不受支持")
        try:
            return self._ok(await self._encode_image_cache_file_data_url(path, mime, max_bytes=PHOTO_REFERENCE_PREVIEW_MAX_BYTES))
        except Exception as exc:
            logger.info("参考资产预览失败: type=%s", type(exc).__name__)
            return self._error("参考资产预览失败")

    async def upload_reference_asset(self) -> dict[str, Any]:
        return await self._save_reference_asset(await request.get_json(silent=True) or {}, existing=None)

    async def update_reference_asset(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        asset = self._reference_asset_find(payload.get("id"))
        if not asset:
            return self._error("参考资产不存在或已删除")
        return await self._save_reference_asset(payload, existing=asset)

    async def _save_reference_asset(self, payload: dict[str, Any], *, existing: dict[str, Any] | None) -> dict[str, Any]:
        raw_scope = payload.get("scope") or (existing or {}).get("scope")
        raw_owner = (
            payload.get("owner_id")
            or payload.get("user_id")
            or payload.get("knowledge_id")
            or payload.get("role_name")
            or payload.get("relationship_role")
            or (existing or {}).get("owner_id")
        )
        scope = normalize_reference_asset_scope(raw_scope)
        if not scope and "/relationship/role/reference/" in str(request.path or ""):
            scope = "relation_role"
        if not scope and payload.get("user_id"):
            scope = "relation_user"
        if not scope and payload.get("knowledge_id"):
            scope = "knowledge"
        if not scope and (payload.get("role_name") or payload.get("relationship_role")):
            scope = "relation_role"
        owner_id = normalize_reference_owner_id(scope, raw_owner)
        owner_error = self._reference_asset_owner_error(scope, owner_id)
        if owner_error:
            return self._error(owner_error)
        if existing is None and len(self._reference_asset_records()) >= REFERENCE_ASSET_MAX_TOTAL:
            return self._error(f"参考资产最多保存 {REFERENCE_ASSET_MAX_TOTAL} 项")
        owner_count = sum(1 for item in self._reference_asset_records() if item.get("scope") == scope and item.get("owner_id") == owner_id and item.get("id") != (existing or {}).get("id"))
        if existing is None and owner_count >= REFERENCE_ASSET_MAX_PER_OWNER:
            return self._error(f"同一归属最多保存 {REFERENCE_ASSET_MAX_PER_OWNER} 张参考图")
        source = str(payload.get("data_url") or payload.get("image") or payload.get("source") or "").strip()
        if source:
            size = self._reference_asset_data_size(source)
            if size < 0:
                return self._error("图片数据不是有效的 Base64 图片")
            if size > REFERENCE_ASSET_MAX_BYTES:
                return self._error(f"参考图过大，上限为 {REFERENCE_ASSET_MAX_BYTES // 1024 // 1024} MB")
            stable_path = await self._reference_asset_stable_path(source, stem=f"{scope}_{owner_id}")
            if not stable_path:
                return self._error("图片无法稳定保存，请重新选择图片")
        else:
            stable_path = _path_text((existing or {}).get("path"), 1200)
        if not stable_path:
            return self._error("缺少图片数据")
        try:
            stored_size = Path(stable_path).stat().st_size
        except OSError:
            return self._error("图片保存后无法读取")
        if stored_size <= 0 or stored_size > REFERENCE_ASSET_MAX_BYTES:
            return self._error("保存后的图片大小不符合限制")
        base = self._reference_asset_payload_fields(payload, existing)
        base.update({"id": (existing or {}).get("id", ""), "scope": scope, "owner_id": owner_id, "path": stable_path})
        asset = normalize_reference_asset(base, now=time.time())
        if not asset:
            return self._error("参考资产元数据无效")
        async with self.plugin._data_lock:
            records = self._reference_asset_records()
            replaced = False
            for index, item in enumerate(records):
                if item.get("id") == asset["id"]:
                    records[index] = asset
                    replaced = True
                    break
            if not replaced:
                records.append(asset)
            self.plugin.data["photo_reference_assets"] = records
            self.plugin._save_data_sync(sections={"photo_reference_assets"})
            data = deepcopy(self.plugin.data)
        return self._ok({"message": "已更新参考资产" if existing else "已上传参考资产", "asset": self._reference_asset_page_item(asset), "worldbook": self._worldbook_summary(data)})

    async def delete_reference_asset(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        asset_id = self._single_line(payload.get("id"), 80)
        if not asset_id:
            return self._error("缺少参考资产 id")
        async with self.plugin._data_lock:
            records = self._reference_asset_records()
            target = next((item for item in records if item.get("id") == asset_id), None)
            if not target:
                return self._error("参考资产不存在或已删除")
            self.plugin.data["photo_reference_assets"] = [item for item in records if item.get("id") != asset_id]
            path = self._reference_asset_local_path(target)
            if path is not None:
                try:
                    root = Path(getattr(self.plugin, "data_dir", ".")).resolve() / "photo_reference_images"
                    path.relative_to(root.resolve())
                    if not any(item.get("path") == str(path) for item in self.plugin.data["photo_reference_assets"]):
                        path.unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
            self.plugin._save_data_sync(sections={"photo_reference_assets"})
            data = deepcopy(self.plugin.data)
        return self._ok({"message": "已删除参考资产", "worldbook": self._worldbook_summary(data)})

    @staticmethod
    def _normalize_photo_reference_asset_scope(value: Any) -> str:
        aliases = {
            "relation": "relation_user",
            "relation_user": "relation_user",
            "user": "relation_user",
            "member": "relation_user",
            "group": "group",
            "knowledge": "knowledge",
            "knowledge_base": "knowledge",
            "knowledge_item": "knowledge",
            "kb": "knowledge",
        }
        return aliases.get(str(value or "").strip().lower(), "")

    @staticmethod
    def _photo_reference_asset_bool(value: Any, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"0", "false", "no", "off", "disabled", "disable"}:
            return False
        if text in {"1", "true", "yes", "on", "enabled", "enable"}:
            return True
        return default

    def _photo_reference_asset_dir(self) -> Path:
        target = Path(str(getattr(self.plugin, "data_dir", "") or ".")).expanduser() / "photo_reference_assets"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _photo_reference_asset_path(self, value: Any) -> Path | None:
        """Resolve a stored asset path without allowing traversal outside its directory."""
        raw = str(value or "").strip()
        if not raw:
            return None
        root = self._photo_reference_asset_dir().resolve()
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError, ValueError):
            return None
        if resolved != root and root not in resolved.parents:
            return None
        return resolved

    def _photo_reference_asset_raw_items(self) -> list[Any]:
        data = getattr(self.plugin, "data", None)
        if not isinstance(data, dict):
            data = {}
            self.plugin.data = data
        assets = data.get("photo_reference_assets")
        if not isinstance(assets, list):
            assets = []
            data["photo_reference_assets"] = assets
        return assets
