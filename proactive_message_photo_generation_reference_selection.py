# -*- coding: utf-8 -*-
"""参考图候选打分与选择域。

由 tools/split_mixin_domain.py 从 proactive_message_photo_generation.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 860 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessagePhotoGenerationMixin）。
"""
from __future__ import annotations

import json
import re
from .conversation_prompt_section import (
    PromptDocument,
    PromptRenderMode,
    prompt_document,
    prompt_section,
    render_prompt_document,
)
from .helpers import _safe_float, _single_line
from .persona_config import runtime_persona_setting
from .photo_reference_catalog import PhotoReference, build_daily_outfit_reference, load_catalog, project_reference_candidate
from .photo_reference_intent import CONTINUITY_MODES, REFERENCE_ROLES, ReferenceIntent, analyze_reference_intent
from .photo_reference_selection import (
    CandidateMatch,
    SelectionResult,
    parse_photo_reference_context_categories,
    select_photo_reference,
)
from .photo_wardrobe_decision import PhotoWardrobeIntent, analyze_photo_wardrobe
from .proactive_message_photo_generation_shared import logger
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _persona_provider_id, _proactive_prompt_part
from dataclasses import replace
from typing import Any



class ProactiveMessagePhotoGenerationReferenceSelectionMixin:
    """参考图候选打分与选择域（从 ProactiveMessagePhotoGenerationMixin 拆出）。"""


    async def _photo_reference_candidates_async(
        self,
        *,
        allow_daily_outfit: bool = True,
        requester_user_id: str = "",
        request_text: str = "",
        ambient_context: str = "",
        scoped_only: bool = False,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        canonical_mode = runtime_persona_setting(self, "photo_reference_catalog", None) is not None
        if canonical_mode:
            catalog = tuple(runtime_persona_setting(self, "photo_reference_catalog", ()) or ())
        else:
            catalog = load_catalog(
                [],
                catalog_version=0,
                legacy_persona=runtime_persona_setting(
                    self, "photo_persona_reference_image_path", ""
                ),
                legacy_library=runtime_persona_setting(self, "photo_reference_library", []),
                preset_names=self._photo_generation_scene_presets().keys(),
            ).references
        updated_catalog = list(catalog)
        catalog_changed = False
        resolver = getattr(self, "_photo_reference_source_to_stable_path", None)
        for index, item in enumerate(catalog):
            if not isinstance(item, PhotoReference) or item.kind != "library":
                continue
            source = item.source
            path = self._photo_reference_local_path(source)
            if not path and re.match(r"^https?://", source, flags=re.I) and callable(resolver):
                try:
                    path = await resolver(source, stem=item.id)
                except Exception as exc:
                    logger.info(
                        "参考图库远程图片下载失败: item=%s error=%s",
                        item.id,
                        _single_line(exc, 120),
                    )
                if path:
                    updated_catalog[index] = replace(item, source=path)
                    catalog_changed = True
            if path:
                candidates.append(project_reference_candidate(item, resolved_source=path))
        if catalog_changed:
            setter = getattr(
                self,
                "_set_photo_reference_catalog_config" if canonical_mode else "_set_photo_reference_library_config",
                None,
            )
            if callable(setter):
                try:
                    payload: Any = updated_catalog
                    if not canonical_mode:
                        payload = [
                            {
                                "path": item.source,
                                "note": item.note,
                                "reference_roles": list(item.reference_roles),
                                "outfit_category": item.outfit_category,
                                "outfit_lock_default": item.outfit_lock_default,
                                "scene_categories": list(item.scene_categories),
                                "preferred_preset": item.preferred_preset,
                            }
                            for item in updated_catalog
                            if isinstance(item, PhotoReference) and item.kind == "library"
                        ]
                    result = setter(payload)
                    if hasattr(result, "__await__"):
                        result = await result
                    if result is False:
                        logger.info("参考图库远程图片已下载但配置保存返回失败")
                except Exception as exc:
                    logger.info(
                        "参考图库远程图片已下载但回写失败: %s",
                        _single_line(exc, 120),
                    )

        if allow_daily_outfit:
            outfit_path = self._daily_outfit_reference_image_path()
            if outfit_path:
                daily_reference = build_daily_outfit_reference(
                    outfit_path,
                    note="今天生成的外出穿搭；仅在画面明确承接今天外出、通勤、上学、逛街或展示当日穿搭时使用，在家、卧室、睡前、刚起床等场景不要使用",
                    preset_names=self._photo_generation_scene_presets().keys(),
                )
                candidates.append(project_reference_candidate(daily_reference, resolved_source=outfit_path))
        persona_path = await self._photo_persona_reference_image_path_async()
        if persona_path and not any(item.get("kind") == "persona" for item in candidates):
            persona = next(
                (
                    item
                    for item in catalog
                    if isinstance(item, PhotoReference) and item.kind == "persona"
                ),
                None,
            )
            if persona is not None:
                candidates.append(project_reference_candidate(persona, resolved_source=persona_path))
            else:
                candidates.append(
                    {
                        "id": "persona",
                        "kind": "persona",
                        "path": persona_path,
                        "source": persona_path,
                        "note": "Bot persona identity reference",
                        "reference_roles": ["identity"],
                        "available_reference_roles": ["identity"],
                        "priority": 400,
                        "metadata_source": "legacy_persona",
                        "outfit_lock_default": False,
                    }
                )
        candidates.extend(
            self._photo_reference_role_asset_candidates(
                request_text=request_text,
            )
        )
        candidates.extend(
            self._photo_reference_relation_asset_candidates(
                requester_user_id=requester_user_id,
                request_text=request_text,
            )
        )
        candidates.extend(
            self._photo_reference_knowledge_asset_candidates(
                request_text=request_text,
                ambient_context=ambient_context,
            )
        )
        if scoped_only:
            candidates = [
                item
                for item in candidates
                if item.get("kind") in {"relation_user", "relation_role", "knowledge_reference"}
            ]
        return candidates

    @staticmethod
    def _photo_reference_candidate_score(
        candidate: dict[str, Any],
        request_text: str,
        ambient_context: str,
        *,
        schedule_history_context: str = "",
        wardrobe_intent: PhotoWardrobeIntent,
        requested_outfit_category: str = "",
    ) -> float:
        request_context = re.sub(r"\s+", "", str(request_text or "")).lower()
        ambient = re.sub(r"\s+", "", str(ambient_context or "")).lower()
        schedule_history = re.sub(r"\s+", "", str(schedule_history_context or "")).lower()
        note = re.sub(r"\s+", "", str(candidate.get("note") or "")).lower()
        kind = candidate.get("kind")
        score = 2.0 if kind == "persona" else 1.0
        if kind == "relation_role":
            # A named role is a stronger signal than an unrelated persona or
            # library image; group intent is still a softer, contextual signal.
            score += 6.0 if candidate.get("role_explicit_mention") else 3.0
        categories = (
            ("home", ("在家", "家里", "居家", "宿舍", "公寓", "卧室", "房间", "客厅", "宅家", "居家室内", "室内日常")),
            ("sleep", ("睡衣", "睡前", "起床", "刚醒", "床上", "夜晚休息")),
            ("outdoor", ("外出", "通勤", "上学", "上班", "逛街", "商场", "街头", "旅行")),
            ("sport", ("运动", "健身", "跑步", "瑜伽", "泳装", "游泳")),
            ("formal", ("正式", "礼服", "宴会", "约会", "聚会", "舞会")),
            ("cos", ("cos", "cosplay", "角色扮演", "制服", "表演服")),
        )
        for name, words in categories:
            request_hit = any(word in request_context for word in words)
            ambient_hit = any(word in ambient for word in words)
            history_hit = any(word in schedule_history for word in words)
            note_hit = any(word in note for word in words)
            if (request_hit or ambient_hit) and candidate.get("kind") == "daily_outfit" and name in {"home", "sleep"}:
                score -= 20.0
            elif note_hit:
                if request_hit:
                    score += 12.0
                if ambient_hit:
                    score += 6.0
                if history_hit:
                    score += 2.0
        candidate_category = str(candidate.get("outfit_category") or "").strip().lower()
        outfit_bearing = "outfit" in set(candidate.get("reference_roles") or ())
        if not outfit_bearing:
            candidate_category = ""
        requested_category = (
            _single_line(requested_outfit_category, 40).lower()
            or wardrobe_intent.target_category
        )
        excluded_categories = set(wardrobe_intent.excluded_categories)
        if candidate_category and candidate_category in excluded_categories:
            score -= 40.0
        elif candidate_category and candidate_category == requested_category:
            score += 18.0
        elif requested_category and candidate_category and candidate_category != "daily_outfit":
            score -= 6.0
        if requested_category == "custom_outfit" and outfit_bearing and bool(candidate.get("outfit_lock_default")):
            score -= 8.0
        structured_scenes = {
            str(value or "").strip().lower()
            for value in (candidate.get("scene_categories") or [])
            if str(value or "").strip()
        }
        def scene_categories(text: str) -> set[str]:
            scenes: set[str] = set()
            if any(token in text for token in ("在家", "家里", "居家", "宿舍", "卧室", "居家室内")):
                scenes.add("home")
            if any(token in text for token in ("卧室", "床边", "睡前", "刚起床")):
                scenes.add("bedroom")
            if any(token in text for token in ("上学", "校园", "教室", "校门")):
                scenes.add("school")
            if any(token in text for token in ("外出", "通勤", "逛街", "街头", "旅行")):
                scenes.add("outdoor")
            if any(token in text for token in ("办公室", "办公", "公司", "工作场所", "office", "workplace")):
                scenes.add("office")
            if any(token in text for token in ("正式场合", "宴会", "婚礼", "舞会", "典礼", "formal event", "banquet")):
                scenes.add("formal_event")
            if any(token in text for token in ("运动", "健身", "跑步", "瑜伽", "球场", "体育馆", "gym", "sport")):
                scenes.add("sport")
            if any(token in text for token in ("海边", "海滩", "沙滩", "泳池", "beach", "seaside", "pool")):
                scenes.add("beach")
            return scenes

        if structured_scenes & scene_categories(request_context):
            score += 10.0
        if structured_scenes & scene_categories(ambient):
            score += 4.0
        if structured_scenes & scene_categories(schedule_history):
            score += 2.0
        structured_times = {
            str(value or "").strip().lower()
            for value in (candidate.get("time_categories") or [])
            if str(value or "").strip()
        }

        def time_categories(text: str) -> set[str]:
            times: set[str] = set()
            mappings = (
                ("morning", ("清晨", "早晨", "早上", "晨间", "morning", "sunrise")),
                ("daytime", ("白天", "日间", "daytime", "daylight")),
                ("afternoon", ("下午", "午后", "afternoon")),
                ("evening", ("傍晚", "黄昏", "日落", "evening", "sunset")),
                ("night", ("夜晚", "晚上", "深夜", "夜景", "night")),
                ("bedtime", ("睡前", "临睡", "bedtime")),
            )
            for category, tokens in mappings:
                if any(token in text for token in tokens):
                    times.add(category)
            return times

        if structured_times & time_categories(request_context):
            score += 8.0
        if structured_times & time_categories(ambient):
            score += 3.0
        if structured_times & time_categories(schedule_history):
            score += 1.0
        for token in re.split(r"[，,。；;、/|：:\s]+", note):
            if len(token) >= 2 and token in request_context:
                score += min(6.0, float(len(token)))
            elif len(token) >= 2 and token in ambient:
                score += min(3.0, float(len(token)) / 2.0)
            elif len(token) >= 2 and token in schedule_history:
                score += min(1.0, float(len(token)) / 4.0)
        return score

    @staticmethod
    def _photo_reference_selection_prompt_document(
        *,
        request_text: str,
        ambient_context: str,
        suggested_scene_preset: str,
        schedule_history_context: str,
        candidate_options: str,
    ) -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.photo_reference_selection.task",
                    title="人物参考图选择",
                    source="proactive_message",
                    content=(
                        "你在为角色生图选择一张人物参考图。结合最终画面需求中的日程、位置、当前场景和服装需求，按管理员给每张图的用途注释判断。\n"
                        "优先选择用途更具体且与当前场景兼容的参考图；只有没有更具体的场景或服装参考时，才选择基础人物身份图。\n"
                        "严格遵守候选的选用策略、排除场景与排除时间；条件不匹配时输出 0，不要为了使用参考图而曲解用户原话。\n"
                        "明确处于家里、卧室、睡前或刚起床时，优先在适用的居家服/睡衣参考中选择；只有明确外出、通勤、上学、逛街或展示今日穿搭时才选今日穿搭。\n"
                        "当前要求明确否定某类服装时，不得选择以该服装为职责的参考图；即使它是唯一候选，也应输出 0。普通换装或自定义衣服没有匹配参考时，可选身份图或输出 0，不要让旧衣服反向覆盖新要求。\n"
                        "用户原始要求高于环境上下文；两者冲突时必须按用户原始要求选图，不能让日程或位置覆盖用户明确要求。\n"
                        "若用户没有明确服装要求，但结构化场景预设给出了服装类别，且候选中存在同类别服装参考，优先选择该服装参考，不要改选基础身份图。结构化预设只用于补足空白，不得覆盖用户明确要求。\n"
                        "当天已发生日程只可作为较弱的经历、服装和连续性线索，不代表当前位置或当前活动。不得用历史中的旧地点覆盖当前环境；用户原始要求和当前环境始终优先于历史日程。\n"
                        "不要仅凭疲惫、揉眼睛、电脑桌等间接描述猜测地点或服装；场景不明确时保持保守，不要虚构居家或外出状态。\n"
                        "若候选带有“角色”和“关系”，且用户在本轮明确点名该角色或关系，优先选择对应的关系角色参考图；它只代表该角色本人，不要把该身份转移给 Bot。没有明确点名角色时，不要因为关系卡文字而选择关系角色参考图。\n"
                        "只输出候选编号，不要解释。"
                    ),
                ), mode=PromptRenderMode.BODY_ONLY),
                prompt_section(
                    key="background.photo_reference_selection.request",
                    title="最终画面需求",
                    source="proactive_message",
                    content=request_text,
                ),
                prompt_section(
                    key="background.photo_reference_selection.environment",
                    title="环境上下文",
                    source="proactive_message",
                    content=ambient_context or "无",
                ),
                prompt_section(
                    key="background.photo_reference_selection.preset",
                    title="结构化场景预设",
                    source="proactive_message",
                    content=suggested_scene_preset or "无",
                ),
                prompt_section(
                    key="background.photo_reference_selection.schedule",
                    title="当天已发生日程",
                    source="proactive_message",
                    content=schedule_history_context or "无",
                ),
                prompt_section(
                    key="background.photo_reference_selection.candidates",
                    title="候选参考图",
                    source="proactive_message",
                    content=candidate_options,
                ),
            ),
            metadata={"task": "photo_reference_selection"},
        )

    async def _select_photo_reference_candidate_async(
        self,
        workflow_kind: str,
        *,
        allow_daily_outfit: bool = True,
        requester_user_id: str = "",
        request_text: str = "",
        ambient_context: str = "",
        schedule_history_context: str = "",
        selection_context: str = "",
        suggested_scene_preset: str = "",
        continuity_key: str = "",
        wardrobe_intent: PhotoWardrobeIntent | None = None,
        trace_id: str = "",
        candidate_overrides: Any = None,
        selection_provider_id: str = "",
        selection_strict_provider: bool = False,
        return_selection_result: bool = False,
    ) -> dict[str, Any] | SelectionResult:
        def empty_selection(reason: str) -> dict[str, Any] | SelectionResult:
            if return_selection_result:
                return SelectionResult(None, (), "none", reason)
            return {}

        using_candidate_overrides = candidate_overrides is not None
        if not using_candidate_overrides and not bool(
            runtime_persona_setting(self, "enable_photo_reference_image", False)
        ):
            return empty_selection("reference_feature_disabled")
        normalized_workflow = str(workflow_kind or "").strip().lower()
        portrait_workflow = normalized_workflow in {"selfie", "portrait", "自拍", "人像"}
        scoped_context = bool(requester_user_id) or bool(
            self._photo_reference_knowledge_asset_candidates(
                request_text=request_text,
                ambient_context=ambient_context,
            )
        ) or bool(self._photo_reference_role_asset_candidates(request_text=request_text))
        if not portrait_workflow and not scoped_context and not using_candidate_overrides:
            return empty_selection("workflow_does_not_use_reference")
        if using_candidate_overrides:
            candidates = []
            for raw_candidate in candidate_overrides or ():
                if not isinstance(raw_candidate, dict):
                    continue
                candidate = self._normalize_photo_reference_candidate_metadata(dict(raw_candidate))
                if not candidate.get("path") and candidate.get("source"):
                    candidate["path"] = candidate["source"]
                candidates.append(candidate)
        else:
            try:
                candidates = await self._photo_reference_candidates_async(
                    allow_daily_outfit=allow_daily_outfit,
                    requester_user_id=requester_user_id,
                    request_text=request_text,
                    ambient_context=ambient_context,
                    scoped_only=not portrait_workflow,
                )
            except TypeError:
                # Keep compatibility with lightweight test/integration adapters that
                # still expose the original one-argument candidate loader.
                candidates = await self._photo_reference_candidates_async(
                    allow_daily_outfit=allow_daily_outfit,
                )
        if not candidates:
            return empty_selection("no_candidates")
        legacy_context = str(selection_context or "").strip()
        if legacy_context:
            looks_like_ambient_context = bool(
                re.search(
                    r"(?:^|[；;，,])\s*(?:时间|状态|当前日程|日程|情绪|可分享碎片|"
                    r"当前位置|当前场景|天气背景|今日穿搭|当天基础穿搭|当天穿搭|日常穿搭)\s*[：:]",
                    legacy_context,
                    flags=re.I,
                )
            )
            if not request_text and not ambient_context:
                if looks_like_ambient_context:
                    ambient_context = legacy_context
                else:
                    request_text = legacy_context
            elif not ambient_context:
                ambient_context = legacy_context
        wardrobe_intent = wardrobe_intent or analyze_photo_wardrobe(request_text)
        suggested_scene_preset = _single_line(suggested_scene_preset, 80)
        suggested_category = ""
        available_presets = self._photo_generation_scene_presets()
        if (
            not wardrobe_intent.target_category
            and suggested_scene_preset
            and suggested_scene_preset in available_presets
        ):
            suggested_category = self._photo_outfit_category_from_text(
                suggested_scene_preset
            )
        requested_category = wardrobe_intent.target_category or suggested_category
        excluded_categories = set(wardrobe_intent.excluded_categories)
        request_scenes, request_times, request_excluded_scenes, request_excluded_times = (
            parse_photo_reference_context_categories(request_text)
        )
        suggested_scenes, suggested_times, suggested_excluded_scenes, suggested_excluded_times = (
            parse_photo_reference_context_categories(suggested_scene_preset)
        )
        ambient_scenes, ambient_times, ambient_excluded_scenes, ambient_excluded_times = (
            parse_photo_reference_context_categories(ambient_context)
        )
        # Hard eligibility follows the strongest current signal. Historical schedule
        # text remains a weak score/prompt hint and must never override this turn.
        if request_scenes or request_excluded_scenes:
            requested_scene_categories = request_scenes
            excluded_scene_categories = request_excluded_scenes
        elif suggested_scenes or suggested_excluded_scenes:
            requested_scene_categories = suggested_scenes
            excluded_scene_categories = suggested_excluded_scenes
        else:
            requested_scene_categories = ambient_scenes
            excluded_scene_categories = ambient_excluded_scenes
        if request_times or request_excluded_times:
            requested_time_categories = request_times
            excluded_time_categories = request_excluded_times
        elif suggested_times or suggested_excluded_times:
            requested_time_categories = suggested_times
            excluded_time_categories = suggested_excluded_times
        else:
            requested_time_categories = ambient_times
            excluded_time_categories = ambient_excluded_times

        seen_candidate_ids: set[str] = set()
        for index, item in enumerate(candidates, start=1):
            base_id = _single_line(item.get("id"), 120) or f"candidate-{index}"
            candidate_id = base_id
            suffix = 2
            while candidate_id in seen_candidate_ids:
                candidate_id = f"{base_id}#{suffix}"
                suffix += 1
            item["id"] = candidate_id
            seen_candidate_ids.add(candidate_id)

        policy_result = select_photo_reference(
            {
                "request_text": request_text,
                "outfit_category": requested_category,
                "scene_categories": requested_scene_categories,
                "time_categories": requested_time_categories,
                "excluded_scene_categories": excluded_scene_categories,
                "excluded_time_categories": excluded_time_categories,
            },
            candidates,
        )
        policy_matches = {item.candidate_id: item for item in policy_result.candidates}
        candidate_policy_exclusions: dict[str, set[str]] = {}
        eligible_candidates: list[dict[str, Any]] = []
        for item in candidates:
            item_id = str(item.get("id") or "")
            reasons = set(policy_matches.get(item_id).excluded if item_id in policy_matches else ())
            candidate_policy_exclusions[item_id] = reasons
            if not reasons:
                eligible_candidates.append(item)

        scored_candidates = [
            (
                item,
                self._photo_reference_candidate_score(
                    item,
                    request_text,
                    ambient_context,
                    schedule_history_context=schedule_history_context,
                    wardrobe_intent=wardrobe_intent,
                    requested_outfit_category=requested_category,
                ),
            )
            for item in candidates
        ]
        def responsible_outfit_category(item: dict[str, Any]) -> str:
            if "outfit" not in set(item.get("reference_roles") or ()):
                return ""
            return str(item.get("outfit_category") or "").strip().lower()

        normal_scored = [
            pair
            for pair in scored_candidates
            if not candidate_policy_exclusions.get(str(pair[0].get("id") or ""))
            and responsible_outfit_category(pair[0]) not in excluded_categories
            and (
                not requested_category
                or not responsible_outfit_category(pair[0])
                or (
                    requested_category != "custom_outfit"
                    and responsible_outfit_category(pair[0]) == requested_category
                )
            )
        ]
        fallback = max(normal_scored, key=lambda pair: pair[1])[0] if normal_scored else None
        selected = fallback
        selection_source = "rule_fallback"
        selection_reason = "model_not_attempted" if eligible_candidates else "no_eligible_reference"
        model_reply = ""
        provider_id = _single_line(selection_provider_id, 160)
        provider_selector = getattr(self, "_task_provider", None)
        if not provider_id and callable(provider_selector):
            provider_id = provider_selector(
                _persona_provider_id(
                    self, "PHOTO_PROMPT_PROVIDER_ID", "photo_prompt_provider_id", "creative"
                ),
                _persona_provider_id(
                    self, "FAST_RESPONSE_PROVIDER_ID", "fast_response_provider_id", "fast"
                ),
                _persona_provider_id(
                    self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"
                ),
                _persona_provider_id(
                    self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                ),
            )
        llm_call = getattr(self, "_llm_call", None)
        specialized_candidate = any(
            bool(item.get("outfit_lock_default"))
            or any(role in {"outfit", "scene", "continuity"} for role in (item.get("reference_roles") or []))
            for item in eligible_candidates
        )
        needs_model_choice = len(eligible_candidates) > 1 or specialized_candidate
        model_attempted = False
        model_selected_id = ""
        if (request_text or ambient_context or schedule_history_context) and needs_model_choice and callable(llm_call):
            model_attempted = True
            selection_reason = "model_invalid_response"
            options = "\n".join(
                f"{index}. id={item['id']}；角色={_single_line(item.get('role_name'), 80) or 'Bot/未指定'}；"
                f"关系={_single_line(item.get('relationship'), 80) or 'none'}；职责={','.join(item.get('reference_roles') or []) or 'identity'}；"
                f"服装类别={_single_line(item.get('outfit_category'), 40) or 'none'}；"
                f"场景类别={','.join(sorted(str(value) for value in (item.get('scene_categories') or []) if str(value).strip())) or 'none'}；"
                f"时间类别={','.join(sorted(str(value) for value in (item.get('time_categories') or []) if str(value).strip())) or 'none'}；"
                f"选用策略={_single_line(item.get('selection_eligibility'), 40) or 'matching_only'}；"
                f"排除场景={','.join(sorted(str(value) for value in (item.get('excluded_scene_categories') or []) if str(value).strip())) or 'none'}；"
                f"排除时间={','.join(sorted(str(value) for value in (item.get('excluded_time_categories') or []) if str(value).strip())) or 'none'}；"
                f"默认锁服装={bool(item.get('outfit_lock_default'))}；注释={_single_line(item.get('note'), 360)}"
                for index, item in enumerate(eligible_candidates, start=1)
            )
            none_option = "\n0. 不使用这些候选参考图，按当前要求生成全新画面"
            prompt = render_prompt_document(
                self._photo_reference_selection_prompt_document(
                    request_text=_single_line(request_text, 1200),
                    ambient_context=_single_line(ambient_context, 800),
                    suggested_scene_preset=suggested_scene_preset,
                    schedule_history_context=_single_line(schedule_history_context, 1200),
                    candidate_options=f"{options}{none_option}",
                )
            )["user"]
            try:
                llm_kwargs = {
                    "max_tokens": 12,
                    "provider_id": provider_id or None,
                    "task": "photo_reference_selection",
                }
                if selection_strict_provider:
                    llm_kwargs["strict_provider"] = True
                raw = await llm_call(prompt, **llm_kwargs)
                model_reply = _single_line(raw, 80)
                match = re.search(r"(?<!\d)(\d{1,2})(?!\d)", model_reply)
                choice = int(match.group(1)) if match else -1
                if match and choice == 0:
                    selected = None
                    selection_source = "model"
                    selection_reason = "fresh_image_requested"
                elif match and 1 <= choice <= len(eligible_candidates):
                    proposed = eligible_candidates[choice - 1]
                    model_selected_id = str(proposed.get("id") or "")
                    proposed_category = responsible_outfit_category(proposed)
                    if proposed_category and proposed_category in excluded_categories:
                        selected = None
                        selection_source = "semantic_exclusion"
                        selection_reason = "model_selected_explicitly_excluded_outfit"
                    elif (
                        requested_category
                        and proposed_category
                        and (
                            requested_category == "custom_outfit"
                            or proposed_category != requested_category
                        )
                    ):
                        selected = fallback
                        selection_source = "semantic_user_request"
                        selection_reason = "model_selected_incompatible_user_outfit"
                    elif (
                        requested_category
                        and not proposed_category
                        and isinstance(fallback, dict)
                        and responsible_outfit_category(fallback) == requested_category
                    ):
                        selected = fallback
                        selection_source = "semantic_scene_preset"
                        selection_reason = "model_ignored_matching_outfit_reference"
                    else:
                        selected = proposed
                        selection_source = "model"
                        selection_reason = "valid_candidate_number"
                elif not model_reply:
                    selection_reason = "model_empty_response"
                elif match:
                    selection_reason = "model_candidate_out_of_range"
            except Exception as exc:
                selection_reason = f"model_error:{type(exc).__name__}"
                logger.info(
                    "参考图库模型选图失败，使用规则兜底: error=%s",
                    _single_line(exc, 120),
                )
        elif len(candidates) == 1 and not specialized_candidate:
            selection_source = "single_candidate"
            selection_reason = "only_one_candidate"
        elif not (request_text or ambient_context or schedule_history_context):
            selection_reason = "empty_selection_context"
        elif not callable(llm_call):
            selection_reason = "model_unavailable"

        score_summary = ",".join(
            f"{_single_line(item.get('id'), 40)}={score:g}"
            for item, score in scored_candidates
        )
        logger.info(
            "参考图库候选评分: fallback=%s scores=%s request=%s ambient=%s",
            fallback.get("id") if isinstance(fallback, dict) else "none",
            score_summary,
            _single_line(request_text, 180),
            _single_line(ambient_context, 120),
        )
        logger.info(
            "参考图库已选图: source=%s reason=%s id=%s kind=%s fallback=%s "
            "model_reply=%s path=%s note=%s candidates=%s",
            selection_source,
            selection_reason,
            selected.get("id") if isinstance(selected, dict) else "none",
            selected.get("kind") if isinstance(selected, dict) else "none",
            fallback.get("id") if isinstance(fallback, dict) else "none",
            model_reply or "-",
            _single_line(selected.get("path"), 260) if isinstance(selected, dict) else "-",
            _single_line(selected.get("note"), 160) if isinstance(selected, dict) else "-",
            len(candidates),
        )
        def structured_exclusions(item: dict[str, Any]) -> tuple[str, ...]:
            reasons = set(candidate_policy_exclusions.get(str(item.get("id") or ""), set()))
            if responsible_outfit_category(item) in excluded_categories:
                reasons.add("outfit")
            return tuple(sorted(reasons))

        structured_matches = tuple(
            CandidateMatch(
                candidate_id=str(item.get("id") or ""),
                score=float(score),
                rank=index,
                matched=tuple(policy_matches.get(str(item.get("id") or "")).matched) if str(item.get("id") or "") in policy_matches else tuple(),
                excluded=structured_exclusions(item),
                reason="formal_model_selection" if selection_source == "model" else selection_reason,
            )
            for index, (item, score) in enumerate(
                sorted(scored_candidates, key=lambda pair: (-pair[1], str(pair[0].get("id") or ""))),
                start=1,
            )
        )
        structured_selection = SelectionResult(
            selected=selected if isinstance(selected, dict) else None,
            candidates=structured_matches,
            selection_source=selection_source,
            selection_reason=selection_reason,
            fallback_id=str(fallback.get("id") or "") if isinstance(fallback, dict) else "",
            model_attempted=model_attempted,
            model_selected_id=model_selected_id,
        )
        await self._append_photo_generation_trace_event_async(
            trace_id,
            "reference_candidates",
            data={
                "candidates": [
                    {
                        "id": item.get("id"),
                        "kind": item.get("kind"),
                        "path": item.get("path"),
                        "roles": list(item.get("reference_roles") or ()),
                        "outfit_category": item.get("outfit_category"),
                        "outfit_lock_default": bool(item.get("outfit_lock_default")),
                        "scene_categories": list(item.get("scene_categories") or ()),
                        "time_categories": list(item.get("time_categories") or ()),
                        "excluded_scene_categories": list(item.get("excluded_scene_categories") or ()),
                        "excluded_time_categories": list(item.get("excluded_time_categories") or ()),
                        "selection_eligibility": item.get("selection_eligibility") or "matching_only",
                        "policy_exclusions": sorted(candidate_policy_exclusions.get(str(item.get("id") or ""), set())),
                        "metadata_source": item.get("metadata_source"),
                        "score": score,
                    }
                    for item, score in scored_candidates
                ],
                "rule_fallback_id": fallback.get("id") if isinstance(fallback, dict) else "",
                "selected_id": selected.get("id") if isinstance(selected, dict) else "",
                "model_reply": model_reply,
                "selection_source": selection_source,
                "selection_reason": selection_reason,
                "selection_result": structured_selection.to_dict(),
                "schedule_history_context": _single_line(schedule_history_context, 800),
                "schedule_history_used": bool(str(schedule_history_context or "").strip()),
            },
        )
        if return_selection_result:
            return structured_selection
        return self._normalize_photo_reference_candidate_metadata(selected) if isinstance(selected, dict) else {}

    @staticmethod
    def _photo_reference_intent_prompt_document(request_text: str) -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.photo_reference_intent",
                    title="参考图职责识别",
                    source="proactive_message",
                    template=(
                        "分析用户对显式参考图的职责要求，只输出一个 JSON 对象：\n"
                        '{{"requested_roles":[],"excluded_roles":[],"continuity_mode":"ambiguous","confidence":0.0}}\n'
                        "roles 只能是 identity、outfit、pose、scene、style、continuity、source。\n"
                        "continuity_mode 只能是 continuation、edit、new_topic、ambiguous。\n"
                        "否定表达放进 excluded_roles，不能同时作为 requested_roles。\n"
                        "无法确定时 confidence 必须低于 0.7；不要猜测服装、场景或连续性。\n\n"
                        "用户要求：{request_text}"
                    ),
                    variables={"request_text": request_text},
                ), mode=PromptRenderMode.BODY_ONLY),
            ),
            metadata={"task": "photo_reference_intent"},
        )

    async def _analyze_photo_reference_intent_async(
        self,
        request_text: str,
        *,
        workflow_kind: str,
        has_explicit_reference: bool,
    ) -> ReferenceIntent:
        rule_intent = analyze_reference_intent(
            request_text,
            has_explicit_reference=has_explicit_reference,
            workflow_kind=workflow_kind,
        )
        llm_call = getattr(self, "_llm_call", None)
        if (
            not has_explicit_reference
            or rule_intent.source != "conservative"
            or not callable(llm_call)
        ):
            return rule_intent
        compact_request = _single_line(request_text, 1200).lower().strip(" ，,。.!！?？；;")
        if re.fullmatch(
            r"(?:参考(?:一下|下)?|参考(?:这个|这张|这张图)(?:一下)?|"
            r"照着(?:这个|这张|这张图)(?:来|画)?|按(?:照)?(?:这个|这张|这张图)(?:来|画)?)",
            compact_request,
        ):
            return rule_intent

        provider_selector = getattr(self, "_task_provider", None)
        provider_id = ""
        if callable(provider_selector):
            provider_id = provider_selector(
                _persona_provider_id(
                    self, "PHOTO_PROMPT_PROVIDER_ID", "photo_prompt_provider_id", "creative"
                ),
                _persona_provider_id(
                    self, "FAST_RESPONSE_PROVIDER_ID", "fast_response_provider_id", "fast"
                ),
                _persona_provider_id(
                    self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"
                ),
                _persona_provider_id(
                    self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                ),
            )

        prompt = render_prompt_document(
            self._photo_reference_intent_prompt_document(
                _single_line(request_text, 1200)
            )
        )["user"]
        try:
            raw = await llm_call(
                prompt,
                max_tokens=180,
                provider_id=provider_id or None,
                task="photo_reference_intent",
            )
            match = re.search(r"\{[\s\S]*\}", str(raw or ""))
            payload = json.loads(match.group(0)) if match else {}
            if not isinstance(payload, dict):
                return rule_intent
            requested_set = {
                str(role or "").strip().lower()
                for role in (payload.get("requested_roles") or [])
            }
            excluded_set = {
                str(role or "").strip().lower()
                for role in (payload.get("excluded_roles") or [])
            }
            requested = tuple(
                role
                for role in REFERENCE_ROLES
                if role in requested_set and role not in excluded_set
            )
            excluded = tuple(role for role in REFERENCE_ROLES if role in excluded_set)
            mode = _single_line(payload.get("continuity_mode"), 30).lower()
            if mode not in CONTINUITY_MODES:
                mode = "ambiguous"
            confidence = _safe_float(payload.get("confidence"), 0.0, 0.0, 1.0)
        except Exception as exc:
            logger.debug(
                "参考职责模型解析失败，使用保守规则: %s",
                _single_line(exc, 120),
            )
            return rule_intent
        if confidence < 0.7:
            return ReferenceIntent(("identity",), (), "ambiguous", confidence, "model_conservative")
        return ReferenceIntent(requested or ("identity",), excluded, mode, confidence, "model")

    async def _select_photo_reference_image_async(
        self,
        workflow_kind: str,
        *,
        allow_daily_outfit: bool = True,
        request_text: str = "",
        ambient_context: str = "",
        selection_context: str = "",
        suggested_scene_preset: str = "",
    ) -> str:
        """Return the selected reference path for legacy image-only callers."""
        selected = await self._select_photo_reference_candidate_async(
            workflow_kind,
            allow_daily_outfit=allow_daily_outfit,
            request_text=request_text,
            ambient_context=ambient_context,
            selection_context=selection_context,
            suggested_scene_preset=suggested_scene_preset,
        )
        return str(selected.get("path") or "") if isinstance(selected, dict) else ""
