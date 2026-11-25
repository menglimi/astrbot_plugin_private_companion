"""Unified 5-tier performance tuning profiles with sparse overrides and Anti-OOM guards.

Stores only ``profile`` (``auto`` / ``eco`` / ``balanced`` / ``turbo`` / ``compat``)
and sparse ``overrides`` (fields explicitly customized by the operator). Any field
not present in ``overrides`` automatically tracks the active preset baseline.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import sys
from threading import Lock
from typing import Any

from companion_v2.storage.sqlite_tuning import (
    HIGH_MEMORY_THRESHOLD_BYTES,
    LOW_MEMORY_THRESHOLD_BYTES,
    ULTRA_MEMORY_THRESHOLD_BYTES,
    VALID_SYNCHRONOUS_MODES,
    SqliteTuningProfile,
    detect_cgroup_memory_limit_bytes,
    detect_cpu_count,
    detect_host_memory_bytes,
    detect_total_memory_bytes,
    set_active_sqlite_tuning_profile,
    tuning_profile as auto_sqlite_profile,
)

_MIB = 1024 * 1024
_GIB = 1024 * 1024 * 1024

PROFILE_IDS = ("auto", "eco", "balanced", "turbo", "compat")
CONCRETE_PROFILE_IDS = ("eco", "balanced", "turbo", "compat")
DEFAULT_PROFILE = "auto"


@dataclass(frozen=True, slots=True)
class TuningProfileMeta:
    """User-facing description for one performance preset tier."""

    id: str
    label: str
    badge: str
    summary: str
    recommended_for: str
    preview_cache: str
    preview_concurrency: str
    preview_disk: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "badge": self.badge,
            "summary": self.summary,
            "recommended_for": self.recommended_for,
            "preview_cache": self.preview_cache,
            "preview_concurrency": self.preview_concurrency,
            "preview_disk": self.preview_disk,
        }


PROFILE_CATALOG: tuple[TuningProfileMeta, ...] = (
    TuningProfileMeta(
        id="auto",
        label="自动推荐",
        badge="默认",
        summary="根据本机可用内存与 CPU 核心数自动匹配，随时可手动切换或微调。",
        recommended_for="绝大多数用户，省心免折腾",
        preview_cache="按硬件自适应",
        preview_concurrency="按核心数自适应",
        preview_disk="按环境自动平衡",
    ),
    TuningProfileMeta(
        id="eco",
        label="省内存轻量化",
        badge="低配友好",
        summary="严控内存占用、关闭内存映射并缩小连接池，杜绝低配机器内存不足闪退。",
        recommended_for="1–2 GB 云服务器 / 手机 Termux / 小内存设备",
        preview_cache="主库 8 MB · 关映射",
        preview_concurrency="2 查库 · 2 后台 · 1 语音",
        preview_disk="常规写盘 · 80 ms 合并",
    ),
    TuningProfileMeta(
        id="balanced",
        label="平衡推荐",
        badge="日常主力",
        summary="流畅度与内存占用折中，常规对话、多窗口并发与后台任务都稳定顺手。",
        recommended_for="4–8 GB 日常电脑 / 标准服务器",
        preview_cache="主库 32 MB · 128 MB 映射",
        preview_concurrency="4 查库 · 3 后台 · 3 语音",
        preview_disk="常规写盘 · 50 ms 合并",
    ),
    TuningProfileMeta(
        id="turbo",
        label="高性能流畅",
        badge="极速响应",
        summary="尽量将热数据与索引留在内存与系统缓存中，提升并发能力，追求最低延迟。",
        recommended_for="16 GB+ 大内存 + 固态硬盘 / 多群高并发",
        preview_cache="主库 128 MB · 大内存映射",
        preview_concurrency="6–8 查库 · 5 后台 · 4 语音",
        preview_disk="常规写盘 · 30 ms 合并",
    ),
    TuningProfileMeta(
        id="compat",
        label="兼容稳妥",
        badge="护盘防卡",
        summary="强制关闭内存映射、降低并发读盘并采用高安全写盘，专治机械硬盘与网络盘卡顿。",
        recommended_for="机械硬盘（HDD）/ NAS 网络盘 / 老旧磁盘 / 杀软频繁扫描",
        preview_cache="主库 16 MB · 强关映射",
        preview_concurrency="2 查库 · 2 后台 · 2 语音",
        preview_disk="高安全 FULL · 100 ms 合并",
    ),
)

PROFILE_META_BY_ID: dict[str, TuningProfileMeta] = {item.id: item for item in PROFILE_CATALOG}


@dataclass(frozen=True, slots=True)
class TuningGroupMeta:
    id: str
    title: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title, "description": self.description}


TUNING_GROUPS: tuple[TuningGroupMeta, ...] = (
    TuningGroupMeta(
        id="database",
        title="数据库读写",
        description="控制 SQLite 缓存、内存映射、只读并发通道与写盘安全策略。",
    ),
    TuningGroupMeta(
        id="concurrency",
        title="任务与并发",
        description="控制写入合并节奏、后台并发任务数以及多段语音同时合成数量。",
    ),
    TuningGroupMeta(
        id="maintenance",
        title="归档与清理",
        description="控制运行明细的保留天数，配合数据库瘦身保持主库苗条。",
    ),
)


@dataclass(frozen=True, slots=True)
class TuningFieldSpec:
    """Declarative specification for one tunable performance setting."""

    key: str
    group: str
    label: str
    kind: str  # "int" or "enum"
    unit: str = ""
    minimum: int | None = None
    maximum: int | None = None
    step: int = 1
    options: tuple[tuple[str, str], ...] = ()
    hot_reload: bool = True
    effect_hint: str = ""
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "group": self.group,
            "label": self.label,
            "kind": self.kind,
            "unit": self.unit,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
            "options": [{"value": val, "label": title} for val, title in self.options],
            "hot_reload": self.hot_reload,
            "apply_mode": "立即热生效" if self.hot_reload else "下次打开副库生效",
            "effect_hint": self.effect_hint,
            "aliases": list(self.aliases),
        }


TUNING_FIELDS: tuple[TuningFieldSpec, ...] = (
    TuningFieldSpec(
        key="cache_size_mb",
        group="database",
        label="主库内存缓存",
        kind="int",
        unit="MB",
        minimum=4,
        maximum=512,
        step=4,
        hot_reload=True,
        effect_hint="存在内存里的常用数据量。调大：翻聊天记录和状态更快，但多占内存；调小：更省内存，但硬盘读得更勤。",
        aliases=("主库缓存", "SQLite缓存", "cache_size", "内存缓存"),
    ),
    TuningFieldSpec(
        key="sidecar_cache_size_mb",
        group="database",
        label="副库内存缓存",
        kind="int",
        unit="MB",
        minimum=2,
        maximum=128,
        step=2,
        hot_reload=False,
        effect_hint="存图库、语义索引、归档等附属库的缓存。调大：搜记忆与翻图库更顺；调小：给低配机腾出更多内存。",
        aliases=("副库缓存", "附属库缓存", "sidecar_cache"),
    ),
    TuningFieldSpec(
        key="mmap_size_mb",
        group="database",
        label="数据库内存映射",
        kind="int",
        unit="MB",
        minimum=0,
        maximum=2048,
        step=32,
        hot_reload=True,
        effect_hint="让系统把数据库当内存直接读。调大：固态硬盘上大库提速明显；设为 0：最省内存，也最适合机械硬盘或网络盘防卡顿。",
        aliases=("内存映射", "mmap", "mmap_size"),
    ),
    TuningFieldSpec(
        key="sidecar_mmap_size_mb",
        group="database",
        label="副库内存映射",
        kind="int",
        unit="MB",
        minimum=0,
        maximum=512,
        step=16,
        hot_reload=False,
        effect_hint="附属库的内存映射上限。调大：提速附属库检索；设为 0：兼容老旧磁盘与小内存设备。",
        aliases=("副库映射", "sidecar_mmap"),
    ),
    TuningFieldSpec(
        key="reader_pool_size",
        group="database",
        label="同时查库通道数",
        kind="int",
        unit="个",
        minimum=1,
        maximum=12,
        step=1,
        hot_reload=True,
        effect_hint="允许几个页面或群聊同时查库不排队。调大：多群并发更顺，略增内存；调小：减少机械硬盘同时寻道卡顿。",
        aliases=("连库数", "读连接池", "只读连接", "reader_pool_size"),
    ),
    TuningFieldSpec(
        key="journal_size_limit_mb",
        group="database",
        label="写入日志截断阈值",
        kind="int",
        unit="MB",
        minimum=8,
        maximum=256,
        step=8,
        hot_reload=True,
        effect_hint="临时写入文件（WAL）超过多大就自动合并回主库。调大：高频写入时少打断；调小：省硬盘空间且重启更快。",
        aliases=("WAL上限", "日志截断", "journal_size_limit"),
    ),
    TuningFieldSpec(
        key="synchronous",
        group="database",
        label="写盘安全策略",
        kind="enum",
        options=(
            ("NORMAL", "常规（推荐 · 速度快且日常安全）"),
            ("FULL", "最稳（每笔刷盘 · 断电不丢但稍慢）"),
        ),
        hot_reload=True,
        effect_hint="常规（NORMAL）：日常够安全且写入快；最稳（FULL）：每笔都立刻落盘，突发断电也不丢数据，但机械硬盘会稍慢。",
        aliases=("写盘策略", "同步模式", "synchronous", "断电保护"),
    ),
    TuningFieldSpec(
        key="write_batch_window_ms",
        group="concurrency",
        label="写入合并等待窗口",
        kind="int",
        unit="ms",
        minimum=0,
        maximum=500,
        step=10,
        hot_reload=True,
        effect_hint="把毫秒内的零碎统计记录攒成一笔写盘。调大：更护硬盘省 IO；调小或设 0：落盘更即时，写盘次数变多。",
        aliases=("批量写入", "合并写入", "write_batch_window_ms"),
    ),
    TuningFieldSpec(
        key="background_slots",
        group="concurrency",
        label="后台并发任务数",
        kind="int",
        unit="个",
        minimum=1,
        maximum=8,
        step=1,
        hot_reload=True,
        effect_hint="同时跑几个日记、反思、主动联系等后台任务。调大：积压消化快但更吃 CPU 与模型并发；调小：系统更安静平稳。",
        aliases=("后台任务并发", "后台槽位", "background_slots"),
    ),
    TuningFieldSpec(
        key="tts_max_concurrency",
        group="concurrency",
        label="语音同时合成数",
        kind="int",
        unit="个",
        minimum=1,
        maximum=6,
        step=1,
        hot_reload=True,
        effect_hint="多段语音回复同时生成几段。调大：长回复发语音更快；调小：避免触发语音接口限流或占满带宽。",
        aliases=("TTS并发", "语音并发", "tts_max_concurrency"),
    ),
    TuningFieldSpec(
        key="telemetry_ttl_days",
        group="maintenance",
        label="运行明细保留天数",
        kind="int",
        unit="天",
        minimum=3,
        maximum=365,
        step=1,
        hot_reload=True,
        effect_hint="超过天数的调用明细在瘦身时归档到冷库（每日统计完整保留）。调小：主库更苗条；调大：保留更久明细便于排查。",
        aliases=("遥测保留", "明细保留", "冷归档天数", "telemetry_ttl_days"),
    ),
)

TUNING_FIELD_BY_KEY: dict[str, TuningFieldSpec] = {spec.key: spec for spec in TUNING_FIELDS}

_CONCRETE_BASELINES: dict[str, dict[str, Any]] = {
    "eco": {
        "cache_size_mb": 8,
        "sidecar_cache_size_mb": 4,
        "mmap_size_mb": 0,
        "sidecar_mmap_size_mb": 0,
        "reader_pool_size": 2,
        "journal_size_limit_mb": 16,
        "synchronous": "NORMAL",
        "write_batch_window_ms": 80,
        "background_slots": 2,
        "tts_max_concurrency": 1,
        "telemetry_ttl_days": 7,
    },
    "balanced": {
        "cache_size_mb": 32,
        "sidecar_cache_size_mb": 8,
        "mmap_size_mb": 128,
        "sidecar_mmap_size_mb": 32,
        "reader_pool_size": 4,
        "journal_size_limit_mb": 32,
        "synchronous": "NORMAL",
        "write_batch_window_ms": 50,
        "background_slots": 3,
        "tts_max_concurrency": 3,
        "telemetry_ttl_days": 14,
    },
    "turbo": {
        "cache_size_mb": 128,
        "sidecar_cache_size_mb": 32,
        "mmap_size_mb": 512,
        "sidecar_mmap_size_mb": 256,
        "reader_pool_size": 6,
        "journal_size_limit_mb": 64,
        "synchronous": "NORMAL",
        "write_batch_window_ms": 30,
        "background_slots": 5,
        "tts_max_concurrency": 4,
        "telemetry_ttl_days": 30,
    },
    "compat": {
        "cache_size_mb": 16,
        "sidecar_cache_size_mb": 8,
        "mmap_size_mb": 0,
        "sidecar_mmap_size_mb": 0,
        "reader_pool_size": 2,
        "journal_size_limit_mb": 16,
        "synchronous": "FULL",
        "write_batch_window_ms": 100,
        "background_slots": 2,
        "tts_max_concurrency": 2,
        "telemetry_ttl_days": 14,
    },
}


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    """Detected host / container hardware summary."""

    effective_memory_bytes: int | None
    host_memory_bytes: int | None
    cgroup_limit_bytes: int | None
    cpu_count: int
    is_32bit: bool
    recommended_profile: str
    recommendation_reason: str

    @property
    def cgroup_limited(self) -> bool:
        return (
            self.cgroup_limit_bytes is not None
            and (
                self.host_memory_bytes is None
                or self.cgroup_limit_bytes < self.host_memory_bytes
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "effective_memory_bytes": self.effective_memory_bytes,
            "effective_memory_gib": (
                round(self.effective_memory_bytes / _GIB, 1)
                if self.effective_memory_bytes is not None
                else None
            ),
            "host_memory_bytes": self.host_memory_bytes,
            "cgroup_limit_bytes": self.cgroup_limit_bytes,
            "cgroup_limited": self.cgroup_limited,
            "cpu_count": self.cpu_count,
            "is_32bit": self.is_32bit,
            "recommended_profile": self.recommended_profile,
            "recommended_profile_label": PROFILE_META_BY_ID[self.recommended_profile].label,
            "recommendation_reason": self.recommendation_reason,
        }


@dataclass(frozen=True, slots=True)
class ClampedFieldNotice:
    """Explain why a requested tuning value was clamped for host safety."""

    field: str
    label: str
    requested: Any
    effective: Any
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "label": self.label,
            "requested": self.requested,
            "effective": self.effective,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PerformancePlan:
    """Resolved performance settings ready to apply to SQLite, batcher, tasks, and TTS."""

    selected_profile: str
    resolved_profile: str
    hardware: HardwareProfile
    baseline: dict[str, Any]
    overrides: dict[str, Any]
    values: dict[str, Any]
    clamped_fields: tuple[ClampedFieldNotice, ...]
    sqlite_profile: SqliteTuningProfile

    @property
    def override_count(self) -> int:
        return len(self.overrides)

    def to_payload(self, *, lite_page_enabled: bool = False, lite_page_effective: bool | None = None) -> dict[str, Any]:
        effective_lite = bool(lite_page_enabled if lite_page_effective is None else lite_page_effective)
        selected_meta = PROFILE_META_BY_ID[self.selected_profile]
        resolved_meta = PROFILE_META_BY_ID[self.resolved_profile]
        baselines = {
            profile_id: dict(_baseline_for_profile(profile_id, self.hardware)[1])
            for profile_id in PROFILE_IDS
        }
        return {
            "profile": self.selected_profile,
            "profile_label": selected_meta.label,
            "resolved_profile": self.resolved_profile,
            "resolved_profile_label": resolved_meta.label,
            "overrides": dict(self.overrides),
            "override_count": self.override_count,
            "baseline": dict(self.baseline),
            "baselines": baselines,
            "values": dict(self.values),
            "clamped_fields": [item.to_dict() for item in self.clamped_fields],
            "hardware": self.hardware.to_dict(),
            "profiles": [item.to_dict() for item in PROFILE_CATALOG],
            "groups": [item.to_dict() for item in TUNING_GROUPS],
            "fields": [spec.to_dict() for spec in TUNING_FIELDS],
            "lite_page": {
                "saved_enabled": bool(lite_page_enabled),
                "effective_enabled": effective_lite,
                "reload_required": bool(lite_page_enabled) != effective_lite,
            },
        }


def detect_hardware_profile(
    *,
    total_memory_bytes: int | None = None,
    host_memory_bytes: int | None = None,
    cgroup_limit_bytes: int | None = None,
    cpu_count: int | None = None,
    is_32bit: bool | None = None,
) -> HardwareProfile:
    """Inspect host RAM (including Linux container cgroup limits) and CPU count."""
    if total_memory_bytes is not None:
        effective_mem: int | None = int(total_memory_bytes) if total_memory_bytes > 0 else None
        host_mem = int(host_memory_bytes) if host_memory_bytes is not None and host_memory_bytes > 0 else effective_mem
        cg_mem = int(cgroup_limit_bytes) if cgroup_limit_bytes is not None and cgroup_limit_bytes > 0 else None
        if cg_mem is not None and effective_mem is not None:
            effective_mem = min(effective_mem, cg_mem)
    else:
        host_mem = int(host_memory_bytes) if host_memory_bytes is not None else detect_host_memory_bytes()
        cg_mem = int(cgroup_limit_bytes) if cgroup_limit_bytes is not None else detect_cgroup_memory_limit_bytes()
        if host_mem is not None and cg_mem is not None:
            effective_mem = min(host_mem, cg_mem)
        elif cg_mem is not None:
            effective_mem = cg_mem
        elif host_mem is not None:
            effective_mem = host_mem
        else:
            effective_mem = detect_total_memory_bytes()

    cpus = detect_cpu_count() if cpu_count is None else max(1, int(cpu_count))
    bit32 = (sys.maxsize <= 2**32) if is_32bit is None else bool(is_32bit)

    if effective_mem is None:
        recommended = "eco"
        reason = f"未能读取本机可用内存（{cpus} 核），为稳妥起见自动推荐「省内存轻量化」；如内存充裕可随时手动切换。"
    else:
        gib = effective_mem / _GIB
        cgroup_note = "（已按容器内存上限折算）" if (cg_mem is not None and (host_mem is None or cg_mem < host_mem)) else ""
        if effective_mem < LOW_MEMORY_THRESHOLD_BYTES:
            recommended = "eco"
            reason = f"识别到可用内存约 {gib:.1f} GB{cgroup_note}、{cpus} 核，推荐「省内存轻量化」以防内存紧张。"
        elif effective_mem < ULTRA_MEMORY_THRESHOLD_BYTES:
            recommended = "balanced"
            reason = f"识别到可用内存约 {gib:.1f} GB{cgroup_note}、{cpus} 核，推荐「平衡推荐」兼顾速度与内存占用。"
        else:
            recommended = "turbo"
            reason = f"识别到可用内存约 {gib:.1f} GB{cgroup_note}、{cpus} 核，内存充裕，推荐「高性能流畅」获得更低延迟。"
        if bit32:
            reason += "当前为 32 位运行环境，已自动关闭内存映射。"

    return HardwareProfile(
        effective_memory_bytes=effective_mem,
        host_memory_bytes=host_mem,
        cgroup_limit_bytes=cg_mem,
        cpu_count=cpus,
        is_32bit=bit32,
        recommended_profile=recommended,
        recommendation_reason=reason,
    )


def _validate_override_value(spec: TuningFieldSpec, raw: Any) -> Any:
    if spec.kind == "enum":
        if type(raw) is not str:
            raise ValueError(f"invalid value for {spec.key}")
        normalized = raw.strip().upper()
        allowed = {opt for opt, _ in spec.options}
        if normalized not in allowed:
            raise ValueError(f"unsupported value for {spec.key}")
        return normalized
    if type(raw) is not int:
        raise ValueError(f"integer required for {spec.key}")
    assert spec.minimum is not None and spec.maximum is not None
    if raw < spec.minimum or raw > spec.maximum:
        raise ValueError(f"{spec.key} out of range [{spec.minimum}, {spec.maximum}]")
    return raw


def normalize_tuning_config(raw: Any, *, strict: bool = False) -> tuple[str, dict[str, Any]]:
    """Validate and normalize ``config['performance']`` into ``(profile, sparse_overrides)``."""
    if not isinstance(raw, Mapping):
        if strict and raw is not None:
            raise ValueError("performance tuning config must be an object")
        return DEFAULT_PROFILE, {}
    section = raw.get("performance") if "performance" in raw and isinstance(raw.get("performance"), Mapping) else raw

    raw_profile = section.get("profile", DEFAULT_PROFILE)
    if raw_profile is None or raw_profile == "":
        profile = DEFAULT_PROFILE
    elif isinstance(raw_profile, str) and raw_profile.strip().lower() in PROFILE_IDS:
        profile = raw_profile.strip().lower()
    elif strict:
        raise ValueError("invalid performance profile")
    else:
        profile = DEFAULT_PROFILE

    raw_overrides = section.get("overrides")
    overrides: dict[str, Any] = {}
    if raw_overrides is None:
        return profile, overrides
    if not isinstance(raw_overrides, Mapping):
        if strict:
            raise ValueError("overrides must be an object")
        return profile, overrides

    for key, value in raw_overrides.items():
        if value is None:
            continue
        spec = TUNING_FIELD_BY_KEY.get(str(key))
        if spec is None:
            if strict:
                raise ValueError(f"unknown tuning field: {key}")
            continue
        try:
            overrides[spec.key] = _validate_override_value(spec, value)
        except ValueError:
            if strict:
                raise
    return profile, overrides


def _baseline_for_profile(
    selected_profile: str,
    hardware: HardwareProfile,
) -> tuple[str, dict[str, Any], SqliteTuningProfile | None]:
    """Compute baseline values and optional exact auto SqliteTuningProfile."""
    if selected_profile == "auto":
        auto_sql = auto_sqlite_profile(hardware.effective_memory_bytes, cpu_count=hardware.cpu_count)
        resolved = hardware.recommended_profile
        base_extras = _CONCRETE_BASELINES[resolved]
        baseline = {
            "cache_size_mb": max(4, auto_sql.cache_size_kib // 1024),
            "sidecar_cache_size_mb": max(2, auto_sql.sidecar_cache_size_kib // 1024),
            "mmap_size_mb": auto_sql.mmap_size // _MIB,
            "sidecar_mmap_size_mb": auto_sql.sidecar_mmap_size // _MIB,
            "reader_pool_size": auto_sql.reader_pool_size,
            "journal_size_limit_mb": max(8, auto_sql.journal_size_limit // _MIB),
            "synchronous": auto_sql.effective_synchronous(),
            "write_batch_window_ms": base_extras["write_batch_window_ms"],
            "background_slots": base_extras["background_slots"],
            "tts_max_concurrency": base_extras["tts_max_concurrency"],
            "telemetry_ttl_days": base_extras["telemetry_ttl_days"],
        }
        return resolved, baseline, auto_sql

    resolved = selected_profile if selected_profile in CONCRETE_PROFILE_IDS else "balanced"
    baseline = dict(_CONCRETE_BASELINES[resolved])
    if resolved == "turbo":
        if hardware.effective_memory_bytes is not None and hardware.effective_memory_bytes >= 20 * _GIB:
            baseline["mmap_size_mb"] = 1024
        baseline["reader_pool_size"] = min(8, max(6, hardware.cpu_count // 2))
    return resolved, baseline, None


def _anti_oom_limits(hardware: HardwareProfile) -> dict[str, tuple[int, str]]:
    """Return dynamic safety ceilings keyed by field name."""
    limits: dict[str, tuple[int, str]] = {}
    if hardware.is_32bit:
        limits["mmap_size_mb"] = (0, "当前为 32 位运行环境，为防虚拟地址空间耗尽，内存映射已安全限制为 0 MB。")
        limits["sidecar_mmap_size_mb"] = (0, "当前为 32 位运行环境，副库内存映射已安全限制为 0 MB。")
    mem = hardware.effective_memory_bytes
    if mem is not None and mem > 0:
        gib = mem / _GIB
        cache_cap = max(4, min(512, int((mem * 0.10) // _MIB)))
        sidecar_cache_cap = max(2, min(128, int((mem * 0.05) // _MIB)))
        limits["cache_size_mb"] = (
            cache_cap,
            f"为防止内存不足，按本机约 {gib:.1f} GB 可用内存的 10% 安全限幅至 {cache_cap} MB。",
        )
        limits["sidecar_cache_size_mb"] = (
            sidecar_cache_cap,
            f"为防止内存不足，按本机约 {gib:.1f} GB 可用内存的 5% 安全限幅至 {sidecar_cache_cap} MB。",
        )
        if not hardware.is_32bit:
            mmap_cap = max(0, min(2048, int((mem * 0.25) // _MIB)))
            sidecar_mmap_cap = max(0, min(512, int((mem * 0.10) // _MIB)))
            limits["mmap_size_mb"] = (
                mmap_cap,
                f"为防止内存占用过高，按本机约 {gib:.1f} GB 可用内存的 25% 安全限幅至 {mmap_cap} MB。",
            )
            limits["sidecar_mmap_size_mb"] = (
                sidecar_mmap_cap,
                f"为防止内存占用过高，按本机约 {gib:.1f} GB 可用内存的 10% 安全限幅至 {sidecar_mmap_cap} MB。",
            )
    return limits


def resolve_performance_plan(
    config: Any = None,
    *,
    total_memory_bytes: int | None = None,
    host_memory_bytes: int | None = None,
    cgroup_limit_bytes: int | None = None,
    cpu_count: int | None = None,
    is_32bit: bool | None = None,
    strict: bool = False,
) -> PerformancePlan:
    """Resolve effective settings, Anti-OOM clamps, and SqliteTuningProfile."""
    selected_profile, raw_overrides = normalize_tuning_config(config, strict=strict)
    hardware = detect_hardware_profile(
        total_memory_bytes=total_memory_bytes,
        host_memory_bytes=host_memory_bytes,
        cgroup_limit_bytes=cgroup_limit_bytes,
        cpu_count=cpu_count,
        is_32bit=is_32bit,
    )
    resolved_profile, baseline, auto_sql = _baseline_for_profile(selected_profile, hardware)

    # Keep sparse overrides strictly sparse: drop overrides equal to baseline only on save if caller wishes,
    # while preserving explicit user overrides in memory so switching profiles remains transparent.
    effective: dict[str, Any] = dict(baseline)
    effective.update(raw_overrides)

    clamped: list[ClampedFieldNotice] = []
    for field_key, (cap, reason) in _anti_oom_limits(hardware).items():
        current = effective[field_key]
        if isinstance(current, int) and current > cap:
            spec = TUNING_FIELD_BY_KEY[field_key]
            clamped.append(
                ClampedFieldNotice(
                    field=field_key,
                    label=spec.label,
                    requested=current,
                    effective=cap,
                    reason=reason,
                )
            )
            effective[field_key] = cap

    # Preserve exact reader_cache_size_kib for untouched auto profiles so existing hardware profiles stay identical.
    cache_kib = int(effective["cache_size_mb"]) * 1024
    if auto_sql is not None and "cache_size_mb" not in raw_overrides and not any(c.field == "cache_size_mb" for c in clamped):
        reader_cache_kib = auto_sql.reader_cache_size_kib
        tier_label = auto_sql.tier
    else:
        reader_cache_kib = min(cache_kib, 65536) if cache_kib > 65536 else cache_kib
        tier_label = resolved_profile

    sync_mode = str(effective["synchronous"]).upper()
    if sync_mode not in VALID_SYNCHRONOUS_MODES:
        sync_mode = "NORMAL"

    sqlite_prof = SqliteTuningProfile(
        tier=tier_label,
        reader_pool_size=int(effective["reader_pool_size"]),
        cache_size_kib=cache_kib,
        reader_cache_size_kib=reader_cache_kib,
        mmap_size=int(effective["mmap_size_mb"]) * _MIB,
        sidecar_cache_size_kib=int(effective["sidecar_cache_size_mb"]) * 1024,
        sidecar_mmap_size=int(effective["sidecar_mmap_size_mb"]) * _MIB,
        journal_size_limit=int(effective["journal_size_limit_mb"]) * _MIB,
        synchronous=sync_mode,
    )

    return PerformancePlan(
        selected_profile=selected_profile,
        resolved_profile=resolved_profile,
        hardware=hardware,
        baseline=baseline,
        overrides=dict(raw_overrides),
        values=effective,
        clamped_fields=tuple(clamped),
        sqlite_profile=sqlite_prof,
    )


def compact_sparse_overrides(
    selected_profile: str,
    overrides: Mapping[str, Any],
    *,
    hardware: HardwareProfile | None = None,
) -> dict[str, Any]:
    """Strip any override that equals the selected profile's baseline."""
    hw = hardware or detect_hardware_profile()
    _, baseline, _ = _baseline_for_profile(selected_profile, hw)
    return {
        key: value
        for key, value in overrides.items()
        if key in TUNING_FIELD_BY_KEY and value is not None and baseline.get(key) != value
    }


