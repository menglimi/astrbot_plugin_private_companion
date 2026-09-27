# -*- coding: utf-8 -*-
"""宿主私有辅助设施（从 main.py 提升而来，供宿主与各域 mixin 共用）。

只放 **被多个模块共同依赖** 的模块级辅助件：单人设事件上下文装饰器及其
实例判定依赖。放在独立模块可避免 mixin 反向 import main.py 造成循环导入，
同时保持「一份实现」的单一所有者。

`_private_companion_runtime` 是通过 `sys.modules` 键共享的模块对象，
此处按同一键取用，拿到的是同一个对象，不产生第二份状态。
"""
from __future__ import annotations

import functools
import inspect
import sys
import threading
from types import ModuleType
from typing import Any
import contextvars
import base64
from pathlib import Path

try:
    from astrbot.api.message_components import BaseMessageComponent, ComponentType, Plain
except ImportError:
    from astrbot.core.message.components import BaseMessageComponent, ComponentType, Plain
from .helpers import _strip_internal_message_blocks
from .persona_config import load_scope_manifest, runtime_persona_setting
from .segmented_message import LLM_SEGMENT_MARKER

_PRIVATE_COMPANION_RUNTIME_KEY = "ASTROBOT_PRIVATE_COMPANION_RUNTIME"


def _new_private_companion_runtime() -> ModuleType:
    runtime = ModuleType(_PRIVATE_COMPANION_RUNTIME_KEY)
    runtime.lock = threading.RLock()
    runtime.active_plugin = None
    return runtime


_private_companion_runtime = sys.modules.setdefault(
    _PRIVATE_COMPANION_RUNTIME_KEY,
    _new_private_companion_runtime(),
)


def _plugin_instance_root(instance: Any) -> str:
    """Return the data/plugins directory name that imported an instance."""
    module_name = str(getattr(type(instance), "__module__", "") or "")
    parts = module_name.split(".")
    if len(parts) >= 3 and parts[:2] == ["data", "plugins"]:
        return parts[2]
    return ""


def _plugin_instance_can_dispatch(instance: Any) -> bool:
    if bool(getattr(instance, "_private_companion_duplicate_instance", False)):
        return False
    if not bool(getattr(instance, "_private_companion_instance_guard_enabled", False)):
        return True
    with _private_companion_runtime.lock:
        return (
            _private_companion_runtime.active_plugin is None
            or _private_companion_runtime.active_plugin is instance
        )


def _multi_persona_event_context(function):
    """Bind one event task to one persona profile for the complete event lifetime."""
    if inspect.isasyncgenfunction(function):
        @functools.wraps(function)
        async def asyncgen_wrapper(self, event, *args, **kwargs):
            if not _plugin_instance_can_dispatch(self):
                return
            scope_checker = getattr(self, "_bot_scope_allows_event", None)
            if callable(scope_checker) and not scope_checker(event):
                return
            activator = getattr(self, "_activate_persona_for_event_context", None)
            if not callable(activator):
                activator = getattr(self, "_activate_persona_for_event", None)
            activation = activator(event) if callable(activator) else (None, "")
            if inspect.isawaitable(activation):
                activation = await activation
            token, _ = activation
            try:
                async for item in function(self, event, *args, **kwargs):
                    yield item
            finally:
                deactivator = getattr(self, "_deactivate_persona_for_event", None)
                if callable(deactivator):
                    deactivator(token)
        return asyncgen_wrapper

    @functools.wraps(function)
    async def async_wrapper(self, event, *args, **kwargs):
        if not _plugin_instance_can_dispatch(self):
            return None
        scope_checker = getattr(self, "_bot_scope_allows_event", None)
        if callable(scope_checker) and not scope_checker(event):
            return None
        activator = getattr(self, "_activate_persona_for_event_context", None)
        if not callable(activator):
            activator = getattr(self, "_activate_persona_for_event", None)
        activation = activator(event) if callable(activator) else (None, "")
        if inspect.isawaitable(activation):
            activation = await activation
        token, _ = activation
        try:
            return await function(self, event, *args, **kwargs)
        finally:
            deactivator = getattr(self, "_deactivate_persona_for_event", None)
            if callable(deactivator):
                deactivator(token)
    return async_wrapper

