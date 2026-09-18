# -*- coding: utf-8 -*-
"""集成契约回归：配置读取口、面板静态资源、生图投影、场景类别、只读工具。

每条用例都对应一次评审里真实复现出来的缺陷，注释写明「漏了什么会怎样」。
"""

from __future__ import annotations

import importlib
import json
import re
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.wardrobe_runtime import WardrobeMixin

ROOT = Path(__file__).resolve().parents[1]
PANELS = ("pages/companion-panel", "pages/陪伴面板")


def _optional(module_name: str, attribute: str):
    """精简桩环境（ASTRBOT_CI_STUBS）没有 astrbot.api.AstrBotConfig：跳过而不是报错。"""

    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None
    return getattr(module, attribute, None)


ProactiveMessageMixin = _optional(
    "astrbot_plugin_private_companion.proactive_message", "ProactiveMessageMixin"
)
PrivateCompanionPageApi = _optional(
    "astrbot_plugin_private_companion.page_api", "PrivateCompanionPageApi"
)
PrivateCompanionPlugin = _optional(
    "astrbot_plugin_private_companion.main", "PrivateCompanionPlugin"
)
NEEDS_HOST = "精简桩环境缺少 astrbot.api.AstrBotConfig"


def _wardrobe_schema() -> dict:
    schema = json.loads((ROOT / "_conf_schema.json").read_bytes().decode("utf-8"))
    return schema["wardrobe_config"]["items"]


class SettingsReadPortTests(unittest.TestCase):
    """schema → config → 实例属性 → 读取口：少一环就是「面板保存当场生效、重启就没了」。"""

    def test_every_wardrobe_schema_key_is_materialized_at_bootstrap(self) -> None:
        source = (ROOT / "plugin_bootstrap.py").read_text(encoding="utf-8")
        for key in _wardrobe_schema():
            self.assertIn("self.%s = " % key.lower(), source, key)

    @unittest.skipIf(PrivateCompanionPageApi is None or PrivateCompanionPlugin is None, NEEDS_HOST)
    def test_every_wardrobe_schema_key_survives_the_panel_write_filter(self) -> None:
        # 面板保存会把整张表单按 _allowed_setting_keys 过滤：invisible / provider 键
        # 不在 schema 公开集里，只能靠字面量表登记，漏了就是「保存成功但刷新回滚」。
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        api._schema_key_index_cache = None
        allowed = api._allowed_setting_keys()
        missing = [key for key in _wardrobe_schema() if key not in allowed]
        self.assertEqual([], missing)

    def test_outfit_mode_fallback_matches_the_schema_default(self) -> None:
        # 读取口兜底写 inventory、schema/bootstrap 写 select：配置里出现非法值时
        # 面板显示「只注入一套」而代码按整份清单跑。
        class _Host:
            def __init__(self, **values):
                self.values = values

            def _wardrobe_setting(self, key, default=None):
                return self.values.get(key, default)

        self.assertEqual("select", _wardrobe_schema()["wardrobe_outfit_mode"]["default"])
        self.assertEqual("select", WardrobeMixin._wardrobe_outfit_mode(_Host()))
        self.assertEqual("select", WardrobeMixin._wardrobe_outfit_mode(_Host(wardrobe_outfit_mode="")))
        self.assertEqual("inventory", WardrobeMixin._wardrobe_outfit_mode(_Host(wardrobe_outfit_mode="inventory")))


