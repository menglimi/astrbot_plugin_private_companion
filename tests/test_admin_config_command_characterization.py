# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin


class _Event:
    def __init__(self, *, private: bool = True, sender_id: str = "user-1") -> None:
        self._private = private
        self._sender_id = sender_id
        self.unified_msg_origin = "aiocqhttp:FriendMessage:user-1"

    def is_private_chat(self) -> bool:
        return self._private

    def get_sender_id(self) -> str:
        return self._sender_id


class _Harness(CommandHandlersMixin):
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.data: dict[str, Any] = {"manual_diagnosis_pending_config": {}}
        self.config: dict[str, Any] = {"example_setting": "old"}
        self.example_setting = "old"
        self.saved_sections: list[set[str]] = []
        self._data_lock = asyncio.Lock()

    def _can_manage_private_companion(self, _event: Any) -> bool:
        return self.allowed

    def _can_manage_group_companion(self, _event: Any) -> bool:
        return self.allowed

    @staticmethod
    def _management_denied_text() -> str:
        return "DENIED"

    @staticmethod
    def _companion_manual_config_specs() -> dict[str, dict[str, Any]]:
        return {"example_setting": {"type": "string"}}

    @staticmethod
    def _companion_manual_config_aliases() -> dict[str, str]:
        return {"示例配置": "example_setting", "example_setting": "example_setting"}

    @staticmethod
    def _companion_manual_config_key_from_alias(value: str) -> str:
        return {"示例配置": "example_setting", "example_setting": "example_setting"}.get(value, "")

    @staticmethod
    def _companion_manual_config_label(_key: str) -> str:
        return "示例配置"

    @staticmethod
    def _companion_manual_format_config_item_value(_key: str, value: Any) -> str:
        return str(value)

    @staticmethod
    def _companion_manual_values_equal(left: Any, right: Any) -> bool:
        return left == right

    @staticmethod
    def _companion_manual_normalize_config_value(_key: str, value: Any) -> tuple[bool, Any, str]:
        return True, str(value), ""

    def _companion_manual_current_config_value(self, key: str) -> Any:
        return getattr(self, key)

    async def _save_config_if_possible(self) -> bool:
        return True

    def _save_data_sync(self, *, sections: set[str], **_kwargs: Any) -> None:
        self.saved_sections.append(set(sections))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example_setting = new", ("example_setting", "new")),
        ("示例配置：新值", ("example_setting", "新值")),
        ("把 示例配置 改成 新值", ("example_setting", "新值")),
        ("", ("", "")),
    ],
)
def test_admin_setting_parser_characterization(raw: str, expected: tuple[str, str]) -> None:
    assert _Harness()._companion_manual_parse_setting_text(raw) == expected


@pytest.mark.asyncio
async def test_admin_setting_permission_text_and_no_persistence() -> None:
    harness = _Harness(allowed=False)

    response = await harness._companion_manual_apply_setting_command(_Event(), "示例配置 新值")

    assert response == "DENIED"
    assert harness.example_setting == "old"
    assert harness.saved_sections == []


@pytest.mark.asyncio
async def test_admin_setting_success_text_and_persistence_sections() -> None:
    harness = _Harness()

    response = await harness._companion_manual_apply_setting_command(_Event(), "示例配置 新值")

    assert response == "已修改并保存配置：\nexample_setting（示例配置）：由 old 改为 新值"
    assert harness.example_setting == "新值"
    assert harness.config["example_setting"] == "新值"
    assert harness.saved_sections[-1] == {"runtime_settings", "manual_diagnosis_pending_config"}


def test_admin_setting_cancel_text_and_persistence_section() -> None:
    harness = _Harness()
    event = _Event()
    key = harness._companion_manual_pending_key(event)
    harness.data["manual_diagnosis_pending_config"][key] = {"ts": 1}

    response = harness._companion_manual_cancel_pending_config(event)

    assert response == "已取消刚才的答疑配置建议。"
    assert harness.saved_sections == [{"manual_diagnosis_pending_config"}]


def test_companion_command_stops_after_admin_config_reply() -> None:
    """Lock the public handler's reply-before-stop ordering without changing main.py."""
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    permission_guard = source.index(
        "if (action in management_actions or bookshelf_password_output_requested)"
    )
    denied_reply = source.index("await self._reply(event, self._management_denied_text())", permission_guard)
    denied_stop = source.index("event.stop_event()", denied_reply)
    common_reply = source.index("if action not in deferred_actions:", denied_stop)
    common_stop = source.index("event.stop_event()", common_reply)

    assert permission_guard < denied_reply < denied_stop
    assert common_reply < common_stop
