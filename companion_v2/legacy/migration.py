"""V1 数据迁移：发现 → 计划 → 交给宿主执行。

这是「装完 V2 之后，原来 V1 的数据怎么办」的实现。目录
(:mod:`companion_v2.legacy.catalog`) 早先只**声明**了要支持哪些来源、每条该怎么
处置、哪几条还没写转换器——它不定位、不打开、不转换任何东西。本模块补上定位与
转换，并严格按目录里的 ``action`` 走。

## 为什么是三步

因为「哪条能迁、哪条只能读、哪条迁不动」必须先给用户看，而不是迁到一半报错。
V1 的数据分三类，处置完全不同：

* ``CONVERTED`` —— 能变成 V2 的记录。只有本插件自己的数据在这一类。
* ``COMPAT_READABLE`` —— 保持原位可读，V2 不复制它。
* 记忆伴侣历史库以 SQLite backup API 归档为完整快照；它不写入 V2 活跃记忆。
* ``RETAINED_EXTERNAL`` / ``HELD`` —— 归别的插件或别的子系统所有。本插件既不该
  搬也不该改，只能报告「它在哪、归谁管」。

## 为什么本模块不写库

配置写入在 V2 里要经过 ``ConfigurationService.patch``，而那需要**操作者身份**、
一次已登记的 operation、以及当前 edit token——三样都属于宿主层，域核心拿不到也不
该伪造。所以这里只产出 ``ConfigIntent``（作用域 + 分组 + 字段改动），由
``astrbot_host`` 侧带着真实 actor 提交。这样切有额外好处：发现与试算变成纯读、
可重复、可测，而真正写库的那一步在宿主里只有几行。

## 三条纪律

1. **只读探测**。发现阶段不写任何东西，连 V1 的库都以 ``mode=ro`` 打开。
2. **不改 V1**。单向读取 + 写入 V2；V1 数据根全程不动，迁移失败也不损坏原安装。
3. **不做无根据的转换**。V1 有而 V2 结构上放不下的值（超上限的刷新间隔、数字型
   LocationID…）由对应域的转换器产出 ``Difference``，页面据此告诉用户哪一项被改
   了、为什么——而不是悄悄改小。
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
import stat

from companion_v2.foundation.outcome import Outcome
from companion_v2.legacy.catalog import (
    LEGACY_COMPATIBILITY_CATALOG,
    CatalogAction,
    LegacyCatalogEntry,
)
from companion_v2.legacy.configuration_migration import (
    configuration_rules,
    convert_configuration,
    convert_account_configuration,
    convert_plugin_configuration,
    convert_semantic_configuration,
    plugin_configuration_rules,
    semantic_configuration_rules,
)
from companion_v2.legacy.weather_migration import convert_weather_config
from companion_v2.legacy.reality_migration import convert_reality_configuration
from companion_v2.legacy.image_configuration_migration import convert_image_configuration
from companion_v2.legacy.image_companion_configuration import convert_image_companion_configuration
from companion_v2.legacy.content_configuration import convert_content_configuration
from companion_v2.legacy.memory_configuration import convert_memory_configuration
from companion_v2.legacy.profile_source import primary_profile
from companion_v2.legacy.native_extension_configuration import (
    SOURCE_PLUGINS, REGISTRY_PLUGINS, WRITABLE_PLUGINS, REVIEW_ONLY_PLUGINS,
    convert_native_extension_configuration, preview_value, review_configuration_differences,
)
from companion_v2.legacy.sqlite_snapshot import open_readonly_sqlite_snapshot


# 目录里 ``source_paths`` 有的写成 ``host-config:<plugin_id>``：那不是文件，
# 而是「宿主配置里的一个插件配置段」，单独处理。
_CONFIG_PREFIX = "host-config:"
_PLUGIN_DATA_PREFIX = "plugin-data:"

# ``companions.json`` is one V1 storage layout, not the storage itself: the authoritative
# file or database is whatever ``storage_backend`` / ``storage_sqlite_path`` /
# ``.storage-backend-state.json`` select (see ``profile_source.primary_profile``).  These
# three sources all read that one primary profile, so the hard-coded catalog name must not
# decide presence: a SQLite-only installation looked empty, and a leftover JSON file beside
# a broken database looked ready.
_PROFILE_SOURCES = frozenset({
    "private.core_profiles",
    "private.authoritative_memory",
    "private.group_relationship_graph",
})


class MigrationState(str, Enum):
    """一个来源在发现阶段的判定结果。"""

    READY = "ready"            # 存在，转换器已就绪，可以迁
    DATA_READY = "data_ready"  # 存在，数据导入器已就绪，可在数据迁移页执行
    ABSENT = "absent"          # 这台机器上没有这份 V1 数据
    NO_HANDLER = "no_handler"  # 存在，但转换器还没写
    READ_ONLY = "read_only"    # 存在，按目录只做兼容读取，不复制
    EXTERNAL = "external"      # 存在，归别的插件所有，这里只报告
    HELD = "held"              # 存在，归属未定，暂不动
    DOCUMENTED_SKIP = "documented_skip"  # 存在，但有明确的数据边界，不自动复制
    UNREADABLE = "unreadable"  # 存在但打不开或结构不符


@dataclass(frozen=True)
class SourceFinding:
    """一个 V1 来源的探测结果。**不含数据内容**，只有结论与理由。"""

    source_id: str
    owner: str
    action: str
    state: str
    paths: tuple[str, ...]
    detail: str = ""
    byte_size: int = 0

    def document(self) -> dict:
        return {
            "source_id": self.source_id,
            "owner": self.owner,
            "action": self.action,
            "state": self.state,
            "paths": list(self.paths),
            "detail": self.detail,
            "byte_size": self.byte_size,
        }


@dataclass(frozen=True)
class ConfigIntent:
    """一条「把某作用域的某几个字段写成这些值」的意图。

    ``group_id`` 是 V2 配置分组名（如 ``state``、``contact``），``changes`` 是
    ``field_id → 值``。``scope`` 用 V2 的配置作用域名（如 ``role.state``），
    由宿主侧解析成真正的 ``ConfigTarget``——作用域到目标的推导依赖运行期身份，
    不属于本模块。
    """

    scope: str
    group_id: str
    changes: dict
    source_id: str
    confidence: dict[str, str] = field(default_factory=dict)
    sources: dict[str, tuple[str, ...]] = field(default_factory=dict)
    target_kind: str = "configuration_registry"


def _intent_value_document(intent: ConfigIntent, field_id: str, value):
    """Expose migration metadata without credentials, workflows or prompt bodies."""
    if intent.target_kind == "reality_config" and field_id == "vision_provider_id":
        return {"configured": bool(value)}
    if field_id in {"weather.api_host", "weather.credential"} or (
            intent.group_id == "weather" and field_id in {"api_host", "credential"}):
        return "（已配置，值不在此显示）" if value not in (None, "") else "（未填写）"
    if intent.target_kind in {"native_extension_config", "configuration_registry"}:
        return preview_value(intent.group_id, field_id, value)
    if intent.target_kind != "image_output_config":
        return value
    if field_id in {"comfyui", "sd_webui"} and isinstance(value, dict):
        workflows = value.get("workflows")
        return {"base_url": value.get("base_url", ""),
                "timeout_seconds": value.get("timeout_seconds"),
                "api_key_set": bool(value.get("api_key")),
                "workflow_operations": sorted(workflows) if isinstance(workflows, dict) else [],
                "parameters_set": bool(value.get("parameters"))}
    if field_id != "endpoints":
        return value
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


@dataclass
class MigrationPlan:
    """发现结果 + 试算结果。"""

    v1_root: str
    findings: tuple[SourceFinding, ...] = ()
    warnings: tuple[str, ...] = ()
    intents: tuple[ConfigIntent, ...] = ()
    differences: tuple[dict, ...] = ()

    def counts(self) -> dict:
        result: dict[str, int] = {}
        for finding in self.findings:
            result[finding.state] = result.get(finding.state, 0) + 1
        return result

    @property
    def migratable(self) -> tuple[SourceFinding, ...]:
        return tuple(item for item in self.findings if item.state == MigrationState.READY.value)

    @property
    def change_count(self) -> int:
        return sum(len(intent.changes) for intent in self.intents)

    def document(self) -> dict:
        return {
            "v1_root": self.v1_root,
            "findings": [item.document() for item in self.findings],
            "counts": self.counts(),
            "warnings": list(self.warnings),
            "change_count": self.change_count,
            "intents": [
                {"scope": item.scope, "group_id": item.group_id, "target_kind": item.target_kind,
                 "fields": sorted(item.changes),
                 "values": {field: _intent_value_document(item, field, value)
                            for field, value in sorted(item.changes.items())},
                 "confidence": dict(sorted(item.confidence.items())),
                 "sources": {key: list(value) for key, value in sorted(item.sources.items())},
                 "source_id": item.source_id}
                for item in self.intents
            ],
            "differences": list(self.differences),
            "supported_v1_versions": list(LEGACY_COMPATIBILITY_CATALOG.supported_v1_versions),
            "migratable": [item.source_id for item in self.migratable],
        }


def read_json(path: Path):
    """读一个 JSON 文件；坏文件返回 None 而不是抛错。

    迁移不该因为一份可选文件损坏就整体失败——那样用户连「还有什么能迁」都看不到。
    损坏会作为 finding 的 detail 报出去。
    """
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _size_of(paths: tuple[Path, ...]) -> int:
    total = 0
    for path in paths:
        try:
            if path.is_file():
                total += path.stat().st_size
                if path.suffix == ".db":
                    wal = Path(str(path) + "-wal")
                    if wal.is_file():
                        total += wal.stat().st_size
            elif path.is_dir():
                for child in path.rglob("*"):
                    if child.is_file():
                        total += child.stat().st_size
        except OSError:
            continue
    return total


def sqlite_tables(path: Path) -> tuple[str, ...] | None:
    """以只读方式列出表名；打不开或不是 SQLite 返回 None。

    ``mode=ro`` 是刻意的：发现阶段连一次意外写入都不该发生，而 sqlite3 的默认
    模式会为不存在的库**创建**文件——那会让「探测」变成一次对 V1 的改动。
    """
    if not path.is_file():
        return None
    try:
        connection = open_readonly_sqlite_snapshot(path)
    except sqlite3.Error:
        return None
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return tuple(row[0] for row in rows)
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def plugin_data_path(v1_root: str | Path, relative_path: str) -> Path:
    """Resolve a plugin_data path using AstrBot's runtime layout and legacy fallbacks."""
    root = Path(v1_root).expanduser()
    parts = PurePosixPath(str(relative_path).replace("\\", "/"))
    if parts.is_absolute() or not parts.parts or any(part in ("", ".", "..") for part in parts.parts):
        raise ValueError("plugin_data_path_invalid")
    candidates = (
        root.parent.parent / "plugin_data" / Path(*parts.parts),
        root.parent / Path(*parts.parts),
        root.parent / "plugin_data" / Path(*parts.parts),
        root / "data" / "plugin_data" / Path(*parts.parts),
        root / "plugin_data" / Path(*parts.parts),
    )
    for candidate in candidates:
        try:
            candidate.lstat()
            return candidate
        except OSError:
            continue
    return candidates[0]


