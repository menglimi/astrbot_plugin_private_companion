# -*- coding: utf-8 -*-
"""角色衣柜：数据层、运行时接线与配置接入的单元测试。"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.persona_config import (
    PERSONA_SETTINGS_SCHEMA_VERSION,
    build_scope_manifest,
    load_schema,
    migrate_persona_profile,
)
from astrbot_plugin_private_companion.wardrobe import (
    DEFAULT_WARDROBE_IMAGE_PROMPT,
    OUTFIT_KIND_BUNDLE,
    OUTFIT_KIND_STYLE,
    PRECISION_EXACT,
    PRECISION_LOOSE,
    SLOT_EXTRA,
    SLOT_FEET,
    SLOT_LOWER,
    SLOT_UPPER,
    SLOT_WHOLE,
    SOURCE_KIND_IMAGE,
    SOURCE_KIND_MANUAL,
    WARDROBE_MAX_DESCRIPTION,
    WARDROBE_MAX_IMAGE_PROMPT,
    WARDROBE_MAX_ITEMS,
    WARDROBE_MAX_NAME,
    WARDROBE_MAX_OUTFITS,
    WARDROBE_MAX_TENDENCY,
    WARDROBE_OUTFIT_FIELD_LIMIT,
    WARDROBE_OUTFIT_REQUEST_LIMIT,
    WARDROBE_PROMPT_PREAMBLE,
    WARDROBE_SLOTS,
    WardrobeError,
    WardrobeLimitError,
    add_wardrobe_item,
    add_wardrobe_outfit,
    build_wardrobe_image_instruction,
    build_wardrobe_outfit_request,
    clear_wardrobe,
    delete_wardrobe_item,
    delete_wardrobe_outfit,
    find_wardrobe_item,
    find_wardrobe_outfit,
    new_wardrobe_item,
    new_wardrobe_outfit,
    normalize_wardrobe_bool,
    normalize_wardrobe_image_prompt,
    normalize_wardrobe_item,
    normalize_wardrobe_items,
    normalize_wardrobe_outfit,
    normalize_wardrobe_outfits,
    normalize_wardrobe_precision,
    normalize_wardrobe_scenes,
    normalize_wardrobe_slot,
    normalize_wardrobe_tags,
    normalize_wardrobe_tendency,
    outfit_photo_profile,
    parse_wardrobe_image_reply,
    parse_wardrobe_outfit_reply,
    render_generated_outfit,
    render_wardrobe_block,
    render_wardrobe_outfit_prompt,
    render_wardrobe_prompt,
    select_wardrobe_outfit,
    update_wardrobe_item,
    update_wardrobe_outfit,
    wardrobe_item_matches_scene,
    wardrobe_outfit_matches_scene,
    wardrobe_summary_lines,
)
from astrbot_plugin_private_companion.wardrobe_runtime import (
    WARDROBE_PROMPT_KEY,
    WardrobeMixin,
)

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# A. 数据层
# ---------------------------------------------------------------------------


class WardrobeDataTests(unittest.TestCase):
    def test_tendency_is_normalized_and_bounded(self) -> None:
        self.assertEqual("", normalize_wardrobe_tendency(None))
        self.assertEqual("偏爱宽松针织", normalize_wardrobe_tendency("  偏爱宽松针织  "))
        self.assertEqual("第一行\n第二行", normalize_wardrobe_tendency("第一行\n\n第二行"))
        long_text = "甲" * (WARDROBE_MAX_TENDENCY + 200)
        self.assertEqual(WARDROBE_MAX_TENDENCY, len(normalize_wardrobe_tendency(long_text)))

    def test_tags_accept_many_separators_and_dedupe(self) -> None:
        self.assertEqual(["居家", "秋冬"], normalize_wardrobe_tags("居家 秋冬"))
        self.assertEqual(["居家", "秋冬"], normalize_wardrobe_tags("居家、秋冬"))
        self.assertEqual(["居家", "秋冬"], normalize_wardrobe_tags(["居家", "秋冬", "居家"]))
        self.assertEqual([], normalize_wardrobe_tags(None))

    def test_item_requires_name_or_description(self) -> None:
        self.assertIsNone(normalize_wardrobe_item({"name": "", "description": ""}))
        self.assertIsNone(normalize_wardrobe_item("不是字典"))
        item = normalize_wardrobe_item({"description": "宽松米色针织开衫，罗纹袖口"})
        self.assertIsNotNone(item)
        # 没有名称时用描述前段兜底，保证条目始终可读。
        assert item is not None
        self.assertTrue(item["name"])
        self.assertEqual(SOURCE_KIND_MANUAL, item["source_kind"])

    def test_item_source_kind_follows_image_source(self) -> None:
        manual = new_wardrobe_item("开衫", "米色", source_kind="")
        self.assertEqual(SOURCE_KIND_MANUAL, manual["source_kind"])
        from_image = new_wardrobe_item("开衫", "米色", source="http://x/y.png", source_kind="")
        self.assertEqual(SOURCE_KIND_IMAGE, from_image["source_kind"])

    def test_new_item_rejects_empty_payload(self) -> None:
        with self.assertRaises(WardrobeError):
            new_wardrobe_item("", "")

    def test_items_normalize_drops_duplicates_and_caps_length(self) -> None:
        raw = [{"name": "开衫", "description": "米色"}, {"name": "开衫", "description": "重复"}]
        self.assertEqual(1, len(normalize_wardrobe_items(raw)))
        many = [{"name": f"衣物{i}", "description": "x"} for i in range(WARDROBE_MAX_ITEMS + 20)]
        self.assertEqual(WARDROBE_MAX_ITEMS, len(normalize_wardrobe_items(many)))

    def test_add_replaces_same_name_in_place(self) -> None:
        items, first = add_wardrobe_item([], name="米色针织开衫", description="薄款")
        self.assertEqual(1, len(items))
        items, second = add_wardrobe_item(
            items,
            name="米色针织开衫",
            description="厚实羊毛",
            tags="冬季",
            source="http://x/y.png",
            source_kind=SOURCE_KIND_IMAGE,
        )
        self.assertEqual(1, len(items), "同名衣物应当原地更新而不是新增")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual("厚实羊毛", second["description"])
        self.assertEqual(["冬季"], second["tags"])
        self.assertEqual(SOURCE_KIND_IMAGE, second["source_kind"])

    def test_add_respects_capacity(self) -> None:
        items: list[dict] = []
        for index in range(WARDROBE_MAX_ITEMS):
            items, _ = add_wardrobe_item(items, name=f"衣物{index}", description="x")
        with self.assertRaises(WardrobeLimitError):
            add_wardrobe_item(items, name="再来一件", description="x")

    def test_name_is_bounded(self) -> None:
        _, item = add_wardrobe_item([], name="名" * 200, description="x")
        self.assertEqual(WARDROBE_MAX_NAME, len(item["name"]))

    def test_description_is_bounded(self) -> None:
        _, item = add_wardrobe_item([], name="开衫", description="描" * (WARDROBE_MAX_DESCRIPTION + 50))
        self.assertEqual(WARDROBE_MAX_DESCRIPTION, len(item["description"]))

    def test_find_by_index_id_and_name(self) -> None:
        items, first = add_wardrobe_item([], name="米色针织开衫", description="薄款")
        items, _ = add_wardrobe_item(items, name="黑色长风衣", description="厚")
        self.assertEqual(first["id"], find_wardrobe_item(items, first["id"])["id"])
        self.assertEqual(first["id"], find_wardrobe_item(items, "1")["id"])
        self.assertEqual(first["id"], find_wardrobe_item(items, "米色针织开衫")["id"])
        self.assertEqual(first["id"], find_wardrobe_item(items, "针织")["id"])
        self.assertIsNone(find_wardrobe_item(items, "不存在"))
        self.assertIsNone(find_wardrobe_item(items, ""))
        self.assertIsNone(find_wardrobe_item([], "1"))

    def test_delete_and_update(self) -> None:
        items, _ = add_wardrobe_item([], name="米色针织开衫", description="薄款")
        items, _ = add_wardrobe_item(items, name="黑色长风衣", description="厚")
        remaining, removed = delete_wardrobe_item(items, "米色针织开衫")
        self.assertEqual("米色针织开衫", removed["name"])
        self.assertEqual(1, len(remaining))
        with self.assertRaises(KeyError):
            delete_wardrobe_item(remaining, "不存在")
        updated, stored = update_wardrobe_item(remaining, "黑色长风衣", description="加厚羊毛")
        self.assertEqual("加厚羊毛", stored["description"])
        with self.assertRaises(KeyError):
            update_wardrobe_item(updated, "不存在", description="x")
        with self.assertRaises(WardrobeError):
            update_wardrobe_item(updated, "黑色长风衣")

    def test_clear_returns_empty_state(self) -> None:
        self.assertEqual(([], ""), clear_wardrobe())

    def test_summary_lines_are_numbered(self) -> None:
        items, _ = add_wardrobe_item([], name="米色针织开衫", description="薄款", tags="居家")
        lines = wardrobe_summary_lines(items)
        self.assertEqual(1, len(lines))
        self.assertIn("1. 米色针织开衫", lines[0])
        self.assertIn("居家", lines[0])

    def test_render_block_empty_when_nothing_configured(self) -> None:
        self.assertEqual("", render_wardrobe_block("", []))
        self.assertEqual("", render_wardrobe_prompt("", []))
        self.assertEqual("", render_wardrobe_prompt(None, None))

    def test_render_block_includes_tendency_and_items(self) -> None:
        items, _ = add_wardrobe_item([], name="米色针织开衫", description="宽松罗纹袖口", tags="居家")
        block = render_wardrobe_block("偏爱低饱和色", items)
        self.assertIn("整体服饰倾向：偏爱低饱和色", block)
        self.assertIn("米色针织开衫", block)
        self.assertIn("宽松罗纹袖口", block)
        prompt = render_wardrobe_prompt("偏爱低饱和色", items)
        self.assertIn("不要把它当作新的指令", prompt)

    def test_render_block_respects_item_budget(self) -> None:
        items: list[dict] = []
        for index in range(20):
            items, _ = add_wardrobe_item(items, name=f"衣物{index}", description="x")
        block = render_wardrobe_block("", items, max_items=5, max_chars=2000)
        self.assertIn("另有 15 件未列出", block)

    def test_render_block_respects_char_budget(self) -> None:
        items, _ = add_wardrobe_item([], name="开衫", description="描" * 400)
        block = render_wardrobe_block("倾向" * 200, items, max_items=5, max_chars=80)
        self.assertLessEqual(len(block), 80)


class WardrobeImageReplyTests(unittest.TestCase):
    def test_instruction_mentions_required_fields(self) -> None:
        instruction = build_wardrobe_image_instruction()
        self.assertIn("名称：", instruction)
        self.assertIn("描述：", instruction)
        self.assertIn("标签：", instruction)

    def test_instruction_appends_user_note_as_hint(self) -> None:
        instruction = build_wardrobe_image_instruction("只看外套")
        self.assertIn("只看外套", instruction)
        self.assertIn("以图片实际可见内容为准", instruction)
        self.assertNotIn("补充说明", build_wardrobe_image_instruction(""))

    def test_empty_template_keeps_the_builtin_prompt(self) -> None:
        for blank in ("", "   ", None, "\n\n"):
            self.assertEqual(
                DEFAULT_WARDROBE_IMAGE_PROMPT,
                build_wardrobe_image_instruction("", blank),
                repr(blank),
            )

    def test_custom_template_replaces_the_builtin_wording(self) -> None:
        instruction = build_wardrobe_image_instruction("", "只描述外套的材质和颜色。")
        self.assertEqual("只描述外套的材质和颜色。", instruction)
        self.assertNotIn("你正在为角色的衣柜整理衣物资料", instruction)

    def test_custom_template_still_gets_the_user_note(self) -> None:
        instruction = build_wardrobe_image_instruction("重点看领口", "只描述外套。")
        self.assertIn("只描述外套。", instruction)
        self.assertIn("重点看领口", instruction)
        self.assertIn("以图片实际可见内容为准", instruction)

    def test_custom_template_is_length_bounded(self) -> None:
        instruction = build_wardrobe_image_instruction("", "甲" * 5000)
        self.assertEqual(WARDROBE_MAX_IMAGE_PROMPT, len(instruction))

    def test_prompt_normalization_keeps_newlines_drops_blank_lines(self) -> None:
        self.assertEqual("第一行\n第二行", normalize_wardrobe_image_prompt("第一行\n\n\n第二行"))
        self.assertEqual("", normalize_wardrobe_image_prompt(None))

    def test_custom_prompt_reply_is_still_parsed(self) -> None:
        parsed = parse_wardrobe_image_reply("名称：风衣\n描述：黑色长款\n标签：外出")
        assert parsed is not None
        self.assertEqual("风衣", parsed["name"])

    def test_parse_labelled_reply(self) -> None:
        parsed = parse_wardrobe_image_reply(
            "名称：碎花连衣裙\n描述：米白底小碎花，方领，及膝\n标签：外出|春夏"
        )
        assert parsed is not None
        self.assertEqual("碎花连衣裙", parsed["name"])
        self.assertEqual("米白底小碎花，方领，及膝", parsed["description"])
        self.assertEqual(["外出", "春夏"], parsed["tags"])

    def test_parse_accepts_english_labels_and_colons(self) -> None:
        parsed = parse_wardrobe_image_reply("Name: Linen shirt\nDescription: beige, relaxed\nTags: summer|casual")
        assert parsed is not None
        self.assertEqual("Linen shirt", parsed["name"])
        self.assertEqual("beige, relaxed", parsed["description"])
        self.assertEqual(["summer", "casual"], parsed["tags"])

    def test_parse_falls_back_to_unlabelled_text(self) -> None:
        parsed = parse_wardrobe_image_reply("一件黑色的长款风衣")
        assert parsed is not None
        self.assertEqual("一件黑色的长款风衣", parsed["description"])
        self.assertTrue(parsed["name"])

    def test_parse_rejects_empty_and_negative_replies(self) -> None:
        for value in ("", "   ", "无", "None", "无法判断", "\n\n"):
            self.assertIsNone(parse_wardrobe_image_reply(value), value)


# ---------------------------------------------------------------------------
# B. 运行时接线
# ---------------------------------------------------------------------------


class _WardrobeHarness(WardrobeMixin):
    def __init__(self) -> None:
        self.config: dict = {
            "enable_wardrobe": True,
            "wardrobe_tendency": "",
            "wardrobe_items": [],
            "enable_wardrobe_prompt": True,
            "wardrobe_prompt_max_items": 12,
            "wardrobe_image_max_count": 3,
            "WARDROBE_VISION_PROVIDER_ID": "",
            "wardrobe_image_prompt": "",
        }
        self.save_calls = 0
        self.save_should_fail = False
        self.describe_calls: list[str] = []
        self.describe_reply: dict | None = {"name": "碎花连衣裙", "description": "米白底小碎花", "tags": ["外出"]}
        self.command_images: list[tuple[str, str]] = []
        self.placed_sections: list[tuple[str, object, int]] = []
        self.placement_error: Exception | None = None
        # 识图替身：真正记录下送进 provider 的 prompt，用来验证提示词接线。
        self.vision_prompts: list[str] = []
        self.vision_provider_calls: list[str] = []

    # --- 识图替身：走真实的 _wardrobe_describe_image 代码路径 ---
    async def _prepare_private_image_sources_for_model(self, sources, *, namespace="vision"):
        return list(sources)

    def _cleanup_prepared_image_sources(self, sources, *, namespace):
        return None

    def _private_image_model_image_items_with_meta(self, sources):
        # 真实签名是 (image_items, source_image_count, has_gif_frames)。
        image_items = [(f"key:{item}", f"url:{item}") for item in sources]
        return image_items, len(sources), False

    def _private_image_provider_by_id(self, provider_id):
        return SimpleNamespace(text_chat=self._fake_text_chat(provider_id))

    def _private_image_visual_provider_candidates(self, umo=""):
        # 生产环境里这里总会给出候选；替身也给一个，否则在未配置模型时
        # 真实 _wardrobe_describe_image 会判定"没有可用候选"而直接放弃。
        return [("vision-default", "plugin_vision", "")]

    @staticmethod
    def _provider_supports_image(provider):
        return True

    def _fake_text_chat(self, provider_id):
        async def _call(*, prompt, image_urls, **kwargs):
            self.vision_provider_calls.append(provider_id)
            self.vision_prompts.append(prompt)
            return SimpleNamespace(completion_text="名称：风衣\n描述：黑色长款\n标签：外出")

        return _call

    def _place_conversation_prompt_section(self, req, marker, section, *, priority=50, force_dynamic=False):
        if self.placement_error is not None:
            raise self.placement_error
        self.placed_sections.append((marker, section, priority))

    # --- 运行时依赖替身 ---
    def persona_setting(self, key: str, default: object = None) -> object:
        return self.config.get(key, default)

    async def _save_config_if_possible(self) -> bool:
        self.save_calls += 1
        return not self.save_should_fail

    async def _photo_reference_images_from_command_context(self, event, user_id, *, limit=12):
        return list(self.command_images), bool(self.command_images)


class _WardrobeCommandHarness(_WardrobeHarness):
    """命令层只需要一个可控的识图结果；真实识图接线由视觉替身用例覆盖。"""

    async def _wardrobe_describe_image(self, image_sources, *, note="", umo="", provider_id=""):  # type: ignore[override]
        self.describe_calls.append(note)
        if self.describe_reply is None:
            return None, "识图模型没有返回可用的衣物描述。"
        return dict(self.describe_reply), ""


class WardrobeMixinTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = _WardrobeCommandHarness()

    async def test_view_reports_state_and_help(self) -> None:
        text, image = await self.plugin._wardrobe_command_payload(None, "u1", "")
        self.assertEqual("", image)
        self.assertIn("角色衣柜：0/", text)
        self.assertIn("整体服饰倾向：（未设置）", text)
        self.assertIn("陪伴 衣柜 添加图片", text)

    async def test_unknown_action_falls_back_to_help(self) -> None:
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "不存在的子命令")
        self.assertIn("未知的衣柜子命令", text)

    async def test_set_tendency_persists(self) -> None:
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "倾向 偏爱宽松针织与低饱和色")
        self.assertIn("已更新整体服饰倾向", text)
        self.assertEqual("偏爱宽松针织与低饱和色", self.plugin.config["wardrobe_tendency"])

    async def test_set_tendency_without_argument_only_reports(self) -> None:
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "倾向")
        self.assertIn("当前整体服饰倾向", text)
        self.assertEqual("", self.plugin.config["wardrobe_tendency"])
        self.assertEqual(0, self.plugin.save_calls)

    async def test_clear_tendency(self) -> None:
        await self.plugin._wardrobe_command_payload(None, "u1", "倾向 偏爱宽松针织")
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "倾向 清空")
        self.assertIn("已清除整体服饰倾向", text)
        self.assertEqual("", self.plugin.config["wardrobe_tendency"])

    async def test_add_item_with_separator(self) -> None:
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "添加 米色针织开衫 | 宽松罗纹袖口")
        self.assertIn("已把「米色针织开衫」加入衣柜", text)
        items = self.plugin.config["wardrobe_items"]
        self.assertEqual(1, len(items))
        self.assertEqual("米色针织开衫", items[0]["name"])
        self.assertEqual("宽松罗纹袖口", items[0]["description"])

    async def test_add_item_without_separator_uses_description(self) -> None:
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "添加 宽松米色针织开衫")
        self.assertIn("加入衣柜", text)
        self.assertEqual(1, len(self.plugin.config["wardrobe_items"]))

    async def test_add_item_without_argument_returns_usage(self) -> None:
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "添加")
        self.assertIn("请这样添加衣物", text)
        self.assertEqual(0, self.plugin.save_calls)

    async def test_delete_item(self) -> None:
        await self.plugin._wardrobe_command_payload(None, "u1", "添加 米色针织开衫 | 薄款")
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "删除 1")
        self.assertIn("已从衣柜移除「米色针织开衫」", text)
        self.assertEqual([], self.plugin.config["wardrobe_items"])

    async def test_delete_missing_item_reports(self) -> None:
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "删除 幽灵衣物")
        self.assertIn("没有找到", text)

    async def test_update_item(self) -> None:
        await self.plugin._wardrobe_command_payload(None, "u1", "添加 米色针织开衫 | 薄款")
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "修改 1 加厚羊毛")
        self.assertIn("已更新「米色针织开衫」", text)
        self.assertEqual("加厚羊毛", self.plugin.config["wardrobe_items"][0]["description"])

    async def test_clear_items_keeps_tendency(self) -> None:
        await self.plugin._wardrobe_command_payload(None, "u1", "倾向 偏爱宽松针织")
        await self.plugin._wardrobe_command_payload(None, "u1", "添加 米色针织开衫 | 薄款")
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "清空")
        self.assertIn("已清空角色衣柜", text)
        self.assertEqual([], self.plugin.config["wardrobe_items"])
        self.assertEqual("偏爱宽松针织", self.plugin.config["wardrobe_tendency"])

    async def test_add_from_image_describes_and_stores(self) -> None:
        self.plugin.command_images = [("/tmp/coat.png", "随消息发送的图片")]
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "添加图片")
        self.assertIn("已加入：碎花连衣裙", text)
        self.assertEqual([""], self.plugin.describe_calls)
        stored = self.plugin.config["wardrobe_items"][0]
        self.assertEqual("碎花连衣裙", stored["name"])
        self.assertEqual("/tmp/coat.png", stored["source"])
        self.assertEqual(SOURCE_KIND_IMAGE, stored["source_kind"])

    async def test_add_from_image_forwards_note_to_vision(self) -> None:
        self.plugin.command_images = [("/tmp/coat.png", "随消息发送的图片")]
        await self.plugin._wardrobe_command_payload(None, "u1", "添加图片 只看外套")
        self.assertEqual(["只看外套"], self.plugin.describe_calls)

    async def test_add_from_image_without_image_returns_usage(self) -> None:
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "添加图片")
        self.assertIn("请把衣物图片和命令一起发送", text)
        self.assertEqual(0, self.plugin.save_calls)

    async def test_add_from_image_surfaces_vision_failure(self) -> None:
        self.plugin.command_images = [("/tmp/coat.png", "随消息发送的图片")]
        self.plugin.describe_reply = None
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "添加图片")
        self.assertIn("没有把衣物加入衣柜", text)
        self.assertIn("识图模型没有返回可用的衣物描述", text)

    async def test_add_from_image_blocked_when_wardrobe_disabled(self) -> None:
        self.plugin.config["enable_wardrobe"] = False
        self.plugin.command_images = [("/tmp/coat.png", "随消息发送的图片")]
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "添加图片")
        self.assertIn("角色衣柜当前是关闭的", text)

    async def test_save_failure_is_reported_and_runtime_rolled_back(self) -> None:
        self.plugin.save_should_fail = True
        text, _ = await self.plugin._wardrobe_command_payload(None, "u1", "添加 米色针织开衫 | 薄款")
        self.assertIn("没有保存成功", text)
        # 配置层必须回到保存前的值，而不是被写成 None。
        self.assertEqual([], self.plugin.config["wardrobe_items"])
        self.assertEqual([], self.plugin._wardrobe_items())
        # 保存前并不存在的运行时属性不应被凭空创建。
        self.assertFalse(hasattr(self.plugin, "wardrobe_items"))

    async def test_save_failure_restores_previous_items(self) -> None:
        await self.plugin._wardrobe_command_payload(None, "u1", "添加 米色针织开衫 | 薄款")
        before = list(self.plugin.config["wardrobe_items"])
        self.plugin.save_should_fail = True
        await self.plugin._wardrobe_command_payload(None, "u1", "添加 黑色长风衣 | 厚")
        self.assertEqual(before, self.plugin.config["wardrobe_items"])
        self.assertEqual("米色针织开衫", self.plugin._wardrobe_items()[0]["name"])

    def test_prompt_section_absent_when_empty(self) -> None:
        self.assertIsNone(self.plugin._wardrobe_prompt_section())

    def test_prompt_section_uses_configured_key(self) -> None:
        self.plugin.config["wardrobe_tendency"] = "偏爱宽松针织"
        section = self.plugin._wardrobe_prompt_section()
        assert section is not None
        self.assertEqual(WARDROBE_PROMPT_KEY, section.key)
        self.assertEqual("wardrobe", section.source)
        self.assertIn("偏爱宽松针织", str(section.content))

    def test_prompt_section_disabled_by_switch(self) -> None:
        self.plugin.config["wardrobe_tendency"] = "偏爱宽松针织"
        self.plugin.config["enable_wardrobe_prompt"] = False
        self.assertIsNone(self.plugin._wardrobe_prompt_section())
        self.plugin.config["enable_wardrobe_prompt"] = True
        self.plugin.config["enable_wardrobe"] = False
        self.assertIsNone(self.plugin._wardrobe_prompt_section())

    def test_item_limit_is_clamped(self) -> None:
        self.plugin.config["wardrobe_prompt_max_items"] = 999
        self.assertEqual(WARDROBE_MAX_ITEMS, self.plugin._wardrobe_prompt_item_limit())
        self.plugin.config["wardrobe_prompt_max_items"] = 0
        self.assertEqual(1, self.plugin._wardrobe_prompt_item_limit())

    def test_image_limit_is_clamped(self) -> None:
        self.plugin.config["wardrobe_image_max_count"] = 99
        self.assertEqual(8, self.plugin._wardrobe_image_limit())
        self.plugin.config["wardrobe_image_max_count"] = 0
        self.assertEqual(1, self.plugin._wardrobe_image_limit())

    async def test_group_injection_places_section_once(self) -> None:
        self.plugin.config["wardrobe_tendency"] = "偏爱宽松针织"
        self.plugin.config["wardrobe_items"] = [{"name": "米色针织开衫", "description": "薄款"}]
        req = SimpleNamespace()
        await self.plugin._append_group_wardrobe_to_request(None, req)
        self.assertEqual(1, len(self.plugin.placed_sections))
        marker, section, priority = self.plugin.placed_sections[0]
        self.assertEqual("<!-- private_companion_group_wardrobe -->", marker)
        self.assertEqual(WARDROBE_PROMPT_KEY, section.key)
        self.assertEqual(13, priority)

    async def test_group_injection_skips_when_wardrobe_empty(self) -> None:
        await self.plugin._append_group_wardrobe_to_request(None, SimpleNamespace())
        self.assertEqual([], self.plugin.placed_sections)

    async def test_group_injection_skips_when_disabled(self) -> None:
        self.plugin.config["wardrobe_tendency"] = "偏爱宽松针织"
        self.plugin.config["enable_wardrobe"] = False
        await self.plugin._append_group_wardrobe_to_request(None, SimpleNamespace())
        self.assertEqual([], self.plugin.placed_sections)

    async def test_group_injection_survives_placement_failure(self) -> None:
        self.plugin.config["wardrobe_tendency"] = "偏爱宽松针织"
        self.plugin.placement_error = RuntimeError("plan unavailable")
        try:
            await self.plugin._append_group_wardrobe_to_request(None, SimpleNamespace())
        except Exception as exc:  # pragma: no cover - 失败即视为回归
            self.fail(f"注入失败不应向上抛出: {exc}")

    def test_vision_provider_id_reads_from_config(self) -> None:
        self.plugin.config["WARDROBE_VISION_PROVIDER_ID"] = "vision-a"
        self.assertEqual("vision-a", self.plugin._wardrobe_vision_provider_id())
        self.plugin.config["WARDROBE_VISION_PROVIDER_ID"] = ""
        self.assertEqual("", self.plugin._wardrobe_vision_provider_id())

    def test_image_prompt_reads_from_config(self) -> None:
        self.assertEqual("", self.plugin._wardrobe_image_prompt())
        self.plugin.config["wardrobe_image_prompt"] = "只描述外套。"
        self.assertEqual("只描述外套。", self.plugin._wardrobe_image_prompt())
        self.plugin.config["wardrobe_image_prompt"] = "行一\n\n行二"
        self.assertEqual("行一\n行二", self.plugin._wardrobe_image_prompt())

    def test_vision_candidates_put_the_override_first(self) -> None:
        self.plugin.config["WARDROBE_VISION_PROVIDER_ID"] = "vision-config"
        candidates = self.plugin._wardrobe_vision_candidates("", preferred="vision-picked")
        self.assertEqual("vision-picked", candidates[0])
        self.assertIn("vision-config", candidates)

    def test_vision_candidates_without_override_start_with_config(self) -> None:
        self.plugin.config["WARDROBE_VISION_PROVIDER_ID"] = "vision-config"
        candidates = self.plugin._wardrobe_vision_candidates("")
        self.assertEqual("vision-config", candidates[0])

    def test_vision_candidates_ignore_blank_override(self) -> None:
        self.plugin.config["WARDROBE_VISION_PROVIDER_ID"] = "vision-config"
        for blank in ("", "   ", None):
            self.assertEqual("vision-config", self.plugin._wardrobe_vision_candidates("", preferred=blank)[0])

    def test_vision_candidates_deduplicate(self) -> None:
        self.plugin.config["WARDROBE_VISION_PROVIDER_ID"] = "vision-config"
        candidates = self.plugin._wardrobe_vision_candidates("", preferred="vision-config")
        self.assertEqual(1, candidates.count("vision-config"), candidates)
        self.assertEqual("vision-config", candidates[0])
        self.assertEqual(len(candidates), len(set(candidates)))

    async def test_describe_uses_the_custom_prompt(self) -> None:
        """自定义提示词必须真的送到识别调用里，而不是只存进配置。"""

        plugin = _WardrobeHarness()
        plugin.config["wardrobe_image_prompt"] = "只看外套。"
        await plugin._wardrobe_describe_image(["/tmp/coat.png"])
        self.assertEqual(1, len(plugin.vision_prompts))
        self.assertTrue(plugin.vision_prompts[0].startswith("只看外套。"))

    async def test_describe_uses_default_prompt_when_unset(self) -> None:
        plugin = _WardrobeHarness()
        await plugin._wardrobe_describe_image(["/tmp/coat.png"])
        self.assertEqual(DEFAULT_WARDROBE_IMAGE_PROMPT, plugin.vision_prompts[0])

    async def test_describe_prefers_the_picked_provider(self) -> None:
        plugin = _WardrobeHarness()
        plugin.config["WARDROBE_VISION_PROVIDER_ID"] = "vision-config"
        await plugin._wardrobe_describe_image(["/tmp/coat.png"], provider_id="vision-picked")
        # 第一个候选就成功了，因此只调用一次；顺序由候选表断言。
        self.assertEqual(["vision-picked"], plugin.vision_provider_calls)
        self.assertEqual(
            ["vision-picked", "vision-config", "vision-default"],
            plugin._wardrobe_vision_candidates("", preferred="vision-picked"),
        )

    def test_prompt_section_unaffected_by_image_prompt(self) -> None:
        self.plugin.config["wardrobe_tendency"] = "偏爱针织"
        self.plugin.config["wardrobe_image_prompt"] = "只看外套。"
        section = self.plugin._wardrobe_prompt_section()
        assert section is not None
        self.assertNotIn("只看外套", str(section.content))

    def test_alias_actions_resolve_to_wardrobe(self) -> None:
        self.assertTrue(issubclass(_WardrobeHarness, WardrobeMixin))
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('wardrobe_command_actions = {"衣柜"', source)
        self.assertIn("self._wardrobe_command_payload(event, user_id, value)", source)


# ---------------------------------------------------------------------------
# C. 配置与人格接入
# ---------------------------------------------------------------------------


class WardrobeConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_schema(ROOT / "_conf_schema.json")
        cls.manifest = build_scope_manifest(cls.schema)

    def test_schema_group_exists_with_all_keys(self) -> None:
        group = self.schema["wardrobe_config"]
        self.assertEqual("object", group["type"])
        items = group["items"]
        for key in (
            "enable_wardrobe",
            "wardrobe_tendency",
            "enable_wardrobe_prompt",
            "wardrobe_prompt_max_items",
            "wardrobe_image_max_count",
            "wardrobe_image_prompt",
            "WARDROBE_VISION_PROVIDER_ID",
            "wardrobe_items",
        ):
            self.assertIn(key, items)
        self.assertEqual([], items["wardrobe_items"]["default"])
        self.assertTrue(items["enable_wardrobe"]["default"])

    def test_keys_are_persona_scoped_and_cloneable(self) -> None:
        for key in (
            "enable_wardrobe",
            "wardrobe_tendency",
            "enable_wardrobe_prompt",
            "wardrobe_prompt_max_items",
            "wardrobe_image_max_count",
            "wardrobe_image_prompt",
            "WARDROBE_VISION_PROVIDER_ID",
            "wardrobe_items",
        ):
            entry = self.manifest[key]
            self.assertEqual("persona", entry["scope"], key)
            self.assertEqual("wardrobe_config", entry["ui_location"], key)
            self.assertTrue(entry["cloneable"], key)

    def test_current_persona_version_materializes_wardrobe_keys(self) -> None:
        self.assertEqual(7, PERSONA_SETTINGS_SCHEMA_VERSION)
        migrated = migrate_persona_profile(
            {"persona_settings": {}, "persona_settings_schema_version": 5},
            manifest=self.manifest,
        )
        settings = migrated["persona_settings"]
        self.assertTrue(settings["enable_wardrobe"])
        self.assertEqual("", settings["wardrobe_tendency"])
        self.assertEqual([], settings["wardrobe_items"])
        self.assertEqual("", settings["wardrobe_image_prompt"])

    def test_existing_wardrobe_values_survive_migration(self) -> None:
        migrated = migrate_persona_profile(
            {
                "persona_settings": {
                    "bot_name": "旧人格",
                    "wardrobe_tendency": "偏爱黑色长风衣",
                    "wardrobe_items": [{"name": "黑色长风衣", "description": "厚"}],
                },
                "persona_settings_schema_version": 5,
            },
            manifest=self.manifest,
        )
        settings = migrated["persona_settings"]
        self.assertEqual("偏爱黑色长风衣", settings["wardrobe_tendency"])
        self.assertEqual("黑色长风衣", settings["wardrobe_items"][0]["name"])

    def test_page_api_exposes_every_wardrobe_key(self) -> None:
        source = (ROOT / "page_api.py").read_text(encoding="utf-8")
        for key in (
            "enable_wardrobe",
            "wardrobe_tendency",
            "enable_wardrobe_prompt",
            "wardrobe_prompt_max_items",
            "wardrobe_image_max_count",
            "WARDROBE_VISION_PROVIDER_ID",
            "wardrobe_items",
        ):
            self.assertGreaterEqual(source.count(f'"{key}"'), 3, key)

    def test_bootstrap_reads_wardrobe_config(self) -> None:
        source = (ROOT / "plugin_bootstrap.py").read_text(encoding="utf-8")
        for attr in (
            "self.enable_wardrobe",
            "self.wardrobe_tendency",
            "self.wardrobe_items",
            "self.enable_wardrobe_prompt",
            "self.wardrobe_prompt_max_items",
            "self.wardrobe_image_max_count",
            "self.wardrobe_image_prompt",
            "self.wardrobe_vision_provider_id",
        ):
            self.assertIn(attr, source, attr)


class WardrobeConfigAccessorTests(unittest.TestCase):
    """用真实配置读取器验证 schema → 运行时取值这条链路。"""

    @classmethod
    def setUpClass(cls) -> None:
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        cls.plugin = PrivateCompanionPlugin

    def _config(self, **overrides):
        config = {"wardrobe_config": {"items": {}}}
        config["wardrobe_config"]["items"].update(overrides)
        return config

    def test_bool_and_string_defaults_come_from_schema(self) -> None:
        config = self._config()
        self.assertTrue(self.plugin._cfg_bool(config, "enable_wardrobe", False))
        self.assertTrue(self.plugin._cfg_bool(config, "enable_wardrobe_prompt", False))
        self.assertEqual("", self.plugin._cfg_str(config, "wardrobe_tendency", ""))
        self.assertEqual("", self.plugin._cfg_str(config, "WARDROBE_VISION_PROVIDER_ID", ""))

    def test_int_defaults_and_clamping(self) -> None:
        config = self._config()
        self.assertEqual(12, self.plugin._cfg_int(config, "wardrobe_prompt_max_items", 5, 1, 40))
        self.assertEqual(3, self.plugin._cfg_int(config, "wardrobe_image_max_count", 5, 1, 8))

    def test_explicit_values_win(self) -> None:
        config = self._config(
            enable_wardrobe=False,
            wardrobe_tendency="偏爱黑色长风衣",
            wardrobe_prompt_max_items=5,
            WARDROBE_VISION_PROVIDER_ID="vision-a",
        )
        self.assertFalse(self.plugin._cfg_bool(config, "enable_wardrobe", True))
        self.assertEqual("偏爱黑色长风衣", self.plugin._cfg_str(config, "wardrobe_tendency", ""))
        self.assertEqual(5, self.plugin._cfg_int(config, "wardrobe_prompt_max_items", 12, 1, 40))
        self.assertEqual("vision-a", self.plugin._cfg_str(config, "WARDROBE_VISION_PROVIDER_ID", ""))

    def test_image_prompt_default_is_empty_and_reads_through(self) -> None:
        self.assertEqual("", self.plugin._cfg_str(self._config(), "wardrobe_image_prompt", "x"))
        config = self._config(wardrobe_image_prompt="只描述外套。")
        self.assertEqual("只描述外套。", self.plugin._cfg_str(config, "wardrobe_image_prompt", ""))

    def test_image_prompt_survives_normalization_with_newlines(self) -> None:
        config = self._config(wardrobe_image_prompt="第一行\n\n第二行")
        raw = self.plugin._cfg_str(config, "wardrobe_image_prompt", "")
        self.assertEqual("第一行\n第二行", normalize_wardrobe_image_prompt(raw))

    def test_items_round_trip_through_normalizer(self) -> None:
        config = self._config()
        config["wardrobe_config"]["items"]["wardrobe_items"] = [
            {"name": "米色针织开衫", "description": "宽松罗纹袖口", "tags": ["居家", "秋冬"]},
            {"name": "", "description": ""},
        ]
        raw = self.plugin._cfg_raw(config, "wardrobe_items", [])
        items = normalize_wardrobe_items(raw)
        self.assertEqual(1, len(items), "空条目必须在读入时被丢弃")
        self.assertEqual("米色针织开衫", items[0]["name"])
        self.assertEqual(["居家", "秋冬"], items[0]["tags"])

    def test_items_default_is_empty_list(self) -> None:
        raw = self.plugin._cfg_raw(self._config(), "wardrobe_items", None)
        self.assertEqual([], normalize_wardrobe_items(raw))

    def test_metadata_help_documents_wardrobe(self) -> None:
        metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
        self.assertIn("陪伴 衣柜", metadata)


# ---------------------------------------------------------------------------
# D. 面板与页面接口
# ---------------------------------------------------------------------------


class WardrobePanelTests(unittest.TestCase):
    PANEL_DIRS = ("companion-panel", "陪伴面板")

    @classmethod
    def setUpClass(cls) -> None:
        cls.scripts: list[str] = []
        cls.htmls: list[str] = []
        cls.styles: list[str] = []
        for name in cls.PANEL_DIRS:
            base = ROOT / "pages" / name
            cls.scripts.append((base / "app.js").read_text(encoding="utf-8"))
            cls.htmls.append((base / "index.html").read_text(encoding="utf-8"))
            cls.styles.append((base / "app.css").read_text(encoding="utf-8"))
        cls.module = (ROOT / "pages" / "companion-panel" / "js" / "features" / "wardrobe.js").read_text(
            encoding="utf-8"
        )
        cls.api = (ROOT / "page_api.py").read_text(encoding="utf-8")
        cls.api_settings = (ROOT / "page_api_settings.py").read_text(encoding="utf-8")

    def test_panel_copies_are_byte_identical(self) -> None:
        for relative in ("app.js", "app.css", "index.html", "js/features/wardrobe.js"):
            first = (ROOT / "pages" / "companion-panel" / relative).read_bytes()
            second = (ROOT / "pages" / "陪伴面板" / relative).read_bytes()
            self.assertEqual(first, second, relative)

    def test_html_exposes_wardrobe_section_and_fields(self) -> None:
        for html in self.htmls:
            self.assertIn('data-world-section="wardrobe"', html)
            self.assertIn('data-world-panel="wardrobe"', html)
            self.assertIn('name="enable_wardrobe"', html)
            self.assertIn('name="wardrobe_tendency"', html)
            self.assertIn('name="enable_wardrobe_prompt"', html)
            self.assertIn('name="wardrobe_prompt_max_items"', html)
            self.assertIn('name="wardrobe_image_max_count"', html)
            self.assertIn('name="wardrobe_items"', html)
            self.assertIn("js/features/wardrobe.js", html)

    def test_html_exposes_vision_settings_block(self) -> None:
        for html in self.htmls:
            self.assertIn("data-wardrobe-vision-settings", html)
            # 识图模型由 JS 渲染成下拉＋手动输入，容器必须在页面里。
            self.assertIn("data-wardrobe-provider-control", html)
            self.assertIn('name="wardrobe_image_prompt"', html)
            self.assertIn("data-wardrobe-image-prompt", html)
            self.assertIn("data-wardrobe-test-provider", html)
            self.assertIn("data-wardrobe-prompt-reset", html)
            self.assertIn("data-wardrobe-prompt-copy", html)

    def test_module_renders_provider_picker_and_manual_input(self) -> None:
        self.assertIn('const PROVIDER_KEY = "WARDROBE_VISION_PROVIDER_ID"', self.module)
        self.assertIn("function renderProviderControl(", self.module)
        self.assertIn("data-wardrobe-provider-select", self.module)
        self.assertIn("data-wardrobe-provider-manual", self.module)
        self.assertIn("CUSTOM_PROVIDER", self.module)
        self.assertIn("state?.availableProviders", self.module)

    def test_module_only_one_control_carries_the_name(self) -> None:
        # 否则 collectFormSettings 会收到两个同名值。
        self.assertIn("if (!isCustom) select.name = PROVIDER_KEY;", self.module)
        self.assertIn("if (isCustom) manual.name = PROVIDER_KEY;", self.module)
        self.assertIn("delete select.name;", self.module)
        self.assertIn("delete manual.name;", self.module)

    def test_module_offers_default_prompt_copy_and_reset(self) -> None:
        self.assertIn("const DEFAULT_IMAGE_PROMPT", self.module)
        self.assertIn("名称：", self.module)
        self.assertIn("data-wardrobe-prompt-copy", self.module)
        self.assertIn("data-wardrobe-prompt-reset", self.module)

    def test_module_sends_picked_provider_to_describe(self) -> None:
        self.assertIn('postJson("/wardrobe/describe"', self.module)
        self.assertIn("provider_id: currentProviderValue(context)", self.module)

    def test_module_can_test_the_picked_provider(self) -> None:
        self.assertIn('postJson("/provider/test"', self.module)
        self.assertIn('("/provider/test", self.test_provider, ["POST"]', self.api)

    def test_html_hides_wardrobe_panel_initially(self) -> None:
        for html in self.htmls:
            marker = 'data-world-panel="wardrobe"'
            index = html.index(marker)
            opening = html.rindex("<section", 0, index)
            self.assertIn("is-hidden", html[opening:index])

    def test_script_hydrates_wardrobe_from_settings(self) -> None:
        for script in self.scripts:
            self.assertIn("function hydrateWardrobePanel()", script)
            self.assertIn("hydrateWardrobePanel();", script)
            self.assertIn('if (sectionKey === "wardrobe") hydrateWardrobePanel();', script)

    def test_script_parses_structured_item_payload(self) -> None:
        for script in self.scripts:
            self.assertIn('if (key === "wardrobe_items")', script)
            # Structured rows must not be collapsed by fillForm.
            self.assertIn('input.value = value.every((item) => item === null || typeof item !== "object")', script)

    def test_module_uses_registered_endpoints_only(self) -> None:
        self.assertIn('postJson("/photo_reference/upload"', self.module)
        self.assertIn('postJson("/wardrobe/describe"', self.module)
        self.assertIn('("/wardrobe/describe", self.describe_wardrobe_image, ["POST"]', self.api)
        self.assertIn('("/photo_reference/upload", self.upload_photo_reference, ["POST"]', self.api)

    def test_module_guards_upload_input(self) -> None:
        self.assertIn("UPLOAD_MIMES", self.module)
        self.assertIn("UPLOAD_MAX_BYTES", self.module)
        self.assertIn("MAX_ITEMS", self.module)

    def test_page_api_disallows_paths_outside_plugin_dirs(self) -> None:
        self.assertIn("def _wardrobe_page_local_path", self.api)
        self.assertIn('data_root / "photo_reference_images"', self.api)

    def test_settings_normalizer_accepts_json_string_and_array(self) -> None:
        self.assertIn("def _normalize_wardrobe_items", self.api_settings)
        self.assertIn("def _normalize_wardrobe_int", self.api_settings)
        self.assertIn("if key == \"wardrobe_items\":", self.api_settings)

    def test_styles_cover_wardrobe_classes(self) -> None:
        for style in self.styles:
            self.assertIn(".wardrobe-manager", style)
            self.assertIn(".wardrobe-item", style)


class WardrobePanelRuntimeTests(unittest.TestCase):
    """用 Node 实际执行面板模块，验证设置 → 列表 → 隐藏字段这条数据链。"""

    MODULE = ROOT / "pages" / "陪伴面板" / "js" / "features" / "wardrobe.js"

    def _run(self, settings: dict, input_value: str = "") -> dict:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        script = f"""
