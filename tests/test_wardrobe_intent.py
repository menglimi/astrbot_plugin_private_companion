# -*- coding: utf-8 -*-
"""本会话明确换装（作者的 dialogue_outfit_override）：读取接管与写入。

P0：只读接管 —— 有换装意图时不再把轮换裁决出的那一套标成「当前着装」。
P1：写入 —— 模型工具经 _wardrobe_set_intent 写进作者那同一个 key。
"""

from __future__ import annotations

import json
import unittest

from astrbot_plugin_private_companion.helpers import _now_ts, _today_key
from astrbot_plugin_private_companion.wardrobe import (
    WARDROBE_PROMPT_MAX_CHARS,
    normalize_wardrobe_items,
    normalize_wardrobe_outfits,
)
from astrbot_plugin_private_companion.wardrobe_runtime import (
    WARDROBE_INTENT_KEY,
    WARDROBE_INTENT_SOURCE_MODEL,
    WARDROBE_INTENT_TTL_SECONDS,
    WardrobeMixin,
)

USER = {"user_id": "u-owner"}


class _IntentHarness(WardrobeMixin):
    """尽量忠实复刻作者那份 override 的过期与身份判定（daily_state.py:11425）。"""

    def __init__(self, *, override=None, items=None, outfits=None, mode="select", detail="full"):
        self.config = {
            "enable_wardrobe": True,
            "enable_wardrobe_prompt": True,
            "wardrobe_tendency": "偏爱宽松针织",
            "wardrobe_items": list(items or []),
            "wardrobe_outfits": list(outfits or []),
            "wardrobe_outfit_mode": mode,
            "wardrobe_injection_detail": detail,
            "wardrobe_outfit_rotation_days": 7,
        }
        self.data: dict = {}
        self.saved_sections: list[set] = []
        if override is not None:
            snapshot = dict(override)
            snapshot.setdefault("date", _today_key())
            snapshot.setdefault("expires_at", _now_ts() + WARDROBE_INTENT_TTL_SECONDS)
            self.data[WARDROBE_INTENT_KEY] = snapshot

    def persona_setting(self, key: str, default: object = None) -> object:
        return self.config.get(key, default)

    async def _save_config_if_possible(self) -> bool:
        return True

    def _schedule_data_save(self, *, sections=(), **_kwargs) -> None:
        self.saved_sections.append(set(sections or ()))

    @staticmethod
    def _private_user_role(user):
        """作者侧的角色判定：只有主要用户能改全局状态（daily_state 同款门禁）。"""

        return "owner" if str((user or {}).get("user_id") or "") == "u-owner" else "friend"

    def _current_dialogue_outfit_override(self, *, user_id: str = "", now=None):
        snapshot = self.data.get(WARDROBE_INTENT_KEY) or {}
        if not isinstance(snapshot, dict) or not snapshot:
            return {}
        if str(snapshot.get("date") or "") != _today_key():
            return {}
        check_now = _now_ts() if now is None else now
        if float(snapshot.get("expires_at") or 0) <= check_now:
            return {}
        source_user_id = str(snapshot.get("source_user_id") or "")
        if user_id and source_user_id != user_id:
            return {}
        return dict(snapshot) if str(snapshot.get("instruction") or "") else {}


ITEMS = normalize_wardrobe_items(
    [
        {"id": "i-tee", "name": "白色纯棉T恤", "description": "基础圆领", "slot": "upper"},
        {"id": "i-jeans", "name": "深蓝直筒牛仔裤", "description": "经典款", "slot": "lower"},
        {"id": "i-swim", "name": "分体泳衣上装", "description": "运动款速干", "slot": "upper"},
        {"id": "i-shirt", "name": "浅蓝条纹衬衫", "description": "棉质", "slot": "upper"},
    ]
)


def _section(**kwargs):
    plugin = _IntentHarness(items=ITEMS, **kwargs)
    return plugin, plugin._wardrobe_prompt_section(USER, "")


