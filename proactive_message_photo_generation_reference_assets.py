# -*- coding: utf-8 -*-
"""参考图资产解析域。

由 tools/split_mixin_domain.py 从 proactive_message_photo_generation.py 机械抽取（25 个方法 + 0 个模块级名字 + 0 个类级赋值 / 668 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessagePhotoGenerationMixin）。
"""
from __future__ import annotations

import re
from .helpers import _path_text, _photo_group_request_matches, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .photo_reference_catalog import PhotoReference, load_catalog, project_reference_candidate
from .photo_reference_selection import SelectionResult
from .proactive_message_photo_generation_shared import logger
from .reference_asset_gate import ReferenceAssetGate, ReferenceAssetPlan
from .reference_assets import normalize_reference_asset, normalize_reference_owner_id, reference_asset_tokens
from pathlib import Path
from typing import Any



class ProactiveMessagePhotoGenerationReferenceAssetsMixin:
    """参考图资产解析域（从 ProactiveMessagePhotoGenerationMixin 拆出）。"""


    def _q5_structured_reference_assets_enabled(self) -> bool:
        return bool(runtime_persona_setting(self, "enable_p5_structured_reference_assets", False))

    def _q5_structured_reference_generation_mode(
        self,
        workflow_kind: str,
        prompt_text: str,
        reference_candidate: dict[str, Any],
    ) -> str:
        kind = _single_line(workflow_kind, 40).lower()
        if kind in {"edit", "改图", "修图", "重绘", "p图"}:
            return "edit"
        if _single_line(reference_candidate.get("kind"), 40).lower() == "recent_sent_photo" or re.search(
            r"续拍|继续拍|接着拍|再来一张|换个姿势|换个表情|same scene|continue the photo",
            str(prompt_text or ""),
            flags=re.I,
        ):
            return "continuation"
        return "new_topic"

    def _q5_prepare_structured_reference_plan(
        self,
        *,
        generation_id: str,
        workflow_kind: str,
        prompt_text: str,
        reference_candidate: dict[str, Any],
        explicit_reference_supplied: bool,
    ) -> tuple[ReferenceAssetGate | None, ReferenceAssetPlan | None, str]:
        if not self._q5_structured_reference_assets_enabled():
            return None, None, "disabled"
        # User-provided and quoted paths remain legacy single-image flows. They
        # can never be promoted into the managed multi-image sink.
        if explicit_reference_supplied:
            return None, None, "legacy_explicit_reference"
        gate = ReferenceAssetGate(getattr(self, "data_dir", ""))
        mode = self._q5_structured_reference_generation_mode(
            workflow_kind,
            prompt_text,
            reference_candidate,
        )
        plan, status = gate.plan(
            runtime_persona_setting(self, "photo_structured_reference_assets", []),
            generation_id=generation_id,
            mode=mode,
        )
        if not plan:
            logger.info(
                "Q5 受管参考素材未进入图片输入汇: trace=%s status=%s",
                _single_line(generation_id, 80),
                status,
            )
            return gate, None, status
        return gate, plan, "ok"

    @staticmethod
    def _q5_managed_reference_candidate(plan: ReferenceAssetPlan) -> dict[str, Any]:
        primary = plan.primary_asset
        if primary is None:
            return {}
        return {
            "id": primary.asset_id,
            "kind": "managed_asset",
            "source": "q5_reference_asset_gate",
            "note": "管理员登记并校验的受管身份参考素材",
            "reference_roles": [item.role for item in plan.assets],
            "outfit_lock_default": any(
                item.role == "outfit" and item.outfit_lock_default
                for item in plan.assets
            ),
            "metadata_source": "q5_reference_asset_gate",
        }

    def _photo_persona_reference_image_path(self) -> str:
        catalog = runtime_persona_setting(self, "photo_reference_catalog", None)
        if catalog is None:
            raw = _path_text(
                runtime_persona_setting(self, "photo_persona_reference_image_path", ""),
                1000,
            )
        else:
            persona = next(
                (
                    item
                    for item in (catalog or ())
                    if isinstance(item, PhotoReference) and item.kind == "persona"
                ),
                None,
            )
            raw = _path_text(persona.source if persona is not None else "", 1000)
        if not raw:
            return ""
        raw = raw.strip().strip('"').strip("'")
        if re.match(r"^https?://", raw, flags=re.I):
            return ""
        candidates = [Path(raw).expanduser()]
        if not candidates[0].is_absolute():
            candidates.append(Path(self.data_dir) / raw)
        for candidate in candidates:
            try:
                path = candidate.resolve()
            except Exception:
                path = candidate
            if not path.exists() or not path.is_file():
                continue
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            return str(path)
        return ""

    @staticmethod
    def _photo_reference_normalize_roles(value: Any) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = re.split(r"[,，、/|\s]+", str(value or ""))
        aliases = {
            "identity": "identity",
            "persona": "identity",
            "face": "identity",
            "人设": "identity",
            "身份": "identity",
            "人物": "identity",
            "脸": "identity",
            "outfit": "outfit",
            "wardrobe": "outfit",
            "clothing": "outfit",
            "服装": "outfit",
            "穿搭": "outfit",
            "pose": "pose",
            "姿势": "pose",
            "scene": "scene",
            "background": "scene",
            "场景": "scene",
            "背景": "scene",
            "style": "style",
            "画风": "style",
            "风格": "style",
            "continuity": "continuity",
            "连续性": "continuity",
            "source": "source",
            "原图": "source",
        }
        roles: list[str] = []
        for item in raw_items:
            key = str(item or "").strip().lower()
            normalized = aliases.get(key, "")
            if normalized and normalized not in roles:
                roles.append(normalized)
        return roles

    @staticmethod
    def _photo_outfit_category_matches(value: Any) -> list[tuple[str, int, int, str]]:
        text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
        if not text:
            return []
        patterns = (
            ("cosplay", r"(?<![a-z0-9])cos(?:play)?(?![a-z0-9])|角色扮演|扮成|女仆装|巫女服|魔法少女|表演服"),
            ("school_uniform", r"校服|学院制服|学生制服|school[\s_-]*uniform"),
            ("sleepwear", r"睡衣|睡裙|睡袍|睡眠服|nightgown|nightdress|pajama|pyjama|sleepwear|bedtime outfit"),
            ("swimwear", r"泳装|泳衣|比基尼|swimsuit|swimwear|bikini"),
            ("sportswear", r"运动服|健身服|瑜伽服|球衣|sportswear|activewear|gym wear|jersey"),
            ("formalwear", r"礼服|晚礼服|正装|燕尾服|西装|tuxedo|formalwear|formal attire|evening gown|\bsuit\b"),
            ("homewear", r"居家服|家居服|家常服|宅家服|homewear|loungewear"),
            ("daily_outfit", r"今日穿搭|当天基础穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit"),
        )
        matches: list[tuple[str, int, int, str]] = []
        for category, pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.I):
                resolved_category = category
                if category == "homewear" and match.group(0).lower() == "loungewear":
                    context = text[max(0, match.start() - 40) : match.end() + 40]
                    if "bedtime" in context:
                        resolved_category = "sleepwear"
                matches.append((resolved_category, match.start(), match.end(), match.group(0)))
        matches.sort(key=lambda item: (item[1], item[2]))
        return matches

    @classmethod
    def _photo_outfit_category_from_text(cls, value: Any) -> str:
        matches = cls._photo_outfit_category_matches(value)
        return matches[0][0] if matches else ""

    @staticmethod
    def _photo_reference_scene_categories_from_text(value: Any) -> list[str]:
        text = re.sub(r"\s+", "", str(value or "")).lower()
        categories: list[str] = []
        mappings = (
            ("home", ("在家", "家里", "居家", "宅家", "home")),
            ("bedroom", ("卧室", "床边", "睡前", "刚起床", "bedroom", "bedtime")),
            ("school", ("上学", "校园", "教室", "校门", "school", "campus")),
            ("office", ("上班", "公司", "办公室", "office", "workplace")),
            ("outdoor", ("外出", "通勤", "逛街", "街头", "旅行", "outdoor", "commute")),
            ("formal_event", ("宴会", "舞会", "典礼", "正式场合", "banquet", "ceremony")),
            ("sport", ("运动", "健身", "跑步", "瑜伽", "球场", "gym", "sport")),
            ("beach", ("海边", "沙滩", "泳池", "beach", "pool")),
        )
        for category, tokens in mappings:
            if any(token in text for token in tokens):
                categories.append(category)
        return categories

    @staticmethod
    def _photo_reference_preset_for_category(category: str) -> str:
        return {
            "sleepwear": "居家睡衣",
            "homewear": "居家服",
            "cosplay": "COS自拍",
            "school_uniform": "校服人像",
            "formalwear": "礼服人像",
            "swimwear": "泳装人像",
            "sportswear": "运动服人像",
            "daily_outfit": "日常穿搭",
            "custom_outfit": "日常穿搭",
        }.get(str(category or "").strip().lower(), "")

    @staticmethod
    def _photo_reference_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on", "是", "开启", "锁定"}

    def _normalize_photo_reference_candidate_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(item or {})
        note = _single_line(normalized.get("note") or normalized.get("description"), 700)
        kind = _single_line(normalized.get("kind"), 40).lower() or "library"
        explicit_roles = normalized.get("reference_roles", normalized.get("reference_role"))
        roles = self._photo_reference_normalize_roles(explicit_roles)
        raw_category = normalized.get("outfit_category") or normalized.get("wardrobe_category")
        if not raw_category and isinstance(normalized.get("wardrobe_categories"), (list, tuple)):
            raw_category = next(iter(normalized.get("wardrobe_categories") or []), "")
        category = _single_line(raw_category, 40).lower()
        if not category:
            category = self._photo_outfit_category_from_text(note)
        if not roles:
            if kind == "persona":
                roles = ["identity"]
            elif kind in {"daily_outfit", "recent_sent_photo"}:
                roles = ["identity", "outfit"]
                if kind == "recent_sent_photo":
                    roles.extend(["scene", "continuity"])
            elif re.search(r"仅(?:用于)?(?:人设|身份|脸|发型)|只(?:参考|用于)(?:人设|身份|脸|发型)|identity only", note, flags=re.I):
                roles = ["identity"]
            elif category:
                roles = ["identity", "outfit"]
            else:
                roles = ["identity"]
        if kind == "daily_outfit" and not category:
            category = "daily_outfit"
        scene_values = normalized.get("scene_categories", normalized.get("scene_tags"))
        if isinstance(scene_values, (list, tuple, set)):
            scene_categories = [
                _single_line(value, 40).lower()
                for value in scene_values
                if _single_line(value, 40)
            ]
        else:
            scene_categories = self._photo_reference_scene_categories_from_text(scene_values or note)
        time_values = normalized.get("time_categories", normalized.get("time_tags"))
        if isinstance(time_values, (list, tuple, set)):
            time_categories = [
                _single_line(value, 40).lower()
                for value in time_values
                if _single_line(value, 40)
            ]
        else:
            time_categories = [
                _single_line(value, 40).lower()
                for value in re.split(r"[,，、/|\s]+", str(time_values or ""))
                if _single_line(value, 40)
            ]
        lock_default = self._photo_reference_bool(
            normalized.get("outfit_lock_default"),
            default=bool("outfit" in roles and (category or kind in {"daily_outfit", "recent_sent_photo"})),
        )
        preferred_preset = _single_line(
            normalized.get("preferred_preset") or normalized.get("preset"),
            60,
        ) or self._photo_reference_preset_for_category(category)
        normalized.update(
            {
                "kind": kind,
                "note": note,
                "reference_roles": list(dict.fromkeys(roles)),
                "outfit_category": category,
                "outfit_lock_default": lock_default,
                "scene_categories": list(dict.fromkeys(scene_categories)),
                "time_categories": list(dict.fromkeys(time_categories)),
                "preferred_preset": preferred_preset,
                "metadata_source": _single_line(normalized.get("metadata_source"), 30)
                or ("configured" if explicit_roles is not None or normalized.get("outfit_category") else "inferred_note"),
            }
        )
        return normalized

    def _photo_reference_library_entries(self) -> list[dict[str, Any]]:
        if runtime_persona_setting(self, "photo_reference_catalog", None) is None:
            loaded = load_catalog(
                [],
                catalog_version=0,
                legacy_library=runtime_persona_setting(self, "photo_reference_library", []),
                preset_names=self._photo_generation_scene_presets().keys(),
            )
            entries = [project_reference_candidate(item) for item in loaded.references if item.kind == "library"]
            for index, entry in enumerate(entries):
                raw_items = runtime_persona_setting(self, "photo_reference_library", []) or []
                raw_item = raw_items[index] if isinstance(raw_items, list) and index < len(raw_items) else None
                entry["_config_format"] = "dict" if isinstance(raw_item, dict) else "text"
            return entries
        return [
            project_reference_candidate(item)
            for item in (runtime_persona_setting(self, "photo_reference_catalog", ()) or ())
            if isinstance(item, PhotoReference) and item.kind == "library"
        ]

    def _photo_reference_local_path(self, source: str) -> str:
        raw = _path_text(source, 1000)
        if not raw or re.match(r"^https?://", raw, flags=re.I):
            return ""
        candidates = [Path(raw).expanduser()]
        if not candidates[0].is_absolute():
            candidates.append(Path(self.data_dir) / raw)
        for candidate in candidates:
            try:
                path = candidate.resolve()
                if path.exists() and path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    return str(path)
            except (OSError, ValueError):
                continue
        return ""

    def _daily_outfit_reference_image_path(self) -> str:
        item = self.data.get("daily_outfit_photo") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(item, dict):
            return ""
        if _single_line(item.get("date"), 20) != _today_key():
            return ""
        raw = _path_text(item.get("path"), 1000)
        if not raw:
            return ""
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = Path(self.data_dir) / raw
            path = path.resolve()
        except Exception:
            path = Path(raw)
        try:
            if not path.exists() or not path.is_file():
                return ""
        except (OSError, ValueError):
            return ""
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            return ""
        return str(path)

    def _photo_persona_reference_image_for_kind(self, workflow_kind: str, *, allow_daily_outfit: bool = True) -> str:
        if not bool(runtime_persona_setting(self, "enable_photo_reference_image", False)):
            return ""
        if str(workflow_kind or "").strip().lower() not in {"selfie", "portrait", "自拍", "人像"}:
            return ""
        if allow_daily_outfit:
            outfit_path = self._daily_outfit_reference_image_path()
            if outfit_path:
                return outfit_path
        return self._photo_persona_reference_image_path()

    async def _photo_persona_reference_image_for_kind_async(
        self,
        workflow_kind: str,
        *,
        allow_daily_outfit: bool = True,
        requester_user_id: str = "",
        request_text: str = "",
        ambient_context: str = "",
        selection_context: str = "",
        suggested_scene_preset: str = "",
        continuity_key: str = "",
    ) -> str:
        if not bool(
            runtime_persona_setting(self, "enable_photo_reference_image", False)
        ):
            return ""
        if str(workflow_kind or "").strip().lower() not in {
            "selfie",
            "portrait",
            "自拍",
            "人像",
        }:
            return ""
        selector = getattr(self, "_select_photo_reference_candidate_async", None)
        if not callable(selector):
            return ""
        try:
            selected = await selector(
                workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                requester_user_id=requester_user_id,
                request_text=request_text,
                ambient_context=ambient_context,
                selection_context=selection_context,
                suggested_scene_preset=suggested_scene_preset,
                continuity_key=continuity_key,
            )
        except TypeError:
            selected = await selector(
                workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                request_text=request_text,
                ambient_context=ambient_context,
                selection_context=selection_context,
            )
        if isinstance(selected, SelectionResult):
            selected = selected.selected
        return _path_text(selected.get("path") if isinstance(selected, dict) else "", 1000)

    async def _photo_persona_reference_image_path_async(self) -> str:
        if not bool(runtime_persona_setting(self, "enable_photo_reference_image", False)):
            return ""
        local_path = self._photo_persona_reference_image_path()
        if local_path:
            return local_path
        catalog = runtime_persona_setting(self, "photo_reference_catalog", None)
        if catalog is None:
            raw = _path_text(
                runtime_persona_setting(self, "photo_persona_reference_image_path", ""),
                1000,
            )
        else:
            persona = next(
                (
                    item
                    for item in (catalog or ())
                    if isinstance(item, PhotoReference) and item.kind == "persona"
                ),
                None,
            )
            raw = _path_text(persona.source if persona is not None else "", 1000)
        if not raw or not re.match(r"^https?://", raw, flags=re.I):
            return ""
        resolver = getattr(self, "_photo_reference_source_to_stable_path", None)
        if not callable(resolver):
            return ""
        try:
            stable_path = await resolver(raw, stem="config_url_reference")
        except Exception as exc:
            logger.info("配置页人设参考图 URL 下载失败: %s url=%s", _single_line(exc, 120), _single_line(raw, 120))
            return ""
        if not stable_path:
            logger.info("配置页人设参考图 URL 未能转为本地参考图: url=%s", _single_line(raw, 120))
            return ""
        setter = getattr(self, "_set_photo_reference_config_path", None)
        if callable(setter):
            try:
                result = setter(stable_path)
                if hasattr(result, "__await__"):
                    result = await result
                if result is False:
                    logger.info(
                        "配置页人设参考图 URL 已下载但配置保存返回失败: path=%s",
                        _single_line(stable_path, 160),
                    )
            except Exception as exc:
                logger.info("配置页人设参考图 URL 已下载但回写失败: %s path=%s", _single_line(exc, 120), _single_line(stable_path, 160))
        logger.info("配置页人设参考图 URL 已缓存为本地文件: path=%s", _single_line(stable_path, 160))
        return stable_path

    def _photo_reference_config_value(self, item: dict[str, Any], source: str = "") -> Any:
        persisted_source = _path_text(source or item.get("source"), 1000)
        note = _single_line(item.get("note"), 500)
        if item.get("_config_format") != "dict":
            return f"{persisted_source} || {note}" if note else persisted_source
        return {
            "path": persisted_source,
            "note": note,
            "reference_roles": list(item.get("reference_roles") or []),
            "outfit_category": _single_line(item.get("outfit_category"), 40),
            "outfit_lock_default": bool(item.get("outfit_lock_default")),
            "scene_categories": list(item.get("scene_categories") or []),
            "preferred_preset": _single_line(item.get("preferred_preset"), 60),
        }

    def _photo_reference_asset_records(self) -> list[dict[str, Any]]:
        raw = self.data.get("photo_reference_assets") if isinstance(getattr(self, "data", None), dict) else []
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw if isinstance(raw, list) else []:
            normalized = normalize_reference_asset(item)
            if not normalized or normalized["id"] in seen:
                continue
            seen.add(normalized["id"])
            records.append(normalized)
        return records

    def _photo_reference_asset_path(self, asset: dict[str, Any]) -> str:
        source = _path_text(asset.get("path") or asset.get("source"), 1200)
        if not source:
            return ""
        resolver = getattr(self, "_photo_reference_local_path", None)
        if callable(resolver):
            try:
                resolved = _path_text(resolver(source), 1200)
                if resolved:
                    return resolved
            except Exception:
                pass
        try:
            path = Path(source).expanduser().resolve()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                return str(path)
        except (OSError, ValueError):
            pass
        return ""

    def _photo_reference_relation_owner_ids(self, requester_user_id: str, request_text: str) -> set[str]:
        owners: set[str] = set()
        raw_requester = _single_line(requester_user_id, 80)
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        if raw_requester:
            owners.add(raw_requester)
            if callable(canonicalizer):
                try:
                    canonical = _single_line(canonicalizer(raw_requester), 80)
                    if canonical:
                        owners.add(canonical)
                except Exception:
                    pass
        profiles = self.data.get("worldbook_member_profiles") if isinstance(getattr(self, "data", None), dict) else {}
        text = re.sub(r"\s+", "", str(request_text or "")).lower()
        if not isinstance(profiles, dict) or not text:
            return owners
        for profile_id, profile in profiles.items():
            if not isinstance(profile, dict) or profile.get("enabled", True) is False:
                continue
            tokens = [profile_id, profile.get("name"), *(profile.get("aliases") or []), *(profile.get("observed_names") or [])]
            if any(len(re.sub(r"\s+", "", str(token or ""))) >= 2 and re.sub(r"\s+", "", str(token or "")).lower() in text for token in tokens):
                owners.add(str(profile_id))
        return owners

    def _photo_reference_relation_asset_candidates(self, *, requester_user_id: str, request_text: str) -> list[dict[str, Any]]:
        owners = self._photo_reference_relation_owner_ids(requester_user_id, request_text)
        if not owners:
            return []
        candidates: list[dict[str, Any]] = []
        for asset in self._photo_reference_asset_records():
            if asset.get("scope") != "relation_user" or asset.get("owner_id") not in owners or asset.get("enabled") is False:
                continue
            path = self._photo_reference_asset_path(asset)
            if not path:
                continue
            roles = list(asset.get("reference_roles") or ("identity",))
            candidates.append({
                "id": asset.get("id"),
                "kind": "relation_user",
                "scope": "relation_user",
                "owner_id": asset.get("owner_id"),
                "path": path,
                "source": asset.get("path"),
                "title": asset.get("title"),
                "note": asset.get("note"),
                "tags": list(asset.get("tags") or []),
                "reference_roles": roles,
                "available_reference_roles": roles,
                "priority": max(650, _safe_int(asset.get("priority"), 0, -1000)),
                "metadata_source": "relation_user",
            })
        return candidates

    def _photo_reference_role_asset_candidates(self, *, request_text: str) -> list[dict[str, Any]]:
        """Resolve setting/relationship-card references only for an explicit role context.

        Role cards describe people other than Bot.  Loading their images for every
        selfie would make an otherwise single-person request ambiguous, so the
        asset is eligible only when the current request names the role/name or
        clearly asks for a group frame.
        """
        if not bool(runtime_persona_setting(self, "enable_bot_relationship_network", False)):
            return []
        cards = self._normalize_bot_relationship_cards(
            runtime_persona_setting(self, "bot_relationship_cards", [])
        )
        if not cards:
            return []
        request_compact = re.sub(r"\s+", "", str(request_text or "")).casefold()
        group_requested = _photo_group_request_matches(request_text)
        role_context: dict[str, dict[str, str]] = {}
        for raw_card in cards:
            parts = [_single_line(part, 200) for part in raw_card.split(" || ", 2)]
            role_name = parts[0] if parts else ""
            if not role_name:
                continue
            owner_id = normalize_reference_owner_id("relation_role", role_name)
            if not owner_id:
                continue
            relation = parts[1] if len(parts) > 1 else ""
            tokens = [role_name, relation]
            explicit_hit = any(
                len(re.sub(r"\s+", "", str(token or ""))) >= 2
                and re.sub(r"\s+", "", str(token or "")).casefold() in request_compact
                for token in tokens
            )
            if explicit_hit or group_requested:
                role_context[owner_id] = {
                    "role_name": role_name,
                    "relationship": relation,
                    "appearance": parts[2] if len(parts) > 2 else "",
                    "explicit_mention": "1" if explicit_hit else "0",
                }
        if not role_context:
            return []
        candidates: list[dict[str, Any]] = []
        for asset in self._photo_reference_asset_records():
            if asset.get("scope") != "relation_role" or asset.get("enabled") is False:
                continue
            owner_id = str(asset.get("owner_id") or "")
            context = role_context.get(owner_id)
            if not context:
                continue
            path = self._photo_reference_asset_path(asset)
            if not path:
                continue
            roles = list(asset.get("reference_roles") or ("identity",))
            candidates.append(
                {
                    "id": asset.get("id"),
                    "kind": "relation_role",
                    "scope": "relation_role",
                    "owner_id": owner_id,
                    "path": path,
                    "source": asset.get("path"),
                    "title": asset.get("title"),
                    "note": asset.get("note"),
                    "tags": list(asset.get("tags") or []),
                    "reference_roles": roles,
                    "available_reference_roles": roles,
                    "priority": max(700, _safe_int(asset.get("priority"), 0, -1000)),
                    "metadata_source": "relation_role",
                    "role_name": context["role_name"],
                    "relationship": context["relationship"],
                    "role_appearance": context["appearance"],
                    "role_explicit_mention": context["explicit_mention"] == "1",
                    "group_photo_requested": group_requested,
                }
            )
        return candidates

    def _photo_reference_knowledge_asset_candidates(self, *, request_text: str, ambient_context: str) -> list[dict[str, Any]]:
        selected = {
            str(item or "").strip()
            for item in (runtime_persona_setting(self, "roleplay_knowledge_source_ids", None) or [])
            if str(item or "").strip().startswith(("kb:", "doc:"))
        }
        if not selected:
            return []
        combined = re.sub(r"\s+", "", f"{request_text}\n{ambient_context}").lower()
        candidates: list[dict[str, Any]] = []
        for asset in self._photo_reference_asset_records():
            if asset.get("scope") != "knowledge" or asset.get("enabled") is False:
                continue
            owner = str(asset.get("owner_id") or "")
            if owner.startswith("doc:"):
                parts = owner.split(":", 2)
                if owner not in selected and (len(parts) < 3 or f"kb:{parts[1]}" not in selected):
                    continue
            elif owner not in selected:
                continue
            tokens = reference_asset_tokens(asset)
            if not tokens or not any(token in combined for token in tokens):
                continue
            path = self._photo_reference_asset_path(asset)
            if not path:
                continue
            roles = list(asset.get("reference_roles") or ("scene", "style"))
            candidates.append({
                "id": asset.get("id"),
                "kind": "knowledge_reference",
                "scope": "knowledge",
                "owner_id": owner,
                "path": path,
                "source": asset.get("path"),
                "title": asset.get("title"),
                "note": asset.get("note"),
                "tags": list(asset.get("tags") or []),
                "reference_roles": roles,
                "available_reference_roles": roles,
                "priority": max(520, _safe_int(asset.get("priority"), 0, -1000)),
                "metadata_source": "knowledge_reference",
                "knowledge_context_match": True,
            })
        return candidates