global.window = {{}};
const fs = require("fs");
eval(fs.readFileSync({json.dumps(str(self.MODULE), ensure_ascii=False)}, "utf8"));
const input = {{ value: {json.dumps(input_value, ensure_ascii=False)} }};
const document = {{
  querySelector: (selector) => (selector === "[data-wardrobe-items-input]" ? input : null),
  querySelectorAll: () => [],
}};
window.PrivateCompanionWardrobe.hydrateWardrobePanel({{
  state: {{ overview: {{ settings: {json.dumps(settings, ensure_ascii=False)} }} }},
  postJson: async () => ({{}}),
  document,
}});
process.stdout.write(JSON.stringify({{
  items: window.PrivateCompanionWardrobe.wardrobeItemsForTest(),
  serialized: input.value,
}}));
"""
        result = subprocess.run(
            [node, "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(result.stdout)

    def test_hydration_normalizes_and_serializes_items(self) -> None:
        payload = self._run(
            {
                "wardrobe_items": [
                    {"name": "米色针织开衫", "description": "宽松罗纹袖口", "tags": "居家 秋冬"},
                    {"name": "", "description": ""},
                    {"name": "米色针织开衫", "description": "重复条目"},
                ]
            }
        )
        self.assertEqual(1, len(payload["items"]), "空条目与同名重复项都应在面板侧被清理")
        item = payload["items"][0]
        self.assertEqual("米色针织开衫", item["name"])
        self.assertEqual(["居家", "秋冬"], item["tags"])
        # 隐藏字段必须是可被服务端解析的 JSON，而不是 [object Object]。
        serialized = json.loads(payload["serialized"])
        self.assertEqual(1, len(serialized))
        self.assertEqual("米色针织开衫", serialized[0]["name"])

    def test_hydration_ignores_placeholder_form_value(self) -> None:
        payload = self._run({}, input_value="[object Object]\n[object Object]")
        self.assertEqual([], payload["items"])

    def test_hydration_reads_draft_when_settings_lack_the_key(self) -> None:
        draft = json.dumps([{"name": "黑色长风衣", "description": "厚"}], ensure_ascii=False)
        payload = self._run({}, input_value=draft)
        self.assertEqual(1, len(payload["items"]))
        self.assertEqual("黑色长风衣", payload["items"][0]["name"])

    def test_hydration_handles_empty_wardrobe(self) -> None:
        payload = self._run({"wardrobe_items": []})
        self.assertEqual([], payload["items"])
        self.assertEqual("[]", payload["serialized"])

    def test_hydration_survives_malformed_settings(self) -> None:
        for value in ("{不是数组", None, 42, {"name": "x"}):
            payload = self._run({"wardrobe_items": value})
            self.assertEqual([], payload["items"], repr(value))


class WardrobeSettingsNormalizerTests(unittest.TestCase):
    def _normalizer(self):
        from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api._schema_key_index_cache = None
        api.plugin = SimpleNamespace()
        return api

    def test_list_payload_accepts_json_string(self) -> None:
        normalizer = self._normalizer()
        payload = json.dumps([{"name": "米色针织开衫", "description": "宽松罗纹袖口", "tags": ["居家"]}])
        result = normalizer._normalize_wardrobe_items(payload)
        self.assertEqual(1, len(result))
        self.assertEqual("米色针织开衫", result[0]["name"])
        self.assertEqual(["居家"], result[0]["tags"])

    def test_list_payload_accepts_real_array(self) -> None:
        normalizer = self._normalizer()
        result = normalizer._normalize_wardrobe_items([{"name": "风衣", "description": "厚"}])
        self.assertEqual("风衣", result[0]["name"])

    def test_list_payload_rejects_garbage(self) -> None:
        normalizer = self._normalizer()
        self.assertEqual([], normalizer._normalize_wardrobe_items("{不是数组"))
        self.assertEqual([], normalizer._normalize_wardrobe_items(""))
        self.assertEqual([], normalizer._normalize_wardrobe_items(None))
        self.assertEqual([], normalizer._normalize_wardrobe_items("[object Object]"))
        self.assertEqual([], normalizer._normalize_wardrobe_items([{"name": ""}]))

    def test_tendency_payload_is_bounded(self) -> None:
        # 倾向字段走模块级规范化函数，这里直接验证该函数的行为边界。
        self.assertEqual("偏爱针织", normalize_wardrobe_tendency("  偏爱针织 "))
        self.assertEqual(WARDROBE_MAX_TENDENCY, len(normalize_wardrobe_tendency("甲" * 5000)))
        self.assertEqual("第一行\n第二行", normalize_wardrobe_tendency("第一行\n\n第二行"))

    def test_int_payload_is_clamped(self) -> None:
        normalizer = self._normalizer()
        self.assertEqual(12, normalizer._normalize_wardrobe_int("abc", 12, 1, 40))
        self.assertEqual(1, normalizer._normalize_wardrobe_int(0, 12, 1, 40))
        self.assertEqual(40, normalizer._normalize_wardrobe_int(999, 12, 1, 40))
        self.assertEqual(3, normalizer._normalize_wardrobe_int(3, 12, 1, 8))

    def test_dispatch_route_normalizes_wardrobe_items(self) -> None:
        normalizer = self._normalizer()
        payload = json.dumps([{"name": "米色针织开衫", "description": "宽松", "tags": "居家"}])
        result = normalizer._normalize_setting_value("wardrobe_items", payload)
        self.assertEqual(1, len(result))
        self.assertEqual("米色针织开衫", result[0]["name"])
        self.assertEqual(["居家"], result[0]["tags"])

    def test_dispatch_route_leaves_unknown_keys_untouched(self) -> None:
        normalizer = self._normalizer()
        self.assertEqual("原值", normalizer._normalize_setting_value("wardrobe_unknown_key_xyz", "原值"))


class WardrobeDescribeEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from quart import Quart

        self.app = Quart(__name__)

    def _plugin(self, root: Path, *, reply: dict | None, error: str = ""):
        class _Plugin:
            def __init__(self) -> None:
                self.data_dir = str(root)
                self.describe_calls: list[tuple[list[str], str, str]] = []

            async def _wardrobe_describe_image(self, sources, *, note="", umo="", provider_id=""):
                self.describe_calls.append((list(sources), note, provider_id))
                return (dict(reply) if reply is not None else None), error

        return _Plugin()

    def _write_image(self, root: Path, name: str = "coat.png") -> Path:
        target = root / "photo_reference_images"
        target.mkdir(parents=True, exist_ok=True)
        path = target / name
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        return path

    async def test_describe_returns_parsed_fields(self) -> None:
        from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = self._write_image(root)
            plugin = self._plugin(root, reply={"name": "碎花连衣裙", "description": "米白底小碎花", "tags": ["外出"]})
            api = PrivateCompanionPageApi(plugin)
            async with self.app.test_request_context("/", method="POST", json={"source": str(path), "note": "只看外套"}):
                result = await api.describe_wardrobe_image()
        self.assertTrue(result["success"])
        self.assertEqual("碎花连衣裙", result["data"]["name"])
        self.assertEqual(["外出"], result["data"]["tags"])
        self.assertEqual([str(path.resolve())], plugin.describe_calls[0][0])
        self.assertEqual("只看外套", plugin.describe_calls[0][1])
        self.assertEqual("", plugin.describe_calls[0][2])

    async def test_describe_rejects_path_outside_plugin_dirs(self) -> None:
        from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outside = root / "outside.png"
            outside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
            plugin = self._plugin(root, reply={"name": "x", "description": "y", "tags": []})
            api = PrivateCompanionPageApi(plugin)
            async with self.app.test_request_context("/", method="POST", json={"source": str(outside)}):
                result = await api.describe_wardrobe_image()
        self.assertFalse(result["success"])
        self.assertEqual([], plugin.describe_calls)

    async def test_describe_rejects_unsupported_suffix(self) -> None:
        from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "photo_reference_images"
            target.mkdir(parents=True, exist_ok=True)
            path = target / "note.txt"
            path.write_text("not an image", encoding="utf-8")
            plugin = self._plugin(root, reply={"name": "x", "description": "y", "tags": []})
            api = PrivateCompanionPageApi(plugin)
            async with self.app.test_request_context("/", method="POST", json={"source": str(path)}):
                result = await api.describe_wardrobe_image()
        self.assertFalse(result["success"])
        self.assertEqual([], plugin.describe_calls)

    async def test_describe_requires_source(self) -> None:
        from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

        with tempfile.TemporaryDirectory() as temp_dir:
            plugin = self._plugin(Path(temp_dir), reply={"name": "x", "description": "y", "tags": []})
            api = PrivateCompanionPageApi(plugin)
            async with self.app.test_request_context("/", method="POST", json={}):
                result = await api.describe_wardrobe_image()
        self.assertFalse(result["success"])
        self.assertEqual([], plugin.describe_calls)

    async def test_describe_reports_vision_failure(self) -> None:
        from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = self._write_image(root)
            plugin = self._plugin(root, reply=None, error="识图模型没有返回可用的衣物描述。")
            api = PrivateCompanionPageApi(plugin)
            async with self.app.test_request_context("/", method="POST", json={"source": str(path)}):
                result = await api.describe_wardrobe_image()
        self.assertFalse(result["success"])
        self.assertIn("识图模型", json.dumps(result, ensure_ascii=False))

    async def test_describe_forwards_the_picked_provider(self) -> None:
        from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = self._write_image(root)
            plugin = self._plugin(root, reply={"name": "风衣", "description": "黑色长款", "tags": []})
            api = PrivateCompanionPageApi(plugin)
            async with self.app.test_request_context(
                "/",
                method="POST",
                json={"source": str(path), "note": "", "provider_id": "vision-picked"},
            ):
                result = await api.describe_wardrobe_image()
        self.assertTrue(result["success"])
        self.assertEqual("vision-picked", plugin.describe_calls[0][2])

    async def test_describe_provider_is_optional(self) -> None:
        from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = self._write_image(root)
            plugin = self._plugin(root, reply={"name": "风衣", "description": "黑色长款", "tags": []})
            api = PrivateCompanionPageApi(plugin)
            async with self.app.test_request_context("/", method="POST", json={"source": str(path)}):
                result = await api.describe_wardrobe_image()
        self.assertTrue(result["success"])
        self.assertEqual("", plugin.describe_calls[0][2])

    async def test_describe_provider_is_bounded(self) -> None:
        from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = self._write_image(root)
            plugin = self._plugin(root, reply={"name": "风衣", "description": "黑色长款", "tags": []})
            api = PrivateCompanionPageApi(plugin)
            async with self.app.test_request_context(
                "/", method="POST", json={"source": str(path), "provider_id": "x" * 500}
            ):
                await api.describe_wardrobe_image()
        self.assertLessEqual(len(plugin.describe_calls[0][2]), 160)

    async def test_describe_reports_missing_capability(self) -> None:
        from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

        with tempfile.TemporaryDirectory() as temp_dir:
            plugin = SimpleNamespace(data_dir=temp_dir)
            api = PrivateCompanionPageApi(plugin)
            async with self.app.test_request_context("/", method="POST", json={"source": "x.png"}):
                result = await api.describe_wardrobe_image()
        self.assertFalse(result["success"])

    async def test_describe_survives_a_raising_describer(self) -> None:
        """识图层抛错时必须返回错误响应，而不是把异常抛回给页面。"""

        from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = self._write_image(root)

            class _Raising:
                def __init__(self) -> None:
                    self.data_dir = str(root)

                async def _wardrobe_describe_image(self, sources, *, note="", umo="", provider_id=""):
                    raise RuntimeError("provider exploded")

            api = PrivateCompanionPageApi(_Raising())
            async with self.app.test_request_context("/", method="POST", json={"source": str(path)}):
                result = await api.describe_wardrobe_image()
        self.assertFalse(result["success"])

    async def test_describe_rejects_non_json_body(self) -> None:
        from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

        with tempfile.TemporaryDirectory() as temp_dir:
            plugin = self._plugin(Path(temp_dir), reply={"name": "x", "description": "y", "tags": []})
            api = PrivateCompanionPageApi(plugin)
            async with self.app.test_request_context("/", method="POST", data="not json"):
                result = await api.describe_wardrobe_image()
        self.assertFalse(result["success"])
        self.assertEqual([], plugin.describe_calls)


class WardrobeVisionControlTests(unittest.TestCase):
    """用 Node 验证识图模型选择器与提示词编辑框的接线。"""

    MODULE = ROOT / "pages" / "陪伴面板" / "js" / "features" / "wardrobe.js"

    _HARNESS = """
