# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import ipaddress
import socket
import time
import unicodedata
import zoneinfo
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

try:  # package import
    from .outbound_tag_registry import (
        _ESCAPED_NONSTANDARD_SELF_CLOSING_TAG_PATTERN,
        _NONSTANDARD_SELF_CLOSING_TAG_PATTERN,
        strip_own_tags,
    )
except ImportError:  # direct test/import from the plugin directory
    from outbound_tag_registry import (
        _ESCAPED_NONSTANDARD_SELF_CLOSING_TAG_PATTERN,
        _NONSTANDARD_SELF_CLOSING_TAG_PATTERN,
        strip_own_tags,
    )
try:  # package import
    from .helpers_part01 import (
        PHOTO_GENERATION_SCOPE_VALUES,
        _ADDRESS_SEPARATOR_PATTERN,
        _ADDRESS_TERM_STRIP_CHARS,
        _ADDRESS_TERM_TAIL_PATTERN,
        _GROUP_MESSAGE_URL_PATTERN,
        _GROUP_SHARE_BOILERPLATE_PATTERN,
        _GROUP_SHARE_MARKER_PATTERN,
        _SECRET_FIELD_PATTERN,
        _clean_address_term,
        _date_key,
        _day_start_ts,
        _group_link_message_context,
        _normalize_photo_subject_owner,
        _normalize_timezone_name,
        _normalize_timezone_setting,
        _now_ts,
        _path_text,
        _photo_group_request_matches,
        _photo_subject_owner_prompt_label,
        _record_unanswered_proactive,
        _reset_unanswered_proactive,
        _resolve_timezone_setting,
        _runtime_secret_values,
        _safe_float,
        _safe_int,
        _set_today_key_timezone,
        _single_address,
        _single_line,
        _split_address_terms,
        _today_key,
        _today_key_timezone,
        _unanswered_proactive_count,
        _url_host_is_public,
        normalize_bot_relationship_cards,
        normalize_photo_generation_scopes,
    )
except ImportError:  # direct test/import from the plugin directory
    from helpers_part01 import (
        PHOTO_GENERATION_SCOPE_VALUES,
        _ADDRESS_SEPARATOR_PATTERN,
        _ADDRESS_TERM_STRIP_CHARS,
        _ADDRESS_TERM_TAIL_PATTERN,
        _GROUP_MESSAGE_URL_PATTERN,
        _GROUP_SHARE_BOILERPLATE_PATTERN,
        _GROUP_SHARE_MARKER_PATTERN,
        _SECRET_FIELD_PATTERN,
        _clean_address_term,
        _date_key,
        _day_start_ts,
        _group_link_message_context,
        _normalize_photo_subject_owner,
        _normalize_timezone_name,
        _normalize_timezone_setting,
        _now_ts,
        _path_text,
        _photo_group_request_matches,
        _photo_subject_owner_prompt_label,
        _record_unanswered_proactive,
        _reset_unanswered_proactive,
        _resolve_timezone_setting,
        _runtime_secret_values,
        _safe_float,
        _safe_int,
        _set_today_key_timezone,
        _single_address,
        _single_line,
        _split_address_terms,
        _today_key,
        _today_key_timezone,
        _unanswered_proactive_count,
        _url_host_is_public,
        normalize_bot_relationship_cards,
        normalize_photo_generation_scopes,
    )
try:  # package import
    from .helpers_part02 import (
        _BINARY_TEXT_PREFIXES,
        _ESCAPED_GROUP_MEMBER_SAFETY_MARKER_PATTERN,
        _ESCAPED_HISTORY_MEDIA_MARKER_PATTERN,
        _GARBLED_TEXT_MARKERS,
        _GROUP_MEMBER_SAFETY_MARKER_PATTERN,
        _HISTORY_MEDIA_MARKER_NAME,
        _HISTORY_MEDIA_MARKER_PATTERN,
        _LEAKED_CHAT_EMOTION_CONTROL_PATTERN,
        _LEGACY_TAG_CANONICAL_ALIASES,
        _LEGACY_TAG_LABEL_ALIASES,
        _LEGACY_TAG_PATTERN,
        _MARKDOWN_CODE_SPAN_PATTERN,
        _MISSING,
        _OPTIONAL_MODEL_DEPENDENCIES,
        _PERSONALITY_SYNC_BLOCK_PATTERN,
        _PERSONALITY_SYNC_CLOSING_TAG_PATTERN,
        _PERSONALITY_SYNC_COMMENT_PATTERN,
        _PHOTO_TOOL_SILENT_SENTINEL_PATTERN,
        _TRUNCATED_PERSONALITY_SYNC_COMMENT_PATTERN,
        _format_history_media_marker,
        _has_history_media_marker,
        _missing_optional_model_dependency,
        _normalize_outbound_punctuation_flow,
        _semantic_text_compact,
        _strip_group_member_safety_markers,
        _strip_history_media_markers,
        _strip_internal_message_blocks,
        _strip_nonstandard_chat_control_tags,
        _strip_outbound_control_blocks,
        _strip_persisted_chat_control_tags,
        _strip_personality_sync_blocks,
        _text_looks_garbled,
        _text_similarity,
        normalize_legacy_tag_text,
    )
except ImportError:  # direct test/import from the plugin directory
    from helpers_part02 import (
        _BINARY_TEXT_PREFIXES,
        _ESCAPED_GROUP_MEMBER_SAFETY_MARKER_PATTERN,
        _ESCAPED_HISTORY_MEDIA_MARKER_PATTERN,
        _GARBLED_TEXT_MARKERS,
        _GROUP_MEMBER_SAFETY_MARKER_PATTERN,
        _HISTORY_MEDIA_MARKER_NAME,
        _HISTORY_MEDIA_MARKER_PATTERN,
        _LEAKED_CHAT_EMOTION_CONTROL_PATTERN,
        _LEGACY_TAG_CANONICAL_ALIASES,
        _LEGACY_TAG_LABEL_ALIASES,
        _LEGACY_TAG_PATTERN,
        _MARKDOWN_CODE_SPAN_PATTERN,
        _MISSING,
        _OPTIONAL_MODEL_DEPENDENCIES,
        _PERSONALITY_SYNC_BLOCK_PATTERN,
        _PERSONALITY_SYNC_CLOSING_TAG_PATTERN,
        _PERSONALITY_SYNC_COMMENT_PATTERN,
        _PHOTO_TOOL_SILENT_SENTINEL_PATTERN,
        _TRUNCATED_PERSONALITY_SYNC_COMMENT_PATTERN,
        _format_history_media_marker,
        _has_history_media_marker,
        _missing_optional_model_dependency,
        _normalize_outbound_punctuation_flow,
        _semantic_text_compact,
        _strip_group_member_safety_markers,
        _strip_history_media_markers,
        _strip_internal_message_blocks,
        _strip_nonstandard_chat_control_tags,
        _strip_outbound_control_blocks,
        _strip_persisted_chat_control_tags,
        _strip_personality_sync_blocks,
        _text_looks_garbled,
        _text_similarity,
        normalize_legacy_tag_text,
    )
try:  # package import
    from .helpers_part03 import (
        _flat_get,
        _memory_archive_warning,
        _redact_outbound_secrets,
        _set_into_config,
    )
except ImportError:  # direct test/import from the plugin directory
    from helpers_part03 import (
        _flat_get,
        _memory_archive_warning,
        _redact_outbound_secrets,
        _set_into_config,
    )