_PROACTIVE_ONLY_TEMP_UNLOCK_ALIASES = {

    "全部": "all",

    "all": "all",

    "被动": "all",

    "被动链路": "all",

    "状态": "inject_passive_states",

    "状态注入": "inject_passive_states",

    "被动状态": "inject_passive_states",

    "图片": "enable_private_image_self_recognition",

    "识图": "enable_private_image_self_recognition",

    "私聊图片": "enable_private_image_self_recognition",

    "合并消息": "enable_forward_message_adaptation",

    "转发": "enable_forward_message_adaptation",

    "转发消息": "enable_forward_message_adaptation",

    "防抖": "enable_message_debounce",

    "智能防抖": "enable_message_debounce",

    "撤回": "enable_recall_enhancement",

    "撤回增强": "enable_recall_enhancement",

    "tts": "enable_tts_enhancement",

    "TTS": "enable_tts_enhancement",

    "语音": "enable_tts_enhancement",

    "分段": "enable_segmented_proactive_reply",

    "回复分段": "enable_segmented_proactive_reply",

    "群聊": "enable_group_companion",

    "群聊观察": "enable_group_companion",

    "技能": "enable_skill_growth_passive_injection",

    "技能注入": "enable_skill_growth_passive_injection",

    "吃什么": "enable_food_menu_recommendation",

    "吃什么候选": "enable_food_menu_recommendation",

    "候选菜单": "enable_food_menu_recommendation",

    "饭点关心": "enable_meal_care_proactive",

    "吃饭关心": "enable_meal_care_proactive",

    "关系网": "enable_worldbook_member_recognition",

    "跨用户记忆": "enable_cross_user_memory_bridge",

    "跨用户记忆互通": "enable_cross_user_memory_bridge",

    "互动查询": "enable_cross_user_memory_bridge",

    "跨群转述": "enable_atrelay_tools",

    "转述工具": "enable_atrelay_tools",

    "livingmemory": "enable_livingmemory_integration",

    "lmem": "enable_livingmemory_integration",

    "记忆插件": "enable_livingmemory_integration",

    "记忆协同": "enable_livingmemory_integration",

}

_ACTIVE_PERSONA_ID = contextvars.ContextVar("private_companion_active_persona_id", default="")

_PERSONA_SETTING_MANIFEST = load_scope_manifest()

_PERSONA_PROFILE_FORBIDDEN_FILENAME_CHARS = frozenset('<>:"/\\|?*%')

_WINDOWS_RESERVED_FILENAME_STEMS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)

class _OneBotReactionImage(BaseMessageComponent):
    """OneBot image segment that preserves QQ's emoji-image subtype flag."""

    type: ComponentType = ComponentType.Image
    file: str
    path: str
    url: str = ""
    sub_type: int = 1
    payload_file: str
    _private_companion_reaction_expression = True

    def __init__(self, path: str) -> None:
        resolved = str(Path(path).resolve(strict=True))
        payload = base64.b64encode(Path(resolved).read_bytes()).decode("ascii")
        super().__init__(
            file=resolved,
            path=resolved,
            url="",
            sub_type=1,
            payload_file=f"base64://{payload}",
        )

    def toDict(self) -> dict[str, Any]:
        return {
            "type": "image",
            "data": {"file": self.payload_file, "sub_type": self.sub_type},
        }

    async def to_dict(self) -> dict[str, Any]:
        return self.toDict()

    def __repr__(self) -> str:
        return f"_OneBotReactionImage(path={self.path!r}, sub_type={self.sub_type})"
_PROACTIVE_ONLY_TEMP_UNLOCK_GROUPS = {
    "private_event_pipeline": {
        "enable_message_debounce",
        "enable_private_image_self_recognition",
        "enable_forward_message_adaptation",
    },
    "group_event_pipeline": {
        "enable_group_companion",
        "enable_message_debounce",
        "enable_forward_message_adaptation",
    },
    "llm_request": {
        "inject_passive_states",
        "enable_intent_emotion_analysis",
        "enable_llm_timer_scheduling",
        "enable_passive_topic_suppression",
        "enable_environment_perception",
        "enable_tts_enhancement",
        "enable_private_image_self_recognition",
        "enable_forward_message_adaptation",
        "enable_group_companion",
        "enable_skill_growth_passive_injection",
        "enable_food_menu_recommendation",
        "enable_worldbook_member_recognition",
        "enable_cross_user_memory_bridge",
        "enable_livingmemory_integration",
    },
    "pc_tools": {
        "enable_atrelay_tools",
        "enable_worldbook_member_recognition",
        "enable_cross_user_memory_bridge",
        "enable_qzone_integration",
    },
}
_PROACTIVE_ONLY_TEMP_UNLOCK_LABELS = {
    "all": "全部被动链路",
    "inject_passive_states": "被动状态注入",
    "enable_intent_emotion_analysis": "意图/情绪分析",
    "enable_llm_timer_scheduling": "预约类主动捕获",
    "enable_passive_topic_suppression": "重复话题抑制",
    "enable_environment_perception": "环境感知",
    "enable_message_debounce": "防抖",
    "enable_recall_enhancement": "撤回增强",
    "enable_private_image_self_recognition": "私聊图片识别",
    "enable_forward_message_adaptation": "合并/转发消息阅读",
    "enable_group_companion": "群聊观察",
    "enable_skill_growth_passive_injection": "技能被动注入",
    "enable_food_menu_recommendation": "吃什么候选",
    "enable_meal_care_proactive": "饭点主动关心",
    "enable_worldbook_member_recognition": "关系网成员识别",
    "enable_cross_user_memory_bridge": "跨用户记忆互通",
    "enable_atrelay_tools": "跨群转述工具",
    "enable_livingmemory_integration": "记忆插件被动引导",
    "enable_tts_enhancement": "TTS 后处理",
    "enable_segmented_proactive_reply": "普通 LLM 分段",
}