class WardrobeIntentTakeoverTests(unittest.TestCase):
    def test_override_with_items_replaces_the_rotating_outfit(self) -> None:
        _plugin, section = _section(
            override={
                "instruction": "换上泳衣",
                "source_user_id": "u-owner",
                "wardrobe_items": ["i-swim"],
            }
        )
        assert section is not None
        content = str(section.content)
        self.assertIn("分体泳衣上装", content)
        self.assertIn("换上泳衣", content)
        # 关键：不能再出现「当前着装：」——那个标题意味着这是我们裁决出来的那一套。
        self.assertNotIn("当前着装：", content)
        self.assertIn("以这次换装为准", content)

    def test_override_outfit_id_resolves_to_its_items(self) -> None:
        outfits = normalize_wardrobe_outfits(
            [{"id": "o-trip", "name": "出差三件套", "kind": "bundle", "items": ["i-tee", "i-jeans"]}]
        )
        plugin = _IntentHarness(
            items=ITEMS,
            outfits=outfits,
            override={
                "instruction": "换上出差那套",
                "source_user_id": "u-owner",
                "wardrobe_outfit_id": "o-trip",
            },
        )
        section = plugin._wardrobe_prompt_section(USER, "")
        assert section is not None
        content = str(section.content)
        self.assertIn("白色纯棉T恤", content)
        self.assertIn("深蓝直筒牛仔裤", content)
        self.assertNotIn("分体泳衣上装", content)

    def test_override_without_items_still_takes_over_and_says_so(self) -> None:
        _plugin, section = _section(
            override={"instruction": "换上 JK 制服", "source_user_id": "u-owner"}
        )
        assert section is not None
        content = str(section.content)
        self.assertIn("换上 JK 制服", content)
        self.assertIn("衣柜清单里没有完全对应的衣物", content)
        self.assertNotIn("当前着装：", content)

    def test_override_wins_over_the_progressive_minimal_body(self) -> None:
        _plugin, section = _section(
            detail="progressive",
            override={"instruction": "换上泳衣", "source_user_id": "u-owner"},
        )
        assert section is not None
        content = str(section.content)
        self.assertIn("换上泳衣", content)
        self.assertNotIn("需要细节时再展开", content)

    def test_override_stays_within_the_character_budget(self) -> None:
        _plugin, section = _section(
            override={
                "instruction": "换上" + "很长的换装描述" * 20,
                "source_user_id": "u-owner",
                "wardrobe_items": ["i-swim", "i-tee", "i-jeans"],
            }
        )
        assert section is not None
        self.assertLessEqual(len(str(section.content)), WARDROBE_PROMPT_MAX_CHARS)


class WardrobeIntentFallbackTests(unittest.TestCase):
    """没有意图时必须与加这个功能之前逐字一致。"""

    def test_without_override_the_rotating_outfit_is_still_injected(self) -> None:
        _plugin, section = _section()
        assert section is not None
        content = str(section.content)
        self.assertIn("当前着装：", content)
        self.assertNotIn("以这次换装为准", content)

    def test_override_from_another_user_is_ignored(self) -> None:
        _plugin, section = _section(
            override={"instruction": "换上泳衣", "source_user_id": "u-someone-else"}
        )
        assert section is not None
        content = str(section.content)
        self.assertNotIn("换上泳衣", content)
        self.assertIn("当前着装：", content)

    def test_override_is_ignored_without_a_user_identity(self) -> None:
        # 群聊（user=None）不带某个人的换装：作者的连续性段落本身也只在私聊注入。
        plugin = _IntentHarness(
            items=ITEMS,
            override={"instruction": "换上泳衣", "source_user_id": "u-owner"},
        )
        section = plugin._wardrobe_prompt_section(None, "")
        assert section is not None
        self.assertNotIn("换上泳衣", str(section.content))

    def test_expired_override_is_ignored(self) -> None:
        _plugin, section = _section(
            override={
                "instruction": "换上泳衣",
                "source_user_id": "u-owner",
                "expires_at": _now_ts() - 1,
            }
        )
        assert section is not None
        self.assertNotIn("换上泳衣", str(section.content))

    def test_missing_host_accessor_degrades_to_normal_behaviour(self) -> None:
        class _NoOverrideAccessor(_IntentHarness):
            def _current_dialogue_outfit_override(self, *, user_id: str = "", now=None):
                raise RuntimeError("host changed")

        plugin = _NoOverrideAccessor(
            items=ITEMS,
            override={"instruction": "换上泳衣", "source_user_id": "u-owner"},
        )
        section = plugin._wardrobe_prompt_section(USER, "")
        assert section is not None
        self.assertIn("当前着装：", str(section.content))

    def test_inventory_mode_is_unaffected_without_override(self) -> None:
        _plugin, section = _section(mode="inventory")
        assert section is not None
        self.assertIn("衣柜里的具体衣物：", str(section.content))


