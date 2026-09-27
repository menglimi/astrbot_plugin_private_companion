# -*- coding: utf-8 -*-
from .event_dispatch_result_proactive_shared import (
    _SEGMENTED_COMMON_FILE_SUFFIXES,
    _SEGMENTED_GENERATED_PUNCTUATION_MARKER,
    _SEGMENTED_PROTECTED_FILE_SUFFIX_PATTERN,
    _SEGMENTED_PROTECTED_LITERAL_PATTERN,
    _SEGMENTED_WIDTH_VARIANT_GROUPS,
    _expand_segmented_width_variant_words,
)
from .event_dispatch_result_proactive_shared import _SEGMENTED_COMMON_FILE_SUFFIXES
from .event_dispatch_result_proactive_part03 import EventDispatchResultProactivePart03Mixin
from .event_dispatch_result_proactive_part02 import EventDispatchResultProactivePart02Mixin
from .event_dispatch_result_proactive_part01 import EventDispatchResultProactivePart01Mixin
class EventDispatchResultProactiveMixin(EventDispatchResultProactivePart01Mixin, EventDispatchResultProactivePart02Mixin, EventDispatchResultProactivePart03Mixin):
    """EventDispatchResultProactiveMixin（从 EventDispatchMixin 拆出）。"""
