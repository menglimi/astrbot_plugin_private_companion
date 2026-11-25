"""Server-only AstrBot provider inventory, supplier clustering, usage aggregation,
and strictly sorted Pricing v2 view builder.

Security guarantees:
- Merges AstrBot ``provider_sources`` + ``provider`` strictly server-side.
- NEVER serializes API keys, raw key lists, or proxy credentials into any
  public payload returned by API routes.
- Always scopes ``astrbot_core_reply_observations`` with ``WHERE role_key = ?``
  and aggregates in SQLite via ``json_each`` (never loads giant ``record_json``
  blobs into Python and never truncates with ``LIMIT 200``).
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
import sqlite3
from typing import Any

from companion_v2.model_gateway.core_observations import available as core_obs_available, role_key
from companion_v2.model_gateway.pricing import (
    DEFAULT_PRICING_TIMEZONE,
    EXCHANGE_RATE_TTL_SECONDS,
    effective_rate_source,
    effective_usd_cny_rate,
    is_rate_stale,
    lookup_cluster_multiplier,
    lookup_pricing_schedule,
    resolve_effective_pricing,
    resolve_tariff,
)
from companion_v2.model_gateway.pricing_catalog import (
    PUBLIC_SOURCE_SPECS,
    catalog_presets,
    find_candidates,
    match_builtin_tariff,
    select_auto_candidate,
)
from companion_v2.model_gateway.pricing_schedule import COMMON_TIMEZONES, schedule_is_active

from .pricing_sources import newapi_pricing_url, safe_origin_label


def _resolve_secret_key(raw_keys: object) -> str:
    """Resolve first non-empty API key (including ``$ENV`` / ``${ENV}``) server-side ONLY."""
    if isinstance(raw_keys, str):
        items = [raw_keys]
    elif isinstance(raw_keys, list):
        items = raw_keys
    else:
        return ""
    for item in items:
        if type(item) is not str:
            continue
        candidate = item.strip()
        if not candidate:
            continue
        if candidate.startswith("$"):
            env_name = candidate[1:]
            if env_name.startswith("{") and env_name.endswith("}"):
                env_name = env_name[1:-1]
            candidate = os.environ.get(env_name, "").strip()
        if candidate:
            return candidate
    return ""


def _extract_model_name(entry: dict[str, Any]) -> str:
    model_cfg = entry.get("model_config")
    if isinstance(model_cfg, dict):
        m = str(model_cfg.get("model") or model_cfg.get("model_id") or "").strip()
        if m:
            return m
    return str(entry.get("model") or entry.get("model_id") or "").strip()


def _extract_candidate_models(entry: dict[str, Any], primary_model: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def _add(val: object) -> None:
        if type(val) is not str:
            return
        clean = val.strip()
        if clean and clean not in seen:
            seen.add(clean)
            found.append(clean)

    _add(primary_model)
    for key in ("models", "model_list", "available_models"):
        raw = entry.get(key)
        if isinstance(raw, list):
            for item in raw:
                if type(item) is str:
                    _add(item)
                elif isinstance(item, dict):
                    _add(item.get("id") or item.get("model") or item.get("name"))
    model_cfg = entry.get("model_config")
    if isinstance(model_cfg, dict):
        _add(model_cfg.get("model"))
    return found


def collect_provider_inventory(
    controller: Any,
    session: Any = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Discover configured & loaded AstrBot providers and suppliers.

    Returns ``(providers_by_id, suppliers_by_id, server_only_connections)``.
    ``server_only_connections`` holds API keys and proxy URLs and MUST NEVER be
    serialized into HTTP responses.
    """
    context = getattr(controller, "context", None)
    cfg: dict[str, Any] = {}
    if context is not None and callable(getattr(context, "get_config", None)):
        try:
            loaded_cfg = context.get_config()
            if isinstance(loaded_cfg, dict):
                cfg = loaded_cfg
        except Exception:
            cfg = {}

    manager = getattr(context, "provider_manager", None) if context is not None else None
    raw_sources: list[dict[str, Any]] = []
    raw_providers: list[dict[str, Any]] = []

    for src_list in (
        cfg.get("provider_sources"),
        getattr(manager, "provider_sources_config", None),
    ):
        if isinstance(src_list, list):
            for item in src_list:
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                    raw_sources.append(item)

    for prov_list in (
        cfg.get("provider"),
        getattr(manager, "providers_config", None),
    ):
        if isinstance(prov_list, list):
            for item in prov_list:
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                    raw_providers.append(item)

    # Deduplicate suppliers by id (preferring first seen from live config)
    suppliers_by_id: dict[str, dict[str, Any]] = {}
    for src in raw_sources:
        sid = src["id"].strip()
        if sid not in suppliers_by_id:
            suppliers_by_id[sid] = dict(src)

    providers_by_id: dict[str, dict[str, Any]] = {}
    connections: dict[str, dict[str, Any]] = {}

    # 1. Configured providers from cmd_config / provider_manager
    for prov in raw_providers:
        pid = prov["id"].strip()
        if pid in providers_by_id:
            continue
        provider_type = str(prov.get("provider_type") or "chat_completion").strip()
        if provider_type and provider_type != "chat_completion":
            continue

        source_id = str(prov.get("provider_source_id") or "").strip()
        source_entry = suppliers_by_id.get(source_id) if source_id else None
        merged = {**(source_entry or {}), **prov}

        primary_model = _extract_model_name(merged)
        candidates = _extract_candidate_models(merged, primary_model)
        cluster_id = source_id or pid
        supplier_name = (
            str((source_entry or {}).get("name") or (source_entry or {}).get("label") or source_id).strip()
            if source_id
            else str(prov.get("name") or prov.get("label") or pid).strip()
        ) or cluster_id

        api_base = str(merged.get("api_base") or merged.get("base_url") or "").strip()
        api_key = _resolve_secret_key(merged.get("key", merged.get("api_key")))
        proxy = str(merged.get("proxy") or "").strip()
        pricing_url = newapi_pricing_url(api_base)

        connections[pid] = {
            "api_base": api_base,
            "api_key": api_key,
            "proxy": proxy,
            "supplier_name": supplier_name,
            "cluster_id": cluster_id,
        }
        providers_by_id[pid] = {
            "id": pid,
            "configured": True,
            "enabled": bool(prov.get("enable", True)),
            "loaded": False,
            "provider_source_id": source_id or None,
            "cluster_id": cluster_id,
            "supplier_name": supplier_name,
            "model": primary_model,
            "candidates": candidates,
            "has_api_base": bool(pricing_url),
            "api_origin": safe_origin_label(api_base) if pricing_url else None,
        }

    # 2. Loaded V2 router providers
    router = getattr(getattr(session, "application", None), "router", None)
    for item in getattr(router, "providers", {}).values():
        pid = str(getattr(getattr(item, "binding", None), "provider", "") or "").strip()
        if not pid:
            continue
        model_name = str(getattr(item, "model", "") or "").strip()
        if pid not in providers_by_id:
            providers_by_id[pid] = {
                "id": pid,
                "configured": True,
                "enabled": True,
                "loaded": True,
                "provider_source_id": None,
                "cluster_id": pid,
                "supplier_name": pid,
                "model": model_name,
                "candidates": [model_name] if model_name else [],
                "has_api_base": False,
                "api_origin": None,
            }
        else:
            providers_by_id[pid]["loaded"] = True
            if not providers_by_id[pid]["model"] and model_name:
                providers_by_id[pid]["model"] = model_name
            if model_name and model_name not in providers_by_id[pid]["candidates"]:
                providers_by_id[pid]["candidates"].append(model_name)

    # 3. Loaded AstrBot native providers via get_all_providers()
    getter = getattr(context, "get_all_providers", None) if context is not None else None
    if callable(getter):
        try:
            for native in list(getter() or []):
                meta = native.meta() if callable(getattr(native, "meta", None)) else None
                native_cfg = getattr(native, "provider_config", None)
                native_cfg = native_cfg if isinstance(native_cfg, dict) else {}
                pid = str(
                    getattr(meta, "id", "") or getattr(native, "id", "") or native_cfg.get("id", "")
                ).strip()
                if not pid:
                    continue
                model_name = ""
                get_model = getattr(native, "get_model", None)
                if callable(get_model):
                    try:
                        model_name = str(get_model() or "").strip()
                    except Exception:
                        model_name = ""
                if not model_name:
                    model_name = _extract_model_name(native_cfg)
                if pid not in providers_by_id:
                    source_id = str(native_cfg.get("provider_source_id") or "").strip()
                    source_entry = suppliers_by_id.get(source_id) if source_id else None
                    merged = {**(source_entry or {}), **native_cfg}
                    api_base = str(merged.get("api_base") or merged.get("base_url") or "").strip()
                    api_key = _resolve_secret_key(merged.get("key", merged.get("api_key")))
                    proxy = str(merged.get("proxy") or "").strip()
                    pricing_url = newapi_pricing_url(api_base)
                    cluster_id = source_id or pid
                    supplier_name = (
                        str((source_entry or {}).get("name") or source_id).strip()
                        if source_id
                        else str(native_cfg.get("name") or pid).strip()
                    ) or cluster_id
                    connections[pid] = {
                        "api_base": api_base,
                        "api_key": api_key,
                        "proxy": proxy,
                        "supplier_name": supplier_name,
                        "cluster_id": cluster_id,
                    }
                    providers_by_id[pid] = {
                        "id": pid,
                        "configured": True,
                        "enabled": bool(native_cfg.get("enable", True)),
                        "loaded": True,
                        "provider_source_id": source_id or None,
                        "cluster_id": cluster_id,
                        "supplier_name": supplier_name,
                        "model": model_name,
                        "candidates": _extract_candidate_models(merged, model_name),
                        "has_api_base": bool(pricing_url),
                        "api_origin": safe_origin_label(api_base) if pricing_url else None,
                    }
                else:
                    providers_by_id[pid]["loaded"] = True
                    if not providers_by_id[pid]["model"] and model_name:
                        providers_by_id[pid]["model"] = model_name
                    if model_name and model_name not in providers_by_id[pid]["candidates"]:
                        providers_by_id[pid]["candidates"].append(model_name)
                    if pid not in connections:
                        api_base = str(native_cfg.get("api_base") or native_cfg.get("base_url") or "").strip()
                        if api_base:
                            pricing_url = newapi_pricing_url(api_base)
                            connections[pid] = {
                                "api_base": api_base,
                                "api_key": _resolve_secret_key(native_cfg.get("key", native_cfg.get("api_key"))),
                                "proxy": str(native_cfg.get("proxy") or "").strip(),
                                "supplier_name": providers_by_id[pid]["supplier_name"],
                                "cluster_id": providers_by_id[pid]["cluster_id"],
                            }
                            if pricing_url:
                                providers_by_id[pid]["has_api_base"] = True
                                providers_by_id[pid]["api_origin"] = safe_origin_label(api_base)
        except Exception:
            pass

    return providers_by_id, suppliers_by_id, connections