_PROACTIVE_ONLY_TEMP_UNLOCK_RELATED = {
    "enable_atrelay_tools": ["enable_worldbook_member_recognition"],
    "enable_cross_user_memory_bridge": ["enable_worldbook_member_recognition"],
    "enable_group_companion": ["enable_worldbook_member_recognition"],
    "enable_forward_message_adaptation": ["enable_private_image_self_recognition"],
}


def _strip_chain_plain_thinking(owner: Any, chain: list[Any]) -> None:
    """Clean registered internal tags from all Plain components as one span."""
    if not bool(runtime_persona_setting(owner, "enable_framework_error_leak_guard", True)):
        return
    plain_components = [(i, comp) for i, comp in enumerate(chain) if isinstance(comp, Plain)]
    if not plain_components:
        return
    all_text = "".join(str(getattr(comp, "text", "") or "") for _, comp in plain_components)
    split_marker_token = "\x00PRIVATE_COMPANION_SPLIT\x00"
    all_text = all_text.replace(LLM_SEGMENT_MARKER, split_marker_token)
    cleaned = _strip_internal_message_blocks(
        all_text,
        tts_enabled=bool(runtime_persona_setting(owner, "enable_tts_enhancement", False)),
    )
    cleaned = cleaned.replace(split_marker_token, LLM_SEGMENT_MARKER)
    if not all_text.startswith("\n"):
        cleaned = cleaned.lstrip("\n")
    if cleaned == all_text:
        return
    for idx, (_, comp) in enumerate(plain_components):
        try:
            comp.text = cleaned if idx == 0 else ""
        except Exception:
            pass
_PHOTO_TOOL_PROMPT_FORMAT_MARKER = "<!-- private_companion_prompt_format_req_v1 -->"

# --- 由 tools/split_main_domain_v2.py --auto-promote 提升 (陪伴指令域) ---
bookshelf_password_reset_actions = {
    "重置夹层密码", "重设夹层密码", "重新生成夹层密码", "刷新夹层密码", "生成夹层密码",
    "重置资料柜密码", "重设资料柜密码", "重新生成资料柜密码", "刷新资料柜密码", "生成资料柜密码",
}
companion_manual_query_actions = {"答疑", "排障", "诊断", "说明"}
daily_outfit_generate_actions = {
    "生成穿搭", "刷新穿搭", "重置穿搭",
    "生成穿搭图", "刷新穿搭图", "重置穿搭图",
    "重新生成穿搭", "重新生成穿搭图", "重生穿搭", "重生穿搭图",
    "生成今日穿搭", "生成今日穿搭图", "生成每日穿搭", "生成每日穿搭图",
}
daily_schedule_cancel_actions = {"删除日程", "取消日程", "移除日程"}
daily_schedule_regenerate_actions = {"重置日程", "生成日程", "刷新日程", "重新生成日程"}
image_api_swap_actions = {
    "切换生图API", "切换生图api", "交换生图API", "交换生图api",
    "切换在线生图API", "切换在线生图api", "交换在线生图API", "交换在线生图api",
    "切换图片API", "切换图片api", "交换图片API", "交换图片api",
    "切换备用生图", "启用备用生图", "使用备用生图", "切到备用生图",
    "切换备选生图", "启用备选生图", "使用备选生图", "切到备选生图",
}
photo_command_actions = {"生图", "画图", "绘图", "生成图片", "出图", "自拍", "拍照", "拍一张", "改图", "修图", "重绘", "P图", "p图"}
qweather_location_bind_actions = {"绑定城市", "设置城市"}
qweather_location_view_actions = {"查看城市", "当前城市", "天气城市"}
qweather_location_unbind_actions = {"解绑城市", "清除城市"}
qweather_location_actions = {
    *qweather_location_bind_actions,
    *qweather_location_view_actions,
    *qweather_location_unbind_actions,
}
wakeup_alarm_actions = {"现实触及", "现实触及闹钟", "现实触及起床", "起床闹钟", "起床提醒", "蓝牙起床", "蓝牙闹钟"}