// 模块里用了 `x instanceof HTMLElement / HTMLSelectElement` 做守卫，
// 所以替身必须是真的实例，否则守卫会抛 ReferenceError。
class FakeHTMLElement {}
class FakeHTMLSelectElement extends FakeHTMLElement {}
class FakeHTMLInputElement extends FakeHTMLElement {}
global.HTMLElement = FakeHTMLElement;
global.HTMLSelectElement = FakeHTMLSelectElement;
global.HTMLInputElement = FakeHTMLInputElement;
function makeElement(tag) {
  const name = String(tag || "").toLowerCase();
  const proto = name === "select" ? FakeHTMLSelectElement
    : name === "input" ? FakeHTMLInputElement : FakeHTMLElement;
  const el = Object.assign(new proto(), {
    tagName: name.toUpperCase(),
    children: [], attributes: {}, dataset: {}, style: {},
    value: "", textContent: "", hidden: false, type: "", name: undefined, selected: false,
    listeners: {},
    appendChild(child) { this.children.push(child); return child; },
    setAttribute(key, val) { this.attributes[key] = val; },
    hasAttribute(key) { return Object.prototype.hasOwnProperty.call(this.attributes, key); },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    dispatch(type, event) { (this.listeners[type] || []).forEach((fn) => fn(event)); },
    focus() { this.focused = true; },
  });
  return el;
}
function buildContext(settings, providers, inputValue) {
  const itemsInput = makeElement("input");
  itemsInput.value = inputValue || "";
  const controlHost = makeElement("div");
  const visionHost = makeElement("details");
  const elements = {};
  const document = {
    activeElement: null,
    createElement: (tag) => makeElement(tag),
    querySelector: (sel) => {
      if (sel === "[data-wardrobe-provider-control]") return controlHost;
      if (sel === "[data-wardrobe-items-input]") return itemsInput;
      if (sel === "[data-wardrobe-vision-settings]") return visionHost;
      // 渲染出来的下拉/输入框是 controlHost 的子节点，按 data 标记查找。
      if (sel === "[data-wardrobe-provider-select]") {
        return controlHost.children.find((c) => c.dataset && c.dataset.wardrobeProviderSelect) || null;
      }
      if (sel === "[data-wardrobe-provider-manual]") {
        return controlHost.children.find((c) => c.dataset && c.dataset.wardrobeProviderManual) || null;
      }
      return elements[sel] || null;
    },
    querySelectorAll: () => [],
    stub: (sel, el) => { elements[sel] = el; return el; },
  };
  const context = {
    state: { overview: { settings }, availableProviders: providers },
    postJson: async () => ({}),
    document,
  };
  return { context, controlHost, visionHost, itemsInput, document };
}
"""

    def _run(self, settings: dict, providers: list, body: str, input_value: str = "") -> dict:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        script = f"""