class WardrobeIntentWriteTests(unittest.TestCase):
    """P1：模型工具把意图写进作者那同一个 key，并落盘。"""

    def test_non_owner_cannot_overwrite_the_intent(self) -> None:
        # 回归：这条 key 是全局的（生图与面板读的是不带用户过滤的那一份）。
        # 作者那条写路径有 owner 门禁，工具路径原先没有 —— 群里任何一个人
        # 让模型调一次就能顶掉主人的意图，照片还会按别人的要求穿。
        plugin = _IntentHarness(items=ITEMS)
        self.assertTrue(
            plugin._wardrobe_set_intent("今天穿泳衣", items="i-swim", user=USER)["ok"]
        )
        outcome = plugin._wardrobe_set_intent(
            "我更喜欢外套", items="i-tee", user={"user_id": "u-someone-else"}
        )
        self.assertFalse(outcome["ok"], outcome)
        self.assertTrue(outcome["error"])
        # 主人的意图原封不动。
        self.assertEqual(
            "今天穿泳衣", plugin._wardrobe_dialogue_override(USER).get("instruction")
        )

    def test_write_is_rejected_when_the_role_check_is_unavailable(self) -> None:
        plugin = _IntentHarness(items=ITEMS)
        plugin._private_user_role = None  # type: ignore[assignment]
        outcome = plugin._wardrobe_set_intent("换上泳衣", items="i-swim", user=USER)
        self.assertFalse(outcome["ok"], "取不到角色判定时必须 fail-closed")

    def test_write_uses_the_authors_key_and_expiry_rules(self) -> None:
        plugin = _IntentHarness(items=ITEMS)
        before = _now_ts()
        outcome = plugin._wardrobe_set_intent(
            "换上泳衣", items="分体泳衣上装", user=USER
        )
        self.assertTrue(outcome["ok"], outcome)
        snapshot = plugin.data[WARDROBE_INTENT_KEY]
        self.assertEqual("换上泳衣", snapshot["instruction"])
        self.assertEqual(WARDROBE_INTENT_SOURCE_MODEL, snapshot["source"])
        self.assertEqual("u-owner", snapshot["source_user_id"])
        self.assertEqual(_today_key(), snapshot["date"])
        self.assertEqual(["i-swim"], snapshot["wardrobe_items"])
        self.assertGreaterEqual(snapshot["expires_at"], before + WARDROBE_INTENT_TTL_SECONDS)
        # 落盘用的是 core_store 登记过的 section 名，写错会直接抛错。
        self.assertEqual([{WARDROBE_INTENT_KEY}], plugin.saved_sections)

    def test_written_intent_takes_over_the_prompt_section(self) -> None:
        plugin = _IntentHarness(items=ITEMS)
        plugin._wardrobe_set_intent("换上泳衣", items="分体泳衣上装", user=USER)
        section = plugin._wardrobe_prompt_section(USER, "")
        assert section is not None
        content = str(section.content)
        self.assertIn("分体泳衣上装", content)
        self.assertNotIn("当前着装：", content)

    def test_resolution_is_conservative(self) -> None:
        plugin = _IntentHarness(items=ITEMS)
        outcome = plugin._wardrobe_set_intent(
            "今天清爽一点", items="白色纯棉T恤, 不存在的外套", user=USER
        )
        self.assertTrue(outcome["ok"], outcome)
        self.assertEqual(["白色纯棉T恤"], outcome["resolved"])
        self.assertEqual(["不存在的外套"], outcome["unresolved"])

    def test_unique_substring_hit_is_accepted(self) -> None:
        # 「泳衣」只对应一件，允许唯一子串命中。
        plugin = _IntentHarness(items=ITEMS)
        outcome = plugin._wardrobe_set_intent("换上泳衣", items="泳衣", user=USER)
        self.assertEqual(["分体泳衣上装"], outcome["resolved"])

    def test_ambiguous_substring_is_not_guessed(self) -> None:
        # 「蓝」同时出现在「深蓝直筒牛仔裤」和「浅蓝条纹衬衫」里：必须判为未命中，
        # 宁可让模型重说，也不能替用户挑错衣服。
        plugin = _IntentHarness(items=ITEMS)
        outcome = plugin._wardrobe_set_intent("随便换换", items="蓝", user=USER)
        self.assertEqual([], outcome["resolved"])
        self.assertEqual(["蓝"], outcome["unresolved"])

    def test_instruction_only_still_records(self) -> None:
        # 衣柜里没有的衣物（例如 JK 制服）按约定只写 instruction，不写 items。
        plugin = _IntentHarness(items=ITEMS)
        outcome = plugin._wardrobe_set_intent("换上 JK 制服", user=USER)
        self.assertTrue(outcome["ok"], outcome)
        snapshot = plugin.data[WARDROBE_INTENT_KEY]
        self.assertEqual([], snapshot["wardrobe_items"])
        self.assertEqual("", snapshot["wardrobe_outfit_id"])

    def test_outfit_name_resolves_to_the_bundle(self) -> None:
        outfits = normalize_wardrobe_outfits(
            [{"id": "o-trip", "name": "出差三件套", "kind": "bundle", "items": ["i-tee", "i-jeans"]}]
        )
        plugin = _IntentHarness(items=ITEMS, outfits=outfits)
        outcome = plugin._wardrobe_set_intent("出差", outfit="出差三件套", user=USER)
        self.assertTrue(outcome["ok"], outcome)
        self.assertEqual("o-trip", plugin.data[WARDROBE_INTENT_KEY]["wardrobe_outfit_id"])
        section = plugin._wardrobe_prompt_section(USER, "")
        assert section is not None
        content = str(section.content)
        self.assertIn("白色纯棉T恤", content)
        self.assertIn("深蓝直筒牛仔裤", content)

    def test_empty_request_is_rejected_with_an_error(self) -> None:
        plugin = _IntentHarness(items=ITEMS)
        outcome = plugin._wardrobe_set_intent("", user=USER)
        self.assertFalse(outcome["ok"])
        self.assertTrue(outcome["error"])
        self.assertNotIn(WARDROBE_INTENT_KEY, plugin.data)

    def test_unsupported_runtime_degrades(self) -> None:
        plugin = _IntentHarness(items=ITEMS)
        del plugin.data
        outcome = plugin._wardrobe_set_intent("换上泳衣", user=USER)
        self.assertFalse(outcome["ok"])
        self.assertTrue(outcome["error"])

    def test_reply_is_json_and_never_raises(self) -> None:
        plugin = _IntentHarness(items=ITEMS)
        parsed = json.loads(plugin._wardrobe_intent_reply("换上泳衣", items="i-swim", user=USER))
        self.assertTrue(parsed["ok"], parsed)

        def boom(**_kwargs):
            raise RuntimeError("boom")

        plugin._wardrobe_set_intent = boom  # type: ignore[method-assign]
        parsed = json.loads(plugin._wardrobe_intent_reply("换上泳衣"))
        self.assertEqual("error", parsed["status"])

    def test_later_write_overwrites_the_earlier_one(self) -> None:
        plugin = _IntentHarness(items=ITEMS)
        plugin._wardrobe_set_intent("换上泳衣", items="i-swim", user=USER)
        plugin._wardrobe_set_intent("换回日常", items="i-tee", user=USER)
        snapshot = plugin.data[WARDROBE_INTENT_KEY]
        self.assertEqual("换回日常", snapshot["instruction"])
        self.assertEqual(["i-tee"], snapshot["wardrobe_items"])