class PanelAssetTests(unittest.TestCase):
    """面板是配置的第二个副本：任何一处漏改都会静默改掉服务端行为。"""

    @staticmethod
    def _read(panel: str, rel: str) -> str:
        return (ROOT / panel / rel).read_text(encoding="utf-8")

    def test_panel_builtin_prompt_matches_the_plugin(self) -> None:
        # 「复制内置提示词」曾经抄的是改版前的三行文本：照它产出的识图回复永远没有
        # 「类型」「部位」，整套/参考/无关三态在自定义提示词路径下全部不可达。
        from astrbot_plugin_private_companion.wardrobe import DEFAULT_WARDROBE_IMAGE_PROMPT

        expected = DEFAULT_WARDROBE_IMAGE_PROMPT.split("\n")
        for panel in PANELS:
            script = self._read(panel, "js/features/wardrobe.js")
            start = script.index("const DEFAULT_IMAGE_PROMPT = [")
            end = script.index("].join", start)
            lines = [
                json.loads(raw)
                for raw in re.findall(r'^\s*("(?:[^"\\]|\\.)*"),\s*$', script[start:end], re.M)
            ]
            self.assertEqual(expected, lines, panel)

    def test_panel_item_normalizer_keeps_server_maintained_fields(self) -> None:
        # 散件列表 hydrate 后立刻重写隐藏域：不保留这些字段，下一次保存就会把
        # 图片关联洗掉、把参考件变成可穿上身。
        for panel in PANELS:
            script = self._read(panel, "js/features/wardrobe.js")
            body = script[
                script.index("function normalizeItem(raw)") : script.index("function normalizeItems(")
            ]
            for field in ("asset_ids", "ownership", "created_at", "updated_at", "version"):
                self.assertIn(field, body, "%s: %s" % (panel, field))

    def test_panel_copies_stay_byte_identical(self) -> None:
        for rel in ("index.html", "js/features/wardrobe.js"):
            self.assertEqual(
                (ROOT / PANELS[0] / rel).read_bytes(),
                (ROOT / PANELS[1] / rel).read_bytes(),
                rel,
            )

    def test_app_css_cache_buster_moved_with_the_styles(self) -> None:
        # 新增了整套区 / 草稿队列的样式：不 bump 就有一批老缓存用户拿不到。
        for panel in PANELS:
            html = self._read(panel, "index.html")
            link = next(line for line in html.splitlines() if "./app.css?v=" in line)
            self.assertIn("wardrobe=v1", link, panel)


class PhotoChannelTests(unittest.TestCase):
    """生图投影必须与对话/工具同源：同一天不能说一套、照片里穿另一套。"""

    class _Host:
        def __init__(self, *, enabled: bool = True, **extra):
            self.enabled = enabled
            self.extra = extra

        def _wardrobe_setting(self, key, default=None):
            return {"wardrobe_photo_source": "wardrobe"}.get(key, default)

        def _wardrobe_enabled(self):
            return self.enabled

    def test_disabled_wardrobe_never_takes_over_the_photo(self) -> None:
        from astrbot_plugin_private_companion.wardrobe_photo import (
            WARDROBE_PHOTO_SOURCE_BUILTIN,
            wardrobe_photo_source,
        )

        self.assertEqual(
            WARDROBE_PHOTO_SOURCE_BUILTIN, wardrobe_photo_source(self._Host(enabled=False))
        )
        self.assertEqual("wardrobe", wardrobe_photo_source(self._Host()))

    def test_photo_profile_comes_from_the_shared_resolver(self) -> None:
        from astrbot_plugin_private_companion import wardrobe_photo as module

        seen = []

        class _Host(self._Host):
            def _wardrobe_resolved_outfit(self):
                seen.append(True)
                return {
                    "source": "generate",
                    "profile": {"top": "海军蓝针织polo衫", "footwear": "棕色乐福鞋"},
                    "look_id": "rule-7",
                }

        profile = module.resolve_daily_outfit_profile(_Host(), date_key="2026-09-14")
        self.assertTrue(seen, "必须走宿主的统一解析入口，而不是自己重新裁决")
        self.assertEqual("海军蓝针织polo衫", profile["top"])
        self.assertEqual("棕色乐福鞋", profile["footwear"])
        self.assertEqual("rule-7", profile["look_id"])

    def test_rule_fallback_prefers_the_host_seed_over_the_date_key(self) -> None:
        # seed 少了人格分量时洗牌顺序不同：实测 365 天里 314 天照片与提示词各穿一套。
        from astrbot_plugin_private_companion import wardrobe_photo as module

        recorded = {}

        class _Host(self._Host):
            def _wardrobe_owned_items(self):
                return [{"id": "w1", "name": "白衬衫", "slot": "upper"}]

            def _wardrobe_owned_outfits(self):
                return []

            def _wardrobe_current_scene(self):
                return "daily"

            def _wardrobe_outfit_seed(self):
                return "2026-09-14|persona-a"

            def _wardrobe_outfit_rotation_days(self):
                return 7

            def _current_dialogue_outfit_override(self, **kwargs):
                return {}

        def fake_select(items, outfits, *, scene, seed, rotation_days):
            recorded["seed"] = seed
            return {"profile": {"top": "白衬衫"}, "look_id": "rule-1"}

        original = module.select_wardrobe_outfit
        module.select_wardrobe_outfit = fake_select
        try:
            profile = module.resolve_daily_outfit_profile(_Host(), date_key="2026-09-14")
        finally:
            module.select_wardrobe_outfit = original
        self.assertEqual("2026-09-14|persona-a", recorded["seed"])
        self.assertEqual("白衬衫", profile["top"])