global.window = {{}};
const fs = require("fs");
eval(fs.readFileSync({json.dumps(str(self.MODULE), ensure_ascii=False)}, "utf8"));
{self._HARNESS}
const built = buildContext(
  {json.dumps(settings, ensure_ascii=False)},
  {json.dumps(providers, ensure_ascii=False)},
  {json.dumps(input_value, ensure_ascii=False)},
);
const context = built.context;
window.PrivateCompanionWardrobe.hydrateWardrobePanel(context);
{body}
"""
        result = subprocess.run(
            [node, "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(result.stdout)

    PROVIDERS = [
        {"id": "p1", "name": "视觉一号", "model": "gpt-4o"},
        {"id": "p2", "name": "视觉二号", "model": "qwen-vl", "is_default": True},
    ]

    def _control_state(self) -> str:
        return """
const host = built.controlHost;
const select = host.children[0];
const manual = host.children[1];
process.stdout.write(JSON.stringify({
  optionValues: select.children.map((o) => o.value),
  optionLabels: select.children.map((o) => o.textContent),
  selectName: select.name || "",
  manualName: manual.name || "",
  manualHidden: Boolean(manual.hidden),
  manualValue: manual.value || "",
}));
"""

    def test_options_come_from_available_providers(self) -> None:
        out = self._run({"WARDROBE_VISION_PROVIDER_ID": ""}, self.PROVIDERS, self._control_state())
        self.assertEqual(["", "p1", "p2", "__custom__"], out["optionValues"])
        self.assertIn("留空", out["optionLabels"][0])
        self.assertIn("视觉一号", out["optionLabels"][1])
        self.assertIn("gpt-4o", out["optionLabels"][1])
        self.assertIn("默认", out["optionLabels"][2])
        self.assertIn("手动输入", out["optionLabels"][3])

    def test_known_provider_selects_and_only_select_carries_name(self) -> None:
        out = self._run({"WARDROBE_VISION_PROVIDER_ID": "p2"}, self.PROVIDERS, self._control_state())
        self.assertEqual("WARDROBE_VISION_PROVIDER_ID", out["selectName"])
        self.assertEqual("", out["manualName"])
        self.assertTrue(out["manualHidden"])

    def test_blank_provider_keeps_select_named_and_manual_hidden(self) -> None:
        out = self._run({"WARDROBE_VISION_PROVIDER_ID": ""}, self.PROVIDERS, self._control_state())
        self.assertEqual("WARDROBE_VISION_PROVIDER_ID", out["selectName"])
        self.assertEqual("", out["manualName"])
        self.assertTrue(out["manualHidden"])

    def test_unknown_provider_falls_back_to_manual_input(self) -> None:
        out = self._run(
            {"WARDROBE_VISION_PROVIDER_ID": "my-own-provider"}, self.PROVIDERS, self._control_state()
        )
        self.assertEqual("", out["selectName"], "自定义值时 select 不应再带 name")
        self.assertEqual("WARDROBE_VISION_PROVIDER_ID", out["manualName"])
        self.assertFalse(out["manualHidden"])
        self.assertEqual("my-own-provider", out["manualValue"])

    def test_switching_to_custom_moves_the_name_to_the_input(self) -> None:
        """真实 change 事件必须把 name 从 select 挪到手动输入框。"""

        out = self._run(
            {"WARDROBE_VISION_PROVIDER_ID": "p1"},
            self.PROVIDERS,
            """
