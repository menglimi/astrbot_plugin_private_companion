"""Static V1 source inventory for the direct-replacement release gate.

The catalog describes supported sources. It intentionally does not locate, open, or
transform any source: locating a V1 installation is a separate concern, and the actual
transformations live beside the domain they convert.

**Handler status (2026-10-09).** Configuration conversion remains separate from data
import. Role journals, group interaction aggregates, worldbook entries and both
reaction-asset catalogs have data importers. Work/delivery and world assets have
documented skip dispositions because their source scopes, external receipts, or asset
ownership cannot be transferred faithfully.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


V1_PLUGIN_ID = "astrbot_plugin_private_companion"
V1_SUPPORTED_VERSION = "6.7.0"
V1_SUPPORTED_VERSIONS = ("6.6.2", V1_SUPPORTED_VERSION, "6.7.2", "6.7.3", "6.7.4")


class CatalogAction(str, Enum):
    """The allowed post-migration disposition for one declared legacy source."""

    CONVERTED = "converted"
    DATA_CONVERTED = "data_converted"
    NO_HANDLER = "no_handler"
    COMPAT_READABLE = "compat_readable"
    RETAINED_EXTERNAL = "retained_external"
    HELD = "held"
    UNSUPPORTED = "unsupported"
    DOCUMENTED_SKIP = "documented_skip"


@dataclass(frozen=True)
class LegacyCatalogEntry:
    """One versioned legacy source and its required V2 handling contract."""

    source_id: str
    source_plugin_id: str
    source_version: str
    owner: str
    source_paths: tuple[str, ...]
    format_signature: str
    action: CatalogAction
    handler_id: str
    acceptance_scope: tuple[str, ...]
    disposition_reason: str = ""


@dataclass(frozen=True)
class CapabilityDispositionEntry:
    """一条能力级处置：**不是**数据归属，而是「这项能力谁对外表达」。

    记忆库会以一致性快照归档，但对话中的活跃记忆仍归原提供方。
    这两件事必须分开表达：快照保留历史与治理数据，不会生成第二份活跃记忆。

    所以数据级事实留在 :data:`LEGACY_COMPATIBILITY_CATALOG`（并留在迁移发现里，
    用户可以逐条看见），能力级事实放在 :data:`CAPABILITY_DISPOSITIONS`。迁移的
    发现/试算只读前者，不会因为这条记录多出一份「数据在哪」的报告。
    """

    capability_id: str
    provider_plugin_id: str
    provider_display_name: str
    owner: str
    disposition: CatalogAction
    declaration: str
    reason: str


# 能力级处置表。目前只有记忆一条：本插件认「我会牢牢记住你」为记忆能力的提供方，
# 表达式（注入与工具）都让它，V2 自己退回去继续记录事实。声明路径写明它公开的
# 取句柄函数——V2 读的就是这个声明，不是文件是否存在。
CAPABILITY_DISPOSITIONS = (
    CapabilityDispositionEntry(
        capability_id="memory",
        provider_plugin_id="astrbot_plugin_memory_companion",
        provider_display_name="我会牢牢记住你",
        owner="memory_companion.conversation_and_governance",
        disposition=CatalogAction.RETAINED_EXTERNAL,
        declaration="main.py:get_active_bridge / main.py:get_memory_companion_bridge",
        reason=(
            "记忆的对外表达（每轮注入与模型可用的记忆工具）归它，V2 让位以免出现两套记忆叙事，"
            "也避免模型把新记忆分别写进两个库；句柄消失、声明停用或读不到时 V2 立刻恢复自己的"
            "注入与工具，所以这是可逆的让位，不是把能力让出去。"
        ),
    ),
)


@dataclass(frozen=True)
class LegacyCompatibilityCatalog:
    """Immutable, reviewable inventory used to scope future automatic migration."""

    schema_version: str
    target_plugin_id: str
    supported_v1_versions: tuple[str, ...]
    entries: tuple[LegacyCatalogEntry, ...]

    def entry(self, source_id: str) -> LegacyCatalogEntry:
        for candidate in self.entries:
            if candidate.source_id == source_id:
                return candidate
        raise KeyError(source_id)

    def entries_for_plugin(self, plugin_id: str) -> tuple[LegacyCatalogEntry, ...]:
        return tuple(item for item in self.entries if item.source_plugin_id == plugin_id)

    def validate(self) -> tuple[str, ...]:
        """Return structural defects without opening any legacy source."""

        errors: list[str] = []
        if not self.schema_version:
            errors.append("schema_version_missing")
        if self.target_plugin_id != V1_PLUGIN_ID:
            errors.append("target_plugin_identity_mismatch")
        if not self.supported_v1_versions:
            errors.append("supported_v1_versions_missing")

        seen_source_ids: set[str] = set()
        for item in self.entries:
            if not item.source_id or item.source_id in seen_source_ids:
                errors.append(f"source_id_invalid:{item.source_id}")
            seen_source_ids.add(item.source_id)
            if not item.source_plugin_id:
                errors.append(f"source_plugin_missing:{item.source_id}")
            if not item.source_version:
                errors.append(f"source_version_missing:{item.source_id}")
            if not item.owner:
                errors.append(f"owner_missing:{item.source_id}")
            if not item.source_paths:
                errors.append(f"source_paths_missing:{item.source_id}")
            if not item.format_signature:
                errors.append(f"format_signature_missing:{item.source_id}")
            if not isinstance(item.action, CatalogAction):
                errors.append(f"action_invalid:{item.source_id}")
            if not item.handler_id:
                errors.append(f"handler_missing:{item.source_id}")
            if not item.acceptance_scope:
                errors.append(f"acceptance_scope_missing:{item.source_id}")
            if item.action is CatalogAction.DOCUMENTED_SKIP and not item.disposition_reason:
                errors.append(f"documented_skip_reason_missing:{item.source_id}")
        return tuple(errors)


LEGACY_COMPATIBILITY_CATALOG = LegacyCompatibilityCatalog(
    schema_version="companion-v2.legacy-compatibility-catalog.v0",
    target_plugin_id=V1_PLUGIN_ID,
    supported_v1_versions=V1_SUPPORTED_VERSIONS,
    entries=(
        LegacyCatalogEntry(
            source_id="private.host_configuration",
            source_plugin_id=V1_PLUGIN_ID,
            source_version=V1_SUPPORTED_VERSION,
            owner="private_companion.configuration",
            source_paths=("host-config:astrbot_plugin_private_companion",),
            format_signature="astrbot-plugin-config.v1",
            action=CatalogAction.CONVERTED,
            handler_id="v2.private-config-converter",
            acceptance_scope=("RG-03", "RG-04", "RG-05"),
        ),
        LegacyCatalogEntry(
            source_id="private.core_profiles",
            source_plugin_id=V1_PLUGIN_ID,
            source_version=V1_SUPPORTED_VERSION,
            owner="private_companion.role_journal_and_person_profiles",
            source_paths=("companions.json", "persona_profiles"),
            format_signature="private-core.json-or-configured-sqlite.v1",
            action=CatalogAction.DATA_CONVERTED,
            handler_id="v2.role-journal-and-worldbook-importer",
            acceptance_scope=("RG-03", "RG-05", "RG-06", "RG-09"),
        ),
        LegacyCatalogEntry(
            source_id="private.authoritative_memory",
            source_plugin_id=V1_PLUGIN_ID,
            source_version=V1_SUPPORTED_VERSION,
            owner="private_companion.req041_private_memory",
            source_paths=("companions.json",),
            format_signature="private-core-authoritative-memory.req041.v1",
            action=CatalogAction.DATA_CONVERTED,
            handler_id="v2.person-private-memory-importer",
            acceptance_scope=("RG-03", "RG-05", "RG-06", "RG-09"),
            disposition_reason=(
                "V1 的 _req041_private_memory 经原身份登记、内容哈希及当前账号/用户核对后，"
                "由操作者确认写入所选 V2 角色的历史查询库；不恢复旧任务、实时状态或发送行为。"
            ),
        ),
        LegacyCatalogEntry(
            source_id="private.group_relationship_graph",
            source_plugin_id=V1_PLUGIN_ID,
            source_version=V1_SUPPORTED_VERSION,
            owner="private_companion.group_observation.relationship_graph",
            source_paths=("companions.json",),
            format_signature="private-core-groups-relationship-edges.json.v1",
            action=CatalogAction.DATA_CONVERTED,
            handler_id="v2.group-relationship-importer",
            acceptance_scope=("RG-03", "RG-05", "RG-06", "RG-09"),
        ),
        LegacyCatalogEntry(
            source_id="private.reaction_assets",
            source_plugin_id=V1_PLUGIN_ID,
            source_version=V1_SUPPORTED_VERSION,
            owner="private_companion.reaction_expression_library",
            source_paths=("reaction_expression_library/catalog.json",
                          "reaction_expression_library/images"),
            format_signature="reaction-expression-library.catalog.v2",
            action=CatalogAction.DATA_CONVERTED,
            handler_id="v2.reaction-asset-importer",
            acceptance_scope=("RG-03", "RG-05", "RG-08"),
        ),
        LegacyCatalogEntry(
            source_id="private.owned_reaction_assets",
            source_plugin_id=V1_PLUGIN_ID,
            source_version=V1_SUPPORTED_VERSION,
            owner="private_companion.owned_reaction_expression_assets",
            source_paths=("owned_reaction_assets", "photo_action_config.owned_reaction_assets"),
            format_signature="owned-reaction-assets.hash-locked-list.v1",
            action=CatalogAction.DATA_CONVERTED,
            handler_id="v2.reaction-asset-importer",
            acceptance_scope=("RG-03", "RG-05", "RG-08"),
        ),
        LegacyCatalogEntry(
            source_id="memory.configuration",
            source_plugin_id="astrbot_plugin_memory_companion",
            source_version=V1_SUPPORTED_VERSION,
            owner="memory_companion.settings",
            source_paths=("host-config:astrbot_plugin_memory_companion",),
            format_signature="memory-companion-config.json.v1",
            action=CatalogAction.CONVERTED,
            handler_id="v2.memory-config-migrator",
            acceptance_scope=("RG-03", "RG-05", "RG-06", "RG-12"),
            disposition_reason=(
                "相关记忆检索的开关和数量、字符预算迁入原生记忆配置；旧摘要、采集、模型、"
                "跨窗口桥接及权限设置逐项说明，不替换为含义不同的新开关。"
            ),
        ),
        LegacyCatalogEntry(
            source_id="memory.timeline_and_governance",
            source_plugin_id="astrbot_plugin_memory_companion",
            source_version=V1_SUPPORTED_VERSION,
            owner="memory_companion.conversation_and_governance",
            source_paths=("plugin-data:astrbot_plugin_memory_companion/memory_companion.db",),
            format_signature="memory-companion.sqlite.v1",
            action=CatalogAction.DATA_CONVERTED,
            handler_id="v2.memory-snapshot-archive",
            acceptance_scope=("RG-03", "RG-05", "RG-06", "RG-09"),
            disposition_reason=(
                "完整记忆库按 SQLite backup API 保存到 V2 隔离归档，包含 WAL 中已提交事务；"
                "时间线与治理表保留原结构，不混入 V2 活跃记忆。"
            ),
        ),
        # 快照归档保留历史与治理记录；活跃记忆提供方由能力处置单独声明。
        LegacyCatalogEntry(
            source_id="private.work_and_delivery",
            source_plugin_id=V1_PLUGIN_ID,
            source_version=V1_SUPPORTED_VERSION,
            owner="private_companion.work_and_delivery",
            source_paths=("framework_work/work.db", "framework_work/delivery.db"),
            format_signature="private-work-delivery.sqlite.v1",
            action=CatalogAction.DOCUMENTED_SKIP,
            handler_id="v2.work-delivery-converter",
            acceptance_scope=("RG-05", "RG-07", "RG-08", "RG-09"),
            disposition_reason=(
                "旧任务可能处于待执行或结果未知状态，投递库还包含旧正文和外部发送回执；"
                "无法复核 V2 当前身份、授权与真实发送结果，因此只统计状态，不复制或重放。"
            ),
        ),
        LegacyCatalogEntry(
            source_id="private.world_assets",
            source_plugin_id=V1_PLUGIN_ID,
            source_version=V1_SUPPORTED_VERSION,
            owner="private_companion.world_and_assets",
            source_paths=("framework_world/album.db", "framework_world/resources"),
            format_signature="private-world-album-and-files.v1",
            action=CatalogAction.DOCUMENTED_SKIP,
            handler_id="v2.world-asset-converter",
            acceptance_scope=("RG-04", "RG-05", "RG-08"),
            disposition_reason=(
                "旧相册由 Image Companion 持有，且外部图片引用没有 V2 的来源、所有权与受众凭据；"
                "仅统计本地目录，不复制资源或重建外部引用。"
            ),
        ),
        LegacyCatalogEntry(
            source_id="content.creative_projects",
            source_plugin_id="astrbot_plugin_content_companion",
            source_version=V1_SUPPORTED_VERSION,
            owner="content_companion.creative_projects",
            source_paths=("creative_state.json",),
            format_signature="content-creative-state.json.v1",
            action=CatalogAction.RETAINED_EXTERNAL,
            handler_id="v2.content-owner-adapter",
            acceptance_scope=("RG-03", "RG-05", "RG-12"),
        ),
        LegacyCatalogEntry(
            source_id="image.generation_state",
            source_plugin_id="astrbot_plugin_image_companion",
            source_version=V1_SUPPORTED_VERSION,
            owner="image_companion.generation_and_assets",
            source_paths=("image_generation_state.json",),
            format_signature="image-generation-state.json.v1",
            action=CatalogAction.RETAINED_EXTERNAL,
            handler_id="v2.image-owner-adapter",
            acceptance_scope=("RG-03", "RG-05", "RG-08", "RG-12"),
        ),
        LegacyCatalogEntry(
            source_id="image.configuration",
            source_plugin_id="astrbot_plugin_image_companion",
            source_version=V1_SUPPORTED_VERSION,
            owner="image_companion.generation_and_assets",
            source_paths=("host-config:astrbot_plugin_image_companion",),
            format_signature="image-companion-config.json.v1",
            action=CatalogAction.CONVERTED,
            handler_id="v2.image-config-migrator",
            acceptance_scope=("RG-03", "RG-05", "RG-08", "RG-12"),
        ),
        LegacyCatalogEntry(
            source_id="game.configuration",
            source_plugin_id="astrbot_plugin_game_companion",
            source_version=V1_SUPPORTED_VERSION,
            owner="game_companion.settings",
            source_paths=("host-config:astrbot_plugin_game_companion",),
            format_signature="game-companion-config.json.v1",
            action=CatalogAction.CONVERTED,
            handler_id="v2.native-extension-config-migrator",
            acceptance_scope=("RG-03", "RG-05", "RG-07", "RG-12"),
        ),
        LegacyCatalogEntry(
            source_id="together.configuration",
            source_plugin_id="astrbot_plugin_together_companion",
            source_version=V1_SUPPORTED_VERSION,
            owner="together_companion.settings",
            source_paths=("host-config:astrbot_plugin_together_companion",),
            format_signature="together-companion-config.json.v1",
            action=CatalogAction.CONVERTED,
            handler_id="v2.native-extension-config-migrator",
            acceptance_scope=("RG-03", "RG-05", "RG-07", "RG-12"),
        ),
        LegacyCatalogEntry(
            source_id="live.configuration",
            source_plugin_id="astrbot_plugin_live_stream_companion",
            source_version=V1_SUPPORTED_VERSION,
            owner="live_stream_companion.settings",
            source_paths=("host-config:astrbot_plugin_live_stream_companion",),
            format_signature="live-stream-companion-config.json.v1",
            action=CatalogAction.CONVERTED,
            handler_id="v2.native-extension-config-migrator",
            acceptance_scope=("RG-03", "RG-05", "RG-07", "RG-12"),
        ),
        LegacyCatalogEntry(
            source_id="content.configuration",
            source_plugin_id="astrbot_plugin_content_companion",
            source_version=V1_SUPPORTED_VERSION,
            owner="content_companion.settings",
            source_paths=("host-config:astrbot_plugin_content_companion",),
            format_signature="content-companion-config.json.v1",
            action=CatalogAction.CONVERTED,
            handler_id="v2.content-config-migrator",
            acceptance_scope=("RG-03", "RG-05", "RG-12"),
            disposition_reason=(
                "创作开关、主创作模型、方向提示词和封面偏好迁入原生创作配置；空间发布、账号凭据与"
                "没有同义入口的多阶段模型保留旧配置并逐项报告。"
            ),
        ),
        LegacyCatalogEntry(
            source_id="screen.configuration",
            source_plugin_id="astrbot_plugin_screen_companion",
            source_version=V1_SUPPORTED_VERSION,
            owner="screen_companion.private_device_settings",
            source_paths=("host-config:astrbot_plugin_screen_companion",),
            format_signature="screen-companion-config.json.v1",
            action=CatalogAction.DOCUMENTED_SKIP,
            handler_id="documented-skip.screen-private-settings",
            acceptance_scope=("RG-03", "RG-05", "RG-07", "RG-12"),
            disposition_reason=(
                "本机屏幕、麦克风和使用记录涉及管理员隐私；新版需要按当前设备与授权重新开启，"
                "不沿用旧版采集开关、目标会话、令牌或预设。原配置保留供人工核对。"
            ),
        ),
        LegacyCatalogEntry(
            source_id="reality.configuration",
            source_plugin_id="astrbot_plugin_reality_companion",
            source_version=V1_SUPPORTED_VERSION,
            owner="reality_companion.configuration",
            source_paths=("host-config:astrbot_plugin_reality_companion",),
            format_signature="reality-companion-config.json.v1",
            action=CatalogAction.CONVERTED,
            handler_id="v2.reality-config-migrator",
            acceptance_scope=("RG-03", "RG-05", "RG-06", "RG-07", "RG-12"),
        ),
        LegacyCatalogEntry(
            source_id="reality.device_and_observations",
            source_plugin_id="astrbot_plugin_reality_companion",
            source_version=V1_SUPPORTED_VERSION,
            owner="reality_companion.device_and_observation",
            source_paths=("plugin-data:astrbot_plugin_reality_companion/reality_companion.json",),
            format_signature="reality-companion-state.json.v1",
            action=CatalogAction.DOCUMENTED_SKIP,
            handler_id="documented-skip.reality-private-state",
            acceptance_scope=("RG-03", "RG-05", "RG-06", "RG-07", "RG-12"),
            disposition_reason=(
                "V1 的用户授权属于旧运行上下文；闹钟、观察记录、播放历史与设备路由不复制，"
                "以免形成过期授权、重复提醒或指向错误设备。V1 文件保持只读。"
            ),
        ),
        LegacyCatalogEntry(
            source_id="extensions.session_and_media",
            source_plugin_id=V1_PLUGIN_ID,
            source_version=V1_SUPPORTED_VERSION,
            owner="extension_session_and_media",
            source_paths=("together", "live", "game", "extension-owned-media"),
            format_signature="extension-specific-state.v1",
            action=CatalogAction.HELD,
            handler_id="v2.extension-owner-inventory",
            acceptance_scope=("RG-03", "RG-05", "RG-07", "RG-12"),
        ),
    ),
)


def validate_capability_dispositions(entries=CAPABILITY_DISPOSITIONS) -> tuple[str, ...]:
    """Return structural defects in the capability-level dispositions.

    和 :meth:`LegacyCompatibilityCatalog.validate` 一样：只查结构，不打开任何来源。
    两条纪律各查一次——能力 id 不能重复（一项能力只能有一个对外表达者），让位
    必须真的是 ``RETAINED_EXTERNAL``（写成 ``COMPAT_READABLE`` 就等于说两边都
    可以对外表达，那正是这次要消掉的重复）。
    """

    errors: list[str] = []
    seen: set[str] = set()
    for item in entries:
        if not item.capability_id or item.capability_id in seen:
            errors.append(f"capability_id_invalid:{item.capability_id}")
        seen.add(item.capability_id)
        if not item.provider_plugin_id:
            errors.append(f"provider_plugin_missing:{item.capability_id}")
        if not item.provider_display_name:
            errors.append(f"provider_display_name_missing:{item.capability_id}")
        if not item.owner:
            errors.append(f"owner_missing:{item.capability_id}")
        if not item.declaration:
            errors.append(f"declaration_missing:{item.capability_id}")
        if not item.reason:
            errors.append(f"reason_missing:{item.capability_id}")
        if not isinstance(item.disposition, CatalogAction):
            errors.append(f"disposition_invalid:{item.capability_id}")
        elif item.disposition is not CatalogAction.RETAINED_EXTERNAL:
            errors.append(f"delegated_capability_must_be_retained_external:{item.capability_id}")
    return tuple(errors)
