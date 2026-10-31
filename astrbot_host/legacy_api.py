"""V1 数据迁移的管理界面接口。

三条路由，对应界面上三步：

    GET  /legacy             发现：V1 的每一份来源在不在、归谁、按合同怎么处置
    GET  /legacy/plan        试算：会写哪些字段、哪一项被改了、为什么（**不写库**）
    POST /legacy/apply       执行：按试算结果写入 V2

## 为什么把写库放在这里而不是域核心

配置写入在 V2 里要经 ``ConfigurationService.patch``，需要**操作者身份**、一次已
登记的 operation、以及当前 edit token——这三样只有宿主层拿得到。所以域侧的
``LegacyMigration`` 只产出 ``ConfigIntent``（作用域 + 分组 + 字段改动），由这里带着
真实 actor 提交。这样切还有个好处：发现与试算成了纯读操作，可以反复跑、可以测；
真正写库的路径只有下面 ``_apply_intent`` 一处。

## 为什么 apply 要重算

``apply`` **不接受**客户端传回来的字段值，只接受 ``plan_token`` 与预览字段的选择标识：令牌必须等于
当前 V1 目录的指纹。不这么做的话，客户端可以在「试算」与「执行」之间换一份数据，
或者干脆自己编一份改动——那等于把「先看后做」这个保证作废。重算一次很便宜
（只读 + 纯转换），换来的是「界面上看到的」与「实际写入的」必定是同一份。
"""

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path

from astrbot.api.web import request
from companion_v2.execution.models import TaskError, digest, require
from companion_v2.configuration.models import ConfigLayer
from companion_v2.foundation.outcome import Outcome
from companion_v2.legacy.migration import LegacyMigration, MigrationState, plugin_data_path
from companion_v2.legacy.image_configuration_migration import plan_image_output_changes
from companion_v2.legacy.reality_migration import (
    REALITY_SETTING_DEFAULTS,
    reality_setting_value,
    reality_settings_state_token,
)
from companion_v2.legacy.native_extension_configuration import (
    SOURCE_PLUGINS, WRITABLE_PLUGINS, REVIEW_ONLY_PLUGINS, HOST_VERIFIED_FIELDS, preview_value,
    setting_value as native_setting_value,
    settings_state_token as native_settings_state_token,
)
from companion_v2.legacy.data_migration import (
    build_data_plan,
    MAX_REFERENCE_IMAGE_BYTES,
    MAX_REFERENCE_IMAGE_ITEMS,
    load_reference_image,
    load_reaction_asset,
    next_reference_image_batch,
    next_reaction_batch,
    next_journal_batch,
)
from companion_v2.legacy.archive import build_v1_archive_plan, copy_v1_archive
from companion_v2.legacy.memory_archive import (
    build_memory_database_plan,
    copy_memory_database_archive,
)
from companion_v2.memory.legacy_materials import (
    MAX_OWNER_BINDINGS,
    LegacyMemoryBinding,
    LegacyOwnerSelector,
)
from companion_v2.references.models import (MAX_ROLE_REFERENCE_BYTES,
    ContentDigest, ReferenceAsset, ReferenceKind, ReferenceRole, ReferenceSource, WardrobeItem)
from companion_v2.references.repositories import ReferenceAssetRepository
from companion_v2.references.service import OWNED_REFERENCE_PREFIX
from companion_v2.storage.reference_asset_schema import reference_limit
from companion_v2.legacy.configuration_migration import (
    ACCOUNT_CONFIGURATION_TARGETS,
    plugin_configuration_defaults,
    plugin_configuration_rules,
)

from .management_identity import dashboard_operator
from .legacy_archive_location import migration_archive_root
from .legacy_config import (V1_HOST_CONFIG_NAME, V1_PLUGIN_DATA_NAME,
                            V1_REALITY_PLUGIN_DATA_NAME, V1_IMAGE_PLUGIN_DATA_NAME,
                            read_v1_host_configuration,
                            read_v1_plugin_configuration, v1_host_config_path,
                            v1_setting, find_v1_data_root)
from .persona_catalog import available_personas
from .legacy_persona_mapping import legacy_account_inventory


