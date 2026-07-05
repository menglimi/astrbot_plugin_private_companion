from __future__ import annotations

import asyncio
from typing import Any
import pytest

from astrbot_pc_pr.helpers import _strip_internal_message_blocks, _strip_outbound_control_blocks
from astrbot_pc_pr.tts_enhancement import TtsEnhancementMixin
from astrbot_pc_pr.group_observation import GroupObservationMixin

# Test Behavior 1: helpers._strip_internal_message_blocks and _strip_outbound_control_blocks
def test_strip_internal_and_outbound_control_blocks():
    # Input with <think> and <reasoning> blocks
    input_text = "Hello! <think>Some thoughts</think> Nice day. <reasoning>Why it is nice</reasoning> Goodbye!"
    
    # _strip_internal_message_blocks replaces <think> and <reasoning> blocks with empty string and normalizes spaces
    res_internal = _strip_internal_message_blocks(input_text)
    assert res_internal == "Hello! Nice day. Goodbye!"
    
    # _strip_outbound_control_blocks removes <think> and <reasoning> and keeps formatting but strips extra newlines
    res_outbound = _strip_outbound_control_blocks(input_text)
    assert "Hello!" in res_outbound
    assert "Nice day." in res_outbound
    assert "Goodbye!" in res_outbound
    assert "think" not in res_outbound
    assert "reasoning" not in res_outbound
    assert res_outbound.strip() == "Hello!  Nice day.  Goodbye!"


# Test Behavior 2: tts_enhancement.TTSEnhancementMixin._sanitize_tts_spoken_text
class DummyTTSEnhancement(TtsEnhancementMixin):
    def __init__(self) -> None:
        self.tts_voice_language = "ja"

    def _tts_provider_allows_emotion_tags(self, kind: str) -> bool:
        return kind in {"fishaudio", "gsv"}

def test_sanitize_tts_spoken_text():
    dummy = DummyTTSEnhancement()
    
    # Punctuation-only texts should return empty string
    assert dummy._sanitize_tts_spoken_text("..", provider_kind="openai") == ""
    assert dummy._sanitize_tts_spoken_text("……", provider_kind="openai") == ""
    assert dummy._sanitize_tts_spoken_text(",, ，，", provider_kind="openai") == ""
    assert dummy._sanitize_tts_spoken_text("   ", provider_kind="openai") == ""
    
    # Non-punctuation-only text should remain
    assert dummy._sanitize_tts_spoken_text("hello", provider_kind="openai") == "hello"
    assert dummy._sanitize_tts_spoken_text("你好", provider_kind="openai") == "你好"


# Test Behavior 3: GroupObservationMixin._mark_group_background_retry
class DummyGroupObservation(GroupObservationMixin):
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self.groups = {}
        self.saved_data = False
        # Mixin might check these configurations
        self.group_slang_summary_minutes = 360

    def _get_group(self, group_id: str) -> dict[str, Any]:
        if group_id not in self.groups:
            self.groups[group_id] = {}
        return self.groups[group_id]

    def _save_data_sync(self) -> None:
        self.saved_data = True

@pytest.mark.asyncio
async def test_mark_group_background_retry_invalid_json():
    dummy = DummyGroupObservation()
    group_id = "test_group_123"
    
    # Preset some states to make sure they get cleared
    group = dummy._get_group(group_id)
    group["group_slang_retry_after"] = 999999.0
    group["group_slang_last_error"] = "old_error"
    group["group_slang_running_at"] = 123456.0
    group["last_slang_summary_at"] = 0.0
    
    now_ts = 150000.0
    
    await dummy._mark_group_background_retry(
        group_id=group_id,
        task="group_slang",
        now=now_ts,
        error="invalid_json"
    )
    
    # Verify states are cleared and last_slang_summary_at is now_ts
    assert group["group_slang_retry_after"] == 0
    assert group["group_slang_last_error"] == ""
    assert group["group_slang_running_at"] == 0
    assert group["last_slang_summary_at"] == now_ts
    assert dummy.saved_data is True

@pytest.mark.asyncio
async def test_mark_group_background_retry_other_error():
    dummy = DummyGroupObservation()
    group_id = "test_group_456"
    
    group = dummy._get_group(group_id)
    group["group_slang_retry_after"] = 0
    group["group_slang_last_error"] = ""
    group["group_slang_running_at"] = 123456.0
    group["last_slang_summary_at"] = 100.0
    
    now_ts = 150000.0
    
    await dummy._mark_group_background_retry(
        group_id=group_id,
        task="group_slang",
        now=now_ts,
        error="some_other_error"
    )
    
    # For other errors, it should set a retry cooldown (10 minutes minimum, up to 30 minutes)
    assert group["group_slang_retry_after"] > now_ts
    assert group["group_slang_last_error"] == "some_other_error"
    assert group["group_slang_running_at"] == 0
    # last_slang_summary_at should not be modified
    assert group["last_slang_summary_at"] == 100.0
    assert dummy.saved_data is True
