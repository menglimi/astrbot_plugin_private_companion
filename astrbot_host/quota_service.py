"""Host-side multi-station quota orchestrator: bounded concurrency, single-flight, FX summary, and lifecycle cleanup."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from time import monotonic
from typing import Any, Callable
from urllib.parse import urlsplit

from companion_v2.model_gateway.pricing import (
    DEFAULT_FALLBACK_USD_CNY,
    PricingSettingsStore,
    effective_usd_cny_rate,
)
from companion_v2.model_gateway.quota import (
    QuotaAwarenessPolicy,
    QuotaSettingsError,
    QuotaSnapshot,
    QuotaSnapshotCache,
    QuotaStationSpec,
    QuotaStationStore,
    redact_quota_text,
    summarize_quota_portfolio,
)

from .exchange_rate import read_astrbot_http_proxy
from .pricing_inventory import collect_provider_inventory, collect_usage_consumption
from .quota_adapters import (
    AdapterProbeContext,
    AdapterRegistry,
    QuotaHttpError,
    StationLogoCache,
    default_registry,
    detect_station,
    exchange_newapi_login_for_token,
    fetch_newapi_status,
    fetch_newapi_topup_info,
    fetch_recent_consumption_logs,
    infer_newapi_fiat_conversion,
    normalize_station_root,
    resolve_newapi_display_params,
)
from .quota_adapters.base import same_origin, validate_proxy_url
from .quota_adapters.newapi import _known_alias_roots
from .service_balance_provider import _catalog as _balance_provider_catalog

logger = logging.getLogger(__name__)

MAX_PROBE_CONCURRENCY = 6
MIN_MANUAL_REFRESH_COOLDOWN_SECONDS = 15.0
AWARENESS_SNAPSHOT_FRESHNESS_CAP_SECONDS = 120.0
DEFAULT_BACKGROUND_DEBOUNCE_SECONDS = 0.05
UPSTREAM_LOGS_CACHE_TTL_SECONDS = 300.0


def _origin_key(url: str) -> str:
    try:
        parsed = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").strip().lower()
    if scheme not in ("http", "https") or not host:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is None:
        port = 443 if scheme == "https" else 80
    return f"{scheme}://{host}:{port}"


def project_legacy_service_balance(config: Any) -> QuotaStationSpec | None:
    """Project an existing single-station ``config['service_balance']`` into a virtual QuotaStationSpec.

    Allows existing users upgrading to the multi-station dashboard to immediately see and monitor
    their previously configured balance source with zero data loss or config mutation.
    """
    if not isinstance(config, dict):
        return None
    sb = config.get("service_balance")
    if not isinstance(sb, dict):
        return None

    try:
        if sb.get("advanced_enabled") is True:
            script = str(sb.get("advanced_script") or "").strip()
            if not script:
                return None
            from_astrbot = sb.get("advanced_from_astrbot") is True
            provider_id = str(sb.get("advanced_provider_id") or "").strip()
            if from_astrbot and provider_id:
                return QuotaStationSpec(
                    id="legacy-default",
                    name="已有余额源（高级脚本）",
                    adapter_id="custom_script",
                    enabled=True,
                    source_mode="astrbot_provider",
                    astrbot_provider_id=provider_id,
                    base_url="",
                    auth_token=str(sb.get("advanced_access_token") or ""),
                    user_id=str(sb.get("advanced_user_id") or ""),
                    custom_script=script,
                    participate_in_awareness=True,
                )
            base_url = str(sb.get("advanced_base_url") or "").strip()
            return QuotaStationSpec(
                id="legacy-default",
                name="已有余额源（高级脚本）",
                adapter_id="custom_script",
                enabled=True,
                source_mode="manual",
                base_url=base_url,
                auth_token=str(sb.get("advanced_access_token") or sb.get("advanced_api_key") or ""),
                user_id=str(sb.get("advanced_user_id") or ""),
                custom_script=script,
                proxy_url=str(sb.get("advanced_proxy_url") or ""),
                participate_in_awareness=True,
            )

        url = str(sb.get("url") or "").strip()
        api_key = str(sb.get("api_key") or "").strip()
        if not url and not api_key:
            return None
        user_id = str(sb.get("user_id") or "").strip()
        divisor_raw = sb.get("value_divisor", "500000")
        try:
            divisor = float(divisor_raw)
        except (TypeError, ValueError):
            divisor = 500_000.0
        return QuotaStationSpec(
            id="legacy-default",
            name="已有余额源（NewAPI）",
            adapter_id="newapi_user_id" if user_id else "newapi",
            enabled=True,
            source_mode="manual",
            base_url=normalize_station_root(url),
            auth_token=api_key,
            user_id=user_id,
            quota_per_unit=divisor if divisor > 0 else 500_000.0,
            participate_in_awareness=True,
        )
    except QuotaSettingsError:
        return None


class QuotaWatchService:
    """Host-scoped multi-station quota monitoring service."""

    def __init__(
        self,
        runtime_root: Path,
        controller: Any,
        *,
        registry: AdapterRegistry | None = None,
        now: Callable[[], datetime] | None = None,
        session_factory: Callable[..., Any] | None = None,
        manual_cooldown_seconds: float = MIN_MANUAL_REFRESH_COOLDOWN_SECONDS,
        max_concurrency: int = MAX_PROBE_CONCURRENCY,
    ):
        self.runtime_root = Path(runtime_root).resolve()
        self.controller = controller
        self.registry = registry or default_registry()
        self._now = now or getattr(controller, "now", None) or (lambda: datetime.now(timezone.utc))
        self._session_factory = session_factory
        self.manual_cooldown_seconds = max(0.0, float(manual_cooldown_seconds))
        self.max_concurrency = max(1, int(max_concurrency))

        self.store = QuotaStationStore(self.runtime_root)
        self.snapshot_cache = QuotaSnapshotCache(self.runtime_root)
        self.logo_cache = StationLogoCache(now=self._now)

        self._loop_bound: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._inflight_all: asyncio.Task[dict[str, QuotaSnapshot]] | None = None
        self._inflight_station: dict[str, asyncio.Task[QuotaSnapshot]] = {}
        self._bg_task: asyncio.Task[None] | None = None
        self._last_refresh_all_monotonic: float | None = None
        self._upstream_logs_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
        self._observed_group_ratios: dict[str, dict[str, float]] = {}

    def _current_time(self) -> datetime:
        current = self._now()
        return current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._loop_bound is not loop:
            self._semaphore = asyncio.Semaphore(self.max_concurrency)
            self._loop_bound = loop
            self._inflight_all = None
            self._inflight_station.clear()
        return self._semaphore

    def _base_controller(self) -> Any:
        return getattr(self.controller, "controller", self.controller)

    def _resolve_global_http_proxy(self) -> str:
        """Read optional AstrBot global ``http_proxy`` as lowest-priority fallback (SSRF-validated)."""
        base = self._base_controller()
        raw_proxy = read_astrbot_http_proxy(base) or ""
        if not raw_proxy:
            context = getattr(base, "context", None)
            getter = getattr(context, "get_config", None) if context is not None else None
            if callable(getter):
                try:
                    cfg = getter()
                    if isinstance(cfg, dict):
                        raw_proxy = str(cfg.get("http_proxy") or "").strip()
                except Exception:
                    raw_proxy = ""
        if not raw_proxy:
            return ""
        try:
            return validate_proxy_url(raw_proxy)
        except QuotaHttpError:
            return ""

    def _pricing_fx(self) -> tuple[str, float]:
        try:
            settings = PricingSettingsStore(self.runtime_root).read_safe()
            currency = str(settings.get("display_currency") or "CNY").upper()
            if currency not in ("CNY", "USD"):
                currency = "CNY"
            rate = effective_usd_cny_rate(settings)
            return currency, rate
        except Exception:
            return "CNY", DEFAULT_FALLBACK_USD_CNY

    def _discover_astrbot_suppliers(
        self,
        session: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Return ``(public_provider_choices, server_only_connections_by_id)``."""
        base = self._base_controller()
        context = getattr(base, "context", None)
        providers_by_id, suppliers_by_id, connections = collect_provider_inventory(base, session)

        # Group by deduplicated cluster_id (provider_source_id or standalone provider id).
        grouped_public: dict[str, dict[str, Any]] = {}
        grouped_conn: dict[str, dict[str, Any]] = {}

        for pid, info in providers_by_id.items():
            cid = str(info.get("cluster_id") or pid).strip()
            if not cid:
                continue
            conn = connections.get(pid, {})
            if cid not in grouped_public:
                grouped_public[cid] = {
                    "id": cid,
                    "label": str(info.get("supplier_name") or cid),
                    "enabled": bool(info.get("enabled", True)),
                    "api_origin": info.get("api_origin") or "",
                    "has_api_base": bool(info.get("has_api_base")),
                    "model_count": 0,
                    "models_preview": [],
                    "provider_ids": [],
                }
            entry = grouped_public[cid]
            entry["provider_ids"].append(pid)
            if info.get("enabled"):
                entry["enabled"] = True
            model = str(info.get("model") or "").strip()
            if model and model not in entry["models_preview"]:
                entry["model_count"] += 1
                if len(entry["models_preview"]) < 4:
                    entry["models_preview"].append(model)
            if cid not in grouped_conn or (not grouped_conn[cid].get("api_key") and conn.get("api_key")):
                grouped_conn[cid] = {
                    "api_base": str(conn.get("api_base") or "").strip(),
                    "api_key": str(conn.get("api_key") or "").strip(),
                    "proxy": str(conn.get("proxy") or "").strip(),
                    "supplier_name": str(conn.get("supplier_name") or cid),
                }
            # Also allow lookup by individual provider id.
            if pid not in grouped_conn and conn:
                grouped_conn[pid] = dict(conn)

        # Include any provider_sources not yet referenced by a model entry.
        for sid, src in suppliers_by_id.items():
            if sid in grouped_public or src.get("provider_type", "chat_completion") != "chat_completion":
                continue
            api_base = str(src.get("api_base") or src.get("base_url") or "").strip()
            raw_keys = src.get("key", src.get("api_key", []))
            if isinstance(raw_keys, str):
                raw_keys = [raw_keys]
            api_key = ""
            if isinstance(raw_keys, list):
                import os

                for item in raw_keys:
                    if not isinstance(item, str):
                        continue
                    val = item
                    if val.startswith("$"):
                        name = val[1:]
                        if name.startswith("{") and name.endswith("}"):
                            name = name[1:-1]
                        val = os.environ.get(name, "")
                    if val.strip():
                        api_key = val.strip()
                        break
            root = normalize_station_root(api_base)
            grouped_public[sid] = {
                "id": sid,
                "label": str(src.get("name") or src.get("label") or sid),
                "enabled": src.get("enable", True) is not False,
                "api_origin": root,
                "has_api_base": bool(root),
                "model_count": 0,
                "models_preview": [],
                "provider_ids": [sid],
            }
            grouped_conn[sid] = {
                "api_base": api_base,
                "api_key": api_key,
                "proxy": str(src.get("proxy") or "").strip(),
                "supplier_name": grouped_public[sid]["label"],
            }

        # Fallback: also inspect service_balance_provider._catalog when tests supply a minimal context.
        if not grouped_public and context is not None:
            try:
                entries, _models, _default = _balance_provider_catalog(context)
            except Exception:
                entries = []
            for src in entries:
                sid = str(src.get("id") or "").strip()
                if not sid or sid in grouped_public:
                    continue
                api_base = str(src.get("api_base") or "").strip()
                root = normalize_station_root(api_base)
                raw_keys = src.get("key", src.get("api_key", []))
                if isinstance(raw_keys, str):
                    raw_keys = [raw_keys]
                api_key = next((k.strip() for k in (raw_keys or []) if isinstance(k, str) and k.strip()), "")
                grouped_public[sid] = {
                    "id": sid,
                    "label": str(src.get("name") or sid),
                    "enabled": src.get("enable", True) is not False,
                    "api_origin": root,
                    "has_api_base": bool(root),
                    "model_count": 0,
                    "models_preview": [],
                    "provider_ids": [sid],
                }
                grouped_conn[sid] = {
                    "api_base": api_base,
                    "api_key": api_key,
                    "proxy": str(src.get("proxy") or "").strip(),
                    "supplier_name": str(src.get("name") or sid),
                }

        public_list = sorted(grouped_public.values(), key=lambda x: (not x["enabled"], x["label"].lower(), x["id"]))
        return public_list, grouped_conn

    def legacy_seed_stations(self) -> tuple[QuotaStationSpec, ...]:
        """Return projected legacy station as an atomic seed when stations.json has not yet been written."""
        if self.store.exists():
            return ()
        base = self._base_controller()
        legacy = project_legacy_service_balance(getattr(base, "config", None))
        return (legacy,) if legacy is not None else ()

    def upsert_station(
        self,
        edit_token: object,
        payload: object,
    ) -> tuple[QuotaStationSpec, list[QuotaStationSpec], QuotaAwarenessPolicy, str]:
        return self.store.upsert_station(
            edit_token,
            payload,
            seed_stations=self.legacy_seed_stations(),
        )

    def update_awareness(
        self,
        edit_token: object,
        patch: object,
    ) -> tuple[list[QuotaStationSpec], QuotaAwarenessPolicy, str]:
        return self.store.update_awareness(
            edit_token,
            patch,
            seed_stations=self.legacy_seed_stations(),
        )

    def _effective_stations(self) -> tuple[list[QuotaStationSpec], QuotaAwarenessPolicy, str, bool]:
        """Return ``(stations, awareness, edit_token, legacy_projected)``."""
        stations, awareness, token = self.store.read_safe()
        if stations or self.store.exists():
            return stations, awareness, token, False
        seed = self.legacy_seed_stations()
        if seed:
            legacy = seed[0]
            return [legacy], QuotaAwarenessPolicy(mode="primary", primary_station_id=legacy.id), token, True
        return [], awareness, token, False

    def _build_probe_context(
        self,
        spec: QuotaStationSpec,
        connections: dict[str, dict[str, Any]],
    ) -> tuple[AdapterProbeContext | None, QuotaSnapshot | None]:
        """Resolve runtime URL, credentials, and proxy without mutating or leaking secrets."""
        base_url = spec.base_url
        astrbot_api_key = ""
        proxy_url = spec.proxy_url

        if spec.source_mode == "astrbot_provider":
            conn = connections.get(spec.astrbot_provider_id)
            if conn is None or not str(conn.get("api_base") or "").strip():
                return None, QuotaSnapshot(
                    station_id=spec.id,
                    ok=False,
                    error=f"绑定的 AstrBot 供应商「{spec.astrbot_provider_id}」不存在或未配置接口地址",
                    error_code="provider_missing",
                    repair_hint="请在编辑弹窗重新选择已启用的 AstrBot 供应商，或切换为手动填写地址。",
                    checked_at=self._current_time().isoformat(),
                )
            base_url = str(conn.get("api_base") or "").strip()
            astrbot_api_key = str(conn.get("api_key") or "").strip()
            if not proxy_url:
                proxy_url = str(conn.get("proxy") or "").strip()

        if not proxy_url:
            proxy_url = self._resolve_global_http_proxy()

        ctx = AdapterProbeContext(
            base_url=base_url,
            auth_token=spec.auth_token,
            user_id=spec.user_id,
            user_id_header=spec.user_id_header,
            proxy_url=proxy_url,
            custom_script=spec.custom_script,
            display_type=spec.display_type,
            quota_per_unit=spec.quota_per_unit,
            fiat_currency=spec.fiat_currency,
            fiat_per_base_unit=spec.fiat_per_base_unit,
            allow_http=spec.allow_http,
            session_factory=self._session_factory,
            now=self._current_time,
            logo_cache=self.logo_cache,
            astrbot_api_key=astrbot_api_key,
        )
        return ctx, None

    async def probe_spec(
        self,
        spec: QuotaStationSpec,
        *,
        connections: dict[str, dict[str, Any]] | None = None,
        session: Any = None,
        use_logo_cache: bool = True,
    ) -> QuotaSnapshot:
        """Probe one station spec using its registered adapter, guarded by the concurrency semaphore."""
        if not spec.enabled:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error="站点已停用",
                error_code="disabled",
                checked_at=self._current_time().isoformat(),
            )
        adapter = self.registry.get(spec.adapter_id)
        if adapter is None:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=f"未找到协议适配器：{spec.adapter_id}",
                error_code="adapter_missing",
                repair_hint="请重新编辑该站点并选择受支持的协议类型。",
                checked_at=self._current_time().isoformat(),
            )

        if connections is None:
            _, connections = self._discover_astrbot_suppliers(session=session)
        ctx, err_snap = self._build_probe_context(spec, connections)
        if err_snap is not None or ctx is None:
            return err_snap  # type: ignore[return-value]
        if not use_logo_cache:
            ctx.logo_cache = None

        sem = self._get_semaphore()
        async with sem:
            try:
                return await adapter.probe(spec, ctx)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[CompanionV2] 额度站点探测异常 station=%s adapter=%s error_type=%s",
                    spec.id,
                    spec.adapter_id,
                    type(exc).__name__,
                )
                msg = redact_quota_text(str(exc), (ctx.auth_token, ctx.astrbot_api_key, ctx.proxy_url))
                return QuotaSnapshot(
                    station_id=spec.id,
                    ok=False,
                    error=msg or "探测过程中出现内部异常",
                    error_code="probe_error",
                    checked_at=self._current_time().isoformat(),
                )

    async def refresh_station(
        self,
        station_id: str,
        *,
        session: Any = None,
    ) -> QuotaSnapshot:
        """Single-flight refresh for one station."""
        self._get_semaphore()
        existing = self._inflight_station.get(station_id)
        if existing is not None and not existing.done():
            return await existing

        stations, _awareness, _token, _legacy = self._effective_stations()
        spec = next((st for st in stations if st.id == station_id), None)
        if spec is None:
            raise QuotaSettingsError("quota_station_not_found", "未找到指定的站点")

        async def _run() -> QuotaSnapshot:
            snap = await self.probe_spec(spec, session=session)
            self.snapshot_cache.put(snap, updated_at=self._current_time().isoformat())
            return snap

        task = asyncio.create_task(_run(), name=f"companion-quota-probe-{station_id}")
        self._inflight_station[station_id] = task
        try:
            return await task
        finally:
            if self._inflight_station.get(station_id) is task:
                self._inflight_station.pop(station_id, None)

    async def refresh_all(
        self,
        *,
        force: bool = True,
        session: Any = None,
    ) -> dict[str, QuotaSnapshot]:
        """Single-flight concurrent refresh of all enabled stations with manual debounce."""
        self._get_semaphore()
        if self._inflight_all is not None and not self._inflight_all.done():
            return await self._inflight_all

        if (
            not force
            and self._last_refresh_all_monotonic is not None
            and (monotonic() - self._last_refresh_all_monotonic) < self.manual_cooldown_seconds
        ):
            snaps, _ = self.snapshot_cache.all()
            return snaps

        async def _run() -> dict[str, QuotaSnapshot]:
            stations, _awareness, _token, _legacy = self._effective_stations()
            _, connections = self._discover_astrbot_suppliers(session=session)
            enabled_specs = [st for st in stations if st.enabled]
            if not enabled_specs:
                self._last_refresh_all_monotonic = monotonic()
                return self.snapshot_cache.all()[0]

            results = await asyncio.gather(
                *(self.probe_spec(st, connections=connections, session=session) for st in enabled_specs),
                return_exceptions=False,
            )
            now_iso = self._current_time().isoformat()
            self.snapshot_cache.put_many(results, updated_at=now_iso)
            self.snapshot_cache.prune([st.id for st in stations])
            self._last_refresh_all_monotonic = monotonic()
            return self.snapshot_cache.all()[0]

        task = asyncio.create_task(_run(), name="companion-quota-refresh-all")
        self._inflight_all = task
        try:
            return await task
        finally:
            if self._inflight_all is task:
                self._inflight_all = None

    async def refresh_for_awareness(
        self,
        specs: list[QuotaStationSpec],
        *,
        session: Any = None,
    ) -> dict[str, QuotaSnapshot]:
        """Return fresh snapshots for awareness evaluation, probing only stale/missing stations."""
        cached, _ = self.snapshot_cache.all()
        now = self._current_time()
        to_probe: list[QuotaStationSpec] = []

        for spec in specs:
            if not spec.enabled:
                continue
            snap = cached.get(spec.id)
            fresh = False
            if snap is not None and snap.checked_at:
                try:
                    checked = datetime.fromisoformat(snap.checked_at)
                    if checked.tzinfo is None:
                        checked = checked.replace(tzinfo=timezone.utc)
                    ttl = min(float(spec.interval_sec), AWARENESS_SNAPSHOT_FRESHNESS_CAP_SECONDS)
                    if 0 <= (now - checked).total_seconds() <= ttl:
                        fresh = True
                except ValueError:
                    fresh = False
            if not fresh:
                to_probe.append(spec)

        if to_probe:
            _, connections = self._discover_astrbot_suppliers(session=session)
            results = await asyncio.gather(
                *(self.probe_spec(st, connections=connections, session=session, use_logo_cache=False) for st in to_probe),
                return_exceptions=False,
            )
            merged_results: list[QuotaSnapshot] = []
            for snap in results:
                prev = cached.get(snap.station_id)
                if not snap.logo_version:
                    preserved_ver = ""
                    preserved_err = ""
                    if prev is not None and (prev.logo_version or prev.logo_error):
                        preserved_ver = prev.logo_version
                        preserved_err = prev.logo_error
                    else:
                        mem_logo = self.logo_cache.get(snap.station_id)
                        if mem_logo is not None and mem_logo.usable:
                            preserved_ver = mem_logo.version
                    if preserved_ver or preserved_err:
                        snap = QuotaSnapshot(
                            **{
                                **snap.to_dict(),
                                "logo_version": preserved_ver,
                                "logo_error": preserved_err,
                            }
                        )
                merged_results.append(snap)
            self.snapshot_cache.put_many(merged_results, updated_at=now.isoformat())
            for snap in merged_results:
                cached[snap.station_id] = snap
        return cached

    def maybe_schedule_background_refresh(self, *, session: Any = None) -> bool:
        """Schedule a non-blocking refresh if any enabled station snapshot is due."""
        stations, _awareness, _token, _legacy = self._effective_stations()
        enabled = [st for st in stations if st.enabled]
        if not enabled:
            return False
        cached, _ = self.snapshot_cache.all()
        now = self._current_time()
        any_due = False
        for st in enabled:
            snap = cached.get(st.id)
            if snap is None or not snap.checked_at:
                any_due = True
                break
            try:
                checked = datetime.fromisoformat(snap.checked_at)
                if checked.tzinfo is None:
                    checked = checked.replace(tzinfo=timezone.utc)
                if (now - checked).total_seconds() >= st.interval_sec:
                    any_due = True
                    break
            except ValueError:
                any_due = True
                break
        if not any_due:
            return False
        if self._bg_task is not None and not self._bg_task.done():
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False

        async def _bg() -> None:
            try:
                await asyncio.sleep(DEFAULT_BACKGROUND_DEBOUNCE_SECONDS)
                await self.refresh_all(force=False, session=session)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("[CompanionV2] 后台额度刷新失败 error_type=%s", type(exc).__name__)

        task = loop.create_task(_bg(), name="companion-quota-bg-refresh")
        self._bg_task = task
        task.add_done_callback(lambda t: setattr(self, "_bg_task", None) if self._bg_task is t else None)
        return True

    async def detect(
        self,
        *,
        base_url: str = "",
        astrbot_provider_id: str = "",
        allow_http: bool = False,
        proxy_url: str = "",
        session: Any = None,
    ) -> dict[str, Any]:
        """Step 1 smart detection supporting both AstrBot provider import and manual URL."""
        target_url = str(base_url or "").strip()
        api_key = ""
        effective_proxy = str(proxy_url or "").strip()
        suggested_provider_label = ""

        if astrbot_provider_id:
            public_list, connections = self._discover_astrbot_suppliers(session=session)
            conn = connections.get(astrbot_provider_id)
            if conn is None or not str(conn.get("api_base") or "").strip():
                raise QuotaSettingsError("quota_provider_missing", "所选 AstrBot 供应商不存在或未配置接口地址")
            target_url = str(conn.get("api_base") or "").strip()
            api_key = str(conn.get("api_key") or "").strip()
            if not effective_proxy:
                effective_proxy = str(conn.get("proxy") or "").strip()
            pub = next((item for item in public_list if item["id"] == astrbot_provider_id), None)
            suggested_provider_label = str(pub["label"]) if pub else astrbot_provider_id

        if not effective_proxy:
            effective_proxy = self._resolve_global_http_proxy()

        result = await detect_station(
            target_url,
            allow_http=allow_http,
            proxy_url=effective_proxy,
            api_key=api_key,
            session_factory=self._session_factory,
        )
        data = result.to_dict()
        if suggested_provider_label and (not data["suggested_name"] or data["confidence"] != "high"):
            data["suggested_name"] = suggested_provider_label
        elif suggested_provider_label and data["adapter_id"] in ("deepseek", "openrouter", "siliconflow", "moonshot"):
            data["suggested_name"] = suggested_provider_label
        data["astrbot_provider_id"] = astrbot_provider_id
        return data

    @staticmethod
    def _can_reuse_stored_token(existing: QuotaStationSpec, raw: dict[str, Any]) -> bool:
        """Allow reusing a persisted auth_token in test_draft only on same source/origin without script escalation."""
        next_source_mode = str(raw.get("source_mode") or existing.source_mode or "manual").strip() or "manual"
        next_adapter = str(raw.get("adapter_id") or existing.adapter_id or "").strip()
        if next_source_mode != existing.source_mode:
            return False
        if existing.adapter_id != "custom_script" and next_adapter == "custom_script":
            return False
        if existing.adapter_id == "custom_script" and next_adapter == "custom_script":
            if str(raw.get("custom_script") or "").strip() != existing.custom_script.strip():
                return False
        if next_source_mode == "astrbot_provider":
            return str(raw.get("astrbot_provider_id") or "").strip() == existing.astrbot_provider_id
        next_url = str(raw.get("base_url") or "").strip()
        if not existing.base_url and not next_url and existing.adapter_id == "custom_script":
            return True
        return same_origin(existing.base_url, next_url)

    async def test_draft(
        self,
        payload: dict[str, Any],
        *,
        session: Any = None,
    ) -> dict[str, Any]:
        """Step 2 draft test without saving; reuses stored secrets only on same origin/source."""
        if type(payload) is not dict:
            raise QuotaSettingsError("quota_config_invalid", "测试参数无效")
        raw = dict(payload)
        station_id = str(raw.get("id") or "").strip()
        if station_id:
            stations, _, _, _ = self._effective_stations()
            existing = next((st for st in stations if st.id == station_id), None)
            if existing is not None:
                if (
                    not str(raw.get("auth_token") or "").strip()
                    and not raw.get("clear_auth_token")
                    and self._can_reuse_stored_token(existing, raw)
                ):
                    raw["auth_token"] = existing.auth_token
                if not str(raw.get("proxy_url") or "").strip() and not raw.get("clear_proxy_url"):
                    raw["proxy_url"] = existing.proxy_url
        if not raw.get("id"):
            raw["id"] = "draft-preview"
        if not str(raw.get("name") or "").strip():
            raw["name"] = "草稿站点"
        raw.pop("clear_auth_token", None)
        raw.pop("clear_proxy_url", None)
        raw["enabled"] = True

        spec = QuotaStationSpec.from_dict(raw)
        snap = await self.probe_spec(spec, session=session, use_logo_cache=False)
        return {
            "tested": True,
            "snapshot": snap.to_dict(),
        }

    async def exchange_token(
        self,
        *,
        base_url: str = "",
        astrbot_provider_id: str = "",
        username: str = "",
        password: str = "",
        user_id_header: str = "New-Api-User",
        allow_http: bool = False,
        proxy_url: str = "",
        session: Any = None,
    ) -> dict[str, Any]:
        """One-time login exchange returning access_token & user_id; password is never persisted."""
        target_url = str(base_url or "").strip()
        effective_proxy = str(proxy_url or "").strip()
        if astrbot_provider_id:
            _, connections = self._discover_astrbot_suppliers(session=session)
            conn = connections.get(astrbot_provider_id)
            if not target_url:
                if conn is None or not str(conn.get("api_base") or "").strip():
                    raise QuotaSettingsError("quota_provider_missing", "所选 AstrBot 供应商不存在或未配置接口地址")
                target_url = str(conn.get("api_base") or "").strip()
            if not effective_proxy and conn is not None:
                effective_proxy = str(conn.get("proxy") or "").strip()

        if not effective_proxy:
            effective_proxy = self._resolve_global_http_proxy()

        try:
            return await exchange_newapi_login_for_token(
                target_url,
                username,
                password,
                user_id_header=user_id_header or "New-Api-User",
                allow_http=allow_http,
                proxy_url=effective_proxy,
                session_factory=self._session_factory,
            )
        except QuotaHttpError as exc:
            raise QuotaSettingsError(exc.code, str(exc)) from exc

    def _collect_provider_usage(self, runtime: Any, session: Any) -> dict[str, dict[str, Any]]:
        """Summarize plugin-side call counts & estimated tokens per AstrBot supplier cluster."""
        base = self._base_controller()
        try:
            providers_by_id, _suppliers, _conns = collect_provider_inventory(base, session)
            target_ctx = getattr(getattr(session, "profile", None), "target", None)
            by_provider, _by_provider_model = collect_usage_consumption(runtime, target_ctx)
        except Exception:
            return {}

        by_cluster: dict[str, dict[str, Any]] = {}
        for pid, row in (by_provider or {}).items():
            if not isinstance(row, dict):
                continue
            info = providers_by_id.get(pid, {})
            cid = str(info.get("cluster_id") or pid).strip()
            if not cid:
                continue
            agg = by_cluster.setdefault(
                cid,
                {"calls": 0, "requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            )
            agg["calls"] += int(row.get("calls") or 0)
            agg["requests"] += int(row.get("requests") or 0)
            agg["input_tokens"] += int(row.get("input_tokens") or 0)
            agg["output_tokens"] += int(row.get("output_tokens") or 0)
            agg["total_tokens"] += int(row.get("total_tokens") or 0)
        return by_cluster

    def get_dashboard_payload(self, *, runtime: Any = None, session: Any = None) -> dict[str, Any]:
        """Assemble the complete secret-free dashboard response."""
        stations, awareness, edit_token, legacy_projected = self._effective_stations()
        snaps, updated_at = self.snapshot_cache.all()
        display_currency, usd_cny_rate = self._pricing_fx()
        summary = summarize_quota_portfolio(
            stations,
            snaps,
            display_currency=display_currency,
            usd_cny_rate=usd_cny_rate,
        )
        astrbot_providers, _connections = self._discover_astrbot_suppliers(session=session)
        provider_usage = self._collect_provider_usage(runtime, session) if runtime is not None else {}

        return {
            "stations": [st.to_public_dict() for st in stations],
            "snapshots": {sid: snap.to_dict() for sid, snap in snaps.items()},
            "summary": summary,
            "awareness": awareness.to_dict(),
            "legacy_projected": legacy_projected,
            "adapters": self.registry.list_descriptors(),
            "astrbot_providers": astrbot_providers,
            "provider_usage": provider_usage,
            "updated_at": updated_at or summary["latest_checked_at"],
            "edit_token": edit_token,
        }

    def get_icon_payload(self, station_id: str) -> dict[str, Any]:
        sid = str(station_id or "").strip()
        if not sid:
            raise QuotaSettingsError("quota_request_invalid", "缺少站点 ID")
        entry = self.logo_cache.get(sid)
        if entry is None or not entry.usable:
            return {"station_id": sid, "available": False, "version": "", "mime_type": "", "data_url": ""}
        return {
            "station_id": sid,
            "available": True,
            "version": entry.version,
            "mime_type": entry.mime_type,
            "data_url": entry.data_url(),
        }

    def invalidate_upstream_logs(self, station_id: str = "") -> None:
        sid = str(station_id or "").strip()
        if not sid:
            self._upstream_logs_cache.clear()
            return
        stale = [key for key in self._upstream_logs_cache if key[0] == sid]
        for key in stale:
            self._upstream_logs_cache.pop(key, None)

    def resolve_station_for_origin(self, origin: str, *, session: Any = None) -> dict[str, Any] | None:
        """Read-only memory bridge exposing matched NewAPI station metadata to PricingCatalogService.

        Matches normalized origin, known syuan.org aliases, and cached /api/status server_address.
        Credentials stay strictly in memory and MUST NEVER be persisted into pricing settings.
        """
        target_root = normalize_station_root(origin)
        target_keys = {_origin_key(r) for r in _known_alias_roots(target_root)}
        target_keys.discard("")
        if not target_keys:
            return None

        stations, _awareness, _tok, _legacy = self._effective_stations()
        snaps, _ = self.snapshot_cache.all()
        _, connections = self._discover_astrbot_suppliers(session=session)

        candidates: list[tuple[int, QuotaStationSpec, str, str, QuotaSnapshot | None]] = []
        for spec in stations:
            if not spec.enabled or spec.adapter_id not in ("newapi", "newapi_user_id"):
                continue
            resolved_base = spec.base_url
            resolved_proxy = spec.proxy_url
            if spec.source_mode == "astrbot_provider":
                conn = connections.get(spec.astrbot_provider_id) or {}
                resolved_base = str(conn.get("api_base") or resolved_base).strip()
                if not resolved_proxy:
                    resolved_proxy = str(conn.get("proxy") or "").strip()
            if not resolved_proxy:
                resolved_proxy = self._resolve_global_http_proxy()

            snap = snaps.get(spec.id)
            station_keys = {_origin_key(r) for r in _known_alias_roots(normalize_station_root(resolved_base))}
            if snap is not None and snap.server_address:
                station_keys.update(_origin_key(r) for r in _known_alias_roots(normalize_station_root(snap.server_address)))
            station_keys.discard("")
            if not (station_keys & target_keys):
                continue
            priority = 0 if spec.auth_token else 1
            candidates.append((priority, spec, resolved_base, resolved_proxy, snap))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        _prio, spec, resolved_base, resolved_proxy, snap = candidates[0]
        resolved_fiat_currency = spec.fiat_currency or (snap.inferred_fiat_currency if snap else "")
        resolved_fiat_pbu = (
            spec.fiat_per_base_unit
            if spec.fiat_per_base_unit is not None
            else (snap.site_recharge_price if (snap and resolved_fiat_currency == "CNY") else None)
        )
        return {
            "station_id": spec.id,
            "station_name": spec.name,
            "adapter_id": spec.adapter_id,
            "base_url": normalize_station_root(resolved_base),
            "server_address": snap.server_address if snap else "",
            "auth_token": spec.auth_token,
            "user_id": spec.user_id,
            "user_id_header": spec.user_id_header or "New-Api-User",
            "proxy_url": resolved_proxy,
            "allow_http": spec.allow_http,
            "group_name": snap.group_name if snap else "",
            "observed_group_ratios": dict(self._observed_group_ratios.get(spec.id, {})),
            "fiat_currency": resolved_fiat_currency,
            "fiat_per_base_unit": resolved_fiat_pbu,
            "custom_currency_exchange_rate": snap.custom_exchange_rate if snap else None,
            "custom_currency_symbol": (snap.symbol if (snap and snap.display_type == "CREDIT") else ""),
        }

    @staticmethod
    def _summarize_upstream_log_rows(
        raw_payload: dict[str, Any],
        *,
        spec: QuotaStationSpec,
        days: int,
        token_name: str,
        cached: bool,
        fetched_at: str,
        supported_stations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        all_rows = raw_payload.get("rows") or []
        token_names = sorted({str(r.get("token_name") or "").strip() for r in all_rows if str(r.get("token_name") or "").strip()})
        wanted_token = str(token_name or "").strip()
        rows = [r for r in all_rows if r.get("token_name") == wanted_token] if wanted_token else list(all_rows)

        wallet_calls = 0
        sub_calls = 0
        wallet_cost = 0.0
        sub_cost = 0.0
        wallet_fiat = 0.0
        sub_fiat = 0.0
        has_fiat = False
        prompt_tokens = 0
        completion_tokens = 0
        cache_tokens = 0
        observed_groups: dict[str, float] = {}

        for row in rows:
            cost = float(row.get("cost") or 0.0)
            fcost = row.get("fiat_cost")
            if fcost is not None:
                has_fiat = True
            if row.get("billing_source") == "subscription":
                sub_calls += 1
                sub_cost += cost
                if fcost is not None:
                    sub_fiat += float(fcost)
            else:
                wallet_calls += 1
                wallet_cost += cost
                if fcost is not None:
                    wallet_fiat += float(fcost)
            prompt_tokens += int(row.get("prompt_tokens") or 0)
            completion_tokens += int(row.get("completion_tokens") or 0)
            cache_tokens += int(row.get("cache_tokens") or 0)
            grp = str(row.get("group") or "").strip()
            gratio = row.get("group_ratio")
            if grp and gratio is not None:
                observed_groups[grp] = float(gratio)

        denom = prompt_tokens if prompt_tokens >= cache_tokens else (prompt_tokens + cache_tokens)
        cache_hit_rate = round((cache_tokens / denom) * 100.0, 1) if denom > 0 else 0.0

        return {
            "station_id": spec.id,
            "station_name": spec.name,
            "stations": supported_stations,
            "days": days,
            "token_name": wanted_token,
            "token_names": token_names,
            "display_type": raw_payload.get("display_type") or "USD",
            "symbol": raw_payload.get("symbol") or "",
            "inferred_fiat_currency": raw_payload.get("inferred_fiat_currency") or "",
            "fiat_per_base_unit": raw_payload.get("fiat_per_base_unit"),
            "total_calls": len(rows),
            "wallet_calls": wallet_calls,
            "subscription_calls": sub_calls,
            "wallet_cost": round(wallet_cost, 6),
            "subscription_cost": round(sub_cost, 6),
            "total_cost": round(wallet_cost + sub_cost, 6),
            "wallet_fiat_cost": round(wallet_fiat, 6) if has_fiat else None,
            "subscription_fiat_cost": round(sub_fiat, 6) if has_fiat else None,
            "total_fiat_cost": round(wallet_fiat + sub_fiat, 6) if has_fiat else None,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cache_tokens": cache_tokens,
            "cache_hit_rate": cache_hit_rate,
            "observed_groups": observed_groups,
            "pages_fetched": int(raw_payload.get("pages_fetched") or 0),
            "truncated": bool(raw_payload.get("truncated", False)),
            "cached": cached,
            "fetched_at": fetched_at,
            "items": rows[:40],
            "counted_in_private_companion_budget": False,
        }

    async def get_upstream_logs(
        self,
        *,
        station_id: str = "",
        days: int = 7,
        token_name: str = "",
        force: bool = False,
        session: Any = None,
    ) -> dict[str, Any]:
        """Read-only upstream NewAPI reconciliation summary with 5-min cache (<=5 pages / <=200 rows)."""
        days_clamped = max(1, min(int(days or 7), 30))
        stations, _awareness, _tok, _legacy = self._effective_stations()
        newapi_stations = [
            st for st in stations if st.enabled and st.adapter_id in ("newapi", "newapi_user_id")
        ]
        supported_list = [
            {"id": st.id, "name": st.name, "adapter_id": st.adapter_id}
            for st in newapi_stations
        ]

        wanted_id = str(station_id or "").strip()
        if not wanted_id:
            if not newapi_stations:
                return {
                    "station_id": "",
                    "station_name": "",
                    "stations": [],
                    "days": days_clamped,
                    "token_name": "",
                    "token_names": [],
                    "total_calls": 0,
                    "wallet_calls": 0,
                    "subscription_calls": 0,
                    "wallet_cost": 0.0,
                    "subscription_cost": 0.0,
                    "total_cost": 0.0,
                    "wallet_fiat_cost": None,
                    "subscription_fiat_cost": None,
                    "total_fiat_cost": None,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cache_tokens": 0,
                    "cache_hit_rate": 0.0,
                    "observed_groups": {},
                    "pages_fetched": 0,
                    "truncated": False,
                    "cached": False,
                    "fetched_at": self._current_time().isoformat(),
                    "items": [],
                    "counted_in_private_companion_budget": False,
                }
            spec = newapi_stations[0]
        else:
            spec = next((st for st in stations if st.id == wanted_id), None)
            if spec is None:
                raise QuotaSettingsError("quota_station_not_found", "未找到指定的站点")
            if spec.adapter_id not in ("newapi", "newapi_user_id"):
                raise QuotaSettingsError("quota_upstream_logs_unsupported", "仅 NewAPI / OneAPI 系站点支持拉取上游消费日志对照")

        cache_key = (spec.id, days_clamped)
        now_mono = monotonic()
        cached_entry = self._upstream_logs_cache.get(cache_key)
        if not force and cached_entry is not None:
            ts_mono, cached_payload = cached_entry
            if (now_mono - ts_mono) < UPSTREAM_LOGS_CACHE_TTL_SECONDS:
                return self._summarize_upstream_log_rows(
                    cached_payload,
                    spec=spec,
                    days=days_clamped,
                    token_name=token_name,
                    cached=True,
                    fetched_at=str(cached_payload.get("fetched_at") or ""),
                    supported_stations=supported_list,
                )

        _, connections = self._discover_astrbot_suppliers(session=session)
        ctx, err_snap = self._build_probe_context(spec, connections)
        if err_snap is not None or ctx is None:
            raise QuotaSettingsError("quota_provider_missing", err_snap.error if err_snap else "无法解析站点连接")
        token = ctx.auth_token.strip()
        if not token:
            raise QuotaSettingsError("missing_credentials", "该站点尚未填写系统访问令牌")

        root = normalize_station_root(ctx.base_url)
        site_status = await fetch_newapi_status(
            root,
            allow_http=ctx.allow_http,
            proxy_url=ctx.proxy_url,
            timeout_seconds=6.0,
            session_factory=ctx.session_factory,
        )
        params = resolve_newapi_display_params(
            site_status,
            override_display_type=ctx.display_type,
            override_per_unit=ctx.quota_per_unit,
        )
        topup_info = (
            await fetch_newapi_topup_info(
                root,
                token=token,
                user_id=ctx.user_id,
                user_id_header=ctx.user_id_header,
                allow_http=ctx.allow_http,
                proxy_url=ctx.proxy_url,
                timeout_seconds=5.0,
                session_factory=ctx.session_factory,
            )
            if params.display_type == "CREDIT"
            else None
        )
        inferred_fiat_cur, _site_price, effective_fiat_pbu = infer_newapi_fiat_conversion(
            params,
            site_status,
            topup_info,
            override_fiat_currency=ctx.fiat_currency,
            override_fiat_per_base_unit=ctx.fiat_per_base_unit,
        )

        now_dt = self._current_time()
        start_ts = int((now_dt - timedelta(days=days_clamped)).timestamp())
        try:
            raw_logs = await fetch_recent_consumption_logs(
                root,
                token=token,
                user_id=ctx.user_id,
                user_id_header=ctx.user_id_header,
                per_unit=params.per_unit,
                display_type=params.display_type,
                display_multiplier=params.display_multiplier,
                symbol=params.symbol,
                inferred_fiat_currency=inferred_fiat_cur,
                effective_fiat_pbu=effective_fiat_pbu,
                max_pages=5,
                page_size=40,
                start_timestamp=start_ts,
                allow_http=ctx.allow_http,
                proxy_url=ctx.proxy_url,
                timeout_seconds=ctx.timeout_seconds,
                session_factory=ctx.session_factory,
            )
        except QuotaHttpError as exc:
            raise QuotaSettingsError(exc.code, str(exc)) from exc

        fetched_at = now_dt.isoformat()
        raw_logs["fetched_at"] = fetched_at
        self._upstream_logs_cache[cache_key] = (monotonic(), raw_logs)

        obs_groups: dict[str, float] = {}
        for row in raw_logs.get("rows") or []:
            grp = str(row.get("group") or "").strip()
            ratio = row.get("group_ratio")
            if grp and ratio is not None:
                obs_groups[grp] = float(ratio)
        if obs_groups:
            self._observed_group_ratios[spec.id] = obs_groups

        return self._summarize_upstream_log_rows(
            raw_logs,
            spec=spec,
            days=days_clamped,
            token_name=token_name,
            cached=False,
            fetched_at=fetched_at,
            supported_stations=supported_list,
        )

    async def close(self) -> None:
        tasks: list[asyncio.Task[Any]] = []
        if self._bg_task is not None and not self._bg_task.done():
            self._bg_task.cancel()
            tasks.append(self._bg_task)
        if self._inflight_all is not None and not self._inflight_all.done():
            self._inflight_all.cancel()
            tasks.append(self._inflight_all)
        for task in list(self._inflight_station.values()):
            if not task.done():
                task.cancel()
                tasks.append(task)
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._bg_task = None
        self._inflight_all = None
        self._inflight_station.clear()
        self._upstream_logs_cache.clear()
        self._observed_group_ratios.clear()


def get_quota_watch_service(
    controller: Any,
    runtime_root: Path | str,
    *,
    session_factory: Callable[..., Any] | None = None,
) -> QuotaWatchService:
    """Return or lazily create the host-scoped QuotaWatchService bound to ``controller``."""
    base = getattr(controller, "controller", controller)
    services = getattr(base, "_quota_watch_services", None)
    if not isinstance(services, dict):
        services = {}
        try:
            setattr(base, "_quota_watch_services", services)
        except Exception:
            pass
    key = str(Path(runtime_root).resolve())
    service = services.get(key)
    if service is None:
        service = QuotaWatchService(
            Path(runtime_root),
            base,
            now=getattr(base, "now", None),
            session_factory=session_factory,
        )
        services[key] = service
    return service


async def close_quota_watch_services(owner: Any) -> None:
    """Cancel and clear any QuotaWatchService instances attached to a host controller."""
    services = getattr(owner, "_quota_watch_services", None)
    if not isinstance(services, dict):
        return
    for service in list(services.values()):
        close_fn = getattr(service, "close", None)
        if callable(close_fn):
            try:
                await close_fn()
            except Exception:
                logger.debug("[CompanionV2] 关闭多站点额度服务失败", exc_info=True)
    services.clear()