class SceneCategoryTests(unittest.TestCase):
    def test_outfit_names_no_longer_leak_scene_categories(self) -> None:
        # 作者原本的候选表是纯英文，撞不上中文场景词；衣柜接管后「黑色运动紧身裤」
        # 会把场景判成 sport，把「今日穿搭参考图」挤掉换成无关的场景图库图。
        from astrbot_plugin_private_companion.photo_reference_selection import (
            parse_photo_reference_context_categories,
        )

        ambient = (
            "在家看书；当天基础穿搭：上装:米色针织开衫，宽松细针织；"
            "下装:黑色运动紧身裤，高弹速干，适合跑步；分享对象：当前用户"
        )
        scenes, times, excluded_scenes, excluded_times = parse_photo_reference_context_categories(
            ambient
        )
        self.assertEqual({"home"}, scenes)
        self.assertEqual(set(), times | excluded_scenes | excluded_times)

    def test_location_after_the_outfit_clause_still_counts(self) -> None:
        from astrbot_plugin_private_companion.photo_reference_selection import (
            parse_photo_reference_context_categories,
        )

        # 注意：作者词表里「卧室」同时属于 bedroom 与 home，所以这里用办公室来验
        # 「衣物名不再贡献场景」，只看地点本身给出来的类别。
        ambient = "当天基础穿搭：上装:居家卫衣；下装:运动长裤；当前场景：办公室"
        scenes, _times, _excluded_scenes, _excluded_times = (
            parse_photo_reference_context_categories(ambient)
        )
        self.assertEqual({"office"}, scenes)

    def test_scene_snapshot_keeps_footwear(self) -> None:
        from astrbot_plugin_private_companion.scene_context import SceneContextMixin

        text = SceneContextMixin._scene_context_outfit_description(
            {"top": "米色针织开衫", "bottom": "深色长裤", "footwear": "白色帆布鞋"}
        )
        self.assertIn("鞋履:白色帆布鞋", text)


@unittest.skipIf(ProactiveMessageMixin is None, NEEDS_HOST)
class RotationReferenceTests(unittest.TestCase):
    """接管后照片提示词里的 "avoid repeating" 约束不能整段消失。"""

    class _Host:
        def __init__(self, profile):
            self.profile = profile

        def _daily_outfit_rotation_history(self):
            return [{"outfit_profile": self.profile}]

        def _normalize_daily_outfit_profile(self, profile):
            return dict(profile or {})

    @staticmethod
    def _reference(host):
        return ProactiveMessageMixin._daily_outfit_rotation_reference(host)

    def test_builtin_profiles_still_use_palette_and_silhouette(self) -> None:
        text = self._reference(
            self._Host(
                {
                    "palette": "charcoal, ivory, and cobalt blue",
                    "silhouette": "clean tailored commute silhouette",
                    "top": "ivory ribbed knit top",
                }
            )
        )
        self.assertIn("color palettes", text)
        self.assertNotIn("tops:", text)

    def test_wardrobe_profiles_fall_back_to_concrete_pieces(self) -> None:
        text = self._reference(
            self._Host({"top": "浅灰连帽卫衣", "bottom": "黑色运动紧身裤", "footwear": "白色帆布鞋"})
        )
        self.assertIn("tops", text)
        self.assertIn("浅灰连帽卫衣", text)