def build_provider_metadata_map(
    controller: Any,
    session: Any,
    settings: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build lightweight metadata map for ``ModelManagement.maintenance()`` without secrets."""
    providers_by_id, _suppliers, connections = collect_provider_inventory(controller, session)
    meta: dict[str, dict[str, Any]] = {}
    for pid, info in providers_by_id.items():
        own_ids = _own_source_ids_for_provider(pid, connections.get(pid), settings)
        meta[pid] = {
            "model": info.get("model") or "",
            "cluster_id": info.get("cluster_id") or pid,
            "own_source_ids": own_ids,
        }
    return meta


def collect_active_bindings(
    session: Any,
    *,
    host_route: dict[str, Any] | None = None,
    model_status: dict[str, Any] | None = None,
) -> set[str]:
    """Compute active-binding provider set across host route, task profiles, and replacement rules."""
    bound: set[str] = set()

    def _add_rules(raw_rules: Any) -> None:
        rules = raw_rules
        if type(rules) is str and rules.strip():
            try:
                rules = json.loads(rules)
            except ValueError:
                return
        if not isinstance(rules, list):
            return
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if rule.get("enabled", True) is False:
                continue
            for key in ("provider", "target_provider", "provider_id"):
                target = rule.get(key)
                if type(target) is str and target.strip():
                    bound.add(target.strip())

    if isinstance(host_route, dict):
        hp = str(host_route.get("provider") or "").strip()
        if hp:
            bound.add(hp)
    if isinstance(model_status, dict):
        for task in model_status.get("tasks") or ():
            if not isinstance(task, dict):
                continue
            fields = task.get("fields") if isinstance(task.get("fields"), dict) else task
            for field in ("provider", "fallback"):
                val = fields.get(field)
                if isinstance(val, dict):
                    val = val.get("effective") if "effective" in val else val.get("value")
                if type(val) is str and val.strip():
                    bound.add(val.strip())
            rules_val = fields.get("replacement_rules")
            if isinstance(rules_val, dict):
                rules_val = rules_val.get("effective") if "effective" in rules_val else rules_val.get("value")
            _add_rules(rules_val)

    router = getattr(getattr(session, "application", None), "router", None)
    if not bound and router is not None:
        for profile in getattr(router, "profiles", {}).values():
            for attr in ("default_provider", "default_fallback"):
                val = getattr(profile, attr, "")
                if type(val) is str and val.strip():
                    bound.add(val.strip())
            _add_rules(getattr(profile, "replacement_rules", None))
    profile_pid = str(getattr(getattr(session, "profile", None), "provider_id", "") or "").strip()
    if profile_pid:
        bound.add(profile_pid)
    return bound


def _empty_usage_bucket() -> dict[str, int]:
    return {
        "calls": 0,
        "requests": 0,
        "total_tokens": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_creation_tokens": 0,
        "output_tokens": 0,
    }


def collect_usage_consumption(
    runtime: Any,
    target_context: Any = None,
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, dict[str, int]]]]:
    """Aggregate full per-provider & per-model token usage from SQLite (0 schema bump).

    Returns ``(by_provider, by_provider_model)``.
    Always scopes ``astrbot_core_reply_observations`` with ``WHERE role_key = ?`` and
    aggregates directly in SQLite via ``json_each`` to avoid loading multi-hundred-MB
    ``record_json`` blobs into Python (no ``LIMIT 200`` truncation!).
    """
    by_provider: dict[str, dict[str, int]] = {}
    by_provider_model: dict[str, dict[str, dict[str, int]]] = {}

    def _accumulate(pid: str, model_name: str, row_vals: dict[str, int]) -> None:
        clean_pid = str(pid or "").strip()
        if not clean_pid:
            return
        clean_model = str(model_name or "").strip()
        p_bucket = by_provider.setdefault(clean_pid, _empty_usage_bucket())
        m_bucket = by_provider_model.setdefault(clean_pid, {}).setdefault(clean_model, _empty_usage_bucket())
        for field in _empty_usage_bucket():
            val = int(row_vals.get(field) or 0)
            p_bucket[field] += val
            m_bucket[field] += val

    try:
        with runtime.read_snapshot():
            # 1. model_calls (exact COUNT(DISTINCT task_id) for requests)
            rows = runtime.read_all(
                """SELECT
                    CASE WHEN json_valid(provider_json)
                        THEN COALESCE(json_extract(provider_json, '$.provider'), '') ELSE '' END AS provider_id,
                    CASE WHEN json_valid(provider_json)
                        THEN COALESCE(json_extract(provider_json, '$.model'), '') ELSE '' END AS model_name,
                    COUNT(*) AS calls,
                    COUNT(DISTINCT task_id) AS requests,
                    SUM(COALESCE(actual_tokens, 0)) AS total_tokens,
                    SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                    SUM(COALESCE(cached_input_tokens, 0)) AS cached_input_tokens,
                    SUM(COALESCE(cache_creation_tokens, 0)) AS cache_creation_tokens,
                    SUM(COALESCE(output_tokens, 0)) AS output_tokens
                FROM model_calls
                WHERE provider_json IS NOT NULL
                GROUP BY provider_id, model_name""",
                (),
            )
            for row in rows:
                _accumulate(
                    row["provider_id"],
                    row["model_name"],
                    {
                        "calls": row["calls"],
                        "requests": row["requests"],
                        "total_tokens": row["total_tokens"],
                        "input_tokens": row["input_tokens"],
                        "cached_input_tokens": row["cached_input_tokens"],
                        "cache_creation_tokens": row["cache_creation_tokens"],
                        "output_tokens": row["output_tokens"],
                    },
                )

            # 2. ALL astrbot_core_reply_observations for the current role (NO LIMIT 200!)
            if core_obs_available(runtime):
                where_clause = ""
                params: tuple[Any, ...] = ()
                if target_context is not None:
                    try:
                        rk = role_key(target_context)
                        where_clause = "WHERE o.role_key = ?"
                        params = (rk,)
                    except Exception:
                        where_clause = ""
                        params = ()

                try:
                    core_agg = runtime.read_all(
                        f"""SELECT
                            COALESCE(json_extract(req.value, '$.provider'), '') AS provider_id,
                            COUNT(*) AS calls,
                            COUNT(DISTINCT o.turn_id) AS requests,
                            SUM(COALESCE(json_extract(req.value, '$.usage.tokens'), 0)) AS total_tokens,
                            SUM(COALESCE(json_extract(req.value, '$.usage.input_tokens'), 0)) AS input_tokens,
                            SUM(COALESCE(json_extract(req.value, '$.usage.cached_input_tokens'), 0)) AS cached_input_tokens,
                            SUM(COALESCE(json_extract(req.value, '$.usage.cache_creation_tokens'), 0)) AS cache_creation_tokens,
                            SUM(COALESCE(json_extract(req.value, '$.usage.output_tokens'), 0)) AS output_tokens
                        FROM astrbot_core_reply_observations o,
                             json_each(o.record_json, '$.requests') req
                        {where_clause}
                        {"AND" if where_clause else "WHERE"} json_valid(o.record_json)
                          AND json_type(o.record_json, '$.requests') = 'array'
                        GROUP BY provider_id""",
                        params,
                    )
                    for row in core_agg:
                        _accumulate(
                            row["provider_id"],
                            "",
                            {
                                "calls": row["calls"],
                                "requests": row["requests"],
                                "total_tokens": row["total_tokens"],
                                "input_tokens": row["input_tokens"],
                                "cached_input_tokens": row["cached_input_tokens"],
                                "cache_creation_tokens": row["cache_creation_tokens"],
                                "output_tokens": row["output_tokens"],
                            },
                        )
                except sqlite3.OperationalError:
                    # Fallback only if SQLite build lacks json_each
                    obs_sql = (
                        "SELECT turn_id, record_json FROM astrbot_core_reply_observations "
                        + ("WHERE role_key = ?" if params else "")
                    )
                    turns_seen: dict[str, set[str]] = {}
                    for obs_row in runtime.read_all(obs_sql, params):
                        try:
                            record = json.loads(obs_row["record_json"])
                        except (TypeError, ValueError):
                            continue
                        if not isinstance(record, dict):
                            continue
                        tid = str(obs_row["turn_id"] or "")
                        for req in record.get("requests") or ():
                            if not isinstance(req, dict):
                                continue
                            pid = str(req.get("provider") or "").strip()
                            if not pid:
                                continue
                            usage = req.get("usage") if isinstance(req.get("usage"), dict) else {}
                            is_new_turn = tid not in turns_seen.setdefault(pid, set())
                            if tid:
                                turns_seen[pid].add(tid)
                            _accumulate(
                                pid,
                                "",
                                {
                                    "calls": 1,
                                    "requests": 1 if is_new_turn else 0,
                                    "total_tokens": int(usage.get("tokens") or 0),
                                    "input_tokens": int(usage.get("input_tokens") or 0),
                                    "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
                                    "cache_creation_tokens": int(usage.get("cache_creation_tokens") or 0),
                                    "output_tokens": int(usage.get("output_tokens") or 0),
                                },
                            )

                # 3. Merge archived cold-telemetry daily rollups so lifetime provider
                #    counts remain exact after cold observations are archived.
                try:
                    from companion_v2.storage.telemetry_archive import load_core_daily_rollups
                    rollup_role = params[0] if params else None
                    for item in load_core_daily_rollups(runtime, role_key_value=rollup_role):
                        totals = item["payload"].get("provider_totals") or {}
                        for pid, vals in totals.items():
                            if isinstance(vals, dict):
                                _accumulate(
                                    pid,
                                    "",
                                    {
                                        "calls": int(vals.get("calls") or 0),
                                        "requests": int(vals.get("requests") or 0),
                                        "total_tokens": int(vals.get("total_tokens") or 0),
                                        "input_tokens": int(vals.get("input_tokens") or 0),
                                        "cached_input_tokens": int(vals.get("cached_input_tokens") or 0),
                                        "cache_creation_tokens": int(vals.get("cache_creation_tokens") or 0),
                                        "output_tokens": int(vals.get("output_tokens") or 0),
                                    },
                                )
                except Exception:
                    pass
    except Exception:
        pass

    return by_provider, by_provider_model


async def read_provider_stat_runtime_models(controller: Any) -> dict[str, list[str]]:
    """Best-effort async enrichment of observed ``provider_model`` names from AstrBot ProviderStat."""
    context = getattr(controller, "context", None)
    getter = getattr(context, "get_db", None) if context is not None else None
    if not callable(getter):
        return {}
    try:
        from sqlalchemy import select
        from astrbot.core.db.po import ProviderStat

        result: dict[str, list[str]] = {}
        async with getter().get_db() as db_session:
            stmt = (
                select(ProviderStat.provider_id, ProviderStat.provider_model)
                .where(ProviderStat.agent_type == "internal")
                .group_by(ProviderStat.provider_id, ProviderStat.provider_model)
            )
            rows = (await db_session.execute(stmt)).all()
        for pid, model in rows:
            clean_pid = str(pid or "").strip()
            clean_model = str(model or "").strip()
            if clean_pid and clean_model:
                bucket = result.setdefault(clean_pid, [])
                if clean_model not in bucket:
                    bucket.append(clean_model)
        return result
    except Exception:
        return {}


def _own_source_ids_for_provider(
    provider_id: str,
    conn: dict[str, Any] | None,
    settings: dict[str, Any],
) -> frozenset[str]:
    ids: set[str] = {f"newapi:{provider_id}"}
    api_base = str((conn or {}).get("api_base") or "").strip()
    cluster_id = str((conn or {}).get("cluster_id") or "").strip()
    target_url = newapi_pricing_url(api_base)
    if target_url:
        norm = target_url.rstrip("/").lower()
        for sid, cfg in (settings.get("price_sources") or {}).items():
            if type(sid) is str and sid.startswith("newapi:") and isinstance(cfg, dict):
                if str(cfg.get("url") or "").strip().rstrip("/").lower() != norm:
                    continue
                # Isolate auth-gated Bearer sources owned by a different cluster.
                if cfg.get("auth_mode") == "bearer":
                    src_cluster = str(cfg.get("cluster_id") or "").strip()
                    src_pid = str(cfg.get("provider_id") or "").strip()
                    if src_cluster and cluster_id and src_cluster != cluster_id and src_pid != provider_id:
                        continue
                ids.add(sid)
    return frozenset(ids)


def _build_sources_list(settings: dict[str, Any], catalog: dict[str, Any]) -> list[dict[str, Any]]:
    cfg_sources = settings.get("price_sources") or {}
    cat_sources = catalog.get("sources") or {}
    ordered_ids: list[str] = [spec["source_id"] for spec in PUBLIC_SOURCE_SPECS]
    for sid in sorted(set(cfg_sources) | set(cat_sources)):
        if sid not in ordered_ids:
            ordered_ids.append(sid)

    result: list[dict[str, Any]] = []
    for sid in ordered_ids:
        scfg = cfg_sources.get(sid) if isinstance(cfg_sources.get(sid), dict) else {}
        scat = cat_sources.get(sid) if isinstance(cat_sources.get(sid), dict) else {}
        enabled = bool(scfg.get("enabled", scat.get("enabled", True)))
        raw_status = str(scat.get("status") or "pending")
        status = "disabled" if not enabled else raw_status
        kind = str(scfg.get("kind") or scat.get("kind") or ("newapi" if sid.startswith("newapi:") else "public"))
        site_meta = deepcopy(scat.get("site_meta")) if isinstance(scat.get("site_meta"), dict) else None
        site_name = str((site_meta or {}).get("system_name") or "").strip() if kind == "newapi" else ""
        label = site_name or str(scfg.get("label") or scat.get("label") or sid)
        entry: dict[str, Any] = {
            "source_id": sid,
            "kind": kind,
            "label": label,
            "url": str(scfg.get("url") or scat.get("url") or ""),
            "enabled": enabled,
            "status": status,
            "model_count": int(scat.get("model_count") or 0),
            "last_sync_at": scat.get("last_sync_at"),
            "last_ok_at": scat.get("last_ok_at"),
            "etag": scat.get("etag"),
            "error": scat.get("error") if enabled else None,
            "provider_id": scfg.get("provider_id") or scat.get("provider_id"),
            "needs_key": bool(scat.get("needs_key", False)),
        }
        if kind == "newapi":
            entry["currency"] = scat.get("currency") or (site_meta or {}).get("currency") or "USD"
            raw_override = scfg.get("currency_override")
            entry["currency_override"] = raw_override if raw_override in ("CNY", "USD") else "auto"
            raw_fiat_rate = scfg.get("fiat_per_base_unit")
            entry["fiat_per_base_unit"] = (
                float(raw_fiat_rate)
                if type(raw_fiat_rate) in (int, float) and float(raw_fiat_rate) > 0
                else None
            )
            eff_scale = (site_meta or {}).get("effective_unit_scale")
            entry["effective_unit_scale"] = (
                float(eff_scale)
                if type(eff_scale) in (int, float) and float(eff_scale) > 0
                else (
                    entry["fiat_per_base_unit"]
                    if entry["fiat_per_base_unit"] is not None
                    else (
                        float((site_meta or {}).get("unit_scale"))
                        if type((site_meta or {}).get("unit_scale")) in (int, float)
                        and float((site_meta or {}).get("unit_scale")) > 0
                        else 1.0
                    )
                )
            )
            entry["auth_mode"] = scfg.get("auth_mode") or scat.get("auth_mode")
            entry["site_meta"] = site_meta
        result.append(entry)
    return result


def build_pricing_payload(
    *,
    controller: Any,
    session: Any,
    runtime: Any,
    settings: dict[str, Any],
    catalog: dict[str, Any],
    host_route: dict[str, Any] | None = None,
    model_status: dict[str, Any] | None = None,
    runtime_models: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Assemble the complete, strictly sorted Pricing v2 API payload (zero secrets!)."""
    now = controller.now()
    providers_by_id, _suppliers_by_id, connections = collect_provider_inventory(controller, session)
    active_bindings = collect_active_bindings(session, host_route=host_route, model_status=model_status)
    target_ctx = getattr(getattr(session, "profile", None), "target", None)
    usage_by_provider, usage_by_provider_model = collect_usage_consumption(runtime, target_ctx)
    rt_models = runtime_models if isinstance(runtime_models, dict) else {}

    pricing_rules = settings.get("pricing_rules") or {}
    provider_tariffs = settings.get("provider_tariffs") or {}
    pricing_multipliers = settings.get("pricing_multipliers") or {}
    pricing_schedules = settings.get("pricing_schedules") or {}
    price_selections = settings.get("price_selections") or {}
    enabled_sources = {
        sid
        for sid, cfg in (settings.get("price_sources") or {}).items()
        if isinstance(cfg, dict) and cfg.get("enabled", True) is True
    }
    catalog_prices = catalog.get("prices") if isinstance(catalog.get("prices"), dict) else {}

    # Collect all known provider IDs across configured, usage, and settings overrides
    all_pids: set[str] = set(providers_by_id) | set(usage_by_provider) | set(provider_tariffs)
    for key in pricing_rules:
        all_pids.add(key.split("|", 1)[0])
    for key in pricing_schedules:
        all_pids.add(key.split("|", 1)[0])
    for pid in price_selections:
        all_pids.add(pid)
    # Also include orphaned cluster multipliers that don't correspond to any configured cluster/provider
    configured_cluster_ids = {info["cluster_id"] for info in providers_by_id.values()}
    for cid in pricing_multipliers:
        if cid not in configured_cluster_ids and cid not in providers_by_id:
            all_pids.add(cid)

    candidates_map: dict[str, dict[str, list[dict[str, Any]]]] = {}
    auto_selected_map: dict[str, dict[str, dict[str, Any] | None]] = {}
    selections_map: dict[str, dict[str, dict[str, Any] | None]] = {}
    unpriced_alerts: list[dict[str, Any]] = []
    configured_items: list[dict[str, Any]] = []
    deleted_items: list[dict[str, Any]] = []

    for pid in all_pids:
        is_configured = pid in providers_by_id
        info = providers_by_id.get(pid)
        usage = usage_by_provider.get(pid, _empty_usage_bucket())
        model_usage = usage_by_provider_model.get(pid, {})
        cluster_id = info["cluster_id"] if info else pid
        supplier_name = info["supplier_name"] if info else pid
        primary_model = info["model"] if info else ""

        # Aggregate candidate models for this provider
        model_list: list[str] = []
        seen_models: set[str] = set()

        def _add_model(m: object) -> None:
            if type(m) is not str:
                return
            cm = m.strip()
            if cm and cm not in seen_models:
                seen_models.add(cm)
                model_list.append(cm)

        _add_model(primary_model)
        if info:
            for m in info.get("candidates") or ():
                _add_model(m)
        for m in rt_models.get(pid) or ():
            _add_model(m)
        for m in model_usage:
            _add_model(m)
        for key in pricing_rules:
            if key.startswith(pid + "|"):
                _add_model(key.split("|", 1)[1])
        for key in pricing_schedules:
            if key.startswith(pid + "|"):
                _add_model(key.split("|", 1)[1])
        for m in (price_selections.get(pid) or {}):
            _add_model(m)

        effective_model = primary_model or (model_list[0] if model_list else "")
        own_source_ids = _own_source_ids_for_provider(pid, connections.get(pid), settings)
        linked_newapi_source_id = next(
            (sid for sid in own_source_ids if sid in (settings.get("price_sources") or {})),
            None,
        )

        # Resolve per-model candidates, auto-selection, and persisted selection
        p_cands: dict[str, list[dict[str, Any]]] = {}
        p_auto: dict[str, dict[str, Any] | None] = {}
        p_sels: dict[str, dict[str, Any] | None] = {}
        for m in model_list:
            cands = find_candidates(
                m,
                catalog=catalog,
                provider_id=pid,
                own_source_ids=own_source_ids,
                enabled_sources=enabled_sources,
            )
            p_cands[m] = [c.to_dict() for c in cands]
            auto_c = select_auto_candidate(cands)
            p_auto[m] = auto_c.to_dict() if auto_c is not None else None
            sel_key = (price_selections.get(pid) or {}).get(m)
            if sel_key and isinstance(catalog_prices.get(sel_key), dict):
                entry = catalog_prices[sel_key]
                p_sels[m] = {
                    "price_key": sel_key,
                    "source_id": str(entry.get("source_id") or ""),
                    "source_label": str(entry.get("source_label") or entry.get("source_id") or ""),
                    "model_id": str(entry.get("model_id") or ""),
                    "rule": deepcopy(entry.get("rule") or {}),
                }
            else:
                p_sels[m] = None

        if p_cands:
            candidates_map[pid] = p_cands
            auto_selected_map[pid] = p_auto
        if any(v is not None for v in p_sels.values()):
            selections_map[pid] = p_sels

        # Primary provider resolution (for card head + backward-compatible v1 fields)
        effective = resolve_effective_pricing(
            pid,
            effective_model,
            settings,
            catalog=catalog,
            cluster_id=cluster_id,
            own_source_ids=own_source_ids,
        )
        v1_resolved = resolve_tariff(
            pid,
            effective_model,
            settings,
            catalog=catalog,
            cluster_id=cluster_id,
            own_source_ids=own_source_ids,
        )
        preset = match_builtin_tariff(effective_model) if effective_model else match_builtin_tariff(pid)
        effective_tariff = None
        if v1_resolved is not None and v1_resolved.tariff.configured:
            t = v1_resolved.tariff
            effective_tariff = {
                "input_per_million": round(t.input / 1_000_000, 6),
                "cached_input_per_million": round(t.cached_input / 1_000_000, 6),
                "cache_creation_per_million": (
                    round(t.cache_creation / 1_000_000, 6) if t.cache_creation is not None else None
                ),
                "output_per_million": round(t.output / 1_000_000, 6),
            }

        sched = lookup_pricing_schedule(pid, effective_model, settings)
        schedule_period_count = (
            len(sched.get("periods") or ()) if schedule_is_active(sched) else 0
        )
        cluster_mult = lookup_cluster_multiplier(pid, settings, cluster_id=cluster_id)
        exact_rule_key = f"{pid}|{effective_model}" if effective_model else ""
        has_override = bool(
            (exact_rule_key and exact_rule_key in pricing_rules)
            or pid in pricing_rules
            or pid in provider_tariffs
        )
        has_schedule = bool(
            (exact_rule_key and exact_rule_key in pricing_schedules)
            or pid in pricing_schedules
        )
        has_selection = bool(price_selections.get(pid))

        # Check unpriced usage per model (total_tokens > 0 & unresolved)
        unpriced_models: list[dict[str, Any]] = []
        if usage["total_tokens"] > 0:
            if model_usage:
                for m_key, m_bucket in model_usage.items():
                    if m_bucket["total_tokens"] <= 0:
                        continue
                    check_model = m_key or effective_model
                    m_eff = resolve_effective_pricing(
                        pid,
                        check_model,
                        settings,
                        catalog=catalog,
                        cluster_id=cluster_id,
                        own_source_ids=own_source_ids,
                    )
                    if m_eff is None:
                        unpriced_models.append({
                            "model": check_model,
                            "total_tokens": m_bucket["total_tokens"],
                            "calls": m_bucket["calls"],
                        })
            elif effective is None:
                unpriced_models.append({
                    "model": effective_model,
                    "total_tokens": usage["total_tokens"],
                    "calls": usage["calls"],
                })
        if unpriced_models:
            unpriced_models.sort(key=lambda item: (-item["total_tokens"], item["model"].lower()))
            unpriced_alerts.append({
                "provider_id": pid,
                "cluster_id": cluster_id,
                "supplier_name": supplier_name,
                "deleted": not is_configured,
                "total_tokens": sum(item["total_tokens"] for item in unpriced_models),
                "calls": sum(item["calls"] for item in unpriced_models),
                "model_count": len(unpriced_models),
                "models": unpriced_models,
            })

        item_dict = {
            "id": pid,
            "model": effective_model,
            "candidates": model_list,
            "runtime_models": list(rt_models.get(pid) or ()),
            "configured": is_configured,
            "enabled": info["enabled"] if info else False,
            "loaded": info["loaded"] if info else False,
            "bound": pid in active_bindings,
            "provider_source_id": info["provider_source_id"] if info else None,
            "cluster_id": cluster_id,
            "supplier_name": supplier_name,
            "has_api_base": info["has_api_base"] if info else False,
            "api_origin": info["api_origin"] if info else None,
            "newapi_source_id": linked_newapi_source_id,
            "total_tokens": usage["total_tokens"],
            "calls": usage["calls"],
            "requests": usage["requests"],
            "input_tokens": usage["input_tokens"],
            "cached_input_tokens": usage["cached_input_tokens"],
            "cache_creation_tokens": usage["cache_creation_tokens"],
            "output_tokens": usage["output_tokens"],
            "override": deepcopy(provider_tariffs.get(pid)),
            "pricing_rule": deepcopy(
                pricing_rules.get(exact_rule_key) if (exact_rule_key and exact_rule_key in pricing_rules)
                else pricing_rules.get(pid)
            ),
            "pricing_rule_key": (
                exact_rule_key if (exact_rule_key and exact_rule_key in pricing_rules)
                else (pid if pid in pricing_rules else None)
            ),
            "preset_match": preset.to_usd_dict() if preset is not None else None,
            "effective_source": effective.source if effective is not None else "unconfigured",
            "effective_mode": effective.mode if effective is not None else None,
            "effective_preset_id": effective.preset_id if effective is not None else None,
            "effective_price_key": effective.price_key if effective is not None else None,
            "effective_source_label": effective.source_label if effective is not None else None,
            "effective_rule": deepcopy(effective.rule) if effective is not None else None,
            "effective_tariff_usd": effective_tariff,
            "cluster_multiplier": cluster_mult,
            "schedule_active": schedule_period_count > 0,
            "schedule_period_count": schedule_period_count,
            "has_override": has_override,
            "has_schedule": has_schedule,
            "has_selection": has_selection,
        }
        if is_configured:
            configured_items.append(item_dict)
        else:
            deleted_items.append(item_dict)

    # Strict sorting:
    # 1. Configured providers: active (bound or total_tokens > 0) FIRST sorted by total_tokens DESC,
    #    bound DESC, id ASC; unused configured AFTER sorted by id ASC.
    def _configured_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        is_active = item["bound"] or item["total_tokens"] > 0
        if is_active:
            return (0, -item["total_tokens"], not item["bound"], item["id"].lower())
        return (1, 0, False, item["id"].lower())

    configured_items.sort(key=_configured_sort_key)

    # 2. Deleted / historical providers: total_tokens DESC, id ASC
    deleted_items.sort(key=lambda item: (-item["total_tokens"], item["id"].lower()))

    # 3. Unpriced alerts: total_tokens DESC, provider_id ASC
    unpriced_alerts.sort(key=lambda item: (-item["total_tokens"], item["provider_id"].lower()))

    # 4. Left-rail clusters grouped by cluster_id (provider_source_id or standalone id)
    cat_sources = catalog.get("sources") if isinstance(catalog.get("sources"), dict) else {}
    clusters_by_id: dict[str, dict[str, Any]] = {}
    for item in configured_items:
        cid = item["cluster_id"]
        cluster = clusters_by_id.get(cid)
        if cluster is None:
            mult = float(pricing_multipliers.get(cid, 1.0))
            cluster = {
                "cluster_id": cid,
                "name": item["supplier_name"],
                "provider_source_id": item["provider_source_id"],
                "multiplier": mult,
                "has_custom_multiplier": (cid in pricing_multipliers and mult != 1.0),
                "has_custom_rules": False,
                "has_bound": False,
                "active_count": 0,
                "provider_count": 0,
                "cluster_total_tokens": 0,
                "newapi_source_id": None,
                "newapi_group_hint": None,
                "providers": [],
            }
            clusters_by_id[cid] = cluster
        cluster["providers"].append(item)
        cluster["provider_count"] += 1
        cluster["cluster_total_tokens"] += item["total_tokens"]
        if item["bound"]:
            cluster["has_bound"] = True
        if item["has_override"]:
            cluster["has_custom_rules"] = True
        if not cluster["newapi_source_id"] and item["newapi_source_id"]:
            cluster["newapi_source_id"] = item["newapi_source_id"]
        if item["bound"] or item["total_tokens"] > 0:
            cluster["active_count"] += 1

    clusters = list(clusters_by_id.values())
    for cluster in clusters:
        cluster["providers"].sort(key=_configured_sort_key)
        sid = cluster.get("newapi_source_id")
        if sid and isinstance(cat_sources.get(sid), dict):
            smeta = cat_sources[sid].get("site_meta")
            if isinstance(smeta, dict) and isinstance(smeta.get("detected_groups"), dict):
                hint = (
                    smeta["detected_groups"].get(cluster["cluster_id"])
                    or smeta["detected_groups"].get("__station__")
                )
                if isinstance(hint, dict):
                    cluster["newapi_group_hint"] = deepcopy(hint)

    # Strict cluster sort: active-first (cluster_total_tokens > 0 || has_bound),
    # then cluster_total_tokens DESC, active_count DESC, name ASC.
    clusters.sort(
        key=lambda c: (
            not (c["cluster_total_tokens"] > 0 or c["has_bound"]),
            -c["cluster_total_tokens"],
            -c["active_count"],
            c["name"].lower(),
            c["cluster_id"].lower(),
        )
    )

    fx = dict(settings["exchange_rate"])
    fx["effective_usd_cny"] = effective_usd_cny_rate(settings)
    fx["effective_source"] = effective_rate_source(settings)
    fx["stale"] = is_rate_stale(settings, now=now)
    fx["ttl_hours"] = EXCHANGE_RATE_TTL_SECONDS // 3600

    return {
        # V1 backward-compatible keys:
        "display_currency": settings["display_currency"],
        "use_builtin_presets": settings["use_builtin_presets"],
        "exchange_rate": fx,
        "provider_tariffs": deepcopy(settings["provider_tariffs"]),
        "providers": configured_items + deleted_items,
        "presets": catalog_presets(),
        "revision": settings["revision"],
        "configuration_scope": "runtime",
        # V2 enriched keys:
        "version": 2,
        "pricing_timezone": settings.get("pricing_timezone", DEFAULT_PRICING_TIMEZONE),
        "available_timezones": list(COMMON_TIMEZONES),
        "pricing_rules": deepcopy(pricing_rules),
        "pricing_multipliers": deepcopy(pricing_multipliers),
        "pricing_schedules": deepcopy(pricing_schedules),
        "price_sources": deepcopy(settings.get("price_sources") or {}),
        "price_selections": deepcopy(price_selections),
        "price_sync": deepcopy(settings.get("price_sync") or {"auto_daily": True, "last_auto_sync_at": None}),
        "sources": _build_sources_list(settings, catalog),
        "catalog_updated_at": catalog.get("updated_at"),
        "clusters": clusters,
        "deleted_providers": deleted_items,
        "unpriced": unpriced_alerts,
        "candidates": candidates_map,
        "auto_selected": auto_selected_map,
        "selections": selections_map,
    }