const host = built.controlHost;
const select = host.children[0];
const manual = host.children[1];
const before = { selectName: select.name || "", manualName: manual.name || "" };
select.value = "__custom__";
built.visionHost.dispatch("change", { target: select });
process.stdout.write(JSON.stringify({
  before,
  selectName: select.name || "",
  manualName: manual.name || "",
  manualHidden: Boolean(manual.hidden),
}));
""",
        )
        self.assertEqual("WARDROBE_VISION_PROVIDER_ID", out["before"]["selectName"])
        self.assertEqual("", out["before"]["manualName"])
        self.assertEqual("", out["selectName"])
        self.assertEqual("WARDROBE_VISION_PROVIDER_ID", out["manualName"])
        self.assertFalse(out["manualHidden"])

    def test_switching_back_restores_the_select_name(self) -> None:
        out = self._run(
            {"WARDROBE_VISION_PROVIDER_ID": "p1"},
            self.PROVIDERS,
            """
const host = built.controlHost;
const select = host.children[0];
const manual = host.children[1];
select.value = "__custom__";
built.visionHost.dispatch("change", { target: select });
select.value = "p2";
built.visionHost.dispatch("change", { target: select });
process.stdout.write(JSON.stringify({
  selectName: select.name || "",
  manualName: manual.name || "",
  manualHidden: Boolean(manual.hidden),
}));
""",
        )
        self.assertEqual("WARDROBE_VISION_PROVIDER_ID", out["selectName"])
        self.assertEqual("", out["manualName"])
        self.assertTrue(out["manualHidden"])

    def test_prompt_editor_uses_stored_value(self) -> None:
        out = self._run(
            {"wardrobe_image_prompt": "只描述外套。"},
            self.PROVIDERS,
            """