PREFIX = "/astrbot_plugin_private_companion/legacy"
MAX_REQUEST_BYTES = 8192
logger = logging.getLogger(__name__)
_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\[^'\"\n]*")


def logger_safe_detail(text: str) -> str:
    """日志里保留错误形状，抹掉绝对路径。"""
    return _WINDOWS_PATH.sub("<path>", str(text))[:200]


def batch_group_receipt_state(code: str) -> str:
    """批量迁移里单个群的回执状态。

    ``legacy_nothing_to_import`` 表示这个 V1 群只有一条空关系网行、也没有群知识或
    群表达可导入：**没有失败**，只是没有东西要写。把它记成 ``failed`` 会让操作者
    去找一条并不存在的错误，因此归到 ``review``（回执文案是「没有可导入内容」，
    不会自动写任何东西）。其余错误码仍然如实记成 ``failed``。
    """
    return "review" if code == "legacy_nothing_to_import" else "failed"

MEMORY_PLUGIN_DATA_NAME = "astrbot_plugin_memory_companion"
_NO_ACCOUNT_CHANGE = object()


@dataclass
class _PendingBindingApply:
    root: Path
    configuration_scope: str
    document: dict
    intent: object
    expected_state_token: str
    auto_import: bool = False


_WEATHER_PLUGIN_FIELDS = frozenset({
    "weather.provider", "weather.api_host", "weather.credential", "weather.refresh_minutes",
})
_WEATHER_REDACTED_FIELDS = frozenset({
    "weather.api_host", "weather.credential", "api_host", "credential",
})
PROACTIVE_CONFIGURATION_SCOPE = "proactive"
REALITY_CONFIGURATION_SCOPE = "reality"
PLUGIN_CONFIGURATION_SCOPE = "plugin"
_PROACTIVE_CONFIGURATION_FIELDS = frozenset({
    "proactive.contact.*",
    "role.state.planning_weather_enabled",
    "role.state.planning_weather_location",
    "role.state.planning_weather_max_age_minutes",
})


class LegacyMigrationWebAPI:
    def __init__(self, controller):
        self.controller = controller
        # All target views share the controller's actual plugin lifetime. Route
        # reconstruction alone must not count as loading the saved configuration.
        self._instance_token = getattr(controller, "lifecycle_token", str(os.getpid()))

    def routes(self):
        from .legacy_archive_api import LegacyArchiveWebAPI
        from .legacy_private_memory_api import PrivateMemoryMigrationAPI
        return [(PREFIX, self.discovery, ["GET"], "Locate the V1 data this installation could migrate"),
                (PREFIX + "/plan", self.plan, ["GET"], "Convert a V1 installation without writing anything"),
            (PREFIX + "/auto", self.auto_import, ["POST"],
             "Import the V1 configuration into the current V2 target without a review step"),
                (PREFIX + "/apply", self.apply, ["POST"], "Write the planned V2 settings"),
                (PREFIX + "/upgrade-confirm", self.confirm_upgrade, ["POST"],
                 "Confirm the migrated configuration after a restart"),
                (PREFIX + "/data-plan", self.data_plan, ["GET"], "Preview reviewed V1 profile, knowledge, reaction and journal data"),
                (PREFIX + "/scope", self.scope, ["GET"], "Preview the role-driven migration scope without writing"),
                (PREFIX + "/batch-apply", self.batch_apply, ["POST"], "Import every automatically mapped window once"),
                (PREFIX + "/data-apply", self.data_apply, ["POST"], "Import the reviewed V1 data batch")] + PrivateMemoryMigrationAPI(self).routes(PREFIX) + LegacyArchiveWebAPI(self).routes(PREFIX)

    # ── 定址 ───────────────────────────────────────────────────────────
    def _v1_root(self, override=None):
        """V1 数据目录在哪。

        先在配置里找显式路径；没有就按「V2 数据目录的同级目录里那个不带 _v2 的
        同名插件」去找——AstrBot 的 plugin_data 是平铺的，所以这是 V1 换 V2 时
        最常见的布局。两条路都不通就如实报错，不猜也不创建目录。
        """
        if override is not None:
            if type(override) is not str or not override.strip():
                raise ValueError("V1 path must be a non-empty absolute directory")
            from pathlib import Path
            explicit = Path(override.strip()).expanduser()
            if not explicit.is_absolute() or not explicit.is_dir():
                raise ValueError("V1 path must be an existing absolute directory")
            return explicit, "request"

        configured = (self.controller.config or {}).get("legacy", {})
        if isinstance(configured, dict):
            explicit = configured.get("v1_root")
            if type(explicit) is str and explicit.strip():
                from pathlib import Path
                return Path(explicit.strip()), "configured"

        return find_v1_data_root(self._v1_source_data_root())

    def _v1_source_data_root(self):
        """Use the canonical AstrBot root even when V2 stores runtime data elsewhere."""
        return (getattr(self.controller, "default_data_root", None)
                or getattr(self.controller, "data_root", "."))

    async def _migration(self, root_override=None, *, session=None):
        root, origin = self._v1_root(root_override)
        registry = getattr(getattr(getattr(session, "application", None), "configuration", None), "registry", None)
        target_scope = getattr(getattr(getattr(session, "profile", None), "target", None), "scope", None)
        configuration_scope_kind = getattr(getattr(target_scope, "kind", None), "value",
                                           getattr(target_scope, "kind", None))
        if configuration_scope_kind not in ("private", "group"):
            configuration_scope_kind = None
        topology = getattr(self.controller, "session_topology", None)
        try:
            accounts = topology.accounts() if callable(getattr(topology, "accounts", None)) else []
        except (TypeError, ValueError, AttributeError):
            accounts = []
        try:
            personas = await available_personas(self.controller.context)
        except (AttributeError, TypeError, ValueError):
            personas = []
        reality_configuration, reality_issue = self._v1_reality_configuration()
        image_configuration, image_issue = self._v1_image_configuration()
        native_configurations, native_issues = self._v1_native_extension_configurations()
        host_configuration, host_issue = self._read_v1_host_configuration()
        enriched_accounts = await legacy_account_inventory(
            self.controller, host_configuration, accounts, personas)
        return LegacyMigration(v1_root=root, v1_configuration=host_configuration,
                               v1_host_configuration_issue=host_issue,
                               configuration_registry=registry,
                               host_accounts=enriched_accounts,
                               configuration_scope_kind=configuration_scope_kind,
                               v1_reality_configuration=reality_configuration,
                                v1_reality_configuration_issue=reality_issue,
                                v1_image_configuration=image_configuration,
                                v1_image_configuration_issue=image_issue,
                                v1_native_configurations=native_configurations,
                                v1_native_configuration_issues=native_issues,
                                native_extension_baselines=self._native_extension_baselines()), root, origin

    # ── V1 的宿主配置 ─────────────────────────────────────────────────
    def _v1_host_config_path(self):
        """V1 插件配置文件的路径；找不到返回 None。

        权威位置是 AstrBot 数据目录的 ``config`` 子目录。V2 数据根形如
        ``…/data/plugin_data/<v2>``，往上两级就是 AstrBot 数据目录；若数据根直接
        挂在数据目录下，往上一级才是。两种都试，与 ``_v1_root`` 对同级目录的
        处理同一风格。
        """
        return v1_host_config_path(self._v1_source_data_root())

    def _v1_host_configuration(self):
        """读出 V1 在宿主里的插件配置映射；读不到返回 None。

        迁移目录把 V1 插件配置声明为 ``host-config:`` 来源，而这份配置的权威位置
        就是宿主配置目录——域侧的 ``LegacyMigration`` 只接收已读出的映射，自己不
        定位宿主文件。读不到（路径不存在或文件损坏）返回 None，域侧会回落到 V1
        数据根里的配置转储，再不行才报「未发现」。
        """
        return self._read_v1_host_configuration()[0]

    def _read_v1_host_configuration(self):
        """区分没有宿主配置与配置存在但无法读取，供迁移发现展示真实状态。"""
        path = self._v1_host_config_path()
        if path is None:
            return None, ""
        try:
            loaded, _, _ = read_v1_host_configuration(self._v1_source_data_root())
        except (OSError, ValueError, UnicodeDecodeError):
            return None, "unreadable"
        return loaded, "" if loaded is not None else "unreadable"

    def _relationship_seed_configuration(self, migration):
        """「用户档案」提示词该从哪一份配置里读。

        ``migration.v1_configuration`` 就是 ``build_data_plan`` 用的那份快照，正常情况下
        一律用它：预览和执行各读一次宿主文件会得到两份不同的 seed，计划令牌也就无法
        证明用户看到的就是将要写入的。

        只有迁移对象**完全没有**携带宿主配置时（直接由 V1 数据根转储构造的迁移）才回退
        读一次宿主配置，否则「用户档案」提示词里的性别、生日、职业、相处方式会被静默
        丢掉。回退路径同样受计划令牌约束：``relationship_seed_state`` 把解析后的 seed 算进
        令牌，两次读取不一致时执行会以 ``legacy_plan_stale`` 拒绝，而不是写出一份没预览过
        的档案。
        """
        configuration = getattr(migration, "v1_configuration", None)
        if isinstance(configuration, dict) and configuration:
            return configuration
        return self._v1_host_configuration()

    def _relationship_config_seed(self, configuration):
        """旧版配置里的「用户档案」提示词 → 主要陪伴对象的关系档案种子。

        用户库里没有性别、生日、职业、相处方式；这些只存在于这份提示词里，
        读不到就返回 None，迁移预览不会因此多出一条空记录。

        ``configuration`` 必须是本次迁移实际使用的那份配置快照
        （``migration.v1_configuration``，也就是传给 ``build_data_plan`` 的同一份，
        统一由 ``_relationship_seed_configuration`` 取）。这里刻意不给默认值：
        少传一次就读成另一份，正是要避免的那个缺陷。
        """
        if not isinstance(configuration, dict):
            return None
        target_ids = v1_setting(configuration, "basic_config.target_user_ids", None)
        if not isinstance(target_ids, (list, tuple)) or not target_ids:
            return None
        primary_user_id = str(target_ids[0] or "").strip()
        if not primary_user_id:
            return None
        text = v1_setting(configuration, "life_schedule_config.roleplay_user_profile_prompt", "")
        from companion_v2.legacy.relationship_profiles import config_relationship_profile
        return config_relationship_profile(text, primary_user_id=primary_user_id)

    def _v1_reality_configuration(self):
        try:
            loaded, _, _ = read_v1_plugin_configuration(
                self._v1_source_data_root(), V1_REALITY_PLUGIN_DATA_NAME,
            )
        except (OSError, ValueError, UnicodeDecodeError):
            return None, "unreadable"
        return loaded, ""

    def _v1_image_configuration(self):
        try:
            loaded, _, _ = read_v1_plugin_configuration(
                self._v1_source_data_root(), V1_IMAGE_PLUGIN_DATA_NAME,
            )
        except (OSError, ValueError, UnicodeDecodeError):
            return None, "unreadable"
        return loaded, ""

    def _v1_native_extension_configurations(self):
        configurations, issues = {}, {}
        for name, plugin_id in (WRITABLE_PLUGINS | REVIEW_ONLY_PLUGINS).items():
            try:
                loaded, _, _ = read_v1_plugin_configuration(self._v1_source_data_root(), plugin_id)
            except (OSError, ValueError, UnicodeDecodeError):
                issues[name] = "unreadable"
            else:
                if loaded is not None:
                    configurations[name] = loaded
        return configurations, issues

    @staticmethod
    def _native_extension_baselines():
        package = Path(__file__).parent / "native_extensions"
        baselines = {}
        for name in SOURCE_PLUGINS:
            try:
                baselines[name] = {
                    key: json.loads((package / name / f"settings-{key}.json").read_text(encoding="utf-8"))
                    for key in ("defaults", "schema")
                }
            except (OSError, ValueError, UnicodeDecodeError):
                continue
        return baselines

    def _fingerprint(self, root) -> str:
        """V1 目录的指纹：只取顶层条目的「名字 + 大小 + 修改时间」。

        用途只有一个——保证「试算」与「执行」面对的是同一份数据。所以不必读内容
        （那可能很贵），只要目录结构变了指纹就变。取不到（目录不存在）返回空串。
        """
        try:
            entries = sorted(
                (child.name, child.stat().st_size, child.stat().st_mtime_ns)
                for child in root.iterdir()
            )
        except OSError:
            return ""
        for name in ("companion_config.json", "plugin_config.json", "config.json"):
            path = root / name
            try:
                stat = path.stat()
                if path.is_file():
                    entries.append((str(path), stat.st_size, stat.st_mtime_ns,
                                    hashlib.sha256(path.read_bytes()).hexdigest()))
            except OSError:
                continue
        # V1 的插件配置在宿主配置目录里，不在 V1 数据根下，顶层条目看不到它；
        # 但它正是迁移要读的那份输入，改了它试算结果就会变，所以一并纳入指纹。
        config_path = self._v1_host_config_path()
        if config_path is not None:
            try:
                stat = config_path.stat()
                content = hashlib.sha256(config_path.read_bytes()).hexdigest()
                entries.append((str(config_path), stat.st_size, stat.st_mtime_ns, content))
            except OSError:
                pass
        for plugin_id in WRITABLE_PLUGINS.values():
            from .legacy_config import v1_plugin_config_path
            path = v1_plugin_config_path(self._v1_source_data_root(), plugin_id)
            if path is None:
                continue
            try:
                stat = path.stat()
                entries.append((str(path), stat.st_size, stat.st_mtime_ns,
                                hashlib.sha256(path.read_bytes()).hexdigest()))
            except OSError:
                pass
        digest = hashlib.sha256(repr(entries).encode("utf-8")).hexdigest()
        return digest

    # ── 路由 ───────────────────────────────────────────────────────────
    async def discovery(self):
        from .web_api import outcome_response
        options = self._query_options()
        if options is False:
            return outcome_response(Outcome.failed("legacy_request_invalid", "invalid legacy query"))
        root_override, configuration_scope = options
        return await self._run(
            lambda migration, root, session: self._discovery(
                migration, root, session, configuration_scope),
            root_override=root_override,
        )

    async def plan(self):
        from .web_api import outcome_response
        options = self._query_options()
        if options is False:
            return outcome_response(Outcome.failed("legacy_request_invalid", "invalid legacy query"))
        root_override, configuration_scope = options
        return await self._run(
            lambda migration, root, session: self._plan(
                migration, root, session, configuration_scope),
            root_override=root_override,
        )

    @staticmethod
    def _query_options():
        query = request.query
        if (set(query.keys()) - {"v1_root", "configuration_scope"}
                or any(len(query.getlist(key)) != 1 for key in query.keys())):
            return False
        configuration_scope = query.get("configuration_scope", "all")
        if configuration_scope not in ("all", PLUGIN_CONFIGURATION_SCOPE,
                                       PROACTIVE_CONFIGURATION_SCOPE, REALITY_CONFIGURATION_SCOPE):
            return False
        return query.get("v1_root"), configuration_scope

    async def apply(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > 300000:
                raise ValueError()
            arguments = json.loads(raw.decode("utf-8")) if raw else {}
            if (type(arguments) is not dict or not {"plan_token"}.issubset(arguments)
                    or set(arguments) - {"plan_token", "v1_root", "configuration_scope", "selected_fields"}):
                raise ValueError()
            if type(arguments["plan_token"]) is not str or not arguments["plan_token"]:
                raise ValueError()
            if "selected_fields" in arguments and (
                    type(arguments["selected_fields"]) is not list
                    or len(arguments["selected_fields"]) > 4096
                    or any(type(value) is not str or len(value) != 64
                           for value in arguments["selected_fields"])):
                raise ValueError()
            root_override = arguments.get("v1_root")
            if root_override is not None and (type(root_override) is not str or not root_override.strip()):
                raise ValueError()
            configuration_scope = arguments.get("configuration_scope", "all")
            if configuration_scope not in ("all", PLUGIN_CONFIGURATION_SCOPE,
                                           PROACTIVE_CONFIGURATION_SCOPE, REALITY_CONFIGURATION_SCOPE):
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("legacy_request_invalid", "invalid apply request"))
        return await self._run(
            lambda migration, root, session: self._apply(
                migration, root, session, arguments["plan_token"], configuration_scope, defer_bindings=True,
                selected_fields=arguments.get("selected_fields")),
            root_override=root_override,
        )

    async def auto_import(self):
        """首启「无感继承」：把 V1 的配置直接试算并写入当前 V2 目标。

        为什么只做配置、不做资料：资料迁移在人格上是**一对多**的
        （`_persona_destination_mapping` 里写明 `automatic_matching: False`、
        `requires_independent_plan_per_destination: True`），V1 一个人格可能对应
        多个 V2 目标，自动挑一个就会写错人。所以这里只接管配置：映射按字段走，
        写入目标就是页面当前选中的账号与角色；资料仍由迁移页让人逐个选择。

        已经导入过配置就不重复执行；资料迁移另走逐个人格确认流程。
        """
        try:
            raw = await request.body()
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError()
            arguments = json.loads(raw.decode("utf-8")) if raw else {}
            if type(arguments) is not dict or set(arguments) - {"v1_root"}:
                raise ValueError()
            root_override = arguments.get("v1_root")
            if root_override is not None and (type(root_override) is not str or not root_override.strip()):
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            from .web_api import outcome_response
            return outcome_response(Outcome.failed("legacy_request_invalid", "invalid auto import request"))

        async def work(migration, root, session):
            if self._already_imported(root):
                return Outcome.success({"state": "already_imported", "v1_root": str(root)})
            planned = self._plan(migration, root, session, PLUGIN_CONFIGURATION_SCOPE)
            if not planned.is_success:
                return planned
            document = planned.unwrap()
            blocked_sources = self._configuration_source_blockers(
                document.get("findings", ()), PLUGIN_CONFIGURATION_SCOPE,
            )
            if blocked_sources:
                return self._configuration_source_unavailable(blocked_sources)
            optional_errors = self._optional_extension_source_errors(
                document.get("findings", ()), PLUGIN_CONFIGURATION_SCOPE)
            token = document.get("plan_token")
            if not token:
                return Outcome.failed("legacy_plan_stale", "试算没有产出可写入的计划")
            if not document.get("change_count"):
                if optional_errors:
                    return Outcome.failed("legacy_native_source_unavailable",
                                          "独立拓展配置尚未读入；核心配置可单独试算，升级暂不登记完成。")
                marked = self._mark_imported(
                    0, root, configuration_scope=PLUGIN_CONFIGURATION_SCOPE,
                    restart_required=False,
                )
                return Outcome.success({
                    "state": "nothing_to_import", "v1_root": str(root),
                    "preserved_count": document.get("preserved_count", 0),
                    "completion_recorded": marked,
                    "configuration_scope": PLUGIN_CONFIGURATION_SCOPE,
                    "restart_required": False,
                })
            applied = await self._apply(migration, root, session, token, PLUGIN_CONFIGURATION_SCOPE,
                                        defer_bindings=True)
            if isinstance(applied, _PendingBindingApply):
                applied.auto_import = True
                return applied
            if not applied.is_success:
                return applied
            result = dict(applied.unwrap())
            result["state"] = "imported" if result.get("completion_recorded") else "partial"
            result["configuration_imported"] = bool(result.get("completion_recorded"))
            result["data_import_required"] = True
            return Outcome.success(result)

        return await self._run(work, root_override=root_override)

    async def confirm_upgrade(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError()
            arguments = json.loads(raw.decode("utf-8")) if raw else {}
            if type(arguments) is not dict or arguments != {"confirmed": True}:
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("legacy_request_invalid", "invalid upgrade confirmation"))

        async def work(migration, root, session):
            if not self._already_imported(root):
                return Outcome.failed("legacy_configuration_not_imported",
                                      "V1 配置尚未记录为已迁移")
            if not self._restart_observed(root):
                return Outcome.failed("legacy_restart_required",
                                      "配置迁移后请先重载本插件或重启 AstrBot，再核对运行状态")
            if not self._mark_upgrade_verified(root):
                return Outcome.failed("legacy_upgrade_status_unavailable",
                                      "核对结果未能保存，请重新读取升级状态")
            return Outcome.success({"state": "verified",
                                   "install": self._install_state(root, [])})

        return await self._run(work)

    async def scope(self):
        """Preview the role-driven migration scope; nothing is written."""
        from .web_api import outcome_response
        query = request.query
        if (set(query.keys()) - {"v1_root"}
                or any(len(query.getlist(key)) != 1 for key in query.keys())):
            return outcome_response(Outcome.failed("legacy_request_invalid", "invalid migration scope query"))
        root_override = query.get("v1_root")
        if root_override is not None and (type(root_override) is not str or not root_override.strip()):
            return outcome_response(Outcome.failed("legacy_request_invalid", "invalid migration scope query"))
        return await self._run(
            lambda migration, root, session: self._scope(migration, root, session),
            root_override=root_override, offload_sync=True)

    async def batch_apply(self):
        """Run every reviewed, automatically mapped window once; never guess."""
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError()
            arguments = json.loads(raw.decode("utf-8")) if raw else {}
            allowed = {"token", "v1_root", "personas", "groups", "options"}
            if (type(arguments) is not dict or set(arguments) - allowed
                    or type(arguments.get("token")) is not str or not arguments["token"]
                    or type(arguments.get("personas", [])) is not list
                    or type(arguments.get("groups", [])) is not list
                    or type(arguments.get("options", {})) is not dict):
                raise ValueError()
            for item in arguments.get("personas", []):
                if type(item) is not str or len(item) > 96:
                    raise ValueError()
            for item in arguments.get("groups", []):
                if (type(item) is not dict or set(item) != {"persona_id", "group_id"}
                        or type(item["persona_id"]) is not str or len(item["persona_id"]) > 96
                        or type(item["group_id"]) is not str or not item["group_id"]
                        or len(item["group_id"]) > 256):
                    raise ValueError()
            root_override = arguments.get("v1_root")
            if root_override is not None and (type(root_override) is not str or not root_override.strip()):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("legacy_request_invalid", "invalid batch migration request"))
        return await self._run(
            lambda migration, root, session: self._batch_apply(migration, root, session, arguments),
            root_override=root_override)

    @staticmethod
    def _group_import_arguments(root, key, plan):
        """Request only the group sections V1 actually has.

        The explicit single-group page applies the same rule from its preview
        (``setup-guide.js`` gates the worldbook checkbox on entries+members and
        the expression checkbox on availability). The batch calls the importer
        directly, so it must derive the same flags from the reviewed plan: an
        empty worldbook is not a request the importer can satisfy, and asking
        for it used to fail a group that only had a relationship network.

        The plan comes from the host, so the fields read below are validated
        first: an unusable plan is one receipt (``legacy_group_plan_invalid``)
        instead of a traceback that escapes the batch.
        """
        if (not isinstance(plan, dict) or type(plan.get("plan_token")) is not str
                or not plan["plan_token"]):
            raise ValueError("legacy_group_plan_invalid")
        worldbook = plan.get("group_worldbook")
        if worldbook is None:
            worldbook = {}
        expression = plan.get("group_expression")
        if (not isinstance(worldbook, dict) or not isinstance(expression, dict)
                or type(expression.get("available")) is not bool):
            raise ValueError("legacy_group_plan_invalid")
        network = plan.get("source_group_network") or {}
        if not isinstance(network, dict):
            raise ValueError("legacy_group_plan_invalid")
        return {
            "v1_root": str(root),
            "source_persona_id": key[0],
            "source_group_id": key[1],
            "plan_token": plan["plan_token"],
            # Group networks are imported per (persona, group); an expression-only
            # V1 group still produces a zero-count row, which is not a network.
            "import_group_network": bool(network.get("member_count") or network.get("edge_count")),
            "confirm_group_mapping": True,
            "confirm_member_ids": True,
            "import_group_worldbook": bool(worldbook.get("entries") or worldbook.get("members")),
            "import_group_expression": bool(expression.get("available")),
            "confirm_expression_mapping": True,
        }

    async def _batch_apply(self, migration, root, session, arguments):
        """Apply the reviewed scope item by item, keeping one receipt per item."""
        from .legacy_group_data_api import LegacyGroupDataWebAPI
        scope = await asyncio.to_thread(self._scope, migration, root, session)
        if not scope.is_success:
            return scope
        scope = scope.unwrap()
        if scope["token"] != arguments["token"]:
            return Outcome.failed("legacy_scope_stale",
                "the reviewed migration scope changed; preview the role again before importing.")
        personas = {item["source_id"]: item for item in scope["personas"]}
        groups = {(item["persona_id"], item["source_id"]): item for item in scope["groups"]}
        options = arguments.get("options") or {}
        flags = {
            "import_reactions": True, "import_group_network": True, "confirm_member_ids": True,
            "import_journals": True, "confirm_persona_mapping": True, "import_worldbook": True,
            "import_archive": options.get("import_archive") is True,
            "import_memory_database": options.get("import_memory_database") is True,
            "confirm_memory_mapping": options.get("confirm_memory_mapping") is True,
            "import_reference_images": True, "import_wardrobe_items": True,
            "confirm_reference_persona_mapping": True,
            "import_expressions": True, "confirm_expression_mapping": True,
            "import_private_contacts": True, "import_relationship_profiles": True,
        }
        items = []
        for persona_id in arguments.get("personas", []):
            entry = personas.get(persona_id)
            if entry is None or not entry["auto"]:
                items.append({"kind": "persona", "persona_id": persona_id, "state": "review",
                              "reason": (entry or {}).get("reason") or "persona_not_in_scope"})
                continue
            items.append(await self._import_persona(migration, root, session, persona_id, flags))
        for request_item in arguments.get("groups", []):
            key = (request_item["persona_id"], request_item["group_id"])
            entry = groups.get(key)
            if entry is None or not entry["auto"]:
                items.append({"kind": "group", "persona_id": key[0], "group_id": key[1],
                              "state": "review",
                              "reason": (entry or {}).get("reason") or "group_not_in_scope"})
                continue
            api = LegacyGroupDataWebAPI(self.controller, entry["target_id"],
                                        getattr(self.controller, "role_id", ""))
            try:
                target, group_session = await asyncio.to_thread(api._target)
                plan = await asyncio.to_thread(api._plan, root, key[0], key[1], target, group_session)
                try:
                    group_arguments = self._group_import_arguments(root, key, plan)
                except (KeyError, TypeError, ValueError):
                    # The reviewed plan cannot be turned into an import request;
                    # that is this group's receipt, with its own error code.
                    items.append({"kind": "group", "persona_id": key[0], "group_id": key[1],
                                  "state": "failed", "reason": "legacy_group_plan_invalid"})
                    continue
                response = await asyncio.to_thread(
                    api.apply_import, group_arguments, root, target, group_session)
            except Exception as error:  # noqa: BLE001 - one group must not stop the rest
                # A plan/apply exception is this group's receipt, not the end of
                # the batch: the next group has its own transaction and target.
                logger.warning("[CompanionV2] 群迁移条目失败 group=%s error_type=%s",
                               logger_safe_detail(key[1]), type(error).__name__)
                items.append({"kind": "group", "persona_id": key[0], "group_id": key[1],
                              "state": "failed",
                              "reason": getattr(error, "code", type(error).__name__)})
                continue
            code = (response.get("data") or {}).get("code") if isinstance(
                response.get("data"), dict) else None
            code = code or response.get("message")
            imported = response.get("status") == "ok"
            items.append({"kind": "group", "persona_id": key[0], "group_id": key[1],
                          "state": "imported" if imported else batch_group_receipt_state(code),
                          "reason": None if imported else code,
                          "result": response.get("data")})
        counts = {"imported": sum(1 for item in items if item["state"] == "imported"),
                  "review": sum(1 for item in items if item["state"] == "review"),
                  "failed": sum(1 for item in items if item["state"] == "failed")}
        return Outcome.success({"token": scope["token"], "items": items, "counts": counts,
                                "memory_database_skipped": not flags["import_memory_database"]})

    async def _import_persona(self, migration, root, session, persona_id, flags):
        plan = await asyncio.to_thread(self._data_plan, migration, root, session, "", persona_id,
            reference_slots_available=24, wardrobe_slots_available=24)
        if not plan.is_success:
            return {"kind": "persona", "persona_id": persona_id, "state": "failed",
                    "reason": plan.failure.code}
        document = plan.unwrap()
        flags = dict(flags)
        # Sections without an available V1 source must not be requested: the
        # importer rejects "import expressions" when the persona has none.
        if not (document.get("expressions") or {}).get("available"):
            flags["import_expressions"] = False
            flags["confirm_expression_mapping"] = False
        # Only request the sections this persona actually has; an empty section
        # is a validator error, not a silent no-op.
        worldbook = document.get("worldbook") or {}
        network = document.get("group_networks") or ()
        worldbook_has_data = bool(worldbook.get("general_count") or worldbook.get("member_count")
            or worldbook.get("groups") or worldbook.get("member_profile_groups"))
        # Group networks are imported per (persona, group) by the group batch;
        # a persona-level request has no group id and would fail as "not found".
        flags["import_group_network"] = False
        flags["import_worldbook"] = worldbook_has_data
        flags["confirm_member_ids"] = True
        contacts = document.get("private_contacts") or {}
        flags["import_private_contacts"] = bool(contacts.get("importable") or contacts.get("total"))
        # Relationship details are part of the same per-person migration; the
        # importer only fills empty V2 fields and reports skipped identities.
        flags["import_relationship_profiles"] = True
        flags["import_journals"] = bool((document.get("journals") or {}).get("batch_count"))
        flags["import_reactions"] = bool((document.get("reaction_assets") or {}).get("batch_count"))
        flags["import_reference_images"] = bool((document.get("reference_images") or {}).get("image_count"))
        flags["import_wardrobe_items"] = bool((document.get("wardrobe_items") or {}).get("image_count"))
        result = await self._data_apply(migration, root, session, document["plan_token"], "", persona_id,
            **flags)
        if not result.is_success:
            return {"kind": "persona", "persona_id": persona_id, "state": "failed",
                    "reason": result.failure.code}
        data = result.unwrap()
        relationships = data.get("relationship_profiles") or {}
        return {"kind": "persona", "persona_id": persona_id, "state": "imported",
                "relationship_profiles": {
                    "imported": relationships.get("imported", 0),
                    "unchanged": relationships.get("unchanged", 0),
                    "skipped": relationships.get("skipped", 0),
                },
                "result": data}

    def _scope(self, migration, root, session):
        from companion_v2.legacy.data_migration import build_data_plan
        from companion_v2.legacy.migration_scope import build_scope
        planned = migration.discover()
        if not planned.is_success:
            return planned
        main_document = build_data_plan(root, v1_configuration=migration.v1_configuration).document()
        context, _service = self._data_context(session)
        scope_ref = context.scope
        platform_id, account_id = scope_ref.platform, scope_ref.account_id
        role_id = getattr(self.controller, "role_id", "")
        group_targets = []
        for item in self.controller.web_api.target_inventory():
            if item.get("scope_kind") != "group":
                continue
            if (item.get("platform_id"), item.get("account_id")) != (platform_id, account_id):
                continue
            roles = [role for role in item.get("roles", ()) if role.get("legacy_group_data_api")]
            group_targets.append({**item, "has_role": bool(roles)})
        persona_roles = {}
        configuration = getattr(self.controller, "config", None) or {}
        rows = configuration.get("multi_host", {}).get("personas", ()) if isinstance(configuration, dict) else ()
        for row in rows if isinstance(rows, (list, tuple)) else ():
            if (isinstance(row, dict) and type(row.get("persona_id")) is str and row["persona_id"]
                    and type(row.get("role_id")) is str and row["role_id"]):
                persona_roles[row["persona_id"]] = row["role_id"]
        # The profile without an id still owns a role: it is the default persona
        # of the account, derived the same way the host derives it.
        from .identity_derivation import derive_role_id
        try:
            # The profile without an id still owns a role: it is the profile the

            # operator is migrating into this role, so the target role wins over

            # the account default.

            persona_roles.setdefault("", role_id or derive_role_id(account_id, "[%None]"))
        except (TypeError, ValueError):
            pass
        plans = [("", main_document)]
        seen = set()
        for item in main_document.get("persona_sources") or ():
            persona_id = item.get("persona_id") if isinstance(item, dict) else None
            if (type(persona_id) is not str or not persona_id or persona_id in seen
                    or item.get("state") != "ready"):
                continue
            seen.add(persona_id)
            try:
                document = build_data_plan(root, source_persona_id=persona_id,
                                           v1_configuration=migration.v1_configuration).document()
            except (OSError, ValueError, TypeError, KeyError):
                continue
            plans.append((persona_id, document))
        return Outcome.success(build_scope(plans, group_targets, persona_roles,
            role_id=role_id, platform_id=platform_id, account_id=account_id))

    async def data_plan(self):
        from .web_api import outcome_response
        options = self._data_query_options()
        if options is False:
            return outcome_response(Outcome.failed("legacy_request_invalid", "invalid data migration query"))
        (root_override, source_group_id, source_persona_id, memory_owner_ids, memory_group_mappings,
         reference_slots, reference_bytes, wardrobe_slots) = options
        response = await self._run(
            lambda migration, root, session: self._data_plan(
                migration, root, session, source_group_id, source_persona_id,
                memory_owner_ids=memory_owner_ids, memory_group_mappings=memory_group_mappings,
                reference_slots_available=reference_slots, reference_bytes_available=reference_bytes,
                wardrobe_slots_available=wardrobe_slots),
            root_override=root_override,
            offload_sync=True,
        )
        if response.get("status") == "ok" and isinstance(response.get("data"), dict):
            response["data"]["persona_destination_mapping"] = self._persona_destination_mapping(
                response["data"].get("persona_sources", ()))
        return response

    def _persona_destination_mapping(self, persona_sources):
        """Describe explicit one-to-many profile destinations without selecting any."""
        destinations = []
        for target in self.controller.web_api.target_inventory():
            if target.get("scope_kind") != "private":
                continue
            for role in target.get("roles", ()):
                if not role.get("legacy_api"):
                    continue
                destinations.append({
                    "target_id": target.get("target_id"),
                    "platform_id": target.get("platform_id"),
                    "account_id": target.get("account_id"),
                    "bot_id": target.get("bot_id"),
                    "participant_id": target.get("participant_id"),
                    "role_id": role.get("role_id"),
                    "role_name": role.get("role_name"),
                    "legacy_api": role.get("legacy_api"),
                })
        return {
            "cardinality": "one_source_to_many_explicit_destinations",
            "source_account_linkage": "unknown_unless_v1_records_it",
            "automatic_matching": False,
            "automatic_fanout": False,
            "requires_independent_plan_per_destination": True,
            "sources": [{
                "source_persona_id": item.get("persona_id", ""),
                "source_label": item.get("label", ""),
                "source_state": item.get("state", "unavailable"),
                "source_platform_id": None,
                "source_account_id": None,
                "source_bot_id": None,
                "destinations": destinations,
            } for item in persona_sources if isinstance(item, dict)],
        }

    async def data_apply(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError()
            arguments = json.loads(raw.decode("utf-8")) if raw else {}
            allowed = {"plan_token", "v1_root", "source_group_id", "source_persona_id",
                       "import_reactions", "import_group_network", "confirm_member_ids",
                       "import_journals", "confirm_persona_mapping", "import_worldbook",
                       "import_archive", "import_memory_database", "memory_owner_ids",
                       "memory_group_mappings", "confirm_memory_mapping", "import_reference_images",
                       "import_wardrobe_items", "confirm_reference_persona_mapping",
                       "import_expressions", "confirm_expression_mapping", "import_private_contacts",
                       "import_relationship_profiles"}
            if (type(arguments) is not dict or not {"plan_token", "import_reactions",
                    "import_group_network", "confirm_member_ids", "source_persona_id",
                    "import_journals", "confirm_persona_mapping", "import_worldbook"}.issubset(arguments)
                    or set(arguments) - allowed):
                raise ValueError()
            if (type(arguments["plan_token"]) is not str or not arguments["plan_token"]
                    or any(type(arguments.get(key, False)) is not bool for key in
                           ("import_expressions", "confirm_expression_mapping", "import_private_contacts",
                            "import_relationship_profiles"))
                    or type(arguments["import_reactions"]) is not bool
                    or type(arguments["import_group_network"]) is not bool
                    or type(arguments["confirm_member_ids"]) is not bool
                    or type(arguments["import_journals"]) is not bool
                    or type(arguments["confirm_persona_mapping"]) is not bool
                    or type(arguments["import_worldbook"]) is not bool
                    or ("import_reference_images" in arguments
                        and type(arguments["import_reference_images"]) is not bool)
                    or ("import_wardrobe_items" in arguments
                        and type(arguments["import_wardrobe_items"]) is not bool)
                    or ("confirm_reference_persona_mapping" in arguments
                        and type(arguments["confirm_reference_persona_mapping"]) is not bool)
                    or ("import_archive" in arguments and type(arguments["import_archive"]) is not bool)
                    or ("import_memory_database" in arguments
                        and type(arguments["import_memory_database"]) is not bool)
                    or ("confirm_memory_mapping" in arguments
                        and type(arguments["confirm_memory_mapping"]) is not bool)
                    or type(arguments["source_persona_id"]) is not str
                    or len(arguments["source_persona_id"]) > 96):
                raise ValueError()
            memory_owner_ids = arguments.get("memory_owner_ids", [])
            if (type(memory_owner_ids) is not list or len(memory_owner_ids) > MAX_OWNER_BINDINGS
                    or any(type(value) is not str or len(value) != 64
                           or any(char not in "0123456789abcdef" for char in value)
                           for value in memory_owner_ids)
                    or len(set(memory_owner_ids)) != len(memory_owner_ids)):
                raise ValueError()
            memory_group_mappings = self._normalize_memory_group_mappings(
                arguments.get("memory_group_mappings", []))
            root_override = arguments.get("v1_root")
            if root_override is not None and (type(root_override) is not str or not root_override.strip()):
                raise ValueError()
            source_group_id = arguments.get("source_group_id", "")
            if type(source_group_id) is not str or len(source_group_id) > 256:
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("legacy_request_invalid", "invalid data migration request"))
        return await self._run(
            lambda migration, root, session: self._data_apply(
                migration, root, session, arguments["plan_token"], source_group_id,
                arguments["source_persona_id"],
                arguments["import_reactions"], arguments["import_group_network"],
                arguments["confirm_member_ids"], arguments["import_journals"],
                arguments["confirm_persona_mapping"], arguments["import_worldbook"],
                arguments.get("import_archive", False),
                arguments.get("import_memory_database", False), memory_owner_ids,
                arguments.get("confirm_memory_mapping", False), memory_group_mappings,
                arguments.get("import_reference_images", False),
                arguments.get("import_wardrobe_items", False),
                arguments.get("confirm_reference_persona_mapping", False),
                arguments.get("import_expressions", False), arguments.get("confirm_expression_mapping", False),
                arguments.get("import_private_contacts", False),
                arguments.get("import_relationship_profiles", False)),
            root_override=root_override,
        )

    @staticmethod
    def _data_query_options():
        query = request.query
        if (set(query.keys()) - {"v1_root", "source_group_id", "source_persona_id", "memory_owner_ids",
                                 "memory_group_mappings", "reference_slots_available",
                                 "reference_bytes_available", "wardrobe_slots_available"}
                or any(len(query.getlist(key)) != 1 for key in query.keys())):
            return False
        root = query.get("v1_root")
        group_id = query.get("source_group_id", "")
        persona_id = query.get("source_persona_id", "")
        raw_owner_ids = query.get("memory_owner_ids", "")
        raw_group_mappings = query.get("memory_group_mappings", "[]")
        raw_reference_slots = query.get("reference_slots_available", "24")
        raw_reference_bytes = query.get("reference_bytes_available", str(MAX_REFERENCE_IMAGE_BYTES * 24))
        raw_wardrobe_slots = query.get("wardrobe_slots_available", "24")
        if (group_id and len(group_id) > 256) or len(persona_id) > 96:
            return False
        owner_ids = tuple(raw_owner_ids.split(",")) if raw_owner_ids else ()
        if (len(owner_ids) > MAX_OWNER_BINDINGS
                or any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
                       for value in owner_ids)
                or len(set(owner_ids)) != len(owner_ids)):
            return False
        try:
            if len(raw_group_mappings) > MAX_REQUEST_BYTES:
                return False
            group_mappings = LegacyMigrationWebAPI._normalize_memory_group_mappings(
                json.loads(raw_group_mappings))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        try:
            reference_slots = max(0, min(24, int(raw_reference_slots)))
            reference_bytes = max(0, min(MAX_REFERENCE_IMAGE_BYTES * 24, int(raw_reference_bytes)))
            wardrobe_slots = max(0, min(24, int(raw_wardrobe_slots)))
        except (TypeError, ValueError):
            return False
        return root, group_id, persona_id, owner_ids, group_mappings, reference_slots, reference_bytes, wardrobe_slots

    @staticmethod
    def _normalize_memory_group_mappings(value):
        if type(value) is not list or len(value) > MAX_OWNER_BINDINGS:
            raise ValueError("legacy_memory_group_mapping_invalid")
        normalized = []
        seen = set()
        for item in value:
            if type(item) is not dict or set(item) != {"owner_id", "target_id", "role_id"}:
                raise ValueError("legacy_memory_group_mapping_invalid")
            owner_id, target_id, role_id = item["owner_id"], item["target_id"], item["role_id"]
            if (type(owner_id) is not str or len(owner_id) != 64
                    or any(char not in "0123456789abcdef" for char in owner_id)
                    or type(target_id) is not str or not target_id or len(target_id) > 128
                    or type(role_id) is not str or not role_id or len(role_id) > 128
                    or owner_id in seen):
                raise ValueError("legacy_memory_group_mapping_invalid")
            seen.add(owner_id)
            normalized.append({"owner_id": owner_id, "target_id": target_id, "role_id": role_id})
        return tuple(sorted(normalized, key=lambda item: item["owner_id"]))

    @staticmethod
    def _data_context(session):
        context = getattr(getattr(session, "profile", None), "target", None)
        service = getattr(getattr(session, "application", None), "semantics", None)
        if context is None or service is None:
            raise TaskError("legacy_data_service_unavailable", "V2 data services are not initialized")
        return context, service

    @staticmethod
    def _reference_import_state(session, context):
        reference_service = getattr(session.application, "references", None)
        if reference_service is None:
            return {"owner": "", "hashes": [], "wardrobe_ids": [],
                    "active_asset_count": 0, "active_wardrobe_count": 0,
                    "reference_slots": 0, "wardrobe_slots": 0,
                    "reference_bytes": 0, "registered": False}
        owner = reference_service.owner(context)
        assets = session.application.runtime.read_all(
            "SELECT asset_id, state, content_hash FROM reference_assets WHERE owner_key = ?",
            (owner,))
        wardrobe = session.application.runtime.read_all(
            "SELECT item_id, state FROM wardrobe_items WHERE owner_key = ?", (owner,))
        payloads = session.application.runtime.read_all(
            "SELECT COALESCE(SUM(size_bytes), 0) AS n FROM reference_asset_payloads WHERE owner_key = ?",
            (owner,))
        used_bytes = payloads[0]["n"] if payloads else 0
        active_assets = sum(row["state"] == "active" for row in assets)
        active_wardrobe = sum(row["state"] == "active" for row in wardrobe)
        return {
            "owner": owner,
            "hashes": sorted({row["content_hash"] for row in assets}),
            "wardrobe_ids": sorted({row["item_id"] for row in wardrobe}),
            "active_asset_count": active_assets,
            "active_wardrobe_count": active_wardrobe,
            "reference_slots": max(0, reference_limit() - active_assets),
            "wardrobe_slots": max(0, reference_limit() - active_wardrobe),
            "reference_bytes": max(0, MAX_ROLE_REFERENCE_BYTES - used_bytes),
            "registered": True,
        }

    def _data_plan_token(self, data_plan, context, service, source_group_id, source_persona_id,
                       existing_reaction_ids=(), existing_journal_heads=None,
                       existing_worldbook=None, archive_inventory_fingerprint="",
                         memory_database_fingerprint="", memory_owner_ids=(), memory_group_mappings=(),
                         reference_state=None, expression_state=None, contact_state=None,
                         relationship_state=None, relationship_seed=None):
        scope = context.scope
        material = {
            "source": data_plan.source_fingerprint,
            "archive_inventory": archive_inventory_fingerprint,
            "memory_database": memory_database_fingerprint,
            "memory_owner_ids": sorted(memory_owner_ids),
            # Selection order does not change a binding. Apply normalizes these
            # mappings too; hash the same selection consistently in both stages.
            "memory_group_mappings": sorted(memory_group_mappings, key=lambda item: item["owner_id"]),
            "source_group_id": source_group_id,
            "source_persona_id": source_persona_id,
            "target_owner": service.reaction_owner(context),
            "scope": [scope.installation_id, scope.platform, scope.bot_id, scope.account_id,
                      getattr(scope.kind, "value", scope.kind), scope.native_scope_id],
            "role": getattr(context.role_binding.role, "subject_id", ""),
            "existing_reaction_ids": sorted(set(existing_reaction_ids)
                                             & {item.sha256 for item in data_plan.reaction_assets}),
            "existing_journal_heads": sorted(
                (kind, local_date, head.get("state", ""), head.get("revision", 0),
                 bool(head.get("imported")), bool(head.get("derivatives_imported")),
                 bool(head.get("derivatives_conflict")))
                for (kind, local_date), head in (existing_journal_heads or {}).items()),
            "existing_worldbook": existing_worldbook or {},
            "reference_state": reference_state or {},
            "expression_state": expression_state or {},
            "private_contacts": contact_state or {},
            "relationship_profiles": relationship_state or {},
            "relationship_seed": relationship_seed or {},
        }
        encoded = json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _data_plan(self, migration, root, session, source_group_id="", source_persona_id="",
                   *, memory_owner_ids=(), memory_group_mappings=(), reference_slots_available=0,
                   reference_bytes_available=MAX_REFERENCE_IMAGE_BYTES * 24,
                   wardrobe_slots_available=0):
        planned = migration.discover()
        if not planned.is_success:
            return planned
        data_plan = build_data_plan(root, source_persona_id=source_persona_id,
                                    v1_configuration=migration.v1_configuration)
        runtime_root = getattr(self.controller, "data_root", None)
        archive_plan = build_v1_archive_plan(
            root, exclude_roots=(runtime_root,) if runtime_root is not None else ())
        memory_plan = self._memory_source_plan(root)
        context, service = self._data_context(session)
        owner = service.ensure_reaction_catalog(context)
        existing_ids = self._reaction_ids(service, owner)
        existing_heads = self._journal_heads(data_plan, session, context)
        worldbook_state = self._worldbook_state(data_plan, context, source_group_id)
        reference_state = self._reference_import_state(session, context)
        existing_reference_hashes = set(reference_state["hashes"])
        existing_wardrobe_ids = set(reference_state["wardrobe_ids"])
        reference_slots_available = min(reference_slots_available, reference_state["reference_slots"])
        wardrobe_slots_available = min(wardrobe_slots_available, reference_state["wardrobe_slots"])
        reference_bytes_available = min(reference_bytes_available, reference_state["reference_bytes"])
        document = data_plan.document(source_group_id=source_group_id,
                                      existing_reaction_ids=existing_ids,
                                      existing_journal_heads=existing_heads,
                                      existing_reference_hashes=existing_reference_hashes,
                                      existing_wardrobe_ids=existing_wardrobe_ids,
                                      reference_slots_available=reference_slots_available,
                                      reference_bytes_available=reference_bytes_available,
                                      wardrobe_slots_available=wardrobe_slots_available)
        from companion_v2.memory.person.legacy_import import contact_plan
        contact_state = contact_plan(self.controller.runtime, context, data_plan.private_contacts)
        from companion_v2.identity_policy.legacy_import import authorization_plan
        contact_state["authorization"] = authorization_plan(
            self.controller.runtime, context, data_plan.private_contacts, self.controller.now())
        document["private_contacts"] = {**contact_state, "rejected": list(data_plan.private_contact_rejected)}
        from companion_v2.relationships.legacy_import import (
            relationship_profile_entries, relationship_profile_plan, relationship_profile_token,
            relationship_seed_state)
        relationship_seed = self._relationship_config_seed(self._relationship_seed_configuration(migration))
        relationship_entries = relationship_profile_entries(
            self.controller.runtime, context, data_plan.relationship_profiles,
            seed=relationship_seed)
        relationship_state = relationship_profile_plan(relationship_entries)
        relationship_token = relationship_profile_token(relationship_entries)
        relationship_seed_token = relationship_seed_state(relationship_seed)
        document["relationship_profiles"] = {**relationship_state,
            "rejected": list(data_plan.relationship_profile_rejected)}
        document["archive"] = archive_plan.document()
        expression_state = self._expression_state(data_plan, context, source_group_id)
        document["expressions"] = expression_state
        document["memory_database"] = memory_plan.document()
        memory_projection = self._memory_projection(session, memory_plan, context, memory_owner_ids,
                                                    memory_group_mappings)
        if not memory_projection.is_success:
            return memory_projection
        memory_projection = memory_projection.unwrap()
        inventory = memory_projection.get("owner_inventory", {})
        scoped_assets = [asset.document() for asset in memory_plan.assets
                         if asset.relative_path == "req041_scoped.db"]
        document["memory_database"].update({
            "projection": memory_projection,
            "identity_inventory": inventory,
            "archive_only": {
                "group_owner_count": inventory.get("archive_only_owner_count", 0),
                "group_record_count": inventory.get("archive_only_record_count", 0),
                "unresolved_records": sum(inventory.get("unresolved_ownership", {}).values()),
                "unmapped_private_records": memory_projection.get("excluded", {}).get("binding_missing", 0),
                "hashed_namespace_assets": scoped_assets,
                "hashed_namespace_record_count": sum(
                    sum(asset.get("table_counts", {}).values()) for asset in scoped_assets),
            },
            "selected_owner_ids": list(memory_owner_ids),
            "selected_owner_count": len(memory_owner_ids),
            "selected_group_mappings": list(memory_group_mappings),
            "group_targets": self._memory_group_target_inventory(),
        })
        group = document.get("selected_group")
        worldbook_group_ids = {item["source_group_id"] for item in document["worldbook"]["groups"]}
        worldbook_group_ids.update(item["source_group_id"]
            for item in document["worldbook"]["member_profile_groups"])
        if source_group_id and group is None and source_group_id not in worldbook_group_ids:
            return Outcome.failed("legacy_group_not_found", "V1 中没有这条群互动记录，请重新检查来源。")
        scope_kind = getattr(context.scope.kind, "value", context.scope.kind)
        document.update({
            "v1_root": str(root),
            "findings": [item.document() for item in planned.unwrap()],
            "source_group_id": source_group_id,
            "target_group_id": context.scope.native_scope_id if scope_kind == "group" else "",
            "group_target_available": scope_kind == "group",
            "requires_member_id_confirmation": bool(group
                or document["worldbook"].get("requires_id_confirmation")),
            "target_private_available": getattr(context.scope.kind, "value", context.scope.kind) == "private",
            "worldbook_state": worldbook_state,
            "plan_token": self._data_plan_token(data_plan, context, service, source_group_id,
                                                source_persona_id, existing_ids, existing_heads,
                                                worldbook_state, archive_plan.inventory_fingerprint,
                                                memory_plan.fingerprint, memory_owner_ids,
                                                memory_group_mappings, reference_state, expression_state,
                                                contact_state,
                                                relationship_state=relationship_token,
                                                relationship_seed=relationship_seed_token),
        })
        document["worldbook"]["available_count"] = (
            document["worldbook"]["general_count"]
            + document["worldbook"]["member_count"]
            + (document["worldbook"]["selected_group_entry_count"]
               + document["worldbook"]["selected_group_member_profile_count"]
               if scope_kind == "group" else 0))
        document["worldbook"]["selected_group_member_count"] = (
            document["worldbook"]["selected_group_member_profile_count"]
            if scope_kind == "group" else 0)
        document["worldbook"]["requires_id_confirmation"] = (
            document["worldbook"]["requires_id_confirmation"]
            or bool(group) or bool(document["worldbook"]["selected_group_member_count"]))
        document["worldbook"].update({
            "selected_group_target_available": scope_kind == "group",
            "base_state": worldbook_state.get("base_state", "absent"),
            "group_state": worldbook_state.get("group_state", "absent"),
            "base_entries_matches": worldbook_state.get("base_entries_matches", False),
            "base_members_matches": worldbook_state.get("base_members_matches", False),
            "group_matches": worldbook_state.get("group_matches", False),
            "group_member_count": worldbook_state.get("group_member_count", 0),
        })
        return Outcome.success(document)

    @staticmethod
    def _memory_database_path(v1_root):
        return plugin_data_path(v1_root, MEMORY_PLUGIN_DATA_NAME + "/memory_companion.db")

    def _expression_state(self, data_plan, context, source_group_id):
        from companion_v2.legacy.expression_data import selected_profile
        from companion_v2.expression.legacy_import import preview
        profiles = getattr(data_plan, "expression_profiles", ())
        profile = selected_profile(profiles, context, source_group_id)
        return {**preview(self.controller.runtime, profile, context), "sources": [
            {"source_kind": item["source_kind"], "source_id": item["source_id"],
             "rule_count": len(item["rules"]), "sample_count": len(item["samples"])} for item in profiles]}

    def _memory_source_plan(self, root):
        """Reuse the checked source inventory while its file snapshot is unchanged."""
        from companion_v2.legacy.memory_archive import _historical_inventory
        path = self._memory_database_path(root)
        files, issues = _historical_inventory(path.parent, excluded=set())
        signature = digest([[name, info] for name, _, info in files] + list(issues))
        owner = getattr(self.controller, "controller", self.controller)
        cache = getattr(owner, "_legacy_memory_source_plans", None)
        if cache is None:
            cache = {}
            owner._legacy_memory_source_plans = cache
        key = str(path.resolve())
        previous = cache.get(key)
        if previous and previous[0] == signature:
            return previous[1]
        plan = build_memory_database_plan(path)
        cache[key] = (signature, plan)
        return plan

    @staticmethod
    def _memory_target(context):
        return {
            "installation_id": context.scope.installation_id,
            "bot_id": context.scope.bot_id,
            "role_id": context.role_binding.role.subject_id,
            "actor_id": context.actor.subject.subject_id,
            "platform": context.scope.platform,
            "account_id": context.scope.account_id,
            "scope_kind": "private",
            "native_scope_id": context.scope.native_scope_id,
        }

    def _memory_group_target_inventory(self):
        web_api = getattr(self.controller, "web_api", None)
        inventory = web_api.target_inventory() if callable(getattr(web_api, "target_inventory", None)) else ()
        if not inventory:
            settings = getattr(self.controller, "settings", None)
            if settings is not None:
                from .group_settings import GroupTarget
                inventory = [{"target_id": target.target_id, "platform_id": target.platform_id,
                    "account_id": target.account_id, "bot_id": target.bot_id,
                    "group_id": target.group_id,
                    "scope_kind": "group" if isinstance(target, GroupTarget) else "private",
                    "roles": [{"role_id": persona.role_id, "role_name": persona.role_id,
                               "persona_ids": [persona.persona_id], "group_memory_api": True}
                              for persona in settings.personas if persona.bot_id == target.bot_id]}
                    for target in settings.all_targets]
        return [{"target_id": item.get("target_id"), "platform_id": item.get("platform_id"),
                 "account_id": item.get("account_id"), "bot_id": item.get("bot_id"),
                 "group_id": item.get("group_id"), "roles": [
                     {"role_id": role.get("role_id"), "role_name": role.get("role_name"),
                      "persona_ids": role.get("persona_ids", [])}
                     for role in item.get("roles", ()) if role.get("group_memory_api")]
                } for item in inventory if item.get("scope_kind") == "group"
                and any(role.get("group_memory_api") for role in item.get("roles", ()))]

    def _resolve_memory_group_bindings(self, session, snapshot_path, mappings):
        if not mappings:
            return ()
        inventory = session.application.memory.query.legacy.inspect_snapshot(snapshot_path)
        owners = {item["owner_id"]: item for item in inventory.get("owners", ())}
        targets = {item["target_id"]: item for item in self._memory_group_target_inventory()}
        settings = getattr(self.controller, "settings", None)
        normalized = []
        for mapping in mappings:
            owner = owners.get(mapping["owner_id"])
            target = targets.get(mapping["target_id"])
            if owner is None:
                raise ValueError("legacy_memory_owner_stale")
            source = LegacyOwnerSelector(**owner["source"])
            if source.scope != "group":
                raise ValueError("legacy_memory_owner_scope_mismatch")
            if target is None:
                raise ValueError("legacy_memory_group_target_invalid")
            if source.group_id != target.get("group_id"):
                raise ValueError("legacy_memory_group_number_mismatch")
            role_id = mapping["role_id"]
            if not any(persona.bot_id == target.get("bot_id") and persona.role_id == role_id
                       for persona in getattr(settings, "personas", ())):
                raise ValueError("legacy_memory_group_role_invalid")
            normalized.append(LegacyMemoryBinding(
                source=source,
                target_installation_id=session.application.runtime.installation_id,
                target_bot_id=target["bot_id"], target_role_id=role_id,
                target_actor_id="", target_platform=target["platform_id"],
                target_account_id=target["account_id"], target_scope_kind="group",
                target_native_scope_id=target["group_id"]))
        if len({item.source.key() for item in normalized}) != len(normalized):
            raise ValueError("legacy_memory_group_mapping_duplicate")
        return tuple(normalized)

    def _memory_projection(self, session, memory_plan, context, memory_owner_ids,
                           memory_group_mappings=()):
        from companion_v2.foundation.refs import ScopeKind

        if not memory_plan.fingerprint or not any(
                asset.relative_path == "memory_companion.db" for asset in memory_plan.assets):
            return Outcome.success({"state": "unavailable", "reason": "snapshot_unavailable",
                                    "imported": {"memories": 0, "timeline": 0},
                                    "excluded": {}, "owner_inventory": {}})
        if memory_owner_ids and context.scope.kind is not ScopeKind.PRIVATE:
            return Outcome.failed("legacy_memory_private_target_required",
                                  "历史私聊记忆只能绑定到 V2 私聊身份。")
        try:
            group_bindings = self._resolve_memory_group_bindings(
                session, memory_plan.path, memory_group_mappings)
            lineage_id = session.application.memory.query.legacy.lineage_id(memory_plan.path)
            planned = session.application.memory.query.legacy.plan_snapshot(
                memory_plan.path, source_fingerprint=memory_plan.fingerprint,
                owner_ids=memory_owner_ids,
                target=self._memory_target(context) if memory_owner_ids else None,
                bindings=group_bindings, lineage_id=lineage_id, destination_scoped=True)
        except ValueError as error:
            code = str(error)
            if code.startswith(("legacy_memory_owner_", "legacy_memory_group_")):
                return Outcome.failed(code, "所选旧身份或目标群映射已变化，请重新读取来源并试算。")
            return Outcome.success({"state": "unavailable", "reason": "schema_unsupported",
                                    "imported": {"memories": 0, "timeline": 0},
                                    "excluded": {}, "owner_inventory": {}})
        return Outcome.success({"state": "ready", **planned})

    @staticmethod
    def _worldbook_source_id(source_persona_id, source_group_id="", category="entries"):
        persona_key = digest(source_persona_id or "primary")[:20]
        if source_group_id:
            return f"legacy-v1-worldbook-group-{persona_key}-{digest(source_group_id)[:16]}"
        return f"legacy-v1-worldbook-{category}-{persona_key}"

    @staticmethod
    def _worldbook_digest(entries, members):
        return digest({"entries": [item.document for item in entries],
                       "members": [item.document for item in members]})

    def _worldbook_state(self, data_plan, context, source_group_id):
        from companion_v2.foundation.refs import ScopeKind
        from companion_v2.knowledge.catalog import KnowledgeCatalog, SourceBinding
        from companion_v2.legacy.data_migration import map_legacy_worldbook

        role_id = context.role_binding.role.subject_id
        installation_id = context.scope.installation_id
        catalog = KnowledgeCatalog(self.controller.runtime)
        entries, members, group_entries, group_members = map_legacy_worldbook(
            data_plan, source_group_id=source_group_id, confirm_member_ids=True,
            include_group=context.scope.kind is ScopeKind.GROUP)
        base_binding = SourceBinding(installation_id, role_id=role_id, persona_id=role_id)
        entry_version = catalog.get(base_binding,
            self._worldbook_source_id(data_plan.source_persona_id, category="entries"))
        member_version = catalog.get(base_binding,
            self._worldbook_source_id(data_plan.source_persona_id, category="members"))
        group = None
        if source_group_id and context.scope.kind is ScopeKind.GROUP:
            group_binding = SourceBinding(installation_id, role_id=role_id, persona_id=role_id,
                native_scope_ids=(context.scope.native_scope_id,), scope_kind=ScopeKind.GROUP,
                scope_route=self._group_scope_route(context.scope))
            group_id = self._worldbook_source_id(data_plan.source_persona_id, source_group_id)
            group = catalog.get(group_binding, group_id)
        entry_digest = self._worldbook_digest(entries, ()) if entries else ""
        member_digest = self._worldbook_digest((), members) if members else ""
        group_digest = self._worldbook_digest(group_entries, group_members) \
            if group_entries or group_members else ""
        entry_conflict = bool(entries and entry_version and entry_version.code != "legacy_v1_imported")
        member_conflict = bool(members and member_version and member_version.code != "legacy_v1_imported")
        group_conflict = bool((group_entries or group_members) and group
                              and group.code != "legacy_v1_imported")
        return {
            "base_state": "conflict" if entry_conflict or member_conflict else
                "present" if entry_version or member_version else "absent",
            "base_entries_conflict": entry_conflict,
            "base_members_conflict": member_conflict,
            "base_entries_matches": bool(entries and entry_version and entry_version.active
                                          and entry_version.digest == entry_digest),
            "base_members_matches": bool(members and member_version and member_version.active
                                          and member_version.digest == member_digest),
            "base_entries_revision": entry_version.revision if entry_version else 0,
            "base_entries_digest": entry_version.digest if entry_version else "",
            "base_members_revision": member_version.revision if member_version else 0,
            "base_members_digest": member_version.digest if member_version else "",
            "group_state": "conflict" if group_conflict else
                "present" if group else "absent",
            "group_conflict": group_conflict,
            "group_matches": bool((group_entries or group_members) and group and group.active
                                   and group.digest == group_digest),
            "group_member_count": len(group_members),
            "group_revision": group.revision if group else 0,
            "group_digest": group.digest if group else "",
        }

    @staticmethod
    def _group_scope_route(scope):
        values = tuple(getattr(scope, name, None) for name in ("platform", "bot_id", "account_id"))
        return values if all(type(value) is str and value.strip() for value in values) else None

    @staticmethod
    def _journal_heads(data_plan, session, context):
        if not data_plan.journals:
            return {}
        from companion_v2.role_work.journal.models import JournalKey, JournalKind

        service = getattr(getattr(session, "application", None), "role_journal", None)
        if service is None:
            raise TaskError("legacy_journal_service_unavailable", "V2 journal storage is not initialized")
        semantic_service = getattr(session.application, "semantics", None)
        if semantic_service is None:
            raise TaskError("legacy_data_service_unavailable", "V2 data services are not initialized")
        wanted = {(item.kind, item.local_date) for item in data_plan.journals}
        found = {}
        from companion_v2.foundation.refs import RoleOwnerRef
        owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                             context.role_binding.role.subject_id)
        for kind in (JournalKind.DIARY, JournalKind.DREAM):
            remaining = {day for item_kind, day in wanted if item_kind == kind.value}
            if not remaining:
                continue
            after_date = None
            last_needed = max(remaining)
            while remaining:
                page = service.list_days(owner, kind=kind, limit=100, after_date=after_date,
                                         include_deleted=True)
                if not page:
                    break
                for head in page:
                    key = (kind.value, head.key.local_date)
                    if key[1] in remaining:
                        found[key] = {"state": head.state.value, "revision": head.revision}
                        remaining.remove(key[1])
                after_date = page[-1].key.local_date
                if after_date >= last_needed or len(page) < 100:
                    break
        for item in data_plan.journals:
            head = found.get(item.key)
            if head is not None:
                operation_id = LegacyMigrationWebAPI._legacy_journal_operation_id(
                    data_plan, context, semantic_service, item)
                receipt = service.operation_receipt(operation_id, owner=owner)
                head["imported"] = bool(receipt and receipt.head.revision == head["revision"]
                                        and receipt.head.state.value == "active")
                head["content_matches"] = False
                if head["state"] == "active":
                    # A changed catalog fingerprint does not make identical
                    # writing a conflict. Compare the current owner's content;
                    # never manufacture a migration receipt or replace an edit.
                    try:
                        entry = service.get(JournalKey(owner, JournalKind(item.kind), item.local_date))
                        head["content_matches"] = all(
                            getattr(entry.draft, field) == getattr(item, field)
                            for field in ("body", "summary", "afterglow", "form"))
                    except TaskError:
                        pass  # Unavailable source ancestry is preserved, never overwritten.
                if item.dream_fragments and head["state"] == "active" and head["imported"]:
                    derivative_operation = LegacyMigrationWebAPI._legacy_journal_derivative_operation_id(
                        data_plan, context, semantic_service, item)
                    derivative_receipt = service.operation_receipt(derivative_operation, owner=owner)
                    key = JournalKey(owner, JournalKind(item.kind), item.local_date)
                    derivatives = service.derivatives(key)
                    expected_derivatives = (derivatives is not None
                        and derivative_receipt is not None
                        and derivative_receipt.head.revision == head["revision"]
                        and derivatives.material.parent.revision == head["revision"]
                        and derivatives.material.summary == item.summary
                        and derivatives.material.dream_fragments == item.dream_fragments)
                    head["derivatives_imported"] = expected_derivatives
                    head["derivatives_conflict"] = derivatives is not None and not expected_derivatives
                else:
                    head["derivatives_imported"] = not item.dream_fragments
                    head["derivatives_conflict"] = False
        return found

    @staticmethod
    def _legacy_journal_operation_id(data_plan, context, semantic_service, item):
        return "legacy-journal-" + digest(["v1-role-journal.v2", data_plan.source_persona_id,
            semantic_service.owner(context),
            item.kind, item.local_date, item.sha256])[:48]

    @staticmethod
    def _legacy_journal_derivative_operation_id(data_plan, context, semantic_service, item):
        return "legacy-journal-derivatives-" + digest(["v1-role-journal.v2", data_plan.source_persona_id,
            semantic_service.owner(context), item.kind, item.local_date, item.sha256])[:40]

    @staticmethod
    def _reaction_ids(service, owner):
        found = set()
        cursor = ""
        while True:
            page = service.store.list(owner, "reaction", after=cursor, limit=256)
            found.update(item["id"] for item in page)
            if len(page) < 256:
                return found
            cursor = page[-1]["id"]

    async def _data_apply(self, migration, root, session, plan_token, source_group_id,
                          source_persona_id, import_reactions, import_group_network,
                          confirm_member_ids, import_journals, confirm_persona_mapping,
                          import_worldbook, import_archive=False, import_memory_database=False,
                          memory_owner_ids=(), confirm_memory_mapping=False, memory_group_mappings=(),
                          import_reference_images=False, import_wardrobe_items=False,
                          confirm_reference_persona_mapping=False,
                          import_expressions=False, confirm_expression_mapping=False, import_private_contacts=False,
                          import_relationship_profiles=False):
        return await asyncio.to_thread(
            self._data_apply_sync, migration, root, session, plan_token, source_group_id,
            source_persona_id, import_reactions, import_group_network, confirm_member_ids,
            import_journals, confirm_persona_mapping, import_worldbook, import_archive,
            import_memory_database, memory_owner_ids, confirm_memory_mapping, memory_group_mappings,
            import_reference_images, import_wardrobe_items, confirm_reference_persona_mapping,
            import_expressions, confirm_expression_mapping, import_private_contacts,
            import_relationship_profiles)

    def _data_apply_sync(self, migration, root, session, plan_token, source_group_id,
                         source_persona_id, import_reactions, import_group_network,
                         confirm_member_ids, import_journals, confirm_persona_mapping,
                         import_worldbook, import_archive=False, import_memory_database=False,
                         memory_owner_ids=(), confirm_memory_mapping=False, memory_group_mappings=(),
                         import_reference_images=False, import_wardrobe_items=False,
                         confirm_reference_persona_mapping=False,
                         import_expressions=False, confirm_expression_mapping=False, import_private_contacts=False,
                         import_relationship_profiles=False):
        found = migration.discover()
        if not found.is_success:
            return found
        data_plan = build_data_plan(root, source_persona_id=source_persona_id,
                                    v1_configuration=migration.v1_configuration)
        runtime_root = getattr(self.controller, "data_root", None)
        archive_plan = build_v1_archive_plan(
            root, exclude_roots=(runtime_root,) if runtime_root is not None else ())
        memory_plan = self._memory_source_plan(root)
        if (not isinstance(memory_owner_ids, (tuple, list)) or len(memory_owner_ids) > MAX_OWNER_BINDINGS
                or any(type(value) is not str or len(value) != 64
                       or any(char not in "0123456789abcdef" for char in value)
                       for value in memory_owner_ids)
                or len(set(memory_owner_ids)) != len(memory_owner_ids)
                or type(confirm_memory_mapping) is not bool):
            return Outcome.failed("legacy_request_invalid", "独立记忆身份映射无效，请重新试算。")
        memory_owner_ids = tuple(sorted(memory_owner_ids))
        try:
            memory_group_mappings = self._normalize_memory_group_mappings(list(memory_group_mappings))
        except ValueError:
            return Outcome.failed("legacy_request_invalid", "独立记忆群身份映射无效，请重新试算。")
        context, service = self._data_context(session)
        owner = service.ensure_reaction_catalog(context)
        existing_ids = self._reaction_ids(service, owner)
        existing_heads = self._journal_heads(data_plan, session, context)
        worldbook_state = self._worldbook_state(data_plan, context, source_group_id)
        reference_state = self._reference_import_state(session, context)
        expression_state = self._expression_state(data_plan, context, source_group_id)
        from companion_v2.memory.person.legacy_import import contact_plan, import_contacts
        contact_state = contact_plan(self.controller.runtime, context, data_plan.private_contacts)
        from companion_v2.identity_policy.legacy_import import authorization_plan, import_authorizations
        contact_state["authorization"] = authorization_plan(
            self.controller.runtime, context, data_plan.private_contacts, self.controller.now())
        from companion_v2.relationships.legacy_import import (
            import_relationship_profiles as import_legacy_relationship_profiles,
            relationship_profile_entries, relationship_profile_token, relationship_seed_state)
        relationship_seed = self._relationship_config_seed(self._relationship_seed_configuration(migration))
        relationship_entries = relationship_profile_entries(
            self.controller.runtime, context, data_plan.relationship_profiles,
            seed=relationship_seed)
        relationship_token = relationship_profile_token(relationship_entries)
        relationship_seed_token = relationship_seed_state(relationship_seed)
        if plan_token != self._data_plan_token(data_plan, context, service, source_group_id,
                                               source_persona_id, existing_ids, existing_heads,
                                               worldbook_state, archive_plan.inventory_fingerprint,
                                               memory_plan.fingerprint, memory_owner_ids,
                                               memory_group_mappings, reference_state, expression_state,
                                               contact_state,
                                               relationship_state=relationship_token,
                                               relationship_seed=relationship_seed_token):
            return Outcome.failed("legacy_plan_stale", "V1 来源或 V2 目标在预览后发生变化，请重新试算。")
        from companion_v2.foundation.refs import ScopeKind
        if import_private_contacts and context.scope.kind is not ScopeKind.PRIVATE:
            return Outcome.failed("legacy_contacts_private_target_required", "请选择这些用户所属的私聊角色。")
        if import_relationship_profiles and context.scope.kind is not ScopeKind.PRIVATE:
            return Outcome.failed("legacy_relationship_private_target_required",
                "人工关系资料只能写入这些用户所属的私聊角色。")
        if import_expressions and (not expression_state["available"] or not confirm_expression_mapping):
            return Outcome.failed("legacy_expression_mapping_required",
                "请确认旧版表达来源与当前角色及同一私聊对象或群的对应关系。")
        if (memory_owner_ids or memory_group_mappings) and (not import_memory_database or not confirm_memory_mapping):
            return Outcome.failed("legacy_memory_mapping_confirmation_required",
                "请先选择独立记忆库迁移，并确认所选旧身份映射。")
        if memory_owner_ids and context.scope.kind is not ScopeKind.PRIVATE:
            return Outcome.failed("legacy_memory_private_target_required",
                                  "历史私聊记忆只能绑定到 V2 私聊身份。")
        if import_reference_images or import_wardrobe_items:
            if not reference_state.get("registered"):
                return Outcome.failed("legacy_reference_service_unavailable",
                    "V2 参考图库服务尚未启动，无法迁入参考图或衣柜。")
            if context.scope.kind is not ScopeKind.PRIVATE:
                return Outcome.failed("legacy_reference_private_target_required",
                    "V1 人设参考图和衣柜只能迁入已选择的人格私聊角色。")
            if confirm_reference_persona_mapping is not True:
                return Outcome.failed("legacy_reference_mapping_confirmation_required",
                    "请确认所选 V1 人格的参考图与衣柜归属于当前 V2 私聊角色。")
        if (not import_reactions and not import_group_network and not import_journals
                and not import_worldbook and not import_archive and not import_memory_database
                and not import_reference_images and not import_wardrobe_items and not import_expressions
                and not import_private_contacts and not import_relationship_profiles):
            return Outcome.failed("legacy_nothing_to_import", "请选择至少一种要导入的数据。")

        archive_result = None
        memory_archive_result = None
        memory_materials_result = None
        if import_memory_database:
            try:
                archive_root = migration_archive_root(self.controller, root)
                memory_archive_result = copy_memory_database_archive(memory_plan, archive_root)
            except Exception as error:  # noqa: BLE001 - keep source paths and memory content private
                code = str(error) if str(error).startswith("memory_archive_") else "memory_archive_failed"
                return Outcome.failed(code,
                    "独立记忆库快照未完成；V2 领域数据尚未写入，请重新试算后重试。")
            if memory_owner_ids or memory_group_mappings:
                try:
                    snapshot = (archive_root / memory_archive_result["archive_id"]
                                / "files" / "memory_companion.db")
                    group_bindings = self._resolve_memory_group_bindings(
                        session, snapshot, memory_group_mappings)
                    memory_materials_result = session.application.memory.query.legacy.import_snapshot(
                        snapshot, source_fingerprint=memory_plan.fingerprint, owner_ids=memory_owner_ids,
                        target=self._memory_target(context) if memory_owner_ids else None,
                        bindings=group_bindings,
                        lineage_id=session.application.memory.query.legacy.lineage_id(memory_plan.path),
                        destination_scoped=True)
                except Exception as error:  # noqa: BLE001 - retain the archived snapshot; omit its contents
                    code = str(error) if str(error).startswith("legacy_memory_") else "legacy_memory_import_failed"
                    return Outcome.failed(code,
                        "只读快照已归档，但历史私聊资料未写入 V2 查询库；其他迁移项目尚未写入。")
            else:
                memory_materials_result = {
                    "state": "archive_only", "selected_owner_count": 0,
                    "imported": {"memories": 0, "timeline": 0},
                    "reason": "identity_mapping_not_selected",
                    "source_archive_retained": True,
                }
        if import_archive:
            try:
                archive_root = migration_archive_root(self.controller, root)
                archive_result = copy_v1_archive(archive_plan, archive_root, allow_incomplete=True)
            except Exception as error:  # noqa: BLE001 - do not expose source paths or content
                # 保留原始异常，否则长路径/权限/磁盘错误都会被折叠成同一句，
                # 用户和日志都无从定位。
                logger.warning("[CompanionV2] 旧版资料归档失败 error_type=%s detail=%s",
                               type(error).__name__, logger_safe_detail(str(error)))
                code = str(error) if str(error).startswith("archive_") else "legacy_archive_failed"
                if code == "legacy_archive_failed" and getattr(error, "winerror", None) in (206, 3):
                    code = "archive_path_too_long"
                return Outcome.failed(code,
                    "V1 只读资料归档未完成；V2 领域数据尚未写入，请重新试算后重试。")

        if import_worldbook and (worldbook_state["base_entries_conflict"]
                                 or worldbook_state["base_members_conflict"]
                                 or worldbook_state["group_conflict"]):
            return Outcome.failed("legacy_worldbook_target_conflict",
                "同名知识来源已由其他功能登记，请先在知识目录检查，迁移没有写入其他数据。")

        selected = None
        if import_group_network:
            from companion_v2.foundation.refs import ScopeKind
            selected = next((item for item in data_plan.group_networks
                             if item["source_group_id"] == source_group_id), None)
            if selected is None:
                return Outcome.failed("legacy_group_not_found", "V1 中没有这条群互动记录，请重新试算。")
            if context.scope.kind is not ScopeKind.GROUP:
                return Outcome.failed("legacy_group_target_required", "群成员互动图必须选择一个 V2 群目标与角色。")
            if confirm_member_ids is not True:
                return Outcome.failed("legacy_member_mapping_confirmation_required",
                                      "请确认旧成员 ID 与目标平台账号仍一致。")

        if import_journals:
            from companion_v2.foundation.refs import ScopeKind
            if context.scope.kind is not ScopeKind.PRIVATE:
                return Outcome.failed("legacy_journal_private_target_required",
                                      "日记与梦境只能导入到当前 V2 私聊角色。")
            if confirm_persona_mapping is not True:
                return Outcome.failed("legacy_persona_mapping_confirmation_required",
                                      "请确认所选 V1 人格的日记与梦境属于当前 V2 角色。")
            if not data_plan.journals:
                return Outcome.failed("legacy_journal_nothing_to_import", "所选 V1 人格没有可导入的日记或梦境。")

        if import_worldbook and source_group_id:
            mapped_group_ids = {group_id for item in data_plan.member_profiles
                                for group_id in item.group_ids}
            mapped_group_ids.update(group_id for item in data_plan.worldbook_items
                                    for group_id in item.group_ids)
            if source_group_id not in mapped_group_ids and not selected:
                return Outcome.failed("legacy_group_not_found", "V1 中没有这条群资料或成员线索，请重新试算。")

        batch = next_reaction_batch(data_plan, existing_ids) if import_reactions else ()
        created, duplicates, errors = [], [], []
        from astrbot_host.media import inspect_image

        for item in batch:
            try:
                content = load_reaction_asset(root, item)
                image = inspect_image(content, 0)
                metadata = {**item.metadata, "legacy_source_id": "private.reaction_assets",
                            "legacy_filename": item.filename}
                imported, was_created = service.reactions.import_image_with_status(
                    owner, image, metadata, collection_source="legacy_migration",
                    enabled=item.enabled)
                (created if was_created else duplicates).append(imported["id"])
            except Exception as error:  # noqa: BLE001 - report per asset and continue idempotently
                errors.append({"filename": item.filename,
                               "reason": getattr(error, "code", type(error).__name__)})

        journal_batch = next_journal_batch(data_plan, existing_heads) if import_journals else ()
        journal_created, journal_errors, journal_derivatives_created = [], [], 0
        if journal_batch:
            from companion_v2.role_work.journal import (DreamRecall, JournalContext, JournalDerivatives,
                JournalDraft, JournalKey, JournalKind, JournalOrigin, JournalRef)
            journal_service = session.application.role_journal
            from companion_v2.foundation.refs import RoleOwnerRef
            owner_ref = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                                     context.role_binding.role.subject_id)
            for item in journal_batch:
                try:
                    kind = JournalKind.DIARY if item.kind == "diary" else JournalKind.DREAM
                    key = JournalKey(owner_ref, kind, item.local_date)
                    imported_head = existing_heads.get(item.key)
                    if imported_head is None:
                        journal_context = JournalContext(key, context.scope, "Asia/Shanghai")
                        if kind is JournalKind.DIARY:
                            draft = JournalDraft(journal_context, body=item.body, summary=item.summary,
                                form=item.form, origin=JournalOrigin.MANUAL)
                        else:
                            recall = DreamRecall.FULL if item.body.strip() else DreamRecall.FRAGMENTS
                            draft = JournalDraft(journal_context, body=item.body, summary=item.summary,
                                afterglow=item.afterglow, form=item.form, recall=recall,
                                origin=JournalOrigin.MANUAL)
                        operation_id = self._legacy_journal_operation_id(data_plan, context, service, item)
                        receipt = journal_service.save(draft, expected_revision=0,
                            operation_id=operation_id, observed_at=self.controller.now())
                        current_head = receipt.head
                        journal_created.append(receipt.head.key.local_date)
                    else:
                        if imported_head.get("imported") is not True:
                            continue
                        current_head = journal_service.head(key)
                        if (current_head.state.value != "active"
                                or current_head.revision != imported_head.get("revision")):
                            journal_errors.append({"kind": item.kind, "date": item.local_date,
                                "reason": "journal_changed_after_preview"})
                            continue
                    if item.dream_fragments and imported_head is None or (
                            item.dream_fragments and imported_head is not None
                            and imported_head.get("derivatives_imported") is not True
                            and imported_head.get("derivatives_conflict") is not True):
                        material = JournalDerivatives(JournalRef(key, current_head.revision),
                            summary=item.summary, dream_fragments=item.dream_fragments)
                        derivative_operation = self._legacy_journal_derivative_operation_id(
                            data_plan, context, service, item)
                        journal_service.save_derivatives(material, expected_revision=0,
                            operation_id=derivative_operation, observed_at=self.controller.now())
                        journal_derivatives_created += 1
                except (TaskError, TypeError, ValueError) as error:
                    journal_errors.append({"kind": item.kind, "date": item.local_date,
                        "reason": getattr(error, "code", type(error).__name__)})

        reference_result = None
        if import_reference_images or import_wardrobe_items:
            reference_result = self._import_legacy_reference_images(
                root, data_plan, session, context, reference_state,
                include_references=import_reference_images, include_wardrobe=import_wardrobe_items)

        group_result = None
        if import_group_network:
            source_key = digest(["v1-group-network-import.v1", source_group_id,
                                 selected["members"], selected["edges"]])
            try:
                group_result = session.application.messages.group_network.import_legacy_aggregate(
                    context, source_key=source_key, members=selected["members"],
                    edges=selected["edges"], at=self.controller.now())
            except TaskError as error:
                errors.append({"source_id": "private.group_relationship_graph",
                               "reason": error.code})

        worldbook_result = None
        if import_worldbook:
            worldbook_result = self._import_legacy_worldbook(data_plan, context, source_group_id,
                confirm_member_ids=confirm_member_ids)
            if worldbook_result.get("errors"):
                errors.extend(worldbook_result["errors"])

        expression_result = None
        if import_expressions:
            from companion_v2.legacy.expression_data import selected_profile
            from companion_v2.expression.legacy_import import import_profile
            expression_result = import_profile(self.controller.runtime,
                selected_profile(data_plan.expression_profiles, context, source_group_id), context,
                self.controller.now(), expected_token=expression_state["token"])

        contact_result = import_contacts(self.controller.runtime, context, data_plan.private_contacts,
            self.controller.now()) if import_private_contacts else None
        if contact_result is not None:
            authorization = import_authorizations(self.controller.runtime, context,
                data_plan.private_contacts, self.controller.now())
            contact_result["authorization"] = authorization
            contact_result["permissions_restored"] = bool(authorization["permissions_restored"])

        relationship_result = import_legacy_relationship_profiles(
            self.controller.runtime, context, relationship_entries, self.controller.now()
        ) if import_relationship_profiles else None

        refreshed_ids = self._reaction_ids(service, owner)
        remaining = sum(1 for item in data_plan.reaction_assets if item.sha256 not in refreshed_ids)
        refreshed_heads = self._journal_heads(data_plan, session, context)
        journal_remaining = sum(1 for item in data_plan.journals
            if item.key not in refreshed_heads or (item.dream_fragments
                and refreshed_heads[item.key].get("imported") is True
                and refreshed_heads[item.key].get("derivatives_imported") is not True
                and refreshed_heads[item.key].get("derivatives_conflict") is not True))
        journal_derivatives_remaining = sum(1 for item in data_plan.journals
            if item.dream_fragments and item.key in refreshed_heads
            and refreshed_heads[item.key].get("imported") is True
            and refreshed_heads[item.key].get("derivatives_imported") is not True
            and refreshed_heads[item.key].get("derivatives_conflict") is not True)
        return Outcome.success({
            "v1_root": str(root),
            "reaction_created": len(created),
            "reaction_duplicates": len(duplicates),
            "reaction_remaining": remaining,
            "reaction_errors": errors,
            "journal_created": len(journal_created),
            "journal_remaining": journal_remaining,
            "journal_derivatives_created": journal_derivatives_created,
            "journal_derivatives_remaining": journal_derivatives_remaining,
            "journal_errors": journal_errors,
            "reference_images": reference_result,
            "expressions": expression_result,
            "private_contacts": contact_result,
            "relationship_profiles": relationship_result,
            "group_network": group_result,
            "worldbook": worldbook_result,
            "archive": archive_result,
            "memory_archive": memory_archive_result,
            "memory_materials": memory_materials_result,
            "errors": errors + journal_errors + (reference_result.get("errors", []) if reference_result else []),
            "source_persona_id": source_persona_id,
            "idempotent": True,
        })

    def _import_legacy_reference_images(self, root, data_plan, session, context, state,
                                        *, include_references, include_wardrobe):
        from companion_v2.conversation.media.models import ImageDescriptor
        from companion_v2.foundation.refs import ScopeKind
        from companion_v2.references.management import ReferenceMaterialManagement
        from companion_v2.references.repositories import ReferenceAssetRepository
        from companion_v2.references.models import ReferenceState
        from companion_v2.legacy.data_migration import load_reference_image, next_reference_image_batch
        from .media import inspect_image

        result = {"reference_created": 0, "reference_roles_added": 0,
                  "reference_remaining": 0, "wardrobe_created": 0,
                  "wardrobe_remaining": 0, "wardrobe_images_skipped": 0, "errors": []}
        if context.scope.kind is not ScopeKind.PRIVATE:
            result["errors"].append({"source_id": "private.photo_references",
                                     "reason": "legacy_reference_private_target_required"})
            return result
        at = self.controller.now()
        operator = self._operator()
        manager = ReferenceMaterialManagement(session.application)
        owner = manager._admit(operator, context, at)
        repository: ReferenceAssetRepository = manager.repository
        existing_hashes = set(state["hashes"])
        existing_wardrobe_ids = set(state["wardrobe_ids"])
        wardrobe_batch = ([item for item in data_plan.wardrobe_items
                          if item.item_id not in existing_wardrobe_ids][:state["wardrobe_slots"]]
                          if include_wardrobe else [])

        by_hash = {item.sha256: item for item in data_plan.reference_images}
        for wardrobe in data_plan.wardrobe_items:
            image = wardrobe.image
            if image is None:
                continue
            previous = by_hash.get(image.sha256)
            if previous is None:
                by_hash[image.sha256] = image
            else:
                by_hash[image.sha256] = replace(
                    previous, roles=tuple(dict.fromkeys((*previous.roles, *image.roles))))
        wardrobe_hashes = {item.image.sha256 for item in wardrobe_batch if item.image is not None}
        ready = [item for item in by_hash.values() if item.sha256 not in existing_hashes
                 and (include_references or item.sha256 in wardrobe_hashes)]
        priority = {item.image.sha256 for item in wardrobe_batch if item.image is not None}
        batch = next_reference_image_batch(ready, slots_available=state["reference_slots"],
            bytes_available=state["reference_bytes"], priority_hashes=priority)
        for item in batch:
            try:
                content = load_reference_image(root, item)
            except Exception as error:  # noqa: BLE001 - keep V1 paths and image bytes private
                result["errors"].append({"filename": item.filename,
                    "reason": getattr(error, "code", str(error) or type(error).__name__)})
                continue
            try:
                captured = inspect_image(content, 0)
                if type(captured.descriptor) is not ImageDescriptor:
                    raise TaskError("reference_image_animated", "animated images cannot be registered as reference material")
                roles = tuple(ReferenceRole(role) for role in item.roles)
                if not roles:
                    roles = (ReferenceRole.IDENTITY,)
                identifier = "v1_ref_" + digest([owner, item.source_key, item.sha256])[:40]
                asset = ReferenceAsset(asset_id=identifier, kind=ReferenceKind.REFERENCE_IMAGE,
                    role=roles[0], content_hash=ContentDigest(item.sha256), size_bytes=len(content),
                    mime_type=captured.descriptor.mime_type,
                    media_ref=OWNED_REFERENCE_PREFIX + "legacy-v1:" + identifier,
                    source=ReferenceSource.OPERATOR, label=item.label[:120],
                    created_at=at, updated_at=at)
                existing = repository.get(owner, identifier)
                if existing is None:
                    with self.controller.runtime.transaction():
                        repository.put(owner, asset, created_at=at, payload=content)
                        for role in roles[1:]:
                            repository.add_role(owner, identifier, role, created_at=at)
                    result["reference_created"] += 1
                else:
                    if (existing.content_hash.sha256 != item.sha256 or existing.state is not ReferenceState.ACTIVE
                            or repository.get_payload(owner, identifier) != content):
                        raise TaskError("reference_operation_conflict", "this migrated reference now names different material")
                    current_roles = set(repository.roles_of(owner, identifier))
                    for role in roles:
                        if role.value not in current_roles:
                            repository.add_role(owner, identifier, role, created_at=at)
                            result["reference_roles_added"] += 1
            except (TaskError, ValueError, TypeError) as error:
                result["errors"].append({"filename": item.filename,
                    "reason": getattr(error, "code", type(error).__name__)})

        active_assets = repository.list(owner, state=ReferenceState.ACTIVE, limit=reference_limit())
        active_hash_to_id = {asset.content_hash.sha256: asset.asset_id for asset in active_assets}
        for item in wardrobe_batch:
            asset_id = None
            if item.image is not None:
                asset_id = active_hash_to_id.get(item.image.sha256)
                if asset_id is None:
                    result["wardrobe_images_skipped"] += 1
            elif item.image_reason:
                result["wardrobe_images_skipped"] += 1
            try:
                wardrobe_item = WardrobeItem(item_id=item.item_id, name=item.name[:120],
                    description=item.description[:2000], tags=tuple(sorted(item.tags[:16])),
                    asset_id=asset_id, source=ReferenceSource.OPERATOR,
                    created_at=at, updated_at=at)
                repository.put_item(owner, wardrobe_item, created_at=at)
                result["wardrobe_created"] += 1
            except (TaskError, ValueError, TypeError) as error:
                result["errors"].append({"source_id": item.source_key,
                    "reason": getattr(error, "code", type(error).__name__)})

        final_assets = self._reference_import_state(session, context)
        result["reference_remaining"] = sum(item.sha256 not in set(final_assets["hashes"])
            for item in by_hash.values() if include_references or item.sha256 in wardrobe_hashes)
        result["wardrobe_remaining"] = sum(item.item_id not in set(final_assets["wardrobe_ids"])
            for item in (data_plan.wardrobe_items if include_wardrobe else ()))
        return result

    def _import_legacy_worldbook(self, data_plan, context, source_group_id, *, confirm_member_ids):
        from companion_v2.foundation.refs import ScopeKind
        from companion_v2.knowledge.catalog import KnowledgeCatalog, SourceBinding
        from companion_v2.legacy.data_migration import map_legacy_worldbook

        include_group = context.scope.kind is ScopeKind.GROUP
        entries, members, group_entries, group_members = map_legacy_worldbook(
            data_plan, source_group_id=source_group_id,
            confirm_member_ids=confirm_member_ids, include_group=include_group)
        if not entries and not members and not group_entries and not group_members:
            return {"base_state": "unchanged", "group_state": "unchanged",
                    "entries_imported": 0, "members_imported": 0, "group_entries_imported": 0,
                    "group_members_imported": 0,
                    "member_ids_unconfirmed": sum(bool(item.member_ids) for item in data_plan.worldbook_items)
                        if not confirm_member_ids else 0,
                    "errors": []}

        catalog = KnowledgeCatalog(self.controller.runtime)
        role_id = context.role_binding.role.subject_id
        installation_id = context.scope.installation_id
        persona_id = data_plan.source_persona_id or "primary"
        locator = "v1-persona:" + persona_id[:96]
        at = self.controller.now()
        result = {"entries_imported": len(entries), "members_imported": len(members),
                  "group_entries_imported": len(group_entries),
                  "group_members_imported": len(group_members), "errors": []}

        def save(binding, source_id, current_entries, current_members):
            fingerprint = self._worldbook_digest(current_entries, current_members)
            current = catalog.get(binding, source_id)
            if current and current.code != "legacy_v1_imported":
                raise TaskError("legacy_worldbook_target_conflict", "knowledge source is already owned")
            if current and current.active and current.digest == fingerprint:
                return "unchanged"
            catalog.save(binding, source_id=source_id, locator=locator, format="legacy_worldbook",
                content_digest=fingerprint, entries=current_entries, members=current_members,
                issues=(), at=at, code="legacy_v1_imported")
            return "imported"

        try:
            if entries:
                binding = SourceBinding(installation_id, role_id=role_id, persona_id=role_id)
                result["entries_state"] = save(binding,
                    self._worldbook_source_id(data_plan.source_persona_id, category="entries"), entries, ())
            else:
                result["entries_state"] = "unchanged"
            if members:
                binding = SourceBinding(installation_id, role_id=role_id, persona_id=role_id)
                result["members_state"] = save(binding,
                    self._worldbook_source_id(data_plan.source_persona_id, category="members"), (), members)
            else:
                result["members_state"] = "unchanged"
            if (group_entries or group_members) and include_group and source_group_id:
                binding = SourceBinding(installation_id, role_id=role_id, persona_id=role_id,
                    native_scope_ids=(context.scope.native_scope_id,), scope_kind=ScopeKind.GROUP,
                    scope_route=self._group_scope_route(context.scope))
                result["group_state"] = save(binding,
                    self._worldbook_source_id(data_plan.source_persona_id, source_group_id),
                    group_entries, group_members)
            else:
                result["group_state"] = "unchanged"
        except (TaskError, TypeError, ValueError) as error:
            result["errors"].append({"source_id": "private.core_profiles.worldbook_entries",
                                     "reason": getattr(error, "code", type(error).__name__)})
        result["member_ids_unconfirmed"] = (sum(bool(item.member_ids) for item in data_plan.worldbook_items)
                                             if not confirm_member_ids else 0)
        result["group_entries_unmapped"] = sum(bool(item.group_ids) for item in data_plan.worldbook_items
            if not source_group_id or source_group_id not in item.group_ids or not include_group)
        return result

    async def _run(self, work, *, root_override=None, offload_sync=False):
        """统一取管理员身份、加载运行档、调用 work(migration, root, session)。"""
        from .web_api import outcome_response
        controller = self.controller
        async with controller._control_lock:
            try:
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                username = request.username
                if type(username) is not str or not username.strip():
                    raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load the explicit V2 profile before migrating")
                dashboard_operator(controller.runtime, username, at=controller.now())
                migration, root, origin = await self._migration(root_override, session=session)
                result = (await asyncio.to_thread(work, migration, root, session)
                          if offload_sync else work(migration, root, session))
                if inspect.isawaitable(result):
                    result = await result
                if not isinstance(result, _PendingBindingApply):
                    return outcome_response(result)
            except TaskError as error:
                return outcome_response(Outcome.failed(error.code, str(error)))
            except (TypeError, ValueError, KeyError):
                logger.warning("[CompanionV2] 旧版迁移请求参数处理失败", exc_info=True)
                return outcome_response(Outcome.failed("legacy_request_invalid", "invalid legacy arguments"))
        # Binding application owns the lifecycle/control locks and may replace
        # the runtime. All writes through the old session have finished above.
        return outcome_response(await self._finish_binding_apply(result))

    # ── 实现 ───────────────────────────────────────────────────────────
    def _discovery(self, migration, root, session, configuration_scope="all"):
        found = migration.discover()
        if not found.is_success:
            return found
        planned = migration.plan()
        if planned.is_success:
            scoped_plan = self._configuration_scoped_plan(planned.unwrap(), configuration_scope)
            scope_status = self._scope_status(session, Outcome.success(scoped_plan))
        else:
            scoped_plan = planned
            scope_status = self._scope_status(session, scoped_plan)
        return Outcome.success({
            "v1_root": str(root),
            "findings": [item.document() for item in scoped_plan.findings],
            "configuration_scope": configuration_scope,
            "scope_status": scope_status,
            "install": self._install_state(root, scope_status),
        })

    def _install_state(self, root, scope_status):
        """这一份 V2 是怎么装上的、还要不要引导用户迁移。

        AstrBot 更新插件时替换的是**原目录**（updater.py:282），目录名不会跟着
        metadata.name 变；插件配置文件名取的又是目录名（star_manager.py:1166）。
        所以「V1 用户点更新」这条路上，V2 会以 V1 的目录名运行、并读写 V1 的
        配置文件。这里把事实报给页面，让升级提示能写清楚，而不是静默发生。

        ``pending`` 表示还没导入过：页面据此决定要不要出升级卡。
        """
        from pathlib import Path

        plugin_root = Path(__file__).resolve().parent.parent
        dir_name = plugin_root.name
        try:
            from main import PLUGIN_NAME
        except Exception:  # pragma: no cover - 只在异常安装形态下
            PLUGIN_NAME = "astrbot_plugin_private_companion"
        config_path = getattr(self.controller.config, "config_path", None)
        source_config = self._v1_host_config_path()
        takeover = (self._migration_root_identity(config_path) == self._migration_root_identity(source_config)
                    if config_path and source_config else dir_name == V1_PLUGIN_DATA_NAME)
        present = Path(root).is_dir()
        imported = self._already_imported(root)
        configuration_scopes = self._configuration_scopes(root)
        configuration_scope_unknown = self._legacy_scope_unknown(root)
        verified_at = self._runtime_metadata_value("legacy_upgrade_verified_at")
        verified_root = self._runtime_metadata_value("legacy_upgrade_verified_root")
        verified = bool(verified_at and (not verified_root
                        or verified_root == self._migration_root_identity(root)))
        restart_observed = self._restart_observed(root)
        restart_required = imported and self._runtime_metadata_value("legacy_restart_required") != "0"
        # 配置导入、重启核对与资料迁移是不同状态；不能从配置标记推断资料完成。
        if present and not imported:
            phase = "upgrade_pending"
        elif present and imported and verified:
            phase = "upgrade_cleanup"
        elif present and imported and restart_required:
            phase = "upgrade_restart_check"
        elif present and imported:
            phase = "upgrade_cleanup"
        else:
            phase = "new_user"
        residues = []
        if present:
            try:
                residues = sorted(
                    name for name in ("companions.json", ".storage-backend-state.json",
                                      "bookshelf_pages")
                    if (Path(root) / name).exists())
            except OSError:
                residues = []
        return {
            "plugin_dir": dir_name,
            "plugin_name": PLUGIN_NAME,
            "takeover": takeover,
            "phase": phase,
            "v1_residues": residues,
            "v1_root": str(root),
            "v1_present": present,
            "configuration_shared": takeover,
            "configuration_imported": imported,
            "configuration_scopes": configuration_scopes,
            "configuration_scope_unknown": configuration_scope_unknown,
            "restart_observed": restart_observed,
            "restart_required": restart_required,
            "upgrade_verified": verified,
            "data_migration_status": "not_inferred",
            "pending": present and bool(scope_status) and not imported,
        }

    def _runtime_metadata_value(self, key):
        try:
            runtime = getattr(getattr(self.controller, "runtime", None), "read_one", None)
            if not callable(runtime):
                return None
            row = runtime("SELECT value FROM runtime_metadata WHERE key = ?", (key,))
            return row[0] if row else None
        except Exception:
            return None

    def _configuration_scopes(self, root=None):
        recorded_root = self._runtime_metadata_value("legacy_configuration_imported_root")
        requested_root = self._migration_root_identity(root)
        if recorded_root and requested_root and recorded_root != requested_root:
            return []
        raw_scopes = self._runtime_metadata_value("legacy_configuration_scopes")
        if not raw_scopes:
            return []
        try:
            scopes = json.loads(raw_scopes)
        except (TypeError, ValueError):
            return []
        if type(scopes) is not list:
            return []
        return [scope for scope in scopes if scope in ("all", "plugin", "proactive", "reality")]

    def _already_imported(self, root=None):
        """V2 侧是否完成过全部配置迁移；局部范围不算整体完成。"""
        return bool({"all", PLUGIN_CONFIGURATION_SCOPE} & set(self._configuration_scopes(root))) \
            or self._legacy_scope_unknown(root)

    def _legacy_scope_unknown(self, root=None):
        """兼容仅记录 ``legacy_imported_at`` 的旧安装，不覆盖已知局部范围。"""
        if not self._runtime_metadata_value("legacy_imported_at"):
            return False
        # 新格式的范围或来源标记存在时，它们是权威信息；即便损坏也不要将局部迁移
        # 猜成「全部完成」。
        if any(self._runtime_metadata_value(key) is not None for key in (
                "legacy_configuration_imported_at",
                "legacy_configuration_imported_root",
                "legacy_configuration_scopes")):
            return False
        try:
            default_root, _ = self._v1_root()
        except (OSError, TypeError, ValueError):
            return False
        return self._migration_root_identity(root) == self._migration_root_identity(default_root)

    @staticmethod
    def _migration_root_identity(root):
        if root is None:
            return None
        from pathlib import Path
        try:
            return os.path.normcase(str(Path(root).resolve()))
        except (OSError, RuntimeError):
            return os.path.normcase(os.path.abspath(str(root)))

    def _restart_observed(self, root=None):
        imported_instance = self._runtime_metadata_value("legacy_imported_instance")
        current_instance = getattr(self, "_instance_token", "")
        # 旧版本没有实例标记，无法证明导入发生于本轮；由操作者进行显式核对。
        return bool(self._already_imported(root)
                    and (not imported_instance or imported_instance != current_instance))

    def _mark_imported(self, written_count, root=None, configuration_scope="all",
                       restart_required=None):
        """记录配置迁移范围、V1 来源以及执行它的插件生命周期。"""
        try:
            runtime = getattr(self.controller, "runtime", None)
            transaction = getattr(runtime, "transaction", None)
            if not callable(transaction):
                return False
            from companion_v2.execution.models import stamp
            observed_at = stamp(self.controller.now())
            instance_token = getattr(self, "_instance_token", "")
            root_identity = self._migration_root_identity(root)
            with transaction() as connection:
                existing_root_row = connection.execute(
                    "SELECT value FROM runtime_metadata WHERE key = ?",
                    ("legacy_configuration_imported_root",),
                ).fetchone()
                existing_root = existing_root_row[0] if existing_root_row else None
                existing_instance_row = connection.execute(
                    "SELECT value FROM runtime_metadata WHERE key = ?",
                    ("legacy_imported_instance",),
                ).fetchone()
                existing_instance = existing_instance_row[0] if existing_instance_row else None
                existing_restart_row = connection.execute(
                    "SELECT value FROM runtime_metadata WHERE key = ?",
                    ("legacy_restart_required",),
                ).fetchone()
                existing_restart = existing_restart_row[0] if existing_restart_row else None
                existing_fields_row = connection.execute(
                    "SELECT value FROM runtime_metadata WHERE key = ?",
                    ("legacy_imported_fields",),
                ).fetchone()
                try:
                    existing_fields = int(existing_fields_row[0]) if existing_fields_row else 0
                except (TypeError, ValueError):
                    existing_fields = 0
                prior_restart_pending = bool(
                    existing_root == root_identity and instance_token
                    and existing_instance == instance_token
                    and (existing_restart == "1" or (existing_restart is None and existing_fields > 0))
                )
                restart_required = bool(written_count) if restart_required is None else bool(restart_required)
                restart_required = restart_required or prior_restart_pending
                existing_scope_row = connection.execute(
                    "SELECT value FROM runtime_metadata WHERE key = ?",
                    ("legacy_configuration_scopes",),
                ).fetchone()
                if existing_root == root_identity and existing_scope_row:
                    try:
                        scopes = json.loads(existing_scope_row[0])
                    except (TypeError, ValueError):
                        scopes = []
                    if type(scopes) is not list:
                        scopes = []
                else:
                    scopes = []
                if configuration_scope == "all":
                    scopes = ["all"]
                elif "all" not in scopes and configuration_scope in (
                        PLUGIN_CONFIGURATION_SCOPE, "proactive", "reality"):
                    scopes = sorted(set(scopes) | {configuration_scope})
                    if {PLUGIN_CONFIGURATION_SCOPE, "reality"}.issubset(scopes):
                        scopes = ["all"]
                values = [
                    ("legacy_configuration_imported_at", observed_at),
                    ("legacy_imported_at", observed_at),
                    ("legacy_imported_fields", str(written_count)),
                    ("legacy_configuration_scopes", json.dumps(scopes)),
                    ("legacy_restart_required", "1" if restart_required else "0"),
                ]
                if root_identity:
                    values.append(("legacy_configuration_imported_root", root_identity))
                if instance_token:
                    values.append(("legacy_imported_instance", instance_token))
                if not restart_required:
                    values.append(("legacy_upgrade_verified_at", observed_at))
                    if root_identity:
                        values.append(("legacy_upgrade_verified_root", root_identity))
                for key, value in values:
                    connection.execute(
                        """INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                        (key, value),
                    )
                if written_count > 0 and restart_required:
                    connection.execute(
                        "DELETE FROM runtime_metadata WHERE key IN (?, ?)",
                        ("legacy_upgrade_verified_at", "legacy_upgrade_verified_root"),
                    )
            return True
        except Exception:
            return False

    def _mark_upgrade_verified(self, root=None):
        try:
            runtime = getattr(self.controller, "runtime", None)
            transaction = getattr(runtime, "transaction", None)
            if not callable(transaction):
                return False
            from companion_v2.execution.models import stamp
            with transaction() as connection:
                values = [("legacy_upgrade_verified_at", stamp(self.controller.now())),
                          ("legacy_restart_required", "0")]
                root_identity = self._migration_root_identity(root)
                if root_identity:
                    values.append(("legacy_upgrade_verified_root", root_identity))
                for key, value in values:
                    connection.execute(
                        """INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                        (key, value),
                    )
            return True
        except Exception:
            return False

    def _plan(self, migration, root, session, configuration_scope="all"):
        planned = migration.plan()
        if not planned.is_success:
            return planned
        plan = self._configuration_scoped_plan(planned.unwrap(), configuration_scope)
        from .legacy_contact_configuration import append_private_contact_plan
        plan = append_private_contact_plan(self.controller, session, migration, plan, configuration_scope)
        intents, differences, scope_status, edit_tokens = self._prepare_intents(session, plan)
        document = plan.document()
        document["intents"] = self._intent_documents(intents)
        document["source_change_count"] = plan.change_count
        document["change_count"] = sum(len(intent.changes) for intent in intents)
        document["preserved_count"] = sum(item["kind"] in ("already_present", "preserved_v2")
                                           for item in differences)
        document["differences"] = differences
        document["configuration_scope"] = configuration_scope
        document["plan_token"] = self._plan_fingerprint(
            root, session, edit_tokens, migration, configuration_scope)
        document["scope_status"] = scope_status
        from .legacy_configuration_review import configuration_rows
        document["configuration_review"] = {
            "version": 1, "rows": configuration_rows(self, session, intents, differences),
        }
        return Outcome.success(document)

    async def _apply(self, migration, root, session, plan_token, configuration_scope="all", *,
                     defer_bindings=False, selected_fields=None):
        """按当前 V1 目录重算一遍，再用宿主的配置服务写入。

        重算而不是相信客户端传来的改动：那是「先看后做」这个保证的全部意义所在。
        """
        planned = migration.plan()
        if not planned.is_success:
            return planned
        plan = self._configuration_scoped_plan(planned.unwrap(), configuration_scope)
        from .legacy_contact_configuration import append_private_contact_plan
        plan = append_private_contact_plan(self.controller, session, migration, plan, configuration_scope)
        blocked_sources = self._configuration_source_blockers(
            [item.document() for item in plan.findings], configuration_scope,
        )
        if blocked_sources:
            return self._configuration_source_unavailable(blocked_sources)
        if not plan.migratable:
            return Outcome.failed(
                "legacy_nothing_to_migrate",
                "这份 V1 数据里没有可以自动迁移的来源。",
            )
        intents, differences, scope_status, edit_tokens = self._prepare_intents(session, plan)
        expected = self._plan_fingerprint(root, session, edit_tokens, migration, configuration_scope)
        if plan_token != expected:
            return Outcome.failed(
                "legacy_plan_stale",
                "V1 来源、账号范围或 V2 配置在试算后发生了变化，请重新试算。",
            )
        if not intents and selected_fields is None:
            return Outcome.failed(
                "legacy_nothing_to_migrate",
                "试算没有产出可写入的新设置；已存在的 V2 设置会被保留。",
            )

        kept_count = 0
        if selected_fields is not None:
            from .legacy_configuration_review import selected_intents
            before_count = sum(len(intent.changes) for intent in intents)
            try:
                intents, edit_tokens = selected_intents(self, session, intents, edit_tokens, selected_fields)
            except TaskError as error:
                return Outcome.failed(error.code, str(error))
            kept_count = before_count - sum(len(intent.changes) for intent in intents)

        baseline_snapshots = {}
        service = session.application.configuration
        operator = self._operator()
        # 此时还没有写入；拆层后的每个目标都必须匹配预览快照。
        # 执行期间由本批前序写入引起的继承变化在 _apply_intent 中逐字段核对。
        for intent in intents:
            if intent.target_kind in ("plugin_config", "semantic_config", "weather_host_settings",
                                      "reality_config", "image_output_config",
                                      "native_extension_config"):
                continue
            target = self._scope_target(session, intent)
            if target is None:
                return Outcome.failed(
                    "legacy_plan_stale",
                    f"迁移目标在执行前发生了变化（{intent.group_id} / {intent.scope}），请重新试算。")
            snapshot = service._snapshot(
                operator, target, intent.group_id, self.controller.now(), "configuration.read",
            )
            if snapshot.edit_token != edit_tokens.get(self._intent_key(intent)):
                return Outcome.failed(
                    "legacy_plan_stale",
                    f"V2 目标设置在执行前发生了变化（{intent.group_id}），请重新试算。")
            baseline_snapshots[self._intent_key(intent)] = snapshot

        written: list[str] = []
        errors: list[dict] = self._optional_extension_source_errors(
            [item.document() for item in plan.findings], configuration_scope)
        restart_required = False
        pending = None
        # A binding update closes and reopens the host runtime. Finish all
        # configuration writes using this session before applying that update.
        for intent in sorted(intents, key=lambda item: item.target_kind == "plugin_config"):
            try:
                if intent.target_kind == "plugin_config":
                    if (defer_bindings and ACCOUNT_CONFIGURATION_TARGETS.intersection(intent.changes)
                            and callable(getattr(self.controller, "apply_bindings", None))):
                        pending = intent
                        continue
                    plugin_written, binding_result = await self._apply_plugin_intent(
                        intent, edit_tokens.get("plugin_config_state", ""))
                    written.extend(plugin_written)
                    restart_required = True
                    if binding_result and (binding_result.get("startup_error")
                                           or binding_result.get("requires_initialize")):
                        errors.append({
                            "scope": intent.scope,
                            "code": binding_result.get("startup_error") or "host_initialize_required",
                            "reason": "绑定配置已保存，但共享运行档没有启动；请读取宿主状态并处理启动原因。",
                        })
                elif intent.target_kind == "weather_host_settings":
                    written.extend(self._apply_weather_host_intent(
                        intent, edit_tokens.get("weather_host_settings", "")))
                elif intent.target_kind == "semantic_config":
                    written.extend(await self._apply_semantic_intent(
                        intent, session, edit_tokens.get("semantic_config_state", "")))
                elif intent.target_kind == "reality_config":
                    written.extend(await self._apply_reality_config_intent(
                        intent, edit_tokens.get("reality_config_state", ""),
                        edit_tokens.get("reality_config_fields", ())))
                elif intent.target_kind == "image_output_config":
                    written.extend(self._apply_image_output_intent(
                        intent, edit_tokens.get("image_output_config_state", "")))
                elif intent.target_kind == "native_extension_config":
                    written.extend(await self._apply_native_extension_config_intent(
                        intent, edit_tokens.get("native_extension_states", {}).get(intent.group_id, "")))
                else:
                    written.extend(self._apply_intent(
                        intent, session, baseline_snapshots[self._intent_key(intent)]))
            except TaskError as error:
                errors.append({"scope": intent.scope, "code": error.code, "reason": str(error)})
            except Exception as error:  # noqa: BLE001 - 一组失败不该拖垮其余组
                errors.append({"scope": intent.scope, "code": "legacy_apply_failed", "reason": str(error)})

        if not written and errors:
            return Outcome.failed(
                "legacy_apply_failed",
                "迁移没有写入任何设置：" + "；".join(item["reason"] for item in errors),
            )
        document = {
            "v1_root": str(root),
            "configuration_scope": configuration_scope,
            "written": written,
            "written_count": len(written),
            "change_count": sum(len(intent.changes) for intent in intents),
            "restart_required": restart_required,
            "differences": differences,
            "errors": errors,
            "selection_applied": selected_fields is not None,
            "kept_count": kept_count,
            "skipped": [
                {"source_id": item.source_id, "state": item.state, "detail": item.detail}
                for item in plan.findings
                if item.state not in (MigrationState.READY.value, MigrationState.ABSENT.value)
            ],
        }
        if pending is not None:
            return _PendingBindingApply(root, configuration_scope, document, pending,
                                        edit_tokens.get("plugin_config_state", ""))
        return self._complete_configuration_apply(root, configuration_scope, document)

    def _complete_configuration_apply(self, root, configuration_scope, document):
        document["written_count"] = len(document["written"])
        document["completion_recorded"] = self._mark_imported(
            document["written_count"], root, configuration_scope,
            restart_required=document["restart_required"],
        ) if not document["errors"] else False
        document["configuration_imported"] = self._already_imported(root)
        return Outcome.success(document)

    async def _finish_binding_apply(self, pending):
        document = pending.document
        try:
            written, result = await self._apply_plugin_intent(pending.intent, pending.expected_state_token)
            document["written"].extend(written)
            document["restart_required"] = True
            if result and (result.get("startup_error") or result.get("requires_initialize")):
                document["errors"].append({"scope": pending.intent.scope,
                    "code": result.get("startup_error") or "host_initialize_required",
                    "reason": "绑定配置已保存，但共享运行档没有启动；请读取宿主状态并处理启动原因。"})
        except TaskError as error:
            if not document["written"]:
                return Outcome.failed(error.code, str(error))
            document["errors"].append({"scope": pending.intent.scope, "code": error.code, "reason": str(error)})
        except Exception as error:
            if not document["written"]:
                return Outcome.failed("legacy_apply_failed", str(error))
            document["errors"].append({"scope": pending.intent.scope,
                "code": "legacy_apply_failed", "reason": str(error)})
        async with self.controller._control_lock:
            outcome = self._complete_configuration_apply(pending.root, pending.configuration_scope, document)
            if pending.auto_import:
                document["state"] = "imported" if document["completion_recorded"] else "partial"
                document["configuration_imported"] = bool(document["completion_recorded"])
                document["data_import_required"] = True
            return outcome

    @staticmethod
    def _configuration_source_blockers(findings, configuration_scope):
        """Require each in-scope source to be confirmed readable or genuinely absent."""
        source_ids = {
            "all": ("private.host_configuration", "reality.configuration", "image.configuration"),
            PLUGIN_CONFIGURATION_SCOPE: ("private.host_configuration", "image.configuration"),
            PROACTIVE_CONFIGURATION_SCOPE: ("private.host_configuration",),
            REALITY_CONFIGURATION_SCOPE: ("reality.configuration",),
        }.get(configuration_scope)
        if source_ids is None:
            return [{"source_id": "configuration_scope", "state": "unknown",
                     "detail": "配置迁移范围无效。"}]
        by_id = {
            item.get("source_id"): item for item in findings
            if isinstance(item, dict) and isinstance(item.get("source_id"), str)
        }
        blockers = []
        for source_id in source_ids:
            finding = by_id.get(source_id)
            if finding is None:
                blockers.append({"source_id": source_id, "state": "missing",
                                 "detail": "迁移发现结果缺少此配置来源记录。"})
                continue
            if finding.get("state") not in (MigrationState.READY.value, MigrationState.ABSENT.value):
                blockers.append({
                    "source_id": source_id,
                    "state": str(finding.get("state") or "unknown"),
                    "detail": str(finding.get("detail") or "配置来源状态无法确认。")[:240],
                })
        return blockers

    @staticmethod
    def _optional_extension_source_errors(findings, configuration_scope):
        if configuration_scope not in ("all", PLUGIN_CONFIGURATION_SCOPE):
            return []
        by_id = {item.get("source_id"): item for item in findings if isinstance(item, dict)}
        errors = []
        for name in WRITABLE_PLUGINS:
            finding = by_id.get(name + ".configuration")
            if finding is None or finding.get("state") in (MigrationState.READY.value,
                                                               MigrationState.ABSENT.value):
                continue
            errors.append({"scope": {"content": "content.authoring", "memory": "memory"}.get(name, name + ".settings"),
                           "code": "legacy_native_source_unavailable",
                           "reason": f"旧 {name} 配置未能读取或目标 schema 不可用；其余配置已允许迁移。"})
        return errors

    @staticmethod
    def _configuration_source_unavailable(blockers):
        labels = {
            "private.host_configuration": "V1 陪伴插件配置",
            "reality.configuration": "V1 Reality 配置",
            "image.configuration": "V1 Image 配置",
            **{name + ".configuration": f"V1 {name} 配置" for name in WRITABLE_PLUGINS},
        }
        reasons = []
        for item in blockers:
            label = labels.get(item.get("source_id"), "配置来源")
            state = item.get("state")
            detail = item.get("detail") or "请检查来源文件后重新发现。"
            reasons.append(f"{label}（{state}）：{detail}")
        return Outcome.failed(
            "legacy_configuration_unavailable",
            "配置来源不完整，尚未写入或登记迁移完成。" + "；".join(reasons),
        )

    @staticmethod
    def _configuration_scoped_plan(plan, configuration_scope):
        if configuration_scope == "all":
            return plan
        if configuration_scope == PLUGIN_CONFIGURATION_SCOPE:
            plugin_sources = {"private.host_configuration", "image.configuration"} | {
                name + ".configuration" for name in WRITABLE_PLUGINS
            }
            intents = tuple(intent for intent in plan.intents
                            if intent.source_id in plugin_sources)
            differences = tuple(item for item in plan.differences
                                if not str(item.get("field", item.get("field_id", ""))).startswith("reality."))
            findings = tuple(item for item in plan.findings
                             if item.source_id in plugin_sources | {"content.configuration", "screen.configuration"})
            return replace(
                plan,
                findings=findings,
                warnings=(
                    "本次迁移 V1 陪伴插件配置中可转换的绑定、模型与运行设置；V2 已有明确设置会保留。",
                    "独立生图、游戏、同玩、直播、创作、记忆拓展的可移植配置也在本范围内；现实拓展配置可单独迁移。",
                ),
                intents=intents,
                differences=differences,
            )
        if configuration_scope == REALITY_CONFIGURATION_SCOPE:
            intents = tuple(intent for intent in plan.intents
                            if intent.target_kind == "reality_config")
            differences = tuple(item for item in plan.differences
                                if str(item.get("field", item.get("field_id", ""))).startswith("reality."))
            findings = tuple(item for item in plan.findings
                             if item.source_id in {"reality.configuration", "reality.device_and_observations"})
            return replace(
                plan,
                findings=findings,
                warnings=(
                    "本次只迁移 V1 Reality 的摄像头基础参数、默认音量及时区；V2 已有非默认设置会保留。",
                    "设备索引、授权、视觉提供方、扬声器路由、提醒与历史数据不会从 V1 复制。",
                ),
                intents=intents,
                differences=differences,
            )
        if configuration_scope != PROACTIVE_CONFIGURATION_SCOPE:
            raise ValueError("unsupported configuration scope")

        allowed_state_fields = {
            field.rsplit(".", 1)[-1] for field in _PROACTIVE_CONFIGURATION_FIELDS
            if field.startswith("role.state.")
        }
        intents = []
        for intent in plan.intents:
            if intent.scope == "proactive.contact" and intent.target_kind == "configuration_registry":
                allowed = set(intent.changes)
            elif intent.scope == "role.state" and intent.target_kind == "configuration_registry":
                allowed = allowed_state_fields
            else:
                continue
            changes = {field: value for field, value in intent.changes.items() if field in allowed}
            if changes:
                intents.append(replace(
                    intent,
                    changes=changes,
                    confidence={field: value for field, value in intent.confidence.items() if field in changes},
                    sources={field: value for field, value in intent.sources.items() if field in changes},
                ))

        allowed_targets = tuple(
            field[:-1] for field in _PROACTIVE_CONFIGURATION_FIELDS if field.endswith(".*")
        )
        allowed_exact = {
            field for field in _PROACTIVE_CONFIGURATION_FIELDS if not field.endswith(".*")
        }
        differences = tuple(item for item in plan.differences
                            if any(str(item.get("field", item.get("field_id", ""))).startswith(prefix)
                                   for prefix in allowed_targets)
                            or item.get("field", item.get("field_id", "")) in allowed_exact
                            or item.get("audit_scope") == "proactive")
        findings = tuple(item for item in plan.findings
                         if item.source_id == "private.host_configuration")
        return replace(
            plan,
            findings=findings,
            warnings=("本次仅纳入主动消息设置及天气地点、开关和数据时效；其他 V1 设置与用户数据不在迁移范围内。",),
            intents=tuple(intents),
            differences=differences,
        )

    def _apply_intent(self, intent, session, baseline_snapshot):
        from companion_v2.legacy.proactive_configuration import PRIVATE_KINDS
        if intent.target_kind in PRIVATE_KINDS:
            # Preference and its exact-route grant commit or roll back together.
            with self.controller.runtime.transaction():
                return self._write_configuration_intent(intent, session, baseline_snapshot)
        return self._write_configuration_intent(intent, session, baseline_snapshot)

    def _write_configuration_intent(self, intent, session, baseline_snapshot):
        """把一条 ConfigIntent 交给配置服务写。

        用 ``patch`` 而不是绕过它直接写表：patch 会带上操作者身份、写变更记录、
        检查 edit token——绕过它等于让这批改动在审计里凭空出现。
        """
        service = session.application.configuration
        operator = self._operator()
        target = self._scope_target(session, intent)
        if target is None:
            raise TaskError("legacy_scope_unavailable", f"当前运行档没有 {intent.scope} 这个作用域")

        snapshot = service._snapshot(operator, target, intent.group_id, self.controller.now(), "configuration.read")
        if snapshot.edit_token != baseline_snapshot.edit_token:
            changed_targets = tuple(f"{intent.group_id}.{field}" for field in intent.changes)
            from companion_v2.legacy.proactive_configuration import PRIVATE_KINDS
            def own_field_changed(field):
                current, baseline = snapshot.fields.get(field), baseline_snapshot.fields.get(field)
                if intent.target_kind in PRIVATE_KINDS and all(item is not None and
                        (item.source is None or item.source.layer in (ConfigLayer.INSTALLATION, ConfigLayer.ROLE))
                        for item in (current, baseline)):
                    # This batch has already validated all original tokens. Its
                    # preceding global writes must not invalidate a person override.
                    return False
                return current != baseline
            if any(own_field_changed(field) for field in changed_targets):
                raise TaskError("legacy_plan_stale", "V2 待迁移字段在迁移执行期间发生了变化，请重新试算。")
        changes = {f"{intent.group_id}.{field}": value for field, value in intent.changes.items()}
        # operation 的属主是**分组声明的 owner_id**，不是分组名——patch 会拿
        # `registry.group(group_id)[0].owner_id` 去比，写成分组名必被判成
        # operation_scope_mismatch（这条原先也从未被测试走到）。
        owner_id = service.registry.group(intent.group_id)[0].owner_id
        operation = self._operation(intent, target, owner_id)
        receipt = service.patch(
            operator, target=target, group_id=intent.group_id, operation=operation,
            expected_edit_token=snapshot.edit_token, changes=changes,
            observed_at=self.controller.now(),
        )
        if not receipt.is_success:
            raise TaskError(receipt.failure.code, receipt.failure.reason)
        saved = receipt.unwrap()
        # Use the same exact-route grant synchronization as the normal editors.
        # The role preference stays role-owned; only the reviewed execution
        # context receives its existing routine's permission. Replays must not
        # undo a subsequent explicit revoke.
        if not saved.replayed and "enabled" in intent.changes:
            context = session.profile.target
            from companion_v2.legacy.proactive_configuration import PRIVATE_KINDS
            if intent.group_id == "proactive.contact" and intent.target_kind in PRIVATE_KINDS:
                from companion_v2.proactive.management import ContactManagement
                ContactManagement(session.application, context).sync_configuration_grant(
                    operator, intent.changes["enabled"],
                    {"replayed": False, "operation_id": saved.operation_id})
            elif intent.group_id == "role.state" and target.layer is ConfigLayer.ROLE:
                from companion_v2.role_work.state.configuration import DEFAULTS, sync_window_grants, target_for
                window = target_for(context)
                effective = require(service.read(operator, target=window,
                    group_id=intent.group_id, observed_at=self.controller.now()))
                settings = {name: effective.values["role.state." + name] for name in DEFAULTS}
                sync_window_grants(self.controller.runtime, window, context.actor.subject,
                    settings, frozenset({"enabled"}), self.controller.now())
            elif intent.group_id == "role.life":
                from companion_v2.role_work.life.management import RoleLifeManagement
                RoleLifeManagement(session.application, context).sync_configuration_grant(
                    operator, intent.changes["enabled"],
                    {"replayed": False, "operation_id": saved.operation_id})
        return list(saved.changed_fields)

    def _operator(self):
        from astrbot.api.web import request
        return dashboard_operator(self.controller.runtime, request.username, at=self.controller.now())

    # ── 作用域可用性 ───────────────────────────────────────────────────
    def _scope_target(self, session, intent):
        """作用域 → 配置目标。

        目标从**会话的管线上下文**推出来（``session.profile.target``）——这与
        ``configuration_api`` 的取法相同。单目标下它是固定 profile 的那个上下文；
        多目标下这些路由按角色挂载，``TargetControllerView.session`` 给的就是
        该目标 + 该角色的上下文。

        以前这里传的是 ``controller.context``（宿主上下文，只有注册路由那套接口），
        于是目标推导必然 AttributeError、被下面兜底成 ``legacy_apply_failed``：
        界面上看着走到了执行，实际一条都没写进去。
        """
        try:
            if intent.target_kind == "plugin_config":
                return None
            from companion_v2.configuration.models import ConfigLayer, ConfigTarget
            service = session.application.configuration
            definitions = service.registry.group(intent.group_id)
            target_context = session.profile.target
            scope = target_context.scope
            role = target_context.role_binding.role
            subject = target_context.actor.subject
            installation_id = scope.installation_id
            from companion_v2.legacy.proactive_configuration import PRIVATE_RELATIONSHIP, PRIVATE_WINDOW
            if intent.target_kind in (PRIVATE_RELATIONSHIP, PRIVATE_WINDOW):
                if intent.group_id != "proactive.contact" or scope.kind.value != "private":
                    return None
                from companion_v2.proactive.configuration import target_for
                from companion_v2.proactive.rhythm_configuration import relationship_target_for
                return (relationship_target_for(target_context) if intent.target_kind == PRIVATE_RELATIONSHIP
                        else target_for(target_context))
            layers = definitions[0].layers
            if intent.group_id == "conversation.image_output" and ConfigLayer.ROLE in layers:
                # Image prompt preferences describe the role.  Keep them at the role
                # layer so a different private/group window does not silently fork
                # the character's image style.
                layer = ConfigLayer.ROLE
            else:
                layer = None
            from companion_v2.configuration.role_settings import RoleSharedSettingsOwner
            owner = getattr(service, "_owners", {}).get(definitions[0].owner_id)
            if layer is None and isinstance(owner, RoleSharedSettingsOwner) and ConfigLayer.WINDOW in layers:
                changed = set(getattr(intent, "changes", {}) or {})
                shared = {field.field_id.rsplit(".", 1)[-1]
                          for field in owner.role_fields(definitions)}
                layer = (ConfigLayer.ROLE if changed and changed <= shared
                         and ConfigLayer.ROLE in layers else ConfigLayer.WINDOW)
            elif layer is None and intent.group_id == "role.state" and ConfigLayer.WINDOW in layers:
                # role.state 的字段同时声明 ROLE/WINDOW，但只有「角色偏好」类字段
                # 允许写角色层；环境项（展示、规划、天气、情绪…）只能写窗口层。
                # 所以按这次要改的字段逐个判断，而不是整组挑一层。
                from companion_v2.role_work.state.configuration import role_preference
                names = [key.rsplit(".", 1)[-1] for key in getattr(intent, "changes", {})]
                role_only = (ConfigLayer.ROLE in layers and bool(names)
                             and all(role_preference(name) for name in names))
                layer = ConfigLayer.ROLE if role_only else ConfigLayer.WINDOW
            elif layer is None:
                layer = next((candidate for candidate in (
                    ConfigLayer.INSTALLATION, ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP,
                    ConfigLayer.WINDOW, ConfigLayer.SCOPE,
                ) if candidate in layers), None)
            if layer is ConfigLayer.INSTALLATION:
                return ConfigTarget(installation_id, layer)
            if layer is ConfigLayer.ROLE:
                return ConfigTarget(installation_id, layer, role, bot_id=scope.bot_id)
            if layer is ConfigLayer.RELATIONSHIP:
                return ConfigTarget(installation_id, layer, role, bot_id=scope.bot_id, subject=subject)
            if layer is ConfigLayer.WINDOW:
                # Match each domain's target_for(context). A relationship subject
                # creates a different storage key and is only valid for that layer.
                return ConfigTarget(installation_id, layer, role, scope, bot_id=scope.bot_id)
            if layer is ConfigLayer.SCOPE:
                return ConfigTarget(installation_id, layer, scope=scope)
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        return None

    def _prepare_intents(self, session, plan):
        """Preview targets, preserve existing values, and bind execution to edit tokens."""
        service = session.application.configuration
        operator = self._operator()
        intents = []
        differences = list(plan.differences)
        scope_status = []
        edit_tokens = {}
        from companion_v2.legacy.proactive_configuration import PRIVATE_KINDS
        edit_tokens["private_contact_preferences"] = digest([
            [intent.target_kind, intent.changes, intent.sources] for intent in plan.intents
            if intent.target_kind in PRIVATE_KINDS])
        for intent in self._configuration_layer_intents(session, plan.intents):
            if intent.target_kind == "native_extension_config":
                native_intent = self._prepare_native_extension_config_intent(
                    intent, differences, scope_status, edit_tokens)
                if native_intent is not None:
                    intents.append(native_intent)
                continue
            if intent.target_kind == "image_output_config":
                image_intent = self._prepare_image_output_intent(
                    intent, differences, scope_status, edit_tokens,
                )
                if image_intent is not None:
                    intents.append(image_intent)
                continue
            if intent.target_kind == "reality_config":
                reality_intent = self._prepare_reality_config_intent(
                    intent, differences, scope_status, edit_tokens,
                )
                if reality_intent is not None:
                    intents.append(reality_intent)
                continue
            if intent.target_kind == "plugin_config":
                weather_intent = self._prepare_weather_host_intent(
                    intent, differences, scope_status, edit_tokens,
                )
                if weather_intent is not None:
                    intents.append(weather_intent)
                plugin_changes = {
                    field: value for field, value in intent.changes.items()
                    if field not in _WEATHER_PLUGIN_FIELDS
                }
                if not plugin_changes:
                    continue
                intent = replace(
                    intent,
                    changes=plugin_changes,
                    confidence={field: value for field, value in intent.confidence.items()
                                if field in plugin_changes},
                    sources={field: value for field, value in intent.sources.items()
                             if field in plugin_changes},
                )
                plugin_intent = self._prepare_plugin_intent(intent, differences, scope_status, edit_tokens)
                if plugin_intent is not None:
                    intents.append(plugin_intent)
                continue
            if intent.target_kind == "semantic_config":
                semantic_intent = self._prepare_semantic_intent(intent, session, differences, scope_status,
                                                                edit_tokens)
                if semantic_intent is not None:
                    intents.append(semantic_intent)
                continue
            target = self._scope_target(session, intent)
            if target is None:
                scope_status.append({"scope": intent.scope, "group_id": intent.group_id,
                                     "available": False, "reason": "unsupported_scope"})
                differences.append({"field": intent.group_id, "kind": "unavailable",
                                    "explain": "当前会话无法为此配置组确定有效目标。"})
                continue
            try:
                snapshot = service._snapshot(
                    operator, target, intent.group_id, self.controller.now(), "configuration.read",
                )
            except Exception as error:  # noqa: BLE001 - report an unavailable group and continue
                scope_status.append({"scope": intent.scope, "group_id": intent.group_id,
                                     "available": False, "reason": "configuration_unavailable"})
                differences.append({"field": intent.group_id, "kind": "unavailable",
                                    "explain": f"当前目标无法预览此配置组：{error}"})
                continue

            scope_status.append({"scope": intent.scope, "group_id": intent.group_id,
                                 "available": True, "reason": ""})
            edit_tokens[self._intent_key(intent)] = snapshot.edit_token
            changes = {}
            confidence = {}
            sources = {}
            for field_id, value in intent.changes.items():
                current = snapshot.fields.get(f"{intent.group_id}.{field_id}")
                from companion_v2.legacy.proactive_configuration import PRIVATE_KINDS
                private_override = intent.target_kind in PRIVATE_KINDS
                existing_override = (current is not None and current.source is not None
                    and (not private_override or current.source.layer in
                         (ConfigLayer.RELATIONSHIP, ConfigLayer.WINDOW)))
                if existing_override:
                    if current.value == value:
                        kind = "already_present"
                        explanation = "V2 已有相同的显式配置，保留现值。"
                    else:
                        kind = "preserved_v2"
                        explanation = "V2 已有不同的显式或继承配置，为避免覆盖，保留 V2 现值。"
                    differences.append({"field": f"{intent.group_id}.{field_id}", "kind": kind,
                                        "before": self._migration_display_value(field_id, value,
                                            target_kind=intent.target_kind),
                                        "after": self._migration_display_value(field_id, current.value,
                                            target_kind=intent.target_kind),
                                        "explain": explanation})
                    continue
                changes[field_id] = value
                if field_id in intent.confidence:
                    confidence[field_id] = intent.confidence[field_id]
                if field_id in intent.sources:
                    sources[field_id] = intent.sources[field_id]
            if changes:
                def preview(values):
                    qualified = {f"{intent.group_id}.{field}": value for field, value in values.items()}
                    return service.preview_changes(
                        operator, target=target, group_id=intent.group_id,
                        changes=qualified, observed_at=self.controller.now(),
                    )

                complete = preview(changes)
                if not complete.is_success:
                    accepted = {}
                    for field_id, value in sorted(changes.items()):
                        trial = dict(accepted)
                        trial[field_id] = value
                        receipt = preview(trial)
                        if receipt.is_success:
                            accepted = trial
                        else:
                            differences.append({
                                "field": f"{intent.group_id}.{field_id}",
                                "kind": "invalid_combination",
                                "explain": "V2 配置校验未通过，已跳过此项：" + receipt.failure.reason,
                            })
                    changes = accepted
                    confidence = {field: value for field, value in confidence.items() if field in changes}
                    sources = {field: value for field, value in sources.items() if field in changes}
            if changes:
                prepared = replace(intent, changes=changes, confidence=confidence, sources=sources)
                # 保留已有值和跳过无效组合都会缩小字段集合。执行使用的是最终
                # 意图的键，不能从同组其他层或原字段集合的令牌中猜测。
                edit_tokens[self._intent_key(prepared)] = snapshot.edit_token
                intents.append(prepared)
        return intents, differences, scope_status, edit_tokens

    def _image_extension_api(self):
        registry = getattr(self.controller, "native_extensions", None)
        getter = getattr(registry, "image_extension_api", None)
        return getter() if callable(getter) else None

    def _prepare_image_output_intent(self, intent, differences, scope_status, edit_tokens):
        api = self._image_extension_api()
        reader = getattr(api, "output_configuration", None)
        if not callable(reader):
            scope_status.append({"scope": intent.scope, "group_id": intent.group_id,
                                 "available": False, "reason": "image_service_unavailable"})
            differences.append({"field": "image.output", "kind": "unavailable",
                                "explain": "V2 原生图片服务当前不可写；启动 V2 图片能力后可重新试算。"})
            return None
        try:
            current = reader()
            settings = current.get("settings") if isinstance(current, dict) else None
            revision = current.get("revision") if isinstance(current, dict) else None
            if not isinstance(settings, dict) or not isinstance(revision, str) or not revision:
                raise ValueError("image_configuration_unavailable")
        except Exception as error:  # noqa: BLE001 - report an unavailable target in the preview
            scope_status.append({"scope": intent.scope, "group_id": intent.group_id,
                                 "available": False, "reason": "image_service_unavailable"})
            differences.append({"field": "image.output", "kind": "unavailable",
                                "explain": f"无法读取 V2 图片模型配置：{type(error).__name__}。"})
            return None

        edit_tokens["image_output_config_state"] = revision
        scope_status.append({"scope": intent.scope, "group_id": intent.group_id,
                             "available": True, "reason": ""})
        # 保留哪些组由迁移层判定：整份保留与「只保留已由 V2 单独设置的下载代理」
        # 都必须让未迁移的端点和后端照常写入，不能整份放弃。
        changes, retained = plan_image_output_changes(settings, intent.changes)
        differences.extend(retained)
        if not changes:
            return None
        prepared = replace(intent, changes=changes,
                           confidence={field: value for field, value in intent.confidence.items()
                                       if field in changes},
                           sources={field: value for field, value in intent.sources.items()
                                    if field in changes})
        edit_tokens[self._intent_key(prepared)] = revision
        return prepared

    def _apply_image_output_intent(self, intent, expected_revision):
        api = self._image_extension_api()
        reader = getattr(api, "output_configuration", None)
        writer = getattr(api, "save_output_configuration", None)
        if not callable(reader) or not callable(writer):
            raise TaskError("legacy_image_service_unavailable", "V2 原生图片服务当前不可写")
        current = reader()
        if not isinstance(current, dict) or current.get("revision") != expected_revision:
            raise TaskError("legacy_plan_stale", "V2 图片模型配置在试算后发生变化，请重新试算。")
        try:
            saved = writer(expected_revision, dict(intent.changes))
        except Exception as error:  # noqa: BLE001 - the native image API exposes stable error codes
            code = getattr(error, "code", "legacy_image_output_invalid")
            raise TaskError(code, "V2 图片模型配置未通过校验；请在模型页检查端点地址、尺寸与协议。") from error
        if not isinstance(saved, dict) or not isinstance(saved.get("settings"), dict):
            raise TaskError("legacy_image_output_unconfirmed", "V2 图片模型配置保存结果未确认。")
        return ["image.output." + field for field in sorted(intent.changes)]

    @staticmethod
    def _configuration_layer_intents(session, intents):
        """Split role defaults and window-only fields before tokens and validation.

        Domain owners define which fields can be shared. Device permissions and
        typing presentation remain in their original window; QQ account status is role-shared.
        """
        service = session.application.configuration
        for intent in intents:
            if intent.target_kind != "configuration_registry":
                yield intent
                continue
            definitions = service.registry.group(intent.group_id)
            owner = getattr(service, "_owners", {}).get(definitions[0].owner_id)
            role_fields = getattr(owner, "role_fields", None)
            if not callable(role_fields):
                yield intent
                continue
            shared = {field.field_id.rsplit(".", 1)[-1] for field in role_fields(definitions)}
            role_changes = {key: value for key, value in intent.changes.items() if key in shared}
            window_changes = {key: value for key, value in intent.changes.items() if key not in shared}
            for subset in (role_changes, window_changes):
                if subset:
                    yield replace(intent, changes=subset,
                        confidence={key: value for key, value in intent.confidence.items() if key in subset},
                        sources={key: value for key, value in intent.sources.items() if key in subset})

    def _reality_runtime(self):
        registry = getattr(self.controller, "native_extensions", None)
        getter = getattr(registry, "service", None)
        return getter("reality") if callable(getter) else None

    def _prepare_native_extension_config_intent(self, intent, differences, scope_status, edit_tokens):
        registry = getattr(self.controller, "native_extensions", None)
        service = registry.service(intent.group_id) if callable(getattr(registry, "service", None)) else None
        config = getattr(service, "config", None)
        if not isinstance(config, dict) or not callable(getattr(config, "revision", None)):
            scope_status.append({"scope": intent.scope, "group_id": intent.group_id,
                                 "available": False, "reason": "native_service_unavailable"})
            return None
        baseline = self._native_extension_baselines().get(intent.group_id, {}).get("defaults", {})
        changes = {}
        for field, value in intent.changes.items():
            if f"{intent.group_id}.{field}" in HOST_VERIFIED_FIELDS and not self._legacy_provider_available(field, value):
                differences.append({"field": f"{intent.group_id}.{field}",
                                    "kind": "provider_reselection_required",
                                    "explain": "旧模型提供方在当前宿主没有可用实例，请在新版共处电话设置中重新选择。"})
                continue
            default = native_setting_value(baseline, field)
            current = native_setting_value(config, field, default)
            if current == value:
                kind, explain = "already_present", "新版拓展已有相同设置。"
            elif current != default:
                kind, explain = "preserved_v2", "新版拓展已有非默认设置，保留现值。"
            else:
                changes[field] = value
                continue
            differences.append({"field": f"{intent.group_id}.{field}",
                                "kind": kind, "explain": explain})
        scope_status.append({"scope": intent.scope, "group_id": intent.group_id,
                             "available": True, "reason": ""})
        if changes:
            edit_tokens.setdefault("native_extension_states", {})[intent.group_id] = (
                native_settings_state_token(config, changes))
        return replace(intent, changes=changes,
                       confidence={key: value for key, value in intent.confidence.items() if key in changes},
                       sources={key: value for key, value in intent.sources.items() if key in changes}) if changes else None

    def _legacy_provider_available(self, field, provider_id):
        context = getattr(self.controller, "context", None)
        if not context or not isinstance(provider_id, str) or not provider_id.strip():
            return False
        try:
            if field == "speech.tts_provider_id":
                from .model_api import _find_tts_provider
                return _find_tts_provider(context, provider_id) is not None
            getter = getattr(context, "get_provider_by_id", None)
            provider = getter(provider_id) if callable(getter) else None
            if field == "speech.stt_provider_id":
                return callable(getattr(provider, "get_text", None))
            if field in ("conversation.vision_provider_id", "vision_provider_id"):
                from .native_extensions.together.model_methods import TogetherModelMethods
                return (callable(getattr(provider, "text_chat", None))
                        and TogetherModelMethods._provider_image_capability(provider) is not False)
            return callable(getattr(provider, "text_chat", None))
        except Exception:  # noqa: BLE001 - 可选宿主提供方查询失败时不导入失效 ID
            return False

    async def _apply_native_extension_config_intent(self, intent, expected_revision):
        registry = getattr(self.controller, "native_extensions", None)
        service = registry.service(intent.group_id) if callable(getattr(registry, "service", None)) else None
        config = getattr(service, "config", None)
        configure = getattr(registry, "configure_service", None)
        if not isinstance(config, dict) or not callable(configure):
            raise TaskError("legacy_native_service_unavailable", "新版拓展配置当前不可写")
        if native_settings_state_token(config, intent.changes) != expected_revision:
            raise TaskError("legacy_plan_stale", "新版拓展配置在试算后发生变化，请重新试算。")
        updates = {}
        for path, value in intent.changes.items():
            section = updates
            parts = path.split(".")
            for part in parts[:-1]:
                section = section.setdefault(part, {})
            section[parts[-1]] = value
        result = await configure(intent.group_id, updates, config.revision())
        if not isinstance(result, dict) or result.get("saved") is not True:
            raise TaskError("legacy_native_apply_failed", "新版拓展配置未确认保存")
        return [f"{intent.group_id}.{field}" for field in intent.changes]

    def _prepare_reality_config_intent(self, intent, differences, scope_status, edit_tokens):
        runtime = self._reality_runtime()
        config = getattr(runtime, "config", None)
        apply = getattr(runtime, "_apply_legacy_migrated_settings", None)
        fields = tuple(sorted(intent.changes))
        if (runtime is None or not isinstance(config, dict) or not callable(apply)
                or set(fields) - set(REALITY_SETTING_DEFAULTS)):
            scope_status.append({"scope": intent.scope, "group_id": intent.group_id,
                                 "available": False, "reason": "reality_service_unavailable"})
            differences.extend({"field": f"reality.{field}", "kind": "unavailable",
                                "explain": "V2 Reality 服务当前不可写；请启动宿主后重新试算。"}
                               for field in fields)
            return None

        edit_tokens["reality_config_fields"] = list(fields)
        edit_tokens["reality_config_state"] = reality_settings_state_token(config, fields)
        scope_status.append({"scope": intent.scope, "group_id": intent.group_id,
                             "available": True, "reason": ""})
        changes = {}
        confidence = {}
        sources = {}
        for field, value in intent.changes.items():
            default = REALITY_SETTING_DEFAULTS[field]
            current = reality_setting_value(config, field, default)
            if current == value:
                differences.append({"field": f"reality.{field}", "kind": "already_present",
                                    "before": str(value), "after": str(current),
                                    "explain": "V2 Reality 设置已有相同值。"})
                continue
            if current != default:
                differences.append({"field": f"reality.{field}", "kind": "preserved_v2",
                                    "before": str(value), "after": str(current),
                                    "explain": "V2 Reality 已有非默认设置，为避免覆盖而保留现值。"})
                continue
            if field == "vision_provider_id" and not self._legacy_provider_available(field, value):
                differences.append({"field": "reality.vision_provider_id",
                                    "kind": "provider_reselection_required",
                                    "explain": "旧视觉模型在当前宿主没有兼容的可用实例，此项未写入；其他偏好继续迁移。"})
                continue
            changes[field] = value
            if field in intent.confidence:
                confidence[field] = intent.confidence[field]
            if field in intent.sources:
                sources[field] = intent.sources[field]
        return replace(intent, changes=changes, confidence=confidence, sources=sources) if changes else None

    async def _apply_reality_config_intent(self, intent, expected_state_token, expected_fields):
        runtime = self._reality_runtime()
        config = getattr(runtime, "config", None)
        apply = getattr(runtime, "_apply_legacy_migrated_settings", None)
        if runtime is None or not isinstance(config, dict) or not callable(apply):
            raise TaskError("legacy_reality_service_unavailable", "V2 Reality 服务当前不可写")
        fields = tuple(expected_fields or intent.changes)
        current_token = reality_settings_state_token(config, fields)
        if current_token != expected_state_token:
            raise TaskError("legacy_plan_stale", "V2 Reality 设置在迁移试算后发生变化，请重新试算。")
        if ("vision_provider_id" in intent.changes and not self._legacy_provider_available(
                "vision_provider_id", intent.changes["vision_provider_id"])):
            raise TaskError("legacy_reality_provider_unavailable", "旧视觉模型目前不可用，请重新试算以接续其他设置。")
        result = apply(dict(intent.changes), expected_state_token, fields)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict) or result.get("saved") is not True:
            reason = result.get("reason") if isinstance(result, dict) else ""
            raise TaskError("legacy_reality_apply_failed", str(reason or "V2 Reality 设置未确认保存"))
        return [f"reality.{field}" for field in intent.changes]

    @staticmethod
    def _prepare_semantic_intent(intent, session, differences, scope_status, edit_tokens):
        semantic = getattr(session.application, "semantics", None)
        if semantic is None or semantic.closed:
            scope_status.append({"scope": intent.scope, "group_id": intent.group_id,
                                 "available": False, "reason": "semantic_service_unavailable"})
            for field_id in intent.changes:
                differences.append({"field": f"semantic.{field_id}", "kind": "unavailable",
                                    "explain": "语义服务未运行；启动 V2 后可重新试算这些设置。"})
            return None

        snapshot = semantic.store.configuration_snapshot()
        edit_tokens["semantic_config_state"] = str(snapshot["revision"])
        scope_status.append({"scope": intent.scope, "group_id": intent.group_id,
                             "available": True, "reason": ""})
        changes, confidence, sources = {}, {}, {}
        for field_id, value in intent.changes.items():
            current = snapshot["values"][field_id]
            if field_id in snapshot["explicit"]:
                if current == value:
                    kind, explanation = "already_present", "V2 语义设置已有相同的显式值，保留现值。"
                else:
                    kind, explanation = "preserved_v2", "V2 语义设置已有不同的显式值，为避免覆盖，保留现值。"
                differences.append({"field": f"semantic.{field_id}", "kind": kind,
                                    "before": str(value), "after": str(current), "explain": explanation})
                continue

            try:
                semantic.store.validate_changes({field_id: value})
                provider_kind = {"embedding_provider": "embedding", "analysis_provider": "analysis"}.get(field_id)
                if value and provider_kind:
                    semantic.gateway.port.binding(value, provider_kind)
            except Exception as error:  # noqa: BLE001 - surface invalid legacy values in the preview
                kind = "invalid_provider" if field_id.endswith("_provider") else "invalid_value"
                differences.append({"field": f"semantic.{field_id}", "kind": kind,
                                    "before": str(value),
                                    "explain": f"V2 语义设置校验未通过，已跳过此项：{error}"})
                continue
            changes[field_id] = value
            if field_id in intent.confidence:
                confidence[field_id] = intent.confidence[field_id]
            if field_id in intent.sources:
                sources[field_id] = intent.sources[field_id]

        if not changes:
            return None
        return replace(intent, changes=changes, confidence=confidence, sources=sources)

    def _prepare_weather_host_intent(self, intent, differences, scope_status, edit_tokens):
        """Validate source, host and credential together through the host settings service."""
        fields = {field: value for field, value in intent.changes.items()
                  if field in _WEATHER_PLUGIN_FIELDS}
        if not fields:
            return None

        config = self.controller.config
        if not isinstance(config, dict) or not callable(getattr(self.controller, "save_plugin_config", None)):
            scope_status.append({"scope": "host_options", "group_id": "weather",
                                 "available": False, "reason": "weather_settings_save_unavailable"})
            for field in fields:
                differences.append({"field": field, "kind": "unavailable",
                                    "explain": "AstrBot 当前没有可用的宿主天气设置保存接口，此组未迁移。"})
            return None

        from .weather_settings import WeatherHostSettings, configured_weather_ports, normalize_host

        try:
            view = WeatherHostSettings(self.controller).view()
        except (TaskError, TypeError, ValueError, KeyError):
            scope_status.append({"scope": "host_options", "group_id": "weather",
                                 "available": False, "reason": "weather_settings_unavailable"})
            for field in fields:
                differences.append({"field": field, "kind": "unavailable",
                                    "explain": "当前宿主天气设置无法读取，天气配置组未迁移。"})
            return None

        edit_tokens["weather_host_settings"] = view["edit_token"]
        defaults = plugin_configuration_defaults()
        explicit = any(
            present and current != defaults.get(field)
            for field in fields
            for present, current in (self._plugin_config_value(config, field),)
        )
        if explicit:
            for field, value in fields.items():
                present, current = self._plugin_config_value(config, field)
                kind = "already_present" if present and current == value else "preserved_v2"
                differences.append({
                    "field": field,
                    "kind": kind,
                    "before": self._migration_display_value(field, value),
                    "after": self._migration_display_value(field, current) if present else "V2 当前设置",
                    "explain": "V2 已有明确的宿主天气设置，为避免混合两套来源或凭据，保留整个天气配置组。",
                })
            scope_status.append({"scope": "host_options", "group_id": "weather",
                                 "available": True, "reason": "preserved_v2"})
            return None

        weather_changes = {field.partition(".")[2]: value for field, value in fields.items()}
        weather_error = None
        if "api_host" in weather_changes:
            try:
                weather_changes["api_host"] = normalize_host(weather_changes["api_host"])
            except ValueError as error:
                weather_error = error
                weather_changes = None
        if weather_changes is not None:
            try:
                candidate = self._plugin_config_candidate(
                    config, {"weather." + field: value for field, value in weather_changes.items()},
                )
                configured_weather_ports(candidate)
            except (TypeError, ValueError, KeyError, OSError) as error:
                weather_error = error
                weather_changes = None

        if weather_changes is None:
            explanation = self._weather_validation_explanation(weather_error)
            scope_status.append({"scope": "host_options", "group_id": "weather",
                                 "available": True, "reason": "weather_configuration_invalid"})
            for field in fields:
                differences.append({
                    "field": field,
                    "kind": "invalid_combination",
                    "explain": "V2 天气设置校验未通过；来源、Host 与凭据作为一组跳过。" + explanation,
                })
            return None

        scope_status.append({"scope": "host_options", "group_id": "weather",
                             "available": True, "reason": ""})
        confidence = {field.partition(".")[2]: value for field, value in intent.confidence.items()
                      if field in fields}
        sources = {field.partition(".")[2]: value for field, value in intent.sources.items()
                   if field in fields}
        return replace(intent, scope="host_options", group_id="weather",
                       target_kind="weather_host_settings", changes=weather_changes,
                       confidence=confidence, sources=sources)

    def _apply_weather_host_intent(self, intent, expected_edit_token):
        from .weather_settings import WeatherHostSettings

        WeatherHostSettings(self.controller).configure({
            "edit_token": expected_edit_token,
            "changes": dict(intent.changes),
        })
        return ["weather." + field for field in sorted(intent.changes)]

    @staticmethod
    async def _apply_semantic_intent(intent, session, expected_revision):
        semantic = getattr(session.application, "semantics", None)
        if semantic is None or semantic.closed:
            raise TaskError("legacy_semantic_unavailable", "V2 semantic service is not running")
        snapshot = semantic.store.configuration_snapshot()
        if str(snapshot["revision"]) != expected_revision:
            raise TaskError("legacy_plan_stale", "V2 语义设置在迁移试算后发生变化，请重新试算。")
        await semantic.configure(dict(intent.changes), snapshot["revision"])
        return [f"semantic.{field_id}" for field_id in sorted(intent.changes)]

    def _prepare_plugin_intent(self, intent, differences, scope_status, edit_tokens):
        config = self.controller.config
        save = getattr(self.controller, "save_plugin_config", None)
        if not isinstance(config, dict) or not callable(save):
            for field_id in intent.changes:
                differences.append({"field": field_id, "kind": "unavailable",
                    "explain": "AstrBot 当前没有可用的插件配置保存接口，此项只显示、不写入。"})
            return None

        defaults = plugin_configuration_defaults()
        edit_tokens["plugin_config_state"] = self._plugin_config_state_token(config)
        changes, confidence, sources = {}, {}, {}
        current_multi_host = config.get("multi_host", {})
        multi_host_enabled = (isinstance(current_multi_host, dict)
                              and current_multi_host.get("enabled") is True)
        proposed_targets = intent.changes.get("multi_host.targets", [])
        proposed_groups = intent.changes.get("multi_host.group_targets", [])
        multi_needed = (multi_host_enabled or len(proposed_targets) > 1 or bool(proposed_groups)
                        or "basic_config.enable_multi_persona_mode"
                        in intent.sources.get("multi_host.personas", ()))
        target_defaults = {
            "target.platform_id": "default", "target.account_id": "",
            "target.participant_id": "", "target.bot_id": "staging-bot",
            "target.role_id": "staging-role", "target.persona_id": "",
        }
        target_proposals = {key: value for key, value in intent.changes.items()
                            if key in target_defaults}
        target_bundle_locked = False
        for field_id in target_proposals:
            present, current = self._plugin_config_value(config, field_id)
            if present and current != target_defaults[field_id]:
                target_bundle_locked = True
                break
        if multi_needed and not multi_host_enabled:
            differences.append({"field": "multi_host.enabled", "kind": "mode_preserved",
                "before": "迁移候选", "after": "false",
                "explain": "已保存可匹配的多目标账号与人格映射；为保留当前运行数据目录，迁移不切换运行模式。启用多目标后这些映射才会生效。"})
        for field_id, value in intent.changes.items():
            if field_id == "multi_host.capabilities":
                differences.append({"field": field_id, "kind": "review_required",
                    "before": str(value),
                    "explain": "旧版能力清单不包含 V2 的目标级授权范围，需在账号与会话页逐目标核对后配置。"})
                continue
            present, current = self._plugin_config_value(config, field_id)
            if field_id in ACCOUNT_CONFIGURATION_TARGETS:
                if field_id.startswith("target.") and multi_host_enabled:
                    differences.append({"field": field_id, "kind": "mode_preserved",
                                        "explain": "当前使用多目标模式；旧单目标身份不覆盖现有运行配置。"})
                    continue
                if field_id.startswith("target.") and target_bundle_locked:
                    if current == value:
                        kind, explanation = "already_present", "V2 已有相同的账号身份，保留现值。"
                    else:
                        kind, explanation = "preserved_v2", "V2 已有显式账号身份，避免迁移覆盖。"
                    differences.append({"field": field_id, "kind": kind, "before": str(value),
                                        "after": str(current), "explain": explanation})
                    continue
                if (field_id.startswith("multi_host.") and not multi_host_enabled
                        and not multi_needed):
                    differences.append({"field": field_id, "kind": "already_represented",
                                        "explain": "单目标身份已由 target 配置承接，不重复创建多目标映射。"})
                    continue
                account_change = self._prepare_account_change(
                    config, field_id, value, present, current, differences,
                )
                if account_change is None:
                    continue
                if account_change is _NO_ACCOUNT_CHANGE:
                    continue
                value = account_change
                changes[field_id] = value
                if field_id in intent.confidence:
                    confidence[field_id] = intent.confidence[field_id]
                if field_id in intent.sources:
                    sources[field_id] = intent.sources[field_id]
                continue
            default = defaults.get(field_id)
            if present and current != default:
                if current == value:
                    kind, explanation = "already_present", "V2 已有相同的插件设置，保留现值。"
                else:
                    kind, explanation = "preserved_v2", "V2 已有不同于 schema 默认值的插件设置，为避免覆盖，保留现值。"
                differences.append({"field": field_id, "kind": kind, "before": str(value),
                                    "after": str(current), "explain": explanation})
                continue
            changes[field_id] = value
            if field_id in intent.confidence:
                confidence[field_id] = intent.confidence[field_id]
            if field_id in intent.sources:
                sources[field_id] = intent.sources[field_id]

        if changes and not self._plugin_config_valid(config, changes):
            # Account routes, group routes, personas and the legacy single-target
            # identity are one contract. Validating these lists one by one drops
            # mappings whose references are supplied by another list.
            identity_changes = {field: value for field, value in changes.items()
                                if field in ACCOUNT_CONFIGURATION_TARGETS}
            other_changes = {field: value for field, value in changes.items()
                             if field not in ACCOUNT_CONFIGURATION_TARGETS}
            accepted = {}
            if identity_changes:
                if self._plugin_config_valid(config, identity_changes):
                    accepted.update(identity_changes)
                else:
                    for field_id in sorted(identity_changes):
                        differences.append({"field": field_id, "kind": "invalid_combination",
                            "explain": "账号、会话与人格映射组合未通过宿主校验，整组映射已跳过。"})
            for field_id, value in sorted(other_changes.items()):
                trial = dict(accepted)
                trial[field_id] = value
                if self._plugin_config_valid(config, trial):
                    accepted = trial
                else:
                    differences.append({"field": field_id, "kind": "invalid_combination",
                        "explain": "宿主设置校验未通过，已跳过此项。"})
            changes = accepted
            confidence = {field: value for field, value in confidence.items() if field in changes}
            sources = {field: value for field, value in sources.items() if field in changes}

        scope_status.append({"scope": intent.scope, "group_id": intent.group_id,
                             "available": True, "reason": "restart_required"})
        return replace(intent, changes=changes, confidence=confidence, sources=sources) if changes else None

    def _prepare_account_change(self, config, field_id, proposed, present, current, differences):
        """Merge identity bindings while preserving every explicit V2 route."""
        if field_id in ("multi_host.targets", "multi_host.group_targets", "multi_host.personas"):
            current_list = current if present else []
            if (not isinstance(current_list, list)
                    or any(not isinstance(item, dict) for item in current_list)
                    or not isinstance(proposed, list)):
                differences.append({"field": field_id, "kind": "invalid_value",
                                    "explain": "V2 账号映射不是列表，旧账号项未写入。"})
                return None
            merged = [dict(item) for item in current_list]
            index = {}
            key_fields = (("platform_id", "account_id", "participant_id")
                          if field_id == "multi_host.targets" else
                          ("platform_id", "account_id", "group_id")
                          if field_id == "multi_host.group_targets" else
                          ("bot_id", "persona_id"))
            for item in merged:
                key = tuple(item.get(name) for name in key_fields)
                if all(value not in (None, "") for value in key):
                    index[key] = item
            changed = False
            for item in proposed if isinstance(proposed, list) else ():
                if not isinstance(item, dict):
                    continue
                key = tuple(item.get(name) for name in key_fields)
                if any(value in (None, "") for value in key):
                    differences.append({"field": field_id, "kind": "invalid_value",
                                        "explain": "迁移候选缺少账号映射键，已跳过。"})
                    continue
                existing = index.get(key)
                if existing is not None:
                    if existing != item:
                        differences.append({"field": field_id, "kind": "preserved_v2",
                                            "before": str(item), "after": str(existing),
                                            "explain": "V2 已有同一路由或人格绑定，保留现有绑定。"})
                    continue
                item = dict(item)
                item.setdefault("__template_key", "private_target" if field_id == "multi_host.targets"
                                else "group_target" if field_id == "multi_host.group_targets"
                                else "persona_mapping")
                merged.append(item)
                index[key] = item
                changed = True
            if not changed:
                if proposed and current_list:
                    differences.append({"field": field_id, "kind": "already_present",
                                        "explain": "V2 已有同一组账号映射，未重复添加。"})
                return _NO_ACCOUNT_CHANGE
            return merged

        defaults = {
            "target.platform_id": "default", "target.account_id": "",
            "target.participant_id": "", "target.bot_id": "staging-bot",
            "target.role_id": "staging-role", "target.persona_id": "",
        }
        if present and current != defaults.get(field_id):
            if current == proposed:
                differences.append({"field": field_id, "kind": "already_present",
                                    "before": str(proposed), "after": str(current),
                                    "explain": "V2 已有相同的账号身份，保留现值。"})
            else:
                differences.append({"field": field_id, "kind": "preserved_v2",
                                    "before": str(proposed), "after": str(current),
                                    "explain": "V2 已有显式账号身份，避免迁移覆盖。"})
            return _NO_ACCOUNT_CHANGE
        return proposed

    @staticmethod
    def _plugin_config_value(config, path):
        current = config
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return False, None
            current = current[part]
        return True, current

    @classmethod
    def _plugin_config_state_token(cls, config):
        values = {}
        targets = {rule["target"] for rule in plugin_configuration_rules()}
        targets.update(ACCOUNT_CONFIGURATION_TARGETS)
        targets.add("multi_host.enabled")
        targets.difference_update(_WEATHER_PLUGIN_FIELDS)
        for target in sorted(targets):
            present, value = cls._plugin_config_value(config, target)
            values[target] = {"present": present, "value": value}
        material = json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @classmethod
    def _plugin_config_candidate(cls, config, changes):
        try:
            candidate = deepcopy(config)
        except TypeError:
            # AstrBot can attach non-copyable runtime values to its config mapping.
            # A shallow root copy is enough because each edited parent is copied below.
            candidate = dict(config)
        for path, value in changes.items():
            parts = path.split(".")
            current = candidate
            for part in parts[:-1]:
                section = current.get(part)
                if section is None:
                    section = {}
                    current[part] = section
                if not isinstance(section, dict):
                    raise ValueError("插件配置分组不是对象")
                section = dict(section)
                current[part] = section
                current = section
            current[parts[-1]] = value
        return candidate

    @classmethod
    def _plugin_config_valid(cls, config, changes):
        try:
            candidate = cls._plugin_config_candidate(config, changes)
            from companion_v2.conversation.pokes import PokePolicy
            from .multi_settings import MultiHostSettings
            from .settings import HostSettings

            multi = candidate.get("multi_host", {})
            if isinstance(multi, dict) and multi.get("enabled", False):
                MultiHostSettings.parse(candidate)
            else:
                HostSettings.parse(candidate)
                account_lists = {"multi_host.targets", "multi_host.personas", "multi_host.group_targets"}
                if account_lists.intersection(changes):
                    probe = deepcopy(candidate)
                    probe.setdefault("multi_host", {})["enabled"] = True
                    probe.setdefault("staging", {})["enabled"] = False
                    MultiHostSettings.parse(probe)
            PokePolicy.parse(candidate.get("poke"))
            return True
        except (TypeError, ValueError, KeyError, AttributeError):
            return False

    async def _apply_plugin_intent(self, intent, expected_state_token):
        config = self.controller.config
        save = getattr(self.controller, "save_plugin_config", None)
        if not isinstance(config, dict) or not callable(save):
            raise TaskError("legacy_plugin_config_unavailable", "AstrBot cannot save plugin settings in this session")
        if self._plugin_config_state_token(config) != expected_state_token:
            raise TaskError("legacy_plan_stale", "V2 插件设置在迁移试算后发生变化，请重新试算。")
        candidate = self._plugin_config_candidate(config, intent.changes)
        if not self._plugin_config_valid(config, intent.changes):
            raise TaskError("legacy_plugin_config_invalid", "迁移后的插件设置未通过宿主校验，请重新试算。")
        apply_bindings = getattr(self.controller, "apply_bindings", None)
        if ACCOUNT_CONFIGURATION_TARGETS.intersection(intent.changes) and callable(apply_bindings):
            result = await apply_bindings(candidate, expected_config=self._plugin_config_candidate(config, {}))
            if not isinstance(result, dict) or result.get("saved") is not True:
                raise TaskError("legacy_binding_apply_failed", "宿主没有确认保存迁移的绑定配置。")
            return sorted(intent.changes), result

        save(candidate)
        refresh = getattr(self.controller, "refresh_settings", None)
        if callable(refresh):
            refresh()
            if getattr(self.controller, "settings_error", None) is not None:
                raise TaskError("legacy_plugin_config_invalid", "保存后宿主设置无法重新读取，请检查插件配置。")
        return sorted(intent.changes), None

    @staticmethod
    def _intent_documents(intents):
        return [
            {"scope": intent.scope, "group_id": intent.group_id, "target_kind": intent.target_kind,
             "fields": sorted(intent.changes), "values": {
                 field: LegacyMigrationWebAPI._migration_display_value(
                     field, value, target_kind=intent.target_kind)
                 for field, value in sorted(intent.changes.items())
             },
             "confidence": dict(sorted(intent.confidence.items())),
             "sources": {key: list(value) for key, value in sorted(intent.sources.items())},
             "source_id": intent.source_id}
            for intent in intents
        ]

    @staticmethod
    def _migration_display_value(field_id, value, *, target_kind=""):
        if field_id in _WEATHER_REDACTED_FIELDS:
            return "（已配置，值不在此显示）" if value not in (None, "") else "（未填写）"
        if target_kind in {"native_extension_config", "configuration_registry"}:
            return preview_value("", field_id, value)
        if target_kind == "image_output_config" and field_id in {"comfyui", "sd_webui"}:
            from companion_v2.legacy.migration import ConfigIntent, _intent_value_document
            return _intent_value_document(ConfigIntent(
                scope="image.output", group_id="image.output", changes={},
                source_id="", target_kind="image_output_config"), field_id, value)
        if target_kind == "image_output_config" and field_id == "endpoints":
            result = []
            for endpoint in value if isinstance(value, list) else ():
                if not isinstance(endpoint, dict):
                    continue
                item = {key: endpoint.get(key) for key in (
                    "id", "name", "enabled", "platform", "model", "width", "height",
                    "quality", "max_reference_images", "base_url", "timeout_seconds", "ratio",
                ) if key in endpoint}
                headers = endpoint.get("custom_headers")
                item["api_key_set"] = bool(endpoint.get("api_key"))
                item["custom_headers_set"] = bool(headers)
                item["custom_header_names"] = sorted(headers) if isinstance(headers, dict) else []
                result.append(item)
            return result
        return value

    @staticmethod
    def _weather_validation_explanation(error):
        known = {
            "use the assigned QWeather API Host": "Host 格式不符合和风分配的 API Host 格式。",
            "invalid QWeather API Key": "凭据不符合 V2 的 QWeather API Key 格式。",
            "invalid QWeather JWT token": "凭据不符合 V2 的 QWeather JWT 格式。",
            "QWeather requires host credentials": "选择 QWeather 时必须同时提供 API Host 与凭据。",
            "QWeather API Key and JWT settings are mutually exclusive": "API Key 与 JWT 配置不能同时启用。",
            "QWeather host credentials are incomplete": "和风 API Host 与凭据没有组成完整配置。",
            "invalid weather source or refresh interval": "V2 天气来源或刷新间隔无效。",
        }
        if error is None:
            return " 请检查 V1 天气配置。"
        return " " + known.get(str(error), "V2 校验异常类型：" + type(error).__name__ + "。")

    @staticmethod
    def _intent_key(intent):
        # 同组的改动可能被拆成多条意图（role.state 的偏好/环境拆层），
        # 所以键里要带上这组字段名，否则两条意图会互相覆盖 edit token，
        # 执行时第二条被判成 legacy_plan_stale。
        names = ",".join(sorted(getattr(intent, "changes", {}) or {}))
        return intent.scope + "|" + intent.group_id + "|" + intent.target_kind + "|" + names

    def _plan_fingerprint_tokens(self, plan_token):
        """占位：指纹里原有的 edit token 无法从摘要反推，这里返回空列表。"""
        return ()

    def _plan_fingerprint(self, root, session, edit_tokens, migration=None, configuration_scope="all"):
        material = {
            "v1": self._fingerprint(root),
            "registry": session.application.configuration.registry.fingerprint,
            "v2_edit_tokens": dict(sorted(edit_tokens.items())),
            "configuration_scope": configuration_scope,
        }
        if migration is not None:
            material["host_account_context"] = getattr(
                migration, "configuration_context_fingerprint", "",
            )
        encoded = json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _scope_status(self, session, planned):
        """试算阶段就回答「这些作用域在这份运行档里能不能写」。

        原先只有执行时才会以 ``legacy_scope_unavailable`` 失败——用户点了执行才知道。
        判据两条：作用域有没有目标推导、分组有没有注册在配置注册表里。
        """
        if not planned.is_success:
            return []
        status = []
        registry = session.application.configuration.registry
        for intent in planned.unwrap().intents:
            if intent.target_kind == "image_output_config":
                api = self._image_extension_api()
                available = callable(getattr(api, "output_configuration", None)) and callable(
                    getattr(api, "save_output_configuration", None))
                status.append({"scope": intent.scope, "group_id": intent.group_id,
                    "available": available,
                    "reason": "" if available else "image_service_unavailable"})
                continue
            if intent.target_kind == "plugin_config":
                status.append({"scope": intent.scope, "group_id": intent.group_id,
                    "available": callable(getattr(self.controller, "save_plugin_config", None)),
                    "reason": "" if callable(getattr(self.controller, "save_plugin_config", None))
                    else "plugin_config_save_unavailable"})
                continue
            if intent.target_kind == "reality_config":
                runtime = self._reality_runtime()
                available = callable(getattr(runtime, "_apply_legacy_migrated_settings", None))
                status.append({"scope": intent.scope, "group_id": intent.group_id,
                    "available": available,
                    "reason": "" if available else "reality_service_unavailable"})
                continue
            if intent.target_kind == "native_extension_config":
                registry_service = getattr(self.controller, "native_extensions", None)
                getter = getattr(registry_service, "service", None)
                service = getter(intent.group_id) if callable(getter) else None
                config = getattr(service, "config", None)
                available = isinstance(config, dict) and callable(getattr(registry_service, "configure_service", None))
                status.append({"scope": intent.scope, "group_id": intent.group_id,
                               "available": available,
                               "reason": "" if available else "native_service_unavailable"})
                continue
            if intent.target_kind == "semantic_config":
                available = getattr(session.application, "semantics", None) is not None
                status.append({"scope": intent.scope, "group_id": intent.group_id,
                    "available": available, "reason": "" if available else "semantic_service_unavailable"})
                continue
            target = self._scope_target(session, intent)
            if target is None:
                status.append({"scope": intent.scope, "group_id": intent.group_id,
                               "available": False, "reason": "unsupported_scope"})
                continue
            try:
                registry.group(intent.group_id)
            except Exception:  # noqa: BLE001 - 注册表用 ConfigurationError 表示未知分组
                status.append({"scope": intent.scope, "group_id": intent.group_id,
                               "available": False, "reason": "group_not_registered"})
                continue
            status.append({"scope": intent.scope, "group_id": intent.group_id, "available": True, "reason": ""})
        return status

    def _operation(self, intent, target, owner_id):
        """为这一次迁移造一条 operation。

        operation_id 必须唯一：配置变更表的 operation_id 是主键，重复提交会被当成
        重放而静默返回旧结果——迁移场景里那意味着「点了执行却没写进去」。
        所以带上一段时间戳与作用域名，而不是一个固定的 id。

        ``owner_id`` 来自分组声明（``registry.group(...)[0].owner_id``），
        作用域用 **目标的授权作用域**——两者都与 ``configuration/service.py``
        的 patch 校验逐字对齐。
        """
        from companion_v2.foundation.refs import OperationRef, VersionRef
        stamp = self.controller.now().strftime("%Y%m%d%H%M%S%f")
        scope = intent.scope.replace(".", "-")
        # 同一组可能被拆成多条意图（role.state 拆层），它们在冻结时钟下会拿到
        # 同一时间戳；把字段名摘要并进 id，保证每条意图的 operation 唯一。
        import hashlib as _hashlib
        fields = _hashlib.sha256(
            (intent.target_kind + "|" + target.key + "|" +
             ",".join(sorted(getattr(intent, "changes", {}) or {}))).encode("utf-8")
        ).hexdigest()[:12]
        operation_id = f"legacy-{scope}-{fields}-{stamp}"
        return OperationRef(operation_id, owner_id, target.authorization_scope,
                            VersionRef(operation_id, 1), operation_id)
