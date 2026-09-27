# -*- coding: utf-8 -*-
# 门面保留 random / _now_ts 名字（拆分前定义在宿主）：
# tests patch("...creative._now_ts") / patch.object(creative.random, ...) 需命中。
import random

from .helpers import _now_ts
from .creative_shared import (
    DEFAULT_AI_DAILY_NEWS_SOURCE,
    DEFAULT_NEWS_SOURCES,
    LEGACY_DEFAULT_NEWS_SOURCES,
    PREVIOUS_TECH_DEFAULT_NEWS_SOURCES,
    _ALMANAC_JI,
    _ALMANAC_YI,
    _LUNAR_DAY_NAMES,
    _LUNAR_MONTH_NAMES,
    _PLATFORM_DISPLAY_NAMES,
    _SOLAR_TERM_DATES,
    _persona_provider_id,
    _render_creative_labeled_section,
    _render_creative_prompt,
)
from .creative_shared import DEFAULT_AI_DAILY_NEWS_SOURCE
from .creative_postgen_edit_rebuild import CreativePostgenEditRebuildMixin
from .creative_persona_memory_context import CreativePersonaMemoryContextMixin
from .creative_generation_core import CreativeGenerationCoreMixin
from .creative_cover import CreativeCoverMixin
from .creative_config_state import CreativeConfigStateMixin
from .creative_chunkgen_project import CreativeChunkgenProjectMixin
from .creative_advance_share import CreativeAdvanceShareMixin
class CreativeMixin(CreativeAdvanceShareMixin, CreativeChunkgenProjectMixin, CreativeConfigStateMixin, CreativeCoverMixin, CreativeGenerationCoreMixin, CreativePersonaMemoryContextMixin, CreativePostgenEditRebuildMixin):
    """创作系统"""

    # ============================================================
    # Story Bible / Memory Pool / Outline / Characters
    # ============================================================

    # ============================================================
    # Outline Generation
    # ============================================================

    # ============================================================
    # Quality Review
    # ============================================================

    # ============================================================
    # Post-Generation Extraction
    # ============================================================

    # ============================================================
    # Manual Edit
    # ============================================================

    # ============================================================
    # Core Chunk Generation (with story_bible + outline + review)
    # ============================================================