_ACTIVE_PLAN_LOCK = Lock()
_ACTIVE_PLAN: PerformancePlan | None = None


def set_active_performance_plan(plan: PerformancePlan | None) -> None:
    """Publish the active performance plan for sidecars, TTS, and telemetry compaction."""
    global _ACTIVE_PLAN
    with _ACTIVE_PLAN_LOCK:
        _ACTIVE_PLAN = plan
    set_active_sqlite_tuning_profile(plan.sqlite_profile if plan is not None else None)


def active_performance_plan() -> PerformancePlan | None:
    with _ACTIVE_PLAN_LOCK:
        return _ACTIVE_PLAN


def clear_active_performance_plan() -> None:
    """Reset the active performance plan (primarily for tests)."""
    set_active_performance_plan(None)


def active_tts_max_concurrency(*, default: int = 3) -> int:
    plan = active_performance_plan()
    if plan is None:
        return default
    value = plan.values.get("tts_max_concurrency")
    return value if isinstance(value, int) and 1 <= value <= 6 else default


def active_telemetry_ttl_days(*, default: int = 14) -> int:
    plan = active_performance_plan()
    if plan is None:
        return default
    value = plan.values.get("telemetry_ttl_days")
    return value if isinstance(value, int) and 3 <= value <= 365 else default


def active_write_batch_window_ms(*, default: int = 50) -> int:
    plan = active_performance_plan()
    if plan is None:
        return default
    value = plan.values.get("write_batch_window_ms")
    return value if isinstance(value, int) and 0 <= value <= 500 else default
