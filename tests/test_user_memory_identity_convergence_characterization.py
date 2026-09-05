import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class _Harness(UserMemoryMixin):
    default_nickname = "你"

    @staticmethod
    def _format_display_name_rename_events(events, *, limit=3):
        return "；".join(
            f"{item.get('old')}→{item.get('new')}"
            for item in (events or [])[-limit:]
            if isinstance(item, dict)
        )


class _FailingObservedNames(list):
    def append(self, item):
        raise RuntimeError("injected observed-name write failure")


def test_identity_anchor_converges_aliases_without_mutating_archive() -> None:
    harness = _Harness()
    user = {
        "nickname": "阿岚",
        "profile_note": "喜欢安静聊天",
        "last_display_name": "岚岚",
        "observed_display_names": ["阿岚", "小岚", "小岚", "岚岚"],
        "unknown_v7": {"keep": [1, 2, 3]},
    }
    before = json.loads(json.dumps(user, ensure_ascii=False))

    anchor = harness._format_private_identity_anchor_for_prompt("u-1", user)

    assert "正在说话的人是 阿岚（ID：u-1）" in anchor
    assert "最近你可能会看到 TA 的显示名是 岚岚、小岚" in anchor
    assert "只使用“阿岚”" in anchor
    assert user == before


def test_display_name_observation_preserves_unknown_fields_across_restart() -> None:
    harness = _Harness()
    user = {
        "last_display_name": "旧名",
        "observed_display_names": ["更旧名"],
        "display_name_events": [{"old": "初名", "new": "更旧名", "ts": 1.0, "future": "kept"}],
        "unknown_v7": {"nested": ["keep"]},
    }

    harness._note_private_display_name_observation(user, "u-1", "新名", now=100.0)
    restarted = json.loads(json.dumps(user, ensure_ascii=False))
    harness._note_private_display_name_observation(restarted, "u-1", "重启后名", now=200.0)

    assert restarted["unknown_v7"] == {"nested": ["keep"]}
    assert restarted["display_name_events"][0]["future"] == "kept"
    assert restarted["display_name_events"][-1] == {"ts": 200.0, "old": "新名", "new": "重启后名"}
    assert restarted["observed_display_names"] == ["更旧名", "新名", "重启后名"]


def test_display_name_observation_fault_is_visible_and_keeps_legacy_partial_write() -> None:
    harness = _Harness()
    user = {
        "last_display_name": "旧名",
        "observed_display_names": _FailingObservedNames(["更旧名"]),
        "unknown_v7": "keep",
    }

    with pytest.raises(RuntimeError, match="injected observed-name write failure"):
        harness._note_private_display_name_observation(user, "u-1", "新名", now=100.0)

    assert user["last_display_name"] == "新名"
    assert user["display_name_events"] == [{"ts": 100.0, "old": "旧名", "new": "新名"}]
    assert user["unknown_v7"] == "keep"


def test_concurrent_display_name_observations_remain_bounded_and_well_formed() -> None:
    harness = _Harness()
    user = {"last_display_name": "seed", "unknown_v7": {"keep": True}}
    names = [f"name-{index}" for index in range(40)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda name: harness._note_private_display_name_observation(user, "u-1", name), names))

    assert user["last_display_name"] in names
    assert 1 <= len(user["observed_display_names"]) <= 8
    assert len(user["observed_display_names"]) == len(set(user["observed_display_names"]))
    assert len(user["display_name_events"]) <= 12
    assert all(set(event) >= {"ts", "old", "new"} for event in user["display_name_events"])
    assert user["unknown_v7"] == {"keep": True}
