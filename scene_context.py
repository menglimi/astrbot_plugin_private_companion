# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import re
from typing import Any

from .helpers import _flat_get, _now_ts, _path_text, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    prompt_section,
    render_prompt_sections,
)


from .scene_context_shared import (
    SCENE_CONTEXT_VERSION,
    _scene_temperature_facts,
    _temperature_number,
    infer_companion_scene_category,
)
from .scene_context_shared import SCENE_CONTEXT_VERSION
from .scene_context_part04 import SceneContextPart04Mixin
from .scene_context_part03 import SceneContextPart03Mixin
from .scene_context_part02 import SceneContextPart02Mixin
from .scene_context_part01 import SceneContextPart01Mixin
class SceneContextMixin(SceneContextPart01Mixin, SceneContextPart02Mixin, SceneContextPart03Mixin, SceneContextPart04Mixin):
    """Build a read-only life-context snapshot shared by visual integrations."""
