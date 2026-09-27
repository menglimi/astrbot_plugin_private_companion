# -*- coding: utf-8 -*-
"""photo_generation 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（130 个方法 + 3 个模块级名字 + 0 个类级赋值 / 5773 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import threading
import uuid
from .conversation_prompt_section import (
    PhotoPromptContent,
    PromptDocument,
    PromptDocumentPart,
    PromptLabelStyle,
    PromptRenderMode,
    PromptSection,
    prompt_document,
    prompt_section,
    render_prompt_document,
)
from .helpers import (
    _normalize_photo_subject_owner,
    _path_text,
    _photo_group_request_matches,
    _redact_outbound_secrets,
    _safe_float,
    _safe_int,
    _single_line,
    _today_key,
    normalize_bot_relationship_cards,
)
from .persona_config import runtime_persona_setting
from .photo_prompt_context import _clip as _clip_photo_prompt_text
from .photo_reference_catalog import PhotoReference, build_daily_outfit_reference, load_catalog, project_reference_candidate
from .photo_reference_feedback import analyze_photo_reference_feedback
from .photo_reference_intent import (
    CONTINUITY_MODES,
    REFERENCE_ROLES,
    ReferenceIntent,
    analyze_indexed_reference_roles,
    analyze_reference_intent,
    explicitly_excludes_reference_outfit,
)
from .photo_reference_plan import PhotoReferencePlan, ReferenceFallback, build_photo_reference_plan
from .photo_reference_selection import (
    CandidateMatch,
    SelectionResult,
    parse_photo_reference_context_categories,
    select_photo_reference,
)
from .photo_wardrobe_decision import PhotoWardrobeDecision, PhotoWardrobeIntent, analyze_photo_wardrobe
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _persona_provider_id, _proactive_prompt_part
from .reference_asset_gate import ReferenceAssetGate, ReferenceAssetPlan
from .reference_assets import normalize_reference_asset, normalize_reference_owner_id, reference_asset_tokens
from .scene_context import infer_companion_scene_category
from .wardrobe_photo import resolve_daily_outfit_profile as resolve_wardrobe_daily_outfit_profile
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .logging_util import get_module_logger
from .proactive_message_photo_generation_entry_attempts import ProactiveMessagePhotoGenerationEntryAttemptsMixin
from .proactive_message_photo_generation_daily_outfit_generation import ProactiveMessagePhotoGenerationDailyOutfitGenerationMixin
from .proactive_message_photo_generation_continuity_prompt_policy import ProactiveMessagePhotoGenerationContinuityPromptPolicyMixin
from .proactive_message_photo_generation_trace_record_feedback import ProactiveMessagePhotoGenerationTraceRecordFeedbackMixin
from .proactive_message_photo_generation_reference_selection import ProactiveMessagePhotoGenerationReferenceSelectionMixin
from .proactive_message_photo_generation_reference_assets import ProactiveMessagePhotoGenerationReferenceAssetsMixin
from .proactive_message_photo_generation_scene_presets_generation import ProactiveMessagePhotoGenerationScenePresetsGenerationMixin
from .proactive_message_photo_generation_selfie_composition_guards import ProactiveMessagePhotoGenerationSelfieCompositionGuardsMixin
from .proactive_message_photo_generation_daily_outfit_prompt_hints import ProactiveMessagePhotoGenerationDailyOutfitPromptHintsMixin
from .proactive_message_photo_generation_reference_plan_tail import ProactiveMessagePhotoGenerationReferencePlanTailMixin

from .proactive_message_photo_generation_shared import (  # noqa: F401  (全族共享件)
    PhotoGenerationResult,  # noqa: F401
    _ExternalPhotoGenerationOutcome,  # noqa: F401
    _PHOTO_GENERATION_TRACE_FILE_LOCK,  # noqa: F401
    _now_ts,  # noqa: F401
    logger,
)


class ProactiveMessagePhotoGenerationMixin(ProactiveMessagePhotoGenerationReferencePlanTailMixin, ProactiveMessagePhotoGenerationDailyOutfitPromptHintsMixin, ProactiveMessagePhotoGenerationSelfieCompositionGuardsMixin, ProactiveMessagePhotoGenerationScenePresetsGenerationMixin, ProactiveMessagePhotoGenerationReferenceAssetsMixin, ProactiveMessagePhotoGenerationReferenceSelectionMixin, ProactiveMessagePhotoGenerationTraceRecordFeedbackMixin, ProactiveMessagePhotoGenerationContinuityPromptPolicyMixin, ProactiveMessagePhotoGenerationDailyOutfitGenerationMixin, ProactiveMessagePhotoGenerationEntryAttemptsMixin):
    """photo_generation 域（从 ProactiveMessageMixin 拆出）。"""
