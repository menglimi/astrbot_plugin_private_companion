"""Public catalog for V2 native extensions and external providers.

The seven capability modules are shipped by the single V2 plugin. They are
extensions of that plugin, not AstrBot plugins discovered through Star.
"""

from typing import Any


EXTERNAL_PROVIDERS: tuple[dict[str, Any], ...] = (
    {
        "plugin_id": "astrbot_plugin_private_companion",
        "capability_id": "private_core_legacy",
        "label": "旧版陪伴核心",
        "description": "仅显示旧版插件的运行状态，旧版资料与执行仍由原插件管理。",
        "furniture": {"id": "legacy-core", "name": "陪伴核心台", "panel_id": "legacy-core"},
    },
    # 目录是外部提供方的唯一发现入口：未登记的插件永远不会被读取状态，
    # 所以视频理解即使由第三方插件执行，也要在这里留一条可见性记录。
    {
        "plugin_id": "astrbot_plugin_video_vision",
        "capability_id": "video_vision",
        "label": "基米看得见视频",
        "description": "普通聊天的原生视频理解由该插件提供（含引用视频与体积压缩）。",
        "furniture": {"id": "video-vision", "name": "观影机", "panel_id": "video-vision"},
    },
)

BUNDLED_EXTENSIONS: tuple[dict[str, Any], ...] = (
    {
        "extension_id": "content", "capability_id": "content", "label": "创作与 QQ 空间",
        "description": "V2 本体内置的创作项目、作品版本、封面与 QQ 空间联动拓展。",
        "furniture": {"id": "content", "name": "创作书架", "panel_id": "content"},
    },
    {
        "extension_id": "image", "capability_id": "image", "label": "生图与相册",
        "description": "V2 本体内置的生成、编辑、参考图与原件保留拓展。",
        "furniture": {"id": "image", "name": "画面工作室", "panel_id": "image"},
    },
    {
        "extension_id": "reality", "capability_id": "reality", "label": "现实触及",
        "description": "V2 本体内置的摄像头、米家、移动端和现实设备拓展。",
        "furniture": {"id": "reality", "name": "现实设备台", "panel_id": "reality"},
    },
    {
        "extension_id": "companion.screen.native", "capability_id": "screen", "label": "屏幕陪伴",
        "description": "V2 本体内置的按需屏幕采集、识别和观察记录拓展。",
        "furniture": {"id": "screen", "name": "屏幕台", "panel_id": "screen"},
    },
    {
        "extension_id": "together", "capability_id": "together", "label": "共处与实时活动",
        "description": "V2 本体内置的共读、观影、通话和实时房间拓展。",
        "furniture": {"id": "together", "name": "共处电话", "panel_id": "together"},
    },
    {
        "extension_id": "game", "capability_id": "game", "label": "共同游玩",
        "description": "V2 本体内置的棋类、派对和多人游戏房间拓展。",
        "furniture": {"id": "game", "name": "游戏棋桌", "panel_id": "game"},
    },
    {
        "extension_id": "live", "capability_id": "live", "label": "直播与表演",
        "description": "V2 本体内置的直播事件、Live2D、SoulLink 和角色呈现拓展。",
        "furniture": {"id": "live", "name": "直播与表演", "panel_id": "live"},
    },
)

# Compatibility import for older callers. Runtime discovery uses the explicit
# external catalog above and never treats this alias as a bundled catalog.
OPTIONAL_PROVIDERS: tuple[dict[str, Any], ...] = EXTERNAL_PROVIDERS
