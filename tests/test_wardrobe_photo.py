# -*- coding: utf-8 -*-
"""生图通道桥接（wardrobe_photo）单元测试：只读、可关、失败即回落。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.wardrobe_photo import (
    PHOTO_PROFILE_FIELDS,
    WARDROBE_PHOTO_SOURCE_BUILTIN,
    resolve_daily_outfit_profile,
    wardrobe_photo_source,
)


class _Host:
    """最小替身：只实现桥接会用到的那几个读取口。"""

    def __init__(self, **overrides):
        # 默认按「用户已主动把照片来源切到衣柜」构造：本文件大多数用例测的是接管后的
        # 行为。想验真实默认值的用例显式传 setting={}（默认 builtin，不接管）。
        self.setting = overrides.pop("setting", {"wardrobe_photo_source": "wardrobe"})
        self.items = overrides.pop("items", [
            {"id": "w_top", "name": "米色针织开衫", "description": "宽松细针织", "slot": "upper"},
            {"id": "w_bottom", "name": "深色直筒长裤", "slot": "lower"},
            {"id": "w_feet", "name": "白色帆布鞋", "slot": "feet"},
            {"id": "w_extra", "name": "细框眼镜", "slot": "extra"},
        ])
        self.outfits = overrides.pop("outfits", [])
        self.raise_on = overrides.pop("raise_on", "")
        # 本会话明确换装（作者的 dialogue_outfit_override）：默认没有。
        self.intent = overrides.pop("intent", {})

    def _wardrobe_setting(self, key, default=None):
        return self.setting.get(key, default)

    def _guard(self, name):
        if self.raise_on == name:
            raise RuntimeError("boom")

    def _wardrobe_owned_items(self):
        self._guard("items"); return self.items

    def _wardrobe_owned_outfits(self):
        self._guard("outfits"); return self.outfits

    def _wardrobe_current_scene(self):
        self._guard("scene"); return "home"

    def _wardrobe_outfit_seed(self):
        self._guard("seed"); return "2026-09-14|persona-a"

    def _wardrobe_outfit_rotation_days(self):
        return 7

    def _wardrobe_current_weather(self):
        return "冷"

    def _current_dialogue_outfit_override(self, *, user_id="", now=None):
        self._guard("intent")
        return dict(self.intent)

    def _wardrobe_override_items(self, snapshot):
        by_id = {row["id"]: row for row in self.items}
        return [by_id[key] for key in (snapshot or {}).get("wardrobe_items", []) if key in by_id]


class WardrobePhotoIntentTests(unittest.TestCase):
    """本会话明确换装优先：用户说今天穿泳衣，照片就该是泳衣。"""

    def test_intent_overrides_the_rotating_outfit(self) -> None:
        host = _Host(
            items=[
                {"id": "w_top", "name": "米色针织开衫", "description": "宽松细针织", "slot": "upper"},
                {"id": "w_swim", "name": "分体泳衣上装", "description": "运动款速干", "slot": "upper"},
                {"id": "w_jeans", "name": "深蓝直筒牛仔裤", "description": "经典款", "slot": "lower"},
            ],
            intent={"instruction": "换上泳衣", "wardrobe_items": ["w_swim"]},
        )
        profile = resolve_daily_outfit_profile(host, date_key="2026-02-11")
        self.assertEqual("分体泳衣上装，运动款速干", profile.get("top"))
        self.assertNotIn("bottom", profile)

    def test_intimate_items_never_reach_the_photo(self) -> None:
        host = _Host(
            items=[
                {"id": "w_under", "name": "白色棉质内衣", "slot": "upper", "intimate": True},
                {"id": "w_jeans", "name": "深蓝直筒牛仔裤", "slot": "lower"},
            ],
            intent={"instruction": "只穿内衣", "wardrobe_items": ["w_under", "w_jeans"]},
        )
        profile = resolve_daily_outfit_profile(host, date_key="2026-02-11")
        self.assertNotIn("内衣", str(profile))
        self.assertEqual("深蓝直筒牛仔裤", profile.get("bottom"))

    def test_unresolvable_intent_falls_back_to_the_rotation(self) -> None:
        # 衣柜里没有「JK 制服」这种只写了 instruction 的意图：照片交回轮换结果，
        # 绝不凭空多出一件衣服。
        host = _Host(intent={"instruction": "换上 JK 制服"})
        profile = resolve_daily_outfit_profile(host, date_key="2026-02-11")
        self.assertEqual("米色针织开衫，宽松细针织", profile.get("top"))

    def test_intent_is_ignored_when_the_takeover_is_switched_off(self) -> None:
        host = _Host(setting={"wardrobe_photo_source": "builtin"}, intent={"instruction": "换上泳衣", "wardrobe_items": ["w_top"]})
        self.assertEqual({}, resolve_daily_outfit_profile(host, date_key="2026-02-11"))

    def test_broken_intent_reader_degrades_to_the_rotation(self) -> None:
        host = _Host(raise_on="intent", intent={"instruction": "换上泳衣"})
        profile = resolve_daily_outfit_profile(host, date_key="2026-02-11")
        self.assertEqual("米色针织开衫，宽松细针织", profile.get("top"))


class WardrobePhotoBridgeTests(unittest.TestCase):
    def test_default_source_is_builtin(self) -> None:
        # 默认不接管：升级后行为不变，要跟随衣柜得用户主动开。
        self.assertEqual(WARDROBE_PHOTO_SOURCE_BUILTIN, wardrobe_photo_source(_Host(setting={})))
        # 只有明确写了 wardrobe（或「衣柜」）才接管。
        self.assertEqual("wardrobe", wardrobe_photo_source(_Host(setting={"wardrobe_photo_source": "wardrobe"})))
        self.assertEqual("wardrobe", wardrobe_photo_source(_Host(setting={"wardrobe_photo_source": "衣柜"})))
        # 拼错、空值、旧的 built-in 写法一律不接管。
        for junk in ("", "  ", "wardrob", "built-in", "内置", None):
            self.assertEqual(
                WARDROBE_PHOTO_SOURCE_BUILTIN,
                wardrobe_photo_source(_Host(setting={"wardrobe_photo_source": junk})),
                junk,
            )

    def test_default_builtin_means_no_takeover(self) -> None:
        # 默认值下整条链路直接交还作者候选表。
        self.assertEqual({}, resolve_daily_outfit_profile(_Host(setting={})))

    def test_profile_carries_the_fields_the_author_was_dropping(self) -> None:
        profile = resolve_daily_outfit_profile(_Host())
        self.assertTrue(profile)
        for key in profile:
            self.assertIn(key, (*PHOTO_PROFILE_FIELDS, "look_id", "scene", "weather"), key)
        # footwear 是作者白名单里缺的那一项，必须真的产出
        self.assertIn("footwear", profile)
        self.assertIn("top", profile)
        self.assertEqual("home", profile["scene"])
        self.assertEqual("冷", profile["weather"])
        self.assertTrue(profile["look_id"])

    def test_builtin_switch_disables_takeover(self) -> None:
        host = _Host(setting={"wardrobe_photo_source": "builtin"})
        self.assertEqual({}, resolve_daily_outfit_profile(host))

    def test_empty_wardrobe_falls_back(self) -> None:
        self.assertEqual({}, resolve_daily_outfit_profile(_Host(items=[], outfits=[])))

    def test_any_failure_degrades_instead_of_raising(self) -> None:
        # 读取口挂掉时要么降级（还有别的数据可用），要么整条回落，
        # 但**绝不能抛异常**把照片链路带崩。
        self.assertEqual({}, resolve_daily_outfit_profile(_Host(raise_on="items")))
        for name in ("outfits", "scene", "seed"):
            profile = resolve_daily_outfit_profile(_Host(raise_on=name))
            self.assertTrue(profile, name)
            self.assertIn("top", profile, name)

    def test_missing_rotation_days_does_not_break(self) -> None:
        host = _Host()
        host._wardrobe_outfit_rotation_days = lambda: "不是数字"
        self.assertTrue(resolve_daily_outfit_profile(host))

    def test_same_seed_is_stable_and_a_new_day_can_differ(self) -> None:
        first = resolve_daily_outfit_profile(_Host(), date_key="2026-09-14|persona-a")
        second = resolve_daily_outfit_profile(_Host(), date_key="2026-09-14|persona-a")
        self.assertEqual(first, second)

    def test_bundle_outfit_wins_and_keeps_its_own_items(self) -> None:
        host = _Host()
        host.outfits = [{
            "id": "o1", "name": "通勤正装", "kind": "bundle", "ownership": "owned",
            "items": ["w_top", "w_bottom"],
        }]
        profile = resolve_daily_outfit_profile(host)
        self.assertIn("米色针织开衫", profile.get("top", ""))
        self.assertIn("深色直筒长裤", profile.get("bottom", ""))


class AuthorWhitelistWiringTests(unittest.TestCase):
    """作者侧三处白名单必须含 footwear，否则我们的字段会被静默丢弃。"""

    def test_author_files_mention_footwear(self) -> None:
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        text = (root / "proactive_message.py").read_text(encoding="utf-8")
        self.assertIn('"footwear": 140,', text)          # _normalize_daily_outfit_profile.limits
        self.assertIn('("footwear", "footwear"),', text)  # _daily_outfit_outfit_hint.fields
        self.assertIn('"footwear": 9,', text)             # cooldown_score 权重
        self.assertIn("resolve_wardrobe_daily_outfit_profile", text)  # 接管钩子


if __name__ == "__main__":
    unittest.main()
