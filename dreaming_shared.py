# -*- coding: utf-8 -*-
"""dreaming 域的跨模块共享件（import 绑定 + 模块级常量）。

由 tmp/split4/mod_split.py 从 dreaming.py 机械抽取：
9 条 import 语句 + 4 个模块级常量，逐字节原样。
宿主 dreaming.py 与各 dreaming_partNN.py 均从本模块 import，
本模块不 import 任何同族模块（叶子模块，杜绝循环 import）。
"""

from __future__ import annotations

import random

import re

from difflib import SequenceMatcher

from datetime import datetime

from typing import Any

from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key

from .persona_config import runtime_persona_setting

from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    prompt_section,
    render_prompt_sections,
)

_ABSTRACT_DREAM_FRAGMENT_MARKERS = (
    "状态", "情绪", "心情", "感觉", "余韵", "碎片", "生活感", "日程", "计划", "总结",
    "今天", "明天", "用户", "主动", "消息", "回复", "关系", "陪伴", "模型", "生成",
)

_DIARY_STATUS_BROADCAST_MARKERS = (
    "今天偏", "当前天气", "状态确认", "今天状态", "能量", "适合推进",
    "平稳推进", "没有什么特别重的话想说", "醒来后慢慢把自己拢回",
    "梦里的雾还没散", "等晚一点遇到合适的小事再讲", "今日状态",
)

_DIARY_CONCRETE_ACTION_MARKERS = (
    "放", "拿", "翻", "写", "擦", "收", "整理", "拉开", "关上", "停", "等",
    "看", "听", "闻", "走", "坐", "喝", "热", "晾", "找", "碰", "摸", "回",
)

_DIARY_DUPLICATE_KEYWORDS = (
    "梦", "梦里", "梦见", "学校", "教室", "窗台", "窗边", "窗", "猫", "橘猫", "星图",
    "发夹", "书包", "餐桌", "糖", "软糖", "花", "雨", "伞", "走廊", "床", "枕头",
)
