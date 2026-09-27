# -*- coding: utf-8 -*-
"""WardrobeMixin — 角色衣柜的运行时接线。

数据层在 :mod:`wardrobe`，本模块只负责把它接进插件运行时：

* 读数：``runtime_persona_setting``（人格作用域）＋ 运行时属性回退。
* 落盘：``_set_into_config`` + ``_save_config_if_possible``，失败即回滚。
* 识图：复用陪伴已有的视觉 provider 解析，但用衣柜专用的衣物描述提示词。
* 提示词：产出一个 ``PromptSection``，由被动回复链注入。
* 命令：``陪伴 衣柜 ...`` 的全部子命令。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _flat_get, _now_ts, _set_into_config, _single_line, _today_key
from .persona_config import PERSONA_SETTINGS_KEY, runtime_persona_setting
from .wardrobe import (
    OUTFIT_KIND_BUNDLE,
    OUTFIT_KIND_STYLE,
    OWNERSHIP_OWNED,
    OWNERSHIP_REFERENCE,
    WARDROBE_IMAGE_KIND_ITEM,
    WARDROBE_IMAGE_KIND_NONE,
    WARDROBE_IMAGE_KIND_OUTFIT,
    WARDROBE_IMAGE_KIND_REFERENCE,
    SOURCE_KIND_IMAGE,
    SOURCE_KIND_MANUAL,
    WARDROBE_MAX_DESCRIPTION,
    WARDROBE_MAX_ITEMS,
    WARDROBE_MAX_NAME,
    WARDROBE_MAX_OUTFITS,
    WARDROBE_MAX_TAG,
    WARDROBE_PROMPT_MAX_CHARS,
    WARDROBE_PROMPT_MAX_ITEMS,
    WARDROBE_PROMPT_PREAMBLE,
    WARDROBE_SLOT_LABELS,
    WardrobeError,
    WardrobeLimitError,
    add_wardrobe_item,
    add_wardrobe_outfit,
    apply_wardrobe_draft,
    build_wardrobe_image_instruction,
    build_wardrobe_outfit_request,
    clear_wardrobe,
    delete_wardrobe_item,
    delete_wardrobe_outfit,
    find_wardrobe_item,
    find_wardrobe_item_by_exact_name,
    find_wardrobe_outfit,
    find_wardrobe_outfit_by_exact_name,
    infer_wardrobe_slot,
    normalize_wardrobe_image_prompt,
    normalize_wardrobe_items,
    normalize_wardrobe_outfits,
    normalize_wardrobe_slot,
    normalize_wardrobe_tendency,
    outfit_photo_profile,
    outfit_photo_profile_from_items,
    parse_wardrobe_image_reply,
    parse_wardrobe_outfit_reply,
    render_generated_outfit,
    render_wardrobe_outfit_prompt,
    render_wardrobe_prompt,
    render_worn_items,
    truncate_wardrobe_text,
    select_wardrobe_outfit,
    update_wardrobe_item,
    update_wardrobe_outfit,
    wardrobe_summary_lines,
)
from .wardrobe_assets import (
    ASSET_ORIGIN_BLOGGER,
    ASSET_ORIGIN_LOCAL,
    ASSET_ORIGIN_PANEL,
    ASSET_ORIGIN_SCREENSHOT,
    ASSET_ORIGIN_SHARE_TEXT,
    ASSET_ORIGIN_TAOBAO,
    ASSET_STATUS_IMPORTED,
    ASSET_STATUS_REJECTED,
    ASSET_STATUS_UNDERSTOOD,
    asset_abs_path,
    import_asset,
    list_pending_drafts,
    load_asset_draft,
    load_asset_index,
    mark_asset_status,
)
from .wardrobe_style import render_reference_profile

WARDROBE_PROMPT_KEY = "wardrobe.character"

# 注入详略：full＝每轮都注入完整着装（原有行为）；
# progressive＝常驻只给一行"今天穿什么"，细节等用户问到再展开。
WARDROBE_DETAIL_FULL = "full"
WARDROBE_DETAIL_PROGRESSIVE = "progressive"
WARDROBE_MINIMAL_MAX_CHARS = 200

# 触发词：命中才展开完整描述。让模型自己决定"要不要展开"不可预测，
# 所以触发权收在关键词与命令上（架构 §风险：触发权）。
_WARDROBE_DETAIL_TRIGGERS = (
    "衣服", "穿着", "穿搭", "着装", "外套", "上衣", "衬衫", "毛衣", "卫衣", "开衫",
    "裤子", "裙", "连衣裙", "鞋", "靴", "袜", "围巾", "帽子", "眼镜", "配饰",
    "背包", "包包", "手提包", "挎包", "书包", "单肩包",
    "打扮", "换装", "换衣", "衣柜", "穿什么", "今天穿", "outfit", "wear",
)

# 单字触发词必须带边界：「包」在面包/红包/打包/邮包 里都不是衣服。
# 用负向后顾把它收紧成「前面不像别的词」的裸包，这样「我的包好看吗」仍然命中，
# 而「我想吃面包」「给你发个红包」「帮我打包文件」不再命中。
_WARDROBE_DETAIL_TRIGGER_PATTERNS = (
    re.compile(r"(?<![面红打邮书钱沙纸背钱])(?<![出行背书])包"),
)

# `好看吗` 单独出现太泛（电影/菜/天气都能这么问），必须与穿着语境同现才算问到衣服。
_WARDROBE_DETAIL_CONTEXT_TRIGGERS = (
    "好看吗", "好看不", "怎么样",
)
_WARDROBE_DETAIL_CONTEXT_WORDS = (
    "穿", "衣服", "衣", "裙", "裤", "鞋", "袜", "外套", "搭", "打扮", "造型",
)

# 区分“没有这个值”和“值是 None”，回滚时据此决定是否写回。
_MISSING = object()

# 识图可能较慢；衣柜是显式命令触发的交互，可以等得久一点。
_WARDROBE_VISION_TIMEOUT_SECONDS = 90.0
_WARDROBE_VISION_MAX_IMAGES = 8

# 推理型视觉模型会把 token 预算花在思考过程上，正文可能整个是空的
# （实测 10 张里 4 张如此，finish_reason=length）。空返回时用这句
# 「只要结论」的补语对同一个 Provider 再问一次，仍失败才换下一个候选。
_WARDROBE_VISION_TERSE_SUFFIX = "\n\n请直接输出上面的字段，不要输出思考过程或额外说明。"
# 草稿队列：面板要显示人话，标签映射收在后端一份，别让前端各写一套。
WARDROBE_DRAFT_KIND_LABELS: dict[str, str] = {
    WARDROBE_IMAGE_KIND_ITEM: "散件",
    WARDROBE_IMAGE_KIND_OUTFIT: "整套",
    WARDROBE_IMAGE_KIND_REFERENCE: "参考整套",
    WARDROBE_IMAGE_KIND_NONE: "无法辨认",
}

WARDROBE_ASSET_ORIGIN_LABELS: dict[str, str] = {
    ASSET_ORIGIN_LOCAL: "本地导入",
    ASSET_ORIGIN_PANEL: "面板上传",
    ASSET_ORIGIN_BLOGGER: "博主参考",
    ASSET_ORIGIN_TAOBAO: "淘宝",
    ASSET_ORIGIN_SHARE_TEXT: "分享文本",
    ASSET_ORIGIN_SCREENSHOT: "截图",
}

# 缩略图只对位图有意义；视频帧与分享文本在队列里只显示一行字。
_WARDROBE_DRAFT_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})

# 按需索取细节的只读工具（在 main.py 用 @filter.llm_tool 注册，这里负责数据与挂载）。
# 名字一旦改了，llm_tool_actions.py 的 known_names 也要跟着改 —— 那张表决定
# 「模型把工具调用当纯文本吐出来」时能不能被识别并从可见回复里剥掉。
WARDROBE_DETAIL_TOOL_NAME = "pc_query_wardrobe_detail"
# 写工具的名字也收在这里：摘除时要读写一起摘，否则同一个开关下两个工具行为不一致。
WARDROBE_INTENT_TOOL_NAME = "pc_set_outfit_intent"

# 本会话明确换装（作者的 dialogue_outfit_override）最多带上几件。
# 上限只是为了把段落长度钉死在 WARDROBE_PROMPT_MAX_CHARS 以内。
WARDROBE_OVERRIDE_MAX_ITEMS = 12

# 「今天穿什么」这条意图**复用作者已有的存储**，不新开 key：
# data["dialogue_outfit_override"] 已经接进 4 个消费点（连续性段落 / 日程调整 /
# scene_context / 状态衰减），core_store 也已登记为可持久化 section。
WARDROBE_INTENT_KEY = "dialogue_outfit_override"
# 过期规则跟作者保持一致：当日 + 12 小时，谁先到算谁。
WARDROBE_INTENT_TTL_SECONDS = 12 * 3600
# 写入来源标记，只用于面板展示（不参与优先级：按约定后写的覆盖先写的）。
WARDROBE_INTENT_SOURCE_MODEL = "model_tool"

# 工具回包的上限：它是「按需展开」，不是把整份衣柜倒给模型，所以比注入段落宽松、
# 但仍有硬上限，避免 40 件长描述把一次工具结果撑成几千字。
WARDROBE_DETAIL_MAX_ITEMS = 40
WARDROBE_DETAIL_MAX_CHARS = 2400
# 只读工具的 scope 取值（含模型常用的中文说法）。不在表里就明确回一句「不认识」，
# 而不是默默按 today 回答 —— 后者会让模型以为自己问的那一份拿到了。
WARDROBE_DETAIL_SCOPES = frozenset(
    {"today", "今天", "今日", "slot", "部位", "all", "全部", "所有", "清单", "inventory"}
)

# 注入段落里那句「可以调用工具」的提示。只在工具真的挂上了这次请求时才拼进去，
# 否则等于让模型调用一个不存在的工具（不支持 function calling 的模型尤其明显）。
WARDROBE_DETAIL_TOOL_HINT = (
    "需要更多细节时可以调用 pc_query_wardrobe_detail（某部位都有什么、今天这身每件是什么）；"
    "不要凭空编造衣柜里没有的衣物。"
)



class _wardrobe_runtimeHostRef:
    """延迟引用宿主 wardrobe_runtime 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import wardrobe_runtime as _host_module

        return getattr(_host_module, name)


_wardrobe_runtime_host = _wardrobe_runtimeHostRef()