def _locate(root: Path, source_paths: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[Path, ...]]:
    """Resolve catalog paths, including AstrBot's global plugin_data directory."""
    present: list[str] = []
    located: list[Path] = []
    for relative in source_paths:
        if relative.startswith(_CONFIG_PREFIX):
            continue
        external = relative.startswith(_PLUGIN_DATA_PREFIX)
        raw_path = relative[len(_PLUGIN_DATA_PREFIX):] if external else relative
        parts = PurePosixPath(raw_path.replace("\\", "/"))
        if parts.is_absolute() or not parts.parts or any(part in ("", ".", "..") for part in parts.parts):
            continue
        path = (plugin_data_path(root, raw_path) if external
                else root.joinpath(*parts.parts))
        try:
            path.lstat()
        except OSError:
            continue
        present.append(relative)
        located.append(path)
    return tuple(present), tuple(located)


class LegacyMigration:
    """一次迁移的发现与试算。宿主侧用 ``plan.document()`` 呈现，用
    ``plan.intents`` 写库。"""

    def __init__(self, *, v1_root: str | Path, v1_configuration=None, configuration_registry=None,
                 host_accounts=(), configuration_scope_kind=None,
                 v1_host_configuration_issue="", v1_reality_configuration=None,
                 v1_reality_configuration_issue="", v1_image_configuration=None,
                 v1_image_configuration_issue="", v1_native_configurations=None,
                 v1_native_configuration_issues=None, native_extension_baselines=None) -> None:
        self.v1_root = Path(v1_root)
        # 宿主侧的插件配置（若拿得到）。给了它，「宿主配置段」这一条来源的存在性
        # 就不必依赖数据根里有没有转储——配置本来就在宿主的配置里。
        self.v1_configuration = v1_configuration if isinstance(v1_configuration, dict) else None
        # One discovery pass asks the same question three times, and answering it reads
        # the whole snapshot, so the resolved primary profile is kept per instance.
        self._primary_profile = None
        self.v1_host_configuration_issue = str(v1_host_configuration_issue or "")[:160]
        self.v1_reality_configuration = (
            v1_reality_configuration if isinstance(v1_reality_configuration, dict) else None
        )
        self.v1_reality_configuration_issue = str(v1_reality_configuration_issue or "")[:160]
        self.v1_image_configuration = (
            v1_image_configuration if isinstance(v1_image_configuration, dict) else None
        )
        self.v1_image_configuration_issue = str(v1_image_configuration_issue or "")[:160]
        self.v1_native_configurations = {
            name: value for name, value in (v1_native_configurations or {}).items()
            if name in WRITABLE_PLUGINS | REVIEW_ONLY_PLUGINS and isinstance(value, dict)
        }
        self.v1_native_configuration_issues = {
            name: str(value)[:160] for name, value in (v1_native_configuration_issues or {}).items()
            if name in WRITABLE_PLUGINS | REVIEW_ONLY_PLUGINS and value
        }
        self.native_extension_baselines = {
            name: value for name, value in (native_extension_baselines or {}).items()
            if name in SOURCE_PLUGINS and isinstance(value, dict)
        }
        self.configuration_registry = configuration_registry
        self.host_accounts = tuple(dict(item) for item in host_accounts if isinstance(item, dict))
        if configuration_scope_kind not in (None, "private", "group"):
            raise ValueError("configuration scope kind must be private or group")
        self.configuration_scope_kind = configuration_scope_kind
        context = json.dumps({"host_accounts": self.host_accounts,
                              "configuration_scope_kind": configuration_scope_kind,
                              "v1_host_configuration": self.v1_configuration,
                              "v1_host_configuration_issue": self.v1_host_configuration_issue,
                              "v1_reality_configuration": self.v1_reality_configuration,
                              "v1_reality_configuration_issue": self.v1_reality_configuration_issue,
                              "v1_image_configuration": self.v1_image_configuration,
                              "v1_image_configuration_issue": self.v1_image_configuration_issue,
                              "v1_native_configurations": {
                                  name: value for name, value in self.v1_native_configurations.items()
                                  if name in WRITABLE_PLUGINS},
                              "v1_native_configuration_issues": {
                                  name: value for name, value in self.v1_native_configuration_issues.items()
                                  if name in WRITABLE_PLUGINS}},
                             ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                             default=str)
        self.configuration_context_fingerprint = hashlib.sha256(context.encode("utf-8")).hexdigest()

    # ── 发现 ───────────────────────────────────────────────────────────
    def discover(self) -> Outcome[tuple[SourceFinding, ...]]:
        """只读探测每个目录条目，返回结论而不是数据。"""
        if not self.v1_root.exists():
            return Outcome.failed("legacy_root_missing", f"没有找到 V1 数据目录：{self.v1_root}")
        if not self.v1_root.is_dir():
            return Outcome.failed("legacy_root_invalid", f"V1 数据位置不是一个目录：{self.v1_root}")
        return Outcome.success(tuple(self._inspect(entry) for entry in LEGACY_COMPATIBILITY_CATALOG.entries))

    def _inspect(self, entry: LegacyCatalogEntry) -> SourceFinding:
        """看一个来源在不在，并按它的 action 给出状态。

        顺序要紧：先判在不在，再判能不能迁。「不在」比「没写转换器」更基础——
        把不存在的项报成 no_handler 会让人去找一份根本没有的数据。
        """
        if entry.source_id in _PROFILE_SOURCES:
            return self._inspect_profile(entry)
        present, located = _locate(self.v1_root, entry.source_paths)
        size = _size_of(located)
        configured = any(path.startswith(_CONFIG_PREFIX) for path in entry.source_paths)
        native_name = entry.source_id.removesuffix(".configuration")
        if (configured and native_name in REGISTRY_PLUGINS
                and self.v1_native_configurations.get(native_name) is not None
                and self.configuration_registry is None):
            return SourceFinding(
                entry.source_id, entry.owner, entry.action.value,
                MigrationState.NO_HANDLER.value, entry.source_paths,
                "新版配置注册表不可用，不能安全试算。",
            )
        if (configured and native_name in SOURCE_PLUGINS
                and self.v1_native_configurations.get(native_name) is not None
                and not all(isinstance(self.native_extension_baselines.get(native_name, {}).get(key), dict)
                            for key in ("defaults", "schema"))):
            return SourceFinding(
                entry.source_id, entry.owner, entry.action.value,
                MigrationState.NO_HANDLER.value, entry.source_paths,
                "新版拓展配置 schema 不可用，不能安全试算。",
            )

        if not present:
            # 只在数据根里找文件是不够的：宿主配置段的 ``source_paths`` 写的是
            # ``host-config:<plugin_id>``，没有文件路径，而 V1 的配置转储可能就
            # 摆在数据根里。所以对这类来源要额外看一眼转储存不存在，否则会给
            # 用户报一句「这台机器上没有这份数据」——而它其实就在那儿。
            if configured and self._configuration_for(entry) is not None:
                state = self._state_for(entry)
                return SourceFinding(
                    entry.source_id, entry.owner, entry.action.value, state.value,
                    entry.source_paths,
                    "V1 的插件配置以配置转储的形式保存在数据根里。",
                    byte_size=size,
                )
            if configured and entry.source_id == "reality.configuration" and self.v1_reality_configuration_issue:
                return SourceFinding(
                    entry.source_id, entry.owner, entry.action.value,
                    MigrationState.UNREADABLE.value, entry.source_paths,
                    "V1 Reality 配置存在但无法读取；原文件保持不变。",
                )
            if configured and entry.source_id == "image.configuration" and self.v1_image_configuration_issue:
                return SourceFinding(
                    entry.source_id, entry.owner, entry.action.value,
                    MigrationState.UNREADABLE.value, entry.source_paths,
                    "V1 Image 配置存在但无法读取；原文件保持不变。",
                )
            if (configured and native_name in WRITABLE_PLUGINS | REVIEW_ONLY_PLUGINS
                    and self.v1_native_configuration_issues.get(native_name)):
                return SourceFinding(
                    entry.source_id, entry.owner, entry.action.value,
                    MigrationState.UNREADABLE.value, entry.source_paths,
                    "旧拓展配置存在但无法读取；原文件保持不变。",
                )
            if configured and entry.source_id == "private.host_configuration":
                issue = self.v1_host_configuration_issue or self._configuration_dump_issue()
                if issue:
                    return SourceFinding(
                        entry.source_id, entry.owner, entry.action.value,
                        MigrationState.UNREADABLE.value, entry.source_paths,
                        "V1 插件配置存在但无法读取；原文件保持不变。",
                    )
            return SourceFinding(
                entry.source_id, entry.owner, entry.action.value,
                MigrationState.ABSENT.value, entry.source_paths,
                "V1 数据根里没有这个路径。",
            )

        if entry.source_id == "memory.timeline_and_governance":
            try:
                info = located[0].lstat()
                unsafe = (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                          or int(getattr(info, "st_file_attributes", 0)) & 0x400)
            except OSError:
                unsafe = True
            if unsafe:
                return SourceFinding(entry.source_id, entry.owner, entry.action.value,
                    MigrationState.UNREADABLE.value, present,
                    "独立记忆库不是可安全读取的普通文件。", byte_size=size)

        for path in located:
            if path.suffix == ".db" and sqlite_tables(path) is None:
                return SourceFinding(
                    entry.source_id, entry.owner, entry.action.value,
                    MigrationState.UNREADABLE.value, present,
                    f"{path.name} 不是可读的 SQLite 库。", byte_size=size,
                )
            if path.suffix == ".json" and read_json(path) is None:
                return SourceFinding(
                    entry.source_id, entry.owner, entry.action.value,
                    MigrationState.UNREADABLE.value, present,
                    f"{path.name} 不是合法的 JSON。", byte_size=size,
                )

        state = self._state_for(entry)
        return SourceFinding(
            entry.source_id, entry.owner, entry.action.value, state.value,
            present, self._detail_for(entry, state), byte_size=size,
        )

    def _inspect_profile(self, entry: LegacyCatalogEntry) -> SourceFinding:
        """Locate one shared V1 profile source through its authoritative resolver."""
        profile = self._primary()
        state = self._state_for(entry)
        # ``companions.json`` is not everything the entry declares: ``persona_profiles``
        # is a source of its own and keeps being located the ordinary way.
        secondary, secondary_paths = _locate(self.v1_root, tuple(
            path for path in entry.source_paths if path != "companions.json"))
        if profile.payload is not None:
            return SourceFinding(
                entry.source_id, entry.owner, entry.action.value, state.value,
                (profile.selected_path(self.v1_root),) + secondary,
                self._detail_for(entry, state),
                byte_size=_size_of((profile.path,)) + _size_of(secondary_paths),
            )
        if profile.error == "core_profile_missing":
            if secondary:
                # No primary store, but the persona files are still importable.
                return SourceFinding(
                    entry.source_id, entry.owner, entry.action.value, state.value,
                    secondary, self._detail_for(entry, state),
                    byte_size=_size_of(secondary_paths),
                )
            # Nothing was ever stored here, so keep reporting absence, not breakage.
            return SourceFinding(
                entry.source_id, entry.owner, entry.action.value,
                MigrationState.ABSENT.value, entry.source_paths,
                self._detail_for(entry, MigrationState.ABSENT),
            )
        # The source is there, the selected store just cannot be read.  The resolver's
        # code is quoted as-is: the data plan reports the same string for the same file.
        return SourceFinding(
            entry.source_id, entry.owner, entry.action.value,
            MigrationState.UNREADABLE.value,
            (profile.selected_path(self.v1_root),) if profile.path is not None
            else entry.source_paths,
            f"{self._detail_for(entry, MigrationState.UNREADABLE)}（{profile.error}）",
            byte_size=_size_of((profile.path,)) if profile.path is not None else 0,
        )

    def _primary(self):
        """Resolve the V1 primary profile once per discovery pass.

        The three profile sources ask the same question, and answering it parses the
        whole snapshot: resolving it per source read a 44 MB ``companions.json`` three
        times in ``discover()`` and a fourth time in the data plan, on the request that
        renders the migration page.
        """
        if self._primary_profile is None:
            self._primary_profile = primary_profile(self.v1_root, self.v1_configuration)
        return self._primary_profile

    @staticmethod
    def _state_for(entry: LegacyCatalogEntry) -> MigrationState:
        """目录声明的处置 → 本次迁移能不能自动做。"""
        if entry.action is CatalogAction.CONVERTED:
            return (
                MigrationState.READY
                if entry.handler_id in HANDLERS
                else MigrationState.NO_HANDLER
            )
        if entry.action is CatalogAction.DATA_CONVERTED:
            return (
                MigrationState.DATA_READY
                if entry.handler_id in HANDLERS
                else MigrationState.NO_HANDLER
            )
        if entry.action is CatalogAction.NO_HANDLER:
            return MigrationState.NO_HANDLER
        if entry.action is CatalogAction.COMPAT_READABLE:
            return MigrationState.READ_ONLY
        if entry.action is CatalogAction.RETAINED_EXTERNAL:
            return MigrationState.EXTERNAL
        if entry.action is CatalogAction.HELD:
            return MigrationState.HELD
        if entry.action is CatalogAction.DOCUMENTED_SKIP:
            return MigrationState.DOCUMENTED_SKIP
        return MigrationState.NO_HANDLER

    @staticmethod
    def _detail_for(entry: LegacyCatalogEntry, state: MigrationState) -> str:
        if state is MigrationState.DOCUMENTED_SKIP:
            return entry.disposition_reason
        return {
            MigrationState.READY: "可以迁移到当前版本。",
            MigrationState.DATA_READY: "数据导入器已就绪；请在数据迁移步骤中预览并选择目标。",
            MigrationState.NO_HANDLER: "这份数据的转换器还没有写，本次迁移会跳过它。",
            MigrationState.READ_ONLY: "保持原位可读，当前版本不复制它。",
            MigrationState.EXTERNAL: f"数据归 {entry.owner} 所有，当前版本只报告它在哪里。",
            MigrationState.HELD: "归属尚未确定，暂不迁移。",
            MigrationState.DOCUMENTED_SKIP: "按数据归属与授权边界保留在旧版。",
            MigrationState.UNREADABLE: "数据存在但读不出来。",
            MigrationState.ABSENT: "这台机器上没有这份数据。",
        }[state]

    # ── 试算 ───────────────────────────────────────────────────────────
    def plan(self) -> Outcome[MigrationPlan]:
        """发现 + 试算。**不写任何东西**，可以反复调用。

        试算把「会改成什么」提前摊开：哪些项会建、哪些会被改小、哪些需要用户补输入。
        用户看到差异再决定，比迁完才发现少了东西好。

        构造时给的 ``v1_configuration`` 是 V1 的插件配置映射；没给就试着从数据根里的
        配置转储读。
        """
        discovered = self.discover()
        if not discovered.is_success:
            # Failure 是冻结 dataclass，不能解包成位置参数。
            return Outcome.failed(discovered.failure.code, discovered.failure.reason)
        findings = discovered.unwrap()

        intents: list[ConfigIntent] = []
        differences: list[dict] = []
        warnings: list[str] = []

        for finding in findings:
            name = finding.source_id.removesuffix(".configuration")
            if finding.state == MigrationState.DOCUMENTED_SKIP.value:
                differences.extend(review_configuration_differences(
                    name, self.v1_native_configurations.get(name)))
            if finding.state != MigrationState.READY.value:
                continue
            converted = self._convert(finding)
            intents.extend(converted[0])
            differences.extend(converted[1])
            warnings.extend(converted[2])

        for name, converter in (("content", convert_content_configuration),
                                ("memory", convert_memory_configuration)):
            source = next((item for item in findings
                           if item.source_id == name + ".configuration"
                           and item.state == MigrationState.READY.value), None)
            if source is None:
                continue
            converted = converter(self.v1_native_configurations.get(name), self.configuration_registry)
            differences.extend(converted.differences)
            grouped = {}
            for target, value in converted.changes.items():
                group = self.configuration_registry.fields[target].group_id
                grouped.setdefault(group, {})[target] = value
            for group, values in grouped.items():
                prior_index = next((index for index, intent in enumerate(intents)
                                    if intent.target_kind == "configuration_registry"
                                    and intent.group_id == group), None)
                prior = intents[prior_index] if prior_index is not None else None
                changes = dict(prior.changes) if prior else {}
                sources = dict(prior.sources) if prior else {}
                confidence = dict(prior.confidence) if prior else {}
                for target, value in values.items():
                    field_id = target.rsplit(".", 1)[-1]
                    if field_id in changes and changes[field_id] != value:
                        differences.append({"field": target, "kind": "source_precedence",
                                            "explain": f"旧独立 {name} 已明确配置此项，以其设置为准；本体设置作为缺省来源。"})
                    changes[field_id] = value
                    sources[field_id] = converted.sources[target]
                    confidence[field_id] = "high"
                merged = ConfigIntent(scope=group, group_id=group,
                    changes=changes, source_id=source.source_id,
                    confidence=confidence, sources=sources)
                if prior_index is None:
                    intents.append(merged)
                else:
                    intents[prior_index] = merged

        image_source = next((item for item in findings
                             if item.source_id == "image.configuration"
                             and item.state == MigrationState.READY.value), None)
        if image_source is not None:
            prior_index = next((index for index, intent in enumerate(intents)
                                if intent.target_kind == "image_output_config"), None)
            prior = intents[prior_index] if prior_index is not None else None
            converted = convert_image_companion_configuration(
                self.v1_image_configuration,
                private_output=prior.changes if prior is not None else None,
                private_sources=prior.sources if prior is not None else None,
            )
            differences.extend(converted.differences)
            if converted.changes:
                merged = ConfigIntent(
                    scope="image.output", group_id="image.output",
                    changes=converted.changes, source_id=image_source.source_id,
                    confidence={field: "high" for field in converted.changes},
                    sources=converted.sources, target_kind="image_output_config",
                )
                if prior_index is None:
                    intents.append(merged)
                else:
                    intents[prior_index] = merged
            else:
                warnings.append("独立 Image 配置没有可执行的默认生图路线；已保留旧陪伴插件原有设置。")

        return Outcome.success(MigrationPlan(
            v1_root=str(self.v1_root),
            findings=findings,
            warnings=tuple(warnings),
            intents=tuple(intents),
            differences=tuple(differences),
        ))

    def _convert(self, finding: SourceFinding) -> tuple[list[ConfigIntent], list[dict], list[str]]:
        """把一个来源转成若干 ``ConfigIntent``。

        配置规则只负责转换账本中已核实的用户设置；目标必须存在于当前 V2 注册表，
        且值通过目标字段自己的类型与范围校验。
        """
        warnings: list[str] = []
        if finding.source_id == "image.configuration" or finding.source_id.removesuffix(".configuration") in REGISTRY_PLUGINS:
            # Merge each standalone source with its matching core settings in
            # plan(), so every configuration group has one revisioned write.
            return [], [], warnings

        if finding.source_id == "reality.configuration":
            if self.v1_reality_configuration is None:
                warnings.append("读不到 V1 Reality 配置，本次没有迁移现实触及设置。")
                return [], [], warnings
            converted = convert_reality_configuration(self.v1_reality_configuration)
            warnings.extend(converted.warnings)
            if not converted.changes:
                return [], list(converted.differences), warnings
            fields = sorted(converted.changes)
            intent = ConfigIntent(
                scope="reality.settings",
                group_id="reality",
                changes=dict(converted.changes),
                source_id=finding.source_id,
                confidence={field: "high" for field in fields},
                sources={field: ("astrbot_plugin_reality_companion_config.json",) for field in fields},
                target_kind="reality_config",
            )
            return [intent], list(converted.differences), warnings

        native_name = finding.source_id.removesuffix(".configuration")
        if native_name in SOURCE_PLUGINS:
            baseline = self.native_extension_baselines.get(native_name, {})
            converted = convert_native_extension_configuration(
                native_name, self.v1_native_configurations.get(native_name),
                baseline.get("defaults"), baseline.get("schema"),
            )
            warnings.extend(converted.warnings)
            if not converted.changes:
                return [], list(converted.differences), warnings
            fields = sorted(converted.changes)
            return [ConfigIntent(
                scope=f"{native_name}.settings", group_id=native_name,
                changes=dict(converted.changes), source_id=finding.source_id,
                confidence={field: "high" for field in fields},
                sources={field: (SOURCE_PLUGINS[native_name] + "_config.json",) for field in fields},
                target_kind="native_extension_config",
            )], list(converted.differences), warnings

        if finding.source_id != "private.host_configuration":
            warnings.append(f"{finding.source_id} 的转换器还没有写，本次不会迁移它。")
            return [], [], warnings

        config = self._configuration()
        if config is None:
            warnings.append("读不到 V1 的插件配置，配置设置未能试算。")
            return [], [], warnings

        grouped: dict[str, dict] = {}
        confidence: dict[str, dict[str, str]] = {}
        sources: dict[str, dict[str, tuple[str, ...]]] = {}
        full_confidence: dict[str, str] = {}
        full_sources: dict[str, tuple[str, ...]] = {}
        weather = convert_weather_config(self._weather_configuration(config))
        if weather.is_success:
            weather_migration = weather.unwrap()
            converted_changes = dict(weather_migration.changes)
            for key in converted_changes:
                full_confidence[key] = "high"
                full_sources[key] = ("weather_config",)
            differences = [
                {"field": item.field_id, "kind": item.kind, "explain": item.summary,
                 "before": item.v1_value, "after": item.v2_value}
                for item in weather_migration.differences
            ]
        else:
            converted_changes = {}
            differences = []
            warnings.append(f"天气设置未能转换：{weather.failure.reason}")

        image_configuration = convert_image_configuration(config)
        differences.extend(image_configuration.differences)
        for field_id, value in image_configuration.preferences.items():
            target = "conversation.image_output." + field_id
            converted_changes[target] = value
            full_confidence[target] = "high"
            full_sources[target] = image_configuration.preference_sources.get(field_id, ())

        if self.configuration_registry is not None:
            configuration = convert_configuration(
                config, registry=self.configuration_registry,
                window_kind=self.configuration_scope_kind,
            )
            converted_changes.update(configuration.changes)
            differences.extend(item.document() | {"field": item.field_id, "explain": item.summary}
                               for item in configuration.differences)
            for target, level in configuration.confidence.items():
                full_confidence[target] = level
                full_sources[target] = configuration.sources[target]
            rules = configuration_rules()
            high = sum(rule["confidence"] == "high" for rule in rules)
            medium = len(rules) - high
            warnings.append(
                f"已按 {len(rules)} 条配置映射规则检查当前 V1 配置（高置信 {high} 条、中置信 {medium} 条）。"
            )

        else:
            warnings.append("当前运行档未提供配置注册表，除天气与插件级设置外的配置尚未试算。")

        plugin_configuration = convert_plugin_configuration(
            config, window_kind=self.configuration_scope_kind,
        )
        differences.extend(item.document() | {"field": item.field_id, "explain": item.summary}
                           for item in plugin_configuration.differences)
        account_configuration = convert_account_configuration(config, accounts=self.host_accounts)
        differences.extend(item.document() | {"field": item.field_id, "explain": item.summary}
                           for item in account_configuration.differences)
        plugin_rules = plugin_configuration_rules()
        plugin_high = sum(rule["confidence"] == "high" for rule in plugin_rules)
        warnings.append(
            f"另检查 {len(plugin_rules)} 条 V2 插件配置映射（高置信 {plugin_high} 条、"
            f"中置信 {len(plugin_rules) - plugin_high} 条），并按当前宿主目录试算账号、目标及人格绑定。"
        )

        semantic_configuration = convert_semantic_configuration(config)
        differences.extend(item.document() | {"field": item.field_id, "explain": item.summary}
                           for item in semantic_configuration.differences)
        semantic_rules = semantic_configuration_rules()
        semantic_high = sum(rule["confidence"] == "high" for rule in semantic_rules)
        warnings.append(
            f"另检查 {len(semantic_rules)} 条语义目录设置映射（高置信 {semantic_high} 条、"
            f"中置信 {len(semantic_rules) - semantic_high} 条）。"
        )
        audit_count = sum(bool(item.get("audit")) for item in differences)
        if audit_count:
            warnings.append(
                f"当前来源有 {audit_count} 项配置兼容说明，请按分类查看设置入口和未接续的能力。"
            )

        for key, value in converted_changes.items():
            scope, _, field_id = str(key).rpartition(".")
            if not scope or not field_id:
                continue
            grouped.setdefault(scope, {})[field_id] = value
            confidence.setdefault(scope, {})[field_id] = full_confidence.get(key, "high")
            sources.setdefault(scope, {})[field_id] = full_sources.get(key, ("weather_config",))

        intents = [
            ConfigIntent(scope=scope, group_id=GROUP_OF_SCOPE.get(scope, scope), changes=changes,
                         source_id=finding.source_id,
                         confidence=confidence.get(scope, {}), sources=sources.get(scope, {}))
            for scope, changes in sorted(grouped.items())
        ]
        plugin_changes = dict(plugin_configuration.changes)
        plugin_changes.update(account_configuration.changes)
        plugin_confidence = dict(plugin_configuration.confidence)
        plugin_confidence.update(account_configuration.confidence)
        plugin_sources = dict(plugin_configuration.sources)
        plugin_sources.update(account_configuration.sources)
        if plugin_changes:
            intents.append(ConfigIntent(
                scope="plugin_config", group_id="plugin", changes=plugin_changes,
                source_id=finding.source_id, confidence=plugin_confidence, sources=plugin_sources,
                target_kind="plugin_config",
            ))
        if semantic_configuration.changes:
            semantic_changes = {field_id.partition(".")[2]: value
                                for field_id, value in semantic_configuration.changes.items()}
            semantic_confidence = {field_id.partition(".")[2]: level
                                   for field_id, level in semantic_configuration.confidence.items()}
            semantic_sources = {field_id.partition(".")[2]: value
                                for field_id, value in semantic_configuration.sources.items()}
            intents.append(ConfigIntent(
                scope="semantic_catalog", group_id="semantic", changes=semantic_changes,
                source_id=finding.source_id, confidence=semantic_confidence, sources=semantic_sources,
                target_kind="semantic_config",
            ))
        if image_configuration.output_changes:
            intents.append(ConfigIntent(
                scope="image.output", group_id="image.output",
                changes=dict(image_configuration.output_changes),
                source_id=finding.source_id,
                confidence={field: "high" for field in image_configuration.output_changes},
                sources=dict(image_configuration.output_sources),
                target_kind="image_output_config",
            ))
        return intents, differences, warnings

    @staticmethod
    def _weather_configuration(config: dict) -> dict:
        """Weather conversion still accepts its historical flat field names.

        AstrBot supplies grouped config objects. Values inside ``weather_config`` are
        authoritative when a flat compatibility value is also present.
        """
        result = {key: value for key, value in config.items() if not isinstance(value, dict)}
        for key, value in config.items():
            if key.startswith("weather_config."):
                result.setdefault(key.partition(".")[2], value)
        nested = config.get("weather_config")
        if isinstance(nested, dict):
            result.update(nested)
        return result

    def _configuration(self):
        """V1 的插件配置：优先用宿主传进来的，其次数据根里的配置转储。

        两条路都要，因为它们的可靠性不同：宿主侧的配置是权威的，但只有宿主能读到；
        数据根里的转储谁都能读，但要求用户先导出过。任一条通就够了。
        """
        if self.v1_configuration is not None:
            return self.v1_configuration
        for name in ("companion_config.json", "plugin_config.json", "config.json"):
            path = self.v1_root / name
            if not path.is_file():
                continue
            loaded = read_json(path)
            if isinstance(loaded, dict):
                # 保留宿主的分组结构：规则按组优先读取，不能把 weather_config
                # 展平后让别组的同名字段覆盖它。
                return loaded
        return None

    def _configuration_dump_issue(self):
        """Return a diagnostic marker when a known configuration dump exists but is unusable."""
        issue = ""
        for name in ("companion_config.json", "plugin_config.json", "config.json"):
            path = self.v1_root / name
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                issue = "unreadable"
                continue
            if not path.is_file():
                issue = "unreadable"
                continue
            loaded = read_json(path)
            if isinstance(loaded, dict):
                return ""
            issue = "unreadable"
        return issue

    def _configuration_for(self, entry: LegacyCatalogEntry):
        native_name = entry.source_id.removesuffix(".configuration")
        if native_name in WRITABLE_PLUGINS | REVIEW_ONLY_PLUGINS:
            return self.v1_native_configurations.get(native_name)
        if entry.source_id == "reality.configuration":
            return self.v1_reality_configuration
        if entry.source_id == "image.configuration":
            return self.v1_image_configuration
        if entry.source_id == "private.host_configuration":
            return self._configuration()
        return None


