# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from quart import Quart

from astrbot_plugin_private_companion.core_store import CoreStoreMixin
from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_GROUP_ID = "8EC9DA9653F094D2D9CC640B6EC225C0"
OFFICIAL_MEMBER_ID = "F05AC3C572EC7FAB4C9A552CF91C651A"
OFFICIAL_GROUP_UMO = f"QBot4012710235:GroupMessage:{OFFICIAL_GROUP_ID}"


class _GroupIdentityHarness(CoreStoreMixin, EventDispatchMixin):
    pass


class _OfficialGroupEvent:
    def __init__(self, *, private: bool = False, expose_getter: bool = True) -> None:
        self.unified_msg_origin = OFFICIAL_GROUP_UMO
        self.session_id = OFFICIAL_GROUP_ID
        self.message_type = SimpleNamespace(name="GROUP")
        self.message_obj = SimpleNamespace(
            group_id=OFFICIAL_GROUP_ID,
            raw_message={
                "group_openid": OFFICIAL_GROUP_ID,
                "openid": OFFICIAL_MEMBER_ID,
                "user_id": OFFICIAL_MEMBER_ID,
                "event_type": "GROUP_AT_MESSAGE_CREATE",
            },
        )
        self._private = private
        self._expose_getter = expose_getter

    def is_private_chat(self) -> bool:
        return self._private

    def get_group_id(self) -> str:
        if not self._expose_getter:
            raise RuntimeError("getter unavailable")
        return OFFICIAL_GROUP_ID

    @staticmethod
    def get_sender_id() -> str:
        return OFFICIAL_MEMBER_ID


class _PagePlugin:
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self.data: dict = {"groups": {}}
        self.group_whitelist_ids: list[str] = []
        self.group_blacklist_ids: list[str] = []
        self.expression_group_learning_source_ids: list[str] = []
        self.expression_group_application_ids: list[str] = []

    _normalize_group_identity_id = staticmethod(CoreStoreMixin._normalize_group_identity_id)

    def _get_group(self, group_id: str) -> dict:
        return self.data["groups"].setdefault(group_id, {"group_id": group_id, "enabled": True})

    @staticmethod
    def _save_data_sync(**_kwargs) -> None:
        return None


class QqOfficialGroupIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _GroupIdentityHarness()

    def test_sid_group_umo_normalizes_to_opaque_session_id(self) -> None:
        self.assertEqual(
            OFFICIAL_GROUP_ID,
            self.harness._normalize_group_identity_id(OFFICIAL_GROUP_UMO),
        )
        self.assertEqual(
            [OFFICIAL_GROUP_ID, "987654321"],
            self.harness._parse_group_id_list([OFFICIAL_GROUP_UMO, OFFICIAL_GROUP_ID, "987654321"]),
        )

    def test_event_extracts_group_openid_without_numeric_assumption(self) -> None:
        self.assertEqual(
            OFFICIAL_GROUP_ID,
            self.harness._extract_group_id_from_event(_OfficialGroupEvent()),
        )
        self.assertEqual(
            OFFICIAL_GROUP_ID,
            self.harness._extract_group_id_from_event(_OfficialGroupEvent(expose_getter=False)),
        )

    def test_private_event_never_uses_group_like_payload(self) -> None:
        self.assertEqual("", self.harness._extract_group_id_from_event(_OfficialGroupEvent(private=True)))

    def test_group_scope_uses_one_canonical_key_even_with_sender_and_full_umo(self) -> None:
        event = _OfficialGroupEvent()
        self.assertEqual(f"group:{OFFICIAL_GROUP_ID}", self.harness._event_scope_key(event))

        event.message_obj.raw_message.pop("group_openid")
        event.message_obj.raw_message["group_id"] = OFFICIAL_GROUP_UMO
        event.is_private_chat = lambda: True
        self.assertEqual(f"group:{OFFICIAL_GROUP_ID}", self.harness._event_scope_key(event))

    def test_friend_umo_stays_private_when_group_like_payload_is_present(self) -> None:
        event = _OfficialGroupEvent()
        event.unified_msg_origin = f"QBot4012710235:FriendMessage:{OFFICIAL_MEMBER_ID}"
        event.is_private_chat = lambda: False
        self.assertEqual("", self.harness._extract_group_id_from_event(event))
        self.assertEqual(
            f"private:{OFFICIAL_MEMBER_ID}",
            self.harness._event_scope_key(event),
        )


class QqOfficialGroupPageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app = Quart(__name__)
        self.plugin = _PagePlugin()
        self.api = PrivateCompanionPageApi(self.plugin)
        self.api._group_summary = lambda group_id, group: {
            "group_id": group_id,
            "name": group.get("manual_group_name") or group.get("name") or "",
            "manual_group_name": group.get("manual_group_name") or "",
        }

    async def test_webui_accepts_full_umo_and_persists_manual_name(self) -> None:
        async with self.app.test_request_context(
            "/group/update",
            method="POST",
            json={"group_id": OFFICIAL_GROUP_UMO, "group_name": "官方测试群", "enabled": True},
        ):
            result = await self.api.update_group()

        self.assertTrue(result["success"])
        self.assertEqual(OFFICIAL_GROUP_ID, result["data"]["group_id"])
        group = self.plugin.data["groups"][OFFICIAL_GROUP_ID]
        self.assertEqual("官方测试群", group["manual_group_name"])
        self.assertEqual("官方测试群", group["name"])
        self.assertEqual("manual", group["group_name_source"])

    async def test_clear_observation_preserves_manual_name(self) -> None:
        self.plugin.data["groups"][OFFICIAL_GROUP_ID] = {
            "group_id": OFFICIAL_GROUP_ID,
            "enabled": True,
            "manual_group_name": "官方测试群",
            "name": "官方测试群",
            "group_name": "官方测试群",
            "recent_messages": [{"text": "旧消息"}],
        }
        async with self.app.test_request_context(
            "/group/update",
            method="POST",
            json={"group_id": OFFICIAL_GROUP_ID, "clear_observation": True},
        ):
            result = await self.api.update_group()

        self.assertTrue(result["success"])
        group = self.plugin.data["groups"][OFFICIAL_GROUP_ID]
        self.assertEqual("官方测试群", group["manual_group_name"])
        self.assertEqual("官方测试群", group["name"])
        self.assertEqual([], group["recent_messages"])

    async def test_manual_group_name_can_be_cleared(self) -> None:
        self.plugin.data["groups"][OFFICIAL_GROUP_ID] = {
            "group_id": OFFICIAL_GROUP_ID,
            "enabled": True,
            "manual_group_name": "旧名称",
            "name": "旧名称",
            "group_name": "旧名称",
            "group_name_source": "manual",
        }
        async with self.app.test_request_context(
            "/group/update",
            method="POST",
            json={"group_id": OFFICIAL_GROUP_ID, "group_name": ""},
        ):
            result = await self.api.update_group()

        self.assertTrue(result["success"])
        group = self.plugin.data["groups"][OFFICIAL_GROUP_ID]
        self.assertEqual("", group["manual_group_name"])
        self.assertNotIn("name", group)
        self.assertNotIn("group_name", group)
        self.assertNotIn("group_name_source", group)

    async def test_delete_group_cleans_equivalent_umo_expression_scopes(self) -> None:
        self.plugin.data["groups"][OFFICIAL_GROUP_ID] = {
            "group_id": OFFICIAL_GROUP_ID,
            "enabled": True,
        }
        self.plugin.expression_group_learning_source_ids = [OFFICIAL_GROUP_UMO, "other-group"]
        self.plugin.expression_group_application_ids = [OFFICIAL_GROUP_UMO]
        self.api._save_config_if_possible = AsyncMock(return_value=True)

        async with self.app.test_request_context(
            "/group/delete",
            method="POST",
            json={"group_id": OFFICIAL_GROUP_ID},
        ):
            result = await self.api.delete_group()

        self.assertTrue(result["success"])
        self.assertEqual(["other-group"], self.plugin.expression_group_learning_source_ids)
        self.assertEqual([], self.plugin.expression_group_application_ids)

    async def test_opaque_group_id_does_not_call_onebot_group_api(self) -> None:
        group = {"group_id": OFFICIAL_GROUP_ID, "enabled": True}
        self.plugin.data["groups"][OFFICIAL_GROUP_ID] = group
        self.api._page_call_onebot_action = AsyncMock()

        await self.api._refresh_group_names_from_platform([(OFFICIAL_GROUP_ID, group)], force=True)

        self.api._page_call_onebot_action.assert_not_awaited()

    def test_both_webui_copies_expose_group_name_editor(self) -> None:
        panel = ROOT / "pages" / "companion-panel"
        mirrored = ROOT / "pages" / "陪伴面板"
        for filename in ("index.html", "app.js", "app.css"):
            self.assertEqual(
                (panel / filename).read_bytes(),
                (mirrored / filename).read_bytes(),
            )
        html = (panel / "index.html").read_text(encoding="utf-8")
        script = (panel / "app.js").read_text(encoding="utf-8")
        self.assertIn('name="group_name"', html)
        self.assertIn('id="groupNameForm"', script)
        self.assertIn('group_name: String(form.get("group_name")', script)


if __name__ == "__main__":
    unittest.main()