class WardrobeIntentPanelTests(unittest.TestCase):
    """面板读取与清除（面板没有用户身份，读的是人格下那一份）。"""

    def test_snapshot_reports_instruction_source_and_items(self) -> None:
        plugin = _IntentHarness(items=ITEMS)
        plugin._wardrobe_set_intent("换上泳衣", items="分体泳衣上装", user=USER)
        snapshot = plugin._wardrobe_intent_snapshot()
        self.assertEqual("换上泳衣", snapshot["instruction"])
        self.assertEqual(WARDROBE_INTENT_SOURCE_MODEL, snapshot["source"])
        self.assertEqual(
            [{"id": "i-swim", "name": "分体泳衣上装", "slot": "upper"}], snapshot["items"]
        )

    def test_snapshot_is_empty_without_an_intent(self) -> None:
        self.assertEqual({}, _IntentHarness(items=ITEMS)._wardrobe_intent_snapshot())

    def test_clear_removes_the_intent_and_persists(self) -> None:
        plugin = _IntentHarness(items=ITEMS)
        plugin._wardrobe_set_intent("换上泳衣", items="i-swim", user=USER)
        plugin.saved_sections.clear()
        self.assertTrue(plugin._wardrobe_clear_intent())
        self.assertEqual({}, plugin._wardrobe_intent_snapshot())
        self.assertEqual([{WARDROBE_INTENT_KEY}], plugin.saved_sections)
        self.assertFalse(plugin._wardrobe_clear_intent())

    def test_cleared_intent_restores_the_rotating_outfit(self) -> None:
        plugin = _IntentHarness(items=ITEMS)
        plugin._wardrobe_set_intent("换上泳衣", items="i-swim", user=USER)
        plugin._wardrobe_clear_intent()
        section = plugin._wardrobe_prompt_section(USER, "")
        assert section is not None
        self.assertIn("当前着装：", str(section.content))


if __name__ == "__main__":
    unittest.main()