const editor = { value: "", placeholder: "" };
const original = built.document.querySelector;
built.document.querySelector = (sel) => (sel === "[data-wardrobe-image-prompt]"
  ? editor : original.call(built.document, sel));
window.PrivateCompanionWardrobe.hydrateWardrobePanel(context);
process.stdout.write(JSON.stringify({ value: editor.value, placeholder: editor.placeholder }));
""",
        )
        self.assertEqual("只描述外套。", out["value"])
        self.assertIn("留空", out["placeholder"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# H. 角色着装系统：部位、贴身标记、场景约束
# ---------------------------------------------------------------------------


class WardrobeSlotAndFlagTests(unittest.TestCase):
    """部位是唯一的分类维度；贴身是一条独立标记，不是一个层级。"""

    def test_slot_alias_and_substring_resolution(self) -> None:
        cases = {
            "upper": SLOT_UPPER,
            "UPPER": SLOT_UPPER,
            "上装": SLOT_UPPER,
            "上衣": SLOT_UPPER,
            "外套": SLOT_UPPER,
            "lower": SLOT_LOWER,
            "裤": SLOT_LOWER,
            "白色棉袜子": SLOT_FEET,
            "鞋子": SLOT_FEET,
            "连衣裙": SLOT_WHOLE,
            "围巾": SLOT_EXTRA,
        }
        for raw, expected in cases.items():
            self.assertEqual(expected, normalize_wardrobe_slot(raw), raw)

    def test_unknown_slot_is_unclassified_rather_than_an_error(self) -> None:
        # 分类不准只是不够精确；抛异常会打断整条命令，代价更大。
        for raw in ("", None, "不存在的部位", 42):
            self.assertEqual("", normalize_wardrobe_slot(raw), repr(raw))

    def test_precision_defaults_to_exact(self) -> None:
        self.assertEqual(PRECISION_EXACT, normalize_wardrobe_precision(None))
        self.assertEqual(PRECISION_EXACT, normalize_wardrobe_precision(""))
        self.assertEqual(PRECISION_EXACT, normalize_wardrobe_precision("exact"))
        self.assertEqual(PRECISION_LOOSE, normalize_wardrobe_precision("loose"))
        self.assertEqual(PRECISION_LOOSE, normalize_wardrobe_precision("模糊"))

    def test_bool_coercion_never_treats_false_string_as_truthy(self) -> None:
        for raw in (False, "false", "0", "off", "", None, "否", "关闭"):
            self.assertFalse(normalize_wardrobe_bool(raw), repr(raw))
        for raw in (True, "true", "1", "on", "是", "开启"):
            self.assertTrue(normalize_wardrobe_bool(raw), repr(raw))

    def test_scenes_are_validated_deduplicated_and_ordered(self) -> None:
        self.assertEqual(
            ["sleep", "home"],
            normalize_wardrobe_scenes(["sleep", "home", "bogus", "home"]),
        )
        self.assertEqual(["home", "sport"], normalize_wardrobe_scenes("home,sport,nope"))
        self.assertEqual([], normalize_wardrobe_scenes(None))

    def test_empty_scene_never_filters_anything(self) -> None:
        restricted = normalize_wardrobe_item({"name": "泳衣上装", "scenes": ["sport"]})
        unrestricted = normalize_wardrobe_item({"name": "开衫"})
        self.assertTrue(wardrobe_item_matches_scene(restricted, ""))
        self.assertTrue(wardrobe_item_matches_scene(unrestricted, "commute"))
        self.assertTrue(wardrobe_item_matches_scene(restricted, "sport"))
        self.assertFalse(wardrobe_item_matches_scene(restricted, "commute"))

    def test_legacy_item_without_new_fields_keeps_working(self) -> None:
        item = normalize_wardrobe_item({"name": "旧条目", "description": "没有新字段"})
        self.assertEqual("", item["slot"])
        self.assertFalse(item["intimate"])
        self.assertEqual(PRECISION_EXACT, item["precision"])
        self.assertEqual([], item["scenes"])

    def test_intimate_false_is_not_overwritten_by_the_alias(self) -> None:
        # 用 _first_present 而不是 or 链，否则 False 会落到下一个别名上。
        item = normalize_wardrobe_item({"name": "x", "intimate": False, "underwear": True})
        self.assertFalse(item["intimate"])

    def test_update_can_clear_slot_and_scenes(self) -> None:
        items, _ = add_wardrobe_item(None, name="开衫", slot="upper", scenes=["home"])
        items, updated = update_wardrobe_item(items, "开衫", slot="", scenes=[])
        self.assertEqual("", updated["slot"])
        self.assertEqual([], updated["scenes"])

    def test_add_keeps_new_fields_when_re_describing_same_name(self) -> None:
        items, _ = add_wardrobe_item(
            None, name="开衫", slot="upper", scenes=["home"], intimate=True
        )
        items, second = add_wardrobe_item(items, name="开衫", description="重新识图描述")
        self.assertEqual("upper", second["slot"])
        self.assertEqual(["home"], second["scenes"])
        self.assertTrue(second["intimate"])
        self.assertEqual(1, len(items))


# ---------------------------------------------------------------------------
# I. 渲染：按部位分组、贴身标记、场景过滤、预算截断
# ---------------------------------------------------------------------------


def _sample_items() -> list[dict]:
    return normalize_wardrobe_items(
        [
            {"name": "米色针织开衫", "description": "宽松版型", "slot": "upper", "tags": ["居家"]},
            {
                "name": "白色棉质内衣",
                "description": "无钢圈",
                "slot": "upper",
                "intimate": True,
                "scenes": ["home"],
            },
            {"name": "深色直筒长裤", "slot": "lower"},
            {"name": "帆布鞋", "slot": "feet"},
            {"name": "细框眼镜", "slot": "extra"},
            {"name": "旧T恤", "description": "没标部位"},
        ]
    )


class WardrobeRenderTests(unittest.TestCase):
    def test_block_groups_by_slot_and_marks_intimate(self) -> None:
        block = render_wardrobe_block("偏爱宽松", _sample_items())
        for header in ("── 上身 ──", "── 下身 ──", "── 足部 ──", "── 配件 ──", "── 未分类 ──"):
            self.assertIn(header, block)
        self.assertIn("白色棉质内衣（贴身）", block)
        # 部位的固定顺序：上身在下身之前，未分类排最后。
        self.assertLess(block.index("── 上身 ──"), block.index("── 下身 ──"))
        self.assertLess(block.index("── 配件 ──"), block.index("── 未分类 ──"))

    def test_block_never_emits_legacy_bracket_headings(self) -> None:
        # 仓库 CI 的 raw_legacy_heading 规则禁止提示词里出现字面量【】标题，
        # 只允许 conversation_prompt_section.py 的 canonical renderer 使用。
        block = render_wardrobe_block("偏爱宽松", _sample_items())
        prompt = render_wardrobe_prompt("偏爱宽松", _sample_items())
        selection = select_wardrobe_outfit(_sample_items(), [], scene="", seed="s")
        outfit_prompt = render_wardrobe_outfit_prompt("偏爱宽松", selection)
        for text in (block, prompt, outfit_prompt):
            self.assertNotIn(chr(0x3010), text)
            self.assertNotIn(chr(0x3011), text)

    def test_block_scene_filter_drops_restricted_items(self) -> None:
        home = render_wardrobe_block("", _sample_items(), scene="home")
        commute = render_wardrobe_block("", _sample_items(), scene="commute")
        self.assertIn("白色棉质内衣", home)
        self.assertNotIn("白色棉质内衣", commute)

    def test_block_truncation_reports_remaining_count(self) -> None:
        block = render_wardrobe_block("", _sample_items(), max_items=2)
        self.assertIn("（另有 4 件未列出）", block)

    def test_block_stays_within_the_character_budget(self) -> None:
        block = render_wardrobe_block("x" * 2000, _sample_items(), max_chars=200)
        self.assertLessEqual(len(block), 200)

    def test_prompt_preamble_states_priority_and_background_role(self) -> None:
        body = render_wardrobe_prompt("偏爱宽松", _sample_items())
        self.assertTrue(body.startswith(WARDROBE_PROMPT_PREAMBLE))
        self.assertIn("除非用户明确要求换装", body)
        self.assertIn("背景事实", body)
        self.assertIn("不要主动汇报", body)

    def test_outfit_prompt_is_empty_for_an_empty_selection(self) -> None:
        self.assertEqual("", render_wardrobe_outfit_prompt("x", None))
        self.assertEqual("", render_wardrobe_outfit_prompt("x", {}))
        self.assertEqual("", render_wardrobe_outfit_prompt("x", {"prompt_text": "   "}))

    def test_outfit_prompt_includes_tendency_and_current_outfit(self) -> None:
        selection = {"prompt_text": "上身：开衫\n下身：长裤"}
        body = render_wardrobe_outfit_prompt("偏爱宽松", selection)
        self.assertTrue(body.startswith(WARDROBE_PROMPT_PREAMBLE))
        self.assertIn("整体服饰倾向：偏爱宽松", body)
        self.assertIn("当前着装：", body)
        self.assertIn("上身：开衫", body)

    def test_summary_lines_show_slot_and_intimate_marker(self) -> None:
        lines = wardrobe_summary_lines(_sample_items())
        self.assertTrue(any("(上身)" in line for line in lines))
        self.assertTrue(any("(上身·贴身)" in line for line in lines))


# ---------------------------------------------------------------------------
# J. 整套（style / bundle）实体
# ---------------------------------------------------------------------------


class WardrobeOutfitEntityTests(unittest.TestCase):
    def test_kind_is_inferred_when_omitted(self) -> None:
        # 有 items -> 准确组合；只有 style -> 模糊整套。
        self.assertEqual(
            OUTFIT_KIND_BUNDLE,
            normalize_wardrobe_outfit({"name": "A", "items": ["x"]})["kind"],
        )
        self.assertEqual(
            OUTFIT_KIND_STYLE,
            normalize_wardrobe_outfit({"name": "B", "style": "宽松"})["kind"],
        )

    def test_bundle_without_items_degrades_to_style(self) -> None:
        # 声称是组合却没有件，等同于空组合；降级比渲染空壳好。
        outfit = normalize_wardrobe_outfit(
            {"name": "空组合", "kind": "bundle", "style": "随便"}
        )
        self.assertEqual(OUTFIT_KIND_STYLE, outfit["kind"])

    def test_kind_accepts_chinese_input(self) -> None:
        self.assertEqual(
            OUTFIT_KIND_BUNDLE,
            normalize_wardrobe_outfit({"name": "A", "kind": "组合", "items": ["x"]})["kind"],
        )
        self.assertEqual(
            OUTFIT_KIND_STYLE,
            normalize_wardrobe_outfit({"name": "B", "kind": "模糊", "style": "s"})["kind"],
        )

    def test_empty_outfit_is_dropped(self) -> None:
        self.assertIsNone(normalize_wardrobe_outfit({}))
        self.assertIsNone(normalize_wardrobe_outfit(None))
        self.assertEqual([], normalize_wardrobe_outfits([{}, None, "x"]))

    def test_items_are_deduplicated_and_capped(self) -> None:
        outfit = normalize_wardrobe_outfit(
            {"name": "A", "items": ["a", "a", "b", "c", "d", "e", "f", "g", "h", "i"]}
        )
        self.assertEqual(["a", "b", "c", "d", "e", "f", "g", "h"], outfit["items"])

    def test_outfit_scene_matching_is_open_by_default(self) -> None:
        open_outfit = normalize_wardrobe_outfit({"name": "通用", "style": "s"})
        scoped = normalize_wardrobe_outfit(
            {"name": "通勤", "style": "s", "scenes": ["commute"]}
        )
        self.assertTrue(wardrobe_outfit_matches_scene(open_outfit, "home"))
        self.assertTrue(wardrobe_outfit_matches_scene(scoped, ""))
        self.assertTrue(wardrobe_outfit_matches_scene(scoped, "commute"))
        self.assertFalse(wardrobe_outfit_matches_scene(scoped, "home"))

    def test_add_find_update_delete_round_trip(self) -> None:
        outfits, added = add_wardrobe_outfit(
            None, name="通勤正装", kind="bundle", items=["a", "b"]
        )
        self.assertEqual(1, len(outfits))
        self.assertEqual(added["id"], find_wardrobe_outfit(outfits, "通勤")["id"])
        self.assertEqual(added["id"], find_wardrobe_outfit(outfits, "1")["id"])
        outfits, updated = update_wardrobe_outfit(outfits, "通勤正装", style="干净利落")
        self.assertEqual("干净利落", updated["style"])
        outfits, removed = delete_wardrobe_outfit(outfits, "通勤正装")
        self.assertEqual([], outfits)
        self.assertEqual(added["id"], removed["id"])

    def test_same_name_replaces_instead_of_piling_up(self) -> None:
        outfits, _ = add_wardrobe_outfit(None, name="居家", style="宽松")
        outfits, second = add_wardrobe_outfit(outfits, name="居家", style="更宽松")
        self.assertEqual(1, len(outfits))
        self.assertEqual("更宽松", second["style"])

    def test_missing_outfit_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            delete_wardrobe_outfit([], "不存在")

    def test_invalid_outfit_raises_wardrobe_error(self) -> None:
        with self.assertRaises(WardrobeError):
            new_wardrobe_outfit("")


# ---------------------------------------------------------------------------
# K. 规则选择器与边界用例
# ---------------------------------------------------------------------------


def _wardrobe_fixture() -> tuple[list[dict], list[dict]]:
    items = normalize_wardrobe_items(
        [
            {"name": "米色针织开衫", "description": "宽松", "slot": "upper"},
            {"name": "白色棉质内衣", "slot": "upper", "intimate": True, "scenes": ["home"]},
            {"name": "棉质内裤", "slot": "lower", "intimate": True, "scenes": ["home"]},
            {"name": "深色直筒长裤", "slot": "lower"},
            {"name": "白色棉袜", "slot": "feet"},
            {"name": "帆布鞋", "description": "低帮", "slot": "feet"},
            {"name": "细框眼镜", "slot": "extra"},
            {"name": "分体泳衣上装", "slot": "upper", "scenes": ["sport"]},
            {"name": "分体泳衣下装", "slot": "lower", "scenes": ["sport"]},
            {"name": "碎花连衣裙", "slot": "whole", "scenes": ["daily"]},
        ]
    )
    by_name = {item["name"]: item["id"] for item in items}
    outfits = normalize_wardrobe_outfits(
        [
            {
                "name": "通勤正装",
                "kind": "bundle",
                "items": [by_name["米色针织开衫"], by_name["深色直筒长裤"]],
                "scenes": ["commute"],
            },
            {
                "name": "慵懒周末",
                "kind": "style",
                "style": "宽松棉质，低饱和色",
                "scenes": ["home"],
            },
        ]
    )
    return items, outfits


class WardrobeOutfitSelectionTests(unittest.TestCase):
    """规则选择器：模型路径失败时的降级方案，也是面板的离线预览。"""

    def test_bundle_wins_over_style_and_rule(self) -> None:
        items, outfits = _wardrobe_fixture()
        result = select_wardrobe_outfit(items, outfits, scene="commute", seed="d1")
        self.assertEqual("bundle", result["source"])
        self.assertEqual("通勤正装", result["outfit_name"])
        self.assertEqual(["bottom", "top"], sorted(result["profile"]))

    def test_style_is_used_when_no_bundle_matches(self) -> None:
        items, outfits = _wardrobe_fixture()
        result = select_wardrobe_outfit(items, outfits, scene="home", seed="d1")
        self.assertEqual("style", result["source"])
        self.assertEqual("慵懒周末", result["outfit_name"])
        self.assertIn("宽松棉质", result["prompt_text"])
        self.assertEqual({}, result["profile"])

    def test_bundle_referencing_deleted_items_falls_through(self) -> None:
        items, outfits = _wardrobe_fixture()
        # 把 bundle 引用的散件全部删掉，它就不该再被选中。
        remaining = [item for item in items if item["name"] not in {"米色针织开衫", "深色直筒长裤"}]
        result = select_wardrobe_outfit(remaining, outfits, scene="commute", seed="d1")
        self.assertEqual("rule", result["source"])

    def test_rule_path_takes_at_most_one_item_per_slot(self) -> None:
        items, _ = _wardrobe_fixture()
        result = select_wardrobe_outfit(items, [], scene="commute", seed="d1")
        slots = [picked["slot"] for picked in result["picked"] if not picked["intimate"]]
        self.assertEqual(len(slots), len(set(slots)))

    def test_intimate_layer_is_an_independent_axis(self) -> None:
        # 穿开衫的同时也穿内衣，两者不能互相竞争同一个名额。
        items, _ = _wardrobe_fixture()
        result = select_wardrobe_outfit(items, [], scene="home", seed="d1")
        picked = {picked["name"]: picked for picked in result["picked"]}
        self.assertIn("米色针织开衫", picked)
        self.assertIn("白色棉质内衣", picked)
        self.assertIn("深色直筒长裤", picked)
        self.assertIn("棉质内裤", picked)

    def test_intimate_stays_out_of_the_photo_profile_but_in_the_prompt(self) -> None:
        items, _ = _wardrobe_fixture()
        result = select_wardrobe_outfit(items, [], scene="home", seed="d1")
        profile_blob = " ".join(result["profile"].values())
        self.assertNotIn("内衣", profile_blob)
        self.assertNotIn("内裤", profile_blob)
        self.assertIn("内衣", result["prompt_text"])
        self.assertIn("（贴身）", result["prompt_text"])

    def test_whole_and_separates_are_exclusive_and_both_get_turns(self) -> None:
        # 连衣裙与「上装 + 下装」是两种互斥穿法。如果无条件让 whole 压制上下装，
        # 一件不受场景限制的连衣裙就会永远霸占衣柜 —— 所以这里既验证互斥，
        # 也验证两者都会轮到。
        items = normalize_wardrobe_items(
            [
                {"name": "碎花连衣裙", "slot": "whole", "scenes": ["home"]},
                {"name": "米色针织开衫", "slot": "upper", "scenes": ["home"]},
                {"name": "深色直筒长裤", "slot": "lower", "scenes": ["home"]},
                {"name": "白色棉质内衣", "slot": "upper", "intimate": True, "scenes": ["home"]},
            ]
        )
        seen_whole = seen_separates = False
        for day in range(1, 40):
            result = select_wardrobe_outfit(items, [], scene="home", seed=f"2026-09-{day:02d}")
            names = {picked["name"] for picked in result["picked"]}
            # 贴身件与穿法无关：穿连衣裙时也穿内衣。
            self.assertIn("白色棉质内衣", names)
            has_whole = "碎花连衣裙" in names
            has_separates = bool({"米色针织开衫", "深色直筒长裤"} & names)
            self.assertFalse(has_whole and has_separates, f"day {day} 同时出现整身与上下装")
            seen_whole = seen_whole or has_whole
            seen_separates = seen_separates or has_separates
        self.assertTrue(seen_whole, "应至少有一天穿连衣裙")
        self.assertTrue(seen_separates, "应至少有一天穿上装 + 下装")

    def test_swimwear_is_kept_out_of_the_commute_scene(self) -> None:
        # 每个场景的候选池都只留一件，断言才是确定的：衣物 id 由 uuid4 生成，
        # 候选池按 id 排序，所以"多候选取其一"的结果会随运行变化 —— 那不是
        # 被测行为，只是测试不确定性。
        items = normalize_wardrobe_items(
            [
                {"name": "米色针织开衫", "slot": "upper", "scenes": ["commute"]},
                {"name": "深色直筒长裤", "slot": "lower", "scenes": ["commute"]},
                {"name": "分体泳衣上装", "slot": "upper", "scenes": ["sport"]},
                {"name": "分体泳衣下装", "slot": "lower", "scenes": ["sport"]},
            ]
        )
        commute = select_wardrobe_outfit(items, [], scene="commute", seed="d1")
        self.assertEqual(
            {"米色针织开衫", "深色直筒长裤"},
            {picked["name"] for picked in commute["picked"]},
        )
        sport = select_wardrobe_outfit(items, [], scene="sport", seed="d1")
        self.assertEqual(
            {"分体泳衣上装", "分体泳衣下装"},
            {picked["name"] for picked in sport["picked"]},
        )

    def test_same_seed_is_idempotent_so_the_outfit_never_flaps(self) -> None:
        items, outfits = _wardrobe_fixture()
        first = select_wardrobe_outfit(items, outfits, scene="sport", seed="2026-09-12|u1")
        second = select_wardrobe_outfit(items, outfits, scene="sport", seed="2026-09-12|u1")
        self.assertEqual(first["look_id"], second["look_id"])
        self.assertEqual([p["id"] for p in first["picked"]], [p["id"] for p in second["picked"]])

    def test_a_new_day_can_produce_a_different_look(self) -> None:
        items, outfits = _wardrobe_fixture()
        looks = {
            select_wardrobe_outfit(items, outfits, scene="sport", seed=f"2026-09-{day}|u1")["look_id"]
            for day in range(10, 25)
        }
        self.assertGreater(len(looks), 1)

    def test_empty_wardrobe_yields_an_empty_selection_without_raising(self) -> None:
        result = select_wardrobe_outfit([], [], scene="home", seed="d1")
        self.assertEqual("rule", result["source"])
        self.assertEqual("", result["prompt_text"])
        self.assertEqual({}, result["profile"])
        self.assertEqual([], result["picked"])

    def test_scene_filtering_removes_everything_gracefully(self) -> None:
        items = normalize_wardrobe_items(
            [{"name": "泳衣", "slot": "upper", "scenes": ["sport"]}]
        )
        result = select_wardrobe_outfit(items, [], scene="commute", seed="d1")
        self.assertEqual([], result["picked"])
        self.assertEqual("", result["prompt_text"])

    def test_recent_ids_are_avoided_when_alternatives_exist(self) -> None:
        items = normalize_wardrobe_items(
            [
                {"name": "开衫A", "slot": "upper"},
                {"name": "开衫B", "slot": "upper"},
            ]
        )
        worn = items[0]["id"]
        result = select_wardrobe_outfit(
            items, [], scene="", seed="fixed", recent_ids={worn}
        )
        picked = {p["id"] for p in result["picked"]}
        self.assertNotIn(worn, picked)
        self.assertIn(items[1]["id"], picked)

    def test_selection_is_always_renderable_and_within_budget(self) -> None:
        items, outfits = _wardrobe_fixture()
        for scene in ("", "home", "commute", "sport", "daily", "school"):
            result = select_wardrobe_outfit(items, outfits, scene=scene, seed="2026-09-12")
            body = render_wardrobe_outfit_prompt("偏爱宽松", result)
            self.assertLessEqual(len(body), 900, scene)
            if result["prompt_text"]:
                self.assertTrue(body.startswith(WARDROBE_PROMPT_PREAMBLE), scene)


# ---------------------------------------------------------------------------
# L. 生成器：请求构造、回复解析、渲染与生图投影
# ---------------------------------------------------------------------------


class WardrobeOutfitGeneratorTests(unittest.TestCase):
    def _items(self) -> list[dict]:
        return normalize_wardrobe_items(
            [
                {"name": "米色针织开衫", "description": "宽松", "slot": "upper", "tags": ["居家"]},
                {"name": "白色棉质内衣", "slot": "upper", "intimate": True, "scenes": ["home"]},
                {"name": "深色直筒长裤", "slot": "lower"},
                {"name": "帆布鞋", "slot": "feet"},
                {"name": "分体泳衣上装", "slot": "upper", "scenes": ["sport"]},
            ]
        )

    def test_request_carries_context_inventory_and_rules(self) -> None:
        outfits = normalize_wardrobe_outfits(
            [{"name": "慵懒周末", "kind": "style", "style": "宽松棉质", "scenes": ["home"]}]
        )
        request = build_wardrobe_outfit_request(
            self._items(),
            outfits,
            tendency="偏爱宽松针织",
            scene="home",
            weather="冷",
            recent_names=["旧外套"],
        )
        self.assertIn("场景：home", request)
        self.assertIn("天气：冷", request)
        self.assertIn("偏爱宽松针织", request)
        self.assertIn("宽松棉质", request)
        self.assertIn("米色针织开衫", request)
        self.assertIn("最近穿过", request)
        self.assertIn("每个部位最多选一件", request)
        self.assertIn('"summary"', request)

    def test_request_respects_the_scene_filter(self) -> None:
        home = build_wardrobe_outfit_request(self._items(), None, scene="home")
        sport = build_wardrobe_outfit_request(self._items(), None, scene="sport")
        self.assertIn("白色棉质内衣", home)
        self.assertNotIn("白色棉质内衣", sport)
        self.assertIn("分体泳衣上装", sport)

    def test_request_never_uses_legacy_bracket_headings(self) -> None:
        request = build_wardrobe_outfit_request(self._items(), None, scene="home")
        self.assertNotIn(chr(0x3010), request)
        self.assertNotIn(chr(0x3011), request)

    def test_request_stays_within_its_own_budget(self) -> None:
        request = build_wardrobe_outfit_request(
            self._items(), None, scene="home", tendency="x" * 4000
        )
        self.assertLessEqual(len(request), WARDROBE_OUTFIT_REQUEST_LIMIT)

    def test_parse_accepts_bare_and_fenced_json(self) -> None:
        bare = '{"top": "衬衫", "bottom": "长裤"}'
        fenced = chr(96) * 3 + 'json\n' + bare + '\n' + chr(96) * 3
        with_prose = "好的，这是搭配：\n" + bare + "\n希望合适。"
        for raw in (bare, fenced, with_prose):
            parsed = parse_wardrobe_outfit_reply(raw)
            self.assertIsNotNone(parsed, raw)
            self.assertEqual("衬衫", parsed["top"])
            self.assertEqual("长裤", parsed["bottom"])

    def test_parse_rejects_unusable_replies(self) -> None:
        # 只有概述或只有配色等于什么都没生成，必须让调用方回退规则选择器。
        for raw in (
            "",
            "   ",
            "我不确定",
            "{}",
            "null",
            "[1, 2]",
            '{"summary": "只有概述"}',
            '{"palette": "低饱和"}',
        ):
            self.assertIsNone(parse_wardrobe_outfit_reply(raw), repr(raw))

    def test_parse_truncates_overlong_fields(self) -> None:
        parsed = parse_wardrobe_outfit_reply('{"top": "' + "x" * 500 + '"}')
        self.assertEqual(WARDROBE_OUTFIT_FIELD_LIMIT, len(parsed["top"]))

    def test_render_puts_summary_first_and_marks_intimate(self) -> None:
        parsed = parse_wardrobe_outfit_reply(
            '{"summary": "居家放松", "top": "针织开衫", "bottom": "长裤",'
            ' "underwear_top": "棉质内衣", "footwear": "帆布鞋"}'
        )
        body = render_generated_outfit(parsed)
        lines = body.split("\n")
        self.assertEqual("居家放松", lines[0])
        self.assertIn("内衣：棉质内衣（贴身）", body)
        self.assertIn("上装：针织开衫", body)
        # 贴身层排在外衣之前，符合穿着顺序。
        self.assertLess(body.index("内衣："), body.index("上装："))

    def test_render_is_empty_for_an_empty_payload(self) -> None:
        self.assertEqual("", render_generated_outfit(None))
        self.assertEqual("", render_generated_outfit({}))

    def test_photo_profile_drops_intimate_fields(self) -> None:
        parsed = parse_wardrobe_outfit_reply(
            '{"top": "针织开衫", "underwear_top": "棉质内衣",'
            ' "underwear_bottom": "棉质内裤", "palette": "低饱和"}'
        )
        profile = outfit_photo_profile(parsed)
        self.assertEqual({"top": "针织开衫", "palette": "低饱和"}, profile)
        self.assertNotIn("underwear_top", profile)
        self.assertNotIn("underwear_bottom", profile)

    def test_photo_profile_matches_the_rule_path_field_names(self) -> None:
        # 两条路径最终都要落进作者的 outfit_profile，字段名必须一致。
        parsed = parse_wardrobe_outfit_reply('{"footwear": "帆布鞋"}')
        profile = outfit_photo_profile(parsed)
        self.assertEqual(set(profile) - {"footwear"}, set())
        self.assertIn("footwear", profile)

    def test_generated_outfit_renders_into_the_injection_budget(self) -> None:
        parsed = parse_wardrobe_outfit_reply(
            json.dumps(
                {
                    "summary": "s" * 200,
                    "top": "t" * 160,
                    "outer": "o" * 160,
                    "bottom": "b" * 160,
                    "footwear": "f" * 160,
                    "accessory": "a" * 160,
                    "palette": "p" * 160,
                    "silhouette": "l" * 160,
                    "underwear_top": "u" * 160,
                    "underwear_bottom": "v" * 160,
                }
            )
        )
        body = render_wardrobe_outfit_prompt("偏爱宽松", {"prompt_text": render_generated_outfit(parsed)})
        self.assertTrue(body.startswith(WARDROBE_PROMPT_PREAMBLE))
        self.assertNotIn(chr(0x3010), body)


# ---------------------------------------------------------------------------
# M. 搭配测试面板的后端契约与生成器运行时
# ---------------------------------------------------------------------------


class WardrobeOutfitPreviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = _WardrobeCommandHarness()
        self.plugin.config.update(
            {
                "wardrobe_outfit_mode": "select",
                "wardrobe_items": [
                    {"name": "米色针织开衫", "slot": "upper"},
                    {"name": "白色棉质内衣", "slot": "upper", "intimate": True},
                    {"name": "深色直筒长裤", "slot": "lower"},
                    {"name": "分体泳衣上装", "slot": "upper", "scenes": ["sport"]},
                ],
            }
        )

    def test_preview_reports_the_injection_without_writing_config(self) -> None:
        before = {key: value for key, value in self.plugin.config.items()}
        data = self.plugin._wardrobe_outfit_preview(scene="home", weather="冷")
        self.assertEqual(before, self.plugin.config)
        self.assertEqual("select", data["mode"])
        self.assertEqual("home", data["scene"])
        self.assertEqual("冷", data["weather"])
        self.assertEqual(4, data["item_count"])
        self.assertIn("场景：home", data["request"])
        self.assertIn("天气：冷", data["request"])
        self.assertEqual(data["injected_chars"], len(data["injected"]))
        self.assertLessEqual(data["injected_chars"], data["injected_limit"])

    def test_preview_lets_the_panel_simulate_another_occasion(self) -> None:
        home = self.plugin._wardrobe_outfit_preview(scene="home")
        sport = self.plugin._wardrobe_outfit_preview(scene="sport")
        home_names = {row["name"] for row in home["rule"]["picked"]}
        sport_names = {row["name"] for row in sport["rule"]["picked"]}
        self.assertNotIn("分体泳衣上装", home_names)
        self.assertIn("分体泳衣上装", sport_names)

    def test_preview_keeps_intimate_out_of_the_photo_projection(self) -> None:
        data = self.plugin._wardrobe_outfit_preview(scene="home")
        profile_blob = " ".join(data["rule"]["profile"].values())
        self.assertNotIn("内衣", profile_blob)
        self.assertIn("内衣", data["rule"]["prompt_text"])

    def test_preview_handles_an_empty_wardrobe(self) -> None:
        self.plugin.config["wardrobe_items"] = []
        data = self.plugin._wardrobe_outfit_preview(scene="home")
        self.assertEqual(0, data["item_count"])
        self.assertEqual([], data["rule"]["picked"])
        self.assertEqual("", data["injected"])
        self.assertFalse(data["generator_ready"])

    def test_generator_is_off_by_default(self) -> None:
        self.assertFalse(self.plugin._wardrobe_generator_enabled())
        self.assertIsNone(self.plugin._wardrobe_cached_generated_outfit())
        data = self.plugin._wardrobe_outfit_preview(scene="home")
        self.assertFalse(data["generator_enabled"])
        self.assertIn(data["rule"]["source"], {"rule", "bundle", "style"})

    async def test_generate_returns_none_without_an_llm_entry_point(self) -> None:
        self.plugin.config["enable_wardrobe_outfit_generate"] = True
        self.assertIsNone(await self.plugin._wardrobe_generate_outfit(None))

    async def test_generate_parses_the_reply_and_then_serves_it_from_cache(self) -> None:
        calls: list[dict] = []

        async def _fake_llm(prompt, **kwargs):
            calls.append({"prompt": prompt, **kwargs})
            return '{"top": "米色针织开衫", "bottom": "深色长裤", "underwear_top": "棉质内衣"}'

        self.plugin._llm_call = _fake_llm
        self.plugin.config["enable_wardrobe_outfit_generate"] = True
        payload = await self.plugin._wardrobe_generate_outfit(None)
        self.assertEqual("米色针织开衫", payload["top"])
        self.assertEqual("wardrobe_outfit_generate", calls[0]["task"])
        again = await self.plugin._wardrobe_generate_outfit(None)
        self.assertEqual(payload, again)
        self.assertEqual(1, len(calls), "第二次调用必须走缓存，不能再打模型")

    async def test_generate_caches_an_unusable_reply_and_degrades(self) -> None:
        calls: list[str] = []

        async def _bad_llm(prompt, **kwargs):
            calls.append(prompt)
            return "我不确定"

        self.plugin._llm_call = _bad_llm
        self.plugin.config["enable_wardrobe_outfit_generate"] = True
        self.assertIsNone(await self.plugin._wardrobe_generate_outfit(None))
        self.assertIsNone(await self.plugin._wardrobe_generate_outfit(None))
        self.assertEqual(1, len(calls), "失败结果也要入缓存，避免同一天反复重试")

    async def test_generate_survives_a_model_exception(self) -> None:
        async def _boom(prompt, **kwargs):
            raise RuntimeError("boom")

        self.plugin._llm_call = _boom
        self.plugin.config["enable_wardrobe_outfit_generate"] = True
        self.assertIsNone(await self.plugin._wardrobe_generate_outfit(None))

    async def test_cached_generation_replaces_the_rule_selection(self) -> None:
        self.plugin.config["enable_wardrobe_outfit_generate"] = True
        self.plugin._wardrobe_outfit_cache()[self.plugin._wardrobe_outfit_cache_key()] = {
            "top": "生成的上装",
            "underwear_top": "生成的内衣",
        }
        body = self.plugin._wardrobe_selected_outfit_body(None, "偏爱宽松")
        self.assertIn("生成的上装", body)
        self.assertIn("生成的内衣（贴身）", body)
        self.assertNotIn(chr(0x3010), body)

    async def test_inventory_mode_ignores_the_generator_entirely(self) -> None:
        self.plugin.config["wardrobe_outfit_mode"] = "inventory"
        self.plugin.config["enable_wardrobe_outfit_generate"] = True
        section = self.plugin._wardrobe_prompt_section(None)
        self.assertIsNotNone(section)
        self.assertTrue(section.content.startswith(WARDROBE_PROMPT_PREAMBLE))
        self.assertIn("衣柜里的具体衣物", section.content)



    def test_preview_reports_unclassified_items(self) -> None:
        # 面板据此提示"这几件不参与自动搭配"，免得用户纳闷为什么它们从不出现。
        self.plugin.config["wardrobe_items"] = [
            {"name": "开衫", "slot": "upper"},
            {"name": "来历不明的外套", "description": "没标部位"},
            {"name": "另一件没标的"},
        ]
        data = self.plugin._wardrobe_outfit_preview(scene="home")
        self.assertEqual(2, data["unclassified_count"])

    def test_preview_reports_zero_unclassified_when_all_classified(self) -> None:
        data = self.plugin._wardrobe_outfit_preview(scene="home")
        self.assertEqual(0, data["unclassified_count"])


# ---------------------------------------------------------------------------
# N. 回归：review 阶段发现、原测试未覆盖的两个真实缺陷
# ---------------------------------------------------------------------------


class WardrobeDeterminismRegressionTests(unittest.TestCase):
    def test_items_without_ids_still_pick_a_stable_outfit(self) -> None:
        # 兜底 id 曾经用 uuid4：每次归一化都重新生成，而候选池按 id 排序，
        # 于是手写配置（没有 id）每调用一次就换一套衣服 —— 8 次能出 4 种组合。
        raw = [
            {"name": "开衫A", "slot": "upper"},
            {"name": "开衫B", "slot": "upper"},
            {"name": "长裤A", "slot": "lower"},
            {"name": "长裤B", "slot": "lower"},
        ]
        combos = set()
        for _ in range(8):
            items = normalize_wardrobe_items(copy.deepcopy(raw))
            result = select_wardrobe_outfit(items, [], scene="home", seed="2026-09-12")
            combos.add(tuple(sorted(row["name"] for row in result["picked"])))
        self.assertEqual(1, len(combos), combos)

    def test_derived_ids_are_stable_across_normalizations(self) -> None:
        raw = {"name": "开衫", "description": "宽松"}
        first = normalize_wardrobe_item(dict(raw))["id"]
        second = normalize_wardrobe_item(dict(raw))["id"]
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("wardrobe_"))

    def test_explicit_ids_are_never_replaced(self) -> None:
        self.assertEqual(
            "my-own-id", normalize_wardrobe_item({"id": "my-own-id", "name": "开衫"})["id"]
        )

    def test_candidate_order_does_not_depend_on_ids(self) -> None:
        # 双层防护：即使 id 不稳定，挑选顺序也按内容走。
        base = [
            {"name": "开衫", "slot": "upper"},
            {"name": "外套", "slot": "upper"},
        ]
        first = normalize_wardrobe_items([dict(row, id=f"a{i}") for i, row in enumerate(base)])
        second = normalize_wardrobe_items([dict(row, id=f"z{i}") for i, row in enumerate(base)])
        picked_a = select_wardrobe_outfit(first, [], scene="home", seed="s")["picked"]
        picked_b = select_wardrobe_outfit(second, [], scene="home", seed="s")["picked"]
        self.assertEqual(
            [row["name"] for row in picked_a], [row["name"] for row in picked_b]
        )

    def test_wardrobe_without_any_slot_still_resolves_an_outfit(self) -> None:
        # 未分类衣物没有部位可依据，正常不参与组合；但整柜都未分类时不能空手
        # 而归，否则 select 模式永远只能落回整份清单。
        legacy = normalize_wardrobe_items(
            [
                {"name": "旧T恤", "description": "没标部位"},
                {"name": "旧裤子", "description": "也没标"},
            ]
        )
        result = select_wardrobe_outfit(legacy, [], scene="home", seed="x")
        self.assertEqual(["旧T恤", "旧裤子"], [row["name"] for row in result["picked"]])
        self.assertIn("其他：旧T恤", result["prompt_text"])
        self.assertTrue(render_wardrobe_outfit_prompt("", result))

    def test_unclassified_fallback_does_not_displace_classified_items(self) -> None:
        items = normalize_wardrobe_items(
            [
                {"name": "开衫", "slot": "upper"},
                {"name": "来历不明的外套", "description": "没标部位"},
            ]
        )
        names = [row["name"] for row in select_wardrobe_outfit(items, [], scene="home", seed="x")["picked"]]
        self.assertIn("开衫", names)
        self.assertNotIn("来历不明的外套", names)