class DetailToolScopeTests(unittest.TestCase):
    def test_unknown_scope_is_reported_instead_of_answered_as_today(self) -> None:
        # 拼错的 scope 默默按 today 回答，模型会以为自己问的那一份拿到了。
        class _Host:
            def _wardrobe_detail_available(self):
                return True

        payload = WardrobeMixin._wardrobe_detail_payload(_Host(), "shoes")
        self.assertEqual("unknown_scope", payload["status"])
        self.assertIn("today", payload["text"])


class ResolvedOutfitTests(unittest.TestCase):
    """「今天穿什么」只有一个入口：本会话意图 > 生成器缓存 > 规则裁决。"""

    class _Host(WardrobeMixin):
        def __init__(self, *, override=None, override_items=None, generated=None):
            self._override = override or {}
            self._override_items = override_items or []
            self._generated = generated

        def _wardrobe_dialogue_override(self, user=None):
            return self._override

        def _wardrobe_override_items(self, snapshot):
            return self._override_items

        def _wardrobe_current_scene(self):
            return "home"

        def _wardrobe_cached_generated_outfit(self):
            return self._generated

        def _wardrobe_outfit_selection(self, user=None):
            return {
                "source": "rule",
                "scene": "home",
                "style": "",
                "prompt_text": "规则那一套",
                "profile": {"top": "规则衬衫"},
                "picked": [],
                "look_id": "rule-1",
                "outfit_name": "",
            }

    @staticmethod
    def _resolve(host):
        return WardrobeMixin._wardrobe_resolved_outfit(host)

    def test_intent_beats_generated_and_rule(self) -> None:
        host = WardrobeMixin()
        host._wardrobe_dialogue_override = lambda user=None: {"instruction": "换上泳衣"}
        host._wardrobe_override_items = lambda snapshot: [
            {"id": "w1", "name": "分体泳衣", "slot": "whole"}
        ]
        host._wardrobe_current_scene = lambda: "home"
        host._wardrobe_cached_generated_outfit = lambda: {"top": "生成的上衣"}
        selection = self._resolve(host)
        self.assertEqual("dialogue_override", selection["source"])
        self.assertIn("分体泳衣", selection["prompt_text"])

    def test_generated_outfit_beats_the_rule(self) -> None:
        selection = self._resolve(self._Host(generated={"top": "海军蓝针织polo衫", "bottom": "白色阔腿裤"}))
        self.assertEqual("generate", selection["source"])
        self.assertEqual("海军蓝针织polo衫", selection["profile"]["top"])

    def test_rule_is_the_last_resort(self) -> None:
        selection = self._resolve(self._Host())
        self.assertEqual("rule", selection["source"])
        self.assertEqual("规则衬衫", selection["profile"]["top"])


class IntentWriteGateTests(unittest.TestCase):
    def test_intent_write_is_refused_when_the_wardrobe_is_disabled(self) -> None:
        # 读工具会被按请求摘掉；写工具若不拦，管理员关掉衣柜后模型仍能改角色着装。
        class _Host(WardrobeMixin):
            data = {}

            def _wardrobe_enabled(self):
                return False

        outcome = WardrobeMixin._wardrobe_set_intent(_Host(), "换上泳衣")
        self.assertFalse(outcome["ok"])
        self.assertTrue(outcome["error"])
        self.assertEqual({}, dict(_Host.data))


if __name__ == "__main__":
    unittest.main()