# 已经写好的转换器。写在代码里而不是配置里：「哪个转换器实现了」是**代码事实**，
# 不是运行期状态；放进配置就会出现「配置说有、代码里没有」的假阳性。
HANDLERS = frozenset({"v2.private-config-converter", "v2.role-journal-importer",
                      "v2.role-journal-and-worldbook-importer",
                      "v2.group-relationship-importer", "v2.reaction-asset-importer",
                      "v2.memory-snapshot-archive", "v2.reality-config-migrator",
                      "v2.person-private-memory-importer", "v2.image-config-migrator",
                      "v2.native-extension-config-migrator", "v2.content-config-migrator",
                      "v2.memory-config-migrator"})

# 转换器产出的键前段（``role.state`` / ``proactive.contact``）**就是** V2 的配置
# 分组名——``role_work/state/configuration.py`` 的 ``GROUP`` 与
# ``proactive/configuration.py`` 的 ``GROUP`` 恰好取这两个值，而
# ``ConfigurationService.patch`` 用的键形状是 ``GROUP + "." + field_id``。
# 所以作用域与分组在这里是同一个字符串，不需要再维护一张映射表：
# 多一张表就多一处会与上游定义漂移的地方。
GROUP_OF_SCOPE = {
    "role.state": "role.state",
    "proactive.contact": "proactive.contact",
}
