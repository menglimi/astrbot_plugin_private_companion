from __future__ import annotations

import asyncio
from copy import deepcopy
import inspect
import math
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .story_authority import (
    STORY_HANDOFF_TARGET_PLUGIN_ID,
    StoryAuthorityError,
    story_authority_controller,
)
from .story_migration_contract import STORY_MIGRATION_OWNER_ID
from .story_handoff_part01 import (
    EnforcedStoryTarget,
    STORY_MIGRATION_COMMIT_KEY,
    STORY_MIGRATION_COMMIT_VERSION,
    STORY_SOURCE_PLUGIN_ID,
    _ABSENT,
    _MARKER_FIELDS,
    _TARGET_API_FAMILY,
    _TARGET_API_VERSION,
    _TARGET_DESCRIPTOR_FIELDS,
    _TARGET_LEDGER_VERSION,
    _TARGET_MIGRATION_CAPABILITIES,
    _TARGET_TASK_VERSIONS,
    _Target,
    _TargetChanged,
    _confirm_persisted_source_marker,
    _digest,
    _exact_dict,
    _fail,
    _fresh_target,
    _generation,
    _matches_marker,
    _matches_snapshot,
    _source_marker,
    _source_state,
    _target_status,
    _timestamp,
    _validate_backup,
    _validate_snapshot_identity,
    _validate_target_descriptor,
    _validate_target_status,
    preflight_story_handoff_sections,
    validate_story_migration_commit_marker,
)
from .story_handoff_part02 import (
    _abort_target_exact,
    _await_target_call,
    _block,
    _cleanup_before_marker,
    _persist_source_marker,
    _private_target,
    _replay_committed_marker,
    _verify_controller_marker,
    call_enforced_story_target,
    resolve_enforced_story_target,
)
from .story_handoff_part03 import (
    commit_story_handoff,
    resume_story_handoff,
)


__all__ = [
    "EnforcedStoryTarget",
    "STORY_MIGRATION_COMMIT_KEY",
    "STORY_MIGRATION_COMMIT_VERSION",
    "call_enforced_story_target",
    "commit_story_handoff",
    "preflight_story_handoff_sections",
    "resolve_enforced_story_target",
    "resume_story_handoff",
    "validate_story_migration_commit_marker",
]
