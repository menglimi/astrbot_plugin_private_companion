"""Product identity and inventory of implemented V2 domains."""

from typing import Any

PRODUCT_ID = "private_companion.v2"
PRODUCT_DISPLAY_NAME = "我会永远陪着你"
PRODUCT_VERSION = "7.0.12"
PROTOCOL_VERSION = "companion.public-extension.v1"


CORE_COMPONENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "conversation",
        "label": "对话与会话宿主",
        "description": "私聊、群聊、多 Bot／多人格路由、消息合并和安全投递。",
        "routes": ("conversation.foreground_reply", "conversation.group_participation"),
    },
    {
        "id": "identity_relationships",
        "label": "身份与关系",
        "description": "安装、角色、对象权限、关系背景和可审阅的关系记忆。",
        "routes": ("identity.resolve", "relationships.manage"),
    },
    {
        "id": "memory",
        "label": "事实与经历",
        "description": "有来源的事实、经历、检索、反思、修订和遗忘边界。",
        "routes": ("memory.read", "memory.propose", "memory.reflect"),
    },
    {
        "id": "life_journal",
        "label": "生活、日记与成长",
        "description": "日程、阅读、睡眠、日记、梦境、成长和角色时间线。",
        "routes": ("life.calendar", "role.journal", "role.state", "role.timeline"),
    },
    {
        "id": "media_sources",
        "label": "媒体、引用与来源",
        "description": "图片、音频、视频、引用、合并转发和来源证据。",
        "routes": ("conversation.media", "conversation.quote", "conversation.forward"),
    },
    {
        "id": "proactive_tasks",
        "label": "主动联系与后台任务",
        "description": "主动联系偏好、日程触发、任务监督、额度和恢复。",
        "routes": ("proactive.contacts", "tasks.supervisor", "agenda.background"),
    },
    {
        "id": "capability_room",
        "label": "能力管理与陪伴小屋",
        "description": "能力注册、授权、配置投影和体素小屋家具入口。",
        "routes": ("capability.inventory", "capability.configuration", "management.dashboard"),
    },
    {
        "id": "diagnostics",
        "label": "诊断与可恢复维护",
        "description": "运行证据、诊断建议、修复确认和停机恢复。",
        "routes": ("diagnostics.inspect", "management.repair_proposal"),
    },
)
